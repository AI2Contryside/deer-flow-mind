"""Tests for the docling extraction utility.

Covers:
- Sidecar path naming (avoids ``with_suffix`` foot-guns).
- ``is_extractable`` / ``is_derived_artifact`` predicates.
- ``build_summary`` aggregates counts and per-format structure.
- ``format_summary_inline`` renders compactly for prompt injection.
- ``extract_with_docling`` produces both sidecars when the underlying
  library is patched (no docling install needed for unit tests).
- ``load_summary`` round-trips the on-disk sidecar.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import patch

from src.utils import document_extract as de

# ---------------------------------------------------------------------------
# Path / predicate helpers
# ---------------------------------------------------------------------------


def test_docling_json_path_uses_compound_suffix(tmp_path):
    src = tmp_path / "report.pdf"
    assert de.docling_json_path(src).name == "report.docling.json"


def test_docling_summary_path_uses_compound_suffix(tmp_path):
    src = tmp_path / "deck.pptx"
    assert de.docling_summary_path(src).name == "deck.docling.summary.json"


def test_docling_paths_handle_multiple_dots_in_stem(tmp_path):
    src = tmp_path / "v2.final.docx"
    # stem is "v2.final" → sidecar must include the full stem
    assert de.docling_json_path(src).name == "v2.final.docling.json"
    assert de.docling_summary_path(src).name == "v2.final.docling.summary.json"


def test_is_extractable_for_office_pdf(tmp_path):
    for ext in (".pdf", ".docx", ".pptx", ".xlsx", ".doc", ".ppt", ".xls"):
        assert de.is_extractable(tmp_path / f"x{ext}")


def test_is_extractable_rejects_unrelated(tmp_path):
    for ext in (".txt", ".md", ".csv", ".json", ""):
        assert not de.is_extractable(tmp_path / f"x{ext}")


def test_is_derived_artifact_detects_sidecars(tmp_path):
    assert de.is_derived_artifact(tmp_path / "x.docling.json")
    assert de.is_derived_artifact(tmp_path / "x.docling.summary.json")
    assert not de.is_derived_artifact(tmp_path / "x.json")
    assert not de.is_derived_artifact(tmp_path / "x.docx")


# ---------------------------------------------------------------------------
# build_summary — uses lightweight fakes that mirror docling's attribute shape
# ---------------------------------------------------------------------------


def _fake_doc(*, texts=None, tables=None, pictures=None, pages=None):
    return SimpleNamespace(
        texts=texts or [],
        tables=tables or [],
        pictures=pictures or [],
        pages=pages or {},
    )


def _fake_section_header(text: str, level: int = 1):
    return SimpleNamespace(label="section_header", text=text, level=level)


def _fake_table(rows: int, cols: int, caption: str | None = None):
    captions = []
    if caption is not None:
        captions.append(SimpleNamespace(text=caption))
    return SimpleNamespace(
        data=SimpleNamespace(num_rows=rows, num_cols=cols),
        captions=captions,
    )


def _fake_picture():
    return SimpleNamespace(label="picture")


def _fake_page(width=612.0, height=792.0):
    return SimpleNamespace(size=SimpleNamespace(width=width, height=height))


def test_build_summary_pdf_collects_sections_and_counts(tmp_path):
    src = tmp_path / "paper.pdf"
    src.write_bytes(b"%PDF-1.4 stub")
    doc = _fake_doc(
        texts=[_fake_section_header("Intro", 1), _fake_section_header("Methods", 2)],
        tables=[_fake_table(10, 5)],
        pictures=[_fake_picture(), _fake_picture()],
        pages={1: _fake_page(), 2: _fake_page()},
    )

    summary = de.build_summary(doc, src)

    assert summary["format"] == "pdf"
    assert summary["page_count"] == 2
    assert summary["table_count"] == 1
    assert summary["picture_count"] == 2
    assert summary["text_count"] == 2
    assert summary["sections"] == [
        {"level": 1, "text": "Intro"},
        {"level": 2, "text": "Methods"},
    ]


def test_build_summary_xlsx_lists_sheets(tmp_path):
    src = tmp_path / "data.xlsx"
    src.write_bytes(b"PK")
    doc = _fake_doc(
        tables=[
            _fake_table(100, 5, caption="Q1 Sales"),
            _fake_table(50, 3, caption="Q2 Sales"),
        ]
    )

    summary = de.build_summary(doc, src)

    assert summary["format"] == "xlsx"
    assert summary["sheets"] == [
        {"name": "Q1 Sales", "rows": 100, "cols": 5},
        {"name": "Q2 Sales", "rows": 50, "cols": 3},
    ]


def test_build_summary_xlsx_falls_back_to_default_sheet_name(tmp_path):
    src = tmp_path / "anon.xlsx"
    src.write_bytes(b"PK")
    doc = _fake_doc(tables=[_fake_table(2, 2), _fake_table(3, 3)])

    summary = de.build_summary(doc, src)

    assert [s["name"] for s in summary["sheets"]] == ["Sheet1", "Sheet2"]


def test_build_summary_pptx_records_slide_count(tmp_path):
    src = tmp_path / "deck.pptx"
    src.write_bytes(b"PK")
    doc = _fake_doc(pages={1: _fake_page(), 2: _fake_page(), 3: _fake_page()})

    summary = de.build_summary(doc, src)

    assert summary["format"] == "pptx"
    assert summary["slide_count"] == 3


def test_build_summary_caps_section_count(tmp_path):
    src = tmp_path / "long.docx"
    src.write_bytes(b"PK")
    many = [_fake_section_header(f"H{i}", 1) for i in range(60)]
    doc = _fake_doc(texts=many)

    summary = de.build_summary(doc, src)

    assert len(summary["sections"]) == de.MAX_SECTIONS_IN_SUMMARY


# ---------------------------------------------------------------------------
# format_summary_inline
# ---------------------------------------------------------------------------


def test_format_summary_inline_pdf():
    s = {"format": "pdf", "page_count": 12, "table_count": 3, "picture_count": 5, "sections": [{}, {}]}
    rendered = de.format_summary_inline(s)
    assert "pdf" in rendered
    assert "12 pages" in rendered
    assert "3 tables" in rendered
    assert "5 pictures" in rendered
    assert "2 sections" in rendered


def test_format_summary_inline_xlsx_singular_sheet():
    s = {"format": "xlsx", "sheets": [{"name": "Only", "rows": 1, "cols": 1}]}
    rendered = de.format_summary_inline(s)
    assert "1 sheet" in rendered and "1 sheets" not in rendered


def test_format_summary_inline_pptx():
    s = {"format": "pptx", "slide_count": 24}
    rendered = de.format_summary_inline(s)
    assert "24 slides" in rendered


# ---------------------------------------------------------------------------
# extract_with_docling — patch DocumentConverter so unit tests don't need
# the heavy docling stack on the test machine.
# ---------------------------------------------------------------------------


def test_extract_with_docling_writes_both_sidecars(tmp_path):
    src = tmp_path / "report.pdf"
    src.write_bytes(b"%PDF-1.4 stub")

    fake_doc = _fake_doc(
        texts=[_fake_section_header("Intro")],
        tables=[_fake_table(2, 2)],
        pages={1: _fake_page()},
    )
    fake_doc.export_to_dict = lambda: {"schema_name": "DoclingDocument", "name": "report"}

    class FakeConverter:
        def convert(self, _path):
            return SimpleNamespace(document=fake_doc)

    with patch("docling.document_converter.DocumentConverter", FakeConverter):
        json_path, summary_path, summary = asyncio.run(de.extract_with_docling(src))

    assert json_path is not None and json_path.is_file()
    assert summary_path is not None and summary_path.is_file()
    assert summary is not None
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload == {"schema_name": "DoclingDocument", "name": "report"}
    persisted_summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert persisted_summary["format"] == "pdf"
    assert persisted_summary["page_count"] == 1


def test_extract_with_docling_returns_none_on_failure(tmp_path):
    src = tmp_path / "broken.pdf"
    src.write_bytes(b"not really a pdf")

    class ExplodingConverter:
        def convert(self, _path):
            raise RuntimeError("boom")

    with patch("docling.document_converter.DocumentConverter", ExplodingConverter):
        json_path, summary_path, summary = asyncio.run(de.extract_with_docling(src))

    assert json_path is None and summary_path is None and summary is None
    # No sidecars on failure — caller falls back to original-only behaviour.
    assert not (tmp_path / "broken.docling.json").exists()
    assert not (tmp_path / "broken.docling.summary.json").exists()


def test_load_summary_round_trips(tmp_path):
    src = tmp_path / "deck.pptx"
    src.write_bytes(b"PK")
    summary_path = de.docling_summary_path(src)
    summary_path.write_text(json.dumps({"format": "pptx", "slide_count": 7}), encoding="utf-8")

    loaded = de.load_summary(src)

    assert loaded == {"format": "pptx", "slide_count": 7}


def test_load_summary_returns_none_when_missing(tmp_path):
    src = tmp_path / "ghost.pdf"
    src.write_bytes(b"%PDF stub")
    assert de.load_summary(src) is None


def test_load_summary_returns_none_on_malformed_json(tmp_path):
    src = tmp_path / "bad.docx"
    src.write_bytes(b"PK")
    de.docling_summary_path(src).write_text("not json", encoding="utf-8")

    assert de.load_summary(src) is None
