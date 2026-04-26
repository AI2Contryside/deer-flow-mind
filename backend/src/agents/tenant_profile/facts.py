"""Bootstrap the per-tenant ``facts`` block of the profile.

Facts are tenant-stable identity info: the active Company (with default
warehouse / cost center / receivable + payable accounts), the current
fiscal year, the Selling/Stock/Accounts/Buying singletons, and the
current user's Employee + role list. See TENANT_PROFILE_DESIGN.md §3.1.

This module exposes two entry points:

* ``bootstrap_facts`` — synchronous, takes a budget in seconds and returns
  whatever it managed to fetch within that budget. Anything we couldn't
  fetch lands in ``warnings`` so the cold-start path (M4) can decide
  whether to fall through to an empty profile or report partial.
* ``facts_to_dict`` — render a ``FactsBundle`` to the JSON shape the
  summarizer prompt expects.

Production plugs in a real ``ErpnextReadOnlyClient`` via
``set_default_erpnext_client``; tests pass a fake client. With the stub
client (the v1 default) every fetch fails with ``NotConfiguredError`` and
``bootstrap_facts`` returns an empty bundle whose ``warnings`` lists the
missing pieces — never raises.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from src.agents.tenant_profile.erpnext_client import (
    ErpnextReadOnlyClient,
    NotConfiguredError,
    get_default_erpnext_client,
)

logger = logging.getLogger(__name__)


# ── Field whitelists (mirror §3.1) ────────────────────────────────────────

_COMPANY_FIELDS = (
    "name",
    "abbr",
    "default_currency",
    "country",
    "default_warehouse",
    "default_cost_center",
    "default_income_account",
    "default_expense_account",
    "default_payable_account",
    "default_receivable_account",
)
_FISCAL_YEAR_FIELDS = ("name", "year_start_date", "year_end_date")
_SELLING_SETTINGS_FIELDS = (
    "cust_master_name",
    "so_required",
    "dn_required",
    "sales_update_frequency",
    "validate_selling_price",
    "allow_against_multiple_purchase_orders",
)
_STOCK_SETTINGS_FIELDS = (
    "item_naming_by",
    "valuation_method",
    "default_warehouse",
    "allow_negative_stock",
    "auto_indent",
    "sample_retention_warehouse",
)
_ACCOUNTS_SETTINGS_FIELDS = (
    "auto_accounting_for_stock",
    "acc_frozen_upto",
    "credit_controller",
    "role_allowed_to_over_bill",
)
_BUYING_SETTINGS_FIELDS = ("supp_master_name", "po_required", "maintain_same_rate")
_GLOBAL_DEFAULTS_FIELDS = ("default_company", "default_currency", "country", "hide_currency_symbol")
_EMPLOYEE_FIELDS = ("name", "employee_name", "department", "designation", "company", "default_shift")
_USER_FIELDS = ("email", "full_name", "language", "time_zone")


# Roles we strip from User.roles — they leak no business info and only bloat
# the prompt. Matches the spirit of §3.1 ("过滤系统角色，仅留业务相关").
_SYSTEM_ROLES = frozenset(
    {
        "All",
        "Guest",
        "Administrator",
        "System Manager",
        "Desk User",
        "Script Manager",
        "Newsletter Manager",
        "Workspace Manager",
        "Dashboard Manager",
        "Report Manager",
        "Translator",
        "Customize Form Manager",
        "Inbox User",
    }
)


@dataclass
class FactsBundle:
    """Everything the summarizer's prompt expects under ``facts``."""

    primary_company: dict[str, Any] | None = None
    additional_companies: list[dict[str, Any]] = field(default_factory=list)
    fiscal_year: dict[str, Any] | None = None
    selling_settings: dict[str, Any] | None = None
    stock_settings: dict[str, Any] | None = None
    accounts_settings: dict[str, Any] | None = None
    buying_settings: dict[str, Any] | None = None
    global_defaults: dict[str, Any] | None = None
    current_employee: dict[str, Any] | None = None
    current_user: dict[str, Any] | None = None
    user_roles: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    fetched_at: str | None = None
    duration_ms: int = 0


def facts_to_dict(facts: FactsBundle) -> dict[str, Any]:
    """Render to the JSON shape the summarizer prompt expects."""
    return {
        "primary_company": facts.primary_company,
        "additional_companies": list(facts.additional_companies),
        "fiscal_year": facts.fiscal_year,
        "settings": {
            "selling": facts.selling_settings,
            "stock": facts.stock_settings,
            "accounts": facts.accounts_settings,
            "buying": facts.buying_settings,
            "global_defaults": facts.global_defaults,
        },
        "current_user": (
            {
                **(facts.current_user or {}),
                "roles": list(facts.user_roles),
            }
            if facts.current_user is not None
            else None
        ),
        "current_employee": facts.current_employee,
        "warnings": list(facts.warnings),
        "fetched_at": facts.fetched_at,
        "duration_ms": facts.duration_ms,
    }


