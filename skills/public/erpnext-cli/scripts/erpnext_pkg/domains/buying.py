"""Buying domain: procure-to-pay business flows.

Mirror of ``selling``, but from the supplier side::

  create_material_request ─► create_rfq_from_mr (make_request_for_quotation)
                          └► create_po_from_mr (make_purchase_order)
  create_supplier_quotation ─► convert_sq_to_po (make_purchase_order)
  create_purchase_order ─► create_receipt_from_po (make_purchase_receipt)
                        └► create_bill_from_po (make_purchase_invoice)
                                │
                                ▼
                        pay_supplier_bill (get_payment_entry)

``procure_to_pay`` runs the full chain.
"""

from __future__ import annotations

import hashlib

from ..core.client import FrappeClient
from ._common import (
    apply_overrides,
    ensure_item,
    ensure_supplier,
    normalize_items,
    submit_if_draft,
)

# ── Idempotency helpers ────────────────────────────────────────────────
#
# Pinned against the 2026-05-02 incident where an LLM agent retry against
# a transient psycopg2.errors.SerializationFailure inserted the same
# Purchase Order three times in 49 seconds; combined with a frappe
# error-handling bug (see frappe/utils/error.py:128) this leaked one
# `idle in transaction (aborted)` PG backend per retry, accumulating to
# 92 stale connections and exhausting max_connections=100 on dev.
#
# The fix at this layer: stamp every newly-inserted PO's `remarks` with a
# deterministic `po-dedup-key:<hash>` marker derived from
# (supplier, schedule_date, normalized items). On a repeat call with
# identical inputs we look up the existing PO and return it instead of
# POSTing again. Cancelled POs (docstatus 2) are excluded from the
# lookup so an explicit cancel-then-recreate flow still works.
_DEDUP_KEY_PREFIX = "po-dedup-key:"


def _po_fingerprint(supplier: str, items: list[dict], schedule_date: str | None) -> str:
    """Deterministic 16-char hash of the business-meaningful PO inputs.

    Order-independent over rows so two calls with the same items in
    different order collapse to the same fingerprint.
    """
    rows = sorted(
        (
            str(r.get("item_code", "")),
            str(r.get("qty", "")),
            str(r.get("rate", "")),
            str(r.get("warehouse", "")),
        )
        for r in items
    )
    payload = repr((supplier, schedule_date or "", rows))
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def _find_existing_po(
    client: FrappeClient, supplier: str, fingerprint: str
) -> dict | None:
    """Return an existing draft or submitted PO whose remarks embed the
    fingerprint, or None. Cancelled POs are ignored on purpose."""
    marker = f"{_DEDUP_KEY_PREFIX}{fingerprint}"
    candidates = client.get_list(
        "Purchase Order",
        filters=[
            ["supplier", "=", supplier],
            ["docstatus", "in", [0, 1]],
            ["remarks", "like", f"%{marker}%"],
        ],
        fields=["name"],
        limit=1,
    )
    if not candidates:
        return None
    return client.get_doc("Purchase Order", candidates[0]["name"])


# ── Primitives ────────────────────────────────────────────────────────

def create_material_request(
    client: FrappeClient,
    items: list,
    *,
    purpose: str = "Purchase",
    company: str | None = None,
    schedule_date: str | None = None,
    submit: bool = True,
    extra: dict | None = None,
) -> dict:
    """Create a Material Request (purchase/material transfer/issue).

    ``purpose`` is one of: *Purchase*, *Material Transfer*, *Material Issue*,
    *Manufacture*, *Customer Provided*.
    """
    rows = normalize_items(items)
    if schedule_date:
        for r in rows:
            r.setdefault("schedule_date", schedule_date)
    doc = {
        "doctype": "Material Request",
        "material_request_type": purpose,
        "items": rows,
    }
    if company: doc["company"] = company
    if schedule_date: doc["schedule_date"] = schedule_date
    if extra: doc.update(extra)
    created = client.insert(doc)
    if submit:
        client.submit("Material Request", created["name"])
        created = client.get_doc("Material Request", created["name"])
    return created


