"""Accounts domain: payments, journal entries, and reconciliation.

The primary business actions here are:

  - **Receive payment** from a customer against Sales Invoice(s)
  - **Make payment** to a supplier against Purchase Invoice(s)
  - **Journal entry** for manual GL postings (accruals, adjustments)
  - **Reconcile payments** — match unallocated Payment Entries to Invoices
  - **Query AR/AP** outstanding
"""

from __future__ import annotations

from ..core.client import FrappeClient
from ._common import apply_overrides, submit_if_draft


# ── Payments (via get_payment_entry) ──────────────────────────────────

def _build_payment_entry(
    client: FrappeClient,
    *,
    party_type: str,
    against_doctype: str,
    against_name: str,
    party: str | None = None,
    mode_of_payment: str | None = None,
    paid_amount: float | None = None,
    reference_no: str | None = None,
    reference_date: str | None = None,
    overrides: dict | None = None,
) -> dict:
    submit_if_draft(client, against_doctype, against_name)
    pe = client.call_method(
        "erpnext.accounts.doctype.payment_entry.payment_entry.get_payment_entry",
        dt=against_doctype, dn=against_name, party_amount=paid_amount,
    )
    if party: pe["party"] = party
    pe.setdefault("party_type", party_type)
    if mode_of_payment: pe["mode_of_payment"] = mode_of_payment
    if paid_amount is not None: pe["paid_amount"] = paid_amount
    if reference_no: pe["reference_no"] = reference_no
    if reference_date: pe["reference_date"] = reference_date
    return apply_overrides(pe, overrides)


