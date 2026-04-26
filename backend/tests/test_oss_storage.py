"""Unit tests for ``src.storage`` — OSS config, key builders, and InMemoryStorage."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.storage import (
    InMemoryStorage,
    OssConfig,
    chat_artifact_key,
    chat_thread_uploads_manifest_key,
    chat_upload_key,
    load_oss_config_from_dict,
    sanitize_filename,
    tenant_prefix,
    thread_upload_key,
    user_prefix,
)


def test_sanitize_filename_basic() -> None:
    assert sanitize_filename("report.pdf") == "report.pdf"
    assert sanitize_filename("../../etc/passwd") == "passwd"
    assert sanitize_filename("windows\\path\\file.txt") == "windows_path_file.txt"
    assert sanitize_filename("weird\x00name.txt") == "weirdname.txt"
    assert sanitize_filename("") == "file"
    assert sanitize_filename(".") == "file"


def test_sanitize_filename_caps_length() -> None:
    long = "a" * 250
    out = sanitize_filename(long)
    assert len(out) == 100


def test_chat_upload_key_shape() -> None:
    day = datetime(2026, 4, 26, 12, 0, 0, tzinfo=timezone.utc)
    key = chat_upload_key(7, 42, day, "../../etc/passwd")
    assert key.startswith("tenants/7/users/42/2026-04-26/")
    assert key.endswith("-passwd")


def test_chat_artifact_key_is_deterministic() -> None:
    assert chat_artifact_key(7, "thread-abc", "chart.png") == "tenants/7/threads/thread-abc/outputs/chart.png"


def test_thread_upload_key_under_thread_prefix() -> None:
    key = thread_upload_key(7, "thread-abc", "report.pdf")
    assert key.startswith("tenants/7/threads/thread-abc/uploads/")
    assert key.endswith("-report.pdf")


def test_manifest_key() -> None:
    assert chat_thread_uploads_manifest_key(7, "thread-abc") == "tenants/7/threads/thread-abc/uploads/_manifest.json"


def test_prefixes() -> None:
    assert tenant_prefix(7) == "tenants/7/"
    assert user_prefix(7, 42) == "tenants/7/users/42/"


def test_load_oss_config_validates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALIYUN_OSS_ACCESS_KEY_ID", "ak")
    monkeypatch.setenv("ALIYUN_OSS_ACCESS_KEY_SECRET", "sk")
    cfg = load_oss_config_from_dict(
        {
            "endpoint": "oss-cn-hangzhou.aliyuncs.com",
            "region": "cn-hangzhou",
            "sys_bucket": "trademind-sys",
            "chat_bucket": "trademind-chat-session",
        }
    )
    assert isinstance(cfg, OssConfig)
    assert cfg.public_host("trademind-sys") == "trademind-sys.oss-cn-hangzhou.aliyuncs.com"
    assert cfg.signed_url_ttl_seconds == 3600  # default applied


def test_load_oss_config_missing_env_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALIYUN_OSS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("ALIYUN_OSS_ACCESS_KEY_SECRET", raising=False)
    with pytest.raises(ValueError) as excinfo:
        load_oss_config_from_dict(
            {
                "endpoint": "oss-cn-hangzhou.aliyuncs.com",
                "region": "cn-hangzhou",
                "sys_bucket": "trademind-sys",
                "chat_bucket": "trademind-chat-session",
            }
        )
    assert "ALIYUN_OSS_ACCESS_KEY_ID" in str(excinfo.value)


def test_in_memory_storage_round_trip(tmp_path: Path) -> None:
    s = InMemoryStorage()
    payload = b"hello world"
    s.put_object("bucket", "key", payload, content_type="text/plain")
    assert s.object_exists("bucket", "key")
    size, ctype = s.head_object("bucket", "key")
    assert size == len(payload)
    assert ctype == "text/plain"

    out = tmp_path / "out.txt"
    s.download_to_path("bucket", "key", out)
    assert out.read_bytes() == payload
    assert s.get_object_bytes("bucket", "key") == payload
    s.delete_object("bucket", "key")
    assert not s.object_exists("bucket", "key")


def test_in_memory_storage_signs_and_public_urls() -> None:
    s = InMemoryStorage()
    assert s.sign_url("b", "k", 60).startswith("https://signed/b/k")
    assert s.public_url("b", "k") == "https://public/b/k"
