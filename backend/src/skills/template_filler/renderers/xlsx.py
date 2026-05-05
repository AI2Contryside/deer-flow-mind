"""XLSX rendering via openpyxl.

We render the simple ``{{ name }}`` form the extractor / jinjaify produce.
openpyxl walks every cell, regex-substitutes scalar tags, preserves
existing styles in place. No xlrd, no temp files, no subprocess.

Why not xltpl? xltpl's ``BookWriter`` is path-based and *internally*
calls ``xlrd.open_workbook(fname)`` — but xlrd 2.0+ dropped xlsx support
and raises ``XLRDError("Excel xlsx file; not supported")`` when it sees
one. xltpl ships an alternative ``writerx.BookWriter`` for xlsx, but
its row-loop / iter-tag features aren't required by v1, and the
openpyxl path stays simpler and avoids the xlrd dependency entirely.

Future hook: when v2 adds row-loop fields (``{%tr for ...%}``), promote
``xltpl.writerx.BookWriter`` here behind a feature flag — the public
``render(...)`` signature stays the same.
"""

from __future__ import annotations

import io
import re
from typing import Any

import openpyxl


def render(jinja_bytes: bytes, data: dict[str, Any]) -> bytes:
    """Render a jinja-tagged .xlsx with the given data dict.

    Walks every cell, replaces ``{{ name }}`` substrings with the matching
    value from ``data`` (or the literal tag if the key isn't present, so
    a missed field stays visible rather than silently emptying the cell).

    Raises:
        RendererError: openpyxl raised while opening / saving the workbook.
    """
    from src.skills.template_filler.renderers.base import RendererError

    try:
        wb = openpyxl.load_workbook(io.BytesIO(jinja_bytes))
        try:
            for ws in wb.worksheets:
                for row in ws.iter_rows():
                    for cell in row:
                        if isinstance(cell.value, str) and "{{" in cell.value:
                            cell.value = _substitute(cell.value, data)
            out = io.BytesIO()
            wb.save(out)
            return out.getvalue()
        finally:
            wb.close()
    except Exception as exc:  # noqa: BLE001
        raise RendererError(f"xlsx render failed: {exc}") from exc


_TAG_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


def _substitute(text: str, data: dict[str, Any]) -> str:
    """Replace every ``{{ name }}`` tag with the matching value from ``data``.

    Missing keys leave the literal tag in place (``{{ unknown }}``) so a
    review pass can spot the gap; assigning ``""`` would silently swallow
    the field.
    """

    def _repl(m: re.Match[str]) -> str:
        key = m.group(1)
        if key not in data:
            return m.group(0)
        v = data[key]
        return "" if v is None else str(v)

    return _TAG_RE.sub(_repl, text)
