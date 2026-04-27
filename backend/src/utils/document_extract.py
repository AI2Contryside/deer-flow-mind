"""Structure-preserving extraction of office/PDF files via docling.

Replaces the legacy markitdown → markdown pipeline. The agent now receives a
structured ``DoclingDocument`` JSON (preserving headings, tables with row/col
spans, formulas, embedded pictures, slide pages, provenance) plus a tiny
manifest summary so the lead agent can scan a file's structure before reading
it. For very large files the agent is expected to write Python (openpyxl
``read_only``, ``python-calamine``, DuckDB ``read_xlsx``, lxml ``iterparse``
on the OOXML zip) rather than reading the whole structured JSON into context.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Mirrors ``CONVERTIBLE_EXTENSIONS`` from the upload router. Centralised here
# so the embedded client and the router agree on what is "extractable".
EXTRACTABLE_EXTENSIONS: frozenset[str] = frozenset({".pdf", ".ppt", ".pptx", ".xls", ".xlsx", ".doc", ".docx"})

DOCLING_JSON_SUFFIX = ".docling.json"
DOCLING_SUMMARY_SUFFIX = ".docling.summary.json"

# Hard cap on how many section-header entries we surface in the prompt
# manifest. Long documents would otherwise blow the system prompt budget.
MAX_SECTIONS_IN_SUMMARY = 30


def is_extractable(file_path: Path) -> bool:
    """Return True when ``file_path`` is a supported office/PDF format."""
    return file_path.suffix.lower() in EXTRACTABLE_EXTENSIONS


def docling_json_path(file_path: Path) -> Path:
    """Return the ``.docling.json`` sibling path for ``file_path``.

    Uses a compound suffix (``<stem>.docling.json``) so the derived file is
    obviously not user-supplied and cannot collide with a user-uploaded
    plain ``.json``.
    """
    return file_path.with_name(file_path.stem + DOCLING_JSON_SUFFIX)


def docling_summary_path(file_path: Path) -> Path:
    """Return the ``.docling.summary.json`` sibling path for ``file_path``."""
    return file_path.with_name(file_path.stem + DOCLING_SUMMARY_SUFFIX)


def is_derived_artifact(file_path: Path) -> bool:
    """True when ``file_path`` is one of our generated docling sidecars.

    The middleware uses this to skip the sidecar files when listing
    historical uploads to the agent — the agent should see the *originals*,
    not our derived JSON, in the ``<uploaded_files>`` block.
    """
    name = file_path.name
    return name.endswith(DOCLING_JSON_SUFFIX) or name.endswith(DOCLING_SUMMARY_SUFFIX)


def _safe_caption(table: Any) -> str | None:
    """Best-effort caption text for a TableItem (sheet name on xlsx)."""
    captions = getattr(table, "captions", None) or []
    for cap in captions:
        text = getattr(cap, "text", None)
        if text is None and isinstance(cap, dict):
            text = cap.get("text")
        if text:
            return str(text)
    return None


def _page_size(page: Any) -> dict[str, float] | None:
    size = getattr(page, "size", None)
    if size is None:
        return None
    width = getattr(size, "width", None)
    height = getattr(size, "height", None)
    if width is None or height is None:
        return None
    return {"width": float(width), "height": float(height)}


def build_summary(doc: Any, file_path: Path) -> dict[str, Any]:
    """Build a tiny per-file structure summary for prompt injection.

    Aggregates **counts and top-level identifiers only** — never full
    content — so injecting the summary into the system prompt has bounded
    cost regardless of document size. The agent reads the full
    ``.docling.json`` (or writes Python against the original) when it
    needs the actual content.
    """
    pages = getattr(doc, "pages", {}) or {}
    page_count = len(pages)
    text_count = len(getattr(doc, "texts", []) or [])
    table_count = len(getattr(doc, "tables", []) or [])
    picture_count = len(getattr(doc, "pictures", []) or [])

    summary: dict[str, Any] = {
        "format": file_path.suffix.lstrip(".").lower(),
        "size_bytes": file_path.stat().st_size,
        "page_count": page_count,
        "text_count": text_count,
        "table_count": table_count,
        "picture_count": picture_count,
    }

    ext = file_path.suffix.lower()
    if ext in {".xlsx", ".xls"}:
        sheets: list[dict[str, Any]] = []
        for table in getattr(doc, "tables", []) or []:
            data = getattr(table, "data", None)
            sheets.append(
                {
                    "name": _safe_caption(table) or f"Sheet{len(sheets) + 1}",
                    "rows": getattr(data, "num_rows", None),
                    "cols": getattr(data, "num_cols", None),
                }
            )
        if sheets:
            summary["sheets"] = sheets
    elif ext in {".pptx", ".ppt"}:
        slides = []
        for page_no, page in pages.items():
            slides.append({"index": int(page_no), "size": _page_size(page)})
        if slides:
            summary["slide_count"] = len(slides)
    elif ext in {".docx", ".doc", ".pdf"}:
        sections: list[dict[str, Any]] = []
        for text in getattr(doc, "texts", []) or []:
            label = getattr(text, "label", None)
            if label in {"section_header", "title"}:
                section_text = (getattr(text, "text", "") or "")[:120]
                sections.append(
                    {
                        "level": getattr(text, "level", None),
                        "text": section_text,
                    }
                )
            if len(sections) >= MAX_SECTIONS_IN_SUMMARY:
                break
        if sections:
            summary["sections"] = sections

    return summary


def _convert_sync(file_path: Path) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Run docling synchronously inside a worker thread.

    Returns ``(docling_dict, summary_dict)`` on success, ``None`` on any
    failure (caller falls back to original-file-only behaviour). Heavy
    work (layout analysis, OCR) happens here, so callers should always
    ``asyncio.to_thread`` this.
    """
    try:
        from docling.document_converter import DocumentConverter
    except ImportError:
        logger.error("docling is not installed. Run `uv add docling` to enable structured Office extraction.")
        return None

    try:
        converter = DocumentConverter()
        result = converter.convert(str(file_path))
        doc = result.document
        return doc.export_to_dict(), build_summary(doc, file_path)
    except Exception:
        logger.warning("docling extraction failed for %s", file_path.name, exc_info=True)
        return None


