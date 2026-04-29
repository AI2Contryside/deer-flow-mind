"""Unit tests for the v3 tenant_onboarding composer + question_bank registry."""

from __future__ import annotations

import pytest

from src.agents.tenant_onboarding import (
    OnboardingFacts,
    TenantProfile,
    compose_initial_profile,
    minimal_profile_dict,
)
from src.agents.tenant_onboarding.composer import collect_erpnext_init_blueprints
from src.agents.tenant_onboarding.question_bank import (
    COMMON_QUESTIONS,
    META_QUESTIONS,
    build_question_plan,
    get_registry,
    required_unanswered,
)
from src.agents.tenant_profile.summarizer.schema import upgrade_profile_dict

# ---------------------------------------------------------------------------
# Schema upgrade (v2 → v3)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_minimal_profile_validates_against_runtime_schema() -> None:
    """Empty/fallback profile must round-trip through TenantProfile."""
    data = minimal_profile_dict("acme-001")
    profile = TenantProfile.model_validate(data)

    assert profile.tenant_id == "acme-001"
    assert profile.schema_version == 3
    assert profile.summary
    assert profile.scenarios == []


@pytest.mark.unit
def test_v2_profile_dict_auto_upgrades_to_v3() -> None:
    legacy = {
        "schema_version": 2,
        "generated_at": "2025-01-01T00:00:00Z",
        "tenant_id": "old-tenant",
        "summary": "legacy",
        "facts": {"company": {"name": "Legacy Co"}},
        "operational_patterns": {},
        "key_entities": {},
        "taxonomy": {},
        "open_questions": [],
    }

    upgraded = upgrade_profile_dict(dict(legacy))

    assert upgraded["schema_version"] == 3
    assert upgraded["scenarios"] == []
    profile = TenantProfile.model_validate(upgraded)
    assert profile.schema_version == 3
    assert profile.scenarios == []


# ---------------------------------------------------------------------------
# Composer (v3 facts namespace + scenarios)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_compose_brokerage_writes_v3_facts_namespaces() -> None:
    facts = OnboardingFacts(
        tenant_id="t-1",
        answers={
            "company_name": "Acme Trading",
            "primary_categories": ["trade"],
            "detailed_scenarios": ["brokerage"],
            "company_country": "China",
            "company_currency": "CNY",
            "fiscal_year_start": "1",
            "multi_company": "single",
            "monthly_volume_band": "10-100",
            "brokerage_warehouse_passthrough": "never",
            "brokerage_selling_currencies": ["USD", "EUR"],
            "brokerage_buying_currencies": ["CNY"],
            "brokerage_default_incoterm": "FOB",
            "brokerage_settlement_tools": ["TT", "LC"],
            "brokerage_uses_sales_partners": "external",
        },
    )

    data = compose_initial_profile(facts)
    profile = TenantProfile.model_validate(data)

    assert profile.scenarios == [{"category": "trade", "scenarios": ["brokerage"]}] or [
        s.model_dump() for s in profile.scenarios
    ] == [{"category": "trade", "scenarios": ["brokerage"]}]
    assert profile.facts["company"] == {
        "name": "Acme Trading",
        "country": "China",
        "currency": "CNY",
        "fiscal_year_start_month": "1",
    }
    assert profile.facts["flags"]["multi_company_structure"] == "single"
    assert profile.facts["flags"]["uses_landed_cost"] is False
    assert profile.facts["brokerage"] == {
        "warehouse_passthrough": "never",
        "settlement_tools": ["TT", "LC"],
        "uses_sales_partners": "external",
    }
    assert profile.operational_patterns.selling_currencies == ["USD", "EUR"]
    assert profile.operational_patterns.buying_currencies == ["CNY"]
    assert profile.operational_patterns.default_incoterm == "FOB"
    assert profile.operational_patterns.monthly_volume_band == "10-100"
    # warehouse_passthrough=never → drop_ship trade_mode
    assert profile.operational_patterns.trade_mode == "drop_ship"


@pytest.mark.unit
def test_compose_general_fallback_when_unknown_only() -> None:
    facts = OnboardingFacts(
        tenant_id="t-2",
        answers={
            "company_name": "Mystery Co",
            "primary_categories": ["unknown"],
            "company_country": "China",
            "company_currency": "USD",
            "fiscal_year_start": "1",
            "multi_company": "single",
            "monthly_volume_band": "<10",
            "gen_description": "We do many things.",
            "gen_handles_physical_goods": "passthrough",
            "gen_invoicing_mode": "b2b",
            "gen_multi_currency": "single",
        },
    )

    data = compose_initial_profile(facts)
    profile = TenantProfile.model_validate(data)

    assert [s.model_dump() for s in profile.scenarios] == [
        {"category": "unknown", "scenarios": ["general"]},
    ]
    assert profile.facts["general"]["description"] == "We do many things."
    assert profile.facts["general"]["handles_physical_goods"] == "passthrough"


