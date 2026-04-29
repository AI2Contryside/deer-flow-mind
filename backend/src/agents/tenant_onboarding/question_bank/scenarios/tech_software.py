"""``tech_software`` — 软件/技术服务.

SaaS / License / project-delivery shop. Subscription billing + usage-based
billing both possible; License renewal tracking sometimes needed.
"""

from __future__ import annotations

from src.agents.tenant_onboarding.question_bank.types import (
    Choice,
    OnboardingQuestion,
    ScenarioPack,
)

_DELIVERY_CHOICES: tuple[Choice, ...] = (
    Choice(value="saas", label_cn="SaaS订阅", label_en="SaaS subscription"),
    Choice(value="license", label_cn="License卖断", label_en="License (perpetual)"),
    Choice(value="project", label_cn="项目交付", label_en="Project delivery"),
    Choice(value="mixed", label_cn="混合", label_en="Mixed"),
)

_PERIOD_CHOICES: tuple[Choice, ...] = (
    Choice(value="monthly", label_cn="月", label_en="Monthly"),
    Choice(value="quarterly", label_cn="季", label_en="Quarterly"),
    Choice(value="yearly", label_cn="年", label_en="Yearly"),
    Choice(value="multi_year", label_cn="多年", label_en="Multi-year"),
    Choice(value="na", label_cn="N/A", label_en="N/A"),
)

_USAGE_CHOICES: tuple[Choice, ...] = (
    Choice(value="none", label_cn="否", label_en="No"),
    Choice(value="partial", label_cn="是,部分", label_en="Yes, partial"),
    Choice(value="primary", label_cn="是,主要", label_en="Yes, primary"),
)

_LICENSE_TRACKING_CHOICES: tuple[Choice, ...] = (
    Choice(value="none", label_cn="否", label_en="No"),
    Choice(value="basic", label_cn="是,简单", label_en="Yes, basic"),
    Choice(value="renewal", label_cn="是,带过期提醒", label_en="Yes, with renewal alerts"),
)


_QUESTIONS: tuple[OnboardingQuestion, ...] = (
    OnboardingQuestion(
        id="tsw_delivery_mode",
        question_cn="主要交付模式",
        question_en="Primary delivery mode",
        profile_path="facts.tech_software.delivery_mode",
        qtype="single",
        tier=1,
        choices=_DELIVERY_CHOICES,
    ),
    OnboardingQuestion(
        id="tsw_subscription_period",
        question_cn="订阅周期(若 SaaS)",
        question_en="Subscription period (if SaaS)",
        profile_path="facts.tech_software.subscription_period",
        qtype="single",
        tier=2,
        choices=_PERIOD_CHOICES,
        depends_on=lambda answers: (answers.get("tsw_delivery_mode") or "") in ("saas", "mixed"),
    ),
    OnboardingQuestion(
        id="tsw_usage_based",
        question_cn="是否按量计费(API/存储)?",
        question_en="Usage-based billing?",
        profile_path="facts.tech_software.usage_based",
        qtype="single",
        tier=1,
        choices=_USAGE_CHOICES,
    ),
    OnboardingQuestion(
        id="tsw_license_tracking",
        question_cn="License/续费追踪?",
        question_en="License / renewal tracking?",
        profile_path="facts.tech_software.license_tracking",
        qtype="single",
        tier=1,
        choices=_LICENSE_TRACKING_CHOICES,
    ),
)


def _erpnext_init(answers: dict, profile: dict, erp) -> dict:  # noqa: ANN001
    delivery = answers.get("tsw_delivery_mode") or "project"
    sub_period = answers.get("tsw_subscription_period") or "na"
    usage = answers.get("tsw_usage_based") or "none"
    lic = answers.get("tsw_license_tracking") or "none"

    return {
        "scenario": "tech_software",
        "subscription_plan": delivery in ("saas", "mixed"),
        "subscription_period": sub_period if sub_period != "na" else None,
        "usage_billing_item": usage != "none",
        "license_renewal_notification": lic == "renewal",
        "explicitly_skip": ["Stock", "Manufacturing"],
    }


SCENARIO = ScenarioPack(
    id="tech_software",
    name_cn="软件/技术服务",
    name_en="Software / tech services",
    parent_category="services",
    description_cn="SaaS / License / 项目交付",
    questions=_QUESTIONS,
    profile_defaults={
        "operational_patterns.primary_workflow": "tech_software",
    },
    erpnext_init_template=_erpnext_init,
)


__all__ = ["SCENARIO"]
