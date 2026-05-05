"""Plugin-shaped question bank primitives.

A ``ScenarioPack`` is one self-contained business scenario — its question
set, profile defaults, and ERPNext init template all live in a single
module under ``scenarios/<id>.py``. The ``registry`` auto-discovers any
module that exposes a ``SCENARIO`` attribute, so adding a new scenario
means dropping one file and restarting.

Question types:
  - ``single``   — radio (one of ``choices``).
  - ``multi``    — checkbox (``max_select`` caps how many).
  - ``text``     — free text up to ~200 chars.
  - ``int_band`` — banded integer ranges modelled as ``choices``.

The dataclasses are ``frozen=True`` because the registry treats them as
shared singletons; mutating one in place would silently leak across
threads.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

QuestionType = Literal["single", "multi", "text", "int_band"]
ScenarioCategory = Literal[
    "trade",
    "manufacturing",
    "services",
    "retail",
    "project",
    "assets",
    "unknown",
]


@dataclass(frozen=True)
class Choice:
    """One selectable option for a ``single`` / ``multi`` / ``int_band`` question."""

    value: str
    label_cn: str
    label_en: str


@dataclass(frozen=True)
class OnboardingQuestion:
    """One question rendered to the user during onboarding.

    ``profile_path`` is the dotted destination inside the final profile
    dict (e.g. ``facts.brokerage.warehouse_passthrough``). Composer uses
    it to assemble a ``TenantProfile`` from collected ``answers`` without
    each scenario hard-coding its own write logic.
    """

    id: str
    question_cn: str
    question_en: str
    profile_path: str
    qtype: QuestionType = "single"
    tier: int = 1
    choices: tuple[Choice, ...] = ()
    max_select: int | None = None
    depends_on: Callable[[dict[str, Any]], bool] | None = field(default=None, compare=False)
    note: str | None = None
    # Backwards-compat: legacy code paths inspected ``options: tuple[str, ...]``
    # of plain strings. New code uses ``choices`` (with bilingual labels).
    # ``options`` is exposed as a derived view by ``__post_init__`` below
    # so the prompt renderer keeps working.

    @property
    def options(self) -> tuple[str, ...]:
        """Compatibility view: list of raw values for legacy renderers."""
        return tuple(c.value for c in self.choices)


@dataclass(frozen=True)
class ScenarioPack:
    """One business-scenario plugin.

    ``id`` is the ``detailed_scenarios[]`` value the meta question writes —
    keep it stable, downstream PG rows reference it.

    ``profile_defaults`` is a flat dotted-key map applied verbatim before
    answer-derived values are written, so each scenario can preset
    cross-cutting flags (e.g. ``operational_patterns.trade_mode``) without
    needing a question.

    ``erpnext_init_template`` is the per-scenario ERPNext bootstrap
    callable. It must be idempotent because multi-scenario tenants run
    every selected pack's init in turn.
    """

    id: str
    name_cn: str
    name_en: str
    parent_category: ScenarioCategory
    description_cn: str
    questions: tuple[OnboardingQuestion, ...]
    profile_defaults: dict[str, Any] = field(default_factory=dict)
    erpnext_init_template: Callable[..., Any] | None = field(default=None, compare=False)


__all__ = [
    "Choice",
    "OnboardingQuestion",
    "QuestionType",
    "ScenarioCategory",
    "ScenarioPack",
]