def create_purchase_order(
    client: FrappeClient,
    supplier: str,
    items: list,
    *,
    company: str | None = None,
    schedule_date: str | None = None,
    currency: str | None = None,
    transaction_date: str | None = None,
    submit: bool = True,
    extra: dict | None = None,
) -> dict:
    """Create a Purchase Order. Submits by default.

    Idempotent on the tuple ``(supplier, schedule_date, items)``: a
    repeat call with identical inputs returns the existing draft or
    submitted PO instead of inserting a duplicate. See module-level
    ``_DEDUP_KEY_PREFIX`` for the rationale and incident pin.
    """
    rows = normalize_items(items)
    if schedule_date:
        for r in rows:
            r.setdefault("schedule_date", schedule_date)

    fingerprint = _po_fingerprint(supplier, rows, schedule_date)
    existing = _find_existing_po(client, supplier, fingerprint)
    if existing is not None:
        if submit and int(existing.get("docstatus", 0)) == 0:
            client.submit("Purchase Order", existing["name"])
            existing = client.get_doc("Purchase Order", existing["name"])
        return existing

    doc = {
        "doctype": "Purchase Order",
        "supplier": supplier,
        "items": rows,
    }
    if company: doc["company"] = company
    if schedule_date: doc["schedule_date"] = schedule_date
    if currency: doc["currency"] = currency
    if transaction_date: doc["transaction_date"] = transaction_date
    if extra: doc.update(extra)

    marker = f"{_DEDUP_KEY_PREFIX}{fingerprint}"
    caller_remarks = (doc.get("remarks") or "").rstrip()
    doc["remarks"] = f"{caller_remarks}\n{marker}" if caller_remarks else marker

    created = client.insert(doc)
    if submit:
        client.submit("Purchase Order", created["name"])
        created = client.get_doc("Purchase Order", created["name"])
    return created


def create_supplier_quotation(
    client: FrappeClient,
    supplier: str,
    items: list,
    *,
    company: str | None = None,
    valid_till: str | None = None,
    submit: bool = False,
    extra: dict | None = None,
) -> dict:
    doc = {
        "doctype": "Supplier Quotation",
        "supplier": supplier,
        "items": normalize_items(items),
    }
    if company: doc["company"] = company
    if valid_till: doc["valid_till"] = valid_till
    if extra: doc.update(extra)
    created = client.insert(doc)
    if submit:
        client.submit("Supplier Quotation", created["name"])
        created = client.get_doc("Supplier Quotation", created["name"])
    return created


# ── Chain builders ────────────────────────────────────────────────────

def create_rfq_from_mr(
    client: FrappeClient,
    material_request: str,
    *,
    suppliers: list[str] | None = None,
    overrides: dict | None = None,
) -> dict:
    """Create a Request For Quotation from a submitted Material Request.

    Wraps ``erpnext.stock.doctype.material_request.material_request.make_request_for_quotation``.
    ``suppliers`` is an optional list of supplier names to add as RFQ recipients.
    """
    submit_if_draft(client, "Material Request", material_request)
    target = client.call_method(
        "erpnext.stock.doctype.material_request.material_request.make_request_for_quotation",
        source_name=material_request,
    )
    if suppliers:
        target.setdefault("suppliers", [])
        for s in suppliers:
            target["suppliers"].append({"supplier": s})
    target = apply_overrides(target, overrides)
    return client.insert(target)


def create_po_from_mr(
    client: FrappeClient,
    material_request: str,
    *,
    submit: bool = True,
    overrides: dict | None = None,
) -> dict:
    """Convert Material Request → Purchase Order.

    Wraps ``erpnext.stock.doctype.material_request.material_request.make_purchase_order``.
    """
    submit_if_draft(client, "Material Request", material_request)
    target = client.call_method(
        "erpnext.stock.doctype.material_request.material_request.make_purchase_order",
        source_name=material_request,
    )
    target = apply_overrides(target, overrides)
    po = client.insert(target)
    if submit:
        client.submit("Purchase Order", po["name"])
        po = client.get_doc("Purchase Order", po["name"])
    return po


