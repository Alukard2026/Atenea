"""Operaciones del MVP; el ámbito siempre procede del usuario autenticado."""

from datetime import date, timedelta
from decimal import Decimal
import re

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, contains_eager

from app.models import Client, Project, TimeEntry, User
from app.workdays import DAY_NAMES, workday_flags
from urllib.parse import urlencode


MIN_DATE = date(1900, 1, 1)
MAX_DATE = date(2100, 12, 31)


class FormError(ValueError):
    def __init__(self, field: str, message: str):
        self.field = field
        self.message = message
        super().__init__(message)


def clean_text(data, field, label, maximum, required=False, multiline=False):
    value = data.get(field, "").strip()
    if required and not value:
        raise FormError(field, f"Introduce {label}.")
    if len(value) > maximum:
        raise FormError(field, f"{label.capitalize()} admite como máximo {maximum} caracteres.")
    if any(not c.isprintable() and not (multiline and c in "\n\r\t") for c in value):
        raise FormError(field, f"Revisa los caracteres de {label}.")
    return value or None


def normalized(column):
    return func.lower(func.regexp_replace(func.trim(column), r"\s+", " ", "g"))


def parse_id(value, field, label):
    if not re.fullmatch(r"[0-9]{1,10}", value) or not 0 < int(value) <= 2**31 - 1:
        raise FormError(field, f"Selecciona {label} válido.")
    return int(value)


def parse_date(value, field="work_date"):
    try:
        parsed = date.fromisoformat(value)
        if not MIN_DATE <= parsed <= MAX_DATE:
            raise ValueError
        return parsed
    except (ValueError, TypeError):
        raise FormError(field, "Introduce una fecha válida entre 1900 y 2100.") from None


def active_clients(db: Session, user: User):
    return db.scalars(select(Client).where(
        Client.organization_id == user.organization_id, Client.is_active.is_(True),
    ).order_by(func.lower(Client.name), Client.id)).all()


def active_projects(db: Session, user: User):
    return db.scalars(select(Project).join(Project.client).where(
        Project.organization_id == user.organization_id,
        Client.organization_id == user.organization_id,
        Project.is_active.is_(True), Client.is_active.is_(True),
    ).options(contains_eager(Project.client)).order_by(func.lower(Client.name), func.lower(Project.name), Project.id)).all()


def require_client(db: Session, user: User, client_id: int):
    client = db.scalar(select(Client).where(
        Client.id == client_id, Client.organization_id == user.organization_id, Client.is_active.is_(True),
    ))
    if client is None:
        raise FormError("client_id", "Selecciona un cliente activo de tu organización.")
    return client


def organization_clients(db: Session, user: User):
    return db.scalars(select(Client).where(Client.organization_id == user.organization_id)
                      .order_by(Client.is_active.desc(), func.lower(Client.name), Client.id)).all()


def organization_client(db: Session, user: User, client_id: int):
    if not 0 < client_id <= 2**31 - 1:
        raise HTTPException(404, "No se encontró el cliente solicitado.")
    client = db.scalar(select(Client).where(Client.id == client_id, Client.organization_id == user.organization_id))
    if client is None:
        raise HTTPException(404, "No se encontró el cliente solicitado.")
    return client


