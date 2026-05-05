"""``brokerage`` — 外贸撮合 (no-inventory broker).

Customer ↔ supplier matchmaking, drop-ship, earn margin / commission.
ERPNext init creates non-stock Item templates, multi-currency Price Lists,
optional Sales Partner masters when the user runs commission agents.
"""

from __future__ import annotations

from src.agents.tenant_onboarding.question_bank.types import (
    Choice,
    OnboardingQuestion,
    ScenarioPack,
)

_PASSTHROUGH_CHOICES: tuple[Choice, ...] = (
    Choice(value="never", label_cn="从不,直发客户", label_en="Never, drop ship"),
    Choice(value="occasional", label_cn="偶尔过手", label_en="Occasional"),
    Choice(value="frequent", label_cn="经常过手", label_en="Frequent"),
)

_CURRENCY_CHOICES: tuple[Choice, ...] = tuple(Choice(value=c, label_cn=c, label_en=c) for c in ("USD", "EUR", "CNY", "JPY", "HKD", "GBP", "Other"))

_INCOTERM_CHOICES: tuple[Choice, ...] = tuple(Choice(value=t, label_cn=t, label_en=t) for t in ("FOB", "CIF", "EXW", "DDP", "DAP", "Other"))

_SETTLEMENT_CHOICES: tuple[Choice, ...] = tuple(Choice(value=v, label_cn=v, label_en=v) for v in ("TT", "LC", "OA", "DP", "DA", "Factoring"))

_SALES_PARTNER_CHOICES: tuple[Choice, ...] = (
    Choice(value="none", label_cn="否,无代理", label_en="No"),
    Choice(value="internal", label_cn="是,内部销售员", label_en="Yes, internal salespeople"),
    Choice(value="external", label_cn="是,外部代理", label_en="Yes, external agents"),
)


_QUESTIONS: tuple[OnboardingQuestion, ...] = (
    OnboardingQuestion(
        id="brokerage_warehouse_passthrough",
        question_cn="货物是否经过你的中转仓?",
        question_en="Do goods pass through your transit warehouse?",
        profile_path="facts.brokerage.warehouse_passthrough",
        qtype="single",
        tier=1,
        choices=_PASSTHROUGH_CHOICES,
    ),
    OnboardingQuestion(
        id="brokerage_selling_currencies",
        question_cn="主要销售币种(多选)",
        question_en="Primary selling currencies (multi)",
        profile_path="operational_patterns.selling_currencies",
        qtype="multi",
        tier=1,
        choices=_CURRENCY_CHOICES,
        max_select=5,
    ),
    OnboardingQuestion(
        id="brokerage_buying_currencies",
        question_cn="主要采购币种(多选)",
        question_en="Primary buying currencies (multi)",
        profile_path="operational_patterns.buying_currencies",
        qtype="multi",
        tier=1,
        choices=_CURRENCY_CHOICES,
        max_select=5,
    ),
    OnboardingQuestion(
        id="brokerage_default_incoterm",
        question_cn="默认 Incoterm?",
        question_en="Default Incoterm?",
        profile_path="operational_patterns.default_incoterm",
        qtype="single",
        tier=1,
        choices=_INCOTERM_CHOICES,
    ),
    OnboardingQuestion(
        id="brokerage_settlement_tools",
        question_cn="主要结算工具(多选)",
        question_en="Primary settlement tools (multi)",
        profile_path="facts.brokerage.settlement_tools",
        qtype="multi",
        tier=1,
        choices=_SETTLEMENT_CHOICES,
        max_select=5,
    ),
    OnboardingQuestion(
        id="brokerage_uses_sales_partners",
        question_cn="是否使用佣金代理?",
        question_en="Do you use commission agents?",
        profile_path="facts.brokerage.uses_sales_partners",
        qtype="single",
        tier=1,
        choices=_SALES_PARTNER_CHOICES,
    ),
)


def _trade_mode_for(passthrough_value: str | None) -> str:
    """Derive operational_patterns.trade_mode from the passthrough answer."""
    if passthrough_value == "never":
        return "drop_ship"
    if passthrough_value in ("occasional", "frequent"):
        return "mixed"
    return "broker"


def _erpnext_init(answers: dict, profile: dict, erp) -> dict:  # noqa: ANN001
    """Brokerage bootstrap — non-stock items + per-currency Price Lists."""
    selling_currencies = answers.get("brokerage_selling_currencies") or []
    buying_currencies = answers.get("brokerage_buying_currencies") or []
    settlement = answers.get("brokerage_settlement_tools") or []
    uses_partners = answers.get("brokerage_uses_sales_partners") or "none"

    price_lists = []
    for cur in selling_currencies:
        price_lists.append({"name": f"Standard Selling - {cur}", "currency": cur, "kind": "selling"})
    for cur in buying_currencies:
        price_lists.append({"name": f"Standard Buying - {cur}", "currency": cur, "kind": "buying"})

    custom_fields: list[dict] = []
    if "LC" in settlement:
        custom_fields.extend(
            [
                {"dt": "Sales Invoice", "fieldname": "lc_number", "label": "LC Number", "fieldtype": "Data"},
                {"dt": "Purchase Invoice", "fieldname": "lc_number", "label": "LC Number", "fieldtype": "Data"},
                {"dt": "Sales Invoice", "fieldname": "bl_number", "label": "B/L Number", "fieldtype": "Data"},
            ]
        )

    return {
        "scenario": "brokerage",
        "item_template": {"is_stock_item": 0, "item_group": "All Item Groups"},
        "price_lists": price_lists,
        "custom_fields": custom_fields,
        "sales_partner_seed": uses_partners != "none",
        "explicitly_skip": ["Workstation", "BOM", "Asset"],
    }


SCENARIO = ScenarioPack(
    id="brokerage",
    name_cn="外贸撮合(无库存)",
    name_en="Trade brokerage (no inventory)",
    parent_category="trade",
    description_cn="客户↔供应商撮合,Drop Ship 直发,赚差价/佣金",
    questions=_QUESTIONS,
    profile_defaults={
        "operational_patterns.trade_mode": "drop_ship",
        "facts.flags.uses_landed_cost": False,
    },
    erpnext_init_template=_erpnext_init,
)


# Re-exported so composer.derive_trade_mode can map answers without importing
# the function under a private name.
def derive_trade_mode(answers: dict) -> str:
    return _trade_mode_for(answers.get("brokerage_warehouse_passthrough"))


__all__ = ["SCENARIO", "derive_trade_mode"]
