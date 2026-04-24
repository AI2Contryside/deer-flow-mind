"""Shared helpers used by every domain module.

The goal: express business flows as small, composable steps so the
domain modules read like the business description, not like REST plumbing.
"""

from __future__ import annotations

from typing import Any

from ..core.client import FrappeClient


def normalize_items(items: list[dict] | list[tuple]) -> list[dict]:
    """Accept a loose ``items`` shape and return ERPNext-ready child rows.

    Supports:
      - ``[{"item_code": "X", "qty": 2, "rate": 100}]``  (pass-through)
      - ``[("X", 2), ("Y", 1, 50)]``                     (tuple form)
    """
    normalized: list[dict] = []
    for item in items:
        if isinstance(item, dict):
            normalized.append(dict(item))
            continue
        if isinstance(item, (list, tuple)):
            if len(item) == 2:
                code, qty = item
                normalized.append({"item_code": code, "qty": qty})
            elif len(item) == 3:
                code, qty, rate = item
                normalized.append({"item_code": code, "qty": qty, "rate": rate})
            else:
                raise ValueError(f"Unsupported item tuple length: {item!r}")
            continue
        raise TypeError(f"items must be dict/tuple/list, got {type(item)}")
    return normalized


def submit_if_draft(client: FrappeClient, doctype: str, name: str) -> dict:
    """Submit a document if it's a draft (docstatus 0). Idempotent."""
    doc = client.get_doc(doctype, name)
    if int(doc.get("docstatus", 0)) == 0:
        client.submit(doctype, name)
        doc = client.get_doc(doctype, name)
    return doc


def save_doc_from_dict(client: FrappeClient, doc: dict, submit: bool = False) -> dict:
    """Insert a doc, optionally submit, return the final stored version."""
    saved = client.insert(doc)
    if submit:
        client.submit(doc["doctype"], saved["name"])
        saved = client.get_doc(doc["doctype"], saved["name"])
    return saved


def fetch_target(client: FrappeClient, make_result: dict) -> dict:
    """``make_*`` methods return an unsaved target doc. Insert and return it.

    Frappe's ``make_*`` builders return a populated target DocType as a
    *dict* — it hasn't been persisted yet. This helper inserts it.
    """
    if not isinstance(make_result, dict):
        raise TypeError(
            f"make_*() returned {type(make_result).__name__}, expected dict"
        )
    # Some make_* methods wrap in {'name': '...', ...} already-inserted form;
    # others return a plain document dict. Disambiguate by checking doctype.
    if "doctype" not in make_result:
        raise ValueError(
            "make_*() did not return a document dict (missing 'doctype')"
        )
    return client.insert(make_result)


def apply_overrides(doc: dict, overrides: dict | None) -> dict:
    """Return a copy of ``doc`` with ``overrides`` merged on top.

    Used to let callers tweak a ``make_*`` result (e.g., set a specific
    warehouse on a Delivery Note) before inserting.
    """
    if not overrides:
        return doc
    out = {**doc}
    for k, v in overrides.items():
        out[k] = v
    return out


def ensure_customer(
    client: FrappeClient,
    customer_name: str,
    *,
    customer_group: str = "All Customer Groups",
    territory: str = "All Territories",
    customer_type: str = "Company",
) -> str:
    """Return an existing Customer's name or create a new one. Idempotent."""
    if client.exists("Customer", customer_name):
        return customer_name
    doc = {
        "doctype": "Customer",
        "customer_name": customer_name,
        "customer_group": customer_group,
        "territory": territory,
        "customer_type": customer_type,
    }
    created = client.insert(doc)
    return created["name"]


def ensure_supplier(
    client: FrappeClient,
    supplier_name: str,
    *,
    supplier_group: str = "All Supplier Groups",
    supplier_type: str = "Company",
) -> str:
    if client.exists("Supplier", supplier_name):
        return supplier_name
    doc = {
        "doctype": "Supplier",
        "supplier_name": supplier_name,
        "supplier_group": supplier_group,
        "supplier_type": supplier_type,
    }
    created = client.insert(doc)
    return created["name"]


def ensure_item(
    client: FrappeClient,
    item_code: str,
    *,
    item_name: str | None = None,
    item_group: str = "All Item Groups",
    stock_uom: str = "Nos",
    is_stock_item: bool = True,
) -> str:
    if client.exists("Item", item_code):
        return item_code
    doc = {
        "doctype": "Item",
        "item_code": item_code,
        "item_name": item_name or item_code,
        "item_group": item_group,
        "stock_uom": stock_uom,
        "is_stock_item": 1 if is_stock_item else 0,
    }
    created = client.insert(doc)
    return created["name"]
