"""XLSX rendering via xltpl.

`xltpl` understands jinja-style tags inside cells and special row-level
tags (`{%tr for ...%}`) for repeating rows. v1 only supports scalar
substitution, so the row-loop syntax isn't exercised — but the same
library handles both, so when v2 adds table fields we don't swap libs.
"""

from __future__ import annotations

import io
import os
import tempfile
from typing import Any


def render(jinja_bytes: bytes, data: dict[str, Any]) -> bytes:
    """Render a jinja-tagged .xlsx with the given data dict.

    xltpl's `BookWriter` API is path-based — it doesn't accept a
    BytesIO. We materialise the input to a temp file under a context
    that cleans up on success or failure, then read the rendered output
    back into bytes.

    Raises:
        ImportError: xltpl is not installed.
        RendererError: render call raised.
    """
    try:
        from xltpl.writer import BookWriter
    except ImportError as exc:  # noqa: BLE001
        raise ImportError(
            "xltpl is required to render .xlsx templates. "
            "Install with: uv add xltpl"
        ) from exc

    from src.skills.template_filler.renderers.base import RendererError

    stringified = _stringify_values(data)
    try:
        with tempfile.TemporaryDirectory() as td:
            in_path = os.path.join(td, "in.xlsx")
            out_path = os.path.join(td, "out.xlsx")
            with open(in_path, "wb") as f:
                f.write(jinja_bytes)
            writer = BookWriter(in_path)
            # xltpl expects a list of "payloads" — one per generated sheet.
            # For the simple-substitution case, every sheet sees the same
            # payload (sheet_name=None means "default").
            writer.render_book(payloads=[{"sheet_name": None, "ctx": stringified}])
            writer.save(out_path)
            with open(out_path, "rb") as f:
                return f.read()
    except Exception as exc:  # noqa: BLE001
        raise RendererError(f"xlsx render failed: {exc}") from exc


def _stringify_values(data: dict[str, Any]) -> dict[str, Any]:
    """See `renderers.docx._stringify_values` — same rule, dup'd to avoid
    a cross-renderer import that would force xltpl to load when only
    docxtpl is available."""
    return {k: _stringify(v) for k, v in data.items()}


def _stringify(v: Any) -> Any:
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, list):
        return [_stringify(x) for x in v]
    if isinstance(v, dict):
        return {k: _stringify(x) for k, x in v.items()}
    return str(v)


def _alternate_render_with_openpyxl(jinja_bytes: bytes, data: dict[str, Any]) -> bytes:
    """Fallback path used when xltpl is unavailable.

    Walks every cell, replaces `{{ name }}` substrings via Python str.format
    with a custom prefix. NOT a full jinja implementation — only handles
    the simple `{{ name }}` form the extractor produces. Style is preserved
    because we mutate cell.value in place.

    Marked private because it's not the prescribed v1 path — it exists so
    Phase 1 has a way to smoke-test the chain end to end while the
    pyproject change for xltpl is queued.
    """
    import re

    import openpyxl

    pattern = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")

    def substitute(text: str) -> str:
        def _repl(m: "re.Match[str]") -> str:
            key = m.group(1)
            val = data.get(key, m.group(0))
            return str(val) if val is not None else ""

        return pattern.sub(_repl, text)

    wb = openpyxl.load_workbook(io.BytesIO(jinja_bytes))
    try:
        for ws in wb.worksheets:
            for row in ws.iter_rows():
                for cell in row:
                    if isinstance(cell.value, str) and "{{" in cell.value:
                        cell.value = substitute(cell.value)
        out = io.BytesIO()
        wb.save(out)
        return out.getvalue()
    finally:
        wb.close()
