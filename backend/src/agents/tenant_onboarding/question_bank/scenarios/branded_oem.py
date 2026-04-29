"""``branded_oem`` — 自有品牌制造.

Own-brand manufacturer; own BOMs, own SKUs, full Manufacturing module.
Distinct from ``oem_contract`` (where brand and BOM might come from the
customer) and ``subcontract_processor`` (where the customer also supplies
materials).
"""

from __future__ import annotations

from src.agents.tenant_onboarding.question_bank.types import (
    Choice,
    OnboardingQuestion,
    ScenarioPack,
)

_BOM_DEPTH_CHOICES: tuple[Choice, ...] = (
    Choice(value="single", label_cn="单层", label_en="Single level"),
    Choice(value="2-3", label_cn="2-3层", label_en="2-3 levels"),
    Choice(value="3+", label_cn="3层以上", label_en="3+ levels"),
)

_WORKSTATION_CHOICES: tuple[Choice, ...] = (
    Choice(value="full_routing", label_cn="是,完整 Routing", label_en="Yes, full routing"),
    Choice(value="order_only", label_cn="否,只跟踪整单", label_en="No, order-level only"),
)

_QC_STAGE_CHOICES: tuple[Choice, ...] = (
    Choice(value="incoming", label_cn="收料 QC", label_en="Incoming QC"),
    Choice(value="in_process", label_cn="在制 QC", label_en="In-process QC"),
    Choice(value="outgoing", label_cn="出货 QC", label_en="Outgoing QC"),
    Choice(value="none", label_cn="都不做", label_en="None"),
)

_PRODUCT_TYPE_CHOICES: tuple[Choice, ...] = (
    Choice(value="electronics", label_cn="电子", label_en="Electronics"),
    Choice(value="apparel", label_cn="服装", label_en="Apparel"),
    Choice(value="machinery", label_cn="机械", label_en="Machinery"),
    Choice(value="food", label_cn="食品", label_en="Food"),
    Choice(value="chemical", label_cn="化工", label_en="Chemicals"),
    Choice(value="home", label_cn="家居", label_en="Home goods"),
    Choice(value="other", label_cn="其他", label_en="Other"),
)

_SUBCONTRACT_CHOICES: tuple[Choice, ...] = (
    Choice(value="none", label_cn="否", label_en="No"),
    Choice(value="auxiliary", label_cn="是,辅料工序", label_en="Yes, auxiliary steps"),
    Choice(value="critical", label_cn="是,关键工序", label_en="Yes, critical steps"),
)


_QUESTIONS: tuple[OnboardingQuestion, ...] = (
    OnboardingQuestion(
        id="bo_bom_depth",
        question_cn="BOM 层级最多多少层?",
        question_en="Maximum BOM depth?",
        profile_path="facts.branded_oem.bom_depth",
        qtype="single",
        tier=1,
        choices=_BOM_DEPTH_CHOICES,
    ),
    OnboardingQuestion(
        id="bo_uses_workstation",
        question_cn="是否启用工序级派工?",
        question_en="Workstation-level routing?",
        profile_path="facts.branded_oem.uses_workstation",
        qtype="single",
        tier=1,
        choices=_WORKSTATION_CHOICES,
    ),
    OnboardingQuestion(
        id="bo_qc_stages",
        question_cn="质检环节(多选)",
        question_en="QC stages (multi)",
        profile_path="facts.branded_oem.qc_stages",
        qtype="multi",
        tier=1,
        choices=_QC_STAGE_CHOICES,
        max_select=4,
    ),
    OnboardingQuestion(
        id="bo_product_categories",
        question_cn="主要产品类型(多选)",
        question_en="Main product types (multi)",
        profile_path="facts.defaults.commodity_categories",
        qtype="multi",
        tier=1,
        choices=_PRODUCT_TYPE_CHOICES,
        max_select=4,
    ),
    OnboardingQuestion(
        id="bo_uses_subcontracting",
        question_cn="是否有委外加工?",
        question_en="Subcontracting?",
        profile_path="facts.flags.uses_subcontracting",
        qtype="single",
        tier=1,
        choices=_SUBCONTRACT_CHOICES,
    ),
)


def _erpnext_init(answers: dict, profile: dict, erp) -> dict:  # noqa: ANN001
    use_ws = (answers.get("bo_uses_workstation") or "order_only") == "full_routing"
    qc = answers.get("bo_qc_stages") or []
    sub = (answers.get("bo_uses_subcontracting") or "none") != "none"
    cats = answers.get("bo_product_categories") or []

    return {
        "scenario": "branded_oem",
        "bom_template": True,
        "workstation_template": use_ws,
        "operation_template": use_ws,
        "quality_procedures": [{"name": stage, "kind": "ensure"} for stage in qc if stage != "none"],
        "subcontracting_order": sub,
        "item_groups": [{"name": cat, "kind": "ensure"} for cat in cats],
        "explicitly_skip": ["POS Profile", "Asset"],
    }


SCENARIO = ScenarioPack(
    id="branded_oem",
    name_cn="自有品牌制造",
    name_en="Own-brand manufacturing",
    parent_category="manufacturing",
    description_cn="自产自销,自家 BOM 自家 SKU",
    questions=_QUESTIONS,
    profile_defaults={
        "operational_patterns.primary_workflow": "branded_manufacturing",
    },
    erpnext_init_template=_erpnext_init,
)


__all__ = ["SCENARIO"]
