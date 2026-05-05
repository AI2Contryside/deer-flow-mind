"""``scan_template_fields`` builtin tool — exposes the deterministic
docx/xlsx visible-text scanner to the ``template_extraction`` lead-agent
profile.

The tool is the *only* path the agent should use to read a template's
content during field extraction:

  - It hides the docx-vs-xlsx dispatch.
  - It returns a stable, agent-friendly text format so the agent doesn't
    burn turns parsing binary file bytes.
  - It caps file size and surfaces every scanner failure as ``Error: ...``
    so the agent can decide whether to ``ask_clarification`` or emit an
    empty result.

Tool name + parameter shape are part of the
``template_extraction_prompt.py`` contract — keep them in sync if either
is renamed.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from langchain_core.tools import tool
from langgraph.prebuilt import ToolRuntime

from src.agents.thread_state import ThreadState
from src.sandbox.tools import get_thread_data, replace_virtual_path

logger = logging.getLogger(__name__)

# Hard cap consistent with the gateway's ``MAX_TEMPLATE_BYTES`` (50 MiB).
# Larger templates almost certainly aren't fillable forms; keep the agent
# from running an LLM pass over a payload that won't extract well anyway.
_MAX_TEMPLATE_BYTES = 50 * 1024 * 1024


def _format_chunks(chunks_iter) -> str:
    """Render scanner chunks into the per-chunk bullet list the prompt expects."""
    blocks: list[str] = []
    for batch in chunks_iter:
        lines = [f"[{f.location_hint}] {f.text.replace(chr(10), ' ⏎ ')}" for f in batch]
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


@tool("scan_template_fields", parse_docstring=True)
def scan_template_fields_tool(runtime: ToolRuntime[object, ThreadState], file_path: str) -> str:
    """Scan a .docx or .xlsx template and return its visible text with position hints.

    Use this once at the start of a template_extraction run. The returned
    text is structured per-line as ``[<location_hint>] <text>`` — exactly
    the shape the field-recognition rules in your system prompt reference.

    Args:
        file_path: Path to the template file. Accepts the agent-visible
            virtual path (``/mnt/user-data/uploads/<name>``) or the
            equivalent host path.

    Returns:
        The scanner output as a plain string. On unsupported extension,
        oversized file, or scanner failure returns ``Error: <code>`` and
        the agent should not retry — fall through to ``ask_clarification``
        or to emitting an empty ``<extracted_fields>[]</extracted_fields>``.
    """
    from src.skills.template_filler.text_scanner import (
        chunk_for_prompt,
        scan_docx,
        scan_xlsx,
    )

    thread_data = get_thread_data(runtime)
    physical_path = replace_virtual_path(file_path, thread_data)
    path = Path(physical_path)

    if not path.exists() or not path.is_file():
        return f"Error: file_not_found ({file_path})"

    size = path.stat().st_size
    if size > _MAX_TEMPLATE_BYTES:
        return f"Error: file_too_large ({size} bytes, max {_MAX_TEMPLATE_BYTES})"

    ext = os.path.splitext(path.name)[1].lower()
    if ext not in (".docx", ".xlsx"):
        return f"Error: unsupported_extension ({ext or 'no extension'})"

    try:
        file_bytes = path.read_bytes()
        if ext == ".docx":
            fragments = scan_docx(file_bytes)
        else:
            fragments = scan_xlsx(file_bytes)
    except Exception as exc:  # noqa: BLE001 — scanner errors are routine
        logger.warning("scan_template_fields failed for %s: %s", file_path, exc, exc_info=True)
        return f"Error: scan_failed ({exc.__class__.__name__}: {exc})"

    if not fragments:
        return "Error: no_text_extracted"

    return _format_chunks(chunk_for_prompt(fragments))
