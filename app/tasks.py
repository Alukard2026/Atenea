"""Tareas personales y recordatorios: ámbitos explícitos, sin envíos ni scheduler."""

from datetime import datetime, time, timedelta, timezone
import re

from fastapi import HTTPException
from sqlalchemy import case, select
from sqlalchemy.orm import joinedload

from app.models import Client, Organization, Project, Task, User
from app.recurrence import RECURRENCES, generate_next, scheduled_instant
from app.timezones import organization_zone
from app.worklog import FormError, clean_text, organization_clients, parse_date, parse_id


PRIORITIES = {"urgent": "Urgente", "high": "Alta", "normal": "Normal", "low": "Baja"}
STATUSES = {"pending": "Pendiente", "completed": "Completada", "cancelled": "Cancelada"}
PERIODS = {"overdue": "Vencidas", "today": "Hoy", "tomorrow": "Mañana", "next7": "Próximos 7 días", "later": "Posteriores", "undated": "Sin fecha"}


def utc_now():
    return datetime.now(timezone.utc)


def aware_now(now=None):
    now = now if now is not None else utc_now()
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Se requiere un instante con zona horaria.")
    return now


def owned_tasks(user):
    return select(Task).where(Task.organization_id == user.organization_id, Task.user_id == user.id).options(joinedload(Task.client), joinedload(Task.project))


def owned_task(db, user, task_id, *, lock=False):
    if not 0 < task_id <= 2**31 - 1:
        raise HTTPException(404, "No se encontró la tarea solicitada.")
    query = owned_tasks(user).where(Task.id == task_id)
    if lock:
        query = query.with_for_update(of=Task)
    task = db.scalar(query)
    if task is None:
        raise HTTPException(404, "No se encontró la tarea solicitada.")
    return task


def local_instant(value, zone, field):
    """Rechazar horas inexistentes o ambiguas por cambio estacional, no adivinarlas."""
    first, second = value.replace(tzinfo=zone, fold=0), value.replace(tzinfo=zone, fold=1)
    if first.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) != value or first.utcoffset() != second.utcoffset():
        raise FormError(field, "Esa hora es ambigua o no existe por el cambio horario. Selecciona otra hora.")
    return first.astimezone(timezone.utc)


def save_task(db, user, data, zone, task_id=None):
    allowed = {"csrf", "title", "description", "due_date", "due_time", "priority", "client_id", "project_id", "reminder_at",
               "recurrence_type", "recurrence_interval", "recurrence_end_date"}
    if data.keys() - allowed:
        raise HTTPException(400, "El formulario contiene campos no permitidos.")
    task = owned_task(db, user, task_id, lock=True) if task_id is not None else None
    title = clean_text(data, "title", "el título", 255, required=True)
    description = clean_text(data, "description", "la descripción", 5000, multiline=True)
    priority = data.get("priority", "normal")
    if priority not in PRIORITIES:
        raise FormError("priority", "Selecciona una prioridad válida.")
    due_date = parse_date(data["due_date"], "due_date") if data.get("due_date") else None
    kind = data.get("recurrence_type", task.recurrence_type if task else "none")
    raw_interval = data.get("recurrence_interval", str(task.recurrence_interval) if task else "1")
    if kind not in RECURRENCES or not re.fullmatch(r"[0-9]{1,3}", raw_interval) or not 1 <= int(raw_interval) <= 365:
        raise FormError("recurrence_type", "Selecciona una repetición e intervalo válidos (1 a 365).")
    interval = int(raw_interval)
    raw_end = data.get("recurrence_end_date", task.recurrence_end_date.isoformat() if task and task.recurrence_end_date else "")
    end_date = parse_date(raw_end, "recurrence_end_date") if raw_end else None
    if kind != "none" and due_date is None:
        raise FormError("due_date", "Una tarea recurrente necesita una fecha inicial.")
    if kind == "none":
        interval, end_date = 1, None
    changed = task is None or (kind, interval, end_date) != (task.recurrence_type, task.recurrence_interval, task.recurrence_end_date)
    if task is not None and changed:
        child_id = db.scalar(select(Task.id).where(Task.organization_id == user.organization_id, Task.user_id == user.id, Task.parent_task_id == task.id))
        if task.status != "pending" or child_id is not None:
            raise FormError("recurrence_type", "La regla solo puede cambiarse en una pendiente sin sucesora. Edita la siguiente ocurrencia.")
    anchor = (due_date if kind != "none" else None) if changed else task.recurrence_anchor_date
    if end_date is not None and (end_date < anchor or end_date < due_date):
        raise FormError("recurrence_end_date", "El fin de recurrencia no puede ser anterior a la fecha de la tarea ni a su fecha inicial.")
    due_time = None
    if data.get("due_time"):
        if due_date is None:
            raise FormError("due_date", "Indica una fecha si deseas indicar hora.")
        try:
            if not re.fullmatch(r"\d{2}:\d{2}", data["due_time"]):
                raise ValueError
            due_time = time.fromisoformat(data["due_time"])
        except ValueError:
            raise FormError("due_time", "Introduce una hora válida (HH:MM).") from None
        retained_automatic = task is not None and task.parent_task_id is not None and (due_date, due_time) == (task.due_date, task.due_time)
        if not retained_automatic:
            local_instant(datetime.combine(due_date, due_time), zone, "due_time")
    reminder = None
    if data.get("reminder_at"):
        raw = data["reminder_at"]
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", raw):
            raise FormError("reminder_at", "Indica fecha y hora válidas para el recordatorio.")
        parse_date(raw[:10], "reminder_at")
        try:
            reminder = local_instant(datetime.fromisoformat(raw), zone, "reminder_at")
        except ValueError as error:
            if isinstance(error, FormError):
                raise
            raise FormError("reminder_at", "Indica fecha y hora válidas para el recordatorio.") from None
    client_id = parse_id(data["client_id"], "client_id", "un cliente") if data.get("client_id") else None
    project_id = parse_id(data["project_id"], "project_id", "un proyecto") if data.get("project_id") else None
    if client_id is not None:
        client = db.scalar(select(Client).where(Client.id == client_id, Client.organization_id == user.organization_id))
        if client is None or (not client.is_active and not (task and task.client_id == client_id)):
            raise FormError("client_id", "Selecciona un cliente activo de tu organización.")
    if project_id is not None:
        project = db.scalar(select(Project).where(Project.id == project_id, Project.organization_id == user.organization_id, Project.client_id == client_id))
        retained = task and task.client_id == client_id and task.project_id == project_id
        if client_id is None or project is None or (not project.is_active and not retained):
            raise FormError("project_id", "Selecciona primero un cliente y después un proyecto activo de ese cliente.")
    if task is None:
        task = Task(organization_id=user.organization_id, user_id=user.id, status="pending")
        db.add(task)
    for field, value in dict(title=title, description=description, priority=priority, due_date=due_date, due_time=due_time,
                             reminder_at=reminder, client_id=client_id, project_id=project_id,
                             recurrence_type=kind, recurrence_interval=interval, recurrence_end_date=end_date,
                             recurrence_anchor_date=anchor, recurrence_index=0 if changed else task.recurrence_index).items():
        setattr(task, field, value)
    return task


