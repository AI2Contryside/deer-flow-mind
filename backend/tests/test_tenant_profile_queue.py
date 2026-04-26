"""Unit tests for tenant_profile.queue (M3) — debounced summarize trigger."""

from __future__ import annotations

import threading
import time

from src.agents.tenant_profile.queue import SummarizeQueue


def test_enqueue_fires_after_debounce_window() -> None:
    fired: list[str] = []
    q = SummarizeQueue(handler=fired.append, debounce_seconds=0.05)
    q.enqueue("acme")
    time.sleep(0.15)
    assert fired == ["acme"]


def test_repeated_enqueue_within_window_collapses_to_one() -> None:
    fired: list[str] = []
    q = SummarizeQueue(handler=fired.append, debounce_seconds=0.1)
    for _ in range(10):
        q.enqueue("acme")
        time.sleep(0.01)
    time.sleep(0.2)
    assert fired == ["acme"]


def test_in_flight_blocks_new_enqueue() -> None:
    started = threading.Event()
    release = threading.Event()
    fired: list[str] = []

    def slow_handler(t: str) -> None:
        started.set()
        release.wait(timeout=2.0)
        fired.append(t)

    q = SummarizeQueue(handler=slow_handler, debounce_seconds=0.01)
    q.enqueue("acme")
    started.wait(timeout=1.0)

    # Trying to enqueue while in-flight should be a no-op.
    q.enqueue("acme")
    assert q.pending_count == 0

    release.set()
    # Give the worker thread a moment to drain.
    for _ in range(20):
        if fired:
            break
        time.sleep(0.05)
    assert fired == ["acme"]


def test_flush_fires_synchronously() -> None:
    fired: list[str] = []
    q = SummarizeQueue(handler=fired.append, debounce_seconds=10.0)
    q.enqueue("acme")
    q.flush("acme")
    assert fired == ["acme"]


def test_cancel_drops_without_firing() -> None:
    fired: list[str] = []
    q = SummarizeQueue(handler=fired.append, debounce_seconds=0.05)
    q.enqueue("acme")
    q.cancel("acme")
    time.sleep(0.15)
    assert fired == []


def test_handler_exception_is_logged_not_propagated() -> None:
    def boom(t: str) -> None:
        raise RuntimeError(f"crash on {t}")

    q = SummarizeQueue(handler=boom, debounce_seconds=0.05)
    q.enqueue("acme")
    time.sleep(0.2)
    # The queue should still be operable after a handler crash.
    fired: list[str] = []
    q._handler = fired.append  # type: ignore[attr-defined]
    q.enqueue("bravo")
    time.sleep(0.15)
    assert fired == ["bravo"]


def test_enqueue_rejects_empty_tenant_id() -> None:
    fired: list[str] = []
    q = SummarizeQueue(handler=fired.append, debounce_seconds=0.05)
    q.enqueue("")  # type: ignore[arg-type]
    q.enqueue(None)  # type: ignore[arg-type]
    time.sleep(0.15)
    assert fired == []


def test_force_flag_persists_across_collapses() -> None:
    """If a force-true enqueue is followed by a force-false one, the result
    should still be force-true (don't lose the user's explicit refresh request)."""
    seen_args: list[tuple[str, bool]] = []

    def capture(t: str) -> None:
        # We don't expose force on the handler signature in production —
        # this test inspects pending state directly.
        pass

    q = SummarizeQueue(handler=capture, debounce_seconds=10.0)
    q.enqueue("acme", force=True)
    q.enqueue("acme", force=False)
    pending = q._pending["acme"]  # type: ignore[attr-defined]
    assert pending.force is True
    seen_args.append((pending.tenant_id, pending.force))
    q.cancel("acme")
