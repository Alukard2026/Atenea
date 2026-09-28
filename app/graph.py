"""Cliente Graph de lectura, por petición. Solo persiste cambios del cache MSAL."""

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import logging
import math
import re
import time
from urllib.parse import unquote, urlsplit

import msal
import requests
from sqlalchemy.orm import undefer

from app import microsoft
from app.models import MicrosoftAccount


GRAPH_ROOT = "https://graph.microsoft.com/v1.0"
MAIL_SCOPES = ["Mail.Read"]
HTTP_TIMEOUT = (5, 15)
MAX_RETRY_WAIT = 2
logger = logging.getLogger("atenea.graph")

ERRORS = {
    "disconnected": (200, "Conecta tu cuenta Microsoft para consultar tu correo."),
    "reconnect": (401, "La conexión Microsoft necesita renovarse. Desconecta y vuelve a conectar tu cuenta."),
    "forbidden": (403, "Microsoft no permite consultar este buzón. Comprueba el acceso y el consentimiento de Mail.Read."),
    "missing": (404, "El correo no está disponible. Puede haberse movido o eliminado en Outlook."),
    "throttled": (429, "Microsoft está limitando las consultas. Espera antes de volver a intentarlo."),
    "unavailable": (503, "Microsoft no está disponible temporalmente. Vuelve a intentarlo más tarde."),
    "timeout": (504, "Microsoft tardó demasiado en responder. Vuelve a intentarlo."),
    "invalid": (400, "La dirección o los filtros no son válidos. Vuelve al inicio del correo."),
    "cursor": (400, "La página ha caducado o no corresponde a esta sesión. Vuelve al inicio del correo."),
    "response": (502, "No se pudo obtener una respuesta válida de Microsoft. Vuelve a intentarlo."),
}


class GraphError(Exception):
    def __init__(self, kind, retry_after=None):
        self.kind = kind
        self.status_code, self.message = ERRORS[kind]
        self.retry_after = retry_after
        super().__init__(self.message)


def safe_log(endpoint, status, request_id=None):
    # No URL, parámetros, cuerpos, headers ni excepciones del proveedor.
    identifier = request_id if isinstance(request_id, str) and re.fullmatch(r"[a-fA-F0-9]{8}(?:-[a-fA-F0-9]{4}){3}-[a-fA-F0-9]{12}", request_id) else "-"
    logger.warning("Graph endpoint=%s status=%s request_id=%s", endpoint, status, identifier)


def retry_delay(value):
    """Retry-After en segundos o HTTP-date, sin usar nunca un valor arbitrario."""
    try:
        if isinstance(value, str) and re.fullmatch(r"[0-9]{1,9}", value.strip()):
            return int(value.strip())
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            return None
        return max(0, math.ceil((dt - datetime.now(timezone.utc)).total_seconds()))
    except (TypeError, ValueError, OverflowError):
        return None


def checked_graph_url(url, account, *, collection=False):
    """Allowlist independiente de la firma del cursor, antes de enviar Bearer."""
    if not isinstance(url, str) or len(url) > 12000 or re.search(r"[\x00-\x20\x7f\\]", url):
        raise GraphError("invalid")
    try:
        parsed = urlsplit(url)
    except ValueError:
        raise GraphError("invalid") from None
    if parsed.scheme != "https" or parsed.netloc != "graph.microsoft.com" or parsed.fragment:
        raise GraphError("invalid")
    path = unquote(parsed.path)
    allowed = {"/v1.0/me/messages"}
    # Graph puede canonicalizar /me al ID de la misma cuenta en nextLink.
    identity = account.microsoft_account_id
    if re.fullmatch(r"[A-Za-z0-9_-]{1,255}", identity):
        allowed.update({f"/v1.0/users/{identity}/messages", f"/v1.0/users('{identity}')/messages"})
    if path in allowed:
        return url
    if not collection and re.fullmatch(r"/v1\.0/me/messages/[A-Za-z0-9_+=-]{1,2048}", path):
        return url
    raise GraphError("invalid")


