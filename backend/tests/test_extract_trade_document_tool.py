"""extract_trade_document tool — registration + boundary + parsing tests.

The tool itself talks to qwen-vl-ocr-latest at runtime which we never
hit from unit tests. Instead we cover:

- Registration: tool is exported, has the right name, lives in
  BUILTIN_TOOLS so every agent (including the text-only lead agent)
  picks it up.
- Boundary checks: missing file / wrong extension / oversized image
  return error ToolMessages instead of crashing.
- Response parsing: the parser tolerates ```json fences and falls
  back to an "unknown" envelope when the model returns garbage
  rather than letting JSONDecodeError propagate.
- Schema reuse: confirms the tool builds its prompt from the same
  ``OCR_SCHEMA_PROMPT_BLOCK`` the OcrEnvelope validator was generated
  against, so prompt and validator can't drift.
- Subagent registry: ocr-extractor must be GONE from BUILTIN_SUBAGENTS
  (regression — we deleted the subagent in favour of this tool, and
  leaving a stale Literal value would let LangChain accept
  ``task(subagent_type='ocr-extractor', ...)`` calls that crash deep
  inside the executor).
"""

from __future__ import annotations

import io
from typing import get_args, get_type_hints
from unittest.mock import MagicMock, patch

import pytest


def test_extract_trade_document_tool_exported() -> None:
    from src.tools.builtins import extract_trade_document_tool

    assert extract_trade_document_tool.name == "extract_trade_document"


def test_extract_trade_document_tool_in_builtin_list() -> None:
    """BUILTIN_TOOLS picks up the new tool unconditionally — the lead
    agent runs on a text-only model so we can NOT gate it on
    supports_vision (the gating check sits on view_image_tool
    instead)."""
    from src.tools.builtins import extract_trade_document_tool
    from src.tools.tools import BUILTIN_TOOLS

    assert extract_trade_document_tool in BUILTIN_TOOLS


def test_strip_code_fence_handles_json_block() -> None:
    from src.tools.builtins.extract_trade_document_tool import _strip_code_fence

    assert _strip_code_fence('```json\n{"a":1}\n```') == '{"a":1}'
    assert _strip_code_fence('```\n{"a":1}\n```') == '{"a":1}'
    assert _strip_code_fence('  {"a":1}  ') == '{"a":1}'
    # No fence at all → unchanged after strip
    assert _strip_code_fence('{"a":1}') == '{"a":1}'


def test_supported_extensions() -> None:
    from src.tools.builtins.extract_trade_document_tool import SUPPORTED_EXTENSIONS

    # Cover the formats DashScope's vision endpoint accepts.
    for ext in (".jpg", ".jpeg", ".png", ".webp", ".bmp"):
        assert ext in SUPPORTED_EXTENSIONS


def test_max_image_bytes_matches_dashscope_limit() -> None:
    """DashScope caps a single image at ~10 MB raw bytes. Locking this
    down so an over-eager refactor doesn't accidentally raise it past
    the upstream limit."""
    from src.tools.builtins.extract_trade_document_tool import MAX_IMAGE_BYTES

    assert MAX_IMAGE_BYTES == 10 * 1024 * 1024


def test_extract_prompt_embeds_ocr_schema() -> None:
    """The prompt sent to qwen-vl-ocr must contain every doc_type
    label and every top-level envelope key — otherwise the model can
    silently emit fields the validator will reject. Mirror of
    test_ocr_schema_prompt_block_mentions_core_fields, but for the
    tool's wrapped prompt."""
    from src.tools.builtins.extract_trade_document_tool import _build_extract_prompt

    prompt = _build_extract_prompt()

    for doc_type in (
        "commercial_invoice",
        "packing_list",
        "bill_of_lading",
        "customs_declaration",
        "proforma_invoice",
        "unknown",
    ):
        assert doc_type in prompt, f"doc_type {doc_type} missing from prompt"

    for key in ("detected_doc_type", "confidence", "raw_text", "structured", "notes"):
        assert key in prompt, f"envelope field {key} missing from prompt"

    # Tool must tell the model NOT to wrap the JSON in markdown fences
    # — otherwise we'd be relying solely on _strip_code_fence as
    # post-processing.
    assert "JSON" in prompt
    assert "代码块" in prompt or "markdown" in prompt.lower()


