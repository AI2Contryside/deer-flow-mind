"""Auto-discovery + question-plan assembler for the scenario plugins.

Adding a new scenario is one file under ``scenarios/<id>.py`` exporting
``SCENARIO = ScenarioPack(...)`` — no edits to this module.

``build_question_plan(answers)`` is the single entry point the lead-agent
prompt and any future settings-page wizard call. It returns the next
ordered batch of questions to ask, given the answers collected so far.
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from collections.abc import Iterable
from threading import Lock
from typing import Any

from src.agents.tenant_onboarding.question_bank import scenarios as _scenarios_pkg
from src.agents.tenant_onboarding.question_bank.common import COMMON_QUESTIONS
from src.agents.tenant_onboarding.question_bank.meta import (
    DETAILED_SCENARIOS,
    META_QUESTIONS,
    PRIMARY_CATEGORIES,
)
from src.agents.tenant_onboarding.question_bank.types import (
    OnboardingQuestion,
    ScenarioPack,
)

logger = logging.getLogger(__name__)

GENERAL_SCENARIO_ID = "general"

_registry_lock = Lock()
_SCENARIO_REGISTRY: dict[str, ScenarioPack] | None = None


def _discover_scenarios() -> dict[str, ScenarioPack]:
    """Scan ``scenarios/`` for modules exposing a ``SCENARIO`` attribute."""
    out: dict[str, ScenarioPack] = {}
    for mod_info in pkgutil.iter_modules(_scenarios_pkg.__path__):
        full_name = f"{_scenarios_pkg.__name__}.{mod_info.name}"
        try:
            mod = importlib.import_module(full_name)
        except Exception as exc:  # noqa: BLE001 — one bad scenario must not break the rest
            logger.warning("scenario %s failed to import: %s", full_name, exc)
            continue
        pack = getattr(mod, "SCENARIO", None)
        if not isinstance(pack, ScenarioPack):
            continue
        if pack.id in out:
            raise RuntimeError(f"duplicate scenario id: {pack.id} (in {full_name})")
        out[pack.id] = pack
    return out


def get_registry() -> dict[str, ScenarioPack]:
    """Return the lazy-initialized ``{scenario_id: ScenarioPack}`` map."""
    global _SCENARIO_REGISTRY
    if _SCENARIO_REGISTRY is None:
        with _registry_lock:
            if _SCENARIO_REGISTRY is None:
                _SCENARIO_REGISTRY = _discover_scenarios()
    return _SCENARIO_REGISTRY


def reset_registry_for_tests() -> None:
    """Clear the cached registry so tests can re-discover after monkey-patching."""
    global _SCENARIO_REGISTRY
    with _registry_lock:
        _SCENARIO_REGISTRY = None


def scenarios_by_category(category: str) -> tuple[ScenarioPack, ...]:
    """All registered packs whose ``parent_category`` matches."""
    return tuple(p for p in get_registry().values() if p.parent_category == category)


def _has_answer(answers: dict[str, Any], qid: str) -> bool:
    val = answers.get(qid)
    if val is None:
        return False
    if isinstance(val, str):
        return val.strip() != ""
    if isinstance(val, list | tuple):
        return len(val) > 0
    return True


def _depends_on_holds(question: OnboardingQuestion, answers: dict[str, Any]) -> bool:
    if question.depends_on is None:
        return True
    try:
        return bool(question.depends_on(answers))
    except Exception as exc:  # noqa: BLE001 — predicate must not break the plan
        logger.warning("depends_on for %s raised: %s — treating as True", question.id, exc)
        return True


def _selected_scenario_ids(answers: dict[str, Any]) -> list[str]:
    """Resolve which scenario packs apply, given the meta answers.

    Routing rules (per design §4.1):
      - ``primary_categories`` empty → no scenarios yet (caller still in meta phase).
      - ``primary_categories == ['unknown']`` (or includes only unknown) →
        ``general`` fallback.
      - Otherwise → whatever ``detailed_scenarios`` lists, intersected with the
        registered packs whose ``parent_category`` matches one of the picks.
    """
    primary = [c for c in (answers.get("primary_categories") or []) if c]
    if not primary:
        return []
    if all(c == "unknown" for c in primary):
        return [GENERAL_SCENARIO_ID] if GENERAL_SCENARIO_ID in get_registry() else []

    # Until ``detailed_scenarios`` is answered, do NOT preload any pack —
    # the user would otherwise see scenario-specific questions before they
    # narrowed down which scenario applies. The plan stays in meta/common
    # phase for now.
    if "detailed_scenarios" not in answers:
        return []

    detailed = [s for s in (answers.get("detailed_scenarios") or []) if s]
    registry = get_registry()
    valid_categories = {c for c in primary if c != "unknown"}
    out: list[str] = []
    for sid in detailed:
        pack = registry.get(sid)
        if pack is None:
            logger.warning("detailed scenario %r not in registry — skipping", sid)
            continue
        if pack.parent_category not in valid_categories:
            logger.warning(
                "detailed scenario %r has category %r not in selected categories %r — skipping",
                sid,
                pack.parent_category,
                sorted(valid_categories),
            )
            continue
        if sid not in out:
            out.append(sid)
    if not out and GENERAL_SCENARIO_ID in registry:
        # User answered detailed_scenarios but nothing valid mapped — fall back.
        out.append(GENERAL_SCENARIO_ID)
    return out


def _expand_detailed_choices(answers: dict[str, Any]) -> tuple[OnboardingQuestion, ...]:
    """Build the dynamic Q1 (detailed_scenarios) by listing packs in the
    selected categories. Returns a singleton tuple — empty if Q1 should be
    skipped (e.g. only ``unknown`` was picked in Q0).
    """
    if not _depends_on_holds(DETAILED_SCENARIOS, answers):
        return ()
    primary = [c for c in (answers.get("primary_categories") or []) if c != "unknown"]
    if not primary:
        return ()
    from src.agents.tenant_onboarding.question_bank.types import Choice

    choices: list[Choice] = []
    for category in primary:
        for pack in scenarios_by_category(category):
            if pack.id == GENERAL_SCENARIO_ID:
                continue
            choices.append(
                Choice(
                    value=pack.id,
                    label_cn=f"[{category}] {pack.name_cn}",
                    label_en=f"[{category}] {pack.name_en}",
                )
            )
    if not choices:
        return ()
    # Rebuild the question with populated dynamic choices.
    return (
        OnboardingQuestion(
            id=DETAILED_SCENARIOS.id,
            question_cn=DETAILED_SCENARIOS.question_cn,
            question_en=DETAILED_SCENARIOS.question_en,
            profile_path=DETAILED_SCENARIOS.profile_path,
            qtype=DETAILED_SCENARIOS.qtype,
            tier=DETAILED_SCENARIOS.tier,
            choices=tuple(choices),
            max_select=DETAILED_SCENARIOS.max_select,
            depends_on=DETAILED_SCENARIOS.depends_on,
            note=DETAILED_SCENARIOS.note,
        ),
    )


def _dedupe_by_profile_path(
    questions: Iterable[OnboardingQuestion],
) -> list[OnboardingQuestion]:
    """Cross-scenario dedupe: same ``profile_path`` is asked once.

    Multiple scenario packs may include semantically identical questions
    (e.g. selling currencies). The first occurrence wins so order remains
    deterministic — registration order = scenario-pack iteration order.
    """
    seen_paths: set[str] = set()
    seen_ids: set[str] = set()
    out: list[OnboardingQuestion] = []
    for q in questions:
        if q.id in seen_ids:
            continue
        if q.profile_path and q.profile_path in seen_paths:
            seen_ids.add(q.id)
            continue
        seen_ids.add(q.id)
        if q.profile_path:
            seen_paths.add(q.profile_path)
        out.append(q)
    return out


def build_question_plan(answers: dict[str, Any]) -> list[OnboardingQuestion]:
    """Assemble the next-question plan given the user's answers so far.

    Order:
      1. Meta (Q0 → Q1) until both answered. Q1 is dynamically populated
         from the registry and skipped if depends_on is False.
      2. Common (5 questions) — every tenant answers all.
      3. Each selected scenario pack's questions, in scenario-pack order,
         deduped by ``profile_path``. Tier-2 questions only surface when
         ``depends_on`` holds.

    Already-answered questions are filtered out, so callers can re-invoke
    this function after each answer to get the remaining queue.
    """
    plan: list[OnboardingQuestion] = []

    if not _has_answer(answers, PRIMARY_CATEGORIES.id):
        plan.append(PRIMARY_CATEGORIES)

    detailed_q = _expand_detailed_choices(answers)
    if detailed_q and not _has_answer(answers, DETAILED_SCENARIOS.id):
        plan.extend(detailed_q)

    for q in COMMON_QUESTIONS:
        if _has_answer(answers, q.id):
            continue
        if not _depends_on_holds(q, answers):
            continue
        plan.append(q)

    selected = _selected_scenario_ids(answers)
    registry = get_registry()
    scenario_questions: list[OnboardingQuestion] = []
    for sid in selected:
        pack = registry.get(sid)
        if pack is None:
            continue
        for q in pack.questions:
            if not _depends_on_holds(q, answers):
                continue
            scenario_questions.append(q)

    for q in _dedupe_by_profile_path(scenario_questions):
        if _has_answer(answers, q.id):
            continue
        plan.append(q)

    return plan


def selected_scenarios(answers: dict[str, Any]) -> list[ScenarioPack]:
    """Return the resolved ScenarioPack objects in plan order."""
    registry = get_registry()
    return [registry[sid] for sid in _selected_scenario_ids(answers) if sid in registry]


def all_questions_for_lookup() -> dict[str, OnboardingQuestion]:
    """Flat ``{question_id: OnboardingQuestion}`` map for composer lookup."""
    out: dict[str, OnboardingQuestion] = {q.id: q for q in META_QUESTIONS}
    for q in COMMON_QUESTIONS:
        out[q.id] = q
    for pack in get_registry().values():
        for q in pack.questions:
            out.setdefault(q.id, q)
    return out


__all__ = [
    "GENERAL_SCENARIO_ID",
    "all_questions_for_lookup",
    "build_question_plan",
    "get_registry",
    "reset_registry_for_tests",
    "scenarios_by_category",
    "selected_scenarios",
]
