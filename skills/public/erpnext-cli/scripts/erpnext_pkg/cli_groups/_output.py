"""Shared output helpers for every CLI subcommand.

Every command supports dual output modes:

  - **Human-readable** (default): tables, colors via ``ReplSkin``.
  - **Machine-readable** (``--json`` or ``CLI_ANYTHING_JSON=1``):
    a JSON document on stdout. Errors become
    ``{"ok": false, "error": {...}}``, successes become
    ``{"ok": true, "data": ...}``.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

import click

from ..core.errors import ERPNextError


def is_json_mode(ctx: click.Context) -> bool:
    """Check whether the current invocation wants JSON output."""
    if ctx.obj and ctx.obj.get("json"):
        return True
    if os.environ.get("CLI_ANYTHING_JSON") == "1":
        return True
    return False


def emit(ctx: click.Context, data: Any, *, human: str | None = None) -> None:
    """Print a successful payload in the right format."""
    if is_json_mode(ctx):
        click.echo(json.dumps({"ok": True, "data": data}, default=str, indent=2))
        return
    if human is not None:
        click.echo(human)
        return
    # Default human format: pretty JSON
    click.echo(json.dumps(data, default=str, indent=2, ensure_ascii=False))


def emit_error(ctx: click.Context, exc: BaseException, *, exit_code: int = 1) -> None:
    """Emit an error and exit with ``exit_code``.

    JSON mode ⇒ structured error; human mode ⇒ stderr + red formatting.
    """
    if isinstance(exc, ERPNextError):
        payload = exc.to_dict()
    else:
        payload = {"error": type(exc).__name__, "message": str(exc)}
    if is_json_mode(ctx):
        click.echo(json.dumps({"ok": False, "error": payload}, default=str, indent=2))
        sys.exit(exit_code)
    click.echo(
        click.style(f"✗ {payload.get('error', 'Error')}: {payload.get('message')}",
                    fg="red"),
        err=True,
    )
    sys.exit(exit_code)


def run_safely(ctx: click.Context, func, *args, **kwargs):
    """Invoke ``func``; on exception, emit and exit. Avoid Click's stack traces
    leaking into JSON output."""
    try:
        return func(*args, **kwargs)
    except ERPNextError as e:
        emit_error(ctx, e)
    except click.ClickException:
        raise
    except Exception as e:  # noqa: BLE001 — CLI boundary
        emit_error(ctx, e, exit_code=2)
