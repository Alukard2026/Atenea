"""Centro, contador y acciones de recordatorios del usuario autenticado."""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from app import notifications, tasks
from app.routers.tasks import task_render, task_zone
from app.routers.work import CurrentUser, PostedForm, commit
from app.web_auth import DatabaseSession
from app.worklog import FormError


router = APIRouter(prefix="/notifications")


def validate_query(request, allowed=()):
    if any(key not in allowed or len(request.query_params.getlist(key)) != 1 for key in request.query_params):
        raise HTTPException(400, "Parámetros de notificaciones inválidos.")


@router.get("")
def notifications_page(request: Request, user: CurrentUser, db: DatabaseSession, page: int = 1):
    validate_query(request, {"page"})
    zone = task_zone(request, user)
    return task_render(request, user, "notifications.html", **notifications.listing(db, user, zone, page))


@router.get("/status")
def notifications_status(request: Request, user: CurrentUser, db: DatabaseSession):
    validate_query(request)
    return notifications.status(db, user)


@router.post("/{task_id}/snooze")
def snooze(task_id: int, request: Request, user: CurrentUser, db: DatabaseSession, data: PostedForm):
    validate_query(request)
    if data.keys() - {"csrf", "delay"}:
        raise HTTPException(400, "El formulario contiene campos no permitidos.")
    try:
        notifications.snooze(db, user, task_id, data.get("delay"), task_zone(request, user))
    except FormError as error:
        raise HTTPException(400, error.message) from None
    commit(db)
    return RedirectResponse("/notifications", status_code=303)


@router.post("/{task_id}/complete")
def complete(task_id: int, request: Request, user: CurrentUser, db: DatabaseSession, data: PostedForm):
    validate_query(request)
    if data.keys() - {"csrf"}:
        raise HTTPException(400, "El formulario contiene campos no permitidos.")
    task = tasks.owned_task(db, user, task_id, lock=True)
    if task.status == "cancelled" or task.reminder_at is None:
        raise HTTPException(409, "La tarea ya no tiene un recordatorio disponible.")
    tasks.set_status(db, user, task_id, "completed", zone=task_zone(request, user))
    commit(db)
    return RedirectResponse("/notifications", status_code=303)
