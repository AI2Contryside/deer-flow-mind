"""``consulting`` — 咨询/方案 (time + deliverables).

Sells time and deliverables; billed by hour, fixed price, or milestones.
Stock module fully off; Project + Timesheet + Activity Cost on.
"""

from __future__ import annotations

from src.agents.tenant_onboarding.question_bank.types import (
    Choice,
    OnboardingQuestion,
    ScenarioPack,
)

_BILLING_CHOICES: tuple[Choice, ...] = (
    Choice(value="hourly", label_cn="按工时", label_en="Hourly"),
    Choice(value="fixed", label_cn="固定项目价", label_en="Fixed project price"),
    Choice(value="milestone", label_cn="按里程碑", label_en="By milestone"),
    Choice(value="outcome", label_cn="按成果", label_en="By outcome"),
    Choice(value="mixed", label_cn="混合", label_en="Mixed"),
)

_HOURLY_RATE_CHOICES: tuple[Choice, ...] = (
    Choice(value="<500", label_cn="<500/h", label_en="<500/h"),
    Choice(value="500-1500", label_cn="500-1500/h", label_en="500-1500/h"),
    Choice(value="1500-3000", label_cn="1500-3000/h", label_en="1500-3000/h"),
    Choice(value="3000+", label_cn="3000+/h", label_en="3000+/h"),
    Choice(value="na", label_cn="N/A", label_en="N/A"),
)

_DURATION_CHOICES: tuple[Choice, ...] = (
    Choice(value="<1m", label_cn="<1月", label_en="<1 month"),
    Choice(value="1-3m", label_cn="1-3月", label_en="1-3 months"),
    Choice(value="3-6m", label_cn="3-6月", label_en="3-6 months"),
    Choice(value="6m+", label_cn="6月+", label_en="6+ months"),
)

_MILESTONE_CHOICES: tuple[Choice, ...] = (
    Choice(value="single", label_cn="否,完成后一次", label_en="No, on completion"),
    Choice(value="2-3", label_cn="2-3阶段", label_en="2-3 phases"),
    Choice(value="4+", label_cn="4阶段以上", label_en="4+ phases"),
)


_QUESTIONS: tuple[OnboardingQuestion, ...] = (
    OnboardingQuestion(
        id="cs_billing_mode",
        question_cn="主要计费方式",
        question_en="Primary billing mode",
        profile_path="facts.consulting.billing_mode",
        qtype="single",
        tier=1,
        choices=_BILLING_CHOICES,
    ),
    OnboardingQuestion(
        id="cs_default_hourly_rate",
        question_cn="默认工时单价档位(本币)",
        question_en="Default hourly rate band (local currency)",
        profile_path="facts.consulting.default_hourly_rate",
        qtype="int_band",
        tier=1,
        choices=_HOURLY_RATE_CHOICES,
    ),
    OnboardingQuestion(
        id="cs_typical_duration",
        question_cn="项目典型周期",
        question_en="Typical project duration",
        profile_path="facts.consulting.typical_duration",
        qtype="single",
        tier=1,
        choices=_DURATION_CHOICES,
    ),
    OnboardingQuestion(
        id="cs_milestone_billing",
        question_cn="是否分阶段收款?",
        question_en="Milestone billing?",
        profile_path="facts.consulting.milestone_billing",
        qtype="single",
        tier=1,
        choices=_MILESTONE_CHOICES,
    ),
)


def _erpnext_init(answers: dict, profile: dict, erp) -> dict:  # noqa: ANN001
    billing = answers.get("cs_billing_mode") or "fixed"
    hourly = answers.get("cs_default_hourly_rate") or "na"
    milestone = answers.get("cs_milestone_billing") or "single"

    return {
        "scenario": "consulting",
        "project_type": [{"name": "Consulting", "kind": "ensure"}],
        "activity_type": [{"name": "Consulting Hours", "kind": "ensure"}] if billing in ("hourly", "milestone", "mixed") else [],
        "activity_cost": {"hourly_band": hourly} if hourly != "na" else None,
        "timesheet_default": billing in ("hourly", "milestone", "mixed"),
        "payment_schedule_template": milestone != "single",
        "explicitly_skip": ["Stock", "BOM", "Workstation"],
    }


SCENARIO = ScenarioPack(
    id="consulting",
    name_cn="咨询/方案",
    name_en="Consulting / professional services",
    parent_category="services",
    description_cn="卖时间和方案,按工时或里程碑收钱",
    questions=_QUESTIONS,
    profile_defaults={
        "operational_patterns.primary_workflow": "consulting",
    },
    erpnext_init_template=_erpnext_init,
)


__all__ = ["SCENARIO"]