@pytest.mark.unit
def test_compose_caps_imported_rows_per_doctype() -> None:
    customers = [
        {"name": f"CUST-{i:03d}", "display": f"Customer {i}"} for i in range(50)
    ]
    facts = OnboardingFacts(
        tenant_id="t-3",
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
        tenant_id="t-4",
        answers={"company_name": "X"},
        open_questions=[
            {"question": f"Q{i}", "candidates": [f"c{j}" for j in range(15)]}
            for i in range(15)
        ],
    )

    data = compose_initial_profile(facts)
    profile = TenantProfile.model_validate(data)

    assert len(profile.open_questions) == 10
    assert all(len(oq.candidates) <= 10 for oq in profile.open_questions)


@pytest.mark.unit
def test_collect_erpnext_init_blueprints_emits_brokerage_shape() -> None:
    answers = {
        "primary_categories": ["trade"],
        "detailed_scenarios": ["brokerage"],
        "brokerage_selling_currencies": ["USD"],
        "brokerage_buying_currencies": ["CNY"],
        "brokerage_settlement_tools": ["LC"],
        "brokerage_uses_sales_partners": "external",
    }

    blueprints = collect_erpnext_init_blueprints(answers)

    assert len(blueprints) == 1
    bp = blueprints[0]
    assert bp["scenario"] == "brokerage"
    assert bp["item_template"]["is_stock_item"] == 0
    price_lists = {pl["name"] for pl in bp["price_lists"]}
    assert "Standard Selling - USD" in price_lists
    assert "Standard Buying - CNY" in price_lists
    # LC was selected → custom fields seeded
    assert any(cf["fieldname"] == "lc_number" for cf in bp["custom_fields"])
    assert bp["sales_partner_seed"] is True


# ---------------------------------------------------------------------------
# Question plan / registry
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_registry_discovers_general_and_brokerage() -> None:
    registry = get_registry()
    assert "general" in registry
    assert "brokerage" in registry
    assert registry["brokerage"].parent_category == "trade"
    assert registry["general"].parent_category == "unknown"


@pytest.mark.unit
def test_plan_starts_with_meta_then_common() -> None:
    plan_ids = [q.id for q in build_question_plan({})]

    # Q0 must be first.
    assert plan_ids[0] == "primary_categories"
    # Common follows immediately after meta (since detailed_scenarios is
    # gated behind primary_categories being answered).
    assert plan_ids[1:] == [q.id for q in COMMON_QUESTIONS]


@pytest.mark.unit
def test_plan_after_q0_loads_q1_then_common() -> None:
    plan_ids = [q.id for q in build_question_plan({"primary_categories": ["trade"]})]

    assert plan_ids[0] == "detailed_scenarios"
    # No scenario-pack questions yet — detailed_scenarios still pending.
    assert all(not pid.startswith("brokerage_") for pid in plan_ids)


@pytest.mark.unit
def test_plan_after_q1_loads_brokerage_questions() -> None:
    answers = {
        "primary_categories": ["trade"],
        "detailed_scenarios": ["brokerage"],
    }
    plan_ids = [q.id for q in build_question_plan(answers)]

    common_ids = [q.id for q in COMMON_QUESTIONS]
    brokerage_qids = [
        "brokerage_warehouse_passthrough",
        "brokerage_selling_currencies",
        "brokerage_buying_currencies",
        "brokerage_default_incoterm",
        "brokerage_settlement_tools",
        "brokerage_uses_sales_partners",
    ]
    # Common comes before scenario questions.
    assert plan_ids[: len(common_ids)] == common_ids
    assert plan_ids[len(common_ids) :] == brokerage_qids


@pytest.mark.unit
def test_plan_unknown_routes_to_general_skipping_q1() -> None:
    plan_ids = [q.id for q in build_question_plan({"primary_categories": ["unknown"]})]

    # Q1 is skipped (depends_on returns False when only `unknown` is picked).
    assert "detailed_scenarios" not in plan_ids
    # General pack questions follow Common.
    assert "gen_description" in plan_ids
    assert "gen_handles_physical_goods" in plan_ids


@pytest.mark.unit
def test_plan_dedupes_scenario_questions_by_profile_path() -> None:
    """When two scenarios share a question with the same profile_path,
    the plan asks it only once."""
    # Brokerage has selling_currencies tied to operational_patterns.selling_currencies.
    # Re-include the same answers but pretend two scenarios were picked —
    # general is the only second pack we have today, and it doesn't share
    # profile_paths with brokerage, so we assert "no duplicate IDs in the plan"
    # as the structural invariant.
    answers = {
        "primary_categories": ["trade", "unknown"],
        "detailed_scenarios": ["brokerage", "general"],
    }
    plan = build_question_plan(answers)

    ids = [q.id for q in plan]
    assert len(ids) == len(set(ids))


