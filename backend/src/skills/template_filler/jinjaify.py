"""Rewrite an uploaded template into a jinja-tagged variant.

Three field shapes, see types.ExtractedField:

  • docx label-anchor "append" mode (default): keeps the anchor in
    place and adds `{{ name }}` after it. Used for "客户名称:" → renders
    to "客户名称:{{ customer_name }}". The label survives, so the user's
    formatted document still reads naturally.
  • docx placeholder "replace" mode: eats the anchor entirely and writes
    `{{ name }}` in its place. Used for "________" or "在此填入" →
    renders to "{{ field }}". The original placeholder string disappears.
  • Header-cell (`cell_anchor`): xlsx-only. Write `{{ name }}` directly
    into the data-row cell so column headers stay intact and the value
    lands one row down at render time.

Returns the rewritten file bytes plus a per-field outcome report so the
review UI can flag fields that didn't apply.

Why duplicate the Go work in Python: the Go renderer is OOXML-text-node
based and can't easily express "write into Sheet1!B2 even when the cell
is currently blank". openpyxl + python-docx do that natively, and
keeping both halves of the field-extraction pipeline (LLM analysis +
deterministic rewrite) in deer-flow-mind avoids a third RPC hop on
every template review.
"""

from __future__ import annotations

import io
import logging
import os
import re
from dataclasses import dataclass
from typing import IO

import openpyxl
from docx import Document as DocxDocument

from src.skills.template_filler.types import ExtractedField

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class JinjaifyOutcome:
    """Per-field result of a jinjaify call."""

    name: str
    applied: bool
    reason: str | None = None


@dataclass(frozen=True)
class JinjaifyResult:
    """Full jinjaify output: rewritten bytes + per-field report."""

    content: bytes
    content_type: str
    outcomes: list[JinjaifyOutcome]

    @property
    def applied(self) -> list[str]:
        return [o.name for o in self.outcomes if o.applied]

    @property
    def skipped(self) -> list[JinjaifyOutcome]:
        return [o for o in self.outcomes if not o.applied]


_JINJA_VAR_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_JINJA_KEYWORDS = frozenset({"for", "endfor", "if", "endif", "else", "elif", "in", "and", "or", "not", "true", "false", "none", "True", "False", "None"})


def _valid_jinja_identifier(name: str) -> bool:
    """Mirror the Go-side jinja_renderer.isValidJinjaIdentifier rules."""
    return bool(name) and bool(_JINJA_VAR_RE.match(name)) and name not in _JINJA_KEYWORDS


def _tag(name: str) -> str:
    return "{{ " + name + " }}"


def jinjaify(file: bytes | IO[bytes], file_name: str, fields: list[ExtractedField]) -> JinjaifyResult:
    """Dispatch by extension. Raises ValueError on unsupported extensions."""
    if isinstance(file, (bytes, bytearray)):
        file = io.BytesIO(bytes(file))
    ext = os.path.splitext(file_name)[1].lower()
    if ext == ".xlsx":
        return _jinjaify_xlsx(file, fields)
    if ext == ".docx":
        return _jinjaify_docx(file, fields)
    raise ValueError(f"unsupported template extension: {ext!r}")


# ---------------------------------------------------------------------------
# xlsx
# ---------------------------------------------------------------------------


def _parse_cell_anchor(anchor: str) -> tuple[str | None, str] | None:
    """Split "Sheet1!B2" into (sheet, cell). Returns None on malformed input.

    Sheet name is optional ("B2" → (None, "B2"), use first sheet). Cell
    must be a coordinate (letters + digits); we don't try to parse range
    syntax (`B2:B5`) — header-cell mode targets a single seed cell.
    """
    if not anchor:
        return None
    parts = anchor.split("!", 1)
    if len(parts) == 2:
        sheet, cell = parts[0].strip(), parts[1].strip()
    else:
        sheet, cell = None, parts[0].strip()
    if not cell or not re.fullmatch(r"[A-Za-z]+[0-9]+", cell):
        return None
    return sheet, cell.upper()


