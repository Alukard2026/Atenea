"""Calendario privado: lectura y formularios con CSRF, sin endpoints de escritura externos."""
from datetime import timedelta

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app import calendar, tasks
from app.models import Project
from app.routers.work import CurrentUser, PostedForm, commit, render
from app.routers.tasks import task_zone
from app.web_auth import DatabaseSession
from app.worklog import FormError, active_clients, active_projects, organization_clients, parse_date

router = APIRouter(prefix="/calendar")


def query_check(request, allowed=()):
    if any(k not in allowed or len(request.query_params.getlist(k)) != 1 for k in request.query_params):
        raise HTTPException(400, "Parámetros de calendario inválidos.")


def event_form(request, user, db, event=None, data=None, error=None):
    zone = task_zone(request, user)
    if data is None:
        data = {"event_type": "hearing", "date": tasks.aware_now().astimezone(zone).date().isoformat()}
        if event:
            data = {k: getattr(event, k) or "" for k in ("event_type", "title", "description", "location", "institution")}
            data.update(client_id=str(event.client_id or ""), project_id=str(event.project_id or ""), all_day="yes" if event.all_day else "",
                        date=event.start_date.isoformat() if event.all_day else event.start_at.astimezone(zone).date().isoformat(),
                        end_date=(event.end_date - timedelta(days=1)).isoformat() if event.all_day else event.end_at.astimezone(zone).date().isoformat() if event.end_at else "",
                        start_time="" if event.all_day else event.start_at.astimezone(zone).strftime("%H:%M"),
                        end_time=event.end_at.astimezone(zone).strftime("%H:%M") if event.end_at else "",
                        reminder_at=event.reminder_at.astimezone(zone).strftime("%Y-%m-%dT%H:%M") if event.reminder_at else "")
    clients, projects = active_clients(db, user), active_projects(db, user)
    if event:
        if event.client and not any(c.id == event.client_id for c in clients):
            clients.append(event.client)
        if event.project and not any(p.id == event.project_id for p in projects):
            projects.append(event.project)
    return render(request, user, "calendar_form.html", data=data, error=error, event=event, types=calendar.EVENT_TYPES, clients=clients, projects=projects, zone=zone)


@router.get("")
def calendar_page(request: Request, user: CurrentUser, db: DatabaseSession):
    query_check(request)
    business_days = [i for i, name in enumerate(("sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday")) if getattr(user.organization, "workday_" + name)]
    return render(request, user, "calendar.html", zone=task_zone(request, user), types=calendar.TYPES, clients=organization_clients(db, user),
                  business_days=business_days,
                  projects=db.scalars(select(Project).where(Project.organization_id == user.organization_id).order_by(Project.name)).all())


@router.get("/events")
def event_feed(request: Request, user: CurrentUser, db: DatabaseSession, start: str = "", end: str = "", types: str = "task,hearing,meeting,appointment,reminder,other", completed: str = "0", client_id: str = "", project_id: str = "", timeZone: str = ""):
    query_check(request, {"start", "end", "types", "completed", "client_id", "project_id", "timeZone"})
    zone = task_zone(request, user)
    if completed not in {"0", "1"} or (timeZone and timeZone != zone.key) or len(types) > 100:
        raise HTTPException(400, "Filtros de calendario inválidos.")
    first, last = calendar.parse_range(start, end, zone)
    return calendar.events(db, user, zone, first, last, types=types.split(",") if types else [], completed=completed == "1", client_id=client_id, project_id=project_id)


@router.get("/new")
def new_page(request: Request, user: CurrentUser, db: DatabaseSession, date: str = "", time: str = ""):
    query_check(request, {"date", "time"})
    data = None
    if date:
        try:
            date = parse_date(date, "date").isoformat()
            if time:
                calendar.instant(date + "T" + time, task_zone(request, user), "start_time")
        except FormError as error:
            raise HTTPException(400, error.message) from None
        data = {"date": date, "start_time": time, "event_type": "hearing", "all_day": "" if time else "yes"}
    elif time:
        raise HTTPException(400, "Selecciona primero la fecha.")
    return event_form(request, user, db, data=data)


@router.post("/new")
def create_event(request: Request, user: CurrentUser, db: DatabaseSession, data: PostedForm):
    query_check(request)
    try:
        event = calendar.save_event(db, user, data, task_zone(request, user))
    except FormError as error:
        return event_form(request, user, db, data=data, error=error)
    identity = event.id
    commit(db)
    return RedirectResponse(f"/calendar/{identity}", status_code=303)


@router.get("/tasks/{task_id}")
def task_detail(task_id: int, request: Request, user: CurrentUser, db: DatabaseSession):
    query_check(request)
    return render(request, user, "calendar_detail.html", task=tasks.owned_task(db, user, task_id), event=None, zone=task_zone(request, user), types=calendar.TYPES, priorities=tasks.PRIORITIES, statuses=tasks.STATUSES)


@router.get("/{event_id}")
def detail(event_id: int, request: Request, user: CurrentUser, db: DatabaseSession):
    query_check(request)
    event = calendar.owned_event(db, user, event_id)
    return render(request, user, "calendar_detail.html", event=event, last_day=event.end_date - timedelta(days=1) if event.all_day else None, task=None, zone=task_zone(request, user), types=calendar.TYPES)


@router.get("/{event_id}/edit")
def edit_page(event_id: int, request: Request, user: CurrentUser, db: DatabaseSession):
    query_check(request)
    return event_form(request, user, db, event=calendar.owned_event(db, user, event_id))


@router.post("/{event_id}/edit")
def edit_event(event_id: int, request: Request, user: CurrentUser, db: DatabaseSession, data: PostedForm):
    query_check(request)
    try:
        calendar.save_event(db, user, data, task_zone(request, user), event_id)
    except FormError as error:
        return event_form(request, user, db, event=calendar.owned_event(db, user, event_id), data=data, error=error)
    commit(db)
    return RedirectResponse(f"/calendar/{event_id}", status_code=303)


@router.post("/{event_id}/cancel")
def cancel(event_id: int, request: Request, user: CurrentUser, db: DatabaseSession, data: PostedForm):
    query_check(request)
    if data.keys() - {"csrf"}:
        raise HTTPException(400, "El formulario contiene campos no permitidos.")
    event = calendar.owned_event(db, user, event_id, lock=True)
    event.status, event.reminder_at = "cancelled", None
    commit(db)
    return RedirectResponse(f"/calendar/{event_id}", status_code=303)
