"""Pin SKILL.md sections that the lead_agent prompt + bootstrap workflow
depend on. If someone deletes the readiness probe section or the
required-fields table, this test fails before the agent regresses.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_SKILL_MD = Path(__file__).resolve().parents[2] / "skills" / "public" / "erpnext-cli" / "SKILL.md"


@pytest.fixture(scope="module")
def skill_md_text() -> str:
    return _SKILL_MD.read_text(encoding="utf-8")


@pytest.mark.unit
def test_bootstrap_status_section_is_present(skill_md_text: str) -> None:
    assert "bootstrap status" in skill_md_text
    assert "mandatory before any chain" in skill_md_text.lower()
    # The schema example must include the keys the agent reads.
    for key in (
        "has_company",
        "ready_for_purchase",
        "ready_for_sales",
        "ready_for_stock_in",
        "missing",
        "probe_errors",
    ):
        assert key in skill_md_text, f"SKILL.md missing key: {key}"


@pytest.mark.unit
def test_required_fields_table_covers_critical_doctypes(skill_md_text: str) -> None:
    # The table is the agent's authoritative reference when a chain isn't
    # possible and it has to fall back to `doc insert`. Missing rows are
    # exactly how b987fdbe got stuck guessing field names.
    for doctype in (
        "Company",
        "Warehouse",
        "Item Group",
        "Item",
        "Supplier",
        "Customer",
        "Purchase Order",
        "Purchase Receipt",
        "Sales Order",
        "Sales Invoice",
        "Stock Entry",
    ):
        assert f"`{doctype}`" in skill_md_text, f"required-fields row missing for {doctype}"


@pytest.mark.unit
def test_currency_gotcha_is_documented(skill_md_text: str) -> None:
    # b987fdbe wasted 3 round-trips because the agent guessed "RMB" instead
    # of "CNY". The doc must call this out by name.
    assert "RMB" in skill_md_text
    assert "CNY" in skill_md_text


# ---------- Stale-content guard (review pass after P0+P1 hardening) --------


@pytest.mark.unit
def test_skill_md_does_not_advise_env_var_enumeration(skill_md_text: str) -> None:
    """Pinned after the SKILL.md review pass. The original boot sequence
    told the agent to run ``env | grep ERPNEXT_*`` as Step 2 — which is
    now banned by the prompt's hard rule 2 and would return empty under
    P0.1c's sandbox env gating. If anyone reintroduces this as
    advisory, the agent gets contradictory guidance.

    Mentions of `env` / `printenv` are still allowed inside the
    "Forbidden inside the harness" caveat — that block tells the agent
    these commands won't work, which is the opposite of advice.
    """
    # The advisory pattern from the old boot sequence must stay deleted.
    assert "env | grep" not in skill_md_text
    # The forbidden-actions section must explicitly list the env probes
    # so a reader sees they are not just unsupported, they are blocked.
    assert "Forbidden inside the harness" in skill_md_text
    # The forbidden block must name the specific commands the prompt
    # also bans — otherwise the doc and the prompt drift apart.
    forbidden_block_start = skill_md_text.index("Forbidden inside the harness")
    forbidden_block = skill_md_text[forbidden_block_start : forbidden_block_start + 1000]
    assert "env" in forbidden_block
    assert "printenv" in forbidden_block
    assert "ERPNEXT_*" in forbidden_block


@pytest.mark.unit
def test_skill_md_does_not_tell_agent_to_call_session_login(skill_md_text: str) -> None:
    """``session login --api-key … --api-secret …`` is the standalone-CLI
    auth path. Inside the harness the user does not have AK/SK to type;
    AuthError must escalate, not collect credentials.
    """
    forbidden_inline = (
        "session login\n",
        "session login \\",
        "do call `session login`",
    )
    for snippet in forbidden_inline:
        assert snippet not in skill_md_text, f"agent must not be told to: {snippet!r}"
    # The doc may still mention the standalone-CLI flag, but only in the
    # "not for agent use" caveat — assert the caveat is present.
    assert "Standalone-CLI mode" in skill_md_text or "standalone-CLI" in skill_md_text


@pytest.mark.unit
def test_skill_md_authentication_section_describes_harness_model(skill_md_text: str) -> None:
    """The new auth section must say credentials are pre-provisioned by
    the harness and tell the agent to escalate on AuthError, not to
    chase env vars or prompt the user for keys.
    """
    assert "harness" in skill_md_text.lower()
    assert "pre-provisioned" in skill_md_text or "pre-provisioned per tenant" in skill_md_text
    # The decision tree must call out what to do on AuthError.
    assert "AuthError" in skill_md_text
    assert "escalate" in skill_md_text.lower() or "Surface this to the user" in skill_md_text


@pytest.mark.unit
def test_error_taxonomy_aligns_with_next_actions_field(skill_md_text: str) -> None:
    """P1.1b added a `next_actions` field on every error envelope. The
    taxonomy table must reference it as authoritative — otherwise the
    agent will fall back to the (necessarily looser) table descriptions.
    """
    assert "next_actions" in skill_md_text
    # The AuthError row no longer points at the deleted boot sequence.
    assert "Re-run the **Authentication boot sequence**" not in skill_md_text
    # ServerError row says stop retrying (the fail-fast guard from P0.2).
    assert "Stop retrying" in skill_md_text or "stop retrying" in skill_md_text


@pytest.mark.unit
def test_examples_pass_json_flag_in_agent_examples(skill_md_text: str) -> None:
    """The `Always pass --json` rule used to be violated by the doc-CRUD
    example block (was: ``erpnext.py doc list "Payment Terms Template"``
    with no ``--json``). Pin the fix so future edits do not regress.
    """
    assert 'erpnext.py --json doc list "Payment Terms Template"' in skill_md_text
    assert 'erpnext.py --json doc get "Company"' in skill_md_text


@pytest.mark.unit
def test_skill_md_lists_bootstrap_group_in_table_and_layout(skill_md_text: str) -> None:
    # bootstrap is a real group at runtime; both the command-groups
    # table and the file-tree layout must mention it.
    assert "`bootstrap`" in skill_md_text
    assert "bootstrap_group.py" in skill_md_text


@pytest.mark.unit
def test_frontmatter_description_reflects_harness_auth_model(skill_md_text: str) -> None:
    """Frontmatter description is the first thing other tools (skill
    discovery, prompt injection) see. It must NOT pitch ``session login``
    as the agent path — but it MAY mention it negatively (e.g. "never
    `session login`").
    """
    frontmatter = skill_md_text.split("---", 2)[1]
    # Anti-recommendation: must call out that agents don't call session
    # login (the previous frontmatter literally said "Authenticate once
    # via `session login` ...").
    assert "never" in frontmatter.lower() and "session login" in frontmatter
    # The description should call out the harness-provisioned model.
    assert "harness" in frontmatter.lower()
    # And must not say "Authenticate once via `session login`" or via env
    # vars — the old recommendation phrase.
    assert "Authenticate once via `session login`" not in frontmatter
    assert "or env vars" not in frontmatter
