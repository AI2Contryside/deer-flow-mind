"""Tests for APP_ENV-aware config path resolution.

Covers both AppConfig (config.<env>.yaml) and ExtensionsConfig
(extensions_config.<env>.json).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.config.app_config import AppConfig, _resolve_env
from src.config.extensions_config import ExtensionsConfig


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


@pytest.fixture
def cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Run each test from an isolated temp directory.

    The resolvers walk Path(os.getcwd()) and its parent, so a fresh tmp_path
    keeps the lookup deterministic and isolated from the real workspace.
    """
    monkeypatch.chdir(tmp_path)
    return tmp_path


# --- _resolve_env --------------------------------------------------------


@pytest.mark.parametrize(
    "raw, want",
    [
        (None, "dev"),
        ("", "dev"),
        ("dev", "dev"),
        ("PROD", "prod"),
        ("  staging  ", "staging"),
    ],
)
def test_resolve_env(monkeypatch: pytest.MonkeyPatch, raw: str | None, want: str) -> None:
    if raw is None:
        monkeypatch.delenv("APP_ENV", raising=False)
    else:
        monkeypatch.setenv("APP_ENV", raw)
    assert _resolve_env() == want


# --- AppConfig.resolve_config_path --------------------------------------


def test_appconfig_picks_env_specific_file(cwd: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEER_FLOW_CONFIG_PATH", raising=False)
    _write(cwd / "config.yaml", "marker: base\n")
    _write(cwd / "config.dev.yaml", "marker: dev\n")
    _write(cwd / "config.prod.yaml", "marker: prod\n")

    monkeypatch.setenv("APP_ENV", "prod")
    resolved = AppConfig.resolve_config_path()
    assert resolved == (cwd / "config.prod.yaml")


def test_appconfig_defaults_to_dev_when_app_env_unset(cwd: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("DEER_FLOW_CONFIG_PATH", raising=False)
    _write(cwd / "config.yaml", "marker: base\n")
    _write(cwd / "config.dev.yaml", "marker: dev\n")

    resolved = AppConfig.resolve_config_path()
    assert resolved == (cwd / "config.dev.yaml")


def test_appconfig_falls_back_to_base_when_env_file_missing(cwd: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEER_FLOW_CONFIG_PATH", raising=False)
    _write(cwd / "config.yaml", "marker: base\n")
    # no config.prod.yaml on purpose

    monkeypatch.setenv("APP_ENV", "prod")
    resolved = AppConfig.resolve_config_path()
    assert resolved == (cwd / "config.yaml")


def test_appconfig_explicit_path_wins_over_app_env(cwd: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "prod")
    explicit = cwd / "explicit.yaml"
    _write(explicit, "marker: explicit\n")
    _write(cwd / "config.prod.yaml", "marker: prod\n")

    resolved = AppConfig.resolve_config_path(str(explicit))
    assert resolved == explicit


def test_appconfig_env_path_var_wins_over_app_env(cwd: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    explicit = cwd / "via_env_var.yaml"
    _write(explicit, "marker: env\n")
    _write(cwd / "config.prod.yaml", "marker: prod\n")

    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("DEER_FLOW_CONFIG_PATH", str(explicit))
    resolved = AppConfig.resolve_config_path()
    assert resolved == explicit


def test_appconfig_raises_when_nothing_found(cwd: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEER_FLOW_CONFIG_PATH", raising=False)
    monkeypatch.setenv("APP_ENV", "prod")
    with pytest.raises(FileNotFoundError):
        AppConfig.resolve_config_path()


# --- ExtensionsConfig.resolve_config_path --------------------------------


def test_extensions_picks_env_specific_file(cwd: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEER_FLOW_EXTENSIONS_CONFIG_PATH", raising=False)
    _write(cwd / "extensions_config.json", "{}")
    _write(cwd / "extensions_config.prod.json", "{}")

    monkeypatch.setenv("APP_ENV", "prod")
    resolved = ExtensionsConfig.resolve_config_path()
    assert resolved == (cwd / "extensions_config.prod.json")


def test_extensions_falls_back_to_base(cwd: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEER_FLOW_EXTENSIONS_CONFIG_PATH", raising=False)
    _write(cwd / "extensions_config.json", "{}")

    monkeypatch.setenv("APP_ENV", "prod")
    resolved = ExtensionsConfig.resolve_config_path()
    assert resolved == (cwd / "extensions_config.json")


def test_extensions_returns_none_when_missing(cwd: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEER_FLOW_EXTENSIONS_CONFIG_PATH", raising=False)
    monkeypatch.setenv("APP_ENV", "prod")
    assert ExtensionsConfig.resolve_config_path() is None


def test_extensions_env_path_var_wins(cwd: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    explicit = cwd / "via_env_var.json"
    explicit.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")
    _write(cwd / "extensions_config.prod.json", "{}")

    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("DEER_FLOW_EXTENSIONS_CONFIG_PATH", str(explicit))
    resolved = ExtensionsConfig.resolve_config_path()
    assert resolved == explicit