def _jinjaify_xlsx(buf: IO[bytes], fields: list[ExtractedField]) -> JinjaifyResult:
    wb = openpyxl.load_workbook(buf)
    outcomes: list[JinjaifyOutcome] = []

    # Index fields by mode so we can apply text-mode after cell-mode (the
    # cell write would otherwise also be a text-replace target).
    cell_fields: list[ExtractedField] = []
    text_fields: list[ExtractedField] = []
    for f in fields:
        if not _valid_jinja_identifier(f.name):
            outcomes.append(JinjaifyOutcome(f.name, False, "invalid_variable_name"))
            continue
        if f.cell_anchor:
            cell_fields.append(f)
        elif f.original_text:
            text_fields.append(f)
        else:
            outcomes.append(JinjaifyOutcome(f.name, False, "no_target"))

    # Pass 1: cell-anchor writes.
    for f in cell_fields:
        parsed = _parse_cell_anchor(f.cell_anchor or "")
        if parsed is None:
            outcomes.append(JinjaifyOutcome(f.name, False, "malformed_cell_anchor"))
            continue
        sheet_name, cell = parsed
        try:
            ws = wb[sheet_name] if sheet_name else wb.active
        except KeyError:
            outcomes.append(JinjaifyOutcome(f.name, False, "sheet_not_found"))
            continue
        try:
            ws[cell] = _tag(f.name)
        except Exception as exc:  # noqa: BLE001 — surface any openpyxl quirk
            logger.warning("xlsx cell write failed for %s!%s: %s", sheet_name, cell, exc)
            outcomes.append(JinjaifyOutcome(f.name, False, f"write_failed:{exc}"))
            continue
        outcomes.append(JinjaifyOutcome(f.name, True))

    # Pass 2: inline-text replaces. Walk every cell once; per-cell, scan
    # the remaining text fields and substitute on first non-empty match.
    # Cells already holding `{{ name }}` from pass 1 are skipped naturally
    # because original_text won't appear in them.
    if text_fields:
        text_outcomes = _xlsx_text_replace(wb, text_fields)
        outcomes.extend(text_outcomes)

    out = io.BytesIO()
    wb.save(out)
    wb.close()
    return JinjaifyResult(
        content=out.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        outcomes=outcomes,
    )


def _xlsx_text_replace(wb: openpyxl.Workbook, fields: list[ExtractedField]) -> list[JinjaifyOutcome]:
    """Per-field count-then-replace with ambiguity detection.

    A field's original_text is ambiguous if it appears in more than one
    cell across the workbook — that's the spreadsheet equivalent of
    "appears twice in one paragraph" on the docx side. We mirror the Go
    renderer's behaviour: skip ambiguous, record the reason.
    """
    outcomes: list[JinjaifyOutcome] = []
    # Snapshot cell strings up front so a replace doesn't perturb later
    # ambiguity counts. Map field index → list of (sheet,cell) hits.
    hits: list[list[tuple[str, str, str]]] = [[] for _ in fields]
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                v = cell.value
                if not isinstance(v, str):
                    continue
                for i, f in enumerate(fields):
                    if f.original_text and f.original_text in v:
                        hits[i].append((ws.title, cell.coordinate, v))

    for i, f in enumerate(fields):
        cell_hits = hits[i]
        if not cell_hits:
            outcomes.append(JinjaifyOutcome(f.name, False, "not_found"))
            continue
        if len(cell_hits) > 1:
            outcomes.append(JinjaifyOutcome(f.name, False, "ambiguous"))
            continue
        sheet, coord, original = cell_hits[0]
        ws = wb[sheet]
        # Replace only the first occurrence within the cell value, mirroring
        # the Go renderer's "single match per paragraph" semantics.
        if f.anchor_mode == "replace":
            replacement = _tag(f.name)
        else:
            replacement = f.original_text + _tag(f.name)
        new_value = original.replace(f.original_text, replacement, 1)
        ws[coord] = new_value
        outcomes.append(JinjaifyOutcome(f.name, True))
    return outcomes


# ---------------------------------------------------------------------------
# docx
# ---------------------------------------------------------------------------


