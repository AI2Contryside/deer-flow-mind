"""``property_management`` — 物业/资产托管.

Manages property assets (residential, commercial, office). Collects rent,
property fees, utilities. Asset tree + Subscription billing for recurring
charges + maintenance work-orders.
"""

from __future__ import annotations

from src.agents.tenant_onboarding.question_bank.types import (
    Choice,
    OnboardingQuestion,
    ScenarioPack,
)

_PROPERTY_TYPE_CHOICES: tuple[Choice, ...] = (
    Choice(value="residential", label_cn="住宅", label_en="Residential"),
    Choice(value="commercial", label_cn="商业", label_en="Commercial"),
    Choice(value="office", label_cn="办公", label_en="Office"),
    Choice(value="industrial", label_cn="工业", label_en="Industrial"),
    Choice(value="mixed", label_cn="混合", label_en="Mixed"),
)

_CHARGE_ITEM_CHOICES: tuple[Choice, ...] = (
    Choice(value="rent", label_cn="租金", label_en="Rent"),
    Choice(value="property_fee", label_cn="物业费", label_en="Property fee"),
    Choice(value="utilities", label_cn="水电", label_en="Utilities"),
    Choice(value="parking", label_cn="停车", label_en="Parking"),
    Choice(value="advertising", label_cn="广告位", label_en="Advertising space"),
    Choice(value="other", label_cn="其他", label_en="Other"),
)

_LEASE_CHOICES: tuple[Choice, ...] = (
    Choice(value="full", label_cn="是,带到期/续约提醒", label_en="Yes, with renewal alerts"),
    Choice(value="none", label_cn="否", label_en="No"),
)

_MAINTENANCE_CHOICES: tuple[Choice, ...] = (
    Choice(value="none", label_cn="否", label_en="No"),
    Choice(value="internal", label_cn="是,内部团队", label_en="Yes, internal team"),
    Choice(value="external", label_cn="是,外包", label_en="Yes, outsourced"),
    Choice(value="mixed", label_cn="混合", label_en="Mixed"),
)


_QUESTIONS: tuple[OnboardingQuestion, ...] = (
    OnboardingQuestion(
        id="pm_property_types",
        question_cn="主要管理对象(多选)",
        question_en="Primary property types managed (multi)",
        profile_path="facts.property_management.property_types",
        qtype="multi",
        tier=1,
        choices=_PROPERTY_TYPE_CHOICES,
        max_select=5,
    ),
    OnboardingQuestion(
        id="pm_charge_items",
        question_cn="收费项目(多选)",
        question_en="Charge items (multi)",
        profile_path="facts.property_management.charge_items",
        qtype="multi",
        tier=1,
        choices=_CHARGE_ITEM_CHOICES,
        max_select=6,
    ),
    OnboardingQuestion(
        id="pm_lease_management",
        question_cn="是否租约管理?",
        question_en="Lease management?",
        profile_path="facts.property_management.lease_management",
        qtype="single",
        tier=1,
        choices=_LEASE_CHOICES,
    ),
    OnboardingQuestion(
        id="pm_maintenance_workflow",
        question_cn="维修工单",
        question_en="Maintenance work-orders",
        profile_path="facts.property_management.maintenance_workflow",
        qtype="single",
        tier=1,
        choices=_MAINTENANCE_CHOICES,
    ),
)


def _erpnext_init(answers: dict, profile: dict, erp) -> dict:  # noqa: ANN001
    property_types = answers.get("pm_property_types") or []
    charge_items = answers.get("pm_charge_items") or []
    lease = answers.get("pm_lease_management") or "none"
    maintenance = answers.get("pm_maintenance_workflow") or "none"

    return {
        "scenario": "property_management",
        "asset_tree": [{"name": pt, "kind": "ensure"} for pt in property_types],
        "service_items": [{"name": ci, "is_stock_item": 0, "kind": "ensure"} for ci in charge_items],
        "subscription_recurring_charges": True,
        "lease_renewal_notification": lease == "full",
        "maintenance_workflow": maintenance != "none",
        "explicitly_skip": ["Stock", "BOM", "Manufacturing"],
    }


SCENARIO = ScenarioPack(
    id="property_management",
    name_cn="物业/资产托管",
    name_en="Property / asset management",
    parent_category="assets",
    description_cn="管房子/铺面,收租金物业费",
    questions=_QUESTIONS,
    profile_defaults={
        "operational_patterns.primary_workflow": "property_management",
    },
    erpnext_init_template=_erpnext_init,
)


__all__ = ["SCENARIO"]
