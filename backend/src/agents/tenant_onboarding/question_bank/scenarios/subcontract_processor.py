"""``subcontract_processor`` — 来料加工.

Customer supplies materials, processor charges a fee for the work and
returns finished goods. Revenue is the processing fee — material flow is
non-financial Stock Entries.
"""

from __future__ import annotations

from src.agents.tenant_onboarding.question_bank.types import (
    Choice,
    OnboardingQuestion,
    ScenarioPack,
)

_FEE_BASIS_CHOICES: tuple[Choice, ...] = (
    Choice(value="per_piece", label_cn="按件", label_en="Per piece"),
    Choice(value="per_kg", label_cn="按公斤", label_en="Per kg"),
    Choice(value="per_hour", label_cn="按工时", label_en="Per hour"),
    Choice(value="per_order", label_cn="按订单总价", label_en="Flat per order"),
)

_SPLIT_ACCOUNTING_CHOICES: tuple[Choice, ...] = (
    Choice(value="split", label_cn="是,科目分开", label_en="Yes, split accounts"),
    Choice(value="merged", label_cn="否,合并入收入", label_en="No, merged into revenue"),
)

_PROCESS_TYPE_CHOICES: tuple[Choice, ...] = (
    Choice(value="machining", label_cn="机加工", label_en="Machining"),
    Choice(value="assembly", label_cn="装配", label_en="Assembly"),
    Choice(value="packaging", label_cn="包装", label_en="Packaging"),
    Choice(value="printing", label_cn="印刷", label_en="Printing"),
    Choice(value="dyeing", label_cn="染色", label_en="Dyeing"),
    Choice(value="other", label_cn="其他", label_en="Other"),
)

_SCRAP_CHOICES: tuple[Choice, ...] = (
    Choice(value="all_return", label_cn="全部返还客户", label_en="All returned"),
    Choice(value="partial", label_cn="部分返还", label_en="Partial return"),
    Choice(value="self_handle", label_cn="不返还,自处理", label_en="Self-handled"),
)


_QUESTIONS: tuple[OnboardingQuestion, ...] = (
    OnboardingQuestion(
        id="sp_fee_basis",
        question_cn="加工费结算方式",
        question_en="Processing fee basis",
        profile_path="facts.subcontract_processor.fee_basis",
        qtype="single",
        tier=1,
        choices=_FEE_BASIS_CHOICES,
    ),
    OnboardingQuestion(
        id="sp_split_material_labor",
        question_cn="是否分料工费记账?",
        question_en="Split material vs labor accounts?",
        profile_path="facts.subcontract_processor.split_accounting",
        qtype="single",
        tier=1,
        choices=_SPLIT_ACCOUNTING_CHOICES,
    ),
    OnboardingQuestion(
        id="sp_process_types",
        question_cn="主要加工类型(多选)",
        question_en="Primary process types (multi)",
        profile_path="facts.subcontract_processor.process_types",
        qtype="multi",
        tier=1,
        choices=_PROCESS_TYPE_CHOICES,
        max_select=4,
    ),
    OnboardingQuestion(
        id="sp_scrap_handling",
        question_cn="废料/余料处理",
        question_en="Scrap / off-cut handling",
        profile_path="facts.subcontract_processor.scrap_handling",
        qtype="single",
        tier=1,
        choices=_SCRAP_CHOICES,
    ),
)


def _erpnext_init(answers: dict, profile: dict, erp) -> dict:  # noqa: ANN001
    fee_basis = answers.get("sp_fee_basis") or "per_piece"
    split = answers.get("sp_split_material_labor") or "merged"
    process_types = answers.get("sp_process_types") or []

    return {
        "scenario": "subcontract_processor",
        "service_items": [{"name": f"Processing Fee - {pt}", "is_stock_item": 0, "fee_basis": fee_basis} for pt in process_types],
        "split_accounting": split == "split",
        "stock_entry_templates": ["Material Receipt", "Manufacture", "Material Issue"],
        "explicitly_skip": ["BOM (own)", "Subscription"],
    }


SCENARIO = ScenarioPack(
    id="subcontract_processor",
    name_cn="来料加工",
    name_en="Subcontract processor",
    parent_category="manufacturing",
    description_cn="客户给料,我们加工后返还,赚加工费",
    questions=_QUESTIONS,
    profile_defaults={
        "operational_patterns.primary_workflow": "subcontract_processing",
    },
    erpnext_init_template=_erpnext_init,
)


__all__ = ["SCENARIO"]
