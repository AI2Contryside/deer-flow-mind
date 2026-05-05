"""Behaviour tests for SelectedTemplateMiddleware.

We assert the middleware is a thin "read state → emit SystemMessage" shim:
no I/O, no network, no HumanMessage mutation (which would leak the prompt
block to the FE).
"""

from __future__ import annotations

from unittest.mock import MagicMock

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from src.agents.middlewares.selected_template_middleware import (
    SelectedTemplateMiddleware,
)

_FIXED_ID = "__selected_template_block__"


def _runtime() -> MagicMock:
    rt = MagicMock()
    rt.context = {}
    return rt


def _state(*, selected: dict | None, messages: list) -> dict:
    return {"selected_template": selected, "messages": messages}


def test_returns_none_when_selected_template_missing():
    mw = SelectedTemplateMiddleware()
    state = _state(selected=None, messages=[HumanMessage(content="hi")])
    assert mw.before_agent(state, _runtime()) is None


def test_returns_none_when_selected_template_not_a_dict():
    mw = SelectedTemplateMiddleware()
    state = _state(selected="not-a-dict", messages=[HumanMessage(content="hi")])  # type: ignore[arg-type]
    assert mw.before_agent(state, _runtime()) is None


def test_emits_system_message_with_block():
    mw = SelectedTemplateMiddleware()
    user_text = "根据这个模板生成员工档案"
    state = _state(
        selected={
            "template_id": "tpl_abc",
            "name": "员工信息.xlsx",
            "type": "excel",
            "fields": [
                {"name": "name", "label": "姓名", "type": "string", "required": True},
                {"name": "department", "label": "部门", "required": False, "description": "员工所属部门"},
            ],
        },
        messages=[HumanMessage(content=user_text)],
    )
    result = mw.before_agent(state, _runtime())
    assert result is not None
    out = result["messages"]
    assert len(out) == 1
    sysmsg = out[0]
    assert isinstance(sysmsg, SystemMessage), "must be SystemMessage so the FE doesn't render it"
    assert sysmsg.id == _FIXED_ID, "stable id is what makes add_messages dedupe across turns"
    text = sysmsg.content
    assert text.startswith("<selected_template>")
    assert text.endswith("</selected_template>")
    assert "tpl_abc" in text
    assert "员工信息.xlsx" in text
    assert "`name`" in text and "姓名" in text
    assert "`department`" in text and "员工所属部门" in text


def test_does_not_touch_existing_messages():
    """Returned dict only contains the new SystemMessage — never mutates user input."""
    mw = SelectedTemplateMiddleware()
    user_text = "原始用户消息"
    state = _state(
        selected={"template_id": "tpl_abc", "name": "x"},
        messages=[HumanMessage(content=user_text)],
    )
    result = mw.before_agent(state, _runtime())
    assert result is not None
    # Returned messages list contains ONLY the new SystemMessage. The
    # original HumanMessage stays untouched in state because the
    # add_messages reducer keeps both — we never re-emit the user message.
    assert len(result["messages"]) == 1
    assert isinstance(result["messages"][0], SystemMessage)


def test_handles_empty_fields_with_explicit_warning():
    mw = SelectedTemplateMiddleware()
    state = _state(
        selected={"template_id": "tpl_abc", "name": "blank.docx", "fields": []},
        messages=[HumanMessage(content="生成文件")],
    )
    result = mw.before_agent(state, _runtime())
    assert result is not None
    text = result["messages"][0].content
    assert "fields 为空" in text or "未抽取" in text


def test_re_emits_with_same_id_for_dedup_across_turns():
    """Multiple invocations all carry the same fixed id so add_messages dedupes."""
    mw = SelectedTemplateMiddleware()
    state = _state(
        selected={"template_id": "tpl_abc", "name": "x"},
        messages=[
            HumanMessage(content="第一轮"),
            AIMessage(content="回答"),
            HumanMessage(content="第二轮"),
        ],
    )
    first = mw.before_agent(state, _runtime())
    second = mw.before_agent(state, _runtime())
    assert first is not None and second is not None
    assert first["messages"][0].id == second["messages"][0].id == _FIXED_ID


def test_handles_invalid_fields_shape_gracefully():
    """Non-list `fields` value falls back to the empty-fields warning path."""
    mw = SelectedTemplateMiddleware()
    state = _state(
        selected={"template_id": "tpl_abc", "name": "x", "fields": "garbage"},
        messages=[HumanMessage(content="x")],
    )
    result = mw.before_agent(state, _runtime())
    assert result is not None
    assert "fields 为空" in result["messages"][0].content or "未抽取" in result["messages"][0].content
