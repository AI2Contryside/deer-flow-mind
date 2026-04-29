"""``engineering_contract`` — 工程总包.

Large, long-cycle, milestone-billed projects, often subcontracted in part.
Project + Cost Center + Payment Schedule + Subcontracting interplay.
"""

from __future__ import annotations

from src.agents.tenant_onboarding.question_bank.types import (
    Choice,
    OnboardingQuestion,
    ScenarioPack,
)

_DURATION_CHOICES: tuple[Choice, ...] = (
    Choice(value="<3m", label_cn="<3月", label_en="<3 months"),
    Choice(value="3-12m", label_cn="3-12月", label_en="3-12 months"),
    Choice(value="1-3y", label_cn="1-3年", label_en="1-3 years"),
    Choice(value="3y+", label_cn="3年+", label_en="3+ years"),
)

_MILESTONE_CHOICES: tuple[Choice, ...] = (
    Choice(value="advance", label_cn="预付款", label_en="Advance payment"),
    Choice(value="progress", label_cn="进度款", label_en="Progress payment"),
    Choice(value="completion", label_cn="竣工款", label_en="Completion payment"),
    Choice(value="warranty", label_cn="质保金", label_en="Warranty retention"),
    Choice(value="acceptance", label_cn="验收款", label_en="Acceptance payment"),
)

_SUBCONTRACTOR_CHOICES: tuple[Choice, ...] = (
    Choice(value="none", label_cn="否,自营", label_en="No, self-executed"),
    Choice(value="partial", label_cn="是,部分", label_en="Yes, partial"),
    Choice(value="heavy", label_cn="是,大量", label_en="Yes, heavy"),
)

_PROGRESSIVE_BILLING_CHOICES: tuple[Choice, ...] = (
    Choice(value="completion", label_cn="否,完工开票", label_en="No, on completion"),
    Choice(value="monthly", label_cn="是,按月", label_en="Yes, monthly"),
    Choice(value="milestone", label_cn="是,按节点", label_en="Yes, by milestone"),
)

_PROJECT_TYPE_CHOICES: tuple[Choice, ...] = (
    Choice(value="civil", label_cn="土建", label_en="Civil"),
    Choice(value="mep", label_cn="机电", label_en="MEP"),
    Choice(value="decoration", label_cn="装饰", label_en="Decoration"),
    Choice(value="it", label_cn="IT", label_en="IT"),
    Choice(value="landscape", label_cn="园林", label_en="Landscape"),
    Choice(value="other", label_cn="其他", label_en="Other"),
)


_QUESTIONS: tuple[OnboardingQuestion, ...] = (
    OnboardingQuestion(
        id="ec_typical_duration",
        question_cn="项目典型周期",
        question_en="Typical project duration",
        profile_path="facts.engineering_contract.typical_duration",
        qtype="single",
        tier=1,
        choices=_DURATION_CHOICES,
    ),
    OnboardingQuestion(
        id="ec_payment_milestones",
        question_cn="收款节点(多选)",
        question_en="Payment milestones (multi)",
        profile_path="facts.engineering_contract.payment_milestones",
        qtype="multi",
        tier=1,
        choices=_MILESTONE_CHOICES,
        max_select=5,
    ),
    OnboardingQuestion(
        id="ec_uses_subcontractors",
        question_cn="是否分包?",
        question_en="Subcontracting?",
        profile_path="facts.engineering_contract.uses_subcontractors",
        qtype="single",
        tier=1,
        choices=_SUBCONTRACTOR_CHOICES,
    ),
    OnboardingQuestion(
        id="ec_progressive_billing",
        question_cn="进度款发票?",
        question_en="Progressive billing?",
        profile_path="facts.engineering_contract.progressive_billing",
        qtype="single",
        tier=1,
        choices=_PROGRESSIVE_BILLING_CHOICES,
    ),
    OnboardingQuestion(
        id="ec_project_types",
        question_cn="主要项目类型(多选)",
        question_en="Primary project types (multi)",
        profile_path="facts.engineering_contract.project_types",
        qtype="multi",
        tier=1,
        choices=_PROJECT_TYPE_CHOICES,
        max_select=4,
    ),
)


def _erpnext_init(answers: dict, profile: dict, erp) -> dict:  # noqa: ANN001
    milestones = answers.get("ec_payment_milestones") or []
    sub = answers.get("ec_uses_subcontractors") or "none"
    project_types = answers.get("ec_project_types") or []

    return {
        "scenario": "engineering_contract",
        "project_type": [{"name": pt, "kind": "ensure"} for pt in project_types],
        "cost_center_per_project": True,
        "payment_schedule_template": [{"milestone": m} for m in milestones],
        "subcontracting_order": sub != "none",
        "explicitly_skip": ["POS", "Stock-heavy SLE"],
    }


SCENARIO = ScenarioPack(
    id="engineering_contract",
    name_cn="工程总包",
    name_en="Engineering general contractor",
    parent_category="project",
    description_cn="大额、长周期、按节点收款,常分包",
    questions=_QUESTIONS,
    profile_defaults={
        "operational_patterns.primary_workflow": "engineering_contract",
    },
    erpnext_init_template=_erpnext_init,
)


__all__ = ["SCENARIO"]
