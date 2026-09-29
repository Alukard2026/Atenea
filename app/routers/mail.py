"""Buzón de solo lectura, exclusivamente de la persona autenticada."""

from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app import ai_provider, mail, mail_rules
from app.graph import GraphClient, GraphError
from app.models import User
from app.routers.auth import templates
from app.timezones import organization_zone
from app.web_auth import DatabaseSession, csrf_token, get_current_user


router = APIRouter(prefix="/mail")
CurrentUser = Annotated[User, Depends(get_current_user)]


def error_page(request, user, error, retry_url="/mail"):
    headers = {"Retry-After": str(error.retry_after)} if error.retry_after is not None else None
    return templates.TemplateResponse(request=request, name="mail_error.html", status_code=error.status_code,
        headers=headers, context={"user": user, "csrf_token": csrf_token(request), "error": error,
                                  "retry_url": retry_url})


@router.get("", response_class=HTMLResponse)
def inbox(request: Request, user: CurrentUser, db: DatabaseSession):
    retry_url = "/mail"
    try:
        filters = mail.filters_from_query(request.query_params)
        cursor = request.query_params.get("cursor")
        retry_url = mail.list_url(filters, cursor)
        zone = organization_zone(user.organization, request.app.state.task_zone)
        client = GraphClient(db, user, request.app.state.settings)
        messages, next_url = mail.list_messages(client, filters, cursor, csrf_token(request), zone)
        mail_rules.analyze_messages(db, user, messages)
        return templates.TemplateResponse(request=request, name="mail_list.html", context={
            "user": user, "csrf_token": csrf_token(request), "messages": messages, "filters": filters,
            "next_url": next_url, "first_url": mail.list_url(filters), "refresh_url": retry_url,
            "has_cursor": bool(cursor), "zone": zone.key,
        })
    except GraphError as error:
        return error_page(request, user, error, retry_url)


@router.get("/{message_id}", response_class=HTMLResponse)
def detail(request: Request, message_id: str, user: CurrentUser, db: DatabaseSession):
    retry_url = "/mail"
    try:
        if request.query_params:
            raise GraphError("invalid")
        mail.message_id(message_id)
        zone = organization_zone(user.organization, request.app.state.task_zone)
        client = GraphClient(db, user, request.app.state.settings)
        message = mail.get_message(client, message_id, zone)
        mail_rules.analyze_messages(db, user, [message])
        retry_url = message["href"]
        return templates.TemplateResponse(request=request, name="mail_detail.html", context={
            "user": user, "csrf_token": csrf_token(request), "message": message, "zone": zone.key,
            "ai": ai_provider.availability(request.app.state.settings),
        })
    except GraphError as error:
        if error.kind not in {"invalid", "cursor"}:
            retry_url = "/mail/" + quote(message_id, safe="")
        return error_page(request, user, error, retry_url)
