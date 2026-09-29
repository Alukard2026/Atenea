"""Calendario local privado. Sin llamadas a proveedores ni generación de recurrencias."""
from datetime import date, datetime, time, timedelta, timezone
import re

from fastapi import HTTPException
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import joinedload

from app.models import CalendarEvent, Client, Project, Task
from app import tasks
from app.recurrence import scheduled_instant
from app.worklog import FormError, clean_text, parse_date, parse_id

TYPES = {"task": "Tarea", "hearing": "Audiencia", "meeting": "Reunión", "appointment": "Cita", "reminder": "Recordatorio", "other": "Otro"}
EVENT_TYPES = {k: v for k, v in TYPES.items() if k != "task"}
MAX_EVENTS = 2000


def owned_events(user):
    return select(CalendarEvent).where(CalendarEvent.organization_id == user.organization_id, CalendarEvent.user_id == user.id).options(joinedload(CalendarEvent.client), joinedload(CalendarEvent.project))


def owned_event(db, user, identity, lock=False):
    if not 0 < identity <= 2**31 - 1:
        raise HTTPException(404, "No se encontró el evento.")
    query = owned_events(user).where(CalendarEvent.id == identity)
    event = db.scalar(query.with_for_update(of=CalendarEvent) if lock else query)
    if event is None:
        raise HTTPException(404, "No se encontró el evento.")
    return event


def instant(raw, zone, field):
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", raw):
        raise FormError(field, "Introduce fecha y hora válidas.")
    parse_date(raw[:10], field)
    try:
        value = datetime.fromisoformat(raw)
    except ValueError:
        raise FormError(field, "Introduce fecha y hora válidas.") from None
    return tasks.local_instant(value, zone, field)


def save_event(db, user, data, zone, identity=None):
    allowed = {"csrf", "event_type", "title", "description", "client_id", "project_id", "date", "end_date", "start_time", "end_time", "all_day", "location", "institution", "reminder_at"}
    if data.keys() - allowed:
        raise HTTPException(400, "El formulario contiene campos no permitidos.")
    event = owned_event(db, user, identity, lock=True) if identity is not None else None
    if event and event.status == "cancelled":
        raise HTTPException(409, "Este evento está cancelado.")
    kind = data.get("event_type", "")
    if kind not in EVENT_TYPES:
        raise FormError("event_type", "Selecciona un tipo válido. Las tareas se crean en su propio formulario.")
    values = {field: clean_text(data, field, label, maximum, required=field == "title", multiline=field == "description") for field, label, maximum in (
        ("title", "el título", 255), ("description", "la descripción", 5000), ("location", "la ubicación", 255), ("institution", "la institución", 255))}
    day = parse_date(data.get("date", ""), "date")
    last = parse_date(data.get("end_date") or day.isoformat(), "end_date")
    if last < day or (last - day).days > 366:
        raise FormError("end_date", "El fin debe ser posterior al inicio y no superar un año.")
    if data.get("all_day", "") not in {"", "yes"}:
        raise FormError("all_day", "Selecciona una duración válida.")
    all_day = data.get("all_day") == "yes"
    if all_day:
        start_at = end_at = None
        start_date, end_date = day, last + timedelta(days=1)
    else:
        start_date = end_date = None
        start_at = instant(day.isoformat() + "T" + data.get("start_time", ""), zone, "start_time")
        end_at = instant(last.isoformat() + "T" + data["end_time"], zone, "end_time") if data.get("end_time") else None
        if (end_at and end_at <= start_at) or (not end_at and last != day):
            raise FormError("end_time", "Indica una hora de fin posterior al inicio.")
    reminder = instant(data["reminder_at"], zone, "reminder_at") if data.get("reminder_at") else None
    # Un snooze puede superar el inicio. Conservarlo al editar otros campos,
    # incluida la precisión de segundos que el formulario no representa.
    retained_reminder = bool(event and event.reminder_at and reminder == event.reminder_at.replace(second=0, microsecond=0))
    if retained_reminder:
        reminder = event.reminder_at
    if reminder and not retained_reminder and (reminder.astimezone(zone).date() > day if all_day else reminder > start_at):
        raise FormError("reminder_at", "El recordatorio no puede ser posterior al inicio del evento.")
    ids = {}
    for field, model in (("client_id", Client), ("project_id", Project)):
        identity_value = parse_id(data[field], field, "un registro") if data.get(field) else None
        if identity_value:
            record = db.scalar(select(model).where(model.id == identity_value, model.organization_id == user.organization_id))
            retained = event is not None and getattr(event, field) == identity_value
            if record is None or (not record.is_active and not retained) or (field == "project_id" and record.client_id != ids["client_id"]):
                raise FormError(field, "Selecciona un cliente o proyecto compatible y activo de tu organización.")
        ids[field] = identity_value
    if event is None:
        event = CalendarEvent(organization_id=user.organization_id, user_id=user.id, status="scheduled")
        db.add(event)
    for key, value in dict(**values, **ids, event_type=kind, start_at=start_at, end_at=end_at, start_date=start_date, end_date=end_date, all_day=all_day, reminder_at=reminder).items():
        setattr(event, key, value)
    db.flush()
    return event