def receive_payment(
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
    """Receive a payment from a customer against a Sales Invoice."""
    pe = _build_payment_entry(
        client,
        party_type="Customer",
        against_doctype="Sales Invoice",
        against_name=sales_invoice,
        mode_of_payment=mode_of_payment,
        paid_amount=paid_amount,
        reference_no=reference_no,
        reference_date=reference_date,
        overrides=overrides,
    )
    saved = client.insert(pe)
    if submit:
        client.submit("Payment Entry", saved["name"])
        saved = client.get_doc("Payment Entry", saved["name"])
    return saved


def make_payment(
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
    """Make a payment to a supplier against a Purchase Invoice."""
    pe = _build_payment_entry(
        client,
        party_type="Supplier",
        against_doctype="Purchase Invoice",
        against_name=purchase_invoice,
        mode_of_payment=mode_of_payment,
        paid_amount=paid_amount,
        reference_no=reference_no,
        reference_date=reference_date,
        overrides=overrides,
    )
    saved = client.insert(pe)
    if submit:
        client.submit("Payment Entry", saved["name"])
        saved = client.get_doc("Payment Entry", saved["name"])
    return saved


# ── Journal Entry ─────────────────────────────────────────────────────

def make_journal_entry(
    client: FrappeClient,
    accounts: list[dict],
    *,
    company: str | None = None,
    posting_date: str | None = None,
    voucher_type: str = "Journal Entry",
    user_remark: str | None = None,
    submit: bool = True,
    extra: dict | None = None,
) -> dict:
    """Create a Journal Entry with the given ``accounts`` rows.

    Each row must include ``account`` and either ``debit_in_account_currency``
    or ``credit_in_account_currency`` (plus optional ``party_type`` / ``party``
    / ``cost_center`` / ``reference_type`` / ``reference_name``).
    """
    debit = sum(float(r.get("debit_in_account_currency", 0) or 0) for r in accounts)
    credit = sum(float(r.get("credit_in_account_currency", 0) or 0) for r in accounts)
    if round(debit - credit, 4) != 0:
        from ..core.errors import WorkflowError
        raise WorkflowError(
            f"Journal Entry is unbalanced: debit={debit}, credit={credit}"
        )
    doc = {
        "doctype": "Journal Entry",
        "voucher_type": voucher_type,
        "accounts": [dict(a) for a in accounts],
    }
    if company: doc["company"] = company
    if posting_date: doc["posting_date"] = posting_date
    if user_remark: doc["user_remark"] = user_remark
    if extra: doc.update(extra)
    created = client.insert(doc)
    if submit:
        client.submit("Journal Entry", created["name"])
        created = client.get_doc("Journal Entry", created["name"])
    return created


# ── Reconciliation ────────────────────────────────────────────────────

def reconcile_payments(
    client: FrappeClient,
    *,
    party_type: str,
    party: str,
    company: str,
    receivable_payable_account: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
) -> dict:
    """Auto-reconcile unallocated Payment Entries against outstanding invoices.

    Wraps ``erpnext.accounts.doctype.payment_reconciliation.payment_reconciliation``
    — runs ``get_unreconciled_entries`` then ``allocate_entries`` then
    ``reconcile`` in sequence.
    """
    pr = {
        "doctype": "Payment Reconciliation",
        "party_type": party_type,
        "party": party,
        "company": company,
    }
    if receivable_payable_account:
        pr["receivable_payable_account"] = receivable_payable_account
    if from_date: pr["from_date"] = from_date
    if to_date: pr["to_date"] = to_date
    doc = client.insert(pr)
    name = doc["name"]
    client.run_doc_method("Payment Reconciliation", name, "get_unreconciled_entries")
    fresh = client.get_doc("Payment Reconciliation", name)
    invoices = fresh.get("invoices") or []
    payments = fresh.get("payments") or []
    if invoices and payments:
        client.run_doc_method("Payment Reconciliation", name, "allocate_entries",
                              args={"invoices": invoices, "payments": payments})
        client.run_doc_method("Payment Reconciliation", name, "reconcile")
    return {
        "reconciliation": name,
        "invoices_matched": len(invoices),
        "payments_matched": len(payments),
    }


# ── Queries ───────────────────────────────────────────────────────────

def accounts_receivable(
    client: FrappeClient,
    *,
    company: str | None = None,
    customer: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """Outstanding Sales Invoices (AR)."""
    filters: list = [
        ["docstatus", "=", 1],
        ["outstanding_amount", ">", 0],
    ]
    if company: filters.append(["company", "=", company])
    if customer: filters.append(["customer", "=", customer])
    return client.get_list(
        "Sales Invoice",
        filters=filters,
        fields=["name", "customer", "posting_date", "due_date",
                "grand_total", "outstanding_amount", "currency", "status"],
        limit=limit,
        order_by="due_date asc",
    )


def accounts_payable(
    client: FrappeClient,
    *,
    company: str | None = None,
    supplier: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """Outstanding Purchase Invoices (AP)."""
    filters: list = [
        ["docstatus", "=", 1],
        ["outstanding_amount", ">", 0],
    ]
    if company: filters.append(["company", "=", company])
    if supplier: filters.append(["supplier", "=", supplier])
    return client.get_list(
        "Purchase Invoice",
        filters=filters,
        fields=["name", "supplier", "posting_date", "due_date",
                "grand_total", "outstanding_amount", "currency", "status"],
        limit=limit,
        order_by="due_date asc",
    )


def cashflow_snapshot(client: FrappeClient, company: str) -> dict:
    """Quick AR/AP snapshot for ``company``."""
    ar = accounts_receivable(client, company=company, limit=500)
    ap = accounts_payable(client, company=company, limit=500)
    return {
        "company": company,
        "ar_count": len(ar),
        "ar_total": round(sum(float(i.get("outstanding_amount") or 0) for i in ar), 2),
        "ap_count": len(ap),
        "ap_total": round(sum(float(i.get("outstanding_amount") or 0) for i in ap), 2),
        "net_position": round(
            sum(float(i.get("outstanding_amount") or 0) for i in ar)
            - sum(float(i.get("outstanding_amount") or 0) for i in ap),
            2,
        ),
    }
