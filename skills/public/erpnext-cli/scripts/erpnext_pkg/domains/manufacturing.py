"""Manufacturing domain: BOM → Work Order → Finished Goods.

The standard manufacturing flow is::

  BOM  ──►  Work Order  ──►  Stock Entry (Material Transfer to WIP)
                         ──►  Stock Entry (Manufacture)   [finished goods in]

Everything hinges on the Work Order. Stock Entries against a Work Order
are built via ``erpnext.manufacturing.doctype.work_order.work_order.make_stock_entry``.
"""

from __future__ import annotations

from ..core.client import FrappeClient
from ._common import apply_overrides, submit_if_draft


def create_bom(
    client: FrappeClient,
    item: str,
    items: list[dict],
    *,
    quantity: float = 1,
    is_active: bool = True,
    is_default: bool = True,
    company: str | None = None,
    submit: bool = True,
    extra: dict | None = None,
) -> dict:
    """Create a Bill of Materials for ``item``.

    ``items`` rows need ``item_code`` + ``qty`` (and optionally ``rate``,
    ``source_warehouse``).
    """
    doc = {
        "doctype": "BOM",
        "item": item,
        "quantity": quantity,
        "is_active": 1 if is_active else 0,
        "is_default": 1 if is_default else 0,
        "items": [dict(i) for i in items],
    }
    if company: doc["company"] = company
    if extra: doc.update(extra)
    created = client.insert(doc)
    if submit:
        client.submit("BOM", created["name"])
        created = client.get_doc("BOM", created["name"])
    return created


def create_work_order(
    client: FrappeClient,
    bom: str,
    qty: float,
    *,
    production_item: str | None = None,
    company: str | None = None,
    fg_warehouse: str | None = None,
    wip_warehouse: str | None = None,
    submit: bool = True,
    extra: dict | None = None,
) -> dict:
    """Create a Work Order from a BOM."""
    if production_item is None:
        bom_doc = client.get_doc("BOM", bom)
        production_item = bom_doc.get("item")
    doc = {
        "doctype": "Work Order",
        "bom_no": bom,
        "production_item": production_item,
        "qty": qty,
    }
    if company: doc["company"] = company
    if fg_warehouse: doc["fg_warehouse"] = fg_warehouse
    if wip_warehouse: doc["wip_warehouse"] = wip_warehouse
    if extra: doc.update(extra)
    created = client.insert(doc)
    if submit:
        client.submit("Work Order", created["name"])
        created = client.get_doc("Work Order", created["name"])
    return created


def issue_raw_materials(
    client: FrappeClient,
    work_order: str,
    *,
    qty: float | None = None,
    submit: bool = True,
    overrides: dict | None = None,
) -> dict:
    """Move raw materials from stock → WIP for a Work Order.

    Wraps ``erpnext.manufacturing.doctype.work_order.work_order.make_stock_entry``
    with ``purpose='Material Transfer for Manufacture'``.
    """
    submit_if_draft(client, "Work Order", work_order)
    kwargs = {"work_order_id": work_order, "purpose": "Material Transfer for Manufacture"}
    if qty is not None:
        kwargs["qty"] = qty
    target = client.call_method(
        "erpnext.manufacturing.doctype.work_order.work_order.make_stock_entry",
        **kwargs,
    )
    target = apply_overrides(target, overrides)
    se = client.insert(target)
    if submit:
        client.submit("Stock Entry", se["name"])
        se = client.get_doc("Stock Entry", se["name"])
    return se


def finish_work_order(
    client: FrappeClient,
    work_order: str,
    *,
    qty: float | None = None,
    submit: bool = True,
    overrides: dict | None = None,
) -> dict:
    """Produce finished goods for a Work Order (``purpose='Manufacture'``)."""
    submit_if_draft(client, "Work Order", work_order)
    kwargs = {"work_order_id": work_order, "purpose": "Manufacture"}
    if qty is not None:
        kwargs["qty"] = qty
    target = client.call_method(
        "erpnext.manufacturing.doctype.work_order.work_order.make_stock_entry",
        **kwargs,
    )
    target = apply_overrides(target, overrides)
    se = client.insert(target)
    if submit:
        client.submit("Stock Entry", se["name"])
        se = client.get_doc("Stock Entry", se["name"])
    return se


def make_from_bom(
    client: FrappeClient,
    bom: str,
    qty: float,
    *,
    company: str | None = None,
    fg_warehouse: str | None = None,
    wip_warehouse: str | None = None,
    issue_materials: bool = True,
) -> dict:
    """End-to-end: BOM → Work Order (submitted) → material transfer (optional)
    → manufacture entry. Returns all artifact names."""
    wo = create_work_order(
        client, bom, qty,
        company=company,
        fg_warehouse=fg_warehouse, wip_warehouse=wip_warehouse,
        submit=True,
    )
    transfer = None
    if issue_materials and wip_warehouse:
        transfer = issue_raw_materials(client, wo["name"], qty=qty, submit=True)
    manufacture = finish_work_order(client, wo["name"], qty=qty, submit=True)
    return {
        "workflow": "make_from_bom",
        "bom": bom,
        "work_order": wo["name"],
        "transfer_stock_entry": transfer["name"] if transfer else None,
        "manufacture_stock_entry": manufacture["name"],
        "qty": qty,
    }
