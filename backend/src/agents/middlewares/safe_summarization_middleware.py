"""Safe wrapper around langchain's ``SummarizationMiddleware``.

The upstream ``SummarizationMiddleware.before_model`` (in
``langchain/agents/middleware/summarization.py``) emits
``RemoveMessage(id=REMOVE_ALL_MESSAGES)`` and replaces the entire history
with whatever ``_create_summary`` returns — *even when that string is the
fallback placeholder* ``"Previous conversation was too long to summarize."``
or an LLM error message.

In thread ``09417ecf-b0e0-470f-ae3d-244a0b1ba74d`` this combination wiped
the user's entire conversation: a single tool response (docling-parsed
xlsx + erpnext bulk listing) was larger than ``trim_tokens_to_summarize``,
``trim_messages`` returned an empty list, ``_create_summary`` short-
circuited to the placeholder, and ``before_model`` then deleted the whole
history. The frontend, which renders thread state directly, showed an
empty conversation; the next run started with no context and burned 25
supersteps re-discovering CLI commands the model already knew.

This middleware fixes that by checking the summary string against a
small set of known fallback / error sentinels and *aborting the state
mutation* when one is hit. The tradeoff is intentional: it is far better
to skip a single summarization round (the trigger will fire again on the
next call once we cross the threshold) than to silently destroy thread
history.
"""

from __future__ import annotations

import logging
from typing import Any, override

from langchain.agents.middleware import SummarizationMiddleware
from langchain_core.messages import RemoveMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from langgraph.runtime import Runtime

logger = logging.getLogger(__name__)


# Strings the upstream middleware returns when summarization didn't actually
# run — keeping them in sync with ``langchain/agents/middleware/summarization.py``.
# The error-prefix is matched as a startswith() because the suffix carries
# the exception class name.
_FAILURE_SENTINELS: frozenset[str] = frozenset(
    {
        "Previous conversation was too long to summarize.",
        "No previous conversation history.",
    }
)
_ERROR_PREFIX = "Error generating summary:"


def _summary_is_fallback(summary: str) -> bool:
    """Detect whether ``summary`` came from a fallback path rather than an LLM."""
    s = (summary or "").strip()
    if s in _FAILURE_SENTINELS:
        return True
    if s.startswith(_ERROR_PREFIX):
        return True
    return False


class SafeSummarizationMiddleware(SummarizationMiddleware):
    """``SummarizationMiddleware`` that refuses to clear history on a failed summary.

    Behaviour matches the parent class on the happy path. On failure
    (``_create_summary`` / ``_acreate_summary`` returns a known fallback
    string), this middleware logs a warning and returns ``None`` from
    ``before_model``, leaving the state untouched. The next ``before_model``
    invocation will retry summarization — the trigger condition is still
    met, so the middleware will fire again next turn and may succeed once
    a smaller tool response has been added.
    """

    @override
    def before_model(self, state: Any, runtime: Runtime) -> dict[str, Any] | None:
        messages = state["messages"]
        self._ensure_message_ids(messages)

        total_tokens = self.token_counter(messages)
        if not self._should_summarize(messages, total_tokens):
            return None

        cutoff_index = self._determine_cutoff_index(messages)
        if cutoff_index <= 0:
            return None

        messages_to_summarize, preserved_messages = self._partition_messages(messages, cutoff_index)
        summary = self._create_summary(messages_to_summarize)

        if _summary_is_fallback(summary):
            logger.warning(
                "Summarization aborted (sentinel summary received: %r). Keeping full message history to avoid wiping thread state. total_tokens=%s messages=%d cutoff=%d",
                summary,
                total_tokens,
                len(messages),
                cutoff_index,
            )
            return None

        new_messages = self._build_new_messages(summary)
        return {
            "messages": [
                RemoveMessage(id=REMOVE_ALL_MESSAGES),
                *new_messages,
                *preserved_messages,
            ]
        }

    @override
    async def abefore_model(self, state: Any, runtime: Runtime) -> dict[str, Any] | None:
        messages = state["messages"]
        self._ensure_message_ids(messages)

        total_tokens = self.token_counter(messages)
        if not self._should_summarize(messages, total_tokens):
            return None

        cutoff_index = self._determine_cutoff_index(messages)
        if cutoff_index <= 0:
            return None

        messages_to_summarize, preserved_messages = self._partition_messages(messages, cutoff_index)
        summary = await self._acreate_summary(messages_to_summarize)

        if _summary_is_fallback(summary):
            logger.warning(
                "Summarization aborted (sentinel summary received: %r). Keeping full message history to avoid wiping thread state. total_tokens=%s messages=%d cutoff=%d",
                summary,
                total_tokens,
                len(messages),
                cutoff_index,
            )
            return None

        new_messages = self._build_new_messages(summary)
        return {
            "messages": [
                RemoveMessage(id=REMOVE_ALL_MESSAGES),
                *new_messages,
                *preserved_messages,
            ]
        }
