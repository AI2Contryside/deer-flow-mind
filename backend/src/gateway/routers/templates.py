"""Template-related Gateway routes.

Phase 2a only ships ``POST /api/template/extract_fields``: an internal
endpoint user_service calls during template upload so the AI can suggest
the field schema. The downstream "jinja-ify the original template" step
stays on the Go side (``internal/jinja_renderer``) — this endpoint is
strictly LLM-driven analysis, no file mutation.

Authentication: the route is mounted alongside the rest of /api/* and
relies on the existing IdentityMiddleware to lift X-Tenant-Id / X-User-Id
into logctx. Service-to-service auth between user_service and DeerFlow
is enforced at the network layer (internal cluster only); MVP does not
add a shared-secret check at the application level.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from src.models import create_chat_model
from src.skills.template_filler.extractor import extract_fields
from src.skills.template_filler.jinjaify import jinjaify
from src.skills.template_filler.types import ExtractedField

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/template", tags=["templates"])


# Cap the upload size at the same ceiling user_service applies to template
# uploads (50 MiB per services/user/conf/config.yaml::oss.max_upload_bytes
# at the time of writing). Reject early so we don't hold a giant payload
# in memory before realising the LLM call would be hopeless.
MAX_TEMPLATE_BYTES = 50 * 1024 * 1024

# Default model used by the extractor — matches the lead agent's default
# text model so we don't drag in vision / thinking budget for a pure text
# task. Override via the request's `model_name` form field if needed.
DEFAULT_EXTRACT_MODEL = "deepseek-v3.6-pro"


class ExtractFieldsResponse(BaseModel):
    """JSON response for /api/template/extract_fields."""

    fields: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[dict[str, Any]] = Field(default_factory=list)
    source_text_preview: str | None = None


def _build_llm_callable(model_name: str | None):
    """Wrap create_chat_model into the LLMCallable Protocol the extractor
    expects: (system, user) -> reply text.

    A fresh BaseChatModel is built per request to avoid a shared mutable
    state between concurrent extractions. The model is cheap to construct
    (config-driven; the underlying HTTP client is process-shared).
    """
    chosen = model_name or DEFAULT_EXTRACT_MODEL

    def call(system_prompt: str, user_prompt: str) -> str:
        # `thinking_enabled=False` keeps reasoning tokens off — extraction
        # is structured output, the model doesn't need an internal monologue.
        chat = create_chat_model(name=chosen, thinking_enabled=False)
        messages = [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]
        result = chat.invoke(messages)
        # Both BaseChatModel.invoke return shapes carry .content as str | list.
        content = getattr(result, "content", None)
        if isinstance(content, list):
            # Some providers return a list of content blocks; concat string
            # parts so downstream JSON parsing sees a flat reply.
            return "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content)
        return str(content or "")

    return call


@router.post("/extract_fields", response_model=ExtractFieldsResponse)
async def extract_fields_endpoint(
    file: UploadFile = File(..., description="Original template (.docx / .xlsx)"),
    model_name: str | None = Form(default=None, description="Override the LLM used for extraction"),
) -> ExtractFieldsResponse:
    """Analyse an uploaded template and return the AI-suggested field schema.

    Called by trademind-backend/services/user during template upload. The
    response is consumed verbatim by the FE field-review UI: each field's
    ``original_text`` becomes the search target the Go-side jinja_renderer
    will substitute for ``{{ name }}`` once the user confirms the schema.

    Errors:
      - 400 on empty / oversized uploads or unsupported extensions
      - 500 only if model construction fails entirely; LLM failures are
        folded into ``warnings`` (extractor is best-effort by design).
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
        llm = _build_llm_callable(model_name)
    except Exception as exc:  # noqa: BLE001 — surface model-config failure
        logger.exception("model construction failed for template extract")
        raise HTTPException(status_code=500, detail=f"llm unavailable: {exc}") from exc

    result = extract_fields(body, file.filename, llm_callable=llm)
    logger.info(
        "extract_fields: file=%s fields=%d warnings=%d",
        file.filename,
        len(result.fields),
        len(result.warnings),
    )
    return ExtractFieldsResponse(
        fields=[f.model_dump(mode="json") for f in result.fields],
        warnings=[w.model_dump() for w in result.warnings],
        source_text_preview=result.source_text_preview,
    )


@router.post("/jinjaify")
async def jinjaify_endpoint(
    file: UploadFile = File(..., description="Original template (.docx / .xlsx)"),
    fields: str = Form(..., description='JSON-encoded list of ExtractedField objects'),
) -> Response:
    """Rewrite an uploaded template into a jinja-tagged variant.

    Called by trademind-backend/services/user::UpdateTemplateFields when
    the user confirms the field schema. Replaces the Go-side renderer for
    xlsx (which couldn't address blank cells by anchor) and is now the
    single source of truth for both formats.

    Response: raw bytes of the rewritten file. Per-field outcome (applied
    / skipped + reason) is encoded in the `X-Jinjaify-Outcomes` header
    as JSON so the caller can surface warnings without parsing the
    binary body.
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

    import json as _json

    try:
        raw_fields = _json.loads(fields)
    except _json.JSONDecodeError as exc:
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

    outcomes_payload = _json.dumps(
        [
            {"name": o.name, "applied": o.applied, "reason": o.reason}
            for o in result.outcomes
        ],
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