def convert_sq_to_po(
    client: FrappeClient,
    supplier_quotation: str,
    *,
    submit: bool = True,
    overrides: dict | None = None,
) -> dict:
    """Supplier Quotation → Purchase Order.

    Wraps ``erpnext.buying.doctype.supplier_quotation.supplier_quotation.make_purchase_order``.
    """
    submit_if_draft(client, "Supplier Quotation", supplier_quotation)
    target = client.call_method(
        "erpnext.buying.doctype.supplier_quotation.supplier_quotation.make_purchase_order",
        source_name=supplier_quotation,
    )
    target = apply_overrides(target, overrides)
    po = client.insert(target)
    if submit:
        client.submit("Purchase Order", po["name"])
        po = client.get_doc("Purchase Order", po["name"])
    return po


def create_receipt_from_po(
    client: FrappeClient,
    purchase_order: str,
    *,
    submit: bool = True,
    overrides: dict | None = None,
) -> dict:
    """Purchase Order → Purchase Receipt (goods received into warehouse).

    Wraps ``erpnext.buying.doctype.purchase_order.purchase_order.make_purchase_receipt``.
    """
    submit_if_draft(client, "Purchase Order", purchase_order)
    target = client.call_method(
        "erpnext.buying.doctype.purchase_order.purchase_order.make_purchase_receipt",
        source_name=purchase_order,
    )
    target = apply_overrides(target, overrides)
    pr = client.insert(target)
    if submit:
        client.submit("Purchase Receipt", pr["name"])
        pr = client.get_doc("Purchase Receipt", pr["name"])
    return pr


def create_bill_from_po(
    client: FrappeClient,
    purchase_order: str,
    *,
    submit: bool = True,
    overrides: dict | None = None,
) -> dict:
    """Purchase Order → Purchase Invoice (supplier bill).

    Wraps ``erpnext.buying.doctype.purchase_order.purchase_order.make_purchase_invoice``.
    """
    submit_if_draft(client, "Purchase Order", purchase_order)
    target = client.call_method(
        "erpnext.buying.doctype.purchase_order.purchase_order.make_purchase_invoice",
        source_name=purchase_order,
    )
    target = apply_overrides(target, overrides)
    pi = client.insert(target)
    if submit:
        client.submit("Purchase Invoice", pi["name"])
        pi = client.get_doc("Purchase Invoice", pi["name"])
    return pi


def create_bill_from_receipt(
    client: FrappeClient,
    purchase_receipt: str,
    *,
    submit: bool = True,
    overrides: dict | None = None,
) -> dict:
    """Purchase Receipt → Purchase Invoice.

    Wraps ``erpnext.stock.doctype.purchase_receipt.purchase_receipt.make_purchase_invoice``.
    """
    submit_if_draft(client, "Purchase Receipt", purchase_receipt)
    target = client.call_method(
        "erpnext.stock.doctype.purchase_receipt.purchase_receipt.make_purchase_invoice",
        source_name=purchase_receipt,
    )
    target = apply_overrides(target, overrides)
    pi = client.insert(target)
    if submit:
        client.submit("Purchase Invoice", pi["name"])
        pi = client.get_doc("Purchase Invoice", pi["name"])
    return pi


