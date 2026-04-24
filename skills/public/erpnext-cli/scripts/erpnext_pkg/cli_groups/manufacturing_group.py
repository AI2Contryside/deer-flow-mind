"""``manufacturing`` subcommands: BOM → work order → finish."""

from __future__ import annotations

import json
from pathlib import Path

import click

from ..domains import manufacturing
from ._output import emit, run_safely


@click.group()
def group():
    """Manufacturing: BOM, work orders, finished goods."""


@group.command("bom")
@click.option("--item", required=True)
@click.option("--items-json")
@click.option("--items-file")
@click.option("--quantity", default=1.0, type=float)
@click.option("--company")
@click.option("--no-submit", is_flag=True)
@click.pass_context
def bom(ctx, item, items_json, items_file, quantity, company, no_submit):
    """Create a Bill of Materials."""
    if items_file:
        rows = json.loads(Path(items_file).expanduser().read_text(encoding="utf-8"))
    elif items_json:
        rows = json.loads(items_json)
    else:
        raise click.UsageError("Provide --items-json or --items-file")
    c = ctx.obj["session"].client()
    emit(ctx, run_safely(
        ctx, manufacturing.create_bom, c, item, rows,
        quantity=quantity,
        company=company or ctx.obj["session"].context.company,
        submit=not no_submit,
    ))


@group.command("work-order")
@click.option("--bom", required=True)
@click.option("--qty", required=True, type=float)
@click.option("--production-item")
@click.option("--company")
@click.option("--fg-warehouse")
@click.option("--wip-warehouse")
@click.option("--no-submit", is_flag=True)
@click.pass_context
def work_order(ctx, bom, qty, production_item, company, fg_warehouse,
               wip_warehouse, no_submit):
    c = ctx.obj["session"].client()
    emit(ctx, run_safely(
        ctx, manufacturing.create_work_order, c, bom, qty,
        production_item=production_item,
        company=company or ctx.obj["session"].context.company,
        fg_warehouse=fg_warehouse, wip_warehouse=wip_warehouse,
        submit=not no_submit,
    ))


@group.command("issue-materials")
@click.argument("work_order")
@click.option("--qty", type=float)
@click.option("--no-submit", is_flag=True)
@click.pass_context
def issue_materials(ctx, work_order, qty, no_submit):
    c = ctx.obj["session"].client()
    emit(ctx, run_safely(
        ctx, manufacturing.issue_raw_materials, c, work_order,
        qty=qty, submit=not no_submit,
    ))


@group.command("finish")
@click.argument("work_order")
@click.option("--qty", type=float)
@click.option("--no-submit", is_flag=True)
@click.pass_context
def finish(ctx, work_order, qty, no_submit):
    c = ctx.obj["session"].client()
    emit(ctx, run_safely(
        ctx, manufacturing.finish_work_order, c, work_order,
        qty=qty, submit=not no_submit,
    ))


@group.command("make-from-bom")
@click.option("--bom", required=True)
@click.option("--qty", required=True, type=float)
@click.option("--company")
@click.option("--fg-warehouse")
@click.option("--wip-warehouse")
@click.option("--skip-material-issue", is_flag=True)
@click.pass_context
def make_from_bom(ctx, bom, qty, company, fg_warehouse, wip_warehouse,
                  skip_material_issue):
    """End-to-end: BOM → Work Order → (optional transfer) → Manufacture."""
    c = ctx.obj["session"].client()
    emit(ctx, run_safely(
        ctx, manufacturing.make_from_bom, c, bom, qty,
        company=company or ctx.obj["session"].context.company,
        fg_warehouse=fg_warehouse, wip_warehouse=wip_warehouse,
        issue_materials=not skip_material_issue,
    ))
