import logging
import mimetypes
from pathlib import Path
from typing import Annotated

from langchain.tools import InjectedToolCallId, ToolRuntime, tool
from langchain_core.messages import ToolMessage
from langgraph.types import Command
from langgraph.typing import ContextT

from src.agents.thread_state import ThreadState
from src.config.paths import VIRTUAL_PATH_PREFIX, get_paths
from src.storage import ACL_PRIVATE, chat_artifact_key, get_default

logger = logging.getLogger(__name__)

OUTPUTS_VIRTUAL_PREFIX = f"{VIRTUAL_PATH_PREFIX}/outputs"


def _build_metadata(
    thread_id: str | None,
    tenant_id: str | None,
    virtual_path: str,
    uploaded: bool,
) -> dict | None:
    """Build the descriptive metadata payload for a presented artifact.

    Returns a dict with `path`, `filename`, `oss_key`, `size`, `mime_type`
    so the gateway can synthesize a signed-URL ApiAttachment for the FE.
    Returns None when the local file is missing — the artifact still appears
    in `ThreadState.artifacts` so the legacy 302 path keeps working.

    `oss_key` is only populated when `uploaded=True` — i.e. the OSS push
    actually succeeded. Otherwise we'd advertise a key for an object that
    isn't there and the gateway would sign a URL that 404s.
    """
    if not thread_id:
        return None
    try:
        local_path = get_paths().resolve_virtual_path(thread_id, virtual_path)
    except Exception as exc:
        logger.warning("Could not resolve %s for metadata: %s", virtual_path, exc)
        return None
    if not local_path.is_file():
        return None
    filename = local_path.name
    mime_type, _ = mimetypes.guess_type(filename)
    metadata: dict = {
        "path": virtual_path,
        "filename": filename,
        "size": local_path.stat().st_size,
        "mime_type": mime_type or "application/octet-stream",
    }
    if uploaded and tenant_id:
        metadata["oss_key"] = chat_artifact_key(tenant_id, thread_id, filename)
    return metadata


def _normalize_presented_filepath(
    runtime: ToolRuntime[ContextT, ThreadState],
    filepath: str,
) -> str:
    """Normalize a presented file path to the `/mnt/user-data/outputs/*` contract.

    Accepts either:
    - A virtual sandbox path such as `/mnt/user-data/outputs/report.md`
    - A host-side thread outputs path such as
      `/app/backend/.deer-flow/threads/<thread>/user-data/outputs/report.md`

    Returns:
        The normalized virtual path.

    Raises:
        ValueError: If runtime metadata is missing or the path is outside the
            current thread's outputs directory.
    """
    if runtime.state is None:
        raise ValueError("Thread runtime state is not available")

    thread_id = runtime.context.get("thread_id")
    if not thread_id:
        raise ValueError("Thread ID is not available in runtime context")

    thread_data = runtime.state.get("thread_data") or {}
    outputs_path = thread_data.get("outputs_path")
    if not outputs_path:
        raise ValueError("Thread outputs path is not available in runtime state")

    outputs_dir = Path(outputs_path).resolve()
    stripped = filepath.lstrip("/")
    virtual_prefix = VIRTUAL_PATH_PREFIX.lstrip("/")

    if stripped == virtual_prefix or stripped.startswith(virtual_prefix + "/"):
        actual_path = get_paths().resolve_virtual_path(thread_id, filepath)
    else:
        actual_path = Path(filepath).expanduser().resolve()

    try:
        relative_path = actual_path.relative_to(outputs_dir)
    except ValueError as exc:
        raise ValueError(f"Only files in {OUTPUTS_VIRTUAL_PREFIX} can be presented: {filepath}") from exc

    return f"{OUTPUTS_VIRTUAL_PREFIX}/{relative_path.as_posix()}"


def _push_to_oss(thread_id: str, tenant_id: str | None, virtual_path: str) -> bool:
    """Best-effort upload of an artifact to the chat bucket.

    Returns True when the object was successfully written to OSS, False when
    the upload was skipped (no tenant, OSS unconfigured, file missing) or
    failed. Callers use the return value to gate downstream metadata so we
    don't advertise an `oss_key` for an object that isn't actually there.

    Skipped silently when there's no tenant in scope, when OSS is not wired
    up, or when the local file is missing — the artifact still appears in
    ``ThreadState.artifacts`` and the artifacts router will fall back to the
    local file. We log warnings so failures are visible during debugging
    without breaking the agent's response.
    """
    if not tenant_id:
        return False
    try:
        storage = get_default()
    except Exception as exc:
        logger.debug("OSS unavailable during present_files: %s", exc)
        return False
    try:
        local_path = get_paths().resolve_virtual_path(thread_id, virtual_path)
    except Exception as exc:
        logger.warning("Could not resolve %s for OSS push: %s", virtual_path, exc)
        return False
    if not local_path.is_file():
        logger.warning("present_files: local file missing: %s", local_path)
        return False
    filename = local_path.name
    key = chat_artifact_key(tenant_id, thread_id, filename)
    bucket = storage.config.chat_bucket
    try:
        with open(local_path, "rb") as fh:
            storage.put_object(bucket, key, fh.read(), content_type="", acl=ACL_PRIVATE)
        logger.info("Mirrored artifact %s -> oss://%s/%s", filename, bucket, key)
        return True
    except Exception as exc:
        logger.warning("OSS push failed for %s: %s", filename, exc)
        return False


