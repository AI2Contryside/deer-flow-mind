"""Record an ERPNext API observation as a single jsonl event.

This is the public entry point the FrappeClient hook calls (M1 ships only the
recorder; the actual hook wiring lives in M3+). The contract:

* Best-effort: every failure is swallowed and warning-logged (§11 decision 1).
* No I/O on the hot path beyond a single appended line.
* Browse-style operations (large get_list, list-without-filter) are dropped
  rather than recorded with weight=browse — keeps the log small and aligns
  with §4.4 anti-pollution.
* Per-thread dedupe also lives here so the summarizer never has to relitigate
  the same ``(thread_id, doctype, name)`` ratio.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any, Literal

from src.agents.tenant_profile import log as log_module
from src.agents.tenant_profile.extractors import (
    Ref,
    extract_refs,
    extract_self_ref,
    refine_payment_entry_party_doctype,
)

logger = logging.getLogger(__name__)

Op = Literal["get_doc", "get_list", "insert", "update", "submit", "cancel", "call_method"]

# Operations whose response is the source-of-truth for refs (read flows).
_READ_OPS: frozenset[str] = frozenset({"get_doc", "get_list"})

# A get_list returning more rows than this is treated as a browse and dropped.
# Matches §4.3: "get_list returning ≤5" is the cut-off for "primary" treatment.
_GET_LIST_PRECISE_THRESHOLD = 5


# ── Per-thread dedupe ──────────────────────────────────────────────────────
#
# Within a single agent thread, the same ``(tenant, doctype, name)`` should
# only contribute one event regardless of how many times tools touch it. The
# state lives in process memory; threads are short-lived so this stays bounded
# in practice. Tests can call ``reset_seen_for_tests``.

_seen_lock = threading.Lock()
_seen_keys: dict[str, set[tuple[str, str, str]]] = {}


def _seen(thread_id: str, tenant_id: str, doctype: str, name: str) -> bool:
    """Return True if this ``(tenant, doctype, name)`` is new for the thread.

    Marks the tuple as seen on first call. False on subsequent calls.
    """
    key = (tenant_id, doctype, name)
    with _seen_lock:
        bucket = _seen_keys.setdefault(thread_id, set())
        if key in bucket:
            return False
        bucket.add(key)
        return True


def reset_seen_for_tests() -> None:
    """Drop the cached per-thread dedupe state. Tests only."""
    with _seen_lock:
        _seen_keys.clear()


# ── Public API ─────────────────────────────────────────────────────────────


def record_event(
    *,
    tenant_id: str,
    op: Op,
    doctype: str,
    name: str | None = None,
    payload: dict[str, Any] | None = None,
    response: Any = None,
    skill: str | None = None,
    method: str | None = None,
    thread_id: str | None = None,
    now: datetime | None = None,
) -> bool:
    """Record one ERPNext API observation.

    ``payload`` is the request body (insert/update/call_method) when present;
    ``response`` is what ERPNext returned (for get_doc/get_list/call_method).
    Either or both can be None.

    Returns True if the event was appended, False if it was dropped (browse,
    deduped, validation failure) OR if the underlying log write failed. The
    boolean is for tests; production callers should ignore it.
    """
    try:
        return _record_event_inner(
            tenant_id=tenant_id,
            op=op,
            doctype=doctype,
            name=name,
            payload=payload,
            response=response,
            skill=skill,
            method=method,
            thread_id=thread_id,
            now=now,
        )
    except Exception as exc:
        # Last-resort safety net. record_event is best-effort by contract.
        logger.warning("tenant_profile: observer crashed for tenant %r doctype %r: %s", tenant_id, doctype, exc)
        return False


def _record_event_inner(
    *,
    tenant_id: str,
    op: Op,
    doctype: str,
    name: str | None,
    payload: dict[str, Any] | None,
    response: Any,
    skill: str | None,
    method: str | None,
    thread_id: str | None,
    now: datetime | None,
) -> bool:
    if not isinstance(tenant_id, str) or not tenant_id:
        return False
    if not isinstance(doctype, str) or not doctype:
        return False
    if op not in _READ_OPS and op not in {"insert", "update", "submit", "cancel", "call_method"}:
        return False

    # Drop full-table browses outright. ``get_list`` with no filter and a
    # large response is typical "show me everything" UX, not operational use.
    if op == "get_list" and _is_browse_response(response):
        return False

    refs = _collect_refs(op=op, doctype=doctype, name=name, payload=payload, response=response)

    # Per-thread dedupe: filter both self and link refs so noisy loops don't
    # inflate counts. Self goes through the same gate (a thread that calls
    # ``get_doc("Customer", "X")`` ten times only contributes once).
    if thread_id:
        refs = [r for r in refs if _seen(thread_id, tenant_id, r.doctype, r.name)]
    if not refs and op in _READ_OPS:
        # No new info — read-side calls with everything deduped don't
        # warrant a log entry. Writes still record to preserve the audit
        # trail (a write tells us the user *acted on* the entity).
        return False

    event: dict[str, Any] = {
        "ts": (now or datetime.now(UTC)).isoformat().replace("+00:00", "Z"),
        "op": op,
        "doctype": doctype,
        "extracted_refs": [{"doctype": r.doctype, "name": r.name, "field": r.field, "weight": r.weight} for r in refs],
    }
    if name:
        event["name"] = name
    if skill:
        event["skill"] = skill
    if method:
        event["method"] = method
    if thread_id:
        event["thread_id"] = thread_id

    return log_module.append_event(tenant_id, event)


# ── Internals ──────────────────────────────────────────────────────────────


def _is_browse_response(response: Any) -> bool:
    """A get_list response is a browse if it returned more than the precise threshold."""
    if isinstance(response, list):
        return len(response) > _GET_LIST_PRECISE_THRESHOLD
    return False


def _collect_refs(
    *,
    op: Op,
    doctype: str,
    name: str | None,
    payload: dict[str, Any] | None,
    response: Any,
) -> list[Ref]:
    refs: list[Ref] = []

    self_ref = extract_self_ref(doctype, name)
    if self_ref is not None:
        refs.append(self_ref)

    # For get_list with a small response, each row's ``name`` becomes a self-ref.
    if op == "get_list" and isinstance(response, list):
        for row in response[:_GET_LIST_PRECISE_THRESHOLD]:
            row_name = row.get("name") if isinstance(row, dict) else None
            row_self = extract_self_ref(doctype, row_name)
            if row_self is not None:
                refs.append(row_self)

    # Link-field refs from request payload (insert/update/call_method).
    payload_refs = extract_refs(doctype, payload)
    if doctype == "Payment Entry":
        payload_refs = refine_payment_entry_party_doctype(payload, payload_refs)
    refs.extend(payload_refs)

    # Link-field refs from response payload (get_doc, post-mutation echoes).
    if isinstance(response, dict):
        resp_refs = extract_refs(doctype, response)
        if doctype == "Payment Entry":
            resp_refs = refine_payment_entry_party_doctype(response, resp_refs)
        refs.extend(resp_refs)

    return _dedupe(refs)


def _dedupe(refs: Iterable[Ref]) -> list[Ref]:
    """Drop intra-event duplicates, preserving first-seen order.

    A Sales Order with the same item appearing twice in ``items[]`` should
    only emit one Item ref per event — the summarizer counts events, not
    line items.
    """
    seen: set[tuple[str, str]] = set()
    out: list[Ref] = []
    for r in refs:
        key = (r.doctype, r.name)
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out
