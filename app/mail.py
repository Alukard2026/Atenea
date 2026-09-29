"""Consultas y presentación efímera del buzón. Sin modelos ni cache de mensajes."""

from datetime import datetime
from html.parser import HTMLParser
import re
import time
from urllib.parse import parse_qs, quote, urlencode, urlsplit

from app import microsoft, mail_rules
from app.graph import GRAPH_ROOT, GraphError, checked_graph_url


LIST_FIELDS = ("id", "subject", "from", "receivedDateTime", "isRead", "hasAttachments", "importance", "bodyPreview", "webLink")
DETAIL_FIELDS = ("id", "subject", "from", "toRecipients", "ccRecipients", "receivedDateTime", "sentDateTime", "importance", "isRead", "hasAttachments", "body", "bodyPreview", "webLink")
PAGE_SIZE = 20
CURSOR_TTL = 15 * 60
MAX_CURSOR_LENGTH = 18000
IMPORTANCE = {"low": "Baja", "normal": "Normal", "high": "Alta"}


def filters_from_query(query):
    allowed = {"state", "importance", "cursor"}
    if set(query) - allowed or any(len(query.getlist(key)) != 1 for key in query):
        raise GraphError("invalid")
    filters = {"state": query.get("state", "all"), "importance": query.get("importance", "all")}
    if filters["state"] not in {"all", "unread", "read"} or filters["importance"] not in {"all", *IMPORTANCE}:
        raise GraphError("invalid")
    if "cursor" in query and not 0 < len(query["cursor"]) <= MAX_CURSOR_LENGTH:
        raise GraphError("cursor")
    return filters


def list_parameters(filters):
    params = {"$select": ",".join(LIST_FIELDS), "$top": str(PAGE_SIZE), "$orderby": "receivedDateTime desc"}
    clauses = []
    if filters["state"] != "all":
        clauses.append({"read": "isRead eq true", "unread": "isRead eq false"}[filters["state"]])
    if filters["importance"] != "all":
        # Solo literales de un enum validado, nunca texto arbitrario.
        clauses.append({key: "importance eq '" + key + "'" for key in IMPORTANCE}[filters["importance"]])
    if clauses:
        # Graph exige primero el campo de orderby en filter (InefficientFilter).
        params["$filter"] = "receivedDateTime ge 0001-01-01T00:00:00Z and " + " and ".join(clauses)
    return params


def validate_next_link(url, client, filters):
    checked_graph_url(url, client.account, collection=True)
    try:
        query = parse_qs(urlsplit(url).query, keep_blank_values=True, max_num_fields=12)
        expected = list_parameters(filters)
        if set(query) - {*expected, "$skip", "$skiptoken"} or any(len(value) != 1 for value in query.values()):
            raise ValueError()
        if any(query.get(key) != [value] for key, value in expected.items()):
            raise ValueError()
        if not ({"$skip", "$skiptoken"} & set(query)):
            raise ValueError()
    except (ValueError, TypeError):
        raise GraphError("cursor") from None
    return url  # Usar la URL original exacta; no reconstruir skip/skiptoken.


def page_cursor(client, url, filters, session_token):
    validate_next_link(url, client, filters)
    token = microsoft.encrypt(client.settings, client.user, "mail-page", {
        "url": url, "filters": filters, "connection": client.connection_id,
        "session": microsoft.digest(session_token), "issued": int(time.time()),
    })
    if len(token) > MAX_CURSOR_LENGTH:
        raise GraphError("cursor")
    return token


def read_cursor(client, token, filters, session_token):
    try:
        if not 0 < len(token) <= MAX_CURSOR_LENGTH:
            raise ValueError()
        payload = microsoft.decrypt(client.settings, client.user, "mail-page", token)
        if (payload["connection"] != client.connection_id or payload["filters"] != filters
            or payload["session"] != microsoft.digest(session_token)
            or not 0 <= time.time() - payload["issued"] <= CURSOR_TTL):
            raise ValueError()
        return validate_next_link(payload["url"], client, filters)
    except Exception:
        raise GraphError("cursor") from None


def list_url(filters, cursor=None):
    params = {key: value for key, value in filters.items() if value != "all"}
    if cursor:
        params["cursor"] = cursor
    return "/mail" + ("?" + urlencode(params) if params else "")


def message_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_+=-]{1,2048}", value):
        raise GraphError("invalid")
    return value


def local_date(value, zone):
    if not isinstance(value, str):
        return "Fecha no disponible"
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            return "Fecha no disponible"
        return dt.astimezone(zone).strftime("%d/%m/%Y %H:%M")
    except (ValueError, OverflowError):
        return "Fecha no disponible"


