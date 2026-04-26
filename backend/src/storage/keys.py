"""Object-key builders for Aliyun OSS.

Mirrors the Go-side ``internal/oss/keys.go`` so signed-URL handlers on either
side authorize objects via the same string-prefix checks. All keys use forward
slashes regardless of OS.
"""

from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timezone

_FILENAME_CTRL_CHARS = re.compile(r"[\x00-\x1f]")


def short_uuid() -> str:
    """8-char hex prefix used to disambiguate uploads sharing a filename."""
    return uuid.uuid4().hex[:8]


def sanitize_filename(name: str) -> str:
    """Strip path separators and control chars; cap to 100 bytes.

    Matches the Go-side ``oss.SanitizeFilename`` so identical inputs produce
    identical sanitised outputs across the two services.
    """
    base = os.path.basename(name or "")
    if base in {"", ".", "/", "\\"}:
        return "file"
    cleaned = _FILENAME_CTRL_CHARS.sub("", base.replace("/", "_").replace("\\", "_"))
    if not cleaned:
        cleaned = "file"
    if len(cleaned.encode("utf-8")) > 100:
        # cap by character count to stay below the byte cap on ASCII inputs
        cleaned = cleaned[:100]
    return cleaned


def chat_upload_key(tenant_id: int | str, user_id: int | str, day: datetime | None, filename: str) -> str:
    """Object key for a chat user upload (`trademind-chat-session` bucket).

    ``day`` defaults to UTC now when None.
    """
    if day is None:
        day = datetime.now(timezone.utc)
    return "/".join(
        [
            "tenants",
            str(tenant_id),
            "users",
            str(user_id),
            day.astimezone(timezone.utc).strftime("%Y-%m-%d"),
            f"{short_uuid()}-{sanitize_filename(filename)}",
        ]
    )


def chat_artifact_key(tenant_id: int | str, thread_id: str, filename: str) -> str:
    """Object key for an AI-generated artifact (`trademind-chat-session`)."""
    return "/".join(
        [
            "tenants",
            str(tenant_id),
            "threads",
            thread_id,
            "outputs",
            sanitize_filename(filename),
        ]
    )


def chat_thread_uploads_prefix(tenant_id: int | str, thread_id: str) -> str:
    """Prefix where per-thread upload manifests live."""
    return f"tenants/{tenant_id}/threads/{thread_id}/uploads/"


def chat_thread_uploads_manifest_key(tenant_id: int | str, thread_id: str) -> str:
    """JSON manifest listing every upload that belongs to this thread."""
    return chat_thread_uploads_prefix(tenant_id, thread_id) + "_manifest.json"


def thread_upload_key(tenant_id: int | str, thread_id: str, filename: str) -> str:
    """Object key for a single thread upload."""
    return f"{chat_thread_uploads_prefix(tenant_id, thread_id)}{short_uuid()}-{sanitize_filename(filename)}"


def tenant_prefix(tenant_id: int | str) -> str:
    return f"tenants/{tenant_id}/"


def user_prefix(tenant_id: int | str, user_id: int | str) -> str:
    return f"{tenant_prefix(tenant_id)}users/{user_id}/"
