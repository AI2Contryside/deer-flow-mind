"""``import_export`` — 自营进出口贸易.

Self-financed import/export trader; holds inventory, books cost of goods,
margin-driven. Distinct from ``brokerage`` because the goods sit on the
balance sheet and ERPNext Stock module is fully on.
"""

from __future__ import annotations

from src.agents.tenant_onboarding.question_bank.types import (
    Choice,
    OnboardingQuestion,
    ScenarioPack,
)

_CURRENCY_CHOICES: tuple[Choice, ...] = tuple(
    Choice(value=c, label_cn=c, label_en=c)
    for c in ("USD", "EUR", "CNY", "JPY", "HKD", "GBP", "Other")
)

_LOGISTICS_CHOICES: tuple[Choice, ...] = (
    Choice(value="sea", label_cn="海运", label_en="Sea"),
    Choice(value="air", label_cn="空运", label_en="Air"),
    Choice(value="land", label_cn="陆运", label_en="Land"),
    Choice(value="express", label_cn="快递", label_en="Express courier"),
    Choice(value="mixed", label_cn="混合", label_en="Mixed"),
)

_TRACKING_CHOICES: tuple[Choice, ...] = (
    Choice(value="none", label_cn="都不需要", label_en="None"),
    Choice(value="batch", label_cn="仅批次", label_en="Batch only"),
    Choice(value="serial", label_cn="仅序列号", label_en="Serial only"),
    Choice(value="both", label_cn="两者都需要", label_en="Both"),
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

_LANDED_COST_CHOICES: tuple[Choice, ...] = (
    Choice(value="per_order", label_cn="是,每单算", label_en="Yes, per order"),
    Choice(value="monthly", label_cn="是,月度结", label_en="Yes, monthly"),
    Choice(value="none", label_cn="否", label_en="No"),
)


_QUESTIONS: tuple[OnboardingQuestion, ...] = (
    OnboardingQuestion(
        id="ie_selling_currencies",
        question_cn="主要销售币种(多选)",
        question_en="Primary selling currencies (multi)",
        profile_path="operational_patterns.selling_currencies",
        qtype="multi",
        tier=1,
        choices=_CURRENCY_CHOICES,
        max_select=5,
    ),
    OnboardingQuestion(
        id="ie_buying_currencies",
        question_cn="主要采购币种(多选)",
        question_en="Primary buying currencies (multi)",
        profile_path="operational_patterns.buying_currencies",
        qtype="multi",
        tier=1,
        choices=_CURRENCY_CHOICES,
        max_select=5,
    ),
    OnboardingQuestion(
        id="ie_logistics_modes",
        question_cn="主要物流方式(多选)",
        question_en="Primary logistics modes (multi)",
        profile_path="facts.import_export.logistics_modes",
        qtype="multi",
        tier=1,
        choices=_LOGISTICS_CHOICES,
        max_select=4,
    ),
    OnboardingQuestion(
        id="ie_tracking_level",
        question_cn="是否需要批次/序列号追踪?",
        question_en="Need batch / serial tracking?",
        profile_path="facts.import_export.tracking_level",
        qtype="single",
        tier=1,
        choices=_TRACKING_CHOICES,
    ),
    OnboardingQuestion(
        id="ie_commodity_categories",
        question_cn="主营品类大类(多选)",
        question_en="Main commodity categories (multi)",
        profile_path="facts.defaults.commodity_categories",
        qtype="multi",
        tier=1,
        choices=_COMMODITY_CHOICES,
        max_select=5,
    ),
    OnboardingQuestion(
        id="ie_uses_landed_cost",
        question_cn="是否需要落地成本分摊?",
        question_en="Need landed cost allocation?",
        profile_path="facts.flags.uses_landed_cost",
        qtype="single",
        tier=1,
        choices=_LANDED_COST_CHOICES,
    ),
)


def derive_tracking_flags(answers: dict) -> dict[str, bool]:
    """Map ie_tracking_level → has_batch / has_serial booleans."""
    level = answers.get("ie_tracking_level")
    return {
        "requires_batch_tracking": level in ("batch", "both"),
        "requires_serial_tracking": level in ("serial", "both"),
    }


def _erpnext_init(answers: dict, profile: dict, erp) -> dict:  # noqa: ANN001
    """Self-trader bootstrap — Stock module fully on, warehouse tree, LCV."""
    flags = derive_tracking_flags(answers)
    selling = answers.get("ie_selling_currencies") or []
    buying = answers.get("ie_buying_currencies") or []
    categories = answers.get("ie_commodity_categories") or []
    landed = answers.get("ie_uses_landed_cost") or "none"

    item_template = {
        "is_stock_item": 1,
        "has_batch_no": int(flags["requires_batch_tracking"]),
        "has_serial_no": int(flags["requires_serial_tracking"]),
    }
    warehouse_tree = [
        {"name": "Onshore - Main", "is_group": 0},
        {"name": "Bonded - Inbound", "is_group": 0},
    ]
    if "sea" in (answers.get("ie_logistics_modes") or []):
        warehouse_tree.append({"name": "Overseas - Transit", "is_group": 0})

    return {
        "scenario": "import_export",
        "item_template": item_template,
        "warehouse_tree": warehouse_tree,
        "price_lists": (
            [{"name": f"Standard Selling - {c}", "currency": c, "kind": "selling"} for c in selling]
            + [{"name": f"Standard Buying - {c}", "currency": c, "kind": "buying"} for c in buying]
        ),
        "landed_cost_voucher": landed != "none",
        "item_groups": [{"name": cat, "kind": "ensure"} for cat in categories],
        "explicitly_skip": ["BOM", "Workstation"],
    }


SCENARIO = ScenarioPack(
    id="import_export",
    name_cn="进出口自营",
    name_en="Self-financed import/export",
    parent_category="trade",
    description_cn="自有库存进出口贸易,吃毛利",
    questions=_QUESTIONS,
    profile_defaults={
        "operational_patterns.trade_mode": "self_trade",
    },
    erpnext_init_template=_erpnext_init,
)


__all__ = ["SCENARIO", "derive_tracking_flags"]