def create_client(db: Session, user: User, data, client_id: int | None = None):
    if user.role != "admin":
        raise HTTPException(403, "Solo los administradores pueden administrar clientes.")
    client = organization_client(db, user, client_id) if client_id is not None else None
    name = clean_text(data, "name", "el nombre del cliente", 255, required=True)
    code = clean_text(data, "code", "el código", 50)
    from app.client_domains import CLIENT_TYPES, is_public_domain, normalize_domain
    try:
        domain = normalize_domain(data.get("email_domain", client.email_domain if client else None))
    except ValueError as error:
        raise FormError("email_domain", str(error)) from None
    if is_public_domain(domain):
        raise FormError("email_domain", "No asocies un proveedor de correo público a un cliente. Deja el dominio vacío.")
    client_type = data.get("client_type", client.client_type if client else "otro")
    if client_type not in CLIENT_TYPES:
        raise FormError("client_type", "Selecciona un tipo de cliente válido.")
    # Este bloqueo transaccional evita altas duplicadas concurrentes desde la web.
    # No sustituye a restricciones únicas para futuros escritores SQL externos.
    db.execute(select(func.pg_advisory_xact_lock(41001, user.organization_id)))
    if domain:
        domain_query = select(Client.id).where(Client.organization_id == user.organization_id, Client.email_domain == domain)
        if client is not None:
            domain_query = domain_query.where(Client.id != client.id)
        if db.scalar(domain_query.limit(1)) is not None:
            raise FormError("email_domain", "Ese dominio ya está asociado a un cliente de tu organización, incluidos los inactivos.")
    duplicate = normalized(Client.name) == normalized(name)
    if code:
        duplicate = or_(duplicate, normalized(Client.code) == normalized(code))
    query = select(Client.id).where(Client.organization_id == user.organization_id, duplicate)
    if client is not None:
        query = query.where(Client.id != client.id)
    if db.scalar(query.limit(1)) is not None:
        raise FormError("name", "Ya existe un cliente con ese nombre o código, incluidos los inactivos.")
    if client is None:
        client = Client(organization_id=user.organization_id, is_active=True)
        db.add(client)
    client.name, client.code = name, code
    client.email_domain, client.client_type = domain, client_type
    return client


def create_project(db: Session, user: User, data):
    client_id = parse_id(data.get("client_id", ""), "client_id", "un cliente")
    require_client(db, user, client_id)
    name = clean_text(data, "name", "el nombre del proyecto", 255, required=True)
    code = clean_text(data, "code", "el código", 50)
    description = clean_text(data, "description", "la descripción", 5000, multiline=True)
    db.execute(select(func.pg_advisory_xact_lock(41001, user.organization_id)))
    duplicate = normalized(Project.name) == normalized(name)
    if code:
        duplicate = or_(duplicate, normalized(Project.code) == normalized(code))
    if db.scalar(select(Project.id).where(
        Project.organization_id == user.organization_id, Project.client_id == client_id, duplicate,
    ).limit(1)) is not None:
        raise FormError("name", "Ya existe un proyecto con ese nombre o código para este cliente, incluidos los inactivos.")
    project = Project(organization_id=user.organization_id, client_id=client_id, name=name, code=code, description=description, is_active=True)
    db.add(project)
    return project


def owned_entries(user: User):
    # Mantener los datos históricos aunque cliente o proyecto estén inactivos.
    return select(TimeEntry).join(TimeEntry.client).outerjoin(TimeEntry.project).where(
        TimeEntry.organization_id == user.organization_id, TimeEntry.user_id == user.id,
        Client.organization_id == user.organization_id,
        or_(TimeEntry.project_id.is_(None), Project.organization_id == user.organization_id),
    ).options(contains_eager(TimeEntry.client), contains_eager(TimeEntry.project))


def owned_entry(db: Session, user: User, entry_id: int):
    if not 0 < entry_id <= 2**31 - 1:
        raise HTTPException(404, "No se encontró el registro solicitado.")
    entry = db.scalar(owned_entries(user).where(TimeEntry.id == entry_id))
    if entry is None:
        raise HTTPException(404, "No se encontró el registro solicitado.")
    return entry


