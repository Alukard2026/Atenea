"""Pantallas de tareas, siempre para el usuario y organización de la sesión."""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from app import tasks
from app.recurrence import RECURRENCES
from app.timezones import organization_zone
from app.routers.work import CurrentUser, PostedForm, commit, render
from app.web_auth import DatabaseSession
from app.worklog import FormError, active_clients, active_projects


router = APIRouter(prefix="/tasks")


def task_zone(request, user):
    return organization_zone(user.organization, request.app.state.task_zone)


def task_render(request, user, template, **context):
    return render(request, user, template, priorities=tasks.PRIORITIES, statuses=tasks.STATUSES,
                  periods=tasks.PERIODS, recurrences=RECURRENCES, zone=task_zone(request, user), **context)


def task_form(request, user, db, *, task=None, data=None, error=None):
    if data is None:
        data = {"priority": "normal", "recurrence_type": "none", "recurrence_interval": "1"}
        if task is not None:
            data = {"title": task.title, "description": task.description or "", "priority": task.priority,
                    "recurrence_type": task.recurrence_type, "recurrence_interval": str(task.recurrence_interval),
                    "recurrence_end_date": task.recurrence_end_date.isoformat() if task.recurrence_end_date else "",
                    "due_date": task.due_date.isoformat() if task.due_date else "",
                    "due_time": task.due_time.strftime("%H:%M") if task.due_time else "",
                    "client_id": str(task.client_id) if task.client_id else "",
                    "project_id": str(task.project_id) if task.project_id else "",
                    "reminder_at": task.reminder_at.astimezone(task_zone(request, user)).strftime("%Y-%m-%dT%H:%M") if task.reminder_at else ""}
    clients, projects = active_clients(db, user), active_projects(db, user)
    if task is not None:
        if task.client is not None and not any(client.id == task.client_id for client in clients):
            clients.append(task.client)
        if task.project is not None and not any(project.id == task.project_id for project in projects):
            projects.append(task.project)
    return task_render(request, user, "task_form.html", task=task, data=data, error=error, clients=clients, projects=projects)


@router.get("")
def tasks_page(request: Request, user: CurrentUser, db: DatabaseSession, status: str = "all", priority: str = "", client_id: str = "", period: str = ""):
    if any(key not in {"status", "priority", "client_id", "period", "saved"} or len(request.query_params.getlist(key)) != 1 for key in request.query_params):
        raise HTTPException(400, "Selecciona los filtros desde la pantalla de tareas.")
    try:
        context = tasks.task_list(db, user, task_zone(request, user), status=status, priority=priority, client_id=client_id, period=period)
    except FormError as error:
        raise HTTPException(400, error.message) from None
    return task_render(request, user, "tasks.html", **context)


@router.get("/new")
def new_task_page(request: Request, user: CurrentUser, db: DatabaseSession, date: str = "", time: str = ""):
    from app.calendar import instant
    from app.worklog import parse_date
    if any(k not in {"date", "time"} or len(request.query_params.getlist(k)) != 1 for k in request.query_params):
        raise HTTPException(400, "Parámetros de tarea inválidos.")
    data = None
    try:
        if date:
            date = parse_date(date, "due_date").isoformat()
            if time:
                instant(date + "T" + time, task_zone(request, user), "due_time")
            data = {"due_date": date, "due_time": time, "priority": "normal", "recurrence_type": "none", "recurrence_interval": "1"}
        elif time:
            raise FormError("due_date", "Selecciona primero la fecha.")
    except FormError as error:
        raise HTTPException(400, error.message) from None
    return task_form(request, user, db, data=data)


@router.post("/new")
def new_task(request: Request, user: CurrentUser, db: DatabaseSession, data: PostedForm):
    try:
        tasks.save_task(db, user, data, task_zone(request, user))
    except FormError as error:
        return task_form(request, user, db, data=data, error=error)
    commit(db)
    return RedirectResponse("/tasks?saved=1", status_code=303)


@router.get("/{task_id}/edit")
def edit_task_page(task_id: int, request: Request, user: CurrentUser, db: DatabaseSession):
    return task_form(request, user, db, task=tasks.owned_task(db, user, task_id))


@router.post("/{task_id}/edit")
def edit_task(task_id: int, request: Request, user: CurrentUser, db: DatabaseSession, data: PostedForm):
    try:
        tasks.save_task(db, user, data, task_zone(request, user), task_id=task_id)
    except FormError as error:
        return task_form(request, user, db, task=tasks.owned_task(db, user, task_id), data=data, error=error)
    commit(db)
    return RedirectResponse("/tasks?saved=1", status_code=303)


@router.post("/{task_id}/status")
def change_task_status(task_id: int, request: Request, user: CurrentUser, db: DatabaseSession, data: PostedForm):
    if set(data) - {"status", "csrf"}:
        raise HTTPException(400, "El formulario contiene campos no permitidos.")
    try:
        tasks.set_status(db, user, task_id, data.get("status", ""), zone=task_zone(request, user))
    except FormError as error:
        raise HTTPException(400, error.message) from None
    commit(db)
    return RedirectResponse("/tasks?saved=1", status_code=303)
