"""Selling domain: quote-to-cash business flows.

Every function here represents a **business process** — a multi-step flow
that an ERPNext user would execute via the web UI's "Create → X" buttons.
The wrappers chain ERPNext's whitelisted ``make_*`` builders so agents
can treat the whole flow as one atomic CLI call.

Flow summary
------------

::

  create_quotation ─────┐
                        ├──► convert_quotation_to_sales_order
  create_sales_order ───┴──► create_delivery_from_order (make_delivery_note)
                             └────► create_invoice_from_delivery  (make_sales_invoice)
                                    └────► collect_payment_for_invoice (get_payment_entry)

``order_to_cash`` runs the whole chain end-to-end. Use the smaller
building blocks when you need to stop at a specific step (e.g., ship now,
invoice later).
"""

from __future__ import annotations

from typing import Any

from ..core.client import FrappeClient
from ._common import (
    apply_overrides,
    ensure_customer,
    ensure_item,
    fetch_target,
    normalize_items,
    submit_if_draft,
)


# ── Primitives ────────────────────────────────────────────────────────

def create_quotation(
    client: FrappeClient,
    customer: str,
    items: list,
    *,
    company: str | None = None,
    currency: str | None = None,
    transaction_date: str | None = None,
    valid_till: str | None = None,
    quotation_to: str = "Customer",
    submit: bool = False,
    extra: dict | None = None,
) -> dict:
    """Create a Quotation for ``customer`` with ``items``.

    Maps to the GUI: *Selling → Quotation → New*.
    """
    doc = {
        "doctype": "Quotation",
        "quotation_to": quotation_to,
        "party_name": customer,
        "customer": customer,
        "items": normalize_items(items),
    }
    if company: doc["company"] = company
    if currency: doc["currency"] = currency
    if transaction_date: doc["transaction_date"] = transaction_date
    if valid_till: doc["valid_till"] = valid_till
    if extra: doc.update(extra)
    created = client.insert(doc)
    if submit:
        client.submit("Quotation", created["name"])
        created = client.get_doc("Quotation", created["name"])
    return created


def create_sales_order(
    client: FrappeClient,
    customer: str,
    items: list,
    *,
    company: str | None = None,
    delivery_date: str | None = None,
    currency: str | None = None,
    transaction_date: str | None = None,
    submit: bool = True,
    extra: dict | None = None,
) -> dict:
    """Create a Sales Order. Submits by default — an unsubmitted SO can't
    be delivered or invoiced."""
    doc = {
        "doctype": "Sales Order",
        "customer": customer,
        "items": normalize_items(items),
    }
    if company: doc["company"] = company
    if delivery_date: doc["delivery_date"] = delivery_date
    if currency: doc["currency"] = currency
    if transaction_date: doc["transaction_date"] = transaction_date
    if extra: doc.update(extra)
    created = client.insert(doc)
    if submit:
        client.submit("Sales Order", created["name"])
        created = client.get_doc("Sales Order", created["name"])
    return created


# ── Chain builders ────────────────────────────────────────────────────

def convert_quotation_to_sales_order(
    client: FrappeClient,
    quotation: str,
    *,
    submit: bool = True,
    overrides: dict | None = None,
) -> dict:
    """Promote a submitted Quotation to a Sales Order.

    Calls ``erpnext.selling.doctype.quotation.quotation.make_sales_order``
    — the same method the GUI's *Create → Sales Order* button uses.
    """
    submit_if_draft(client, "Quotation", quotation)
    target = client.call_method(
        "erpnext.selling.doctype.quotation.quotation.make_sales_order",
        source_name=quotation,
    )
    target = apply_overrides(target, overrides)
    so = client.insert(target)
    if submit:
        client.submit("Sales Order", so["name"])
        so = client.get_doc("Sales Order", so["name"])
    return so


