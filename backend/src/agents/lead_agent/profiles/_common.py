"""Shared helpers used by multiple lead-agent profiles.

These were ``_resolve_model_name`` / ``_create_summarization_middleware``
/ ``_create_todo_list_middleware`` private to ``lead_agent.agent``. Lifted
here so business / bootstrap / tenant_onboarding / template_extraction
profiles can share them without circular imports back into the facade.
"""

from __future__ import annotations

import logging

from langchain.agents.middleware import SummarizationMiddleware

from src.agents.middlewares.safe_summarization_middleware import SafeSummarizationMiddleware
from src.agents.middlewares.todo_middleware import TodoMiddleware
from src.config.app_config import get_app_config
from src.config.summarization_config import get_summarization_config
from src.models import create_chat_model

logger = logging.getLogger(__name__)


def resolve_model_name(requested_model_name: str | None = None) -> str:
    """Pick a runtime model name, falling back to the configured default.

    Raises ``ValueError`` when no models are configured. Logs a warning
    when an explicit request couldn't be honored. Behavior preserved from
    the original ``lead_agent.agent._resolve_model_name``.
    """
    app_config = get_app_config()
    default_model_name = app_config.models[0].name if app_config.models else None
    if default_model_name is None:
        raise ValueError("No chat models are configured. Please configure at least one model in config.yaml.")

    if requested_model_name and app_config.get_model_config(requested_model_name):
        return requested_model_name

    if requested_model_name and requested_model_name != default_model_name:
        logger.warning(f"Model '{requested_model_name}' not found in config; fallback to default model '{default_model_name}'.")
    return default_model_name


def create_summarization_middleware() -> SummarizationMiddleware | None:
    """Build the configured summarization middleware, or None if disabled."""
    config = get_summarization_config()

    if not config.enabled:
        return None

    trigger = None
    if config.trigger is not None:
        if isinstance(config.trigger, list):
            trigger = [t.to_tuple() for t in config.trigger]
        else:
            trigger = config.trigger.to_tuple()

    keep = config.keep.to_tuple()

    if config.model_name:
        model = config.model_name
    else:
        model = create_chat_model(thinking_enabled=False)

    kwargs: dict = {
        "model": model,
        "trigger": trigger,
        "keep": keep,
    }

    if config.trim_tokens_to_summarize is not None:
        kwargs["trim_tokens_to_summarize"] = config.trim_tokens_to_summarize

    if config.summary_prompt is not None:
        kwargs["summary_prompt"] = config.summary_prompt

    return SafeSummarizationMiddleware(**kwargs)


_TODO_SYSTEM_PROMPT = """
<todo_list_system>
You have access to the `write_todos` tool to help you manage and track complex multi-step objectives.

**CRITICAL RULES:**
- Mark todos as completed IMMEDIATELY after finishing each step - do NOT batch completions
- Keep EXACTLY ONE task as `in_progress` at any time (unless tasks can run in parallel)
- Update the todo list in REAL-TIME as you work - this gives users visibility into your progress
- DO NOT use this tool for simple tasks (< 3 steps) - just complete them directly

**When to Use:**
This tool is designed for complex objectives that require systematic tracking:
- Complex multi-step tasks requiring 3+ distinct steps
- Non-trivial tasks needing careful planning and execution
- User explicitly requests a todo list
- User provides multiple tasks (numbered or comma-separated list)
- The plan may need revisions based on intermediate results

**When NOT to Use:**
- Single, straightforward tasks
- Trivial tasks (< 3 steps)
- Purely conversational or informational requests
- Simple tool calls where the approach is obvious

**Best Practices:**
- Break down complex tasks into smaller, actionable steps
- Use clear, descriptive task names
- Remove tasks that become irrelevant
- Add new tasks discovered during implementation
- Don't be afraid to revise the todo list as you learn more

**Task Management:**
Writing todos takes time and tokens - use it when helpful for managing complex problems, not for simple requests.
</todo_list_system>
"""


