"""Behaviour tests for ``try_resync_presented_artifact``.

Covers the bug where modifying a previously-presented file (``str_replace`` /
``write_file``) silently fails to update the canvas because nothing pushes a
fresh ``artifact_metadata`` event downstream. The resync helper is what closes
that gap; these tests pin its contract.
"""

import importlib
from types import SimpleNamespace

present_file_tool_module = importlib.import_module("src.tools.builtins.present_file_tool")
try_resync_presented_artifact = present_file_tool_module.try_resync_presented_artifact


def _make_runtime(outputs_path: str, artifacts: list[str], thread_id: str = "thread-1", tenant_id: str | None = "tenant-1") -> SimpleNamespace:
    return SimpleNamespace(
        state={"thread_data": {"outputs_path": outputs_path}, "artifacts": artifacts},
        context={"thread_id": thread_id, "tenant_id": tenant_id},
    )


def test_resync_returns_none_when_artifacts_empty(tmp_path):
    outputs_dir = tmp_path / "threads" / "thread-1" / "user-data" / "outputs"
    outputs_dir.mkdir(parents=True)
    artifact_path = outputs_dir / "report.md"
    artifact_path.write_text("hello")

    result = try_resync_presented_artifact(
        runtime=_make_runtime(str(outputs_dir), artifacts=[]),
        filepath=str(artifact_path),
        tool_call_id="tc-1",
    )

    assert result is None


def test_resync_returns_none_when_file_not_presented_yet(tmp_path):
    outputs_dir = tmp_path / "threads" / "thread-1" / "user-data" / "outputs"
    outputs_dir.mkdir(parents=True)
    presented = outputs_dir / "presented.md"
    presented.write_text("v1")
    not_yet = outputs_dir / "draft.md"
    not_yet.write_text("draft")

    result = try_resync_presented_artifact(
        runtime=_make_runtime(str(outputs_dir), artifacts=["/mnt/user-data/outputs/presented.md"]),
        filepath=str(not_yet),
        tool_call_id="tc-2",
    )

    assert result is None


def test_resync_returns_none_when_path_outside_outputs(tmp_path):
    outputs_dir = tmp_path / "threads" / "thread-1" / "user-data" / "outputs"
    workspace_dir = tmp_path / "threads" / "thread-1" / "user-data" / "workspace"
    outputs_dir.mkdir(parents=True)
    workspace_dir.mkdir(parents=True)
    leaked = workspace_dir / "scratch.txt"
    leaked.write_text("scratch")

    result = try_resync_presented_artifact(
        runtime=_make_runtime(str(outputs_dir), artifacts=["/mnt/user-data/outputs/presented.md"]),
        filepath=str(leaked),
        tool_call_id="tc-3",
    )

    assert result is None


def test_resync_emits_command_when_presented_file_modified(tmp_path, monkeypatch):
    outputs_dir = tmp_path / "threads" / "thread-1" / "user-data" / "outputs"
    outputs_dir.mkdir(parents=True)
    artifact_path = outputs_dir / "report.md"
    artifact_path.write_text("v2 content longer than v1")

    # _build_metadata resolves the virtual outputs path back to a local file via
    # get_paths(). Stub it to return our tmp_path artifact so the metadata fields
    # (size, mime_type) reflect the modified file.
    monkeypatch.setattr(
        present_file_tool_module,
        "get_paths",
        lambda: SimpleNamespace(resolve_virtual_path=lambda thread_id, path: artifact_path),
    )

    result = try_resync_presented_artifact(
        runtime=_make_runtime(str(outputs_dir), artifacts=["/mnt/user-data/outputs/report.md"]),
        filepath=str(artifact_path),
        tool_call_id="tc-4",
    )

    assert result is not None
    update = result.update
    # Tool message must carry the original tool_call_id so LangGraph closes the
    # tool call cleanly even when we hand back a Command instead of "OK".
    assert update["messages"][0].tool_call_id == "tc-4"
    assert update["messages"][0].content == "OK"
    # artifact_metadata is what the gateway needs to re-sign + push to the FE.
    assert "artifact_metadata" in update
    assert len(update["artifact_metadata"]) == 1
    meta = update["artifact_metadata"][0]
    assert meta["path"] == "/mnt/user-data/outputs/report.md"
    assert meta["filename"] == "report.md"
    assert meta["size"] == artifact_path.stat().st_size


def test_resync_accepts_virtual_path_input(tmp_path, monkeypatch):
    outputs_dir = tmp_path / "threads" / "thread-1" / "user-data" / "outputs"
    outputs_dir.mkdir(parents=True)
    artifact_path = outputs_dir / "report.md"
    artifact_path.write_text("updated")

    monkeypatch.setattr(
        present_file_tool_module,
        "get_paths",
        lambda: SimpleNamespace(resolve_virtual_path=lambda thread_id, path: artifact_path),
    )

    result = try_resync_presented_artifact(
        runtime=_make_runtime(str(outputs_dir), artifacts=["/mnt/user-data/outputs/report.md"]),
        filepath="/mnt/user-data/outputs/report.md",
        tool_call_id="tc-5",
    )

    assert result is not None
    assert result.update["artifact_metadata"][0]["filename"] == "report.md"


def test_resync_returns_none_when_thread_id_missing(tmp_path):
    outputs_dir = tmp_path / "threads" / "thread-1" / "user-data" / "outputs"
    outputs_dir.mkdir(parents=True)
    artifact_path = outputs_dir / "report.md"
    artifact_path.write_text("v2")

    runtime = SimpleNamespace(
        state={"thread_data": {"outputs_path": str(outputs_dir)}, "artifacts": ["/mnt/user-data/outputs/report.md"]},
        context={},
    )

    result = try_resync_presented_artifact(
        runtime=runtime,
        filepath=str(artifact_path),
        tool_call_id="tc-6",
    )

    assert result is None
