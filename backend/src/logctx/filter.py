"""logging.Filter that mirrors logctx ContextVars onto every LogRecord.

Install via ``install_log_filter()`` from logctx.config — the filter is
attached to every handler on the root logger so format strings can read
``%(log_id)s`` / ``%(tenant_id)s`` / ... unconditionally.

LogRecord attributes are populated only when missing so that explicit
``logger.info("...", extra={"log_id": "override"})`` always wins.
"""

from __future__ import annotations

import logging

from src.logctx.context import KNOWN_FIELDS, current_fields


class LogContextFilter(logging.Filter):
    """Inject logctx fields into the LogRecord before emission."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: D401 — logging API
        f = current_fields()
        snapshot = f.as_dict()
        for name in KNOWN_FIELDS:
            if not hasattr(record, name) or not getattr(record, name, None):
                # default to "-" so format strings stay aligned even when a
                # field is absent (most common for unauthenticated logs).
                setattr(record, name, snapshot.get(name) or "-")
        return True
