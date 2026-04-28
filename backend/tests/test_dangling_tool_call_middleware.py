"""Tests for ``DanglingToolCallMiddleware``.

Pinned to thread ``f0829e23-15f8-432a-8229-4466b5d59d7f``: a previous version
of this middleware patched only ``request.messages`` (per-request) and did
not persist the repair to state, so the dangling tool_call survived every
superstep and the model retried until the recursion limit aborted the run.

The contract this test fixes in stone:

  - Dangling AIMessages are repaired *in place* (same ``id``) so the
    ``add_messages`` reducer overwrites them at their existing positions
    — never appended at the tail (which would violate OpenAI's strict
    ordering for tool_calls).
  - After repair, the message has empty ``tool_calls`` /
    ``invalid_tool_calls`` and the raw OpenAI mirror in
    ``additional_kwargs`` is stripped (otherwise LangChain re-derives the
    dangling tool_calls on next refresh).
  - The cancellation note is appended to ``content`` so the model is told
    the call was cancelled and must not be retried.
  - Original ``content`` shape is preserved (str → str, list → list).
  - Clean histories are no-ops.
  - ``before_model`` and ``wrap_model_call`` agree: the wrap is a
    belt-and-braces guard for the very first model call after a repair,
    when state is clean but the in-flight request still carries the
    dangling history.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from src.agents.middlewares.dangling_tool_call_middleware import (
    _CANCELLED_NOTE,
    DanglingToolCallMiddleware,
    _find_dangling_repairs,
)


def _dangling_msg() -> AIMessage:
    return AIMessage(
        id="ai-old",
        content="让我帮您处理订单",
        tool_calls=[{"id": "call_OLD", "name": "selling_order", "args": {"customer": "X"}}],
        additional_kwargs={"tool_calls": [{"id": "call_OLD", "function": {"name": "selling_order"}}]},
    )


def _clean_pair() -> list:
    ai = AIMessage(
        id="ai-clean",
        content="",
        tool_calls=[{"id": "c1", "name": "t", "args": {}}],
    )
    tm = ToolMessage(content="ok", tool_call_id="c1", name="t")
    return [ai, tm]


def test_repairs_dangling_in_place_with_same_id():
    dangling = _dangling_msg()
    msgs = [HumanMessage(id="u1", content="hi"), dangling, *_clean_pair()]

    repairs = _find_dangling_repairs(msgs)

    assert len(repairs) == 1
    repaired = repairs[0]
    # Same id ensures add_messages reducer replaces in place rather than
    # appending — protects OpenAI's tool_call ↔ tool_message ordering.
    assert repaired.id == "ai-old"


def test_repair_clears_all_tool_call_surfaces():
    repairs = _find_dangling_repairs([_dangling_msg()])
    repaired = repairs[0]

    assert repaired.tool_calls == []
    assert repaired.invalid_tool_calls == []
    # Raw OpenAI mirror must also be stripped — LangChain re-derives
    # ``tool_calls`` from ``additional_kwargs.tool_calls`` if left intact,
    # silently undoing the repair.
    assert "tool_calls" not in repaired.additional_kwargs


def test_repair_appends_cancellation_note_and_preserves_content():
    repairs = _find_dangling_repairs([_dangling_msg()])
    repaired = repairs[0]

    assert "让我帮您处理订单" in repaired.content
    assert _CANCELLED_NOTE.strip() in repaired.content


def test_repair_preserves_list_content_shape():
    list_content = [{"type": "text", "text": "hello"}]
    dangling = AIMessage(
        id="ai-x",
        content=list_content,
        tool_calls=[{"id": "cX", "name": "t", "args": {}}],
    )

    repairs = _find_dangling_repairs([dangling])

    assert isinstance(repairs[0].content, list)
    assert any(block.get("type") == "text" and "cancelled" in block.get("text", "").lower() for block in repairs[0].content)


def test_clean_history_is_noop():
    msgs = [HumanMessage(id="u", content="x"), *_clean_pair()]

    assert _find_dangling_repairs(msgs) == []


def test_partial_fanout_repairs_full_aimessage():
    # Model emitted two parallel tool_calls; only one got a ToolMessage.
    # We repair by clearing *all* tool_calls — partial-id surgery is more
    # complex and protocol-equivalent, since the message reads as a plain
    # text turn either way.
    ai = AIMessage(
        id="ai-fanout",
        content="",
        tool_calls=[
            {"id": "call_A", "name": "t", "args": {}},
            {"id": "call_B", "name": "t", "args": {}},
        ],
    )
    tm = ToolMessage(content="ok", tool_call_id="call_A", name="t")

    repairs = _find_dangling_repairs([ai, tm])

    assert len(repairs) == 1
    assert repairs[0].tool_calls == []


def test_before_model_returns_messages_state_update():
    mw = DanglingToolCallMiddleware()
    state = {"messages": [HumanMessage(id="u", content="hi"), _dangling_msg()]}

    out = mw._repair(state)

    assert out is not None
    assert "messages" in out
    assert len(out["messages"]) == 1
    assert out["messages"][0].id == "ai-old"


def test_before_model_noop_when_clean():
    mw = DanglingToolCallMiddleware()
    state = {"messages": [HumanMessage(id="u", content="x"), *_clean_pair()]}

    assert mw._repair(state) is None


def test_wrap_request_patches_in_flight_message_list():
    """Belt-and-braces: the very first model call after ``before_model`` runs
    still uses the request that was assembled before state was updated, so
    the wrap path must independently sanitize ``request.messages`` to keep
    OpenAI / DeepSeek from rejecting the dangling history."""

    class _Req:
        def __init__(self, messages):
            self.messages = list(messages)

        def override(self, *, messages):
            return _Req(messages)

    mw = DanglingToolCallMiddleware()
    dangling = _dangling_msg()
    req = _Req([HumanMessage(id="u", content="hi"), dangling, *_clean_pair()])

    patched = mw._patch_request(req)

    # Same length, dangling message replaced at its position
    assert len(patched.messages) == len(req.messages)
    repaired = next(m for m in patched.messages if getattr(m, "id", None) == "ai-old")
    assert repaired.tool_calls == []
    assert _CANCELLED_NOTE.strip() in repaired.content
