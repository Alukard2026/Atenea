"""Recordatorios personales calculados sobre las tareas; sin entregas externas."""

from datetime import datetime, time, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import case, func, select

from app import tasks
from app.models import Task
from app.worklog import FormError


ATTENTION_WINDOW = timedelta(minutes=15)
PAGE_SIZE = 30


def scope(user):
    return (Task.organization_id == user.organization_id, Task.user_id == user.id,
            Task.status == "pending", Task.reminder_at.is_not(None))


def status(db, user, now=None):
    now = tasks.aware_now(now)
    pending, overdue, next_at = db.execute(select(
        func.count().filter(Task.reminder_at <= now + ATTENTION_WINDOW),
        func.count().filter(Task.reminder_at < now),
        func.min(Task.reminder_at).filter(Task.reminder_at >= now),
    ).where(*scope(user))).one()
    return {"pending": pending, "overdue": overdue,
            "next_at": next_at.astimezone(timezone.utc).isoformat() if next_at else None}


def listing(db, user, zone, page=1, now=None):
    now = tasks.aware_now(now)
    total = db.scalar(select(func.count()).select_from(Task).where(*scope(user)))
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    if not 1 <= page <= pages:
        raise HTTPException(404, "No se encontró la página solicitada.")
    query = tasks.owned_tasks(user).where(*scope(user)).order_by(
        case((Task.reminder_at < now, 0), else_=1),
        case((Task.priority == "urgent", 0), else_=1), Task.reminder_at, Task.id,
    ).offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE)
    rows = []
    for task in db.scalars(query):
        local = task.reminder_at.astimezone(zone)
        category = "Vencido" if task.reminder_at < now else "Hoy" if local.date() == now.astimezone(zone).date() else "Próximo"
        rows.append({"task": task, "when": local, "category": category})
    return {"rows": rows, "page": page, "pages": pages, "total": total,
            "notification_status": status(db, user, now)}


def snooze(db, user, task_id, choice, zone, now=None):
    now = tasks.aware_now(now)
    if choice not in {"15m", "1h", "tomorrow"}:
        raise FormError("delay", "Selecciona una opción válida para posponer.")
    task = tasks.owned_task(db, user, task_id, lock=True)
    if task.status != "pending" or task.reminder_at is None:
        raise HTTPException(409, "La tarea ya no tiene un recordatorio pendiente.")
    if choice == "tomorrow":
        tomorrow = now.astimezone(zone).date() + timedelta(days=1)
        instant = tasks.local_instant(datetime.combine(tomorrow, time(9)), zone, "delay")
    else:
        instant = now.astimezone(timezone.utc) + timedelta(minutes=15 if choice == "15m" else 60)
    task.reminder_at = instant
    return task
