"""Microsoft completamente simulado; PostgreSQL real con rollback por prueba."""

import base64
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import io
import json
import logging
import secrets
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner
import msal
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import microsoft
from app.config import Settings
from app.database import engine, get_db
from app.main import create_app
from app.models import MicrosoftAccount, MicrosoftOAuthFlow, Organization, User


class MicrosoftTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.settings = Settings(
            session_secret=secrets.token_urlsafe(32), microsoft_client_id=str(uuid4()),
            microsoft_client_secret=secrets.token_urlsafe(32), microsoft_tenant="common",
            microsoft_redirect_uri="http://localhost/integrations/microsoft/callback",
            token_encryption_key=Fernet.generate_key().decode(),
        )
        cls.app = create_app(cls.settings)

    def setUp(self):
        self.connection = engine.connect()
        self.addCleanup(self.connection.close)
        self.transaction = self.connection.begin()
        self.addCleanup(lambda: self.transaction.rollback() if self.transaction.is_active else None)
        self.models = (User, Organization, MicrosoftAccount, MicrosoftOAuthFlow)
        self.baseline = self.counts()
        with Session(bind=self.connection, join_transaction_mode="create_savepoint") as db, db.begin():
            a, b = Organization(name=uuid4().hex), Organization(name=uuid4().hex)
            users = [User(organization=o, email=uuid4().hex + "@example.invalid", full_name="Test", hashed_password="unused") for o in (a, a, b)]
            db.add_all(users)
            db.flush()
            self.owners = [SimpleNamespace(id=u.id, organization_id=u.organization_id) for u in users]

        def test_db():
            with Session(bind=self.connection, join_transaction_mode="create_savepoint") as db:
                yield db
        self.app.dependency_overrides[get_db] = test_db
        self.addCleanup(self.app.dependency_overrides.clear)
        self.app.state.settings = self.settings
        self.addCleanup(setattr, self.app.state, "settings", self.settings)
        self.browser = TestClient(self.app, base_url="http://localhost", follow_redirects=False)
        self.browser.__enter__()
        self.addCleanup(self.browser.__exit__, None, None, None)
        self.access = secrets.token_urlsafe(40)
        self.refresh = secrets.token_urlsafe(40)
        self.code = secrets.token_urlsafe(30)
        self.verifier = secrets.token_urlsafe(40)
        self.nonce = secrets.token_urlsafe(30)
        self.sdk = MagicMock()
        self.acquire_override = None
        self.tenant = "mock-tenant"
        self.constructor = patch("app.microsoft.msal.ConfidentialClientApplication", side_effect=self.fake_client).start()
        self.addCleanup(patch.stopall)
        # Bloquear cualquier transporte real, aunque una prueba olvide un mock.
        self.network = patch("requests.sessions.Session.send", side_effect=AssertionError("Red Microsoft prohibida en pruebas")).start()
        self.graph = patch("app.microsoft.requests.get").start()
        self.graph_response = self.graph.return_value.__enter__.return_value
        self.graph_response.status_code = 200
        self.graph_response.json.return_value = {"id": "mock-account", "mail": "outlook@example.invalid", "userPrincipalName": "principal@example.invalid", "displayName": "<b>Microsoft</b>"}
        self.sign_in(0)

    def counts(self):
        return tuple(self.connection.scalar(select(func.count()).select_from(m)) for m in self.models)

    def tearDown(self):
        self.transaction.rollback()
        self.assertEqual(self.counts(), self.baseline)

    def sign_in(self, index, csrf=None):
        self.owner = self.owners[index]
        self.csrf = csrf or secrets.token_urlsafe(32)
        payload = {"user_id": self.owner.id, "organization_id": self.owner.organization_id, "csrf_token": self.csrf}
        cookie = TimestampSigner(self.settings.session_secret).sign(base64.b64encode(json.dumps(payload).encode())).decode()
        self.browser.cookies.clear()
        self.browser.cookies.set("atenea_session", cookie)

    def fake_client(self, *args, **kwargs):
        cache = kwargs["token_cache"]
        def initiate(**options):
            self.flow = {"state": options["state"], "code_verifier": self.verifier, "nonce": self.nonce,
                         "auth_uri": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize?state=" + options["state"]}
            return dict(self.flow)
        def acquire(flow, response, scopes):
            self.assertEqual(flow, self.flow)
            self.assertEqual(response["state"], flow["state"])
            self.assertEqual(scopes, microsoft.SCOPES)
            cache.add({"client_id": self.settings.microsoft_client_id, "scope": scopes,
                "token_endpoint": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
                "response": {"access_token": self.access, "refresh_token": self.refresh, "token_type": "Bearer", "expires_in": 3600}})
            return {"access_token": self.access, "id_token_claims": {"tid": self.tenant}}
        self.sdk.initiate_auth_code_flow.side_effect = initiate
        self.sdk.acquire_token_by_auth_code_flow.side_effect = self.acquire_override or acquire
        return self.sdk

    def begin(self):
        return self.browser.post("/integrations/microsoft/connect", data={"csrf": self.csrf})

    def callback(self, **overrides):
        params = {"state": self.flow["state"], "code": self.code}
        params.update(overrides)
        return self.browser.get("/integrations/microsoft/callback", params=params)

    def account(self, owner=None):
        owner = owner or self.owner
        return self.connection.execute(select(MicrosoftAccount.__table__).where(
            MicrosoftAccount.user_id == owner.id, MicrosoftAccount.organization_id == owner.organization_id,
        )).mappings().one_or_none()

    def pending(self):
        return self.connection.scalar(select(func.count()).select_from(MicrosoftOAuthFlow))

    def connect(self):
        self.assertEqual(self.begin().status_code, 303)
        self.assertEqual(self.callback().headers["location"], "/integrations/microsoft")

    def test_disconnected_and_authenticated_routes(self):
        page = self.browser.get("/integrations/microsoft")
        self.assertIn("No conectado", page.text)
        self.assertIn("Conectar Microsoft", page.text)
        self.assertIn("form-action 'self' https://login.microsoftonline.com;", page.headers["content-security-policy"])
        self.assertNotIn("login.microsoftonline.com", self.browser.get("/login").headers["content-security-policy"])
        self.constructor.assert_not_called()
        self.graph.assert_not_called()
        self.browser.cookies.clear()
        for path, method in (("", "get"), ("/connect", "post"), ("/callback", "get"), ("/disconnect", "post")):
            self.assertEqual(getattr(self.browser, method)("/integrations/microsoft" + path).headers["location"], "/login")

    def test_start_requires_csrf_and_stores_encrypted_flow(self):
        self.assertEqual(self.browser.get("/integrations/microsoft/connect").status_code, 405)
        self.assertEqual(self.browser.post("/integrations/microsoft/connect").status_code, 403)
        response = self.begin()
        self.assertEqual(response.status_code, 303)
        self.assertIn("form-action 'self' https://login.microsoftonline.com;", response.headers["content-security-policy"])
        kwargs = self.sdk.initiate_auth_code_flow.call_args.kwargs
        self.assertEqual(kwargs["scopes"], ["User.Read", "Mail.Read"])
        self.assertEqual(kwargs["response_mode"], "query")
        self.assertEqual(kwargs["redirect_uri"], self.settings.microsoft_redirect_uri)
        row = self.connection.execute(select(MicrosoftOAuthFlow.__table__)).mappings().one()
        self.assertEqual(row["state_hash"], microsoft.digest(self.flow["state"]))
        self.assertEqual(microsoft.decrypt(self.settings, self.owner, "flow", row["flow_encrypted"])["flow"], self.flow)
        self.assertNotIn(self.verifier, str(row))
        self.assertNotIn(self.nonce, str(row))
        cookie = self.browser.cookies.get("atenea_session")
        session = json.loads(base64.b64decode(TimestampSigner(self.settings.session_secret).unsign(cookie)))
        self.assertEqual(set(session), {"user_id", "organization_id", "csrf_token"})

    def test_invalid_missing_duplicate_state_never_exchanges_code(self):
        self.begin()
        for state in ("", "wrong", "x" * 513):
            self.assertIn("result=error", self.callback(state=state).headers["location"])
        response = self.browser.get("/integrations/microsoft/callback", params=[("state", self.flow["state"]), ("state", self.flow["state"]), ("code", self.code)])
        self.assertIn("result=error", response.headers["location"])
        self.sdk.acquire_token_by_auth_code_flow.assert_not_called()
        self.graph.assert_not_called()
        self.assertIsNone(self.account())

    def test_callback_identity_encrypted_cache_and_no_secrets(self):
        self.connect()
        row = self.account()
        self.assertEqual((row["organization_id"], row["user_id"]), (self.owner.organization_id, self.owner.id))
        self.assertEqual(row["microsoft_account_id"], "mock-account")
        self.assertEqual(row["tenant_id"], "mock-tenant")
        self.assertTrue(row["is_active"])
        self.assertEqual(self.pending(), 0)
        raw = microsoft.decrypt(self.settings, self.owner, "cache", row["token_cache_encrypted"])
        cache = msal.SerializableTokenCache()
        cache.deserialize(raw)
        self.assertIn(self.access, raw)
        self.assertIn(self.refresh, raw)
        self.assertNotIn(self.access, str(row))
        self.assertNotIn(self.refresh, str(row))
        page = self.browser.get("/integrations/microsoft")
        self.assertIn("Conectado", page.text)
        self.assertIn("outlook@example.invalid", page.text)
        self.assertIn("&lt;b&gt;Microsoft&lt;/b&gt;", page.text)
        self.assertNotIn("<b>Microsoft</b>", page.text)
        for secret in (self.access, self.refresh, self.settings.microsoft_client_secret, self.settings.token_encryption_key, self.verifier):
            self.assertNotIn(secret, page.text + str(page.headers))
        args, kwargs = self.graph.call_args
        self.assertEqual(args, ("https://graph.microsoft.com/v1.0/me",))
        self.assertEqual(kwargs["headers"], {"Authorization": "Bearer " + self.access})
        self.assertFalse(kwargs["allow_redirects"])

    def test_replay_and_expired_flow(self):
        self.connect()
        self.assertIn("result=error", self.callback().headers["location"])
        self.assertEqual(self.sdk.acquire_token_by_auth_code_flow.call_count, 1)
        self.begin()
        self.connection.execute(update(MicrosoftOAuthFlow).values(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
        self.assertIn("result=error", self.callback().headers["location"])
        self.assertEqual(self.sdk.acquire_token_by_auth_code_flow.call_count, 1)
        self.assertEqual(self.pending(), 0)

    def test_callback_cannot_be_transferred_to_other_user_or_organization(self):
        self.begin()
        initial_csrf = self.csrf
        for index in (1, 2):
            self.sign_in(index, csrf=initial_csrf)
            self.assertIn("result=error", self.callback().headers["location"])
            self.assertIsNone(self.account())
        self.sdk.acquire_token_by_auth_code_flow.assert_not_called()
        self.sign_in(0, csrf=initial_csrf)
        self.assertEqual(self.callback().headers["location"], "/integrations/microsoft")

    def test_callback_requires_original_session_even_for_same_user(self):
        self.begin()
        self.sign_in(0)
        self.assertIn("result=error", self.callback().headers["location"])
        self.sdk.acquire_token_by_auth_code_flow.assert_not_called()

    def test_callback_rejects_browser_owner_ids(self):
        self.begin()
        self.assertIn("result=error", self.callback(user_id=self.owners[1].id).headers["location"])
        self.assertIn("result=error", self.callback(organization_id=self.owners[2].organization_id).headers["location"])
        self.sdk.acquire_token_by_auth_code_flow.assert_not_called()

    def test_users_and_organizations_only_see_and_delete_own_connection(self):
        for index in (0, 1, 2):
            self.sign_in(index)
            self.assertIn("No conectado", self.browser.get("/integrations/microsoft").text)
            self.connect()
        self.sign_in(1)
        response = self.browser.post("/integrations/microsoft/disconnect", data={"csrf": self.csrf, "user_id": self.owners[0].id, "organization_id": self.owners[2].organization_id})
        self.assertEqual(response.status_code, 303)
        self.assertIsNone(self.account())
        self.assertIsNotNone(self.account(self.owners[0]))
        self.assertIsNotNone(self.account(self.owners[2]))

    def test_database_rejects_mismatched_owner(self):
        with self.assertRaises(IntegrityError), self.connection.begin_nested():
            self.connection.execute(MicrosoftAccount.__table__.insert().values(
                organization_id=self.owners[2].organization_id, user_id=self.owners[0].id,
                microsoft_account_id="mock", principal_name="mock", is_active=False,
            ))

    def test_cipher_rejects_wrong_owner_purpose_key_and_tampering(self):
        self.connect()
        value = self.account()["token_cache_encrypted"]
        for owner, purpose, settings, encrypted in (
            (self.owners[1], "cache", self.settings, value),
            (self.owners[2], "cache", self.settings, value),
            (self.owner, "flow", self.settings, value),
            (self.owner, "cache", replace(self.settings, token_encryption_key=Fernet.generate_key().decode()), value),
            (self.owner, "cache", self.settings, value[:-10] + "corrupt"),
        ):
            with self.assertRaises(microsoft.MicrosoftError):
                microsoft.decrypt(settings, owner, purpose, encrypted)

    def test_disconnect_csrf_removes_cache_and_pending_flow_and_is_idempotent(self):
        self.connect()
        self.begin()
        self.assertEqual(self.browser.get("/integrations/microsoft/disconnect").status_code, 405)
        self.assertEqual(self.browser.post("/integrations/microsoft/disconnect").status_code, 403)
        self.assertIsNotNone(self.account())
        for _ in range(2):
            self.assertEqual(self.browser.post("/integrations/microsoft/disconnect", data={"csrf": self.csrf}).status_code, 303)
        self.assertIsNone(self.account())
        self.assertEqual(self.pending(), 0)
        self.assertIn("result=error", self.callback().headers["location"])
        self.assertIn("No conectado", self.browser.get("/integrations/microsoft").text)

    def test_provider_failure_consumes_flow_without_exposing_error_or_tokens(self):
        self.begin()
        self.acquire_override = RuntimeError(self.access + self.settings.microsoft_client_secret)
        with self.assertNoLogs(level="WARNING"):
            response = self.callback()
        self.assertIn("result=error", response.headers["location"])
        self.assertNotIn(self.access, response.text + str(response.headers))
        self.assertEqual(self.pending(), 0)
        self.assertIsNone(self.account())
        self.graph.assert_not_called()

    def test_consent_denial_does_not_exchange_or_echo_provider_message(self):
        self.begin()
        response = self.callback(error="access_denied", error_description=self.settings.microsoft_client_secret)
        self.assertNotIn(self.settings.microsoft_client_secret, response.text + str(response.headers))
        self.sdk.acquire_token_by_auth_code_flow.assert_not_called()
        self.assertEqual(self.pending(), 0)

    def test_graph_failure_preserves_existing_connection(self):
        self.connect()
        previous = dict(self.account())
        self.begin()
        self.graph_response.status_code = 401
        self.assertIn("result=error", self.callback().headers["location"])
        self.assertEqual(dict(self.account()), previous)
        self.assertEqual(self.pending(), 0)

    def test_reconnect_replaces_own_cache_without_duplicate(self):
        self.connect()
        previous = dict(self.account())
        self.access, self.refresh = secrets.token_urlsafe(40), secrets.token_urlsafe(40)
        self.connect()
        row = self.account()
        self.assertEqual(row["id"], previous["id"])
        self.assertNotEqual(row["token_cache_encrypted"], previous["token_cache_encrypted"])

    def test_optional_tenant_and_mail(self):
        self.begin()
        self.tenant = None
        self.graph_response.json.return_value.update(mail=None)
        self.assertEqual(self.callback().headers["location"], "/integrations/microsoft")
        self.assertIsNone(self.account()["tenant_id"])
        self.assertIn("principal@example.invalid", self.browser.get("/integrations/microsoft").text)

    def test_disabled_user_or_organization_cannot_complete(self):
        self.begin()
        for model, identity in ((User, self.owner.id), (Organization, self.owner.organization_id)):
            with self.connection.begin_nested() as savepoint:
                self.connection.execute(update(model).where(model.id == identity).values(is_active=False))
                self.assertEqual(self.callback().headers["location"], "/login")
                savepoint.rollback()
            self.sign_in(0)
        self.sdk.acquire_token_by_auth_code_flow.assert_not_called()

    def test_configuration_missing_or_invalid_is_safe_and_disconnect_still_works(self):
        self.connect()
        for changes in ({"token_encryption_key": "replace_me"}, {"microsoft_client_secret": ""}, {"microsoft_tenant": "../bad"}, {"microsoft_redirect_uri": "http://example.invalid/integrations/microsoft/callback"}):
            settings = replace(self.settings, **changes)
            self.assertFalse(microsoft.configured(settings))
            self.app.state.settings = settings
            self.assertIn("result=error", self.begin().headers["location"])
        self.assertEqual(self.browser.post("/integrations/microsoft/disconnect", data={"csrf": self.csrf}).status_code, 303)
        self.assertIsNone(self.account())
        self.assertNotIn(self.settings.microsoft_client_secret, repr(self.settings))
        self.assertNotIn(self.settings.token_encryption_key, repr(self.settings))

    def test_debug_logs_and_uvicorn_access_logs_do_not_expose_secrets(self):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        root = logging.getLogger()
        previous = root.level
        root.setLevel(logging.DEBUG)
        root.addHandler(handler)
        self.addCleanup(root.setLevel, previous)
        self.addCleanup(root.removeHandler, handler)
        self.connect()
        for name in ("msal.token_cache", "msal.oauth2cli.oidc", "urllib3.connectionpool"):
            logging.getLogger(name).error("%s", self.access + self.refresh + self.settings.microsoft_client_secret)
        access = logging.getLogger("uvicorn.access")
        access.info('%s - "%s %s HTTP/%s" %d', "test", "GET", "/integrations/microsoft/callback?code=" + self.code, "1.1", 303)
        for value in (self.access, self.refresh, self.settings.microsoft_client_secret, self.settings.token_encryption_key):
            self.assertNotIn(value, stream.getvalue())
        # TestClient registra sus propias URLs; filtrar Uvicorn se verifica aisladamente.
        record = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, "%s", ("/integrations/microsoft/callback?code=" + self.code,), None)
        self.assertFalse(microsoft.CallbackAccessFilter().filter(record))


class MSALContractTests(unittest.TestCase):
    def test_real_msal_generates_exact_scopes_pkce_nonce_and_rejects_state_offline(self):
        http = MagicMock()
        http.get.return_value.status_code = 200
        http.get.return_value.json.return_value = {
            "authorization_endpoint": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
            "token_endpoint": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
            "issuer": "https://login.microsoftonline.com/{tenantid}/v2.0",
        }
        http.get.return_value.text = json.dumps(http.get.return_value.json.return_value)
        with patch("requests.sessions.Session.send", side_effect=AssertionError("Sin red")):
            app = msal.ConfidentialClientApplication(str(uuid4()), client_credential=secrets.token_urlsafe(32),
                authority="https://login.microsoftonline.com/common", http_client=http, instance_discovery=False)
            flow = app.initiate_auth_code_flow(microsoft.SCOPES, redirect_uri="http://localhost/integrations/microsoft/callback", response_mode="query")
            query = parse_qs(urlsplit(flow["auth_uri"]).query)
            self.assertEqual(set(query["scope"][0].split()), {"openid", "profile", "offline_access", "User.Read", "Mail.Read"})
            self.assertEqual(query["response_type"], ["code"])
            self.assertEqual(query["code_challenge_method"], ["S256"])
            self.assertTrue(flow["code_verifier"])
            self.assertTrue(query["nonce"])
            with self.assertRaises(ValueError):
                app.acquire_token_by_auth_code_flow(flow, {"state": "invalid", "code": "mock"})
            http.post.assert_not_called()
