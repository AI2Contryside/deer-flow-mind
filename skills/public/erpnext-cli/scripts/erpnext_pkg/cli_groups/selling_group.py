"""``selling`` subcommands: quote-to-cash business flows."""

from __future__ import annotations

import click

from ..domains import selling
from ._items import resolve_items
from ._output import emit, run_safely


def _items_options(fn):
    fn = click.option("--items-file", help="Path to JSON file of items")(fn)
    fn = click.option("--items-json", help="Inline JSON array of items")(fn)
    fn = click.option("--item", "item_specs", multiple=True,
                      help="CODE:QTY or CODE:QTY:RATE (repeatable)")(fn)
    return fn


@click.group()
def group():
    """Selling workflows: quotation → order → delivery → invoice → payment."""


@group.command("quote")
@click.option("--customer", required=True)
@_items_options
@click.option("--company")
@click.option("--currency")
@click.option("--valid-till")
@click.option("--submit", is_flag=True)
@click.pass_context
def quote(ctx, customer, item_specs, items_json, items_file, company, currency, valid_till, submit):
    """Create a Quotation for CUSTOMER with one or more items."""
    items = resolve_items(item_specs, items_json, items_file)
    c = ctx.obj["session"].client()
    res = run_safely(
        ctx, selling.create_quotation, c, customer, items,
        company=company or ctx.obj["session"].context.company,
        currency=currency or ctx.obj["session"].context.default_currency,
        valid_till=valid_till, submit=submit,
    )
    emit(ctx, res)


@group.command("order")
@click.option("--customer", required=True)
@_items_options
@click.option("--company")
@click.option("--delivery-date")
@click.option("--currency")
@click.option("--no-submit", is_flag=True, help="Keep as draft (default: submit).")
@click.pass_context
def order(ctx, customer, item_specs, items_json, items_file, company, delivery_date, currency, no_submit):
    """Create a Sales Order."""
    items = resolve_items(item_specs, items_json, items_file)
    c = ctx.obj["session"].client()
    sess = ctx.obj["session"]
    res = run_safely(
        ctx, selling.create_sales_order, c, customer, items,
        company=company or sess.context.company,
        delivery_date=delivery_date,
        currency=currency or sess.context.default_currency,
        submit=not no_submit,
    )
    ctx.obj["session"] = sess.log("sales_order.create", {"customer": customer, "name": res.get("name")})
    ctx.obj["session"].save(ctx.obj["session_file"])
    emit(ctx, res)


@group.command("quote-to-order")
@click.argument("quotation")
@click.option("--no-submit", is_flag=True)
@click.pass_context
def quote_to_order(ctx, quotation, no_submit):
    """Promote QUOTATION to a Sales Order."""
    c = ctx.obj["session"].client()
    emit(ctx, run_safely(ctx, selling.convert_quotation_to_sales_order,
                         c, quotation, submit=not no_submit))


@group.command("deliver")
@click.argument("sales_order")
@click.option("--no-submit", is_flag=True)
@click.pass_context
def deliver(ctx, sales_order, no_submit):
    """Create a Delivery Note from SALES_ORDER."""
    c = ctx.obj["session"].client()
    emit(ctx, run_safely(ctx, selling.create_delivery_from_order,
                         c, sales_order, submit=not no_submit))


@group.command("invoice-from-order")
@click.argument("sales_order")
@click.option("--no-submit", is_flag=True)
@click.pass_context
def invoice_from_order(ctx, sales_order, no_submit):
    """Invoice a Sales Order directly (no Delivery Note)."""
    c = ctx.obj["session"].client()
    emit(ctx, run_safely(ctx, selling.create_invoice_from_order,
                         c, sales_order, submit=not no_submit))


@group.command("invoice-from-delivery")
@click.argument("delivery_note")
@click.option("--no-submit", is_flag=True)
@click.pass_context
def invoice_from_delivery(ctx, delivery_note, no_submit):
    c = ctx.obj["session"].client()
    emit(ctx, run_safely(ctx, selling.create_invoice_from_delivery,
                         c, delivery_note, submit=not no_submit))


