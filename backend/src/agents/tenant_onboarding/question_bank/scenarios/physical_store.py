"""``physical_store`` — 实体连锁门店.

Brick-and-mortar store(s); POS, loyalty, inter-store transfers. The most
demanding setup in retail because every store is a Warehouse.
"""

from __future__ import annotations

from src.agents.tenant_onboarding.question_bank.types import (
    Choice,
    OnboardingQuestion,
    ScenarioPack,
)

_STORE_COUNT_CHOICES: tuple[Choice, ...] = (
    Choice(value="1", label_cn="单店", label_en="Single store"),
    Choice(value="2-5", label_cn="2-5", label_en="2-5"),
    Choice(value="5-20", label_cn="5-20", label_en="5-20"),
    Choice(value="20+", label_cn="20+", label_en="20+"),
)

_POS_CHOICES: tuple[Choice, ...] = (
    Choice(value="paper", label_cn="纸质单", label_en="Paper-only"),
    Choice(value="pos_system", label_cn="POS 系统(电子)", label_en="POS system (electronic)"),
    Choice(value="online_only", label_cn="纯电子(无线下)", label_en="Online-only (no offline)"),
)

_LOYALTY_CHOICES: tuple[Choice, ...] = (
    Choice(value="none", label_cn="否", label_en="No"),
    Choice(value="store_local", label_cn="是,门店级", label_en="Yes, per-store"),
    Choice(value="cross_store", label_cn="是,跨店通用", label_en="Yes, cross-store"),
)

_CATEGORY_CHOICES: tuple[Choice, ...] = (
    Choice(value="apparel", label_cn="服装", label_en="Apparel"),
    Choice(value="food", label_cn="食品", label_en="Food"),
    Choice(value="daily", label_cn="日用", label_en="Daily goods"),
    Choice(value="cosmetics", label_cn="化妆", label_en="Cosmetics"),
    Choice(value="digital", label_cn="数码", label_en="Digital / Electronics"),
    Choice(value="home", label_cn="家居", label_en="Home goods"),
    Choice(value="other", label_cn="其他", label_en="Other"),
)

_TRANSFER_CHOICES: tuple[Choice, ...] = (
    Choice(value="none", label_cn="否,各店独立", label_en="No, isolated"),
    Choice(value="occasional", label_cn="偶尔", label_en="Occasional"),
    Choice(value="daily", label_cn="日常", label_en="Daily"),
)


_QUESTIONS: tuple[OnboardingQuestion, ...] = (
    OnboardingQuestion(
        id="ps_store_count",
        question_cn="门店数量",
        question_en="Store count",
        profile_path="facts.physical_store.store_count",
        qtype="single",
        tier=1,
        choices=_STORE_COUNT_CHOICES,
    ),
    OnboardingQuestion(
        id="ps_uses_pos",
        question_cn="POS 收银方式",
        question_en="POS / cashier mode",
        profile_path="facts.physical_store.uses_pos",
        qtype="single",
        tier=1,
        choices=_POS_CHOICES,
    ),
    OnboardingQuestion(
        id="ps_uses_loyalty",
        question_cn="会员/积分系统",
        question_en="Loyalty / member system",
        profile_path="facts.physical_store.uses_loyalty",
        qtype="single",
        tier=1,
        choices=_LOYALTY_CHOICES,
    ),
    OnboardingQuestion(
        id="ps_categories",
        question_cn="主营品类(多选)",
        question_en="Main categories (multi)",
        profile_path="facts.defaults.commodity_categories",
        qtype="multi",
        tier=1,
        choices=_CATEGORY_CHOICES,
        max_select=4,
    ),
    OnboardingQuestion(
        id="ps_inter_store_transfers",
        question_cn="多门店调拨频率",
        question_en="Inter-store transfer frequency",
        profile_path="facts.physical_store.inter_store_transfers",
        qtype="single",
        tier=2,
        choices=_TRANSFER_CHOICES,
        depends_on=lambda answers: answers.get("ps_store_count") not in (None, "1"),
    ),
)


def _erpnext_init(answers: dict, profile: dict, erp) -> dict:  # noqa: ANN001
    count = answers.get("ps_store_count") or "1"
    pos = answers.get("ps_uses_pos") or "pos_system"
    loyalty = answers.get("ps_uses_loyalty") or "none"
    transfers = answers.get("ps_inter_store_transfers") or "none"

    pos_count_band = {"1": 1, "2-5": 3, "5-20": 10, "20+": 25}[count]

    return {
        "scenario": "physical_store",
        "pos_profiles_band": pos_count_band if pos != "paper" else 0,
        "loyalty_program": loyalty != "none",
        "loyalty_cross_store": loyalty == "cross_store",
        "warehouse_per_store": True,
        "stock_entry_template": "Material Transfer" if transfers != "none" else None,
        "explicitly_skip": ["Multi-currency masters"],
    }


SCENARIO = ScenarioPack(
    id="physical_store",
    name_cn="实体连锁",
    name_en="Physical retail chain",
    parent_category="retail",
    description_cn="门店收银,会员积分",
    questions=_QUESTIONS,
    profile_defaults={
        "operational_patterns.primary_workflow": "retail_physical",
    },
    erpnext_init_template=_erpnext_init,
)


__all__ = ["SCENARIO"]
