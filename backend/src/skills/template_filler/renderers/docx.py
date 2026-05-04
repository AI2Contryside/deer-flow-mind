"""DOCX rendering via docxtpl.

`docxtpl` (python-docx-template) handles jinja templates inside .docx
including tables, lists, and conditional sections. We expose a thin
function that takes jinja-bytes + a data dict and returns rendered
bytes — the OSS upload + LangGraph plumbing lives elsewhere.

Stringification rule: every value passed to docxtpl is coerced to its
string form before render. Keeps v1 simple — number/date typing happens
at the human-review UI, the renderer just substitutes.
"""

from __future__ import annotations

import io
from typing import Any


def render(jinja_bytes: bytes, data: dict[str, Any]) -> bytes:
    """Render a jinja-tagged .docx with the given data dict.

    The template is opened from bytes (not a file path) so the upload
    pipeline doesn't have to materialise it on disk. Returned bytes are
    a fresh .docx ready to upload to OSS.

    Raises:
        ImportError: docxtpl is not installed.
        RendererError: docxtpl loaded but raised on render (invalid jinja
            syntax, undefined variable in strict mode, etc.).
    """
    try:
        from docxtpl import DocxTemplate
    except ImportError as exc:  # noqa: BLE001
        raise ImportError("docxtpl is required to render .docx templates. Install with: uv add docxtpl") from exc

    from src.skills.template_filler.renderers.base import RendererError

    stringified = _stringify_values(data)
    try:
        tpl = DocxTemplate(io.BytesIO(jinja_bytes))
        tpl.render(stringified)
        out = io.BytesIO()
        tpl.save(out)
        return out.getvalue()
    except Exception as exc:  # noqa: BLE001
        raise RendererError(f"docx render failed: {exc}") from exc


def _stringify_values(data: dict[str, Any]) -> dict[str, Any]:
    """Coerce every leaf to str so templates don't crash on typed inputs.

    Lists / dicts pass through so docxtpl's loop / nested-attr syntax
    keeps working, but their leaves are stringified recursively.
    """
    return {k: _stringify(v) for k, v in data.items()}


def _stringify(v: Any) -> Any:
    if v is None:
        return ""
    if isinstance(v, (str, bool)):
        # bool first (it's a subclass of int) so True/False round-trip
        # as strings rather than 1/0.
        return v if isinstance(v, str) else str(v)
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, list):
        return [_stringify(x) for x in v]
    if isinstance(v, dict):
        return {k: _stringify(x) for k, x in v.items()}
    return str(v)
