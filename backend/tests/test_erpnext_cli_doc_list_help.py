"""Tests pinning the discoverability hints on `doc list` and `doc insert` help.

Pinned after session b987fdbe-... where the agent tried `doc list --doctype X`
(msg 47) and `doc list X --data {...}` (msg 49) — both wrong invocations Click
flagged with "No such option" but without telling the agent the correct shape.
The help text now contains the right shape verbatim and a "Common mistakes"
block calling out the b987fdbe failures by name.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

_SKILL_SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "public" / "erpnext-cli" / "scripts"
if str(_SKILL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS))

from erpnext_pkg.cli import cli  # noqa: E402


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.mark.unit
def test_doc_list_help_says_doctype_is_positional(runner: CliRunner) -> None:
    res = runner.invoke(cli, ["doc", "list", "--help"])
    assert res.exit_code == 0
    out = res.output
    assert "positional" in out
    # The exact b987fdbe failure modes are flagged.
    assert "--doctype X" in out and "WRONG" in out
    assert "--data {...}" in out


@pytest.mark.unit
def test_doc_list_help_shows_concrete_example(runner: CliRunner) -> None:
    res = runner.invoke(cli, ["doc", "list", "--help"])
    out = res.output
    # At least one runnable example with the right shape.
    assert "doc list Company" in out
    assert "--filter" in out
    assert "--field" in out


@pytest.mark.unit
def test_doc_insert_help_steers_toward_domain_commands(runner: CliRunner) -> None:
    res = runner.invoke(cli, ["doc", "insert", "--help"])
    assert res.exit_code == 0
    out = res.output
    # The agent should prefer chains over raw inserts for transactional flows.
    assert "domain commands" in out
    assert "order-to-cash" in out or "procure-to-pay" in out


@pytest.mark.unit
def test_doc_insert_help_shows_data_inline_example(runner: CliRunner) -> None:
    res = runner.invoke(cli, ["doc", "insert", "--help"])
    out = res.output
    assert "--doctype Company" in out or "Company" in out
    assert "--data" in out


@pytest.mark.unit
def test_top_level_help_lists_bootstrap_command(runner: CliRunner) -> None:
    res = runner.invoke(cli, ["--help"])
    assert res.exit_code == 0
    assert "bootstrap" in res.output


@pytest.mark.unit
def test_bootstrap_status_command_is_registered(runner: CliRunner) -> None:
    res = runner.invoke(cli, ["bootstrap", "--help"])
    assert res.exit_code == 0
    assert "status" in res.output
