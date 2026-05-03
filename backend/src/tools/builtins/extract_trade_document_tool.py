"""extract_trade_document — single-call OCR for foreign-trade documents.

Replaces the previous ``ocr-extractor`` subagent with a single tool
invocation. Cost / correctness rationale:

* Qwen3.6-Flash is the multimodal flash-tier checkpoint we route OCR
  through. We invoke it directly as a tool (one ``HumanMessage`` with
  ``[image_url, text]`` content blocks) instead of wrapping it in a
  ``create_agent`` + ``view_image_tool`` loop. One LLM call per
  document instead of the 3-5 calls a subagent loop spends on
  viewing → thinking → writing JSON → present_files.
* Thinking mode is explicitly disabled in ``config.yaml`` via
  ``extra_body.enable_thinking=false``. OCR is a deterministic
  transcription task; reasoning tokens add latency and cost without
  improving fidelity, and the single-shot HumanMessage payload can't
  carry ``reasoning_content`` across turns even if we wanted to.

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

OCR_MODEL_NAME = "Qwen3.6-Flash"

# DashScope vision endpoint caps a single image at ~10 MB raw bytes;
# base64 encoding adds ~33% so the wire payload stays under the upstream
# request-body limit. Reject earlier so we surface a clear error instead
# of waiting for an opaque 413 / 400 from the provider.
MAX_IMAGE_BYTES = 10 * 1024 * 1024

# Qwen3.6-Flash accepts the standard Qwen-VL image set. We deliberately
# limit the surface to keep error reporting tight — uncommon formats
# can be re-encoded by the user.
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}

# Output cap. Qwen3.6-Flash exposes a 1M context window so a single
# response can technically fit thousands of items, but per-row OCR
# fidelity drops once the model is asked to keep too many parallel
# fields in flight. Clamp at 100 rows per call and report the full
# count in `notes`; users with denser docs can split the image or
# export to Excel. The constant lives in code so the prompt and the
# post-validation can never drift.
MAX_LINE_ITEMS_PER_CALL = 100


def _build_extract_prompt() -> str:
    """Wrap the shared OcrEnvelope schema in an instruction block.

    Kept in a helper (not a module-level constant) so the schema block
    is rebuilt at import time but the wrapping prose stays inline and
    grep-able. The model sees this prompt PLUS the image as a single
    user message — there is no system prompt, intentionally, to keep
    one shape across providers.

    Even with Qwen3.6-Flash's 1M context window we still constrain
    the two fields that grow without bound: ``raw_text`` (full
    transcription) and ``structured.items`` (line-item table). Output
    headroom isn't the bottleneck anymore — per-row fidelity is. Asking
    the model to hold 300+ items in working memory degrades extraction
    quality even when the JSON would fit. Headers and totals stay
    unconstrained; they're always small.
    """
    return f"""请对这张外贸单据图片做严格 OCR 与字段抽取，**只输出 JSON**，不要解释、不要 markdown 代码块包裹。

输出 schema：

{OCR_SCHEMA_PROMPT_BLOCK}

**输出长度约束（必须遵守，否则 JSON 会被截断）：**

1. ``raw_text`` 仅包含**单据头部信息**：标题、单号、日期、客户名/供应商名、地址、说明、合同条款等元数据。
   **不要**逐行抄写商品明细表格——明细已经放在 ``structured.items`` 里。``raw_text`` 控制在 1000 字以内。

2. ``structured.items`` 最多输出 **{MAX_LINE_ITEMS_PER_CALL}** 行。如果实际行数更多：
   - 抽取**前 {MAX_LINE_ITEMS_PER_CALL} 行**（按图上从上到下顺序）
   - 在 ``notes`` 中写明："明细共 N 行，已抽取前 {MAX_LINE_ITEMS_PER_CALL} 行；如需完整数据请拆分图片或导出 Excel"
   - 优先保证已抽取行的字段准确，**不要**为了塞下更多行而省略字段

3. 头部字段（invoice_no, total_amount, currency 等）**始终完整输出**，不受上面约束。

