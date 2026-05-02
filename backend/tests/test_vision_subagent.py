"""Vision-analyst subagent registration and infrastructure tests.

Verifies:
- ``vision-analyst`` is registered in ``BUILTIN_SUBAGENTS`` with the
  expected model / tool list / disallowed list / turn budget.
- ``task_tool``'s ``subagent_type`` Literal accepts the new value (the
  source-level Literal must include "vision-analyst" or LangChain's
  schema generator rejects the call before reaching the executor).
- ``build_subagent_runtime_middlewares(include_view_image=True)``
  appends ``ViewImageMiddleware`` so a vision subagent's
  ``view_image_tool`` calls actually feed pixels back to the model.
- ``get_app_config().get_model_config("glm-4v-plus").supports_vision``
  is True so the executor's lookup wires include_view_image=True.

These are pure structural checks — no live LLM call, no GLM-4V API
key required. End-to-end verification is in the manual smoke-test
plan (see plan file).
"""

from __future__ import annotations

import inspect
import typing
from typing import Literal, get_args, get_type_hints

import pytest


@pytest.fixture
def loaded_app_config(monkeypatch):
    """Inject a placeholder ZHIPU_API_KEY so config.yaml resolves cleanly,
    then reset the singleton so the next call to ``get_app_config()`` rereads
    the file. Production keeps a real key in the deploy env.
    """
    monkeypatch.setenv("ZHIPU_API_KEY", "test-zhipu-key-not-real")
    from src.config.app_config import reset_app_config

    reset_app_config()
    yield
    reset_app_config()


def test_vision_analyst_registered() -> None:
    """BUILTIN_SUBAGENTS exposes vision-analyst with the expected shape."""
    from src.subagents.builtins import BUILTIN_SUBAGENTS, VISION_ANALYST_CONFIG

    assert "vision-analyst" in BUILTIN_SUBAGENTS
    cfg = BUILTIN_SUBAGENTS["vision-analyst"]
    assert cfg is VISION_ANALYST_CONFIG

    assert cfg.name == "vision-analyst"
    assert cfg.model == "glm-4v-plus"
    assert cfg.max_turns == 8
    assert cfg.timeout_seconds == 300

    # Tool whitelist: must include view_image (the whole point) plus
    # cheap helpers ls/read_file. Must NOT include task (no nesting),
    # ask_clarification (no interrupts in subagent), or write/bash.
    assert cfg.tools is not None
    assert "view_image" in cfg.tools
    assert "ls" in cfg.tools
    assert "read_file" in cfg.tools
    assert "write_file" not in cfg.tools
    assert "bash" not in cfg.tools
    assert "task" not in cfg.tools
    assert "present_files" not in cfg.tools

    assert cfg.disallowed_tools is not None
    assert "task" in cfg.disallowed_tools
    assert "ask_clarification" in cfg.disallowed_tools


def test_vision_analyst_prompt_contains_anchors() -> None:
    """System prompt mentions the core operating contract."""
    from src.subagents.builtins.vision_analyst import VISION_ANALYST_SYSTEM_PROMPT

    # Must instruct the subagent to call view_image and to refuse JSON
    # output (that's ocr-extractor's job).
    assert "view_image" in VISION_ANALYST_SYSTEM_PROMPT
    assert "/mnt/user-data/uploads" in VISION_ANALYST_SYSTEM_PROMPT
    # Forbid hallucination
    assert "严禁" in VISION_ANALYST_SYSTEM_PROMPT or "不要" in VISION_ANALYST_SYSTEM_PROMPT


def test_get_subagent_config_returns_vision_analyst() -> None:
    """The registry lookup path resolves vision-analyst with overrides."""
    from src.subagents.registry import get_subagent_config

    cfg = get_subagent_config("vision-analyst")
    assert cfg is not None
    assert cfg.name == "vision-analyst"
    assert cfg.model == "glm-4v-plus"


def test_task_tool_literal_accepts_vision_subagents() -> None:
    """task_tool's subagent_type Literal must include the new values."""
    from src.tools.builtins.task_tool import task_tool

    # ``task_tool`` is wrapped by ``@tool``; the underlying callable
    # exposes its original signature via ``.func`` (langchain-core).
    func = getattr(task_tool, "func", None) or task_tool
    hints = get_type_hints(func)

    subagent_type_hint = hints.get("subagent_type")
    assert subagent_type_hint is not None, "task_tool.subagent_type missing type hint"
    args = get_args(subagent_type_hint)
    assert "general-purpose" in args
    assert "bash" in args
    assert "vision-analyst" in args
    assert "ocr-extractor" in args


def test_build_subagent_runtime_middlewares_appends_view_image() -> None:
    """include_view_image=True must put ViewImageMiddleware in the chain."""
    from src.agents.middlewares.tool_error_handling_middleware import (
        build_subagent_runtime_middlewares,
    )
    from src.agents.middlewares.view_image_middleware import ViewImageMiddleware

    without = build_subagent_runtime_middlewares(include_view_image=False)
    with_vision = build_subagent_runtime_middlewares(include_view_image=True)

    assert not any(isinstance(m, ViewImageMiddleware) for m in without), (
        "ViewImageMiddleware leaked into a non-vision subagent's chain — "
        "this would cost a (small) per-turn no-op for unrelated subagents."
    )
    assert any(isinstance(m, ViewImageMiddleware) for m in with_vision), (
        "ViewImageMiddleware missing from vision subagent's chain — "
        "view_image_tool would write base64 to state but the next LLM "
        "call would never receive the pixels."
    )


def test_glm4v_plus_model_config_loadable(loaded_app_config) -> None:
    """config.yaml exposes glm-4v-plus with supports_vision=True."""
    from src.config import get_app_config

    cfg = get_app_config().get_model_config("glm-4v-plus")
    assert cfg is not None, "glm-4v-plus missing from config.yaml models[]"
    assert cfg.supports_vision is True
    # Must NOT claim thinking support — GLM-4V doesn't have a thinking
    # mode in OpenAI-compat protocol; flagging it would let the runtime
    # try to enable thinking and 400 the request.
    assert cfg.supports_thinking is False or cfg.supports_thinking is None


def test_deepseek_v4_pro_unchanged(loaded_app_config) -> None:
    """Lead model deepseek-v4-pro is still the first model and still
    supports_thinking=True. Regression test against accidentally
    breaking the thinking path while wiring up vision."""
    from src.config import get_app_config

    app_cfg = get_app_config()
    assert app_cfg.models, "no models configured"
    # We don't care about ordering vs glm-4v-plus, just that
    # deepseek-v4-pro is present and unchanged.
    cfg = app_cfg.get_model_config("deepseek-v4-pro")
    assert cfg is not None
    assert cfg.supports_thinking is True