def text(value, limit=10000):
    return value[:limit] if isinstance(value, str) else ""


def recipient(value):
    address = value.get("emailAddress", {}) if isinstance(value, dict) else {}
    if not isinstance(address, dict):
        return "Remitente no disponible"
    name, email = text(address.get("name"), 320), text(address.get("address"), 320)
    return f"{name} <{email}>" if name and email else name or email or "Remitente no disponible"


def outlook_link(value):
    if not isinstance(value, str) or len(value) > 8192 or re.search(r"[\x00-\x20\x7f\\]", value):
        return None
    try:
        url = urlsplit(value)
        if url.scheme == "https" and url.netloc in {"outlook.office.com", "outlook.office365.com", "outlook.live.com"}:
            return value
    except ValueError:
        pass
    return None


class BodyText(HTMLParser):
    """Extractor de texto, NO generador de HTML. La salida siempre se escapa."""
    blocked = {"script", "style", "head", "iframe", "object", "embed", "form", "svg", "math", "template", "noscript"}
    breaks = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "blockquote", "hr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hidden = []
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in self.blocked and tag != "embed":
            self.hidden.append(tag)
        if not self.hidden and tag in self.breaks:
            self.parts.append("\n")

    def handle_startendtag(self, tag, attrs):
        if not self.hidden and tag in self.breaks:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if self.hidden:
            if tag == self.hidden[-1]:
                self.hidden.pop()
        elif tag in self.breaks:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def body_text(body):
    if not isinstance(body, dict):
        return ""
    content = text(body.get("content"), 1_000_000)
    if text(body.get("contentType"), 20).lower() == "text":
        return content
    extractor = BodyText()
    extractor.feed(content)
    extractor.close()
    return re.sub(r"\n[ \t]*\n(?:[ \t]*\n)+", "\n\n", "".join(extractor.parts)).strip()


def present_message(raw, zone, detail=False):
    if not isinstance(raw, dict):
        raise GraphError("response")
    identity = message_id(raw.get("id"))
    importance = raw.get("importance", "normal")
    if not isinstance(importance, str):
        raise GraphError("response")
    result = {
        "rule_input": mail_rules.message_input(raw),
        "href": "/mail/" + quote(identity, safe=""),
        "subject": text(raw.get("subject"), 1000) or "(Sin asunto)",
        "sender": recipient(raw.get("from")),
        "received": local_date(raw.get("receivedDateTime"), zone),
        "is_read": raw.get("isRead") is True,
        "has_attachments": raw.get("hasAttachments") is True,
        "importance": importance if importance in IMPORTANCE else "normal",
        "importance_label": IMPORTANCE.get(importance, "Normal"),
        "preview": text(raw.get("bodyPreview"), 240),
        "outlook_url": outlook_link(raw.get("webLink")),
    }
    if detail:
        recipients, cc = raw.get("toRecipients") or [], raw.get("ccRecipients") or []
        if not isinstance(recipients, list) or not isinstance(cc, list):
            raise GraphError("response")
        result.update(
            recipients=[recipient(v) for v in recipients],
            cc=[recipient(v) for v in cc],
            sent=local_date(raw.get("sentDateTime"), zone), body=body_text(raw.get("body")),
            body_truncated=isinstance(raw.get("body"), dict) and isinstance(raw["body"].get("content"), str)
                           and len(raw["body"]["content"]) > 1_000_000,
        )
    return result


def list_messages(client, filters, cursor, session_token, zone):
    url = read_cursor(client, cursor, filters, session_token) if cursor else GRAPH_ROOT + "/me/messages"
    payload = client.get(url, params=None if cursor else list_parameters(filters))
    rows = payload.get("value")
    if not isinstance(rows, list) or len(rows) > PAGE_SIZE:
        raise GraphError("response")
    messages = [present_message(row, zone) for row in rows]
    next_url = None
    if payload.get("@odata.nextLink"):
        next_url = list_url(filters, page_cursor(client, payload["@odata.nextLink"], filters, session_token))
    return messages, next_url


def get_message(client, identity, zone):
    identity = message_id(identity)
    payload = client.get(GRAPH_ROOT + "/me/messages/" + quote(identity, safe=""),
                         params={"$select": ",".join(DETAIL_FIELDS)}, endpoint="message")
    if payload.get("id") != identity:
        raise GraphError("response")
    return present_message(payload, zone, detail=True)
