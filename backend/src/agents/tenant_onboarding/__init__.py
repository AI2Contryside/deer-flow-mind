"""Tenant onboarding flow.

A single subagent (registered as ``tenant-onboarding`` in
``src.subagents.builtins``) drives the first-time setup of an ERPNext tenant:
collecting answers via Q&A and/or uploaded Excel, calling the ``erpnext-cli``
skill to seed Company / Warehouse / master data, and writing a v1
``profile.json`` so the runtime ``tenant_profile`` summarizer can take over.

The Python module here only holds the pure helpers:
- ``profile_schema`` — re-export of ``TenantProfile`` plus a minimal builder.
- ``composer`` — turn a collected-facts dict into a validated profile dict.
- ``question_bank`` — the v1 required-question list.
- ``prompt`` — the subagent system prompt template.

The actual Frappe / ERPNext writes are done by the subagent itself through
the ``erpnext-cli`` skill at runtime, not from this module.
"""

from src.agents.tenant_onboarding.composer import (
    OnboardingFacts,
    compose_initial_profile,
)
from src.agents.tenant_onboarding.profile_schema import (
    TenantProfile,
    minimal_profile_dict,
)
from src.agents.tenant_onboarding.prompt import build_system_prompt
from src.agents.tenant_onboarding.question_bank import (
    REQUIRED_QUESTIONS,
    OnboardingQuestion,
)

__all__ = [
    "OnboardingFacts",
    "OnboardingQuestion",
    "REQUIRED_QUESTIONS",
    "TenantProfile",
    "build_system_prompt",
    "compose_initial_profile",
    "minimal_profile_dict",
]
