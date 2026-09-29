"""Tareas desde metadatos y preview limitado; nunca obtiene el cuerpo completo."""

import json
import time
from urllib.parse import quote

from fastapi import HTTPException
from sqlalchemy import func, select

from app import mail, mail_rules, microsoft, tasks, worklog
from app.graph import GRAPH_ROOT, GraphError
from app.models import Client, MicrosoftAccount, Task


TASK_FIELDS = {"csrf", "title", "description", "due_date", "due_time", "priority", "client_id", "project_id", "reminder_at",
               "recurrence_type", "recurrence_interval", "recurrence_end_date"}
FORM_FIELDS = TASK_FIELDS | {"intent", "action", "new_client_name", "new_client_type", "email_domain"}


def context_token(client, identity, csrf):
    return microsoft.encrypt(client.settings, client.user, "mail-task", {
        "message": identity, "connection": client.connection_id, "session": microsoft.digest(csrf), "issued": time.time(),
    })


def validate_context(client, identity, csrf, token):
    try:
        if len(token) > 8000:
            raise ValueError()
        value = microsoft.decrypt(client.settings, client.user, "mail-task", token)
        if (value["message"] != identity or value["connection"] != client.connection_id
            or value["session"] != microsoft.digest(csrf) or not 0 <= time.time() - value["issued"] <= 1800):
            raise ValueError()
    except Exception:
        raise GraphError("cursor") from None


def message_metadata(client, identity):
    identity = mail.message_id(identity)
    payload = client.get(GRAPH_ROOT + "/me/messages/" + quote(identity, safe=""),
                         params={"$select": "id,subject,from,bodyPreview"}, endpoint="message")
    if payload.get("id") != identity:
        raise GraphError("response")
    features = mail_rules.message_input(payload)
    return {"subject": features.subject[:255] or "Correo sin asunto", "sender": features.sender_email,
            "rule_input": features}


def lock_connection(client):
    microsoft.lock_owner(client.db, client.user)
    account = client.db.scalar(microsoft.owner_query(MicrosoftAccount, client.user).where(
        MicrosoftAccount.is_active.is_(True)).execution_options(populate_existing=True))
    if account is None or client._connection_id(account) != client.connection_id:
        raise GraphError("reconnect")


def create_detected_client(db, user, suggestion, data):
    if user.role != "admin":
        raise HTTPException(403, "Solo los administradores pueden crear clientes. Selecciona uno existente.")
    if not suggestion.domain or suggestion.public_provider:
        raise worklog.FormError("client_id", "Selecciona un cliente manualmente; este remitente no identifica una empresa.")
    # Compartir el bloqueo con todas las altas/ediciones del catálogo.
    db.execute(select(func.pg_advisory_xact_lock(41001, user.organization_id)))
    existing = db.scalar(select(Client).where(Client.organization_id == user.organization_id, Client.email_domain == suggestion.domain))
    if existing:
        if not existing.is_active:
            raise worklog.FormError("client_id", "El cliente de ese dominio está archivado. Un administrador debe reactivarlo; no se creará un duplicado.")
        return existing
    client = worklog.create_client(db, user, {
        "name": data.get("new_client_name", ""), "email_domain": suggestion.domain,
        "client_type": data.get("new_client_type", suggestion.client_type),
    })
    db.flush()
    return client


def save_mail_task(client, identity, data, zone):
    lock_connection(client)
    source = microsoft.digest(json.dumps([client.connection_id[1], identity], separators=(",", ":")))
    existing = client.db.scalar(select(Task).where(Task.organization_id == client.user.organization_id,
        Task.user_id == client.user.id, Task.source_type == "microsoft_mail", Task.source_id == source))
    if existing:
        return existing
    task = tasks.save_task(client.db, client.user, {k: v for k, v in data.items() if k in TASK_FIELDS}, zone)
    task.source_type, task.source_id = "microsoft_mail", source
    client.db.flush()
    return task