def parse_range(start, end, zone):
    def boundary(raw):
        if len(raw) > 40:
            raise ValueError
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
            return tasks.local_instant(datetime.combine(parse_date(raw), time()), zone, "range")
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if value.tzinfo is None or value.utcoffset() is None or not 1900 <= value.year <= 2100:
            raise ValueError
        return value.astimezone(timezone.utc)
    try:
        first, last = boundary(start), boundary(end)
        if not timedelta(0) < last - first <= timedelta(days=93):
            raise ValueError
        return first, last
    except (ValueError, OverflowError):
        raise HTTPException(400, "Selecciona un rango válido de hasta 93 días, con zona horaria.") from None


def events(db, user, zone, start, end, *, types=None, completed=False, client_id="", project_id=""):
    """Rango semiabierto [start,end). Filtros y permisos siempre en servidor."""
    start, end = tasks.aware_now(start), tasks.aware_now(end)
    kinds = set(TYPES) if types is None else set(types)
    if kinds - TYPES.keys():
        raise HTTPException(400, "Selecciona tipos de evento válidos.")
    filters = {}
    for key, raw, model in (("client_id", client_id, Client), ("project_id", project_id, Project)):
        if raw:
            try:
                identity = parse_id(raw, key, "un filtro")
            except FormError as error:
                raise HTTPException(400, error.message) from None
            row = db.scalar(select(model).where(model.id == identity, model.organization_id == user.organization_id))
            if row is None or (key == "project_id" and filters.get("client_id") and row.client_id != filters["client_id"]):
                raise HTTPException(400, "El filtro seleccionado no está disponible.")
            filters[key] = identity
    local_start, local_end = start.astimezone(zone), end.astimezone(zone)
    first_day = local_start.date()
    end_day = local_end.date() + (timedelta(days=1) if local_end.time() != time() else timedelta())
    result = []
    def add(record, kind, when, finish, all_day, url, prefix, reminder=False):
        if kind not in kinds:
            return
        actual = date.fromisoformat(when) if all_day else datetime.fromisoformat(when)
        if all_day:
            if not (actual < end_day and date.fromisoformat(finish or when) >= first_day):
                return
        elif not (actual < end and (datetime.fromisoformat(finish) > start if finish else actual >= start)):
            return
        details = {"type": kind, "typeLabel": TYPES[kind], "title": record.title, "client": record.client.name if record.client else "", "project": record.project.name if record.project else "", "status": record.status,
                   "priority": getattr(record, "priority", None), "institution": getattr(record, "institution", None)}
        result.append({"id": f"{prefix}-{record.id}{'-reminder' if reminder else ''}", "title": f"{TYPES[kind]} · {record.title}", "start": when, "end": finish,
                       "allDay": all_day, "url": url, "editable": False, "classNames": [f"event-{kind}"] + (["event-completed"] if record.status == "completed" else []), "extendedProps": details})
    def bounded(query):
        rows = db.scalars(query.limit(MAX_EVENTS + 1)).all()
        if len(rows) > MAX_EVENTS:
            raise HTTPException(422, "Demasiados eventos. Reduce el rango o utiliza los filtros.")
        return rows
    task_windows = []
    if "task" in kinds:
        task_windows.append(and_(Task.due_date >= first_day, Task.due_date < end_day))
    if "reminder" in kinds:
        task_windows.append(and_(Task.status == "pending", Task.reminder_at >= start, Task.reminder_at < end))
    query = tasks.owned_tasks(user).where(Task.status.in_(["pending", "completed"] if completed else ["pending"]), or_(False, *task_windows))
    for key, value in filters.items():
        query = query.where(getattr(Task, key) == value)
    if {"task", "reminder"} & kinds:
        for task in bounded(query):
            due = scheduled_instant(datetime.combine(task.due_date, task.due_time), zone) if task.due_date and task.due_time else None
            url = f"/calendar/tasks/{task.id}"
            if task.due_date:
                add(task, "task", due.isoformat() if due else task.due_date.isoformat(), None if due else (task.due_date + timedelta(days=1)).isoformat(), due is None, url, "task")
            if task.reminder_at and task.reminder_at != due and task.status == "pending":
                add(task, "reminder", task.reminder_at.isoformat(), None, False, url, "task", True)
    event_window = or_(
        and_(CalendarEvent.all_day.is_(False), CalendarEvent.start_at < end, or_(CalendarEvent.end_at > start, and_(CalendarEvent.end_at.is_(None), CalendarEvent.start_at >= start))),
        and_(CalendarEvent.all_day.is_(True), CalendarEvent.start_date < end_day, CalendarEvent.end_date > first_day))
    event_windows = [and_(CalendarEvent.event_type.in_(kinds & EVENT_TYPES.keys()), event_window)]
    if "reminder" in kinds:
        event_windows.append(and_(CalendarEvent.reminder_at >= start, CalendarEvent.reminder_at < end))
    query = owned_events(user).where(CalendarEvent.status == "scheduled", or_(*event_windows))
    for key, value in filters.items():
        query = query.where(getattr(CalendarEvent, key) == value)
    for event in bounded(query):
        url = f"/calendar/{event.id}"
        add(event, event.event_type, event.start_date.isoformat() if event.all_day else event.start_at.isoformat(), event.end_date.isoformat() if event.all_day else event.end_at.isoformat() if event.end_at else None, event.all_day, url, "event")
        if event.reminder_at and event.reminder_at != event.start_at and event.event_type != "reminder":
            add(event, "reminder", event.reminder_at.isoformat(), None, False, url, "event", True)
    if len(result) > MAX_EVENTS:
        raise HTTPException(422, "Demasiados eventos. Reduce el rango o utiliza los filtros.")
    return result


