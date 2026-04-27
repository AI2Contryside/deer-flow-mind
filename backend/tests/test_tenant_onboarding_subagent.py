"""Tests for the inline tenant-onboarding flow on the lead agent.

The first-time-setup spec used to live in a dedicated ``tenant-onboarding``
subagent, but ``ask_clarification`` only interrupts execution when
``ClarificationMiddleware`` is in the chain — and that middleware is
bound to the lead agent only. Inside a subagent the placeholder tool just
returned a literal string and the user never saw the question. The flow
now lives directly in the lead agent's system prompt under
``<onboarding_required>``; these tests pin the visible contract.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.mark.unit
def test_tenant_onboarding_subagent_no_longer_registered() -> None:
    """Regression guard: after the inline migration, the subagent registry
    must not list ``tenant-onboarding``. If it comes back, the lead agent
    will see it in the ``<subagent_system>`` listing and may delegate
    again — and the user will get the silent-clarification bug back."""
    from src.subagents.builtins import BUILTIN_SUBAGENTS
    from src.subagents.registry import get_subagent_config, get_subagent_names

    assert "tenant-onboarding" not in BUILTIN_SUBAGENTS
    assert "tenant-onboarding" not in get_subagent_names()
    assert get_subagent_config("tenant-onboarding") is None


@pytest.mark.unit
def test_lead_agent_inlines_onboarding_phases_when_profile_missing(tmp_path: Path) -> None:
    """No profile.json → the lead agent's system prompt carries the full
    inlined spec (channel selection / ERPNext seeding / profile composition)
    and the tenant_id + company name. Without the inlined spec the lead
    agent has no instructions and just chats with the user instead of
    seeding ERPNext."""
    from src.agents.lead_agent.prompt import apply_prompt_template

    with patch("src.agents.tenant_profile.store.get_profile_path") as mock_path:
        mock_path.return_value = tmp_path / "missing" / "profile.json"

        prompt = apply_prompt_template(
            subagent_enabled=True,
            tenant_id="acme-001",
            tenant_name="Acme Trading Ltd.",
        )

    assert "</onboarding_required>" in prompt
    # tenant_id + company name appear verbatim so the lead agent can
    # forward them into write_profile / Company creation respectively.
    assert "acme-001" in prompt
    assert "Acme Trading Ltd." in prompt
    # All three phases must be present — the spec is now self-contained
    # in the lead agent prompt, not split across a subagent file.
    assert "<phase_1_channel_selection>" in prompt
    assert "<phase_2_erpnext_seeding>" in prompt
    assert "<phase_3_profile_composition>" in prompt
    # Phase 1 references ask_clarification (the lead agent has
    # ClarificationMiddleware so this is the right path; subagents can't
    # use it).
    assert "ask_clarification" in prompt
    # Phase 3 must use write_profile + the print sentinel the FE polls
    # implicitly via /gateway/onboarding/status.
    assert "write_profile" in prompt
    assert "WROTE profile.json" in prompt
    # Termination bracket so the model knows when to stop.
    assert "<termination>" in prompt


@pytest.mark.unit
def test_lead_agent_skips_onboarding_section_when_profile_exists(tmp_path: Path) -> None:
    from src.agents.lead_agent.prompt import apply_prompt_template

    profile_path = tmp_path / "acme-001" / "profile.json"
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text("{}")

    with patch("src.agents.tenant_profile.store.get_profile_path", return_value=profile_path):
        prompt = apply_prompt_template(subagent_enabled=True, tenant_id="acme-001")

    # The closing tag is unique to the actual onboarding block — the
    # subagent_section also references the literal ``<onboarding_required>``
    # opener as documentation, which would otherwise produce a false match.
    assert "</onboarding_required>" not in prompt


@pytest.mark.unit
def test_lead_agent_skips_onboarding_section_when_no_tenant_id() -> None:
    """Without a tenant_id we can't safely scope writes — never inject."""
    from src.agents.lead_agent.prompt import apply_prompt_template

    prompt = apply_prompt_template(subagent_enabled=True, tenant_id=None)

    # The closing tag is unique to the actual onboarding block — the
    # subagent_section also references the literal ``<onboarding_required>``
    # opener as documentation, which would otherwise produce a false match.
    assert "</onboarding_required>" not in prompt


@pytest.mark.unit
def test_lead_agent_inlines_onboarding_even_when_subagents_disabled(tmp_path: Path) -> None:
    """The inlined onboarding flow uses ``ask_clarification`` + ``bash``
    directly, not the ``task`` tool — so it works regardless of whether
    subagents are enabled. This is a behavior change from the previous
    subagent-based flow which required ``subagent_enabled=True``."""
    from src.agents.lead_agent.prompt import apply_prompt_template

    with patch("src.agents.tenant_profile.store.get_profile_path") as mock_path:
        mock_path.return_value = tmp_path / "missing" / "profile.json"

        prompt = apply_prompt_template(subagent_enabled=False, tenant_id="acme-001")

    assert "</onboarding_required>" in prompt


@pytest.mark.unit
def test_lead_agent_falls_back_when_company_name_missing(tmp_path: Path) -> None:
    """Without a tenant_name we still emit onboarding but tell the model
    to surface a single open_question rather than asking the user."""
    from src.agents.lead_agent.prompt import apply_prompt_template

    with patch("src.agents.tenant_profile.store.get_profile_path") as mock_path:
        mock_path.return_value = tmp_path / "missing" / "profile.json"

        prompt = apply_prompt_template(
            subagent_enabled=True,
            tenant_id="acme-001",
            tenant_name=None,
        )

    assert "</onboarding_required>" in prompt
    assert "<not provided>" in prompt
