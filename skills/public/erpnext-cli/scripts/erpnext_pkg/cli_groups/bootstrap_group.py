"""``bootstrap`` subcommands: tenant readiness probe.

Pinned after session b987fdbe-... where the agent was asked "把这个订单
中的商品入库" against a tenant whose ERPNext site had **zero** master
data — no Company, no Warehouse, no Item Group, no Supplier. Without a
single quick way to see this, the agent walked a 12-step Q&A wizard
asking the user to spell out every default, then thrashed for 84 steps
when the first ``Company`` insert returned BrokenPipe.

``bootstrap status`` returns one structured payload that says, in one
shot, what masters exist and which workflow chains are ready to call.
The lead_agent prompt now requires this command before any
``selling`` / ``buying`` / ``stock`` / ``manufacturing`` chain — so the
agent picks an appropriate path (chain vs. setup wizard) up front
instead of probing.

Output schema (machine-readable when ``--json``):

    {
      "ok": true,
      "data": {
        "has_company": bool,
        "company_count": int,
        "default_warehouse": str | null,
        "warehouse_count": int,
        "item_group_count": int,
        "item_count": int,
        "supplier_count": int,
        "customer_count": int,
        "ready_for_purchase": bool,    # has Company + Warehouse + Supplier + Item
        "ready_for_sales":    bool,    # has Company + Warehouse + Customer + Item
        "ready_for_stock_in": bool,    # has Company + Warehouse + Item
        "missing": [
          {"doctype": "Company", "reason": "no record found", "blocks": ["all chains"]},
          ...
        ]
      }
    }
"""

from __future__ import annotations

from typing import Any

import click

from ..core.client import FrappeClient
from ..core.errors import ERPNextError
from ._output import emit, emit_error


@click.group()
def group():
    """Tenant readiness probe — one call to see if chains can run."""


# DocTypes whose presence the readiness flags depend on. Order matters
# only for the ``missing`` list ordering (readers eyeball the first one).
_PROBES: list[tuple[str, str]] = [
    ("Company", "company"),
    ("Warehouse", "warehouse"),
    ("Item Group", "item_group"),
    ("Item", "item"),
    ("Supplier", "supplier"),
    ("Customer", "customer"),
]


def _safe_count(client: FrappeClient, doctype: str, max_check: int = 1) -> tuple[int, str | None]:
    """Return (count, error_message). Caps the probe at ``max_check`` records
    because we only need to know if anything exists for the readiness flags;
    counting all rows on a fresh-but-not-empty tenant would be wasteful.

    The status_code, if it surfaces, is preserved as ``error_message`` so the
    agent can tell "tenant has no rows" (count=0, error_message=None) from
    "Frappe is on fire" (count=0, error_message="ServerError 500"). The
    prompt rule is then: if any probe errors, surface the error and stop
    rather than charging into a chain.
    """
    try:
        rows = client.get_list(doctype, limit=max_check, fields=["name"])
        return len(rows), None
    except ERPNextError as e:
        return 0, f"{type(e).__name__} {e.status_code or ''}".strip()
    except Exception as e:  # noqa: BLE001 — defensive at CLI boundary
        return 0, type(e).__name__


def _build_missing(counts: dict[str, int], errors: dict[str, str | None]) -> list[dict[str, Any]]:
    """Compose the ``missing`` array from the probe results. The ``blocks``
    field tells the agent which downstream chains a given gap blocks, so it
    can decide whether to set up that piece or pivot to a different chain.
    """
    blocks_by_doctype = {
        "Company":    ["all chains"],
        "Warehouse":  ["stock-in", "delivery", "purchase-receipt"],
        "Item Group": ["item-creation", "purchase", "sales"],
        "Item":       ["purchase", "sales", "stock-in"],
        "Supplier":   ["purchase", "rfq-to-po"],
        "Customer":   ["sales", "quote-to-cash"],
    }
    out: list[dict[str, Any]] = []
    for doctype, key in _PROBES:
        if errors.get(doctype):
            out.append({
                "doctype": doctype,
                "reason": f"probe failed: {errors[doctype]}",
                "blocks": blocks_by_doctype[doctype],
            })
        elif counts.get(doctype, 0) == 0:
            out.append({
                "doctype": doctype,
                "reason": "no record found",
                "blocks": blocks_by_doctype[doctype],
            })
    return out


def compute_status(session: Any) -> dict[str, Any]:
    """Run all probes against ``session.client()`` and assemble the status
    payload. Pure function — no Click coupling — so tests can drive it
    with a fake session without spinning up a CliRunner.
    """
    client = session.client()

    counts: dict[str, int] = {}
    errors: dict[str, str | None] = {}
    for doctype, _key in _PROBES:
        c, err = _safe_count(client, doctype)
        counts[doctype] = c
        errors[doctype] = err

    # Default warehouse comes from the session context (set via
    # ``session set-context --warehouse``). When absent, we leave it null
    # — the agent should treat that as "needs to be picked or created".
    ctx_section = session.redacted().get("context") or {}
    default_warehouse = ctx_section.get("default_warehouse") or None

    has_company = counts.get("Company", 0) > 0
    has_warehouse = counts.get("Warehouse", 0) > 0
    has_item = counts.get("Item", 0) > 0
    has_supplier = counts.get("Supplier", 0) > 0
    has_customer = counts.get("Customer", 0) > 0

    return {
        "has_company": has_company,
        "company_count": counts.get("Company", 0),
        "default_warehouse": default_warehouse,
        "warehouse_count": counts.get("Warehouse", 0),
        "item_group_count": counts.get("Item Group", 0),
        "item_count": counts.get("Item", 0),
        "supplier_count": counts.get("Supplier", 0),
        "customer_count": counts.get("Customer", 0),
        "ready_for_purchase":  has_company and has_warehouse and has_supplier and has_item,
        "ready_for_sales":     has_company and has_warehouse and has_customer and has_item,
        "ready_for_stock_in":  has_company and has_warehouse and has_item,
        "missing": _build_missing(counts, errors),
        "probe_errors": {dt: err for dt, err in errors.items() if err},
    }


@group.command("status")
@click.pass_context
def status(ctx: click.Context) -> None:
    """One-shot tenant readiness probe.

    Returns whether enough master data exists for purchase / sales /
    stock chains to run. **Call this before any chain command** so the
    agent picks the right next step instead of failing half-way through
    a chain.
    """
    session = ctx.obj["session"]
    try:
        payload = compute_status(session)
    except ERPNextError as e:
        emit_error(ctx, e)
        return
    emit(ctx, payload)
