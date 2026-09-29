"""Formularios Outlook → tarea y confirmación de cliente detectado."""

from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from app import mail, mail_rules, mail_tasks
from app.client_domains import suggest_client
from app.graph import GraphClient, GraphError
from app.routers.mail import error_page
from app.routers.tasks import task_render, task_zone
from app.routers.work import CurrentUser, commit
from app.web_auth import DatabaseSession, csrf_token, validate_csrf
from app.worklog import FormError, active_clients, active_projects


router = APIRouter(prefix="/mail")


async def posted_data(request: Request):
    async with request.form(max_files=0, max_fields=24, max_part_size=8192) as form:
        if any(not isinstance(v, str) or len(form.getlist(k)) != 1 for k, v in form.items()):
            raise HTTPException(400, "Formulario inválido.")
        data = dict(form)
    validate_csrf(request, data.get("csrf", ""))
    if set(data) - mail_tasks.FORM_FIELDS:
        raise HTTPException(400, "El formulario contiene campos no permitidos.")
    return data


def render_form(request, user, db, client, identity, metadata, *, data=None, error=None, created=False, from_ai=False):
    analysis = mail_rules.analyze_inputs(db, user, [metadata["rule_input"]])[0]
    suggestion = analysis.entity
    if data is None:
        data = {"title": metadata["subject"], "description": "", "priority": analysis.priority,
                "client_id": str(suggestion.client.id) if suggestion.client and suggestion.client.is_active else ""}
    data = dict(data)
    data["intent"] = mail_tasks.context_token(client, identity, csrf_token(request))
    data.setdefault("new_client_name", suggestion.name)
    data.setdefault("new_client_type", suggestion.client_type)
    return task_render(request, user, "task_form.html", task=None, data=data, error=error,
        clients=active_clients(db, user), projects=active_projects(db, user),
        mail_context={"suggestion": suggestion, "analysis": analysis, "created": created, "from_ai": from_ai,
                      "action": "/mail/" + quote(identity, safe="") + "/create-task"})


@router.get("/{message_id}/create-task")
def create_page(request: Request, message_id: str, user: CurrentUser, db: DatabaseSession):
    try:
        if request.query_params:
            raise GraphError("invalid")
        mail.message_id(message_id)
        client = GraphClient(db, user, request.app.state.settings)
        metadata = mail_tasks.message_metadata(client, message_id)
        return render_form(request, user, db, client, message_id, metadata)
    except GraphError as error:
        return error_page(request, user, error)


@router.post("/{message_id}/create-task")
def create_from_mail(request: Request, message_id: str, user: CurrentUser, db: DatabaseSession,
                     data: Annotated[dict, Depends(posted_data)]):
    try:
        if request.query_params or data.get("action") not in {"create_client", "create_task"}:
            raise GraphError("invalid")
        mail.message_id(message_id)
        client = GraphClient(db, user, request.app.state.settings)
        mail_tasks.validate_context(client, message_id, csrf_token(request), data.get("intent", ""))
        metadata = mail_tasks.message_metadata(client, message_id)
        try:
            if data["action"] == "create_client":
                suggestion = suggest_client(db, user, metadata["sender"])
                if data.get("email_domain") != suggestion.domain:
                    raise FormError("email_domain", "El dominio debe corresponder al remitente. Recarga el formulario.")
                mail_tasks.lock_connection(client)
                detected = mail_tasks.create_detected_client(db, user, suggestion, data)
                data["client_id"], data["project_id"] = str(detected.id), ""
                commit(db)
                return render_form(request, user, db, client, message_id, metadata, data=data, created=True)
            task = mail_tasks.save_mail_task(client, message_id, data, task_zone(request, user))
            target = f"/tasks/{task.id}/edit"
            commit(db)
            return RedirectResponse(target, status_code=303)
        except FormError as error:
            db.rollback()
            return render_form(request, user, db, client, message_id, metadata, data=data, error=error)
    except GraphError as error:
        db.rollback()
        return error_page(request, user, error)
