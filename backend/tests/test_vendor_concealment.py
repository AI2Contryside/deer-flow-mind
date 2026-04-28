"""Pin the vendor-concealment rule on every user-facing agent prompt.

The agent drives ERPNext via ``erpnext-cli`` internally, but the user must
never see those names. These tests guard against regressions where someone
edits a user-facing template snippet and lets the brand leak again (e.g. the
old "draft a structured artifact the user can paste into ERPNext" line).
"""

from __future__ import annotations

import pytest

from src.agents.concealment import VENDOR_CONCEALMENT_BLOCK
from src.agents.lead_agent.prompt import apply_prompt_template
from src.agents.tenant_onboarding.prompt import build_system_prompt


@pytest.fixture
def lead_prompt() -> str:
    return apply_prompt_template(subagent_enabled=False, agent_name="TestAgent", tenant_id=None)


@pytest.fixture
def lead_prompt_with_onboarding() -> str:
    # Drive the ``<onboarding_required>`` branch so the inline copy of the
    # onboarding script (used when subagents are off) is exercised too.
    return apply_prompt_template(
        subagent_enabled=False,
        agent_name="TestAgent",
        tenant_id="acme-001",
        tenant_name="Acme Trading",
    )


@pytest.fixture
def onboarding_prompt() -> str:
    return build_system_prompt()


# ---------- concealment block is wired into both surfaces ------------------


@pytest.mark.unit
def test_vendor_concealment_block_present_in_lead_prompt(lead_prompt: str) -> None:
    assert "<vendor_concealment>" in lead_prompt
    assert "</vendor_concealment>" in lead_prompt
    # Sanity: a couple of canonical phrases pin the rule's intent so it cannot
    # be accidentally weakened to a vague comment.
    assert "implementation detail" in lead_prompt
    assert "user must never see them" in lead_prompt


@pytest.mark.unit
def test_vendor_concealment_block_present_in_onboarding_prompt(onboarding_prompt: str) -> None:
    assert "<vendor_concealment>" in onboarding_prompt
    assert "</vendor_concealment>" in onboarding_prompt
    assert "implementation detail" in onboarding_prompt


@pytest.mark.unit
def test_concealment_block_constant_lists_forbidden_brand_tokens() -> None:
    # If the constant stops mentioning the brand tokens explicitly, the model
    # has no way to know which strings to avoid. Pin the contract.
    for token in ("ERPNext", "Frappe", "erpnext-cli", "X-Tenant-ID"):
        assert token in VENDOR_CONCEALMENT_BLOCK


# ---------- user-facing snippets do not leak the brand ---------------------


@pytest.mark.unit
def test_lead_prompt_does_not_instruct_user_paste_into_erpnext(lead_prompt: str) -> None:
    # Old snippet: "draft a structured artifact the user can paste into ERPNext".
    # That instruction told the model to literally name ERPNext to the user.
    # Replacement should mention the workspace generically.
    assert "paste into ERPNext" not in lead_prompt
    assert "apply manually in their workspace" in lead_prompt


@pytest.mark.unit
def test_lead_prompt_autherror_handling_is_concealment_aware(lead_prompt: str) -> None:
    # The AuthError branch used to say "Surface the error verbatim to the
    # user", which leaks the raw envelope (and the brand name inside it).
    # The replacement should keep the error code but explicitly forbid
    # quoting the raw envelope.
    assert "Surface the error verbatim to the user and stop" not in lead_prompt
    assert "AuthError" in lead_prompt  # bare code is still cited
    # ``**do NOT**`` markdown wraps the verb, so match the substantive phrase.
    assert "paste the raw envelope" in lead_prompt


@pytest.mark.unit
def test_onboarding_termination_message_does_not_template_brand_name(onboarding_prompt: str) -> None:
    # The visible final-message template must not name the back-office stack.
    # We allow ERPNext to appear inside the model's instructions (it has to,
    # so the model knows which CLI to pick), but the literal final-message
    # template the model is told to echo must be brand-free.
    termination = onboarding_prompt.split("<termination>", 1)[1].split("</termination>", 1)[0]
    for forbidden in ("ERPNext", "Frappe", "erpnext-cli"):
        assert forbidden not in termination, f"termination message leaks brand token: {forbidden}"


@pytest.mark.unit
def test_inline_onboarding_termination_does_not_template_brand_name(lead_prompt_with_onboarding: str) -> None:
    # Same guard for the inline copy embedded in the lead agent prompt when
    # the tenant has no profile.json yet. The model echoes this template
    # verbatim, so brand tokens here would land directly in the user's chat.
    assert "<onboarding_required>" in lead_prompt_with_onboarding, "fixture should trigger onboarding branch"
    body = lead_prompt_with_onboarding.split("<onboarding_required>", 1)[1].split("</onboarding_required>", 1)[0]
    termination = body.split("<termination>", 1)[1].split("</termination>", 1)[0]
    for forbidden in ("ERPNext", "Frappe", "erpnext-cli"):
        assert forbidden not in termination, f"inline termination leaks brand token: {forbidden}"
