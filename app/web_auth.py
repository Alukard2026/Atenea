"""Dependencias reutilizables de sesión y protección CSRF."""

import re
import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session, contains_eager, defer

from app.database import get_db
from app.models import Organization, User


DatabaseSession = Annotated[Session, Depends(get_db)]


def csrf_token(request: Request) -> str:
    token = request.session.get("csrf_token")
    if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
        token = secrets.token_urlsafe(32)
        request.session["csrf_token"] = token
    return token


def validate_csrf(request: Request, submitted: str) -> None:
    expected = request.session.get("csrf_token")
    if (
        not isinstance(expected, str)
        or not re.fullmatch(r"[A-Za-z0-9_-]{43}", submitted)
        or not secrets.compare_digest(expected.encode("utf-8"), submitted.encode("utf-8"))
    ):
        raise HTTPException(403, "El formulario ha caducado. Recarga la página e inténtalo de nuevo.")


def get_optional_user(request: Request, db: DatabaseSession) -> User | None:
    """Comprueba los dos IDs y los estados actuales en PostgreSQL en cada acceso."""
    user_id = request.session.get("user_id")
    organization_id = request.session.get("organization_id")
    if user_id is None and organization_id is None:
        return None
    if any(type(value) is not int or not 0 < value <= 2**31 - 1 for value in (user_id, organization_id)):
        request.session.clear()
        return None
    user = db.scalar(
        select(User).join(User.organization).where(
            User.id == user_id,
            User.organization_id == organization_id,
            User.is_active.is_(True),
            Organization.is_active.is_(True),
        ).options(contains_eager(User.organization), defer(User.hashed_password, raiseload=True))
    )
    if user is None:
        request.session.clear()
    return user


def get_current_user(user: Annotated[User | None, Depends(get_optional_user)]) -> User:
    """Usar Depends(get_current_user) en todas las futuras rutas protegidas."""
    if user is None:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return user


def get_admin_user(user: Annotated[User, Depends(get_current_user)]) -> User:
    """El rol se consulta en PostgreSQL junto con el usuario en cada petición."""
    if user.role != "admin":
        raise HTTPException(403, "Solo los administradores de la organización pueden acceder a esta configuración.")
    return user
