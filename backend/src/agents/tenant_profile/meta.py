"""Per-tenant ``meta.json`` — archive bookkeeping plus M3 trigger state.

This file holds metadata that the various tenant_profile layers share:
  * ``last_summarize_ts`` — when M3 last ran (or null)
  * ``schema_version`` — bumped by M4 if the profile schema rev changes
  * ``pending_oss_uploads`` — entries the archive layer couldn't ship to OSS
    yet (each item: local path, sha8, attempts, last_error)

M2 only writes ``pending_oss_uploads`` and ``schema_version``; M3/M4 will
extend the same file. Atomic writes via temp-file + rename, mirroring the
memory subsystem's pattern.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from src.config.paths import get_paths

logger = logging.getLogger(__name__)

_TENANT_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
_META_FILENAME = "meta.json"

SCHEMA_VERSION = 1

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


@dataclass(frozen=True)
class PendingUpload:
    """A gzipped log file that hasn't successfully landed in OSS yet."""

    local_path: str
    bucket: str
    key: str
    sha8: str
    size_bytes: int
    attempts: int = 0
    last_error: str | None = None
    first_attempt_at: str | None = None
    last_attempt_at: str | None = None


@dataclass
class Meta:
    """Mutable in-memory view of meta.json. Use the read/update helpers."""

    schema_version: int = SCHEMA_VERSION
    last_summarize_ts: str | None = None
    last_summarize_error: str | None = None
    event_count_since_last: int = 0
    pending_oss_uploads: list[PendingUpload] = field(default_factory=list)


def _validate_tenant_id(tenant_id: str) -> None:
    if not isinstance(tenant_id, str) or not _TENANT_ID_RE.match(tenant_id):
        raise ValueError(f"invalid tenant_id for tenant_profile meta: {tenant_id!r}")


def _tenant_dir(tenant_id: str) -> Path:
    _validate_tenant_id(tenant_id)
    return get_paths().base_dir / "tenant_profile" / tenant_id


def get_meta_path(tenant_id: str) -> Path:
    return _tenant_dir(tenant_id) / _META_FILENAME


def _get_lock(tenant_id: str) -> threading.Lock:
    with _locks_guard:
        lock = _locks.get(tenant_id)
        if lock is None:
            lock = threading.Lock()
            _locks[tenant_id] = lock
        return lock


def _decode_pending(raw: Any) -> list[PendingUpload]:
    if not isinstance(raw, list):
        return []
    out: list[PendingUpload] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        try:
            out.append(
                PendingUpload(
                    local_path=str(entry["local_path"]),
                    bucket=str(entry["bucket"]),
                    key=str(entry["key"]),
                    sha8=str(entry["sha8"]),
                    size_bytes=int(entry.get("size_bytes", 0)),
                    attempts=int(entry.get("attempts", 0)),
                    last_error=entry.get("last_error"),
                    first_attempt_at=entry.get("first_attempt_at"),
                    last_attempt_at=entry.get("last_attempt_at"),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning("tenant_profile: skipping malformed pending_oss_uploads entry: %s", exc)
    return out


def read_meta(tenant_id: str) -> Meta:
    """Return current meta. Returns defaults when the file doesn't exist yet."""
    try:
        path = get_meta_path(tenant_id)
    except ValueError:
        return Meta()
    if not path.exists():
        return Meta()
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("tenant_profile: failed to read meta for tenant %r: %s", tenant_id, exc)
        return Meta()
    if not isinstance(data, dict):
        return Meta()
    return Meta(
        schema_version=int(data.get("schema_version", SCHEMA_VERSION)),
        last_summarize_ts=data.get("last_summarize_ts"),
        last_summarize_error=data.get("last_summarize_error"),
        event_count_since_last=int(data.get("event_count_since_last", 0)),
        pending_oss_uploads=_decode_pending(data.get("pending_oss_uploads")),
    )


def write_meta(tenant_id: str, meta: Meta) -> bool:
    """Atomically replace meta.json with the contents of ``meta``.

    Best-effort: returns False on failure but never raises. Caller serialises
    via ``mutate``.
    """
    try:
        _validate_tenant_id(tenant_id)
        path = get_meta_path(tenant_id)
        path.parent.mkdir(parents=True, exist_ok=True)
    except (ValueError, OSError) as exc:
        logger.warning("tenant_profile: failed to prepare meta path for tenant %r: %s", tenant_id, exc)
        return False

    payload = {
        "schema_version": meta.schema_version,
        "last_summarize_ts": meta.last_summarize_ts,
        "last_summarize_error": meta.last_summarize_error,
        "event_count_since_last": meta.event_count_since_last,
        "pending_oss_uploads": [asdict(p) for p in meta.pending_oss_uploads],
    }

    tmp = path.with_suffix(".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)
        tmp.replace(path)
        return True
    except OSError as exc:
        logger.warning("tenant_profile: failed to write meta for tenant %r: %s", tenant_id, exc)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def mutate(tenant_id: str, fn: Any) -> Meta:
    """Read-modify-write meta under the per-tenant lock.

    ``fn`` receives the current ``Meta`` and may mutate it in place; the
    returned (possibly same) ``Meta`` is written back. Returns the resulting
    ``Meta`` even if the write failed — callers that need to observe write
    failure should use ``write_meta`` directly.
    """
    lock = _get_lock(tenant_id)
    with lock:
        current = read_meta(tenant_id)
        result = fn(current)
        new_meta = result if isinstance(result, Meta) else current
        write_meta(tenant_id, new_meta)
        return new_meta


def reset_locks_for_tests() -> None:
    with _locks_guard:
        _locks.clear()
