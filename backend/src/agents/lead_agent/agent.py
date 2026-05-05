"""Lead-agent factory.

Thin facade over the ``profiles`` package. Per-run inputs are normalized
into a ``ProfileContext``; the registry picks a ``LeadAgentProfile`` and
the profile owns the (system-prompt + tools + middlewares + model) bundle.

Module-level helper names (``_resolve_model_name``, ``_build_middlewares``
…) are preserved as aliases so existing tests that monkey-patch them on
this module keep working. New code should reach into
``src.agents.lead_agent.profiles`` directly instead.
"""

from __future__ import annotations

import logging
import uuid

from langchain.agents import create_agent
from langchain_core.runnables import RunnableConfig

# Re-exports kept for test/monkey-patch compatibility.
from src.agents.lead_agent.profiles import (
    LeadAgentProfile,
    ModelSpec,
    ProfileContext,
    resolve_profile,
)
from src.agents.lead_agent.profiles import _common as _profile_common
from src.agents.lead_agent.profiles import business as _business_profile
from src.agents.middlewares.view_image_middleware import ViewImageMiddleware  # noqa: F401 — re-export
from src.config.app_config import get_app_config  # noqa: F401 — re-export
from src.models import create_chat_model  # noqa: F401 — re-export

logger = logging.getLogger(__name__)


# ---- Backward-compat helper aliases -----------------------------------------
# These wrap the new ``profiles._common`` and ``profiles.business`` helpers so
# code that imports ``from src.agents.lead_agent.agent import ...`` (and tests
# that monkey-patch attributes on this module) keeps working unchanged.


def _resolve_model_name(requested_model_name: str | None = None) -> str:
    """Legacy alias for ``profiles._common.resolve_model_name``."""
    return _profile_common.resolve_model_name(requested_model_name)


def _create_summarization_middleware():
    """Legacy alias for ``profiles._common.create_summarization_middleware``."""
    return _profile_common.create_summarization_middleware()


def _create_todo_list_middleware(is_plan_mode: bool):
    """Legacy alias for ``profiles._common.create_todo_list_middleware``."""
    return _profile_common.create_todo_list_middleware(is_plan_mode)


def _build_middlewares(config: RunnableConfig, model_name: str | None, agent_name: str | None = None):
    """Legacy alias for the business profile's middleware chain.

    Kept as a single-line shim so the legacy signature
    ``(config, model_name, agent_name=None)`` continues to work for tests
    and any out-of-tree caller. New code should call
    ``profile.select_middlewares(ctx, config, spec)`` directly.
    """
    ctx = _build_profile_context_from_config(config)
    if agent_name is not None:
        ctx = _replace(ctx, agent_name=agent_name)
    spec = ModelSpec(
        name=model_name or "",
        thinking_enabled=ctx.thinking_enabled,
        reasoning_effort=ctx.reasoning_effort,
    )
    return list(_business_profile._select_middlewares(ctx, config, spec))


# ---- Profile context assembly -----------------------------------------------


def _runtime_context() -> dict:
    """Per-run context dict, or empty when called outside a run.

    LangGraph 0.6+ moved per-request keys (tenant_id, subagent_enabled,
    thinking_enabled, …) from ``config.configurable`` to
    ``runtime.context``. The two are mutually exclusive on the wire — the
    server returns 400 if both are set. ``make_lead_agent`` is called both
    at compile time (LangGraph dev startup, no runtime) and per run, so
    this helper swallows the "no runtime" case.
    """
    try:
        from langgraph.runtime import get_runtime

        runtime = get_runtime()
    except Exception:
        return {}
    ctx = getattr(runtime, "context", None)
    if isinstance(ctx, dict):
        return ctx
    return {}


def _replace(ctx: ProfileContext, **changes) -> ProfileContext:
    """Return a copy of ``ctx`` with ``changes`` applied (immutable update)."""
    from dataclasses import replace as _dc_replace

    return _dc_replace(ctx, **changes)


