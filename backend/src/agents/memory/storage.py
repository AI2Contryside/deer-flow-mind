"""Memory storage abstraction layer with support for multiple backends."""

import copy
import io
import json
import logging
import re
import threading
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Any

from src.config.memory_config import get_memory_config

logger = logging.getLogger(__name__)

# Tenant ids come from a trusted upstream (gateway-issued JWT claim) but are
# interpolated directly into a filesystem path; treat them as untrusted at the
# storage boundary and reject anything that could escape the per-tenant dir.
_TENANT_ID_RE = re.compile(r"[A-Za-z0-9_\-]{1,64}")

_OSS_CONTENT_TYPE = "application/json; charset=utf-8"


class MemoryStorage(ABC):
    """Abstract base class for memory storage backends."""

    @abstractmethod
    def read(self, key: str) -> dict[str, Any] | None:
        """Read memory data from storage.

        Args:
            key: The storage key (e.g., 'memory/tenant_id/memory.json')

        Returns:
            Memory data dictionary, or None if not found.
        """
        pass

    @abstractmethod
    def write(self, key: str, data: dict[str, Any]) -> bool:
        """Write memory data to storage.

        Args:
            key: The storage key
            data: The memory data to store

        Returns:
            True if successful, False otherwise.
        """
        pass

    @abstractmethod
    def exists(self, key: str) -> bool:
        """Check if a key exists in storage.

        Args:
            key: The storage key

        Returns:
            True if exists, False otherwise.
        """
        pass

    @abstractmethod
    def get_lock(self, key: str) -> threading.Lock:
        """Get a lock for the given key to handle concurrent updates.

        Args:
            key: The storage key

        Returns:
            A threading.Lock instance for this key.
        """
        pass


class FileMemoryStorage(MemoryStorage):
    """File-based memory storage implementation."""

    def __init__(self, base_dir: Path | None = None):
        """Initialize the file storage.

        Args:
            base_dir: Base directory for memory files. If None, uses default.
        """
        if base_dir is None:
            from src.config.paths import get_paths

            paths_base = get_paths().base_dir
            config = get_memory_config()
            if config.storage_path:
                # Honour the documented contract on MemoryConfig.storage_path:
                # absolute paths are used as-is, relative paths are resolved
                # against Paths.base_dir (which is the volume-mounted
                # DEER_FLOW_HOME inside Docker). Resolving against cwd here
                # silently dropped tenant shards into the container's writable
                # layer, which was lost on container rebuild.
                candidate = Path(config.storage_path)
                base_dir = candidate if candidate.is_absolute() else paths_base / candidate
            else:
                base_dir = paths_base / "memory"
        self._base_dir = base_dir
        self._locks: dict[str, threading.Lock] = {}
        self._locks_lock = threading.Lock()

    def _get_file_path(self, key: str) -> Path:
        """Convert storage key to file path.

        Args:
            key: Storage key (e.g., 'memory/tenant_id/memory.json')

        Returns:
            Full file path
        """
        return self._base_dir / key

    def read(self, key: str) -> dict[str, Any] | None:
        """Read memory data from file.

        Args:
            key: The storage key

        Returns:
            Memory data dictionary, or None if not found.
        """
        file_path = self._get_file_path(key)
        if not file_path.exists():
            return None

        try:
            with open(file_path, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"Failed to read memory file {key}: {e}")
            return None

    def write(self, key: str, data: dict[str, Any]) -> bool:
        """Write memory data to file.

        Args:
            key: The storage key
            data: The memory data to store

        Returns:
            True if successful, False otherwise.
        """
        file_path = self._get_file_path(key)

        try:
            file_path.parent.mkdir(parents=True, exist_ok=True)

            data["lastUpdated"] = datetime.utcnow().isoformat() + "Z"

            temp_path = file_path.with_suffix(".tmp")
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

            temp_path.replace(file_path)
            return True
        except OSError as e:
            print(f"Failed to write memory file {key}: {e}")
            return False

    def exists(self, key: str) -> bool:
        """Check if a key exists in storage.

        Args:
            key: The storage key

        Returns:
            True if exists, False otherwise.
        """
        return self._get_file_path(key).exists()

    def get_lock(self, key: str) -> threading.Lock:
        """Get a lock for the given key.

        Args:
            key: The storage key

        Returns:
            A threading.Lock instance for this key.
        """
        with self._locks_lock:
            if key not in self._locks:
                self._locks[key] = threading.Lock()
            return self._locks[key]


