"""Unit tests for the tenant_onboarding composer + question_bank helpers."""

from __future__ import annotations

import pytest

from src.agents.tenant_onboarding import (
    REQUIRED_QUESTIONS,
    OnboardingFacts,
    TenantProfile,
    compose_initial_profile,
    minimal_profile_dict,
)
from src.agents.tenant_onboarding.question_bank import required_unanswered


@pytest.mark.unit
def test_minimal_profile_validates_against_runtime_schema() -> None:
    """Empty/fallback profile must round-trip through TenantProfile."""
    data = minimal_profile_dict("acme-001")

    profile = TenantProfile.model_validate(data)

    assert profile.tenant_id == "acme-001"
    assert profile.schema_version == 2
    assert profile.summary  # non-empty


@pytest.mark.unit
def test_compose_uses_company_facts_in_summary() -> None:
    facts = OnboardingFacts(
        tenant_id="t-1",
        answers={
            "company_name": "Shenzhen Acme Trading Co.",
            "company_country": "China",
            "company_currency": "CNY",
            "business_type": "Trading (买卖型外贸)",
            "default_warehouse": "Stores",
        },
    )

    data = compose_initial_profile(facts)
    profile = TenantProfile.model_validate(data)

    assert "Shenzhen Acme" in profile.summary
    assert "CNY" in profile.summary
    assert profile.operational_patterns.currencies_in_use == ["CNY"]
    assert profile.operational_patterns.default_warehouse_by_company == {
        "Shenzhen Acme Trading Co.": "Stores",
    }
    # primary_workflow should record the business type verbatim so the
    # runtime summarizer can pattern-match it later.
    assert profile.operational_patterns.primary_workflow == "Trading (买卖型外贸)"


@pytest.mark.unit
def test_compose_caps_imported_rows_per_doctype() -> None:
    """Customer cap is 20 — extra rows must be dropped, not blow up the schema."""
    customers = [{"name": f"CUST-{i:03d}", "display": f"Customer {i}"} for i in range(50)]
    facts = OnboardingFacts(
        tenant_id="t-2",
        answers={"company_name": "X", "company_currency": "USD"},
        imported={"Customer": customers},
    )

    data = compose_initial_profile(facts)
    profile = TenantProfile.model_validate(data)

    assert len(profile.key_entities.customers) == 20
    assert profile.key_entities.customers[0].name == "CUST-000"
    assert profile.facts["imported_counts"]["Customer"] == 50


@pytest.mark.unit
def test_compose_open_questions_truncated_and_normalized() -> None:
    facts = OnboardingFacts(
        tenant_id="t-3",
        answers={"company_name": "X"},
        open_questions=[{"question": f"Q{i}", "candidates": [f"c{j}" for j in range(15)]} for i in range(15)],
    )

    data = compose_initial_profile(facts)
    profile = TenantProfile.model_validate(data)

    assert len(profile.open_questions) == 10  # max_length cap
    assert all(len(oq.candidates) <= 10 for oq in profile.open_questions)


@pytest.mark.unit
def test_compose_skips_imported_rows_without_name() -> None:
    facts = OnboardingFacts(
        tenant_id="t-4",
        answers={"company_name": "X"},
        imported={"Customer": [{"display": "no name here"}, {"name": "CUST-1"}]},
    )

    data = compose_initial_profile(facts)
    profile = TenantProfile.model_validate(data)

    assert [r.name for r in profile.key_entities.customers] == ["CUST-1"]


@pytest.mark.unit
def test_required_unanswered_returns_all_t1_when_empty() -> None:
    pending = required_unanswered({})

    t1_ids = {q.id for q in REQUIRED_QUESTIONS if q.tier == 1}
    assert {q.id for q in pending} == t1_ids


@pytest.mark.unit
def test_required_unanswered_includes_t2_when_dependency_holds() -> None:
    answers = {q.id: "x" for q in REQUIRED_QUESTIONS if q.tier == 1}
    answers["business_type"] = "Manufacturing (生产型出口)"

    pending = required_unanswered(answers)

    pending_ids = {q.id for q in pending}
    assert "has_subcontracting" in pending_ids


@pytest.mark.unit
def test_required_unanswered_skips_t2_when_dependency_does_not_hold() -> None:
    answers = {q.id: "x" for q in REQUIRED_QUESTIONS if q.tier == 1}
    answers["business_type"] = "Trading (买卖型外贸)"

    pending = required_unanswered(answers)

    pending_ids = {q.id for q in pending}
    assert "has_subcontracting" not in pending_ids


@pytest.mark.unit
def test_required_unanswered_treats_empty_string_as_unanswered() -> None:
    answers = {q.id: "x" for q in REQUIRED_QUESTIONS if q.tier == 1}
    answers["company_name"] = ""

    pending = required_unanswered(answers)

    assert any(q.id == "company_name" for q in pending)
