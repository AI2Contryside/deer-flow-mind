"""Regression tests for LocalSandbox credential-env isolation.

Pinned after thread 5093394f-19ce-4e55-90e4-f81fdda17bce: the agent ran
``echo "ERPNEXT_API_KEY set: $([ -n \"$ERPNEXT_API_KEY\" ] && echo yes ...)"``
— a command for which ``tools._extract_erpnext_env`` correctly returned
``{}`` (the gate did not match). Yet bash still printed ``yes`` because
``LocalSandbox.execute_command`` used ``subprocess.run(env=None)`` whenever
the per-call env dict was empty, and ``env=None`` makes subprocess inherit
the **entire** parent process os.environ — including whatever credential
vars the langgraph container picked up from ``.env`` / ``env_file``.

The fix in ``local_sandbox.py`` always starts from ``os.environ.copy()``,
strips the credential deny-list, and merges the per-call dict back on top.
That makes the gating in ``tools._extract_erpnext_env`` the **only** path
by which ERPNEXT_* reach the subprocess.

These tests reach into the real ``LocalSandbox.execute_command`` so a future
refactor that re-introduces ``env=None`` inheritance fails immediately.
"""

from __future__ import annotations

import os

import pytest

from src.sandbox.local.local_sandbox import LocalSandbox


@pytest.fixture
def sandbox() -> LocalSandbox:
    # No path mappings needed — these tests don't touch /mnt/* virtual paths.
    return LocalSandbox(id="test-credential-isolation")


@pytest.fixture
def leaky_parent_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Simulate the langgraph container having ERPNEXT_* in its os.environ
    (the conditions that caused the leak in 5093394f)."""
    fake = {
        "ERPNEXT_URL": "http://leaked-from-parent.example",
        "ERPNEXT_API_KEY": "leaked-key-DO-NOT-USE",
        "ERPNEXT_API_SECRET": "leaked-secret-DO-NOT-USE",
        "ERPNEXT_TENANT_ID": "999",
    }
    for k, v in fake.items():
        monkeypatch.setenv(k, v)
    return fake


@pytest.mark.unit
def test_subprocess_does_not_inherit_erpnext_secrets_when_env_is_none(
    sandbox: LocalSandbox,
    leaky_parent_env: dict[str, str],
) -> None:
    # No per-call env → must NOT inherit ERPNEXT secrets from parent process.
    # ERPNEXT_URL is allowed to inherit (deployment-level fallback alongside
    # ERPNEXT_HOST_HEADER); only api_key / api_secret / tenant_id are stripped.
    out = sandbox.execute_command('echo "URL=$ERPNEXT_URL"; echo "KEY=$ERPNEXT_API_KEY"; echo "SECRET=$ERPNEXT_API_SECRET"; echo "TENANT=$ERPNEXT_TENANT_ID"')
    assert "URL=http://leaked-from-parent.example" in out
    assert "KEY=" in out and "leaked-key" not in out
    assert "SECRET=" in out and "leaked-secret" not in out
    assert "TENANT=" in out and "999" not in out


@pytest.mark.unit
def test_subprocess_does_not_inherit_when_env_is_empty_dict(
    sandbox: LocalSandbox,
    leaky_parent_env: dict[str, str],
) -> None:
    # Empty dict (the historical fast-path) must behave the same as None.
    out = sandbox.execute_command("echo $ERPNEXT_API_KEY", env={})
    assert "leaked-key" not in out


@pytest.mark.unit
def test_subprocess_sees_per_call_env_when_explicitly_injected(
    sandbox: LocalSandbox,
    leaky_parent_env: dict[str, str],
) -> None:
    # The CLI-launcher path (gating is upstream in tools.py) explicitly passes
    # env=dict(...). That dict must override anything we strip from inheritance.
    explicit = {
        "ERPNEXT_URL": "http://explicit-injected.example",
        "ERPNEXT_API_KEY": "explicit-key",
        "ERPNEXT_API_SECRET": "explicit-secret",
        "ERPNEXT_TENANT_ID": "42",
    }
    out = sandbox.execute_command(
        'echo "URL=$ERPNEXT_URL"; echo "KEY=$ERPNEXT_API_KEY"; echo "SECRET=$ERPNEXT_API_SECRET"; echo "TENANT=$ERPNEXT_TENANT_ID"',
        env=explicit,
    )
    assert "URL=http://explicit-injected.example" in out
    assert "KEY=explicit-key" in out
    assert "SECRET=explicit-secret" in out
    assert "TENANT=42" in out
    # And the parent-process leaked values must NOT show through.
    assert "leaked-key" not in out
    assert "leaked-secret" not in out


@pytest.mark.unit
def test_other_inherited_env_vars_still_pass_through(
    sandbox: LocalSandbox,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The deny-list is narrow on purpose — PATH, HOME, LANG, LANGSMITH_*,
    # OPENAI_API_KEY, etc. must still reach the subprocess so `python`,
    # `uv`, and downstream tools work. Pin a non-credential var to make
    # sure we didn't accidentally clobber the whole environment.
    monkeypatch.setenv("DEER_FLOW_TEST_MARKER", "alive")
    out = sandbox.execute_command("echo MARKER=$DEER_FLOW_TEST_MARKER")
    assert "MARKER=alive" in out
    # PATH must survive — without it, /bin/bash can't find `echo` builtin
    # in some shells, but more importantly subsequent `python …` calls die.
    assert os.environ.get("PATH") is not None
    out = sandbox.execute_command('echo PATH_PRESENT=$([ -n "$PATH" ] && echo yes || echo no)')
    assert "PATH_PRESENT=yes" in out
