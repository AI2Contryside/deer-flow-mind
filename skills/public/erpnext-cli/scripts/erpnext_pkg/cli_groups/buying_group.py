"""``buying`` subcommands: procure-to-pay business flows."""

from __future__ import annotations

import click

from ..domains import buying
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
    """Buying workflows: material request → PO → receipt → invoice → payment."""


@group.command("material-request")
@_items_options
@click.option("--purpose", default="Purchase",
              type=click.Choice(["Purchase", "Material Transfer", "Material Issue",
                                 "Manufacture", "Customer Provided"]))
@click.option("--company")
@click.option("--schedule-date")
@click.option("--no-submit", is_flag=True)
@click.pass_context
def material_request(ctx, item_specs, items_json, items_file,
                     purpose, company, schedule_date, no_submit):
    """Create a Material Request."""
    items = resolve_items(item_specs, items_json, items_file)
    c = ctx.obj["session"].client()
    emit(ctx, run_safely(
        ctx, buying.create_material_request, c, items,
        purpose=purpose,
        company=company or ctx.obj["session"].context.company,
        schedule_date=schedule_date, submit=not no_submit,
    ))


@group.command("order")
@click.option("--supplier", required=True)
@_items_options
@click.option("--company")
@click.option("--schedule-date")
@click.option("--currency")
@click.option("--no-submit", is_flag=True)
@click.pass_context
def order(ctx, supplier, item_specs, items_json, items_file,
          company, schedule_date, currency, no_submit):
    """Create a Purchase Order."""
    items = resolve_items(item_specs, items_json, items_file)
    c = ctx.obj["session"].client()
    sess = ctx.obj["session"]
    res = run_safely(
        ctx, buying.create_purchase_order, c, supplier, items,
        company=company or sess.context.company,
        schedule_date=schedule_date,
        currency=currency or sess.context.default_currency,
        submit=not no_submit,
    )
    ctx.obj["session"] = sess.log("purchase_order.create",
                                  {"supplier": supplier, "name": res.get("name")})
    ctx.obj["session"].save(ctx.obj["session_file"])
    emit(ctx, res)


@group.command("supplier-quote")
@click.option("--supplier", required=True)
@_items_options
@click.option("--company")
@click.option("--valid-till")
@click.option("--submit", is_flag=True)
@click.pass_context
def supplier_quote(ctx, supplier, item_specs, items_json, items_file,
                   company, valid_till, submit):
    """Create a Supplier Quotation."""
    items = resolve_items(item_specs, items_json, items_file)
    c = ctx.obj["session"].client()
    emit(ctx, run_safely(
        ctx, buying.create_supplier_quotation, c, supplier, items,
        company=company or ctx.obj["session"].context.company,
        valid_till=valid_till, submit=submit,
    ))


@group.command("rfq-from-mr")
@click.argument("material_request")
@click.option("--supplier", "suppliers", multiple=True,
              help="Suppliers to request quotes from (repeatable).")
@click.pass_context
def rfq_from_mr(ctx, material_request, suppliers):
    c = ctx.obj["session"].client()
    emit(ctx, run_safely(
        ctx, buying.create_rfq_from_mr, c, material_request,
        suppliers=list(suppliers) or None,
    ))


@group.command("po-from-mr")
@click.argument("material_request")
@click.option("--no-submit", is_flag=True)
@click.pass_context
def po_from_mr(ctx, material_request, no_submit):
    c = ctx.obj["session"].client()
    emit(ctx, run_safely(ctx, buying.create_po_from_mr,
                         c, material_request, submit=not no_submit))


@group.command("po-from-sq")
@click.argument("supplier_quotation")
@click.option("--no-submit", is_flag=True)
@click.pass_context
def po_from_sq(ctx, supplier_quotation, no_submit):
    c = ctx.obj["session"].client()
    emit(ctx, run_safely(ctx, buying.convert_sq_to_po,
                         c, supplier_quotation, submit=not no_submit))


