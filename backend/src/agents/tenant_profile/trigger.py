"""When to run the summarizer.

Three triggers (TENANT_PROFILE_DESIGN.md §5.1):

1. Total event count since last summarize ≥ ``event_count_threshold``.
2. Time since last summarize ≥ ``time_threshold_seconds``.
3. Explicit ``force=True`` (skips cooldown).

A ``cooldown_seconds`` floor prevents triggers 1 and 2 from firing more
than once per cooldown window. Force overrides the cooldown — that's the
"user just told me they added a new warehouse" path.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from src.agents.tenant_profile import log as log_module
from src.agents.tenant_profile import meta as meta_module
from src.agents.tenant_profile.config import TriggerConfig, get_tenant_profile_config

logger = logging.getLogger(__name__)

TriggerReason = Literal[
    "force",
    "event_count",
    "time",
    "skipped_cooldown",
    "skipped_no_events",
    "skipped_below_thresholds",
]


@dataclass(frozen=True)
class TriggerDecision:
    """Result of evaluating ``should_summarize``."""

    should_run: bool
    reason: TriggerReason
    event_count: int
    seconds_since_last: float | None


def should_summarize(tenant_id: str, *, force: bool = False, now: datetime | None = None) -> TriggerDecision:
    """Decide whether to fire the summarizer for ``tenant_id`` right now.

    Pure function over ``meta.json`` + ``usage_log.jsonl`` — no side effects.
    The queue/runner is responsible for calling ``mark_summarize_started``
    before kicking off and ``mark_summarize_finished`` afterwards.
    """
    cfg: TriggerConfig = get_tenant_profile_config().trigger
    now_dt = now or datetime.now(UTC)

    event_count = log_module.count_events(tenant_id)
    state = meta_module.read_meta(tenant_id)
    seconds_since_last = _seconds_since(state.last_summarize_ts, now_dt)

    if force:
        return TriggerDecision(True, "force", event_count, seconds_since_last)

    if event_count == 0:
        return TriggerDecision(False, "skipped_no_events", event_count, seconds_since_last)

    if seconds_since_last is not None and seconds_since_last < cfg.cooldown_seconds:
        return TriggerDecision(False, "skipped_cooldown", event_count, seconds_since_last)

    if event_count >= cfg.event_count_threshold:
        return TriggerDecision(True, "event_count", event_count, seconds_since_last)

    if seconds_since_last is None or seconds_since_last >= cfg.time_threshold_seconds:
        # Includes the "never summarised before" case — once events exist and
        # we're past cooldown, an idle-but-non-zero log gets a first run.
        return TriggerDecision(True, "time", event_count, seconds_since_last)

    return TriggerDecision(False, "skipped_below_thresholds", event_count, seconds_since_last)


def mark_summarize_started(tenant_id: str, *, now: datetime | None = None) -> None:
    """Record the in-flight start time and snapshot the current event count.

    The runner should call this just before invoking the LLM so a crashed
    run doesn't immediately re-trigger on the next sweep.
    """
    now_iso = (now or datetime.now(UTC)).isoformat().replace("+00:00", "Z")
    event_count = log_module.count_events(tenant_id)
    meta_module.mutate(tenant_id, lambda m: _mark_started(m, now_iso, event_count))


def mark_summarize_finished(tenant_id: str, *, success: bool, error: str | None = None, now: datetime | None = None) -> None:
    """Record completion. On success, reset event_count_since_last to 0.

    On failure, leave event_count_since_last where it was so the next run
    still considers the same window — coupled with cooldown this prevents
    a tight retry loop.
    """
    now_iso = (now or datetime.now(UTC)).isoformat().replace("+00:00", "Z")
    meta_module.mutate(tenant_id, lambda m: _mark_finished(m, success, error, now_iso))


def _seconds_since(iso: str | None, now: datetime) -> float | None:
    if not iso:
        return None
    try:
        # Tolerate the trailing "Z" we emit from ISO strings.
        cleaned = iso.replace("Z", "+00:00") if iso.endswith("Z") else iso
        last = datetime.fromisoformat(cleaned)
    except ValueError:
        logger.warning("tenant_profile: unparseable last_summarize_ts %r — treating as null", iso)
        return None
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    delta = now - last
    return delta.total_seconds()


def _mark_started(meta: meta_module.Meta, now_iso: str, event_count: int) -> meta_module.Meta:
    # We don't have a separate "in_flight" field yet; bumping
    # last_summarize_ts at start works as a soft cooldown and gets refreshed
    # at finish. event_count_since_last is set to a sentinel only on success.
    meta.last_summarize_ts = now_iso
    meta.last_summarize_error = None
    meta.event_count_since_last = event_count
    return meta


def _mark_finished(meta: meta_module.Meta, success: bool, error: str | None, now_iso: str) -> meta_module.Meta:
    meta.last_summarize_ts = now_iso
    if success:
        meta.last_summarize_error = None
        meta.event_count_since_last = 0
    else:
        meta.last_summarize_error = error
    return meta
