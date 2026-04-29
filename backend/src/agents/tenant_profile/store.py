"""Read/write the per-tenant ``profile.json`` produced by the summarizer.

The schema lives in ``summarizer/schema.py``. This module is responsible
for the on-disk layout and lock-protected atomic writes only — it doesn't
reach into pydantic models so summarizer output and a hand-edited profile
both flow through the same path.

OSS mirroring (best-effort): every successful local write is followed by
an upload to ``trademind-chat-session`` under ``tenants/<tid>/profile/profile.json``
so trademind-backend's gateway can serve the FE settings page off OSS
without reaching into DeerFlow's local filesystem. Sync failures are
logged and swallowed — the local file remains source of truth for the
AI's hot path, and the next successful write rolls everything forward.
"""

from __future__ import annotations

import io
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
_OSS_CONTENT_TYPE = "application/json; charset=utf-8"

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

    Profiles persisted under earlier ``schema_version`` values are auto-upgraded
    to the current schema in-memory; the caller is responsible for persisting
    the upgrade if desired.
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
    # Lazy import to avoid circulars: schema lives under summarizer/.
    from src.agents.tenant_profile.summarizer.schema import upgrade_profile_dict

    return upgrade_profile_dict(data)


def write_profile(tenant_id: str, profile: dict[str, Any]) -> bool:
    """Atomically write ``profile`` under the per-tenant lock. Best-effort.

    On a successful local write, also pushes a copy to the configured OSS
    bucket — the gateway-backed FE settings page reads profile.json
    directly from OSS so DeerFlow doesn't need to host an HTTP read API.
    OSS upload failures are logged and ignored; the next successful write
    re-uploads.
    """
    try:
        _validate_tenant_id(tenant_id)
        path = get_profile_path(tenant_id)
        path.parent.mkdir(parents=True, exist_ok=True)
    except (ValueError, OSError) as exc:
        logger.warning("tenant_profile: failed to prepare profile path for tenant %r: %s", tenant_id, exc)
        return False

    payload = json.dumps(profile, indent=2, ensure_ascii=False)
    tmp = path.with_suffix(".tmp")
    try:
        with _get_lock(tenant_id), open(tmp, "w", encoding="utf-8") as fh:
            fh.write(payload)
        tmp.replace(path)
    except OSError as exc:
        logger.warning("tenant_profile: failed to write profile.json for tenant %r: %s", tenant_id, exc)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False

    _mirror_profile_to_oss(tenant_id, payload)
    return True


def read_profile_from_oss(tenant_id: str) -> dict[str, Any] | None:
    """Pull a profile JSON object from OSS into a dict.

    Used by the gateway-side FE flow when local DeerFlow state isn't
    accessible (e.g. backend service running on a different host). The
    reader does NOT auto-upgrade schema_version — callers can pipe through
    ``upgrade_profile_dict`` if they want the typed view.

    Returns ``None`` when OSS is unconfigured, the object is missing, or
    deserialization fails.
    """
    try:
        _validate_tenant_id(tenant_id)
    except ValueError:
        return None
    storage, bucket, key = _resolve_oss_target(tenant_id)
    if storage is None or bucket is None or key is None:
        return None
    try:
        body = storage.get_object_bytes(bucket, key)
    except Exception as exc:  # noqa: BLE001 — OSS read is best-effort
        logger.warning(
            "tenant_profile: OSS get_object failed for tenant %r: %s", tenant_id, exc
        )
        return None
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        logger.warning("tenant_profile: OSS body not valid JSON for tenant %r: %s", tenant_id, exc)
        return None
    if not isinstance(data, dict):
        return None
    return data


def _mirror_profile_to_oss(tenant_id: str, payload: str) -> None:
    """Best-effort upload of the freshly-written profile to OSS."""
    storage, bucket, key = _resolve_oss_target(tenant_id)
    if storage is None or bucket is None or key is None:
        return
    try:
        storage.put_object(
            bucket,
            key,
            io.BytesIO(payload.encode("utf-8")),
            content_type=_OSS_CONTENT_TYPE,
        )
    except Exception as exc:  # noqa: BLE001 — sync is best-effort
        logger.warning(
            "tenant_profile: OSS mirror failed for tenant %r: %s", tenant_id, exc
        )


def _resolve_oss_target(tenant_id: str) -> tuple[Any, str | None, str | None]:
    """Return (storage, bucket, key) or ``(None, None, None)`` when OSS
    isn't configured. Lazy imports keep the module testable without
    pulling oss2 at module-load time."""
    try:
        from src.storage import oss_client as oss_module
        from src.storage.keys import tenant_profile_key
    except Exception as exc:  # noqa: BLE001
        logger.debug("tenant_profile: storage module unavailable (%s)", exc)
        return None, None, None
    try:
        storage = oss_module.get_default()
    except Exception as exc:  # noqa: BLE001 — config missing is fine
        logger.debug("tenant_profile: OSS not configured (%s)", exc)
        return None, None, None
    bucket = getattr(getattr(storage, "config", None), "chat_bucket", None)
    if not bucket:
        # Fall back to a hard-coded bucket name only if config doesn't expose
        # one — keeps tests with InMemoryStorage from breaking.
        bucket = "trademind-chat-session"
    return storage, bucket, tenant_profile_key(tenant_id)


def reset_locks_for_tests() -> None:
    with _locks_guard:
        _locks.clear()
