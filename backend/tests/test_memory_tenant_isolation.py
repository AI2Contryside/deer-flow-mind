"""Regression tests for the tenant-isolation fixes in the memory subsystem.

Covers four behaviours that were previously broken and could leak one
tenant's memory into another's prompt:

1. ``FileMemoryStorage`` resolves a relative ``storage_path`` against
   ``Paths.base_dir`` (the volume-mounted ``DEER_FLOW_HOME``), not against
   the process cwd — otherwise tenant shards landed in the container's
   writable layer and were lost on rebuild.
2. ``get_tenant_memory_key`` rejects tenant ids that contain anything
   outside ``[A-Za-z0-9_-]`` so a malformed claim cannot escape its dir.
3. ``_get_memory_context`` returns ``""`` when ``tenant_id`` is ``None``,
   instead of falling back to the shared global file.
4. ``MemoryMiddleware.after_agent`` skips enqueuing when ``tenant_id`` is
   missing so we never silently write to that shared file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from src.agents.memory.storage import (
    FileMemoryStorage,
    create_empty_memory,
    get_tenant_memory_key,
)


# ── 1. storage_path resolution ────────────────────────────────────────────


def test_relative_storage_path_resolves_under_base_dir(tmp_path, monkeypatch) -> None:
    base = tmp_path / "deer-flow-home"
    base.mkdir()

    fake_paths = MagicMock()
    fake_paths.base_dir = base
    monkeypatch.setattr("src.config.paths.get_paths", lambda: fake_paths)

    cfg = MagicMock()
    cfg.storage_path = "memory"
    monkeypatch.setattr("src.agents.memory.storage.get_memory_config", lambda: cfg)

    storage = FileMemoryStorage()

    assert storage._base_dir == base / "memory"


def test_absolute_storage_path_used_as_is(tmp_path, monkeypatch) -> None:
    abs_dir = tmp_path / "abs-memory"

    cfg = MagicMock()
    cfg.storage_path = str(abs_dir)
    monkeypatch.setattr("src.agents.memory.storage.get_memory_config", lambda: cfg)

    fake_paths = MagicMock()
    fake_paths.base_dir = tmp_path / "should-not-be-used"
    monkeypatch.setattr("src.config.paths.get_paths", lambda: fake_paths)

    storage = FileMemoryStorage()

    assert storage._base_dir == abs_dir


def test_empty_storage_path_falls_back_to_base_dir_memory(tmp_path, monkeypatch) -> None:
    base = tmp_path / "deer-flow-home"
    base.mkdir()

    fake_paths = MagicMock()
    fake_paths.base_dir = base
    monkeypatch.setattr("src.config.paths.get_paths", lambda: fake_paths)

    cfg = MagicMock()
    cfg.storage_path = ""
    monkeypatch.setattr("src.agents.memory.storage.get_memory_config", lambda: cfg)

    storage = FileMemoryStorage()

    assert storage._base_dir == base / "memory"


def test_tenant_shard_lands_under_base_dir(tmp_path, monkeypatch) -> None:
    """End-to-end: write to tenant 42 lands under {base_dir}/memory/42/memory.json."""
    base = tmp_path / "deer-flow-home"
    base.mkdir()

    fake_paths = MagicMock()
    fake_paths.base_dir = base
    monkeypatch.setattr("src.config.paths.get_paths", lambda: fake_paths)

    cfg = MagicMock()
    cfg.storage_path = "memory"
    monkeypatch.setattr("src.agents.memory.storage.get_memory_config", lambda: cfg)

    storage = FileMemoryStorage()
    payload: dict[str, Any] = create_empty_memory()
    storage.write(get_tenant_memory_key("42"), payload)

    assert (base / "memory" / "42" / "memory.json").exists()


# ── 2. tenant_id sanitize ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "good_id",
    ["1", "42", "tenant_a", "Tenant-7", "abc_DEF-123", "x" * 64],
)
def test_get_tenant_memory_key_accepts_valid(good_id: str) -> None:
    assert get_tenant_memory_key(good_id) == f"{good_id}/memory.json"


@pytest.mark.parametrize(
    "bad_id",
    [
        "",
        "../etc/passwd",
        "10/../11",
        "a/b",
        "tenant id",
        "tenant\x00id",
        "x" * 65,
        "中文",
    ],
)
def test_get_tenant_memory_key_rejects_invalid(bad_id: str) -> None:
    with pytest.raises(ValueError):
        get_tenant_memory_key(bad_id)


def test_get_tenant_memory_key_rejects_non_string() -> None:
    with pytest.raises(ValueError):
        get_tenant_memory_key(None)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        get_tenant_memory_key(42)  # type: ignore[arg-type]


# ── 3. _get_memory_context fail-closed ────────────────────────────────────


def test_get_memory_context_returns_empty_when_tenant_id_missing(monkeypatch) -> None:
    from src.agents.lead_agent.prompt import _get_memory_context

    cfg = MagicMock()
    cfg.enabled = True
    cfg.injection_enabled = True
    cfg.max_injection_tokens = 2000
    monkeypatch.setattr("src.config.memory_config.get_memory_config", lambda: cfg)

    sentinel = MagicMock(side_effect=AssertionError("must not be called when tenant_id is None"))
    monkeypatch.setattr("src.agents.memory.updater.get_memory_data_with_tenant", sentinel)

    assert _get_memory_context(agent_name=None, tenant_id=None) == ""
    sentinel.assert_not_called()


def test_get_memory_context_loads_tenant_specific_when_id_present(monkeypatch) -> None:
    from src.agents.lead_agent.prompt import _get_memory_context

    cfg = MagicMock()
    cfg.enabled = True
    cfg.injection_enabled = True
    cfg.max_injection_tokens = 2000
    monkeypatch.setattr("src.config.memory_config.get_memory_config", lambda: cfg)

    expected = {"facts": [{"content": "tenant42 fact", "confidence": 0.9}]}
    monkeypatch.setattr(
        "src.agents.memory.updater.get_memory_data_with_tenant",
        lambda tid: expected if tid == "42" else {},
    )
    monkeypatch.setattr(
        "src.agents.memory.format_memory_for_injection",
        lambda data, max_tokens: f"FORMATTED:{data['facts'][0]['content']}",
    )

    out = _get_memory_context(agent_name=None, tenant_id="42")

    assert out.startswith("<memory>")
    assert "tenant42 fact" in out


# ── 4. MemoryMiddleware fail-closed ───────────────────────────────────────


def _runtime_with(context: dict[str, Any]) -> Any:
    runtime = MagicMock()
    runtime.context = context
    return runtime


def test_memory_middleware_skips_when_tenant_id_missing(monkeypatch) -> None:
    from langchain_core.messages import AIMessage, HumanMessage

    from src.agents.middlewares.memory_middleware import MemoryMiddleware

    cfg = MagicMock()
    cfg.enabled = True
    monkeypatch.setattr("src.agents.middlewares.memory_middleware.get_memory_config", lambda: cfg)

    queue = MagicMock()
    monkeypatch.setattr("src.agents.middlewares.memory_middleware.get_memory_queue", lambda: queue)

    mw = MemoryMiddleware()
    state = {"messages": [HumanMessage(content="hi"), AIMessage(content="hello")]}

    result = mw.after_agent(state, _runtime_with({"thread_id": "t-1"}))

    assert result is None
    queue.add.assert_not_called()


def test_memory_middleware_enqueues_when_tenant_id_present(monkeypatch) -> None:
    from langchain_core.messages import AIMessage, HumanMessage

    from src.agents.middlewares.memory_middleware import MemoryMiddleware

    cfg = MagicMock()
    cfg.enabled = True
    monkeypatch.setattr("src.agents.middlewares.memory_middleware.get_memory_config", lambda: cfg)

    queue = MagicMock()
    monkeypatch.setattr("src.agents.middlewares.memory_middleware.get_memory_queue", lambda: queue)

    mw = MemoryMiddleware()
    state = {"messages": [HumanMessage(content="hi"), AIMessage(content="hello")]}

    mw.after_agent(state, _runtime_with({"thread_id": "t-1", "tenant_id": "42"}))

    queue.add.assert_called_once()
    kwargs = queue.add.call_args.kwargs
    assert kwargs["thread_id"] == "t-1"
    assert kwargs["tenant_id"] == "42"
