"""Recordatorios privados de tareas y eventos, calculados sin duplicar registros."""

from datetime import datetime, time, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import case, func, literal, select, union_all

from app import calendar, tasks
from app.models import CalendarEvent, Task
from app.worklog import FormError


ATTENTION_WINDOW = timedelta(minutes=15)
PAGE_SIZE = 30


def scope(user):
    return (Task.organization_id == user.organization_id, Task.user_id == user.id,
            Task.status == "pending", Task.reminder_at.is_not(None))


def reminder_query(user):
    """Un único conjunto paginable; cada rama aplica ambos límites de propietario."""
    return union_all(
        select(literal("task").label("kind"), Task.id.label("id"),
               Task.reminder_at.label("reminder_at"),
               case((Task.priority == "urgent", 0), else_=1).label("rank"))
        .where(*scope(user)),
        select(literal("event"), CalendarEvent.id, CalendarEvent.reminder_at, literal(1))
        .where(CalendarEvent.organization_id == user.organization_id,
               CalendarEvent.user_id == user.id, CalendarEvent.status == "scheduled",
               CalendarEvent.reminder_at.is_not(None)),
    ).subquery()


def status(db, user, now=None):
    now = tasks.aware_now(now)
    reminders = reminder_query(user)
    at = reminders.c.reminder_at
    pending, overdue, next_at = db.execute(select(
        func.count().filter(at <= now + ATTENTION_WINDOW),
        func.count().filter(at < now),
        func.min(at).filter(at >= now),
    ).select_from(reminders)).one()
    return {"pending": pending, "overdue": overdue,
            "next_at": next_at.astimezone(timezone.utc).isoformat() if next_at else None}


def listing(db, user, zone, page=1, now=None):
    now = tasks.aware_now(now)
    reminders = reminder_query(user)
    total = db.scalar(select(func.count()).select_from(reminders))
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    if not 1 <= page <= pages:
        raise HTTPException(404, "No se encontró la página solicitada.")
    query = select(reminders).order_by(
        case((reminders.c.reminder_at < now, 0), else_=1),
        reminders.c.rank, reminders.c.reminder_at, reminders.c.kind, reminders.c.id,
    ).offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE)
    selected = db.execute(query).all()
    task_ids = [r.id for r in selected if r.kind == "task"]
    event_ids = [r.id for r in selected if r.kind == "event"]
    records = {}
    if task_ids:
        records.update({("task", t.id): t for t in db.scalars(tasks.owned_tasks(user).where(Task.id.in_(task_ids), *scope(user)))})
    if event_ids:
        records.update({("event", e.id): e for e in db.scalars(calendar.owned_events(user).where(CalendarEvent.id.in_(event_ids), CalendarEvent.status == "scheduled", CalendarEvent.reminder_at.is_not(None)))})
    rows = []
    for selected_row in selected:
        record = records.get((selected_row.kind, selected_row.id))
        if record is None:  # Cancelado/eliminado entre las consultas de lectura.
            continue
        local = record.reminder_at.astimezone(zone)
        category = "Vencido" if record.reminder_at < now else "Hoy" if local.date() == now.astimezone(zone).date() else "Próximo"
        is_task = selected_row.kind == "task"
        rows.append({selected_row.kind: record, "item": record, "kind": selected_row.kind,
                     "label": "Tarea" if is_task else calendar.EVENT_TYPES[record.event_type],
                     "url": f"/tasks/{record.id}/edit" if is_task else f"/calendar/{record.id}",
                     "action": f"/notifications/{record.id}" if is_task else f"/notifications/events/{record.id}",
                     "when": local, "category": category})
    return {"rows": rows, "page": page, "pages": pages, "total": total,
            "notification_status": status(db, user, now)}


def snooze_instant(choice, zone, now=None):
    now = tasks.aware_now(now)
    if choice not in {"15m", "1h", "tomorrow"}:
        raise FormError("delay", "Selecciona una opción válida para posponer.")
    if choice == "tomorrow":
        tomorrow = now.astimezone(zone).date() + timedelta(days=1)
        instant = tasks.local_instant(datetime.combine(tomorrow, time(9)), zone, "delay")
    else:
        instant = now.astimezone(timezone.utc) + timedelta(minutes=15 if choice == "15m" else 60)
    return instant


def snooze(db, user, task_id, choice, zone, now=None):
    instant = snooze_instant(choice, zone, now)
    task = tasks.owned_task(db, user, task_id, lock=True)
    if task.status != "pending" or task.reminder_at is None:
        raise HTTPException(409, "La tarea ya no tiene un recordatorio pendiente.")
    task.reminder_at = instant
    return task


def snooze_event(db, user, event_id, choice, zone, now=None):
    instant = snooze_instant(choice, zone, now)
    event = calendar.owned_event(db, user, event_id, lock=True)
    if event.status != "scheduled" or event.reminder_at is None:
        raise HTTPException(409, "El evento ya no tiene un recordatorio pendiente.")
    event.reminder_at = instant
    return event
