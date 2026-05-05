"""``domestic_wholesale`` — 国内分销/批发.

Multi-tier domestic distribution; payment terms heavy, salesperson
commission, periodic rebates. Multi-currency is rare; LC is irrelevant.
"""

from __future__ import annotations

from src.agents.tenant_onboarding.question_bank.types import (
    Choice,
    OnboardingQuestion,
    ScenarioPack,
)

_TIERED_PRICING_CHOICES: tuple[Choice, ...] = (
    Choice(value="single", label_cn="否,统一价", label_en="No, single price"),
    Choice(value="two_tier", label_cn="2级(批发/零售)", label_en="2-tier (wholesale/retail)"),
    Choice(value="multi_tier", label_cn="3级以上", label_en="3+ tiers"),
)

_CUSTOMER_TYPE_CHOICES: tuple[Choice, ...] = (
    Choice(value="distributor", label_cn="经销商", label_en="Distributor"),
    Choice(value="sub_wholesale", label_cn="二级批发", label_en="Sub-wholesaler"),
    Choice(value="retail_store", label_cn="零售门店", label_en="Retail store"),
    Choice(value="ecommerce", label_cn="电商", label_en="E-commerce"),
    Choice(value="end_consumer", label_cn="直接消费者", label_en="End consumer"),
)

_PAYMENT_TERMS_CHOICES: tuple[Choice, ...] = (
    Choice(value="cash", label_cn="现款", label_en="Cash"),
    Choice(value="net30", label_cn="30天", label_en="Net 30"),
    Choice(value="net60", label_cn="60天", label_en="Net 60"),
    Choice(value="net90", label_cn="90天", label_en="Net 90"),
    Choice(value="net120plus", label_cn="120天+", label_en="Net 120+"),
)

_REBATE_CHOICES: tuple[Choice, ...] = (
    Choice(value="none", label_cn="否", label_en="No"),
    Choice(value="quarterly", label_cn="季度", label_en="Quarterly"),
    Choice(value="annual", label_cn="年度", label_en="Annual"),
    Choice(value="quarterly_annual", label_cn="季度+年度", label_en="Quarterly + Annual"),
)

_COMMISSION_CHOICES: tuple[Choice, ...] = (
    Choice(value="none", label_cn="无", label_en="None"),
    Choice(value="per_order", label_cn="按订单", label_en="Per order"),
    Choice(value="per_collection", label_cn="按回款", label_en="Per collection"),
    Choice(value="mixed", label_cn="混合", label_en="Mixed"),
)


_QUESTIONS: tuple[OnboardingQuestion, ...] = (
    OnboardingQuestion(
        id="dw_tiered_pricing",
        question_cn="是否分级价?",
        question_en="Tiered pricing?",
        profile_path="facts.domestic_wholesale.tiered_pricing",
        qtype="single",
        tier=1,
        choices=_TIERED_PRICING_CHOICES,
    ),
    OnboardingQuestion(
        id="dw_customer_types",
        question_cn="主要客户类型(多选)",
        question_en="Primary customer types (multi)",
        profile_path="facts.domestic_wholesale.customer_types",
        qtype="multi",
        tier=1,
        choices=_CUSTOMER_TYPE_CHOICES,
        max_select=5,
    ),
    OnboardingQuestion(
        id="dw_payment_terms",
        question_cn="主要账期(多选)",
        question_en="Primary payment terms (multi)",
        profile_path="operational_patterns.payment_terms_in_use",
        qtype="multi",
        tier=1,
        choices=_PAYMENT_TERMS_CHOICES,
        max_select=5,
    ),
    OnboardingQuestion(
        id="dw_uses_rebates",
        question_cn="是否返利/年终奖励?",
        question_en="Rebates / year-end rewards?",
        profile_path="facts.domestic_wholesale.rebate_cycle",
        qtype="single",
        tier=1,
        choices=_REBATE_CHOICES,
    ),
    OnboardingQuestion(
        id="dw_salesperson_commission",
        question_cn="业务员佣金方式?",
        question_en="Salesperson commission scheme?",
        profile_path="facts.domestic_wholesale.commission_basis",
        qtype="single",
        tier=1,
        choices=_COMMISSION_CHOICES,
    ),
)


def _erpnext_init(answers: dict, profile: dict, erp) -> dict:  # noqa: ANN001
    tiered = answers.get("dw_tiered_pricing") or "single"
    customer_types = answers.get("dw_customer_types") or []
    rebate = answers.get("dw_uses_rebates") or "none"
    commission = answers.get("dw_salesperson_commission") or "none"

    price_lists: list[dict] = [{"name": "Standard Selling", "kind": "selling"}]
    if tiered == "two_tier":
        price_lists.extend(
            [
                {"name": "Wholesale", "kind": "selling"},
                {"name": "Retail", "kind": "selling"},
            ]
        )
    elif tiered == "multi_tier":
        price_lists.extend([{"name": f"Tier-{i}", "kind": "selling"} for i in range(1, 4)])

    return {
        "scenario": "domestic_wholesale",
        "price_lists": price_lists,
        "customer_groups": [{"name": ct, "kind": "ensure"} for ct in customer_types],
        "payment_terms_template": True,
        "sales_person_tree": commission != "none",
        "pricing_rule_rebate": rebate != "none",
        "explicitly_skip": ["Subscription", "POS Profile"],
    }


SCENARIO = ScenarioPack(
    id="domestic_wholesale",
    name_cn="国内分销/批发",
    name_en="Domestic wholesale / distribution",
    parent_category="trade",
    description_cn="境内多级分销,赊销为主",
    questions=_QUESTIONS,
    profile_defaults={
        "operational_patterns.trade_mode": "domestic_wholesale",
    },
    erpnext_init_template=_erpnext_init,
)


__all__ = ["SCENARIO"]
