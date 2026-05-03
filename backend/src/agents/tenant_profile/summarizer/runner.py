"""Drive a single summarize run end-to-end.

Pipeline:
  1. Read inputs (facts is a pass-through; previous profile if any; usage log).
  2. Roll the usage log up into a per-doctype top-N preview.
  3. Build the user prompt and call the LLM.
  4. Parse + pydantic-validate the response into ``TenantProfile``.
  5. Enforce that every name in the profile traces back to facts or the usage
     window (defensive — the prompt forbids fabrication; this catches bugs).
  6. Write the profile, mark meta.json finished, and rotate/archive the log.

The LLM call is injected so tests pass a deterministic stub. Production
constructs the model from app config in ``_default_llm`` lazily.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from src.agents.tenant_profile import archive as archive_module
from src.agents.tenant_profile import facts as facts_module
from src.agents.tenant_profile import log as log_module
from src.agents.tenant_profile import store as store_module
from src.agents.tenant_profile import trigger as trigger_module
from src.agents.tenant_profile.config import get_tenant_profile_config
from src.agents.tenant_profile.summarizer import prompt as prompt_module
from src.agents.tenant_profile.summarizer.schema import TenantProfile, collect_entity_names

logger = logging.getLogger(__name__)

# A single LLM call: takes (system_prompt, user_prompt) and returns the raw
# string the model produced. Wrapping at this layer keeps tests trivial.
LlmInvoker = Callable[[str, str], str]


@dataclass(frozen=True)
class SummarizeInput:
    """Everything fed to the prompt — exposed so tests can inspect it."""

    tenant_id: str
    now_iso: str
    facts: dict[str, Any]
    previous_profile: dict[str, Any] | None
    usage_window: dict[str, Any]
    config: dict[str, Any]


@dataclass(frozen=True)
class SummarizeOutcome:
    """What ``run_summarize`` produced (for tests + callers that care)."""

    success: bool
    profile: TenantProfile | None
    error: str | None
    skipped_reason: str | None = None


# ── Public API ────────────────────────────────────────────────────────────


def run_summarize(
    tenant_id: str,
    *,
    force: bool = False,
    user_email: str | None = None,
    llm: LlmInvoker | None = None,
    now: datetime | None = None,
) -> SummarizeOutcome:
    """Entry point used by the queue. Idempotent on failure (state preserved)."""
    decision = trigger_module.should_summarize(tenant_id, force=force, now=now)
    if not decision.should_run:
        return SummarizeOutcome(success=False, profile=None, error=None, skipped_reason=decision.reason)

    trigger_module.mark_summarize_started(tenant_id, now=now)

    try:
        inputs = build_summarize_input(tenant_id, user_email=user_email, now=now)
        # Bind tenant_id into the default LLM via partial so the
        # token-usage recorder can attribute this background call to a
        # synthetic ``bg:profile:<tenant>:…`` turn. Custom ``llm`` injections
        # (used by tests / fakes) bypass this — they don't talk to a real
        # provider, so there's nothing to record.
        from functools import partial as _partial

        llm_fn = llm if llm is not None else _partial(_default_llm, tenant_id=tenant_id)
        raw = llm_fn(prompt_module.SYSTEM_PROMPT, _render_user_prompt(inputs))
        profile = _parse_and_validate(raw, inputs)
    except SummarizeError as exc:
        trigger_module.mark_summarize_finished(tenant_id, success=False, error=str(exc), now=now)
        return SummarizeOutcome(success=False, profile=None, error=str(exc))
    except Exception as exc:
        # Last-resort safety net — runner failure must NOT take down the queue.
        logger.exception("tenant_profile: summarize crashed for tenant %r", tenant_id)
        trigger_module.mark_summarize_finished(tenant_id, success=False, error=f"{type(exc).__name__}: {exc}", now=now)
        return SummarizeOutcome(success=False, profile=None, error=f"{type(exc).__name__}: {exc}")

    if not store_module.write_profile(tenant_id, profile.model_dump()):
        # Persist failed — meta should reflect the error so we don't think
        # we shipped when we didn't.
        trigger_module.mark_summarize_finished(tenant_id, success=False, error="failed to write profile.json", now=now)
        return SummarizeOutcome(success=False, profile=profile, error="failed to write profile.json")

    trigger_module.mark_summarize_finished(tenant_id, success=True, now=now)

    # Rotate + archive the consumed log so the next window starts empty.
    archive_module.archive_log(tenant_id)

    return SummarizeOutcome(success=True, profile=profile, error=None)


def build_summarize_input(
    tenant_id: str,
    *,
    user_email: str | None,
    now: datetime | None,
) -> SummarizeInput:
    """Assemble the dict bundle the prompt sees. Pure I/O — no LLM."""
    cfg = get_tenant_profile_config()
    now_dt = now or datetime.now(UTC)
    now_iso = now_dt.isoformat().replace("+00:00", "Z")

    facts_bundle = facts_module.bootstrap_facts(
        user_email=user_email,
        timeout_seconds=cfg.facts.sync_bootstrap_timeout_seconds,
        now_iso=now_iso,
    )
    facts = facts_module.facts_to_dict(facts_bundle)

    previous_profile = store_module.read_profile(tenant_id)
    events = log_module.read_events(tenant_id)
    usage_window = _rollup_events(events, now_iso=now_iso)

    return SummarizeInput(
        tenant_id=tenant_id,
        now_iso=now_iso,
        facts=facts,
        previous_profile=previous_profile,
        usage_window=usage_window,
        config={
            "top_k": cfg.top_k.model_dump(),
            "recently_quiet_runs_to_drop": cfg.decay.recently_quiet_runs_to_drop,
            "output_token_budget": cfg.summarizer.output_token_budget,
        },
    )


# ── Internals ─────────────────────────────────────────────────────────────


class SummarizeError(RuntimeError):
    """Validation / parsing failure. Distinct so the runner can branch on it."""


def _render_user_prompt(inputs: SummarizeInput) -> str:
    return prompt_module.USER_PROMPT_PREAMBLE.format(
        tenant_id=inputs.tenant_id,
        now_iso=inputs.now_iso,
        facts_json=json.dumps(inputs.facts, ensure_ascii=False, indent=2),
        previous_profile_json=json.dumps(inputs.previous_profile, ensure_ascii=False, indent=2) if inputs.previous_profile is not None else "null",
        usage_window_json=json.dumps(inputs.usage_window, ensure_ascii=False, indent=2),
        config_json=json.dumps(inputs.config, ensure_ascii=False, indent=2),
    )


def _rollup_events(events: list[dict[str, Any]], *, now_iso: str) -> dict[str, Any]:
    """Group raw events by (target_doctype, target_name).

    Output shape (matches §5.2 of the design doc):
        {
          "from": <first event ts>,
          "to": <now_iso>,
          "total_events": int,
          "by_doctype": [
            {
              "doctype": "Customer",
              "total": 42,
              "primary": 38, "secondary": 4, "browse": 0,
              "top": [
                {"name": "FOOCORP-001", "count": 24, "first_seen": "...",
                 "last_seen": "...", "skills": [...]}
              ]
            }
          ]
        }
    Entries are sorted by primary count desc, secondary count desc, last_seen desc.
    Top-N per doctype is capped to keep the prompt budget bounded.
    """
    if not events:
        return {"from": None, "to": now_iso, "total_events": 0, "by_doctype": []}

    # Tally per (doctype, name).
    per_entity: dict[tuple[str, str], dict[str, Any]] = {}
    per_doctype_totals: dict[str, dict[str, int]] = {}

    first_ts: str | None = None
    for evt in events:
        ts = evt.get("ts")
        if isinstance(ts, str) and (first_ts is None or ts < first_ts):
            first_ts = ts
        skill = evt.get("skill")
        for ref in evt.get("extracted_refs", []) or []:
            if not isinstance(ref, dict):
                continue
            doctype = ref.get("doctype")
            name = ref.get("name")
            weight = ref.get("weight", "primary")
            if not isinstance(doctype, str) or not isinstance(name, str):
                continue

            doc_totals = per_doctype_totals.setdefault(doctype, {"total": 0, "primary": 0, "secondary": 0, "browse": 0})
            doc_totals["total"] += 1
            if weight in ("primary", "secondary", "browse"):
                doc_totals[weight] += 1

            entry = per_entity.setdefault(
                (doctype, name),
                {
                    "name": name,
                    "count": 0,
                    "primary_count": 0,
                    "first_seen": ts,
                    "last_seen": ts,
                    "skills": set(),
                },
            )
            entry["count"] += 1
            if weight == "primary":
                entry["primary_count"] += 1
            if isinstance(ts, str):
                if not entry["first_seen"] or ts < entry["first_seen"]:
                    entry["first_seen"] = ts
                if not entry["last_seen"] or ts > entry["last_seen"]:
                    entry["last_seen"] = ts
            if isinstance(skill, str):
                entry["skills"].add(skill)

    # Bucket per doctype with cap.
    by_doctype: list[dict[str, Any]] = []
    by_doctype_index: dict[str, list[dict[str, Any]]] = {}
    for (doctype, _name), entry in per_entity.items():
        by_doctype_index.setdefault(doctype, []).append(entry)

    for doctype, entries in by_doctype_index.items():
        entries.sort(key=lambda e: (e["primary_count"], e["count"], e["last_seen"] or ""), reverse=True)
        # 50-row cap per design §5.2 — keeps prompt bounded even with thousands of events.
        capped = entries[:50]
        # Convert sets → sorted lists for stable JSON output.
        cleaned = [
            {
                "name": e["name"],
                "count": e["count"],
                "primary_count": e["primary_count"],
                "first_seen": e["first_seen"],
                "last_seen": e["last_seen"],
                "skills": sorted(e["skills"]),
            }
            for e in capped
        ]
        totals = per_doctype_totals[doctype]
        by_doctype.append(
            {
                "doctype": doctype,
                "total": totals["total"],
                "primary": totals["primary"],
                "secondary": totals["secondary"],
                "browse": totals["browse"],
                "top": cleaned,
            }
        )
    by_doctype.sort(key=lambda d: d["primary"], reverse=True)

    return {
        "from": first_ts,
        "to": now_iso,
        "total_events": len(events),
        "by_doctype": by_doctype,
    }


def _parse_and_validate(raw: str, inputs: SummarizeInput) -> TenantProfile:
    """Parse the LLM's JSON response, run pydantic, then assert name eligibility."""
    raw = (raw or "").strip()
    if not raw:
        raise SummarizeError("LLM returned empty output")

    # Strip markdown fences if the model ignored the "no fences" instruction.
    if raw.startswith("```"):
        first_nl = raw.find("\n")
        last_fence = raw.rfind("```")
        if first_nl != -1 and last_fence > first_nl:
            raw = raw[first_nl + 1 : last_fence].strip()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SummarizeError(f"LLM output is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise SummarizeError("LLM output JSON must be an object")

    # Force tenant_id and generated_at to inputs — the LLM has no business
    # making these up.
    data["tenant_id"] = inputs.tenant_id
    data.setdefault("generated_at", inputs.now_iso)
    data.setdefault("facts", inputs.facts)
    # Upgrade legacy v1/v2 dicts to v3 (adds scenarios=[]).
    from src.agents.tenant_profile.summarizer.schema import upgrade_profile_dict

    upgrade_profile_dict(data)

    try:
        profile = TenantProfile.model_validate(data)
    except ValidationError as exc:
        raise SummarizeError(f"profile failed schema validation: {exc.errors()[:3]}") from exc

    _enforce_name_eligibility(profile, inputs)
    return profile


def _enforce_name_eligibility(profile: TenantProfile, inputs: SummarizeInput) -> None:
    """Reject profiles that include names not justified by the input bundle.

    Eligibility = name appears in either:
      * usage_window.by_doctype.<doctype>.top.*.name (matching doctype), or
      * any string anywhere inside facts (cross-doctype wildcard so e.g. a
        Company name from facts can appear in operational_patterns).
    """
    eligible = _collect_eligible_names(inputs)
    any_bucket = eligible.get("__any__", set())
    fabricated: list[tuple[str, str]] = []
    for dt, nm in collect_entity_names(profile):
        if nm in eligible.get(dt, set()):
            continue
        if nm in any_bucket:
            continue
        fabricated.append((dt, nm))
    if fabricated:
        sample = fabricated[:5]
        raise SummarizeError(f"profile contains names absent from facts/usage_window: {sample}")


def _collect_eligible_names(inputs: SummarizeInput) -> dict[str, set[str]]:
    """Build the (doctype → set[name]) map of allowable names.

    For names found in arbitrary facts strings we don't know which doctype
    they belong to, so we stash them under a sentinel ``__any__`` bucket
    that the eligibility check accepts as a wildcard.

    Names already in ``previous_profile`` are also eligible — the decay
    rule (TENANT_PROFILE_DESIGN.md §5.6) requires entities to survive
    one or two summarize windows of inactivity before being dropped, so
    the LLM has to be allowed to carry them over.
    """
    eligible: dict[str, set[str]] = {}

    for entry in inputs.usage_window.get("by_doctype", []) or []:
        if not isinstance(entry, dict):
            continue
        doctype = entry.get("doctype")
        if not isinstance(doctype, str):
            continue
        bucket = eligible.setdefault(doctype, set())
        for top in entry.get("top", []) or []:
            if isinstance(top, dict) and isinstance(top.get("name"), str):
                bucket.add(top["name"])

    if inputs.previous_profile:
        _collect_previous_profile_names(inputs.previous_profile, eligible)

    any_bucket: set[str] = set()
    _collect_strings(inputs.facts, any_bucket)
    eligible["__any__"] = any_bucket
    return eligible


def _collect_previous_profile_names(previous: dict[str, Any], eligible: dict[str, set[str]]) -> None:
    """Carry over every key_entity / taxonomy name from the previous profile."""
    ke = previous.get("key_entities") or {}
    flat_groups: dict[str, str] = {
        "customers": "Customer",
        "suppliers": "Supplier",
        "items": "Item",
        "warehouses": "Warehouse",
        "price_lists": "Price List",
        "uoms": "UOM",
        "sales_tax_templates": "Sales Taxes and Charges Template",
        "purchase_tax_templates": "Purchase Taxes and Charges Template",
        "payment_terms_templates": "Payment Terms Template",
    }
    for slot, doctype in flat_groups.items():
        for ref in ke.get(slot) or []:
            if isinstance(ref, dict) and isinstance(ref.get("name"), str):
                eligible.setdefault(doctype, set()).add(ref["name"])

    bucketed_groups: dict[str, str] = {
        "accounts_by_root_type": "Account",
        "cost_centers_by_parent": "Cost Center",
    }
    for slot, doctype in bucketed_groups.items():
        buckets = ke.get(slot) or {}
        if isinstance(buckets, dict):
            for refs in buckets.values():
                if not isinstance(refs, list):
                    continue
                for ref in refs:
                    if isinstance(ref, dict) and isinstance(ref.get("name"), str):
                        eligible.setdefault(doctype, set()).add(ref["name"])

    tax = previous.get("taxonomy") or {}
    tax_bucketed: dict[str, str] = {
        "item_groups_by_parent": "Item Group",
        "customer_groups_by_parent": "Customer Group",
        "supplier_groups_by_parent": "Supplier Group",
        "territories_by_parent": "Territory",
    }
    for slot, doctype in tax_bucketed.items():
        buckets = tax.get(slot) or {}
        if isinstance(buckets, dict):
            for refs in buckets.values():
                if not isinstance(refs, list):
                    continue
                for ref in refs:
                    if isinstance(ref, dict) and isinstance(ref.get("name"), str):
                        eligible.setdefault(doctype, set()).add(ref["name"])
    for slot, doctype in (("sales_persons", "Sales Person"), ("brands", "Brand")):
        for ref in tax.get(slot) or []:
            if isinstance(ref, dict) and isinstance(ref.get("name"), str):
                eligible.setdefault(doctype, set()).add(ref["name"])


def _collect_strings(node: Any, bucket: set[str]) -> None:
    if isinstance(node, str):
        if node:
            bucket.add(node)
    elif isinstance(node, dict):
        for v in node.values():
            _collect_strings(v, bucket)
    elif isinstance(node, list):
        for v in node:
            _collect_strings(v, bucket)


def _default_llm(system_prompt: str, user_prompt: str, *, tenant_id: str | None = None) -> str:
    """Production LLM invocation. Imported lazily so test paths don't need
    the langchain/anthropic stack on hand."""
    from langchain_core.messages import HumanMessage, SystemMessage

    from src.models import create_chat_model
    from src.storage.token_usage import background_invoke_config

    cfg = get_tenant_profile_config().summarizer.model
    chat = create_chat_model(name=cfg.model, thinking_enabled=cfg.thinking_enabled)
    response = chat.invoke(
        [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)],
        config=background_invoke_config("profile", tenant_id or "unknown"),
    )
    content = getattr(response, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        # Some providers return a list of content blocks.
        return "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content)
    return str(content or "")
