"""Append-only JSONL log of ERPNext API observations, per tenant.

The summarizer (M3) will consume these events; the archive layer (M2) will
gzip + upload them after a successful summarize. M1 only provides:

* path resolution (``get_log_path``)
* validated tenant id (mirrors the memory subsystem regex)
* ``append_event`` for the observer
* ``read_events`` and ``count_events`` for tests + future trigger logic
* ``rotate_log`` to swap the live file for an archive sibling

Design contract: see TENANT_PROFILE_DESIGN.md §4.5.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.config.paths import get_paths

logger = logging.getLogger(__name__)

# Tenant ids cross filesystem-path boundaries here — same regex the memory
# subsystem uses (src/agents/memory/storage.py).
_TENANT_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")

_USAGE_LOG_FILENAME = "usage_log.jsonl"
_ARCHIVE_DIRNAME = "usage_log.archive"

# Per-tenant locks. Many threads can be writing for the same tenant if multiple
# skill calls return concurrently. We don't claim crash-safety — only that
# in-process appends won't interleave a half-line.
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _validate_tenant_id(tenant_id: str) -> None:
    if not isinstance(tenant_id, str) or not _TENANT_ID_RE.match(tenant_id):
        raise ValueError(f"invalid tenant_id for usage log: {tenant_id!r}")


def _tenant_dir(tenant_id: str) -> Path:
    _validate_tenant_id(tenant_id)
    return get_paths().base_dir / "tenant_profile" / tenant_id


def get_log_path(tenant_id: str) -> Path:
    """Path to the live (uncompressed) jsonl for ``tenant_id``."""
    return _tenant_dir(tenant_id) / _USAGE_LOG_FILENAME


def get_archive_dir(tenant_id: str) -> Path:
    """Where rotated jsonl.gz copies are kept locally (M2 will populate)."""
    return _tenant_dir(tenant_id) / _ARCHIVE_DIRNAME


def _get_lock(tenant_id: str) -> threading.Lock:
    with _locks_guard:
        lock = _locks.get(tenant_id)
        if lock is None:
            lock = threading.Lock()
            _locks[tenant_id] = lock
        return lock


def append_event(tenant_id: str, event: dict[str, Any]) -> bool:
    """Append a single event line to the tenant's jsonl.

    Returns True on success, False on any failure (disk full, permission,
    serialisation error). Never raises — observer failure must not break the
    caller's main flow (see §11 decision 1).
    """
    try:
        _validate_tenant_id(tenant_id)
        path = get_log_path(tenant_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
    except (ValueError, TypeError) as exc:
        logger.warning("tenant_profile: failed to encode event for tenant %r: %s", tenant_id, exc)
        return False
    except OSError as exc:
        logger.warning("tenant_profile: failed to prepare log path for tenant %r: %s", tenant_id, exc)
        return False

    lock = _get_lock(tenant_id)
    try:
        with lock, open(path, "a", encoding="utf-8") as fh:
            fh.write(line)
        return True
    except OSError as exc:
        logger.warning("tenant_profile: failed to append usage event for tenant %r: %s", tenant_id, exc)
        return False


def read_events(tenant_id: str) -> list[dict[str, Any]]:
    """Read all events from the live jsonl. Returns ``[]`` if no log yet.

    Malformed lines are skipped with a warning rather than raising — a single
    truncated tail shouldn't block a summarize run.
    """
    try:
        path = get_log_path(tenant_id)
    except ValueError:
        return []
    if not path.exists():
        return []

    events: list[dict[str, Any]] = []
    try:
        with open(path, encoding="utf-8") as fh:
            for lineno, raw in enumerate(fh, start=1):
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    events.append(json.loads(raw))
                except json.JSONDecodeError as exc:
                    logger.warning(
                        "tenant_profile: skipping malformed usage log line %d for tenant %r: %s",
                        lineno,
                        tenant_id,
                        exc,
                    )
    except OSError as exc:
        logger.warning("tenant_profile: failed to read usage log for tenant %r: %s", tenant_id, exc)
        return []
    return events


def count_events(tenant_id: str) -> int:
    """Return the number of newline-terminated lines in the live log.

    Cheaper than ``len(read_events(...))`` because it skips JSON parsing.
    Used by trigger evaluation to decide whether to fire the summarizer.
    """
    try:
        path = get_log_path(tenant_id)
    except ValueError:
        return 0
    if not path.exists():
        return 0
    total = 0
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                total += chunk.count(b"\n")
    except OSError as exc:
        logger.warning("tenant_profile: failed to count usage events for tenant %r: %s", tenant_id, exc)
        return 0
    return total


def rotate_log(tenant_id: str) -> Path | None:
    """Atomically move the live jsonl into the archive dir; create empty live file.

    Returns the path of the rotated file, or ``None`` if there was nothing to
    rotate. Caller (M2 archive layer) is responsible for gzipping and OSS
    uploading the rotated file. The rotated filename is timestamp-based so
    concurrent rotations within the same millisecond can still distinguish
    via short-uuid suffix from the storage keys helper.

    The rename happens under the per-tenant lock to avoid interleaving with
    in-flight ``append_event`` calls.
    """
    try:
        _validate_tenant_id(tenant_id)
    except ValueError:
        return None
    src = get_log_path(tenant_id)
    if not src.exists() or src.stat().st_size == 0:
        return None

    archive_dir = get_archive_dir(tenant_id)
    archive_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    # Short suffix to disambiguate same-second rotations; keeps filenames sortable.
    suffix = os.urandom(2).hex()
    rotated = archive_dir / f"usage_log-{stamp}-{suffix}.jsonl"

    lock = _get_lock(tenant_id)
    try:
        with lock:
            # Re-check inside the lock to avoid racing with another rotator.
            if not src.exists() or src.stat().st_size == 0:
                return None
            os.replace(src, rotated)
            # Recreate empty live file so next append doesn't see ENOENT racing.
            src.touch()
        return rotated
    except OSError as exc:
        logger.warning("tenant_profile: failed to rotate usage log for tenant %r: %s", tenant_id, exc)
        return None


def reset_locks_for_tests() -> None:
    """Drop the cached per-tenant locks. Tests only — production keeps them."""
    with _locks_guard:
        _locks.clear()