@pytest.mark.unit
def test_required_unanswered_compat_shim_delegates_to_plan() -> None:
    """Legacy callers using ``required_unanswered`` get the v3 plan back."""
    pending = required_unanswered({})
    plan = build_question_plan({})

    assert [q.id for q in pending] == [q.id for q in plan]


@pytest.mark.unit
def test_meta_and_common_questions_have_stable_ids() -> None:
    """Regression guard: PG `tenant_profile` rows reference these IDs by
    string. Renaming a question without coordinated migration breaks
    historical tenants on the settings page."""
    assert [q.id for q in META_QUESTIONS] == [
        "primary_categories",
        "detailed_scenarios",
    ]
    assert [q.id for q in COMMON_QUESTIONS] == [
        "company_country",
        "company_currency",
        "fiscal_year_start",
        "multi_company",
        "monthly_volume_band",
    ]


@pytest.mark.unit
def test_company_name_question_removed_from_bank() -> None:
    """Regression guard: company_name is provided by the lead agent now,
    so onboarding must NOT re-ask the user.
    """
    plan = build_question_plan({})
    assert all(q.id != "company_name" for q in plan)


# ---------------------------------------------------------------------------
# Coverage for the full 17-scenario registry
# ---------------------------------------------------------------------------


_EXPECTED_SCENARIOS = {
    "trade": {"brokerage", "import_export", "domestic_wholesale", "cross_border_ecommerce"},
    "manufacturing": {"branded_oem", "oem_contract", "subcontract_processor"},
    "services": {"consulting", "trade_services", "tech_software"},
    "retail": {"physical_store", "online_dtc"},
    "project": {"engineering_contract", "creative_agency"},
    "assets": {"equipment_rental", "property_management"},
    "unknown": {"general"},
}


@pytest.mark.unit
def test_registry_has_full_17_scenario_coverage() -> None:
    registry = get_registry()
    expected_ids = set().union(*_EXPECTED_SCENARIOS.values())
    assert set(registry) == expected_ids
    for category, ids in _EXPECTED_SCENARIOS.items():
        actual = {p.id for p in registry.values() if p.parent_category == category}
        assert actual == ids, f"category {category}: expected {ids}, got {actual}"


@pytest.mark.unit
@pytest.mark.parametrize(
    "scenario_id",
    sorted(set().union(*_EXPECTED_SCENARIOS.values())),
)
def test_each_scenario_pack_has_well_formed_questions(scenario_id: str) -> None:
    """Every pack must have ≥1 question and every question must declare a
    ``profile_path`` that the composer can project an answer into."""
    pack = get_registry()[scenario_id]
    assert pack.questions, f"{scenario_id} has no questions"
    for q in pack.questions:
        assert q.id, f"{scenario_id}: empty question id"
        assert q.profile_path, f"{scenario_id}/{q.id}: empty profile_path"
        if q.qtype in ("single", "multi", "int_band"):
            assert q.choices, f"{scenario_id}/{q.id}: choice-type question without choices"


@pytest.mark.unit
@pytest.mark.parametrize(
    "scenario_id",
    sorted(set().union(*_EXPECTED_SCENARIOS.values())),
)
def test_each_scenario_pack_emits_blueprint(scenario_id: str) -> None:
    """Each scenario's ``erpnext_init_template`` must produce a dict
    blueprint when called with a ``primary_categories`` / ``detailed_scenarios``
    selection that targets it. Blueprints are idempotent so empty answers
    are also a valid input."""
    registry = get_registry()
    pack = registry[scenario_id]
    answers: dict = {
        "primary_categories": [pack.parent_category],
        "detailed_scenarios": [scenario_id],
    }
    blueprints = collect_erpnext_init_blueprints(answers)
    assert len(blueprints) == 1
    assert blueprints[0]["scenario"] == scenario_id


@pytest.mark.unit
def test_multi_scenario_plan_dedupes_shared_profile_paths() -> None:
    """import_export and branded_oem both write commodity_categories under
    ``facts.defaults.commodity_categories`` — only the first occurrence
    should appear in the plan."""
    answers = {
        "primary_categories": ["trade", "manufacturing"],
        "detailed_scenarios": ["import_export", "branded_oem"],
    }
    plan = build_question_plan(answers)
    ids = [q.id for q in plan]

    assert len(ids) == len(set(ids))
    # The import_export commodity question survives.
    assert "ie_commodity_categories" in ids
    # The branded_oem one is deduped because it shares the profile_path.
    assert "bo_product_categories" not in ids


