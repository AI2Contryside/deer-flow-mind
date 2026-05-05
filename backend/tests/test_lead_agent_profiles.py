"""Tests for the lead-agent profile system (S1 of the task-type refactor).

Cover the registry/router behavior + ProfileContext assembly. Profile
hook implementations get exercised via existing integration tests; this
file pins the wiring.
"""

from __future__ import annotations

import pytest

from src.agents.lead_agent import agent as lead_agent_module
from src.agents.lead_agent.profiles import (
    LeadAgentProfile,
    ModelSpec,
    ProfileContext,
    bootstrap,
    business,
    get_profile,
    list_profiles,
    register_profile,
    resolve_profile,
)

# ---------- Registry ---------------------------------------------------------


def test_builtin_profiles_register_on_import():
    profiles = list_profiles()
    assert "business" in profiles
    assert "bootstrap_agent" in profiles


def test_get_profile_unknown_raises():
    with pytest.raises(KeyError, match="No lead-agent profile registered"):
        get_profile("nonexistent_task_type")


def test_register_profile_replaces_existing():
    fake = LeadAgentProfile(
        task_type="business",
        build_system_prompt=lambda ctx: "x",
        select_tools=lambda ctx, spec: [],
        select_middlewares=lambda ctx, config, spec: [],
        resolve_model=lambda ctx: ModelSpec(name="x"),
    )
    original = get_profile("business")
    try:
        register_profile(fake)
        assert get_profile("business") is fake
    finally:
        register_profile(original)


# ---------- Routing ----------------------------------------------------------


def test_resolve_profile_explicit_task_type_wins():
    ctx = ProfileContext(task_type="business")
    assert resolve_profile(ctx) is business.PROFILE


def test_resolve_profile_unknown_task_type_raises():
    ctx = ProfileContext(task_type="not_a_real_task")
    with pytest.raises(KeyError):
        resolve_profile(ctx)


def test_resolve_profile_picks_bootstrap_when_task_type_explicit():
    ctx = ProfileContext(task_type="bootstrap_agent")
    assert resolve_profile(ctx) is bootstrap.PROFILE


def test_resolve_profile_default_is_business():
    ctx = ProfileContext()
    assert resolve_profile(ctx) is business.PROFILE


# ---------- Context assembly -------------------------------------------------


def test_build_profile_context_reads_configurable():
    config = {
        "configurable": {
            "task_type": "business",
            "thinking_enabled": False,
            "model_name": "foo-model",
            "tenant_id": 42,  # arrives as int from JSON gateway
            "tenant_name": "  Acme Trading  ",
            "user_id": "u1",
            "thread_id": "t1",
            "subagent_enabled": True,
            "max_concurrent_subagents": 5,
            "agent_name": "TestAgent",
            "is_plan_mode": True,
            "user_email": "u1@example.com",
            "log_id": "log-1",
        }
    }
    ctx = lead_agent_module._build_profile_context_from_config(config)

    assert ctx.task_type == "business"
    assert ctx.thinking_enabled is False
    assert ctx.requested_model_name == "foo-model"
    assert ctx.tenant_id == "42"  # normalized to str
    assert ctx.tenant_name == "Acme Trading"  # stripped
    assert ctx.user_id == "u1"
    assert ctx.session_id == "t1"
    assert ctx.subagent_enabled is True
    assert ctx.max_concurrent_subagents == 5
    assert ctx.agent_name == "TestAgent"
    assert ctx.is_plan_mode is True
    assert ctx.user_email == "u1@example.com"
    assert ctx.log_id == "log-1"


def test_build_profile_context_handles_blank_tenant_id():
    config = {"configurable": {"tenant_id": ""}}
    ctx = lead_agent_module._build_profile_context_from_config(config)
    assert ctx.tenant_id is None


def test_build_profile_context_treats_zero_tenant_id_as_unset():
    config = {"configurable": {"tenant_id": 0}}
    ctx = lead_agent_module._build_profile_context_from_config(config)
    assert ctx.tenant_id is None


def test_build_profile_context_session_id_falls_back_to_session_id_key():
    config = {"configurable": {"session_id": "sess-1"}}
    ctx = lead_agent_module._build_profile_context_from_config(config)
    assert ctx.session_id == "sess-1"


def test_build_profile_context_legacy_model_key():
    """Old callers used 'model' instead of 'model_name'."""
    config = {"configurable": {"model": "legacy-model"}}
    ctx = lead_agent_module._build_profile_context_from_config(config)
    assert ctx.requested_model_name == "legacy-model"


def test_build_profile_context_translates_is_bootstrap_to_task_type():
    """S5 back-compat: legacy ``is_bootstrap=True`` callers must keep working
    by being mapped to the new explicit ``task_type=bootstrap_agent``."""
    config = {"configurable": {"is_bootstrap": True}}
    ctx = lead_agent_module._build_profile_context_from_config(config)
    assert ctx.task_type == "bootstrap_agent"


def test_build_profile_context_explicit_task_type_wins_over_is_bootstrap():
    """When both are set, the modern explicit selector wins."""
    config = {"configurable": {"is_bootstrap": True, "task_type": "business"}}
    ctx = lead_agent_module._build_profile_context_from_config(config)
    assert ctx.task_type == "business"