@tool("present_files", parse_docstring=True)
def present_file_tool(
    runtime: ToolRuntime[ContextT, ThreadState],
    filepaths: list[str],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Make files visible to the user for viewing and rendering in the client interface.

    When to use the present_files tool:

    - Making any file available for the user to view, download, or interact with
    - Presenting multiple related files at once
    - After creating files that should be presented to the user

    When NOT to use the present_files tool:
    - When you only need to read file contents for your own processing
    - For temporary or intermediate files not meant for user viewing

    Notes:
    - You should call this tool after creating files and moving them to the `/mnt/user-data/outputs` directory.
    - This tool can be safely called in parallel with other tools. State updates are handled by a reducer to prevent conflicts.

    Args:
        filepaths: List of absolute file paths to present to the user. **Only** files in `/mnt/user-data/outputs` can be presented.
    """
    try:
        normalized_paths = [_normalize_presented_filepath(runtime, filepath) for filepath in filepaths]
    except ValueError as exc:
        return Command(
            update={"messages": [ToolMessage(f"Error: {exc}", tool_call_id=tool_call_id)]},
        )

    # Mirror each artifact to OSS so other replicas / the desktop client can
    # fetch it via signed URL without depending on this host's filesystem.
    # Track per-path success so the metadata only advertises an oss_key when
    # the object actually landed in the bucket.
    thread_id = runtime.context.get("thread_id") if runtime.context else None
    tenant_id = runtime.context.get("tenant_id") if runtime.context else None
    upload_status: dict[str, bool] = {}
    if thread_id:
        for vp in normalized_paths:
            upload_status[vp] = _push_to_oss(thread_id, tenant_id, vp)

    metadata = [meta for meta in (_build_metadata(thread_id, tenant_id, vp, upload_status.get(vp, False)) for vp in normalized_paths) if meta is not None]

    update: dict = {
        "artifacts": normalized_paths,
        "messages": [ToolMessage("Successfully presented files", tool_call_id=tool_call_id)],
    }
    if metadata:
        update["artifact_metadata"] = metadata

    # The merge_artifacts / merge_artifact_metadata reducers handle dedup
    return Command(update=update)


def try_resync_presented_artifact(
    runtime: ToolRuntime[ContextT, ThreadState],
    filepath: str,
    tool_call_id: str,
) -> Command | None:
    """Re-push an artifact to OSS when it has already been presented.

    Called by write_file / str_replace after they modify a file in the sandbox.
    If the file is in the current ``ThreadState.artifacts`` list (i.e. the agent
    already called ``present_files`` for it earlier in this thread), we re-upload
    the file to OSS at the same key and emit an ``artifact_metadata`` update so
    the gateway re-signs the URL and the FE re-downloads with fresh bytes.

    Without this, every "modify a previously-presented file" turn is invisible to
    the FE: ``str_replace`` and ``write_file`` only return ``"OK"`` and never
    touch ``artifact_metadata``, so the canvas keeps showing stale content until
    the agent remembers to call ``present_files`` again — which it routinely
    doesn't, because nothing forces it to.

    Returns:
        A ``Command`` carrying the metadata update + the tool's ``ToolMessage``
        when a resync was performed, or ``None`` when no resync applies (file
        not under outputs/, never presented, no thread context, OSS unconfigured).
        Callers should return ``"OK"`` when this returns ``None``.
    """
    if runtime.state is None:
        return None
    artifacts = runtime.state.get("artifacts") or []
    if not artifacts:
        return None

    try:
        normalized = _normalize_presented_filepath(runtime, filepath)
    except ValueError:
        # File isn't under /mnt/user-data/outputs — can't be a presented artifact.
        return None
    if normalized not in artifacts:
        return None

    thread_id = runtime.context.get("thread_id") if runtime.context else None
    tenant_id = runtime.context.get("tenant_id") if runtime.context else None
    if not thread_id:
        return None

    uploaded = _push_to_oss(thread_id, tenant_id, normalized)
    metadata = _build_metadata(thread_id, tenant_id, normalized, uploaded)

    update: dict = {"messages": [ToolMessage("OK", tool_call_id=tool_call_id)]}
    if metadata:
        update["artifact_metadata"] = [metadata]
    return Command(update=update)