def create_delivery_from_order(
    client: FrappeClient,
    sales_order: str,
    *,
    submit: bool = True,
    overrides: dict | None = None,
) -> dict:
    """Generate a Delivery Note from a submitted Sales Order.

    Wraps ``erpnext.selling.doctype.sales_order.sales_order.make_delivery_note``.
    """
    submit_if_draft(client, "Sales Order", sales_order)
    target = client.call_method(
        "erpnext.selling.doctype.sales_order.sales_order.make_delivery_note",
        source_name=sales_order,
    )
    target = apply_overrides(target, overrides)
    dn = client.insert(target)
    if submit:
        client.submit("Delivery Note", dn["name"])
        dn = client.get_doc("Delivery Note", dn["name"])
    return dn


def create_invoice_from_order(
    client: FrappeClient,
    sales_order: str,
    *,
    submit: bool = True,
    overrides: dict | None = None,
) -> dict:
    """Generate a Sales Invoice directly from a Sales Order.

    Wraps ``erpnext.selling.doctype.sales_order.sales_order.make_sales_invoice``.
    """
    submit_if_draft(client, "Sales Order", sales_order)
    target = client.call_method(
        "erpnext.selling.doctype.sales_order.sales_order.make_sales_invoice",
        source_name=sales_order,
    )
    target = apply_overrides(target, overrides)
    si = client.insert(target)
    if submit:
        client.submit("Sales Invoice", si["name"])
        si = client.get_doc("Sales Invoice", si["name"])
    return si


def create_invoice_from_delivery(
    client: FrappeClient,
    delivery_note: str,
    *,
    submit: bool = True,
    overrides: dict | None = None,
) -> dict:
    """Generate a Sales Invoice from a submitted Delivery Note.

    Wraps ``erpnext.stock.doctype.delivery_note.delivery_note.make_sales_invoice``.
    """
    submit_if_draft(client, "Delivery Note", delivery_note)
    target = client.call_method(
        "erpnext.stock.doctype.delivery_note.delivery_note.make_sales_invoice",
        source_name=delivery_note,
    )
    target = apply_overrides(target, overrides)
    si = client.insert(target)
    if submit:
        client.submit("Sales Invoice", si["name"])
        si = client.get_doc("Sales Invoice", si["name"])
    return si


def collect_payment_for_invoice(
    client: FrappeClient,
    sales_invoice: str,
    *,
    mode_of_payment: str | None = None,
    paid_amount: float | None = None,
    reference_no: str | None = None,
    reference_date: str | None = None,
    submit: bool = True,
    overrides: dict | None = None,
) -> dict:
    """Create + submit a Payment Entry that settles a Sales Invoice.

    Wraps ``erpnext.accounts.doctype.payment_entry.payment_entry.get_payment_entry``
    — the same builder the *Create → Payment* button calls.
    """
    submit_if_draft(client, "Sales Invoice", sales_invoice)
    pe = client.call_method(
        "erpnext.accounts.doctype.payment_entry.payment_entry.get_payment_entry",
        dt="Sales Invoice", dn=sales_invoice,
    )
    if mode_of_payment: pe["mode_of_payment"] = mode_of_payment
    if paid_amount is not None: pe["paid_amount"] = paid_amount
    if reference_no: pe["reference_no"] = reference_no
    if reference_date: pe["reference_date"] = reference_date
    pe = apply_overrides(pe, overrides)
    pe_saved = client.insert(pe)
    if submit:
        client.submit("Payment Entry", pe_saved["name"])
        pe_saved = client.get_doc("Payment Entry", pe_saved["name"])
    return pe_saved


# ── End-to-end workflows ──────────────────────────────────────────────

def order_to_cash(
    client: FrappeClient,
    customer: str,
    items: list,
    *,
    company: str | None = None,
    delivery_date: str | None = None,
    mode_of_payment: str | None = None,
) -> dict:
    """Full Order-to-Cash: Sales Order → Delivery Note → Sales Invoice
    → Payment Entry.

    Returns a dict with the name of every doc created at each step, so
    agents can drill in / audit.
    """
    so = create_sales_order(
        client, customer, items,
        company=company, delivery_date=delivery_date, submit=True,
    )
    dn = create_delivery_from_order(client, so["name"], submit=True)
    si = create_invoice_from_delivery(client, dn["name"], submit=True)
    pe = collect_payment_for_invoice(
        client, si["name"], mode_of_payment=mode_of_payment, submit=True,
    )
    return {
        "workflow": "order_to_cash",
        "customer": customer,
        "sales_order": so["name"],
        "delivery_note": dn["name"],
        "sales_invoice": si["name"],
        "payment_entry": pe["name"],
        "grand_total": si.get("grand_total"),
        "paid_amount": pe.get("paid_amount"),
    }


