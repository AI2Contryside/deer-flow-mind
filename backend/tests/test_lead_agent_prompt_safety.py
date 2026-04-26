"""Tests for lead_agent prompt safety rules.

These tests pin the hard rules added after session b987fdbe-... where the
agent leaked api credentials, bypassed the erpnext-cli skill with raw curl,
and fabricated a non-existent psycopg2 root cause. The rules must remain
present verbatim or the regression returns.
"""

from __future__ import annotations

import pytest

from src.agents.lead_agent.prompt import apply_prompt_template


@pytest.fixture
def prompt() -> str:
    return apply_prompt_template(subagent_enabled=False, agent_name="TestAgent", tenant_id=None)


@pytest.mark.unit
def test_prompt_forbids_raw_curl_to_backoffice(prompt: str) -> None:
    # Hard rule 1: must explicitly call out the forbidden tool surfaces so
    # the model does not silently fall back to curl/requests when the CLI
    # is awkward.
    assert "Only `erpnext-cli` may talk to the back-office system" in prompt
    for forbidden in ("curl", "wget", "requests", "httpx", "urllib"):
        assert forbidden in prompt, f"missing forbidden surface: {forbidden}"


@pytest.mark.unit
def test_prompt_forbids_credential_echo(prompt: str) -> None:
    # Hard rule 2: must enumerate the credential / endpoint surfaces the
    # model is forbidden to echo. b987fdbe leaked all of these to the user.
    assert "Never name, echo, or reference credentials or internal endpoints" in prompt
    for forbidden_token in ("api_key", "api_secret", "Authorization: token", "ERPNEXT_", "/data00/"):
        assert forbidden_token in prompt, f"prompt should warn about: {forbidden_token}"


@pytest.mark.unit
def test_prompt_requires_failfast_after_repeated_errors(prompt: str) -> None:
    # Hard rule 4: bound the retry loop. b987fdbe ran 8+ retries against
    # the same BrokenPipe before giving up.
    assert "Stop after repeated failure" in prompt
    # Match across the line wrap: "...3 times in a\n   row..."
    assert "3 times in a" in prompt and "row**" in prompt
    assert "stopped\n   retrying" in prompt or "stopped retrying" in prompt


@pytest.mark.unit
def test_prompt_forbids_root_cause_fabrication(prompt: str) -> None:
    # Hard rule 5: anti-hallucination. b987fdbe fabricated
    # "psycopg2 ... row-level security policy" which never appeared in any
    # tool output.
    assert "Never fabricate a root cause" in prompt
    assert "verbatim" in prompt
    assert "psycopg2 RLS" in prompt  # the canonical example we cite to the model


@pytest.mark.unit
def test_prompt_says_to_escalate_when_cli_insufficient(prompt: str) -> None:
    # Hard rule 3: the safe failure mode is to tell the user the toolset
    # cannot do this, not to hack around with raw HTTP.
    assert "If the CLI cannot do what the user wants" in prompt
    # Account for a line wrap inserted between "not" and "supported"
    assert "this operation is not" in prompt and "supported by the current toolset" in prompt


# ---------- P1.1c: bootstrap-first workflow ---------------------------------


@pytest.mark.unit
def test_prompt_requires_bootstrap_status_before_chain_commands(prompt: str) -> None:
    # b987fdbe ran the full setup wizard with the user before realizing
    # the tenant was empty; the prompt must steer the agent to call
    # ``bootstrap status`` first so the empty-tenant diagnosis is the
    # first move, not a 12-step Q&A.
    assert "bootstrap status" in prompt
    assert "before invoking any chain command" in prompt.lower()


@pytest.mark.unit
def test_prompt_treats_next_actions_as_authoritative(prompt: str) -> None:
    # P1.1b populates `next_actions` on every error envelope. The prompt
    # must tell the agent to use it instead of inventing a recovery path
    # — that's how b987fdbe ended up retrying BrokenPipe 8 times.
    assert "next_actions" in prompt
    assert "authoritative" in prompt
