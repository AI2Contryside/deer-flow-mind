"""Unit tests for ClarificationMiddleware.

Two concern areas:

1. ``_format_clarification_message`` — the canonical text representation a
   non-widget client (older frontend, IM channel) sees in chat history. The
   output must:
     - Stay readable for non-widget clients.
     - Be deterministic enough for the frontend's stripping regex to remove
       it once the rich widget is rendered.

2. ``_handle_clarification`` — the LangGraph ``interrupt()`` flow. On the
   first call it must raise ``GraphInterrupt`` with a structured payload so
   the gateway can route the user's reply back to the same thread via
   ``Command(resume=...)``. On the second call (post-resume) it must commit
   ``ToolMessage(question) + HumanMessage(answer)`` so the chat history shape
   matches what FE renderers and IM channels already understand.
"""

from typing import Any
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.errors import GraphInterrupt
from langgraph.types import Command

from src.agents.middlewares.clarification_middleware import ClarificationMiddleware


@pytest.fixture
def anyio_backend() -> str:
    # Restrict ``@pytest.mark.anyio`` tests to the asyncio backend; trio is
    # not a dependency of this project and pulling it in just to satisfy the
    # default backend matrix would be wasted infra.
    return "asyncio"


def _format(args: dict) -> str:
    return ClarificationMiddleware()._format_clarification_message(args)


# --- _format_clarification_message ----------------------------------------


def test_format_question_only_uses_default_icon():
    out = _format({"question": "请问您要继续吗?", "clarification_type": "missing_info"})

    assert out.startswith("❓ ")
    assert "请问您要继续吗?" in out


def test_format_renders_context_before_question():
    out = _format(
        {
            "question": "请问您的姓名、年龄和性别是什么?",
            "clarification_type": "missing_info",
            "context": "需要了解您的基本信息以便更好地为您服务。",
        }
    )

    # Context comes first, blank line, then the question.
    assert out.startswith("❓ 需要了解您的基本信息以便更好地为您服务。")
    assert "\n请问您的姓名、年龄和性别是什么?" in out


def test_format_options_use_numbered_list():
    out = _format(
        {
            "question": "您希望按哪种方式处理?",
            "clarification_type": "approach_choice",
            "options": ["保留原数据", "整体覆盖", "增量合并"],
        }
    )

    assert "🔀 您希望按哪种方式处理?" in out
    assert "  1. 保留原数据" in out
    assert "  2. 整体覆盖" in out
    assert "  3. 增量合并" in out


def test_format_fields_labels_appear_with_required_marker():
    out = _format(
        {
            "question": "请补充客户信息。",
            "clarification_type": "missing_info",
            "fields": [
                {"label": "公司名称", "type": "text", "required": True},
                {"label": "成立年份", "type": "number", "placeholder": "如 2018"},
            ],
        }
    )

    assert "  · 公司名称 *" in out
    # Non-required fields lack the asterisk; the type hint is parenthesised.
    assert "  · 成立年份（number）" in out


def test_format_fields_render_select_options_inline():
    out = _format(
        {
            "question": "请补充信息。",
            "clarification_type": "missing_info",
            "fields": [
                {
                    "label": "主要市场",
                    "type": "multiselect",
                    "options": ["北美", "欧洲", "东南亚", "中东"],
                }
            ],
        }
    )

    # multiselect type + options hint are merged into a single (…) suffix.
    assert "  · 主要市场（multiselect；选项：北美/欧洲/东南亚/中东）" in out


def test_format_fields_truncate_option_list_when_long():
    out = _format(
        {
            "question": "选择品类。",
            "clarification_type": "missing_info",
            "fields": [
                {
                    "label": "品类",
                    "type": "select",
                    "options": [f"选项{i}" for i in range(8)],
                }
            ],
        }
    )

    # Only the first 5 options appear, then an ellipsis. Keeps the inline
    # hint short for non-widget clients.
    assert "选项0/选项1/选项2/选项3/选项4/…" in out


def test_format_fields_skip_malformed_entries():
    # Entries missing a label or that aren't dicts are silently dropped so a
    # bad agent payload can't blow up the chat.
    out = _format(
        {
            "question": "请补充信息。",
            "clarification_type": "missing_info",
            "fields": [
                {"label": "姓名"},
                {"type": "text"},  # no label → dropped
                "not a dict",  # wrong type → dropped
                {"label": "  ", "type": "text"},  # empty label → dropped
                {"label": "电话"},
            ],
        }
    )

    assert "  · 姓名" in out
    assert "  · 电话" in out
    # Only two rendered field lines exist.
    assert out.count("  · ") == 2