class GraphClient:
    """Instancia local a una petición; ningún token vive en estado global."""
    def __init__(self, db, user, settings):
        self.db, self.user, self.settings = db, user, settings
        self.account = db.scalar(microsoft.owner_query(MicrosoftAccount, user).where(MicrosoftAccount.is_active.is_(True)))
        if self.account is None:
            raise GraphError("disconnected")
        self.connection_id = self._connection_id(self.account)

    @staticmethod
    def _connection_id(account):
        return [account.id, account.microsoft_account_id, account.connected_at.isoformat()]

    def access_token(self, force_refresh=False):
        try:
            # Mismo bloqueo que connect/disconnect: no perder un refresh ni
            # recrear una conexión que otro proceso acaba de eliminar.
            microsoft.lock_owner(self.db, self.user)
            account = self.db.scalar(microsoft.owner_query(MicrosoftAccount, self.user).where(
                MicrosoftAccount.is_active.is_(True),
            ).options(undefer(MicrosoftAccount.token_cache_encrypted)).execution_options(populate_existing=True))
            if account is None:
                raise GraphError("disconnected")
            if self._connection_id(account) != self.connection_id:
                raise GraphError("reconnect")
            try:
                raw = microsoft.decrypt(self.settings, self.user, "cache", account.token_cache_encrypted)
                cache = msal.SerializableTokenCache()
                cache.deserialize(raw)
            except Exception:
                raise GraphError("reconnect") from None
            sdk = microsoft.client(self.settings, cache)
            accounts = sdk.get_accounts()
            # El cache de 1A se crea vacío al conectar y pertenece a una única
            # identidad. No elegir get_accounts()[0] de un cache ambiguo.
            if len(accounts) != 1:
                raise GraphError("reconnect")
            candidate = accounts[0]
            # No comparar realm con tenant_id: MSAL puede guardar "common" o
            # "organizations" como realm. El cache completo ya está ligado al
            # propietario mediante el sobre cifrado y contiene una sola cuenta.
            result = sdk.acquire_token_silent_with_error(MAIL_SCOPES, account=candidate, force_refresh=force_refresh)
            if cache.has_state_changed:
                account.token_cache_encrypted = microsoft.encrypt(self.settings, self.user, "cache", cache.serialize())
            # Guardar también una rotación de refresh token antes de consultar Graph.
            self.db.commit()
            if not result or result.get("error"):
                if result and result.get("error") in {"temporarily_unavailable", "server_error"}:
                    raise GraphError("unavailable")
                raise GraphError("reconnect")
            token = result.get("access_token")
            if not isinstance(token, str) or not token:
                raise GraphError("reconnect")
            return token
        except GraphError as error:
            self.db.rollback()
            safe_log("token", error.status_code)
            raise
        except requests.Timeout:
            self.db.rollback()
            safe_log("token", "timeout")
            raise GraphError("timeout") from None
        except Exception:
            self.db.rollback()
            safe_log("token", "unavailable")
            raise GraphError("unavailable") from None

    def get(self, url, *, params=None, endpoint="messages"):
        if endpoint not in {"messages", "message"}:
            raise GraphError("invalid")
        checked_graph_url(url, self.account, collection=endpoint == "messages")
        token = self.access_token()
        # Un único reintento total: refresh tras 401 O backoff corto tras 429.
        for attempt in range(2):
            try:
                with requests.get(url, params=params,
                    headers={"Authorization": "Bearer " + token, "Prefer": 'outlook.body-content-type="text"'},
                    timeout=HTTP_TIMEOUT, allow_redirects=False,
                ) as response:
                    status = response.status_code
                    if status == 200:
                        payload = response.json()
                        if not isinstance(payload, dict):
                            raise GraphError("response")
                        return payload
                    safe_log(endpoint, status, response.headers.get("request-id"))
                    delay = retry_delay(response.headers.get("Retry-After")) if status == 429 else None
                # Cerrar primero la respuesta, especialmente antes del backoff.
                if status == 401 and attempt == 0:
                    token = self.access_token(force_refresh=True)
                    continue
                if status == 429 and attempt == 0 and delay is not None and delay <= MAX_RETRY_WAIT:
                    time.sleep(delay)
                    continue
                if status == 429:
                    raise GraphError("throttled", retry_after=delay if delay is not None else 30)
                if status in {401, 403, 404}:
                    raise GraphError({401: "reconnect", 403: "forbidden", 404: "missing"}[status])
                if 500 <= status <= 599:
                    raise GraphError("unavailable")
                raise GraphError("response")
            except GraphError:
                raise
            except requests.Timeout:
                safe_log(endpoint, "timeout")
                raise GraphError("timeout") from None
            except requests.RequestException:
                safe_log(endpoint, "transport")
                raise GraphError("unavailable") from None
            except (ValueError, TypeError):
                safe_log(endpoint, "invalid_response")
                raise GraphError("response") from None
        raise GraphError("unavailable")