def quote_to_cash(
    client: FrappeClient,
    customer: str,
    items: list,
    *,
    company: str | None = None,
    valid_till: str | None = None,
    delivery_date: str | None = None,
    mode_of_payment: str | None = None,
) -> dict:
    """Quote-to-Cash: Quotation → Sales Order → Delivery Note → Invoice → Payment."""
    q = create_quotation(
        client, customer, items,
        company=company, valid_till=valid_till, submit=True,
    )
    so = convert_quotation_to_sales_order(client, q["name"], submit=True)
    dn = create_delivery_from_order(client, so["name"], submit=True)
    si = create_invoice_from_delivery(client, dn["name"], submit=True)
    pe = collect_payment_for_invoice(
        client, si["name"], mode_of_payment=mode_of_payment, submit=True,
    )
    return {
        "workflow": "quote_to_cash",
        "customer": customer,
        "quotation": q["name"],
        "sales_order": so["name"],
        "delivery_note": dn["name"],
        "sales_invoice": si["name"],
        "payment_entry": pe["name"],
    }


def onboard_customer_with_order(
    client: FrappeClient,
    customer_name: str,
    items: list,
    *,
    company: str | None = None,
    customer_group: str = "All Customer Groups",
    territory: str = "All Territories",
    ensure_items: bool = False,
) -> dict:
    """Create Customer (and optionally Items) if missing, then raise a
    Sales Order. Idempotent for repeat customer names.
    """
    ensure_customer(
        client, customer_name,
        customer_group=customer_group, territory=territory,
    )
    if ensure_items:
        for it in normalize_items(items):
            ensure_item(client, it["item_code"])
    so = create_sales_order(client, customer_name, items, company=company, submit=True)
    return {
        "workflow": "onboard_customer_with_order",
        "customer": customer_name,
        "sales_order": so["name"],
        "grand_total": so.get("grand_total"),
    }


# ── Read-side helpers ─────────────────────────────────────────────────

def list_open_sales_orders(
    client: FrappeClient,
    *,
    customer: str | None = None,
    company: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """Sales Orders that are submitted but not yet fully billed or delivered."""
    filters: list = [
        ["docstatus", "=", 1],
        ["status", "not in", ["Closed", "Completed", "Cancelled"]],
    ]
    if customer: filters.append(["customer", "=", customer])
    if company: filters.append(["company", "=", company])
    return client.get_list(
        "Sales Order",
        filters=filters,
        fields=["name", "customer", "transaction_date", "delivery_date",
                "status", "per_delivered", "per_billed", "grand_total", "currency"],
        limit=limit,
        order_by="transaction_date desc",
    )


def customer_dashboard(client: FrappeClient, customer: str) -> dict:
    """Aggregate the key selling metrics a human sees on the Customer page."""
    open_sos = list_open_sales_orders(client, customer=customer, limit=100)
    outstanding = client.get_list(
        "Sales Invoice",
        filters=[["customer", "=", customer], ["outstanding_amount", ">", 0],
                 ["docstatus", "=", 1]],
        fields=["name", "grand_total", "outstanding_amount", "due_date", "status"],
        limit=100,
    )
    return {
        "customer": customer,
        "open_sales_orders_count": len(open_sos),
        "open_sales_orders": open_sos[:20],
        "outstanding_invoices_count": len(outstanding),
        "outstanding_invoices": outstanding[:20],
        "total_outstanding": sum(
            float(i.get("outstanding_amount") or 0) for i in outstanding
        ),
    }
