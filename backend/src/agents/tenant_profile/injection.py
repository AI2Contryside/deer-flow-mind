"""Render ``profile.json`` into a system-prompt section.

Called from ``lead_agent/prompt.py:_get_profile_context`` at every prompt
assembly. Handles three states:

  1. profile.json present → render and inject.
  2. profile.json missing → synchronously bootstrap facts (2s budget),
     write a facts-only profile, render that. Frequent / taxonomy
     sections render empty since we have no observation history yet.
  3. bootstrap times out / fails → return empty string. The agent falls
     through to its normal path (asking ERPNext directly when needed).

NestedSet doctypes (Account / Cost Center / Item / Customer / Supplier
groups / Territory) render bucketed per §6.2 of the design — never flat.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from src.agents.tenant_profile import facts as facts_module
from src.agents.tenant_profile import store as store_module
from src.agents.tenant_profile.config import get_tenant_profile_config

logger = logging.getLogger(__name__)


def get_profile_context(tenant_id: str | None, *, user_email: str | None = None) -> str:
    """Public entry. Returns the rendered markdown section (or empty string).

    Best-effort: every failure mode collapses to "" so a misconfigured
    tenant_profile feature never breaks the agent's main path.
    """
    if not tenant_id:
        return ""
    cfg = get_tenant_profile_config()
    if not cfg.enabled:
        return ""

    try:
        profile = store_module.read_profile(tenant_id)
        if profile is None:
            profile = _cold_start_bootstrap(tenant_id, user_email=user_email)
        if profile is None:
            return ""
        return render_profile_section(profile)
    except Exception as exc:
        # Last-resort: never let injection failure block the conversation.
        logger.warning("tenant_profile: failed to render profile context for tenant %r: %s", tenant_id, exc)
        return ""


def _cold_start_bootstrap(tenant_id: str, *, user_email: str | None) -> dict[str, Any] | None:
    """Sync facts bootstrap with a hard 2s timeout.

    Returns a minimal facts-only profile dict on success, or None if the
    bootstrap produced nothing useful within the budget.
    """
    cfg = get_tenant_profile_config().facts
    bundle = facts_module.bootstrap_facts(
        user_email=user_email,
        timeout_seconds=cfg.sync_bootstrap_timeout_seconds,
        now_iso=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    )
    facts_dict = facts_module.facts_to_dict(bundle)
    if bundle.primary_company is None and bundle.current_user is None:
        # Bootstrap returned nothing — likely the stub client (ERPNext not
        # wired) or a hard timeout. Don't write a profile that's just
        # warnings; the next agent turn will retry.
        return None

    profile = {
        "schema_version": 3,
        "generated_at": facts_dict.get("fetched_at"),
        "tenant_id": tenant_id,
        "summary": "",  # populated on first real summarize run
        "scenarios": [],
        "facts": facts_dict,
        "operational_patterns": {},
        "key_entities": {},
        "taxonomy": {},
        "open_questions": [],
        "_cold_start": True,  # marker so summarizer doesn't treat this as previous
    }
    store_module.write_profile(tenant_id, profile)
    return profile


def render_profile_section(profile: dict[str, Any]) -> str:
    """Render a profile dict to the markdown form the lead agent embeds."""
    lines: list[str] = ["<tenant_profile>"]

    facts = profile.get("facts") or {}
    identity = _render_identity(facts)
    if identity:
        lines.append(identity)

    summary = (profile.get("summary") or "").strip()
    if summary:
        lines.append(f"**Tenant brief**: {summary}")

    op_patterns = profile.get("operational_patterns") or {}
    op_block = _render_operational_patterns(op_patterns)
    if op_block:
        lines.append("")
        lines.append(op_block)

    key_entities = profile.get("key_entities") or {}
    ke_block = _render_key_entities(key_entities)
    if ke_block:
        lines.append("")
        lines.append(ke_block)

    taxonomy = profile.get("taxonomy") or {}
    tax_block = _render_taxonomy(taxonomy)
    if tax_block:
        lines.append("")
        lines.append(tax_block)

    cfg = get_tenant_profile_config().injection
    quiet = _render_recently_quiet(key_entities) if cfg.show_recently_quiet else ""
    if quiet:
        lines.append("")
        lines.append(quiet)

    open_qs = profile.get("open_questions") or []
    if cfg.show_open_questions and open_qs:
        lines.append("")
        lines.append("### Open questions")
        for q in open_qs:
            if isinstance(q, dict) and q.get("question"):
                lines.append(f"- {q['question']}")

    lines.append("</tenant_profile>")
    return "\n".join(lines) + "\n"


# ── Section helpers ───────────────────────────────────────────────────────


def _render_identity(facts: dict[str, Any]) -> str:
    """Top "Identity" line — what the agent sees first."""
    pc = facts.get("primary_company") or {}
    employee = facts.get("current_employee") or {}
    user = facts.get("current_user") or {}
    fy = facts.get("fiscal_year") or {}
    parts: list[str] = []

    if pc.get("name"):
        identity_bits = [pc["name"]]
        meta_bits: list[str] = []
        if pc.get("default_currency"):
            meta_bits.append(pc["default_currency"])
        if pc.get("country"):
            meta_bits.append(pc["country"])
        if fy.get("name"):
            meta_bits.append(f"FY{fy['name']}")
        if meta_bits:
            identity_bits.append(f"({', '.join(meta_bits)})")
        parts.append(f"**Identity**: {' '.join(identity_bits)}")

    additional = facts.get("additional_companies") or []
    if additional:
        names = ", ".join(c.get("name", "?") for c in additional if isinstance(c, dict))
        parts.append(f"**Additional companies**: {names}")

    if employee.get("employee_name") or user.get("full_name"):
        you_bits = [employee.get("employee_name") or user.get("full_name")]
        if employee.get("designation"):
            you_bits.append(employee["designation"])
        if employee.get("department"):
            you_bits.append(f"dept={employee['department']}")
        if employee.get("company"):
            you_bits.append(f"company={employee['company']}")
        parts.append("**You are**: " + " · ".join(b for b in you_bits if b))

    return "\n".join(parts)


def _render_operational_patterns(op: dict[str, Any]) -> str:
    if not op:
        return ""
    lines: list[str] = ["### Operational patterns"]
    if op.get("primary_workflow"):
        lines.append(f"- Primary workflow: {op['primary_workflow']}")
    currencies = op.get("currencies_in_use") or []
    if currencies:
        lines.append(f"- Currencies in use: {', '.join(currencies)}")
    if op.get("valuation_method_observed"):
        lines.append(f"- Valuation method: {op['valuation_method_observed']}")
    defaults = op.get("default_warehouse_by_company") or {}
    if defaults:
        for company, wh in defaults.items():
            lines.append(f"- Default warehouse · {company}: {wh}")
    terms = op.get("payment_terms_in_use") or []
    if terms:
        lines.append(f"- Payment terms in use: {', '.join(terms)}")
    return "\n".join(lines) if len(lines) > 1 else ""


def _render_key_entities(ke: dict[str, Any]) -> str:
    if not ke:
        return ""
    sections: list[str] = []

    flat_groups = [
        ("Frequent customers", ke.get("customers")),
        ("Frequent suppliers", ke.get("suppliers")),
        ("Frequent items", ke.get("items")),
        ("Frequent warehouses", ke.get("warehouses")),
        ("Price lists in use", ke.get("price_lists")),
        ("UOMs in use", ke.get("uoms")),
        ("Sales tax templates", ke.get("sales_tax_templates")),
        ("Purchase tax templates", ke.get("purchase_tax_templates")),
        ("Payment terms templates", ke.get("payment_terms_templates")),
    ]
    for title, entries in flat_groups:
        section = _render_active_entities(title, entries or [])
        if section:
            sections.append(section)

    accounts_section = _render_bucketed("Frequent accounts (by root_type)", ke.get("accounts_by_root_type"))
    if accounts_section:
        sections.append(accounts_section)
    cc_section = _render_bucketed("Frequent cost centers (by parent)", ke.get("cost_centers_by_parent"))
    if cc_section:
        sections.append(cc_section)

    return "\n\n".join(sections)


def _render_taxonomy(tax: dict[str, Any]) -> str:
    if not tax:
        return ""
    sections: list[str] = []
    bucketed_groups = [
        ("Item Groups (by parent)", tax.get("item_groups_by_parent")),
        ("Customer Groups (by parent)", tax.get("customer_groups_by_parent")),
        ("Supplier Groups (by parent)", tax.get("supplier_groups_by_parent")),
        ("Territories (by parent)", tax.get("territories_by_parent")),
    ]
    for title, buckets in bucketed_groups:
        section = _render_bucketed(title, buckets)
        if section:
            sections.append(section)

    flat = [
        ("Sales persons", tax.get("sales_persons")),
        ("Brands", tax.get("brands")),
    ]
    for title, entries in flat:
        section = _render_active_entities(title, entries or [])
        if section:
            sections.append(section)

    return "\n\n".join(sections)


def _render_active_entities(title: str, entries: list[dict[str, Any]]) -> str:
    """Render only ``status="active"`` rows. recently_quiet goes elsewhere."""
    rows = [e for e in entries if isinstance(e, dict) and e.get("status", "active") == "active"]
    if not rows:
        return ""
    lines = [f"### {title}"]
    for row in rows:
        lines.append(_format_entity_line(row))
    return "\n".join(lines)


def _render_bucketed(title: str, buckets: dict[str, Any] | None) -> str:
    if not isinstance(buckets, dict) or not buckets:
        return ""
    lines = [f"### {title}"]
    for bucket_name, entries in buckets.items():
        if not isinstance(entries, list) or not entries:
            continue
        active = [e for e in entries if isinstance(e, dict) and e.get("status", "active") == "active"]
        if not active:
            continue
        lines.append(f"- **{bucket_name or '(root)'}**:")
        for row in active:
            lines.append("  " + _format_entity_line(row))
    return "\n".join(lines) if len(lines) > 1 else ""


def _render_recently_quiet(key_entities: dict[str, Any]) -> str:
    """One short section for entities that haven't been seen recently.

    Pulled from the same key_entities buckets but only the
    ``status="recently_quiet"`` rows. We render a short tabular summary so
    the agent knows to confirm when a user mentions one of these.
    """
    quiet: list[tuple[str, dict[str, Any]]] = []
    for key in ("customers", "suppliers", "items", "warehouses"):
        for entry in key_entities.get(key) or []:
            if isinstance(entry, dict) and entry.get("status") == "recently_quiet":
                quiet.append((key, entry))
    for key in ("accounts_by_root_type", "cost_centers_by_parent"):
        buckets = key_entities.get(key) or {}
        if isinstance(buckets, dict):
            for entries in buckets.values():
                if not isinstance(entries, list):
                    continue
                for entry in entries:
                    if isinstance(entry, dict) and entry.get("status") == "recently_quiet":
                        quiet.append((key, entry))
    if not quiet:
        return ""
    lines = ["### Recently quiet (confirm if user mentions)"]
    for category, row in quiet:
        name = row.get("name") or "?"
        display = row.get("display") or name
        lines.append(f"- {name} — {display} ({category})")
    return "\n".join(lines)


def _format_entity_line(row: dict[str, Any]) -> str:
    name = row.get("name") or "?"
    display = row.get("display")
    note = row.get("note")
    extras = row.get("extras") or {}
    parts = [f"- {name}"]
    if display and display != name:
        parts[0] += f" — {display}"
    extra_bits = [f"{k}={v}" for k, v in extras.items() if v not in (None, "", [], {})]
    if extra_bits:
        parts[0] += f" [{', '.join(extra_bits)}]"
    if note:
        parts[0] += f" · {note}"
    return parts[0]
