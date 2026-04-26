"""Thin wrapper around the Aliyun OSS Python SDK (oss2).

Defines a tight ``Storage`` Protocol so callers can substitute fakes in tests
without importing oss2. Production code uses ``OssClient`` which holds a
single ``oss2.Auth`` and lazily caches per-bucket ``oss2.Bucket`` handles.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any, BinaryIO, Protocol, runtime_checkable

import oss2  # type: ignore[import-untyped]

from src.storage.oss_config import OssConfig

ACL_PRIVATE = "private"
ACL_PUBLIC_READ = "public-read"


@runtime_checkable
class Storage(Protocol):
    """Minimal surface needed by uploads/artifacts/middleware code."""

    def put_object(self, bucket: str, key: str, body: BinaryIO | bytes, content_type: str = "", acl: str = "") -> None: ...

    def sign_url(self, bucket: str, key: str, ttl_seconds: int) -> str: ...

    def public_url(self, bucket: str, key: str) -> str: ...

    def delete_object(self, bucket: str, key: str) -> None: ...

    def head_object(self, bucket: str, key: str) -> tuple[int, str]: ...

    def download_to_path(self, bucket: str, key: str, local_path: str | Path) -> None: ...

    def get_object_bytes(self, bucket: str, key: str) -> bytes: ...

    def object_exists(self, bucket: str, key: str) -> bool: ...

    @property
    def config(self) -> OssConfig: ...


class OssClient:
    """Default ``Storage`` implementation backed by oss2."""

    def __init__(self, config: OssConfig) -> None:
        self._config = config
        self._auth = oss2.Auth(config.access_key_id, config.access_key_secret)
        self._buckets: dict[str, oss2.Bucket] = {}

    @property
    def config(self) -> OssConfig:
        return self._config

    def _bucket(self, name: str) -> oss2.Bucket:
        bkt = self._buckets.get(name)
        if bkt is None:
            bkt = oss2.Bucket(self._auth, f"https://{self._config.endpoint}", name)
            self._buckets[name] = bkt
        return bkt

    def put_object(self, bucket: str, key: str, body: BinaryIO | bytes, content_type: str = "", acl: str = "") -> None:
        headers: dict[str, Any] = {}
        if content_type:
            headers["Content-Type"] = content_type
        if acl:
            # oss2 maps ACL via x-oss-object-acl header.
            headers["x-oss-object-acl"] = acl
        self._bucket(bucket).put_object(key, body, headers=headers)

    def sign_url(self, bucket: str, key: str, ttl_seconds: int) -> str:
        if ttl_seconds < 60:
            ttl_seconds = 60
        # slash_safe=True keeps `/` unencoded in the path so the URL works in
        # browser <img> / <a> tags without further decoding.
        return self._bucket(bucket).sign_url("GET", key, ttl_seconds, slash_safe=True)

    def public_url(self, bucket: str, key: str) -> str:
        return f"https://{self._config.public_host(bucket)}/{key}"

    def delete_object(self, bucket: str, key: str) -> None:
        self._bucket(bucket).delete_object(key)

    def head_object(self, bucket: str, key: str) -> tuple[int, str]:
        meta = self._bucket(bucket).head_object(key)
        size = int(meta.headers.get("Content-Length", "0") or 0)
        ctype = meta.headers.get("Content-Type", "") or ""
        return size, ctype

    def download_to_path(self, bucket: str, key: str, local_path: str | Path) -> None:
        p = Path(local_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        self._bucket(bucket).get_object_to_file(key, str(p))

    def get_object_bytes(self, bucket: str, key: str) -> bytes:
        result = self._bucket(bucket).get_object(key)
        return result.read()

    def object_exists(self, bucket: str, key: str) -> bool:
        return bool(self._bucket(bucket).object_exists(key))


# ─────────────────────────── singleton plumbing ───────────────────────────

_default: Storage | None = None


def set_default(storage: Storage) -> None:
    """Install a process-wide default Storage. Used by tests too."""
    global _default
    _default = storage


def get_default() -> Storage:
    """Return the installed default. Initializes from app config on first use."""
    global _default
    if _default is None:
        _default = _build_from_app_config()
    return _default


def _build_from_app_config() -> Storage:
    # Imported lazily so circular imports don't bite at module-load time.
    from src.config.app_config import get_app_config
    from src.storage.oss_config import load_oss_config_from_dict

    cfg = get_app_config()
    raw = getattr(cfg, "oss", None)
    if raw is None:
        # AppConfig is `extra="allow"` so unknown keys land on the model. If
        # `oss:` is missing entirely, surface a clear error.
        raise ValueError("oss: missing `oss:` section in config.yaml")
    if hasattr(raw, "model_dump"):
        raw = raw.model_dump()
    return OssClient(load_oss_config_from_dict(raw))


# Convenience for tests that need an in-memory fake.
class InMemoryStorage:
    """Test double satisfying the Storage protocol."""

    def __init__(self, config: OssConfig | None = None) -> None:
        self._objects: dict[tuple[str, str], bytes] = {}
        self._content_types: dict[tuple[str, str], str] = {}
        self._config = config or OssConfig(
            endpoint="oss-test.local",
            region="cn-test",
            sys_bucket="trademind-sys",
            chat_bucket="trademind-chat-session",
            access_key_id="ak",
            access_key_secret="sk",
        )

    @property
    def config(self) -> OssConfig:
        return self._config

    def put_object(self, bucket: str, key: str, body: BinaryIO | bytes, content_type: str = "", acl: str = "") -> None:
        if isinstance(body, (bytes, bytearray)):
            data = bytes(body)
        else:
            data = body.read()
        self._objects[(bucket, key)] = data
        self._content_types[(bucket, key)] = content_type or "application/octet-stream"

    def sign_url(self, bucket: str, key: str, ttl_seconds: int) -> str:
        return f"https://signed/{bucket}/{key}?ttl={ttl_seconds}"

    def public_url(self, bucket: str, key: str) -> str:
        return f"https://public/{bucket}/{key}"

    def delete_object(self, bucket: str, key: str) -> None:
        self._objects.pop((bucket, key), None)
        self._content_types.pop((bucket, key), None)

    def head_object(self, bucket: str, key: str) -> tuple[int, str]:
        if (bucket, key) not in self._objects:
            raise FileNotFoundError(f"{bucket}/{key}")
        return len(self._objects[(bucket, key)]), self._content_types.get((bucket, key), "application/octet-stream")

    def download_to_path(self, bucket: str, key: str, local_path: str | Path) -> None:
        if (bucket, key) not in self._objects:
            raise FileNotFoundError(f"{bucket}/{key}")
        p = Path(local_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(self._objects[(bucket, key)])

    def get_object_bytes(self, bucket: str, key: str) -> bytes:
        if (bucket, key) not in self._objects:
            raise FileNotFoundError(f"{bucket}/{key}")
        return self._objects[(bucket, key)]

    def object_exists(self, bucket: str, key: str) -> bool:
        return (bucket, key) in self._objects

    # Helper for tests
    def list_keys(self, bucket: str) -> list[str]:
        return [k for (b, k) in self._objects.keys() if b == bucket]

    def get_text(self, bucket: str, key: str) -> str:
        return self._objects[(bucket, key)].decode("utf-8")

    def put_text(self, bucket: str, key: str, text: str) -> None:
        self._objects[(bucket, key)] = text.encode("utf-8")
        self._content_types[(bucket, key)] = "application/json"


# Re-export for callers that want a one-line import.
__all__ = [
    "ACL_PRIVATE",
    "ACL_PUBLIC_READ",
    "InMemoryStorage",
    "OssClient",
    "Storage",
    "get_default",
    "set_default",
]


def _read_all(stream: BinaryIO | bytes) -> bytes:
    """Helper used by some tests; not exported."""
    if isinstance(stream, (bytes, bytearray)):
        return bytes(stream)
    if hasattr(stream, "read"):
        return stream.read()  # type: ignore[no-any-return]
    raise TypeError(f"unsupported body type: {type(stream)!r}")


# Silence unused-import warning while keeping the helper available.
_ = io