@group.command("collect-payment")
@click.argument("sales_invoice")
@click.option("--mode-of-payment")
@click.option("--paid-amount", type=float)
@click.option("--reference-no")
@click.option("--reference-date")
@click.option("--no-submit", is_flag=True)
@click.pass_context
def collect_payment(ctx, sales_invoice, mode_of_payment, paid_amount,
                    reference_no, reference_date, no_submit):
    """Create + submit a Payment Entry against SALES_INVOICE."""
    c = ctx.obj["session"].client()
    emit(ctx, run_safely(
        ctx, selling.collect_payment_for_invoice, c, sales_invoice,
        mode_of_payment=mode_of_payment, paid_amount=paid_amount,
        reference_no=reference_no, reference_date=reference_date,
        submit=not no_submit,
    ))


@group.command("order-to-cash")
@click.option("--customer", required=True)
@_items_options
@click.option("--company")
@click.option("--delivery-date")
@click.option("--mode-of-payment")
@click.pass_context
def order_to_cash(ctx, customer, item_specs, items_json, items_file,
                  company, delivery_date, mode_of_payment):
    """End-to-end: Sales Order → Delivery Note → Invoice → Payment."""
    items = resolve_items(item_specs, items_json, items_file)
    c = ctx.obj["session"].client()
    sess = ctx.obj["session"]
    res = run_safely(
        ctx, selling.order_to_cash, c, customer, items,
        company=company or sess.context.company,
        delivery_date=delivery_date,
        mode_of_payment=mode_of_payment,
    )
    ctx.obj["session"] = sess.log("order_to_cash", res)
    ctx.obj["session"].save(ctx.obj["session_file"])
    emit(ctx, res)


@group.command("quote-to-cash")
@click.option("--customer", required=True)
@_items_options
@click.option("--company")
@click.option("--valid-till")
@click.option("--delivery-date")
@click.option("--mode-of-payment")
@click.pass_context
def quote_to_cash(ctx, customer, item_specs, items_json, items_file,
                  company, valid_till, delivery_date, mode_of_payment):
    """End-to-end: Quotation → SO → DN → SI → PE."""
    items = resolve_items(item_specs, items_json, items_file)
    c = ctx.obj["session"].client()
    sess = ctx.obj["session"]
    res = run_safely(
        ctx, selling.quote_to_cash, c, customer, items,
        company=company or sess.context.company,
        valid_till=valid_till, delivery_date=delivery_date,
        mode_of_payment=mode_of_payment,
    )
    emit(ctx, res)


@group.command("onboard-customer")
@click.option("--customer", required=True)
@_items_options
@click.option("--company")
@click.option("--customer-group", default="All Customer Groups")
@click.option("--territory", default="All Territories")
@click.option("--ensure-items", is_flag=True,
              help="Auto-create Items that don't exist yet.")
@click.pass_context
def onboard_customer(ctx, customer, item_specs, items_json, items_file,
                     company, customer_group, territory, ensure_items):
    """Create Customer (if missing) and raise a Sales Order in one step."""
    items = resolve_items(item_specs, items_json, items_file)
    c = ctx.obj["session"].client()
    sess = ctx.obj["session"]
    res = run_safely(
        ctx, selling.onboard_customer_with_order, c, customer, items,
        company=company or sess.context.company,
        customer_group=customer_group, territory=territory,
        ensure_items=ensure_items,
    )
    emit(ctx, res)


@group.command("dashboard")
@click.argument("customer")
@click.pass_context
def dashboard(ctx, customer):
    """Show open SOs + outstanding invoices for CUSTOMER."""
    c = ctx.obj["session"].client()
    emit(ctx, run_safely(ctx, selling.customer_dashboard, c, customer))


@group.command("list-open")
@click.option("--customer")
@click.option("--company")
@click.option("--limit", default=20, type=int)
@click.pass_context
def list_open(ctx, customer, company, limit):
    """List open (submitted, not closed/completed) Sales Orders."""
    c = ctx.obj["session"].client()
    emit(ctx, run_safely(
        ctx, selling.list_open_sales_orders, c,
        customer=customer, company=company, limit=limit,
    ))