_storage_instance: FileMemoryStorage | None = None
_storage_lock = threading.Lock()


def get_memory_storage() -> FileMemoryStorage:
    """Get the global memory storage instance.

    Returns:
        The memory storage instance.
    """
    global _storage_instance
    with _storage_lock:
        if _storage_instance is None:
            _storage_instance = FileMemoryStorage()
        return _storage_instance


def set_memory_storage(storage: FileMemoryStorage) -> None:
    """Set the global memory storage instance.

    Args:
        storage: The storage instance to use.
    """
    global _storage_instance
    with _storage_lock:
        _storage_instance = storage


def get_tenant_memory_key(tenant_id: str) -> str:
    """Get the storage key for tenant memory.

    The ``memory/`` segment lives in ``storage_path`` (config), not in the
    key — otherwise we end up with ``{base}/memory/memory/{tenant}/memory.json``
    which is what the previous layout produced inside the container.

    Args:
        tenant_id: The tenant ID

    Returns:
        Storage key for tenant memory: ``<tenant_id>/memory.json``.

    Raises:
        ValueError: tenant_id is empty or contains characters outside
        ``[A-Za-z0-9_-]``. The id is interpolated into a filesystem path,
        so anything else (``..``, ``/``, control chars) could let a caller
        read or overwrite another tenant's memory file.
    """
    if not isinstance(tenant_id, str) or not _TENANT_ID_RE.fullmatch(tenant_id):
        raise ValueError(f"invalid tenant_id for memory key: {tenant_id!r}")
    return f"{tenant_id}/memory.json"


def create_empty_memory() -> dict[str, Any]:
    """Create an empty memory structure.

    Returns:
        Empty memory dictionary.
    """
    return {
        "version": "1.0",
        "lastUpdated": datetime.utcnow().isoformat() + "Z",
        "user": {
            "workContext": {"summary": "", "updatedAt": ""},
            "personalContext": {"summary": "", "updatedAt": ""},
            "topOfMind": {"summary": "", "updatedAt": ""},
        },
        "history": {
            "recentMonths": {"summary": "", "updatedAt": ""},
            "earlierContext": {"summary": "", "updatedAt": ""},
            "longTermBackground": {"summary": "", "updatedAt": ""},
        },
        "facts": [],
    }


def read_memory(tenant_id: str) -> dict[str, Any]:
    """Read memory for a specific tenant.

    Reads the local cache first; on a miss, falls back to OSS and replicates
    the result back to the local cache so subsequent reads stay fast. When
    OSS is unavailable or empty for the tenant, returns a fresh empty
    memory document.

    Args:
        tenant_id: The tenant ID

    Returns:
        Memory data for the tenant
    """
    storage = get_memory_storage()

    memory_key = get_tenant_memory_key(tenant_id)
    memory_data = storage.read(memory_key)
    if memory_data is not None:
        return memory_data

    lock = storage.get_lock(memory_key)
    with lock:
        memory_data = storage.read(memory_key)
        if memory_data is not None:
            return memory_data

        oss_data = read_memory_from_oss(tenant_id)
        if oss_data is None:
            return create_empty_memory()

        try:
            # FileMemoryStorage.write mutates ``lastUpdated`` in place; deepcopy
            # so the dict we return reflects what's actually stored in OSS.
            storage.write(memory_key, copy.deepcopy(oss_data))
        except Exception as exc:  # noqa: BLE001 — local cache is best-effort
            logger.warning(
                "memory: failed to cache OSS data locally for tenant %r: %s",
                tenant_id,
                exc,
            )
        return oss_data


