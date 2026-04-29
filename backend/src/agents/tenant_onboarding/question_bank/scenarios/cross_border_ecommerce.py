"""``cross_border_ecommerce`` — 跨境电商 (platform sellers).

Amazon/Shopee/independent-store sellers; overseas warehouse common, return
rates non-trivial, platform sync cadence is a real concern.
"""

from __future__ import annotations

from src.agents.tenant_onboarding.question_bank.types import (
    Choice,
    OnboardingQuestion,
    ScenarioPack,
)

_PLATFORM_CHOICES: tuple[Choice, ...] = (
    Choice(value="amazon", label_cn="亚马逊", label_en="Amazon"),
    Choice(value="shopee", label_cn="Shopee", label_en="Shopee"),
    Choice(value="aliexpress", label_cn="速卖通", label_en="AliExpress"),
    Choice(value="ebay", label_cn="eBay", label_en="eBay"),
    Choice(value="independent", label_cn="独立站(Shopify/Magento)", label_en="Independent (Shopify/Magento)"),
    Choice(value="tiktok", label_cn="TikTok Shop", label_en="TikTok Shop"),
    Choice(value="other", label_cn="其他", label_en="Other"),
)

_OVERSEAS_WH_CHOICES: tuple[Choice, ...] = (
    Choice(value="none", label_cn="否,国内直邮", label_en="No, China direct"),
    Choice(value="self", label_cn="是,自营", label_en="Yes, self-operated"),
    Choice(value="third_party", label_cn="是,第三方(FBA/海外仓服务)", label_en="Yes, 3PL (FBA / overseas)"),
    Choice(value="mixed", label_cn="混合", label_en="Mixed"),
)

_RETURN_RATE_CHOICES: tuple[Choice, ...] = (
    Choice(value="<5", label_cn="<5%", label_en="<5%"),
    Choice(value="5-15", label_cn="5-15%", label_en="5-15%"),
    Choice(value="15+", label_cn="15%+", label_en="15%+"),
)

_COMMODITY_CHOICES: tuple[Choice, ...] = (
    Choice(value="apparel", label_cn="服装鞋帽", label_en="Apparel"),
    Choice(value="electronics", label_cn="电子电器", label_en="Electronics"),
    Choice(value="machinery", label_cn="机械设备", label_en="Machinery"),
    Choice(value="food_agri", label_cn="食品农产品", label_en="Food / Agri"),
    Choice(value="chemical", label_cn="化工", label_en="Chemicals"),
    Choice(value="home", label_cn="家居", label_en="Home goods"),
    Choice(value="auto_parts", label_cn="汽配", label_en="Auto parts"),
    Choice(value="other", label_cn="其他", label_en="Other"),
)

_SYNC_FREQ_CHOICES: tuple[Choice, ...] = (
    Choice(value="realtime", label_cn="实时", label_en="Real-time"),
    Choice(value="hourly", label_cn="每小时", label_en="Hourly"),
    Choice(value="daily", label_cn="每日", label_en="Daily"),
    Choice(value="manual", label_cn="手工导入", label_en="Manual import"),
)


_QUESTIONS: tuple[OnboardingQuestion, ...] = (
    OnboardingQuestion(
        id="ec_platforms",
        question_cn="主要销售平台(多选)",
        question_en="Primary sales platforms (multi)",
        profile_path="facts.cross_border_ecommerce.platforms",
        qtype="multi",
        tier=1,
        choices=_PLATFORM_CHOICES,
        max_select=6,
    ),
    OnboardingQuestion(
        id="ec_uses_overseas_warehouse",
        question_cn="是否使用海外仓?",
        question_en="Overseas warehouse?",
        profile_path="facts.flags.uses_overseas_warehouse",
        qtype="single",
        tier=1,
        choices=_OVERSEAS_WH_CHOICES,
    ),
    OnboardingQuestion(
        id="ec_return_rate_band",
        question_cn="退货率档位",
        question_en="Return rate band",
        profile_path="facts.cross_border_ecommerce.return_rate_band",
        qtype="single",
        tier=1,
        choices=_RETURN_RATE_CHOICES,
    ),
    OnboardingQuestion(
        id="ec_commodity_categories",
        question_cn="主营品类(多选)",
        question_en="Main commodity categories (multi)",
        profile_path="facts.defaults.commodity_categories",
        qtype="multi",
        tier=1,
        choices=_COMMODITY_CHOICES,
        max_select=5,
    ),
    OnboardingQuestion(
        id="ec_sync_frequency",
        question_cn="平台订单同步频率",
        question_en="Platform order sync frequency",
        profile_path="facts.cross_border_ecommerce.sync_frequency",
        qtype="single",
        tier=1,
        choices=_SYNC_FREQ_CHOICES,
    ),
)


def _erpnext_init(answers: dict, profile: dict, erp) -> dict:  # noqa: ANN001
    platforms = answers.get("ec_platforms") or []
    overseas = answers.get("ec_uses_overseas_warehouse") or "none"
    sync = answers.get("ec_sync_frequency") or "manual"
    categories = answers.get("ec_commodity_categories") or []

    warehouse_tree: list[dict] = [{"name": "Onshore - Main", "is_group": 0}]
    if overseas != "none":
        warehouse_tree.append({"name": "Overseas - Pool", "is_group": 1})

    return {
        "scenario": "cross_border_ecommerce",
        "warehouse_tree": warehouse_tree,
        "platform_customers": [{"name": p, "kind": "ensure"} for p in platforms],
        "return_pricing_rule": True,
        "scheduled_sync": {"frequency": sync, "enabled": sync != "manual"},
        "item_groups": [{"name": cat, "kind": "ensure"} for cat in categories],
        "explicitly_skip": ["LC handling", "Subcontracting Order"],
    }


SCENARIO = ScenarioPack(
    id="cross_border_ecommerce",
    name_cn="跨境电商",
    name_en="Cross-border e-commerce",
    parent_category="trade",
    description_cn="亚马逊/Shopee/独立站平台卖家,海外仓常见,退货高频",
    questions=_QUESTIONS,
    profile_defaults={
        "operational_patterns.trade_mode": "cross_border_ecommerce",
    },
    erpnext_init_template=_erpnext_init,
)


__all__ = ["SCENARIO"]
