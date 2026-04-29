"""Plugin-shaped question bank.

Public API:
    - ``OnboardingQuestion`` / ``Choice`` / ``ScenarioPack`` (types)
    - ``build_question_plan(answers)`` (registry-driven plan)
    - ``META_QUESTIONS`` / ``COMMON_QUESTIONS`` (static groups)
    - ``get_registry()`` / ``selected_scenarios()`` / ``scenarios_by_category()``
    - ``REQUIRED_QUESTIONS`` / ``required_unanswered()`` — legacy compat shims
      that translate to the meta+common+plan flow. New code should call
      ``build_question_plan(answers)`` directly.
"""

from __future__ import annotations

from typing import Any

from src.agents.tenant_onboarding.question_bank.common import COMMON_QUESTIONS
from src.agents.tenant_onboarding.question_bank.meta import META_QUESTIONS
from src.agents.tenant_onboarding.question_bank.registry import (
    GENERAL_SCENARIO_ID,
    all_questions_for_lookup,
    build_question_plan,
    get_registry,
    reset_registry_for_tests,
    scenarios_by_category,
    selected_scenarios,
)
from src.agents.tenant_onboarding.question_bank.types import (
    Choice,
    OnboardingQuestion,
    QuestionType,
    ScenarioCategory,
    ScenarioPack,
)


def _build_required_compat() -> tuple[OnboardingQuestion, ...]:
    """Legacy ``REQUIRED_QUESTIONS`` view: meta + common in declaration order.

    The pre-v3 prompt rendered a single static list. Scenario-specific
    questions are now per-pack and only surface once Q0/Q1 are answered,
    so the legacy list deliberately stops at common. Callers that want the
    full plan should switch to ``build_question_plan``.
    """
    return tuple(META_QUESTIONS) + tuple(COMMON_QUESTIONS)


REQUIRED_QUESTIONS: tuple[OnboardingQuestion, ...] = _build_required_compat()


def required_unanswered(answers: dict[str, Any]) -> list[OnboardingQuestion]:
    """Compat shim — delegates to ``build_question_plan``."""
    return build_question_plan(answers)


__all__ = [
    "COMMON_QUESTIONS",
    "Choice",
    "GENERAL_SCENARIO_ID",
    "META_QUESTIONS",
    "OnboardingQuestion",
    "QuestionType",
    "REQUIRED_QUESTIONS",
    "ScenarioCategory",
    "ScenarioPack",
    "all_questions_for_lookup",
    "build_question_plan",
    "get_registry",
    "required_unanswered",
    "reset_registry_for_tests",
    "scenarios_by_category",
    "selected_scenarios",
]