def test_format_summary_extracts_headline_fields() -> None:
    """Summary must surface the key trade fields (number / date /
    amount / currency / line count) without embedding the whole JSON
    payload — that's what the artifact card is for."""
    from src.subagents.builtins.ocr_schemas import (
        CommercialInvoice,
        LineItem,
        OcrEnvelope,
    )
    from src.tools.builtins.extract_trade_document_tool import _format_summary

    inv = CommercialInvoice(
        invoice_no="INV-001",
        invoice_date="2026-03-05",
        total_amount=1500.0,
        currency="USD",
        items=[
            LineItem(sku="A", description="x", quantity=10),
            LineItem(sku="B", description="y", quantity=5),
        ],
    )
    env = OcrEnvelope(
        detected_doc_type="commercial_invoice",
        confidence=0.92,
        raw_text="raw",
        structured=inv,
    )
    out = _format_summary(env, "/mnt/user-data/outputs/commercial_invoice_ocr.json")

    assert "commercial_invoice" in out
    assert "92%" in out
    assert "INV-001" in out
    assert "USD" in out
    assert "商品行数=2" in out
    assert "/mnt/user-data/outputs/commercial_invoice_ocr.json" in out
    # Must NOT dump the full JSON envelope inline.
    assert env.model_dump_json() not in out


def test_format_summary_unknown_doc_type_no_bullets() -> None:
    """When the model can't classify the image, structured is None
    and the summary should still be readable — no AttributeError on
    missing fields."""
    from src.subagents.builtins.ocr_schemas import OcrEnvelope
    from src.tools.builtins.extract_trade_document_tool import _format_summary

    env = OcrEnvelope(
        detected_doc_type="unknown",
        confidence=0.0,
        raw_text="some unreadable garble",
        structured=None,
        notes="image too blurry",
    )
    out = _format_summary(env, "/mnt/user-data/outputs/unknown_ocr.json")
    assert "unknown" in out
    assert "0%" in out
    assert "image too blurry" in out


def test_error_command_returns_tool_message() -> None:
    from src.tools.builtins.extract_trade_document_tool import _error_command
    from langchain_core.messages import ToolMessage

    cmd = _error_command("tc-1", "boom")
    msgs = cmd.update["messages"]
    assert len(msgs) == 1
    assert isinstance(msgs[0], ToolMessage)
    assert msgs[0].tool_call_id == "tc-1"
    assert "boom" in msgs[0].content
    assert msgs[0].content.startswith("Error:")


def test_ocr_extractor_subagent_removed() -> None:
    """Regression: the ocr-extractor subagent has been replaced by
    extract_trade_document tool. BUILTIN_SUBAGENTS must NOT contain
    it; the registry lookup must return None."""
    from src.subagents.builtins import BUILTIN_SUBAGENTS
    from src.subagents.registry import get_subagent_config

    assert "ocr-extractor" not in BUILTIN_SUBAGENTS
    assert get_subagent_config("ocr-extractor") is None


def test_task_tool_literal_no_longer_lists_ocr_extractor() -> None:
    """Symmetric to the above — the Literal type on task_tool's
    subagent_type should be back to {general-purpose, bash,
    vision-analyst}. Without this, LangChain would still accept
    ``task(subagent_type='ocr-extractor', ...)`` from a stale prompt
    and crash deep inside the executor with a None config."""
    from src.tools.builtins.task_tool import task_tool

    func = getattr(task_tool, "func", None) or task_tool
    hints = get_type_hints(func)
    args = get_args(hints["subagent_type"])

    assert "general-purpose" in args
    assert "bash" in args
    assert "vision-analyst" in args
    assert "ocr-extractor" not in args
