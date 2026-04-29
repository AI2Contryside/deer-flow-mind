"""``equipment_rental`` — 设备租赁.

Rents out equipment / machinery / vehicles for a fee. Asset Category +
Maintenance Schedule + Subscription billing for recurring rents.
"""

from __future__ import annotations

from src.agents.tenant_onboarding.question_bank.types import (
    Choice,
    OnboardingQuestion,
    ScenarioPack,
)

_RENTAL_PERIOD_CHOICES: tuple[Choice, ...] = (
    Choice(value="short", label_cn="短租 <1月", label_en="Short (<1 month)"),
    Choice(value="monthly", label_cn="月租", label_en="Monthly"),
    Choice(value="quarterly", label_cn="季租", label_en="Quarterly"),
    Choice(value="yearly", label_cn="年租", label_en="Yearly"),
    Choice(value="mixed", label_cn="混合", label_en="Mixed"),
)

_DEPOSIT_CHOICES: tuple[Choice, ...] = (
    Choice(value="none", label_cn="否", label_en="No"),
    Choice(value="standard", label_cn="是,统一标准", label_en="Yes, standard"),
    Choice(value="per_asset", label_cn="是,按设备", label_en="Yes, per-asset"),
)

_MAINTENANCE_CHOICES: tuple[Choice, ...] = (
    Choice(value="none", label_cn="否", label_en="No"),
    Choice(value="periodic", label_cn="是,周期性", label_en="Yes, periodic"),
    Choice(value="usage", label_cn="是,按使用量", label_en="Yes, usage-based"),
)

_ASSET_TYPE_CHOICES: tuple[Choice, ...] = (
    Choice(value="office", label_cn="办公设备", label_en="Office equipment"),
    Choice(value="construction", label_cn="工程机械", label_en="Construction machinery"),
    Choice(value="it", label_cn="IT 设备", label_en="IT equipment"),
    Choice(value="vehicle", label_cn="车辆", label_en="Vehicles"),
    Choice(value="furniture", label_cn="家具", label_en="Furniture"),
    Choice(value="other", label_cn="其他", label_en="Other"),
)


_QUESTIONS: tuple[OnboardingQuestion, ...] = (
    OnboardingQuestion(
        id="er_rental_period",
        question_cn="主要租赁周期",
        question_en="Primary rental period",
        profile_path="facts.equipment_rental.rental_period",
        qtype="single",
        tier=1,
        choices=_RENTAL_PERIOD_CHOICES,
    ),
    OnboardingQuestion(
        id="er_deposit_management",
        question_cn="押金管理",
        question_en="Deposit management",
        profile_path="facts.equipment_rental.deposit_management",
        qtype="single",
        tier=1,
        choices=_DEPOSIT_CHOICES,
    ),
    OnboardingQuestion(
        id="er_maintenance_schedule",
        question_cn="维护/保养计划",
        question_en="Maintenance schedule",
        profile_path="facts.equipment_rental.maintenance_schedule",
        qtype="single",
        tier=1,
        choices=_MAINTENANCE_CHOICES,
    ),
    OnboardingQuestion(
        id="er_asset_types",
        question_cn="主要资产类型(多选)",
        question_en="Primary asset types (multi)",
        profile_path="facts.equipment_rental.asset_types",
        qtype="multi",
        tier=1,
        choices=_ASSET_TYPE_CHOICES,
        max_select=4,
    ),
)


def _erpnext_init(answers: dict, profile: dict, erp) -> dict:  # noqa: ANN001
    period = answers.get("er_rental_period") or "monthly"
    deposit = answers.get("er_deposit_management") or "none"
    maintenance = answers.get("er_maintenance_schedule") or "none"
    asset_types = answers.get("er_asset_types") or []

    return {
        "scenario": "equipment_rental",
        "asset_categories": [{"name": at, "kind": "ensure"} for at in asset_types],
        "deposit_account": deposit != "none",
        "maintenance_schedule": maintenance != "none",
        "subscription_for_recurring_rent": period in ("monthly", "quarterly", "yearly", "mixed"),
        "explicitly_skip": ["Stock SLE flow"],
    }


SCENARIO = ScenarioPack(
    id="equipment_rental",
    name_cn="设备租赁",
    name_en="Equipment rental",
    parent_category="assets",
    description_cn="出租设备收租金",
    questions=_QUESTIONS,
    profile_defaults={
        "operational_patterns.primary_workflow": "equipment_rental",
    },
    erpnext_init_template=_erpnext_init,
)


__all__ = ["SCENARIO"]
