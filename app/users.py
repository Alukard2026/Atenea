"""Administración de cuentas locales con identidad global y ámbito organizativo."""
import re

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import defer

from app.models import Organization, User
from app.security import hash_password, InvalidPasswordError
from app.worklog import FormError, clean_text

ROLES = {"user": "Usuario", "admin": "Administrador"}
EMAIL_UNAVAILABLE = "No se puede utilizar ese email. Revisa los datos o contacta con soporte."


def normalize_email(value):
    if not isinstance(value, str):
        raise FormError("email", "Introduce un email válido.")
    value = value.strip().lower()
    if len(value) > 320 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value) or not value.isprintable():
        raise FormError("email", "Introduce un email válido.")
    return value


def email_key():
    return func.lower(func.btrim(User.email))


def owned_user(db, admin, identity):
    if not 0 < identity <= 2**31 - 1:
        raise HTTPException(404, "No se encontró el usuario.")
    user = db.scalar(select(User).where(User.id == identity, User.organization_id == admin.organization_id).options(defer(User.hashed_password, raiseload=True)))
    if user is None:
        raise HTTPException(404, "No se encontró el usuario.")
    return user


def lock_organization(db, admin):
    """Serializar cambios de admins; revalidar permisos tras esperar el bloqueo."""
    expected_version = admin.auth_version
    active = db.scalar(select(Organization.is_active).where(Organization.id == admin.organization_id).with_for_update())
    current = db.execute(select(User.role, User.is_active, User.auth_version).where(User.id == admin.id, User.organization_id == admin.organization_id)).one_or_none()
    if not active or current is None or current.role != "admin" or not current.is_active or current.auth_version != expected_version:
        raise HTTPException(403, "Tu sesión ya no permite administrar usuarios. Vuelve a iniciar sesión.")


def password_hash(data):
    password = data.get("password", "")
    if password != data.get("password_confirmation", ""):
        raise FormError("password_confirmation", "Las contraseñas no coinciden.")
    try:
        return hash_password(password)
    except InvalidPasswordError as error:
        raise FormError("password", str(error)) from None


def save_user(db, admin, data, identity=None):
    allowed = {"csrf", "full_name", "email", "role", "is_active"}
    if identity is None:
        allowed |= {"password", "password_confirmation"}
    if data.keys() - allowed:
        raise HTTPException(400, "El formulario contiene campos no permitidos.")
    lock_organization(db, admin)
    target = owned_user(db, admin, identity) if identity is not None else None
    name = clean_text(data, "full_name", "el nombre completo", 255, required=True)
    email = normalize_email(data.get("email", ""))
    role, active = data.get("role", ""), data.get("is_active", "")
    if role not in ROLES:
        raise FormError("role", "Selecciona un rol válido.")
    if active not in {"yes", "no"}:
        raise FormError("is_active", "Selecciona un estado válido.")
    active = active == "yes"
    query = select(User.id).where(email_key() == email)
    if target is not None:
        query = query.where(User.id != target.id)
    if db.scalar(query.limit(1)) is not None:
        raise FormError("email", EMAIL_UNAVAILABLE)
    if target and target.role == "admin" and target.is_active and (role != "admin" or not active):
        other_admin = db.scalar(select(User.id).where(User.organization_id == admin.organization_id, User.id != target.id, User.role == "admin", User.is_active.is_(True)).limit(1))
        if other_admin is None:
            raise FormError("role", "La organización debe conservar al menos un administrador activo.")
    if target is None:
        target = User(organization_id=admin.organization_id, hashed_password=password_hash(data), auth_version=0)
        db.add(target)
    elif target.is_active != active or target.email.strip().lower() != email:
        target.auth_version += 1
    target.full_name, target.email, target.role, target.is_active = name, email, role, active
    db.flush()
    return target


def reset_password(db, admin, identity, data):
    if data.keys() - {"csrf", "password", "password_confirmation"}:
        raise HTTPException(400, "El formulario contiene campos no permitidos.")
    lock_organization(db, admin)
    target = owned_user(db, admin, identity)
    target.hashed_password = password_hash(data)
    target.auth_version += 1
    db.flush()
    return target
