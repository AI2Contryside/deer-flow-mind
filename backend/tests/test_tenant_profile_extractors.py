"""Unit tests for tenant_profile.extractors (M1).

Covers the link-field extraction rules from TENANT_PROFILE_DESIGN.md
Appendix A. Each happy-path test uses a payload that looks like a real
ERPNext doc dict; defensive tests verify the walker tolerates missing
keys, wrong types, and unknown doctypes silently.
"""

from __future__ import annotations

from src.agents.tenant_profile.extractors import (
    PROFILE_DENYLIST,
    PROFILE_WHITELIST,
    Ref,
    extract_refs,
    extract_self_ref,
    is_denied,
    is_profile_eligible,
    refine_payment_entry_party_doctype,
)

# ── extract_refs: happy paths ─────────────────────────────────────────────


def test_extract_refs_sales_order_pulls_customer_items_warehouses() -> None:
    payload = {
        "customer": "FOOCORP-001",
        "customer_group": "OEM-KR",
        "territory": "Korea",
        "currency": "USD",
        "selling_price_list": "Standard Selling",
        "taxes_and_charges": "VAT 13% — China",
        "payment_terms_template": "Net 30",
        "items": [
            {"item_code": "VLV-001", "item_group": "Valves", "uom": "Nos", "warehouse": "Stores - ACME", "cost_center": "Main - ACME"},
            {"item_code": "VLV-002", "uom": "Nos", "warehouse": "Stores - ACME"},
        ],
        "taxes": [{"account_head": "VAT Output - ACME"}],
        "sales_team": [{"sales_person": "张三"}],
    }
    refs = extract_refs("Sales Order", payload)

    by_doctype = {(r.doctype, r.name) for r in refs}
    assert ("Customer", "FOOCORP-001") in by_doctype
    assert ("Customer Group", "OEM-KR") in by_doctype
    assert ("Territory", "Korea") in by_doctype
    assert ("Currency", "USD") in by_doctype
    assert ("Price List", "Standard Selling") in by_doctype
    assert ("Sales Taxes and Charges Template", "VAT 13% — China") in by_doctype
    assert ("Payment Terms Template", "Net 30") in by_doctype
    assert ("Item", "VLV-001") in by_doctype
    assert ("Item", "VLV-002") in by_doctype
    assert ("Item Group", "Valves") in by_doctype
    assert ("UOM", "Nos") in by_doctype
    assert ("Warehouse", "Stores - ACME") in by_doctype
    assert ("Cost Center", "Main - ACME") in by_doctype
    assert ("Account", "VAT Output - ACME") in by_doctype
    assert ("Sales Person", "张三") in by_doctype


def test_extract_refs_purchase_order_pulls_supplier_and_buying_price_list() -> None:
    payload = {
        "supplier": "SUPP-001",
        "supplier_group": "Domestic",
        "buying_price_list": "Standard Buying",
        "items": [{"item_code": "RAW-1", "uom": "Kg", "warehouse": "Raw - ACME"}],
    }
    refs = {(r.doctype, r.name) for r in extract_refs("Purchase Order", payload)}
    assert ("Supplier", "SUPP-001") in refs
    assert ("Supplier Group", "Domestic") in refs
    assert ("Price List", "Standard Buying") in refs
    assert ("Item", "RAW-1") in refs
    assert ("Warehouse", "Raw - ACME") in refs


def test_extract_refs_payment_entry_dynamic_party_default_customer() -> None:
    payload = {"party": "FOOCORP-001", "paid_from": "Debtors - ACME", "paid_to": "Bank - ACME"}
    refs = extract_refs("Payment Entry", payload)
    by_doctype = {(r.doctype, r.name) for r in refs}
    # Default static rule treats party as Customer.
    assert ("Customer", "FOOCORP-001") in by_doctype
    assert ("Account", "Debtors - ACME") in by_doctype
    assert ("Account", "Bank - ACME") in by_doctype


def test_refine_payment_entry_party_to_supplier() -> None:
    payload = {"party_type": "Supplier", "party": "SUPP-001", "paid_from": "Bank - ACME", "paid_to": "Creditors - ACME"}
    refs = extract_refs("Payment Entry", payload)
    refined = refine_payment_entry_party_doctype(payload, refs)
    by_doctype = {(r.doctype, r.name) for r in refined}
    assert ("Supplier", "SUPP-001") in by_doctype
    assert ("Customer", "SUPP-001") not in by_doctype


