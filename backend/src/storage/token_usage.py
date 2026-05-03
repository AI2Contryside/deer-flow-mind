"""Token-usage persistence for DeerFlow LLM calls.

Records per-turn, per-model token usage to Postgres. One row per
``(turn_id, model)`` — a single conversational turn that fans out across
multiple LLM calls (lead agent + middlewares + subagents) accumulates
into the same row via ``INSERT ... ON CONFLICT DO UPDATE``.

The DSN is read from the ``TOKEN_USAGE_DSN`` env var. When unset, the
module degrades to a no-op so DeerFlow runs unchanged in environments
without the table provisioned.

Design notes
------------
- Writes are pushed onto a bounded ``queue.Queue`` from the LangChain
  callback; a daemon thread drains the queue and writes synchronously
  via ``psycopg``. This keeps the agent's hot path off the database
  round-trip and lets the same code serve sync and async callbacks
  without juggling event loops.
- The writer thread is started lazily on first ``record_usage`` call.
- Failures are logged at WARNING and swallowed — token accounting must
  never break an agent run.
"""

from __future__ import annotations

import logging
import os
import queue
import threading
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DSN_ENV_VAR = "TOKEN_USAGE_DSN"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS conversation_turn_token_usage (
    id                bigserial PRIMARY KEY,
    session_id        text  NOT NULL,
    turn_id           text  NOT NULL,
    model             text  NOT NULL,
    input_tokens      int   NOT NULL DEFAULT 0,
    output_tokens     int   NOT NULL DEFAULT 0,
    cached_tokens     int   NOT NULL DEFAULT 0,
    reasoning_tokens  int   NOT NULL DEFAULT 0,
    llm_call_count    int   NOT NULL DEFAULT 1,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT conversation_turn_token_usage_turn_model_uniq
        UNIQUE (turn_id, model)
);
CREATE INDEX IF NOT EXISTS idx_turn_usage_session
    ON conversation_turn_token_usage (session_id, created_at);
"""

UPSERT_SQL = """
INSERT INTO conversation_turn_token_usage
    (session_id, turn_id, model,
     input_tokens, output_tokens, cached_tokens, reasoning_tokens,
     llm_call_count)
VALUES (%s, %s, %s, %s, %s, %s, %s, 1)
ON CONFLICT (turn_id, model) DO UPDATE SET
    input_tokens     = conversation_turn_token_usage.input_tokens     + EXCLUDED.input_tokens,
    output_tokens    = conversation_turn_token_usage.output_tokens    + EXCLUDED.output_tokens,
    cached_tokens    = conversation_turn_token_usage.cached_tokens    + EXCLUDED.cached_tokens,
    reasoning_tokens = conversation_turn_token_usage.reasoning_tokens + EXCLUDED.reasoning_tokens,
    llm_call_count   = conversation_turn_token_usage.llm_call_count   + 1,
    updated_at       = now();
"""


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TokenUsageRecord:
    """A single LLM-call token-usage delta to be merged into a turn."""

    session_id: str
    turn_id: str
    model: str
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    reasoning_tokens: int


# ---------------------------------------------------------------------------
# Background writer
# ---------------------------------------------------------------------------

_QUEUE_MAX = 1000
_SHUTDOWN_SENTINEL: object = object()

_state_lock = threading.Lock()
_queue: queue.Queue | None = None
_writer_thread: threading.Thread | None = None
_schema_ready: bool = False
_disabled: bool = False  # flipped True after a fatal connection error


def _get_dsn() -> str | None:
    dsn = os.environ.get(DSN_ENV_VAR)
    return dsn.strip() if dsn else None


def _ensure_writer_started() -> queue.Queue | None:
    """Lazily start the writer thread. Returns the queue, or None when disabled."""
    global _queue, _writer_thread, _disabled

    if _disabled:
        return None

    dsn = _get_dsn()
    if dsn is None:
        return None

    with _state_lock:
        if _disabled:
            return None
        if _queue is None:
            _queue = queue.Queue(maxsize=_QUEUE_MAX)
        if _writer_thread is None or not _writer_thread.is_alive():
            _writer_thread = threading.Thread(
                target=_writer_loop,
                args=(_queue, dsn),
                name="token-usage-writer",
                daemon=True,
            )
            _writer_thread.start()
        return _queue


def record_usage(record: TokenUsageRecord) -> None:
    """Enqueue a usage record for background persistence.

    Safe to call from any thread, sync or async context. Drops the record
    (with a warning) when the queue is saturated so the caller is never
    blocked on the database.
    """
    q = _ensure_writer_started()
    if q is None:
        return
    try:
        q.put_nowait(record)
    except queue.Full:
        logger.warning("token_usage queue full; dropping record for turn=%s model=%s", record.turn_id, record.model)


def shutdown(timeout: float = 5.0) -> None:
    """Flush the queue and stop the writer thread. Intended for tests / shutdown hooks."""
    global _queue, _writer_thread, _schema_ready
    with _state_lock:
        q = _queue
        t = _writer_thread
    if q is None or t is None:
        return
    q.put(_SHUTDOWN_SENTINEL)
    t.join(timeout=timeout)
    with _state_lock:
        _queue = None
        _writer_thread = None
        _schema_ready = False


def _writer_loop(q: queue.Queue, dsn: str) -> None:
    """Daemon-thread loop that drains the queue and writes to Postgres."""
    global _disabled, _schema_ready

    try:
        import psycopg
    except ImportError:
        logger.warning("psycopg not installed; token usage recording disabled")
        _disabled = True
        return

    try:
        conn = psycopg.connect(dsn, autocommit=True)
    except Exception:
        logger.warning("token_usage: failed to open Postgres connection; disabling", exc_info=True)
        _disabled = True
        return

    try:
        if not _schema_ready:
            try:
                with conn.cursor() as cur:
                    cur.execute(SCHEMA_SQL)
                _schema_ready = True
            except Exception:
                logger.warning("token_usage: failed to ensure schema; disabling", exc_info=True)
                _disabled = True
                return

        while True:
            item = q.get()
            if item is _SHUTDOWN_SENTINEL:
                return
            if not isinstance(item, TokenUsageRecord):
                continue
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        UPSERT_SQL,
                        (
                            item.session_id,
                            item.turn_id,
                            item.model,
                            item.input_tokens,
                            item.output_tokens,
                            item.cached_tokens,
                            item.reasoning_tokens,
                        ),
                    )
            except Exception:
                logger.warning("token_usage: upsert failed for turn=%s model=%s", item.turn_id, item.model, exc_info=True)
                # Try to recover the connection on the next iteration; if it
                # is permanently broken, reconnect once.
                try:
                    conn.close()
                except Exception:
                    pass
                try:
                    conn = psycopg.connect(dsn, autocommit=True)
                except Exception:
                    logger.warning("token_usage: reconnect failed; disabling", exc_info=True)
                    _disabled = True
                    return
    finally:
        try:
            conn.close()
        except Exception:
            pass
