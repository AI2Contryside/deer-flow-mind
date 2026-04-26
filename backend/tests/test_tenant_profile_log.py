"""Unit tests for tenant_profile.log (M1).

Covers the on-disk jsonl contract: tenant id validation, append/read
roundtrip, malformed-line tolerance, and the rotation primitive that
M2's archive layer will hang OSS uploads off of.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.agents.tenant_profile import log as log_module


@pytest.fixture(autouse=True)
def _isolate_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the log module at ``tmp_path`` instead of the real DEER_FLOW_HOME."""
    fake_paths = MagicMock()
    fake_paths.base_dir = tmp_path
    monkeypatch.setattr("src.agents.tenant_profile.log.get_paths", lambda: fake_paths)
    log_module.reset_locks_for_tests()


def test_append_event_then_read_back_roundtrip(tmp_path: Path) -> None:
    event = {"op": "get_doc", "doctype": "Customer", "name": "FOOCORP-001"}
    assert log_module.append_event("acme", event) is True

    events = log_module.read_events("acme")
    assert events == [event]


def test_append_multiple_events_preserves_order(tmp_path: Path) -> None:
    for i in range(5):
        log_module.append_event("acme", {"i": i})
    events = log_module.read_events("acme")
    assert [e["i"] for e in events] == [0, 1, 2, 3, 4]


def test_count_events_matches_read_events_len() -> None:
    for i in range(7):
        log_module.append_event("acme", {"i": i})
    assert log_module.count_events("acme") == 7
    assert len(log_module.read_events("acme")) == 7


def test_count_events_returns_zero_when_log_missing() -> None:
    assert log_module.count_events("nonexistent-tenant-id") == 0


def test_read_events_returns_empty_when_log_missing() -> None:
    assert log_module.read_events("nonexistent-tenant-id") == []


def test_read_events_skips_malformed_lines(tmp_path: Path) -> None:
    log_module.append_event("acme", {"good": 1})
    # Manually corrupt by appending a non-JSON line.
    path = log_module.get_log_path("acme")
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("not-json-at-all\n")
    log_module.append_event("acme", {"good": 2})

    events = log_module.read_events("acme")
    assert events == [{"good": 1}, {"good": 2}]


def test_invalid_tenant_id_is_rejected_silently() -> None:
    # Path-traversal attempt — must return False, never raise, never write.
    assert log_module.append_event("../escape", {"x": 1}) is False
    assert log_module.append_event("with/slash", {"x": 1}) is False
    assert log_module.append_event("", {"x": 1}) is False
    # And nothing should have been created on disk for those calls.
    assert log_module.read_events("../escape") == []


def test_invalid_tenant_id_count_returns_zero() -> None:
    assert log_module.count_events("with/slash") == 0


def test_get_log_path_validates_tenant_id() -> None:
    with pytest.raises(ValueError):
        log_module.get_log_path("with/slash")


def test_rotate_log_moves_live_to_archive_and_creates_empty_live() -> None:
    log_module.append_event("acme", {"op": "get_doc"})
    log_module.append_event("acme", {"op": "insert"})

    rotated = log_module.rotate_log("acme")
    assert rotated is not None
    assert rotated.exists()
    assert rotated.parent.name == "usage_log.archive"

    # Rotated file contains the original two lines.
    with open(rotated, encoding="utf-8") as fh:
        rotated_lines = [json.loads(line) for line in fh if line.strip()]
    assert len(rotated_lines) == 2

    # Live file now exists but is empty.
    live = log_module.get_log_path("acme")
    assert live.exists()
    assert live.stat().st_size == 0


def test_rotate_log_returns_none_when_nothing_to_rotate() -> None:
    # No log file at all.
    assert log_module.rotate_log("acme") is None

    # Empty log file.
    log_module.get_log_path("acme").parent.mkdir(parents=True, exist_ok=True)
    log_module.get_log_path("acme").touch()
    assert log_module.rotate_log("acme") is None


def test_rotate_log_invalid_tenant_returns_none() -> None:
    assert log_module.rotate_log("../escape") is None


def test_concurrent_appends_do_not_truncate_lines() -> None:
    """Smoke test — many threads appending shouldn't produce mid-line splits.

    This isn't a strict race-freedom guarantee; it's a smoke test that the
    per-tenant lock prevents the obvious case where two writes interleave.
    """
    threads_count = 8
    per_thread = 50
    barrier = threading.Barrier(threads_count)

    def worker(tid: int) -> None:
        barrier.wait()
        for i in range(per_thread):
            log_module.append_event("acme", {"tid": tid, "i": i})

    workers = [threading.Thread(target=worker, args=(t,)) for t in range(threads_count)]
    for w in workers:
        w.start()
    for w in workers:
        w.join()

    events = log_module.read_events("acme")
    assert len(events) == threads_count * per_thread
    for e in events:
        assert "tid" in e and "i" in e
