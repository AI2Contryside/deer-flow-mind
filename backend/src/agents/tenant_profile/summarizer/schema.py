"""Pydantic models for the summarizer's ``profile.json`` output.

Strict schema with field caps so a runaway LLM can't blow up the prompt
budget. ``EntityRef.name`` MUST appear in the input data (facts or
usage_window) — that invariant is enforced by ``runner.py`` after parsing,
not in the schema itself, so failures yield a clear error message rather
than a cryptic pydantic complaint.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

EntityStatus = Literal["active", "recently_quiet"]


class EntityRef(BaseModel):
    """A single key entity (Customer, Item, Warehouse, …) in the profile."""

    name: str
    display: str | None = None
    note: str | None = Field(default=None, max_length=120)
    status: EntityStatus = "active"
    quiet_runs: int = Field(default=0, ge=0)
    extras: dict[str, Any] = Field(default_factory=dict)


class OperationalPatterns(BaseModel):
    primary_workflow: str | None = Field(default=None, max_length=200)
    currencies_in_use: list[str] = Field(default_factory=list, max_length=10)
    valuation_method_observed: str | None = Field(default=None, max_length=40)
    default_warehouse_by_company: dict[str, str] = Field(default_factory=dict)
    payment_terms_in_use: list[str] = Field(default_factory=list, max_length=10)


class KeyEntities(BaseModel):
    customers: list[EntityRef] = Field(default_factory=list, max_length=20)
    suppliers: list[EntityRef] = Field(default_factory=list, max_length=20)
    items: list[EntityRef] = Field(default_factory=list, max_length=30)
    warehouses: list[EntityRef] = Field(default_factory=list, max_length=10)
    price_lists: list[EntityRef] = Field(default_factory=list, max_length=5)
    uoms: list[EntityRef] = Field(default_factory=list, max_length=10)
    accounts_by_root_type: dict[str, list[EntityRef]] = Field(default_factory=dict)
    cost_centers_by_parent: dict[str, list[EntityRef]] = Field(default_factory=dict)
    sales_tax_templates: list[EntityRef] = Field(default_factory=list, max_length=5)
    purchase_tax_templates: list[EntityRef] = Field(default_factory=list, max_length=5)
    payment_terms_templates: list[EntityRef] = Field(default_factory=list, max_length=5)


class Taxonomy(BaseModel):
    item_groups_by_parent: dict[str, list[EntityRef]] = Field(default_factory=dict)
    customer_groups_by_parent: dict[str, list[EntityRef]] = Field(default_factory=dict)
    supplier_groups_by_parent: dict[str, list[EntityRef]] = Field(default_factory=dict)
    territories_by_parent: dict[str, list[EntityRef]] = Field(default_factory=dict)
    sales_persons: list[EntityRef] = Field(default_factory=list, max_length=10)
    brands: list[EntityRef] = Field(default_factory=list, max_length=5)


class OpenQuestion(BaseModel):
    question: str = Field(max_length=200)
    candidates: list[str] = Field(default_factory=list, max_length=10)


class TenantProfile(BaseModel):
    """Top-level shape persisted as profile.json."""

    schema_version: Literal[2] = 2
    generated_at: str  # ISO timestamp
    tenant_id: str
    summary: str = Field(max_length=500)
    facts: dict[str, Any] = Field(default_factory=dict)
    operational_patterns: OperationalPatterns = Field(default_factory=OperationalPatterns)
    key_entities: KeyEntities = Field(default_factory=KeyEntities)
    taxonomy: Taxonomy = Field(default_factory=Taxonomy)
    open_questions: list[OpenQuestion] = Field(default_factory=list, max_length=10)


def collect_entity_names(profile: TenantProfile) -> set[tuple[str, str]]:
    """Every ``(doctype, name)`` referenced in the profile.

    Used by ``runner.py`` to enforce: each name must trace back to either
    facts or the usage window. ``doctype`` is inferred from which slot the
    EntityRef sits in.
    """
    out: set[tuple[str, str]] = set()
    ke = profile.key_entities

    for ref in ke.customers:
        out.add(("Customer", ref.name))
    for ref in ke.suppliers:
        out.add(("Supplier", ref.name))
    for ref in ke.items:
        out.add(("Item", ref.name))
    for ref in ke.warehouses:
        out.add(("Warehouse", ref.name))
    for ref in ke.price_lists:
        out.add(("Price List", ref.name))
    for ref in ke.uoms:
        out.add(("UOM", ref.name))
    for refs in ke.accounts_by_root_type.values():
        for ref in refs:
            out.add(("Account", ref.name))
    for refs in ke.cost_centers_by_parent.values():
        for ref in refs:
            out.add(("Cost Center", ref.name))
    for ref in ke.sales_tax_templates:
        out.add(("Sales Taxes and Charges Template", ref.name))
    for ref in ke.purchase_tax_templates:
        out.add(("Purchase Taxes and Charges Template", ref.name))
    for ref in ke.payment_terms_templates:
        out.add(("Payment Terms Template", ref.name))

    tax = profile.taxonomy
    for refs in tax.item_groups_by_parent.values():
        for ref in refs:
            out.add(("Item Group", ref.name))
    for refs in tax.customer_groups_by_parent.values():
        for ref in refs:
            out.add(("Customer Group", ref.name))
    for refs in tax.supplier_groups_by_parent.values():
        for ref in refs:
            out.add(("Supplier Group", ref.name))
    for refs in tax.territories_by_parent.values():
        for ref in refs:
            out.add(("Territory", ref.name))
    for ref in tax.sales_persons:
        out.add(("Sales Person", ref.name))
    for ref in tax.brands:
        out.add(("Brand", ref.name))

    return out
