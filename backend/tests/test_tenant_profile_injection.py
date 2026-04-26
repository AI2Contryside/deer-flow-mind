"""Unit tests for tenant_profile.injection (M4): rendering + cold start + e2e."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.agents.tenant_profile import facts as facts_module
from src.agents.tenant_profile import log as log_module
from src.agents.tenant_profile import meta as meta_module
from src.agents.tenant_profile import store as store_module
from src.agents.tenant_profile.config import (
    reset_tenant_profile_config_for_tests,
)
from src.agents.tenant_profile.erpnext_client import set_default_erpnext_client
from src.agents.tenant_profile.injection import (
    get_profile_context,
    render_profile_section,
)
from src.agents.tenant_profile.summarizer import runner as runner_module
from src.agents.tenant_profile.summarizer.schema import EntityRef, KeyEntities, OperationalPatterns, TenantProfile


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_paths = MagicMock()
    fake_paths.base_dir = tmp_path
    monkeypatch.setattr("src.agents.tenant_profile.log.get_paths", lambda: fake_paths)
    monkeypatch.setattr("src.agents.tenant_profile.meta.get_paths", lambda: fake_paths)
    monkeypatch.setattr("src.agents.tenant_profile.store.get_paths", lambda: fake_paths)
    log_module.reset_locks_for_tests()
    meta_module.reset_locks_for_tests()
    store_module.reset_locks_for_tests()
    reset_tenant_profile_config_for_tests()
    set_default_erpnext_client(None)
    yield
    set_default_erpnext_client(None)
    reset_tenant_profile_config_for_tests()


# ── Renderer ──────────────────────────────────────────────────────────────


def test_render_minimal_profile_with_only_facts() -> None:
    profile = {
        "schema_version": 2,
        "tenant_id": "acme",
        "summary": "",
        "facts": {
            "primary_company": {"name": "ACME Industrial Ltd", "default_currency": "USD", "country": "China"},
            "current_user": {"full_name": "Alice Wong"},
            "current_employee": {"employee_name": "Alice Wong", "designation": "Sales Manager", "company": "ACME Industrial Ltd"},
            "fiscal_year": {"name": "2026"},
        },
    }
    out = render_profile_section(profile)
    assert "<tenant_profile>" in out
    assert "ACME Industrial Ltd" in out
    assert "USD" in out
    assert "FY2026" in out
    assert "Sales Manager" in out
    assert "</tenant_profile>" in out


def test_render_full_profile_buckets_accounts_by_root_type() -> None:
    profile = {
        "schema_version": 2,
        "tenant_id": "acme",
        "summary": "Shanghai industrial valve distributor.",
        "facts": {"primary_company": {"name": "ACME"}},
        "operational_patterns": {
            "primary_workflow": "Order-to-Cash via SO -> DN -> SI",
            "currencies_in_use": ["USD", "CNY"],
        },
        "key_entities": {
            "customers": [{"name": "FOOCORP-001", "display": "Foo Corp", "note": "Primary OEM customer"}],
            "items": [{"name": "VLV-001", "display": "Industrial Valve"}],
            "accounts_by_root_type": {
                "Asset": [
                    {"name": "Bank - Cash CNY", "extras": {"account_type": "Bank"}},
                    {"name": "Trade Receivables - ACME", "extras": {"account_type": "Receivable"}},
                ],
                "Income": [{"name": "Sales - Industrial"}],
            },
        },
        "taxonomy": {
            "customer_groups_by_parent": {"OEM": [{"name": "OEM-KR"}, {"name": "OEM-SEA"}]},
        },
        "open_questions": [{"question": "Confirm PO reference style for Foo Corp"}],
    }
    out = render_profile_section(profile)
    assert "Tenant brief" in out and "Shanghai industrial valve distributor" in out
    assert "Order-to-Cash" in out
    # NestedSet bucketing — Asset / Income headers should appear as bucket labels.
    assert "**Asset**" in out
    assert "Bank - Cash CNY" in out
    assert "**Income**" in out
    # Taxonomy bucket label.
    assert "**OEM**" in out
    assert "OEM-KR" in out
    # Open question.
    assert "Confirm PO reference style" in out


def test_render_separates_active_from_recently_quiet() -> None:
    profile = {
        "schema_version": 2,
        "tenant_id": "acme",
        "summary": "",
        "facts": {},
        "key_entities": {
            "customers": [
                {"name": "ACTIVE-1", "display": "Active Co", "status": "active"},
                {"name": "QUIET-1", "display": "Legacy Co", "status": "recently_quiet", "note": "no activity 1 period"},
            ],
        },
    }
    out = render_profile_section(profile)
    # Active section lists ACTIVE-1, NOT QUIET-1.
    assert "ACTIVE-1" in out
    active_section_idx = out.index("Frequent customers")
    quiet_section_idx = out.index("Recently quiet")
    # Active appears before recently_quiet.
    assert active_section_idx < quiet_section_idx
    # QUIET-1 only shows up under "Recently quiet".
    assert "QUIET-1" in out
    quiet_block = out[quiet_section_idx:]
    assert "QUIET-1" in quiet_block


def test_render_skips_empty_sections() -> None:
    profile = {"schema_version": 2, "tenant_id": "acme", "summary": "", "facts": {}, "key_entities": {}, "taxonomy": {}}
    out = render_profile_section(profile)
    assert "Frequent customers" not in out
    assert "Operational patterns" not in out
    assert "Recently quiet" not in out


# ── get_profile_context: read existing profile ───────────────────────────


def test_get_profile_context_reads_existing_profile() -> None:
    profile = TenantProfile(
        schema_version=2,
        generated_at="2026-04-27T10:00:00Z",
        tenant_id="acme",
        summary="Test tenant.",
        facts={"primary_company": {"name": "ACME"}},
        operational_patterns=OperationalPatterns(),
        key_entities=KeyEntities(customers=[EntityRef(name="FOOCORP-001", display="Foo Corp")]),
        open_questions=[],
    )
    store_module.write_profile("acme", profile.model_dump())

    out = get_profile_context("acme")
    assert "FOOCORP-001" in out
    assert "ACME" in out


def test_get_profile_context_returns_empty_when_tenant_id_missing() -> None:
    assert get_profile_context(None) == ""
    assert get_profile_context("") == ""


def test_get_profile_context_returns_empty_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.agents.tenant_profile import config as config_module

    cfg = config_module.TenantProfileConfig(enabled=False)
    config_module.set_tenant_profile_config(cfg)
    assert get_profile_context("acme") == ""


# ── Cold start ────────────────────────────────────────────────────────────


def test_cold_start_with_stub_client_returns_empty() -> None:
    """Default ERPNext client = stub → bootstrap finds nothing → empty section.

    The intent is that cold-start should NOT block the conversation when
    ERPNext access isn't wired. The agent falls through to its existing
    "ask the user" / "query ERPNext directly" path.
    """
    out = get_profile_context("acme", user_email="alice@acme.com")
    assert out == ""
    # And no profile.json should be written for a useless empty bundle.
    assert store_module.read_profile("acme") is None


def test_cold_start_with_working_client_writes_facts_only_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_bundle = facts_module.FactsBundle(
        primary_company={"name": "ACME Industrial Ltd", "default_currency": "USD", "country": "China"},
        current_user={"email": "alice@acme.com", "full_name": "Alice Wong"},
        user_roles=["Sales Manager"],
        fetched_at="2026-04-27T10:00:00Z",
    )

    def fake_bootstrap(*, user_email, timeout_seconds, client=None, now_iso=None):
        return fake_bundle

    monkeypatch.setattr("src.agents.tenant_profile.injection.facts_module.bootstrap_facts", fake_bootstrap)

    out = get_profile_context("acme", user_email="alice@acme.com")
    assert "ACME Industrial Ltd" in out
    assert "Alice Wong" in out

    saved = store_module.read_profile("acme")
    assert saved is not None
    assert saved["facts"]["primary_company"]["name"] == "ACME Industrial Ltd"
    assert saved.get("_cold_start") is True


def test_cold_start_does_not_overwrite_existing_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    store_module.write_profile("acme", {"schema_version": 2, "tenant_id": "acme", "summary": "EXISTING", "facts": {}, "_marker": "preserved"})

    def boom(**_kw):
        raise AssertionError("cold-start path should not run when profile exists")

    monkeypatch.setattr("src.agents.tenant_profile.injection.facts_module.bootstrap_facts", boom)

    out = get_profile_context("acme")
    assert "EXISTING" in out  # rendered from existing profile
    saved = store_module.read_profile("acme")
    assert saved is not None and saved.get("_marker") == "preserved"


def test_get_profile_context_swallows_unexpected_exceptions(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bug inside the renderer should never propagate to the agent."""

    def crash(_profile):
        raise RuntimeError("renderer bug")

    monkeypatch.setattr("src.agents.tenant_profile.injection.render_profile_section", crash)
    store_module.write_profile("acme", {"schema_version": 2, "tenant_id": "acme", "summary": "X", "facts": {}})
    assert get_profile_context("acme") == ""