def bootstrap_facts(
    *,
    user_email: str | None,
    timeout_seconds: float = 2.0,
    client: ErpnextReadOnlyClient | None = None,
    now_iso: str | None = None,
) -> FactsBundle:
    """Fetch the facts block, giving up after ``timeout_seconds`` overall.

    Each ERPNext call is best-effort — failures land in
    ``bundle.warnings`` rather than raising. The total wall-clock time
    is capped (soft cap; we check after each fetch). Returns whatever we
    managed to gather; callers can decide whether the result is rich
    enough to inject.
    """
    started = time.monotonic()
    deadline = started + max(timeout_seconds, 0.0)
    bundle = FactsBundle(fetched_at=now_iso)
    cli = client if client is not None else get_default_erpnext_client()

    def remaining() -> float:
        return deadline - time.monotonic()

    def safe_get_doc(doctype: str, name: str, fields: tuple[str, ...]) -> dict[str, Any] | None:
        if remaining() <= 0:
            bundle.warnings.append(f"timeout before {doctype} {name!r}")
            return None
        try:
            doc = cli.get_doc(doctype, name)
        except NotConfiguredError:
            bundle.warnings.append(f"erpnext client not configured ({doctype} {name!r})")
            return None
        except Exception as exc:
            bundle.warnings.append(f"failed to load {doctype} {name!r}: {exc}")
            return None
        if not isinstance(doc, dict):
            return None
        return {k: doc[k] for k in fields if k in doc}

    def safe_get_singleton(doctype: str, fields: tuple[str, ...]) -> dict[str, Any] | None:
        # ERPNext "Single" doctypes are addressable by their own doctype name.
        return safe_get_doc(doctype, doctype, fields)

    # 1. Global defaults — gives us the default company name to anchor on.
    bundle.global_defaults = safe_get_singleton("Global Defaults", _GLOBAL_DEFAULTS_FIELDS)

    # 2. Companies. We pull all and pick a primary by `default_company` from
    #    Global Defaults, falling back to the first one returned.
    companies: list[dict[str, Any]] = []
    if remaining() > 0:
        try:
            companies_raw = cli.get_list(
                "Company",
                fields=list(_COMPANY_FIELDS),
                limit=20,
            )
            companies = [{k: c[k] for k in _COMPANY_FIELDS if k in c} for c in companies_raw if isinstance(c, dict)]
        except NotConfiguredError:
            bundle.warnings.append("erpnext client not configured (Company list)")
        except Exception as exc:
            bundle.warnings.append(f"failed to list Company: {exc}")
    if companies:
        primary_name = (bundle.global_defaults or {}).get("default_company")
        primary = next((c for c in companies if c.get("name") == primary_name), companies[0])
        bundle.primary_company = primary
        bundle.additional_companies = [c for c in companies if c.get("name") != primary.get("name")]

    # 3. Fiscal Year — pick the one whose date range covers today. We just
    #    fetch all and let the caller resolve; for v1 we return them all and
    #    let the summarizer pick (cheap; fiscal years are few).
    if remaining() > 0:
        try:
            fy_rows = cli.get_list(
                "Fiscal Year",
                fields=list(_FISCAL_YEAR_FIELDS),
                limit=10,
            )
            if fy_rows:
                # Pick the most recent by year_end_date.
                fy_rows = [r for r in fy_rows if isinstance(r, dict)]
                fy_rows.sort(key=lambda r: r.get("year_end_date") or "", reverse=True)
                bundle.fiscal_year = {k: fy_rows[0][k] for k in _FISCAL_YEAR_FIELDS if k in fy_rows[0]}
        except NotConfiguredError:
            bundle.warnings.append("erpnext client not configured (Fiscal Year list)")
        except Exception as exc:
            bundle.warnings.append(f"failed to list Fiscal Year: {exc}")

    # 4. Settings singletons.
    bundle.selling_settings = safe_get_singleton("Selling Settings", _SELLING_SETTINGS_FIELDS)
    bundle.stock_settings = safe_get_singleton("Stock Settings", _STOCK_SETTINGS_FIELDS)
    bundle.accounts_settings = safe_get_singleton("Accounts Settings", _ACCOUNTS_SETTINGS_FIELDS)
    bundle.buying_settings = safe_get_singleton("Buying Settings", _BUYING_SETTINGS_FIELDS)

    # 5. Current user — only meaningful if we know the email.
    if user_email and remaining() > 0:
        user = safe_get_doc("User", user_email, _USER_FIELDS + ("roles",))
        if user is not None:
            bundle.current_user = {k: user[k] for k in _USER_FIELDS if k in user}
            bundle.user_roles = _filter_roles(user.get("roles"))

        # 6. Employee record linked by user_id. ERPNext stores the user link
        #    on Employee.user_id, so list-with-filter is the way.
        if remaining() > 0:
            try:
                emp_rows = cli.get_list(
                    "Employee",
                    filters={"user_id": user_email},
                    fields=list(_EMPLOYEE_FIELDS),
                    limit=1,
                )
                if emp_rows and isinstance(emp_rows[0], dict):
                    bundle.current_employee = {k: emp_rows[0][k] for k in _EMPLOYEE_FIELDS if k in emp_rows[0]}
            except NotConfiguredError:
                bundle.warnings.append("erpnext client not configured (Employee list)")
            except Exception as exc:
                bundle.warnings.append(f"failed to find Employee for user_id {user_email}: {exc}")

    bundle.duration_ms = int((time.monotonic() - started) * 1000)
    return bundle


def _filter_roles(raw: Any) -> list[str]:
    """Drop system roles from a ``User.roles`` child table.

    ERPNext returns roles either as a list of strings (in some shapes) or
    a list of ``{"role": "..."}`` dicts (the canonical child table form).
    We tolerate both.
    """
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        role: str | None = None
        if isinstance(item, str):
            role = item
        elif isinstance(item, dict):
            candidate = item.get("role")
            if isinstance(candidate, str):
                role = candidate
        if role and role not in _SYSTEM_ROLES:
            out.append(role)
    return out
