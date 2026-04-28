"""``session`` subcommands: login, logout, status, context, history."""

from __future__ import annotations

import json

import click

from ..core.errors import AuthError
from ..core.session import SESSION_FILE, Session, cookie_jar_for
from ._output import emit, run_safely


def _purge_cookie_jar(session_path) -> None:
    """Delete the sidecar cookie file. Best-effort — missing file is fine."""
    jar = cookie_jar_for(session_path)
    try:
        jar.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


@click.group()
def group():
    """Connection + context state (URL, credentials, default company, etc.)."""


@group.command("login")
@click.option("--url", required=True, help="ERPNext site URL, e.g. https://erp.example.com")
@click.option("--api-key", help="API key for token auth (preferred).")
@click.option("--api-secret", help="API secret for token auth.")
@click.option("--username", help="Username for session auth (fallback).")
@click.option("--password", help="Password for session auth.")
@click.option("--save-credentials", is_flag=True,
              help="Persist secret/password to the session file (chmod 600). "
                   "Otherwise you must supply them via env each run.")
@click.option("--no-verify-ssl", is_flag=True, help="Disable TLS verification.")
@click.pass_context
def login(ctx, url, api_key, api_secret, username, password,
          save_credentials, no_verify_ssl):
    """Authenticate and persist a session."""
    if not ((api_key and api_secret) or (username and password)):
        raise click.UsageError(
            "Provide either --api-key + --api-secret or --username + --password"
        )
    s = Session(
        url=url,
        api_key=api_key, api_secret=api_secret,
        username=username, password=password,
        save_credentials=save_credentials,
        verify_ssl=not no_verify_ssl,
        source_path=ctx.obj["session_file"],
    )

    def _verify():
        c = s.client()
        return c.ping()

    info = run_safely(ctx, _verify)
    path = s.save(ctx.obj["session_file"])
    emit(ctx, {"url": url, "logged_in_user": info, "session_file": str(path)})


@group.command("logout")
@click.pass_context
def logout(ctx):
    """Clear stored credentials (keeps URL and context defaults)."""
    path = ctx.obj["session_file"]
    s = Session.load(path)
    cleared = Session(
        url=s.url,
        api_key=None, api_secret=None,
        username=None, password=None,
        save_credentials=False,
        verify_ssl=s.verify_ssl,
        context=s.context,
        history=s.history,
        source_path=path,
    )
    cleared.save(path)
    _purge_cookie_jar(path)
    emit(ctx, {"cleared": True, "url_kept": s.url})


@group.command("status")
@click.pass_context
def status(ctx):
    """Show the current session (secrets redacted)."""
    s: Session = ctx.obj["session"]
    emit(ctx, s.redacted())


@group.command("ping")
@click.pass_context
def ping(ctx):
    """Verify the session is usable."""
    s: Session = ctx.obj["session"]
    info = run_safely(ctx, lambda: s.client().ping())
    emit(ctx, {"ok": True, "user": info})


@group.command("set-context")
@click.option("--company")
@click.option("--warehouse", "default_warehouse")
@click.option("--customer", "default_customer")
@click.option("--supplier", "default_supplier")
@click.option("--price-list", "default_price_list")
@click.option("--currency", "default_currency")
@click.option("--cost-center", "default_cost_center")
@click.pass_context
def set_context(ctx, **updates):
    """Set default company / warehouse / customer / etc. for future commands."""
    updates = {k: v for k, v in updates.items() if v is not None}
    if not updates:
        raise click.UsageError("Pass at least one --company/--warehouse/...")
    s: Session = ctx.obj["session"]
    new_session = s.with_context(**updates)
    new_session.save(ctx.obj["session_file"])
    ctx.obj["session"] = new_session
    emit(ctx, {"context": new_session.redacted()["context"]})


@group.command("history")
@click.option("--limit", default=20, type=int)
@click.pass_context
def history(ctx, limit):
    """Show recent workflow events."""
    s: Session = ctx.obj["session"]
    emit(ctx, s.history[-limit:])


@group.command("clear")
@click.confirmation_option(prompt="Delete session file?")
@click.pass_context
def clear(ctx):
    """Delete the entire session file."""
    path = ctx.obj["session_file"]
    if path.is_file():
        path.unlink()
    _purge_cookie_jar(path)
    emit(ctx, {"deleted": str(path)})
