"""Unit tests for tenant_profile archive + meta layer (M2)."""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.agents.tenant_profile import archive as archive_module
from src.agents.tenant_profile import log as log_module
from src.agents.tenant_profile import meta as meta_module
from src.agents.tenant_profile.meta import Meta, PendingUpload
from src.storage import InMemoryStorage, tenant_profile_usage_log_key

# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolate_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point every tenant_profile path at ``tmp_path``."""
    fake_paths = MagicMock()
    fake_paths.base_dir = tmp_path
    monkeypatch.setattr("src.agents.tenant_profile.log.get_paths", lambda: fake_paths)
    monkeypatch.setattr("src.agents.tenant_profile.meta.get_paths", lambda: fake_paths)
    log_module.reset_locks_for_tests()
    meta_module.reset_locks_for_tests()


@pytest.fixture
def storage() -> InMemoryStorage:
    return InMemoryStorage()


def _seed_log(tenant_id: str, n: int) -> None:
    for i in range(n):
        log_module.append_event(tenant_id, {"i": i, "doctype": "Customer", "name": f"X-{i}"})


# ── tenant_profile_usage_log_key shape ────────────────────────────────────


def test_usage_log_key_shape() -> None:
    assert tenant_profile_usage_log_key("acme", "20260426", "abcd1234") == "tenants/acme/profile/usage_log/20260426-abcd1234.jsonl.gz"


# ── archive_log: happy path ───────────────────────────────────────────────


def test_archive_log_rotates_compresses_uploads(storage: InMemoryStorage) -> None:
    _seed_log("acme", 3)
    result = archive_module.archive_log("acme", storage=storage)

    assert result.rotated is True
    assert result.uploaded is True
    assert result.pending is False
    assert result.local_path is not None and result.local_path.exists()
    assert result.local_path.suffix == ".gz"

    # The OSS object exists under the right bucket+key prefix.
    assert result.bucket is not None and result.key is not None
    assert result.key.startswith("tenants/acme/profile/usage_log/")
    assert result.key.endswith(".jsonl.gz")
    assert storage.object_exists(result.bucket, result.key)

    # Local archive copy is gzipped jsonl with the original 3 events.
    raw = gzip.decompress(result.local_path.read_bytes()).decode("utf-8")
    lines = [json.loads(line) for line in raw.splitlines() if line.strip()]
    assert [e["i"] for e in lines] == [0, 1, 2]


def test_archive_log_filename_contains_content_sha8(storage: InMemoryStorage) -> None:
    _seed_log("acme", 2)
    result = archive_module.archive_log("acme", storage=storage)
    assert result.local_path is not None
    # Filename: usage_log-<YYYYMMDDTHHMMSSZ>-<sha8>.jsonl.gz
    parts = result.local_path.stem.replace(".jsonl", "").split("-")
    assert parts[0] == "usage_log"
    sha8 = parts[-1]
    assert len(sha8) == 8
    assert all(c in "0123456789abcdef" for c in sha8)
    # The OSS key should embed the same sha8.
    assert result.key is not None and result.key.endswith(f"-{sha8}.jsonl.gz")


def test_archive_log_clears_live_jsonl(storage: InMemoryStorage) -> None:
    _seed_log("acme", 5)
    archive_module.archive_log("acme", storage=storage)
    assert log_module.count_events("acme") == 0


def test_archive_log_with_no_events_returns_no_op(storage: InMemoryStorage) -> None:
    result = archive_module.archive_log("acme", storage=storage)
    assert result.rotated is False
    assert result.uploaded is False
    assert result.pending is False
    assert result.local_path is None


def test_archive_log_local_copy_persists_after_upload(storage: InMemoryStorage) -> None:
    """Decision 6: local archive is kept after a successful OSS upload."""
    _seed_log("acme", 2)
    result = archive_module.archive_log("acme", storage=storage)
    assert result.local_path is not None
    assert result.local_path.exists()


