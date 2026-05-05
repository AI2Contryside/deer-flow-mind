"""Unit tests for the OSS double-write in tenant_profile.store."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.agents.tenant_profile import store as store_module
from src.config import paths as paths_module

# OSS double-write is opt-in on environments where oss2 is installed. Skip
# the whole module gracefully on bare dev/test machines without the SDK
# rather than failing collection.
oss_module = pytest.importorskip("src.storage.oss_client")


@pytest.fixture
def tmp_paths(tmp_path: Path) -> Path:
    """Re-anchor the runtime base_dir so each test gets a clean filesystem.

    Paths is a lazy singleton; reach into the module to swap in a Paths
    instance rooted at the pytest tmp dir, then restore on teardown.
    """
    original = paths_module._paths
    paths_module._paths = paths_module.Paths(base_dir=tmp_path)
    store_module.reset_locks_for_tests()
    yield tmp_path
    paths_module._paths = original
    store_module.reset_locks_for_tests()


@pytest.fixture
def fake_oss() -> oss_module.InMemoryStorage:
    storage = oss_module.InMemoryStorage()
    oss_module.set_default(storage)
    yield storage
    oss_module.set_default(None)  # type: ignore[arg-type]


@pytest.mark.unit
def test_write_profile_mirrors_to_oss(tmp_paths: Path, fake_oss: oss_module.InMemoryStorage) -> None:
    profile = {
        "schema_version": 3,
        "tenant_id": "42",
        "summary": "test",
        "facts": {"company": {"name": "Acme"}},
    }

    ok = store_module.write_profile("42", profile)

    assert ok is True
    # Local file lands where get_profile_path says.
    local_text = store_module.get_profile_path("42").read_text(encoding="utf-8")
    assert json.loads(local_text)["facts"]["company"]["name"] == "Acme"
    # OSS object lands at the canonical key.
    expected_key = "tenants/42/profile/profile.json"
    assert expected_key in fake_oss.list_keys("trademind-chat-session")
    oss_text = fake_oss.get_text("trademind-chat-session", expected_key)
    assert json.loads(oss_text) == profile


@pytest.mark.unit
def test_oss_failure_does_not_break_local_write(tmp_paths: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """If OSS upload raises, the local write is still considered successful."""

    class BoomStorage:
        @property
        def config(self) -> object:
            class Cfg:
                chat_bucket = "trademind-chat-session"

            return Cfg()

        def put_object(self, *args: object, **kwargs: object) -> None:
            raise RuntimeError("simulated network error")

    oss_module.set_default(BoomStorage())  # type: ignore[arg-type]
    try:
        ok = store_module.write_profile("42", {"schema_version": 3, "tenant_id": "42", "summary": "x", "facts": {}})
        assert ok is True
        # Local write went through.
        local_text = store_module.get_profile_path("42").read_text(encoding="utf-8")
        assert json.loads(local_text)["tenant_id"] == "42"
    finally:
        oss_module.set_default(None)  # type: ignore[arg-type]


@pytest.mark.unit
def test_read_profile_from_oss_returns_dict(tmp_paths: Path, fake_oss: oss_module.InMemoryStorage) -> None:
    fake_oss.put_text(
        "trademind-chat-session",
        "tenants/99/profile/profile.json",
        json.dumps({"schema_version": 3, "tenant_id": "99", "summary": "from-oss"}),
    )

    profile = store_module.read_profile_from_oss("99")

    assert profile is not None
    assert profile["tenant_id"] == "99"
    assert profile["summary"] == "from-oss"


@pytest.mark.unit
def test_read_profile_from_oss_returns_none_when_missing(tmp_paths: Path, fake_oss: oss_module.InMemoryStorage) -> None:
    profile = store_module.read_profile_from_oss("does-not-exist")
    assert profile is None


@pytest.mark.unit
def test_read_profile_from_oss_returns_none_when_not_configured(tmp_paths: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When OSS isn't configured (e.g. local dev), the helper returns None
    rather than raising — callers fall back to local file reads."""

    def boom() -> object:
        raise RuntimeError("oss not configured")

    monkeypatch.setattr(oss_module, "get_default", boom)
    profile = store_module.read_profile_from_oss("42")
    assert profile is None


@pytest.mark.unit
def test_invalid_tenant_id_skips_oss(tmp_paths: Path, fake_oss: oss_module.InMemoryStorage) -> None:
    """Tenant IDs failing the regex must NOT touch OSS — guard against
    callers passing FE input straight through."""
    ok = store_module.write_profile("../../etc/passwd", {"schema_version": 3, "tenant_id": "x", "summary": "", "facts": {}})
    assert ok is False
    # No OSS key was created.
    assert fake_oss.list_keys("trademind-chat-session") == []