_TODO_TOOL_DESCRIPTION = """Use this tool to create and manage a structured task list for complex work sessions.

**IMPORTANT: Only use this tool for complex tasks (3+ steps). For simple requests, just do the work directly.**

## When to Use

Use this tool in these scenarios:
1. **Complex multi-step tasks**: When a task requires 3 or more distinct steps or actions
2. **Non-trivial tasks**: Tasks requiring careful planning or multiple operations
3. **User explicitly requests todo list**: When the user directly asks you to track tasks
4. **Multiple tasks**: When users provide a list of things to be done
5. **Dynamic planning**: When the plan may need updates based on intermediate results

## When NOT to Use

Skip this tool when:
1. The task is straightforward and takes less than 3 steps
2. The task is trivial and tracking provides no benefit
3. The task is purely conversational or informational
4. It's clear what needs to be done and you can just do it

## How to Use

1. **Starting a task**: Mark it as `in_progress` BEFORE beginning work
2. **Completing a task**: Mark it as `completed` IMMEDIATELY after finishing
3. **Updating the list**: Add new tasks, remove irrelevant ones, or update descriptions as needed
4. **Multiple updates**: You can make several updates at once (e.g., complete one task and start the next)

## Task States

- `pending`: Task not yet started
- `in_progress`: Currently working on (can have multiple if tasks run in parallel)
- `completed`: Task finished successfully

## Task Completion Requirements

**CRITICAL: Only mark a task as completed when you have FULLY accomplished it.**

Never mark a task as completed if:
- There are unresolved issues or errors
- Work is partial or incomplete
- You encountered blockers preventing completion
- You couldn't find necessary resources or dependencies
- Quality standards haven't been met

If blocked, keep the task as `in_progress` and create a new task describing what needs to be resolved.

## Best Practices

- Create specific, actionable items
- Break complex tasks into smaller, manageable steps
- Use clear, descriptive task names
- Update task status in real-time as you work
- Mark tasks complete IMMEDIATELY after finishing (don't batch completions)
- Remove tasks that are no longer relevant
- **IMPORTANT**: When you write the todo list, mark your first task(s) as `in_progress` immediately
- **IMPORTANT**: Unless all tasks are completed, always have at least one task `in_progress` to show progress

Being proactive with task management demonstrates thoroughness and ensures all requirements are completed successfully.

**Remember**: If you only need a few tool calls to complete a task and it's clear what to do, it's better to just do the task directly and NOT use this tool at all.
"""


def create_todo_list_middleware(is_plan_mode: bool) -> TodoMiddleware | None:
    """Build TodoMiddleware iff plan mode is enabled."""
    if not is_plan_mode:
        return None
    return TodoMiddleware(system_prompt=_TODO_SYSTEM_PROMPT, tool_description=_TODO_TOOL_DESCRIPTION)


def maybe_disable_thinking(model_name: str | None, thinking_enabled: bool) -> bool:
    """Return effective thinking flag, downgrading to False on incompatible models."""
    if not thinking_enabled or model_name is None:
        return thinking_enabled
    app_config = get_app_config()
    model_config = app_config.get_model_config(model_name)
    if model_config is None:
        return thinking_enabled
    if not model_config.supports_thinking:
        logger.warning(f"Thinking mode is enabled but model '{model_name}' does not support it; fallback to non-thinking mode.")
        return False
    return thinking_enabled


def profile_model_override(task_type: str) -> str | None:
    """Look up the optional ``models[task_type]`` override from config.yaml.

    Returns ``None`` when the section is absent or the profile has no
    explicit override. Best-effort — any failure is logged and treated as
    "no override" so model resolution stays robust.
    """
    try:
        from src.config.lead_agent_profiles_config import get_lead_agent_profiles_config

        return get_lead_agent_profiles_config().model_override(task_type)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to read profile model override for %s: %s", task_type, exc)
        return None


def model_supports_vision(model_name: str | None) -> bool:
    """Whether the resolved model is vision-capable."""
    if model_name is None:
        return False
    app_config = get_app_config()
    model_config = app_config.get_model_config(model_name)
    return bool(model_config and model_config.supports_vision)
