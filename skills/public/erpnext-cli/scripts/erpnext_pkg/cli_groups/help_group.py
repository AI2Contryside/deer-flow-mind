"""``help`` command — LLM-friendly per-command help.

Click's built-in ``--help`` is human-targeted: pretty-printed columns
that need parsing back into structure to be useful to an agent. The
``help`` subcommand here returns the *same* information as a JSON
envelope (in ``--json`` mode) so an LLM can:

  - List all groups with one-line summaries.
  - List a group's commands with one-line summaries.
  - Get a full per-command spec: parameters (auto-introspected from
    Click), enriched parameter descriptions, output schema, working
    examples, common errors with recoveries, and related commands.

Three call shapes (always in --json mode for agents):

    # Overview of every group
    erpnext.py --json help

    # All commands in a group
    erpnext.py --json help selling

    # Full spec for one command
    erpnext.py --json help selling order-to-cash

Agents should call ``help <group> <command>`` BEFORE invoking a command
they haven't used recently. The resulting JSON tells them exactly which
flags exist, what types they take, what the success envelope looks like,
and how to recover from typical errors — without reading source.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

import click

from . import _help_data
from . import _help_introspect as introspect
from ._output import emit, emit_error


# ─────────────────────────────────────────────────────────────────────
# Builders — assemble the JSON payloads from Click + curated data
# ─────────────────────────────────────────────────────────────────────

def _root_group(ctx: click.Context) -> click.Group:
    """Walk up to the root ``cli`` group regardless of nesting depth."""
    cur = ctx
    while cur.parent is not None:
        cur = cur.parent
    cmd = cur.command
    if not isinstance(cmd, click.Group):
        raise click.UsageError("Internal: root command is not a group.")
    return cmd


def _build_groups_overview(root: click.Group) -> dict[str, Any]:
    """Top-level: list every group with a one-line summary + which chains it owns."""
    groups: list[dict[str, Any]] = []
    for name in introspect.list_groups(root):
        rec = _help_data.get_group_record(name)
        entry: dict[str, Any] = {
            "group": name,
            "summary": rec.summary if rec else (
                # Fall back to Click's group docstring.
                (root.commands[name].help or "").strip().split("\n", 1)[0]
            ),
        }
        if rec is not None:
            entry["process"] = rec.process
            entry["when_to_use"] = rec.when_to_use
            entry["key_chains"] = list(rec.key_chains)
        groups.append(entry)
    return {
        "kind": "groups_overview",
        "usage": (
            "Run `help <group>` to see commands in a group, or "
            "`help <group> <command>` for a full per-command spec."
        ),
        "groups": groups,
        "tips": [
            "Always pass --json for machine-readable output.",
            "Run `bootstrap status` before any selling/buying/stock chain.",
            "Prefer domain commands over `doc insert` for SO/PO/Invoice/Stock Entry.",
        ],
    }


def _build_group_overview(root: click.Group, group_name: str) -> dict[str, Any] | None:
    """One group: its summary + every command's one-line summary."""
    group = root.commands.get(group_name)
    if not isinstance(group, click.Group):
        return None
    rec = _help_data.get_group_record(group_name)

    commands: list[dict[str, Any]] = []
    for cname, cmd in introspect.list_commands(group):
        cmd_rec = _help_data.get_command_record(group_name, cname)
        commands.append({
            "command": f"{group_name} {cname}",
            "summary": (cmd_rec.summary if cmd_rec else
                        (cmd.get_short_help_str() or "").strip()),
            "process": cmd_rec.process if cmd_rec else None,
        })

    payload: dict[str, Any] = {
        "kind": "group_overview",
        "group": group_name,
        "summary": (rec.summary if rec else
                    (group.help or "").strip().split("\n", 1)[0]),
        "commands": commands,
        "next_step": f"Run `help {group_name} <command>` for parameter spec.",
    }
    if rec is not None:
        payload["process"] = rec.process
        payload["when_to_use"] = rec.when_to_use
        payload["key_chains"] = list(rec.key_chains)
    return payload


def _command_record_to_dict(rec: _help_data.HelpRecord) -> dict[str, Any]:
    """Curated metadata → JSON-friendly dict, dropping empties for compactness."""
    out: dict[str, Any] = {
        "summary": rec.summary,
        "process": rec.process,
        "when_to_use": rec.when_to_use,
    }
    if rec.preconditions:
        out["preconditions"] = list(rec.preconditions)
    if rec.param_notes:
        out["param_notes"] = dict(rec.param_notes)
    if rec.output:
        out["output_schema"] = dict(rec.output)
    if rec.examples:
        out["examples"] = [dict(e) for e in rec.examples]
    if rec.common_errors:
        out["common_errors"] = [dict(e) for e in rec.common_errors]
    if rec.related:
        out["related"] = list(rec.related)
    return out


