"""Tests for the S4 config-driven profile registry.

Coverage:
  - Section parser tolerates missing / malformed entries.
  - Disabled profiles are reported as not registered (raise on get/route).
  - Per-profile model override is honored when no explicit request is set.
  - Auto-routing to onboarding skips a disabled tenant_onboarding profile.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from src.agents.lead_agent.profiles import (
    ProfileContext,
    get_profile,
    resolve_profile,
)
from src.agents.lead_agent.profiles import _common as profiles_common
from src.agents.lead_agent.profiles import (
    business as business_profile,
)
from src.config.lead_agent_profiles_config import (
    LeadAgentProfilesConfig,
    ProfileEntry,
    load_lead_agent_profiles_config_from_dict,
    reset_lead_agent_profiles_config_cache,
)

# ---------- Parser ---------------------------------------------------------


def test_parser_returns_empty_when_section_missing():
    cfg = load_lead_agent_profiles_config_from_dict(None)
    assert cfg.profiles == {}


def test_parser_treats_unknown_keys_as_default_enabled():
    """Default-enabled means: when a profile isn't listed, it stays on."""
    cfg = load_lead_agent_profiles_config_from_dict({"business": {"enabled": True}})
    assert cfg.is_enabled("business") is True
    assert cfg.is_enabled("brand_new_profile") is True  # default


def test_parser_skips_malformed_entries():
    cfg = load_lead_agent_profiles_config_from_dict(
        {
            "business": {"enabled": True},
            "weird": "not-a-dict",
            123: {"enabled": True},  # non-string key
            "tenant_onboarding": {"enabled": False, "model": "  small  "},
        }
    )
    assert cfg.is_enabled("business") is True
    assert cfg.is_enabled("tenant_onboarding") is False
    assert cfg.model_override("tenant_onboarding") == "small"
    assert "weird" not in cfg.profiles
    assert 123 not in cfg.profiles  # type: ignore[comparison-overlap]


# ---------- Registry honors enable flag ------------------------------------


def test_get_profile_raises_for_disabled(monkeypatch):
    """Resolving a disabled profile must surface as KeyError so callers
    can't silently route to a profile the operator turned off."""
    cfg = LeadAgentProfilesConfig(profiles={"business": ProfileEntry(enabled=False)})
    monkeypatch.setattr("src.config.lead_agent_profiles_config._loaded", cfg)
    try:
        with pytest.raises(KeyError, match="disabled by lead_agent_profiles config"):
            get_profile("business")
    finally:
        reset_lead_agent_profiles_config_cache()


def test_resolve_profile_skips_onboarding_when_disabled(tmp_path: Path, monkeypatch):
    """Even if the tenant has no profile.json, a disabled onboarding
    profile must not be auto-selected — fall back to business."""
    cfg = LeadAgentProfilesConfig(profiles={"tenant_onboarding": ProfileEntry(enabled=False)})
    monkeypatch.setattr("src.config.lead_agent_profiles_config._loaded", cfg)
    try:
        with patch("src.agents.tenant_profile.store.get_profile_path") as mock_path:
            mock_path.return_value = tmp_path / "missing" / "profile.json"
            ctx = ProfileContext(tenant_id="acme-001", tenant_name="Acme")
            assert resolve_profile(ctx) is business_profile.PROFILE
    finally:
        reset_lead_agent_profiles_config_cache()


# ---------- Per-profile model override -------------------------------------


def test_profile_model_override_lookup(monkeypatch):
    cfg = LeadAgentProfilesConfig(
        profiles={
            "business": ProfileEntry(enabled=True, model="big-model"),
            "template_extraction": ProfileEntry(enabled=True, model=None),
        }
    )
    monkeypatch.setattr("src.config.lead_agent_profiles_config._loaded", cfg)
    try:
        assert profiles_common.profile_model_override("business") == "big-model"
        assert profiles_common.profile_model_override("template_extraction") is None
        assert profiles_common.profile_model_override("nonexistent_task") is None
    finally:
        reset_lead_agent_profiles_config_cache()


def test_business_profile_uses_config_override_when_no_request_model(monkeypatch):
    """Config override beats agent_config / workspace default when the
    request didn't pin a specific model."""
    from src.config.app_config import AppConfig
    from src.config.model_config import ModelConfig
    from src.config.sandbox_config import SandboxConfig

    app = AppConfig(
        models=[
            ModelConfig(
                name="default-model",
                display_name="default-model",
                description=None,
                use="langchain_openai:ChatOpenAI",
                model="default-model",
                supports_thinking=False,
                supports_vision=False,
            ),
            ModelConfig(
                name="override-model",
                display_name="override-model",
                description=None,
                use="langchain_openai:ChatOpenAI",
                model="override-model",
                supports_thinking=False,
                supports_vision=False,
            ),
        ],
        sandbox=SandboxConfig(use="src.sandbox.local:LocalSandboxProvider"),
    )
    monkeypatch.setattr(profiles_common, "get_app_config", lambda: app)
    monkeypatch.setattr(business_profile, "load_agent_config", lambda name: None)

    cfg = LeadAgentProfilesConfig(profiles={"business": ProfileEntry(model="override-model")})
    monkeypatch.setattr("src.config.lead_agent_profiles_config._loaded", cfg)
    try:
        ctx = ProfileContext()  # no requested_model_name
        spec = business_profile.PROFILE.resolve_model(ctx)
        assert spec.name == "override-model"
    finally:
        reset_lead_agent_profiles_config_cache()


def test_explicit_request_beats_config_override(monkeypatch):
    """``ctx.requested_model_name`` wins over the per-profile override."""
    from src.config.app_config import AppConfig
    from src.config.model_config import ModelConfig
    from src.config.sandbox_config import SandboxConfig

    app = AppConfig(
        models=[
            ModelConfig(
                name="default-model",
                display_name="default-model",
                description=None,
                use="langchain_openai:ChatOpenAI",
                model="default-model",
                supports_thinking=False,
                supports_vision=False,
            ),
            ModelConfig(
                name="explicit-model",
                display_name="explicit-model",
                description=None,
                use="langchain_openai:ChatOpenAI",
                model="explicit-model",
                supports_thinking=False,
                supports_vision=False,
            ),
        ],
        sandbox=SandboxConfig(use="src.sandbox.local:LocalSandboxProvider"),
    )
    monkeypatch.setattr(profiles_common, "get_app_config", lambda: app)
    monkeypatch.setattr(business_profile, "load_agent_config", lambda name: None)

    cfg = LeadAgentProfilesConfig(profiles={"business": ProfileEntry(model="config-override")})
    monkeypatch.setattr("src.config.lead_agent_profiles_config._loaded", cfg)
    try:
        ctx = ProfileContext(requested_model_name="explicit-model")
        spec = business_profile.PROFILE.resolve_model(ctx)
        assert spec.name == "explicit-model"
    finally:
        reset_lead_agent_profiles_config_cache()
