"""Lead-agent profile system.

Importing this package registers the built-in profiles (business +
bootstrap + tenant_onboarding) via side effects. Additional profiles
register themselves on import — keep registration in the profile module
body, not in __init__.
"""

from __future__ import annotations

# Side-effect imports: each module calls register_profile on import.
from src.agents.lead_agent.profiles import (  # noqa: E402,F401
    bootstrap,
    business,
    template_extraction,
    tenant_onboarding,
)
from src.agents.lead_agent.profiles.registry import (
    get_profile,
    list_profiles,
    register_profile,
    resolve_profile,
)
from src.agents.lead_agent.profiles.types import (
    LeadAgentProfile,
    ModelSpec,
    ProfileContext,
)

__all__ = [
    "LeadAgentProfile",
    "ModelSpec",
    "ProfileContext",
    "get_profile",
    "list_profiles",
    "register_profile",
    "resolve_profile",
]
