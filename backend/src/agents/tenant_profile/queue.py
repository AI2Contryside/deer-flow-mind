"""Debounced summarize queue, modeled on ``src.agents.memory.queue``.

We use the same shape (per-tenant dedupe + threading.Timer) so behaviour
under bursty traffic matches what the rest of the agent already does.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Same default as memory updates; cheap to override per-test.
_DEFAULT_DEBOUNCE_SECONDS = 30.0

# What to do when the timer fires. Decoupled so tests can plug in a
# synchronous handler instead of importing the LLM-bound runner.
SummarizeHandler = Callable[[str], None]


@dataclass
class _Pending:
    tenant_id: str
    force: bool


class SummarizeQueue:
    """One-tenant-at-a-time, debounced trigger of the summarizer."""

    def __init__(self, handler: SummarizeHandler, debounce_seconds: float = _DEFAULT_DEBOUNCE_SECONDS) -> None:
        self._handler = handler
        self._debounce = debounce_seconds
        self._lock = threading.Lock()
        self._timers: dict[str, threading.Timer] = {}
        self._pending: dict[str, _Pending] = {}
        self._in_flight: set[str] = set()

    def enqueue(self, tenant_id: str, *, force: bool = False) -> None:
        """Schedule a summarize for ``tenant_id`` after the debounce window.

        Repeated calls within the window collapse into one. ``force=True``
        sticks across collapses (so a coalesced "force + non-force" still
        forces).
        """
        if not isinstance(tenant_id, str) or not tenant_id:
            return
        with self._lock:
            if tenant_id in self._in_flight:
                # Don't pile up while one is running; once it finishes the
                # next ``enqueue`` call will schedule again.
                return
            existing = self._pending.get(tenant_id)
            self._pending[tenant_id] = _Pending(
                tenant_id=tenant_id,
                force=force or (existing.force if existing is not None else False),
            )
            timer = self._timers.get(tenant_id)
            if timer is not None:
                timer.cancel()
            t = threading.Timer(self._debounce, self._fire, args=(tenant_id,))
            t.daemon = True
            self._timers[tenant_id] = t
            t.start()

    def flush(self, tenant_id: str | None = None) -> None:
        """Synchronously fire the queued summarize for ``tenant_id`` (or all).

        Used by tests and by graceful shutdown.
        """
        with self._lock:
            if tenant_id is None:
                tenants = list(self._pending.keys())
            else:
                tenants = [tenant_id] if tenant_id in self._pending else []
            for t in tenants:
                timer = self._timers.pop(t, None)
                if timer is not None:
                    timer.cancel()
        for t in tenants:
            self._fire(t)

    def cancel(self, tenant_id: str | None = None) -> None:
        """Drop pending entries without firing. Tests only."""
        with self._lock:
            if tenant_id is None:
                for timer in self._timers.values():
                    timer.cancel()
                self._timers.clear()
                self._pending.clear()
                self._in_flight.clear()
                return
            timer = self._timers.pop(tenant_id, None)
            if timer is not None:
                timer.cancel()
            self._pending.pop(tenant_id, None)
            self._in_flight.discard(tenant_id)

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    # ── Internals ─────────────────────────────────────────────────────────

    def _fire(self, tenant_id: str) -> None:
        with self._lock:
            entry = self._pending.pop(tenant_id, None)
            self._timers.pop(tenant_id, None)
            if entry is None or tenant_id in self._in_flight:
                return
            self._in_flight.add(tenant_id)
        try:
            self._handler(tenant_id)
        except Exception:
            logger.exception("tenant_profile: summarize handler crashed for tenant %r", tenant_id)
        finally:
            with self._lock:
                self._in_flight.discard(tenant_id)


_singleton: SummarizeQueue | None = None
_singleton_lock = threading.Lock()


def get_summarize_queue(handler: SummarizeHandler | None = None, debounce_seconds: float | None = None) -> SummarizeQueue:
    """Lazily-constructed process-wide queue.

    The first caller wins; subsequent calls ignore ``handler`` /
    ``debounce_seconds``. Tests should call ``reset_summarize_queue_for_tests``
    between cases to swap handlers.
    """
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            if handler is None:
                # Default handler: import the runner lazily so test-only
                # callers don't drag in LLM deps just to inspect state.
                from src.agents.tenant_profile.summarizer.runner import run_summarize

                handler = run_summarize
            _singleton = SummarizeQueue(handler, debounce_seconds if debounce_seconds is not None else _DEFAULT_DEBOUNCE_SECONDS)
        return _singleton


def reset_summarize_queue_for_tests() -> None:
    global _singleton
    with _singleton_lock:
        if _singleton is not None:
            _singleton.cancel()
        _singleton = None
