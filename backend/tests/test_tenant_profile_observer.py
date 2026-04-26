"""Unit tests for tenant_profile.observer (M1).

Covers the contract documented in TENANT_PROFILE_DESIGN.md §4.3:

* Read-side calls (get_doc / get_list precise) record self refs.
* get_list with > 5 rows is a browse — drop the event entirely.
* Write-side calls record link refs from request payload.
* Per-thread dedupe prevents tool-loop inflation.
* All failure modes return False without raising (best-effort contract).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.agents.tenant_profile import log as log_module
from src.agents.tenant_profile import observer


@pytest.fixture(autouse=True)
def _isolate_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_paths = MagicMock()
    fake_paths.base_dir = tmp_path
    monkeypatch.setattr("src.agents.tenant_profile.log.get_paths", lambda: fake_paths)
    log_module.reset_locks_for_tests()
    observer.reset_seen_for_tests()


# ── Read-side ──────────────────────────────────────────────────────────────


def test_get_doc_on_master_records_self_ref() -> None:
    ok = observer.record_event(
        tenant_id="acme",
        op="get_doc",
        doctype="Customer",
        name="FOOCORP-001",
        thread_id="t1",
    )
    assert ok is True

    events = log_module.read_events("acme")
    assert len(events) == 1
    e = events[0]
    assert e["doctype"] == "Customer"
    assert e["name"] == "FOOCORP-001"
    assert {"doctype": "Customer", "name": "FOOCORP-001", "field": "_self", "weight": "primary"} in e["extracted_refs"]


def test_get_list_precise_records_each_row_as_self_ref() -> None:
    response = [
        {"name": "FOOCORP-001"},
        {"name": "BAR-002"},
        {"name": "BAZ-003"},
    ]
    ok = observer.record_event(
        tenant_id="acme",
        op="get_list",
        doctype="Customer",
        response=response,
        thread_id="t1",
    )
    assert ok is True

    events = log_module.read_events("acme")
    assert len(events) == 1
    names = {ref["name"] for ref in events[0]["extracted_refs"]}
    assert names == {"FOOCORP-001", "BAR-002", "BAZ-003"}


def test_get_list_with_many_rows_is_browse_and_dropped() -> None:
    response = [{"name": f"CUST-{i}"} for i in range(50)]
    ok = observer.record_event(
        tenant_id="acme",
        op="get_list",
        doctype="Customer",
        response=response,
        thread_id="t1",
    )
    assert ok is False
    assert log_module.count_events("acme") == 0


def test_get_doc_on_transactional_doctype_records_link_refs_only() -> None:
    # Sales Order is denylisted, so no self ref. But its outgoing link refs
    # (Customer, Item, ...) should still flow through.
    response = {
        "name": "SAL-ORD-2026-00001",
        "customer": "FOOCORP-001",
        "items": [{"item_code": "VLV-001", "warehouse": "Stores - ACME", "uom": "Nos"}],
    }
    ok = observer.record_event(
        tenant_id="acme",
        op="get_doc",
        doctype="Sales Order",
        name="SAL-ORD-2026-00001",
        response=response,
        thread_id="t1",
    )
    assert ok is True

    events = log_module.read_events("acme")
    refs = events[0]["extracted_refs"]
    by_doctype = {(r["doctype"], r["name"]) for r in refs}
    assert ("Sales Order", "SAL-ORD-2026-00001") not in by_doctype  # denylisted
    assert ("Customer", "FOOCORP-001") in by_doctype
    assert ("Item", "VLV-001") in by_doctype
    assert ("Warehouse", "Stores - ACME") in by_doctype


# ── Write-side ─────────────────────────────────────────────────────────────


def test_insert_records_link_refs_from_payload() -> None:
    payload = {
        "customer": "FOOCORP-001",
        "items": [{"item_code": "VLV-001", "warehouse": "Stores - ACME", "uom": "Nos"}],
        "selling_price_list": "Standard Selling",
    }
    ok = observer.record_event(
        tenant_id="acme",
        op="insert",
        doctype="Sales Order",
        payload=payload,
        thread_id="t1",
    )
    assert ok is True

    events = log_module.read_events("acme")
    by_doctype = {(r["doctype"], r["name"]) for r in events[0]["extracted_refs"]}
    assert ("Customer", "FOOCORP-001") in by_doctype
    assert ("Item", "VLV-001") in by_doctype
    assert ("Price List", "Standard Selling") in by_doctype


def test_payment_entry_supplier_party_refined_at_record_time() -> None:
    payload = {"party_type": "Supplier", "party": "SUPP-001", "paid_from": "Bank - ACME", "paid_to": "Creditors - ACME"}
    ok = observer.record_event(
        tenant_id="acme",
        op="insert",
        doctype="Payment Entry",
        payload=payload,
        thread_id="t1",
    )
    assert ok is True

    refs = log_module.read_events("acme")[0]["extracted_refs"]
    by_doctype = {(r["doctype"], r["name"]) for r in refs}
    assert ("Supplier", "SUPP-001") in by_doctype
    assert ("Customer", "SUPP-001") not in by_doctype


# ── Dedupe ─────────────────────────────────────────────────────────────────


def test_repeated_get_doc_in_same_thread_dedupes_to_zero() -> None:
    args = dict(tenant_id="acme", op="get_doc", doctype="Customer", name="FOOCORP-001", thread_id="t1")

    # First call records.
    assert observer.record_event(**args) is True
    # Second identical call — every ref is dedup'd, so the event is dropped.
    assert observer.record_event(**args) is False

    assert log_module.count_events("acme") == 1


def test_dedupe_is_per_thread_not_global() -> None:
    args = dict(tenant_id="acme", op="get_doc", doctype="Customer", name="FOOCORP-001")
    assert observer.record_event(thread_id="t1", **args) is True
    # Different thread → fresh dedupe state, records again.
    assert observer.record_event(thread_id="t2", **args) is True
    assert log_module.count_events("acme") == 2


def test_dedupe_does_not_block_writes_when_partially_overlapping() -> None:
    # First call records ref to FOOCORP-001 (Customer self).
    assert observer.record_event(tenant_id="acme", op="get_doc", doctype="Customer", name="FOOCORP-001", thread_id="t1")
    # Then an insert references FOOCORP-001 again (deduped) plus a new VLV-001
    # (not seen). The writes path retains the event because it has new info.
    payload = {
        "customer": "FOOCORP-001",
        "items": [{"item_code": "VLV-001", "warehouse": "Stores - ACME", "uom": "Nos"}],
    }
    assert observer.record_event(tenant_id="acme", op="insert", doctype="Sales Order", payload=payload, thread_id="t1")

    events = log_module.read_events("acme")
    assert len(events) == 2
    # Second event should NOT contain the deduped Customer ref.
    insert_refs = {(r["doctype"], r["name"]) for r in events[1]["extracted_refs"]}
    assert ("Customer", "FOOCORP-001") not in insert_refs
    assert ("Item", "VLV-001") in insert_refs


# ── Defensive: bad inputs never raise ─────────────────────────────────────


def test_empty_tenant_id_returns_false_silently() -> None:
    assert observer.record_event(tenant_id="", op="get_doc", doctype="Customer", name="X") is False


def test_invalid_tenant_id_path_traversal_does_not_create_files(tmp_path: Path) -> None:
    assert observer.record_event(tenant_id="../escape", op="get_doc", doctype="Customer", name="X") is False
    # Nothing should have been created outside tmp_path/tenant_profile.
    assert not (tmp_path.parent / "escape").exists()


def test_unknown_op_returns_false() -> None:
    # The Op type narrows valid values but observers might be fed bad strings
    # by buggy callers; we should fail closed.
    assert observer.record_event(tenant_id="acme", op="weird-op", doctype="Customer", name="X") is False  # type: ignore[arg-type]


def test_unknown_doctype_with_no_extraction_rules_records_only_self_when_eligible() -> None:
    # Brand has no EXTRACTION_RULES mapping but is on the whitelist — get_doc
    # should still record the self-ref.
    ok = observer.record_event(tenant_id="acme", op="get_doc", doctype="Brand", name="ACME", thread_id="t1")
    assert ok is True
    refs = log_module.read_events("acme")[0]["extracted_refs"]
    assert refs == [{"doctype": "Brand", "name": "ACME", "field": "_self", "weight": "primary"}]


def test_event_shape_includes_all_optional_fields() -> None:
    now = datetime(2026, 4, 26, 10, 30, 0, tzinfo=UTC)
    observer.record_event(
        tenant_id="acme",
        op="call_method",
        doctype="Sales Order",
        name="SAL-ORD-1",
        payload={"customer": "FOOCORP-001"},
        skill="erpnext-cli/selling/order-to-cash",
        method="erpnext.selling.doctype.sales_order.sales_order.make_sales_invoice",
        thread_id="t1",
        now=now,
    )
    e = log_module.read_events("acme")[0]
    assert e["ts"] == "2026-04-26T10:30:00Z"
    assert e["op"] == "call_method"
    assert e["doctype"] == "Sales Order"
    assert e["name"] == "SAL-ORD-1"
    assert e["skill"] == "erpnext-cli/selling/order-to-cash"
    assert e["method"].endswith("make_sales_invoice")
    assert e["thread_id"] == "t1"


def test_log_write_failure_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(observer.log_module, "append_event", lambda *_a, **_k: False)
    assert observer.record_event(tenant_id="acme", op="get_doc", doctype="Customer", name="FOOCORP-001", thread_id="t1") is False


def test_observer_swallows_unexpected_exceptions(monkeypatch: pytest.MonkeyPatch) -> None:
    """Last-resort safety net: even a programmer error inside the recorder
    must not crash the caller's main flow."""

    def boom(*_a, **_kw) -> list:
        raise RuntimeError("synthetic crash inside extractor")

    monkeypatch.setattr(observer, "_collect_refs", boom)
    assert observer.record_event(tenant_id="acme", op="get_doc", doctype="Customer", name="X") is False