def write_memory(tenant_id: str, data: dict[str, Any]) -> bool:
    """Write memory for a specific tenant.

    Writes through to the local cache first, then mirrors the same payload
    to OSS as a best-effort upload. The local write is the source of truth
    for the agent's hot path; OSS is the durability layer that survives
    container rebuilds and lets sibling services (e.g. the gateway-side FE)
    read the same memory without reaching into DeerFlow's filesystem.

    Args:
        tenant_id: The tenant ID
        data: The memory data to write

    Returns:
        True if the local write succeeded. OSS mirror failures are logged
        and swallowed so a transient OSS outage does not surface as a
        failed memory update.
    """
    storage = get_memory_storage()

    memory_key = get_tenant_memory_key(tenant_id)
    lock = storage.get_lock(memory_key)
    with lock:
        ok = storage.write(memory_key, data)
        if ok:
            # ``storage.write`` mutates ``data`` in place to stamp lastUpdated,
            # so serialising ``data`` here gives a payload byte-identical to
            # what landed on disk.
            try:
                payload = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
            except (TypeError, ValueError) as exc:
                logger.warning(
                    "memory: skipped OSS mirror for tenant %r — payload not serialisable: %s",
                    tenant_id,
                    exc,
                )
            else:
                _mirror_memory_to_oss(tenant_id, payload)
        return ok


def read_memory_from_oss(tenant_id: str) -> dict[str, Any] | None:
    """Pull a memory JSON object from OSS into a dict.

    Returns ``None`` when OSS is unconfigured, the tenant_id is invalid,
    the object is missing, or deserialization fails. Used both internally
    by ``read_memory`` for cache-miss fallback and externally by callers
    (e.g. the gateway-backed FE) that need to read memory across hosts.
    """
    if not isinstance(tenant_id, str) or not _TENANT_ID_RE.fullmatch(tenant_id):
        return None
    storage, bucket, key = _resolve_oss_target(tenant_id)
    if storage is None or bucket is None or key is None:
        return None
    try:
        body = storage.get_object_bytes(bucket, key)
    except Exception as exc:  # noqa: BLE001 — OSS read is best-effort
        logger.debug("memory: OSS get_object failed for tenant %r: %s", tenant_id, exc)
        return None
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        logger.warning("memory: OSS body not valid JSON for tenant %r: %s", tenant_id, exc)
        return None
    if not isinstance(data, dict):
        return None
    return data


def _mirror_memory_to_oss(tenant_id: str, payload: bytes) -> None:
    """Best-effort upload of the freshly-written memory document to OSS."""
    storage, bucket, key = _resolve_oss_target(tenant_id)
    if storage is None or bucket is None or key is None:
        return
    try:
        storage.put_object(
            bucket,
            key,
            io.BytesIO(payload),
            content_type=_OSS_CONTENT_TYPE,
        )
    except Exception as exc:  # noqa: BLE001 — sync is best-effort
        logger.warning("memory: OSS mirror failed for tenant %r: %s", tenant_id, exc)


def _resolve_oss_target(tenant_id: str) -> tuple[Any, str | None, str | None]:
    """Return ``(storage, bucket, key)`` or ``(None, None, None)`` when OSS
    isn't configured. Lazy imports keep this module testable without
    pulling oss2 at module-load time."""
    try:
        from src.storage import oss_client as oss_module
        from src.storage.keys import tenant_memory_key
    except Exception as exc:  # noqa: BLE001
        logger.debug("memory: storage module unavailable (%s)", exc)
        return None, None, None
    try:
        storage = oss_module.get_default()
    except Exception as exc:  # noqa: BLE001 — config missing is fine
        logger.debug("memory: OSS not configured (%s)", exc)
        return None, None, None
    bucket = getattr(getattr(storage, "config", None), "chat_bucket", None) or "trademind-chat-session"
    return storage, bucket, tenant_memory_key(tenant_id)
