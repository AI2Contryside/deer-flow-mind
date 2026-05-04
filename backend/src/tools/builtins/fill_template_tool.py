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
    data: dict[str, Any],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Render the user-selected template with the supplied data and present the result.

    When to use:
    - The current thread has a `selected_template` in state (the user picked a
      template via the FE composer) and they've asked you to generate a
      finalised document.

    When NOT to use:
    - No `selected_template` is set — fall back to writing a plain file under
      `/mnt/user-data/outputs` and calling `present_files` instead.
    - The user only wants partial information — render a small markdown / text
      file rather than burning a templated artifact.

    Args:
        output_name: filename for the generated artifact (e.g.
            "PO-Acme-2026-01.xlsx"). The extension MUST match the source
            template's type (.docx for word, .xlsx for excel) — the renderer
            picks the engine based on this name.
        data: dict whose keys MUST match `selected_template.fields[i].name`.
            Values are stringified for substitution. Keys not in
            `selected_template.fields` are silently ignored; fields the
            user marked as required should not be omitted.
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

    # 1. Download the jinja-tagged template.
    try:
        jinja_bytes = _download_jinja_template(template_url)
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("fill_template: download failed for %s: %s", template_id, exc)
        return _error_command(tool_call_id, f"download template failed: {exc}")

    # 2. Render with the supplied data.
    try:
        rendered = fill_template_bytes(jinja_bytes, output_name, data)
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