如果不是外贸单据或图片不清晰，``detected_doc_type`` 填 ``"unknown"``、``confidence`` 填 0~0.3、``raw_text`` 填看到的所有文字（同样 ≤1000 字）、``structured`` 填 null、``notes`` 写一句简短说明。"""


def _strip_code_fence(text: str) -> str:
    """Remove ```json ... ``` fences if the model emits them despite
    being told not to. Tolerant: also handles bare ``` fences,
    truncated trailing fences (the closing ``` may have been cut off
    by max_tokens), and leading/trailing whitespace."""
    cleaned = text.strip()
    fence_pattern = re.compile(r"^```(?:json|JSON)?\s*\n?(.*?)\n?```$", re.DOTALL)
    match = fence_pattern.match(cleaned)
    if match:
        return match.group(1).strip()
    # Truncation can leave us with a leading ```json but no closing fence.
    # Strip the opening fence so we can still try to parse the body.
    if cleaned.startswith("```"):
        first_newline = cleaned.find("\n")
        if first_newline >= 0:
            cleaned = cleaned[first_newline + 1 :]
    return cleaned.strip()


def _try_recover_truncated_json(raw: str) -> dict | None:
    """Best-effort recovery of a truncated JSON object.

    Even with Qwen3.6-Flash's 1M context window, responses can still
    get cut off mid-array — provider-side hard caps, network drops,
    or pathological prompts that explode token count. The ``items``
    field is the usual culprit: we land with valid JSON up to some
    last comma followed by an incomplete item and no closing brackets.
    Falling back to ``unknown_ocr.json`` in that case throws away
    everything the model DID extract — doc_type, headers, and the
    valid items already in hand.

    Strategy:
      1. Walk backwards to find the last complete value boundary
         (a ``}`` or ``]`` or a primitive followed by either
         whitespace-and-end or a comma).
      2. Trim everything after that boundary.
      3. Close any open ``[`` / ``{`` with their counterparts.
      4. Try ``json.loads`` on the result.

    Returns the parsed dict on success, ``None`` if recovery isn't
    feasible (the response was so badly mangled that even the
    headers are unreachable).

    NOT a general-purpose JSON repair tool — only handles the
    specific "truncated by token cap" failure mode. For schema
    violations (wrong types, missing required fields) the caller
    should still fall back to the unknown envelope.
    """
    text = raw.strip()
    if not text or not text.startswith("{"):
        return None

    # Find the last position that's a complete value boundary.
    # We scan the string tracking string-escape state and bracket
    # depth, then remember the index right after the last ``}``,
    # ``]`` or string-end that occurs at depth ≥1 inside the items
    # array (depth ≥2 from root). Simpler heuristic that works in
    # practice: cut at the last ``},`` or ``],`` we see.
    last_safe = -1
    in_string = False
    escape = False
    for i, ch in enumerate(text):
        if escape:
            escape = False
            continue
        if ch == "\\" and in_string:
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        # Outside strings, any ``},`` or ``],`` ends a complete sibling
        # value — we can safely truncate just BEFORE the comma.
        if ch == "," and i > 0 and text[i - 1] in "}]":
            last_safe = i  # cut here, dropping the comma onward
        # Also handle the case where we're at the end of the last
        # element of an array/object (no trailing comma): the closing
        # bracket itself is a safe boundary, but only if it's
        # followed by another ``]`` / ``}`` / EOF — meaning the
        # higher-level structure also closed cleanly. We keep this
        # simple by sticking with the comma rule.

    if last_safe < 0:
        return None

    truncated = text[:last_safe]
    # Now balance brackets/braces.
    depth_brace = 0
    depth_bracket = 0
    in_string = False
    escape = False
    for ch in truncated:
        if escape:
            escape = False
            continue
        if ch == "\\" and in_string:
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth_brace += 1
        elif ch == "}":
            depth_brace -= 1
        elif ch == "[":
            depth_bracket += 1
        elif ch == "]":
            depth_bracket -= 1

    if depth_brace < 0 or depth_bracket < 0:
        return None  # something is fundamentally wrong, give up

    # Close arrays first (they're nested inside objects), then objects.
    closing = "]" * depth_bracket + "}" * depth_brace
    candidate = truncated + closing

    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


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
            f"Image too large ({file_size:,} bytes > {MAX_IMAGE_BYTES:,} byte limit). Please compress or split the image and retry.",
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

    # 4. Build the SINGLE user message. One user message with image +
    # instruction — no system prompt, no ToolMessage history, so the
    # payload shape is identical regardless of which Qwen-VL checkpoint
    # is wired in via OCR_MODEL_NAME.
    messages = [
        HumanMessage(
            content=[
                {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{image_b64}"}},
                {"type": "text", "text": _build_extract_prompt()},
            ]
        )
    ]

    # 5. Direct invoke. ``thinking_enabled=False`` because OCR is a
    # deterministic transcription — Qwen3.6-Flash is a thinking-capable
    # model whose thinking mode is also pinned off in config.yaml via
    # ``extra_body.enable_thinking=false``. Both the call-site flag and
    # the static config disable thinking; either alone would suffice
    # but together they survive future config typos.
    #
    # Forward the parent run's metadata (session_id / turn_id) so the
    # token-usage recorder can attribute this OCR call to the same
    # conversational turn as the rest of the agent's work. ``runtime.config``
    # inside a tool is a child config scoped to the tool node, so the
    # turn_id stamped on the parent agent's config does not always
    # propagate. Read from LangChain's active-runnable-config contextvar
    # (the same source title_middleware uses) — that one always reflects
    # the live agent run.
    parent_metadata: dict = {}
    try:
        from langchain_core.runnables.config import var_child_runnable_config

        active_config = var_child_runnable_config.get()
        if active_config is not None:
            parent_metadata = dict(active_config.get("metadata") or {})
    except Exception:  # pragma: no cover - defensive
        parent_metadata = {}
    if not parent_metadata and runtime is not None:
        # Fallback: ToolRuntime.config may carry the metadata too. Cheap
        # belt-and-braces in case the contextvar isn't populated.
        try:
            parent_metadata = dict((runtime.config or {}).get("metadata") or {})
        except Exception:
            parent_metadata = {}

    # TEMP DIAGNOSTIC: log every source of correlation metadata so we can
    # see which (if any) reach the OCR tool's invocation context.
    try:
        from src.storage.token_usage import get_run_metadata as _diag_get_run_metadata

        _diag_ctxvar = _diag_get_run_metadata()
    except Exception as _diag_exc:
        _diag_ctxvar = f"<get_run_metadata raised: {_diag_exc!r}>"
    _diag_runtime_cfg = None
    try:
        _diag_runtime_cfg = (runtime.config or {}).get("metadata") if runtime else None
    except Exception as _diag_exc:
        _diag_runtime_cfg = f"<runtime.config raised: {_diag_exc!r}>"
    logger.warning(
        "ocr-token-diag: parent_metadata=%r ctxvar=%r runtime_cfg_metadata=%r",
        parent_metadata,
        _diag_ctxvar,
        _diag_runtime_cfg,
    )

    invoke_config = {
        "metadata": parent_metadata,
        "tags": ["internal:ocr"],
        "run_name": "extract_trade_document",
    }
    try:
        model = create_chat_model(name=OCR_MODEL_NAME, thinking_enabled=False)
        response = model.invoke(messages, config=invoke_config)
    except Exception as exc:
        logger.exception("OCR model invocation failed for %s", image_path)
        return _error_command(tool_call_id, f"OCR model call failed: {exc}")

    # 6. Extract text. Some chat-model classes return ``content`` as a
    # list of blocks even for text-only replies; flatten to string.
    raw_response = response.content
    if isinstance(raw_response, list):
        raw_response = "".join(block.get("text", "") if isinstance(block, dict) else str(block) for block in raw_response)
    raw_response = str(raw_response).strip()

    if not raw_response:
        return _error_command(tool_call_id, "OCR model returned empty response")

    # 7. Parse JSON. Three-tier strategy:
    #
    #   a. Try ``json.loads`` directly (after stripping any ```json
    #      fences the model emits despite being told not to).
    #   b. If that fails, attempt ``_try_recover_truncated_json`` to
    #      salvage a response that was cut off mid-array by the
    #      output token cap. Real production case: a 321-row sales
    #      order produced ~11k tokens of JSON, qwen-vl-max stopped at
    #      8192, the response came back with an unclosed ``items``
    #      array, and we'd otherwise throw away the doc_type, all
    #      headers, and the ~80 valid items the model HAD extracted.
    #   c. Only if recovery also fails do we fall back to the
    #      ``unknown`` envelope so the user at least sees raw_text.
    cleaned = _strip_code_fence(raw_response)
    envelope: OcrEnvelope
    try:
        envelope_dict = json.loads(cleaned)
        envelope = OcrEnvelope.model_validate(envelope_dict)
    except (json.JSONDecodeError, ValidationError) as exc:
        recovered_dict = _try_recover_truncated_json(cleaned)
        recovered_envelope: OcrEnvelope | None = None
        if recovered_dict is not None:
            # Annotate the recovery in `notes` so the lead agent knows
            # to warn the user that the response was truncated.
            existing_notes = recovered_dict.get("notes") or ""
            truncation_note = "⚠ 模型输出在 token 上限处被截断；已自动抢救可解析部分。若关键字段缺失，请拆分图片后重试。"
            recovered_dict["notes"] = f"{existing_notes}\n{truncation_note}".strip() if existing_notes else truncation_note
            try:
                recovered_envelope = OcrEnvelope.model_validate(recovered_dict)
            except ValidationError as recovery_exc:
                logger.warning(
                    "Truncated-JSON recovery parsed but failed validation (%s)",
                    recovery_exc,
                )

        if recovered_envelope is not None:
            logger.info(
                "OCR truncated; recovered envelope with %d items (raw len=%d, cleaned len=%d)",
                len((recovered_envelope.structured.items if recovered_envelope.structured else []) if hasattr(recovered_envelope.structured, "items") else []),
                len(raw_response),
                len(cleaned),
            )
            envelope = recovered_envelope
        else:
            logger.warning(
                "OCR returned non-conforming JSON (%s); recovery failed; falling back to unknown envelope. raw=%r",
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
    return f"已识别 {doc_type}（置信度 {pct}%{bullets_text}）。完整 JSON 已写到 {output_virtual_path}，已自动推送给用户的 Canvas 卡片。{notes}"
