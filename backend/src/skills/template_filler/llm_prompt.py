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
你的任务是识别每个"用户应填写的占位符",输出 JSON 数组。

可见的占位符通常具备这些特征中的至少一个:
  - 用方括号包裹("[客户名]"、"【日期】"、"《金额》")
  - 用尖括号包裹("<供应商>")
  - 用占位词描述,如"在此填写..."、"待定"、"XXX"、"此处填入"
  - 标签紧跟空白冒号:"客户:    "(空白处需要填入客户名)

不应被识别为占位符:
  - 标题、固定表头、说明文字
  - 日期格式样例(如"年    月    日")、单位说明(如"金额(元)")
  - 已经填写过的固定数据

输出格式(纯 JSON 数组,不带 markdown 围栏):
[
  {
    "name": "snake_case_变量名",
    "label": "中文显示名,简短",
    "type": "string|number|date|currency",
    "required": true,
    "original_text": "原文中**完整、连续、唯一**的占位符文本(用于程序定位)",
    "location_hint": "采用输入中的位置提示原样回填",
    "description": "字段语义,一句话(可选)"
  }
]

重要:`original_text` 必须能在输入文本中通过 `str.find` 命中,且在所属段落里只出现**一次**。
如果一段文本里同样的占位串出现两次(比如表格里两列都是"[商品名]"),请只输出一条字段;
程序会要求人工 review。如果某段没有占位符,跳过该段即可。
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
        try:
            field = ExtractedField(
                name=str(raw["name"]),
                label=str(raw.get("label") or raw["name"]),
                type=ftype,
                required=bool(raw.get("required", True)),
                original_text=str(raw["original_text"]),
                location_hint=raw.get("location_hint"),
                description=raw.get("description"),
            )
        except (KeyError, ValueError) as exc:
            logger.debug("dropping malformed field row %r: %s", raw, exc)
            continue
        out.append(field)
    return out, None
