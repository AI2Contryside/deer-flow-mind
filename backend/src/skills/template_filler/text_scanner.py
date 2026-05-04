"""Pull visible text out of a docx / xlsx template, with position hints.

This is the deterministic half of field extraction — no LLM, no heuristics
about *which* text spans are placeholders. We just emit "here is the text,
here is where it lives" so the LLM gets a structured view it can reason
about with location hints intact.
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from dataclasses import dataclass
from typing import IO

import openpyxl
from docx import Document as DocxDocument


@dataclass(frozen=True)
class TextFragment:
    """One chunk of visible text plus where it came from.

    `location_hint` is human-readable — used by the LLM prompt and surfaced
    on the review UI, never as a programmatic addressing scheme. The actual
    rewrite path uses `text` itself (matched by the Go jinja_renderer).
    """

    text: str
    location_hint: str


def scan_docx(file: bytes | IO[bytes]) -> list[TextFragment]:
    """Extract paragraph and table-cell text from a .docx file.

    `python-docx` already concatenates run text per paragraph, which is
    exactly what we want — placeholders split across runs come back fused.
    Empty paragraphs are dropped to keep the LLM context lean.
    """
    if isinstance(file, bytes):
        file = io.BytesIO(file)
    doc = DocxDocument(file)
    out: list[TextFragment] = []

    for idx, para in enumerate(doc.paragraphs, start=1):
        text = para.text.strip()
        if not text:
            continue
        out.append(TextFragment(text=text, location_hint=f"段落 {idx}"))

    for table_idx, table in enumerate(doc.tables, start=1):
        for row_idx, row in enumerate(table.rows, start=1):
            for col_idx, cell in enumerate(row.cells, start=1):
                cell_text = cell.text.strip()
                if not cell_text:
                    continue
                out.append(
                    TextFragment(
                        text=cell_text,
                        location_hint=f"表 {table_idx} · 行{row_idx}列{col_idx}",
                    )
                )
    return out


def scan_xlsx(file: bytes | IO[bytes]) -> list[TextFragment]:
    """Extract every non-empty cell's display value from a .xlsx file.

    `data_only=False` is critical — we want the *formula or the literal*
    cell value, not a cached evaluated number. Sheets are scanned in
    workbook order; cells in row-major order within each sheet.
    """
    if isinstance(file, bytes):
        file = io.BytesIO(file)
    wb = openpyxl.load_workbook(file, data_only=False, read_only=True)
    out: list[TextFragment] = []

    try:
        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            for row in ws.iter_rows():
                for cell in row:
                    if cell.value is None:
                        continue
                    raw = str(cell.value).strip()
                    if not raw:
                        continue
                    out.append(
                        TextFragment(
                            text=raw,
                            location_hint=f"{sheet_name}!{cell.coordinate}",
                        )
                    )
    finally:
        wb.close()
    return out


def chunk_for_prompt(fragments: list[TextFragment], max_chars: int = 8000) -> Iterator[list[TextFragment]]:
    """Split fragments into batches the LLM can comfortably ingest.

    `max_chars` is a soft budget on the joined "text + hint" payload per
    batch. Big templates (1000+ rows) would otherwise blow the context
    window — chunking keeps each LLM call bounded and lets the caller
    merge the field sets with simple deduplication on `original_text`.
    """
    batch: list[TextFragment] = []
    used = 0
    for frag in fragments:
        cost = len(frag.text) + len(frag.location_hint) + 4  # tab/newline overhead
        if batch and used + cost > max_chars:
            yield batch
            batch, used = [], 0
        batch.append(frag)
        used += cost
    if batch:
        yield batch