def save_entry(db: Session, user: User, data, entry_id: int | None = None):
    # Serializar escrituras del usuario para que dos peticiones no superen juntas
    # el máximo diario. La edición y eliminación utilizan el mismo bloqueo.
    db.execute(select(func.pg_advisory_xact_lock(41002, user.id)))
    entry = owned_entry(db, user, entry_id) if entry_id is not None else None
    work_date = parse_date(data.get("work_date", ""))
    if work_date > date.today():
        raise FormError("work_date", "Selecciona una fecha trabajada pasada o actual.")
    client_id = parse_id(data.get("client_id", ""), "client_id", "un cliente")
    raw_project = data.get("project_id", "").strip()
    project_id = parse_id(raw_project, "project_id", "un proyecto") if raw_project else None
    # Un registro histórico puede conservar su cliente archivado; no permite
    # seleccionar un cliente inactivo distinto ni crear registros nuevos con él.
    if entry is None or client_id != entry.client_id:
        require_client(db, user, client_id)
    if project_id is not None:
        project = db.scalar(select(Project).where(
            Project.id == project_id, Project.organization_id == user.organization_id,
            Project.client_id == client_id,
        ))
        retained = entry is not None and entry.client_id == client_id and entry.project_id == project_id
        if project is None or (not project.is_active and not retained):
            raise FormError("project_id", "Selecciona un proyecto activo del cliente indicado o deja el proyecto vacío.")
    description = clean_text(data, "description", "la descripción de lo que se hizo", 5000, required=True, multiline=True)
    raw_hours = data.get("hours", "").strip()
    if not re.fullmatch(r"(?:[0-9]{1,2}(?:[.,][0-9]{1,2})?|[.,][0-9]{1,2})", raw_hours):
        raise FormError("hours", "Introduce horas entre 0.01 y 24, con un máximo de dos decimales.")
    hours = Decimal(raw_hours.replace(",", "."))
    if not Decimal("0") < hours <= Decimal("24"):
        raise FormError("hours", "Las horas deben ser mayores que cero y no superar 24.")
    billable = data.get("billable", "")
    if billable not in {"yes", "no"}:
        raise FormError("billable", "Indica si la actividad es facturable.")
    daily_query = select(func.coalesce(func.sum(TimeEntry.hours), 0)).where(
        TimeEntry.organization_id == user.organization_id, TimeEntry.user_id == user.id,
        TimeEntry.work_date == work_date,
    )
    if entry is not None:
        daily_query = daily_query.where(TimeEntry.id != entry.id)
    if db.scalar(daily_query) + hours > Decimal("24"):
        raise FormError("hours", "El total de tus registros de ese día no puede superar 24 horas.")
    if entry is None:
        # Compatibilidad con el campo activity existente; ya no requiere otro input.
        entry = TimeEntry(organization_id=user.organization_id, user_id=user.id,
                          activity=" ".join(description.split())[:255])
        db.add(entry)
    entry.work_date = work_date
    entry.client_id = client_id
    entry.project_id = project_id
    entry.description = description
    entry.hours = hours
    entry.billable = billable == "yes"
    return entry


def month_bounds(value: str = ""):
    try:
        first = date.fromisoformat(value + "-01") if value else date.today().replace(day=1)
        if value and not re.fullmatch(r"[0-9]{4}-[0-9]{2}", value):
            raise ValueError
        if not MIN_DATE <= first <= MAX_DATE:
            raise ValueError
    except (TypeError, ValueError):
        raise FormError("month", "Selecciona un mes válido (AAAA-MM), entre 1900 y 2100.") from None
    next_month = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
    return first, next_month


def month_url(month: str, filters=None):
    return "/hours/history?" + urlencode({"month": month, **{k: v for k, v in (filters or {}).items() if v}})


def current_month_total(db: Session, user: User, *, today=None):
    first, end = month_bounds(today.strftime("%Y-%m") if today is not None else "")
    return db.scalar(select(func.coalesce(func.sum(TimeEntry.hours), 0)).where(
        TimeEntry.organization_id == user.organization_id, TimeEntry.user_id == user.id,
        TimeEntry.work_date >= first, TimeEntry.work_date < end,
    ))