def set_status(db, user, task_id, status, now=None, zone=None):
    if status not in STATUSES:
        raise FormError("status", "Selecciona un estado válido.")
    task = owned_task(db, user, task_id, lock=True)
    if task.status != status:
        task.status = status
        task.completed_at = aware_now(now).astimezone(timezone.utc) if status == "completed" else None
    if status in {"completed", "cancelled"}:
        generate_next(db, user, task, zone or organization_zone(user.organization))
    return task


def period_for(task, now, zone):
    now = aware_now(now)
    local = now.astimezone(zone)
    if task.due_date is None:
        return "undated"
    difference = (task.due_date - local.date()).days
    if difference < 0 or (difference == 0 and task.due_time is not None and scheduled_instant(datetime.combine(task.due_date, task.due_time), zone) < now):
        return "overdue"
    if difference == 0:
        return "today"
    if difference == 1:
        return "tomorrow"
    return "next7" if difference <= 7 else "later"


def task_list(db, user, zone, *, status="all", priority="", client_id="", period="", now=None):
    now = aware_now(now)
    if status not in {"all", *STATUSES} or (priority and priority not in PRIORITIES) or (period and period not in PERIODS):
        raise FormError("filters", "Selecciona filtros válidos.")
    query = owned_tasks(user)
    if status != "all":
        query = query.where(Task.status == status)
    if priority:
        query = query.where(Task.priority == priority)
    clients = organization_clients(db, user)
    if client_id:
        identity = parse_id(client_id, "client_id", "un cliente")
        if not any(client.id == identity for client in clients):
            raise FormError("client_id", "El cliente seleccionado no está disponible.")
        query = query.where(Task.client_id == identity)
    query = query.order_by(case((Task.status == "pending", 0), (Task.status == "completed", 1), else_=2),
                           Task.due_date.asc().nulls_last(), Task.due_time.asc().nulls_last(),
                           case(*[(Task.priority == value, index) for index, value in enumerate(PRIORITIES)]), Task.id.desc())
    tasks = db.scalars(query).all()
    successors = {child.parent_task_id: child for child in db.scalars(select(Task).where(
        Task.organization_id == user.organization_id, Task.user_id == user.id,
        Task.parent_task_id.in_([task.id for task in tasks]),
    ))} if tasks else {}
    rows = []
    for task in tasks:
        category = period_for(task, now, zone)
        match = category == period or (period == "next7" and category == "tomorrow")
        if period and not match:
            continue
        rows.append({"task": task, "period": category, "next_task": successors.get(task.id), "reminder_due": task.status == "pending" and task.reminder_at is not None and task.reminder_at <= now})
    return {"rows": rows, "clients": clients, "filters": {"status": status, "priority": priority, "client_id": client_id, "period": period}}


def dashboard_summary(db, user, zone, now=None):
    now = aware_now(now)
    result = {"overdue": 0, "today": 0, "upcoming": 0}
    tasks = db.scalars(select(Task).where(Task.organization_id == user.organization_id, Task.user_id == user.id,
                                        Task.status == "pending", Task.due_date <= now.astimezone(zone).date() + timedelta(days=7))).all()
    for task in tasks:
        category = period_for(task, now, zone)
        if category in {"overdue", "today"}:
            result[category] += 1
        elif category in {"tomorrow", "next7"}:
            result["upcoming"] += 1
    return result


def due_reminders(db, user, *, now=None, limit=100):
    """Lectura acotada para un usuario/organización explícitos, sin enviar ni consumir.

    Un futuro scheduler deberá recorrer ámbitos autorizados y añadir entrega
    idempotente. Repetir este helper devuelve recordatorios aún pendientes.
    """
    now = aware_now(now)
    if not 1 <= limit <= 1000:
        raise ValueError("El límite debe estar entre 1 y 1000.")
    return db.scalars(owned_tasks(user).join(Task.user).join(Task.organization).where(
        Task.status == "pending", Task.reminder_at <= now, User.is_active.is_(True), Organization.is_active.is_(True),
    ).order_by(Task.reminder_at, Task.id).limit(limit)).all()
