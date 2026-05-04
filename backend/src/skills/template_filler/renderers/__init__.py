"""Renderers turn jinja-tagged templates + a data dict into output bytes.

The package is import-safe even if docxtpl / xltpl are not installed —
each renderer imports its heavy dependency lazily inside `render(...)`.
This keeps the upload-time path (extractor) decoupled from the chat-time
path (filler), and lets unit tests skip cleanly when the optional
dependency is absent.
"""

from src.skills.template_filler.renderers.base import (
    RendererError,
    RenderResult,
    render_for_extension,
)

__all__ = ["RendererError", "RenderResult", "render_for_extension"]
