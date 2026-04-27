"""Tests for the /api/onboarding/status router.

The desktop FE polls this between SSE turns during the dedicated init step
and only transitions to MainApp on ``completed: true``. Regressions here
silently strand users on the init screen forever, so we keep the contract
covered with explicit round-trip tests.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from src.gateway.routers import onboarding


def _run(coro):
    return asyncio.run(coro)


def test_returns_completed_true_when_profile_exists(tmp_path: Path) -> None:
    profile_path = tmp_path / "1001" / "profile.json"
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    profile_path.write_text("{}")

    with patch.object(onboarding, "get_profile_path", return_value=profile_path):
        res = _run(onboarding.get_onboarding_status(x_tenant_id="1001"))

    assert res.tenant_id == "1001"
    assert res.completed is True


def test_returns_completed_false_when_profile_missing(tmp_path: Path) -> None:
    profile_path = tmp_path / "1001" / "profile.json"  # not created

    with patch.object(onboarding, "get_profile_path", return_value=profile_path):
        res = _run(onboarding.get_onboarding_status(x_tenant_id="1001"))

    assert res.tenant_id == "1001"
    assert res.completed is False


def test_rejects_missing_tenant_header() -> None:
    with pytest.raises(HTTPException) as excinfo:
        _run(onboarding.get_onboarding_status(x_tenant_id=None))
    assert excinfo.value.status_code == 400


def test_rejects_blank_tenant_header() -> None:
    with pytest.raises(HTTPException) as excinfo:
        _run(onboarding.get_onboarding_status(x_tenant_id="   "))
    assert excinfo.value.status_code == 400


def test_rejects_invalid_tenant_id_format() -> None:
    """``get_profile_path`` enforces ``[A-Za-z0-9_\\-]{1,64}`` — surface a
    400 instead of letting a ValueError bubble up as a 500."""

    def boom(_tid):
        raise ValueError("invalid tenant_id")

    with patch.object(onboarding, "get_profile_path", side_effect=boom):
        with pytest.raises(HTTPException) as excinfo:
            _run(onboarding.get_onboarding_status(x_tenant_id="../etc/passwd"))
    assert excinfo.value.status_code == 400
