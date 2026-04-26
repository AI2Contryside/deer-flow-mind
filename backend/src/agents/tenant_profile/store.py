"""Read/write the per-tenant ``profile.json`` produced by the summarizer.

The schema lives in ``summarizer/schema.py``. This module is responsible
for the on-disk layout and lock-protected atomic writes only — it doesn't
reach into pydantic models so summarizer output and a hand-edited profile
both flow through the same path.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from pathlib import Path
from typing import Any

from src.config.paths import get_paths

logger = logging.getLogger(__name__)

_TENANT_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
_PROFILE_FILENAME = "profile.json"

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _validate_tenant_id(tenant_id: str) -> None:
    if not isinstance(tenant_id, str) or not _TENANT_ID_RE.match(tenant_id):
        raise ValueError(f"invalid tenant_id for tenant_profile store: {tenant_id!r}")


def _tenant_dir(tenant_id: str) -> Path:
    _validate_tenant_id(tenant_id)
    return get_paths().base_dir / "tenant_profile" / tenant_id


def get_profile_path(tenant_id: str) -> Path:
    return _tenant_dir(tenant_id) / _PROFILE_FILENAME


def _get_lock(tenant_id: str) -> threading.Lock:
    with _locks_guard:
        lock = _locks.get(tenant_id)
        if lock is None:
            lock = threading.Lock()
            _locks[tenant_id] = lock
        return lock


def read_profile(tenant_id: str) -> dict[str, Any] | None:
    """Return the persisted profile dict, or ``None`` if missing/unreadable.

    Best-effort: malformed JSON is logged and treated as absent so callers
    can fall back to "first run" behaviour rather than crashing.
    """
    try:
        path = get_profile_path(tenant_id)
    except ValueError:
        return None
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("tenant_profile: failed to read profile.json for tenant %r: %s", tenant_id, exc)
        return None
    if not isinstance(data, dict):
        return None
    return data


def write_profile(tenant_id: str, profile: dict[str, Any]) -> bool:
    """Atomically write ``profile`` under the per-tenant lock. Best-effort."""
    try:
        _validate_tenant_id(tenant_id)
        path = get_profile_path(tenant_id)
        path.parent.mkdir(parents=True, exist_ok=True)
    except (ValueError, OSError) as exc:
        logger.warning("tenant_profile: failed to prepare profile path for tenant %r: %s", tenant_id, exc)
        return False

    tmp = path.with_suffix(".tmp")
    try:
        with _get_lock(tenant_id), open(tmp, "w", encoding="utf-8") as fh:
            json.dump(profile, fh, indent=2, ensure_ascii=False)
        tmp.replace(path)
        return True
    except OSError as exc:
        logger.warning("tenant_profile: failed to write profile.json for tenant %r: %s", tenant_id, exc)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def reset_locks_for_tests() -> None:
    with _locks_guard:
        _locks.clear()
