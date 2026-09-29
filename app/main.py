"""Arranque: python -m uvicorn app.main:create_app --factory --reload."""

import logging
from datetime import datetime
from pathlib import Path
import secrets
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import Settings, load_settings
from app.models import User
from app.routers.auth import LOGIN_ERROR, login_page, router, templates
from app.routers.work import router as work_router
from app.routers.settings import router as settings_router
from app.routers.reports import router as reports_router
from app.routers.tasks import router as tasks_router
from app.routers.microsoft import router as microsoft_router
from app.routers.mail import router as mail_router
from app.routers.mail_tasks import router as mail_tasks_router
from app.routers.mail_ai import router as mail_ai_router
from app.routers.notifications import router as notifications_router
from app import notifications
from app.models import MicrosoftAccount, Task
from sqlalchemy import select, func
from app.microsoft import configure_safe_logging
from app.tasks import dashboard_summary, aware_now
from app.timezones import organization_zone
from app.security import hash_password
from app.web_auth import DatabaseSession, csrf_token, get_current_user
from app.worklog import current_month_total, current_week_total


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings if settings is not None else load_settings()
    configure_safe_logging()
    app = FastAPI(title="Atenea", debug=False, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.task_zone = ZoneInfo(settings.app_timezone)
    app.state.settings = settings
    app.state.dummy_password_hash = hash_password(secrets.token_urlsafe(32))
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        session_cookie="atenea_session",
        max_age=settings.session_max_age,
        same_site="lax",
        https_only=settings.session_cookie_secure,
    )

    @app.middleware("http")
    async def safe_responses(request: Request, call_next):
        try:
            response = await call_next(request)
        except Exception:
            # Evitar tracebacks de SQLAlchemy con parámetros o datos sensibles.
            logging.getLogger("atenea").error("No se pudo procesar una solicitud; detalles sensibles omitidos.")
            response = PlainTextResponse("No se pudo completar la solicitud. Inténtalo más tarde.", status_code=503)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        # Chromium comprueba form-action también tras el 303 del POST OAuth.
        form_action = "'self'"
        if request.url.path in {"/integrations/microsoft", "/integrations/microsoft/connect"}:
            form_action += " https://login.microsoftonline.com"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; style-src 'self'; img-src 'self'; script-src 'self'; connect-src 'self'; "
            f"form-action {form_action}; frame-ancestors 'none'; base-uri 'none'"
        )
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_form(request: Request, error: RequestValidationError):
        # La respuesta predeterminada puede incluir el valor inválido del password.
        if request.url.path == "/login":
            return login_page(request, LOGIN_ERROR, status_code=400)
        return PlainTextResponse("Formulario inválido. Recarga la página.", status_code=400)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, error: StarletteHTTPException):
        if error.status_code == 303:
            return RedirectResponse("/login", status_code=303)
        return templates.TemplateResponse(
            request=request, name="error.html", status_code=error.status_code,
            context={"message": error.detail, "status_code": error.status_code}, headers=error.headers,
        )

    app.mount("/static", StaticFiles(directory=str(Path(__file__).resolve().parent / "static")), name="static")
    app.include_router(router)
    app.include_router(work_router)
    app.include_router(settings_router)
    app.include_router(reports_router)
    app.include_router(tasks_router)
    app.include_router(microsoft_router)
    app.include_router(mail_router)
    app.include_router(mail_tasks_router)
    app.include_router(mail_ai_router)
    app.include_router(notifications_router)

    @app.get("/", include_in_schema=False)
    def index():
        return RedirectResponse("/dashboard", status_code=303)

    @app.get("/dashboard", response_class=HTMLResponse)
    def dashboard(request: Request, user: Annotated[User, Depends(get_current_user)], db: DatabaseSession):
        zone = organization_zone(user.organization, app.state.task_zone)
        now = aware_now()
        local_day = now.astimezone(zone).date()
        reminder_status = notifications.status(db, user, now)
        return templates.TemplateResponse(
            request=request, name="dashboard.html",
            context={"user": user, "csrf_token": csrf_token(request), "week_total": current_week_total(db, user, today=local_day),
                     "month_total": current_month_total(db, user, today=local_day), "task_summary": dashboard_summary(db, user, zone, now),
                     "notification_status": reminder_status,
                     "next_reminder": datetime.fromisoformat(reminder_status["next_at"]).astimezone(zone) if reminder_status["next_at"] else None,
                     "pending_tasks": db.scalar(select(func.count()).select_from(Task).where(Task.organization_id == user.organization_id, Task.user_id == user.id, Task.status == "pending")),
                     "microsoft_connected": db.scalar(select(MicrosoftAccount.id).where(MicrosoftAccount.organization_id == user.organization_id, MicrosoftAccount.user_id == user.id, MicrosoftAccount.is_active.is_(True))) is not None},
        )

    return app
