"""Tenant-onboarding profile.

Auto-selected by the registry when a tenant has no ``profile.json`` yet.
Slimmer than the business profile:

- system prompt: onboarding-only spec from ``onboarding_prompt.py``
- tools: bash + sandbox file ops + ask_clarification + present_file
  (no MCP, no community web tools, no subagent ``task`` tool)
- middlewares: lead-runtime base + Title + Memory + RepeatedToolFailure
  + Clarification (no Summarization / Todo / NextStep / Vision /
  SubagentLimit — onboarding doesn't need them)

``MemoryMiddleware`` is intentionally retained: per the S2 design call,
onboarding turns *should* feed memory.json so the business profile picks
up tenant preferences from the very first conversation.
"""

from __future__ import annotations

import logging

from langchain.agents.middleware import AgentMiddleware
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool

from src.agents.lead_agent.onboarding_prompt import apply_onboarding_prompt_template
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
from src.agents.middlewares.clarification_middleware import ClarificationMiddleware
from src.agents.middlewares.memory_middleware import MemoryMiddleware
from src.agents.middlewares.repeated_tool_failure_middleware import RepeatedToolFailureMiddleware
from src.agents.middlewares.title_middleware import TitleMiddleware
from src.agents.middlewares.tool_error_handling_middleware import build_lead_runtime_middlewares

logger = logging.getLogger(__name__)


def _resolve_model(ctx: ProfileContext) -> ModelSpec:
    """Onboarding ignores the per-tenant agent_config — the tenant is brand new.

    Precedence: explicit request → ``lead_agent_profiles.tenant_onboarding.model``
    config override → workspace default.
    """
    name = ctx.requested_model_name or profile_model_override("tenant_onboarding") or resolve_model_name()
    thinking = maybe_disable_thinking(name, ctx.thinking_enabled)
    return ModelSpec(name=name, thinking_enabled=thinking, reasoning_effort=ctx.reasoning_effort)


def _build_system_prompt(ctx: ProfileContext) -> str:
    if not ctx.tenant_id:
        # Defensive: registry shouldn't route here without a tenant_id,
        # but if it ever does, fail loudly rather than build a malformed
        # prompt that silently embeds an empty tenant id into write_profile
        # instructions.
        raise ValueError("tenant_onboarding profile requires a tenant_id in the run context")
    return apply_onboarding_prompt_template(tenant_id=ctx.tenant_id, tenant_name=ctx.tenant_name)


def _select_tools(ctx: ProfileContext, spec: ModelSpec) -> list[BaseTool]:
    """Manually composed lean tool list — no MCP, no community tools."""
    from src.sandbox.tools import (
        bash_tool,
        ls_tool,
        read_file_tool,
        str_replace_tool,
        write_file_tool,
    )
    from src.tools.builtins import ask_clarification_tool, present_file_tool

    return [
        bash_tool,
        ls_tool,
        read_file_tool,
        write_file_tool,
        str_replace_tool,
        ask_clarification_tool,
        present_file_tool,
    ]


def _select_middlewares(ctx: ProfileContext, config: RunnableConfig, spec: ModelSpec) -> list[AgentMiddleware]:
    """Reuse the lead-runtime base + a handful of essentials."""
    middlewares: list[AgentMiddleware] = list(build_lead_runtime_middlewares(lazy_init=True))
    middlewares.append(TitleMiddleware())
    middlewares.append(MemoryMiddleware(agent_name=ctx.agent_name))
    middlewares.append(RepeatedToolFailureMiddleware())
    middlewares.append(ClarificationMiddleware())
    return middlewares


def _dispatch_build_system_prompt(ctx: ProfileContext) -> str:
    return _build_system_prompt(ctx)


def _dispatch_select_tools(ctx: ProfileContext, spec: ModelSpec):
    return _select_tools(ctx, spec)


def _dispatch_select_middlewares(ctx: ProfileContext, config: RunnableConfig, spec: ModelSpec):
    return _select_middlewares(ctx, config, spec)


def _dispatch_resolve_model(ctx: ProfileContext) -> ModelSpec:
    return _resolve_model(ctx)


PROFILE = LeadAgentProfile(
    task_type="tenant_onboarding",
    build_system_prompt=_dispatch_build_system_prompt,
    select_tools=_dispatch_select_tools,
    select_middlewares=_dispatch_select_middlewares,
    resolve_model=_dispatch_resolve_model,
)


register_profile(PROFILE)
