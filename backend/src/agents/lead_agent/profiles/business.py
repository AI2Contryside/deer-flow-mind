"""Business profile — the main foreign-trade operations agent.

Lifts the previous ``make_lead_agent`` default branch into a profile.
S1 keeps behavior bit-for-bit; the onboarding ``<onboarding_required>``
splice currently still happens inside ``apply_prompt_template`` and
moves out in S2.
"""

from __future__ import annotations

import logging

from langchain.agents.middleware import AgentMiddleware
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool

from src.agents.lead_agent.profiles import _common
from src.agents.lead_agent.profiles._common import (
    maybe_disable_thinking,
    model_supports_vision,
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
from src.agents.middlewares.clarification_middleware import ClarificationMiddleware
from src.agents.middlewares.memory_middleware import MemoryMiddleware
from src.agents.middlewares.next_step_state_middleware import NextStepStateMiddleware
from src.agents.middlewares.repeated_tool_failure_middleware import RepeatedToolFailureMiddleware
from src.agents.middlewares.subagent_limit_middleware import SubagentLimitMiddleware
from src.agents.middlewares.title_middleware import TitleMiddleware
from src.agents.middlewares.tool_error_handling_middleware import build_lead_runtime_middlewares
from src.agents.middlewares.view_image_middleware import ViewImageMiddleware
from src.config.agents_config import load_agent_config
from src.config.next_step_config import get_next_step_config

logger = logging.getLogger(__name__)


def _resolve_model(ctx: ProfileContext) -> ModelSpec:
    """Pick the effective model.

    Precedence (highest to lowest):
      1. ``ctx.requested_model_name`` (explicit request from the run)
      2. Per-profile override from ``lead_agent_profiles.business.model``
      3. ``agent_config.model`` (the per-tenant custom-agent yaml)
      4. Workspace default (``models[0].name``)

    Drops thinking when the resolved model doesn't support it.
    """
    agent_config = load_agent_config(ctx.agent_name)
    agent_model_name = agent_config.model if agent_config and agent_config.model else resolve_model_name()

    name = ctx.requested_model_name or profile_model_override("business") or agent_model_name
    thinking = maybe_disable_thinking(name, ctx.thinking_enabled)
    return ModelSpec(name=name, thinking_enabled=thinking, reasoning_effort=ctx.reasoning_effort)


def _build_system_prompt(ctx: ProfileContext) -> str:
    return apply_prompt_template(
        subagent_enabled=ctx.subagent_enabled,
        max_concurrent_subagents=ctx.max_concurrent_subagents,
        agent_name=ctx.agent_name,
        tenant_id=ctx.tenant_id,
        tenant_name=ctx.tenant_name,
        user_email=ctx.user_email,
    )


def _select_tools(ctx: ProfileContext, spec: ModelSpec) -> list[BaseTool]:
    # Lazy import: src.tools imports from src.agents indirectly via builtins.
    from src.tools import get_available_tools

    agent_config = load_agent_config(ctx.agent_name)
    return get_available_tools(
        model_name=spec.name,
        groups=agent_config.tool_groups if agent_config else None,
        subagent_enabled=ctx.subagent_enabled,
    )


def _select_middlewares(ctx: ProfileContext, config: RunnableConfig, spec: ModelSpec) -> list[AgentMiddleware]:
    """Reproduce the original lead-agent middleware chain order.

    Order matters — see ``lead_agent.agent`` legacy comments. Each branch
    is gated identically to the pre-refactor implementation so behavior
    diff is zero.
    """
    middlewares: list[AgentMiddleware] = list(build_lead_runtime_middlewares(lazy_init=True))

    # Look up summarization / todo helpers via module reference so tests
    # that patch ``profiles._common.create_summarization_middleware`` see
    # the override at call time.
    summarization_middleware = _common.create_summarization_middleware()
    if summarization_middleware is not None:
        middlewares.append(summarization_middleware)

    todo_middleware = _common.create_todo_list_middleware(ctx.is_plan_mode)
    if todo_middleware is not None:
        middlewares.append(todo_middleware)

    middlewares.append(TitleMiddleware())
    middlewares.append(MemoryMiddleware(agent_name=ctx.agent_name))

    if get_next_step_config().enabled:
        middlewares.append(NextStepStateMiddleware())

    if model_supports_vision(spec.name):
        middlewares.append(ViewImageMiddleware())

    if ctx.subagent_enabled:
        middlewares.append(SubagentLimitMiddleware(max_concurrent=ctx.max_concurrent_subagents))

    middlewares.append(RepeatedToolFailureMiddleware())
    middlewares.append(ClarificationMiddleware())

    return middlewares


# Profile hooks dispatched via module-level lookup so tests that
# ``monkeypatch.setattr(business, "_select_tools", ...)`` affect the
# already-registered ``PROFILE`` without re-registering.
def _dispatch_build_system_prompt(ctx: ProfileContext) -> str:
    return _build_system_prompt(ctx)


def _dispatch_select_tools(ctx: ProfileContext, spec: ModelSpec):
    return _select_tools(ctx, spec)


def _dispatch_select_middlewares(ctx: ProfileContext, config: RunnableConfig, spec: ModelSpec):
    return _select_middlewares(ctx, config, spec)


def _dispatch_resolve_model(ctx: ProfileContext) -> ModelSpec:
    return _resolve_model(ctx)


PROFILE = LeadAgentProfile(
    task_type="business",
    build_system_prompt=_dispatch_build_system_prompt,
    select_tools=_dispatch_select_tools,
    select_middlewares=_dispatch_select_middlewares,
    resolve_model=_dispatch_resolve_model,
)


register_profile(PROFILE)
