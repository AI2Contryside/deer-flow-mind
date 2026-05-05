"""Tests for RepeatedToolFailureMiddleware.

Pinned against session b987fdbe-...: the same Frappe ``BrokenPipeError``
came back from msg 27 onward and the agent re-tried it 8 more times
across both ``erpnext.py doc insert`` and raw ``curl`` paths. The guard
must stop on the third repeat with a generic user-facing message and
route to END.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.types import Command

from src.agents.middlewares.repeated_tool_failure_middleware import (
    RepeatedToolFailureMiddleware,
    _count_trailing_signature_repeats,
    _failure_signature,
)


def _tool_msg(name: str, content: str, status: str | None = None, tool_call_id: str = "tc-1") -> ToolMessage:
    kwargs = {"content": content, "tool_call_id": tool_call_id, "name": name}
    if status is not None:
        kwargs["status"] = status
    return ToolMessage(**kwargs)


def _request_with_history(messages: list, tool_call_name: str = "bash", tool_call_id: str = "tc-new"):
    """Minimal ToolCallRequest stand-in. The middleware reads request.tool_call
    and request.state.messages — we expose both via SimpleNamespace.
    """
    return SimpleNamespace(
        tool_call={"id": tool_call_id, "name": tool_call_name, "args": {}},
        state={"messages": messages},
    )


# ---------- _failure_signature ---------------------------------------------


@pytest.mark.unit
def test_signature_none_for_success() -> None:
    msg = _tool_msg("bash", '{"ok": true, "data": [...]}', status=None)
    assert _failure_signature(msg) is None


@pytest.mark.unit
def test_signature_extracts_error_class_from_b987fdbe_payload() -> None:
    # Verbatim payload shape from session b987fdbe msg 27/35.
    payload = '{ "ok": false, "error": { "error": "ServerError", "message": "BrokenPipeError: [Errno 32] Broken pipe", "status_code": 500 } }'
    sig = _failure_signature(_tool_msg("bash", payload))
    assert sig is not None
    assert "bash::" in sig
    # First class match wins (ServerError appears before BrokenPipeError).
    assert sig.endswith("ServerError")


@pytest.mark.unit
def test_signature_falls_back_to_status_code() -> None:
    sig = _failure_signature(_tool_msg("bash", '"status_code": 500\nFailure', status="error"))
    assert sig == "bash::http_500"


@pytest.mark.unit
def test_signature_uses_explicit_status_field() -> None:
    sig = _failure_signature(_tool_msg("read_file", "no useful tokens", status="error"))
    assert sig == "read_file::error"


# ---------- _count_trailing_signature_repeats ------------------------------


@pytest.mark.unit
def test_count_only_counts_trailing_matches() -> None:
    msgs = [
        HumanMessage(content="hi"),
        _tool_msg("bash", "ServerError", tool_call_id="a"),
        _tool_msg("bash", "ServerError", tool_call_id="b"),
        AIMessage(content="thinking"),  # interleaved AIMessage doesn't break streak
        _tool_msg("bash", "ServerError", tool_call_id="c"),
    ]
    assert _count_trailing_signature_repeats(msgs, "bash::ServerError") == 3


@pytest.mark.unit
def test_count_resets_on_success() -> None:
    msgs = [
        _tool_msg("bash", "ServerError", tool_call_id="a"),
        _tool_msg("bash", "ServerError", tool_call_id="b"),
        _tool_msg("bash", '{"ok": true}', tool_call_id="c"),  # success
        _tool_msg("bash", "ServerError", tool_call_id="d"),
    ]
    assert _count_trailing_signature_repeats(msgs, "bash::ServerError") == 1


@pytest.mark.unit
def test_count_resets_on_different_signature() -> None:
    msgs = [
        _tool_msg("bash", "ServerError", tool_call_id="a"),
        _tool_msg("bash", "ServerError", tool_call_id="b"),
        _tool_msg("bash", "ValidationError", tool_call_id="c"),
    ]
    assert _count_trailing_signature_repeats(msgs, "bash::ServerError") == 0


# ---------- middleware abort behaviour -------------------------------------


@pytest.mark.unit
def test_middleware_passes_through_success() -> None:
    mw = RepeatedToolFailureMiddleware()
    success = _tool_msg("bash", '{"ok": true, "data": []}')
    request = _request_with_history(messages=[])

    out = mw._maybe_abort(request, success)
    assert out is success


@pytest.mark.unit
def test_middleware_passes_first_two_failures() -> None:
    mw = RepeatedToolFailureMiddleware()
    history = [_tool_msg("bash", "ServerError", tool_call_id="t1")]
    request = _request_with_history(history)
    second = _tool_msg("bash", "ServerError", tool_call_id="t2")

    out = mw._maybe_abort(request, second)
    assert out is second  # passes through, not yet at threshold


@pytest.mark.unit
def test_middleware_aborts_on_third_consecutive_same_failure() -> None:
    mw = RepeatedToolFailureMiddleware()
    history = [
        _tool_msg("bash", "ServerError", tool_call_id="t1"),
        _tool_msg("bash", "ServerError", tool_call_id="t2"),
    ]
    request = _request_with_history(history, tool_call_id="t3")
    third = _tool_msg("bash", "ServerError", tool_call_id="t3")

    out = mw._maybe_abort(request, third)
    assert isinstance(out, Command)
    # Routes to END so the agent stops.
    assert out.goto == "__end__"
    # Emits a synthetic ToolMessage satisfying the open tool_call_id.
    update_msgs = out.update["messages"]
    assert len(update_msgs) == 1
    assert update_msgs[0].tool_call_id == "t3"
    # Generic user-facing copy — no leaked stack frames or upstream URL.
    assert "已暂停操作" in update_msgs[0].content
    assert "10.37" not in update_msgs[0].content
    assert "BrokenPipe" not in update_msgs[0].content


@pytest.mark.unit
def test_middleware_does_not_abort_when_failures_differ() -> None:
    mw = RepeatedToolFailureMiddleware()
    history = [
        _tool_msg("bash", "ServerError", tool_call_id="t1"),
        _tool_msg("bash", "ValidationError", tool_call_id="t2"),
    ]
    request = _request_with_history(history, tool_call_id="t3")
    third = _tool_msg("bash", "AuthError", tool_call_id="t3")

    out = mw._maybe_abort(request, third)
    # Three failures but three different signatures — pass through.
    assert out is third


@pytest.mark.unit
def test_middleware_passes_through_command_results() -> None:
    # Other middlewares (e.g. ClarificationMiddleware) can return a
    # Command; we must not interfere with those.
    mw = RepeatedToolFailureMiddleware()
    cmd = Command(goto="__end__", update={"messages": []})
    request = _request_with_history(messages=[])
    out = mw._maybe_abort(request, cmd)
    assert out is cmd
