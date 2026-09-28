"""Lectura del buzón con Graph/MSAL simulados y rollback de PostgreSQL."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
import html
import json
import logging
import re
import secrets
from unittest.mock import MagicMock, patch
import unittest
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import uuid4

import msal
import requests
from sqlalchemy import event, select, update

from app import mail, microsoft
from app.graph import GRAPH_ROOT, retry_delay
from app.models import MicrosoftAccount, Organization, User
import test_microsoft


class MailTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test_microsoft.MicrosoftTests.setUpClass()

    def setUp(self):
        self.fixture = test_microsoft.MicrosoftTests()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.f = self.fixture
        self.db = self.f.connection
        self.browser = self.f.browser
        self.settings = self.f.settings
        self.tokens = [secrets.token_urlsafe(40) for _ in range(3)]
        self.refresh_tokens = [secrets.token_urlsafe(40) for _ in range(3)]
        self.payloads = [{"value": [self.message(i)]} for i in range(3)]
        self.silent_result = None
        self.rotate = False
        self.sdks = []
        self.f.constructor.side_effect = self.fake_sdk
        self.f.graph.side_effect = self.fake_get
        self.db.execute(update(User).where(User.id == self.f.owners[0].id).values(role="admin"))
        for index in range(3):
            self.add_account(index)

    def tearDown(self):
        self.fixture.tearDown()

    def message(self, index=0):
        return {"id": f"message-{index}=", "subject": f"Asunto privado {index}",
            "from": {"emailAddress": {"name": f"Remitente {index}", "address": f"sender{index}@example.invalid"}},
            "receivedDateTime": "2026-09-28T02:30:00Z", "sentDateTime": "2026-09-28T02:29:00Z",
            "isRead": False, "hasAttachments": True, "importance": "high", "bodyPreview": f"Preview confidencial {index}",
            "toRecipients": [{"emailAddress": {"address": "to@example.invalid"}}],
            "ccRecipients": [{"emailAddress": {"address": "cc@example.invalid"}}],
            "body": {"contentType": "text", "content": f"Cuerpo confidencial {index}"},
            "webLink": "https://outlook.office.com/mail/inbox/id/mock"}

    def cache_event(self, index, access=None, refresh=None):
        return {"client_id": self.settings.microsoft_client_id, "scope": microsoft.SCOPES,
            "grant_type": "authorization_code", "token_endpoint": "https://login.microsoftonline.com/mock-tenant/oauth2/v2.0/token",
            "response": {"access_token": access or self.tokens[index], "refresh_token": refresh or self.refresh_tokens[index],
                "expires_in": 3600, "id_token_claims": {"sub": f"remote-{index}", "oid": f"remote-{index}",
                    "tid": "mock-tenant", "preferred_username": f"mail{index}@example.invalid"}}}

    def add_account(self, index):
        owner = self.f.owners[index]
        cache = msal.SerializableTokenCache()
        cache.add(self.cache_event(index))
        self.db.execute(MicrosoftAccount.__table__.insert().values(
            organization_id=owner.organization_id, user_id=owner.id, microsoft_account_id=f"remote-{index}",
            principal_name=f"mail{index}@example.invalid", tenant_id="mock-tenant",
            token_cache_encrypted=microsoft.encrypt(self.settings, owner, "cache", cache.serialize()),
        ))

    def fake_sdk(self, *args, **kwargs):
        cache = kwargs["token_cache"]
        sdk = MagicMock()
        accounts = list(cache.search(msal.TokenCache.CredentialType.ACCOUNT))
        sdk.get_accounts.return_value = accounts
        def silent(scopes, account, force_refresh=False):
            self.assertEqual(scopes, ["Mail.Read"])
            index = int(account["local_account_id"].split("-")[-1])
            if self.rotate:
                cache.add(self.cache_event(index, refresh=self.new_refresh))
            return self.silent_result if self.silent_result is not None else {"access_token": self.tokens[index]}
        sdk.acquire_token_silent_with_error.side_effect = silent
        self.sdks.append(sdk)
        return sdk

    def response(self, status=200, payload=None, headers=None):
        response = MagicMock()
        response.__enter__.return_value = response
        response.status_code = status
        response.headers = headers or {}
        response.json.return_value = payload if payload is not None else {"error": {"message": self.tokens[0] + " internal content"}}
        return response

    def fake_get(self, url, **kwargs):
        token = kwargs["headers"]["Authorization"].removeprefix("Bearer ")
        index = self.tokens.index(token)
        payload = self.payloads[index] if urlsplit(url).path.endswith("/messages") else self.message(index)
        return self.response(payload=deepcopy(payload))

    def encrypted_cache(self, index=0):
        return self.db.scalar(select(MicrosoftAccount.__table__.c.token_cache_encrypted).where(MicrosoftAccount.user_id == self.f.owners[index].id))

    def next_link(self, filters=None, **overrides):
        params = {**mail.list_parameters(filters or {"state": "all", "importance": "all"}), "$skip": "20"}
        params.update(overrides)
        return GRAPH_ROOT + "/me/messages?" + urlencode(params)

    def next_local_url(self, response):
        match = re.search(r'href="([^"]+)" rel="next"', response.text)
        self.assertIsNotNone(match)
        return html.unescape(match.group(1))

    def test_unauthenticated_list_and_detail_redirect(self):
        self.browser.cookies.clear()
        for path in ("/mail", "/mail/message-0="):
            response = self.browser.get(path)
            self.assertEqual(response.status_code, 303)
            self.assertEqual(response.headers["location"], "/login")
        self.f.constructor.assert_not_called()
        self.f.graph.assert_not_called()

    def test_disconnected_and_inactive_account_do_not_call_microsoft(self):
        self.db.execute(update(MicrosoftAccount).where(MicrosoftAccount.user_id == self.f.owners[0].id).values(is_active=False))
        for path in ("/mail", "/mail/message-0="):
            response = self.browser.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertIn("Microsoft no conectado", response.text)
            self.assertIn('href="/integrations/microsoft"', response.text)
        self.f.constructor.assert_not_called()
        self.f.graph.assert_not_called()

    def test_list_has_limited_select_and_no_body(self):
        response = self.browser.get("/mail")
        self.assertEqual(response.status_code, 200)
        for value in ("Asunto privado 0", "Remitente 0", "Preview confidencial 0", "No leído", "Con adjuntos", "Importancia alta"):
            self.assertIn(value, response.text)
        self.assertNotIn("Cuerpo confidencial", response.text)
        args, kwargs = self.f.graph.call_args
        self.assertEqual(args, (GRAPH_ROOT + "/me/messages",))
        self.assertEqual(set(kwargs["params"]["$select"].split(",")), set(mail.LIST_FIELDS))
        self.assertNotIn("body", kwargs["params"]["$select"].split(","))
        self.assertEqual(kwargs["params"]["$orderby"], "receivedDateTime desc")
        self.assertEqual(kwargs["params"]["$top"], "20")
        self.assertEqual(kwargs["timeout"], (5, 15))
        self.assertFalse(kwargs["allow_redirects"])
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.headers["referrer-policy"], "no-referrer")

    def test_detail_fetches_only_one_message_as_text(self):
        response = self.browser.get("/mail/message-0%3D")
        self.assertEqual(response.status_code, 200)
        for value in ("Cuerpo confidencial 0", "to@example.invalid", "cc@example.invalid", "Abrir en Outlook", "Alta", "No leído"):
            self.assertIn(value, response.text)
        self.assertEqual(self.f.graph.call_count, 1)
        args, kwargs = self.f.graph.call_args
        self.assertEqual(args, (GRAPH_ROOT + "/me/messages/message-0%3D",))
        self.assertEqual(set(kwargs["params"]["$select"].split(",")), set(mail.DETAIL_FIELDS))
        self.assertEqual(kwargs["headers"]["Prefer"], 'outlook.body-content-type="text"')
        self.assertNotIn("attachments", kwargs["params"]["$select"].split(","))

    def test_users_organizations_and_admin_read_only_their_own_tokens(self):
        for index in range(3):
            self.f.sign_in(index)
            response = self.browser.get("/mail")
            self.assertEqual(response.status_code, 200)
            self.assertIn(f"Asunto privado {index}", response.text)
            for other in set(range(3)) - {index}:
                self.assertNotIn(f"Asunto privado {other}", response.text)
            self.assertTrue(self.f.graph.call_args.kwargs["headers"]["Authorization"] == "Bearer " + self.tokens[index])

    def test_admin_cannot_supply_owner_or_graph_parameters(self):
        for name in ("user_id", "organization_id", "microsoft_account_id", "url", "nextLink", "$filter", "$select", "q", "search"):
            for path in ("/mail", "/mail/message-0="):
                self.assertEqual(self.browser.get(path, params={name: "unexpected"}).status_code, 400)
        self.f.constructor.assert_not_called()
        self.f.graph.assert_not_called()

    def test_foreign_message_id_is_only_requested_in_own_mailbox(self):
        self.f.graph.side_effect = [self.response(404)]
        response = self.browser.get("/mail/message-1=")
        self.assertEqual(response.status_code, 404)
        self.assertTrue(self.f.graph.call_args.kwargs["headers"]["Authorization"] == "Bearer " + self.tokens[0])
        self.assertIn("/me/messages/", self.f.graph.call_args.args[0])
        self.assertNotIn("Cuerpo confidencial 1", response.text)

    def test_valid_enum_filters_and_orderby_compatibility(self):
        for state in ("all", "read", "unread"):
            for importance in ("all", "low", "normal", "high"):
                response = self.browser.get("/mail", params={"state": state, "importance": importance})
                self.assertEqual(response.status_code, 200)
                params = self.f.graph.call_args.kwargs["params"]
                if state != "all" or importance != "all":
                    self.assertTrue(params["$filter"].startswith("receivedDateTime ge "))
                if state != "all":
                    self.assertIn("isRead eq " + ("true" if state == "read" else "false"), params["$filter"])
                if importance != "all":
                    self.assertIn("importance eq '" + importance + "'", params["$filter"])
                self.assertNotIn("$search", params)

    def test_invalid_inputs_duplicates_and_path_injection(self):
        queries = [{"state": "unread or true"}, {"importance": "high' or true"}, {"state": ""},
                   [("state", "read"), ("state", "unread")], {"cursor": ""}, {"cursor": "a" * 18001}]
        for params in queries:
            self.assertEqual(self.browser.get("/mail", params=params).status_code, 400)
        for path in ("/mail/id%3F$expand=attachments", "/mail/id%25", "/mail/$value", "/mail/id/attachments"):
            self.assertIn(self.browser.get(path).status_code, (400, 404))
        self.f.graph.assert_not_called()

    def test_pagination_uses_original_nextlink_and_signed_encrypted_cursor(self):
        link = self.next_link(**{"$skip": "20", "$skiptoken": "opaque+/=value"})
        self.payloads[0]["@odata.nextLink"] = link
        first = self.browser.get("/mail")
        local = self.next_local_url(first)
        self.assertNotIn("graph.microsoft.com", local)
        self.assertNotIn("opaque", local)
        self.payloads[0] = {"value": []}
        second = self.browser.get(local)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(self.f.graph.call_args.args[0], link)
        self.assertIsNone(self.f.graph.call_args.kwargs["params"])
        self.assertIn("Volver al inicio", second.text)
        self.assertNotIn('rel="next"', second.text)

    def test_cursor_cannot_cross_user_organization_session_or_filters(self):
        self.payloads[0]["@odata.nextLink"] = self.next_link()
        local = self.next_local_url(self.browser.get("/mail"))
        count = self.f.graph.call_count
        original_csrf = self.f.csrf
        for index in (1, 2, 0):
            self.f.sign_in(index)
            self.assertEqual(self.browser.get(local).status_code, 400)
        self.f.sign_in(0, csrf=original_csrf)
        self.assertEqual(self.browser.get(local + "&state=read").status_code, 400)
        self.assertEqual(self.f.graph.call_count, count)

    def test_expired_tampered_and_plain_url_cursor_rejected(self):
        self.payloads[0]["@odata.nextLink"] = self.next_link()
        local = self.next_local_url(self.browser.get("/mail"))
        token = parse_qs(urlsplit(local).query)["cursor"][0]
        payload = microsoft.decrypt(self.settings, self.f.owner, "mail-page", token)
        payload["issued"] -= mail.CURSOR_TTL + 10
        expired = microsoft.encrypt(self.settings, self.f.owner, "mail-page", payload)
        local = "/mail?" + urlencode({"cursor": expired})
        self.assertEqual(self.browser.get(local).status_code, 400)
        for value in ("https://graph.microsoft.com/v1.0/me/messages", "https://evil.invalid", "tampered"):
            self.assertEqual(self.browser.get("/mail", params={"cursor": value}).status_code, 400)
        self.assertEqual(self.f.graph.call_count, 1)

    def test_external_and_unsafe_nextlinks_never_requested(self):
        urls = ["https://evil.invalid/messages", "http://graph.microsoft.com/v1.0/me/messages",
            "https://graph.microsoft.com.evil.invalid/v1.0/me/messages", "https://graph.microsoft.com@evil.invalid/v1.0/me/messages",
            "https://graph.microsoft.com:444/v1.0/me/messages", "https://graph.microsoft.com/v1.0/me/messages#fragment",
            "https://graph.microsoft.com/v1.0/me/drive", "https://graph.microsoft.com/v1.0/users/remote-1/messages",
            "https://graph.microsoft.com/v1.0/me/messages/../drive", "https://graph.microsoft.com/v1.0/me/messages\r\n",
            self.next_link(**{"$select": "id,body"}), self.next_link(**{"$expand": "attachments"})]
        for url in urls:
            self.payloads[0]["@odata.nextLink"] = url
            self.assertIn(self.browser.get("/mail").status_code, (400, 502))
        self.assertEqual(self.f.graph.call_count, len(urls))
        self.assertTrue(all(c.args[0] == GRAPH_ROOT + "/me/messages" for c in self.f.graph.call_args_list))

    def test_nextlink_canonical_own_user_path_supported(self):
        self.payloads[0]["@odata.nextLink"] = self.next_link().replace("/me/messages", "/users/remote-0/messages")
        local = self.next_local_url(self.browser.get("/mail"))
        self.f.graph.side_effect = [self.response(payload={"value": []})]
        self.assertEqual(self.browser.get(local).status_code, 200)

    def test_401_refresh_once_then_friendly_reconnect(self):
        self.f.graph.side_effect = [self.response(401), self.response(401)]
        response = self.browser.get("/mail")
        self.assertEqual(response.status_code, 401)
        self.assertIn("Revisar conexión Microsoft", response.text)
        self.assertEqual(self.f.graph.call_count, 2)
        self.assertEqual([s.acquire_token_silent_with_error.call_args.kwargs["force_refresh"] for s in self.sdks], [False, True])

    def test_401_can_recover_with_silent_refresh(self):
        self.f.graph.side_effect = [self.response(401), self.response(payload=self.payloads[0])]
        self.assertEqual(self.browser.get("/mail").status_code, 200)
        self.assertEqual(self.f.graph.call_count, 2)

    def test_403_404_5xx_and_redirect_are_safe(self):
        for remote, expected in ((403, 403), (404, 404), (500, 503), (502, 503), (503, 503), (302, 502)):
            self.f.graph.side_effect = [self.response(remote, headers={"Location": "https://evil.invalid"})]
            response = self.browser.get("/mail/message-0=")
            self.assertEqual(response.status_code, expected)
            self.assertNotIn(self.tokens[0], response.text)
            self.assertNotIn("internal content", response.text)

    def test_429_respects_short_retry_after_and_bounds_attempts(self):
        self.f.graph.side_effect = [self.response(429, headers={"Retry-After": "2"}), self.response(429, headers={"Retry-After": "20"})]
        with patch("app.graph.time.sleep") as sleep:
            response = self.browser.get("/mail")
        sleep.assert_called_once_with(2)
        self.assertEqual(self.f.graph.call_count, 2)
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers["retry-after"], "20")
        self.assertIn("20 segundos", response.text)

    def test_429_long_missing_or_invalid_delay_does_not_wait_or_retry(self):
        for value in ("120", "invalid", "-1", None):
            self.f.graph.side_effect = [self.response(429, headers={"Retry-After": value})]
            with patch("app.graph.time.sleep") as sleep:
                response = self.browser.get("/mail")
            sleep.assert_not_called()
            self.assertEqual(response.status_code, 429)
            self.assertEqual(response.headers["retry-after"], "120" if value == "120" else "30")

    def test_429_retry_can_succeed(self):
        self.f.graph.side_effect = [self.response(429, headers={"Retry-After": "1"}), self.response(payload=self.payloads[0])]
        with patch("app.graph.time.sleep") as sleep:
            self.assertEqual(self.browser.get("/mail").status_code, 200)
        sleep.assert_called_once_with(1)

    def test_retry_after_supports_http_date(self):
        value = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=90), usegmt=True)
        self.assertTrue(88 <= retry_delay(value) <= 91)

    def test_timeout_and_transport_failure_are_safe(self):
        for error, status in ((requests.Timeout(self.tokens[0]), 504), (requests.ConnectionError(self.tokens[0]), 503)):
            self.f.graph.side_effect = error
            response = self.browser.get("/mail")
            self.assertEqual(response.status_code, status)
            self.assertIn("Volver a intentar", response.text)
            self.assertNotIn(self.tokens[0], response.text)

    def test_html_is_converted_to_escaped_text_with_no_remote_resources(self):
        raw = self.message()
        raw["subject"] = '<img src=x onerror="alert(1)">'
        raw["body"] = {"contentType": "html", "content": '''<h1>Hola</h1><p>Texto útil &amp; seguro</p>
            <script>stolenToken()</script><style>body{background:url(https://tracking.invalid)}</style>
            <iframe src="https://tracking.invalid">oculto</iframe><form><input value="hidden-secret"></form>
            <object data="https://tracking.invalid">oculto</object><svg onload="evil()"></svg>
            <p onclick="evil()">Visible</p><img src="https://tracking.invalid/pixel" onerror="evil()">
            <a href="javascript:evil()">Enlace como texto</a><embed src="https://tracking.invalid">
            &lt;script&gt;literal&lt;/script&gt;'''}
        self.f.graph.side_effect = [self.response(payload=raw)]
        response = self.browser.get("/mail/message-0=")
        self.assertEqual(response.status_code, 200)
        body = response.text.split('<div class="mail-body">', 1)[1].split("</div>", 1)[0]
        for blocked in ("<script", "<img", "onclick", "onerror", "javascript:", "tracking.invalid", "stolenToken", "hidden-secret", "<iframe", "<form", "<object", "<svg"):
            self.assertNotIn(blocked, body)
        self.assertIn("Texto útil &amp; seguro", body)
        self.assertIn("&lt;script&gt;literal&lt;/script&gt;", body)
        self.assertIn("img-src 'self'", response.headers["content-security-policy"])

    def test_plaintext_is_never_marked_safe_and_outlook_links_are_allowlisted(self):
        for link in ("javascript:alert(1)", "https://outlook.office.com.evil.invalid/", "https://outlook.office.com@evil.invalid", "http://outlook.live.com/"):
            raw = self.message()
            raw.update(webLink=link, body={"contentType": "text", "content": "<script>alert(1)</script>"})
            self.f.graph.side_effect = [self.response(payload=raw)]
            response = self.browser.get("/mail/message-0=")
            self.assertNotIn("Abrir en Outlook", response.text)
            self.assertNotIn("<script>", response.text)
            self.assertIn("&lt;script&gt;", response.text)

    def test_organization_timezone_used_for_list_and_detail(self):
        self.db.execute(update(Organization).where(Organization.id == self.f.owner.organization_id).values(timezone="America/Guatemala"))
        for path in ("/mail", "/mail/message-0="):
            response = self.browser.get(path)
            self.assertIn("27/09/2026 20:30", response.text)
            self.assertIn("America/Guatemala", response.text)
        self.db.execute(update(Organization).where(Organization.id == self.f.owner.organization_id).values(timezone="Asia/Tokyo"))
        self.assertIn("28/09/2026 11:30", self.browser.get("/mail").text)

    def test_naive_dates_do_not_use_server_timezone(self):
        self.payloads[0]["value"][0]["receivedDateTime"] = "2026-09-28T02:30:00"
        self.assertIn("Fecha no disponible", self.browser.get("/mail").text)

    def test_no_message_content_persisted_even_when_token_rotates(self):
        self.rotate = True
        self.new_refresh = secrets.token_urlsafe(40)
        before_other = self.encrypted_cache(1)
        statements = []
        def capture(connection, cursor, statement, parameters, context, executemany):
            statements.append((statement, parameters))
        event.listen(self.db, "before_cursor_execute", capture)
        try:
            self.assertEqual(self.browser.get("/mail").status_code, 200)
            self.assertEqual(self.browser.get("/mail/message-0=").status_code, 200)
        finally:
            event.remove(self.db, "before_cursor_execute", capture)
        sql = repr(statements)
        for value in ("Asunto privado", "Preview confidencial", "Cuerpo confidencial", "sender0@example.invalid", "to@example.invalid", self.tokens[0], self.new_refresh):
            self.assertNotIn(value, sql)
        self.assertEqual(self.encrypted_cache(1), before_other)
        raw = microsoft.decrypt(self.settings, self.f.owner, "cache", self.encrypted_cache())
        self.assertIn(self.new_refresh, raw)
        self.assertNotIn("Cuerpo confidencial", raw)
        writes = [s.lower() for s, _ in statements if s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))]
        self.assertTrue(writes)
        self.assertTrue(all(s.startswith("update microsoft_accounts set ") and "token_cache_encrypted=" in s for s in writes))

    def test_unchanged_cache_does_not_write(self):
        before = self.encrypted_cache()
        self.assertEqual(self.browser.get("/mail").status_code, 200)
        self.assertEqual(self.encrypted_cache(), before)

    def test_real_msal_common_cache_hit_and_expired_token_refresh_without_network(self):
        from msal.application import ConfidentialClientApplication

        for expired in (False, True):
            cache = msal.SerializableTokenCache()
            cache_event = self.cache_event(0)
            cache_event["token_endpoint"] = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
            cache_event["response"]["expires_in"] = -3600 if expired else 3600
            cache.add(cache_event)
            self.db.execute(update(MicrosoftAccount).where(MicrosoftAccount.user_id == self.f.owner.id).values(
                token_cache_encrypted=microsoft.encrypt(self.settings, self.f.owner, "cache", cache.serialize())))
            refreshed = secrets.token_urlsafe(40)
            http = MagicMock()
            http.get.return_value.status_code = 200
            http.get.return_value.text = json.dumps({
                "authorization_endpoint": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
                "token_endpoint": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
                "issuer": "https://login.microsoftonline.com/{tenantid}/v2.0",
            })
            http.post.return_value.status_code = 200
            http.post.return_value.text = json.dumps({"access_token": self.tokens[0], "refresh_token": refreshed,
                "scope": "Mail.Read", "token_type": "Bearer", "expires_in": 3600})
            def factory(*args, **kwargs):
                return ConfidentialClientApplication(*args, **kwargs, http_client=http, instance_discovery=False)
            self.f.constructor.side_effect = factory
            self.assertEqual(self.browser.get("/mail").status_code, 200)
            if expired:
                http.post.assert_called_once()
                raw = microsoft.decrypt(self.settings, self.f.owner, "cache", self.encrypted_cache())
                self.assertTrue(refreshed in raw)
            else:
                http.post.assert_not_called()

    def test_disconnect_during_session_prevents_further_graph_calls(self):
        self.payloads[0]["@odata.nextLink"] = self.next_link()
        local = self.next_local_url(self.browser.get("/mail"))
        self.assertEqual(self.browser.post("/integrations/microsoft/disconnect", data={"csrf": self.f.csrf}).status_code, 303)
        count = self.f.graph.call_count
        for path in ("/mail", "/mail/message-0=", local):
            self.assertIn("Microsoft no conectado", self.browser.get(path).text)
        self.assertEqual(self.f.graph.call_count, count)
        self.assertIsNone(self.encrypted_cache())

    def test_reconnect_invalidates_old_cursor(self):
        self.payloads[0]["@odata.nextLink"] = self.next_link()
        local = self.next_local_url(self.browser.get("/mail"))
        self.db.execute(update(MicrosoftAccount).where(MicrosoftAccount.user_id == self.f.owner.id).values(connected_at=datetime.now(timezone.utc) + timedelta(seconds=1)))
        self.assertEqual(self.browser.get(local).status_code, 400)
        self.assertEqual(self.f.graph.call_count, 1)

    def test_invalid_or_undecryptable_cache_requires_reconnect_without_secret_leak(self):
        for value in ("not-encrypted", microsoft.encrypt(self.settings, self.f.owner, "cache", "invalid-json"),
                      microsoft.encrypt(self.settings, self.f.owners[1], "cache", "{}"),
                      microsoft.encrypt(self.settings, self.f.owner, "cache", "{}")):
            self.db.execute(update(MicrosoftAccount).where(MicrosoftAccount.user_id == self.f.owner.id).values(token_cache_encrypted=value))
            response = self.browser.get("/mail")
            self.assertEqual(response.status_code, 401)
            self.assertIn("Revisar conexión Microsoft", response.text)
            self.assertNotIn(value, response.text)
        self.f.graph.assert_not_called()

    def test_silent_auth_failure_and_temporary_failure_do_not_call_graph(self):
        for result, status in (({"error": "invalid_grant", "error_description": self.refresh_tokens[0]}, 401),
                               ({"error": "temporarily_unavailable", "error_description": self.tokens[0]}, 503), ({}, 401)):
            self.silent_result = result
            response = self.browser.get("/mail")
            self.assertEqual(response.status_code, status)
            self.assertNotIn(self.refresh_tokens[0], response.text)
            self.assertNotIn(self.tokens[0], response.text)
        self.f.graph.assert_not_called()

    def test_malformed_graph_response_does_not_render_raw_json(self):
        for payload in ({"value": "invalid"}, {"value": [{}]}, {"value": [self.message()] * 21}):
            self.f.graph.side_effect = [self.response(payload=payload)]
            self.assertIn(self.browser.get("/mail").status_code, (400, 502))

    def test_logs_contain_only_safe_technical_metadata(self):
        request_id = str(uuid4())
        self.f.graph.side_effect = [self.response(503, headers={"request-id": request_id})]
        with self.assertLogs("atenea.graph", level="WARNING") as captured:
            response = self.browser.get("/mail")
        self.assertEqual(response.status_code, 503)
        log = "\n".join(captured.output)
        self.assertIn("endpoint=messages status=503", log)
        self.assertIn(request_id, log)
        for secret in (*self.tokens, *self.refresh_tokens, self.settings.microsoft_client_secret,
                       self.settings.token_encryption_key, "Cuerpo confidencial", "Authorization", "Asunto privado"):
            self.assertNotIn(secret, log + response.text)
        self.f.graph.side_effect = [self.response(503, headers={"request-id": self.tokens[0]})]
        with self.assertLogs("atenea.graph", level="WARNING") as captured:
            self.browser.get("/mail")
        self.assertNotIn(self.tokens[0], "".join(captured.output))
        for path in ("/mail", "/mail?cursor=opaque", "/mail/message-id"):
            record = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d', ("test", "GET", path, "1.1", 200), None)
            self.assertFalse(microsoft.CallbackAccessFilter().filter(record))

    def test_no_write_routes_or_additional_scopes(self):
        for method in ("post", "put", "patch", "delete"):
            for path in ("/mail", "/mail/message-0="):
                self.assertEqual(getattr(self.browser, method)(path).status_code, 405)
        self.assertEqual(microsoft.SCOPES, ["User.Read", "Mail.Read"])
        self.f.graph.assert_not_called()
