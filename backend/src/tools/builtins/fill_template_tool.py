"""fill_template — render a user-selected template with AI-supplied data.

Flow:
  1. Read ``selected_template`` from ThreadState. The gateway populates
     this when chat_stream sees ``selected_template`` in the request body
     (see Phase 2c). State carries the jinja-tagged template's signed
     download URL plus the field schema the user reviewed at upload time.
  2. Download the jinja-tagged file (signed URL → bytes).
  3. Render via ``skill.fill_template_bytes`` — pure function, picks
     docxtpl / xltpl by extension, returns OOXML bytes.
  4. Write to the thread's ``/mnt/user-data/outputs/<output_name>``,
     push to OSS through the same ``present_file_tool`` helpers, and
     emit ``artifacts`` + ``artifact_metadata`` so the desktop client's
     Canvas auto-previews.

Bucket choice:
  Phase 2a writes to ``trademind-chat-session`` (per-thread, following
  the existing ``chat_artifact_key`` pattern). User decision E favoured
  ``trademind-sys`` for permanence; that requires a parallel signed-URL
  path in trademind-backend's gateway (sys bucket isn't on the canvas
  preview path today). MVP lands the chat-bucket flow so canvas works
  end-to-end with zero gateway changes; the migration is tracked as a
  follow-up.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated, Any

import httpx
from langchain.tools import InjectedToolCallId, ToolRuntime, tool
from langchain_core.messages import ToolMessage
from langgraph.types import Command
from langgraph.typing import ContextT

from src.agents.thread_state import ThreadState
from src.sandbox.tools import get_thread_data, replace_virtual_path
from src.skills.template_filler.skill import TemplateFillError, fill_template_bytes
from src.tools.builtins.present_file_tool import _build_metadata, _push_to_oss

logger = logging.getLogger(__name__)

# Defensive cap on the jinja template we download: rendering balloons the
# output (a 100 KB template + a 1000-row data dict can produce a 10 MB
# xlsx), so the *input* shouldn't exceed user_service's 50 MiB upload
# limit anyway.
MAX_TEMPLATE_BYTES = 50 * 1024 * 1024
DOWNLOAD_TIMEOUT_SECONDS = 30


def _error_command(tool_call_id: str, message: str) -> Command:
    """Return a Command that surfaces an error to the lead agent without
    touching artifact state. Mirrors extract_trade_document_tool's pattern."""
    return Command(
        update={"messages": [ToolMessage(f"Error: {message}", tool_call_id=tool_call_id)]},
    )


def _download_jinja_template(url: str) -> bytes:
    """Fetch the jinja-tagged template via signed URL.

    Follows redirects (the user_service download endpoint 302s to OSS).
    Streams to memory with a hard byte cap to avoid OOM on a malformed
    URL pointing at something huge.
    """
    with httpx.Client(timeout=DOWNLOAD_TIMEOUT_SECONDS, follow_redirects=True) as client:
        with client.stream("GET", url) as resp:
            resp.raise_for_status()
            chunks: list[bytes] = []
            total = 0
            for chunk in resp.iter_bytes():
                total += len(chunk)
                if total > MAX_TEMPLATE_BYTES:
                    raise ValueError(f"template too large: > {MAX_TEMPLATE_BYTES} bytes")
                chunks.append(chunk)
    return b"".join(chunks)


