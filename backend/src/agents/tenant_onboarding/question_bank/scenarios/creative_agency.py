"""``creative_agency`` — 创意/广告/咨询项目.

Short-to-medium duration projects, frequent freelancer outsourcing,
billed by hour or by project. Like consulting but more outsourced.
"""

from __future__ import annotations

from src.agents.tenant_onboarding.question_bank.types import (
    Choice,
    OnboardingQuestion,
    ScenarioPack,
)

_PROJECT_TYPE_CHOICES: tuple[Choice, ...] = (
    Choice(value="design", label_cn="设计", label_en="Design"),
    Choice(value="advertising", label_cn="广告投放", label_en="Advertising"),
    Choice(value="branding", label_cn="品牌策划", label_en="Branding"),
    Choice(value="event", label_cn="活动执行", label_en="Event execution"),
    Choice(value="content", label_cn="内容生产", label_en="Content production"),
    Choice(value="other", label_cn="其他", label_en="Other"),
)

_FREELANCER_CHOICES: tuple[Choice, ...] = (
    Choice(value="none", label_cn="否", label_en="No"),
    Choice(value="occasional", label_cn="偶尔", label_en="Occasionally"),
    Choice(value="regular", label_cn="常态", label_en="Regularly"),
)

_BILLING_CHOICES: tuple[Choice, ...] = (
    Choice(value="fixed", label_cn="项目固定", label_en="Fixed project"),
    Choice(value="hourly", label_cn="按工时", label_en="Hourly"),
    Choice(value="outcome", label_cn="按成果", label_en="By outcome"),
    Choice(value="mixed", label_cn="混合", label_en="Mixed"),
)

_TIMESHEET_CHOICES: tuple[Choice, ...] = (
    Choice(value="none", label_cn="否", label_en="No"),
    Choice(value="internal", label_cn="是,内部 KPI", label_en="Yes, internal KPI"),
    Choice(value="customer", label_cn="是,客户结算用", label_en="Yes, for customer billing"),
)


_QUESTIONS: tuple[OnboardingQuestion, ...] = (
    OnboardingQuestion(
        id="ca_project_types",
        question_cn="主要项目类型(多选)",
        question_en="Primary project types (multi)",
        profile_path="facts.creative_agency.project_types",
        qtype="multi",
        tier=1,
        choices=_PROJECT_TYPE_CHOICES,
        max_select=5,
    ),
    OnboardingQuestion(
        id="ca_uses_freelancers",
        question_cn="是否用外包/自由职业者?",
        question_en="Use freelancers / outsourcing?",
        profile_path="facts.creative_agency.uses_freelancers",
        qtype="single",
        tier=1,
        choices=_FREELANCER_CHOICES,
    ),
    OnboardingQuestion(
        id="ca_billing_mode",
        question_cn="计费方式",
        question_en="Billing mode",
        profile_path="facts.creative_agency.billing_mode",
        qtype="single",
        tier=1,
        choices=_BILLING_CHOICES,
    ),
    OnboardingQuestion(
        id="ca_tracks_timesheet",
        question_cn="是否追踪团队工时?",
        question_en="Track team timesheets?",
        profile_path="facts.creative_agency.tracks_timesheet",
        qtype="single",
        tier=1,
        choices=_TIMESHEET_CHOICES,
    ),
)


def _erpnext_init(answers: dict, profile: dict, erp) -> dict:  # noqa: ANN001
    project_types = answers.get("ca_project_types") or []
    freelancers = answers.get("ca_uses_freelancers") or "none"
    billing = answers.get("ca_billing_mode") or "fixed"
    timesheet = answers.get("ca_tracks_timesheet") or "none"

    return {
        "scenario": "creative_agency",
        "project_type": [{"name": pt, "kind": "ensure"} for pt in project_types],
        "activity_type": [{"name": "Project Hours", "kind": "ensure"}] if billing in ("hourly", "mixed") else [],
        "timesheet": timesheet != "none",
        "freelancer_supplier_group": freelancers != "none",
        "explicitly_skip": ["Stock", "Manufacturing"],
    }


SCENARIO = ScenarioPack(
    id="creative_agency",
    name_cn="创意/广告/咨询项目",
    name_en="Creative agency / project shop",
    parent_category="project",
    description_cn="短到中周期项目,频繁外包",
    questions=_QUESTIONS,
    profile_defaults={
        "operational_patterns.primary_workflow": "creative_agency",
    },
    erpnext_init_template=_erpnext_init,
)


__all__ = ["SCENARIO"]
