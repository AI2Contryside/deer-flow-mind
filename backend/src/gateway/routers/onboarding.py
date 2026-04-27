"""Onboarding status router.

Lets the FE find out whether the ``tenant-onboarding`` subagent has finished
its first-time-setup flow for a given tenant. Completion == ``profile.json``
exists on disk under the tenant's profile directory (the subagent writes
this only after it has seeded ERPNext masters successfully).

The FE polls this after each chat-stream end during the dedicated
"initializing your workspace" step so it can transition to MainApp once
the agent reports done. Tenant identity is taken from the ``X-Tenant-ID``
header; falling back to a query parameter is intentionally not supported
because the trademind-backend gateway already enforces JWT-scoped tenant
identity and proxies the header through.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from src.agents.tenant_profile.store import get_profile_path

router = APIRouter(prefix="/api", tags=["onboarding"])


class OnboardingStatusResponse(BaseModel):
    """Whether the tenant has completed the first-time onboarding."""

    tenant_id: str = Field(..., description="Tenant identifier")
    completed: bool = Field(..., description="True iff profile.json exists for the tenant")


@router.get(
    "/onboarding/status",
    response_model=OnboardingStatusResponse,
    summary="Get tenant onboarding status",
    description=(
        "Returns ``completed=true`` once the tenant-onboarding subagent has "
        "written ``profile.json`` for this tenant. The trademind-backend "
        "gateway proxies this with the JWT-derived tenant id."
    ),
)
async def get_onboarding_status(
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> OnboardingStatusResponse:
    tenant_id = (x_tenant_id or "").strip()
    if not tenant_id:
        raise HTTPException(status_code=400, detail="X-Tenant-ID header is required")
    try:
        completed = get_profile_path(tenant_id).exists()
    except ValueError as exc:
        # tenant_id failed the [A-Za-z0-9_\-]{1,64} validator — treat as
        # caller error rather than 500 so the FE can surface a useful
        # message instead of crashing the init flow.
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return OnboardingStatusResponse(tenant_id=tenant_id, completed=completed)
