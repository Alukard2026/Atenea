"""Formularios y vistas de clientes, proyectos y horas del usuario actual."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.exc import IntegrityError

from app.models import User
from app.routers.auth import templates
from app.web_auth import DatabaseSession, csrf_token, get_current_user, validate_csrf
from app.web_auth import get_admin_user
from app import worklog
from app.client_domains import CLIENT_TYPES


router = APIRouter(default_response_class=HTMLResponse)
CurrentUser = Annotated[User, Depends(get_current_user)]
AdminUser = Annotated[User, Depends(get_admin_user)]


async def secure_form(request: Request):
    async with request.form(max_files=0, max_fields=16, max_part_size=8192) as form:
        if any(not isinstance(value, str) or len(form.getlist(key)) != 1 for key, value in form.items()):
            raise HTTPException(400, "El formulario contiene campos inválidos. Recarga la página.")
        data = dict(form)
    validate_csrf(request, data.get("csrf", ""))
    if {"organization_id", "user_id"} & data.keys():
        raise HTTPException(400, "La organización y el usuario se determinan mediante la sesión.")
    return data


PostedForm = Annotated[dict, Depends(secure_form)]


def render(request, user, template, *, data=None, error=None, **context):
    return templates.TemplateResponse(
        request=request, name=template, status_code=422 if error else 200,
        context={"user": user, "csrf_token": csrf_token(request), "data": data or {},
                 "errors": {error.field: error.message} if error else {}, "client_types": CLIENT_TYPES, **context},
    )


def commit(db):
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "No se pudo guardar el cambio. Recarga la página y comprueba los datos seleccionados.") from None


@router.get("/clients")
def clients_page(request: Request, user: CurrentUser, db: DatabaseSession):
    return render(request, user, "clients.html", clients=worklog.organization_clients(db, user) if user.role == "admin" else worklog.active_clients(db, user))


@router.post("/clients")
def create_client(request: Request, user: AdminUser, db: DatabaseSession, data: PostedForm):
    try:
        worklog.create_client(db, user, data)
    except worklog.FormError as error:
        return render(request, user, "clients.html", data=data, error=error, clients=worklog.organization_clients(db, user))
    commit(db)
    return RedirectResponse("/clients?created=1", status_code=303)


@router.get("/clients/{client_id}/edit")
def edit_client_page(client_id: int, request: Request, user: AdminUser, db: DatabaseSession):
    client = worklog.organization_client(db, user, client_id)
    return render(request, user, "client_edit.html", client=client, data={"name": client.name, "code": client.code or "",
                  "email_domain": client.email_domain or "", "client_type": client.client_type})


@router.post("/clients/{client_id}/edit")
def edit_client(client_id: int, request: Request, user: AdminUser, db: DatabaseSession, data: PostedForm):
    client = worklog.organization_client(db, user, client_id)
    try:
        worklog.create_client(db, user, data, client_id=client_id)
    except worklog.FormError as error:
        return render(request, user, "client_edit.html", client=client, data=data, error=error)
    commit(db)
    return RedirectResponse("/clients?updated=1", status_code=303)


@router.post("/clients/{client_id}/status")
def client_status(client_id: int, request: Request, user: AdminUser, db: DatabaseSession, data: PostedForm):
    client = worklog.organization_client(db, user, client_id)
    if data.get("active") not in {"yes", "no"}:
        raise HTTPException(400, "Selecciona un estado válido para el cliente.")
    client.is_active = data["active"] == "yes"
    commit(db)
    return RedirectResponse("/clients?updated=1", status_code=303)


@router.get("/projects")
def projects_page(request: Request, user: CurrentUser, db: DatabaseSession):
    return render(request, user, "projects.html", clients=worklog.active_clients(db, user), projects=worklog.active_projects(db, user))


@router.post("/projects")
def create_project(request: Request, user: CurrentUser, db: DatabaseSession, data: PostedForm):
    try:
        worklog.create_project(db, user, data)
    except worklog.FormError as error:
        return render(request, user, "projects.html", data=data, error=error,
                      clients=worklog.active_clients(db, user), projects=worklog.active_projects(db, user))
    commit(db)
    return RedirectResponse("/projects?created=1", status_code=303)


def hours_form(request, user, db, *, data=None, entry=None, error=None):
    if data is None:
        data = {"work_date": date.today().isoformat(), "billable": "yes"}
        if entry is not None:
            data = {"work_date": entry.work_date.isoformat(), "client_id": str(entry.client_id),
                    "project_id": str(entry.project_id) if entry.project_id else "",
                    "description": entry.description or entry.activity, "hours": str(entry.hours),
                    "billable": "yes" if entry.billable else "no"}
    clients, projects = worklog.active_clients(db, user), worklog.active_projects(db, user)
    if entry is not None:
        if not any(client.id == entry.client_id for client in clients):
            clients.append(entry.client)
        if entry.project is not None and not any(project.id == entry.project_id for project in projects):
            projects.append(entry.project)
    return render(request, user, "hours_form.html", data=data, entry=entry, error=error,
                  clients=clients, projects=projects, today=date.today().isoformat())


@router.get("/hours")
def hours_page(request: Request, user: CurrentUser, db: DatabaseSession):
    return hours_form(request, user, db)


@router.post("/hours")
def create_hours(request: Request, user: CurrentUser, db: DatabaseSession, data: PostedForm):
    try:
        entry = worklog.save_entry(db, user, data)
    except worklog.FormError as error:
        return hours_form(request, user, db, data=data, error=error)
    month = entry.work_date.strftime("%Y-%m")
    commit(db)
    return RedirectResponse(worklog.month_url(month) + "&saved=1", status_code=303)


@router.get("/hours/history")
def history_page(request: Request, user: CurrentUser, db: DatabaseSession, month: str = "",
                 client_id: str = "", project_id: str = "", billable: str = ""):
    try:
        summary = worklog.monthly_view(db, user, month, client_id, project_id, billable)
    except worklog.FormError as error:
        raise HTTPException(400, error.message) from None
    return render(request, user, "hours_history.html", **summary)


# La ruta estática precede a las rutas que contienen un ID.
@router.get("/hours/week")
def week_page(request: Request, user: CurrentUser, db: DatabaseSession, week: str = ""):
    try:
        anchor = worklog.parse_date(week, "week") if week else date.today()
    except worklog.FormError as error:
        raise HTTPException(400, error.message) from None
    return render(request, user, "hours_week.html", **worklog.weekly_view(db, user, anchor))


@router.get("/hours/{entry_id}/edit")
def edit_hours_page(entry_id: int, request: Request, user: CurrentUser, db: DatabaseSession):
    return hours_form(request, user, db, entry=worklog.owned_entry(db, user, entry_id))


@router.post("/hours/{entry_id}/edit")
def edit_hours(entry_id: int, request: Request, user: CurrentUser, db: DatabaseSession, data: PostedForm):
    try:
        entry = worklog.save_entry(db, user, data, entry_id=entry_id)
    except worklog.FormError as error:
        return hours_form(request, user, db, data=data, entry=worklog.owned_entry(db, user, entry_id), error=error)
    month = entry.work_date.strftime("%Y-%m")
    commit(db)
    return RedirectResponse(worklog.month_url(month) + "&saved=1", status_code=303)


@router.get("/hours/{entry_id}/delete")
def delete_hours_page(entry_id: int, request: Request, user: CurrentUser, db: DatabaseSession):
    return render(request, user, "hours_delete.html", entry=worklog.owned_entry(db, user, entry_id))


@router.post("/hours/{entry_id}/delete")
def delete_hours(entry_id: int, request: Request, user: CurrentUser, db: DatabaseSession, data: PostedForm):
    day = worklog.delete_entry(db, user, entry_id)
    commit(db)
    return RedirectResponse(worklog.month_url(day.strftime("%Y-%m")) + "&deleted=1", status_code=303)
