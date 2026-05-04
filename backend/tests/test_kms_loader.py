"""Unit tests for src.config.kms_loader (KMS Secrets Manager resolver)."""

from __future__ import annotations

import pytest

from src.config import kms_loader
from src.config.kms_loader import KMSResolver, is_kms_ref, strip_kms_prefix


class _FakeKMSClient:
    """Minimal SecretClient stub that records calls and returns canned values."""

    def __init__(self, values: dict[str, str | None]) -> None:
        self.values = values
        self.calls: list[str] = []

    def get_secret_value(self, secret_name: str) -> str | None:
        self.calls.append(secret_name)
        return self.values.get(secret_name)


def test_is_kms_ref_detects_prefix():
    assert is_kms_ref("kms://deerflow/openai")
    assert not is_kms_ref("$OPENAI_API_KEY")
    assert not is_kms_ref("kms-not-a-ref")
    assert not is_kms_ref(None)
    assert not is_kms_ref(123)


def test_strip_kms_prefix_removes_only_when_present():
    assert strip_kms_prefix("kms://x/y") == "x/y"
    assert strip_kms_prefix("plain") == "plain"


def test_resolve_passes_through_non_refs():
    fake = _FakeKMSClient(values={})
    r = KMSResolver(client=fake, enabled=True)
    assert r.resolve("plain-value") == "plain-value"
    assert fake.calls == []


def test_resolve_caches_within_ttl():
    fake = _FakeKMSClient(values={"deerflow/openai": "sk-xxx"})
    r = KMSResolver(client=fake, enabled=True, ttl_seconds=60)
    for _ in range(3):
        assert r.resolve("kms://deerflow/openai") == "sk-xxx"
    assert fake.calls == ["deerflow/openai"]


def test_resolve_disabled_raises_for_ref():
    r = KMSResolver(client=_FakeKMSClient(values={}), enabled=False)
    with pytest.raises(RuntimeError, match="KMS disabled"):
        r.resolve("kms://anything")


def test_resolve_empty_name_raises():
    r = KMSResolver(client=_FakeKMSClient(values={}), enabled=True)
    with pytest.raises(ValueError, match="empty secret name"):
        r.resolve("kms://")


def test_resolve_empty_body_raises():
    fake = _FakeKMSClient(values={})  # name not present -> body=None
    r = KMSResolver(client=fake, enabled=True)
    with pytest.raises(RuntimeError, match="empty body"):
        r.resolve("kms://does/not/exist")


def test_resolve_in_config_recurses_dict_and_list():
    fake = _FakeKMSClient(values={
        "deerflow/openai": "sk-1",
        "deerflow/postgres": "postgres://u:p@h/db",
    })
    r = KMSResolver(client=fake, enabled=True)

    config = {
        "models": [
            {"name": "openai", "api_key": "kms://deerflow/openai"},
            {"name": "deepseek", "api_key": "plain-key"},
        ],
        "checkpointer": {"connection_string": "kms://deerflow/postgres"},
        "port": 8001,
    }
    resolved = r.resolve_in_config(config)
    assert resolved["models"][0]["api_key"] == "sk-1"
    assert resolved["models"][1]["api_key"] == "plain-key"
    assert resolved["checkpointer"]["connection_string"] == "postgres://u:p@h/db"
    assert resolved["port"] == 8001


def test_default_resolver_uses_module_level_function(monkeypatch):
    """resolve_kms_value() should delegate to get_default_resolver()."""
    fake = _FakeKMSClient(values={"deerflow/jwt": "j-secret"})
    fake_resolver = KMSResolver(client=fake, enabled=True)

    monkeypatch.setattr(kms_loader, "_default_resolver", fake_resolver)
    assert kms_loader.resolve_kms_value("kms://deerflow/jwt") == "j-secret"
    monkeypatch.setattr(kms_loader, "_default_resolver", None)


def test_app_config_resolve_env_variables_handles_kms(monkeypatch):
    """app_config.AppConfig.resolve_env_variables wires through to KMS loader."""
    from src.config import app_config

    fake = _FakeKMSClient(values={
        "deerflow/openai": "sk-from-kms",
        "deerflow/anthropic": "claude-from-kms",
    })
    fake_resolver = KMSResolver(client=fake, enabled=True)
    monkeypatch.setattr(kms_loader, "_default_resolver", fake_resolver)

    monkeypatch.setenv("DEEPSEEK_API_KEY", "kms://deerflow/openai")  # env -> kms indirection

    config_in = {
        "models": [
            {"name": "openai", "api_key": "kms://deerflow/openai"},
            {"name": "anthropic", "api_key": "kms://deerflow/anthropic"},
            {"name": "deepseek", "api_key": "$DEEPSEEK_API_KEY"},
        ],
    }
    out = app_config.AppConfig.resolve_env_variables(config_in)
    assert out["models"][0]["api_key"] == "sk-from-kms"
    assert out["models"][1]["api_key"] == "claude-from-kms"
    assert out["models"][2]["api_key"] == "sk-from-kms"  # via $VAR -> kms

    monkeypatch.setattr(kms_loader, "_default_resolver", None)
