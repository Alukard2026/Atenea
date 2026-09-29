"""Minimización y traspaso efímero de sugerencias; ninguna persistencia de correo."""

from dataclasses import dataclass
from datetime import datetime
import json
import re
import time

from app import ai_provider, mail, microsoft, tasks
from app.ai_schema import EmailAnalysis, TaskDraft
from app.worklog import FormError


MAX_BODY_CHARACTERS = 12000
MAX_INPUT_CHARACTERS = ai_provider.MAX_INPUT_CHARACTERS
DRAFT_TTL = 15 * 60


@dataclass(frozen=True)
class PreparedEmail:
    payload: str
    truncated: bool


def prepare_email(message, zone, settings):
    """Allowlist explícita. Nunca serializar Request, User, Account ni Settings."""
    known_secrets = [value for value in (
        settings.session_secret, settings.openai_api_key, settings.microsoft_client_secret,
        settings.token_encryption_key,
    ) if len(value) >= 8 and value != "replace_me"]

    def clean(value, limit):
        # Reutilizar el extractor de Atenea, incluso si Graph etiquetó HTML como texto.
        value = mail.body_text({"contentType": "html", "content": value})
        for secret in known_secrets:
            value = value.replace(secret, "[dato reservado]")
        value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", value)
        return value[:limit]

    analysis = message["analysis"]
    body = clean(message["body"], MAX_BODY_CHARACTERS + 1)
    partial = bool(message["body_truncated"] or len(body) > MAX_BODY_CHARACTERS or len(message["subject"]) >= 1000)
    context = {
        "subject": clean(message["subject"], 1000),
        "sender": clean(message["rule_input"].sender_email, 320),
        "received_local": message["received"], "timezone": zone.key,
        "body_text": body[:MAX_BODY_CHARACTERS], "truncated": partial,
        "known_client": {"name": clean(analysis.entity.name, 255), "type": analysis.entity.client_type} if analysis.entity.client else None,
        "rules": {"categories": list(analysis.categories), "priority": analysis.priority},
    }
    payload = json.dumps(context, ensure_ascii=False)
    while len(payload) > MAX_INPUT_CHARACTERS:
        context["truncated"] = True
        context["body_text"] = context["body_text"][:max(0, len(context["body_text"]) - (len(payload) - MAX_INPUT_CHARACTERS))]
        payload = json.dumps(context, ensure_ascii=False)
    return PreparedEmail(payload, context["truncated"])


def analyze_email(settings, prepared):
    try:
        result = ai_provider.get_provider(settings).analyze_email(prepared.payload)
        return EmailAnalysis.model_validate(result)
    except ai_provider.AIError:
        raise
    except Exception:
        raise ai_provider.AIError("invalid") from None


def task_draft(result, zone):
    warnings = []
    if any(item.kind == "inferred" for item in result.action_items):
        warnings.append("Hay acciones inferidas por la IA; confirma que realmente correspondan antes de crear una tarea.")
    if any(item.date is None for item in result.dates_found):
        warnings.append("Hay fechas no determinadas. Verifica su interpretación en el correo original.")
    if not result.suggested_task_title:
        return None, warnings
    dates = {(item.due_date, item.due_time) for item in result.action_items if item.kind == "explicit" and item.due_date}
    due_date, due_time = next(iter(dates)) if len(dates) == 1 else (None, None)
    if len(dates) > 1:
        warnings.append("Hay varios plazos: el formulario dejará la fecha vacía para que la elijas.")
    if due_date and due_time:
        try:
            tasks.local_instant(datetime.fromisoformat(due_date + "T" + due_time), zone, "due_time")
        except FormError:
            due_date, due_time = None, None
            warnings.append("La hora sugerida es ambigua o inexistente en tu zona horaria. Elige fecha y hora en el formulario.")
    return TaskDraft(title=result.suggested_task_title, description=result.suggested_task_description or "",
                     priority=result.suggested_priority, due_date=due_date, due_time=due_time), warnings


def encode_draft(client, identity, csrf, draft):
    return microsoft.encrypt(client.settings, client.user, "mail-ai-draft", {
        "message": identity, "connection": client.connection_id, "session": microsoft.digest(csrf),
        "issued": time.time(), "draft": draft.model_dump(),
    })


def decode_draft(client, identity, csrf, token):
    try:
        if not 0 < len(token) <= 40000:
            raise ValueError()
        value = microsoft.decrypt(client.settings, client.user, "mail-ai-draft", token)
        if (value["message"] != identity or value["connection"] != client.connection_id
            or value["session"] != microsoft.digest(csrf) or not 0 <= time.time() - value["issued"] <= DRAFT_TTL):
            raise ValueError()
        return TaskDraft.model_validate(value["draft"])
    except Exception:
        raise ai_provider.AIError("draft") from None
