"""HR domain: employee onboarding + leave application.

Kept deliberately small — HR is a large space, and the most common
agent tasks are "create an employee" and "apply for leave".
"""

from __future__ import annotations

from ..core.client import FrappeClient


def create_employee(
    client: FrappeClient,
    first_name: str,
    *,
    last_name: str | None = None,
    gender: str = "Prefer not to say",
    date_of_birth: str | None = None,
    date_of_joining: str | None = None,
    company: str | None = None,
    department: str | None = None,
    designation: str | None = None,
    status: str = "Active",
    user_id: str | None = None,
    extra: dict | None = None,
) -> dict:
    doc = {
        "doctype": "Employee",
        "first_name": first_name,
        "gender": gender,
        "status": status,
    }
    if last_name: doc["last_name"] = last_name
    if date_of_birth: doc["date_of_birth"] = date_of_birth
    if date_of_joining: doc["date_of_joining"] = date_of_joining
    if company: doc["company"] = company
    if department: doc["department"] = department
    if designation: doc["designation"] = designation
    if user_id: doc["user_id"] = user_id
    if extra: doc.update(extra)
    return client.insert(doc)


def apply_leave(
    client: FrappeClient,
    employee: str,
    *,
    leave_type: str,
    from_date: str,
    to_date: str,
    reason: str | None = None,
    half_day: bool = False,
    submit: bool = True,
    extra: dict | None = None,
) -> dict:
    doc = {
        "doctype": "Leave Application",
        "employee": employee,
        "leave_type": leave_type,
        "from_date": from_date,
        "to_date": to_date,
        "half_day": 1 if half_day else 0,
    }
    if reason: doc["description"] = reason
    if extra: doc.update(extra)
    created = client.insert(doc)
    if submit:
        client.submit("Leave Application", created["name"])
        created = client.get_doc("Leave Application", created["name"])
    return created


def mark_attendance(
    client: FrappeClient,
    employee: str,
    attendance_date: str,
    *,
    status: str = "Present",
    company: str | None = None,
    working_hours: float | None = None,
    submit: bool = True,
    extra: dict | None = None,
) -> dict:
    doc = {
        "doctype": "Attendance",
        "employee": employee,
        "attendance_date": attendance_date,
        "status": status,
    }
    if company: doc["company"] = company
    if working_hours is not None: doc["working_hours"] = working_hours
    if extra: doc.update(extra)
    created = client.insert(doc)
    if submit:
        client.submit("Attendance", created["name"])
        created = client.get_doc("Attendance", created["name"])
    return created


def onboard_employee(
    client: FrappeClient,
    first_name: str,
    *,
    last_name: str | None = None,
    date_of_joining: str,
    company: str,
    department: str | None = None,
    designation: str | None = None,
    gender: str = "Prefer not to say",
    user_id: str | None = None,
) -> dict:
    """Create Employee + initial Attendance row for ``date_of_joining``."""
    emp = create_employee(
        client, first_name, last_name=last_name,
        date_of_joining=date_of_joining,
        company=company, department=department, designation=designation,
        gender=gender, user_id=user_id,
    )
    return {
        "workflow": "onboard_employee",
        "employee": emp["name"],
        "date_of_joining": date_of_joining,
    }
