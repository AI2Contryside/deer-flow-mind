"""Meta questions (Q0 + Q1) — scenario routing.

Q0 (``primary_categories``) is asked first and decides which detailed
scenarios are even visible in Q1. Q1 (``detailed_scenarios``) is the
multi-select that picks the actual scenario packs to load.

Selecting ``unknown`` in Q0 short-circuits to the ``general`` fallback
pack and skips Q1 entirely (handled by ``registry.build_question_plan``).
"""

from __future__ import annotations

from src.agents.tenant_onboarding.question_bank.types import (
    Choice,
    OnboardingQuestion,
)

PRIMARY_CATEGORY_CHOICES: tuple[Choice, ...] = (
    Choice(value="trade", label_cn="贸易/分销", label_en="Trade / Distribution"),
    Choice(value="manufacturing", label_cn="制造/生产", label_en="Manufacturing"),
    Choice(value="services", label_cn="服务/咨询", label_en="Services / Consulting"),
    Choice(value="retail", label_cn="零售/电商", label_en="Retail / E-commerce"),
    Choice(value="project", label_cn="项目/工程", label_en="Project / Engineering"),
    Choice(value="assets", label_cn="资产/租赁", label_en="Assets / Rental"),
    Choice(value="unknown", label_cn="不确定", label_en="Not sure"),
)


PRIMARY_CATEGORIES = OnboardingQuestion(
    id="primary_categories",
    question_cn="主营业务大类是?(可多选,1-3 个)",
    question_en="Primary business categories? (multi-select, 1-3)",
    profile_path="scenarios[].category",
    qtype="multi",
    tier=1,
    choices=PRIMARY_CATEGORY_CHOICES,
    max_select=3,
)

DETAILED_SCENARIOS = OnboardingQuestion(
    id="detailed_scenarios",
    question_cn="在你选的大类下,具体属于哪种?(每个大类最多选 2 个)",
    question_en="Within each selected category, which detailed scenarios? (max 2 per category)",
    profile_path="scenarios[].scenarios",
    qtype="multi",
    tier=1,
    choices=(),  # Choices are dynamic — populated by registry from selected categories.
    max_select=4,
    # Skip Q1 entirely when the user picked ``unknown`` — registry routes them
    # to the ``general`` fallback pack so they aren't forced through a list of
    # categories they already said they couldn't classify.
    depends_on=lambda answers: bool(
        [c for c in (answers.get("primary_categories") or []) if c != "unknown"]
    ),
)


META_QUESTIONS: tuple[OnboardingQuestion, ...] = (
    PRIMARY_CATEGORIES,
    DETAILED_SCENARIOS,
)


__all__ = [
    "DETAILED_SCENARIOS",
    "META_QUESTIONS",
    "PRIMARY_CATEGORIES",
    "PRIMARY_CATEGORY_CHOICES",
]
