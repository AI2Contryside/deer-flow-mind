"""Stock domain: material movements and warehouse-level flows.

ERPNext's Stock Entry is one DocType with a ``purpose`` that determines
the semantics (Material Transfer, Material Issue, Material Receipt,
Manufacture, Send to Subcontractor, etc.). Rather than expose a single
``create_stock_entry(purpose=…)`` that agents have to reason about, this
module gives each purpose its own business-named function.

Key primitives:

  - ``material_transfer``    — Warehouse A → Warehouse B
  - ``material_issue``       — Warehouse A → consumption (no target)
  - ``material_receipt``     — External → Warehouse A
  - ``stock_reconciliation`` — Physical count reconciliation
  - ``get_stock_levels``     — Current bin quantities
"""

from __future__ import annotations

from ..core.client import FrappeClient
from ._common import normalize_items


# ── Movements ─────────────────────────────────────────────────────────

def _items_for_transfer(items: list, source: str | None, target: str | None) -> list[dict]:
    rows: list[dict] = []
    for it in normalize_items(items):
        r = dict(it)
        # Stock Entry Detail's writable per-item price is ``basic_rate``;
        # ``rate`` is a computed column ERPNext recomputes from
        # ``basic_rate`` × ``conversion_factor`` × FX. If the caller
        # passed the more intuitive ``rate`` (matching what selling /
        # buying commands accept), promote it to ``basic_rate`` and mark
        # ``set_basic_rate_manually=1`` so ERPNext won't overwrite it
        # from the Item master's valuation_rate. Pinned to thread
        # 0bc19732-... where the model burned ~5 supersteps reading the
        # CLI source code to discover this mapping.
        if "rate" in r and "basic_rate" not in r:
            r["basic_rate"] = r.pop("rate")
            r.setdefault("set_basic_rate_manually", 1)
        if source: r.setdefault("s_warehouse", source)
        if target: r.setdefault("t_warehouse", target)
        rows.append(r)
    return rows


def material_transfer(
    client: FrappeClient,
    items: list,
    *,
    source_warehouse: str,
    target_warehouse: str,
    company: str | None = None,
    posting_date: str | None = None,
    submit: bool = True,
    extra: dict | None = None,
) -> dict:
    """Transfer stock between two warehouses.

    Business action: move goods from ``source_warehouse`` to
    ``target_warehouse``. This is a Stock Entry with
    ``stock_entry_type='Material Transfer'``.
    """
    doc = {
        "doctype": "Stock Entry",
        "stock_entry_type": "Material Transfer",
        "purpose": "Material Transfer",
        "from_warehouse": source_warehouse,
        "to_warehouse": target_warehouse,
        "items": _items_for_transfer(items, source_warehouse, target_warehouse),
    }
    if company: doc["company"] = company
    if posting_date: doc["posting_date"] = posting_date
    if extra: doc.update(extra)
    return _save_stock_entry(client, doc, submit)


def material_issue(
    client: FrappeClient,
    items: list,
    *,
    source_warehouse: str,
    company: str | None = None,
    posting_date: str | None = None,
    submit: bool = True,
    extra: dict | None = None,
) -> dict:
    """Issue stock out of a warehouse (consumption, write-off, etc.)."""
    doc = {
        "doctype": "Stock Entry",
        "stock_entry_type": "Material Issue",
        "purpose": "Material Issue",
        "from_warehouse": source_warehouse,
        "items": _items_for_transfer(items, source_warehouse, None),
    }
    if company: doc["company"] = company
    if posting_date: doc["posting_date"] = posting_date
    if extra: doc.update(extra)
    return _save_stock_entry(client, doc, submit)


def material_receipt(
    client: FrappeClient,
    items: list,
    *,
    target_warehouse: str,
    company: str | None = None,
    posting_date: str | None = None,
    submit: bool = True,
    extra: dict | None = None,
) -> dict:
    """Receive stock into a warehouse (outside a Purchase Receipt)."""
    doc = {
        "doctype": "Stock Entry",
        "stock_entry_type": "Material Receipt",
        "purpose": "Material Receipt",
        "to_warehouse": target_warehouse,
        "items": _items_for_transfer(items, None, target_warehouse),
    }
    if company: doc["company"] = company
    if posting_date: doc["posting_date"] = posting_date
    if extra: doc.update(extra)
    return _save_stock_entry(client, doc, submit)


def stock_reconciliation(
    client: FrappeClient,
    items: list,
    *,
    company: str | None = None,
    posting_date: str | None = None,
    purpose: str = "Stock Reconciliation",
    submit: bool = True,
    extra: dict | None = None,
) -> dict:
    """Adjust on-hand quantities to match a physical count.

    Each ``items`` row should have: ``item_code``, ``warehouse``,
    ``qty`` (new quantity), optionally ``valuation_rate``.
    """
    rows: list[dict] = []
    for it in items:
        if not isinstance(it, dict):
            raise TypeError(
                "stock_reconciliation items must be dicts with "
                "item_code/warehouse/qty/valuation_rate"
            )
        rows.append(dict(it))
    doc = {
        "doctype": "Stock Reconciliation",
        "purpose": purpose,
        "items": rows,
    }
    if company: doc["company"] = company
    if posting_date: doc["posting_date"] = posting_date
    if extra: doc.update(extra)
    created = client.insert(doc)
    if submit:
        client.submit("Stock Reconciliation", created["name"])
        created = client.get_doc("Stock Reconciliation", created["name"])
    return created


def _save_stock_entry(client: FrappeClient, doc: dict, submit: bool) -> dict:
    created = client.insert(doc)
    if submit:
        client.submit("Stock Entry", created["name"])
        created = client.get_doc("Stock Entry", created["name"])
    return created


# ── Queries ───────────────────────────────────────────────────────────

def get_stock_levels(
    client: FrappeClient,
    *,
    item_code: str | None = None,
    warehouse: str | None = None,
    company: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """Current on-hand stock from the Bin DocType."""
    filters: list = []
    if item_code: filters.append(["item_code", "=", item_code])
    if warehouse: filters.append(["warehouse", "=", warehouse])
    bins = client.get_list(
        "Bin",
        filters=filters or None,
        fields=["item_code", "warehouse", "actual_qty", "reserved_qty",
                "projected_qty", "valuation_rate", "stock_uom"],
        limit=limit,
        order_by="item_code asc",
    )
    if company:
        warehouses = {
            w["name"] for w in client.get_list(
                "Warehouse",
                filters=[["company", "=", company]],
                fields=["name"], limit=500,
            )
        }
        bins = [b for b in bins if b.get("warehouse") in warehouses]
    return bins


def warehouse_summary(client: FrappeClient, warehouse: str) -> dict:
    """Items on hand in a single warehouse, plus totals."""
    levels = get_stock_levels(client, warehouse=warehouse, limit=500)
    total_value = sum(
        float(b.get("actual_qty") or 0) * float(b.get("valuation_rate") or 0)
        for b in levels
    )
    return {
        "warehouse": warehouse,
        "item_count": len([b for b in levels if float(b.get("actual_qty") or 0) > 0]),
        "total_valuation": round(total_value, 2),
        "items": levels,
    }
