"""Una sucesora por ocurrencia, calculada desde una fecha ancla estable."""

from calendar import monthrange
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select

from app.models import Task


RECURRENCES = {"none": "No repetir", "daily": "Diaria", "weekly": "Semanal", "monthly": "Mensual", "yearly": "Anual"}


def occurrence_date(anchor, kind, interval, index):
    if kind not in RECURRENCES or kind == "none" or not 1 <= interval <= 365 or index < 0:
        raise ValueError("Regla de recurrencia inválida.")
    offset = interval * index
    try:
        if kind in {"daily", "weekly"}:
            result = anchor + timedelta(days=offset * (7 if kind == "weekly" else 1))
        else:
            months = (anchor.year * 12 + anchor.month - 1) + offset * (12 if kind == "yearly" else 1)
            year, month = divmod(months, 12)
            month += 1
            result = date(year, month, min(anchor.day, monthrange(year, month)[1]))
    except (ValueError, OverflowError):
        return None
    return result if date(1900, 1, 1) <= result <= date(2100, 12, 31) else None


def scheduled_instant(local, zone):
    """Automáticas: fold=0; un hueco DST se desplaza hacia adelante por su duración."""
    first = local.replace(tzinfo=zone, fold=0)
    return first.astimezone(timezone.utc)


def generate_next(db, user, task, zone):
    """El llamador mantiene FOR UPDATE en la tarea y confirma todo conjuntamente.

    La unicidad de parent_task_id refuerza la idempotencia en PostgreSQL.
    No se recorren atrasos: se crea solamente una fila, incluso si ya está vencida.
    """
    if task.user_id != user.id or task.organization_id != user.organization_id:
        raise ValueError("La tarea no pertenece al ámbito indicado.")
    if task.recurrence_type == "none":
        return None
    existing = db.scalar(select(Task).where(Task.organization_id == user.organization_id, Task.user_id == user.id, Task.parent_task_id == task.id))
    if existing is not None:
        return existing
    next_index = task.recurrence_index + 1
    next_date = occurrence_date(task.recurrence_anchor_date, task.recurrence_type, task.recurrence_interval, next_index)
    if next_date is None or (task.recurrence_end_date is not None and next_date > task.recurrence_end_date):
        return None
    next_time = task.due_time
    reminder = None
    if task.reminder_at is not None:
        local_reminder = task.reminder_at.astimezone(zone).replace(tzinfo=None)
        # Sin hora límite se conserva la hora del recordatorio y su distancia en días.
        clock = task.due_time or local_reminder.time()
        offset = datetime.combine(task.due_date, clock) - local_reminder
        local_next_reminder = datetime.combine(next_date, clock) - offset
        reminder = scheduled_instant(local_next_reminder, zone)
    child = Task(
        organization_id=user.organization_id, user_id=user.id, parent_task_id=task.id,
        title=task.title, description=task.description, client_id=task.client_id, project_id=task.project_id,
        priority=task.priority, status="pending", due_date=next_date, due_time=next_time, reminder_at=reminder,
        recurrence_type=task.recurrence_type, recurrence_interval=task.recurrence_interval,
        recurrence_end_date=task.recurrence_end_date, recurrence_anchor_date=task.recurrence_anchor_date,
        recurrence_index=next_index,
    )
    db.add(child)
    db.flush()
    return child
