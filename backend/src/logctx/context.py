"""ContextVar-backed identity bundle.

Each known field has its own ContextVar so that ``bind(field=value)``
on one task doesn't bleed into a sibling task — ``contextvars`` already
gives us per-task isolation under asyncio. Empty / None values are
stored as the empty string so logging format strings can read the var
unconditionally without raising LookupError.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass

# Header names exchanged with trademind-backend / desktop FE / sibling
# services. Keep in sync with internal/ctxutil/ctxutil.go on the Go side.
HEADER_TENANT_ID = "X-Tenant-Id"
HEADER_USER_ID = "X-User-Id"
HEADER_LOG_ID = "X-Log-Id"
HEADER_SESSION_ID = "X-Session-Id"

# All known field names — extending the four below is the single point
# of change for adding a new identity dimension. Update this tuple, the
# Fields dataclass, the ContextVar table, and LogContextFilter; every
# other file in this package picks the new field up automatically.
KNOWN_FIELDS: tuple[str, ...] = ("tenant_id", "user_id", "log_id", "session_id")

_CTX_VARS: dict[str, ContextVar[str]] = {name: ContextVar(f"logctx_{name}", default="") for name in KNOWN_FIELDS}

_HEADER_BY_FIELD: Mapping[str, str] = {
    "tenant_id": HEADER_TENANT_ID,
    "user_id": HEADER_USER_ID,
    "log_id": HEADER_LOG_ID,
    "session_id": HEADER_SESSION_ID,
}


@dataclass(frozen=True)
class Fields:
    """Immutable snapshot of the identity bundle attached to the current task."""

    tenant_id: str = ""
    user_id: str = ""
    log_id: str = ""
    session_id: str = ""

    def as_dict(self) -> dict[str, str]:
        return {name: getattr(self, name) for name in KNOWN_FIELDS}

    def merged_with(self, other: Fields) -> Fields:
        """Return a new Fields where non-empty values in ``other`` win."""
        kwargs = self.as_dict()
        for name in KNOWN_FIELDS:
            if v := getattr(other, name):
                kwargs[name] = v
        return Fields(**kwargs)


def current_fields() -> Fields:
    """Snapshot the current task's identity fields."""
    return Fields(**{name: var.get() for name, var in _CTX_VARS.items()})


def bind_fields(fields: Fields) -> list[Token]:
    """Set every non-empty attribute of *fields* on the matching ContextVar.

    Returns the tokens needed to roll the values back. Use ``bind()`` for
    the common ``with`` flow.
    """
    tokens: list[Token] = []
    for name in KNOWN_FIELDS:
        v = getattr(fields, name)
        if v:
            tokens.append(_CTX_VARS[name].set(str(v)))
    return tokens


@contextmanager
def bind(**values: str | int | None) -> Iterator[Fields]:
    """Bind identity fields for the duration of the block.

    Unknown field names raise ``KeyError`` so typos don't silently no-op.
    Empty / None values are skipped (they don't clear an outer binding).
    """
    fields_kwargs: dict[str, str] = {}
    for k, v in values.items():
        if k not in _CTX_VARS:
            raise KeyError(f"unknown logctx field {k!r}; valid: {KNOWN_FIELDS}")
        if v in (None, ""):
            continue
        fields_kwargs[k] = str(v)

    tokens = bind_fields(Fields(**fields_kwargs))
    try:
        yield current_fields()
    finally:
        # Reset in reverse so nested binds unwind cleanly.
        for tok in reversed(tokens):
            tok.var.reset(tok)


def new_log_id() -> str:
    """Generate a fresh log id (UUID4 hex without dashes for compactness)."""
    return uuid.uuid4().hex


def ensure_log_id() -> str:
    """Return the current log_id, generating + binding one when empty.

    Useful at request entry points (FastAPI middleware, LangGraph entry)
    where we want to guarantee every log line carries a correlation id.
    """
    existing = _CTX_VARS["log_id"].get()
    if existing:
        return existing
    fresh = new_log_id()
    _CTX_VARS["log_id"].set(fresh)
    return fresh


def headers_from_context() -> dict[str, str]:
    """Render the current identity bundle as outbound HTTP headers.

    Empty fields are omitted so callers can layer additional headers on
    top without us overwriting a value we don't carry.
    """
    f = current_fields()
    out: dict[str, str] = {}
    for name in KNOWN_FIELDS:
        v = getattr(f, name)
        if v:
            out[_HEADER_BY_FIELD[name]] = v
    return out


def fields_from_headers(headers: Mapping[str, str]) -> Fields:
    """Pick the four identity headers out of a header mapping (case-insensitive)."""
    lowered = {k.lower(): v for k, v in headers.items()}
    return Fields(
        tenant_id=lowered.get(HEADER_TENANT_ID.lower(), "") or "",
        user_id=lowered.get(HEADER_USER_ID.lower(), "") or "",
        log_id=lowered.get(HEADER_LOG_ID.lower(), "") or "",
        session_id=lowered.get(HEADER_SESSION_ID.lower(), "") or "",
    )
