"""``crm`` subcommands: lead → opportunity → quotation / customer."""

from __future__ import annotations

import click

from ..domains import crm
from ._items import resolve_items
from ._output import emit, run_safely


def _items_options(fn):
    fn = click.option("--items-file")(fn)
    fn = click.option("--items-json")(fn)
    fn = click.option("--item", "item_specs", multiple=True,
                      help="CODE:QTY or CODE:QTY:RATE (repeatable)")(fn)
    return fn


@click.group()
def group():
    """CRM workflows: lead → opportunity → quotation / customer."""


@group.command("lead")
@click.option("--name", "lead_name", required=True)
@click.option("--company-name")
@click.option("--email")
@click.option("--mobile")
@click.option("--source")
@click.pass_context
def lead(ctx, lead_name, company_name, email, mobile, source):
    c = ctx.obj["session"].client()
    emit(ctx, run_safely(
        ctx, crm.create_lead, c, lead_name,
        company_name=company_name, email_id=email,
        mobile_no=mobile, source=source,
    ))


@group.command("lead-to-opportunity")
@click.argument("lead")
@click.pass_context
def lead_to_opp(ctx, lead):
    c = ctx.obj["session"].client()
    emit(ctx, run_safely(ctx, crm.convert_lead_to_opportunity, c, lead))


@group.command("opportunity-to-quote")
@click.argument("opportunity")
@_items_options
@click.pass_context
def opp_to_quote(ctx, opportunity, item_specs, items_json, items_file):
    try:
        items = resolve_items(item_specs, items_json, items_file)
    except ValueError:
        items = None
    c = ctx.obj["session"].client()
    emit(ctx, run_safely(
        ctx, crm.convert_opportunity_to_quotation, c, opportunity, items=items,
    ))


@group.command("lead-to-customer")
@click.argument("lead")
@click.option("--customer-group", default="All Customer Groups")
@click.option("--territory", default="All Territories")
@click.pass_context
def lead_to_customer(ctx, lead, customer_group, territory):
    c = ctx.obj["session"].client()
    emit(ctx, run_safely(
        ctx, crm.convert_lead_to_customer, c, lead,
        customer_group=customer_group, territory=territory,
    ))


@group.command("lead-to-quotation")
@click.option("--name", "lead_name", required=True)
@_items_options
@click.option("--company-name")
@click.option("--email")
@click.option("--mobile")
@click.pass_context
def lead_to_quotation(ctx, lead_name, item_specs, items_json, items_file,
                      company_name, email, mobile):
    items = resolve_items(item_specs, items_json, items_file)
    c = ctx.obj["session"].client()
    emit(ctx, run_safely(
        ctx, crm.lead_to_quotation, c, lead_name, items,
        email_id=email, mobile_no=mobile, company_name=company_name,
    ))