def test_format_no_fields_no_options_returns_question_only():
    out = _format({"question": "怎么继续?", "clarification_type": "ambiguous_requirement"})

    # No trailing options/field block; the icon picks up ambiguous_requirement.
    assert out == "🤔 怎么继续?"


# --- _handle_clarification (interrupt + resume flow) ----------------------


def _make_request(args: dict | None = None, tool_call_id: str = "call_123") -> Any:
    """Build a minimal ToolCallRequest stub.

    The middleware only reads ``request.tool_call``, so a MagicMock with that
    attribute is sufficient — pulling in the real ``ToolCallRequest`` would
    require the rest of the LangGraph runtime which is overkill here.
    """
    request = MagicMock()
    request.tool_call = {
        "name": "ask_clarification",
        "args": args or {"question": "请补充信息?", "clarification_type": "missing_info"},
        "id": tool_call_id,
    }
    return request


def test_handle_clarification_calls_interrupt_with_payload(monkeypatch):
    # First-call semantics: ``interrupt(payload)`` must be invoked with the
    # structured payload that the gateway / FE / IM channel will read off
    # ``thread.interrupts``. We replace the imported ``interrupt`` symbol
    # with a stub that raises ``GraphInterrupt`` so the failure mode (no
    # return value) matches what real LangGraph does inside a paused run.
    from src.agents.middlewares import clarification_middleware as cm

    captured: dict[str, Any] = {}

    def fake_interrupt(payload):
        captured["payload"] = payload
        raise GraphInterrupt(())

    monkeypatch.setattr(cm, "interrupt", fake_interrupt)

    middleware = ClarificationMiddleware()
    request = _make_request(
        {
            "question": "怎么继续?",
            "clarification_type": "approach_choice",
            "options": ["A", "B"],
        },
        tool_call_id="call_xyz",
    )

    with pytest.raises(GraphInterrupt):
        middleware._handle_clarification(request)

    payload = captured["payload"]
    assert payload["type"] == "ask_clarification"
    assert payload["tool_call_id"] == "call_xyz"
    assert payload["args"] == request.tool_call["args"]
    # formatted_question gives non-widget clients (IM channels, older FE) a
    # readable rendering without parsing the args.
    assert payload["formatted_question"].startswith("🔀 怎么继续?")
    assert "  1. A" in payload["formatted_question"]


def test_build_resume_messages_with_answer_emits_tool_then_human():
    middleware = ClarificationMiddleware()
    msgs = middleware._build_resume_messages(
        formatted_question="❓ 怎么继续?",
        tool_call_id="call_42",
        answer="按方案 A",
    )

    assert len(msgs) == 2
    tool, human = msgs
    assert isinstance(tool, ToolMessage)
    # The ToolMessage carries the formatted question (closing the original
    # tool_call) so non-widget clients can replay the conversation.
    assert tool.content == "❓ 怎么继续?"
    assert tool.tool_call_id == "call_42"
    assert tool.name == "ask_clarification"
    assert isinstance(human, HumanMessage)
    assert human.content == "按方案 A"


def test_build_resume_messages_strips_whitespace_only_answers():
    # An answer of whitespace is treated as empty — committing it as a
    # HumanMessage would just clutter history without giving the model any
    # signal. Only the ToolMessage gets emitted so the protocol invariant
    # (every tool_call.id has a matching ToolMessage) holds.
    middleware = ClarificationMiddleware()
    msgs = middleware._build_resume_messages(
        formatted_question="❓ Q",
        tool_call_id="call_42",
        answer="   \n  ",
    )

    assert len(msgs) == 1
    assert isinstance(msgs[0], ToolMessage)


def test_build_resume_messages_with_none_answer_emits_only_tool_message():
    # ``Command(resume=None)`` is the legitimate "user skipped" path. We
    # must still close the tool_call with a ToolMessage; otherwise the next
    # supersection runs into a dangling tool_call and DanglingToolCallMiddleware
    # has to repair it.
    middleware = ClarificationMiddleware()
    msgs = middleware._build_resume_messages(
        formatted_question="❓ Q",
        tool_call_id="call_42",
        answer=None,
    )

    assert len(msgs) == 1
    assert isinstance(msgs[0], ToolMessage)
    assert msgs[0].tool_call_id == "call_42"


def test_build_resume_messages_coerces_non_string_answers():
    # ``Command(resume=...)`` accepts arbitrary values. Coerce sensibly so
    # the model still gets a readable HumanMessage instead of a Python repr
    # leak.
    middleware = ClarificationMiddleware()
    msgs = middleware._build_resume_messages(
        formatted_question="❓ Q",
        tool_call_id="call_42",
        answer=42,
    )

    assert len(msgs) == 2
    assert isinstance(msgs[1], HumanMessage)
    assert msgs[1].content == "42"


