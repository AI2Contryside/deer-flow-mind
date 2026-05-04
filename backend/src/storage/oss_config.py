"""Aliyun OSS configuration for deer-flow-mind.

Mirrors the Go-side `internal/oss/config.go` so object-key conventions and
bucket assignments stay in lockstep across services. Secrets (AK/SK) come
from `oss.access_key_id` / `oss.access_key_secret` in yaml — typically
rendered from a KMS Secrets Manager reference (kms://...) at config-load
time. As a local-dev fallback the env vars ALIYUN_OSS_ACCESS_KEY_ID /
_SECRET are still honoured when the yaml fields are empty.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

# Env var names. Both must be set or load_oss_config_from_dict() raises.
ENV_ACCESS_KEY_ID = "ALIYUN_OSS_ACCESS_KEY_ID"
ENV_ACCESS_KEY_SECRET = "ALIYUN_OSS_ACCESS_KEY_SECRET"

# Defaults match the Go side so behaviour is identical when both pick up the
# same `oss:` block.
DEFAULT_SIGNED_URL_TTL_SECONDS = 3600
DEFAULT_MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MiB
DEFAULT_AVATAR_MAX_BYTES = 5 * 1024 * 1024  # 5 MiB


@dataclass(frozen=True)
class OssConfig:
    """Resolved OSS configuration. Immutable after construction."""

    endpoint: str
    region: str
    sys_bucket: str
    chat_bucket: str
    signed_url_ttl_seconds: int = DEFAULT_SIGNED_URL_TTL_SECONDS
    max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES
    avatar_max_bytes: int = DEFAULT_AVATAR_MAX_BYTES

    access_key_id: str = field(repr=False, default="")
    access_key_secret: str = field(repr=False, default="")

    def public_host(self, bucket: str) -> str:
        """Return e.g. 'trademind-sys.oss-cn-hangzhou.aliyuncs.com'."""
        return f"{bucket}.{self.endpoint}"


def load_oss_config_from_dict(data: dict[str, Any]) -> OssConfig:
    """Build an OssConfig from a parsed `oss:` dict + env vars.

    Raises:
        ValueError: when any required field is missing.
    """
    endpoint = (data.get("endpoint") or "").strip()
    region = (data.get("region") or "").strip()
    sys_bucket = (data.get("sys_bucket") or "").strip()
    chat_bucket = (data.get("chat_bucket") or "").strip()

    signed_ttl = int(data.get("signed_url_ttl_seconds") or DEFAULT_SIGNED_URL_TTL_SECONDS)
    max_upload = int(data.get("max_upload_bytes") or DEFAULT_MAX_UPLOAD_BYTES)
    avatar_max = int(data.get("avatar_max_bytes") or DEFAULT_AVATAR_MAX_BYTES)

    ak = (data.get("access_key_id") or "").strip() or (os.getenv(ENV_ACCESS_KEY_ID) or "").strip()
    sk = (data.get("access_key_secret") or "").strip() or (os.getenv(ENV_ACCESS_KEY_SECRET) or "").strip()

    missing: list[str] = []
    if not endpoint:
        missing.append("oss.endpoint")
    if not region:
        missing.append("oss.region")
    if not sys_bucket:
        missing.append("oss.sys_bucket")
    if not chat_bucket:
        missing.append("oss.chat_bucket")
    if not ak:
        missing.append(f"oss.access_key_id (yaml or env {ENV_ACCESS_KEY_ID})")
    if not sk:
        missing.append(f"oss.access_key_secret (yaml or env {ENV_ACCESS_KEY_SECRET})")
    if missing:
        raise ValueError(f"oss config incomplete: missing {', '.join(missing)}")

    return OssConfig(
        endpoint=endpoint,
        region=region,
        sys_bucket=sys_bucket,
        chat_bucket=chat_bucket,
        signed_url_ttl_seconds=signed_ttl,
        max_upload_bytes=max_upload,
        avatar_max_bytes=avatar_max,
        access_key_id=ak,
        access_key_secret=sk,
    )
