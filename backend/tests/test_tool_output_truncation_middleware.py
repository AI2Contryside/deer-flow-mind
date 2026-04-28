"""Tests for ``ToolOutputTruncationMiddleware``.

Pinned to thread ``09417ecf-b0e0-470f-ae3d-244a0b1ba74d``: a single oversized
``ToolMessage`` (docling-parsed xlsx + erpnext bulk listing) defeated
``SummarizationMiddleware`` because ``trim_messages`` returned an empty
list. This middleware prevents that class of failure by capping every
``ToolMessage`` at a configurable size before it reaches state, and
spilling the original to a sandbox file the model can read on demand.

Contract:

  - ToolMessages whose content fits under the cap pass through untouched.
  - ToolMessages exceeding the cap are rewritten in place: head + note + tail.
  - The full payload is written to ``/mnt/user-data/workspace/<spill_dir>/<id>.txt``
    via the sandbox provider — when available.
  - When the sandbox is unavailable or the write fails, in-place truncation
    still happens (the note degrades to "sandbox unavailable") — the
    middleware **never** lets oversized content through.
  - Multimodal (list-of-blocks) content keeps non-text blocks intact and
    only truncates the largest text block.
  - ``Command`` returns are passed through (control-flow, not state).
  - Tools listed in ``skip_tool_names`` are exempted.
  - Already-truncated messages (resume / replay) are not double-truncated.
  - Disabled config ⇒ pure pass-through.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from src.agents.middlewares.tool_output_truncation_middleware import (
    _TRUNCATION_FLAG,
    ToolOutputTruncationMiddleware,
)
from src.config.tool_output_config import ToolOutputTruncationConfig

# ── shared helpers ────────────────────────────────────────────────────


def _cfg(**overrides) -> ToolOutputTruncationConfig:
    # Defaults sized so head+tail < max with room for the truncation note.
    base = {
        "enabled": True,
        "max_chars": 500,
        "keep_head_chars": 20,
        "keep_tail_chars": 10,
        "spill_dir": ".tool_outputs",
        "skip_tool_names": [],
    }
    base.update(overrides)
    return ToolOutputTruncationConfig(**base)


def _request(*, name: str = "bash", id: str = "call_123", with_sandbox: bool = True):
    """Fake ToolCallRequest. Only the fields the middleware reads matter."""
    runtime = MagicMock()
    runtime.state = {"sandbox": {"sandbox_id": "sbx-test"}} if with_sandbox else {"sandbox": None}
    request = MagicMock()
    request.tool_call = {"name": name, "id": id}
    request.runtime = runtime
    return request


def _patch_sandbox(monkeypatch, *, captured: list, raise_on_write: bool = False):
    """Stub the sandbox provider so write_file appends to ``captured``."""

    fake_sandbox = MagicMock()
    if raise_on_write:
        fake_sandbox.write_file.side_effect = OSError("disk full")
    else:
        fake_sandbox.write_file.side_effect = lambda path, content: captured.append((path, content))

    fake_provider = MagicMock()
    fake_provider.get.return_value = fake_sandbox

    monkeypatch.setattr(
        "src.agents.middlewares.tool_output_truncation_middleware.get_sandbox_provider",
        lambda: fake_provider,
    )
    return fake_sandbox


# ── pass-through cases ───────────────────────────────────────────────


def test_disabled_config_is_pure_passthrough(monkeypatch):
    captured: list = []
    _patch_sandbox(monkeypatch, captured=captured)
    mw = ToolOutputTruncationMiddleware(_cfg(enabled=False))
    big = "x" * 9999
    msg = ToolMessage(content=big, tool_call_id="c", name="bash")

    out = mw.wrap_tool_call(_request(), lambda req: msg)

    assert out is msg
    assert captured == []


def test_under_cap_passes_through_unchanged(monkeypatch):
    captured: list = []
    _patch_sandbox(monkeypatch, captured=captured)
    mw = ToolOutputTruncationMiddleware(_cfg(max_chars=100))
    msg = ToolMessage(content="short", tool_call_id="c", name="bash")

    out = mw.wrap_tool_call(_request(), lambda req: msg)

    assert out is msg
    assert captured == []


def test_command_return_passes_through(monkeypatch):
    captured: list = []
    _patch_sandbox(monkeypatch, captured=captured)
    mw = ToolOutputTruncationMiddleware(_cfg())
    cmd = Command(update={"foo": "bar"})

    out = mw.wrap_tool_call(_request(), lambda req: cmd)

    assert out is cmd


def test_skip_tool_names_exempts_named_tool(monkeypatch):
    captured: list = []
    _patch_sandbox(monkeypatch, captured=captured)
    mw = ToolOutputTruncationMiddleware(_cfg(skip_tool_names=["view_image"]))
    big = "x" * 9999
    msg = ToolMessage(content=big, tool_call_id="c", name="view_image")

    out = mw.wrap_tool_call(_request(name="view_image"), lambda req: msg)

    assert out is msg


def test_already_truncated_not_double_truncated(monkeypatch):
    captured: list = []
    _patch_sandbox(monkeypatch, captured=captured)
    mw = ToolOutputTruncationMiddleware(_cfg(max_chars=50))
    big = "x" * 9999
    msg = ToolMessage(
        content=big,
        tool_call_id="c",
        name="bash",
        additional_kwargs={_TRUNCATION_FLAG: {"original_chars": 9999, "spill_path": "/x/y.txt"}},
    )

    out = mw.wrap_tool_call(_request(), lambda req: msg)

    assert out is msg


# ── string truncation ────────────────────────────────────────────────


def test_oversized_string_is_truncated_with_head_tail_and_spilled(monkeypatch):
    captured: list = []
    _patch_sandbox(monkeypatch, captured=captured)
    mw = ToolOutputTruncationMiddleware(_cfg(max_chars=300, keep_head_chars=20, keep_tail_chars=10))
    payload = "HEAD" + "M" * 500 + "TAIL"  # 508 chars > 300 cap
    msg = ToolMessage(content=payload, tool_call_id="call_xyz", name="bash")

    out = mw.wrap_tool_call(_request(id="call_xyz"), lambda req: msg)

    assert out is not msg
    assert isinstance(out, ToolMessage)
    assert out.content.startswith("HEAD")  # head preserved
    assert out.content.endswith("MTAIL")  # tail preserved
    assert "TRUNCATED" in out.content
    assert "/mnt/user-data/workspace/.tool_outputs/call_xyz.txt" in out.content
    assert len(out.content) < len(payload)
    # additional_kwargs metadata
    meta = out.additional_kwargs[_TRUNCATION_FLAG]
    assert meta["original_chars"] == len(payload)
    assert meta["spill_path"] == "/mnt/user-data/workspace/.tool_outputs/call_xyz.txt"
    assert meta["tool_name"] == "bash"
    # Spilled to sandbox
    assert len(captured) == 1
    spill_path, spill_content = captured[0]
    assert spill_path == "/mnt/user-data/workspace/.tool_outputs/call_xyz.txt"
    assert spill_content == payload


def test_truncation_note_includes_size_and_tool_name(monkeypatch):
    captured: list = []
    _patch_sandbox(monkeypatch, captured=captured)
    mw = ToolOutputTruncationMiddleware(_cfg(max_chars=50))
    payload = "x" * 1234
    msg = ToolMessage(content=payload, tool_call_id="abc", name="my_tool")

    out = mw.wrap_tool_call(_request(name="my_tool", id="abc"), lambda req: msg)

    assert "1234 chars" in out.content
    assert "'my_tool'" in out.content


# ── failure paths ────────────────────────────────────────────────────


def test_sandbox_missing_still_truncates_with_degraded_note(monkeypatch):
    # Sandbox state absent — request.runtime.state["sandbox"] is None
    monkeypatch.setattr(
        "src.agents.middlewares.tool_output_truncation_middleware.get_sandbox_provider",
        lambda: MagicMock(),
    )
    mw = ToolOutputTruncationMiddleware(_cfg(max_chars=50))
    payload = "x" * 999
    msg = ToolMessage(content=payload, tool_call_id="abc", name="bash")

    out = mw.wrap_tool_call(_request(with_sandbox=False), lambda req: msg)

    assert "TRUNCATED" in out.content
    assert "Sandbox unavailable" in out.content
    assert out.additional_kwargs[_TRUNCATION_FLAG]["spill_path"] is None


def test_sandbox_write_failure_still_truncates_with_degraded_note(monkeypatch):
    captured: list = []
    _patch_sandbox(monkeypatch, captured=captured, raise_on_write=True)
    mw = ToolOutputTruncationMiddleware(_cfg(max_chars=50))
    payload = "x" * 999
    msg = ToolMessage(content=payload, tool_call_id="abc", name="bash")

    out = mw.wrap_tool_call(_request(), lambda req: msg)

    # Truncated even though write failed
    assert len(out.content) < len(payload)
    assert out.additional_kwargs[_TRUNCATION_FLAG]["spill_path"] is None
    # Degraded note (no path)
    assert "Sandbox unavailable" in out.content


# ── multimodal handling ──────────────────────────────────────────────


def test_list_content_truncates_only_largest_text_block(monkeypatch):
    captured: list = []
    _patch_sandbox(monkeypatch, captured=captured)
    mw = ToolOutputTruncationMiddleware(_cfg(max_chars=50, keep_head_chars=10, keep_tail_chars=5))
    blocks = [
        {"type": "text", "text": "small"},
        {"type": "image_url", "image_url": "https://example.com/foo.png"},
        {"type": "text", "text": "X" * 999},  # the big one
        {"type": "text", "text": "tiny"},
    ]
    msg = ToolMessage(content=blocks, tool_call_id="img1", name="bash")

    out = mw.wrap_tool_call(_request(id="img1"), lambda req: msg)

    assert isinstance(out.content, list)
    assert len(out.content) == 4
    # image_url block intact
    assert out.content[1] == {"type": "image_url", "image_url": "https://example.com/foo.png"}
    # small text intact
    assert out.content[0] == {"type": "text", "text": "small"}
    assert out.content[3] == {"type": "text", "text": "tiny"}
    # big text truncated
    big_block = out.content[2]
    assert big_block["type"] == "text"
    assert "TRUNCATED" in big_block["text"]
    assert len(big_block["text"]) < 999
    # Spill content joins all text blocks
    assert len(captured) == 1
    _, spill_content = captured[0]
    assert "small" in spill_content and "tiny" in spill_content
    assert spill_content.count("X") == 999


def test_zero_text_blocks_passes_through(monkeypatch):
    """List with only image blocks (no text to truncate) shouldn't blow up."""
    captured: list = []
    _patch_sandbox(monkeypatch, captured=captured)
    # Image-only content has size 0 so this validates the under-cap branch
    # regardless of max_chars value.
    mw = ToolOutputTruncationMiddleware(_cfg(max_chars=500))
    blocks = [{"type": "image_url", "image_url": "x"}]
    msg = ToolMessage(content=blocks, tool_call_id="i", name="bash")

    out = mw.wrap_tool_call(_request(), lambda req: msg)

    # _content_size returns 0 for image-only list → under cap → unchanged
    assert out is msg


# ── config validation ────────────────────────────────────────────────


def test_config_rejects_keep_chars_exceeding_max():
    with pytest.raises(ValueError, match="strictly less than max_chars"):
        ToolOutputTruncationConfig(enabled=True, max_chars=100, keep_head_chars=80, keep_tail_chars=30)


def test_config_accepts_keep_chars_under_max():
    cfg = ToolOutputTruncationConfig(enabled=True, max_chars=100, keep_head_chars=40, keep_tail_chars=30)
    assert cfg.max_chars == 100
