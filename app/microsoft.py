"""OAuth delegado de Microsoft; solo identidad /me y cache MSAL cifrado."""

from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import re
import secrets
from urllib.parse import urlsplit
from uuid import UUID

from cryptography.fernet import Fernet
import msal
import requests
from sqlalchemy import delete, select

from app.models import MicrosoftAccount, MicrosoftOAuthFlow, User


SCOPES = ["User.Read", "Mail.Read"]  # MSAL agrega openid, profile y offline_access.
GRAPH_ME = "https://graph.microsoft.com/v1.0/me"
ERROR = "No se pudo conectar Microsoft. Vuelve a iniciar la conexión."


class MicrosoftError(Exception):
    def __init__(self):
        super().__init__(ERROR)


class CallbackAccessFilter(logging.Filter):
    """Omitir códigos OAuth e identificadores/cursores del buzón en access logs."""
    def filter(self, record):
        message = record.getMessage()
        return "/integrations/microsoft/callback" not in message and not re.search(r"/mail(?:[/?\s]|$)", message)


def configure_safe_logging():
    logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, CallbackAccessFilter) for f in logger.filters):
        logger.addFilter(CallbackAccessFilter())
    # MSAL puede registrar respuestas de tokens a nivel DEBUG, incluso sin PII.
    for name in {"msal", "requests", "urllib3", "openai", "httpx2", "httpcore2", *logging.Logger.manager.loggerDict}:
        if name.split(".")[0] in {"msal", "requests", "urllib3", "openai", "httpx2", "httpcore2"}:
            sdk_logger = logging.getLogger(name)
            sdk_logger.disabled = True
            sdk_logger.setLevel(logging.CRITICAL + 1)


def cipher(settings):
    try:
        return Fernet(settings.token_encryption_key.encode("ascii"))
    except Exception:
        raise MicrosoftError() from None


def configured(settings):
    try:
        values = (settings.microsoft_client_id, settings.microsoft_client_secret,
                  settings.microsoft_tenant, settings.microsoft_redirect_uri, settings.token_encryption_key)
        if any(not v or v == "replace_me" for v in values):
            return False
        UUID(settings.microsoft_client_id)
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,254}", settings.microsoft_tenant):
            return False
        uri = urlsplit(settings.microsoft_redirect_uri)
        if (uri.scheme != "https" and not (uri.scheme == "http" and uri.hostname in {"localhost", "127.0.0.1"})):
            return False
        if not uri.hostname or uri.username or uri.password or uri.query or uri.fragment or uri.path != "/integrations/microsoft/callback":
            return False
        cipher(settings)
        return True
    except Exception:
        return False


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def encrypt(settings, user, purpose, value):
    payload = {"organization_id": user.organization_id, "user_id": user.id, "purpose": purpose, "value": value}
    return cipher(settings).encrypt(json.dumps(payload).encode("utf-8")).decode("ascii")


def decrypt(settings, user, purpose, value):
    try:
        payload = json.loads(cipher(settings).decrypt(value.encode("ascii")))
        if (payload["organization_id"], payload["user_id"], payload["purpose"]) != (user.organization_id, user.id, purpose):
            raise MicrosoftError()
        return payload["value"]
    except Exception:
        raise MicrosoftError() from None


def owner_query(model, user):
    return select(model).where(model.organization_id == user.organization_id, model.user_id == user.id)


def lock_owner(db, user):
    # Serializa connect/callback/disconnect incluso si aún no hay conexión.
    db.execute(select(User.id).where(User.id == user.id, User.organization_id == user.organization_id).with_for_update()).one()


def client(settings, cache):
    if not configured(settings):
        raise MicrosoftError()
    return msal.ConfidentialClientApplication(
        settings.microsoft_client_id, client_credential=settings.microsoft_client_secret,
        authority="https://login.microsoftonline.com/" + settings.microsoft_tenant,
        token_cache=cache, timeout=15, enable_pii_log=False,
    )


def configuration_digest(settings):
    return digest(json.dumps([settings.microsoft_client_id, settings.microsoft_tenant, settings.microsoft_redirect_uri]))


