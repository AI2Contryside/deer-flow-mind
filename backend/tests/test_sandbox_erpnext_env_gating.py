"""Tests for sandbox bash-tool ERPNext credential env gating.

Pinned after session b987fdbe-... where ``ERPNEXT_API_KEY`` /
``ERPNEXT_API_SECRET`` were unconditionally injected into every bash
subprocess. The agent then ran ``echo $ERPNEXT_API_KEY`` and rendered
the raw secret to chat (msg 18-19), then used the same value to call
``curl -H "Authorization: token <ak>:<sk>" …`` repeatedly (msgs 1, 3, 5,
7, 9, 28, 36, 64, 66, 68, 70, 72), bypassing the erpnext-cli skill
entirely.

The gate restricts env injection to commands that actually invoke the
erpnext-cli launcher path. Everything else — ``echo``, ``env``,
``printenv``, ``curl``, ad-hoc ``python -c`` — runs with a clean env.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.sandbox.tools import _command_invokes_erpnext_cli, _extract_erpnext_env


def _runtime(creds: dict | None = None, tenant_id: str | None = "15"):
    """Minimal ToolRuntime stand-in. Only ``runtime.context`` is read."""
    context: dict = {}
    if creds is not None:
        context["erpnext_credentials"] = creds
    if tenant_id is not None:
        context["tenant_id"] = tenant_id
    return SimpleNamespace(context=context, state={})


_GOOD_CREDS = {
    "url": "http://10.37.31.108:8000",
    "api_key": "b7f7419b3bb9fc1",
    "api_secret": "bbe0bce22f13719",
    "email": "test@company.com",
}


@pytest.mark.unit
def test_command_invokes_erpnext_cli_detects_launcher_path() -> None:
    # Absolute-path form (what the SKILL.md examples model verbatim).
    assert _command_invokes_erpnext_cli("python /mnt/skills/public/erpnext-cli/scripts/erpnext.py --json doc list Company")
    assert _command_invokes_erpnext_cli("cd /tmp && python /mnt/skills/public/erpnext-cli/scripts/erpnext.py session status")
    # `cd <skill> && python scripts/erpnext.py …` form — what models reach for
    # in practice. Earlier substring-match missed this and broke thread
    # 5093394f-…; pinned to keep the gate matching both invocation shapes.
    assert _command_invokes_erpnext_cli("cd /mnt/skills/public/erpnext-cli && python scripts/erpnext.py --json session status")
    assert _command_invokes_erpnext_cli("cd /mnt/skills/public/erpnext-cli && python scripts/erpnext.py --json bootstrap status 2>&1")


@pytest.mark.unit
def test_command_invokes_erpnext_cli_rejects_envvar_probes_and_curl() -> None:
    # Every one of these patterns appeared in session b987fdbe-... and
    # MUST NOT receive credentials.
    for cmd in (
        "echo $ERPNEXT_API_KEY",
        "env",
        "printenv",
        "echo $ERPNEXT_URL=$ERPNEXT_URL",
        # ↓ raw curl with hand-typed token: still wrong, but that path is
        # forbidden by the prompt; the gate just makes sure $ERPNEXT_API_KEY
        # interpolation gives nothing.
        'curl -s -H "Authorization: token $ERPNEXT_API_KEY:$ERPNEXT_API_SECRET" http://10.37.31.108:8000/api/resource/Company',
        "python -c 'import os; print(os.environ.get(\"ERPNEXT_API_KEY\"))'",
        "ls /mnt/skills/",
        # `cd <skill> && env` — has the skill dir prefix but no `erpnext.py`
        # token, so the new dir+filename gate must still reject it. Without
        # this guard a model could `cd` into the skill dir and exfiltrate.
        "cd /mnt/skills/public/erpnext-cli && env",
        "cd /mnt/skills/public/erpnext-cli && cat README.md",
    ):
        assert not _command_invokes_erpnext_cli(cmd), f"unexpected match for: {cmd}"


@pytest.mark.unit
def test_extract_erpnext_env_blocks_when_command_does_not_invoke_cli() -> None:
    runtime = _runtime(creds=_GOOD_CREDS)
    # Each of these is a documented exfiltration / bypass attempt from
    # the b987fdbe transcript; none should receive any env.
    for cmd in (
        "echo $ERPNEXT_API_KEY",
        "env",
        "printenv",
        "curl -H 'Authorization: token x:y' http://10.37.31.108/api/",
    ):
        assert _extract_erpnext_env(runtime, cmd) == {}, f"leaked env for: {cmd}"


@pytest.mark.unit
def test_extract_erpnext_env_returns_creds_when_invoking_cli() -> None:
    runtime = _runtime(creds=_GOOD_CREDS)
    cmd = "python /mnt/skills/public/erpnext-cli/scripts/erpnext.py --json doc list Company"
    env = _extract_erpnext_env(runtime, cmd)
    assert env["ERPNEXT_URL"] == _GOOD_CREDS["url"]
    assert env["ERPNEXT_API_KEY"] == _GOOD_CREDS["api_key"]
    assert env["ERPNEXT_API_SECRET"] == _GOOD_CREDS["api_secret"]
    assert env["ERPNEXT_TENANT_ID"] == "15"


@pytest.mark.unit
def test_extract_erpnext_env_handles_missing_credentials() -> None:
    # No erpnext_credentials in context (user not yet provisioned).
    runtime = _runtime(creds=None)
    cmd = "python /mnt/skills/public/erpnext-cli/scripts/erpnext.py session status"
    assert _extract_erpnext_env(runtime, cmd) == {}


@pytest.mark.unit
def test_extract_erpnext_env_requires_all_three_fields() -> None:
    runtime = _runtime(creds={"url": "http://10.0.0.1", "api_key": "x"})  # no secret
    cmd = "python /mnt/skills/public/erpnext-cli/scripts/erpnext.py session status"
    assert _extract_erpnext_env(runtime, cmd) == {}


@pytest.mark.unit
def test_extract_erpnext_env_handles_no_runtime() -> None:
    assert _extract_erpnext_env(None, "anything") == {}
