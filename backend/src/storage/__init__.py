"""OSS storage abstraction shared by gateway routers, middlewares, and tools."""

from src.storage.keys import (
    chat_artifact_key,
    chat_thread_uploads_manifest_key,
    chat_thread_uploads_prefix,
    chat_upload_key,
    sanitize_filename,
    short_uuid,
    tenant_prefix,
    thread_upload_key,
    user_prefix,
)
from src.storage.oss_client import (
    ACL_PRIVATE,
    ACL_PUBLIC_READ,
    InMemoryStorage,
    OssClient,
    Storage,
    get_default,
    set_default,
)
from src.storage.oss_config import (
    DEFAULT_AVATAR_MAX_BYTES,
    DEFAULT_MAX_UPLOAD_BYTES,
    DEFAULT_SIGNED_URL_TTL_SECONDS,
    ENV_ACCESS_KEY_ID,
    ENV_ACCESS_KEY_SECRET,
    OssConfig,
    load_oss_config_from_dict,
)

__all__ = [
    "ACL_PRIVATE",
    "ACL_PUBLIC_READ",
    "DEFAULT_AVATAR_MAX_BYTES",
    "DEFAULT_MAX_UPLOAD_BYTES",
    "DEFAULT_SIGNED_URL_TTL_SECONDS",
    "ENV_ACCESS_KEY_ID",
    "ENV_ACCESS_KEY_SECRET",
    "InMemoryStorage",
    "OssClient",
    "OssConfig",
    "Storage",
    "chat_artifact_key",
    "chat_thread_uploads_manifest_key",
    "chat_thread_uploads_prefix",
    "chat_upload_key",
    "get_default",
    "load_oss_config_from_dict",
    "sanitize_filename",
    "set_default",
    "short_uuid",
    "tenant_prefix",
    "thread_upload_key",
    "user_prefix",
]
