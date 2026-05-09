"""Tests for ``create_purchase_order`` idempotency.

Pinned against the 2026-05-02 incident where an LLM agent retried a
Purchase Order create against a transient ``SerializationFailure`` and
inserted three duplicate POs in 49 seconds. Combined with a frappe
error-handling bug (now patched separately), each retry leaked an
``idle in transaction (aborted)`` PG backend, accumulating to 92 stale
connections and exhausting ``max_connections=100`` on the dev cluster.

Contract under test:

  - First call inserts and stamps ``remarks`` with a deterministic
    ``po-dedup-key:<hash>`` marker.
  - A second call with byte-identical inputs returns the existing PO and
    does **not** issue a second ``insert``.
  - Item ordering inside the items list does not affect the fingerprint
    (sorted-tuple hashing).
  - Different supplier or different items cleanly produce a new PO.
  - Existing draft + ``submit=True`` triggers a single submit on the
    pre-existing record (no duplicate doc, no double submit).
  - Caller-supplied ``remarks`` are preserved and the dedup marker is
    appended on a new line.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Skill bundle import path (mirrors test_erpnext_cli_stock_rate_mapping.py).
_SKILL_SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "public" / "erpnext-cli" / "scripts"
if str(_SKILL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS))

from erpnext_pkg.domains.buying import _DEDUP_KEY_PREFIX, _po_fingerprint, create_purchase_order  # noqa: E402


class FakeClient:
    """Minimal in-memory FrappeClient stand-in.

    Records every insert/submit and answers ``get_list``/``get_doc`` from
    its own state so tests can assert on call counts and PO contents
    without round-tripping a real Frappe.
    """

    def __init__(self) -> None:
        # (doctype, name) -> doc dict (with ``name`` and ``docstatus`` populated)
        self.docs: dict[tuple[str, str], dict] = {}
        self.inserted: list[dict] = []
        self.submitted: list[tuple[str, str]] = []
        self._counter = 0

    # ── FrappeClient surface used by buying.py ────────────────────────

    def get_list(
        self,
        doctype: str,
        *,
        filters=None,
        fields=None,
        limit: int = 20,
        start: int = 0,
        order_by: str | None = None,
    ) -> list[dict]:
        rows = [(k, v) for k, v in self.docs.items() if k[0] == doctype]
        for f in filters or []:
            field, op, value = f
            if op == "=":
                rows = [(k, v) for k, v in rows if v.get(field) == value]
            elif op == "in":
                rows = [(k, v) for k, v in rows if v.get(field) in value]
            elif op == "like":
                pattern = value.strip("%")
                rows = [(k, v) for k, v in rows if pattern in (v.get(field) or "")]
            else:
                raise NotImplementedError(f"FakeClient.get_list op {op!r}")
        return [{"name": k[1]} for k, _ in rows[:limit]]

    def get_doc(self, doctype: str, name: str) -> dict:
        return dict(self.docs[(doctype, name)])

    def insert(self, doc: dict) -> dict:
        self.inserted.append(dict(doc))
        self._counter += 1
        name = f"{doc['doctype'].replace(' ', '')}-{self._counter:04d}"
        stored = dict(doc)
        stored["name"] = name
        stored.setdefault("docstatus", 0)
        self.docs[(doc["doctype"], name)] = stored
        return {"name": name}

    def submit(self, doctype: str, name: str) -> dict:
        self.submitted.append((doctype, name))
        self.docs[(doctype, name)]["docstatus"] = 1
        return {"name": name}


def _po_args() -> dict:
    """Real-world payload from the 2026-05-02 incident traceback."""
    return dict(
        supplier="Butterfly",
        items=[
            {"item_code": "BT2506-13-2000", "qty": 1040, "rate": 5.5, "warehouse": "Stores - TA1G"},
            {"item_code": "BT2506-10-1500", "qty": 800, "rate": 14.0, "warehouse": "Stores - TA1G"},
        ],
        company="test_a1_group",
        schedule_date="2026-05-02",
        currency="CNY",
    )


# ── Tests ─────────────────────────────────────────────────────────────


def test_first_call_inserts_with_dedup_marker_in_remarks():
    client = FakeClient()

    result = create_purchase_order(client, **_po_args())

    assert len(client.inserted) == 1
    inserted = client.inserted[0]
    assert _DEDUP_KEY_PREFIX in inserted["remarks"]
    # submit=True default → docstatus advances to 1
    assert result["docstatus"] == 1
    assert len(client.submitted) == 1


def test_identical_second_call_returns_existing_without_insert():
    """The bug we're closing: agent retry must not produce a duplicate PO."""
    client = FakeClient()
    first = create_purchase_order(client, **_po_args())

    second = create_purchase_order(client, **_po_args())

    assert len(client.inserted) == 1, "second identical call must be a no-op insert-wise"
    assert second["name"] == first["name"]


