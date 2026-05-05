"""LLM prompt + response parsing for field extraction.

The prompt is intentionally narrow:
  - Show the LLM the visible text + where it lives.
  - Ask for a JSON list of fields with {name, label, type, required,
    original_text, location_hint, description}.
  - Constrain `name` to snake_case and `original_text` to a verbatim
    substring — that's what the Go jinja_renderer will look for.

Parsing tolerates trailing commentary by extracting the first top-level
JSON array from the reply.
"""

from __future__ import annotations

import json
import logging
import re

from src.skills.template_filler.text_scanner import TextFragment
from src.skills.template_filler.types import ExtractedField, FieldType

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是中文办公文档字段抽取助手。给定一个模板文件中可见的文本片段(每条带"位置提示"),
你的任务是识别每个"用户应填写的位置",输出 JSON 数组。

支持两种识别模式,根据模板形态自动选择:

【模式 A — 占位符模式 (inline_text)】
模板里写了明显的待填标记。识别特征:
  - 用方括号包裹("[客户名]"、"【日期】"、"《金额》")
  - 用尖括号包裹("<供应商>")
  - 占位词:"在此填写..."、"待定"、"XXX"、"此处填入"
  - 标签紧跟空白冒号:"客户:    "(空白处需要填入客户名)

【模式 B — 列头表格模式 (header_cell)】
xlsx 里常见:某一行是连续多列的中文短标签(列头),其下一行或几行是空白(留给用户填数据)。
识别特征:
  - 第 N 行连续多个单元格都是表头性短文本(2-12 字,如"姓名"/"工号"/"部门"/"金额")
  - 第 N+1 行(及之后若干行)的对应列**完全空白**(输入中根本不出现这些位置)
  - 列头本身**不是**占位符,而是字段标签

对模式 B,把每个列头识别为一个字段。`cell_anchor` 设为该列**第一个空白数据行**的位置(取列头下方第一行,
列标与列头一致),格式 "<sheet_name>!<cell>",如 "Sheet1!B2"——位置一定要参考输入位置提示中的 sheet 名。
**列头本身不写进 original_text**,留空字符串即可。

【不应识别为字段的内容】
  - 标题、说明、固定表头(如"销售订单")、单位说明(如"金额(元)")、日期格式样例("年  月  日")
  - 已经填写过的固定数据
  - 仅一行的孤立标签(没有连续列头集群)

输出格式(纯 JSON 数组,不带 markdown 围栏):
[
  // 模式 A 示例
  {
    "name": "customer_name",
    "label": "客户名称",
    "type": "string",
    "required": true,
    "original_text": "[客户名]",
    "cell_anchor": null,
    "location_hint": "段落 4",
    "description": "采购方公司全称"
  },
  // 模式 B 示例(列头"姓名"在 A1,数据行在 A2)
  {
    "name": "name",
    "label": "姓名",
    "type": "string",
    "required": true,
    "original_text": "",
    "cell_anchor": "Sheet1!A2",
    "location_hint": "Sheet1!A1",
    "description": null
  }
]

模式互斥规则:**每个字段只填一个**——要么 original_text 非空(模式 A),要么 cell_anchor 非空(模式 B)。
绝不要两个都填。如果一段文本里同样的占位串出现两次(比如两列都是"[商品名]"),请只输出一条;
程序会要求人工 review。
"""


def build_user_message(fragments: list[TextFragment]) -> str:
    """Render fragments into the bullet list the LLM will see.

    One line per fragment, with location hint up front so the model can
    reference it back in `location_hint` without inventing new addresses.
    """
    lines: list[str] = ["以下是模板中可见的文本(每行格式:[位置] 文本):", ""]
    for f in fragments:
        # Strip embedded newlines so the bullet stays on one line — the
        # location is per-cell/per-paragraph anyway.
        flat = f.text.replace("\n", " ⏎ ")
        lines.append(f"[{f.location_hint}] {flat}")
    lines.append("")
    lines.append("请输出 JSON 数组,只包含识别到的占位符字段。")
    return "\n".join(lines)


_TOP_LEVEL_ARRAY_RE = re.compile(r"\[\s*(?:\{.*?\})?\s*(?:,\s*\{.*?\}\s*)*\]", re.DOTALL)


def parse_llm_reply(content: str) -> tuple[list[ExtractedField], str | None]:
    """Parse the LLM's raw text into ExtractedField list.

    Returns (fields, error_message). If parsing fails we return [] plus an
    error code the caller can fold into ExtractionResult.warnings rather
    than blowing up the upload — partial / no extraction is recoverable
    by the human review UI.
    """
    text = content.strip()
    if not text:
        return [], "empty_llm_reply"

    # Strip ```json fences if present.
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
        text = text.strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = _TOP_LEVEL_ARRAY_RE.search(text)
        if not match:
            return [], "llm_unparseable"
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            return [], "llm_unparseable"

    if not isinstance(parsed, list):
        return [], "llm_unparseable"

    out: list[ExtractedField] = []
    for raw in parsed:
        if not isinstance(raw, dict):
            continue
        # Tolerate type-as-string-not-in-enum by falling back to STRING.
        type_raw = raw.get("type", "string")
        try:
            ftype = FieldType(type_raw) if type_raw else FieldType.STRING
        except ValueError:
            ftype = FieldType.STRING
        # Inline-text and header-cell are mutually exclusive but the LLM
        # may emit both (or neither) on a bad day. Normalise:
        #   - empty original_text → ""
        #   - "null" / explicit None on cell_anchor → None
        # The downstream renderer rejects rows where both stay empty, so
        # parsing is permissive — we don't drop the row here.
        original_text = raw.get("original_text") or ""
        cell_anchor = raw.get("cell_anchor")
        if cell_anchor in (None, "", "null"):
            cell_anchor = None
        else:
            cell_anchor = str(cell_anchor)
        # Skip rows that have neither — they can't be jinja-ified and
        # would just confuse the review UI.
        if not original_text and not cell_anchor:
            logger.debug("dropping field with no original_text / cell_anchor: %r", raw)
            continue
        try:
            field = ExtractedField(
                name=str(raw["name"]),
                label=str(raw.get("label") or raw["name"]),
                type=ftype,
                required=bool(raw.get("required", True)),
                original_text=str(original_text),
                cell_anchor=cell_anchor,
                location_hint=raw.get("location_hint"),
                description=raw.get("description"),
            )
        except (KeyError, ValueError) as exc:
            logger.debug("dropping malformed field row %r: %s", raw, exc)
            continue
        out.append(field)
    return out, None
