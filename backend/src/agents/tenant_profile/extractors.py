"""Link-field extractors for ERPNext payloads.

Pure functions: take a doctype + payload dict, return the list of link-field
references that profile-eligible doctypes are mentioned in. The summarizer
later uses these references as evidence that a Customer/Item/Warehouse/etc.
is operationally in use.

Design contract: see TENANT_PROFILE_DESIGN.md §4.3 and Appendix A.

Path syntax for rules:
    "field"              top-level scalar
    "child[].field"      iterate over child-table rows, pull field per row
    "child[].nested[].x" nested child tables (rare, but supported)
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Literal

Weight = Literal["primary", "secondary", "browse"]


@dataclass(frozen=True)
class Ref:
    """A single link-field reference extracted from an ERPNext payload."""

    doctype: str
    name: str
    field: str
    weight: Weight


# ── Doctype categorisation ────────────────────────────────────────────────

# Master/reference data — these CAN be promoted to the profile. The summarizer
# uses this list to validate that an entity may end up in profile.key_entities.
PROFILE_WHITELIST: frozenset[str] = frozenset(
    {
        "Customer",
        "Supplier",
        "Item",
        "Warehouse",
        "Account",
        "Cost Center",
        "Price List",
        "UOM",
        "Item Group",
        "Customer Group",
        "Supplier Group",
        "Territory",
        "Sales Person",
        "Brand",
        "Sales Taxes and Charges Template",
        "Purchase Taxes and Charges Template",
        "Payment Terms Template",
        "Pricing Rule",
        "Currency",
        "Company",
        "Fiscal Year",
        "Employee",
    }
)

# Transactional / ledger / per-instance documents. We extract refs FROM their
# payloads but never count the document itself as a profile candidate.
PROFILE_DENYLIST: frozenset[str] = frozenset(
    {
        # Selling
        "Quotation",
        "Sales Order",
        "Sales Invoice",
        "Delivery Note",
        "Pick List",
        "Stock Reservation Entry",
        # Buying
        "Material Request",
        "Request for Quotation",
        "Supplier Quotation",
        "Purchase Order",
        "Purchase Receipt",
        "Purchase Invoice",
        # Stock
        "Stock Entry",
        "Stock Reconciliation",
        "Shipment",
        "Quality Inspection",
        "Landed Cost Voucher",
        # Accounts
        "Payment Entry",
        "Journal Entry",
        "Dunning",
        "Subscription",
        "Subscription Plan",
        "POS Invoice",
        "POS Opening Entry",
        "POS Closing Entry",
        "Bank Transaction",
        "Period Closing Voucher",
        # Ledgers
        "Stock Ledger Entry",
        "GL Entry",
        "Payment Ledger Entry",
        "Bin",
        "Serial and Batch Bundle",
        "Item Price",
        # Manufacturing / Subcontracting / Assets / CRM / Projects (v2 scope)
        "Work Order",
        "Job Card",
        "BOM",
        "Production Plan",
        "Subcontracting Order",
        "Subcontracting Receipt",
        "Asset",
        "Asset Movement",
        "Asset Repair",
        "Lead",
        "Opportunity",
        "Prospect",
        "Project",
        "Task",
        "Timesheet",
    }
)


# ── Per-doctype rules ─────────────────────────────────────────────────────
#
# Each entry: (path, target_doctype, weight)
# Only paths that point at PROFILE_WHITELIST doctypes are useful — we list
# them explicitly to keep the surface auditable rather than infer from
# field metadata at runtime.

_Rules = list[tuple[str, str, Weight]]

EXTRACTION_RULES: dict[str, _Rules] = {
    # ── Selling ────────────────────────────────────────────────────────
    "Sales Order": [
        ("customer", "Customer", "primary"),
        ("customer_group", "Customer Group", "primary"),
        ("territory", "Territory", "primary"),
        ("currency", "Currency", "primary"),
        ("selling_price_list", "Price List", "primary"),
        ("taxes_and_charges", "Sales Taxes and Charges Template", "primary"),
        ("payment_terms_template", "Payment Terms Template", "primary"),
        ("items[].item_code", "Item", "primary"),
        ("items[].item_group", "Item Group", "primary"),
        ("items[].brand", "Brand", "primary"),
        ("items[].uom", "UOM", "primary"),
        ("items[].warehouse", "Warehouse", "primary"),
        ("items[].cost_center", "Cost Center", "primary"),
        ("taxes[].account_head", "Account", "primary"),
        ("sales_team[].sales_person", "Sales Person", "primary"),
    ],
    "Quotation": [
        ("customer", "Customer", "primary"),
        ("party_name", "Customer", "secondary"),  # Dynamic Link to Lead/Prospect/Customer
        ("customer_group", "Customer Group", "primary"),
        ("territory", "Territory", "primary"),
        ("currency", "Currency", "primary"),
        ("selling_price_list", "Price List", "primary"),
        ("taxes_and_charges", "Sales Taxes and Charges Template", "primary"),
        ("items[].item_code", "Item", "primary"),
        ("items[].uom", "UOM", "primary"),
        ("items[].warehouse", "Warehouse", "primary"),
        ("taxes[].account_head", "Account", "primary"),
    ],
    "Sales Invoice": [
        ("customer", "Customer", "primary"),
        ("debit_to", "Account", "primary"),
        ("currency", "Currency", "primary"),
        ("selling_price_list", "Price List", "primary"),
        ("taxes_and_charges", "Sales Taxes and Charges Template", "primary"),
        ("payment_terms_template", "Payment Terms Template", "primary"),
        ("items[].item_code", "Item", "primary"),
        ("items[].item_group", "Item Group", "primary"),
        ("items[].uom", "UOM", "primary"),
        ("items[].warehouse", "Warehouse", "primary"),
        ("items[].cost_center", "Cost Center", "primary"),
        ("items[].income_account", "Account", "primary"),
        ("taxes[].account_head", "Account", "primary"),
        ("sales_team[].sales_person", "Sales Person", "primary"),
    ],
    "Delivery Note": [
        ("customer", "Customer", "primary"),
        ("customer_group", "Customer Group", "primary"),
        ("territory", "Territory", "primary"),
        ("currency", "Currency", "primary"),
        ("set_warehouse", "Warehouse", "primary"),
        ("items[].item_code", "Item", "primary"),
        ("items[].uom", "UOM", "primary"),
        ("items[].warehouse", "Warehouse", "primary"),
        ("items[].cost_center", "Cost Center", "primary"),
    ],
    # ── Buying ─────────────────────────────────────────────────────────
    "Purchase Order": [
        ("supplier", "Supplier", "primary"),
        ("supplier_group", "Supplier Group", "primary"),
        ("currency", "Currency", "primary"),
        ("buying_price_list", "Price List", "primary"),
        ("taxes_and_charges", "Purchase Taxes and Charges Template", "primary"),
        ("payment_terms_template", "Payment Terms Template", "primary"),
        ("items[].item_code", "Item", "primary"),
        ("items[].item_group", "Item Group", "primary"),
        ("items[].uom", "UOM", "primary"),
        ("items[].warehouse", "Warehouse", "primary"),
        ("items[].cost_center", "Cost Center", "primary"),
        ("taxes[].account_head", "Account", "primary"),
    ],
    "Purchase Receipt": [
        ("supplier", "Supplier", "primary"),
        ("currency", "Currency", "primary"),
        ("set_warehouse", "Warehouse", "primary"),
        ("items[].item_code", "Item", "primary"),
        ("items[].uom", "UOM", "primary"),
        ("items[].warehouse", "Warehouse", "primary"),
        ("items[].cost_center", "Cost Center", "primary"),
    ],
    "Purchase Invoice": [
        ("supplier", "Supplier", "primary"),
        ("credit_to", "Account", "primary"),
        ("currency", "Currency", "primary"),
        ("buying_price_list", "Price List", "primary"),
        ("taxes_and_charges", "Purchase Taxes and Charges Template", "primary"),
        ("payment_terms_template", "Payment Terms Template", "primary"),
        ("items[].item_code", "Item", "primary"),
        ("items[].uom", "UOM", "primary"),
        ("items[].warehouse", "Warehouse", "primary"),
        ("items[].cost_center", "Cost Center", "primary"),
        ("items[].expense_account", "Account", "primary"),
        ("taxes[].account_head", "Account", "primary"),
    ],
    "Request for Quotation": [
        ("suppliers[].supplier", "Supplier", "primary"),
        ("items[].item_code", "Item", "primary"),
        ("items[].warehouse", "Warehouse", "primary"),
        ("items[].uom", "UOM", "primary"),
    ],
    "Supplier Quotation": [
        ("supplier", "Supplier", "primary"),
        ("currency", "Currency", "primary"),
        ("buying_price_list", "Price List", "primary"),
        ("items[].item_code", "Item", "primary"),
        ("items[].uom", "UOM", "primary"),
    ],
    # ── Stock ──────────────────────────────────────────────────────────
    "Material Request": [
        ("items[].item_code", "Item", "primary"),
        ("items[].uom", "UOM", "primary"),
        ("items[].warehouse", "Warehouse", "primary"),
        ("items[].from_warehouse", "Warehouse", "primary"),
        ("items[].cost_center", "Cost Center", "primary"),
    ],
    "Stock Entry": [
        ("from_warehouse", "Warehouse", "primary"),
        ("to_warehouse", "Warehouse", "primary"),
        ("items[].item_code", "Item", "primary"),
        ("items[].s_warehouse", "Warehouse", "primary"),
        ("items[].t_warehouse", "Warehouse", "primary"),
        ("items[].uom", "UOM", "primary"),
        ("items[].cost_center", "Cost Center", "primary"),
        ("items[].expense_account", "Account", "primary"),
    ],
    "Stock Reconciliation": [
        ("items[].item_code", "Item", "primary"),
        ("items[].warehouse", "Warehouse", "primary"),
    ],
    # ── Accounts ───────────────────────────────────────────────────────
    "Payment Entry": [
        ("party", "Customer", "secondary"),  # Dynamic — refined by party_type below
        ("paid_from", "Account", "primary"),
        ("paid_to", "Account", "primary"),
        ("paid_from_account_currency", "Currency", "primary"),
        ("paid_to_account_currency", "Currency", "primary"),
        ("references[].account", "Account", "primary"),
        ("deductions[].account", "Account", "primary"),
        ("deductions[].cost_center", "Cost Center", "primary"),
    ],
    "Journal Entry": [
        ("accounts[].account", "Account", "primary"),
        ("accounts[].cost_center", "Cost Center", "primary"),
        ("accounts[].party", "Customer", "secondary"),
    ],
    # ── Master-data updates: capture outgoing taxonomy refs ───────────
    "Customer": [
        ("customer_group", "Customer Group", "primary"),
        ("territory", "Territory", "primary"),
        ("default_currency", "Currency", "primary"),
        ("default_price_list", "Price List", "primary"),
        ("payment_terms", "Payment Terms Template", "primary"),
    ],
    "Supplier": [
        ("supplier_group", "Supplier Group", "primary"),
        ("default_currency", "Currency", "primary"),
        ("default_price_list", "Price List", "primary"),
        ("payment_terms", "Payment Terms Template", "primary"),
    ],
    "Item": [
        ("item_group", "Item Group", "primary"),
        ("brand", "Brand", "primary"),
        ("stock_uom", "UOM", "primary"),
        ("item_defaults[].company", "Company", "secondary"),
        ("item_defaults[].default_warehouse", "Warehouse", "primary"),
    ],
}


# ── Public API ────────────────────────────────────────────────────────────


def is_profile_eligible(doctype: str) -> bool:
    """Whether ``doctype`` can appear as a profile.key_entities entry."""
    return doctype in PROFILE_WHITELIST


def is_denied(doctype: str) -> bool:
    """Whether ``doctype`` is on the explicit denylist (overrides whitelist)."""
    return doctype in PROFILE_DENYLIST


def extract_self_ref(doctype: str, name: str | None) -> Ref | None:
    """Self-reference for a get_doc/get_list-of-one targeting a master doctype.

    Returns ``None`` for transactional doctypes (denylist) or when ``name`` is
    missing — those calls only contribute their *outgoing* link refs.
    """
    if not name or not isinstance(name, str):
        return None
    if doctype in PROFILE_DENYLIST:
        return None
    if doctype not in PROFILE_WHITELIST:
        return None
    return Ref(doctype=doctype, name=name, field="_self", weight="primary")


def extract_refs(doctype: str, payload: dict[str, Any] | None) -> list[Ref]:
    """Pull every whitelist-eligible link reference out of ``payload``.

    ``payload`` is the dict shape ERPNext uses for both inputs (insert/update)
    and outputs (get_doc). Unknown doctypes return an empty list — silently,
    so a new transactional doctype showing up doesn't crash the observer.
    """
    if not isinstance(payload, dict):
        return []
    rules = EXTRACTION_RULES.get(doctype)
    if not rules:
        return []

    refs: list[Ref] = []
    for path, target_dt, weight in rules:
        # Belt-and-braces: never emit refs to a denylisted target even if a
        # rule entry mistakenly points at one.
        if target_dt in PROFILE_DENYLIST:
            continue
        for value in _walk(payload, _split_path(path)):
            if not value:
                continue
            if not isinstance(value, str):
                # ERPNext link fields are always strings; skip anything else
                # rather than coerce (a numeric id here is a payload bug).
                continue
            refs.append(Ref(doctype=target_dt, name=value, field=path, weight=weight))
    return refs


def refine_payment_entry_party_doctype(payload: dict[str, Any] | None, refs: list[Ref]) -> list[Ref]:
    """Rewrite Payment Entry party refs to match payload['party_type'].

    Payment Entry stores the party doctype dynamically in ``party_type``. The
    static rule in EXTRACTION_RULES treats it as Customer (most common); this
    helper retypes it to Supplier/Employee when the payload says so. Returns a
    new list, never mutates input.
    """
    if not isinstance(payload, dict):
        return refs
    party_type = payload.get("party_type")
    if not party_type or party_type == "Customer":
        return refs
    if party_type not in PROFILE_WHITELIST:
        return refs
    return [Ref(doctype=party_type, name=r.name, field=r.field, weight=r.weight) if r.field == "party" else r for r in refs]


# ── Path walker ───────────────────────────────────────────────────────────


def _split_path(path: str) -> list[str]:
    return path.split(".")


def _walk(node: Any, parts: list[str]) -> Iterable[Any]:
    """Walk ``parts`` against ``node``, expanding ``foo[]`` into list children.

    Yields leaf values. Missing keys / wrong types stop that branch silently —
    callers handle "not present" by producing fewer refs, not by raising.
    """
    if not parts:
        if node is not None:
            yield node
        return
    head, *rest = parts
    if head.endswith("[]"):
        key = head[:-2]
        children = node.get(key) if isinstance(node, dict) else None
        if isinstance(children, list):
            for child in children:
                yield from _walk(child, rest)
        return
    sub = node.get(head) if isinstance(node, dict) else None
    if sub is None:
        return
    yield from _walk(sub, rest)
