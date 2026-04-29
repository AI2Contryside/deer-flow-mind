"""``online_dtc`` — 独立站 DTC.

Direct-to-consumer via own storefront / mini-program / WeChat. Distinct
from cross_border_ecommerce in that it's domestic-first and the customer
relationship is more durable (subscriptions, loyalty).
"""

from __future__ import annotations

from src.agents.tenant_onboarding.question_bank.types import (
    Choice,
    OnboardingQuestion,
    ScenarioPack,
)

_CHANNEL_CHOICES: tuple[Choice, ...] = (
    Choice(value="independent", label_cn="独立站(Shopify/Magento)", label_en="Independent (Shopify/Magento)"),
    Choice(value="mini_mall", label_cn="微商城", label_en="WeChat mini-mall"),
    Choice(value="mini_program", label_cn="小程序", label_en="WeChat mini-program"),
    Choice(value="wechat_group", label_cn="微信群", label_en="WeChat group"),
    Choice(value="other", label_cn="其他", label_en="Other"),
)

_OVERSEAS_WH_CHOICES: tuple[Choice, ...] = (
    Choice(value="none", label_cn="否,国内发", label_en="No, ship from China"),
    Choice(value="self", label_cn="是,自营", label_en="Yes, self-operated"),
    Choice(value="third_party", label_cn="是,第三方", label_en="Yes, 3PL"),
    Choice(value="mixed", label_cn="混合", label_en="Mixed"),
)

_SUBSCRIPTION_CHOICES: tuple[Choice, ...] = (
    Choice(value="none", label_cn="否", label_en="No"),
    Choice(value="some_skus", label_cn="是,部分 SKU", label_en="Yes, some SKUs"),
    Choice(value="all", label_cn="是,全订阅", label_en="Yes, all"),
)

_PAYMENT_CHANNEL_CHOICES: tuple[Choice, ...] = (
    Choice(value="alipay", label_cn="支付宝", label_en="Alipay"),
    Choice(value="wechat_pay", label_cn="微信支付", label_en="WeChat Pay"),
    Choice(value="stripe", label_cn="Stripe", label_en="Stripe"),
    Choice(value="paypal", label_cn="PayPal", label_en="PayPal"),
    Choice(value="bank", label_cn="银行", label_en="Bank transfer"),
    Choice(value="other", label_cn="其他", label_en="Other"),
)


_QUESTIONS: tuple[OnboardingQuestion, ...] = (
    OnboardingQuestion(
        id="dtc_channels",
        question_cn="销售渠道(多选)",
        question_en="Sales channels (multi)",
        profile_path="facts.online_dtc.channels",
        qtype="multi",
        tier=1,
        choices=_CHANNEL_CHOICES,
        max_select=5,
    ),
    OnboardingQuestion(
        id="dtc_uses_overseas_warehouse",
        question_cn="是否海外仓?",
        question_en="Overseas warehouse?",
        profile_path="facts.flags.uses_overseas_warehouse",
        qtype="single",
        tier=1,
        choices=_OVERSEAS_WH_CHOICES,
    ),
    OnboardingQuestion(
        id="dtc_uses_subscription",
        question_cn="是否订阅商品?",
        question_en="Subscription SKUs?",
        profile_path="facts.online_dtc.uses_subscription",
        qtype="single",
        tier=1,
        choices=_SUBSCRIPTION_CHOICES,
    ),
    OnboardingQuestion(
        id="dtc_payment_channels",
        question_cn="主要支付渠道(多选)",
        question_en="Primary payment channels (multi)",
        profile_path="facts.online_dtc.payment_channels",
        qtype="multi",
        tier=1,
        choices=_PAYMENT_CHANNEL_CHOICES,
        max_select=5,
    ),
)


def _erpnext_init(answers: dict, profile: dict, erp) -> dict:  # noqa: ANN001
    overseas = answers.get("dtc_uses_overseas_warehouse") or "none"
    subscription = answers.get("dtc_uses_subscription") or "none"
    payment_channels = answers.get("dtc_payment_channels") or []

    return {
        "scenario": "online_dtc",
        "modes_of_payment": [{"name": ch, "kind": "ensure"} for ch in payment_channels],
        "subscription_plan": subscription != "none",
        "warehouse_tree": (
            [{"name": "Onshore - Main", "is_group": 0}]
            + ([{"name": "Overseas - Pool", "is_group": 1}] if overseas != "none" else [])
        ),
        "explicitly_skip": ["LC", "BOM"],
    }


SCENARIO = ScenarioPack(
    id="online_dtc",
    name_cn="独立站/小程序 DTC",
    name_en="Online DTC (own storefront / mini-program)",
    parent_category="retail",
    description_cn="微商城/小程序/独立站直接卖给消费者",
    questions=_QUESTIONS,
    profile_defaults={
        "operational_patterns.primary_workflow": "online_dtc",
    },
    erpnext_init_template=_erpnext_init,
)


__all__ = ["SCENARIO"]
