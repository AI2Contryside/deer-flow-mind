"""Tests for the token-usage recorder.

Covers:

- The callback extracts ``input``/``output``/``cached``/``reasoning`` from
  a synthetic ``LLMResult`` and enqueues one record per model.
- Multiple calls inside the same turn aggregate (matching the UPSERT
  behaviour at the DB layer — we assert the *intent* by counting enqueued
  records).
- Missing ``session_id``/``turn_id`` is a no-op (no record enqueued).
- ``record_usage`` is a no-op when ``TOKEN_USAGE_DSN`` is unset.
- The model factory attaches the recorder to the model instance.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import pytest

# ---------------------------------------------------------------------------
# Synthetic LLMResult helpers — avoids depending on the real provider SDKs.
# ---------------------------------------------------------------------------


@dataclass
class _FakeMessage:
    usage_metadata: dict[str, Any]
    response_metadata: dict[str, Any]


@dataclass
class _FakeGen:
    message: _FakeMessage


def _make_result(
    *,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cached: int = 0,
    reasoning: int = 0,
):
    """Build a minimal object that quacks like ``LLMResult`` for our extractor."""

    @dataclass
    class _FakeResult:
        generations: list[list[_FakeGen]]

    usage: dict[str, Any] = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }
    if cached:
        usage["input_token_details"] = {"cache_read": cached}
    if reasoning:
        usage["output_token_details"] = {"reasoning": reasoning}

    msg = _FakeMessage(
        usage_metadata=usage,
        response_metadata={"model_name": model},
    )
    return _FakeResult(generations=[[_FakeGen(message=msg)]])


# ---------------------------------------------------------------------------
# Callback behaviour
# ---------------------------------------------------------------------------


def test_callback_enqueues_one_record_per_model(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.storage import token_usage_callback as cb_mod
    from src.storage.token_usage_callback import TokenUsageRecorder

    captured: list[Any] = []
    monkeypatch.setattr(cb_mod, "record_usage", captured.append)

    handler = TokenUsageRecorder()
    run_id = uuid4()
    metadata = {"session_id": "thread-A", "turn_id": "turn-1"}

    handler.on_chat_model_start({}, [], run_id=run_id, metadata=metadata)
    handler.on_llm_end(
        _make_result(model="claude-opus-4-7", input_tokens=100, output_tokens=20, cached=10, reasoning=5),
        run_id=run_id,
        metadata=metadata,
    )

    assert len(captured) == 1
    rec = captured[0]
    assert rec.session_id == "thread-A"
    assert rec.turn_id == "turn-1"
    assert rec.model == "claude-opus-4-7"
    assert rec.input_tokens == 100
    assert rec.output_tokens == 20
    assert rec.cached_tokens == 10
    assert rec.reasoning_tokens == 5


def test_callback_falls_back_to_thread_id_when_session_id_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.storage import token_usage_callback as cb_mod
    from src.storage.token_usage_callback import TokenUsageRecorder

    captured: list[Any] = []
    monkeypatch.setattr(cb_mod, "record_usage", captured.append)

    handler = TokenUsageRecorder()
    run_id = uuid4()
    metadata = {"thread_id": "thread-B", "turn_id": "turn-2"}

    handler.on_llm_end(
        _make_result(model="m", input_tokens=1, output_tokens=1),
        run_id=run_id,
        metadata=metadata,
    )

    assert len(captured) == 1
    assert captured[0].session_id == "thread-B"


def test_callback_skips_when_correlation_metadata_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.storage import token_usage_callback as cb_mod
    from src.storage.token_usage_callback import TokenUsageRecorder

    captured: list[Any] = []
    monkeypatch.setattr(cb_mod, "record_usage", captured.append)

    handler = TokenUsageRecorder()
    handler.on_llm_end(
        _make_result(model="m", input_tokens=10, output_tokens=2),
        run_id=uuid4(),
        metadata={},  # no session_id / turn_id
    )

    assert captured == []


def test_callback_recovers_metadata_stashed_at_start(monkeypatch: pytest.MonkeyPatch) -> None:
    """on_llm_end without metadata still records — handler stashed it on start."""
    from src.storage import token_usage_callback as cb_mod
    from src.storage.token_usage_callback import TokenUsageRecorder

    captured: list[Any] = []
    monkeypatch.setattr(cb_mod, "record_usage", captured.append)

    handler = TokenUsageRecorder()
    run_id = uuid4()
    handler.on_chat_model_start({}, [], run_id=run_id, metadata={"session_id": "s", "turn_id": "t"})
    handler.on_llm_end(
        _make_result(model="m", input_tokens=3, output_tokens=4),
        run_id=run_id,
        # No metadata kwarg this time.
    )

    assert len(captured) == 1
    assert captured[0].turn_id == "t"


def test_callback_skips_zero_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    """A response with usage_metadata but all zeros should produce no record."""
    from src.storage import token_usage_callback as cb_mod
    from src.storage.token_usage_callback import TokenUsageRecorder

    captured: list[Any] = []
    monkeypatch.setattr(cb_mod, "record_usage", captured.append)

    handler = TokenUsageRecorder()
    handler.on_llm_end(
        _make_result(model="m", input_tokens=0, output_tokens=0),
        run_id=uuid4(),
        metadata={"session_id": "s", "turn_id": "t"},
    )

    assert captured == []


def test_callback_aggregates_batched_generations_per_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """A single LLMResult with multiple generations on the same model produces one record."""
    from src.storage.token_usage_callback import _build_records

    msg_a = _FakeMessage(usage_metadata={"input_tokens": 10, "output_tokens": 5}, response_metadata={"model_name": "m"})
    msg_b = _FakeMessage(usage_metadata={"input_tokens": 20, "output_tokens": 8}, response_metadata={"model_name": "m"})

    @dataclass
    class _R:
        generations: list[list[_FakeGen]]

    result = _R(generations=[[_FakeGen(message=msg_a), _FakeGen(message=msg_b)]])

    records = _build_records(result, "s", "t", fallback_model=None)
    assert len(records) == 1
    assert records[0].input_tokens == 30
    assert records[0].output_tokens == 13


# ---------------------------------------------------------------------------
# record_usage no-ops without a DSN
# ---------------------------------------------------------------------------


def test_record_usage_noop_without_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.storage import token_usage as token_usage_mod

    monkeypatch.delenv(token_usage_mod.DSN_ENV_VAR, raising=False)
    # Reset module state to ensure a clean slate.
    monkeypatch.setattr(token_usage_mod, "_queue", None, raising=False)
    monkeypatch.setattr(token_usage_mod, "_writer_thread", None, raising=False)
    monkeypatch.setattr(token_usage_mod, "_disabled", False, raising=False)

    record = token_usage_mod.TokenUsageRecord(
        session_id="s",
        turn_id="t",
        model="m",
        input_tokens=1,
        output_tokens=1,
        cached_tokens=0,
        reasoning_tokens=0,
    )

    # Must return without raising and without starting a writer thread.
    token_usage_mod.record_usage(record)

    assert token_usage_mod._queue is None
    assert token_usage_mod._writer_thread is None


# ---------------------------------------------------------------------------
# Model factory wires the recorder
# ---------------------------------------------------------------------------


def test_factory_attaches_recorder_callback(monkeypatch: pytest.MonkeyPatch) -> None:
    """create_chat_model must append a TokenUsageRecorder to model.callbacks."""
    # ``test_subagent_executor.py`` installs a session-scoped autouse fixture
    # that replaces ``src.models`` with ``MagicMock()`` for the rest of the
    # session. Force a clean reimport so we exercise the real factory.
    import sys

    for mod in ("src.models", "src.models.factory"):
        sys.modules.pop(mod, None)

    from src.models import factory as factory_mod

    # Build a stand-in chat model class — factory uses ``resolve_class`` to
    # construct it. We patch resolve_class to return our stub regardless of
    # what config.yaml requested.
    class _StubChatModel:
        def __init__(self, **_kwargs: Any) -> None:
            self.callbacks: list[Any] = []

    @dataclass
    class _StubModelConfig:
        use: str = "stub"
        name: str = "stub"
        display_name: str | None = None
        description: str | None = None
        supports_thinking: bool = False
        supports_reasoning_effort: bool = False
        when_thinking_enabled: dict | None = None
        thinking: dict | None = None
        supports_vision: bool = False

        def model_dump(self, **_kwargs: Any) -> dict:
            return {}

    class _StubAppConfig:
        models = [_StubModelConfig()]

        def get_model_config(self, _name: str) -> _StubModelConfig:
            return self.models[0]

    monkeypatch.setattr(factory_mod, "get_app_config", lambda: _StubAppConfig())
    monkeypatch.setattr(factory_mod, "is_tracing_enabled", lambda: False)
    monkeypatch.setattr(factory_mod, "resolve_class", lambda *_a, **_kw: _StubChatModel)

    instance = factory_mod.create_chat_model(name="stub")

    # Identity-by-class-name to survive sys.modules churn from other tests
    # in the suite that re-import storage modules.
    assert any(type(cb).__name__ == "TokenUsageRecorder" for cb in instance.callbacks), "TokenUsageRecorder should be attached to every model created by create_chat_model"


# ---------------------------------------------------------------------------
# Direct-invoke call sites must forward run metadata
# ---------------------------------------------------------------------------


def test_ocr_tool_forwards_run_metadata_to_invoke(monkeypatch: pytest.MonkeyPatch) -> None:
    """extract_trade_document_tool must thread session_id/turn_id through invoke's config.

    Without this, the TokenUsageRecorder attached at the model level can't
    correlate the OCR call to the parent run and skips it entirely.
    """
    import sys
    from unittest.mock import MagicMock

    from langchain_core.messages import AIMessage

    # Import the *module* explicitly via sys.modules. Importing by dotted
    # path returns the StructuredTool re-exported in builtins/__init__.py
    # because the package attribute and the submodule share a name.
    import src.tools.builtins.extract_trade_document_tool  # noqa: F401  -- ensure submodule is loaded

    ocr_mod = sys.modules["src.tools.builtins.extract_trade_document_tool"]
    ocr_tool = ocr_mod.extract_trade_document_tool

    captured_config: dict[str, Any] = {}

    class _StubModel:
        def invoke(self, _messages: Any, config: Any = None) -> Any:
            captured_config.update(config or {})
            # Return a tiny valid OCR JSON envelope so the rest of the tool
            # pipeline runs to completion without choking on parse errors.
            return AIMessage(content='{"doc_type": "unknown", "fields": {}, "items": []}')

    monkeypatch.setattr(ocr_mod, "create_chat_model", lambda **_kw: _StubModel())
    # Bypass virtual-path translation and OSS push so we don't need a real
    # sandbox or storage backend in this unit test.
    monkeypatch.setattr(ocr_mod, "replace_virtual_path", lambda p, _td: p)
    monkeypatch.setattr(ocr_mod, "get_thread_data", lambda _r: {})
    monkeypatch.setattr(ocr_mod, "_push_to_oss", lambda *_a, **_kw: None)
    monkeypatch.setattr(ocr_mod, "_build_metadata", lambda *_a, **_kw: {})

    # Stand up a temp image file so the path / size / read checks pass.
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
        f.write(b"\xff\xd8\xff\xe0fake-jpeg")
        image_path = f.name

    runtime = MagicMock()
    # Leave runtime.config empty to prove we read from the LangChain
    # contextvar (which is the production-reliable source).
    runtime.config = {}
    runtime.state = {"sandbox": None, "thread_data": {}}

    from langchain_core.runnables.config import var_child_runnable_config

    token = var_child_runnable_config.set({"metadata": {"session_id": "thread-X", "turn_id": "turn-9"}})
    try:
        # The ``@tool`` decorator wraps the function; ``.func`` exposes the raw callable.
        ocr_tool.func(
            runtime=runtime,
            image_path=image_path,
            tool_call_id="tc-test",
        )
    finally:
        var_child_runnable_config.reset(token)

    metadata = captured_config.get("metadata") or {}
    assert metadata.get("session_id") == "thread-X"
    assert metadata.get("turn_id") == "turn-9"


# ---------------------------------------------------------------------------
# Background-call helper + wiring
# ---------------------------------------------------------------------------


def test_background_invoke_config_format() -> None:
    """Synthetic turn_id must be deterministic in shape: bg:{source}:{key}:..."""
    from src.storage.token_usage import background_invoke_config

    cfg = background_invoke_config("memory", "thread-123")
    metadata = cfg["metadata"]
    assert metadata["session_id"] == "thread-123"
    turn_id = metadata["turn_id"]
    assert turn_id.startswith("bg:memory:thread-123:")
    # Two consecutive calls produce distinct turn_ids so an UPSERT
    # doesn't collapse separate background runs into one row.
    cfg2 = background_invoke_config("memory", "thread-123")
    assert cfg2["metadata"]["turn_id"] != turn_id
    # The empty-source / empty-key guard rails: no NULLs leak into the row.
    cfg3 = background_invoke_config("", "")
    assert cfg3["metadata"]["session_id"] == "unknown"
    assert cfg3["metadata"]["turn_id"].startswith("bg:unknown:unknown:")


def test_memory_updater_passes_background_config_to_invoke(monkeypatch: pytest.MonkeyPatch) -> None:
    """MemoryUpdater.update_memory must pass a bg:memory:* config to invoke."""
    from unittest.mock import MagicMock

    import src.agents.memory.updater as updater_mod

    # Force the config to be enabled so update_memory actually runs the LLM.
    monkeypatch.setattr(updater_mod, "get_memory_config", lambda: MagicMock(enabled=True, fact_confidence_threshold=0.7, max_facts=100, model_name=None))
    monkeypatch.setattr(updater_mod, "get_memory_data", lambda _agent: {"user": {}, "history": {}, "facts": []})
    monkeypatch.setattr(updater_mod, "format_conversation_for_update", lambda _msgs: "fake conversation")
    monkeypatch.setattr(updater_mod, "_save_memory_to_file", lambda *_a, **_kw: True)

    captured: dict[str, Any] = {}

    class _StubModel:
        def invoke(self, _prompt: Any, config: Any = None) -> Any:
            captured["config"] = config
            return MagicMock(content='{"user": {}, "history": {}, "newFacts": [], "factsToRemove": []}')

    updater = updater_mod.MemoryUpdater()
    monkeypatch.setattr(updater, "_get_model", lambda: _StubModel())

    updater.update_memory(messages=[MagicMock()], thread_id="thread-mem")

    assert captured.get("config") is not None
    metadata = captured["config"].get("metadata") or {}
    assert metadata.get("session_id") == "thread-mem"
    assert metadata.get("turn_id", "").startswith("bg:memory:thread-mem:")


def test_title_middleware_merges_run_metadata_into_invoke_config() -> None:
    """TitleMiddleware._generate_title must merge parent run's session_id/turn_id
    (read from LangChain's active-runnable-config contextvar) into the invoke config
    so the recorder attributes the title call to the turn.
    """
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    from langchain_core.messages import AIMessage, HumanMessage
    from langchain_core.runnables.config import var_child_runnable_config

    from src.agents.middlewares.title_middleware import TitleMiddleware

    middleware = TitleMiddleware()
    fake_model = MagicMock()
    fake_model.ainvoke = AsyncMock(return_value=MagicMock(content="标题"))

    import src.agents.middlewares.title_middleware as title_mod

    original_create = title_mod.create_chat_model
    title_mod.create_chat_model = lambda **_kw: fake_model

    # Simulate LangGraph having populated the active-runnable-config
    # contextvar with our run's metadata before the middleware fires.
    token = var_child_runnable_config.set({"metadata": {"session_id": "thread-Y", "turn_id": "turn-42"}})
    try:
        state = {
            "messages": [
                HumanMessage(content="hello"),
                AIMessage(content="hi"),
            ]
        }
        asyncio.run(middleware._generate_title(state))

        _, kwargs = fake_model.ainvoke.await_args
        run_config = kwargs.get("config") or {}
        metadata = run_config.get("metadata") or {}
        assert metadata.get("session_id") == "thread-Y"
        assert metadata.get("turn_id") == "turn-42"
        # Existing internal_invocation marker must still be present so the
        # gateway/FE filter for hiding title prompts in the chat stream
        # continues to work.
        assert metadata.get("internal_invocation") == "title"
        assert "internal:title" in run_config.get("tags", [])
    finally:
        title_mod.create_chat_model = original_create
        var_child_runnable_config.reset(token)
