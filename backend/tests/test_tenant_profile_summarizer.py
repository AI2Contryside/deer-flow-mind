"""Unit tests for the summarizer runner + e2e flow (M3)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.agents.tenant_profile import log as log_module
from src.agents.tenant_profile import meta as meta_module
from src.agents.tenant_profile import store as store_module
from src.agents.tenant_profile.config import (
    reset_tenant_profile_config_for_tests,
)
from src.agents.tenant_profile.erpnext_client import set_default_erpnext_client
from src.agents.tenant_profile.summarizer import runner as runner_module
from src.agents.tenant_profile.summarizer.schema import EntityRef, KeyEntities, OpenQuestion, TenantProfile


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


# ── Rollup ────────────────────────────────────────────────────────────────


def test_rollup_groups_events_by_doctype_and_name() -> None:
    events = [
        {
            "ts": "2026-04-26T10:00:00Z",
            "skill": "selling/order-to-cash",
            "extracted_refs": [
                {"doctype": "Customer", "name": "FOOCORP-001", "field": "customer", "weight": "primary"},
                {"doctype": "Item", "name": "VLV-001", "field": "items[].item_code", "weight": "primary"},
            ],
        },
        {
            "ts": "2026-04-26T11:00:00Z",
            "skill": "selling/order-to-cash",
            "extracted_refs": [
                {"doctype": "Customer", "name": "FOOCORP-001", "field": "_self", "weight": "primary"},
            ],
        },
    ]
    rollup = runner_module._rollup_events(events, now_iso="2026-04-26T12:00:00Z")
    assert rollup["total_events"] == 2
    customer_bucket = next(b for b in rollup["by_doctype"] if b["doctype"] == "Customer")
    assert customer_bucket["primary"] == 2
    foocorp = next(t for t in customer_bucket["top"] if t["name"] == "FOOCORP-001")
    assert foocorp["count"] == 2
    assert foocorp["primary_count"] == 2
    assert foocorp["first_seen"] == "2026-04-26T10:00:00Z"
    assert foocorp["last_seen"] == "2026-04-26T11:00:00Z"
    assert "selling/order-to-cash" in foocorp["skills"]


def test_rollup_handles_empty_events() -> None:
    rollup = runner_module._rollup_events([], now_iso="2026-04-26T12:00:00Z")
    assert rollup == {"from": None, "to": "2026-04-26T12:00:00Z", "total_events": 0, "by_doctype": []}


def test_rollup_caps_top_at_50_per_doctype() -> None:
    events = [
        {
            "ts": "2026-04-26T10:00:00Z",
            "extracted_refs": [{"doctype": "Customer", "name": f"C-{i:04d}", "field": "_self", "weight": "primary"}],
        }
        for i in range(120)
    ]
    rollup = runner_module._rollup_events(events, now_iso="2026-04-26T12:00:00Z")
    cust = next(b for b in rollup["by_doctype"] if b["doctype"] == "Customer")
    assert len(cust["top"]) == 50
    assert cust["total"] == 120


# ── Parse + validate + name eligibility ───────────────────────────────────


def _fake_input(usage_window: dict, facts: dict | None = None) -> runner_module.SummarizeInput:
    return runner_module.SummarizeInput(
        tenant_id="acme",
        now_iso="2026-04-27T10:00:00Z",
        facts=facts or {},
        previous_profile=None,
        usage_window=usage_window,
        config={"top_k": {"customer": 20}, "recently_quiet_runs_to_drop": 2, "output_token_budget": 2000},
    )


def _profile_payload(*, customers: list[str]) -> dict:
    return {
        "schema_version": 2,
        "generated_at": "2026-04-27T10:00:00Z",
        "tenant_id": "acme",
        "summary": "Test tenant.",
        "facts": {},
        "operational_patterns": {},
        "key_entities": {
            "customers": [{"name": n, "display": n, "note": "primary client"} for n in customers],
        },
        "taxonomy": {},
        "open_questions": [],
    }


def test_parse_and_validate_happy_path() -> None:
    inputs = _fake_input(
        usage_window={"by_doctype": [{"doctype": "Customer", "total": 1, "primary": 1, "secondary": 0, "browse": 0, "top": [{"name": "FOOCORP-001", "count": 1, "primary_count": 1, "first_seen": "x", "last_seen": "y", "skills": []}]}]}
    )
    raw = json.dumps(_profile_payload(customers=["FOOCORP-001"]))
    profile = runner_module._parse_and_validate(raw, inputs)
    assert profile.key_entities.customers[0].name == "FOOCORP-001"
    # tenant_id and generated_at always come from inputs, not the LLM.
    assert profile.tenant_id == "acme"
    assert profile.generated_at == "2026-04-27T10:00:00Z"


def test_parse_strips_markdown_fences() -> None:
    inputs = _fake_input(usage_window={"by_doctype": []})
    raw = "```json\n" + json.dumps(_profile_payload(customers=[])) + "\n```"
    profile = runner_module._parse_and_validate(raw, inputs)
    assert profile.tenant_id == "acme"


def test_parse_rejects_invalid_json() -> None:
    inputs = _fake_input(usage_window={"by_doctype": []})
    with pytest.raises(runner_module.SummarizeError, match="not valid JSON"):
        runner_module._parse_and_validate("definitely not json", inputs)


def test_parse_rejects_empty_output() -> None:
    inputs = _fake_input(usage_window={"by_doctype": []})
    with pytest.raises(runner_module.SummarizeError, match="empty"):
        runner_module._parse_and_validate("", inputs)


def test_parse_rejects_fabricated_customer_names() -> None:
    """Eligibility guard: if the LLM invents a Customer that isn't in
    facts or usage_window, we reject the whole profile."""
    inputs = _fake_input(
        usage_window={"by_doctype": [{"doctype": "Customer", "total": 1, "primary": 1, "secondary": 0, "browse": 0, "top": [{"name": "FOOCORP-001", "count": 1, "primary_count": 1, "first_seen": "x", "last_seen": "y", "skills": []}]}]}
    )
    raw = json.dumps(_profile_payload(customers=["FOOCORP-001", "MADE-UP-CO"]))
    with pytest.raises(runner_module.SummarizeError, match="absent from facts/usage_window"):
        runner_module._parse_and_validate(raw, inputs)


def test_parse_accepts_name_seen_anywhere_in_facts() -> None:
    """A Company name that only appears in facts (not usage_window) is still
    eligible. Facts strings are accepted as a wildcard pool."""
    inputs = _fake_input(
        usage_window={"by_doctype": []},
        facts={"primary_company": {"name": "ACME Industrial Ltd"}},
    )
    payload = _profile_payload(customers=[])
    payload["operational_patterns"] = {
        "default_warehouse_by_company": {"ACME Industrial Ltd": "Stores - ACME"},
    }
    payload["key_entities"]["warehouses"] = [{"name": "Stores - ACME", "display": "Stores"}]
    inputs = _fake_input(
        usage_window={"by_doctype": [{"doctype": "Warehouse", "total": 0, "primary": 0, "secondary": 0, "browse": 0, "top": [{"name": "Stores - ACME", "count": 1, "primary_count": 1, "first_seen": "x", "last_seen": "y", "skills": []}]}]},
        facts={"primary_company": {"name": "ACME Industrial Ltd"}},
    )
    profile = runner_module._parse_and_validate(json.dumps(payload), inputs)
    assert profile.key_entities.warehouses[0].name == "Stores - ACME"


# ── End-to-end: 50 events → trigger → summarize → profile.json ────────────


def test_e2e_50_events_trigger_summarize_writes_profile() -> None:
    # Seed 50 SO insert events that all reference FOOCORP-001 + VLV-001.
    for i in range(50):
        log_module.append_event(
            "acme",
            {
                "ts": f"2026-04-26T10:{i:02d}:00Z",
                "op": "insert",
                "doctype": "Sales Order",
                "name": f"SAL-ORD-{i:04d}",
                "skill": "selling/order-to-cash",
                "extracted_refs": [
                    {"doctype": "Customer", "name": "FOOCORP-001", "field": "customer", "weight": "primary"},
                    {"doctype": "Item", "name": "VLV-001", "field": "items[].item_code", "weight": "primary"},
                    {"doctype": "Warehouse", "name": "Stores - ACME", "field": "items[].warehouse", "weight": "primary"},
                ],
            },
        )

    # Stub LLM that produces a valid TenantProfile referencing only the seeded names.
    profile = TenantProfile(
        schema_version=2,
        generated_at="2026-04-27T10:00:00Z",
        tenant_id="acme",
        summary="Acme test tenant.",
        facts={},
        key_entities=KeyEntities(
            customers=[EntityRef(name="FOOCORP-001", display="Foo Corp", note="primary OEM customer")],
            items=[EntityRef(name="VLV-001", display="Industrial Valve")],
            warehouses=[EntityRef(name="Stores - ACME")],
        ),
        open_questions=[OpenQuestion(question="confirm Foo Corp's PO reference style")],
    )

    def fake_llm(system: str, user: str) -> str:
        assert "tenant_id=acme" in user
        return profile.model_dump_json()

    outcome = runner_module.run_summarize(
        "acme",
        force=True,
        user_email="alice@acme.com",  # stub client → empty facts but no crash
        llm=fake_llm,
        now=datetime(2026, 4, 27, 10, 0, 0, tzinfo=UTC),
    )

    assert outcome.success is True
    assert outcome.profile is not None
    saved = store_module.read_profile("acme")
    assert saved is not None
    assert saved["key_entities"]["customers"][0]["name"] == "FOOCORP-001"
    assert saved["tenant_id"] == "acme"

    # Meta should reflect a successful run.
    state = meta_module.read_meta("acme")
    assert state.event_count_since_last == 0
    assert state.last_summarize_error is None
    assert state.last_summarize_ts == "2026-04-27T10:00:00Z"

    # Log has been rotated — live file is empty, archive contains the consumed events.
    assert log_module.count_events("acme") == 0


def test_run_summarize_skips_when_below_threshold() -> None:
    log_module.append_event(
        "acme",
        {
            "ts": "2026-04-26T10:00:00Z",
            "extracted_refs": [{"doctype": "Customer", "name": "X", "field": "_self", "weight": "primary"}],
        },
    )
    # Force the trigger to deny by setting last_summarize within cooldown.
    meta_module.write_meta(
        "acme",
        meta_module.Meta(last_summarize_ts="2026-04-27T09:59:59Z"),
    )

    def never_called(_s: str, _u: str) -> str:
        raise AssertionError("LLM should not be called when trigger denies")

    outcome = runner_module.run_summarize(
        "acme",
        llm=never_called,
        now=datetime(2026, 4, 27, 10, 0, 0, tzinfo=UTC),
    )
    assert outcome.success is False
    assert outcome.skipped_reason in ("skipped_cooldown", "skipped_below_thresholds")


def test_run_summarize_records_error_on_llm_garbage() -> None:
    log_module.append_event(
        "acme",
        {
            "ts": "2026-04-26T10:00:00Z",
            "extracted_refs": [{"doctype": "Customer", "name": "X", "field": "_self", "weight": "primary"}],
        },
    )

    def bad_llm(_s: str, _u: str) -> str:
        return "not a json object at all"

    outcome = runner_module.run_summarize("acme", force=True, llm=bad_llm)
    assert outcome.success is False
    assert outcome.error and "JSON" in outcome.error
    state = meta_module.read_meta("acme")
    assert state.last_summarize_error and "JSON" in state.last_summarize_error
    # Previous profile (none) should remain absent.
    assert store_module.read_profile("acme") is None


def test_run_summarize_preserves_previous_profile_on_failure() -> None:
    # Seed a previous profile so we can verify it isn't overwritten on failure.
    store_module.write_profile("acme", {"summary": "previous profile", "_marker": True})
    log_module.append_event(
        "acme",
        {"ts": "2026-04-26T10:00:00Z", "extracted_refs": [{"doctype": "Customer", "name": "X", "field": "_self", "weight": "primary"}]},
    )

    def crash(_s: str, _u: str) -> str:
        raise RuntimeError("LLM unavailable")

    outcome = runner_module.run_summarize("acme", force=True, llm=crash)
    assert outcome.success is False
    saved = store_module.read_profile("acme")
    assert saved is not None and saved.get("_marker") is True
