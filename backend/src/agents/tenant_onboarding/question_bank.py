"""Required question bank for onboarding v1.

The subagent walks this list during the Q&A phase, asking via
``ask_clarification`` for items the user has not answered yet (either
implicitly through uploaded Excel or explicitly in chat).

Tier == 1 questions are mandatory: the subagent must not finish onboarding
until each is answered. Tier == 2 questions surface only when the answer
to a tier-1 question reveals they apply (``depends_on`` predicate).

These questions are intentionally small in number — every additional
mandatory question is friction at first contact, and the runtime summarizer
will fill gaps over time. The list is meant to be the smallest set that
makes a useful first-pass ``profile.json``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass(frozen=True)
class OnboardingQuestion:
    """One required item in the onboarding question bank."""

    id: str
    question_cn: str
    question_en: str
    profile_path: str  # dot-path into the profile dict, e.g. "facts.company.currency"
    tier: int = 1
    options: tuple[str, ...] = ()
    depends_on: Callable[[dict], bool] | None = field(default=None, compare=False)
    note: str | None = None


REQUIRED_QUESTIONS: tuple[OnboardingQuestion, ...] = (
    OnboardingQuestion(
        id="company_name",
        question_cn="公司全称是什么?(将作为 ERPNext 的 Company 名称)",
        question_en="What is the legal company name? (used as the ERPNext Company)",
        profile_path="facts.company.name",
    ),
    OnboardingQuestion(
        id="company_country",
        question_cn="公司主要经营所在国家?",
        question_en="Primary country of operation?",
        profile_path="facts.company.country",
        options=("China", "United States", "Germany", "United Kingdom", "Japan", "Other"),
    ),
    OnboardingQuestion(
        id="company_currency",
        question_cn="记账本位币?",
        question_en="Default reporting currency?",
        profile_path="facts.company.currency",
        options=("CNY", "USD", "EUR", "JPY", "HKD", "Other"),
    ),
    OnboardingQuestion(
        id="fiscal_year_start",
        question_cn="会计年度从几月开始?",
        question_en="Fiscal year start month?",
        profile_path="facts.company.fiscal_year_start_month",
        options=("1", "4", "7", "10"),
    ),
    OnboardingQuestion(
        id="business_type",
        question_cn="主要业务模式是什么?",
        question_en="Primary business model?",
        profile_path="operational_patterns.primary_workflow",
        options=(
            "Trading (买卖型外贸)",
            "Manufacturing (生产型出口)",
            "Service (服务/技术出口)",
            "Mixed (混合)",
        ),
    ),
    OnboardingQuestion(
        id="default_uom",
        question_cn="主要使用的库存计量单位?",
        question_en="Primary stock UoM?",
        profile_path="facts.defaults.stock_uom",
        options=("Nos", "Box", "Carton", "Kg", "Meter", "Set", "Pair", "Other"),
    ),
    OnboardingQuestion(
        id="default_warehouse",
        question_cn="给默认仓库起一个名字(将创建为 'Stores - <ABBR>' 的子仓)",
        question_en="Default warehouse leaf name (e.g. 'Stores')",
        profile_path="facts.defaults.default_warehouse_leaf",
        note="Defaults to 'Stores' if the user has no preference.",
    ),
    OnboardingQuestion(
        id="default_price_list",
        question_cn="默认销售价目表名称?",
        question_en="Default selling price list name?",
        profile_path="facts.defaults.selling_price_list",
        note="Defaults to 'Standard Selling' if the user has no preference.",
    ),
    # Tier-2: only when manufacturing is in scope
    OnboardingQuestion(
        id="has_subcontracting",
        question_cn="是否涉及委外加工(供应商加工后回收)?",
        question_en="Do you use subcontracting (sending raw materials to suppliers)?",
        profile_path="facts.flags.uses_subcontracting",
        tier=2,
        options=("Yes", "No"),
        depends_on=lambda answers: "Manufacturing" in (answers.get("business_type") or ""),
    ),
)


def required_unanswered(answers: dict) -> list[OnboardingQuestion]:
    """Return the tier-1 questions that have no answer yet.

    Tier-2 questions are appended only when their ``depends_on`` predicate
    is satisfied by the current answers.
    """
    pending: list[OnboardingQuestion] = []
    for q in REQUIRED_QUESTIONS:
        if q.id in answers and answers[q.id] not in (None, ""):
            continue
        if q.tier == 1:
            pending.append(q)
            continue
        if q.depends_on is None:
            continue
        try:
            if q.depends_on(answers):
                pending.append(q)
        except Exception:  # noqa: BLE001 — predicate must never break onboarding
            pending.append(q)
    return pending


__all__ = [
    "OnboardingQuestion",
    "REQUIRED_QUESTIONS",
    "required_unanswered",
]