# ── e2e: previous_profile flow + recently_quiet decay ────────────────────


def test_previous_profile_recently_quiet_decay_through_summarize() -> None:
    """After two summarize runs where FOOCORP doesn't reappear and the LLM
    follows the decay rule, the entity should drop from the rendered prompt
    via the 'recently quiet' → drop pipeline.

    We drive this with a deterministic LLM stub that mimics the decay
    behaviour the prompt asks for.
    """
    # Seed previous profile with FOOCORP active.
    initial = TenantProfile(
        schema_version=2,
        generated_at="2026-04-26T10:00:00Z",
        tenant_id="acme",
        summary="Acme tenant.",
        facts={},
        key_entities=KeyEntities(customers=[EntityRef(name="FOOCORP-001", display="Foo Corp", status="active", quiet_runs=0)]),
        open_questions=[],
    )
    store_module.write_profile("acme", initial.model_dump())

    # Round 1: usage_window has ZERO Customer events (FOOCORP not seen). LLM
    # marks FOOCORP recently_quiet, quiet_runs=1.
    log_module.append_event(
        "acme",
        {
            "ts": "2026-04-27T10:00:00Z",
            "extracted_refs": [{"doctype": "Item", "name": "VLV-001", "field": "_self", "weight": "primary"}],
        },
    )

    def llm_round_1(_s: str, _u: str) -> str:
        decayed = TenantProfile(
            schema_version=2,
            generated_at="2026-04-27T10:00:00Z",
            tenant_id="acme",
            summary="Acme tenant.",
            facts={},
            key_entities=KeyEntities(
                items=[EntityRef(name="VLV-001", display="Industrial Valve")],
                customers=[
                    EntityRef(
                        name="FOOCORP-001",
                        display="Foo Corp",
                        status="recently_quiet",
                        quiet_runs=1,
                        note="no activity this period",
                    )
                ],
            ),
            open_questions=[],
        )
        return decayed.model_dump_json()

    outcome = runner_module.run_summarize("acme", force=True, llm=llm_round_1, now=datetime(2026, 4, 27, 10, 0, 0, tzinfo=UTC))
    assert outcome.success is True

    # FOOCORP should now render under "Recently quiet" (not "Frequent customers").
    out_round_1 = get_profile_context("acme")
    assert "Recently quiet" in out_round_1
    rq_section = out_round_1[out_round_1.index("Recently quiet") :]
    assert "FOOCORP-001" in rq_section

    # Round 2: still no FOOCORP. LLM bumps quiet_runs=2 → drops from output
    # (matching the decay rule — recently_quiet_runs_to_drop default is 2).
    log_module.append_event(
        "acme",
        {
            "ts": "2026-04-28T10:00:00Z",
            "extracted_refs": [{"doctype": "Item", "name": "VLV-001", "field": "_self", "weight": "primary"}],
        },
    )

    def llm_round_2(_s: str, _u: str) -> str:
        dropped = TenantProfile(
            schema_version=2,
            generated_at="2026-04-28T10:00:00Z",
            tenant_id="acme",
            summary="Acme tenant.",
            facts={},
            key_entities=KeyEntities(items=[EntityRef(name="VLV-001", display="Industrial Valve")]),  # no customers
            open_questions=[],
        )
        return dropped.model_dump_json()

    outcome2 = runner_module.run_summarize("acme", force=True, llm=llm_round_2, now=datetime(2026, 4, 28, 10, 0, 0, tzinfo=UTC))
    assert outcome2.success is True

    out_round_2 = get_profile_context("acme")
    assert "FOOCORP-001" not in out_round_2
    assert "Recently quiet" not in out_round_2
