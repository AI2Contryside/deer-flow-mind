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

For PDF / Office formats the router runs ``docling`` to produce a structured
``DoclingDocument`` JSON sibling (``<stem>.docling.json``) plus a tiny
summary sibling (``<stem>.docling.summary.json``). Both are mirrored to OSS
when tenant context is present. The agent reads the structured JSON when it
needs cell/heading/table fidelity, or writes Python (openpyxl, calamine,
DuckDB) directly against the original for very large files.
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
from src.utils.document_extract import EXTRACTABLE_EXTENSIONS, extract_with_docling, is_derived_artifact

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/threads/{thread_id}/uploads", tags=["uploads"])

# Re-export under the legacy name so existing call sites (embedded client,
# tests) continue to work after the markitdown → docling migration.
CONVERTIBLE_EXTENSIONS = EXTRACTABLE_EXTENSIONS


class UploadResponse(BaseModel):
    """Response model for file upload.

    File entries carry mostly string values (paths, URLs, sizes serialised
    as strings) but also a structured ``docling_summary`` dict for office
    files, so the dict value type must be ``Any``.
    """

    success: bool
    files: list[dict[str, Any]]
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


async def extract_structured(file_path: Path) -> tuple[Path | None, Path | None, dict[str, Any] | None]:
    """Run docling on ``file_path`` and write the JSON + summary sidecars.

    Returns ``(docling_json_path, summary_path, summary_dict)``. All three
    are ``None`` when docling is not installed or extraction failed —
    callers fall back to original-file-only behaviour without raising.
    """
    return await extract_with_docling(file_path)


@router.post("", response_model=UploadResponse)
async def upload_files(
    thread_id: str,
    files: list[UploadFile] = File(...),
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-ID")] = None,
) -> UploadResponse:
    """Upload multiple files to a thread's uploads directory.

    For PDF, PPT, Excel, and Word files, ``docling`` produces a structured
    ``DoclingDocument`` JSON sibling (``<stem>.docling.json``) plus a tiny
    summary sibling (``<stem>.docling.summary.json``). All files (original
    and the docling sidecars) are saved to /mnt/user-data/uploads.
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

            # Run docling on PDF / Office formats to produce structured JSON
            # plus a tiny summary the lead agent can scan before reading.
            file_ext = file_path.suffix.lower()
            if file_ext in EXTRACTABLE_EXTENSIONS:
                docling_json_path, summary_path, summary = await extract_structured(file_path)
                manifest = _mirror_docling_artifacts(
                    docling_json_path=docling_json_path,
                    summary_path=summary_path,
                    summary=summary,
                    file_info=file_info,
                    thread_id=thread_id,
                    paths=paths,
                    sandbox_id=sandbox_id,
                    sandbox=sandbox,
                    storage=storage,
                    use_oss=use_oss,
                    tenant_id=tenant_id,
                    manifest=manifest,
                    manifest_bucket=manifest_bucket,
                    safe_filename=safe_filename,
                )

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


def _mirror_docling_artifacts(
    *,
    docling_json_path: Path | None,
    summary_path: Path | None,
    summary: dict[str, Any] | None,
    file_info: dict[str, Any],
    thread_id: str,
    paths: Any,
    sandbox_id: str,
    sandbox: Any,
    storage: Storage | None,
    use_oss: bool,
    tenant_id: str,
    manifest: dict[str, Any],
    manifest_bucket: str,
    safe_filename: str,
) -> dict[str, Any]:
    """Sync docling sidecars to the sandbox + OSS and enrich ``file_info``.

    Returns the (possibly updated) tenant upload manifest. When extraction
    failed (any of the docling outputs is ``None``), this is a no-op and
    the caller continues with original-file-only behaviour.
    """
    if docling_json_path is None or summary_path is None or summary is None:
        return manifest

    file_info["docling_summary"] = summary

    sidecars: list[tuple[Path, str]] = [
        (docling_json_path, "application/json"),
        (summary_path, "application/json"),
    ]

    for sidecar_path, content_type in sidecars:
        sidecar_relative = str(paths.sandbox_uploads_dir(thread_id) / sidecar_path.name)
        sidecar_virtual = f"{VIRTUAL_PATH_PREFIX}/uploads/{sidecar_path.name}"

        if sandbox_id != "local":
            sandbox.update_file(sidecar_virtual, sidecar_path.read_bytes())

        # Two well-known shapes so callers can address either sidecar by
        # role without scanning the directory.
        if sidecar_path == docling_json_path:
            file_info["docling_json_file"] = sidecar_path.name
            file_info["docling_json_path"] = sidecar_relative
            file_info["docling_json_virtual_path"] = sidecar_virtual
            file_info["docling_json_artifact_url"] = f"/api/threads/{thread_id}/artifacts/mnt/user-data/uploads/{sidecar_path.name}"
        else:
            file_info["docling_summary_file"] = sidecar_path.name
            file_info["docling_summary_path"] = sidecar_relative
            file_info["docling_summary_virtual_path"] = sidecar_virtual
            file_info["docling_summary_artifact_url"] = f"/api/threads/{thread_id}/artifacts/mnt/user-data/uploads/{sidecar_path.name}"

        if use_oss and storage is not None:
            sidecar_oss_key = f"tenants/{tenant_id}/threads/{thread_id}/uploads/{short_uuid()}-{sanitize_filename(sidecar_path.name)}"
            try:
                storage.put_object(
                    manifest_bucket,
                    sidecar_oss_key,
                    sidecar_path.read_bytes(),
                    content_type=content_type,
                    acl=ACL_PRIVATE,
                )
                if sidecar_path == docling_json_path:
                    file_info["docling_json_oss_key"] = sidecar_oss_key
                else:
                    file_info["docling_summary_oss_key"] = sidecar_oss_key
                manifest = _record_in_manifest(
                    manifest,
                    {
                        "filename": sidecar_path.name,
                        "oss_key": sidecar_oss_key,
                        "size": sidecar_path.stat().st_size,
                        "content_type": content_type,
                        "derived_from": safe_filename,
                    },
                )
            except Exception as exc:
                logger.warning("OSS mirror failed for docling sidecar %s: %s", sidecar_path.name, exc)

    return manifest


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
        if not file_path.is_file():
            continue
        # Hide docling sidecars from the user-facing listing — they are
        # derived artifacts the agent reads on demand, not "uploaded files".
        if is_derived_artifact(file_path):
            continue
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
