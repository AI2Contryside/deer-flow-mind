"""template_filler — tenant-template field extraction and rendering.

Two responsibilities, deliberately split:

  1. Field extraction (this package's `extractor`):
       Read an uploaded `.docx` / `.xlsx` template, surface visible text and
       its position, ask the LLM which spans look like "user-fillable"
       placeholders, return a structured schema. The Go side
       (`internal/jinja_renderer`) then uses each field's `original_text`
       to rewrite the file into a jinja-tagged version. Runs at upload time.

  2. Filling a jinja-tagged template (this package's `renderers`):
       Given an already-jinja-tagged template (output of step 1) and a
       data dict, render it via docxtpl / xltpl and return the bytes.
       Runs in chat at AI-tool-call time. Phase 1 ships a skeleton; the
       real OSS upload + LangGraph tool wiring lands in Phase 2.

The two halves are deliberately decoupled so the upload path doesn't
depend on docxtpl/xltpl being importable, and the chat path doesn't pull
in the LLM extraction stack.
"""

from src.skills.template_filler.types import (
    ExtractedField,
    ExtractionResult,
    ExtractionWarning,
    FieldType,
)

__all__ = [
    "ExtractedField",
    "ExtractionResult",
    "ExtractionWarning",
    "FieldType",
]
