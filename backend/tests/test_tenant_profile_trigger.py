"""Unit tests for tenant_profile.trigger (M3)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.agents.tenant_profile import log as log_module
from src.agents.tenant_profile import meta as meta_module
from src.agents.tenant_profile import trigger as trigger_module
from src.agents.tenant_profile.config import (
    TenantProfileConfig,
    TriggerConfig,
    reset_tenant_profile_config_for_tests,
    set_tenant_profile_config,
)


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_paths = MagicMock()
    fake_paths.base_dir = tmp_path
    monkeypatch.setattr("src.agents.tenant_profile.log.get_paths", lambda: fake_paths)
    monkeypatch.setattr("src.agents.tenant_profile.meta.get_paths", lambda: fake_paths)
    log_module.reset_locks_for_tests()
    meta_module.reset_locks_for_tests()
    reset_tenant_profile_config_for_tests()
    yield
    reset_tenant_profile_config_for_tests()


def _set_thresholds(*, events: int = 50, time_seconds: int = 86400, cooldown: int = 1800) -> None:
    set_tenant_profile_config(
        TenantProfileConfig(
            trigger=TriggerConfig(
                event_count_threshold=events,
                primary_event_threshold=10,
                time_threshold_seconds=time_seconds,
                cooldown_seconds=cooldown,
            )
        )
    )


def _seed_events(tenant_id: str, n: int) -> None:
    for i in range(n):
        log_module.append_event(tenant_id, {"i": i})


# ── Threshold logic ──────────────────────────────────────────────────────


def test_no_events_means_no_run() -> None:
    _set_thresholds(events=10)
    decision = trigger_module.should_summarize("acme")
    assert decision.should_run is False
    assert decision.reason == "skipped_no_events"


def test_events_below_threshold_and_no_time_pressure_skips() -> None:
    # cooldown=0 so we get the "below thresholds" branch, not "cooldown".
    _set_thresholds(events=50, time_seconds=86400, cooldown=0)
    _seed_events("acme", 5)
    # No prior summarize means seconds_since_last is None — explicitly set
    # last_summarize_ts so we hit the "skipped_below_thresholds" path
    # instead of the time fallback.
    now = datetime(2026, 4, 27, 12, 0, 0, tzinfo=UTC)
    meta_module.write_meta(
        "acme",
        meta_module.Meta(last_summarize_ts=(now - timedelta(seconds=120)).isoformat().replace("+00:00", "Z")),
    )
    decision = trigger_module.should_summarize("acme", now=now)
    assert decision.should_run is False
    assert decision.reason == "skipped_below_thresholds"


def test_event_count_threshold_fires() -> None:
    _set_thresholds(events=10, cooldown=0)
    _seed_events("acme", 12)
    decision = trigger_module.should_summarize("acme")
    assert decision.should_run is True
    assert decision.reason == "event_count"


def test_time_threshold_fires_when_idle() -> None:
    _set_thresholds(events=50, time_seconds=60, cooldown=0)
    _seed_events("acme", 1)  # below event threshold
    now = datetime(2026, 4, 27, 12, 0, 0, tzinfo=UTC)
    last = (now - timedelta(seconds=120)).isoformat().replace("+00:00", "Z")
    meta_module.write_meta("acme", meta_module.Meta(last_summarize_ts=last))
    decision = trigger_module.should_summarize("acme", now=now)
    assert decision.should_run is True
    assert decision.reason == "time"


def test_cooldown_blocks_immediate_retrigger() -> None:
    _set_thresholds(events=10, cooldown=1800)
    _seed_events("acme", 12)
    now = datetime(2026, 4, 27, 12, 0, 0, tzinfo=UTC)
    last = (now - timedelta(seconds=300)).isoformat().replace("+00:00", "Z")
    meta_module.write_meta("acme", meta_module.Meta(last_summarize_ts=last))
    decision = trigger_module.should_summarize("acme", now=now)
    assert decision.should_run is False
    assert decision.reason == "skipped_cooldown"


def test_force_overrides_cooldown() -> None:
    _set_thresholds(events=10, cooldown=1800)
    _seed_events("acme", 1)
    now = datetime(2026, 4, 27, 12, 0, 0, tzinfo=UTC)
    last = (now - timedelta(seconds=60)).isoformat().replace("+00:00", "Z")
    meta_module.write_meta("acme", meta_module.Meta(last_summarize_ts=last))
    decision = trigger_module.should_summarize("acme", force=True, now=now)
    assert decision.should_run is True
    assert decision.reason == "force"


def test_first_run_with_events_fires_via_time_branch() -> None:
    """No prior summarize yet, events present, no cooldown to honour."""
    _set_thresholds(events=999, time_seconds=999_999, cooldown=0)
    _seed_events("acme", 3)
    decision = trigger_module.should_summarize("acme")
    assert decision.should_run is True
    assert decision.reason == "time"  # null last → falls through to time branch


# ── Mark started / finished ──────────────────────────────────────────────


def test_mark_started_sets_last_summarize_and_event_count() -> None:
    _seed_events("acme", 5)
    now = datetime(2026, 4, 27, 12, 0, 0, tzinfo=UTC)
    trigger_module.mark_summarize_started("acme", now=now)
    state = meta_module.read_meta("acme")
    assert state.last_summarize_ts == "2026-04-27T12:00:00Z"
    assert state.event_count_since_last == 5
    assert state.last_summarize_error is None


def test_mark_finished_success_resets_event_count() -> None:
    _seed_events("acme", 5)
    trigger_module.mark_summarize_started("acme")
    trigger_module.mark_summarize_finished("acme", success=True)
    state = meta_module.read_meta("acme")
    assert state.event_count_since_last == 0
    assert state.last_summarize_error is None


def test_mark_finished_failure_records_error_and_keeps_count() -> None:
    _seed_events("acme", 5)
    trigger_module.mark_summarize_started("acme")
    trigger_module.mark_summarize_finished("acme", success=False, error="boom")
    state = meta_module.read_meta("acme")
    assert state.event_count_since_last == 5
    assert state.last_summarize_error == "boom"
