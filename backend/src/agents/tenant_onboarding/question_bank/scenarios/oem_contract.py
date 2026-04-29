"""``oem_contract`` — OEM contract manufacturer.

Builds for brand customers; materials may be customer-supplied, BOM may
be customer-supplied. Capacity tracking matters; own-brand modules off.
"""

from __future__ import annotations

from src.agents.tenant_onboarding.question_bank.types import (
    Choice,
    OnboardingQuestion,
    ScenarioPack,
)

_MATERIAL_SUPPLY_CHOICES: tuple[Choice, ...] = (
    Choice(value="self", label_cn="否,自购", label_en="No, self-purchased"),
    Choice(value="customer", label_cn="是,客供", label_en="Yes, customer-supplied"),
    Choice(value="partial", label_cn="部分客供", label_en="Partial"),
)

_BOM_SOURCE_CHOICES: tuple[Choice, ...] = (
    Choice(value="self", label_cn="自家 BOM", label_en="Own BOM"),
    Choice(value="customer", label_cn="客户提供", label_en="Customer-supplied"),
    Choice(value="mixed", label_cn="混合", label_en="Mixed"),
)

_CAPACITY_CHOICES: tuple[Choice, ...] = (
    Choice(value="<10k", label_cn="<1万件", label_en="<10k units"),
    Choice(value="10k-100k", label_cn="1-10万", label_en="10k-100k"),
    Choice(value="100k-1m", label_cn="10-100万", label_en="100k-1M"),
    Choice(value="1m+", label_cn="100万+", label_en="1M+"),
)

_CUSTOMER_TYPE_CHOICES: tuple[Choice, ...] = (
    Choice(value="brand", label_cn="品牌商", label_en="Brand owner"),
    Choice(value="trader", label_cn="贸易商", label_en="Trader"),
    Choice(value="retailer", label_cn="零售商", label_en="Retailer"),
    Choice(value="other", label_cn="其他", label_en="Other"),
)


_QUESTIONS: tuple[OnboardingQuestion, ...] = (
    OnboardingQuestion(
        id="oc_customer_supplies_materials",
        question_cn="客户是否供料?",
        question_en="Customer-supplied materials?",
        profile_path="facts.oem_contract.customer_supplies_materials",
        qtype="single",
        tier=1,
        choices=_MATERIAL_SUPPLY_CHOICES,
    ),
    OnboardingQuestion(
        id="oc_uses_customer_bom",
        question_cn="使用谁的 BOM?",
        question_en="Whose BOM?",
        profile_path="facts.oem_contract.bom_source",
        qtype="single",
        tier=1,
        choices=_BOM_SOURCE_CHOICES,
    ),
    OnboardingQuestion(
        id="oc_monthly_capacity",
        question_cn="月产能上限",
        question_en="Monthly capacity ceiling",
        profile_path="facts.oem_contract.monthly_capacity",
        qtype="single",
        tier=1,
        choices=_CAPACITY_CHOICES,
    ),
    OnboardingQuestion(
        id="oc_customer_types",
        question_cn="主要客户类型(多选)",
        question_en="Primary customer types (multi)",
        profile_path="facts.oem_contract.customer_types",
        qtype="multi",
        tier=1,
        choices=_CUSTOMER_TYPE_CHOICES,
        max_select=4,
    ),
)


def _erpnext_init(answers: dict, profile: dict, erp) -> dict:  # noqa: ANN001
    cust_supplies = answers.get("oc_customer_supplies_materials") or "self"
    bom_source = answers.get("oc_uses_customer_bom") or "self"
    customer_types = answers.get("oc_customer_types") or []

    return {
        "scenario": "oem_contract",
        "item_template": {
            "is_stock_item": 1,
            "customer_provided_item": int(cust_supplies != "self"),
        },
        "customer_groups": [{"name": ct, "kind": "ensure"} for ct in customer_types],
        "bom_import_hint": bom_source != "self",
        "capacity_band_recorded": True,
        "explicitly_skip": ["POS", "Asset Category"],
    }


SCENARIO = ScenarioPack(
    id="oem_contract",
    name_cn="OEM 代工",
    name_en="OEM contract manufacturing",
    parent_category="manufacturing",
    description_cn="给品牌方代工,可能客供料、可能用客户 BOM",
    questions=_QUESTIONS,
    profile_defaults={
        "operational_patterns.primary_workflow": "oem_contract",
    },
    erpnext_init_template=_erpnext_init,
)


__all__ = ["SCENARIO"]
