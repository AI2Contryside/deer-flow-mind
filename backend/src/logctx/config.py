"""One-shot helpers for wiring LogContextFilter into stdlib logging.

The gateway entry point (``src/gateway/app.py``) and the LangGraph
entry point (``src/agents/lead_agent/agent.py``) both call
``install_log_filter()`` once during process startup; everything else
just imports ``logging.getLogger(__name__)`` as usual and gets the
identity fields for free.
"""

from __future__ import annotations

import logging

from src.logctx.filter import LogContextFilter

# Default log line. ``%(log_id)s`` etc. resolve via LogContextFilter.
DEFAULT_FORMAT = (
    "%(asctime)s %(levelname)s "
    "tenant_id=%(tenant_id)s user_id=%(user_id)s "
    "log_id=%(log_id)s session_id=%(session_id)s "
    "%(name)s - %(message)s"
)

DEFAULT_DATEFMT = "%Y-%m-%d %H:%M:%S"


def log_format_with_context() -> str:
    """Return the canonical log format string. Exposed for tests / docs."""
    return DEFAULT_FORMAT


def install_log_filter(
    *,
    level: int | str | None = None,
    fmt: str | None = None,
    datefmt: str | None = None,
) -> None:
    """Attach LogContextFilter to every handler on the root logger.

    Idempotent: a handler already carrying a LogContextFilter is left
    alone. When the root logger has no handlers (typical in tests), one
    StreamHandler is installed so log lines remain visible.
    """
    root = logging.getLogger()
    if level is not None:
        root.setLevel(level)

    formatter = logging.Formatter(fmt or DEFAULT_FORMAT, datefmt or DEFAULT_DATEFMT)

    if not root.handlers:
        handler = logging.StreamHandler()
        root.addHandler(handler)

    for handler in root.handlers:
        if not any(isinstance(f, LogContextFilter) for f in handler.filters):
            handler.addFilter(LogContextFilter())
        handler.setFormatter(formatter)
