"""XLSX rendering via openpyxl.

Two input shapes are supported on the same ``render(...)`` call:

  • ``data: dict``  — single-record substitution. Walks every cell,
    replaces ``{{ name }}`` with the matching value from ``data``.
    Used when the template has just one form-style row to fill (e.g.
    a one-off invoice header).

  • ``data: list[dict]`` — multi-row expansion. The xlsx form templates
    we generate from "header + blank rows" carry exactly one *seed
    row* of ``{{ name }}`` tags directly under the header. With a list
    input we replicate that seed row once per dict and substitute
    per-row, producing a single workbook with N data rows. This is the
    shape the LLM should hit when the user pastes a batch (e.g.
    multiple employees / multiple line items).

No xlrd, no temp files, no subprocess. We previously routed through
xltpl, but its ``BookWriter`` calls ``xlrd.open_workbook`` internally
and xlrd 2.0+ rejects xlsx with ``XLRDError("Excel xlsx file; not
supported")``. The openpyxl path skips that whole dependency chain.
"""

from __future__ import annotations

import io
import re
from typing import Any

import openpyxl


def render(jinja_bytes: bytes, data: dict[str, Any] | list[dict[str, Any]]) -> bytes:
    """Render a jinja-tagged xlsx with either a single dict or a list of dicts.

    Args:
        jinja_bytes: bytes of the jinja-tagged template (i.e. the
            ``.jinja.xlsx`` produced by the upload pipeline).
        data: scalar substitution dict, OR a list of such dicts to expand
            seed rows into a multi-row table. An empty list produces an
            xlsx with the seed row stripped (header preserved).

    Raises:
        RendererError: openpyxl raised while opening / saving the workbook.
    """
    from src.skills.template_filler.renderers.base import RendererError

    is_list = isinstance(data, list)

    try:
        wb = openpyxl.load_workbook(io.BytesIO(jinja_bytes))
        try:
            for ws in wb.worksheets:
                if is_list:
                    _expand_seed_rows(ws, data)  # type: ignore[arg-type]
                else:
                    _scalar_substitute(ws, data)  # type: ignore[arg-type]
            out = io.BytesIO()
            wb.save(out)
            return out.getvalue()
        finally:
            wb.close()
    except Exception as exc:  # noqa: BLE001
        raise RendererError(f"xlsx render failed: {exc}") from exc


_TAG_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


def _scalar_substitute(ws: Any, data: dict[str, Any]) -> None:
    """Walk the sheet and substitute ``{{ name }}`` tags in place."""
    for row in ws.iter_rows():
        for cell in row:
            if isinstance(cell.value, str) and "{{" in cell.value:
                cell.value = _substitute(cell.value, data)


def _expand_seed_rows(ws: Any, data_list: list[dict[str, Any]]) -> None:
    """Expand each row containing jinja tags into ``len(data_list)`` rows.

    Algorithm:
      1. Snapshot the rows that carry at least one ``{{ ... }}`` tag.
         Capture the *full* row (including non-jinja constants like
         column-header labels in nested headers) so we can re-emit it
         verbatim per data dict.
      2. For each seed row, write rows ``[seed_idx ..  seed_idx + N - 1]``
         using the captured template, substituting per-row data.

    Notes:
      - Non-jinja cells in the seed row are copied as-is. If a constant
        cell sits in the same row as a tag (rare in real form templates,
        but possible), it gets duplicated across all output rows.
      - We assume the rows below the seed are blank (the upload pipeline
        produces "header + single seed row + blank" templates). If a
        user has data already past row N+1, this will overwrite it —
        the alternative (insert_rows) silently shifts subsequent
        formulas / styles which is worse for the more common case.
      - Multiple seed rows aren't really expected (one tag-bearing
        row per sheet is the upload pipeline's convention), but the
        loop tolerates them.
    """
    if not data_list:
        return

    seed_rows: list[tuple[int, list[tuple[str, Any]]]] = []
    for row in ws.iter_rows():
        if not row:
            continue
        if any(isinstance(c.value, str) and "{{" in c.value for c in row):
            row_idx = row[0].row
            template_cells = [(c.column_letter, c.value) for c in row]
            seed_rows.append((row_idx, template_cells))

    for seed_idx, template_cells in seed_rows:
        for offset, item in enumerate(data_list):
            target_row = seed_idx + offset
            for col_letter, tpl_value in template_cells:
                if isinstance(tpl_value, str) and "{{" in tpl_value:
                    new_value = _substitute(tpl_value, item)
                else:
                    new_value = tpl_value
                ws[f"{col_letter}{target_row}"] = new_value


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
