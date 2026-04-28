"""Click → JSON parameter introspection.

The ``help`` command needs structured per-parameter records (name, type,
required, default, etc.) so an LLM can reason about a command without
parsing prose. Click already carries this information on its
``Command.params`` list — we just normalize it into JSON-friendly dicts.

Auto-extraction keeps the help output drift-proof: when a developer adds
a new ``@click.option(...)`` to a command, the help record reflects it
immediately without anyone editing the static metadata.
"""

from __future__ import annotations

import inspect
from typing import Any

import click


def _clean_docstring(text: str | None) -> str:
    """Dedent a multi-line docstring so JSON output isn't littered with
    leading whitespace from Python source indentation. ``inspect.cleandoc``
    handles the typical Python convention where the first line has no
    leading indent but later lines are indented to match the surrounding
    function body."""
    if not text:
        return ""
    return inspect.cleandoc(text)


_FLAG_KEYS = {"is_flag", "count", "is_bool_flag"}


def _type_name(t: click.ParamType) -> str:
    """Render a Click ParamType as a short, agent-readable type string."""
    if isinstance(t, click.Choice):
        return f"choice[{', '.join(t.choices)}]"
    if isinstance(t, click.IntRange):
        return f"int[{t.min}..{t.max}]"
    if isinstance(t, click.FloatRange):
        return f"float[{t.min}..{t.max}]"
    if isinstance(t, click.Path):
        return "path"
    name = getattr(t, "name", None)
    if not name:
        return type(t).__name__.lower()
    return name


def _default_for_json(value: Any) -> Any:
    """Coerce a Click default into something JSON can render.

    Click sometimes sets defaults to tuples / Path / sentinel objects;
    keep simple primitives as-is, stringify the rest. Click's own
    ``UNSET`` sentinel is normalized to ``None`` so the JSON envelope
    doesn't leak internal repr strings to agents.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    cls_name = type(value).__name__
    if cls_name == "Sentinel" or repr(value).endswith(".UNSET"):
        return None
    if isinstance(value, (list, tuple)):
        if not value:
            return None
        return [_default_for_json(v) for v in value]
    return str(value)


def introspect_option(opt: click.Option) -> dict[str, Any]:
    """Return a JSON-friendly record for a single ``click.Option``.

    Drops keys whose values are empty/absent so the JSON payload stays
    compact for agent consumption. ``required``/``multiple`` are kept
    even when False because they're load-bearing facts.
    """
    is_flag = bool(getattr(opt, "is_flag", False))
    record: dict[str, Any] = {
        "kind": "option",
        "names": list(opt.opts),
        "type": "flag" if is_flag else _type_name(opt.type),
        "required": bool(opt.required),
        "multiple": bool(opt.multiple),
        "help": opt.help or "",
    }
    secondary = list(getattr(opt, "secondary_opts", []) or [])
    if secondary:
        record["secondary_names"] = secondary
    default = _default_for_json(opt.default)
    if default is not None:
        record["default"] = default
    if isinstance(opt.type, click.Choice):
        record["choices"] = list(opt.type.choices)
    if getattr(opt, "envvar", None):
        record["envvar"] = opt.envvar
    return record


def introspect_argument(arg: click.Argument) -> dict[str, Any]:
    """Return a JSON-friendly record for a single ``click.Argument``."""
    return {
        "kind": "argument",
        "name": (arg.name or "").upper(),
        "type": _type_name(arg.type),
        "required": bool(arg.required),
        "nargs": arg.nargs,
    }


def introspect_command(cmd: click.Command) -> dict[str, Any]:
    """Return parameters + Click's own short_help/help for a command."""
    arguments: list[dict[str, Any]] = []
    options: list[dict[str, Any]] = []
    for p in cmd.params:
        if isinstance(p, click.Argument):
            arguments.append(introspect_argument(p))
        elif isinstance(p, click.Option):
            options.append(introspect_option(p))
    return {
        "short_help": (cmd.get_short_help_str() or "").strip(),
        "help": _clean_docstring(cmd.help),
        "arguments": arguments,
        "options": options,
    }


def list_groups(root: click.Group) -> list[str]:
    """Names of immediate subgroups of the root CLI, alphabetically sorted."""
    out: list[str] = []
    for name, obj in root.commands.items():
        if isinstance(obj, click.Group):
            out.append(name)
    return sorted(out)


def list_commands(group: click.Group) -> list[tuple[str, click.Command]]:
    """``[(name, cmd), ...]`` for a group, alphabetically sorted by name.

    Filters out hidden commands so the help surface matches what's
    actually meant to be agent-facing.
    """
    pairs = [
        (name, cmd) for name, cmd in group.commands.items()
        if not getattr(cmd, "hidden", False) and not isinstance(cmd, click.Group)
    ]
    return sorted(pairs, key=lambda x: x[0])
