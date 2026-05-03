"""Middleware for proactive next-step suggestion budget / cooldown bookkeeping.

The lead-agent prompt teaches the model to wrap proactive next-step
suggestions in a hidden ``<next_step options="..." primary="...">…</next_step>``
tag (see ``src/agents/lead_agent/prompt.py``: ``NEXT_STEP_SECTION``). The
frontend strips the tag and renders an action card; IM channels keep the
inner natural-language sentence.

This middleware does two things, derived entirely from message history
(no extra ThreadState fields, no persisted counters):

1. **before_model**: scans the conversation, computes how many times the
   agent has already suggested, whether the user accepted / rejected the
   most recent one, and whether we are in a post-rejection cooldown — then
   injects a ``<next_step_state>`` SystemMessage so the model can self-gate.
   Uses a fixed message id so LangGraph's ``add_messages`` reducer replaces
   the previous turn's state block in place rather than accumulating.
2. **after_model**: if the model emitted a ``<next_step>`` tag whose options
   are too similar to the prior turn's (suggesting the same thing twice),
   strips the tag from the AIMessage. Hard guard against the policy block
   being ignored.

Disabled-mode is handled in ``_build_middlewares``: when
``NextStepConfig.enabled`` is false this class is not mounted.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Literal, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from src.config.next_step_config import NextStepConfig, get_next_step_config

logger = logging.getLogger(__name__)


# Fixed id so LangGraph's add_messages reducer replaces the previous turn's
# runtime-state SystemMessage in place rather than accumulating one per turn.
RUNTIME_STATE_MESSAGE_ID = "__next_step_runtime_state__"

# Hidden tag the model wraps proactive suggestions in. Captured groups:
# 1) options (pipe-separated), 2) primary (optional), 3) inner display text.
NEXT_STEP_TAG_RE = re.compile(
    r'<next_step\s+options="([^"]+)"(?:\s+primary="([^"]*)")?\s*>(.*?)</next_step>',
    re.DOTALL,
)


Classification = Literal["accept", "reject", "neutral", "none"]


@dataclass(frozen=True)
class NextStepRuntimeState:
    """Per-turn computed state injected into the model prompt."""

    asked_count: int
    last_round_options: tuple[str, ...]
    last_round_response: Classification
    consecutive_rejections: int
    cooldown_remaining: int


@dataclass(frozen=True)
class _Suggestion:
    options: tuple[str, ...]
    primary: str | None
    display_text: str


def extract_suggestion(content: str) -> _Suggestion | None:
    """Pull the first ``<next_step>`` tag out of an AIMessage content string.

    Returns ``None`` when the tag is missing. Options shorter than 1 char
    after stripping are dropped — the model is expected to produce 1-2
    real options per the prompt policy.
    """
    if not content:
        return None
    match = NEXT_STEP_TAG_RE.search(content)
    if not match:
        return None
    raw_options, raw_primary, display = match.group(1), match.group(2), match.group(3)
    options = tuple(o.strip() for o in raw_options.split("|") if o.strip())
    if not options:
        return None
    primary = (raw_primary or "").strip() or None
    return _Suggestion(options=options, primary=primary, display_text=display.strip())


def _ai_text(msg: AIMessage) -> str:
    """Coerce AIMessage.content (str or list) to a single string for tag scan."""
    content = msg.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for chunk in content:
            if isinstance(chunk, str):
                parts.append(chunk)
            elif isinstance(chunk, dict):
                text = chunk.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    return ""


def _human_text(msg: HumanMessage) -> str:
    """Coerce HumanMessage.content to plain string and trim."""
    content = msg.content
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for chunk in content:
            if isinstance(chunk, str):
                parts.append(chunk)
            elif isinstance(chunk, dict):
                text = chunk.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts).strip()
    return ""


def _is_runtime_state_message(msg: Any) -> bool:
    """Skip the SystemMessage we inject ourselves so we don't mis-count it."""
    return isinstance(msg, SystemMessage) and getattr(msg, "id", None) == RUNTIME_STATE_MESSAGE_ID


def _kw_match(keyword: str, text_lower: str) -> bool:
    """Match a keyword against lower-cased user text.

    ASCII keywords use ``\\b`` word boundaries to avoid false matches
    (``"yes"`` must not match ``"yesterday"``). Non-ASCII keywords (zh / etc.)
    use plain substring — there is no reliable word boundary in Chinese.
    """
    keyword_lower = keyword.lower().strip()
    if not keyword_lower:
        return False
    if keyword_lower.isascii():
        return bool(re.search(rf"\b{re.escape(keyword_lower)}\b", text_lower))
    return keyword_lower in text_lower


