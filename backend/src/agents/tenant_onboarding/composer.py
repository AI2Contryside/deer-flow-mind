"""Compose a v1 ``profile.json`` from collected onboarding facts.

Inputs:
- ``answers``: keyed by ``OnboardingQuestion.id`` — what the user told us.
- ``imported``: per-doctype lists of created records, e.g. {"Customer": [...]}.
  Comes from the subagent's ERPNext seed phase.

Output: a dict that round-trips through ``TenantProfile.model_validate`` so
the runtime summarizer can pick it up as ``previous_profile`` without
rewriting it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.agents.tenant_onboarding.profile_schema import (
    EntityRef,
    KeyEntities,
    OpenQuestion,
    OperationalPatterns,
    Taxonomy,
    TenantProfile,
    _now_iso,  # noqa: PLC2701
)

# Caps mirror the runtime schema so the composer can't accidentally produce
# a profile too big for the prompt. We re-derive from the model fields where
# possible to stay in lockstep when the runtime schema bumps its limits.
_DEFAULT_TOP_K = {
    "customers": 20,
    "suppliers": 20,
    "items": 30,
    "warehouses": 10,
    "price_lists": 5,
    "uoms": 10,
}


@dataclass
class OnboardingFacts:
    """Everything the subagent has gathered before composing the profile."""

    tenant_id: str
    answers: dict[str, Any] = field(default_factory=dict)
    imported: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    open_questions: list[dict[str, Any]] = field(default_factory=list)
    summary_override: str | None = None


def _entity_refs(rows: list[dict[str, Any]], cap: int) -> list[EntityRef]:
    out: list[EntityRef] = []
    for row in rows[:cap]:
        name = row.get("name") or row.get("docname") or row.get("id")
        if not name:
            continue
        display = row.get("display") or row.get("title") or row.get("customer_name") or row.get("supplier_name") or row.get("item_name")
        note = row.get("note")
        extras = {k: v for k, v in row.items() if k not in {"name", "docname", "id", "display", "title", "note"}}
        out.append(EntityRef(name=str(name), display=display, note=note, extras=extras))
    return out


def _summary_from_answers(answers: dict[str, Any]) -> str:
    parts: list[str] = []
    if name := answers.get("company_name"):
        parts.append(str(name))
    if country := answers.get("company_country"):
        parts.append(str(country))
    if currency := answers.get("company_currency"):
        parts.append(str(currency))
    if biz := answers.get("business_type"):
        parts.append(str(biz).split(" (")[0])
    head = " · ".join(parts) if parts else "Newly onboarded tenant"
    return f"{head}. Profile seeded by onboarding-v1; runtime summarizer will refine."


def compose_initial_profile(facts: OnboardingFacts) -> dict[str, Any]:
    """Build a schema-validated profile dict. Raises ``ValidationError`` on bad input."""
    answers = facts.answers
    imported = facts.imported

    op = OperationalPatterns(
        primary_workflow=str(answers["business_type"]) if answers.get("business_type") else None,
        currencies_in_use=[answers["company_currency"]] if answers.get("company_currency") else [],
        default_warehouse_by_company=({answers["company_name"]: answers["default_warehouse"]} if answers.get("company_name") and answers.get("default_warehouse") else {}),
    )

    key_entities = KeyEntities(
        customers=_entity_refs(imported.get("Customer", []), _DEFAULT_TOP_K["customers"]),
        suppliers=_entity_refs(imported.get("Supplier", []), _DEFAULT_TOP_K["suppliers"]),
        items=_entity_refs(imported.get("Item", []), _DEFAULT_TOP_K["items"]),
        warehouses=_entity_refs(imported.get("Warehouse", []), _DEFAULT_TOP_K["warehouses"]),
        price_lists=_entity_refs(imported.get("Price List", []), _DEFAULT_TOP_K["price_lists"]),
        uoms=_entity_refs(imported.get("UOM", []), _DEFAULT_TOP_K["uoms"]),
    )

    open_q = [OpenQuestion(question=str(q["question"])[:200], candidates=[str(c) for c in (q.get("candidates") or [])][:10]) for q in facts.open_questions[:10]]

    summary = (facts.summary_override or _summary_from_answers(answers))[:500]

    profile = TenantProfile(
        generated_at=_now_iso(),
        tenant_id=facts.tenant_id,
        summary=summary,
        facts={
            "answers": answers,
            "source": "onboarding-v1",
            "imported_counts": {dt: len(rows) for dt, rows in imported.items()},
        },
        operational_patterns=op,
        key_entities=key_entities,
        taxonomy=Taxonomy(),
        open_questions=open_q,
    )
    return profile.model_dump(mode="json")


__all__ = [
    "OnboardingFacts",
    "compose_initial_profile",
]
