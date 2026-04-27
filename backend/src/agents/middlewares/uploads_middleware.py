"""Middleware to inject uploaded files information into agent context."""

import logging
from pathlib import Path
from typing import Any, NotRequired, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import HumanMessage
from langgraph.runtime import Runtime

from src.config.paths import Paths, get_paths
from src.utils.document_extract import (
    docling_json_path,
    format_summary_inline,
    is_derived_artifact,
    load_summary,
)

logger = logging.getLogger(__name__)


class UploadsMiddlewareState(AgentState):
    """State schema for uploads middleware."""

    uploaded_files: NotRequired[list[dict] | None]


class UploadsMiddleware(AgentMiddleware[UploadsMiddlewareState]):
    """Middleware to inject uploaded files information into the agent context.

    Reads file metadata from the current message's additional_kwargs.files
    (set by the frontend after upload) and prepends an <uploaded_files> block
    to the last human message so the model knows which files are available.
    """

    state_schema = UploadsMiddlewareState

    def __init__(self, base_dir: str | None = None):
        """Initialize the middleware.

        Args:
            base_dir: Base directory for thread data. Defaults to Paths resolution.
        """
        super().__init__()
        self._paths = Paths(base_dir) if base_dir else get_paths()

    def _create_files_message(self, new_files: list[dict], historical_files: list[dict]) -> str:
        """Create a formatted message listing uploaded files.

        Args:
            new_files: Files uploaded in the current message.
            historical_files: Files uploaded in previous messages.

        Returns:
            Formatted string inside <uploaded_files> tags.
        """
        lines = ["<uploaded_files>"]

        lines.append("The following files were uploaded in this message:")
        lines.append("")
        if new_files:
            for file in new_files:
                lines.extend(self._render_file_entry(file))
        else:
            lines.append("(empty)")

        if historical_files:
            lines.append("The following files were uploaded in previous messages and are still available:")
            lines.append("")
            for file in historical_files:
                lines.extend(self._render_file_entry(file))

        lines.append("Use `read_file` on the path above for plain reads. For PDF / Office files a")
        lines.append("structured `*.docling.json` sibling is available with full DoclingDocument JSON")
        lines.append("(headings, tables with row/col spans, formulas, embedded pictures, page bboxes).")
        lines.append("For very large spreadsheets prefer DuckDB `read_xlsx(path, sheet=, range=)` or")
        lines.append("python-calamine over `pd.read_excel`. For huge .docx / .pptx, stream via")
        lines.append("`zipfile` + `lxml.etree.iterparse` rather than loading into context.")
        lines.append("</uploaded_files>")

        return "\n".join(lines)

    @staticmethod
    def _render_file_entry(file: dict[str, Any]) -> list[str]:
        """Render one file's lines for the ``<uploaded_files>`` block.

        Includes a one-line structural summary and the docling sibling
        path when a ``docling_summary`` is attached to the file metadata
        (set by uploads.py at upload time, or hydrated from the on-disk
        sidecar by ``before_agent``).
        """
        size_kb = file["size"] / 1024
        size_str = f"{size_kb:.1f} KB" if size_kb < 1024 else f"{size_kb / 1024:.1f} MB"

        summary = file.get("docling_summary") or {}
        structure_inline = format_summary_inline(summary) if summary else ""
        suffix = f" [{structure_inline}]" if structure_inline else ""

        lines = [f"- {file['filename']} ({size_str}){suffix}"]
        lines.append(f"  Path: {file['path']}")
        if file.get("docling_json_virtual_path"):
            lines.append(f"  Structured: {file['docling_json_virtual_path']}")

        sections = summary.get("sections")
        if sections:
            preview = " | ".join(s.get("text", "") for s in sections[:6] if s.get("text"))
            if preview:
                lines.append(f"  Sections: {preview}")
        sheets = summary.get("sheets")
        if sheets:
            preview = ", ".join(f"{s.get('name')} ({s.get('rows')}×{s.get('cols')})" for s in sheets[:6])
            lines.append(f"  Sheets: {preview}")

        lines.append("")
        return lines

    def _files_from_kwargs(self, message: HumanMessage, uploads_dir: Path | None = None) -> list[dict] | None:
        """Extract file info from message additional_kwargs.files.

        The frontend sends uploaded file metadata in additional_kwargs.files
        after a successful upload. Each entry has: filename, size (bytes),
        path (virtual path), status.

        Args:
            message: The human message to inspect.
            uploads_dir: Physical uploads directory used to verify file existence.
                         When provided, entries whose files no longer exist are skipped.

        Returns:
            List of file dicts with virtual paths, or None if the field is absent or empty.
        """
        kwargs_files = (message.additional_kwargs or {}).get("files")
        if not isinstance(kwargs_files, list) or not kwargs_files:
            return None

        files = []
        for f in kwargs_files:
            if not isinstance(f, dict):
                continue
            filename = f.get("filename") or ""
            if not filename or Path(filename).name != filename:
                continue
            if uploads_dir is not None and not (uploads_dir / filename).is_file():
                continue
            entry = {
                "filename": filename,
                "size": int(f.get("size") or 0),
                "path": f"/mnt/user-data/uploads/{filename}",
                "extension": Path(filename).suffix,
            }
            self._attach_docling_metadata(entry, uploads_dir, raw=f)
            files.append(entry)
        return files if files else None

    @staticmethod
    def _attach_docling_metadata(
        entry: dict[str, Any],
        uploads_dir: Path | None,
        *,
        raw: dict[str, Any] | None = None,
    ) -> None:
        """Hydrate the docling sidecar metadata onto a file entry in place.

        Prefers the summary already attached to ``raw`` (passed back by
        the frontend after upload) but falls back to reading the on-disk
        ``.docling.summary.json`` so historical uploads also get the
        structured hint.
        """
        if uploads_dir is None:
            return

        physical_path = uploads_dir / entry["filename"]
        sibling_json = docling_json_path(physical_path)
        if sibling_json.is_file():
            entry["docling_json_virtual_path"] = f"/mnt/user-data/uploads/{sibling_json.name}"

        summary: dict[str, Any] | None = None
        if raw is not None and isinstance(raw.get("docling_summary"), dict):
            summary = raw["docling_summary"]
        if summary is None:
            summary = load_summary(physical_path)
        if summary is not None:
            entry["docling_summary"] = summary

    @override
    def before_agent(self, state: UploadsMiddlewareState, runtime: Runtime) -> dict | None:
        """Inject uploaded files information before agent execution.

        New files come from the current message's additional_kwargs.files.
        Historical files are scanned from the thread's uploads directory,
        excluding the new ones.

        Prepends <uploaded_files> context to the last human message content.
        The original additional_kwargs (including files metadata) is preserved
        on the updated message so the frontend can read it from the stream.

        Args:
            state: Current agent state.
            runtime: Runtime context containing thread_id.

        Returns:
            State updates including uploaded files list.
        """
        messages = list(state.get("messages", []))
        if not messages:
            return None

        last_message_index = len(messages) - 1
        last_message = messages[last_message_index]

        if not isinstance(last_message, HumanMessage):
            return None

        # Resolve uploads directory for existence checks
        thread_id = runtime.context.get("thread_id")
        uploads_dir = self._paths.sandbox_uploads_dir(thread_id) if thread_id else None

        # Get newly uploaded files from the current message's additional_kwargs.files
        new_files = self._files_from_kwargs(last_message, uploads_dir) or []

        # Collect historical files from the uploads directory (all except the new ones).
        # Skip docling sidecars — they're derived artifacts, not user uploads.
        new_filenames = {f["filename"] for f in new_files}
        historical_files: list[dict] = []
        if uploads_dir and uploads_dir.exists():
            for file_path in sorted(uploads_dir.iterdir()):
                if not file_path.is_file() or file_path.name in new_filenames:
                    continue
                if is_derived_artifact(file_path):
                    continue
                stat = file_path.stat()
                entry: dict[str, Any] = {
                    "filename": file_path.name,
                    "size": stat.st_size,
                    "path": f"/mnt/user-data/uploads/{file_path.name}",
                    "extension": file_path.suffix,
                }
                self._attach_docling_metadata(entry, uploads_dir)
                historical_files.append(entry)

        if not new_files and not historical_files:
            return None

        logger.debug(f"New files: {[f['filename'] for f in new_files]}, historical: {[f['filename'] for f in historical_files]}")

        # Create files message and prepend to the last human message content
        files_message = self._create_files_message(new_files, historical_files)

        # Extract original content - handle both string and list formats
        original_content = ""
        if isinstance(last_message.content, str):
            original_content = last_message.content
        elif isinstance(last_message.content, list):
            text_parts = []
            for block in last_message.content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text_parts.append(block.get("text", ""))
            original_content = "\n".join(text_parts)

        # Create new message with combined content.
        # Preserve additional_kwargs (including files metadata) so the frontend
        # can read structured file info from the streamed message.
        updated_message = HumanMessage(
            content=f"{files_message}\n\n{original_content}",
            id=last_message.id,
            additional_kwargs=last_message.additional_kwargs,
        )

        messages[last_message_index] = updated_message

        return {
            "uploaded_files": new_files,
            "messages": messages,
        }
