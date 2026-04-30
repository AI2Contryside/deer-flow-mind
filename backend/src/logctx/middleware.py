"""FastAPI ASGI middleware that lifts identity headers into logctx.

Order in app.py: install before any router-bearing middleware so every
endpoint's logs carry tenant_id / user_id / log_id / session_id without
each handler having to read headers itself.
"""

from __future__ import annotations

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from src.logctx.context import (
    HEADER_LOG_ID,
    bind_fields,
    ensure_log_id,
    fields_from_headers,
)


class IdentityMiddleware:
    """Pure-ASGI middleware. Extracts X-* headers, binds them on the
    current task's logctx, generates a fresh log_id when missing, and
    echoes log_id back on the response.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return

        raw_headers = scope.get("headers") or []
        # ASGI headers are list[tuple[bytes, bytes]] with lowercased names.
        as_dict = {k.decode("latin-1"): v.decode("latin-1") for k, v in raw_headers}

        fields = fields_from_headers(as_dict)
        tokens = bind_fields(fields)
        try:
            log_id = ensure_log_id()

            async def send_wrapper(message: Message) -> None:
                if message["type"] == "http.response.start":
                    headers = list(message.get("headers") or [])
                    headers.append((HEADER_LOG_ID.lower().encode("latin-1"), log_id.encode("latin-1")))
                    message = {**message, "headers": headers}
                await send(message)

            await self.app(scope, receive, send_wrapper)
        finally:
            for tok in reversed(tokens):
                tok.var.reset(tok)
