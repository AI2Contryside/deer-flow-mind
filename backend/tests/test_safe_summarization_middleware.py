"""Tests for ``SafeSummarizationMiddleware``.

Pinned to thread ``09417ecf-b0e0-470f-ae3d-244a0b1ba74d``: the upstream
``SummarizationMiddleware`` ran ``RemoveMessage(REMOVE_ALL_MESSAGES)``
plus a fallback placeholder string, wiping the entire thread history
when ``trim_messages`` returned an empty list. Frontend rendered an
empty conversation; the next run lost all context and burned 25
supersteps re-discovering CLI commands.

Contract this test fixes in stone:

  - On the happy path (LLM returns a real summary), the wrapper
    behaves identically to the parent: emits ``RemoveMessage`` +
    summary HumanMessage + preserved tail.
  - On the fallback path (``_create_summary`` returns the sentinel
    ``"Previous conversation was too long to summarize."`` or
    ``"No previous conversation history."`` or any
    ``"Error generating summary: ..."``), ``before_model`` returns
    ``None`` — state is NOT mutated, history is preserved.
  - The check is on ``str.strip()`` so trailing whitespace doesn't
    accidentally bypass the guard.
  - Both sync and async paths apply the same guard.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

from langchain_core.messages import RemoveMessage

from src.agents.middlewares.safe_summarization_middleware import (
    SafeSummarizationMiddleware,
    _summary_is_fallback,
)


def _make_mw_skipping_init(
    *,
    summary: str,
    cutoff: int = 5,
    should_summarize: bool = True,
    new_messages: list | None = None,
    preserved: list | None = None,
) -> SafeSummarizationMiddleware:
    """Build an instance bypassing ``__init__`` so we don't need a real model.

    ``SummarizationMiddleware`` validates its model param eagerly; the
    test seam is to skip the constructor and stub the methods we care
    about.
    """
    mw = SafeSummarizationMiddleware.__new__(SafeSummarizationMiddleware)
    # Methods invoked by before_model:
    mw._ensure_message_ids = MagicMock()
    mw.token_counter = MagicMock(return_value=999_999)
    mw._should_summarize = MagicMock(return_value=should_summarize)
    mw._determine_cutoff_index = MagicMock(return_value=cutoff)
    mw._partition_messages = MagicMock(return_value=([f"old{i}" for i in range(cutoff)], preserved or ["preserved-tail"]))
    mw._create_summary = MagicMock(return_value=summary)
    mw._acreate_summary = MagicMock(return_value=_async_return(summary))
    mw._build_new_messages = MagicMock(return_value=new_messages or ["summary-msg"])
    return mw


async def _async_return(value):
    return value


def test_fallback_too_long_aborts_state_mutation():
    mw = _make_mw_skipping_init(summary="Previous conversation was too long to summarize.")

    out = mw.before_model({"messages": [object()] * 30}, runtime=None)

    assert out is None, "Sentinel summary must abort state mutation"


def test_fallback_no_history_aborts_state_mutation():
    mw = _make_mw_skipping_init(summary="No previous conversation history.")

    out = mw.before_model({"messages": [object()] * 30}, runtime=None)

    assert out is None


def test_error_prefix_aborts_state_mutation():
    # langchain returns "Error generating summary: <ExceptionClass: ...>"
    # when self.model.invoke raises.
    mw = _make_mw_skipping_init(summary="Error generating summary: TimeoutError: deepseek timed out")

    out = mw.before_model({"messages": [object()] * 30}, runtime=None)

    assert out is None


def test_real_summary_emits_remove_all_and_replacement():
    mw = _make_mw_skipping_init(
        summary="User wanted to ingest scarves; ERPNext Stock Entry was created in Draft state at MAT-STE-2026-00008.",
        new_messages=["summary-as-human"],
        preserved=["preserved-1", "preserved-2"],
    )

    out = mw.before_model({"messages": [object()] * 30}, runtime=None)

    assert out is not None
    msgs = out["messages"]
    # First entry must be the wholesale removal sentinel
    assert isinstance(msgs[0], RemoveMessage)
    # Then the summary, then the preserved tail
    assert msgs[1] == "summary-as-human"
    assert msgs[2:] == ["preserved-1", "preserved-2"]


def test_should_not_summarize_returns_none_without_calling_summary():
    mw = _make_mw_skipping_init(summary="ignored", should_summarize=False)

    out = mw.before_model({"messages": [object()] * 30}, runtime=None)

    assert out is None
    mw._create_summary.assert_not_called()


def test_negative_cutoff_returns_none():
    mw = _make_mw_skipping_init(summary="ignored", cutoff=0)

    out = mw.before_model({"messages": [object()] * 30}, runtime=None)

    assert out is None
    mw._create_summary.assert_not_called()


def test_async_fallback_aborts_state_mutation():
    mw = _make_mw_skipping_init(summary="Previous conversation was too long to summarize.")

    out = asyncio.run(mw.abefore_model({"messages": [object()] * 30}, runtime=None))

    assert out is None


def test_async_real_summary_emits_remove_and_replacement():
    mw = _make_mw_skipping_init(
        summary="real summary text",
        new_messages=["summary"],
        preserved=["tail"],
    )

    out = asyncio.run(mw.abefore_model({"messages": [object()] * 30}, runtime=None))

    assert out is not None
    msgs = out["messages"]
    assert isinstance(msgs[0], RemoveMessage)


def test_summary_is_fallback_helper():
    assert _summary_is_fallback("Previous conversation was too long to summarize.")
    assert _summary_is_fallback("  Previous conversation was too long to summarize.  "), "Whitespace must be stripped before comparing"
    assert _summary_is_fallback("No previous conversation history.")
    assert _summary_is_fallback("Error generating summary: TimeoutError")
    assert not _summary_is_fallback("Real summary about ERPNext stock entry.")
    assert not _summary_is_fallback("")  # empty string isn't a sentinel