# ── archive_log: upload failure → pending ────────────────────────────────


def test_archive_log_upload_failure_enqueues_pending() -> None:
    failing = MagicMock(spec=InMemoryStorage)
    failing.put_object.side_effect = RuntimeError("simulated network error")

    _seed_log("acme", 2)
    result = archive_module.archive_log("acme", storage=failing)

    assert result.rotated is True
    assert result.uploaded is False
    assert result.pending is True
    assert result.local_path is not None and result.local_path.exists()

    state = meta_module.read_meta("acme")
    assert len(state.pending_oss_uploads) == 1
    pending = state.pending_oss_uploads[0]
    assert pending.attempts == 1
    assert pending.last_error and "simulated network error" in pending.last_error
    assert pending.local_path == str(result.local_path)
    assert pending.key == result.key


def test_archive_log_storage_unavailable_falls_back_to_pending(monkeypatch: pytest.MonkeyPatch) -> None:
    """When OSS is entirely unconfigured, we still produce a local archive
    and queue an upload for later — never raise."""

    def boom() -> None:
        raise ValueError("oss: missing oss section")

    monkeypatch.setattr("src.agents.tenant_profile.archive.get_default", boom)

    _seed_log("acme", 2)
    result = archive_module.archive_log("acme")  # no explicit storage

    assert result.rotated is True
    assert result.uploaded is False
    assert result.pending is True
    state = meta_module.read_meta("acme")
    assert len(state.pending_oss_uploads) == 1


# ── retry_pending_uploads ─────────────────────────────────────────────────


def test_retry_pending_succeeds_and_clears_queue(storage: InMemoryStorage, tmp_path: Path) -> None:
    failing = MagicMock(spec=InMemoryStorage)
    failing.put_object.side_effect = RuntimeError("flaky")

    _seed_log("acme", 2)
    result = archive_module.archive_log("acme", storage=failing)
    assert result.pending is True

    # Now retry with a working storage.
    successes = archive_module.retry_pending_uploads("acme", storage=storage)
    assert successes == 1
    state = meta_module.read_meta("acme")
    assert state.pending_oss_uploads == []
    assert storage.object_exists(result.bucket, result.key)


def test_retry_pending_increments_attempts_on_repeated_failure() -> None:
    failing = MagicMock(spec=InMemoryStorage)
    failing.put_object.side_effect = RuntimeError("flaky")

    _seed_log("acme", 2)
    archive_module.archive_log("acme", storage=failing)

    archive_module.retry_pending_uploads("acme", storage=failing)
    state = meta_module.read_meta("acme")
    assert len(state.pending_oss_uploads) == 1
    assert state.pending_oss_uploads[0].attempts == 2


def test_retry_pending_skips_after_max_attempts() -> None:
    failing = MagicMock(spec=InMemoryStorage)
    failing.put_object.side_effect = RuntimeError("flaky")

    _seed_log("acme", 2)
    archive_module.archive_log("acme", storage=failing)
    # Hammer it past the limit (default 3).
    for _ in range(5):
        archive_module.retry_pending_uploads("acme", storage=failing)

    state = meta_module.read_meta("acme")
    assert len(state.pending_oss_uploads) == 1
    # Attempts cap at the threshold — further retries don't bump the counter
    # because we never call put_object once attempts >= max_retry_attempts.
    assert state.pending_oss_uploads[0].attempts == archive_module.DEFAULT_MAX_RETRY_ATTEMPTS

    # Only original 1 attempt + 2 retries = 3 calls, then the loop short-circuits.
    assert failing.put_object.call_count == archive_module.DEFAULT_MAX_RETRY_ATTEMPTS


