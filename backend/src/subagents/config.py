"""Subagent configuration definitions."""

from dataclasses import dataclass, field


@dataclass
class SubagentConfig:
    """Configuration for a subagent.

    Attributes:
        name: Unique identifier for the subagent.
        description: When Claude should delegate to this subagent.
        system_prompt: The system prompt that guides the subagent's behavior.
        tools: Optional list of tool names to allow. If None, inherits all tools.
        disallowed_tools: Optional list of tool names to deny.
        model: Model to use - 'inherit' uses parent's model.
        max_turns: Maximum number of conversation turns (model calls) before
            stopping. Distinct from ``recursion_limit``: a single turn can
            produce multiple LangGraph supersteps (one per middleware +
            model + tool execution), so the LangGraph budget needs to be
            larger than the conversation budget.
        recursion_limit: Optional explicit cap on LangGraph supersteps. When
            ``None`` we derive it as ``max_turns *
            DEFAULT_RECURSION_MULTIPLIER`` so subagents with the standard
            middleware stack (sandbox, uploads, dangling-tool-call,
            clarification, …) don't exhaust the budget after a fraction
            of their conversation budget. Set this explicitly when a
            subagent runs an unusually heavy middleware chain or invokes
            many tool calls per turn.
        timeout_seconds: Maximum execution time in seconds (default: 900 = 15 minutes).
    """

    name: str
    description: str
    system_prompt: str
    tools: list[str] | None = None
    disallowed_tools: list[str] | None = field(default_factory=lambda: ["task"])
    model: str = "inherit"
    max_turns: int = 50
    recursion_limit: int | None = None
    timeout_seconds: int = 900


# Each conversation turn typically costs ~3-5 supersteps after the lead
# middleware chain (ThreadData, Uploads, Sandbox, DanglingToolCall,
# RepeatedToolFailure, Clarification — plus the model + tool nodes). 5 is
# conservative; the timeout still bounds wall-clock time so over-budgeting
# supersteps is cheap.
DEFAULT_RECURSION_MULTIPLIER: int = 5


def resolve_recursion_limit(config: SubagentConfig) -> int:
    """Return the LangGraph ``recursion_limit`` to use for this subagent.

    Prefer the explicit field on the config; otherwise multiply
    ``max_turns`` by ``DEFAULT_RECURSION_MULTIPLIER``. Pulled out into a
    helper so tests can pin the conversion and the executor stays
    declarative.
    """
    if config.recursion_limit is not None and config.recursion_limit > 0:
        return config.recursion_limit
    return max(config.max_turns, 1) * DEFAULT_RECURSION_MULTIPLIER
