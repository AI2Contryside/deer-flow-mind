"""template_filler — tenant-template scanning, jinjaify, and rendering.

Three responsibilities, deliberately split:

  1. Visible-text scanning (``text_scanner``):
       Read an uploaded `.docx` / `.xlsx` template and return its visible
       text + position hints as ``TextFragment`` records. Deterministic;
       no LLM. Used by the ``scan_template_fields`` agent tool.

       (Field *extraction* — deciding which spans are placeholders — used
       to live here too in ``extractor.py`` + ``llm_prompt.py``. After S3
       of the lead-agent task-type refactor that's gone: extraction now
       runs as the ``template_extraction`` lead-agent profile, kicked off
       via ``POST /api/template/extract_fields``.)

  2. Jinja-tagging an extracted template (``jinjaify``):
       Given an already-extracted ``ExtractedField[]`` schema and the
       original template bytes, rewrite each anchor into a jinja variable
       and emit a jinja-tagged template. Runs at upload time after the
       agent's extraction completes.

  3. Filling a jinja-tagged template (``renderers``):
       Given a jinja-tagged template plus a data dict, render via
       docxtpl / xltpl. Runs in chat at AI-tool-call time.

The three halves are deliberately decoupled so each path imports only
what it needs.
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
