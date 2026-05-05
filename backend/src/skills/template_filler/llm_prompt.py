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
from typing import Literal

from src.skills.template_filler.text_scanner import TextFragment
from src.skills.template_filler.types import ExtractedField, FieldType

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是外贸办公文档字段抽取助手。模板使用者是不懂技术的外贸文员,他们**不会**自己写
"[XXX]"、"<XXX>"、"{{XXX}}" 这种占位符——模板里出现的"待填位置"通常就是:
  - 一段标签(如「客户名称」、「合同编号」、「金额」)后面**留空**或**有冒号**
  - 标签后面跟一段下划线 / 连续空格 / 全角空格,等着用户填
  - 一些纯描述性占位词(「在此填入...」、「待定」、「XXX」)

你的任务是基于文档结构和语义,识别哪些是"用户填表时会写数据的位置",输出 JSON 数组。

==================================================
模板类型自动判断
==================================================

输入的位置提示告诉你这是 docx 还是 xlsx:
  - "段落 N" / "表 N · 行X列Y"  → **docx 模板**
  - "<sheet_name>!<cell>"        → **xlsx 模板**
  - "<sheet_name>!__overview__"  → xlsx 工作表结构概览(不是字段)

==================================================
xlsx 模板 — 列头识别(cell_anchor 模式)
==================================================

xlsx 模板几乎都是"表头 + 空白数据行"结构:第 1 行是中文标签(姓名/工号/合同号),后面行是空(等用户填)。
没有占位符,LLM 必须读懂这是一张表单。

判断信号:
  - **__overview__ 元数据**:每个工作表第一条 `[<sheet>!__overview__]` 文本会描述该 sheet 维度,
    并在符合"列头 + 空白填充行"时打 ⚠️ 标记。看到这个提示就直接把该行所有标签都识别成字段。
  - 即使没有 ⚠️ 提示,只要某一行有 ≥2 个连续的中文短标签(2-12 字),就视为列头行。

输出规则(每个列头一条字段):
  - `cell_anchor` = "<sheet>!<列字母><数据行号>",数据行号 = 列头行号 + 1
    (例:列头在 A1,数据行就是 A2;列头在 A3、B3,数据行是 A4、B4)
  - `original_text` 留空字符串
  - `anchor_mode` 字段在 xlsx 模式下不起作用,可省略或填 "append"

==================================================
docx 模板 — 标签锚点识别(original_text 模式)
==================================================

docx 模板典型样貌(注意!**没有任何 [] 占位符**):

  合同编号:____________________
  甲方:
  乙方公司名称:
  日期:    年    月    日
  金额:¥
  备注:在此填入...

每一行都是"标签 + 待填位置"。你要为每个标签输出一条字段,并通过 `original_text` + `anchor_mode` 告诉
程序怎么把待填位置改写成 jinja 变量。

两种锚点模式(docx 专用):

  ➊ **anchor_mode = "append"**(默认,**优先用这个**)
     当标签本身有意义、用户填表时希望保留时使用。
     - `original_text` = 完整的"标签+冒号"或"标签+冒号+空白"片段
     - 渲染结果:把 `original_text` 替换为 `original_text + "{{ 字段名 }}"`(在末尾追加 jinja)
     - 例:`original_text="客户名称:"` → 渲染后 "客户名称:{{ customer_name }}"
     - 例:`original_text="合同编号:"` → 渲染后 "合同编号:{{ contract_no }}"

  ➋ **anchor_mode = "replace"**
     当锚点本身是"无意义的占位串"、需要被完全吃掉时使用。
     - `original_text` = 那段占位串本身(下划线、占位词、连续 X 等)
     - 渲染结果:把 `original_text` 整段替换为 "{{ 字段名 }}"
     - 例:`original_text="____________________"` → 渲染后 "{{ contract_no }}"
     - 例:`original_text="在此填入客户名"` → 渲染后 "{{ customer_name }}"

**如何选择 mode**:
  - 看 `original_text` 是否含有"用户希望最终文档保留的字符"(标签、冒号、单位符号 ¥、$等)
    → 含有保留字符 = "append"
    → 都是占位符号(`_`、`X`、空白、占位词)= "replace"
  - 拿不准时优先用 "append" — 它最不会丢信息

定位独特性要求:`original_text` 必须在文档里能精准匹配到一处。如果文档里有多处一样的下划线串,
LLM 应把上下文也包含进来,例如不是 `"____"` 而是 `"合同编号:____"`(此时改成 anchor_mode="append")。

==================================================
不应识别为字段
==================================================

  - 标题、固定说明、固定数据(章节标题、版权页等)
  - 已经填写过的内容(看起来不是空白/下划线/占位词)
  - `__overview__` 元数据本身(那是结构提示)
  - 单位说明、格式注释("(元)"、"年 月 日" 这种格式样例 — 但如果是「日期:____年____月____日」整体,可以拆出 3 个独立字段)

==================================================
输出格式(纯 JSON 数组,不带 markdown 围栏)
==================================================

[
  // docx 标签 anchor (append 模式 — 默认)
  {
    "name": "customer_name",
    "label": "客户名称",
    "type": "string",
    "required": true,
    "original_text": "客户名称:",
    "anchor_mode": "append",
    "cell_anchor": null,
    "location_hint": "段落 2",
    "description": "采购方公司全称"
  },
  // docx 占位串 (replace 模式 — 下划线整段消除)
  {
    "name": "contract_no",
    "label": "合同编号",
    "type": "string",
    "required": true,
    "original_text": "合同编号:____________________",
    "anchor_mode": "append",
    "cell_anchor": null,
    "location_hint": "段落 1",
    "description": null
  },
  // xlsx 列头(cell_anchor 模式,数据行 = 列头行+1)
  {
    "name": "name",
    "label": "姓名",
    "type": "string",
    "required": true,
    "original_text": "",
    "anchor_mode": "append",
    "cell_anchor": "Sheet1!A2",
    "location_hint": "Sheet1!A1",
    "description": null
  }
]

互斥规则:每条字段要么 `original_text` 非空(docx),要么 `cell_anchor` 非空(xlsx),不要两个都填。
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
        # anchor_mode is only meaningful for docx text fields. Default to
        # "append" so a forgetful LLM still produces a label-preserving
        # render. Anything outside the allowed enum collapses to default.
        anchor_mode_raw = (raw.get("anchor_mode") or "append").strip().lower()
        anchor_mode: Literal["append", "replace"] = (
            anchor_mode_raw if anchor_mode_raw in ("append", "replace") else "append"
        )
        try:
            field = ExtractedField(
                name=str(raw["name"]),
                label=str(raw.get("label") or raw["name"]),
                type=ftype,
                required=bool(raw.get("required", True)),
                original_text=str(original_text),
                anchor_mode=anchor_mode,
                cell_anchor=cell_anchor,
                location_hint=raw.get("location_hint"),
                description=raw.get("description"),
            )
        except (KeyError, ValueError) as exc:
            logger.debug("dropping malformed field row %r: %s", raw, exc)
            continue
        out.append(field)
    return out, None
