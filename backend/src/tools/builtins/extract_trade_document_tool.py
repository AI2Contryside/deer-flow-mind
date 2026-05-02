"""extract_trade_document — single-call OCR for foreign-trade documents.

Replaces the previous ``ocr-extractor`` subagent with a single tool
invocation. Cost / correctness rationale:

* qwen-vl-ocr-latest is the OCR-specialised checkpoint (~5-10x cheaper
  output tokens than qwen-vl-plus / qwen-vl-max), but it is a
  stateless single-turn model — it 400s on requests that contain a
  system message, multi-turn history, or tool-call sequences, which
  is exactly what LangChain's ``create_agent`` + ``view_image_tool``
  loop produces. So we cannot route it through a subagent.
* As a tool, we control the messages payload directly: one
  ``HumanMessage`` with ``[image_url, text]`` content blocks. That is
  the only shape qwen-vl-ocr accepts, and it costs exactly one LLM
  call per document instead of the 3-5 calls a subagent loop spends
  on viewing → thinking → writing JSON → present_files.

Lead agent's ``<vision_routing>`` block routes here for trade
documents (Commercial Invoice, Packing List, B/L, Customs
Declaration, Proforma Invoice). Free-form image Q&A still goes to
the ``vision-analyst`` subagent on qwen-vl-plus.

State updates returned via ``Command``:

* ``messages`` — a ``ToolMessage`` summarising what was extracted so
  the lead agent can write a user-facing one-liner.
* ``artifacts`` + ``artifact_metadata`` — wired through the same
  reducers as ``present_file_tool``, so the desktop client renders
  the OCR JSON with ``OcrResultViewer`` automatically. We do NOT
  expect lead agent to call ``present_files`` after — that would be
  one wasted LLM call.
"""

from __future__ import annotations

import base64
import json
import logging
import mimetypes
import re
from pathlib import Path
from typing import Annotated

from langchain.tools import InjectedToolCallId, ToolRuntime, tool
from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.types import Command
from langgraph.typing import ContextT
from pydantic import ValidationError

from src.agents.thread_state import ThreadState
from src.models import create_chat_model
from src.sandbox.tools import get_thread_data, replace_virtual_path
from src.subagents.builtins.ocr_schemas import OCR_SCHEMA_PROMPT_BLOCK, OcrEnvelope
from src.tools.builtins.present_file_tool import _build_metadata, _push_to_oss

logger = logging.getLogger(__name__)

OCR_MODEL_NAME = "qwen-vl-ocr-latest"

# DashScope vision endpoint caps a single image at ~10 MB raw bytes;
# base64 encoding adds ~33% so the wire payload stays under the upstream
# request-body limit. Reject earlier so we surface a clear error instead
# of waiting for an opaque 413 / 400 from the provider.
MAX_IMAGE_BYTES = 10 * 1024 * 1024

# qwen-vl-ocr accepts the same set of formats as the other Qwen-VL
# models. We deliberately limit the surface to keep error reporting
# tight — uncommon formats can be re-encoded by the user.
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def _build_extract_prompt() -> str:
    """Wrap the shared OcrEnvelope schema in an instruction block.

    Kept in a helper (not a module-level constant) so the schema block
    is rebuilt at import time but the wrapping prose stays inline and
    grep-able. The model sees this prompt PLUS the image as a single
    user message — there is no system prompt, intentionally, because
    qwen-vl-ocr's single-turn protocol does not accept one.
    """
    return f"""请对这张外贸单据图片做严格 OCR 与字段抽取，**只输出 JSON**，不要解释、不要 markdown 代码块包裹。

输出 schema：

{OCR_SCHEMA_PROMPT_BLOCK}

如果不是外贸单据或图片不清晰，``detected_doc_type`` 填 ``"unknown"``、``confidence`` 填 0~0.3、``raw_text`` 填看到的所有文字、``structured`` 填 null、``notes`` 写一句简短说明。"""


def _strip_code_fence(text: str) -> str:
    """Remove ```json ... ``` fences if the model emits them despite
    being told not to. Tolerant: also handles bare ``` fences and
    leading/trailing whitespace."""
    cleaned = text.strip()
    fence_pattern = re.compile(r"^```(?:json|JSON)?\s*\n?(.*?)\n?```$", re.DOTALL)
    match = fence_pattern.match(cleaned)
    if match:
        return match.group(1).strip()
    return cleaned


def _error_command(tool_call_id: str, msg: str) -> Command:
    return Command(
        update={"messages": [ToolMessage(f"Error: {msg}", tool_call_id=tool_call_id)]},
    )


