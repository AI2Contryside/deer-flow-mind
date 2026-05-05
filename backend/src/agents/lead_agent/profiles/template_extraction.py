"""Template-extraction profile.

Selected explicitly via ``task_type=template_extraction`` from the
gateway when a tenant uploads a ``.docx`` / ``.xlsx`` template. Replaces
the deleted ``src.skills.template_filler.extractor.extract_fields`` fast
path so extraction can:

  - call ``scan_template_fields`` once per file (deterministic scanner)
  - call ``ask_clarification`` for genuinely ambiguous fields and have
    the gateway/FE surface them as native cards
  - emit ``ExtractedField[]`` JSON inside ``<extracted_fields>`` tags so
    the upstream parser is unambiguous

Lean by design — no MCP, no community web tools, no subagents, no
vision middleware, no next-step nudge, no memory injection. The profile
is single-shot per upload.
"""

from __future__ import annotations

import logging

from langchain.agents.middleware import AgentMiddleware
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool

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
from src.agents.lead_agent.template_extraction_prompt import (
    apply_template_extraction_prompt_template,
)
from src.agents.middlewares.clarification_middleware import ClarificationMiddleware
from src.agents.middlewares.repeated_tool_failure_middleware import RepeatedToolFailureMiddleware
from src.agents.middlewares.tool_error_handling_middleware import build_lead_runtime_middlewares

logger = logging.getLogger(__name__)


def _resolve_model(ctx: ProfileContext) -> ModelSpec:
    """Force thinking off — extraction is structured output, no monologue.

    The request can still pin a model via ``ctx.requested_model_name``
    (gateway uses this when the caller wants a specific extractor); the
    fall-back is the workspace default.
    """
    name = ctx.requested_model_name or profile_model_override("template_extraction") or resolve_model_name()
    # Extraction never benefits from a thinking trace — it'd just spend
    # tokens on "is this a label or a placeholder" deliberation that the
    # rules in the system prompt already cover.
    thinking = maybe_disable_thinking(name, False)
    return ModelSpec(name=name, thinking_enabled=thinking, reasoning_effort=None)


def _build_system_prompt(ctx: ProfileContext) -> str:
    return apply_template_extraction_prompt_template()


def _select_tools(ctx: ProfileContext, spec: ModelSpec) -> list[BaseTool]:
    """Three tools: scan + clarify + read_file.

    ``ask_clarification`` is in: when the agent finds a genuinely
    ambiguous span (same downscore between two field types, two
    candidate names for the same column header, etc.), it should
    interrupt the run and surface a card to the user rather than guess.
    The B2 gateway proxies the SSE stream to the FE, so the interrupt
    becomes a native field-review card.
    """
    from src.sandbox.tools import read_file_tool
    from src.tools.builtins import (
        ask_clarification_tool,
        scan_template_fields_tool,
    )

    return [
        scan_template_fields_tool,
        ask_clarification_tool,
        read_file_tool,
    ]


def _select_middlewares(ctx: ProfileContext, config: RunnableConfig, spec: ModelSpec) -> list[AgentMiddleware]:
    """Lean middleware chain — drop everything onboarding/business needs.

    Keeps ``ClarificationMiddleware`` so ``ask_clarification`` calls
    actually pause the run (the upstream depends on the SSE interrupt
    event to render a field-review card).
    """
    middlewares: list[AgentMiddleware] = list(build_lead_runtime_middlewares(lazy_init=True))
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
    task_type="template_extraction",
    build_system_prompt=_dispatch_build_system_prompt,
    select_tools=_dispatch_select_tools,
    select_middlewares=_dispatch_select_middlewares,
    resolve_model=_dispatch_resolve_model,
)


register_profile(PROFILE)
