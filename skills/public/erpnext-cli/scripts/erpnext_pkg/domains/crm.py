"""CRM domain: lead → opportunity → customer + quotation flows."""

from __future__ import annotations

from ..core.client import FrappeClient
from ._common import apply_overrides, normalize_items, submit_if_draft


def create_lead(
    client: FrappeClient,
    lead_name: str,
    *,
    company_name: str | None = None,
    email_id: str | None = None,
    mobile_no: str | None = None,
    source: str | None = None,
    status: str = "Lead",
    extra: dict | None = None,
) -> dict:
    doc = {
        "doctype": "Lead",
        "lead_name": lead_name,
        "status": status,
    }
    if company_name: doc["company_name"] = company_name
    if email_id: doc["email_id"] = email_id
    if mobile_no: doc["mobile_no"] = mobile_no
    if source: doc["source"] = source
    if extra: doc.update(extra)
    return client.insert(doc)


def convert_lead_to_opportunity(
    client: FrappeClient,
    lead: str,
    *,
    overrides: dict | None = None,
) -> dict:
    """Wraps ``erpnext.crm.doctype.lead.lead.make_opportunity``."""
    target = client.call_method(
        "erpnext.crm.doctype.lead.lead.make_opportunity",
        source_name=lead,
    )
    target = apply_overrides(target, overrides)
    return client.insert(target)


def convert_opportunity_to_quotation(
    client: FrappeClient,
    opportunity: str,
    *,
    items: list | None = None,
    overrides: dict | None = None,
) -> dict:
    """Wraps ``erpnext.crm.doctype.opportunity.opportunity.make_quotation``.

    If the opportunity has no items yet, pass them via ``items``.
    """
    submit_if_draft(client, "Opportunity", opportunity)
    target = client.call_method(
        "erpnext.crm.doctype.opportunity.opportunity.make_quotation",
        source_name=opportunity,
    )
    if items:
        target["items"] = normalize_items(items)
    target = apply_overrides(target, overrides)
    return client.insert(target)


def convert_lead_to_customer(
    client: FrappeClient,
    lead: str,
    *,
    customer_group: str = "All Customer Groups",
    territory: str = "All Territories",
    customer_type: str = "Company",
    overrides: dict | None = None,
) -> dict:
    """Wraps ``erpnext.crm.doctype.lead.lead.make_customer``."""
    target = client.call_method(
        "erpnext.crm.doctype.lead.lead.make_customer",
        source_name=lead,
    )
    target.setdefault("customer_group", customer_group)
    target.setdefault("territory", territory)
    target.setdefault("customer_type", customer_type)
    target = apply_overrides(target, overrides)
    return client.insert(target)


def lead_to_quotation(
    client: FrappeClient,
    lead_name: str,
    items: list,
    *,
    email_id: str | None = None,
    mobile_no: str | None = None,
    company_name: str | None = None,
) -> dict:
    """End-to-end: Lead → Opportunity → Quotation (draft)."""
    lead = create_lead(
        client, lead_name,
        email_id=email_id, mobile_no=mobile_no, company_name=company_name,
    )
    opp = convert_lead_to_opportunity(client, lead["name"])
    q = convert_opportunity_to_quotation(client, opp["name"], items=items)
    return {
        "workflow": "lead_to_quotation",
        "lead": lead["name"],
        "opportunity": opp["name"],
        "quotation": q["name"],
    }
