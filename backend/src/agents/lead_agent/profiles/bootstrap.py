"""Bootstrap profile — minimal-tools agent for the create-custom-agent flow.

Replaces the ``is_bootstrap=True`` branch of the legacy factory. Same
prompt template as business but with ``available_skills={"bootstrap"}``
and the ``setup_agent`` builtin tool appended.
"""

from __future__ import annotations

from langchain.agents.middleware import AgentMiddleware
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool

from src.agents.lead_agent.profiles import business
from src.agents.lead_agent.profiles._common import (
    maybe_disable_thinking,
    profile_model_override,
    resolve_model_name,
)
from src.agents.lead_agent.profiles.registry import register_profile
from src.agents.lead_agent.profiles.types import (
    LeadAgentProfile,
    ModelSpec,
    ProfileContext,
)
from src.agents.lead_agent.prompt import apply_prompt_template


def _resolve_model(ctx: ProfileContext) -> ModelSpec:
    """Bootstrap flow ignores the per-tenant agent config (no agent yet)."""
    name = ctx.requested_model_name or profile_model_override("bootstrap_agent") or resolve_model_name()
    thinking = maybe_disable_thinking(name, ctx.thinking_enabled)
    return ModelSpec(name=name, thinking_enabled=thinking, reasoning_effort=ctx.reasoning_effort)


def _build_system_prompt(ctx: ProfileContext) -> str:
    return apply_prompt_template(
        subagent_enabled=ctx.subagent_enabled,
        max_concurrent_subagents=ctx.max_concurrent_subagents,
        available_skills={"bootstrap"},
        tenant_id=ctx.tenant_id,
        tenant_name=ctx.tenant_name,
    )


def _select_tools(ctx: ProfileContext, spec: ModelSpec) -> list[BaseTool]:
    from src.tools import get_available_tools
    from src.tools.builtins import setup_agent

    base = get_available_tools(model_name=spec.name, subagent_enabled=ctx.subagent_enabled)
    return list(base) + [setup_agent]


def _select_middlewares(ctx: ProfileContext, config: RunnableConfig, spec: ModelSpec) -> list[AgentMiddleware]:
    # Reuse business middleware chain — bootstrap shares the same runtime
    # ergonomics (clarification, memory, sandbox, etc.).
    return business._select_middlewares(ctx, config, spec)


def _dispatch_build_system_prompt(ctx: ProfileContext) -> str:
    return _build_system_prompt(ctx)


def _dispatch_select_tools(ctx: ProfileContext, spec: ModelSpec):
    return _select_tools(ctx, spec)


def _dispatch_select_middlewares(ctx: ProfileContext, config: RunnableConfig, spec: ModelSpec):
    return _select_middlewares(ctx, config, spec)


def _dispatch_resolve_model(ctx: ProfileContext) -> ModelSpec:
    return _resolve_model(ctx)


PROFILE = LeadAgentProfile(
    task_type="bootstrap_agent",
    build_system_prompt=_dispatch_build_system_prompt,
    select_tools=_dispatch_select_tools,
    select_middlewares=_dispatch_select_middlewares,
    resolve_model=_dispatch_resolve_model,
)


register_profile(PROFILE)
