"""``stock`` subcommands: material movements + warehouse queries."""

from __future__ import annotations

import json

import click

from ..domains import stock
from ._items import resolve_items
from ._output import emit, run_safely


def _items_options(fn):
    fn = click.option("--items-file", help="Path to JSON file of items")(fn)
    fn = click.option("--items-json", help="Inline JSON array of items")(fn)
    fn = click.option("--item", "item_specs", multiple=True,
                      help="CODE:QTY (repeatable)")(fn)
    return fn


@click.group()
def group():
    """Stock movements and warehouse queries."""


@group.command("transfer")
@_items_options
@click.option("--source", "source_warehouse", required=True)
@click.option("--target", "target_warehouse", required=True)
@click.option("--company")
@click.option("--posting-date")
@click.option("--no-submit", is_flag=True)
@click.pass_context
def transfer(ctx, item_specs, items_json, items_file,
             source_warehouse, target_warehouse, company, posting_date, no_submit):
    """Move stock from --source to --target warehouse."""
    items = resolve_items(item_specs, items_json, items_file)
    c = ctx.obj["session"].client()
    emit(ctx, run_safely(
        ctx, stock.material_transfer, c, items,
        source_warehouse=source_warehouse, target_warehouse=target_warehouse,
        company=company or ctx.obj["session"].context.company,
        posting_date=posting_date, submit=not no_submit,
    ))


@group.command("issue")
@_items_options
@click.option("--source", "source_warehouse", required=True)
@click.option("--company")
@click.option("--posting-date")
@click.option("--no-submit", is_flag=True)
@click.pass_context
def issue(ctx, item_specs, items_json, items_file,
          source_warehouse, company, posting_date, no_submit):
    """Issue (consume / write-off) stock out of a warehouse."""
    items = resolve_items(item_specs, items_json, items_file)
    c = ctx.obj["session"].client()
    emit(ctx, run_safely(
        ctx, stock.material_issue, c, items,
        source_warehouse=source_warehouse,
        company=company or ctx.obj["session"].context.company,
        posting_date=posting_date, submit=not no_submit,
    ))


@group.command("receipt")
@_items_options
@click.option("--target", "target_warehouse", required=True)
@click.option("--company")
@click.option("--posting-date")
@click.option("--no-submit", is_flag=True)
@click.pass_context
def receipt(ctx, item_specs, items_json, items_file,
            target_warehouse, company, posting_date, no_submit):
    """Receive stock into a warehouse (outside a Purchase Receipt)."""
    items = resolve_items(item_specs, items_json, items_file)
    c = ctx.obj["session"].client()
    emit(ctx, run_safely(
        ctx, stock.material_receipt, c, items,
        target_warehouse=target_warehouse,
        company=company or ctx.obj["session"].context.company,
        posting_date=posting_date, submit=not no_submit,
    ))


@group.command("reconcile")
@click.option("--items-json", help="Inline JSON of reconciliation rows "
              "(each needs item_code, warehouse, qty, valuation_rate).")
@click.option("--items-file")
@click.option("--company")
@click.option("--posting-date")
@click.option("--no-submit", is_flag=True)
@click.pass_context
def reconcile(ctx, items_json, items_file, company, posting_date, no_submit):
    """Adjust stock quantities to a physical count."""
    if items_file:
        from pathlib import Path
        rows = json.loads(Path(items_file).expanduser().read_text(encoding="utf-8"))
    elif items_json:
        rows = json.loads(items_json)
    else:
        raise click.UsageError("Provide --items-json or --items-file")
    c = ctx.obj["session"].client()
    emit(ctx, run_safely(
        ctx, stock.stock_reconciliation, c, rows,
        company=company or ctx.obj["session"].context.company,
        posting_date=posting_date, submit=not no_submit,
    ))


@group.command("levels")
@click.option("--item", "item_code")
@click.option("--warehouse")
@click.option("--company")
@click.option("--limit", default=100, type=int)
@click.pass_context
def levels(ctx, item_code, warehouse, company, limit):
    c = ctx.obj["session"].client()
    emit(ctx, run_safely(
        ctx, stock.get_stock_levels, c,
        item_code=item_code, warehouse=warehouse,
        company=company, limit=limit,
    ))


@group.command("warehouse")
@click.argument("warehouse")
@click.pass_context
def warehouse(ctx, warehouse):
    c = ctx.obj["session"].client()
    emit(ctx, run_safely(ctx, stock.warehouse_summary, c, warehouse))
