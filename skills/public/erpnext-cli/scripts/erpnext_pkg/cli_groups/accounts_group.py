"""``accounts`` subcommands: payments, journal entries, reconciliation."""

from __future__ import annotations

import json
from pathlib import Path

import click

from ..domains import accounts
from ._output import emit, run_safely


@click.group()
def group():
    """Accounting: AR, AP, payments, journal entries."""


@group.command("receive-payment")
@click.argument("sales_invoice")
@click.option("--mode-of-payment")
@click.option("--paid-amount", type=float)
@click.option("--reference-no")
@click.option("--reference-date")
@click.option("--no-submit", is_flag=True)
@click.pass_context
def receive_payment(ctx, sales_invoice, mode_of_payment, paid_amount,
                    reference_no, reference_date, no_submit):
    c = ctx.obj["session"].client()
    emit(ctx, run_safely(
        ctx, accounts.receive_payment, c, sales_invoice,
        mode_of_payment=mode_of_payment, paid_amount=paid_amount,
        reference_no=reference_no, reference_date=reference_date,
        submit=not no_submit,
    ))


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
    c = ctx.obj["session"].client()
    emit(ctx, run_safely(
        ctx, accounts.make_payment, c, purchase_invoice,
        mode_of_payment=mode_of_payment, paid_amount=paid_amount,
        reference_no=reference_no, reference_date=reference_date,
        submit=not no_submit,
    ))


@group.command("journal")
@click.option("--accounts-json", help="Inline JSON array of account rows.")
@click.option("--accounts-file")
@click.option("--company")
@click.option("--posting-date")
@click.option("--remark")
@click.option("--no-submit", is_flag=True)
@click.pass_context
def journal(ctx, accounts_json, accounts_file, company, posting_date, remark, no_submit):
    """Post a balanced Journal Entry.

    Accounts rows need: account, debit_in_account_currency or
    credit_in_account_currency (+ party_type/party where applicable).
    """
    if accounts_file:
        rows = json.loads(Path(accounts_file).expanduser().read_text(encoding="utf-8"))
    elif accounts_json:
        rows = json.loads(accounts_json)
    else:
        raise click.UsageError("Provide --accounts-json or --accounts-file")
    c = ctx.obj["session"].client()
    emit(ctx, run_safely(
        ctx, accounts.make_journal_entry, c, rows,
        company=company or ctx.obj["session"].context.company,
        posting_date=posting_date, user_remark=remark,
        submit=not no_submit,
    ))


@group.command("reconcile")
@click.option("--party-type", required=True, type=click.Choice(["Customer", "Supplier"]))
@click.option("--party", required=True)
@click.option("--company", required=True)
@click.option("--from-date")
@click.option("--to-date")
@click.option("--receivable-payable-account")
@click.pass_context
def reconcile(ctx, party_type, party, company, from_date, to_date,
              receivable_payable_account):
    c = ctx.obj["session"].client()
    emit(ctx, run_safely(
        ctx, accounts.reconcile_payments, c,
        party_type=party_type, party=party, company=company,
        from_date=from_date, to_date=to_date,
        receivable_payable_account=receivable_payable_account,
    ))


@group.command("ar")
@click.option("--customer")
@click.option("--company")
@click.option("--limit", default=100, type=int)
@click.pass_context
def ar(ctx, customer, company, limit):
    """Accounts Receivable (outstanding Sales Invoices)."""
    c = ctx.obj["session"].client()
    emit(ctx, run_safely(
        ctx, accounts.accounts_receivable, c,
        customer=customer,
        company=company or ctx.obj["session"].context.company,
        limit=limit,
    ))


@group.command("ap")
@click.option("--supplier")
@click.option("--company")
@click.option("--limit", default=100, type=int)
@click.pass_context
def ap(ctx, supplier, company, limit):
    """Accounts Payable (outstanding Purchase Invoices)."""
    c = ctx.obj["session"].client()
    emit(ctx, run_safely(
        ctx, accounts.accounts_payable, c,
        supplier=supplier,
        company=company or ctx.obj["session"].context.company,
        limit=limit,
    ))


@group.command("snapshot")
@click.option("--company")
@click.pass_context
def snapshot(ctx, company):
    """Quick AR/AP position for the company."""
    company = company or ctx.obj["session"].context.company
    if not company:
        raise click.UsageError("--company is required (or set one via 'session set-context').")
    c = ctx.obj["session"].client()
    emit(ctx, run_safely(ctx, accounts.cashflow_snapshot, c, company))
