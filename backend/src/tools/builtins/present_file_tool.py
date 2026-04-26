import logging
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


def _push_to_oss(thread_id: str, tenant_id: str | None, virtual_path: str) -> None:
    """Best-effort upload of an artifact to the chat bucket.

    Skipped silently when there's no tenant in scope, when OSS is not wired
    up, or when the local file is missing — the artifact still appears in
    ``ThreadState.artifacts`` and the artifacts router will fall back to the
    local file. We log warnings so failures are visible during debugging
    without breaking the agent's response.
    """
    if not tenant_id:
        return
    try:
        storage = get_default()
    except Exception as exc:
        logger.debug("OSS unavailable during present_files: %s", exc)
        return
    try:
        local_path = get_paths().resolve_virtual_path(thread_id, virtual_path)
    except Exception as exc:
        logger.warning("Could not resolve %s for OSS push: %s", virtual_path, exc)
        return
    if not local_path.is_file():
        logger.warning("present_files: local file missing: %s", local_path)
        return
    filename = local_path.name
    key = chat_artifact_key(tenant_id, thread_id, filename)
    bucket = storage.config.chat_bucket
    try:
        with open(local_path, "rb") as fh:
            storage.put_object(bucket, key, fh.read(), content_type="", acl=ACL_PRIVATE)
        logger.info("Mirrored artifact %s -> oss://%s/%s", filename, bucket, key)
    except Exception as exc:
        logger.warning("OSS push failed for %s: %s", filename, exc)


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
    thread_id = runtime.context.get("thread_id") if runtime.context else None
    tenant_id = runtime.context.get("tenant_id") if runtime.context else None
    if thread_id:
        for vp in normalized_paths:
            _push_to_oss(thread_id, tenant_id, vp)

    # The merge_artifacts reducer will handle merging and deduplication
    return Command(
        update={
            "artifacts": normalized_paths,
            "messages": [ToolMessage("Successfully presented files", tool_call_id=tool_call_id)],
        },
    )
