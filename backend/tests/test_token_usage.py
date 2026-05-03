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
