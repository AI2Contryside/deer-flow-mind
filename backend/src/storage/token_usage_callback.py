"""LangChain callback that captures per-call token usage.

Attached to every chat model created by ``src.models.factory.create_chat_model``.
Reads ``session_id`` and ``turn_id`` from the ``RunnableConfig`` metadata that
``make_lead_agent`` (and the subagent executor) stamp at run time, then enqueues
a ``TokenUsageRecord`` for the background writer.

Inherits from ``BaseCallbackHandler`` only — every method here is non-blocking
(enqueue into a thread-safe queue), so LangChain's async dispatch can wrap the
sync hooks via ``run_in_executor`` without measurable cost. Any exception
inside the handler is logged at WARNING and swallowed; token accounting must
never break an agent run.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

from src.storage.token_usage import TokenUsageRecord, get_run_metadata, record_usage

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------


def _coerce_int(value: Any) -> int:
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _extract_model_name(message: Any, fallback: str | None) -> str:
    response_metadata = getattr(message, "response_metadata", None) or {}
    for key in ("model_name", "model", "model_id"):
        value = response_metadata.get(key)
        if value:
            return str(value)
    return fallback or "unknown"


def _build_records(
    response: LLMResult,
    session_id: str,
    turn_id: str,
    fallback_model: str | None,
) -> list[TokenUsageRecord]:
    """Aggregate ``response.generations`` into one record per model.

    ``LLMResult.generations`` is a 2-D list to accommodate batched calls; we
    sum every leaf into per-model totals so a batched call still produces one
    delta per model.
    """
    by_model: dict[str, dict[str, int]] = {}

    for gen_list in response.generations:
        for gen in gen_list:
            message = getattr(gen, "message", None)
            if message is None:
                continue
            usage = getattr(message, "usage_metadata", None)
            if not usage:
                continue
            model = _extract_model_name(message, fallback_model)
            bucket = by_model.setdefault(
                model,
                {"input": 0, "output": 0, "cached": 0, "reasoning": 0},
            )
            bucket["input"] += _coerce_int(usage.get("input_tokens"))
            bucket["output"] += _coerce_int(usage.get("output_tokens"))

            input_details = usage.get("input_token_details") or {}
            bucket["cached"] += _coerce_int(input_details.get("cache_read"))

            output_details = usage.get("output_token_details") or {}
            bucket["reasoning"] += _coerce_int(output_details.get("reasoning"))

    return [
        TokenUsageRecord(
            session_id=session_id,
            turn_id=turn_id,
            model=model,
            input_tokens=totals["input"],
            output_tokens=totals["output"],
            cached_tokens=totals["cached"],
            reasoning_tokens=totals["reasoning"],
        )
        for model, totals in by_model.items()
        if any(totals.values())
    ]


# ---------------------------------------------------------------------------
# Callback handler
# ---------------------------------------------------------------------------


class TokenUsageRecorder(BaseCallbackHandler):
    """Capture per-LLM-call token usage and forward it to the background writer.

    Operates from sync or async LangChain dispatch — every method enqueues
    into a thread-safe queue and returns immediately.
    """

    raise_error: bool = False

    def __init__(self) -> None:
        super().__init__()
        # Some LangChain versions don't re-pass ``metadata`` to ``on_llm_end``;
        # stash on start, recover on end, keyed by run_id.
        self._pending: dict[UUID, dict[str, Any]] = {}

    # -- start hooks ------------------------------------------------------------

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: Any,
        *,
        run_id: UUID,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        if metadata:
            self._pending[run_id] = metadata

    def on_llm_start(
        self,
        serialized: dict[str, Any],
        prompts: Any,
        *,
        run_id: UUID,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        if metadata:
            self._pending[run_id] = metadata

    # -- end / error hooks ------------------------------------------------------

    def on_llm_end(
        self,
        response: LLMResult,
        *,
        run_id: UUID,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        try:
            meta = metadata or self._pending.pop(run_id, None) or {}
            # Opt-out flag: call sites that record their own usage directly
            # (e.g. extract_trade_document_tool) set this so we don't double-
            # count the same call.
            if meta.get("_skip_token_recorder"):
                return
            session_id = meta.get("session_id") or meta.get("thread_id")
            turn_id = meta.get("turn_id")
            if not session_id or not turn_id:
                # Fall back to the per-run contextvar that ``make_lead_agent``
                # binds. This catches OCR tool / title middleware / ad-hoc
                # ``model.invoke`` sites where LangChain's RunnableConfig
                # propagation does not surface the metadata in time.
                ctx_meta = get_run_metadata()
                if ctx_meta:
                    session_id = session_id or ctx_meta.get("session_id")
                    turn_id = turn_id or ctx_meta.get("turn_id")
            if not session_id or not turn_id:
                return
            fallback_model = meta.get("model_name") or meta.get("model")
            records = _build_records(response, str(session_id), str(turn_id), fallback_model)
            for record in records:
                record_usage(record)
        except Exception:
            logger.warning("token_usage callback failed", exc_info=True)
        finally:
            self._pending.pop(run_id, None)

    def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        self._pending.pop(run_id, None)