def test_retry_pending_drops_entries_when_local_file_disappeared(storage: InMemoryStorage) -> None:
    failing = MagicMock(spec=InMemoryStorage)
    failing.put_object.side_effect = RuntimeError("flaky")

    _seed_log("acme", 2)
    result = archive_module.archive_log("acme", storage=failing)
    assert result.local_path is not None

    # Simulate the operator deleting the local archive.
    archive_module._delete_local_archive_for_tests(result.local_path)

    archive_module.retry_pending_uploads("acme", storage=storage)
    state = meta_module.read_meta("acme")
    assert state.pending_oss_uploads == []
    # And nothing landed in OSS either.
    assert not storage.object_exists(result.bucket, result.key)


def test_archive_log_drains_pending_before_new_rotation(storage: InMemoryStorage) -> None:
    failing = MagicMock(spec=InMemoryStorage)
    failing.put_object.side_effect = RuntimeError("flaky")

    # First archive fails — sits in pending.
    _seed_log("acme", 2)
    first = archive_module.archive_log("acme", storage=failing)
    assert first.pending is True

    # Second archive: storage is now healthy. Both pending AND new should land.
    _seed_log("acme", 3)
    second = archive_module.archive_log("acme", storage=storage)

    assert second.uploaded is True
    state = meta_module.read_meta("acme")
    assert state.pending_oss_uploads == []
    assert storage.object_exists(first.bucket, first.key)
    assert storage.object_exists(second.bucket, second.key)


# ── Meta read/write ────────────────────────────────────────────────────────


def test_meta_read_returns_defaults_when_missing() -> None:
    state = meta_module.read_meta("acme")
    assert state == Meta()


def test_meta_write_then_read_roundtrip() -> None:
    state = Meta(
        last_summarize_ts="2026-04-26T10:00:00Z",
        event_count_since_last=42,
        pending_oss_uploads=[
            PendingUpload(
                local_path="/tmp/a.gz",
                bucket="trademind-chat-session",
                key="tenants/acme/profile/usage_log/20260426-deadbeef.jsonl.gz",
                sha8="deadbeef",
                size_bytes=1024,
                attempts=2,
                last_error="boom",
            )
        ],
    )
    meta_module.write_meta("acme", state)

    rt = meta_module.read_meta("acme")
    assert rt.last_summarize_ts == "2026-04-26T10:00:00Z"
    assert rt.event_count_since_last == 42
    assert len(rt.pending_oss_uploads) == 1
    assert rt.pending_oss_uploads[0].sha8 == "deadbeef"
    assert rt.pending_oss_uploads[0].attempts == 2


def test_meta_invalid_tenant_does_not_crash() -> None:
    # write returns False rather than raising on bad tenant id.
    assert meta_module.write_meta("../escape", Meta()) is False
    assert meta_module.read_meta("../escape") == Meta()


def test_meta_skips_malformed_pending_entries(tmp_path: Path) -> None:
    """A meta.json with a partially-bad pending list should drop bad entries
    rather than reject the whole file."""
    path = meta_module.get_meta_path("acme")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "pending_oss_uploads": [
                    {"this is": "garbage"},  # missing fields
                    {
                        "local_path": "/tmp/x.gz",
                        "bucket": "trademind-chat-session",
                        "key": "tenants/acme/profile/usage_log/20260426-cafebabe.jsonl.gz",
                        "sha8": "cafebabe",
                        "size_bytes": 100,
                        "attempts": 0,
                    },
                ],
            }
        )
    )
    state = meta_module.read_meta("acme")
    assert len(state.pending_oss_uploads) == 1
    assert state.pending_oss_uploads[0].sha8 == "cafebabe"


def test_mutate_serialises_concurrent_callers() -> None:
    """``mutate`` is the right way to read-modify-write under the lock —
    smoke test: two callers each bumping a counter end up at +2, not +1."""

    def bump(meta: Meta) -> Meta:
        meta.event_count_since_last += 1
        return meta

    meta_module.mutate("acme", bump)
    meta_module.mutate("acme", bump)
    assert meta_module.read_meta("acme").event_count_since_last == 2
