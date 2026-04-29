"""``general`` — fallback scenario when the tenant can't classify themselves.

Keeps onboarding short (4 questions) and defers detail to the runtime
summarizer / a later setting-page top-up. Used when:
  - Q0 ``primary_categories`` includes only ``unknown``.
  - Q0 picked some categories but Q1 returned no detailed scenarios that fit.

ERPNext init for ``general`` is the bare minimum (Company + Fiscal Year +
default Item Group root) — see design §8.
"""

from __future__ import annotations

from src.agents.tenant_onboarding.question_bank.types import (
    Choice,
    OnboardingQuestion,
    ScenarioPack,
)

_PHYSICAL_GOODS_CHOICES: tuple[Choice, ...] = (
    Choice(value="hold_inventory", label_cn="是,持有库存", label_en="Yes, hold inventory"),
    Choice(value="passthrough", label_cn="是,过手不持有", label_en="Yes, passthrough only"),
    Choice(value="none", label_cn="否,不经手实物", label_en="No physical goods"),
)

_INVOICING_MODE_CHOICES: tuple[Choice, ...] = (
    Choice(value="b2b", label_cn="对公为主", label_en="B2B primarily"),
    Choice(value="b2c", label_cn="对私为主", label_en="B2C primarily"),
    Choice(value="none", label_cn="不开票", label_en="No invoicing"),
)

_MULTI_CURRENCY_CHOICES: tuple[Choice, ...] = (
    Choice(value="single", label_cn="单币种", label_en="Single currency"),
    Choice(value="2-3", label_cn="2-3 币种", label_en="2-3 currencies"),
    Choice(value="many", label_cn="多币种", label_en="Many currencies"),
)


_QUESTIONS: tuple[OnboardingQuestion, ...] = (
    OnboardingQuestion(
        id="gen_description",
        question_cn="用一两句话描述你的主要业务流程",
        question_en="Describe your main business in one or two sentences",
        profile_path="facts.general.description",
        qtype="text",
        tier=1,
        note="Free text up to ~200 chars.",
    ),
    OnboardingQuestion(
        id="gen_handles_physical_goods",
        question_cn="是否经手实物商品?",
        question_en="Do you handle physical goods?",
        profile_path="facts.general.handles_physical_goods",
        qtype="single",
        tier=1,
        choices=_PHYSICAL_GOODS_CHOICES,
    ),
    OnboardingQuestion(
        id="gen_invoicing_mode",
        question_cn="主要开票模式",
        question_en="Primary invoicing mode",
        profile_path="facts.general.invoicing_mode",
        qtype="single",
        tier=1,
        choices=_INVOICING_MODE_CHOICES,
    ),
    OnboardingQuestion(
        id="gen_multi_currency",
        question_cn="是否多币种结算?",
        question_en="Multi-currency settlement?",
        profile_path="facts.general.multi_currency",
        qtype="single",
        tier=1,
        choices=_MULTI_CURRENCY_CHOICES,
    ),
)


def _erpnext_init(answers: dict, profile: dict, erp) -> dict:  # noqa: ANN001
    """Minimal ERPNext bootstrap: Company + Fiscal Year + Item Group root.

    Implementation lives in the erpnext-cli skill — this stub returns a
    declarative shape that the caller (composer.run_scenario_inits) hands to
    the CLI. Idempotent: pre-existing records are skipped, never overwritten.
    """
    return {
        "scenario": "general",
        "doctypes": [
            {"doctype": "Company", "kind": "ensure"},
            {"doctype": "Fiscal Year", "kind": "ensure"},
            {"doctype": "Item Group", "name": "All Item Groups", "kind": "ensure"},
        ],
    }


SCENARIO = ScenarioPack(
    id="general",
    name_cn="通用/不确定",
    name_en="General / Unspecified",
    parent_category="unknown",
    description_cn="客户不确定具体业务形态时的兜底场景,只问最少必要信息,运行时由 summarizer 慢慢补",
    questions=_QUESTIONS,
    profile_defaults={},
    erpnext_init_template=_erpnext_init,
)


__all__ = ["SCENARIO"]
