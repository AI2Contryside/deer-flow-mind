"""Tests for skill.fill_template_bytes (the pure-function entry).

The function delegates to renderers, so all we verify here is the
exception wrapping (every lower-level error must come back as
TemplateFillError) and the dispatch by output_name.
"""

from __future__ import annotations

import io

import openpyxl

from src.skills.template_filler.skill import (
    TemplateFillError,
    fill_template_bytes,
)


def _xlsx_with_tags(rows: list[tuple[str, str]]) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    for ri, (a, b) in enumerate(rows, start=1):
        ws.cell(row=ri, column=1, value=a)
        ws.cell(row=ri, column=2, value=b)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_fill_template_unsupported_extension_raises():
    try:
        fill_template_bytes(b"x", "out.pdf", {})
    except TemplateFillError as exc:
        assert "unsupported extension" in str(exc)
    else:
        raise AssertionError("expected TemplateFillError")


def test_fill_template_missing_dep_wraps_importerror():
    """If neither docxtpl nor xltpl is installed, the surface error is
    TemplateFillError — callers don't see ImportError leaking through."""
    try:
        import xltpl  # noqa: F401
        return  # xltpl present — this test isn't applicable
    except ImportError:
        pass

    try:
        fill_template_bytes(_xlsx_with_tags([("客户:", "{{ customer }}")]),
                            "out.xlsx", {"customer": "A"})
    except TemplateFillError as exc:
        assert "renderer dependency missing" in str(exc)
    else:
        raise AssertionError("expected TemplateFillError")