@pytest.mark.unit
def test_compose_import_export_derives_tracking_flags() -> None:
    facts = OnboardingFacts(
        tenant_id="t-ie",
        answers={
            "company_name": "Self Trader Co",
            "primary_categories": ["trade"],
            "detailed_scenarios": ["import_export"],
            "company_country": "China",
            "company_currency": "CNY",
            "fiscal_year_start": "1",
            "multi_company": "single",
            "monthly_volume_band": "100-500",
            "ie_selling_currencies": ["USD"],
            "ie_buying_currencies": ["CNY"],
            "ie_logistics_modes": ["sea"],
            "ie_tracking_level": "both",
            "ie_commodity_categories": ["electronics"],
            "ie_uses_landed_cost": "monthly",
        },
    )

    data = compose_initial_profile(facts)
    profile = TenantProfile.model_validate(data)

    assert profile.facts["flags"]["requires_batch_tracking"] is True
    assert profile.facts["flags"]["requires_serial_tracking"] is True
    # import_export profile_defaults is the plain "self_trade" trade_mode.
    assert profile.operational_patterns.trade_mode == "self_trade"


@pytest.mark.unit
def test_compose_consulting_routes_to_services_category() -> None:
    facts = OnboardingFacts(
        tenant_id="t-cs",
        answers={
            "company_name": "Acme Advisory",
            "primary_categories": ["services"],
            "detailed_scenarios": ["consulting"],
            "company_country": "Singapore",
            "company_currency": "USD",
            "fiscal_year_start": "1",
            "multi_company": "single",
            "monthly_volume_band": "10-100",
            "cs_billing_mode": "milestone",
            "cs_default_hourly_rate": "1500-3000",
            "cs_typical_duration": "3-6m",
            "cs_milestone_billing": "2-3",
        },
    )

    data = compose_initial_profile(facts)
    profile = TenantProfile.model_validate(data)

    assert [s.model_dump() for s in profile.scenarios] == [
        {"category": "services", "scenarios": ["consulting"]},
    ]
    assert profile.facts["consulting"] == {
        "billing_mode": "milestone",
        "default_hourly_rate": "1500-3000",
        "typical_duration": "3-6m",
        "milestone_billing": "2-3",
    }
    assert profile.operational_patterns.primary_workflow == "consulting"


@pytest.mark.unit
def test_compose_multi_category_emits_multiple_scenario_refs() -> None:
    """A tenant who picks both manufacturing and services gets one
    ScenarioRef per category, with packs grouped under their owner.
    """
    facts = OnboardingFacts(
        tenant_id="t-multi",
        answers={
            "company_name": "Hybrid Co",
            "primary_categories": ["manufacturing", "services"],
            "detailed_scenarios": ["branded_oem", "consulting"],
            "company_country": "China",
            "company_currency": "CNY",
            "fiscal_year_start": "1",
            "multi_company": "single",
            "monthly_volume_band": "10-100",
            # branded_oem
            "bo_bom_depth": "2-3",
            "bo_uses_workstation": "full_routing",
            "bo_qc_stages": ["incoming", "outgoing"],
            "bo_uses_subcontracting": "auxiliary",
            # consulting
            "cs_billing_mode": "hourly",
            "cs_default_hourly_rate": "500-1500",
            "cs_typical_duration": "1-3m",
            "cs_milestone_billing": "single",
        },
    )

    data = compose_initial_profile(facts)
    profile = TenantProfile.model_validate(data)
    refs = [s.model_dump() for s in profile.scenarios]

    assert refs == [
        {"category": "manufacturing", "scenarios": ["branded_oem"]},
        {"category": "services", "scenarios": ["consulting"]},
    ]
    # Both packs' fact subtrees populated.
    assert profile.facts["branded_oem"]["bom_depth"] == "2-3"
    assert profile.facts["consulting"]["billing_mode"] == "hourly"


@pytest.mark.unit
def test_collect_blueprints_emits_one_per_selected_scenario() -> None:
    answers = {
        "primary_categories": ["trade", "manufacturing"],
        "detailed_scenarios": ["brokerage", "branded_oem"],
        "brokerage_selling_currencies": ["USD"],
        "brokerage_buying_currencies": ["CNY"],
    }

    blueprints = collect_erpnext_init_blueprints(answers)

    scenario_ids = [bp["scenario"] for bp in blueprints]
    assert scenario_ids == ["brokerage", "branded_oem"]


@pytest.mark.unit
def test_pack_ids_are_url_safe_for_pg_storage() -> None:
    """Scenario IDs land in a Postgres TEXT[] column (`detailed_scenarios`)
    and in JSONB keys (`scenario_data`). Keep them ASCII-snake_case so we
    don't have to worry about encoding edge cases on the FE settings page.
    """
    import re

    pattern = re.compile(r"^[a-z][a-z0-9_]*$")
    for sid in get_registry():
        assert pattern.match(sid), f"scenario id {sid!r} not url-safe"
