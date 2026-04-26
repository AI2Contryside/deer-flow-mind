"""Unit tests for tenant_profile.facts (M3) — bootstrap with injected client."""

from __future__ import annotations

from typing import Any

import pytest

from src.agents.tenant_profile.erpnext_client import NotConfiguredError, StubErpnextClient
from src.agents.tenant_profile.facts import bootstrap_facts, facts_to_dict


class FakeClient:
    """Deterministic in-memory ERPNext fake driven by a docs+lists fixture."""

    def __init__(
        self,
        docs: dict[tuple[str, str], dict[str, Any]] | None = None,
        lists: dict[str, list[dict[str, Any]]] | None = None,
        raise_on: set[str] | None = None,
    ) -> None:
        self.docs = docs or {}
        self.lists = lists or {}
        self.raise_on = raise_on or set()
        self.calls: list[tuple[str, ...]] = []

    def get_doc(self, doctype: str, name: str) -> dict[str, Any] | None:
        self.calls.append(("get_doc", doctype, name))
        if doctype in self.raise_on:
            raise RuntimeError(f"synthetic failure for {doctype}")
        return self.docs.get((doctype, name))

    def get_list(
        self,
        doctype: str,
        *,
        filters: dict[str, Any] | None = None,
        fields: list[str] | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        self.calls.append(("get_list", doctype))
        if doctype in self.raise_on:
            raise RuntimeError(f"synthetic failure for {doctype}")
        rows = self.lists.get(doctype, [])
        if filters:
            rows = [r for r in rows if all(r.get(k) == v for k, v in filters.items())]
        return rows[:limit]


def _seed_acme() -> FakeClient:
    """A typical multi-company tenant with a current user + settings."""
    return FakeClient(
        docs={
            ("Global Defaults", "Global Defaults"): {
                "default_company": "ACME Industrial Ltd",
                "default_currency": "USD",
                "country": "China",
            },
            ("Selling Settings", "Selling Settings"): {"so_required": 1, "dn_required": 0},
            ("Stock Settings", "Stock Settings"): {"valuation_method": "FIFO", "default_warehouse": "Stores - ACME"},
            ("Accounts Settings", "Accounts Settings"): {"auto_accounting_for_stock": 1},
            ("Buying Settings", "Buying Settings"): {"po_required": 1},
            ("User", "alice@acme.com"): {
                "email": "alice@acme.com",
                "full_name": "Alice Wong",
                "language": "zh",
                "time_zone": "Asia/Shanghai",
                "roles": [{"role": "Sales Manager"}, {"role": "All"}, {"role": "Item Manager"}],
            },
        },
        lists={
            "Company": [
                {"name": "ACME Industrial Ltd", "abbr": "ACME", "default_currency": "USD", "country": "China"},
                {"name": "ACME HK Ltd", "abbr": "AHK", "default_currency": "HKD", "country": "Hong Kong"},
            ],
            "Fiscal Year": [
                {"name": "2026", "year_start_date": "2026-04-01", "year_end_date": "2027-03-31"},
                {"name": "2025", "year_start_date": "2025-04-01", "year_end_date": "2026-03-31"},
            ],
            "Employee": [
                {
                    "name": "EMP-001",
                    "employee_name": "Alice Wong",
                    "department": "Sales",
                    "company": "ACME Industrial Ltd",
                    "user_id": "alice@acme.com",
                }
            ],
        },
    )


# ── Happy paths ───────────────────────────────────────────────────────────


def test_bootstrap_facts_full_acme_tenant() -> None:
    bundle = bootstrap_facts(user_email="alice@acme.com", timeout_seconds=5.0, client=_seed_acme())

    assert bundle.primary_company is not None
    assert bundle.primary_company["name"] == "ACME Industrial Ltd"
    assert len(bundle.additional_companies) == 1
    assert bundle.additional_companies[0]["name"] == "ACME HK Ltd"

    assert bundle.fiscal_year is not None
    assert bundle.fiscal_year["name"] == "2026"  # picked by latest year_end_date

    assert bundle.selling_settings == {"so_required": 1, "dn_required": 0}
    assert bundle.stock_settings == {"valuation_method": "FIFO", "default_warehouse": "Stores - ACME"}
    assert bundle.accounts_settings == {"auto_accounting_for_stock": 1}
    assert bundle.buying_settings == {"po_required": 1}

    assert bundle.current_user is not None and bundle.current_user["full_name"] == "Alice Wong"
    # System roles ("All") filtered; business roles kept.
    assert "All" not in bundle.user_roles
    assert "Sales Manager" in bundle.user_roles
    assert "Item Manager" in bundle.user_roles

    assert bundle.current_employee is not None
    assert bundle.current_employee["name"] == "EMP-001"


def test_bootstrap_facts_picks_first_company_when_no_default(_acme: FakeClient | None = None) -> None:
    cli = _seed_acme()
    cli.docs[("Global Defaults", "Global Defaults")] = {}  # no default_company
    bundle = bootstrap_facts(user_email="alice@acme.com", timeout_seconds=5.0, client=cli)

    assert bundle.primary_company is not None
    # Falls back to the first listed company.
    assert bundle.primary_company["name"] == "ACME Industrial Ltd"


def test_bootstrap_facts_without_user_email_skips_user_lookups() -> None:
    cli = _seed_acme()
    bundle = bootstrap_facts(user_email=None, timeout_seconds=5.0, client=cli)
    assert bundle.current_user is None
    assert bundle.current_employee is None
    assert bundle.user_roles == []
    assert all(c[0] != "get_doc" or c[1] != "User" for c in cli.calls)


# ── Defensive paths ──────────────────────────────────────────────────────


def test_bootstrap_facts_with_stub_client_returns_empty_with_warnings() -> None:
    bundle = bootstrap_facts(user_email="alice@acme.com", timeout_seconds=1.0, client=StubErpnextClient())
    assert bundle.primary_company is None
    assert bundle.additional_companies == []
    assert bundle.current_user is None
    # Each call should have logged a "not configured" warning, not raised.
    assert any("not configured" in w for w in bundle.warnings)


def test_bootstrap_facts_swallows_per_doctype_errors() -> None:
    cli = _seed_acme()
    cli.raise_on = {"Stock Settings"}  # only this doctype throws
    bundle = bootstrap_facts(user_email="alice@acme.com", timeout_seconds=5.0, client=cli)
    assert bundle.stock_settings is None
    assert any("Stock Settings" in w for w in bundle.warnings)
    # Other facts still get fetched.
    assert bundle.selling_settings == {"so_required": 1, "dn_required": 0}


def test_bootstrap_facts_zero_timeout_returns_minimal_bundle() -> None:
    bundle = bootstrap_facts(user_email="alice@acme.com", timeout_seconds=0.0, client=_seed_acme())
    # Maybe one fetch sneaks through before the deadline check, but most are skipped.
    assert bundle.duration_ms >= 0
    # Definitely warnings for the skipped items.
    assert any("timeout" in w for w in bundle.warnings)


def test_facts_to_dict_shape_matches_prompt_contract() -> None:
    bundle = bootstrap_facts(user_email="alice@acme.com", timeout_seconds=5.0, client=_seed_acme())
    d = facts_to_dict(bundle)

    assert "primary_company" in d and d["primary_company"] is not None
    assert "additional_companies" in d
    assert "fiscal_year" in d
    assert "settings" in d
    assert set(d["settings"]) == {"selling", "stock", "accounts", "buying", "global_defaults"}
    assert d["current_user"] is not None and "roles" in d["current_user"]
    assert "warnings" in d


def test_stub_client_raises_not_configured() -> None:
    stub = StubErpnextClient()
    with pytest.raises(NotConfiguredError):
        stub.get_doc("Customer", "X")
    with pytest.raises(NotConfiguredError):
        stub.get_list("Customer")
