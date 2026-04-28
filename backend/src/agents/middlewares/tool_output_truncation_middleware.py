"""Truncate oversized ``ToolMessage`` payloads and spill the original to a file.

Why
---
``langchain``'s ``SummarizationMiddleware`` calls ``trim_messages(...,
max_tokens=trim_tokens_to_summarize, strategy="last", start_on="human")``
when preparing the input to the summarizer LLM. If a single message is
larger than the trim budget, ``trim_messages`` returns an empty list and
the summarizer short-circuits to a fallback string. Without
``SafeSummarizationMiddleware`` that path used to wipe the entire thread
history (see ``safe_summarization_middleware.py`` and thread
``09417ecf-...``).

Even with the safe wrapper, an oversized message means summarization
*aborts*, which means the next call hits the same trigger again, churns
the same trim/abort, and the conversation just keeps growing — eventually
busting the model window. The robust fix is to ensure no single
``ToolMessage`` ever exceeds ``trim_tokens_to_summarize / N``: this
middleware caps content size and writes the original to a sandbox file
the model can read on demand.

How
---
1. Wrap ``ToolNode`` via ``wrap_tool_call`` (after
   ``ToolErrorHandlingMiddleware`` so even error messages get capped).
2. After the tool runs, inspect the returned ``ToolMessage``. If the
   *string* portion of ``content`` exceeds ``max_chars``:

   a. Write the original full payload to
      ``/mnt/user-data/workspace/<spill_dir>/<tool_call_id>.txt`` via the
      sandbox provider (so it lands at the right physical thread path).
   b. Replace ``content`` with ``head + truncation_note + tail``. The
      note tells the model the file path and exact byte count so it can
      ``read_file`` / ``bash cat`` for specific portions if needed.
   c. Stash diagnostic metadata in ``additional_kwargs.tool_output_truncation``
      for the frontend (so it can render a "📄 full output" link).

3. List-shaped ``content`` (multi-modal blocks) is handled per-block —
   only ``text`` blocks are truncated; ``image_url`` and other blocks
   pass through untouched.

4. Never hard-fails. If the sandbox write fails, the message still gets
   in-place truncated (with a degraded note saying the spill failed) so
   we never propagate an oversized message into state.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from src.config.tool_output_config import (
    ToolOutputTruncationConfig,
    get_tool_output_config,
)
from src.sandbox import get_sandbox_provider

logger = logging.getLogger(__name__)


_TRUNCATION_FLAG = "tool_output_truncation"
_WORKSPACE_PATH = "/mnt/user-data/workspace"


def _spill_path(spill_dir: str, tool_call_id: str) -> str:
    """Compose the virtual sandbox path where the full payload lives."""
    safe_id = tool_call_id.replace("/", "_").replace("\\", "_") or "unknown"
    return f"{_WORKSPACE_PATH}/{spill_dir.strip('/')}/{safe_id}.txt"


def _build_truncation_note(
    *,
    original_chars: int,
    spill_path: str | None,
    tool_name: str,
) -> str:
    """The marker block inserted between head and tail of a truncated payload."""
    if spill_path:
        return f"\n\n... [TRUNCATED: {original_chars} chars in original {tool_name!r} output. Full content saved to {spill_path} — use bash 'cat' or read_file to inspect specific portions if needed.] ...\n\n"
    return f"\n\n... [TRUNCATED: {original_chars} chars in original {tool_name!r} output. Sandbox unavailable, full content was not preserved — work with head/tail below or re-run a more targeted query.] ...\n\n"


def _truncate_string(
    content: str,
    *,
    keep_head_chars: int,
    keep_tail_chars: int,
    spill_path: str | None,
    tool_name: str,
) -> str:
    """Stitch ``head + truncation_note + tail`` for the message body."""
    head = content[:keep_head_chars]
    tail = content[-keep_tail_chars:] if keep_tail_chars > 0 else ""
    note = _build_truncation_note(
        original_chars=len(content),
        spill_path=spill_path,
        tool_name=tool_name,
    )
    return head + note + tail


def _content_size(content: Any) -> int:
    """Total character count of stringy parts of ``content``.

    Multimodal content (list of blocks) only contributes ``text`` blocks
    to the size — image blocks etc. are bounded by their own URL and
    don't push us into trim-failure territory.
    """
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        total = 0
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                total += len(block.get("text", "") or "")
            elif isinstance(block, str):
                total += len(block)
        return total
    return len(str(content))


def _truncate_block_list(
    blocks: list,
    *,
    keep_head_chars: int,
    keep_tail_chars: int,
    spill_path: str | None,
    tool_name: str,
) -> list:
    """Truncate the largest text block in a multimodal content list.

    Strategy: locate the single biggest text block (the others are likely
    small captions / instructions), truncate just that block, and pass
    every other block through. This preserves image references intact.
    """
    if not blocks:
        return blocks
    biggest_idx = -1
    biggest_len = 0
    for i, block in enumerate(blocks):
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text", "") or ""
            if len(text) > biggest_len:
                biggest_len = len(text)
                biggest_idx = i
    if biggest_idx < 0:
        return blocks  # nothing string-shaped to shrink

    new_blocks = list(blocks)
    original_text = new_blocks[biggest_idx].get("text", "")
    new_blocks[biggest_idx] = {
        **new_blocks[biggest_idx],
        "text": _truncate_string(
            original_text,
            keep_head_chars=keep_head_chars,
            keep_tail_chars=keep_tail_chars,
            spill_path=spill_path,
            tool_name=tool_name,
        ),
    }
    return new_blocks


def _serialize_for_spill(content: Any) -> str:
    """Render ``content`` to a single string for the spill file."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text", "") or "")
                else:
                    # Non-text blocks: render as compact JSON-ish hint so the
                    # spill file is still self-describing.
                    parts.append(f"[non-text block: {block.get('type', 'unknown')}]")
            else:
                parts.append(str(block))
        return "\n".join(parts)
    return str(content)


