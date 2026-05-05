"""Mark orphaned LangGraph runs as ``interrupted`` at startup.

When the LangGraph dev server (``langgraph_runtime_inmem``) restarts, runs that
were ``status="running"`` at the moment of shutdown are persisted to disk
(``.langgraph_api/.langgraph_ops.pckl``) and reloaded as-is on the next boot.
Because ``max_workers=1`` is the default and the in-memory queue blocks
pending runs behind anything still in ``running`` state, a single zombie run
permanently wedges the entire queue for that thread (and others, if it holds
the lone worker slot).

This module sweeps the persisted run store **before workers come up** and
flips every ``status="running"`` row to ``status="interrupted"``. Any run that
is genuinely active at the moment this runs would have to have been picked up
by a worker — but ``make_checkpointer()`` (the call site) is invoked by the
LangGraph startup pipeline strictly before queue / worker boot, so by
construction nothing is legitimately ``running`` here.

If ``langgraph_runtime_inmem`` is not the active runtime (e.g. LangSmith
Deployment in production), the import fails and this becomes a no-op.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


def mark_orphan_runs_as_interrupted() -> int:
    """Flip every ``status="running"`` run in the in-memory store to
    ``status="interrupted"``.

    Returns the number of runs mutated. Returns ``0`` (and logs at debug) if
    the in-memory runtime is not installed or the store has no runs.
    """
    store = _load_global_store()
    if store is None:
        return 0

    runs: list[dict[str, Any]] = store.get("runs") or []
    if not runs:
        return 0

    now = datetime.now(UTC)
    orphan_ids: list[str] = []

    for run in runs:
        if run.get("status") != "running":
            continue
        run["status"] = "interrupted"
        run["updated_at"] = now
        orphan_ids.append(str(run.get("run_id")))

    if orphan_ids:
        logger.warning(
            "Marked %d orphan run(s) as interrupted at startup: %s",
            len(orphan_ids),
            ", ".join(orphan_ids),
        )

    return len(orphan_ids)


def _load_global_store() -> Any | None:
    """Return the in-memory runtime's GLOBAL_STORE, or ``None`` if the runtime
    is not installed (e.g. running against a hosted LangGraph deployment)."""
    try:
        from langgraph_runtime_inmem.database import GLOBAL_STORE
    except ImportError:
        logger.debug("langgraph_runtime_inmem not installed; skipping orphan run cleanup")
        return None
    return GLOBAL_STORE
