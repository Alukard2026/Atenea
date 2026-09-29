"""IA manual por mensaje: POST+CSRF, Graph del dueño y formulario sin guardado."""

import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select

from app import ai_provider, mail, mail_ai, mail_rules, mail_tasks
from app.graph import GraphClient, GraphError
from app.routers.auth import templates
from app.routers.mail import error_page
from app.routers.mail_tasks import render_form
from app.routers.tasks import task_zone
from app.routers.work import CurrentUser
from app.web_auth import DatabaseSession, csrf_token, validate_csrf


router = APIRouter(prefix="/mail")


async def ai_form(request: Request):
    if len(await request.body()) > 50000:
        raise HTTPException(413, "Formulario demasiado grande.")
    async with request.form(max_files=0, max_fields=3, max_part_size=40000) as form:
        if any(not isinstance(v, str) or len(form.getlist(k)) != 1 for k, v in form.items()):
            raise HTTPException(400, "Formulario inválido.")
        data = dict(form)
    validate_csrf(request, data.get("csrf", ""))
    return data


AIForm = Annotated[dict, Depends(ai_form)]


def ai_error_page(request, user, error):
    return templates.TemplateResponse(request=request, name="mail_ai_error.html", status_code=error.status_code,
        context={"user": user, "csrf_token": csrf_token(request), "message": error.message})


def validate(request, data, fields):
    if request.query_params or data.keys() - fields:
        raise HTTPException(400, "El formulario contiene campos no permitidos.")
    error = ai_provider.configuration_error(request.app.state.settings)
    if error:
        raise ai_provider.AIError(error)


@router.post("/{message_id}/analyze")
def analyze(request: Request, message_id: str, user: CurrentUser, db: DatabaseSession, data: AIForm):
    try:
        validate(request, data, {"csrf"})
        mail.message_id(message_id)
        if time.time() < request.session.get("ai_next_allowed", 0):
            raise ai_provider.AIError("busy")
        client = GraphClient(db, user, request.app.state.settings)
        zone = task_zone(request, user)
        message = mail.get_message(client, message_id, zone)
        mail_rules.analyze_messages(db, user, [message])
        # Graph puede renovar/confirmar cache; bloquear después de obtener el mensaje.
        if not db.scalar(select(func.pg_try_advisory_xact_lock(41002, user.id))):
            raise ai_provider.AIError("busy")
        mail_tasks.lock_connection(client)  # Evitar desconexión/cambio durante el envío.
        request.session["ai_next_allowed"] = time.time() + 60
        prepared = mail_ai.prepare_email(message, zone, client.settings)
        result = mail_ai.analyze_email(client.settings, prepared)
        draft, warnings = mail_ai.task_draft(result, zone)
        token = mail_ai.encode_draft(client, message_id, csrf_token(request), draft) if draft else None
        return templates.TemplateResponse(request=request, name="mail_detail.html", context={
            "user": user, "csrf_token": csrf_token(request), "message": message, "zone": zone.key,
            "ai": ai_provider.availability(client.settings), "ai_result": result,
            "ai_partial": prepared.truncated, "ai_draft": token, "ai_warnings": warnings,
        })
    except GraphError as error:
        db.rollback()
        return error_page(request, user, error)
    except ai_provider.AIError as error:
        db.rollback()
        return ai_error_page(request, user, error)


@router.post("/{message_id}/ai/create-task")
def suggested_task(request: Request, message_id: str, user: CurrentUser, db: DatabaseSession, data: AIForm):
    try:
        validate(request, data, {"csrf", "draft"})
        mail.message_id(message_id)
        client = GraphClient(db, user, request.app.state.settings)
        draft = mail_ai.decode_draft(client, message_id, csrf_token(request), data.get("draft", ""))
        metadata = mail_tasks.message_metadata(client, message_id)  # Acceso actual al buzón propio.
        mail_tasks.lock_connection(client)
        suggestion = mail_rules.analyze_inputs(db, user, [metadata["rule_input"]])[0].entity
        values = {"title": draft.title, "description": draft.description, "priority": draft.priority,
                  "due_date": draft.due_date or "", "due_time": draft.due_time or "",
                  "client_id": str(suggestion.client.id) if suggestion.client and suggestion.client.is_active else ""}
        return render_form(request, user, db, client, message_id, metadata, data=values, from_ai=True)
    except GraphError as error:
        db.rollback()
        return error_page(request, user, error)
    except ai_provider.AIError as error:
        db.rollback()
        return ai_error_page(request, user, error)
