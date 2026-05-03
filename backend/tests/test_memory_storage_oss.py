"""Unit tests for the OSS double-write in ``src.agents.memory.storage``.

Covers the same five behaviours we guarantee for ``tenant_profile.store``:

1. Successful local writes are mirrored to OSS at the canonical key.
2. OSS upload failures do NOT propagate — local writes still report success.
3. ``read_memory_from_oss`` deserialises the OSS body into a dict.
4. ``read_memory`` falls back to OSS when the local cache is empty and
   replicates the result locally.
5. Invalid tenant ids are rejected before touching OSS.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.agents.memory import storage as storage_module
from src.agents.memory.storage import (
    FileMemoryStorage,
    create_empty_memory,
    read_memory,
    read_memory_from_oss,
    set_memory_storage,
    write_memory,
)

# OSS double-write is opt-in on environments where oss2 is installed. Skip
# the whole module gracefully on bare dev/test machines without the SDK
# rather than failing collection.
oss_module = pytest.importorskip("src.storage.oss_client")


@pytest.fixture
def fresh_memory_storage(tmp_path: Path) -> FileMemoryStorage:
    """Anchor the memory storage to a clean temp dir and reset the singleton."""
    storage = FileMemoryStorage(base_dir=tmp_path / "memory")
    set_memory_storage(storage)
    yield storage
    set_memory_storage(None)  # type: ignore[arg-type]


@pytest.fixture
def fake_oss() -> oss_module.InMemoryStorage:
    storage = oss_module.InMemoryStorage()
    oss_module.set_default(storage)
    yield storage
    oss_module.set_default(None)  # type: ignore[arg-type]


@pytest.mark.unit
def test_write_memory_mirrors_to_oss(fresh_memory_storage: FileMemoryStorage, fake_oss: oss_module.InMemoryStorage) -> None:
    payload = create_empty_memory()
    payload["facts"].append(
        {
            "id": "fact_test",
            "content": "user prefers Chinese responses",
            "category": "preference",
            "confidence": 0.95,
            "createdAt": "2026-05-03T00:00:00Z",
            "source": "test-thread",
        }
    )

    ok = write_memory("42", payload)

    assert ok is True
    expected_key = "tenants/42/memory/memory.json"
    assert expected_key in fake_oss.list_keys("trademind-chat-session")
    oss_text = fake_oss.get_text("trademind-chat-session", expected_key)
    oss_payload = json.loads(oss_text)
    assert oss_payload["facts"][0]["content"] == "user prefers Chinese responses"
    # The local file and OSS object must agree byte-for-byte (both are
    # produced from the same in-memory dict).
    local_text = (fresh_memory_storage._base_dir / "42" / "memory.json").read_text(encoding="utf-8")
    assert json.loads(local_text) == oss_payload


@pytest.mark.unit
def test_oss_failure_does_not_break_local_write(
    fresh_memory_storage: FileMemoryStorage,
) -> None:
    """If OSS upload raises, the local write is still considered successful."""

    class BoomStorage:
        @property
        def config(self) -> object:
            class Cfg:
                chat_bucket = "trademind-chat-session"

            return Cfg()

        def put_object(self, *args: object, **kwargs: object) -> None:
            raise RuntimeError("simulated network error")

        def get_object_bytes(self, bucket: str, key: str) -> bytes:
            raise FileNotFoundError(f"{bucket}/{key}")

    oss_module.set_default(BoomStorage())  # type: ignore[arg-type]
    try:
        ok = write_memory("42", create_empty_memory())
        assert ok is True
        # Local write went through.
        assert (fresh_memory_storage._base_dir / "42" / "memory.json").exists()
    finally:
        oss_module.set_default(None)  # type: ignore[arg-type]


@pytest.mark.unit
def test_read_memory_from_oss_returns_dict(fresh_memory_storage: FileMemoryStorage, fake_oss: oss_module.InMemoryStorage) -> None:
    fake_oss.put_text(
        "trademind-chat-session",
        "tenants/99/memory/memory.json",
        json.dumps({"version": "1.0", "user": {}, "history": {}, "facts": []}),
    )

    result = read_memory_from_oss("99")

    assert result is not None
    assert result["version"] == "1.0"


@pytest.mark.unit
def test_read_memory_from_oss_returns_none_when_missing(fresh_memory_storage: FileMemoryStorage, fake_oss: oss_module.InMemoryStorage) -> None:
    assert read_memory_from_oss("does-not-exist") is None


@pytest.mark.unit
def test_read_memory_from_oss_returns_none_when_not_configured(fresh_memory_storage: FileMemoryStorage, monkeypatch: pytest.MonkeyPatch) -> None:
    """Unconfigured OSS must not raise; callers fall back to empty memory."""

    def boom() -> object:
        raise RuntimeError("oss not configured")

    monkeypatch.setattr(oss_module, "get_default", boom)
    assert read_memory_from_oss("42") is None


@pytest.mark.unit
def test_read_memory_falls_back_to_oss_when_local_missing(fresh_memory_storage: FileMemoryStorage, fake_oss: oss_module.InMemoryStorage) -> None:
    """If the local cache has no file, ``read_memory`` should pull from OSS
    and replicate locally for subsequent fast reads."""
    canonical = create_empty_memory()
    canonical["facts"].append(
        {
            "id": "fact_seed",
            "content": "seeded from OSS",
            "category": "context",
            "confidence": 0.9,
            "createdAt": "2026-05-03T00:00:00Z",
            "source": "seed",
        }
    )
    fake_oss.put_text(
        "trademind-chat-session",
        "tenants/77/memory/memory.json",
        json.dumps(canonical),
    )

    result = read_memory("77")

    assert any(f["id"] == "fact_seed" for f in result["facts"])
    # Local cache should now exist so the next read is a hot-path read.
    local = fresh_memory_storage._base_dir / "77" / "memory.json"
    assert local.exists()
    assert any(f["id"] == "fact_seed" for f in json.loads(local.read_text(encoding="utf-8"))["facts"])


@pytest.mark.unit
def test_read_memory_returns_empty_when_neither_local_nor_oss(fresh_memory_storage: FileMemoryStorage, fake_oss: oss_module.InMemoryStorage) -> None:
    result = read_memory("brand-new-tenant")

    assert result["facts"] == []
    assert result["version"] == "1.0"


@pytest.mark.unit
def test_read_memory_uses_local_cache_first(fresh_memory_storage: FileMemoryStorage, fake_oss: oss_module.InMemoryStorage) -> None:
    """When the local file exists, OSS is never consulted on read."""
    local_payload = create_empty_memory()
    local_payload["facts"].append(
        {
            "id": "fact_local",
            "content": "local wins",
            "category": "context",
            "confidence": 0.9,
            "createdAt": "2026-05-03T00:00:00Z",
            "source": "local",
        }
    )
    write_memory("33", local_payload)
    # Stash a different payload in OSS to prove we're reading from local.
    fake_oss.put_text(
        "trademind-chat-session",
        "tenants/33/memory/memory.json",
        json.dumps({"version": "1.0", "user": {}, "history": {}, "facts": [{"id": "fact_oss"}]}),
    )

    result = read_memory("33")

    assert any(f["id"] == "fact_local" for f in result["facts"])
    assert not any(f.get("id") == "fact_oss" for f in result["facts"])


@pytest.mark.unit
def test_invalid_tenant_id_skips_oss(fresh_memory_storage: FileMemoryStorage, fake_oss: oss_module.InMemoryStorage) -> None:
    """Tenant IDs failing the regex must NOT touch OSS — guard against
    callers passing FE input straight through."""
    with pytest.raises(ValueError):
        write_memory("../../etc/passwd", create_empty_memory())
    assert fake_oss.list_keys("trademind-chat-session") == []


@pytest.mark.unit
def test_read_memory_from_oss_rejects_invalid_tenant(fresh_memory_storage: FileMemoryStorage, fake_oss: oss_module.InMemoryStorage) -> None:
    assert read_memory_from_oss("../../etc/passwd") is None
    assert read_memory_from_oss("") is None
    assert read_memory_from_oss(None) is None  # type: ignore[arg-type]


@pytest.mark.unit
def test_read_memory_from_oss_handles_corrupt_json(fresh_memory_storage: FileMemoryStorage, fake_oss: oss_module.InMemoryStorage) -> None:
    fake_oss.put_text(
        "trademind-chat-session",
        "tenants/55/memory/memory.json",
        "not valid json{",
    )

    assert read_memory_from_oss("55") is None


@pytest.mark.unit
def test_mirror_skipped_when_local_write_fails(
    fresh_memory_storage: FileMemoryStorage,
    fake_oss: oss_module.InMemoryStorage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the local write fails, OSS must not be touched — the upload
    payload would be incomplete and could overwrite a healthy OSS object."""

    def fail_write(self: FileMemoryStorage, key: str, data: dict) -> bool:
        return False

    monkeypatch.setattr(storage_module.FileMemoryStorage, "write", fail_write)

    ok = write_memory("42", create_empty_memory())

    assert ok is False
    assert fake_oss.list_keys("trademind-chat-session") == []
