"""Config-driven enable/disable + per-profile model override for the
lead-agent profile system (S4 of the task-type refactor).

Loaded from ``config.yaml`` under the top-level ``lead_agent_profiles``
key. Shape:

    lead_agent_profiles:
      business:
        enabled: true
      tenant_onboarding:
        enabled: true
        # Optional — pin a specific model for this profile, overrides the
        # request's model_name when not explicitly forced. Useful e.g. to
        # send onboarding to a cheaper deterministic model.
        model: deepseek-v4-flash
      template_extraction:
        enabled: true
      bootstrap_agent:
        enabled: false   # disable the legacy create-agent flow

Section is optional — if missing, every registered profile is treated as
enabled and no model overrides apply (== legacy pre-S4 behaviour).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProfileEntry:
    """One profile's config slice."""

    enabled: bool = True
    model: str | None = None


@dataclass(frozen=True)
class LeadAgentProfilesConfig:
    """Container for per-profile config. Maps task_type → ProfileEntry."""

    profiles: dict[str, ProfileEntry] = field(default_factory=dict)

    def get(self, task_type: str) -> ProfileEntry:
        """Look up a profile's config; default to ``enabled=True`` when unset."""
        return self.profiles.get(task_type, ProfileEntry())

    def is_enabled(self, task_type: str) -> bool:
        return self.get(task_type).enabled

    def model_override(self, task_type: str) -> str | None:
        return self.get(task_type).model


def load_lead_agent_profiles_config_from_dict(data: Any) -> LeadAgentProfilesConfig:
    """Parse a raw dict (or None) into ``LeadAgentProfilesConfig``.

    Tolerant: malformed entries are dropped with a warning rather than
    raising — a bad single-profile entry must not block agent boot.
    """
    if not isinstance(data, dict) or not data:
        return LeadAgentProfilesConfig(profiles={})

    profiles: dict[str, ProfileEntry] = {}
    for task_type, raw in data.items():
        if not isinstance(task_type, str) or not task_type:
            logger.warning("lead_agent_profiles: skipping non-string key %r", task_type)
            continue
        if not isinstance(raw, dict):
            logger.warning("lead_agent_profiles[%s]: expected mapping, got %s", task_type, type(raw).__name__)
            continue
        enabled = bool(raw.get("enabled", True))
        model_value = raw.get("model")
        model: str | None = str(model_value).strip() if isinstance(model_value, str) and model_value.strip() else None
        profiles[task_type] = ProfileEntry(enabled=enabled, model=model)
    return LeadAgentProfilesConfig(profiles=profiles)


_loaded: LeadAgentProfilesConfig | None = None


def get_lead_agent_profiles_config() -> LeadAgentProfilesConfig:
    """Return the lazily-loaded singleton (reads ``config.yaml`` once).

    On first call, pulls the section out of ``AppConfig`` (the global
    config dict already loaded for the rest of the app) and parses it.
    Returns an empty config if anything goes wrong — this section is
    optional and a parse failure must not crash agent assembly.
    """
    global _loaded
    if _loaded is not None:
        return _loaded
    try:
        from src.config.app_config import get_app_config

        # AppConfig has ``extra="allow"`` so the raw key shows up via
        # ``model_extra``. Fall back to model_dump() lookup if extras
        # ever get tightened up.
        app_config = get_app_config()
        raw = None
        extra = getattr(app_config, "model_extra", None)
        if isinstance(extra, dict):
            raw = extra.get("lead_agent_profiles")
        if raw is None:
            dumped = app_config.model_dump()
            raw = dumped.get("lead_agent_profiles")
        _loaded = load_lead_agent_profiles_config_from_dict(raw)
    except Exception as exc:  # noqa: BLE001 — section is optional
        logger.warning("Failed to load lead_agent_profiles config; defaulting to all-enabled: %s", exc)
        _loaded = LeadAgentProfilesConfig(profiles={})
    return _loaded


def reset_lead_agent_profiles_config_cache() -> None:
    """Reset the cached singleton (test helper)."""
    global _loaded
    _loaded = None
