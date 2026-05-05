"""Tests for subagent recursion-limit resolution.

Pinned after live-deploy session where the bash subagent died with
``GRAPH_RECURSION_LIMIT: Recursion limit of 30 reached`` — bash's
max_turns=30 was being used directly as the LangGraph recursion budget,
even though one conversation turn fans out into multiple supersteps.

(Historical note: the original repro routed onboarding work into the
bash subagent because the ``task`` schema didn't list ``tenant-onboarding``.
That subagent has since been deleted — onboarding now runs inline on the
lead agent — but the recursion-limit fix still applies to the remaining
subagents.)

Two invariants this file pins:

1. ``resolve_recursion_limit`` returns the explicit field when set.
2. Otherwise it scales ``max_turns`` by ``DEFAULT_RECURSION_MULTIPLIER``
   so middlewares + tool calls don't exhaust the budget.
"""

from __future__ import annotations

import pytest

from src.subagents.config import (
    DEFAULT_RECURSION_MULTIPLIER,
    SubagentConfig,
    resolve_recursion_limit,
)


@pytest.mark.unit
def test_explicit_recursion_limit_wins_over_max_turns_scaling() -> None:
    cfg = SubagentConfig(
        name="x",
        description="",
        system_prompt="",
        max_turns=10,
        recursion_limit=400,
    )

    assert resolve_recursion_limit(cfg) == 400


@pytest.mark.unit
def test_recursion_limit_falls_back_to_scaled_max_turns() -> None:
    cfg = SubagentConfig(
        name="x",
        description="",
        system_prompt="",
        max_turns=30,
    )

    assert resolve_recursion_limit(cfg) == 30 * DEFAULT_RECURSION_MULTIPLIER


@pytest.mark.unit
def test_recursion_limit_treats_none_and_nonpositive_as_unset() -> None:
    """Defensive: a config that accidentally sets ``recursion_limit=0`` or a
    negative value falls back to the multiplier rather than producing a
    LangGraph error at run time."""
    for invalid in (None, 0, -5):
        cfg = SubagentConfig(
            name="x",
            description="",
            system_prompt="",
            max_turns=20,
            recursion_limit=invalid,
        )
        assert resolve_recursion_limit(cfg) == 20 * DEFAULT_RECURSION_MULTIPLIER


@pytest.mark.unit
def test_task_tool_subagent_type_literal_excludes_tenant_onboarding() -> None:
    """``tenant-onboarding`` was removed as a subagent — the lead agent
    runs onboarding inline now. If it leaks back into the ``task`` tool's
    Literal the lead agent will see it as a delegation target again and
    we'll re-introduce the silent ``ask_clarification`` bug.

    Reads the source file directly so this test stays runnable in test
    environments that lack the heavyweight runtime imports the full
    ``task_tool`` module pulls in (oss2, langgraph dev server, …).
    """
    from pathlib import Path

    src = Path(__file__).resolve().parent.parent / "src" / "tools" / "builtins" / "task_tool.py"
    text = src.read_text(encoding="utf-8")
    assert "Literal[" in text, "task_tool.py no longer declares a Literal for subagent_type"
    assert '"tenant-onboarding"' not in text, "tenant-onboarding leaked back into task_tool.subagent_type Literal — onboarding is now inline on the lead agent and must NOT be a delegation target."
