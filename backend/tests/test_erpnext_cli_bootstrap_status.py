"""Tests for erpnext-cli bootstrap_group.status.

Pinned against session b987fdbe-... where the agent had no quick way to
see "tenant is empty" and walked the user through 12 turns of Q&A. The
``bootstrap status`` command must summarize tenant readiness in one shot
so the agent can decide setup-vs-chain immediately.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Skill bundle import path (same trick used by test_erpnext_cli_errors_redaction).
_SKILL_SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "public" / "erpnext-cli" / "scripts"
if str(_SKILL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS))

from erpnext_pkg.cli_groups.bootstrap_group import (  # noqa: E402
    _PROBES,
    _build_missing,
    _safe_count,
)
from erpnext_pkg.core.errors import AuthError, ServerError  # noqa: E402

# ---------- _safe_count ----------------------------------------------------


@pytest.mark.unit
def test_safe_count_returns_count_when_present() -> None:
    client = MagicMock()
    client.get_list.return_value = [{"name": "Acme Corp"}]
    count, err = _safe_count(client, "Company")
    assert count == 1
    assert err is None
    client.get_list.assert_called_once_with("Company", limit=1, fields=["name"])


@pytest.mark.unit
def test_safe_count_treats_typed_error_as_zero_with_signature() -> None:
    client = MagicMock()
    client.get_list.side_effect = ServerError("BrokenPipeError", status_code=500)
    count, err = _safe_count(client, "Company")
    assert count == 0
    assert err == "ServerError 500"


@pytest.mark.unit
def test_safe_count_distinguishes_empty_tenant_from_broken_upstream() -> None:
    # Empty tenant: no exception, empty list -> count=0, err=None.
    client = MagicMock()
    client.get_list.return_value = []
    count, err = _safe_count(client, "Item")
    assert count == 0
    assert err is None


@pytest.mark.unit
def test_safe_count_falls_back_for_unexpected_exception() -> None:
    client = MagicMock()
    client.get_list.side_effect = ConnectionError("network down")
    count, err = _safe_count(client, "Item")
    assert count == 0
    assert err == "ConnectionError"


# ---------- _build_missing -------------------------------------------------


@pytest.mark.unit
def test_build_missing_returns_empty_when_all_present() -> None:
    counts = {dt: 5 for dt, _ in _PROBES}
    errors: dict[str, str | None] = {dt: None for dt, _ in _PROBES}
    assert _build_missing(counts, errors) == []


@pytest.mark.unit
def test_build_missing_fresh_tenant_b987fdbe_shape() -> None:
    # Verbatim shape from session b987fdbe (tenant 15): every probe = 0.
    counts = {dt: 0 for dt, _ in _PROBES}
    errors: dict[str, str | None] = {dt: None for dt, _ in _PROBES}
    missing = _build_missing(counts, errors)

    assert len(missing) == len(_PROBES)
    # Company comes first in the list — that's what the prompt should
    # show the user as the "first thing to fix".
    assert missing[0]["doctype"] == "Company"
    assert missing[0]["reason"] == "no record found"
    assert "all chains" in missing[0]["blocks"]


@pytest.mark.unit
def test_build_missing_marks_probe_errors_distinctly() -> None:
    counts = {dt: 0 for dt, _ in _PROBES}
    errors = {dt: None for dt, _ in _PROBES}
    errors["Company"] = "ServerError 500"
    missing = _build_missing(counts, errors)

    company_entry = next(m for m in missing if m["doctype"] == "Company")
    assert "probe failed" in company_entry["reason"]
    assert "ServerError 500" in company_entry["reason"]


# ---------- end-to-end status command --------------------------------------


def _invoke_status(_monkeypatch, get_list_return: dict) -> dict:
    """Drive bootstrap_group.compute_status with a fake session.client().

    We test the pure ``compute_status`` function directly, not the Click
    wrapper — Click's pass_context decorator calls
    ``click.get_current_context()`` even when invoked via ``.callback``,
    which fails outside a real CLI run.
    """
    from erpnext_pkg.cli_groups.bootstrap_group import compute_status

    client = MagicMock()

    def fake_get_list(doctype: str, **_kwargs) -> list[dict]:
        if isinstance(get_list_return.get(doctype), Exception):
            raise get_list_return[doctype]
        return get_list_return.get(doctype, [])

    client.get_list.side_effect = fake_get_list

    session = MagicMock()
    session.client.return_value = client
    session.redacted.return_value = {"context": {"default_warehouse": "主仓库 - BIEL"}}

    return compute_status(session)


@pytest.mark.unit
def test_status_fully_seeded_tenant_is_ready_for_everything(monkeypatch) -> None:
    payload = _invoke_status(
        monkeypatch,
        get_list_return={dt: [{"name": "x"}] for dt, _ in _PROBES},
    )
    assert payload["has_company"] is True
    assert payload["ready_for_purchase"] is True
    assert payload["ready_for_sales"] is True
    assert payload["ready_for_stock_in"] is True
    assert payload["missing"] == []
    assert payload["default_warehouse"] == "主仓库 - BIEL"


@pytest.mark.unit
def test_status_b987fdbe_empty_tenant_blocks_every_chain(monkeypatch) -> None:
    payload = _invoke_status(monkeypatch, get_list_return={})
    assert payload["has_company"] is False
    assert payload["ready_for_purchase"] is False
    assert payload["ready_for_sales"] is False
    assert payload["ready_for_stock_in"] is False
    # All six probes should appear in missing.
    missing_doctypes = {m["doctype"] for m in payload["missing"]}
    assert missing_doctypes == {dt for dt, _ in _PROBES}


@pytest.mark.unit
def test_status_partial_seed_only_blocks_the_unmet_chains(monkeypatch) -> None:
    # Has Company + Warehouse + Item + Supplier (so purchase ready), but no
    # Customer (so sales NOT ready) and no Item Group records.
    payload = _invoke_status(monkeypatch, get_list_return={
        "Company": [{"name": "BIEL"}],
        "Warehouse": [{"name": "Main"}],
        "Item": [{"name": "SKU-001"}],
        "Supplier": [{"name": "NOXIA"}],
        # missing: Item Group, Customer
    })
    assert payload["ready_for_purchase"] is True
    assert payload["ready_for_sales"] is False
    assert payload["ready_for_stock_in"] is True
    missing_doctypes = {m["doctype"] for m in payload["missing"]}
    assert missing_doctypes == {"Item Group", "Customer"}


@pytest.mark.unit
def test_status_surfaces_upstream_probe_errors(monkeypatch) -> None:
    # Frappe is half-broken: Company probe fails with 500, others return 0.
    payload = _invoke_status(monkeypatch, get_list_return={
        "Company": ServerError("BrokenPipe", status_code=500),
    })
    assert payload["company_count"] == 0
    assert payload["probe_errors"]["Company"] == "ServerError 500"
    company_entry = next(m for m in payload["missing"] if m["doctype"] == "Company")
    assert "probe failed" in company_entry["reason"]


@pytest.mark.unit
def test_status_surfaces_auth_failure_distinctly(monkeypatch) -> None:
    payload = _invoke_status(monkeypatch, get_list_return={
        dt: AuthError("token rejected", status_code=403) for dt, _ in _PROBES
    })
    # Every probe failed with the same auth error, so every chain is blocked.
    assert payload["ready_for_purchase"] is False
    assert all(err == "AuthError 403" for err in payload["probe_errors"].values())