def test_wrap_tool_call_passes_through_non_clarification_calls():
    # Only ``ask_clarification`` is intercepted; every other tool runs
    # untouched. Regression guard against accidental over-broad matches in
    # the dispatch check.
    middleware = ClarificationMiddleware()
    request = MagicMock()
    request.tool_call = {"name": "bash", "args": {"command": "ls"}, "id": "call_1"}
    sentinel = ToolMessage(content="ok", tool_call_id="call_1")

    handler = MagicMock(return_value=sentinel)
    result = middleware.wrap_tool_call(request, handler)

    assert result is sentinel
    handler.assert_called_once_with(request)


def test_wrap_tool_call_routes_clarification_through_handler(monkeypatch):
    from src.agents.middlewares import clarification_middleware as cm

    monkeypatch.setattr(cm, "interrupt", lambda _p: (_ for _ in ()).throw(GraphInterrupt(())))

    middleware = ClarificationMiddleware()
    request = _make_request()
    handler = MagicMock()  # must NOT be called for ask_clarification

    with pytest.raises(GraphInterrupt):
        middleware.wrap_tool_call(request, handler)

    # The middleware short-circuits before invoking the underlying tool;
    # without this guarantee, the real ``ask_clarification`` placeholder
    # function would run and pollute history with its dummy return value.
    handler.assert_not_called()


@pytest.mark.anyio
async def test_awrap_tool_call_passes_through_non_clarification_calls():
    middleware = ClarificationMiddleware()
    request = MagicMock()
    request.tool_call = {"name": "bash", "args": {}, "id": "call_1"}
    sentinel = ToolMessage(content="ok", tool_call_id="call_1")

    async def handler(_req: Any) -> ToolMessage:
        return sentinel

    result = await middleware.awrap_tool_call(request, handler)

    assert result is sentinel


@pytest.mark.anyio
async def test_awrap_tool_call_routes_clarification_through_handler(monkeypatch):
    from src.agents.middlewares import clarification_middleware as cm

    def _raise(_payload):
        raise GraphInterrupt(())

    monkeypatch.setattr(cm, "interrupt", _raise)

    middleware = ClarificationMiddleware()
    request = _make_request()

    async def handler(_req: Any) -> ToolMessage:  # pragma: no cover — must not run
        raise AssertionError("clarification handler must not invoke the underlying tool")

    with pytest.raises(GraphInterrupt):
        await middleware.awrap_tool_call(request, handler)


def test_ask_clarification_tool_is_not_return_direct():
    # Regression guard: with the interrupt()/resume design, the tools→model
    # edge in langchain's create_agent (factory.py `_make_tools_to_model_edge`)
    # routes to END when EVERY client-side tool call on the last AIMessage has
    # return_direct=True. If ask_clarification is flagged return_direct, then
    # after the user submits an answer and ClarificationMiddleware commits the
    # ToolMessage + HumanMessage, the agent exits instead of letting the model
    # react — i.e. the model silently never replies to the clarification
    # answer. Keep return_direct off so the loop continues into the model node.
    # Import the leaf module directly: src.tools.builtins.__init__ pulls in
    # present_file_tool, which in turn imports the oss2 SDK. That dependency
    # isn't installed in the test env (and shouldn't be, for a unit test of a
    # config flag), so we sidestep the package-level re-export.
    from src.tools.builtins.clarification_tool import ask_clarification_tool

    assert ask_clarification_tool.return_direct is False, "ask_clarification must NOT be return_direct — see ClarificationMiddleware docstring + clarification_tool.py comment for why."


def test_handle_clarification_returns_command_when_interrupt_resumes(monkeypatch):
    # Simulate the resume path: re-execution of ``_handle_clarification``
    # where ``interrupt()`` returns the resume value rather than raising.
    # We can't easily drive a real LangGraph run from a unit test, so patch
    # the ``interrupt`` symbol the middleware imported.
    from src.agents.middlewares import clarification_middleware as cm

    monkeypatch.setattr(cm, "interrupt", lambda _payload: "用户选 A")

    middleware = ClarificationMiddleware()
    result = middleware._handle_clarification(
        _make_request(
            {"question": "怎么继续?", "clarification_type": "approach_choice", "options": ["A", "B"]},
            tool_call_id="call_resume",
        )
    )

    assert isinstance(result, Command)
    update = result.update
    assert isinstance(update, dict)
    messages = update["messages"]
    assert len(messages) == 2
    tool, human = messages
    assert isinstance(tool, ToolMessage)
    assert tool.tool_call_id == "call_resume"
    assert tool.name == "ask_clarification"
    assert "怎么继续?" in tool.content
    assert isinstance(human, HumanMessage)
    assert human.content == "用户选 A"
