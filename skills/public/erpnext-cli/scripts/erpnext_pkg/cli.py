"""Top-level CLI entrypoint for ``cli-anything-erpnext``.

Wires together every command group (``session``, ``doc``, ``selling``,
``buying``, ``stock``, ``accounts``, ``manufacturing``, ``crm``, ``hr``)
and the REPL. Running ``cli-anything-erpnext`` with no subcommand drops
into the interactive REPL (per HARNESS.md Phase 3).
"""

from __future__ import annotations

import os
from pathlib import Path

import click

from . import __version__
from .cli_groups import (
    accounts_group,
    bootstrap_group,
    buying_group,
    crm_group,
    doc_group,
    hr_group,
    manufacturing_group,
    repl_group,
    selling_group,
    session_group,
    stock_group,
)
from .cli_groups._output import emit_error
from .core.session import SESSION_FILE, Session


@click.group(
    invoke_without_command=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)
@click.option("--json", "json_mode", is_flag=True,
              envvar="CLI_ANYTHING_JSON",
              help="Machine-readable JSON output on every command.")
@click.option("--session-file", type=click.Path(),
              help="Override session file path (default: ~/.cli-anything-erpnext/session.json)")
@click.option("--tenant", "tenant_id", envvar="ERPNEXT_TENANT_ID",
              help="Tenant id sent as X-Tenant-ID on every request "
                   "(also reads ERPNEXT_TENANT_ID env var).")
@click.version_option(version=__version__, prog_name="cli-anything-erpnext")
@click.pass_context
def cli(ctx: click.Context, json_mode: bool, session_file: str | None,
        tenant_id: str | None):
    """ERPNext business-process CLI.

    Primary surface: the domain groups (``selling``, ``buying``, ``stock``,
    ``accounts``, ``manufacturing``, ``crm``, ``hr``). Each exposes
    workflow-level commands like ``order-to-cash`` / ``procure-to-pay``
    that chain multiple ERPNext DocTypes in one call.

    For DocTypes without a first-class workflow, use ``doc`` (raw CRUD).
    Authenticate once with ``session login`` then reuse the session.
    """
    path = Path(session_file) if session_file else SESSION_FILE
    try:
        sess = Session.load(path)
    except Exception as e:  # noqa: BLE001 — bad session file = fatal
        emit_error(ctx, e)
        return

    # Propagate tenant id via env so Session.client() picks it up without
    # every cli_groups/*.py callsite needing to thread it through. Setting
    # the env var is scoped to this process invocation.
    if tenant_id:
        os.environ["ERPNEXT_TENANT_ID"] = tenant_id

    ctx.ensure_object(dict)
    ctx.obj["json"] = json_mode
    ctx.obj["session_file"] = path
    ctx.obj["session"] = sess
    ctx.obj["tenant_id"] = tenant_id or os.environ.get("ERPNEXT_TENANT_ID")

    if ctx.invoked_subcommand is None:
        ctx.invoke(repl_group.repl)


cli.add_command(session_group.group, name="session")
cli.add_command(bootstrap_group.group, name="bootstrap")
cli.add_command(doc_group.group, name="doc")
cli.add_command(selling_group.group, name="selling")
cli.add_command(buying_group.group, name="buying")
cli.add_command(stock_group.group, name="stock")
cli.add_command(accounts_group.group, name="accounts")
cli.add_command(manufacturing_group.group, name="manufacturing")
cli.add_command(crm_group.group, name="crm")
cli.add_command(hr_group.group, name="hr")
cli.add_command(repl_group.repl)


def main():
    cli()


if __name__ == "__main__":
    main()