async def extract_with_docling(file_path: Path) -> tuple[Path | None, Path | None, dict[str, Any] | None]:
    """Extract structured Docling JSON + summary for ``file_path``.

    Args:
        file_path: Path to the source office/PDF file.

    Returns:
        Tuple of ``(docling_json_path, summary_json_path, summary_dict)``.
        All three are ``None`` on failure — the caller proceeds with
        original-file-only behaviour.
    """
    converted = await asyncio.to_thread(_convert_sync, file_path)
    if converted is None:
        return None, None, None

    docling_dict, summary = converted

    json_path = docling_json_path(file_path)
    summary_path = docling_summary_path(file_path)
    json_path.write_text(
        json.dumps(docling_dict, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info(
        "Extracted %s → %s (%d tables, %d pictures, %d pages)",
        file_path.name,
        json_path.name,
        summary["table_count"],
        summary["picture_count"],
        summary["page_count"],
    )
    return json_path, summary_path, summary


def load_summary(file_path: Path) -> dict[str, Any] | None:
    """Read the cached docling summary sidecar next to ``file_path``.

    Returns ``None`` when the sidecar is missing or malformed — callers
    should treat that as "no structured info available" rather than an
    error.
    """
    summary_path = docling_summary_path(file_path)
    if not summary_path.is_file():
        return None
    try:
        return json.loads(summary_path.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("Failed to read docling summary %s", summary_path, exc_info=True)
        return None


def format_summary_inline(summary: dict[str, Any]) -> str:
    """Render a one-line structural summary for the ``<uploaded_files>`` block.

    Kept intentionally compact (single line, ASCII-only punctuation) so the
    middleware can prepend it after the file size without pushing the
    prompt block past a few hundred bytes per file.
    """
    fmt = summary.get("format", "?")
    parts: list[str] = [fmt]

    if "page_count" in summary and summary["page_count"]:
        parts.append(f"{summary['page_count']} pages")
    if "slide_count" in summary and summary["slide_count"]:
        parts.append(f"{summary['slide_count']} slides")
    if "sheets" in summary and summary["sheets"]:
        sheet_count = len(summary["sheets"])
        parts.append(f"{sheet_count} sheet{'s' if sheet_count != 1 else ''}")
    if summary.get("table_count"):
        parts.append(f"{summary['table_count']} tables")
    if summary.get("picture_count"):
        parts.append(f"{summary['picture_count']} pictures")
    if "sections" in summary and summary["sections"]:
        parts.append(f"{len(summary['sections'])} sections")

    return ", ".join(parts)
