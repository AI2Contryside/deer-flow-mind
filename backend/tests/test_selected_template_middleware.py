"""Behaviour tests for SelectedTemplateMiddleware.

We assert the middleware is a thin "read state → prepend block" shim:
no I/O, no network, no fancy state mutations beyond messages.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from langchain_core.messages import AIMessage, HumanMessage

from src.agents.middlewares.selected_template_middleware import (
    SelectedTemplateMiddleware,
)


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


def test_returns_none_when_messages_empty():
    mw = SelectedTemplateMiddleware()
    state = _state(selected={"template_id": "t_1"}, messages=[])
    assert mw.before_agent(state, _runtime()) is None


def test_returns_none_when_no_human_message():
    mw = SelectedTemplateMiddleware()
    state = _state(
        selected={"template_id": "t_1"},
        messages=[AIMessage(content="answer")],
    )
    assert mw.before_agent(state, _runtime()) is None


def test_injects_block_with_template_metadata():
    mw = SelectedTemplateMiddleware()
    user_text = "根据这个模板生成员工档案"
    state = _state(
        selected={
            "template_id": "tpl_abc",
            "name": "员工信息.xlsx",
            "type": "excel",
            "fields": [
                {"name": "name", "label": "姓名", "type": "string", "required": True},
                {"name": "department", "label": "部门", "type": "string", "required": False, "description": "员工所属部门"},
            ],
        },
        messages=[HumanMessage(content=user_text)],
    )
    result = mw.before_agent(state, _runtime())
    assert result is not None
    new_msgs = result["messages"]
    assert len(new_msgs) == 1
    new_text = new_msgs[0].content
    # Block opens / closes correctly.
    assert new_text.startswith("<selected_template>")
    assert "</selected_template>" in new_text
    # Original user text preserved at the end.
    assert new_text.endswith(user_text)
    # Metadata + fields rendered.
    assert "tpl_abc" in new_text
    assert "员工信息.xlsx" in new_text
    assert "`name`" in new_text and "姓名" in new_text
    assert "`department`" in new_text and "员工所属部门" in new_text


def test_idempotent_when_block_already_in_message():
    """Resume / retry path: block exists, don't double-prepend."""
    mw = SelectedTemplateMiddleware()
    seeded = (
        "<selected_template>\n"
        "用户已选模板 …\n"
        "</selected_template>\n\n"
        "原始用户消息"
    )
    state = _state(
        selected={"template_id": "tpl_abc", "name": "x"},
        messages=[HumanMessage(content=seeded)],
    )
    assert mw.before_agent(state, _runtime()) is None


def test_handles_empty_fields_with_explicit_warning():
    mw = SelectedTemplateMiddleware()
    state = _state(
        selected={"template_id": "tpl_abc", "name": "blank.docx", "fields": []},
        messages=[HumanMessage(content="生成文件")],
    )
    result = mw.before_agent(state, _runtime())
    assert result is not None
    text = result["messages"][0].content
    # Empty fields path renders a fallback note, not the field list header.
    assert "fields 为空" in text or "未抽取" in text


def test_finds_last_human_when_trailed_by_ai_message():
    mw = SelectedTemplateMiddleware()
    state = _state(
        selected={"template_id": "tpl_abc", "name": "x"},
        messages=[
            HumanMessage(content="第一轮"),
            AIMessage(content="回答"),
            HumanMessage(content="第二轮"),
            AIMessage(content="另一个回答"),
        ],
    )
    result = mw.before_agent(state, _runtime())
    assert result is not None
    msgs = result["messages"]
    # Block prepended only on the latest HumanMessage (index 2).
    assert "<selected_template>" not in msgs[0].content
    assert "<selected_template>" in msgs[2].content
    assert msgs[2].content.endswith("第二轮")


def test_handles_list_content_blocks():
    """assistant-ui sometimes carries content as a list of typed blocks."""
    mw = SelectedTemplateMiddleware()
    state = _state(
        selected={"template_id": "tpl_abc", "name": "x"},
        messages=[
            HumanMessage(
                content=[
                    {"type": "text", "text": "第一段"},
                    {"type": "text", "text": "第二段"},
                ]
            )
        ],
    )
    result = mw.before_agent(state, _runtime())
    assert result is not None
    text = result["messages"][0].content
    assert "<selected_template>" in text
    assert "第一段" in text
    assert "第二段" in text
