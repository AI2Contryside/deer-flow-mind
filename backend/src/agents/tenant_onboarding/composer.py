"""Compose a v3 ``profile.json`` from collected onboarding answers.

Inputs:
- ``answers``: keyed by ``OnboardingQuestion.id`` — what the user told us.
- ``imported``: per-doctype lists of created records, e.g. {"Customer": [...]}.
  Comes from the lead-agent ERPNext seed phase.

Output: a dict that round-trips through ``TenantProfile.model_validate`` so
the runtime summarizer can pick it up as ``previous_profile`` without
rewriting it.

Routing rules (v3, design §6):
  - Each ``OnboardingQuestion`` carries a ``profile_path`` (dotted-path
    relative to the **profile root**, e.g. ``facts.company.country``).
    The composer projects answers into a working profile-shaped dict and
    then validates it through pydantic.
  - Scenarios are partitioned by ``parent_category`` and emitted as a
    ``ScenarioRef`` list so the runtime summarizer knows which
    ``facts.<scenario_id>.*`` subtrees are relevant.
  - Cross-scenario derived facts (e.g. trade_mode for brokerage) are applied
    via per-scenario ``profile_defaults`` and explicit derive helpers.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from src.agents.tenant_onboarding.profile_schema import (
    EntityRef,
    KeyEntities,
    OpenQuestion,
    OperationalPatterns,
    ScenarioRef,
    Taxonomy,
    TenantProfile,
    _now_iso,  # noqa: PLC2701
)
from src.agents.tenant_onboarding.question_bank import (
    all_questions_for_lookup,
    get_registry,
    selected_scenarios,
)
from src.agents.tenant_onboarding.question_bank.types import OnboardingQuestion

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

# Pre-filled context the lead agent injects into the ``task`` prompt — these
# are not real onboarding questions and have their own dedicated writers, so
# the path-projection fallback skips them.
_NON_QUESTION_KEYS = frozenset({"company_name"})


@dataclass
class OnboardingFacts:
    """Everything gathered before composing the profile.

    ``imported`` is per-doctype rows created by the ERPNext seeding phase.
    ``open_questions`` is whatever the agent couldn't resolve during seed.
    """

    tenant_id: str
    answers: dict[str, Any] = field(default_factory=dict)
    imported: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    open_questions: list[dict[str, Any]] = field(default_factory=list)
    summary_override: str | None = None


# ----- helpers for nested-dict / dotted-path writes ----------------------


def _set_dotted(target: dict[str, Any], path: str, value: Any) -> None:
    """Write ``value`` into ``target`` at the dotted ``path``.

    ``target['a']['b']['c'] = value`` for ``path='a.b.c'``. Intermediate
    dicts are created as needed. ``path`` containing ``[]`` is treated as
    "structural placeholder" and skipped (used for meta paths like
    ``scenarios[].category`` which the composer materializes separately).
    """
    if not path or "[]" in path:
        return
    parts = path.split(".")
    cursor: dict[str, Any] = target
    for part in parts[:-1]:
        nxt = cursor.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cursor[part] = nxt
        cursor = nxt
    cursor[parts[-1]] = value


def _get_dotted(target: dict[str, Any], path: str) -> Any | None:
    cursor: Any = target
    for part in path.split("."):
        if not isinstance(cursor, dict):
            return None
        cursor = cursor.get(part)
    return cursor


# ----- KeyEntities seed from imported rows -------------------------------


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


# ----- summary builder ---------------------------------------------------


def _summary_from_answers(answers: dict[str, Any], scenario_ids: list[str]) -> str:
    parts: list[str] = []
    if name := answers.get("company_name"):
        parts.append(str(name))
    if country := answers.get("company_country"):
        parts.append(str(country))
    if currency := answers.get("company_currency"):
        parts.append(str(currency))
    if scenario_ids:
        parts.append("/".join(scenario_ids))
    head = " · ".join(parts) if parts else "Newly onboarded tenant"
    return f"{head}. Profile seeded by onboarding-v3; runtime summarizer will refine."


# ----- ScenarioRef list --------------------------------------------------


def _build_scenario_refs(answers: dict[str, Any]) -> list[ScenarioRef]:
    """Group selected detailed scenarios by their parent_category."""
    primary = [c for c in (answers.get("primary_categories") or []) if c]
    if not primary:
        return []
    selected_packs = selected_scenarios(answers)
    by_cat: dict[str, list[str]] = defaultdict(list)
    for pack in selected_packs:
        by_cat[pack.parent_category].append(pack.id)

    refs: list[ScenarioRef] = []
    primary_set = set(primary)
    for category in primary:
        ids = by_cat.get(category, [])
        refs.append(ScenarioRef(category=category, scenarios=ids))  # type: ignore[arg-type]

    # If the only selected pack is the ``general`` fallback (because no
    # detailed scenario fit a real category), keep ``unknown`` visible too.
    if "unknown" in by_cat and "unknown" not in primary_set:
        refs.append(ScenarioRef(category="unknown", scenarios=by_cat["unknown"]))
    return refs


# ----- writers operating on the full profile dict ------------------------


def _write_company_facts(profile_root: dict[str, Any], answers: dict[str, Any]) -> None:
    """Persist the company name pulled from the lead-agent ``task`` prompt
    (not from a question — the FE already collected it).
    """
    name = answers.get("company_name")
    if name:
        _set_dotted(profile_root, "facts.company.name", str(name))


def _write_answers_via_paths(
    profile_root: dict[str, Any],
    answers: dict[str, Any],
    questions: dict[str, OnboardingQuestion],
) -> None:
    """Walk every answered question and write its value to ``profile_path``.

    ``profile_path`` is rooted at the profile (e.g. ``facts.company.country``,
    ``operational_patterns.selling_currencies``), so we apply it directly to
    the full profile dict rather than to the facts subtree.
    """
    for qid, value in answers.items():
        if qid in _NON_QUESTION_KEYS:
            continue
        question = questions.get(qid)
        if question is None:
            # Free-form answers preserved under ``facts.answers.<qid>``.
            _set_dotted(profile_root, f"facts.answers.{qid}", value)
            continue
        if not question.profile_path or "[]" in question.profile_path:
            continue
        if value is None:
            continue
        _set_dotted(profile_root, question.profile_path, value)


def _apply_profile_defaults(profile_root: dict[str, Any], defaults: dict[str, Any]) -> None:
    """Apply scenario-pack ``profile_defaults`` (dotted paths from profile
    root) without overwriting explicit answers already written.
    """
    for path, value in defaults.items():
        if _get_dotted(profile_root, path) is not None:
            continue
        _set_dotted(profile_root, path, value)


def _derive_brokerage_trade_mode(profile_root: dict[str, Any], answers: dict[str, Any]) -> None:
    """If brokerage is in scope, derive ``operational_patterns.trade_mode``."""
    if "brokerage" not in {p.id for p in selected_scenarios(answers)}:
        return
    try:
        from src.agents.tenant_onboarding.question_bank.scenarios.brokerage import (
            derive_trade_mode,
        )

        mode = derive_trade_mode(answers)
    except Exception:  # noqa: BLE001
        return
    _set_dotted(profile_root, "operational_patterns.trade_mode", mode)


def _derive_import_export_tracking_flags(profile_root: dict[str, Any], answers: dict[str, Any]) -> None:
    """If import_export is in scope, lift ``ie_tracking_level`` into the
    cross-scenario ``facts.flags.requires_batch_tracking`` /
    ``requires_serial_tracking`` booleans the runtime summarizer reads.
    """
    if "import_export" not in {p.id for p in selected_scenarios(answers)}:
        return
    try:
        from src.agents.tenant_onboarding.question_bank.scenarios.import_export import (
            derive_tracking_flags,
        )

        flags = derive_tracking_flags(answers)
    except Exception:  # noqa: BLE001
        return
    for key, value in flags.items():
        _set_dotted(profile_root, f"facts.flags.{key}", value)


def _ensure_currencies_in_use(profile_root: dict[str, Any], answers: dict[str, Any]) -> None:
    """Union of selling+buying currencies populates the legacy field that
    the runtime summarizer's prompt still keys off.
    """
    op = profile_root.setdefault("operational_patterns", {})
    sell = op.get("selling_currencies") or []
    buy = op.get("buying_currencies") or []
    if sell or buy:
        union: list[str] = []
        for c in (*sell, *buy):
            if c not in union:
                union.append(c)
        op.setdefault("currencies_in_use", union[:10])
    elif company_currency := answers.get("company_currency"):
        op.setdefault("currencies_in_use", [str(company_currency)])


# ----- entry point -------------------------------------------------------


def compose_initial_profile(facts: OnboardingFacts) -> dict[str, Any]:
    """Build a schema-validated profile dict.

    Raises ``pydantic.ValidationError`` on bad input — callers should not
    swallow it; a malformed profile means the composer can't safely write
    to disk.
    """
    answers = dict(facts.answers)
    imported = facts.imported

    # Build a working profile-shaped dict so dotted profile_paths from
    # questions land in the right slot directly.
    profile_root: dict[str, Any] = {
        "facts": {
            "source": "onboarding-v3",
            "imported_counts": {dt: len(rows) for dt, rows in imported.items()},
            "answers": dict(answers),  # raw audit trail
        },
        "operational_patterns": {},
    }

    _write_company_facts(profile_root, answers)
    questions = all_questions_for_lookup()
    _write_answers_via_paths(profile_root, answers, questions)
    _derive_brokerage_trade_mode(profile_root, answers)
    _derive_import_export_tracking_flags(profile_root, answers)

    for pack in selected_scenarios(answers):
        if pack.profile_defaults:
            _apply_profile_defaults(profile_root, pack.profile_defaults)

    _ensure_currencies_in_use(profile_root, answers)

    # Default-warehouse-by-company is a per-company dict. When the lead-agent
    # task prompt provided a company name AND the user picked a default
    # warehouse, expose it on operational_patterns for the runtime injection.
    if answers.get("company_name") and answers.get("default_warehouse") and not profile_root["operational_patterns"].get("default_warehouse_by_company"):
        profile_root["operational_patterns"]["default_warehouse_by_company"] = {str(answers["company_name"]): str(answers["default_warehouse"])}

    # Lift typed sub-models out of the working dict.
    op_block = profile_root.get("operational_patterns") or {}
    op = OperationalPatterns(**{k: v for k, v in op_block.items() if k in OperationalPatterns.model_fields})

    fact_tree = profile_root.get("facts") or {}

    scenario_refs = _build_scenario_refs(answers)
    scenario_ids = [sid for ref in scenario_refs for sid in ref.scenarios]

    key_entities = KeyEntities(
        customers=_entity_refs(imported.get("Customer", []), _DEFAULT_TOP_K["customers"]),
        suppliers=_entity_refs(imported.get("Supplier", []), _DEFAULT_TOP_K["suppliers"]),
        items=_entity_refs(imported.get("Item", []), _DEFAULT_TOP_K["items"]),
        warehouses=_entity_refs(imported.get("Warehouse", []), _DEFAULT_TOP_K["warehouses"]),
        price_lists=_entity_refs(imported.get("Price List", []), _DEFAULT_TOP_K["price_lists"]),
        uoms=_entity_refs(imported.get("UOM", []), _DEFAULT_TOP_K["uoms"]),
    )

    open_q = [
        OpenQuestion(
            question=str(q["question"])[:200],
            candidates=[str(c) for c in (q.get("candidates") or [])][:10],
        )
        for q in facts.open_questions[:10]
    ]

    summary = (facts.summary_override or _summary_from_answers(answers, scenario_ids))[:500]

    profile = TenantProfile(
        generated_at=_now_iso(),
        tenant_id=facts.tenant_id,
        summary=summary,
        scenarios=scenario_refs,
        facts=fact_tree,
        operational_patterns=op,
        key_entities=key_entities,
        taxonomy=Taxonomy(),
        open_questions=open_q,
    )
    return profile.model_dump(mode="json")


def collect_erpnext_init_blueprints(answers: dict[str, Any]) -> list[dict[str, Any]]:
    """Return per-scenario ERPNext init blueprints in execution order.

    Each blueprint is a declarative shape produced by the scenario pack's
    ``erpnext_init_template`` callable. Callers (the lead-agent prompt or a
    future provisioner script) hand each blueprint to the ``erpnext-cli``
    skill in turn — packs are idempotent so running the same blueprint
    twice is safe.
    """
    blueprints: list[dict[str, Any]] = []
    registry = get_registry()
    for pack in selected_scenarios(answers):
        if pack.erpnext_init_template is None:
            continue
        try:
            shape = pack.erpnext_init_template(answers, {}, None)
        except Exception:  # noqa: BLE001 — one bad pack must not break the rest
            continue
        if isinstance(shape, dict):
            blueprints.append(shape)
    # Suppress unused-name lint on registry; kept for future expansion when
    # the composer also needs to look up packs not currently selected.
    del registry
    return blueprints


__all__ = [
    "OnboardingFacts",
    "collect_erpnext_init_blueprints",
    "compose_initial_profile",
]
