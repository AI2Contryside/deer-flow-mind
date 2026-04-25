"""Tests for PatchedChatDeepSeek's reasoning_content round-tripping.

Background: DeepSeek / Doubao-style endpoints in thinking mode reject any
follow-up request whose prior assistant message lacks ``reasoning_content``
("The `reasoning_content` in the thinking mode must be passed back to the
API."). Stock ChatDeepSeek captures reasoning_content from responses but
drops it on outgoing serialization. PatchedChatDeepSeek closes that loop.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from src.models.patched_deepseek import PatchedChatDeepSeek


@pytest.fixture
def model() -> PatchedChatDeepSeek:
    return PatchedChatDeepSeek(
        model="deepseek-v4-flash",
        api_key="sk-test",
        api_base="https://api.deepseek.com",
        max_tokens=100,
        temperature=0,
    )


def _assistant_dicts(payload: dict) -> list[dict]:
    return [m for m in payload["messages"] if m.get("role") == "assistant"]


def test_reasoning_content_is_injected_into_assistant_payload(model: PatchedChatDeepSeek) -> None:
    # Arrange
    messages = [
        HumanMessage(content="查客户数量"),
        AIMessage(
            content="好的，让我先加载技能。",
            additional_kwargs={"reasoning_content": "我应该先读 SKILL.md"},
            tool_calls=[
                {
                    "name": "read_file",
                    "args": {"path": "/mnt/skills/public/erpnext-cli/SKILL.md"},
                    "id": "call_1",
                    "type": "tool_call",
                }
            ],
        ),
        ToolMessage(content="# erpnext-cli\n...", tool_call_id="call_1"),
    ]

    # Act
    payload = model._get_request_payload(messages)

    # Assert
    assistants = _assistant_dicts(payload)
    assert len(assistants) == 1
    assert assistants[0]["reasoning_content"] == "我应该先读 SKILL.md"


def test_multiple_assistant_messages_each_get_their_own_reasoning(model: PatchedChatDeepSeek) -> None:
    messages = [
        HumanMessage(content="第一个问题"),
        AIMessage(
            content="第一个回答",
            additional_kwargs={"reasoning_content": "第一段思考"},
        ),
        HumanMessage(content="第二个问题"),
        AIMessage(
            content="第二个回答",
            additional_kwargs={"reasoning_content": "第二段思考"},
        ),
    ]

    payload = model._get_request_payload(messages)
    assistants = _assistant_dicts(payload)

    assert len(assistants) == 2
    assert assistants[0]["reasoning_content"] == "第一段思考"
    assert assistants[1]["reasoning_content"] == "第二段思考"


def test_assistant_without_reasoning_is_left_alone(model: PatchedChatDeepSeek) -> None:
    messages = [
        HumanMessage(content="hi"),
        AIMessage(content="hello"),
    ]

    payload = model._get_request_payload(messages)
    assistants = _assistant_dicts(payload)

    assert len(assistants) == 1
    assert "reasoning_content" not in assistants[0]


def test_system_message_does_not_shift_alignment(model: PatchedChatDeepSeek) -> None:
    """When the agent prepends a system prompt, payload length changes but the
    fallback path matches assistant-by-assistant rather than by index."""
    messages = [
        SystemMessage(content="You are TradeMind."),
        HumanMessage(content="查客户数量"),
        AIMessage(
            content="加载技能",
            additional_kwargs={"reasoning_content": "需要 SKILL.md"},
        ),
    ]

    payload = model._get_request_payload(messages)
    assistants = _assistant_dicts(payload)

    assert len(assistants) == 1
    assert assistants[0]["reasoning_content"] == "需要 SKILL.md"


def test_tool_and_human_messages_are_untouched(model: PatchedChatDeepSeek) -> None:
    messages = [
        HumanMessage(content="hi"),
        AIMessage(
            content="ok",
            additional_kwargs={"reasoning_content": "thinking"},
            tool_calls=[
                {"name": "ls", "args": {"path": "/"}, "id": "c1", "type": "tool_call"}
            ],
        ),
        ToolMessage(content="bin\nusr", tool_call_id="c1"),
    ]

    payload = model._get_request_payload(messages)

    user_dict = next(m for m in payload["messages"] if m["role"] == "user")
    tool_dict = next(m for m in payload["messages"] if m["role"] == "tool")

    assert "reasoning_content" not in user_dict
    assert "reasoning_content" not in tool_dict