def _jinjaify_docx(buf: IO[bytes], fields: list[ExtractedField]) -> JinjaifyResult:
    """python-docx text replace at paragraph level.

    Iterates every paragraph (body + tables + headers + footers), checks
    each text field's original_text. Matches in cell mode are skipped
    with reason 'cell_anchor_unsupported_for_docx' since docx has no
    address space the LLM can target.
    """
    doc = DocxDocument(buf)
    outcomes: list[JinjaifyOutcome] = []

    text_fields: list[ExtractedField] = []
    for f in fields:
        if not _valid_jinja_identifier(f.name):
            outcomes.append(JinjaifyOutcome(f.name, False, "invalid_variable_name"))
            continue
        if f.cell_anchor and not f.original_text:
            outcomes.append(JinjaifyOutcome(f.name, False, "cell_anchor_unsupported_for_docx"))
            continue
        if not f.original_text:
            outcomes.append(JinjaifyOutcome(f.name, False, "no_target"))
            continue
        text_fields.append(f)

    # Per-field paragraph-level scan + ambiguity check, matching the Go
    # renderer's contract.
    paragraphs = list(_iter_all_paragraphs(doc))
    hit_counts: list[int] = [0] * len(text_fields)
    for p_text in paragraphs:
        for i, f in enumerate(text_fields):
            if f.original_text in p_text:
                hit_counts[i] += p_text.count(f.original_text)

    # Replace in a single pass over paragraphs. Within a paragraph, fuse
    # all runs into the first <w:t> like the Go renderer — python-docx's
    # `paragraph.text = ...` setter does exactly that.
    field_status = ["pending"] * len(text_fields)
    for paragraph in _iter_all_paragraphs_obj(doc):
        if not paragraph.text:
            continue
        for i, f in enumerate(text_fields):
            if field_status[i] != "pending":
                continue
            if hit_counts[i] == 0:
                continue
            if hit_counts[i] > 1:
                continue  # marked below
            if f.original_text in paragraph.text:
                # anchor_mode = "append" — keep the anchor (which is the
                # human-readable label) and write the jinja tag right
                # after it. anchor_mode = "replace" — eat the anchor
                # entirely (it was a placeholder string with no semantic
                # value to preserve).
                if f.anchor_mode == "replace":
                    new_value = _tag(f.name)
                else:
                    new_value = f.original_text + _tag(f.name)
                paragraph.text = paragraph.text.replace(f.original_text, new_value, 1)
                field_status[i] = "applied"
                # Once applied, decrement total count so future paragraphs
                # holding the same string don't re-fire (shouldn't happen
                # given the unambiguity check, but be defensive).
                hit_counts[i] = 0
                break

    for i, f in enumerate(text_fields):
        if field_status[i] == "applied":
            outcomes.append(JinjaifyOutcome(f.name, True))
        elif hit_counts[i] > 1:
            outcomes.append(JinjaifyOutcome(f.name, False, "ambiguous"))
        else:
            outcomes.append(JinjaifyOutcome(f.name, False, "not_found"))

    out = io.BytesIO()
    doc.save(out)
    return JinjaifyResult(
        content=out.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        outcomes=outcomes,
    )


def _iter_all_paragraphs(doc):
    """Yield all paragraph text (body, tables, headers, footers)."""
    for p in _iter_all_paragraphs_obj(doc):
        yield p.text


def _iter_all_paragraphs_obj(doc):
    """Yield paragraph objects covering the whole doc surface area.

    Headers/footers/tables aren't reachable from `doc.paragraphs` alone;
    walk explicitly so AI-identified fields in any of them get rewritten.
    """
    for p in doc.paragraphs:
        yield p
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for p in cell.paragraphs:
                    yield p
    for section in doc.sections:
        for hdr_or_ftr in (section.header, section.footer, section.first_page_header, section.first_page_footer, section.even_page_header, section.even_page_footer):
            if hdr_or_ftr is None:
                continue
            for p in hdr_or_ftr.paragraphs:
                yield p
            for table in hdr_or_ftr.tables:
                for row in table.rows:
                    for cell in row.cells:
                        for p in cell.paragraphs:
                            yield p
