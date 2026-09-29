"""Login por email global; la cuenta determina la organización de la sesión."""

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.models import User
from app.security import verify_password
from app.users import email_key
from app.web_auth import DatabaseSession, csrf_token, get_optional_user, validate_csrf


router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))
LOGIN_ERROR = "Email o contraseña incorrectos."


async def valid_login_fields(request: Request):
    async with request.form(max_files=0, max_fields=4, max_part_size=4096) as form:
        return not request.query_params and all(key in {"email", "password", "csrf"} and isinstance(value, str) and len(form.getlist(key)) == 1 for key, value in form.items())


def login_page(request: Request, error: str | None = None, status_code: int = 200):
    return templates.TemplateResponse(
        request=request, name="login.html",
        context={"csrf_token": csrf_token(request), "error": error},
        status_code=status_code,
    )


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request, user: Annotated[User | None, Depends(get_optional_user)]):
    if user is not None:
        return RedirectResponse("/dashboard", status_code=303)
    return login_page(request)


@router.post("/login", response_class=HTMLResponse)
def login(
    request: Request,
    db: DatabaseSession,
    valid_fields: Annotated[bool, Depends(valid_login_fields)],
    email: Annotated[str, Form(max_length=320)] = "",
    password: Annotated[str, Form(max_length=72)] = "",
    csrf: Annotated[str, Form(max_length=100)] = "",
):
    validate_csrf(request, csrf)
    matches = db.scalars(select(User).where(email_key() == email.strip().lower()).options(joinedload(User.organization)).limit(2)).all()
    user = matches[0] if len(matches) == 1 else None
    organization_record = user.organization if user is not None else None

    # También ejecutar bcrypt cuando no existe la cuenta, para reducir diferencias
    # de tiempo evidentes. No registrar cuerpos de formulario ni valores de sesión.
    candidate_hash = user.hashed_password if user is not None else request.app.state.dummy_password_hash
    password_ok = verify_password(password, candidate_hash)
    if (
        not valid_fields or not password_ok or user is None or organization_record is None
        or not user.is_active or not organization_record.is_active
    ):
        request.session.clear()
        return login_page(request, LOGIN_ERROR, status_code=401)

    request.session.clear()
    request.session.update({"user_id": user.id, "organization_id": user.organization_id, "auth_version": user.auth_version})
    csrf_token(request)  # Rotar también el token al cambiar de identidad.
    return RedirectResponse("/dashboard", status_code=303)


@router.post("/logout")
def logout(request: Request, csrf: Annotated[str, Form(max_length=100)] = ""):
    validate_csrf(request, csrf)
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
