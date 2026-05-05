"""Tests for the tenant-onboarding flow.

After S2 of the task-type profile refactor, onboarding lives in its own
profile (``src.agents.lead_agent.profiles.tenant_onboarding``) — when a
tenant has no ``profile.json``, the registry routes the run to that
profile instead of splicing an ``<onboarding_required>`` block into the
business prompt. These tests pin the visible contract:

  - The onboarding profile's prompt carries the inlined phase 1/2/3 spec
    plus the tenant_id and company name verbatim.
  - The business profile's prompt does *not* carry the onboarding block
    regardless of profile.json state (auto-routing handles that).
  - The legacy ``tenant-onboarding`` subagent is still gone.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from src.agents.lead_agent.onboarding_prompt import apply_onboarding_prompt_template
from src.agents.lead_agent.profiles import (
    ProfileContext,
    resolve_profile,
)
from src.agents.lead_agent.profiles import business as business_profile
from src.agents.lead_agent.profiles import tenant_onboarding as onboarding_profile
from src.agents.lead_agent.prompt import apply_prompt_template


@pytest.mark.unit
def test_tenant_onboarding_subagent_no_longer_registered() -> None:
    """Regression guard: the legacy subagent must stay gone — onboarding
    runs as its own profile, not as a delegated subagent."""
    from src.subagents.builtins import BUILTIN_SUBAGENTS
    from src.subagents.registry import get_subagent_config, get_subagent_names

    assert "tenant-onboarding" not in BUILTIN_SUBAGENTS
    assert "tenant-onboarding" not in get_subagent_names()
    assert get_subagent_config("tenant-onboarding") is None


# ---------- Onboarding prompt content ---------------------------------------


@pytest.mark.unit
def test_onboarding_prompt_has_all_three_phases() -> None:
    prompt = apply_onboarding_prompt_template(tenant_id="acme-001", tenant_name="Acme Trading Ltd.")

    assert "<phase_1_channel_selection>" in prompt
    assert "<phase_2_erpnext_seeding>" in prompt
    assert "<phase_3_profile_composition>" in prompt
    assert "<termination>" in prompt
    assert "</onboarding_required>" in prompt


@pytest.mark.unit
def test_onboarding_prompt_embeds_tenant_id_and_company() -> None:
    prompt = apply_onboarding_prompt_template(tenant_id="acme-001", tenant_name="Acme Trading Ltd.")

    assert "acme-001" in prompt
    assert "Acme Trading Ltd." in prompt


@pytest.mark.unit
def test_onboarding_prompt_uses_ask_clarification_and_write_profile() -> None:
    """Phase 1 must steer to ``ask_clarification``; phase 3 must end with
    the ``WROTE profile.json`` sentinel the FE polls for."""
    prompt = apply_onboarding_prompt_template(tenant_id="acme-001", tenant_name="Acme")

    assert "ask_clarification" in prompt
    assert "write_profile" in prompt
    assert "WROTE profile.json" in prompt


@pytest.mark.unit
def test_onboarding_prompt_falls_back_when_company_name_missing() -> None:
    prompt = apply_onboarding_prompt_template(tenant_id="acme-001", tenant_name=None)

    assert "</onboarding_required>" in prompt
    assert "<not provided>" in prompt


@pytest.mark.unit
def test_onboarding_prompt_requires_tenant_id() -> None:
    with pytest.raises(ValueError, match="tenant_id"):
        apply_onboarding_prompt_template(tenant_id="")


# ---------- Routing ---------------------------------------------------------


@pytest.mark.unit
def test_router_picks_onboarding_when_profile_missing(tmp_path: Path) -> None:
    """tenant_id present + profile.json missing → onboarding profile."""
    with patch("src.agents.tenant_profile.store.get_profile_path") as mock_path:
        mock_path.return_value = tmp_path / "missing" / "profile.json"

        ctx = ProfileContext(tenant_id="acme-001", tenant_name="Acme")
        assert resolve_profile(ctx) is onboarding_profile.PROFILE


@pytest.mark.unit
def test_router_picks_business_when_profile_exists(tmp_path: Path) -> None:
    profile_path = tmp_path / "acme-001" / "profile.json"
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text("{}")

    with patch("src.agents.tenant_profile.store.get_profile_path", return_value=profile_path):
        ctx = ProfileContext(tenant_id="acme-001")
        assert resolve_profile(ctx) is business_profile.PROFILE


@pytest.mark.unit
def test_router_picks_business_when_no_tenant_id() -> None:
    """Without a tenant_id we can't safely scope onboarding writes → business."""
    ctx = ProfileContext(tenant_id=None)
    assert resolve_profile(ctx) is business_profile.PROFILE


@pytest.mark.unit
def test_router_falls_back_to_business_on_store_error() -> None:
    """Filesystem hiccup must not trap an existing tenant in onboarding."""
    with patch("src.agents.tenant_profile.store.get_profile_path", side_effect=RuntimeError("io")):
        ctx = ProfileContext(tenant_id="acme-001")
        assert resolve_profile(ctx) is business_profile.PROFILE


# ---------- Business prompt no longer splices onboarding ---------------------


@pytest.mark.unit
def test_business_prompt_never_splices_onboarding(tmp_path: Path) -> None:
    """After S2 the business prompt is purely business — onboarding is a
    separate profile reached via the registry, not a conditional splice."""
    with patch("src.agents.tenant_profile.store.get_profile_path") as mock_path:
        mock_path.return_value = tmp_path / "missing" / "profile.json"
        prompt = apply_prompt_template(subagent_enabled=True, tenant_id="acme-001", tenant_name="Acme")

    assert "</onboarding_required>" not in prompt
