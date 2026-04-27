"""Tests for the tenant-onboarding subagent registration + lead-agent nudge."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.mark.unit
def test_tenant_onboarding_is_registered() -> None:
    from src.subagents.builtins import BUILTIN_SUBAGENTS
    from src.subagents.registry import get_subagent_config, get_subagent_names

    assert "tenant-onboarding" in BUILTIN_SUBAGENTS
    assert "tenant-onboarding" in get_subagent_names()

    cfg = get_subagent_config("tenant-onboarding")
    assert cfg is not None
    assert cfg.name == "tenant-onboarding"


@pytest.mark.unit
def test_tenant_onboarding_blocks_recursive_task_calls() -> None:
    """The subagent must not be able to spawn grandchildren."""
    from src.subagents.registry import get_subagent_config

    cfg = get_subagent_config("tenant-onboarding")
    assert cfg is not None
    assert "task" in (cfg.disallowed_tools or [])


@pytest.mark.unit
def test_tenant_onboarding_inherits_parent_tools() -> None:
    """We deliberately leave ``tools=None`` so bash, ask_clarification, etc. inherit."""
    from src.subagents.registry import get_subagent_config

    cfg = get_subagent_config("tenant-onboarding")
    assert cfg is not None
    assert cfg.tools is None  # inherit-all


@pytest.mark.unit
def test_lead_agent_injects_onboarding_section_when_profile_missing(tmp_path: Path) -> None:
    """No profile.json → ``<onboarding_required>`` block appears in the system prompt."""
    from src.agents.lead_agent.prompt import apply_prompt_template

    with patch("src.agents.tenant_profile.store.get_profile_path") as mock_path:
        mock_path.return_value = tmp_path / "missing" / "profile.json"

        prompt = apply_prompt_template(subagent_enabled=True, tenant_id="acme-001")

    assert "<onboarding_required>" in prompt
    assert "tenant-onboarding" in prompt
    assert "acme-001" in prompt


@pytest.mark.unit
def test_lead_agent_skips_onboarding_section_when_profile_exists(tmp_path: Path) -> None:
    from src.agents.lead_agent.prompt import apply_prompt_template

    profile_path = tmp_path / "acme-001" / "profile.json"
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text("{}")

    with patch("src.agents.tenant_profile.store.get_profile_path", return_value=profile_path):
        prompt = apply_prompt_template(subagent_enabled=True, tenant_id="acme-001")

    assert "<onboarding_required>" not in prompt


@pytest.mark.unit
def test_lead_agent_skips_onboarding_section_when_no_tenant_id() -> None:
    """Without a tenant_id we cannot safely scope writes — never inject the nudge."""
    from src.agents.lead_agent.prompt import apply_prompt_template

    prompt = apply_prompt_template(subagent_enabled=True, tenant_id=None)

    assert "<onboarding_required>" not in prompt


@pytest.mark.unit
def test_lead_agent_skips_onboarding_section_when_subagents_disabled(tmp_path: Path) -> None:
    """With subagents off the ``task`` tool isn't available — don't tell the agent to call it."""
    from src.agents.lead_agent.prompt import apply_prompt_template

    with patch("src.agents.tenant_profile.store.get_profile_path") as mock_path:
        mock_path.return_value = tmp_path / "missing" / "profile.json"

        prompt = apply_prompt_template(subagent_enabled=False, tenant_id="acme-001")

    assert "<onboarding_required>" not in prompt
