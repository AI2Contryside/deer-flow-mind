"""Renderer dispatch by file extension.

Both renderer modules expose a single public function `render(jinja_bytes,
data) -> bytes`. This module only adds the dispatch layer + result type.

Why dispatch lives here (not in the gateway router): keeps the renderer
selection deterministic and unit-testable. The gateway just hands off
file_name + bytes + data; testing different formats stays a parameter
sweep, not a routing exercise.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


class RendererError(RuntimeError):
    """Raised when rendering fails after the optional deps loaded successfully.

    Distinct from ImportError (raised by the renderer modules when their
    optional deps are missing) so callers can tell "library not installed"
    apart from "library installed but template was malformed / data
    dict didn't satisfy the template's required vars".
    """


@dataclass(frozen=True)
class RenderResult:
    """Bytes produced by a renderer plus best-effort content metadata.

    `content_type` is the OOXML mime type the gateway should advertise on
    the artifact upload — keeps the renderer the single source of truth
    even when callers don't introspect file_name themselves.
    """

    content: bytes
    content_type: str


def render_for_extension(file_name: str, jinja_bytes: bytes, data: dict) -> RenderResult:
    """Pick the right renderer by extension and render.

    Raises:
        ValueError: extension is neither .docx nor .xlsx.
        ImportError: the matching renderer's optional dependency
            (docxtpl / xltpl) is not installed.
        RendererError: renderer ran but raised on the template / data.
    """
    ext = os.path.splitext(file_name)[1].lower()
    if ext == ".docx":
        from src.skills.template_filler.renderers import docx as docx_renderer

        out = docx_renderer.render(jinja_bytes, data)
        return RenderResult(
            content=out,
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
    if ext == ".xlsx":
        from src.skills.template_filler.renderers import xlsx as xlsx_renderer

        out = xlsx_renderer.render(jinja_bytes, data)
        return RenderResult(
            content=out,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    raise ValueError(f"unsupported extension: {ext!r}")
