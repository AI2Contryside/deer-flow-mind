"""Upload router for handling file uploads.

Files are written to thread-scoped local disk (so the local sandbox keeps
working unchanged) AND mirrored to Aliyun OSS under
``tenants/<tenant>/threads/<thread>/uploads/<key>``. A small JSON manifest
co-located with the OSS uploads tracks the mapping from filename → OSS key
so the artifacts router and uploads middleware can hydrate files in other
service replicas without depending on the host filesystem.

OSS mirroring is best-effort: if no ``X-Tenant-ID`` header is present or the
OSS singleton is unavailable, the route still succeeds with local-only
storage and the response simply omits ``oss_key`` / ``signed_url`` fields.
"""

import json
import logging
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, File, Header, HTTPException, UploadFile
from pydantic import BaseModel

from src.config.paths import VIRTUAL_PATH_PREFIX, get_paths
from src.sandbox.sandbox_provider import get_sandbox_provider
from src.storage import (
    ACL_PRIVATE,
    Storage,
    chat_thread_uploads_manifest_key,
    get_default,
    sanitize_filename,
    short_uuid,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/threads/{thread_id}/uploads", tags=["uploads"])

# File extensions that should be converted to markdown
CONVERTIBLE_EXTENSIONS = {
    ".pdf",
    ".ppt",
    ".pptx",
    ".xls",
    ".xlsx",
    ".doc",
    ".docx",
}


class UploadResponse(BaseModel):
    """Response model for file upload."""

    success: bool
    files: list[dict[str, str]]
    message: str


def get_uploads_dir(thread_id: str) -> Path:
    """Get the uploads directory for a thread.

    Args:
        thread_id: The thread ID.

    Returns:
        Path to the uploads directory.
    """
    base_dir = get_paths().sandbox_uploads_dir(thread_id)
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir


def _try_get_storage() -> Storage | None:
    """Best-effort access to the OSS singleton; return None if unavailable.

    OSS is optional for local-only deployments and during tests, so we never
    raise from here — the caller falls back to local-only behaviour.
    """
    try:
        return get_default()
    except Exception as exc:
        logger.warning("OSS unavailable, skipping mirror: %s", exc)
        return None


def _read_manifest(storage: Storage, bucket: str, key: str) -> dict[str, Any]:
    """Load the per-thread upload manifest from OSS, or return an empty stub."""
    try:
        if not storage.object_exists(bucket, key):
            return {"files": []}
        raw = storage.get_object_bytes(bucket, key)
        return json.loads(raw.decode("utf-8"))
    except Exception as exc:
        logger.warning("Failed to read upload manifest %s/%s: %s", bucket, key, exc)
        return {"files": []}


def _write_manifest(storage: Storage, bucket: str, key: str, manifest: dict[str, Any]) -> None:
    """Persist the manifest. Failures are logged but do not abort the upload."""
    try:
        body = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
        storage.put_object(bucket, key, body, content_type="application/json", acl=ACL_PRIVATE)
    except Exception as exc:
        logger.warning("Failed to write upload manifest %s/%s: %s", bucket, key, exc)


async def convert_file_to_markdown(file_path: Path) -> Path | None:
    """Convert a file to markdown using markitdown.

    Args:
        file_path: Path to the file to convert.

    Returns:
        Path to the markdown file if conversion was successful, None otherwise.
    """
    try:
        from markitdown import MarkItDown

        md = MarkItDown()
        result = md.convert(str(file_path))

        # Save as .md file with same name
        md_path = file_path.with_suffix(".md")
        md_path.write_text(result.text_content, encoding="utf-8")

        logger.info(f"Converted {file_path.name} to markdown: {md_path.name}")
        return md_path
    except Exception as e:
        logger.error(f"Failed to convert {file_path.name} to markdown: {e}")
        return None


@router.post("", response_model=UploadResponse)
async def upload_files(
    thread_id: str,
    files: list[UploadFile] = File(...),
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-ID")] = None,
) -> UploadResponse:
    """Upload multiple files to a thread's uploads directory.

    For PDF, PPT, Excel, and Word files, they will be converted to markdown using markitdown.
    All files (original and converted) are saved to /mnt/user-data/uploads.
    When the ``X-Tenant-ID`` header is present, files are also mirrored to OSS
    under ``tenants/<tenant>/threads/<thread>/uploads/`` and the per-thread
    manifest is updated so other replicas can hydrate the file on demand.

    Args:
        thread_id: The thread ID to upload files to.
        files: List of files to upload.
        x_tenant_id: Tenant identifier propagated by the gateway (optional).

    Returns:
        Upload response with success status and file information.
    """
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")

    uploads_dir = get_uploads_dir(thread_id)
    paths = get_paths()
    uploaded_files = []

    sandbox_provider = get_sandbox_provider()
    sandbox_id = sandbox_provider.acquire(thread_id)
    sandbox = sandbox_provider.get(sandbox_id)

    storage = _try_get_storage()
    tenant_id = (x_tenant_id or "").strip()
    use_oss = bool(storage and tenant_id)
    manifest_bucket = storage.config.chat_bucket if use_oss else ""
    manifest_key = chat_thread_uploads_manifest_key(tenant_id, thread_id) if use_oss else ""
    manifest = _read_manifest(storage, manifest_bucket, manifest_key) if use_oss else {"files": []}

    for file in files:
        if not file.filename:
            continue

        try:
            # Normalize filename to prevent path traversal
            safe_filename = Path(file.filename).name
            if not safe_filename or safe_filename in {".", ".."} or "/" in safe_filename or "\\" in safe_filename:
                logger.warning(f"Skipping file with unsafe filename: {file.filename!r}")
                continue

            content = await file.read()
            file_path = uploads_dir / safe_filename
            file_path.write_bytes(content)

            # Build relative path from backend root
            relative_path = str(paths.sandbox_uploads_dir(thread_id) / safe_filename)
            virtual_path = f"{VIRTUAL_PATH_PREFIX}/uploads/{safe_filename}"

            # Keep local sandbox source of truth in thread-scoped host storage.
            # For non-local sandboxes, also sync to virtual path for runtime visibility.
            if sandbox_id != "local":
                sandbox.update_file(virtual_path, content)

            file_info: dict[str, Any] = {
                "filename": safe_filename,
                "size": str(len(content)),
                "path": relative_path,  # Actual filesystem path (relative to backend/)
                "virtual_path": virtual_path,  # Path for Agent in sandbox
                "artifact_url": f"/api/threads/{thread_id}/artifacts/mnt/user-data/uploads/{safe_filename}",  # HTTP URL
            }

            # ── OSS mirror (best-effort) ─────────────────────────────────
            if use_oss:
                oss_key = f"tenants/{tenant_id}/threads/{thread_id}/uploads/{short_uuid()}-{sanitize_filename(safe_filename)}"
                try:
                    storage.put_object(
                        manifest_bucket,
                        oss_key,
                        content,
                        content_type=file.content_type or "application/octet-stream",
                        acl=ACL_PRIVATE,
                    )
                    file_info["oss_key"] = oss_key
                    file_info["oss_bucket"] = manifest_bucket
                    file_info["signed_url"] = storage.sign_url(manifest_bucket, oss_key, storage.config.signed_url_ttl_seconds)
                    manifest = _record_in_manifest(
                        manifest,
                        {
                            "filename": safe_filename,
                            "oss_key": oss_key,
                            "size": len(content),
                            "content_type": file.content_type or "application/octet-stream",
                        },
                    )
                except Exception as exc:
                    logger.warning("OSS mirror failed for %s: %s", safe_filename, exc)

            logger.info(f"Saved file: {safe_filename} ({len(content)} bytes) to {relative_path}")

            # Check if file should be converted to markdown
            file_ext = file_path.suffix.lower()
            if file_ext in CONVERTIBLE_EXTENSIONS:
                md_path = await convert_file_to_markdown(file_path)
                if md_path:
                    md_relative_path = str(paths.sandbox_uploads_dir(thread_id) / md_path.name)
                    md_virtual_path = f"{VIRTUAL_PATH_PREFIX}/uploads/{md_path.name}"

                    if sandbox_id != "local":
                        sandbox.update_file(md_virtual_path, md_path.read_bytes())

                    file_info["markdown_file"] = md_path.name
                    file_info["markdown_path"] = md_relative_path
                    file_info["markdown_virtual_path"] = md_virtual_path
                    file_info["markdown_artifact_url"] = f"/api/threads/{thread_id}/artifacts/mnt/user-data/uploads/{md_path.name}"

                    if use_oss:
                        md_oss_key = f"tenants/{tenant_id}/threads/{thread_id}/uploads/{short_uuid()}-{sanitize_filename(md_path.name)}"
                        try:
                            storage.put_object(
                                manifest_bucket,
                                md_oss_key,
                                md_path.read_bytes(),
                                content_type="text/markdown; charset=utf-8",
                                acl=ACL_PRIVATE,
                            )
                            file_info["markdown_oss_key"] = md_oss_key
                            manifest = _record_in_manifest(
                                manifest,
                                {
                                    "filename": md_path.name,
                                    "oss_key": md_oss_key,
                                    "size": md_path.stat().st_size,
                                    "content_type": "text/markdown; charset=utf-8",
                                    "derived_from": safe_filename,
                                },
                            )
                        except Exception as exc:
                            logger.warning("OSS mirror failed for markdown %s: %s", md_path.name, exc)

            uploaded_files.append(file_info)

        except Exception as e:
            logger.error(f"Failed to upload {file.filename}: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to upload {file.filename}: {str(e)}")

    if use_oss:
        _write_manifest(storage, manifest_bucket, manifest_key, manifest)

    return UploadResponse(
        success=True,
        files=uploaded_files,
        message=f"Successfully uploaded {len(uploaded_files)} file(s)",
    )


def _record_in_manifest(manifest: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
    """Return a new manifest with `entry` inserted/replaced by filename.

    Keeps the structure immutable from the caller's perspective so a partial
    failure in subsequent code paths can't leave a half-written manifest.
    """
    files = list(manifest.get("files") or [])
    files = [f for f in files if f.get("filename") != entry["filename"]]
    files.append(entry)
    return {**manifest, "files": files}


@router.get("/list", response_model=dict)
async def list_uploaded_files(thread_id: str) -> dict:
    """List all files in a thread's uploads directory.

    Args:
        thread_id: The thread ID to list files for.

    Returns:
        Dictionary containing list of files with their metadata.
    """
    uploads_dir = get_uploads_dir(thread_id)

    if not uploads_dir.exists():
        return {"files": [], "count": 0}

    files = []
    for file_path in sorted(uploads_dir.iterdir()):
        if file_path.is_file():
            stat = file_path.stat()
            relative_path = str(get_paths().sandbox_uploads_dir(thread_id) / file_path.name)
            files.append(
                {
                    "filename": file_path.name,
                    "size": stat.st_size,
                    "path": relative_path,  # Actual filesystem path
                    "virtual_path": f"{VIRTUAL_PATH_PREFIX}/uploads/{file_path.name}",  # Path for Agent in sandbox
                    "artifact_url": f"/api/threads/{thread_id}/artifacts/mnt/user-data/uploads/{file_path.name}",  # HTTP URL
                    "extension": file_path.suffix,
                    "modified": stat.st_mtime,
                }
            )

    return {"files": files, "count": len(files)}


@router.delete("/{filename}")
async def delete_uploaded_file(
    thread_id: str,
    filename: str,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-ID")] = None,
) -> dict:
    """Delete a file from a thread's uploads directory.

    Also attempts to remove the corresponding OSS object and manifest entry
    when the tenant header is present.

    Args:
        thread_id: The thread ID.
        filename: The filename to delete.
        x_tenant_id: Tenant identifier (optional).

    Returns:
        Success message.
    """
    uploads_dir = get_uploads_dir(thread_id)
    file_path = uploads_dir / filename

    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {filename}")

    # Security check: ensure the path is within the uploads directory
    try:
        file_path.resolve().relative_to(uploads_dir.resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied")

    try:
        file_path.unlink()
        logger.info(f"Deleted file: {filename}")
    except Exception as e:
        logger.error(f"Failed to delete {filename}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to delete {filename}: {str(e)}")

    # Best-effort OSS cleanup
    storage = _try_get_storage()
    tenant_id = (x_tenant_id or "").strip()
    if storage and tenant_id:
        bucket = storage.config.chat_bucket
        manifest_key = chat_thread_uploads_manifest_key(tenant_id, thread_id)
        manifest = _read_manifest(storage, bucket, manifest_key)
        retained = []
        for entry in manifest.get("files") or []:
            if entry.get("filename") == filename:
                try:
                    storage.delete_object(bucket, entry.get("oss_key", ""))
                except Exception as exc:
                    logger.warning("OSS delete failed for %s: %s", entry.get("oss_key"), exc)
            else:
                retained.append(entry)
        _write_manifest(storage, bucket, manifest_key, {**manifest, "files": retained})

    return {"success": True, "message": f"Deleted {filename}"}
