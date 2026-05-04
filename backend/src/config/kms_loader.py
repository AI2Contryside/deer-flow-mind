"""Aliyun KMS Secrets Manager resolver for DeerFlow configuration.

Configuration values shaped like ``kms://<secret-name>`` are looked up via
the shared KMS gateway (cn-hangzhou by default) using the standard Aliyun
credential chain (ECS RAM Role -> AK/SK env -> ~/.aliyun/credentials).
Plaintext values are cached in-process with a short TTL so config reads
don't hit KMS repeatedly.

Fail-closed semantics: when ``KMS_ENABLED=false`` is set in the environment
and a ``kms://`` reference is encountered, ``resolve_kms_value`` raises a
``RuntimeError`` so misconfiguration surfaces at startup rather than
silently leaking the placeholder string into a downstream client.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from typing import Protocol

REF_PREFIX = "kms://"

_ENV_ENABLED = "KMS_ENABLED"
_ENV_REGION = "KMS_REGION"
_ENV_ENDPOINT = "KMS_ENDPOINT"
_ENV_CACHE_TTL = "KMS_CACHE_TTL_SECONDS"

_DEFAULT_REGION = "cn-hangzhou"
_DEFAULT_TTL_SECONDS = 300  # 5 minutes


def is_kms_ref(value: object) -> bool:
    """Return True iff ``value`` is a ``kms://...`` reference string."""
    return isinstance(value, str) and value.startswith(REF_PREFIX)


def strip_kms_prefix(value: str) -> str:
    return value[len(REF_PREFIX) :] if value.startswith(REF_PREFIX) else value


@dataclass
class _CachedValue:
    plaintext: str
    expires_at: float


class SecretClient(Protocol):
    """Narrow surface KMSResolver needs from a KMS client.

    Defined as a Protocol so tests can pass a fake without importing the
    Aliyun SDK transport stack into the unit-test environment.
    """

    def get_secret_value(self, secret_name: str) -> str | None:  # pragma: no cover
        ...


class _AliyunKMSClient:
    """Production SDK adapter. Imports the SDK lazily on construction."""

    def __init__(self, region: str, endpoint: str) -> None:
        from alibabacloud_credentials.client import Client as CredentialClient
        from alibabacloud_kms20160120.client import Client as KmsClient
        from alibabacloud_tea_openapi import models as openapi_models

        credential = CredentialClient(None)  # default chain: RAM role -> env -> profile
        config = openapi_models.Config(credential=credential)
        config.endpoint = endpoint
        config.region_id = region
        self._client = KmsClient(config)

    def get_secret_value(self, secret_name: str) -> str | None:
        from alibabacloud_kms20160120 import models as kms_models

        request = kms_models.GetSecretValueRequest(secret_name=secret_name)
        response = self._client.get_secret_value(request)
        body = getattr(response, "body", None)
        if body is None:
            return None
        return getattr(body, "secret_data", None)


class KMSResolver:
    """Resolve ``kms://`` references against Aliyun KMS Secrets Manager.

    The resolver is process-wide and lazily initializes the SDK client on
    first use. Tests can inject a fake by passing ``client=...``; the
    public ``resolve`` method only calls ``client.get_secret_value(...)``.
    """

    def __init__(self, *, client=None, enabled: bool | None = None, ttl_seconds: int | None = None) -> None:
        self._client = client
        self._client_lock = threading.Lock()
        self._cache: dict[str, _CachedValue] = {}
        self._cache_lock = threading.Lock()

        if enabled is None:
            enabled = os.environ.get(_ENV_ENABLED, "true").strip().lower() != "false"
        self._enabled = enabled

        if ttl_seconds is None:
            raw = os.environ.get(_ENV_CACHE_TTL, "").strip()
            ttl_seconds = int(raw) if raw.isdigit() and int(raw) > 0 else _DEFAULT_TTL_SECONDS
        self._ttl = ttl_seconds

    def resolve(self, value: str) -> str:
        """Return the plaintext for a ``kms://`` reference; pass-through otherwise."""
        if not is_kms_ref(value):
            return value
        if not self._enabled:
            raise RuntimeError(
                f"KMS disabled (KMS_ENABLED=false) but reference {value!r} encountered. "
                "Set KMS_ENABLED=true or replace the reference with a plaintext value."
            )
        secret_name = strip_kms_prefix(value)
        if not secret_name:
            raise ValueError("KMS reference has empty secret name")

        cached = self._cache_get(secret_name)
        if cached is not None:
            return cached

        client = self._get_client()
        plaintext = client.get_secret_value(secret_name)
        if not plaintext:
            raise RuntimeError(f"KMS GetSecretValue returned empty body for {secret_name!r}")

        self._cache_put(secret_name, plaintext)
        return plaintext

    def resolve_in_config(self, config: object) -> object:
        """Recursively replace every ``kms://`` reference inside a nested dict/list."""
        if isinstance(config, str):
            return self.resolve(config) if is_kms_ref(config) else config
        if isinstance(config, dict):
            return {k: self.resolve_in_config(v) for k, v in config.items()}
        if isinstance(config, list):
            return [self.resolve_in_config(item) for item in config]
        return config

    # ------------------------------------------------------------------
    # internals

    def _cache_get(self, name: str) -> str | None:
        with self._cache_lock:
            entry = self._cache.get(name)
            if entry is None:
                return None
            if entry.expires_at < time.monotonic():
                del self._cache[name]
                return None
            return entry.plaintext

    def _cache_put(self, name: str, plaintext: str) -> None:
        with self._cache_lock:
            self._cache[name] = _CachedValue(
                plaintext=plaintext,
                expires_at=time.monotonic() + self._ttl,
            )

    def _get_client(self):
        if self._client is not None:
            return self._client
        with self._client_lock:
            if self._client is not None:
                return self._client
            self._client = self._build_client()
            return self._client

    def _build_client(self) -> SecretClient:
        region = os.environ.get(_ENV_REGION, "").strip() or _DEFAULT_REGION
        endpoint = os.environ.get(_ENV_ENDPOINT, "").strip() or f"kms.{region}.aliyuncs.com"
        return _AliyunKMSClient(region=region, endpoint=endpoint)


_default_resolver: KMSResolver | None = None
_default_resolver_lock = threading.Lock()


def get_default_resolver() -> KMSResolver:
    """Return the package-level resolver, building it lazily on first use."""
    global _default_resolver
    if _default_resolver is None:
        with _default_resolver_lock:
            if _default_resolver is None:
                _default_resolver = KMSResolver()
    return _default_resolver


def reset_default_resolver() -> None:
    """Drop the cached default resolver (used by tests)."""
    global _default_resolver
    with _default_resolver_lock:
        _default_resolver = None


def resolve_kms_value(value: str) -> str:
    """Resolve a single ``kms://`` reference using the default resolver."""
    return get_default_resolver().resolve(value)
