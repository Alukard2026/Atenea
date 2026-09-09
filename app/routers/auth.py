"""Login por organización y cierre de sesión mediante formularios HTML."""

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select

from app.models import Organization, User
from app.security import verify_password
from app.web_auth import DatabaseSession, csrf_token, get_optional_user, validate_csrf


router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))
LOGIN_ERROR = "No se pudo iniciar sesión. Comprueba la organización, el email y la contraseña."


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
    organization: Annotated[str, Form(max_length=255)] = "",
    email: Annotated[str, Form(max_length=320)] = "",
    password: Annotated[str, Form(max_length=72)] = "",
    csrf: Annotated[str, Form(max_length=100)] = "",
):
    validate_csrf(request, csrf)
    organizations = db.scalars(select(Organization).where(
        func.lower(func.trim(Organization.name)) == func.lower(organization.strip()),
    ).limit(2)).all()
    organization_record = organizations[0] if len(organizations) == 1 else None
    user = None
    if organization_record is not None:
        user = db.scalar(select(User).where(
            User.organization_id == organization_record.id,
            func.lower(User.email) == func.lower(email.strip()),
        ))

    # También ejecutar bcrypt cuando no existe la cuenta, para reducir diferencias
    # de tiempo evidentes. No registrar cuerpos de formulario ni valores de sesión.
    candidate_hash = user.hashed_password if user is not None else request.app.state.dummy_password_hash
    password_ok = verify_password(password, candidate_hash)
    if (
        not password_ok or user is None or organization_record is None
        or not user.is_active or not organization_record.is_active
    ):
        request.session.clear()
        return login_page(request, LOGIN_ERROR, status_code=401)

    request.session.clear()
    request.session.update({"user_id": user.id, "organization_id": user.organization_id})
    csrf_token(request)  # Rotar también el token al cambiar de identidad.
    return RedirectResponse("/dashboard", status_code=303)


@router.post("/logout")
def logout(request: Request, csrf: Annotated[str, Form(max_length=100)] = ""):
    validate_csrf(request, csrf)
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
