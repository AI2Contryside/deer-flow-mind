"""Middleware to fix dangling tool calls in message history.

A dangling tool call occurs when an AIMessage carries ``tool_calls`` but the
conversation has already moved past it without producing a matching
``ToolMessage`` for every ``tool_call.id`` (typical cause: the user cancelled
the run mid-tool, leaving the persisted state with an unfinished tool call).

Why this matters
----------------
The OpenAI / DeepSeek tool-calling protocol requires that every
``tool_call.id`` on an AIMessage be answered by a ToolMessage with the
**same** ``tool_call_id`` immediately after it. The model cannot influence
``tool_call.id`` — it is server-generated — so the model can never produce a
"matching" retry on its own. A dangling tool_call therefore wedges the
conversation: the model sees an unanswered call, decides to "retry", emits a
new tool_call with a *new* id, the new call gets a real ToolMessage, but the
original dangling id stays orphan in state forever. On the next superstep the
dangling call is still there → the model retries again → infinite loop until
the LangGraph recursion limit (25 by default) aborts the run.

What this middleware does (and does NOT do)
-------------------------------------------
We **rewrite the persisted state once** so the dangling call is gone for good:

  - Find every AIMessage whose ``tool_calls`` contains an id with no
    corresponding ToolMessage downstream.
  - Replace that AIMessage *in place* (same ``id`` → ``add_messages``
    reducer swaps it at its existing position; OpenAI's ordering invariant
    holds).
  - Clear ``tool_calls`` / ``invalid_tool_calls`` / the raw mirror in
    ``additional_kwargs`` so the protocol no longer demands a matching
    ToolMessage. Append a short note to ``content`` telling the model the
    call was cancelled and **must not** be retried.

The earlier version of this middleware patched ``request.messages`` in
``wrap_model_call`` only — model-visible but not persisted. That just
re-injected the same placeholder every superstep, which the model read as
"still interrupted, retry again". See thread
``f0829e23-15f8-432a-8229-4466b5d59d7f`` for the failure mode.
"""

import logging
from collections.abc import Awaitable, Callable
from typing import Any, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelCallResult, ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.runtime import Runtime

logger = logging.getLogger(__name__)


_CANCELLED_NOTE = (
    "\n\n[Note: a tool call was issued earlier in this turn but did not return "
    "a result before the conversation continued — most likely the user cancelled "
    "it. Treat the call as already resolved. Do NOT retry it. Continue based on "
    "the latest user message.]"
)


def _content_with_note(content: Any) -> Any:
    """Append the cancellation note while preserving the original content shape.

    LangChain ``AIMessage.content`` can be a plain string or a list of
    content blocks (Anthropic / DeepSeek style). We append the note as a
    text block when the original is a list, otherwise concatenate as
    string. Anything else is coerced to ``str`` and concatenated — losing
    a content-shape edge case is preferable to losing the note.
    """
    if isinstance(content, str):
        return content + _CANCELLED_NOTE
    if isinstance(content, list):
        return list(content) + [{"type": "text", "text": _CANCELLED_NOTE.lstrip("\n")}]
    return str(content) + _CANCELLED_NOTE


def _find_dangling_repairs(messages: list) -> list[AIMessage]:
    """Return AIMessage replacements for every dangling AIMessage in ``messages``.

    Each returned message keeps the original ``id`` so the ``add_messages``
    reducer replaces it in place rather than appending a duplicate at the
    tail (which would violate OpenAI's strict ordering for tool_calls).
    """
    existing_tool_msg_ids: set[str] = set()
    for msg in messages:
        if isinstance(msg, ToolMessage):
            existing_tool_msg_ids.add(msg.tool_call_id)

    repairs: list[AIMessage] = []
    for msg in messages:
        if not isinstance(msg, AIMessage):
            continue
        tool_calls = getattr(msg, "tool_calls", None) or []
        if not tool_calls:
            continue
        # An AIMessage is "dangling" iff *any* of its tool_calls lacks a
        # matching ToolMessage. We don't try to repair partial fan-outs by
        # filtering individual ids — protocol-wise, dropping all tool_calls
        # is simpler and equally correct: the message reads as a plain text
        # turn that happened to mention some intent.
        if not any(tc.get("id") and tc["id"] not in existing_tool_msg_ids for tc in tool_calls):
            continue

        # Strip the raw OpenAI tool_calls mirror in additional_kwargs too —
        # otherwise LangChain re-derives ``tool_calls`` from it on the next
        # message refresh and the dangling state silently comes back.
        new_additional_kwargs = dict(msg.additional_kwargs or {})
        new_additional_kwargs.pop("tool_calls", None)

        repairs.append(
            msg.model_copy(
                update={
                    "content": _content_with_note(msg.content),
                    "tool_calls": [],
                    "invalid_tool_calls": [],
                    "additional_kwargs": new_additional_kwargs,
                }
            )
        )
    return repairs


class DanglingToolCallMiddleware(AgentMiddleware[AgentState]):
    """Repair AIMessages whose ``tool_calls`` have no matching ToolMessage.

    Runs in ``before_model`` so the repair is persisted to thread state via
    the ``add_messages`` reducer (same-id replacement). Once a dangling
    call is repaired, subsequent supersteps see clean state and this
    middleware no-ops.
    """

    def _repair(self, state: AgentState) -> dict | None:
        repairs = _find_dangling_repairs(state.get("messages", []))
        if not repairs:
            return None
        logger.warning(
            "Repairing %d dangling AIMessage tool_call(s); clearing tool_calls and persisting cancellation note to state",
            len(repairs),
        )
        return {"messages": repairs}

    @override
    def before_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        return self._repair(state)

    @override
    async def abefore_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        return self._repair(state)

    # ── Belt-and-braces: also patch the request payload on the way in ──
    #
    # ``before_model`` writes the repair to state, but the same superstep's
    # model call uses the request that was assembled *before* state was
    # updated. Without this wrap, the very first model call after a repair
    # could still go out with a dangling history and trigger a 400 from
    # OpenAI / DeepSeek. From the next superstep onward the wrap is a
    # no-op (state is already clean).

    def _patch_request(self, request: ModelRequest) -> ModelRequest:
        repairs = _find_dangling_repairs(list(request.messages))
        if not repairs:
            return request
        repairs_by_id = {m.id: m for m in repairs if m.id}
        patched = [repairs_by_id.get(m.id, m) for m in request.messages]
        return request.override(messages=patched)

    @override
    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelCallResult:
        return handler(self._patch_request(request))

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelCallResult:
        return await handler(self._patch_request(request))
