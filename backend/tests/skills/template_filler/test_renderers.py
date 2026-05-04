"""Tests for renderers — dispatch logic + the openpyxl xlsx fallback.

The optional deps (docxtpl, xltpl) are NOT installed in Phase 1, so the
docx render and xltpl render paths use `pytest.importorskip` to defer
to Phase 2. The dispatch + openpyxl fallback paths are testable today.
"""

from __future__ import annotations

import io

import openpyxl
import pytest

from src.skills.template_filler.renderers import RendererError, render_for_extension
from src.skills.template_filler.renderers.xlsx import _alternate_render_with_openpyxl


def _make_xlsx_with_jinja_tags() -> bytes:
    """Produce a tiny xlsx whose cells already carry `{{ name }}` markers."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"] = "客户:"
    ws["B1"] = "{{ customer_name }}"
    ws["A2"] = "金额:"
    ws["B2"] = "金额是 {{ amount }} 元"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_render_for_extension_rejects_unknown():
    with pytest.raises(ValueError, match="unsupported extension"):
        render_for_extension("template.pdf", b"", {})


def test_alternate_openpyxl_substitutes_simple_tags():
    """The standalone openpyxl renderer (no xltpl needed) does scalar substitution."""
    src = _make_xlsx_with_jinja_tags()
    rendered = _alternate_render_with_openpyxl(
        src, {"customer_name": "Acme Hardware", "amount": 12345}
    )

    wb = openpyxl.load_workbook(io.BytesIO(rendered))
    ws = wb.active
    assert ws["B1"].value == "Acme Hardware"
    assert ws["B2"].value == "金额是 12345 元"


def test_alternate_openpyxl_leaves_missing_keys_intact():
    """Missing keys leave `{{ x }}` literal so the bug is visible upstream."""
    src = _make_xlsx_with_jinja_tags()
    rendered = _alternate_render_with_openpyxl(src, {"customer_name": "A"})

    wb = openpyxl.load_workbook(io.BytesIO(rendered))
    ws = wb.active
    assert ws["B1"].value == "A"
    assert "{{ amount }}" in ws["B2"].value


def test_alternate_openpyxl_handles_no_tags():
    """Cells without tags must round-trip unchanged."""
    wb = openpyxl.Workbook()
    wb.active["A1"] = "no template here"
    buf = io.BytesIO()
    wb.save(buf)

    rendered = _alternate_render_with_openpyxl(buf.getvalue(), {"customer_name": "X"})
    wb2 = openpyxl.load_workbook(io.BytesIO(rendered))
    assert wb2.active["A1"].value == "no template here"


def test_render_xlsx_via_dispatch_when_xltpl_missing_raises_importerror():
    """The dispatch path raises ImportError (not RendererError) when xltpl
    isn't installed, so the LangGraph wrapper can fall back / surface a
    distinct error to the AI."""
    pytest.importorskip("openpyxl")  # available in Phase 1
    try:
        import xltpl  # noqa: F401

        pytest.skip("xltpl is installed; ImportError path doesn't apply")
    except ImportError:
        pass

    with pytest.raises(ImportError, match="xltpl"):
        render_for_extension(
            "template.xlsx", _make_xlsx_with_jinja_tags(), {"customer_name": "X"}
        )


def test_render_docx_when_docxtpl_missing_raises_importerror():
    try:
        import docxtpl  # noqa: F401

        pytest.skip("docxtpl is installed; ImportError path doesn't apply")
    except ImportError:
        pass

    # We don't even need a valid docx — the import error fires first.
    with pytest.raises(ImportError, match="docxtpl"):
        render_for_extension("anything.docx", b"\x00\x00", {})


def test_renderer_error_class_distinguishes_from_importerror():
    """Sanity: RendererError is its own exception type."""
    err = RendererError("kaboom")
    assert isinstance(err, RuntimeError)
    assert not isinstance(err, ImportError)
