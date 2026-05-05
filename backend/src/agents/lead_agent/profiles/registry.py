"""Profile registry + routing.

Profiles register themselves at import time via ``register_profile``.
``resolve_profile`` picks one for a given ``ProfileContext`` using:

1. Explicit ``ctx.task_type`` — gateway routes use this.
2. Tenant-onboarding heuristic: tenant_id present + no ``profile.json``
   → onboarding profile (added in S2).
3. Fallback to ``business`` profile.

The legacy ``is_bootstrap`` boolean is gone (S5); callers now pass
``task_type="bootstrap_agent"`` directly. The agent factory still maps
``is_bootstrap=True`` in the run configurable to the explicit task_type
for one release.
"""

from __future__ import annotations

import logging

from src.agents.lead_agent.profiles.types import LeadAgentProfile, ProfileContext

logger = logging.getLogger(__name__)

_DEFAULT_TASK_TYPE = "business"
_BOOTSTRAP_TASK_TYPE = "bootstrap_agent"
_ONBOARDING_TASK_TYPE = "tenant_onboarding"

_REGISTRY: dict[str, LeadAgentProfile] = {}


def _tenant_needs_onboarding(tenant_id: str) -> bool:
    """Best-effort: True iff this tenant has no ``profile.json`` on disk yet.

    Routing decision must not depend on a healthy filesystem — any error
    surfaces as "treat as already onboarded" so we route to business and
    don't trap an existing tenant in the onboarding flow because of a
    transient store glitch.
    """
    try:
        from src.agents.tenant_profile.store import get_profile_path

        return not get_profile_path(tenant_id).exists()
    except Exception as exc:
        logger.warning("Failed to check tenant profile presence; assume onboarded: %s", exc)
        return False


def register_profile(profile: LeadAgentProfile) -> None:
    """Register a profile under its ``task_type``. Last writer wins."""
    if profile.task_type in _REGISTRY:
        logger.debug("Re-registering lead-agent profile '%s'", profile.task_type)
    _REGISTRY[profile.task_type] = profile


def get_profile(task_type: str) -> LeadAgentProfile:
    """Look up a registered profile or raise ``KeyError``.

    Honors the ``lead_agent_profiles`` config: a profile that's been
    disabled there is reported as "not registered" so callers can't
    accidentally route to a profile the operator turned off.
    """
    from src.config.lead_agent_profiles_config import get_lead_agent_profiles_config

    if task_type not in _REGISTRY:
        raise KeyError(f"No lead-agent profile registered for task_type='{task_type}'. Registered: {sorted(_REGISTRY.keys())}")
    if not get_lead_agent_profiles_config().is_enabled(task_type):
        raise KeyError(f"Lead-agent profile '{task_type}' is disabled by lead_agent_profiles config. Enable it in config.yaml under lead_agent_profiles.{task_type}.enabled.")
    return _REGISTRY[task_type]


def list_profiles() -> list[str]:
    """Sorted list of registered task_types (for diagnostics)."""
    return sorted(_REGISTRY.keys())


def resolve_profile(ctx: ProfileContext) -> LeadAgentProfile:
    """Pick the profile that should handle this run.

    Priority order:
      1. Explicit ``ctx.task_type`` — wins over everything (gateway uses
         this to route template extraction etc.).
      2. Tenant has a ``tenant_id`` but no ``profile.json`` yet —
         onboarding profile (added in S2).
      3. Fallback to business profile.
    """
    if ctx.task_type:
        return get_profile(ctx.task_type)
    from src.config.lead_agent_profiles_config import get_lead_agent_profiles_config

    cfg = get_lead_agent_profiles_config()
    if ctx.tenant_id and _ONBOARDING_TASK_TYPE in _REGISTRY and cfg.is_enabled(_ONBOARDING_TASK_TYPE) and _tenant_needs_onboarding(ctx.tenant_id):
        return get_profile(_ONBOARDING_TASK_TYPE)
    return get_profile(_DEFAULT_TASK_TYPE)


def _ensure_builtins_registered() -> None:
    """Import side-effect: registers the built-in profiles.

    Called from ``profiles/__init__.py``. Imports are local to avoid
    circular imports during module loading.
    """
    # Imported for their register_profile() side effects.
    from src.agents.lead_agent.profiles import (  # noqa: F401
        bootstrap,
        business,
        tenant_onboarding,
    )
