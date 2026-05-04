"""End-to-end field extraction: file bytes → ExtractionResult.

Public entry: `extract_fields(file_bytes, file_name, llm_callable=None)`.

`llm_callable` is injected so unit tests can pass a deterministic stub.
Production code passes a thin wrapper around the project's
`src.models.create_chat_model(...)`. Wiring the wrapper lives in the
gateway router (Phase 2) — keeping the dependency at the boundary keeps
this module unit-testable without booting the model factory.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from typing import Protocol

from src.skills.template_filler.llm_prompt import (
    SYSTEM_PROMPT,
    build_user_message,
    parse_llm_reply,
)
from src.skills.template_filler.text_scanner import (
    TextFragment,
    chunk_for_prompt,
    scan_docx,
    scan_xlsx,
)
from src.skills.template_filler.types import (
    ExtractedField,
    ExtractionResult,
    ExtractionWarning,
)

logger = logging.getLogger(__name__)


class LLMCallable(Protocol):
    """Minimum surface needed from a chat model.

    Takes (system_prompt, user_prompt) → reply text. Wider Anthropic /
    LangChain APIs collapse to this in production via a small wrapper.
    """

    def __call__(self, system_prompt: str, user_prompt: str) -> str: ...


def _scan_by_extension(file_bytes: bytes, file_name: str) -> tuple[list[TextFragment], str | None]:
    """Dispatch to docx/xlsx scanner by extension.

    Returns (fragments, error). On unsupported extension or scanner failure,
    fragments is [] and error is a code the caller bubbles up as a warning.
    """
    ext = os.path.splitext(file_name)[1].lower()
    try:
        if ext == ".docx":
            return scan_docx(file_bytes), None
        if ext == ".xlsx":
            return scan_xlsx(file_bytes), None
    except Exception as exc:  # noqa: BLE001 — surface every parse failure
        logger.warning("text scan failed for %s: %s", file_name, exc, exc_info=True)
        return [], "scan_failed"
    return [], "unsupported_extension"


def _dedupe_fields(fields: list[ExtractedField]) -> list[ExtractedField]:
    """Drop later duplicates that share `name` or `original_text`.

    Multi-batch extraction can produce overlapping fields when adjacent
    chunks see the same text. We keep the first occurrence so order is
    preserved for the review UI.
    """
    seen_names: set[str] = set()
    seen_originals: set[str] = set()
    out: list[ExtractedField] = []
    for f in fields:
        if f.name in seen_names or f.original_text in seen_originals:
            continue
        seen_names.add(f.name)
        seen_originals.add(f.original_text)
        out.append(f)
    return out


def _preview(fragments: list[TextFragment], limit: int = 1500) -> str:
    """Truncated text view used in ExtractionResult.source_text_preview."""
    parts: list[str] = []
    used = 0
    for frag in fragments:
        line = f"[{frag.location_hint}] {frag.text}"
        if used + len(line) > limit:
            parts.append("…")
            break
        parts.append(line)
        used += len(line) + 1
    return "\n".join(parts)


def extract_fields(
    file_bytes: bytes,
    file_name: str,
    llm_callable: LLMCallable | Callable[[str, str], str] | None = None,
) -> ExtractionResult:
    """Run the full pipeline against a single uploaded template.

    `llm_callable` defaults to a stub that returns "[]" — useful so unit
    tests covering only the scan/path-of-text-through-the-system can run
    without an LLM. Production callers (gateway router) supply a real
    chat-model wrapper.
    """
    fragments, scan_err = _scan_by_extension(file_bytes, file_name)
    warnings: list[ExtractionWarning] = []
    if scan_err:
        warnings.append(
            ExtractionWarning(
                code=scan_err,
                message=f"无法解析模板内容: {file_name}",
            )
        )
        return ExtractionResult(fields=[], warnings=warnings)
    if not fragments:
        warnings.append(ExtractionWarning(code="no_text_extracted", message="未提取到任何可见文本"))
        return ExtractionResult(fields=[], warnings=warnings)

    # Stub LLM if none provided. Useful for tests of the scan-only path.
    llm: Callable[[str, str], str] = llm_callable if llm_callable is not None else (lambda _s, _u: "[]")

    all_fields: list[ExtractedField] = []
    parse_failed_chunks = 0
    for batch in chunk_for_prompt(fragments):
        user_msg = build_user_message(batch)
        try:
            reply = llm(SYSTEM_PROMPT, user_msg)
        except Exception as exc:  # noqa: BLE001
            logger.warning("LLM call failed for chunk: %s", exc, exc_info=True)
            warnings.append(ExtractionWarning(code="llm_call_failed", message=str(exc)))
            continue
        fields, err = parse_llm_reply(reply)
        if err:
            parse_failed_chunks += 1
        all_fields.extend(fields)

    if parse_failed_chunks:
        warnings.append(
            ExtractionWarning(
                code="llm_unparseable",
                message=f"{parse_failed_chunks} 个文本批次的 LLM 输出无法解析",
            )
        )

    return ExtractionResult(
        fields=_dedupe_fields(all_fields),
        warnings=warnings,
        source_text_preview=_preview(fragments),
    )
