"""Read-only ERPNext client interface used by facts bootstrap and the summarizer.

The summarizer is forbidden from mutating ERPNext (decision 2). Facts bootstrap
does no mutation either. Both layers go through this Protocol so production
can plug in a real HTTP client (or wrap the ``cli-anything-erpnext`` skill
subprocess) and tests can pass a deterministic in-memory fake.

M3 ships only the Protocol + a stub that raises ``NotConfiguredError``; real
plumbing arrives once the FrappeClient observer hook is wired in M4 / on
deploy. Until then, facts bootstrap returns an empty bundle and the
summarizer runs without tool access.
"""

from __future__ import annotations

from typing import Any, Protocol


class NotConfiguredError(RuntimeError):
    """Raised by the stub client to signal the production wiring isn't in place yet."""


class ErpnextReadOnlyClient(Protocol):
    """The minimal surface ``facts.py`` and the summarizer can rely on."""

    def get_doc(self, doctype: str, name: str) -> dict[str, Any] | None: ...

    def get_list(
        self,
        doctype: str,
        *,
        filters: dict[str, Any] | None = None,
        fields: list[str] | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]: ...


class StubErpnextClient:
    """No-op client for environments where ERPNext access isn't wired yet.

    Every call raises ``NotConfiguredError``. Callers must catch this and
    fall back gracefully (facts → empty bundle; summarizer → no tool calls).
    """

    def get_doc(self, doctype: str, name: str) -> dict[str, Any] | None:
        raise NotConfiguredError(f"ERPNext read-only client not configured (get_doc {doctype} {name!r})")

    def get_list(
        self,
        doctype: str,
        *,
        filters: dict[str, Any] | None = None,
        fields: list[str] | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        raise NotConfiguredError(f"ERPNext read-only client not configured (get_list {doctype})")


_default: ErpnextReadOnlyClient | None = None


def get_default_erpnext_client() -> ErpnextReadOnlyClient:
    """Return the installed default. Falls back to the stub when nothing is set."""
    return _default if _default is not None else StubErpnextClient()


def set_default_erpnext_client(client: ErpnextReadOnlyClient | None) -> None:
    """Install a process-wide default. Pass ``None`` to revert to the stub."""
    global _default
    _default = client
