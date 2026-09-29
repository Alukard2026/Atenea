"""Gestión administrativa local, sin invitaciones ni envío de credenciales."""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app import users
from app.models import User
from app.routers.work import AdminUser, PostedForm, render
from app.web_auth import DatabaseSession
from app.worklog import FormError

router = APIRouter(prefix="/settings/users")


def query_check(request, allowed=()):
    if any(k not in allowed or len(request.query_params.getlist(k)) != 1 for k in request.query_params):
        raise HTTPException(400, "Parámetros de usuarios inválidos.")


def safe_data(data):
    return {k: v for k, v in data.items() if k in {"full_name", "email", "role", "is_active"}}


def form(request, admin, target=None, data=None, error=None, reset=False):
    if data is None:
        data = {"role": "user", "is_active": "yes"} if target is None else {"full_name": target.full_name, "email": target.email, "role": target.role, "is_active": "yes" if target.is_active else "no"}
    return render(request, admin, "user_form.html", target=target, data=safe_data(data), error=error, roles=users.ROLES, reset=reset)


@router.get("")
def listing(request: Request, admin: AdminUser, db: DatabaseSession, page: int = 1, saved: str = ""):
    query_check(request, {"page", "saved"})
    total = db.scalar(select(func.count()).select_from(User).where(User.organization_id == admin.organization_id))
    pages = max(1, (total + 49) // 50)
    if not 1 <= page <= pages:
        raise HTTPException(404, "No se encontró la página.")
    # Proyección explícita: nunca consultar hashes para listar cuentas.
    rows = db.execute(select(User.id, User.full_name, User.email, User.role, User.is_active).where(User.organization_id == admin.organization_id).order_by(func.lower(User.full_name), User.id).offset((page - 1) * 50).limit(50)).all()
    return render(request, admin, "users.html", rows=rows, roles=users.ROLES, page=page, pages=pages, total=total, saved=saved == "1")


@router.get("/new")
def new_page(request: Request, admin: AdminUser):
    query_check(request)
    return form(request, admin)


@router.post("/new")
def create(request: Request, admin: AdminUser, db: DatabaseSession, data: PostedForm):
    query_check(request)
    try:
        users.save_user(db, admin, data)
        db.commit()
    except (FormError, IntegrityError) as error:
        db.rollback()
        return form(request, admin, data=data, error=error if isinstance(error, FormError) else FormError("email", users.EMAIL_UNAVAILABLE))
    return RedirectResponse("/settings/users?saved=1", status_code=303)


@router.get("/{identity}/edit")
def edit_page(identity: int, request: Request, admin: AdminUser, db: DatabaseSession):
    query_check(request)
    return form(request, admin, target=users.owned_user(db, admin, identity))


@router.post("/{identity}/edit")
def edit(identity: int, request: Request, admin: AdminUser, db: DatabaseSession, data: PostedForm):
    query_check(request)
    try:
        users.save_user(db, admin, data, identity)
        db.commit()
    except (FormError, IntegrityError) as error:
        db.rollback()
        return form(request, admin, target=users.owned_user(db, admin, identity), data=data, error=error if isinstance(error, FormError) else FormError("email", users.EMAIL_UNAVAILABLE))
    return RedirectResponse("/dashboard" if identity == admin.id else "/settings/users?saved=1", status_code=303)


@router.get("/{identity}/password")
def password_page(identity: int, request: Request, admin: AdminUser, db: DatabaseSession):
    query_check(request)
    return form(request, admin, target=users.owned_user(db, admin, identity), reset=True)


@router.post("/{identity}/password")
def password_reset(identity: int, request: Request, admin: AdminUser, db: DatabaseSession, data: PostedForm):
    query_check(request)
    try:
        users.reset_password(db, admin, identity, data)
        db.commit()
    except FormError as error:
        db.rollback()
        return form(request, admin, target=users.owned_user(db, admin, identity), error=error, reset=True)
    if identity == admin.id:
        request.session.clear()
        return RedirectResponse("/login", status_code=303)
    return RedirectResponse("/settings/users?saved=1", status_code=303)
