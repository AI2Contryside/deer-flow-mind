"""Type definitions for lead-agent profile system.

A ``LeadAgentProfile`` is a (system-prompt + tool-selector +
middleware-selector + model-resolver) bundle keyed by a ``task_type``.
The lead-agent factory resolves a profile from the per-run context and
delegates assembly to it, so the three task types — main business
workflow, tenant onboarding, template placeholder extraction — can pick
their own prompt, tool surface, middleware chain, and model independently
instead of branching inside one factory.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from langchain.agents.middleware import AgentMiddleware
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool

from src.agents.thread_state import ThreadState


@dataclass(frozen=True)
class ProfileContext:
    """Per-run inputs the profile system reads to decide how to assemble.

    Built once per ``make_lead_agent`` call from
    ``config.configurable`` + ``runtime.context``. Immutable so profiles
    can be assumed pure functions of context.

    ``task_type`` is the explicit selector. When None, the registry
    falls back to a single heuristic — auto-routing a tenant with a
    missing ``profile.json`` to the onboarding profile — and otherwise
    uses the business profile.

    The S5 cleanup removed the legacy ``is_bootstrap`` boolean: callers
    now pass ``task_type="bootstrap_agent"`` directly. The agent factory
    still accepts ``is_bootstrap=True`` in the run configurable for one
    release and translates it to the explicit task_type for back-compat.
    """

    task_type: str | None = None
    thinking_enabled: bool = True
    reasoning_effort: str | None = None
    requested_model_name: str | None = None
    is_plan_mode: bool = False
    subagent_enabled: bool = False
    max_concurrent_subagents: int = 3
    agent_name: str | None = None
    tenant_id: str | None = None
    tenant_name: str | None = None
    user_id: str = ""
    user_email: str | None = None
    log_id: str = ""
    session_id: str = ""


@dataclass(frozen=True)
class ModelSpec:
    """Model selection result returned by ``profile.resolve_model``.

    Decoupled from the raw model name so a profile can override
    thinking / reasoning_effort independently — e.g. template_extraction
    might force thinking off for a fast lightweight pass even when the
    request asks for it.
    """

    name: str
    thinking_enabled: bool = True
    reasoning_effort: str | None = None


# Type aliases for the four pluggable hooks. Profiles define small
# free functions matching these signatures and bundle them in
# ``LeadAgentProfile``; this avoids subclassing and keeps each profile a
# flat module.
BuildSystemPrompt = Callable[[ProfileContext], str]
SelectTools = Callable[[ProfileContext, ModelSpec], Sequence[BaseTool]]
SelectMiddlewares = Callable[[ProfileContext, RunnableConfig, ModelSpec], Sequence[AgentMiddleware]]
ResolveModel = Callable[[ProfileContext], ModelSpec]


@dataclass(frozen=True)
class LeadAgentProfile:
    """A runnable profile of the lead agent for one task type."""

    task_type: str
    build_system_prompt: BuildSystemPrompt
    select_tools: SelectTools
    select_middlewares: SelectMiddlewares
    resolve_model: ResolveModel
    state_schema: type = ThreadState
