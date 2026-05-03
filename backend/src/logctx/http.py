"""Helpers for outbound HTTP — adds the four identity headers to every
request derived from the current logctx state.

Use ``build_outbound_headers()`` when constructing httpx / requests
calls. New identity fields registered in logctx.context propagate here
automatically, so skill / sandbox HTTP wrappers don't need updating.
"""

from __future__ import annotations

from collections.abc import Mapping

from src.logctx.context import headers_from_context


def build_outbound_headers(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """Merge logctx-derived headers with caller-provided extras.

    Extras win on conflict so a caller can override (e.g. force a
    specific tenant for an admin sweep) without us silently clobbering.
    """
    headers = headers_from_context()
    if extra:
        headers.update(extra)
    return headers
