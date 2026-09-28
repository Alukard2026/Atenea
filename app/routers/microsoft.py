"""Conexión Microsoft de la persona autenticada, sin endpoints de correo."""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app import microsoft
from app.models import MicrosoftAccount, User
from app.routers.auth import templates
from app.web_auth import DatabaseSession, csrf_token, get_current_user, validate_csrf


router = APIRouter(prefix="/integrations/microsoft")
CurrentUser = Annotated[User, Depends(get_current_user)]


@router.get("", response_class=HTMLResponse)
def status(request: Request, user: CurrentUser, db: DatabaseSession):
    account = db.scalar(microsoft.owner_query(MicrosoftAccount, user).where(MicrosoftAccount.is_active.is_(True)))
    return templates.TemplateResponse(request=request, name="microsoft.html", context={
        "user": user, "csrf_token": csrf_token(request), "account": account,
        "configured": microsoft.configured(request.app.state.settings),
        "error": microsoft.ERROR if request.query_params.get("result") == "error" else None,
    })


@router.post("/connect")
def connect(request: Request, user: CurrentUser, db: DatabaseSession, csrf: Annotated[str, Form(max_length=100)] = ""):
    validate_csrf(request, csrf)
    try:
        uri = microsoft.begin_flow(db, user, request.app.state.settings, csrf_token(request))
    except microsoft.MicrosoftError:
        return RedirectResponse("/integrations/microsoft?result=error", status_code=303)
    return RedirectResponse(uri, status_code=303)


@router.get("/callback")
def callback(request: Request, user: CurrentUser, db: DatabaseSession):
    try:
        # No aceptar IDs de propietario ni reflejar mensajes del proveedor.
        params = request.query_params
        if any(len(params.getlist(key)) > 1 for key in params) or any(k in params for k in ("user_id", "organization_id")):
            raise microsoft.MicrosoftError()
        response = {key: params[key] for key in ("code", "state", "error") if key in params}
        microsoft.finish_flow(db, user, request.app.state.settings, request.session.get("csrf_token", ""), response)
    except microsoft.MicrosoftError:
        db.rollback()
        return RedirectResponse("/integrations/microsoft?result=error", status_code=303)
    return RedirectResponse("/integrations/microsoft", status_code=303)


@router.post("/disconnect")
def disconnect(request: Request, user: CurrentUser, db: DatabaseSession, csrf: Annotated[str, Form(max_length=100)] = ""):
    validate_csrf(request, csrf)
    microsoft.disconnect(db, user)
    return RedirectResponse("/integrations/microsoft", status_code=303)