def monthly_view(db: Session, user: User, month="", client_id="", project_id="", billable=""):
    first, end = month_bounds(month)
    filters = {"client_id": client_id, "project_id": project_id, "billable": billable}
    query = owned_entries(user).where(TimeEntry.work_date >= first, TimeEntry.work_date < end)
    clients = organization_clients(db, user)  # Incluye archivados para consultar historia.
    projects = db.scalars(select(Project).join(Project.client).where(
        Project.organization_id == user.organization_id, Client.organization_id == user.organization_id,
    ).options(contains_eager(Project.client)).order_by(Client.name, Project.name, Project.id)).all()
    if client_id:
        selected_client = parse_id(client_id, "client_id", "un cliente")
        if not any(client.id == selected_client for client in clients):
            raise FormError("client_id", "El cliente seleccionado no está disponible.")
        query = query.where(TimeEntry.client_id == selected_client)
    if project_id:
        if project_id == "none":
            query = query.where(TimeEntry.project_id.is_(None))
        else:
            selected_project = parse_id(project_id, "project_id", "un proyecto")
            if not any(project.id == selected_project and (not client_id or project.client_id == int(client_id)) for project in projects):
                raise FormError("project_id", "El proyecto seleccionado no corresponde al cliente o no está disponible.")
            query = query.where(TimeEntry.project_id == selected_project)
    if billable:
        if billable not in {"yes", "no"}:
            raise FormError("billable", "Selecciona facturable, no facturable o todos.")
        query = query.where(TimeEntry.billable.is_(billable == "yes"))
    entries = db.scalars(query.order_by(TimeEntry.work_date.desc(), TimeEntry.id.desc())).all()
    billable_total = sum((entry.hours for entry in entries if entry.billable), Decimal("0.00"))
    non_billable_total = sum((entry.hours for entry in entries if not entry.billable), Decimal("0.00"))
    previous = first - timedelta(days=1)
    return {"entries": entries, "month": first.strftime("%Y-%m"), "month_label": first.strftime("%m/%Y"),
            "filters": filters, "clients": clients, "projects": projects,
            "month_total": billable_total + non_billable_total,
            "billable_total": billable_total, "non_billable_total": non_billable_total,
            "previous_month_url": month_url(previous.strftime("%Y-%m"), filters) if previous >= MIN_DATE else None,
            "next_month_url": month_url(end.strftime("%Y-%m"), filters) if end <= MAX_DATE else None}


def delete_entry(db: Session, user: User, entry_id: int):
    db.execute(select(func.pg_advisory_xact_lock(41002, user.id)))
    entry = owned_entry(db, user, entry_id)
    work_date = entry.work_date
    db.delete(entry)
    return work_date


def monday_for(day: date):
    return day - timedelta(days=day.weekday())


def current_week_total(db: Session, user: User, *, today=None):
    monday = monday_for(today if today is not None else date.today())
    return db.scalar(select(func.coalesce(func.sum(TimeEntry.hours), 0)).where(
        TimeEntry.organization_id == user.organization_id, TimeEntry.user_id == user.id,
        TimeEntry.work_date >= monday, TimeEntry.work_date <= monday + timedelta(days=6),
    ))


def weekly_view(db: Session, user: User, anchor: date):
    """Resumen calendario reutilizable por HTML y futuras exportaciones.

    Incluye todos los registros propios de lunes a domingo y los clasifica según
    la configuración actual de la organización, también para semanas pasadas.
    """
    monday = monday_for(anchor)
    sunday = monday + timedelta(days=6)
    entries = db.scalars(owned_entries(user).where(
        TimeEntry.work_date >= monday, TimeEntry.work_date <= sunday,
    ).order_by(TimeEntry.work_date, TimeEntry.created_at, TimeEntry.id)).all()
    flags = workday_flags(user.organization)
    days = [{"date": monday + timedelta(days=index), "label": label,
             "is_workday": flags[index], "entries": [], "total": Decimal("0.00")}
            for index, label in enumerate(DAY_NAMES)]
    for entry in entries:
        day = days[entry.work_date.weekday()]
        day["entries"].append(entry)
        day["total"] += entry.hours
    previous_week, next_week = monday - timedelta(days=7), monday + timedelta(days=7)
    working_total = sum((day["total"] for day in days if day["is_workday"]), Decimal("0.00"))
    non_working_total = sum((day["total"] for day in days if not day["is_workday"]), Decimal("0.00"))
    return {
        "week_start": monday, "week_end": sunday, "days": days,
        "working_total": working_total, "non_working_total": non_working_total,
        "week_total": working_total + non_working_total,
        "previous_week": previous_week if previous_week >= MIN_DATE else None,
        "next_week": next_week if next_week <= MAX_DATE else None,
    }
