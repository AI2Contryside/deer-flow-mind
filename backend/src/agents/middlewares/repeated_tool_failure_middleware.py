"""Middleware that bounds the agent's retry loop on repeated tool failures.

Pinned after session b987fdbe-... where the agent hit
``BrokenPipeError [Errno 32]`` from Frappe on the very first ``Company``
insert and then re-tried the same operation eight more times — switching
between ``erpnext.py doc insert`` and raw ``curl`` / ``python -c
"...requests..."`` — before exhausting recursion_limit. The 84-step
thrash burned the user's run, leaked ``Authorization: token <ak>:<sk>``
headers into chat (msgs 28, 36, 64, 66, 68, 70, 72), and produced no
business outcome.

This middleware watches every ``wrap_tool_call`` result. When the same
tool returns 3 consecutive failures **with the same failure signature**,
it short-circuits the run with a fixed user-facing message and routes
to ``END``. Successful calls or a different signature reset the counter,
so legitimate flows that mix successes with the occasional 5xx are not
affected.

Failure signature is derived from:
  - ``tool_name``
  - the tool message ``status`` (when set to ``error`` by the tool runner)
  - the first error class / status_code / dotted-path token found in the
    tool's content body (e.g. ``ServerError`` / ``500`` /
    ``BrokenPipeError``)

That last component is what makes "same failure" precise — flapping
between unrelated errors does not trip the guard, but the same Frappe
error 8 times in a row does.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from typing import override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.graph import END
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

logger = logging.getLogger(__name__)

# How many identical failures in a row trigger a forced abort. Three is
# the smallest number that distinguishes "transient hiccup" (retry-once
# is normal) from "stuck loop". Keep this in sync with the lead_agent
# prompt rule "Stop after repeated failure".
_REPEAT_FAILURE_THRESHOLD = 3

# User-visible message returned when the guard fires. Deliberately
# generic so it does not leak stack frames or upstream URLs.
_ABORT_MESSAGE = "系统暂时不可用，已暂停操作。（同一个工具连续 3 次失败，已自动停止以避免无限重试。请稍后重试，或换一种方式描述你的需求。）"

# Common upstream error tokens the tool runner / erpnext-cli surfaces. We
# extract the *first* match as the signature so wording variations of
# the same upstream failure cluster together.
_ERROR_CLASS_RE = re.compile(
    r"\b(ServerError|AuthError|NotFoundError|ValidationError|PermissionError_?|"
    r"WorkflowError|BrokenPipeError|TimeoutError|ConnectionError|"
    r"GraphRecursionError|MandatoryError|RateLimitError)\b"
)
_STATUS_CODE_RE = re.compile(r'"?status_code"?\s*[:=]\s*(\d{3})')
_HTTP_5XX_RE = re.compile(r"\b(5\d{2})\b")


def _failure_signature(msg: ToolMessage) -> str | None:
    """Return a short, normalized signature when ``msg`` is a tool failure;
    None when the call succeeded.

    Anything that looks like ``status='error'`` or contains a recognized
    error class / 5xx status code counts as a failure.
    """
    status = getattr(msg, "status", None)
    content = msg.content if isinstance(msg.content, str) else str(msg.content or "")

    failed = status == "error"
    error_token = ""

    m = _ERROR_CLASS_RE.search(content)
    if m:
        error_token = m.group(1)
        failed = True

    if not error_token:
        m = _STATUS_CODE_RE.search(content)
        if m:
            code = m.group(1)
            error_token = f"http_{code}"
            if code.startswith("5"):
                failed = True
        else:
            m = _HTTP_5XX_RE.search(content)
            if m:
                error_token = f"http_{m.group(1)}"
                failed = True

    if not failed:
        return None
    tool_name = getattr(msg, "name", "") or "unknown"
    return f"{tool_name}::{error_token or 'error'}"


def _count_trailing_signature_repeats(messages: list, signature: str) -> int:
    """Count how many of the trailing ToolMessages share ``signature``.

    Walks backward from the end of ``messages``, stopping at the first
    ToolMessage whose signature differs (success or different error).
    Non-tool messages are skipped — interleaving AIMessages does not
    reset the streak, but a successful tool call does.
    """
    count = 0
    for msg in reversed(messages):
        if not isinstance(msg, ToolMessage):
            continue
        sig = _failure_signature(msg)
        if sig is None:
            return count  # success breaks the streak
        if sig != signature:
            return count
        count += 1
    return count


class RepeatedToolFailureMiddleware(AgentMiddleware[AgentState]):
    """Abort the run when the same tool fails the same way 3 times in a row.

    Implements ``wrap_tool_call`` so it sees both the request (for the
    tool_call_id we have to satisfy) and the response (for the failure
    signature). Successful calls and signature-mismatched failures are
    passed through unchanged.

    The guard does not consult external state — everything it needs is
    already in ``messages``, so it works correctly whether the run is
    fresh, resumed from a checkpoint, or replaying a thread.
    """

    @override
    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        result = handler(request)
        return self._maybe_abort(request, result)

    @override
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        result = await handler(request)
        return self._maybe_abort(request, result)

    def _maybe_abort(
        self,
        request: ToolCallRequest,
        result: ToolMessage | Command,
    ) -> ToolMessage | Command:
        # Commands (e.g. clarification interrupts) are pass-through; this
        # middleware only counts plain tool messages.
        if not isinstance(result, ToolMessage):
            return result

        signature = _failure_signature(result)
        if signature is None:
            return result  # success — let it through

        # Pull the running message list off the request state. Different
        # runtimes expose slightly different field names; try the common
        # ones and fall back to an empty list rather than raising.
        state = getattr(request, "state", None) or {}
        messages = []
        if isinstance(state, dict):
            messages = state.get("messages") or []
        else:
            messages = getattr(state, "messages", None) or []

        # The just-produced failure isn't in `messages` yet, so we need
        # threshold - 1 trailing matches to make this the threshold-th in
        # a row.
        prior_streak = _count_trailing_signature_repeats(list(messages), signature)
        total_streak = prior_streak + 1
        if total_streak < _REPEAT_FAILURE_THRESHOLD:
            return result

        logger.warning(
            "RepeatedToolFailureMiddleware aborting run: signature=%s streak=%d (threshold=%d)",
            signature,
            total_streak,
            _REPEAT_FAILURE_THRESHOLD,
        )

        # Satisfy the tool_call_id so the message history stays well
        # formed, then route to END so the agent stops looping.
        tool_call_id = request.tool_call.get("id", "") if isinstance(request.tool_call, dict) else ""
        abort_msg = ToolMessage(
            content=_ABORT_MESSAGE,
            tool_call_id=tool_call_id,
            name=request.tool_call.get("name", "") if isinstance(request.tool_call, dict) else "",
            status="error",
        )
        return Command(update={"messages": [abort_msg]}, goto=END)