def classify_user_response(
    user_text: str,
    last_options: tuple[str, ...],
    config: NextStepConfig,
) -> Classification:
    """Decide whether a user reply accepts / rejects the previous suggestion.

    Precedence:
      1. Exact option match → ``accept`` (frontend button click sends the
         option string verbatim, so this is the highest-confidence signal).
      2. Reject keyword anywhere in the message → ``reject``. Reject wins
         over accept so ``"好的，但是先不录入"`` resolves to reject (the user's
         intent is the negation, not the polite preface).
      3. Accept keyword → ``accept``.
      4. The full option text appears as a substring of the message → ``accept``.
      5. Otherwise ``neutral`` (user changed topic, asked a follow-up
         question, etc. — does not count as rejection).
    """
    text = (user_text or "").strip()
    if not text:
        return "neutral"
    text_lower = text.lower()

    for option in last_options:
        if text == option:
            return "accept"

    for kw in config.reject_keywords:
        if _kw_match(kw, text_lower):
            return "reject"

    for kw in config.accept_keywords:
        if _kw_match(kw, text_lower):
            return "accept"

    for option in last_options:
        if option and option in text:
            return "accept"

    return "neutral"


def compute_runtime_state(messages: list[Any], config: NextStepConfig) -> NextStepRuntimeState:
    """Recompute the ``<next_step_state>`` block from message history.

    Pure function over the message list — no persisted counters. The
    message history is the source of truth: each AIMessage with a
    ``<next_step>`` tag counts against the budget, each subsequent
    HumanMessage triggers an accept / reject / neutral classification.

    Cooldown is modeled as "after the most recent ``rejection_threshold``
    consecutive rejections, the next ``cooldown_turns`` user messages are
    silent". We compute it by counting rejections among the most recent
    classifications and how many user turns have elapsed since the
    cooldown trigger.
    """
    asked_count = 0
    classifications: list[Classification] = []
    last_options: tuple[str, ...] = ()

    pending_options: tuple[str, ...] | None = None
    for msg in messages:
        if _is_runtime_state_message(msg):
            continue
        if isinstance(msg, AIMessage):
            suggestion = extract_suggestion(_ai_text(msg))
            if suggestion is not None:
                asked_count += 1
                pending_options = suggestion.options
                last_options = suggestion.options
        elif isinstance(msg, HumanMessage) and pending_options is not None:
            verdict = classify_user_response(_human_text(msg), pending_options, config)
            classifications.append(verdict)
            pending_options = None

    last_round_response: Classification = classifications[-1] if classifications else "none"

    consecutive_rejections = 0
    for verdict in reversed(classifications):
        if verdict == "reject":
            consecutive_rejections += 1
            continue
        if verdict == "accept":
            consecutive_rejections = 0
        break

    cooldown_remaining = 0
    if config.rejection_threshold > 0:
        for trigger_idx in range(len(classifications) - 1, -1, -1):
            window = classifications[max(0, trigger_idx - config.rejection_threshold + 1) : trigger_idx + 1]
            if len(window) == config.rejection_threshold and all(v == "reject" for v in window):
                turns_since_trigger = len(classifications) - 1 - trigger_idx
                remaining = config.cooldown_turns - turns_since_trigger
                if remaining > 0:
                    cooldown_remaining = remaining
                break

    return NextStepRuntimeState(
        asked_count=asked_count,
        last_round_options=last_options,
        last_round_response=last_round_response,
        consecutive_rejections=consecutive_rejections,
        cooldown_remaining=cooldown_remaining,
    )


def render_runtime_state(state: NextStepRuntimeState, config: NextStepConfig) -> str:
    """Render the ``<next_step_state>`` block the model reads each turn."""
    last_options_text = " | ".join(state.last_round_options) if state.last_round_options else "(无)"

    if state.last_round_response == "accept":
        hint = f"用户已接受上一轮建议（{last_options_text}），现在执行该动作即可，**不要**再生成新的 <next_step> 标签。"
    elif state.last_round_response == "reject":
        hint = f"用户拒绝了上一轮建议（{last_options_text}），本轮严禁追问；尊重用户意图。"
    elif state.last_round_response == "neutral" and state.last_round_options:
        hint = f'上一轮已追问"{last_options_text}"但用户未明确回应，本轮**严禁**重复同一主题；若触发条件再次满足，可考虑「不同主题」的 next-step。'
    else:
        hint = "尚未在本会话中追问过 next-step。"

    return f"<next_step_state>\n本会话已主动追问 {state.asked_count} 次（上限 {config.budget}）；连续被拒 {state.consecutive_rejections} 次；冷却剩余 {state.cooldown_remaining} 轮。\n{hint}\n</next_step_state>"


