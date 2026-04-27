"""Re-export the runtime ``TenantProfile`` schema and provide a minimal builder.

The subagent's job is to produce a ``profile.json`` that the runtime
``tenant_profile`` summarizer will pick up as ``previous_profile`` on its
next pass — so the schema MUST match
``src.agents.tenant_profile.summarizer.schema.TenantProfile`` exactly. We
deliberately import-and-re-export rather than redefine to keep the two in
lockstep.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from src.agents.tenant_profile.summarizer.schema import (
    EntityRef,
    KeyEntities,
    OpenQuestion,
    OperationalPatterns,
    Taxonomy,
    TenantProfile,
)

ONBOARDING_SOURCE = "onboarding-v1"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def minimal_profile_dict(
    tenant_id: str,
    *,
    summary: str = "Tenant onboarding pending: profile not yet seeded.",
    facts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a schema-conformant empty profile.

    Used as a fallback when onboarding is interrupted before any facts are
    collected, so the file on disk is at least a valid ``TenantProfile`` and
    not a partial object the runtime summarizer will reject.
    """
    profile = TenantProfile(
        generated_at=_now_iso(),
        tenant_id=tenant_id,
        summary=summary,
        facts=facts or {},
        operational_patterns=OperationalPatterns(),
        key_entities=KeyEntities(),
        taxonomy=Taxonomy(),
        open_questions=[],
    )
    return profile.model_dump(mode="json")


__all__ = [
    "EntityRef",
    "KeyEntities",
    "ONBOARDING_SOURCE",
    "OpenQuestion",
    "OperationalPatterns",
    "Taxonomy",
    "TenantProfile",
    "minimal_profile_dict",
]
