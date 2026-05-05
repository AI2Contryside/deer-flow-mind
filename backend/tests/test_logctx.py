"""Tests for src.logctx — context vars, header round-trips, FastAPI
middleware, and logging filter integration.
"""

from __future__ import annotations

import logging

import pytest

from src.logctx import (
    HEADER_LOG_ID,
    HEADER_SESSION_ID,
    HEADER_TENANT_ID,
    HEADER_USER_ID,
    LogContextFilter,
    bind,
    bind_fields,
    build_outbound_headers,
    current_fields,
    ensure_log_id,
    headers_from_context,
    install_log_filter,
)
from src.logctx.context import Fields, fields_from_headers


def test_bind_isolates_to_block():
    assert current_fields().tenant_id == ""
    with bind(tenant_id="t-1", user_id="u-1") as f:
        assert f.tenant_id == "t-1"
        assert current_fields().user_id == "u-1"
    assert current_fields().tenant_id == ""


def test_bind_skips_empty_and_none():
    with bind(tenant_id="t-1"):
        with bind(tenant_id=None, user_id=""):
            # outer tenant must survive — empty/None did not clear it
            assert current_fields().tenant_id == "t-1"
            assert current_fields().user_id == ""


def test_bind_rejects_unknown_field():
    with pytest.raises(KeyError):
        with bind(unknown_field="x"):
            pass


def test_ensure_log_id_generates_once_then_idempotent():
    tokens = bind_fields(Fields())  # explicit empty bind so no leak between tests
    try:
        first = ensure_log_id()
        second = ensure_log_id()
        assert first and first == second
    finally:
        for tok in reversed(tokens):
            tok.var.reset(tok)


def test_headers_round_trip():
    with bind(tenant_id="t-1", user_id="u-2", log_id="l-3", session_id="s-4"):
        outbound = headers_from_context()
        assert outbound[HEADER_TENANT_ID] == "t-1"
        assert outbound[HEADER_USER_ID] == "u-2"
        assert outbound[HEADER_LOG_ID] == "l-3"
        assert outbound[HEADER_SESSION_ID] == "s-4"

    parsed = fields_from_headers(outbound)
    assert parsed.tenant_id == "t-1"
    assert parsed.session_id == "s-4"


def test_headers_case_insensitive():
    raw = {"x-tenant-id": "t-x", "X-LOG-ID": "l-x"}
    parsed = fields_from_headers(raw)
    assert parsed.tenant_id == "t-x"
    assert parsed.log_id == "l-x"


def test_build_outbound_headers_extras_win():
    with bind(tenant_id="t-from-ctx"):
        merged = build_outbound_headers({HEADER_TENANT_ID: "t-override", "X-Other": "1"})
    assert merged[HEADER_TENANT_ID] == "t-override"
    assert merged["X-Other"] == "1"


def test_log_filter_injects_fields(caplog):
    install_log_filter(level=logging.INFO)
    logger = logging.getLogger("logctx.test")
    with caplog.at_level(logging.INFO, logger="logctx.test"):
        # Ensure caplog handler also has our filter so attributes appear.
        for h in caplog.handler, *logging.getLogger().handlers:
            if not any(isinstance(f, LogContextFilter) for f in h.filters):
                h.addFilter(LogContextFilter())

        with bind(tenant_id="t-log", log_id="l-log"):
            logger.info("hello")

    record = next(r for r in caplog.records if r.message == "hello")
    assert getattr(record, "tenant_id") == "t-log"
    assert getattr(record, "log_id") == "l-log"
    # Unset fields render as "-" so format strings stay aligned.
    assert getattr(record, "user_id") == "-"


def _run_middleware(scope_headers: list[tuple[bytes, bytes]]):
    """Drive IdentityMiddleware against a stub ASGI app via asyncio.run."""
    import asyncio

    from src.logctx import IdentityMiddleware

    captured: dict[str, object] = {}
    sent: list[dict] = []

    async def app(scope, receive, send):
        captured["tenant"] = current_fields().tenant_id
        captured["log_id"] = current_fields().log_id
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    async def send(msg):
        sent.append(msg)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def driver():
        await IdentityMiddleware(app)({"type": "http", "headers": scope_headers}, receive, send)

    asyncio.run(driver())
    return captured, sent


def test_identity_middleware_lifts_headers_and_echoes_log_id():
    captured, sent = _run_middleware([(b"x-tenant-id", b"t-mw"), (b"x-log-id", b"l-mw")])
    assert captured["tenant"] == "t-mw"
    assert captured["log_id"] == "l-mw"
    start = next(m for m in sent if m["type"] == "http.response.start")
    echoed = dict(start["headers"])
    assert echoed[b"x-log-id"] == b"l-mw"


def test_identity_middleware_generates_log_id_when_absent():
    captured, sent = _run_middleware([])
    assert captured["log_id"] != ""
    start = next(m for m in sent if m["type"] == "http.response.start")
    headers = dict(start["headers"])
    assert headers[b"x-log-id"].decode() == captured["log_id"]
