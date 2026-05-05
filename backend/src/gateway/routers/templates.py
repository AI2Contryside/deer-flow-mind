"""Template-related Gateway routes.

S3 / S6 of the lead-agent task-type refactor: ``POST /api/template/extract_fields``
is now an **agent-driven async kickoff (full B2 contract)**:

  - The route saves the upload into a freshly-created LangGraph thread's
    sandbox uploads dir, kicks off a run with
    ``runtime.context.task_type=template_extraction``, and returns
    ``{"thread_id": ..., "run_id": ..., "status": "running",
       "uploaded_path": "/mnt/user-data/uploads/<safe_filename>",
       "sse_path": "/api/langgraph/threads/<thread_id>/runs/<run_id>/stream",
       "result_marker": {"open_tag": "<extracted_fields>", "close_tag": "</extracted_fields>"}}``
    immediately — there is **no synchronous "result" body**.

  - The upstream caller subscribes to the LangGraph SSE stream at
    ``sse_path`` (which the trademind gateway service proxies for the
    desktop client; user_service itself never reaches LangGraph) and:
      * passes ``ask_clarification`` interrupts straight to the FE for
        native field-review cards (the agent run pauses on each interrupt
        and resumes when the FE replays the user's answer);
      * parses the agent's final AI message for the
        ``<extracted_fields>...</extracted_fields>`` block and JSON-loads
        the array into ``ExtractedField[]``.

The legacy fast path (``extract_fields()`` + ``llm_prompt.SYSTEM_PROMPT``)
is gone — every caller goes through the agent now. There is **no**
``runs.wait`` shortcut: callers that want a synchronous response must
either wrap this stream themselves or invoke the agent in-process via
``DeerFlowClient`` (used by tests and the embedded client).

The ``/jinjaify`` endpoint is unchanged (deterministic file rewrite, no
LLM, no agent).
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from src.config.paths import get_paths
from src.skills.template_filler.jinjaify import jinjaify
from src.skills.template_filler.types import ExtractedField

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/template", tags=["templates"])


# Cap the upload size at the same ceiling user_service applies to template
# uploads (50 MiB per services/user/conf/config.yaml::oss.max_upload_bytes
# at the time of writing). Reject early so we don't hold a giant payload
# in memory before realising the LLM call would be hopeless.
MAX_TEMPLATE_BYTES = 50 * 1024 * 1024

DEFAULT_LANGGRAPH_URL = "http://localhost:2024"
DEFAULT_ASSISTANT_ID = "lead_agent"

# Initial human message passed to the agent when the run kicks off. The
# template_extraction profile's system prompt instructs it to call
# scan_template_fields(file_path) — this message just hands over the path.
_INITIAL_MESSAGE_TEMPLATE = "请抽取这份模板的字段(单文件单轮任务):\nfile_path = {virtual_path}\n调用 scan_template_fields 后,按规则输出 <extracted_fields>...</extracted_fields>。"


class ExtractFieldsAsyncResponse(BaseModel):
    """B2 async kickoff response for /api/template/extract_fields.

    The response carries enough information for the caller to subscribe
    to the LangGraph run stream — *no extracted fields are returned
    here*. The fields land in the agent's final AI message, which the
    caller parses out of the SSE ``messages-tuple`` events.
    """

    thread_id: str
    run_id: str | None = None
    status: str = "running"
    uploaded_path: str = Field(description="Agent-visible path the file landed at.")
    sse_path: str = Field(description="Relative URL of the LangGraph run SSE stream. Subscribe via the trademind gateway-service proxy; do NOT hit LangGraph directly from user_service.")
    result_marker: dict[str, str] = Field(
        default_factory=lambda: {
            "open_tag": "<extracted_fields>",
            "close_tag": "</extracted_fields>",
        },
        description="Markers the agent wraps its final JSON in. The caller looks for this in the final AI message.",
    )


def _safe_filename(name: str) -> str:
    """Strip path components from a user-supplied filename.

    Keeps the basename only — no directory traversal possible. Same
    treatment as the uploads router.
    """
    return os.path.basename(name) or f"upload-{uuid.uuid4().hex[:8]}"


def _extract_langgraph_url(request: Request) -> str:
    """Locate the LangGraph server URL.

    Prefer the gateway's running config (set at startup), fall back to
    the documented default. Tests can override via app state.
    """
    state = getattr(request.app, "state", None)
    if state is not None:
        url = getattr(state, "langgraph_url", None)
        if isinstance(url, str) and url:
            return url
    return os.environ.get("DEER_FLOW_LANGGRAPH_URL", DEFAULT_LANGGRAPH_URL)


async def _kickoff_run(
    *,
    langgraph_url: str,
    assistant_id: str,
    thread_id: str,
    virtual_path: str,
    requested_model_name: str | None,
    tenant_id: str | None,
    user_id: str | None,
) -> str | None:
    """Start a template_extraction run on the LangGraph server.

    Uses ``runs.create`` (not ``runs.wait``) so we can return immediately
    while the agent works. Returns the ``run_id`` (best-effort — the SDK
    sometimes returns a stub object on certain backends).
    """
    from langgraph_sdk import get_client

    client = get_client(url=langgraph_url)

    runtime_context: dict[str, Any] = {
        "task_type": "template_extraction",
        # Extraction never wants the thinking trace — keep parity with
        # the profile's resolve_model.
        "thinking_enabled": False,
        "is_plan_mode": False,
        "subagent_enabled": False,
    }
    if requested_model_name:
        runtime_context["model_name"] = requested_model_name
    if tenant_id:
        runtime_context["tenant_id"] = tenant_id
    if user_id:
        runtime_context["user_id"] = user_id

    initial = _INITIAL_MESSAGE_TEMPLATE.format(virtual_path=virtual_path)
    run = await client.runs.create(
        thread_id,
        assistant_id,
        input={"messages": [{"role": "human", "content": initial}]},
        context=runtime_context,
    )
    if isinstance(run, dict):
        return run.get("run_id")
    return getattr(run, "run_id", None)


@router.post("/extract_fields", response_model=ExtractFieldsAsyncResponse)
async def extract_fields_endpoint(
    request: Request,
    file: UploadFile = File(..., description="Original template (.docx / .xlsx)"),
    model_name: str | None = Form(default=None, description="Override the LLM the agent uses for extraction"),
) -> ExtractFieldsAsyncResponse:
    """B2 async kickoff: save upload, create thread, start agent, return ids.

    Returns immediately with the thread/run identifiers. The caller is
    responsible for subscribing to the LangGraph SSE stream and parsing
    ``<extracted_fields>...</extracted_fields>`` from the final AI message.

    Errors:
      - 400 on empty / oversized uploads or unsupported extensions
      - 502 if the LangGraph server can't be reached / fails to create
        the thread or run
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="missing filename")
    body = await file.read()
    if not body:
        raise HTTPException(status_code=400, detail="empty upload")
    if len(body) > MAX_TEMPLATE_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"template too large: {len(body)} bytes, max {MAX_TEMPLATE_BYTES}",
        )

    safe = _safe_filename(file.filename)
    ext = os.path.splitext(safe)[1].lower()
    if ext not in (".docx", ".xlsx"):
        raise HTTPException(status_code=400, detail=f"unsupported template extension: {ext or 'none'}")

    # Lift identity from logctx-friendly headers; trademind-backend
    # already injects these on internal calls.
    tenant_id = request.headers.get("X-Tenant-Id") or request.headers.get("x-tenant-id")
    user_id = request.headers.get("X-User-Id") or request.headers.get("x-user-id")

    langgraph_url = _extract_langgraph_url(request)

    try:
        from langgraph_sdk import get_client
    except ImportError as exc:
        raise HTTPException(status_code=502, detail=f"langgraph_sdk unavailable: {exc}") from exc

    try:
        client = get_client(url=langgraph_url)
        thread = await client.threads.create()
    except Exception as exc:  # noqa: BLE001 — surface as 502
        logger.exception("LangGraph thread creation failed")
        raise HTTPException(status_code=502, detail=f"langgraph thread create failed: {exc}") from exc

    thread_id = thread["thread_id"] if isinstance(thread, dict) else getattr(thread, "thread_id", None)
    if not thread_id:
        raise HTTPException(status_code=502, detail="langgraph returned no thread_id")

    # Drop the upload into the thread's sandbox uploads dir. The agent
    # will see it at ``/mnt/user-data/uploads/<safe>`` per the standard
    # virtual-path mapping.
    uploads_dir = get_paths().sandbox_uploads_dir(thread_id)
    uploads_dir.mkdir(parents=True, exist_ok=True)
    target = uploads_dir / safe
    target.write_bytes(body)
    virtual_path = f"/mnt/user-data/uploads/{safe}"

    try:
        run_id = await _kickoff_run(
            langgraph_url=langgraph_url,
            assistant_id=DEFAULT_ASSISTANT_ID,
            thread_id=str(thread_id),
            virtual_path=virtual_path,
            requested_model_name=model_name,
            tenant_id=tenant_id,
            user_id=user_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("LangGraph run.create failed for thread %s", thread_id)
        raise HTTPException(status_code=502, detail=f"langgraph run create failed: {exc}") from exc

    logger.info(
        "extract_fields kickoff: file=%s thread_id=%s run_id=%s tenant=%s",
        safe,
        thread_id,
        run_id,
        tenant_id or "-",
    )

    return ExtractFieldsAsyncResponse(
        thread_id=str(thread_id),
        run_id=str(run_id) if run_id else None,
        status="running",
        uploaded_path=virtual_path,
        sse_path=f"/api/langgraph/threads/{thread_id}/runs/{run_id}/stream" if run_id else f"/api/langgraph/threads/{thread_id}/history",
    )


@router.post("/jinjaify")
async def jinjaify_endpoint(
    file: UploadFile = File(..., description="Original template (.docx / .xlsx)"),
    fields: str = Form(..., description="JSON-encoded list of ExtractedField objects"),
) -> Response:
    """Rewrite an uploaded template into a jinja-tagged variant.

    Unchanged from pre-S3 — this endpoint is deterministic file rewriting,
    not LLM analysis, so it stays synchronous.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="missing filename")
    body = await file.read()
    if not body:
        raise HTTPException(status_code=400, detail="empty upload")
    if len(body) > MAX_TEMPLATE_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"template too large: {len(body)} bytes, max {MAX_TEMPLATE_BYTES}",
        )

    try:
        raw_fields = json.loads(fields)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"invalid fields JSON: {exc}") from exc
    if not isinstance(raw_fields, list):
        raise HTTPException(status_code=400, detail="fields must be a JSON array")

    parsed_fields: list[ExtractedField] = []
    for raw in raw_fields:
        try:
            parsed_fields.append(ExtractedField.model_validate(raw))
        except Exception as exc:  # noqa: BLE001 — surface as 400 with detail
            raise HTTPException(status_code=400, detail=f"invalid field: {exc}") from exc

    try:
        result = jinjaify(body, file.filename, parsed_fields)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("jinjaify failed for %s", file.filename)
        raise HTTPException(status_code=500, detail=f"jinjaify failed: {exc}") from exc

    outcomes_payload = json.dumps(
        [{"name": o.name, "applied": o.applied, "reason": o.reason} for o in result.outcomes],
        ensure_ascii=False,
    )
    logger.info(
        "jinjaify: file=%s fields_in=%d applied=%d skipped=%d",
        file.filename,
        len(parsed_fields),
        len(result.applied),
        len(result.skipped),
    )
    return Response(
        content=result.content,
        media_type=result.content_type,
        headers={"X-Jinjaify-Outcomes": outcomes_payload},
    )