def pay_supplier_bill(
    client: FrappeClient,
    purchase_invoice: str,
    *,
    mode_of_payment: str | None = None,
    paid_amount: float | None = None,
    reference_no: str | None = None,
    reference_date: str | None = None,
    submit: bool = True,
    overrides: dict | None = None,
) -> dict:
    """Create + submit a Payment Entry to settle a Purchase Invoice.

    Wraps ``erpnext.accounts.doctype.payment_entry.payment_entry.get_payment_entry``.
    """
    submit_if_draft(client, "Purchase Invoice", purchase_invoice)
    pe = client.call_method(
        "erpnext.accounts.doctype.payment_entry.payment_entry.get_payment_entry",
        dt="Purchase Invoice", dn=purchase_invoice,
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

def procure_to_pay(
    client: FrappeClient,
    supplier: str,
    items: list,
    *,
    company: str | None = None,
    schedule_date: str | None = None,
    mode_of_payment: str | None = None,
) -> dict:
    """Full Procure-to-Pay: Purchase Order → Purchase Receipt → Purchase
    Invoice → Payment Entry."""
    po = create_purchase_order(
        client, supplier, items,
        company=company, schedule_date=schedule_date, submit=True,
    )
    pr = create_receipt_from_po(client, po["name"], submit=True)
    pi = create_bill_from_receipt(client, pr["name"], submit=True)
    pe = pay_supplier_bill(
        client, pi["name"], mode_of_payment=mode_of_payment, submit=True,
    )
    return {
        "workflow": "procure_to_pay",
        "supplier": supplier,
        "purchase_order": po["name"],
        "purchase_receipt": pr["name"],
        "purchase_invoice": pi["name"],
        "payment_entry": pe["name"],
        "grand_total": pi.get("grand_total"),
        "paid_amount": pe.get("paid_amount"),
    }


def request_to_pay(
    client: FrappeClient,
    supplier: str,
    items: list,
    *,
    company: str | None = None,
    schedule_date: str | None = None,
    mode_of_payment: str | None = None,
) -> dict:
    """Request-to-Pay: Material Request → Purchase Order → Receipt →
    Invoice → Payment Entry."""
    mr = create_material_request(
        client, items, purpose="Purchase",
        company=company, schedule_date=schedule_date, submit=True,
    )
    po = create_po_from_mr(client, mr["name"], submit=False)
    po["supplier"] = supplier
    client.update("Purchase Order", po["name"], {"supplier": supplier})
    client.submit("Purchase Order", po["name"])
    pr = create_receipt_from_po(client, po["name"], submit=True)
    pi = create_bill_from_receipt(client, pr["name"], submit=True)
    pe = pay_supplier_bill(
        client, pi["name"], mode_of_payment=mode_of_payment, submit=True,
    )
    return {
        "workflow": "request_to_pay",
        "supplier": supplier,
        "material_request": mr["name"],
        "purchase_order": po["name"],
        "purchase_receipt": pr["name"],
        "purchase_invoice": pi["name"],
        "payment_entry": pe["name"],
    }


def onboard_supplier_with_order(
    client: FrappeClient,
    supplier_name: str,
    items: list,
    *,
    company: str | None = None,
    supplier_group: str = "All Supplier Groups",
    ensure_items: bool = False,
) -> dict:
    """Create Supplier (and optionally Items), then raise a Purchase
    Order. Idempotent for existing suppliers."""
    ensure_supplier(client, supplier_name, supplier_group=supplier_group)
    if ensure_items:
        for it in normalize_items(items):
            ensure_item(client, it["item_code"])
    po = create_purchase_order(client, supplier_name, items, company=company, submit=True)
    return {
        "workflow": "onboard_supplier_with_order",
        "supplier": supplier_name,
        "purchase_order": po["name"],
        "grand_total": po.get("grand_total"),
    }


# ── Read-side helpers ─────────────────────────────────────────────────

def list_open_purchase_orders(
    client: FrappeClient,
    *,
    supplier: str | None = None,
    company: str | None = None,
    limit: int = 20,
) -> list[dict]:
    filters: list = [
        ["docstatus", "=", 1],
        ["status", "not in", ["Closed", "Completed", "Cancelled"]],
    ]
    if supplier: filters.append(["supplier", "=", supplier])
    if company: filters.append(["company", "=", company])
    return client.get_list(
        "Purchase Order",
        filters=filters,
        fields=["name", "supplier", "transaction_date", "schedule_date",
                "status", "per_received", "per_billed", "grand_total", "currency"],
        limit=limit,
        order_by="transaction_date desc",
    )


def supplier_dashboard(client: FrappeClient, supplier: str) -> dict:
    open_pos = list_open_purchase_orders(client, supplier=supplier, limit=100)
    outstanding = client.get_list(
        "Purchase Invoice",
        filters=[["supplier", "=", supplier], ["outstanding_amount", ">", 0],
                 ["docstatus", "=", 1]],
        fields=["name", "grand_total", "outstanding_amount", "due_date", "status"],
        limit=100,
    )
    return {
        "supplier": supplier,
        "open_purchase_orders_count": len(open_pos),
        "open_purchase_orders": open_pos[:20],
        "outstanding_bills_count": len(outstanding),
        "outstanding_bills": outstanding[:20],
        "total_outstanding": sum(
            float(i.get("outstanding_amount") or 0) for i in outstanding
        ),
    }
