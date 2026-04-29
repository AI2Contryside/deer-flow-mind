"""``trade_services`` — 报关/货代/物流服务.

Sells trade-related services (booking, customs, warehousing). May advance
duties on behalf of customers — that needs a reimbursable account split.
"""

from __future__ import annotations

from src.agents.tenant_onboarding.question_bank.types import (
    Choice,
    OnboardingQuestion,
    ScenarioPack,
)

_SERVICE_TYPE_CHOICES: tuple[Choice, ...] = (
    Choice(value="ocean_booking", label_cn="海运订舱", label_en="Ocean booking"),
    Choice(value="air_booking", label_cn="空运订舱", label_en="Air booking"),
    Choice(value="customs", label_cn="报关", label_en="Customs clearance"),
    Choice(value="trucking", label_cn="陆运", label_en="Trucking"),
    Choice(value="warehousing", label_cn="仓储", label_en="Warehousing"),
    Choice(value="insurance", label_cn="保险", label_en="Insurance"),
    Choice(value="other", label_cn="其他", label_en="Other"),
)

_TRACKING_DOCS_CHOICES: tuple[Choice, ...] = (
    Choice(value="all", label_cn="是,所有", label_en="Yes, all"),
    Choice(value="some", label_cn="是,部分", label_en="Yes, some"),
    Choice(value="none", label_cn="否", label_en="No"),
)

_BILLING_BASIS_CHOICES: tuple[Choice, ...] = (
    Choice(value="per_shipment", label_cn="按票", label_en="Per shipment"),
    Choice(value="per_kg", label_cn="按重量(KG)", label_en="Per kg"),
    Choice(value="per_cbm", label_cn="按体积(CBM)", label_en="Per cubic meter"),
    Choice(value="per_hour", label_cn="按工时", label_en="Per hour"),
    Choice(value="mixed", label_cn="混合", label_en="Mixed"),
)

_ADVANCE_DUTIES_CHOICES: tuple[Choice, ...] = (
    Choice(value="none", label_cn="否", label_en="No"),
    Choice(value="reimburse", label_cn="是,等客户报销", label_en="Yes, reimbursed by customer"),
    Choice(value="settle", label_cn="是,统一结算", label_en="Yes, batch settled"),
)


_QUESTIONS: tuple[OnboardingQuestion, ...] = (
    OnboardingQuestion(
        id="ts_service_types",
        question_cn="主要服务类型(多选)",
        question_en="Primary service types (multi)",
        profile_path="facts.trade_services.service_types",
        qtype="multi",
        tier=1,
        choices=_SERVICE_TYPE_CHOICES,
        max_select=6,
    ),
    OnboardingQuestion(
        id="ts_tracks_documents",
        question_cn="是否追踪运单/提单号?",
        question_en="Track waybill / B/L numbers?",
        profile_path="facts.trade_services.tracks_documents",
        qtype="single",
        tier=1,
        choices=_TRACKING_DOCS_CHOICES,
    ),
    OnboardingQuestion(
        id="ts_billing_basis",
        question_cn="计费基础",
        question_en="Billing basis",
        profile_path="facts.trade_services.billing_basis",
        qtype="single",
        tier=1,
        choices=_BILLING_BASIS_CHOICES,
    ),
    OnboardingQuestion(
        id="ts_advances_duties",
        question_cn="是否代垫海关税费?",
        question_en="Advance customs duties?",
        profile_path="facts.trade_services.advances_duties",
        qtype="single",
        tier=1,
        choices=_ADVANCE_DUTIES_CHOICES,
    ),
)


def _erpnext_init(answers: dict, profile: dict, erp) -> dict:  # noqa: ANN001
    services = answers.get("ts_service_types") or []
    tracks = answers.get("ts_tracks_documents") or "none"
    advances = answers.get("ts_advances_duties") or "none"

    custom_fields: list[dict] = []
    if tracks != "none":
        custom_fields.extend(
            [
                {"dt": "Sales Invoice", "fieldname": "bl_no", "label": "B/L No.", "fieldtype": "Data"},
                {"dt": "Sales Invoice", "fieldname": "awb_no", "label": "AWB No.", "fieldtype": "Data"},
            ]
        )

    return {
        "scenario": "trade_services",
        "service_items": [
            {"name": svc, "is_stock_item": 0, "kind": "ensure"} for svc in services
        ],
        "custom_fields": custom_fields,
        "reimbursable_account": advances != "none",
        "shipment_doctype": True,
        "explicitly_skip": ["BOM", "Workstation"],
    }


SCENARIO = ScenarioPack(
    id="trade_services",
    name_cn="报关/货代/物流服务",
    name_en="Trade services (customs / freight forwarding / logistics)",
    parent_category="services",
    description_cn="卖物流相关服务,可能代垫税费",
    questions=_QUESTIONS,
    profile_defaults={
        "operational_patterns.primary_workflow": "trade_services",
    },
    erpnext_init_template=_erpnext_init,
)


__all__ = ["SCENARIO"]
