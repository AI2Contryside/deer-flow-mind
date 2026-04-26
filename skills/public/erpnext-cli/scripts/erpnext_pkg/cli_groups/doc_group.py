"""``doc`` subcommands: raw DocType CRUD (escape hatch).

The primary surface of this CLI is the business-domain groups
(``selling``, ``buying``, ...). ``doc`` exists as an escape hatch for
the long tail of DocTypes that don't have first-class workflows —
think Company setup, User creation, custom Doctypes, ad-hoc reads.
"""

from __future__ import annotations

import json
from pathlib import Path

import click

from ._output import emit, run_safely


@click.group()
def group():
    """Raw DocType CRUD (escape hatch for DocTypes without a workflow)."""


@group.command("get")
@click.argument("doctype")
@click.argument("name")
@click.pass_context
def get(ctx, doctype, name):
    """Fetch a single DocType record."""
    c = ctx.obj["session"].client()
    emit(ctx, run_safely(ctx, c.get_doc, doctype, name))


@group.command(
    "list",
    epilog=(
        "Examples (DOCTYPE is positional, NOT --doctype):\n"
        '  doc list Company\n'
        '  doc list Item --limit 5 --field name --field item_group\n'
        '  doc list "Sales Order" --filter "docstatus=1" --filter "status:in:[\\"To Deliver\\"]"\n'
        '  doc list Customer --order-by "creation desc" --limit 10\n'
        "\n"
        "Common mistakes:\n"
        "  doc list --doctype X        # WRONG — DOCTYPE is positional, drop --doctype\n"
        "  doc list X --data {...}     # WRONG — --data is for `doc insert`, not list\n"
        "                                use --filter / --field / --limit instead"
    ),
)
@click.argument("doctype")
@click.option("--filter", "filters", multiple=True,
              help='Filter expression (repeatable): "field=value" or '
                   '"field:op:value" (e.g. "docstatus=1", '
                   '"status:in:[\"Open\",\"To Bill\"]"). NOT --data.')
@click.option("--field", "fields", multiple=True, help="Fields to fetch (repeatable).")
@click.option("--limit", default=20, type=int)
@click.option("--start", default=0, type=int)
@click.option("--order-by")
@click.pass_context
def list_cmd(ctx, doctype, filters, fields, limit, start, order_by):
    """List records of DOCTYPE with optional filters.

    DOCTYPE is a **positional** argument — pass it as the first token,
    not via --doctype. Use --filter (repeatable) for WHERE clauses,
    --field (repeatable) to project columns, --limit / --start to paginate.
    """
    c = ctx.obj["session"].client()
    parsed = _parse_filters(filters)
    data = run_safely(
        ctx, c.get_list, doctype,
        filters=parsed or None,
        fields=list(fields) or None,
        limit=limit, start=start, order_by=order_by,
    )
    emit(ctx, data)


@group.command(
    "insert",
    epilog=(
        "Examples:\n"
        '  doc insert --doctype Company --data \'{"company_name":"BIEL","abbr":"BIEL","default_currency":"CNY","country":"Hong Kong"}\'\n'
        '  doc insert --doctype Item    --file ./item.json --submit\n'
        '  doc insert --data \'{"doctype":"Customer","customer_name":"NOXIA"}\'  # doctype in JSON works too\n'
        "\n"
        "Tip: for high-frequency business flows (Sales Order, Purchase Order,\n"
        "Quotation, Invoice, Stock Entry, ...), prefer the domain commands\n"
        "(`selling order-to-cash`, `buying procure-to-pay`, `stock stock-in`)\n"
        "over `doc insert` — they chain multiple DocTypes correctly in one call."
    ),
)
@click.option("--doctype")
@click.option("--file", "file_path",
              help="Path to JSON file with the full doc body.")
