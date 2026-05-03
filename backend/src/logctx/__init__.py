"""Per-request identity propagation: tenant_id / user_id / log_id /
session_id (and any future field) flow through context vars, FastAPI
middleware, logging filters, and httpx clients via this single package.

Adding a new field is a four-line change: extend Fields, add the matching
HEADER_* constant, register a ContextVar in context.py, and update
LogContextFilter — every call site that already imports from logctx picks
the new field up automatically.
"""

from src.logctx.config import install_log_filter, log_format_with_context
from src.logctx.context import (
    HEADER_LOG_ID,
    HEADER_SESSION_ID,
    HEADER_TENANT_ID,
    HEADER_USER_ID,
    Fields,
    bind,
    bind_fields,
    current_fields,
    ensure_log_id,
    headers_from_context,
    new_log_id,
)
from src.logctx.filter import LogContextFilter
from src.logctx.http import build_outbound_headers
from src.logctx.middleware import IdentityMiddleware

__all__ = [
    "Fields",
    "HEADER_LOG_ID",
    "HEADER_SESSION_ID",
    "HEADER_TENANT_ID",
    "HEADER_USER_ID",
    "IdentityMiddleware",
    "LogContextFilter",
    "bind",
    "bind_fields",
    "build_outbound_headers",
    "current_fields",
    "ensure_log_id",
    "headers_from_context",
    "install_log_filter",
    "log_format_with_context",
    "new_log_id",
]