def _merge_param_notes(
    introspected: list[dict[str, Any]],
    notes: dict[str, str],
    *,
    is_argument: bool,
) -> list[dict[str, Any]]:
    """Augment auto-introspected params with curated descriptions.

    Lookup keys for ``notes``:
      - For arguments: the upper-cased name (e.g. ``CUSTOMER``).
      - For options: any of the flag opts (e.g. ``--customer``).

    Click's own ``help=`` text is preserved as ``help_short``; the
    curated note (when present) lands as ``description`` since it's
    typically richer.
    """
    out: list[dict[str, Any]] = []
    for p in introspected:
        merged = dict(p)
        if is_argument:
            note = notes.get(p["name"])
        else:
            note = None
            for opt_name in p.get("names", ()):
                if opt_name in notes:
                    note = notes[opt_name]
                    break
        if note:
            merged["description"] = note
        if not merged.get("description") and merged.get("help"):
            merged["description"] = merged["help"]
        out.append(merged)
    return out


def _build_command_help(
    root: click.Group, group_name: str, cmd_name: str,
) -> dict[str, Any] | None:
    """Full per-command spec: introspection + curated metadata, merged."""
    group = root.commands.get(group_name)
    if not isinstance(group, click.Group):
        return None
    cmd = group.commands.get(cmd_name)
    if cmd is None or isinstance(cmd, click.Group):
        return None

    introspected = introspect.introspect_command(cmd)
    rec = _help_data.get_command_record(group_name, cmd_name)
    notes = dict(rec.param_notes) if rec else {}

    payload: dict[str, Any] = {
        "kind": "command_help",
        "command": f"{group_name} {cmd_name}",
        "invocation_template": (
            f"python /mnt/skills/public/erpnext-cli/scripts/erpnext.py "
            f"--json {group_name} {cmd_name} [options]"
        ),
        "click_help": introspected["help"] or introspected["short_help"],
        "arguments": _merge_param_notes(
            introspected["arguments"], notes, is_argument=True,
        ),
        "options": _merge_param_notes(
            introspected["options"], notes, is_argument=False,
        ),
    }
    if rec is not None:
        payload.update(_command_record_to_dict(rec))
    return payload


# ─────────────────────────────────────────────────────────────────────
# Suggestion helpers — make wrong-name errors self-correcting
# ─────────────────────────────────────────────────────────────────────

def _unknown_group_error(root: click.Group, name: str) -> dict[str, Any]:
    return {
        "kind": "unknown_group",
        "requested": name,
        "message": f"Unknown group: {name!r}.",
        "available_groups": introspect.list_groups(root),
        "next_step": "Re-run `help` (no args) for a per-group overview.",
    }


def _unknown_command_error(
    root: click.Group, group_name: str, cmd_name: str,
) -> dict[str, Any]:
    group = root.commands.get(group_name)
    available: list[str] = []
    if isinstance(group, click.Group):
        available = [n for n, _ in introspect.list_commands(group)]
    return {
        "kind": "unknown_command",
        "requested": f"{group_name} {cmd_name}",
        "message": f"Unknown command: {group_name} {cmd_name!r}.",
        "available_commands": available,
        "next_step": f"Re-run `help {group_name}` for the command list.",
    }


# ─────────────────────────────────────────────────────────────────────
# Click wiring — single command, variadic positional path
# ─────────────────────────────────────────────────────────────────────

@click.command(
    "help",
    short_help="LLM-friendly help: parameter specs, examples, common errors.",
    epilog=(
        "Examples:\n"
        "  help                                 # all groups overview\n"
        "  help selling                         # commands in 'selling'\n"
        "  help selling order-to-cash           # full spec for one command\n"
    ),
)
@click.argument("path", nargs=-1)
@click.pass_context
def help_cmd(ctx: click.Context, path: tuple[str, ...]) -> None:
    """LLM-friendly help.

    With no args: list every group with summary + key chains.
    With one arg: list every command in that group.
    With two args: full per-command spec — parameters, output schema,
    working examples, and common errors with recoveries.
    """
    try:
        root = _root_group(ctx)
    except click.UsageError as e:
        emit_error(ctx, e)
        return

    if len(path) == 0:
        emit(ctx, _build_groups_overview(root))
        return

    if len(path) == 1:
        payload = _build_group_overview(root, path[0])
        if payload is None:
            emit(ctx, _unknown_group_error(root, path[0]))
            return
        emit(ctx, payload)
        return

    if len(path) == 2:
        group_name, cmd_name = path
        if group_name not in root.commands:
            emit(ctx, _unknown_group_error(root, group_name))
            return
        payload = _build_command_help(root, group_name, cmd_name)
        if payload is None:
            emit(ctx, _unknown_command_error(root, group_name, cmd_name))
            return
        emit(ctx, payload)
        return

    emit_error(ctx, click.UsageError(
        "help expects at most two positional arguments: "
        "[<group> [<command>]]."
    ))