def _build_profile_context_from_config(config: RunnableConfig) -> ProfileContext:
    """Translate a LangGraph ``RunnableConfig`` into a ``ProfileContext``.

    Merges ``runtime.context`` (preferred, LangGraph 0.6+) over
    ``config.configurable`` so the live request wins on conflicts.

    S5 back-compat: the legacy ``is_bootstrap=True`` flag is translated
    here to ``task_type="bootstrap_agent"`` so existing call sites
    (LangGraph dev startup payloads, embedded client invocations) keep
    working without code changes. New code should pass ``task_type``
    directly.
    """
    ctx_dict = _runtime_context()
    cfg = {**(config.get("configurable") or {}), **ctx_dict}

    raw_tenant_id = cfg.get("tenant_id")
    tenant_id: str | None = str(raw_tenant_id) if raw_tenant_id not in (None, "", 0) else None

    raw_tenant_name = cfg.get("tenant_name")
    tenant_name: str | None = str(raw_tenant_name).strip() if raw_tenant_name not in (None, "") else None

    task_type = cfg.get("task_type")
    if not task_type and bool(cfg.get("is_bootstrap", False)):
        task_type = "bootstrap_agent"

    return ProfileContext(
        task_type=task_type,
        thinking_enabled=cfg.get("thinking_enabled", True),
        reasoning_effort=cfg.get("reasoning_effort"),
        requested_model_name=cfg.get("model_name") or cfg.get("model"),
        is_plan_mode=cfg.get("is_plan_mode", False),
        subagent_enabled=cfg.get("subagent_enabled", False),
        max_concurrent_subagents=cfg.get("max_concurrent_subagents", 3),
        agent_name=cfg.get("agent_name"),
        tenant_id=tenant_id,
        tenant_name=tenant_name,
        user_id=str(cfg.get("user_id") or ""),
        user_email=cfg.get("user_email"),
        log_id=str(cfg.get("log_id") or ""),
        session_id=str(cfg.get("thread_id") or cfg.get("session_id") or ""),
    )


# ---- Identity / telemetry side effects --------------------------------------


def _bind_logctx(ctx: ProfileContext) -> None:
    """Mirror the per-request identity bundle into logctx ContextVars.

    Every middleware / tool / skill log line, plus outbound HTTP calls,
    will then carry tenant_id / user_id / log_id / session_id. LangGraph's
    FastAPI-side IdentityMiddleware doesn't run for in-process runs, so
    this is the canonical binding point on the LangGraph side.
    """
    from src.logctx import bind_fields, ensure_log_id
    from src.logctx.context import Fields as _LogCtxFields

    bind_fields(
        _LogCtxFields(
            tenant_id=ctx.tenant_id or "",
            user_id=ctx.user_id,
            log_id=ctx.log_id,
            session_id=ctx.session_id,
        )
    )
    ensure_log_id()


def _inject_run_metadata(config: RunnableConfig, ctx: ProfileContext, profile: LeadAgentProfile, spec: ModelSpec) -> None:
    """Stamp run metadata for LangSmith + bind a (session_id, turn_id) pair.

    ``turn_id`` correlates every LLM call (lead agent + middlewares +
    subagents) belonging to a single user message. Generated fresh per
    ``make_lead_agent`` call; propagated to subagents via ``parent_context``
    in ``task_tool``. Both flow into the ``TokenUsageRecorder`` callback.
    """
    if "metadata" not in config:
        config["metadata"] = {}

    turn_id = config["metadata"].get("turn_id") or uuid.uuid4().hex

    config["metadata"].update(
        {
            "agent_name": ctx.agent_name or "default",
            "model_name": spec.name or "default",
            "thinking_enabled": spec.thinking_enabled,
            "reasoning_effort": spec.reasoning_effort,
            "is_plan_mode": ctx.is_plan_mode,
            "subagent_enabled": ctx.subagent_enabled,
            "session_id": ctx.session_id,
            "turn_id": turn_id,
            "task_type": profile.task_type,
        }
    )

    if ctx.session_id and turn_id:
        from src.storage.token_usage import set_run_metadata

        set_run_metadata(session_id=ctx.session_id, turn_id=turn_id)


# ---- Entry point ------------------------------------------------------------


def make_lead_agent(config: RunnableConfig):
    """Assemble a lead agent for the current run.

    Resolves a ``LeadAgentProfile`` from the runtime context and delegates
    (system_prompt + tools + middlewares + model) selection to it.
    """
    ctx = _build_profile_context_from_config(config)
    _bind_logctx(ctx)

    profile = resolve_profile(ctx)
    spec = profile.resolve_model(ctx)

    if not spec.name:
        raise ValueError("No chat model could be resolved. Please configure at least one model in config.yaml or provide a valid 'model_name'/'model' in the request.")

    logger.info(
        "Create Agent(profile=%s, agent=%s) -> thinking_enabled: %s, reasoning_effort: %s, model_name: %s, is_plan_mode: %s, subagent_enabled: %s, max_concurrent_subagents: %s",
        profile.task_type,
        ctx.agent_name or "default",
        spec.thinking_enabled,
        spec.reasoning_effort,
        spec.name,
        ctx.is_plan_mode,
        ctx.subagent_enabled,
        ctx.max_concurrent_subagents,
    )

    _inject_run_metadata(config, ctx, profile, spec)

    return create_agent(
        model=create_chat_model(name=spec.name, thinking_enabled=spec.thinking_enabled, reasoning_effort=spec.reasoning_effort),
        tools=list(profile.select_tools(ctx, spec)),
        middleware=list(profile.select_middlewares(ctx, config, spec)),
        system_prompt=profile.build_system_prompt(ctx),
        state_schema=profile.state_schema,
    )