@click.option("--data", help="Inline JSON for the doc body.")
@click.option("--submit", is_flag=True)
@click.pass_context
def insert(ctx, doctype, file_path, data, submit):
    """Insert a new document from --file or --data.

    --data takes inline JSON. Either pass --doctype explicitly or include
    "doctype" inside the JSON. Add --submit to also submit the document
    after insert (idempotent for already-submitted docs).
    """
    doc = _load_json(file_path, data)
    if doctype: doc["doctype"] = doctype
    if "doctype" not in doc:
        raise click.UsageError("doc must have a 'doctype' (use --doctype or include in JSON)")
    c = ctx.obj["session"].client()
    created = run_safely(ctx, c.insert, doc)
    if submit:
        run_safely(ctx, c.submit, created["doctype"] if "doctype" in created else doc["doctype"], created["name"])
        created = run_safely(ctx, c.get_doc, doc["doctype"], created["name"])
    emit(ctx, created)


@group.command("update")
@click.argument("doctype")
@click.argument("name")
@click.option("--data", required=True, help="Inline JSON with fields to update.")
@click.pass_context
def update(ctx, doctype, name, data):
    c = ctx.obj["session"].client()
    changes = json.loads(data)
    emit(ctx, run_safely(ctx, c.update, doctype, name, changes))


@group.command("submit")
@click.argument("doctype")
@click.argument("name")
@click.pass_context
def submit_cmd(ctx, doctype, name):
    c = ctx.obj["session"].client()
    emit(ctx, run_safely(ctx, c.submit, doctype, name))


@group.command("cancel")
@click.argument("doctype")
@click.argument("name")
@click.pass_context
def cancel(ctx, doctype, name):
    c = ctx.obj["session"].client()
    emit(ctx, run_safely(ctx, c.cancel, doctype, name))


@group.command("delete")
@click.argument("doctype")
@click.argument("name")
@click.pass_context
def delete(ctx, doctype, name):
    c = ctx.obj["session"].client()
    emit(ctx, run_safely(ctx, c.delete, doctype, name))


@group.command("call")
@click.argument("method")
@click.option("--arg", "args", multiple=True,
              help='Argument: "key=value" or "key:=<json>"')
@click.option("--data", help="Inline JSON; merged with --arg.")
@click.pass_context
def call(ctx, method, args, data):
    """Call an arbitrary whitelisted Frappe method by dotted path."""
    kwargs: dict = json.loads(data) if data else {}
    for a in args:
        if ":=" in a:
            k, v = a.split(":=", 1)
            kwargs[k.strip()] = json.loads(v)
        elif "=" in a:
            k, v = a.split("=", 1)
            kwargs[k.strip()] = v
        else:
            raise click.UsageError(f"--arg expects key=value or key:=<json>, got {a!r}")
    c = ctx.obj["session"].client()
    emit(ctx, run_safely(ctx, c.call_method, method, **kwargs))


# ── Helpers ───────────────────────────────────────────────────────────

def _parse_filters(raw: tuple[str, ...]) -> list:
    """Parse ``field=value`` and ``field:op:value`` into Frappe list form."""
    out: list = []
    for expr in raw:
        if ":" in expr and expr.count(":") >= 2:
            parts = expr.split(":", 2)
            field, op, val_raw = parts
            try:
                val = json.loads(val_raw)
            except json.JSONDecodeError:
                val = val_raw
            out.append([field.strip(), op.strip(), val])
            continue
        if "=" in expr:
            field, val = expr.split("=", 1)
            try:
                val_p = json.loads(val)
            except json.JSONDecodeError:
                val_p = val
            out.append([field.strip(), "=", val_p])
            continue
        raise click.UsageError(f"--filter expects field=value or field:op:value, got {expr!r}")
    return out


def _load_json(file_path: str | None, data: str | None) -> dict:
    if file_path and data:
        raise click.UsageError("Use either --file or --data, not both")
    if file_path:
        return json.loads(Path(file_path).expanduser().read_text(encoding="utf-8"))
    if data:
        return json.loads(data)
    raise click.UsageError("Provide --file or --data")