def test_three_retries_only_insert_once():
    """Mirrors the 49-second triple-retry pattern from the incident."""
    client = FakeClient()

    create_purchase_order(client, **_po_args())
    create_purchase_order(client, **_po_args())
    create_purchase_order(client, **_po_args())

    assert len(client.inserted) == 1
    assert len(client.submitted) == 1, "must not double-submit"


def test_item_order_does_not_change_fingerprint():
    """Hashing sorts rows so a re-ordered items list still dedupes."""
    client = FakeClient()
    args = _po_args()
    create_purchase_order(client, **args)

    args["items"] = list(reversed(args["items"]))
    create_purchase_order(client, **args)

    assert len(client.inserted) == 1


def test_different_items_produces_new_po():
    client = FakeClient()
    args = _po_args()
    create_purchase_order(client, **args)

    args["items"] = [{"item_code": "DIFFERENT-SKU", "qty": 1, "rate": 99}]
    create_purchase_order(client, **args)

    assert len(client.inserted) == 2


def test_different_supplier_produces_new_po():
    client = FakeClient()
    args = _po_args()
    create_purchase_order(client, **args)

    args["supplier"] = "RUI LEE"
    create_purchase_order(client, **args)

    assert len(client.inserted) == 2


def test_different_schedule_date_produces_new_po():
    client = FakeClient()
    args = _po_args()
    create_purchase_order(client, **args)

    args["schedule_date"] = "2026-05-03"
    create_purchase_order(client, **args)

    assert len(client.inserted) == 2


def test_existing_draft_with_submit_true_gets_submitted_once():
    """First call with submit=False leaves a draft; second call with
    submit=True must promote that draft, not insert a fresh one."""
    client = FakeClient()
    create_purchase_order(client, **_po_args(), submit=False)
    assert client.submitted == []

    create_purchase_order(client, **_po_args(), submit=True)

    assert len(client.inserted) == 1
    assert len(client.submitted) == 1


def test_existing_submitted_does_not_resubmit():
    client = FakeClient()
    create_purchase_order(client, **_po_args())  # submit=True default → docstatus=1

    create_purchase_order(client, **_po_args())

    assert len(client.submitted) == 1, "must not re-submit a submitted PO"


def test_caller_remarks_preserved_with_marker_appended():
    client = FakeClient()

    create_purchase_order(
        client, **_po_args(), extra={"remarks": "Q4 batch order from agent"}
    )

    inserted = client.inserted[0]
    assert "Q4 batch order from agent" in inserted["remarks"]
    assert _DEDUP_KEY_PREFIX in inserted["remarks"]
    # Caller content stays first; marker is appended on a new line.
    assert inserted["remarks"].startswith("Q4 batch order from agent")


def test_fingerprint_is_deterministic():
    items = [{"item_code": "X", "qty": 1, "rate": 10}]
    fp_a = _po_fingerprint("Sup", items, "2026-01-01")
    fp_b = _po_fingerprint("Sup", items, "2026-01-01")
    assert fp_a == fp_b
    assert len(fp_a) == 16  # 16 hex chars from sha1 prefix


def test_fingerprint_changes_when_qty_changes():
    """Catch the silent bug: dedup must not collapse a different qty
    onto the same PO. Without per-row qty in the hash a typo'd retry
    would be silently merged into the wrong amount."""
    fp_a = _po_fingerprint("Sup", [{"item_code": "X", "qty": 1, "rate": 10}], "2026-01-01")
    fp_b = _po_fingerprint("Sup", [{"item_code": "X", "qty": 2, "rate": 10}], "2026-01-01")
    assert fp_a != fp_b