@group.command("receive")
@click.argument("purchase_order")
@click.option("--no-submit", is_flag=True)
@click.pass_context
def receive(ctx, purchase_order, no_submit):
    """Create a Purchase Receipt from PURCHASE_ORDER."""
    c = ctx.obj["session"].client()
    emit(ctx, run_safely(ctx, buying.create_receipt_from_po,
                         c, purchase_order, submit=not no_submit))


@group.command("bill-from-po")
@click.argument("purchase_order")
@click.option("--no-submit", is_flag=True)
@click.pass_context
def bill_from_po(ctx, purchase_order, no_submit):
    c = ctx.obj["session"].client()
    emit(ctx, run_safely(ctx, buying.create_bill_from_po,
                         c, purchase_order, submit=not no_submit))


@group.command("bill-from-receipt")
@click.argument("purchase_receipt")
@click.option("--no-submit", is_flag=True)
@click.pass_context
def bill_from_receipt(ctx, purchase_receipt, no_submit):
    c = ctx.obj["session"].client()
    emit(ctx, run_safely(ctx, buying.create_bill_from_receipt,
                         c, purchase_receipt, submit=not no_submit))


@group.command("pay")
@click.argument("purchase_invoice")
@click.option("--mode-of-payment")
@click.option("--paid-amount", type=float)
@click.option("--reference-no")
@click.option("--reference-date")
@click.option("--no-submit", is_flag=True)
@click.pass_context
def pay(ctx, purchase_invoice, mode_of_payment, paid_amount,
        reference_no, reference_date, no_submit):
    """Pay a supplier against PURCHASE_INVOICE."""
    c = ctx.obj["session"].client()
    emit(ctx, run_safely(
        ctx, buying.pay_supplier_bill, c, purchase_invoice,
        mode_of_payment=mode_of_payment, paid_amount=paid_amount,
        reference_no=reference_no, reference_date=reference_date,
        submit=not no_submit,
    ))


@group.command("procure-to-pay")
@click.option("--supplier", required=True)
@_items_options
@click.option("--company")
@click.option("--schedule-date")
@click.option("--mode-of-payment")
@click.pass_context
def procure_to_pay(ctx, supplier, item_specs, items_json, items_file,
                   company, schedule_date, mode_of_payment):
    """End-to-end: PO → Receipt → Invoice → Payment."""
    items = resolve_items(item_specs, items_json, items_file)
    c = ctx.obj["session"].client()
    sess = ctx.obj["session"]
    res = run_safely(
        ctx, buying.procure_to_pay, c, supplier, items,
        company=company or sess.context.company,
        schedule_date=schedule_date,
        mode_of_payment=mode_of_payment,
    )
    ctx.obj["session"] = sess.log("procure_to_pay", res)
    ctx.obj["session"].save(ctx.obj["session_file"])
    emit(ctx, res)


@group.command("onboard-supplier")
@click.option("--supplier", required=True)
@_items_options
@click.option("--company")
@click.option("--supplier-group", default="All Supplier Groups")
@click.option("--ensure-items", is_flag=True)
@click.pass_context
def onboard_supplier(ctx, supplier, item_specs, items_json, items_file,
                     company, supplier_group, ensure_items):
    items = resolve_items(item_specs, items_json, items_file)
    c = ctx.obj["session"].client()
    emit(ctx, run_safely(
        ctx, buying.onboard_supplier_with_order, c, supplier, items,
        company=company or ctx.obj["session"].context.company,
        supplier_group=supplier_group, ensure_items=ensure_items,
    ))


@group.command("dashboard")
@click.argument("supplier")
@click.pass_context
def dashboard(ctx, supplier):
    c = ctx.obj["session"].client()
    emit(ctx, run_safely(ctx, buying.supplier_dashboard, c, supplier))


@group.command("list-open")
@click.option("--supplier")
@click.option("--company")
@click.option("--limit", default=20, type=int)
@click.pass_context
def list_open(ctx, supplier, company, limit):
    c = ctx.obj["session"].client()
    emit(ctx, run_safely(
        ctx, buying.list_open_purchase_orders, c,
        supplier=supplier, company=company, limit=limit,
    ))
