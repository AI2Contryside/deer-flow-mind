"""Tests for fill_template_tool — helper functions + builtin registration.

End-to-end tool invocation through `tool.invoke(...)` requires a fully-
constructed LangGraph ToolRuntime, which depends on a checkpointer + agent
graph. That's covered by integration tests run against a live LangGraph
service. Here we verify the small surfaces the tool exposes:
  - download helper handles success / size cap / HTTP error paths
  - error-command builder returns the expected ToolMessage shape
  - the tool is listed in BUILTIN_TOOLS so the agent can call it
"""

from __future__ import annotations

import io

import httpx
import openpyxl
import pytest
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from src.tools.builtins.fill_template_tool import (
    MAX_TEMPLATE_BYTES,
    _download_jinja_template,
    _error_command,
)


def _xlsx_with_tag(tag: str = "{{ customer_name }}") -> bytes:
    wb = openpyxl.Workbook()
    wb.active["A1"] = "客户:"
    wb.active["B1"] = tag
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ----------------------------- _error_command -----------------------------


def test_error_command_returns_tool_message():
    cmd = _error_command("call_xyz", "kaboom")
    assert isinstance(cmd, Command)
    update = cmd.update
    assert isinstance(update, dict)
    msgs = update.get("messages")
    assert msgs and len(msgs) == 1
    msg = msgs[0]
    assert isinstance(msg, ToolMessage)
    assert msg.tool_call_id == "call_xyz"
    assert "kaboom" in msg.content
    # Error path doesn't touch artifact state.
    assert "artifacts" not in update
    assert "artifact_metadata" not in update


# ------------------------- _download_jinja_template -----------------------


def test_download_returns_body_on_200():
    payload = _xlsx_with_tag()

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload)

    transport = httpx.MockTransport(handler)
    # Replace the underlying httpx.Client in the helper by mocking httpx.Client
    with httpx.Client(transport=transport) as _:
        # The helper builds its own client, so we monkeypatch the constructor.
        pass

    # Easier: direct dependency injection via patch.
    from unittest.mock import patch

    with patch("src.tools.builtins.fill_template_tool.httpx.Client") as mock_cls:
        mock_cls.return_value.__enter__.return_value.stream.return_value.__enter__.return_value.iter_bytes.return_value = [payload]
        mock_cls.return_value.__enter__.return_value.stream.return_value.__enter__.return_value.raise_for_status = lambda: None
        out = _download_jinja_template("https://example.test/x.xlsx")
        assert out == payload


def test_download_raises_on_http_error():
    """4xx / 5xx surfaces as httpx.HTTPError so the tool wraps it."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, content=b"<Error>NoSuchKey</Error>")

    transport = httpx.MockTransport(handler)
    from unittest.mock import patch

    real_client = httpx.Client(transport=transport)

    with patch("src.tools.builtins.fill_template_tool.httpx.Client", return_value=real_client):
        with pytest.raises(httpx.HTTPError):
            _download_jinja_template("https://example.test/missing.xlsx")


def test_download_rejects_oversized_response():
    """Body that streams past MAX_TEMPLATE_BYTES is killed mid-flight."""
    huge = b"x" * (MAX_TEMPLATE_BYTES + 1)

    from unittest.mock import patch

    with patch("src.tools.builtins.fill_template_tool.httpx.Client") as mock_cls:
        stream_ctx = mock_cls.return_value.__enter__.return_value.stream.return_value.__enter__.return_value
        stream_ctx.raise_for_status = lambda: None
        stream_ctx.iter_bytes.return_value = [huge]
        with pytest.raises(ValueError, match="too large"):
            _download_jinja_template("https://example.test/huge.xlsx")


# --------------------------- BUILTIN_TOOLS wiring -------------------------


def test_fill_template_tool_listed_in_builtin_tools():
    """The tool must show up so the lead agent can invoke it."""
    from src.tools.builtins import fill_template_tool
    from src.tools.tools import BUILTIN_TOOLS

    assert fill_template_tool in BUILTIN_TOOLS


def test_fill_template_tool_has_expected_args():
    """Sanity: @tool exposed the documented user-facing args.

    We inspect args_schema's model_fields rather than .model_json_schema()
    because the latter blows up on InjectedToolCallId (a langchain-internal
    Callable annotation that has no JSON-schema rendering).
    """
    from src.tools.builtins import fill_template_tool

    fields = fill_template_tool.args_schema.model_fields
    assert "output_name" in fields
    assert "data" in fields