def _suggestions_overlap(prior: tuple[str, ...], current: tuple[str, ...], threshold: float) -> bool:
    """True when the new suggestion's options look too much like the prior turn's.

    Uses difflib SequenceMatcher on the joined option strings — a coarse but
    cheap signal. Intended only as a hard backstop when the model ignores
    the policy block; legitimate "different topic" suggestions stay below
    the default 0.6 threshold.
    """
    if not prior or not current:
        return False
    prior_text = "|".join(sorted(prior))
    current_text = "|".join(sorted(current))
    ratio = SequenceMatcher(None, prior_text, current_text).ratio()
    return ratio >= threshold


def _strip_next_step_tag(content: str) -> str:
    """Remove every ``<next_step>`` tag from a content string."""
    return NEXT_STEP_TAG_RE.sub("", content).rstrip()


def _strip_next_step_from_ai_message(msg: AIMessage) -> AIMessage | None:
    """Return a new AIMessage with all ``<next_step>`` tags removed.

    Returns ``None`` when no tag was present (no rewrite needed). Preserves
    the message id so LangGraph replaces the original via ``add_messages``.
    Handles both plain-string and list-of-dict content shapes.
    """
    content = msg.content
    if isinstance(content, str):
        if "<next_step" not in content:
            return None
        cleaned = _strip_next_step_tag(content)
        if cleaned == content:
            return None
        return AIMessage(
            content=cleaned,
            id=msg.id,
            additional_kwargs=msg.additional_kwargs,
            response_metadata=msg.response_metadata,
            tool_calls=msg.tool_calls,
        )
    if isinstance(content, list):
        rewritten: list[Any] = []
        changed = False
        for chunk in content:
            if isinstance(chunk, dict) and isinstance(chunk.get("text"), str) and "<next_step" in chunk["text"]:
                cleaned = _strip_next_step_tag(chunk["text"])
                if cleaned != chunk["text"]:
                    changed = True
                rewritten.append({**chunk, "text": cleaned})
            else:
                rewritten.append(chunk)
        if not changed:
            return None
        return AIMessage(
            content=rewritten,
            id=msg.id,
            additional_kwargs=msg.additional_kwargs,
            response_metadata=msg.response_metadata,
            tool_calls=msg.tool_calls,
        )
    return None


class NextStepStateMiddleware(AgentMiddleware[AgentState]):
    """Injects ``<next_step_state>`` and strips same-topic repeats."""

    @override
    def before_model(self, state: AgentState) -> dict | None:
        config = get_next_step_config()
        if not config.enabled:
            return None

        messages = state.get("messages", [])
        runtime_state = compute_runtime_state(messages, config)
        rendered = render_runtime_state(runtime_state, config)
        # Fixed id → add_messages replaces the prior turn's state block in place.
        system_msg = SystemMessage(content=rendered, id=RUNTIME_STATE_MESSAGE_ID)
        return {"messages": [system_msg]}

    @override
    def after_model(self, state: AgentState) -> dict | None:
        config = get_next_step_config()
        if not config.enabled:
            return None

        messages = state.get("messages", [])
        if not messages:
            return None
        last = messages[-1]
        if not isinstance(last, AIMessage):
            return None

        new_suggestion = extract_suggestion(_ai_text(last))
        if new_suggestion is None:
            return None

        prior_options = _previous_suggestion_options(messages[:-1])
        if not _suggestions_overlap(prior_options, new_suggestion.options, config.same_topic_similarity):
            return None

        rewritten = _strip_next_step_from_ai_message(last)
        if rewritten is None:
            return None
        logger.info(
            "next_step: stripped same-topic suggestion (prior=%s, new=%s)",
            list(prior_options),
            list(new_suggestion.options),
        )
        return {"messages": [rewritten]}


def _previous_suggestion_options(messages: list[Any]) -> tuple[str, ...]:
    """Find the most recent prior ``<next_step>`` suggestion in history."""
    for msg in reversed(messages):
        if _is_runtime_state_message(msg):
            continue
        if isinstance(msg, AIMessage):
            suggestion = extract_suggestion(_ai_text(msg))
            if suggestion is not None:
                return suggestion.options
    return ()
