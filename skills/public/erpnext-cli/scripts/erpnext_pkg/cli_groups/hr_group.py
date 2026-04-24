"""``hr`` subcommands: employees, leave, attendance."""

from __future__ import annotations

import click

from ..domains import hr
from ._output import emit, run_safely


@click.group()
def group():
    """HR: employees, leave, attendance."""


@group.command("employee")
@click.option("--first-name", required=True)
@click.option("--last-name")
@click.option("--gender", default="Prefer not to say")
@click.option("--dob", "date_of_birth")
@click.option("--joining", "date_of_joining")
@click.option("--company")
@click.option("--department")
@click.option("--designation")
@click.option("--user-id")
@click.pass_context
def employee(ctx, first_name, last_name, gender, date_of_birth, date_of_joining,
             company, department, designation, user_id):
    c = ctx.obj["session"].client()
    emit(ctx, run_safely(
        ctx, hr.create_employee, c, first_name,
        last_name=last_name, gender=gender,
        date_of_birth=date_of_birth, date_of_joining=date_of_joining,
        company=company or ctx.obj["session"].context.company,
        department=department, designation=designation, user_id=user_id,
    ))


@group.command("leave")
@click.option("--employee", required=True)
@click.option("--type", "leave_type", required=True)
@click.option("--from", "from_date", required=True)
@click.option("--to", "to_date", required=True)
@click.option("--reason")
@click.option("--half-day", is_flag=True)
@click.option("--no-submit", is_flag=True)
@click.pass_context
def leave(ctx, employee, leave_type, from_date, to_date, reason, half_day, no_submit):
    c = ctx.obj["session"].client()
    emit(ctx, run_safely(
        ctx, hr.apply_leave, c, employee,
        leave_type=leave_type, from_date=from_date, to_date=to_date,
        reason=reason, half_day=half_day, submit=not no_submit,
    ))


@group.command("attendance")
@click.option("--employee", required=True)
@click.option("--date", "attendance_date", required=True)
@click.option("--status", default="Present",
              type=click.Choice(["Present", "Absent", "On Leave", "Half Day", "Work From Home"]))
@click.option("--hours", "working_hours", type=float)
@click.option("--company")
@click.option("--no-submit", is_flag=True)
@click.pass_context
def attendance(ctx, employee, attendance_date, status, working_hours, company, no_submit):
    c = ctx.obj["session"].client()
    emit(ctx, run_safely(
        ctx, hr.mark_attendance, c, employee, attendance_date,
        status=status, working_hours=working_hours,
        company=company or ctx.obj["session"].context.company,
        submit=not no_submit,
    ))


@group.command("onboard")
@click.option("--first-name", required=True)
@click.option("--last-name")
@click.option("--joining", "date_of_joining", required=True)
@click.option("--company", required=True)
@click.option("--department")
@click.option("--designation")
@click.option("--gender", default="Prefer not to say")
@click.option("--user-id")
@click.pass_context
def onboard(ctx, first_name, last_name, date_of_joining, company,
            department, designation, gender, user_id):
    c = ctx.obj["session"].client()
    emit(ctx, run_safely(
        ctx, hr.onboard_employee, c, first_name,
        last_name=last_name, date_of_joining=date_of_joining,
        company=company, department=department, designation=designation,
        gender=gender, user_id=user_id,
    ))