@tool("extract_trade_document", parse_docstring=True)
def extract_trade_document_tool(
    runtime: ToolRuntime[ContextT, ThreadState],
    image_path: str,
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Extract structured fields from a foreign-trade document image in a single LLM call.

    Use this for trade documents — Commercial Invoice / Packing List /
    Bill of Lading / Customs Declaration / Proforma Invoice — written
    to ``/mnt/user-data/uploads/<filename>`` by the user. Returns a
    structured JSON envelope (validated against ``OcrEnvelope``)
    written to ``/mnt/user-data/outputs/<doc_type>_ocr.json`` and
    surfaced to the user as a Canvas card automatically. You do NOT
    need to call ``present_files`` after — this tool already wires
    ``artifacts`` / ``artifact_metadata`` into state.

    For free-form image questions on non-document images (product
    photos, factory shots, screenshots, "what does this look like"),
    delegate to ``task(subagent_type='vision-analyst')`` instead.

    Args:
        image_path: Absolute virtual path to the image, typically ``/mnt/user-data/uploads/<filename>``. Supported formats: jpg, jpeg, png, webp, bmp. Max 10 MB.
    """
    # 1. Resolve virtual path → host path. Same machinery the other
    # vision tools use, so behaviour is consistent across local /
    # sandbox modes.
    thread_data = get_thread_data(runtime)
    actual_path_str = replace_virtual_path(image_path, thread_data)
    path = Path(actual_path_str)

    if not path.is_absolute():
        return _error_command(tool_call_id, f"Path must be absolute, got: {image_path}")
    if not path.exists():
        return _error_command(tool_call_id, f"Image file not found: {image_path}")
    if not path.is_file():
        return _error_command(tool_call_id, f"Path is not a file: {image_path}")
    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        supported = ", ".join(sorted(SUPPORTED_EXTENSIONS))
        return _error_command(tool_call_id, f"Unsupported image format: {path.suffix}. Supported: {supported}")

    # 2. Size pre-check. DashScope's hard limit is ~10 MB; we reject
    # earlier with a clearer message.
    file_size = path.stat().st_size
    if file_size <= 0:
        return _error_command(tool_call_id, f"Empty image file: {image_path}")
    if file_size > MAX_IMAGE_BYTES:
        return _error_command(
            tool_call_id,
            f"Image too large ({file_size:,} bytes > {MAX_IMAGE_BYTES:,} byte limit). "
            f"Please compress or split the image and retry.",
        )

    # 3. Read + base64-encode for inline data URL.
    try:
        image_bytes = path.read_bytes()
    except OSError as exc:
        return _error_command(tool_call_id, f"Failed to read image: {exc}")
    image_b64 = base64.b64encode(image_bytes).decode("utf-8")
    mime_type, _ = mimetypes.guess_type(str(path))
    if mime_type is None:
        # Conservative default. qwen-vl-ocr accepts image/jpeg even
        # for some PNGs; a wrong mime is much better than a missing one.
        mime_type = "image/jpeg"

    # 4. Build the SINGLE user message. No system prompt — qwen-vl-ocr
    # rejects it. No ToolMessage history — qwen-vl-ocr rejects that
    # too. Just one user message with image + instruction.
    messages = [
        HumanMessage(
            content=[
                {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{image_b64}"}},
                {"type": "text", "text": _build_extract_prompt()},
            ]
        )
    ]

    # 5. Direct invoke. ``thinking_enabled=False`` — qwen-vl-ocr has no
    # thinking mode anyway and the flag would noop, but we set it
    # explicitly so the factory doesn't try to apply thinking
    # parameters from a future config.yaml typo.
    try:
        model = create_chat_model(name=OCR_MODEL_NAME, thinking_enabled=False)
        response = model.invoke(messages)
    except Exception as exc:
        logger.exception("OCR model invocation failed for %s", image_path)
        return _error_command(tool_call_id, f"OCR model call failed: {exc}")

    # 6. Extract text. Some chat-model classes return ``content`` as a
    # list of blocks even for text-only replies; flatten to string.
    raw_response = response.content
    if isinstance(raw_response, list):
        raw_response = "".join(
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in raw_response
        )
    raw_response = str(raw_response).strip()

    if not raw_response:
        return _error_command(tool_call_id, "OCR model returned empty response")

    # 7. Parse JSON. Tolerate ```json fences even though the prompt
    # forbids them — vision models occasionally relapse, and falling
    # back to "unknown" envelope when JSON is genuinely malformed
    # keeps the user experience smooth.
    cleaned = _strip_code_fence(raw_response)
    envelope: OcrEnvelope
    try:
        envelope_dict = json.loads(cleaned)
        envelope = OcrEnvelope.model_validate(envelope_dict)
    except (json.JSONDecodeError, ValidationError) as exc:
        logger.warning(
            "OCR returned non-conforming JSON (%s); falling back to unknown envelope. raw=%r",
            exc,
            raw_response[:500],
        )
        envelope = OcrEnvelope(
            detected_doc_type="unknown",
            confidence=0.0,
            raw_text=raw_response,
            structured=None,
            notes=f"模型输出不是合规 JSON：{type(exc).__name__}",
        )

    # 8. Write JSON to outputs as a typed artifact. Filename convention
    # ``<doc_type>_ocr.json`` is matched by the frontend
    # ``ArtifactRouter`` to pick ``OcrResultViewer``.
    outputs_virtual_dir = "/mnt/user-data/outputs"
    outputs_actual_dir = Path(replace_virtual_path(outputs_virtual_dir, thread_data))
    try:
        outputs_actual_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return _error_command(tool_call_id, f"Could not create outputs dir: {exc}")

    output_filename = f"{envelope.detected_doc_type}_ocr.json"
    output_actual_path = outputs_actual_dir / output_filename
    try:
        output_actual_path.write_text(
            envelope.model_dump_json(indent=2),
            encoding="utf-8",
        )
    except OSError as exc:
        return _error_command(tool_call_id, f"Failed to write output JSON: {exc}")

    output_virtual_path = f"{outputs_virtual_dir}/{output_filename}"

    # 9. Mirror to OSS + build artifact metadata so the gateway can
    # serve a signed URL to the desktop client. Best-effort — if OSS
    # is unconfigured, the artifact still appears in state and the
    # gateway falls back to local serving.
    thread_id = runtime.context.get("thread_id") if runtime.context else None
    tenant_id = runtime.context.get("tenant_id") if runtime.context else None

    uploaded_to_oss = False
    if thread_id:
        uploaded_to_oss = _push_to_oss(thread_id, tenant_id, output_virtual_path)
    metadata = _build_metadata(thread_id, tenant_id, output_virtual_path, uploaded_to_oss)

    # 10. Compose user-facing summary so lead agent has something
    # concrete to paraphrase. Limit to ~2 lines so the agent doesn't
    # echo a wall of text when it would be cleaner to just say
    # "see Canvas".
    summary = _format_summary(envelope, output_virtual_path)

    update: dict = {
        "messages": [ToolMessage(summary, tool_call_id=tool_call_id)],
        "artifacts": [output_virtual_path],
    }
    if metadata is not None:
        update["artifact_metadata"] = [metadata]

    return Command(update=update)


def _format_summary(envelope: OcrEnvelope, output_virtual_path: str) -> str:
    """Build the ToolMessage payload — short, factual, machine-readable.

    The lead agent will read this and turn it into a one-liner for the
    user (per the ``<vision_routing>`` prompt rule "引用 Canvas 卡片，
    不要把整段 JSON 贴在聊天里"). We intentionally do NOT dump the full
    JSON envelope into the message — that's what the artifact card is
    for, and pasting JSON is exactly what we're trying to avoid.
    """
    pct = round(envelope.confidence * 100)
    doc_type = envelope.detected_doc_type
    bullets: list[str] = []
    structured = envelope.structured
    if structured is not None:
        # Pull doc-type-specific headline fields. We use getattr with a
        # default of None so this stays robust against schema additions.
        for attr, label in (
            ("invoice_no", "单号"),
            ("pl_no", "装箱单号"),
            ("bl_no", "提单号"),
            ("declaration_no", "报关单号"),
            ("invoice_date", "日期"),
            ("pl_date", "日期"),
            ("bl_date", "日期"),
            ("declaration_date", "日期"),
            ("total_amount", "金额"),
            ("currency", "币种"),
            ("total_packages", "总件数"),
        ):
            val = getattr(structured, attr, None)
            if val is None:
                continue
            bullets.append(f"{label}={val}")
            # 4 bullets covers single-number/date/amount/currency for
            # the common doc types — enough for the lead agent to write
            # a useful one-liner without dumping every field.
            if len(bullets) >= 4:
                break
        items = getattr(structured, "items", None) or []
        if items:
            bullets.append(f"商品行数={len(items)}")
    bullets_text = ("，" + "，".join(bullets)) if bullets else ""
    notes = f"\n备注：{envelope.notes}" if envelope.notes else ""
    return (
        f"已识别 {doc_type}（置信度 {pct}%{bullets_text}）。"
        f"完整 JSON 已写到 {output_virtual_path}，已自动推送给用户的 Canvas 卡片。"
        f"{notes}"
    )
