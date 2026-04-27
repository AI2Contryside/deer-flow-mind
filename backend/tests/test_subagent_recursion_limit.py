"""Tests for subagent recursion-limit resolution.

Pinned after live-deploy session where the lead agent dispatched the
onboarding work to the ``bash`` subagent (because the ``task`` tool's
schema didn't list ``tenant-onboarding``) and then died with
``GRAPH_RECURSION_LIMIT: Recursion limit of 30 reached`` — bash's
max_turns=30 was being used directly as the LangGraph recursion budget,
even though one conversation turn fans out into multiple supersteps.

Three invariants this file pins:

1. ``resolve_recursion_limit`` returns the explicit field when set.
2. Otherwise it scales ``max_turns`` by ``DEFAULT_RECURSION_MULTIPLIER``
   so middlewares + tool calls don't exhaust the budget.
3. The ``tenant-onboarding`` builtin keeps a generous explicit cap so
   future tweaks to ``max_turns`` don't silently shrink it.
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
def test_tenant_onboarding_has_generous_explicit_cap() -> None:
    """Onboarding has the heaviest middleware fan-out of any subagent and
    has historically needed ~80 conversation turns. Pin the explicit cap
    so a future refactor that drops it back to ``max_turns`` doesn't
    silently re-introduce the recursion-limit bug."""
    from src.subagents.builtins.tenant_onboarding import TENANT_ONBOARDING_CONFIG

    assert TENANT_ONBOARDING_CONFIG.recursion_limit is not None
    assert TENANT_ONBOARDING_CONFIG.recursion_limit >= 400
    # And it's strictly larger than max_turns — otherwise we've regressed
    # to the pre-fix "1 turn = 1 superstep" assumption.
    assert TENANT_ONBOARDING_CONFIG.recursion_limit > TENANT_ONBOARDING_CONFIG.max_turns


@pytest.mark.unit
def test_task_tool_subagent_type_literal_includes_tenant_onboarding() -> None:
    """If ``tenant-onboarding`` falls out of the ``Literal`` on the
    ``task`` tool, the LLM can't dispatch to it — LangChain validates
    tool calls against the schema and the model silently re-routes the
    work to ``general-purpose`` or ``bash``. That regressed once
    already (the bash subagent's ``max_turns=30`` then triggered the
    GRAPH_RECURSION_LIMIT error).

    Reads the source file directly so this test stays runnable in test
    environments that lack the heavyweight runtime imports the full
    ``task_tool`` module pulls in (oss2, langgraph dev server, …).
    """
    from pathlib import Path

    src = Path(__file__).resolve().parent.parent / "src" / "tools" / "builtins" / "task_tool.py"
    text = src.read_text(encoding="utf-8")
    # Match the subagent_type Literal regardless of whitespace / line wrapping.
    needle_literal = "Literal["
    needle_value = '"tenant-onboarding"'
    assert needle_literal in text, "task_tool.py no longer declares a Literal for subagent_type"
    assert needle_value in text, (
        "tenant-onboarding missing from task_tool.subagent_type Literal — the LLM "
        "won't be able to dispatch to it and the lead agent will fall back to "
        "general-purpose / bash, breaking the onboarding recursion budget."
    )
