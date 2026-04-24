"""``repl`` command: interactive REPL using the unified cli-anything skin."""

from __future__ import annotations

import shlex

import click

from ..utils.repl_skin import ReplSkin


@click.command("repl")
@click.pass_context
def repl(ctx):
    """Interactive REPL. Runs if you invoke ``cli-anything-erpnext`` with no args."""
    skin = ReplSkin("erpnext", version="1.0.0")
    skin.print_banner()

    sess = ctx.obj["session"]
    site = sess.url or "<no session>"
    company = sess.context.company or "<no company>"
    skin.status_block(
        {"Site": site, "Company": company,
         "JSON mode": "on" if ctx.obj.get("json") else "off"},
        title="Session",
    )
    skin.hint("Type a subcommand (e.g., 'selling list-open'), 'help', or 'quit'.")
    print()

    pt_session = skin.create_prompt_session()
    root = ctx.find_root().command

    while True:
        try:
            line = skin.get_input(
                pt_session,
                project_name=sess.url or "",
                context=sess.context.company or "",
            )
        except (EOFError, KeyboardInterrupt):
            skin.print_goodbye()
            return

        if not line:
            continue
        if line.lower() in {"exit", "quit", "q"}:
            skin.print_goodbye()
            return
        if line.lower() in {"help", "?"}:
            click.echo(root.get_help(ctx))
            continue

        try:
            argv = shlex.split(line)
        except ValueError as e:
            skin.error(f"Parse error: {e}")
            continue

        try:
            root.main(
                args=argv,
                standalone_mode=False,
                obj=ctx.obj,
                prog_name="cli-anything-erpnext",
            )
        except click.ClickException as e:
            e.show()
        except SystemExit:
            pass
        except Exception as e:  # noqa: BLE001 — REPL boundary
            skin.error(str(e))
