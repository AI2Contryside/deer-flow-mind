"""Built-in subagent configurations."""

from .bash_agent import BASH_AGENT_CONFIG
from .general_purpose import GENERAL_PURPOSE_CONFIG
from .tenant_onboarding import TENANT_ONBOARDING_CONFIG

__all__ = [
    "BASH_AGENT_CONFIG",
    "GENERAL_PURPOSE_CONFIG",
    "TENANT_ONBOARDING_CONFIG",
]

# Registry of built-in subagents
BUILTIN_SUBAGENTS = {
    "general-purpose": GENERAL_PURPOSE_CONFIG,
    "bash": BASH_AGENT_CONFIG,
    "tenant-onboarding": TENANT_ONBOARDING_CONFIG,
}
