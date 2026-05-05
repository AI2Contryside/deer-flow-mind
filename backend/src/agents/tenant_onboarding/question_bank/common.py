"""Cross-scenario common questions — every tenant answers all 5.

These cover identity (country / currency / fiscal year) and shape
(legal-entity structure, monthly volume band) that every downstream
ERPNext init template needs regardless of business model.

UoM / default warehouse / default price list — which the legacy v1 bank
asked here — moved into stock-bearing scenario packs (import_export,
branded_oem, physical_store, …) per design §6.2: no point asking a
consulting tenant for a stock UoM.
"""

from __future__ import annotations

from src.agents.tenant_onboarding.question_bank.types import (
    Choice,
    OnboardingQuestion,
)

_COUNTRY_CHOICES: tuple[Choice, ...] = (
    Choice(value="China", label_cn="中国", label_en="China"),
    Choice(value="United States", label_cn="美国", label_en="United States"),
    Choice(value="Germany", label_cn="德国", label_en="Germany"),
    Choice(value="Japan", label_cn="日本", label_en="Japan"),
    Choice(value="United Kingdom", label_cn="英国", label_en="United Kingdom"),
    Choice(value="Singapore", label_cn="新加坡", label_en="Singapore"),
    Choice(value="Hong Kong", label_cn="中国香港", label_en="Hong Kong"),
    Choice(value="Other", label_cn="其他", label_en="Other"),
)

_CURRENCY_CHOICES: tuple[Choice, ...] = (Choice(value=c, label_cn=c, label_en=c) for c in ("CNY", "USD", "EUR", "JPY", "HKD", "GBP", "Other"))
_CURRENCY_CHOICES = tuple(_CURRENCY_CHOICES)

_FISCAL_CHOICES: tuple[Choice, ...] = (
    Choice(value="1", label_cn="1 月", label_en="January"),
    Choice(value="4", label_cn="4 月", label_en="April"),
    Choice(value="7", label_cn="7 月", label_en="July"),
    Choice(value="10", label_cn="10 月", label_en="October"),
)

_MULTI_COMPANY_CHOICES: tuple[Choice, ...] = (
    Choice(value="single", label_cn="否,单一公司", label_en="No, single entity"),
    Choice(value="onshore_offshore", label_cn="是,离岸+在岸", label_en="Yes, onshore + offshore"),
    Choice(value="multi_onshore", label_cn="是,多在岸", label_en="Yes, multiple onshore"),
    Choice(value="other", label_cn="是,其他结构", label_en="Yes, other structure"),
)

_VOLUME_BAND_CHOICES: tuple[Choice, ...] = (
    Choice(value="<10", label_cn="<10 单/月", label_en="<10 / month"),
    Choice(value="10-100", label_cn="10-100 单/月", label_en="10-100 / month"),
    Choice(value="100-500", label_cn="100-500 单/月", label_en="100-500 / month"),
    Choice(value="500+", label_cn="500+ 单/月", label_en="500+ / month"),
)


COMPANY_COUNTRY = OnboardingQuestion(
    id="company_country",
    question_cn="公司主要经营所在国家?",
    question_en="Primary country of operation?",
    profile_path="facts.company.country",
    qtype="single",
    tier=1,
    choices=_COUNTRY_CHOICES,
)

COMPANY_CURRENCY = OnboardingQuestion(
    id="company_currency",
    question_cn="记账本位币(公司本币)?",
    question_en="Company reporting currency?",
    profile_path="facts.company.currency",
    qtype="single",
    tier=1,
    choices=_CURRENCY_CHOICES,
)

FISCAL_YEAR_START = OnboardingQuestion(
    id="fiscal_year_start",
    question_cn="会计年度起始月?",
    question_en="Fiscal year start month?",
    profile_path="facts.company.fiscal_year_start_month",
    qtype="single",
    tier=1,
    choices=_FISCAL_CHOICES,
)

MULTI_COMPANY = OnboardingQuestion(
    id="multi_company",
    question_cn="是否多法人主体?",
    question_en="Multiple legal entities?",
    profile_path="facts.flags.multi_company_structure",
    qtype="single",
    tier=1,
    choices=_MULTI_COMPANY_CHOICES,
)

MONTHLY_VOLUME_BAND = OnboardingQuestion(
    id="monthly_volume_band",
    question_cn="预期月业务单量级?",
    question_en="Expected monthly transaction volume?",
    profile_path="operational_patterns.monthly_volume_band",
    qtype="single",
    tier=1,
    choices=_VOLUME_BAND_CHOICES,
)


COMMON_QUESTIONS: tuple[OnboardingQuestion, ...] = (
    COMPANY_COUNTRY,
    COMPANY_CURRENCY,
    FISCAL_YEAR_START,
    MULTI_COMPANY,
    MONTHLY_VOLUME_BAND,
)


__all__ = [
    "COMMON_QUESTIONS",
    "COMPANY_COUNTRY",
    "COMPANY_CURRENCY",
    "FISCAL_YEAR_START",
    "MONTHLY_VOLUME_BAND",
    "MULTI_COMPANY",
]