def test_refine_payment_entry_party_unknown_type_passes_through() -> None:
    payload = {"party_type": "Random Doctype", "party": "X-1"}
    refs = [Ref(doctype="Customer", name="X-1", field="party", weight="primary")]
    out = refine_payment_entry_party_doctype(payload, refs)
    assert out == refs  # unchanged when party_type isn't whitelist


# ── extract_refs: defensive paths ─────────────────────────────────────────


def test_extract_refs_unknown_doctype_returns_empty() -> None:
    assert extract_refs("Made Up Doctype", {"customer": "X"}) == []


def test_extract_refs_none_payload_returns_empty() -> None:
    assert extract_refs("Sales Order", None) == []


def test_extract_refs_non_dict_payload_returns_empty() -> None:
    assert extract_refs("Sales Order", "not a dict") == []  # type: ignore[arg-type]


def test_extract_refs_missing_optional_fields_does_not_raise() -> None:
    # Only the customer field is set; everything else is absent.
    refs = extract_refs("Sales Order", {"customer": "FOOCORP-001"})
    assert any(r.doctype == "Customer" and r.name == "FOOCORP-001" for r in refs)
    # Children list missing → no item/uom/warehouse refs, and no exception.
    assert not any(r.doctype == "Item" for r in refs)


def test_extract_refs_skips_non_string_values() -> None:
    # ERPNext link fields are always strings; an integer here is a payload bug —
    # we must not accidentally coerce it (an item id of 0 would become "0").
    payload = {"customer": 12345, "items": [{"item_code": None, "warehouse": ""}]}
    refs = extract_refs("Sales Order", payload)
    assert refs == []


def test_extract_refs_handles_empty_string_link_value() -> None:
    payload = {"customer": "", "items": [{"item_code": "VLV-001"}]}
    refs = extract_refs("Sales Order", payload)
    by_doctype = {(r.doctype, r.name) for r in refs}
    assert ("Customer", "") not in by_doctype
    assert ("Item", "VLV-001") in by_doctype


def test_extract_refs_nested_children_walk_correctly() -> None:
    payload = {
        "items": [
            {"item_code": "A", "warehouse": "W1"},
            {"item_code": "B", "warehouse": "W2"},
            {"item_code": "C"},  # missing warehouse
        ]
    }
    refs = extract_refs("Sales Order", payload)
    items = sorted(r.name for r in refs if r.doctype == "Item")
    warehouses = sorted(r.name for r in refs if r.doctype == "Warehouse")
    assert items == ["A", "B", "C"]
    assert warehouses == ["W1", "W2"]


# ── extract_self_ref ─────────────────────────────────────────────────────


def test_extract_self_ref_for_whitelist_returns_primary() -> None:
    ref = extract_self_ref("Customer", "FOOCORP-001")
    assert ref == Ref(doctype="Customer", name="FOOCORP-001", field="_self", weight="primary")


def test_extract_self_ref_for_denylist_returns_none() -> None:
    assert extract_self_ref("Sales Order", "SO-1") is None


def test_extract_self_ref_for_unknown_doctype_returns_none() -> None:
    assert extract_self_ref("Made Up Doctype", "X") is None


def test_extract_self_ref_with_empty_or_none_name_returns_none() -> None:
    assert extract_self_ref("Customer", None) is None
    assert extract_self_ref("Customer", "") is None


# ── Categorisation predicates ─────────────────────────────────────────────


def test_is_profile_eligible_matches_whitelist() -> None:
    assert is_profile_eligible("Customer")
    assert not is_profile_eligible("Sales Order")
    assert not is_profile_eligible("Made Up")


def test_is_denied_matches_denylist() -> None:
    assert is_denied("Sales Order")
    assert not is_denied("Customer")


def test_whitelist_and_denylist_are_disjoint() -> None:
    # A doctype that's both eligible AND denied would be a contradiction.
    assert not (PROFILE_WHITELIST & PROFILE_DENYLIST)
