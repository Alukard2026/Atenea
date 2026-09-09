"""Datos de informes personales, compartidos por las vistas y los Excel."""

from datetime import timedelta
from decimal import Decimal

from app.models import TimeEntry
from app.worklog import MAX_DATE, MIN_DATE, monday_for, month_bounds, owned_entries, weekly_view


MONTH_NAMES = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre")
ZERO = Decimal("0.00")


def totals(entries):
    billable = sum((entry.hours for entry in entries if entry.billable), ZERO)
    non_billable = sum((entry.hours for entry in entries if not entry.billable), ZERO)
    return {"total": billable + non_billable, "billable_total": billable, "non_billable_total": non_billable}


def week_report(db, user, anchor):
    report = weekly_view(db, user, anchor)
    entries = [entry for day in report["days"] for entry in day["entries"]]
    return {**report, "entries": entries, **totals(entries)}


def month_report(db, user, month=""):
    first, end = month_bounds(month)
    calendar_start = monday_for(first)
    calendar_end = monday_for(end - timedelta(days=1)) + timedelta(days=7)
    all_entries = db.scalars(owned_entries(user).where(
        TimeEntry.work_date >= calendar_start, TimeEntry.work_date < calendar_end,
    ).order_by(TimeEntry.work_date, TimeEntry.created_at, TimeEntry.id)).all()
    entries = [entry for entry in all_entries if first <= entry.work_date < end]
    weeks = []
    by_week = {}
    start = calendar_start
    while start < end:
        week = {"start": start, "end": start + timedelta(days=6), "total": ZERO, "month_total": ZERO}
        weeks.append(week)
        by_week[start] = week
        start += timedelta(days=7)
    for entry in all_entries:
        week = by_week[monday_for(entry.work_date)]
        week["total"] += entry.hours
        if first <= entry.work_date < end:
            week["month_total"] += entry.hours
    clients = {}
    for entry in entries:
        client = clients.setdefault(entry.client_id, {"name": entry.client.name, "code": entry.client.code or "", "total": ZERO})
        client["total"] += entry.hours
    previous = first - timedelta(days=1)
    return {
        "month": first.strftime("%Y-%m"), "month_label": f"{MONTH_NAMES[first.month - 1].capitalize()} {first.year}",
        "start": first, "end": end - timedelta(days=1), "entries": entries,
        "weeks": weeks, "clients": sorted(clients.values(), key=lambda client: (client["name"].casefold(), client["code"])),
        "active_days": len({entry.work_date for entry in entries}), **totals(entries),
        "previous_month": previous.strftime("%Y-%m") if previous >= MIN_DATE else None,
        "next_month": end.strftime("%Y-%m") if end <= MAX_DATE else None,
    }