def upcoming(db, user, zone, now=None):
    now = tasks.aware_now(now)
    last = now + timedelta(days=30)
    today, end_day = now.astimezone(zone).date(), last.astimezone(zone).date()
    rows = []
    def append(record, kind, when, all_day, url, identity):
        rows.append({"id": identity, "start": when.date().isoformat() if all_day else when.isoformat(), "when": when.astimezone(zone), "allDay": all_day, "url": url,
                     "extendedProps": {"title": record.title, "type": kind, "typeLabel": TYPES[kind], "client": record.client.name if record.client else ""}})
    # Cinco consultas acotadas e indexadas; nunca cargar el historial al abrir Inicio.
    query = tasks.owned_tasks(user).where(Task.status == "pending", Task.due_date >= today, Task.due_date <= end_day,
        or_(Task.due_date > today, Task.due_time.is_(None), Task.due_time >= now.astimezone(zone).time().replace(tzinfo=None)))
    for item in db.scalars(query.order_by(Task.due_date, Task.due_time.asc().nullsfirst(), Task.id).limit(8)):
        when = scheduled_instant(datetime.combine(item.due_date, item.due_time or time()), zone)
        append(item, "task", when, item.due_time is None, f"/calendar/tasks/{item.id}", f"task-{item.id}")
        if item.due_time is None:
            rows[-1]["start"] = item.due_date.isoformat()
    for model, base in ((Task, tasks.owned_tasks(user).where(Task.status == "pending")), (CalendarEvent, owned_events(user).where(CalendarEvent.status == "scheduled"))):
        query = base.where(model.reminder_at >= now, model.reminder_at < last).order_by(model.reminder_at, model.id).limit(8)
        for item in db.scalars(query):
            due = scheduled_instant(datetime.combine(item.due_date, item.due_time), zone) if model == Task and item.due_date and item.due_time else item.start_at if model == CalendarEvent else None
            if item.reminder_at == due or (model == CalendarEvent and item.event_type == "reminder"):
                continue
            append(item, "reminder", item.reminder_at, False, f"/calendar/tasks/{item.id}" if model == Task else f"/calendar/{item.id}", f"{model.__tablename__}-{item.id}-reminder")
    base = owned_events(user).where(CalendarEvent.status == "scheduled")
    for item in db.scalars(base.where(CalendarEvent.start_at >= now, CalendarEvent.start_at < last).order_by(CalendarEvent.start_at, CalendarEvent.id).limit(8)):
        append(item, item.event_type, item.start_at, False, f"/calendar/{item.id}", f"event-{item.id}")
    for item in db.scalars(base.where(CalendarEvent.start_date >= today, CalendarEvent.start_date <= end_day).order_by(CalendarEvent.start_date, CalendarEvent.id).limit(8)):
        append(item, item.event_type, datetime.combine(item.start_date, time(), tzinfo=zone), True, f"/calendar/{item.id}", f"event-{item.id}")
    return sorted(rows, key=lambda row: (row["when"], row["id"]))[:8]
