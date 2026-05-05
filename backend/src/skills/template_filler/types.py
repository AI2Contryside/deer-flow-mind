"""Shared data types for the template_filler package.

These are also the on-the-wire shape carried over `/api/template/extract_fields`
(when that route lands in Phase 2). Keeping them as pydantic models means we
get validation + JSON schema for free.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class FieldType(StrEnum):
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
    """One AI-detected fillable position in a template.

    The target users (foreign-trade clerks) don't know what placeholders
    are — they fill data into Word/Excel templates that have *labels and
    blank space*, not `[XXX]` markers. Two modes cover the real shapes:

      • Docx label-anchor mode (`original_text` + `anchor_mode`):
          The LLM identifies a label or placeholder substring. `anchor_mode`
          decides whether jinjaify keeps the anchor or eats it:

            - "append" (default): the anchor *is* the label and we want it
              preserved. Example: `original_text="客户名称:"` →
              ``客户名称:{{ customer_name }}``. Used when the label has
              meaning the user expects to keep ("Customer Name:", "Date:").

            - "replace": the anchor is a placeholder string with no
              standalone meaning. Example: `original_text="________"`,
              `original_text="在此填入客户名"`, or `original_text="[客户名]"`.
              jinjaify drops the anchor entirely and writes `{{ name }}`.

      • Xlsx header-cell mode (`cell_anchor`):
          `cell_anchor` is a data-row cell address (e.g. `Sheet1!B2`).
          Renderer writes `{{ name }}` directly into that cell, leaving
          column headers intact. Used for "header row + blank rows" entry
          forms typical in Excel templates.

    A field with neither original_text nor cell_anchor is rejected at
    jinjaify time. A field with both populated takes the cell_anchor path
    (exact location wins over text search).
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
        description="Docx label-anchor mode: the substring jinjaify will search for in paragraphs. Combined with anchor_mode to decide whether the anchor is preserved or replaced. Empty when in xlsx header-cell mode.",
    )
    anchor_mode: Literal["append", "replace"] = Field(
        default="append",
        description=(
            "How jinjaify treats original_text once found. "
            '"append" keeps the anchor and writes the jinja tag after it '
            '(label-style: "客户:" → "客户:{{ customer_name }}"). '
            '"replace" eats the anchor entirely and writes the tag in its '
            'place (placeholder-style: "____" → "{{ field }}"). '
            "Ignored when cell_anchor is set."
        ),
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