def begin_flow(db, user, settings, session_token):
    try:
        cache = msal.SerializableTokenCache()
        flow = client(settings, cache).initiate_auth_code_flow(
            scopes=SCOPES, redirect_uri=settings.microsoft_redirect_uri,
            # El retorno GET conserva la cookie SameSite=Lax de Atenea.
            state=secrets.token_urlsafe(32), prompt="select_account", response_mode="query",
        )
        uri = urlsplit(flow["auth_uri"])
        if uri.scheme != "https" or uri.hostname != "login.microsoftonline.com":
            raise MicrosoftError()
        lock_owner(db, user)
        db.execute(delete(MicrosoftOAuthFlow).where(
            MicrosoftOAuthFlow.organization_id == user.organization_id, MicrosoftOAuthFlow.user_id == user.id,
        ))
        db.add(MicrosoftOAuthFlow(
            organization_id=user.organization_id, user_id=user.id,
            state_hash=digest(flow["state"]), session_hash=digest(session_token),
            flow_encrypted=encrypt(settings, user, "flow", {"flow": flow, "configuration": configuration_digest(settings)}),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        ))
        db.commit()
        return flow["auth_uri"]
    except Exception:
        db.rollback()
        raise MicrosoftError() from None


def graph_profile(access_token):
    with requests.get(
        GRAPH_ME, params={"$select": "id,mail,userPrincipalName,displayName"},
        headers={"Authorization": "Bearer " + access_token}, timeout=15, allow_redirects=False,
    ) as response:
        if response.status_code != 200:
            raise MicrosoftError()
        return response.json()


def identity_text(value, limit, required=False):
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise MicrosoftError()
    return value


def finish_flow(db, user, settings, session_token, response):
    lock_owner(db, user)
    pending = db.scalar(owner_query(MicrosoftOAuthFlow, user))
    state = response.get("state", "")
    if (pending is None or not isinstance(state, str) or len(state) > 512
        or not secrets.compare_digest(pending.state_hash, digest(state))
        or not secrets.compare_digest(pending.session_hash, digest(session_token))):
        raise MicrosoftError()
    # La eliminación y la conexión se confirman bajo el mismo bloqueo. Un fallo
    # de Microsoft también consume el intento; una desconexión posterior gana.
    db.delete(pending)
    db.flush()
    try:
        if pending.expires_at <= datetime.now(timezone.utc) or response.get("error") or not response.get("code"):
            raise MicrosoftError()
        saved = decrypt(settings, user, "flow", pending.flow_encrypted)
        if saved["configuration"] != configuration_digest(settings):
            raise MicrosoftError()
        cache = msal.SerializableTokenCache()
        result = client(settings, cache).acquire_token_by_auth_code_flow(saved["flow"], response, scopes=SCOPES)
        if "error" in result or not result.get("access_token"):
            raise MicrosoftError()
        profile = graph_profile(result["access_token"])
        identity = {
            "microsoft_account_id": identity_text(profile.get("id"), 255, True),
            "principal_name": identity_text(profile.get("userPrincipalName") or profile.get("mail"), 320, True),
            "email": identity_text(profile.get("mail"), 320),
            "display_name": identity_text(profile.get("displayName"), 255),
            "tenant_id": identity_text(result.get("id_token_claims", {}).get("tid"), 255),
        }
        encrypted_cache = encrypt(settings, user, "cache", cache.serialize())
    except Exception:
        db.commit()
        raise MicrosoftError() from None
    account = db.scalar(owner_query(MicrosoftAccount, user))
    if account is None:
        account = MicrosoftAccount(organization_id=user.organization_id, user_id=user.id)
        db.add(account)
    for name, value in identity.items():
        setattr(account, name, value)
    account.token_cache_encrypted = encrypted_cache
    account.is_active = True
    account.connected_at = account.updated_at = datetime.now(timezone.utc)
    db.commit()


def disconnect(db, user):
    lock_owner(db, user)
    for model in (MicrosoftOAuthFlow, MicrosoftAccount):
        db.execute(delete(model).where(model.organization_id == user.organization_id, model.user_id == user.id))
    db.commit()