class ToolOutputTruncationMiddleware(AgentMiddleware[AgentState]):
    """Cap individual ``ToolMessage`` content size, spill original to disk."""

    def __init__(self, config: ToolOutputTruncationConfig | None = None):
        super().__init__()
        self._config = config or get_tool_output_config()

    # ── public hooks ─────────────────────────────────────────────────

    @override
    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        result = handler(request)
        return self._maybe_truncate(result, request)

    @override
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        result = await handler(request)
        return self._maybe_truncate(result, request)

    # ── core logic ───────────────────────────────────────────────────

    def _maybe_truncate(
        self,
        result: ToolMessage | Command,
        request: ToolCallRequest,
    ) -> ToolMessage | Command:
        cfg = self._config
        if not cfg.enabled:
            return result
        if not isinstance(result, ToolMessage):
            return result  # Command / control-flow returns are passed through

        tool_name = str(request.tool_call.get("name") or "unknown_tool")
        if tool_name in cfg.skip_tool_names:
            return result

        # Don't double-truncate on resume / replay paths.
        if (result.additional_kwargs or {}).get(_TRUNCATION_FLAG):
            return result

        size = _content_size(result.content)
        if size <= cfg.max_chars:
            return result

        tool_call_id = str(request.tool_call.get("id") or "unknown")
        spill_path = _spill_path(cfg.spill_dir, tool_call_id)
        spill_succeeded = self._spill_to_sandbox(request, spill_path, _serialize_for_spill(result.content))
        spill_path_for_note = spill_path if spill_succeeded else None

        # Apply truncation in the appropriate content shape.
        if isinstance(result.content, list):
            new_content: Any = _truncate_block_list(
                result.content,
                keep_head_chars=cfg.keep_head_chars,
                keep_tail_chars=cfg.keep_tail_chars,
                spill_path=spill_path_for_note,
                tool_name=tool_name,
            )
        else:
            new_content = _truncate_string(
                result.content if isinstance(result.content, str) else str(result.content),
                keep_head_chars=cfg.keep_head_chars,
                keep_tail_chars=cfg.keep_tail_chars,
                spill_path=spill_path_for_note,
                tool_name=tool_name,
            )

        new_additional_kwargs = dict(result.additional_kwargs or {})
        new_additional_kwargs[_TRUNCATION_FLAG] = {
            "original_chars": size,
            "spill_path": spill_path_for_note,
            "tool_name": tool_name,
        }

        logger.warning(
            "Truncated oversized ToolMessage: tool=%s id=%s original_chars=%d kept_head=%d kept_tail=%d spill=%s",
            tool_name,
            tool_call_id,
            size,
            cfg.keep_head_chars,
            cfg.keep_tail_chars,
            spill_path_for_note or "FAILED",
        )

        return result.model_copy(
            update={
                "content": new_content,
                "additional_kwargs": new_additional_kwargs,
            }
        )

    def _spill_to_sandbox(
        self,
        request: ToolCallRequest,
        spill_path: str,
        content: str,
    ) -> bool:
        """Write ``content`` to ``spill_path`` via the sandbox provider.

        Returns True on success, False on any failure (logged at warning).
        We never raise: a failed spill must not break the tool call —
        in-place truncation alone is still strictly better than letting
        the oversized message poison summarization.
        """
        sandbox_state: dict | None = None
        runtime = getattr(request, "runtime", None)
        if runtime is not None:
            state = getattr(runtime, "state", None)
            if isinstance(state, dict):
                sandbox_state = state.get("sandbox")
        if not sandbox_state:
            logger.warning("Cannot spill oversized tool output: sandbox state missing on runtime")
            return False
        sandbox_id = sandbox_state.get("sandbox_id")
        if not sandbox_id:
            logger.warning("Cannot spill oversized tool output: sandbox_id missing in sandbox state")
            return False
        try:
            sandbox = get_sandbox_provider().get(sandbox_id)
        except Exception:  # noqa: BLE001 — provider lookups have varied error types
            logger.warning(
                "Cannot spill oversized tool output: sandbox provider lookup failed",
                exc_info=True,
            )
            return False
        if sandbox is None:
            logger.warning(
                "Cannot spill oversized tool output: sandbox %s not found in provider",
                sandbox_id,
            )
            return False
        try:
            sandbox.write_file(spill_path, content)
        except Exception:  # noqa: BLE001 — write failures must not propagate
            logger.warning(
                "Spill write failed for %s; falling back to in-place truncation only",
                spill_path,
                exc_info=True,
            )
            return False
        return True
