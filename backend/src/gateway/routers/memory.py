"""Memory API router for retrieving and managing global memory data."""

from typing import Annotated

from fastapi import APIRouter, Header
from pydantic import BaseModel, Field

from src.agents.memory.storage import create_empty_memory
from src.agents.memory.updater import get_memory_data_with_tenant, reload_memory_data
from src.config.memory_config import get_memory_config

router = APIRouter(prefix="/api", tags=["memory"])


class ContextSection(BaseModel):
    """Model for context sections (user and history)."""

    summary: str = Field(default="", description="Summary content")
    updatedAt: str = Field(default="", description="Last update timestamp")


class UserContext(BaseModel):
    """Model for user context."""

    workContext: ContextSection = Field(default_factory=ContextSection)
    personalContext: ContextSection = Field(default_factory=ContextSection)
    topOfMind: ContextSection = Field(default_factory=ContextSection)


class HistoryContext(BaseModel):
    """Model for history context."""

    recentMonths: ContextSection = Field(default_factory=ContextSection)
    earlierContext: ContextSection = Field(default_factory=ContextSection)
    longTermBackground: ContextSection = Field(default_factory=ContextSection)


class Fact(BaseModel):
    """Model for a memory fact."""

    id: str = Field(..., description="Unique identifier for the fact")
    content: str = Field(..., description="Fact content")
    category: str = Field(default="context", description="Fact category")
    confidence: float = Field(default=0.5, description="Confidence score (0-1)")
    createdAt: str = Field(default="", description="Creation timestamp")
    source: str = Field(default="unknown", description="Source thread ID")


class MemoryResponse(BaseModel):
    """Response model for memory data."""

    version: str = Field(default="1.0", description="Memory schema version")
    lastUpdated: str = Field(default="", description="Last update timestamp")
    user: UserContext = Field(default_factory=UserContext)
    history: HistoryContext = Field(default_factory=HistoryContext)
    facts: list[Fact] = Field(default_factory=list)


class MemoryConfigResponse(BaseModel):
    """Response model for memory configuration."""

    enabled: bool = Field(..., description="Whether memory is enabled")
    storage_path: str = Field(..., description="Path to memory storage file")
    debounce_seconds: int = Field(..., description="Debounce time for memory updates")
    max_facts: int = Field(..., description="Maximum number of facts to store")
    fact_confidence_threshold: float = Field(..., description="Minimum confidence threshold for facts")
    injection_enabled: bool = Field(..., description="Whether memory injection is enabled")
    max_injection_tokens: int = Field(..., description="Maximum tokens for memory injection")


class MemoryStatusResponse(BaseModel):
    """Response model for memory status."""

    config: MemoryConfigResponse
    data: MemoryResponse


@router.get(
    "/memory",
    response_model=MemoryResponse,
    summary="Get Memory Data",
    description=(
        "Retrieve memory data for the tenant identified by the X-Tenant-ID "
        "header. Without the header an empty memory document is returned — "
        "we deliberately do NOT fall back to a shared file because that "
        "leaked one tenant's memory to another."
    ),
)
async def get_memory(
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> MemoryResponse:
    """Get memory data. Tenant-scoped when X-Tenant-ID is present.

    Fail-closed: if the gateway didn't forward a tenant id we return an
    empty memory document rather than the global file. The previous
    behaviour exposed cross-tenant data whenever the header was missing.
    """
    if not x_tenant_id or not x_tenant_id.strip():
        return MemoryResponse(**create_empty_memory())
    memory_data = get_memory_data_with_tenant(x_tenant_id.strip())
    return MemoryResponse(**memory_data)


@router.get(
    "/memory/tenant",
    response_model=MemoryResponse,
    summary="Get Tenant Memory Data",
    description="Retrieve memory data for a specific tenant. Requires X-Tenant-ID header.",
)
async def get_tenant_memory(
    x_tenant_id: Annotated[str, Header()],
) -> MemoryResponse:
    """Get memory data for a specific tenant.

    This endpoint requires X-Tenant-ID header.

    Args:
        x_tenant_id: Tenant ID from header

    Returns:
        The memory data for the specified tenant.

    Raises:
        HTTPException: If tenant_id is missing
    """
    memory_data = get_memory_data_with_tenant(x_tenant_id)
    return MemoryResponse(**memory_data)


@router.post(
    "/memory/reload",
    response_model=MemoryResponse,
    summary="Reload Memory Data",
    description="Reload memory data from the storage file, refreshing the in-memory cache.",
)
async def reload_memory() -> MemoryResponse:
    """Reload memory data from file.

    This forces a reload of the memory data from the storage file,
    useful when the file has been modified externally.

    Returns:
        The reloaded memory data.
    """
    memory_data = reload_memory_data()
    return MemoryResponse(**memory_data)


@router.get(
    "/memory/config",
    response_model=MemoryConfigResponse,
    summary="Get Memory Configuration",
    description="Retrieve the current memory system configuration.",
)
async def get_memory_config_endpoint() -> MemoryConfigResponse:
    """Get the memory system configuration.

    Returns:
        The current memory configuration settings.
    """
    config = get_memory_config()
    return MemoryConfigResponse(
        enabled=config.enabled,
        storage_path=config.storage_path,
        debounce_seconds=config.debounce_seconds,
        max_facts=config.max_facts,
        fact_confidence_threshold=config.fact_confidence_threshold,
        injection_enabled=config.injection_enabled,
        max_injection_tokens=config.max_injection_tokens,
    )


@router.get(
    "/memory/status",
    response_model=MemoryStatusResponse,
    summary="Get Memory Status",
    description="Retrieve both memory configuration and current data in a single request.",
)
async def get_memory_status(
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> MemoryStatusResponse:
    """Get the memory system status including configuration and data.

    Tenant-scoped via X-Tenant-ID, with the same fail-closed contract as
    GET /memory: a missing header returns an empty memory document
    instead of the shared global file.
    """
    config = get_memory_config()
    if x_tenant_id and x_tenant_id.strip():
        memory_data = get_memory_data_with_tenant(x_tenant_id.strip())
    else:
        memory_data = create_empty_memory()

    return MemoryStatusResponse(
        config=MemoryConfigResponse(
            enabled=config.enabled,
            storage_path=config.storage_path,
            debounce_seconds=config.debounce_seconds,
            max_facts=config.max_facts,
            fact_confidence_threshold=config.fact_confidence_threshold,
            injection_enabled=config.injection_enabled,
            max_injection_tokens=config.max_injection_tokens,
        ),
        data=MemoryResponse(**memory_data),
    )
