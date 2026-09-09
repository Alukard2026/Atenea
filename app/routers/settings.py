"""Configuración de días laborables de la organización del administrador."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import update

from app.models import Organization, User
from app.routers.work import PostedForm, commit, render
from app.web_auth import DatabaseSession, get_admin_user
from app.workdays import DAY_NAMES, WORKDAY_FIELDS
from app.timezones import supported_timezones, valid_timezone
from app.worklog import FormError


router = APIRouter(default_response_class=HTMLResponse)
AdminUser = Annotated[User, Depends(get_admin_user)]


@router.get("/settings/timezone")
def timezone_page(request: Request, user: AdminUser):
    return render(request, user, "settings_timezone.html", zones=sorted(supported_timezones()), selected_zone=user.organization.timezone)


@router.post("/settings/timezone")
def save_timezone(request: Request, user: AdminUser, db: DatabaseSession, data: PostedForm):
    if set(data) - {"csrf", "timezone"}:
        raise HTTPException(400, "El formulario contiene campos no permitidos.")
    try:
        zone = valid_timezone(data.get("timezone"))
    except ValueError:
        return render(request, user, "settings_timezone.html", zones=sorted(supported_timezones()), selected_zone=user.organization.timezone,
                      error=FormError("timezone", "Selecciona una zona horaria válida de la lista."))
    user.organization.timezone = zone
    commit(db)
    return RedirectResponse("/settings/timezone?saved=1", status_code=303)


@router.get("/settings/workdays")
def workdays_page(request: Request, user: AdminUser):
    return render(request, user, "settings_workdays.html", workdays=[
        {"field": field, "label": label, "enabled": getattr(user.organization, field)}
        for field, label in zip(WORKDAY_FIELDS, DAY_NAMES)
    ])


@router.post("/settings/workdays")
def save_workdays(request: Request, user: AdminUser, db: DatabaseSession, data: PostedForm):
    # Un checkbox sin marcar no se envía. Siete ausencias significan siete False.
    # Rechazar nombres/valores desconocidos evita modificaciones parciales ambiguas.
    if set(data) - set(WORKDAY_FIELDS) - {"csrf"} or any(
        data[field] != "on" for field in WORKDAY_FIELDS if field in data
    ):
        raise HTTPException(400, "La selección de días laborables no es válida. Recarga el formulario.")
    values = {field: field in data for field in WORKDAY_FIELDS}
    result = db.execute(update(Organization).where(
        Organization.id == user.organization_id, Organization.is_active.is_(True),
    ).values(**values))
    if result.rowcount != 1:
        raise HTTPException(403, "No se pudo actualizar la organización activa.")
    commit(db)
    return RedirectResponse("/settings/workdays?saved=1", status_code=303)
