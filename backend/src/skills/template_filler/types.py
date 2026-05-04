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

    `original_text` is the literal substring the LLM saw in the source
    template. The Go-side `jinja_renderer` searches for this string and
    replaces it with `{{ name }}`. The match must be unambiguous within
    its paragraph or it will be skipped (see jinja_renderer.SkipReason).
    """

    name: str = Field(
        ...,
        description="snake_case identifier used as the jinja variable name. Must be a bare identifier (no dots, no operators).",
    )
    label: str = Field(..., description="Chinese display label for the form UI.")
    type: FieldType = FieldType.STRING
    required: bool = True
    original_text: str = Field(
        ...,
        description="Verbatim placeholder text in the source template. Used by the jinja-rewriter to know what to substitute.",
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
