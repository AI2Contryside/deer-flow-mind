"""Shared data types for the template_filler package.

These are also the on-the-wire shape carried over `/api/template/extract_fields`
(when that route lands in Phase 2). Keeping them as pydantic models means we
get validation + JSON schema for free.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class FieldType(str, Enum):
    """User-facing field types.

    v1 only renders STRING — number/date/currency are accepted in the schema
    so the review UI can pre-populate the right input widget, but the
    renderer stringifies everything before substitution.
    """

    STRING = "string"
    NUMBER = "number"
    DATE = "date"
    CURRENCY = "currency"
    ENUM = "enum"  # reserved


class ExtractedField(BaseModel):
    """One AI-detected fillable placeholder.

    Two complementary modes — exactly one is normally populated for a
    given field:

      • Inline-text mode:    `original_text` is the verbatim placeholder
        substring (`[客户名]`, `<日期>`, etc.). Renderer does a paragraph-
        level text replace on the source.

      • Header-cell mode:    `cell_anchor` is the *data-row* cell address
        (e.g. `Sheet1!B2`) where the value should land. Used for "header
        + blank rows" templates where the user intends column headers as
        labels and the data rows as where the AI fills values. Renderer
        writes `{{ name }}` directly into that cell, leaving headers intact.

    A field with neither populated is rejected at jinjaify time. A field
    with both populated is rendered via cell_anchor (exact location wins
    over text search).
    """

    name: str = Field(
        ...,
        description="snake_case identifier used as the jinja variable name. Must be a bare identifier (no dots, no operators).",
    )
    label: str = Field(..., description="Chinese display label for the form UI.")
    type: FieldType = FieldType.STRING
    required: bool = True
    original_text: str = Field(
        default="",
        description="Inline-text mode: verbatim placeholder string in the source template. Empty when the field is in header-cell mode.",
    )
    cell_anchor: str | None = Field(
        default=None,
        description='Header-cell mode (xlsx only): "<sheet>!<cell>" address of the data-row cell that should hold the value, e.g. "Sheet1!B2". Mutually exclusive with original_text in normal use.',
    )
    location_hint: str | None = Field(
        default=None,
        description='Where the LLM saw the field, for the review UI: "B3" (xlsx cell address), "段落 4" (docx paragraph index), or free text.',
    )
    description: str | None = Field(
        default=None,
        description="Short semantic description, e.g. '采购方公司全称'.",
    )


class ExtractionWarning(BaseModel):
    """Non-fatal issue surfaced during extraction."""

    code: str = Field(..., description='Machine-readable: "no_text_extracted", "llm_unparseable", ...')
    message: str


class ExtractionResult(BaseModel):
    """Full result from extracting fields out of a single template upload."""

    fields: list[ExtractedField]
    warnings: list[ExtractionWarning] = Field(default_factory=list)
    # `source_text_preview` lets callers see the truncated view the LLM was
    # given when the result is unsatisfying. Helpful for debugging / review.
    source_text_preview: str | None = None
