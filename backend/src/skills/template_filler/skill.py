"""Pure-function entry point for template filling.

This is the byte-in / byte-out core. The LangGraph tool wrapper that:
  - reads `selected_template` out of ThreadState.context,
  - downloads the jinja-tagged template from OSS via signed URL,
  - calls `fill_template_bytes(...)`,
  - uploads the result back to OSS,
  - emits an artifact via `present_file_tool`-style metadata,
... lives in `src/tools/builtins/fill_template_tool.py` (Phase 2). Keeping
the pure function here lets the LangGraph wrapper stay thin and lets
unit tests verify the core without the tool runtime.
"""

from __future__ import annotations

import logging
from typing import Any

from src.skills.template_filler.renderers import (
    RendererError,
    RenderResult,
    render_for_extension,
)

logger = logging.getLogger(__name__)


class TemplateFillError(RuntimeError):
    """Top-level error raised by `fill_template_bytes`.

    Wraps lower-level `RendererError` / `ImportError` so callers only
    need one except clause.
    """


def fill_template_bytes(
    jinja_bytes: bytes,
    output_name: str,
    data: dict[str, Any] | list[dict[str, Any]],
) -> RenderResult:
    """Render a jinja-tagged template into the final document bytes.

    Args:
        jinja_bytes: bytes of the *jinja-tagged* template (the .jinja.docx /
            .jinja.xlsx variant produced by the upload pipeline). Plain
            unprocessed templates won't have substitution markers and
            will pass through unchanged.
        output_name: filename the renderer uses for extension-based
            dispatch. The bytes don't change — this is metadata for
            picking docx vs xlsx and for the eventual OSS object key.
        data: jinja context. Either a single dict (one record substituted
            into the seed row) or a list of dicts (xlsx only — replicate
            the seed row once per item to produce a multi-row table). For
            docx, list inputs collapse to the first dict with a warning,
            since docx has no "row-loop" concept.

    Returns:
        A RenderResult with rendered bytes and the OOXML mime type.

    Raises:
        TemplateFillError: any failure during render. Wraps the
            underlying ImportError / RendererError / ValueError.
    """
    import os

    ext = os.path.splitext(output_name)[1].lower()
    effective_data: dict[str, Any] | list[dict[str, Any]] = data

    if isinstance(data, list) and ext == ".docx":
        if not data:
            effective_data = {}
        else:
            logger.warning(
                "fill_template_bytes: docx received list[%d]; using first item only",
                len(data),
            )
            effective_data = data[0]

    try:
        return render_for_extension(output_name, jinja_bytes, effective_data)
    except ImportError as exc:
        raise TemplateFillError(f"renderer dependency missing for {output_name}: {exc}") from exc
    except RendererError as exc:
        raise TemplateFillError(str(exc)) from exc
    except ValueError as exc:
        raise TemplateFillError(str(exc)) from exc
