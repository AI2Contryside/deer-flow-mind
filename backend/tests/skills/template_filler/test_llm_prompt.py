"""Unit tests for LLM prompt building and reply parsing."""

from __future__ import annotations

from src.skills.template_filler.llm_prompt import (
    build_user_message,
    parse_llm_reply,
)
from src.skills.template_filler.text_scanner import TextFragment
from src.skills.template_filler.types import FieldType


def test_build_user_message_one_line_per_fragment():
    fragments = [
        TextFragment(text="客户:[客户名]", location_hint="段落 1"),
        TextFragment(text="日期:[日期]", location_hint="段落 2"),
    ]
    msg = build_user_message(fragments)

    assert "[段落 1] 客户:[客户名]" in msg
    assert "[段落 2] 日期:[日期]" in msg
    # Newlines flatten to a marker so each fragment stays on its own line.
    fragments2 = [TextFragment(text="line1\nline2", location_hint="L")]
    msg2 = build_user_message(fragments2)
    assert "[L] line1 ⏎ line2" in msg2
    # Embedded \n must not split the bullet across lines.
    assert "\n[L]" in msg2 or msg2.count("[L]") == 1


def test_parse_pure_json_array():
    raw = """
    [
      {"name":"customer_name","label":"客户","type":"string","required":true,"original_text":"[客户名]","location_hint":"段落 1","description":"采购方"},
      {"name":"date","label":"日期","type":"date","required":false,"original_text":"[日期]","location_hint":"段落 2"}
    ]
    """
    fields, err = parse_llm_reply(raw)
    assert err is None
    assert len(fields) == 2
    assert fields[0].name == "customer_name"
    assert fields[0].type == FieldType.STRING
    assert fields[0].required is True
    assert fields[1].type == FieldType.DATE
    assert fields[1].required is False


def test_parse_strips_markdown_fence():
    raw = """```json
[{"name":"x","label":"X","type":"string","required":true,"original_text":"[x]"}]
```"""
    fields, err = parse_llm_reply(raw)
    assert err is None and len(fields) == 1
    assert fields[0].name == "x"


def test_parse_recovers_array_from_chatty_reply():
    raw = (
        "好的,我识别到 1 个字段:\n\n"
        '[{"name":"客户名","label":"客户","type":"string","required":true,"original_text":"[客户]"}]'
        "\n\n以上是结果。"
    )
    fields, err = parse_llm_reply(raw)
    assert err is None
    assert len(fields) == 1


def test_parse_returns_error_on_unparseable_text():
    fields, err = parse_llm_reply("我无法识别字段")
    assert fields == []
    assert err == "llm_unparseable"


def test_parse_returns_error_on_empty_reply():
    fields, err = parse_llm_reply("   ")
    assert fields == []
    assert err == "empty_llm_reply"


def test_parse_drops_malformed_rows_keeps_valid_ones():
    raw = """
    [
      {"name":"good","label":"OK","original_text":"[ok]"},
      {"label":"missing name","original_text":"[bad]"},
      {"name":"missing_original"},
      {"name":"weird_type","label":"X","type":"emoji","original_text":"[w]"}
    ]
    """
    fields, err = parse_llm_reply(raw)
    assert err is None
    names = [f.name for f in fields]
    # "good" survives. "missing name" dropped (no name). "missing_original"
    # dropped (no original_text). "weird_type" survives with type=string fallback.
    assert "good" in names
    assert "weird_type" in names
    assert "missing name" not in names
    assert "missing_original" not in names

    weird = next(f for f in fields if f.name == "weird_type")
    assert weird.type == FieldType.STRING


def test_parse_returns_error_on_non_array_json():
    fields, err = parse_llm_reply('{"name":"x","original_text":"[x]"}')
    assert fields == []
    assert err == "llm_unparseable"