@tool("fill_template", parse_docstring=True)
def fill_template_tool(
    runtime: ToolRuntime[ContextT, ThreadState],
    output_name: str,
    tool_call_id: Annotated[str, InjectedToolCallId],
    data: dict[str, Any] | None = None,
    data_list: list[dict[str, Any]] | None = None,
) -> Command:
    """Render the user's selected template with the supplied data, return the artifact.

    The thread carries a `selected_template` state field (template_id, name,
    type, fields, signed download URL) populated by the gateway when the user
    picks a template via the FE composer. This tool is the ONLY supported
    way to produce an artifact based on that template — never roll your own
    docx / xlsx, never use `present_files` for templated output.

    ============================================================
    WHEN TO CALL
    ============================================================
    Call this tool whenever ALL of:
      1. The thread has `selected_template` set (a `<selected_template>` block
         is visible in your context); AND
      2. The user has supplied — explicitly or implicitly — enough field data
         to fill the template (or you've decided to render a partially-filled
         draft for them to review).

    Do NOT ask "which template did you pick?" or "what type of file?" —
    both are already known from `selected_template`. If the user is asking
    something unrelated to the template, ignore the selection.

    If required fields are missing, do not call this tool: ask
    `ask_clarification` listing the SPECIFIC missing fields by their `label`
    (e.g. "还需要 工号、部门"), not a generic "需要什么数据".

    ============================================================
    SINGLE RECORD vs BATCH — pick one parameter
    ============================================================
    The template's file type drives the choice:

      • **xlsx, multiple records** (multiple employees, multiple line items,
        any "fill a table with N rows"):
            data_list=[{...}, {...}, ...]
        → ONE call, ONE workbook with N rows. The tool replicates the
        template's seed row once per dict. NEVER call once per record on
        xlsx — that produces N separate files, which is wrong.

      • **xlsx, single record** OR **docx, any record**:
            data={...}
        → ONE call, ONE file. docx has no row-loop concept; if the user
        gives multiple records for a docx template, call this tool once
        per record (each with its own `output_name`).

    `data` and `data_list` are mutually exclusive — pass exactly one. If
    you pass both, `data_list` wins.

    ============================================================
    DATA KEY CONTRACT
    ============================================================
    Every key in `data` (or in each dict of `data_list`) MUST match a
    `name` listed in the `<selected_template>` block. Keys outside that
    schema are silently ignored — they don't error, but they don't render
    either. Required fields (marked 必填 in the block) should not be
    omitted; the tool will leave the literal `{{ name }}` tag in the
    output cell so a missed field is visible to the user instead of
    silently empty.

    ============================================================
    OUTPUT NAME
    ============================================================
    `output_name` must end in the template's file extension (.docx or
    .xlsx). Pick a descriptive, user-recognisable name like
    "员工信息_2026Q1.xlsx" or "采购合同_深圳泰科_20260301.docx". Avoid
    timestamps that aren't in the user's frame; avoid "output.xlsx".

    Args:
        output_name: filename for the generated artifact. Extension MUST
            match the source template's type (.docx / .xlsx). The renderer
            dispatches on this extension.
        data: single record dict. Keys must match `selected_template.fields[*].name`.
            Mutually exclusive with `data_list`.
        data_list: multiple records, one per row in the rendered xlsx.
            Each dict follows the same key contract as `data`. xlsx-batch
            shape — see the SINGLE RECORD vs BATCH section above.
    """
    state = runtime.state or {}
    selected = state.get("selected_template")
    if not isinstance(selected, dict):
        return _error_command(
            tool_call_id,
            "no template selected for this thread; ask the user to pick one or use present_files for plain output",
        )

    template_url = selected.get("jinja_download_url")
    template_id = selected.get("template_id")
    template_name_human = selected.get("name") or "<unnamed>"
    if not template_url or not template_id:
        return _error_command(
            tool_call_id,
            "selected_template is malformed (missing template_id or jinja_download_url)",
        )

    # Resolve the data argument: data_list wins when given (lets the LLM
    # pass a batch in one call). Both empty / both set are user errors;
    # one set wins is the expected path. data_list takes precedence so a
    # confused model sending both still picks the multi-row shape on
    # xlsx rather than silently dropping rows.
    effective_data: dict[str, Any] | list[dict[str, Any]]
    if data_list is not None:
        if not isinstance(data_list, list) or any(not isinstance(d, dict) for d in data_list):
            return _error_command(tool_call_id, "data_list must be a list of dicts")
        effective_data = data_list
    elif data is not None:
        if not isinstance(data, dict):
            return _error_command(tool_call_id, "data must be a dict")
        effective_data = data
    else:
        return _error_command(tool_call_id, "must pass either `data` or `data_list`")

    # 1. Download the jinja-tagged template.
    try:
        jinja_bytes = _download_jinja_template(template_url)
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("fill_template: download failed for %s: %s", template_id, exc)
        return _error_command(tool_call_id, f"download template failed: {exc}")

    # 2. Render with the supplied data.
    try:
        rendered = fill_template_bytes(jinja_bytes, output_name, effective_data)
    except TemplateFillError as exc:
        return _error_command(tool_call_id, f"render failed: {exc}")

    # 3. Write to the thread's outputs directory so the artifact path
    #    matches the present_files contract (only `/mnt/user-data/outputs`
    #    files are presentable).
    try:
        thread_data = get_thread_data(runtime)
    except ValueError as exc:
        return _error_command(tool_call_id, f"thread data unavailable: {exc}")

    outputs_virtual = "/mnt/user-data/outputs"
    outputs_actual_dir = Path(replace_virtual_path(outputs_virtual, thread_data))
    try:
        outputs_actual_dir.mkdir(parents=True, exist_ok=True)
        output_actual_path = outputs_actual_dir / output_name
        output_actual_path.write_bytes(rendered.content)
    except OSError as exc:
        return _error_command(tool_call_id, f"write output failed: {exc}")
    output_virtual_path = f"{outputs_virtual}/{output_name}"

    # 4. Mirror to OSS + build artifact metadata. Best-effort — if OSS is
    #    unconfigured the artifact still appears in state and the gateway
    #    falls back to local serving.
    thread_id = runtime.context.get("thread_id") if runtime.context else None
    tenant_id = runtime.context.get("tenant_id") if runtime.context else None
    uploaded = False
    if thread_id:
        uploaded = _push_to_oss(thread_id, tenant_id, output_virtual_path)
    metadata = _build_metadata(thread_id, tenant_id, output_virtual_path, uploaded)

    summary = f"已基于模板「{template_name_human}」生成 {output_name},请在画布中查看。"
    update: dict = {
        "messages": [ToolMessage(summary, tool_call_id=tool_call_id)],
        "artifacts": [output_virtual_path],
    }
    if metadata is not None:
        update["artifact_metadata"] = [metadata]
    return Command(update=update)
