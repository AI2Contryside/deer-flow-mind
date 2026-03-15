"""Memory storage abstraction layer with support for multiple backends."""

import json
import os
import threading
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Any

from src.config.memory_config import get_memory_config


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
            config = get_memory_config()
            if config.storage_path:
                base_dir = Path(config.storage_path)
            else:
                from src.config.paths import get_paths
                base_dir = get_paths().base_dir / "memory"
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

    Args:
        tenant_id: The tenant ID

    Returns:
        Storage key for tenant memory
    """
    return f"memory/{tenant_id}/memory.json"


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

    Args:
        tenant_id: The tenant ID

    Returns:
        Memory data for the tenant
    """
    storage = get_memory_storage()

    memory_key = get_tenant_memory_key(tenant_id)
    memory_data = storage.read(memory_key)
    if memory_data is None:
        memory_data = create_empty_memory()

    return memory_data


def write_memory(tenant_id: str, data: dict[str, Any]) -> bool:
    """Write memory for a specific tenant.

    Args:
        tenant_id: The tenant ID
        data: The memory data to write

    Returns:
        True if successful
    """
    storage = get_memory_storage()

    memory_key = get_tenant_memory_key(tenant_id)
    lock = storage.get_lock(memory_key)
    with lock:
        return storage.write(memory_key, data)
