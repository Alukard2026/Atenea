"""Autenticación web contra PostgreSQL con datos aislados mediante rollback."""

import base64
import json
from pathlib import Path
import re
import secrets
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner
from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from app.config import Settings, load_settings
from app.database import engine, get_db
from app.main import create_app
from app.models import Organization, User
from app.routers.auth import LOGIN_ERROR
from app.security import hash_password


class ConfigurationTests(unittest.TestCase):
    def test_missing_secret_fails_without_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("app.config.ENV_PATH", Path(directory) / ".env"), patch.dict(
                "os.environ", {"SESSION_SECRET": secrets.token_urlsafe(32)},
            ):
                with self.assertRaises(RuntimeError):
                    load_settings()

    def test_dotenv_settings_and_secure_cookie(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("SESSION_SECRET=" + secrets.token_urlsafe(32) + "\nSESSION_COOKIE_SECURE=true\n", encoding="utf-8")
            with patch("app.config.ENV_PATH", path):
                settings = load_settings()
            self.assertTrue(settings.session_cookie_secure)
            self.assertTrue(settings.session_secret not in repr(settings))
            with TestClient(create_app(settings), base_url="https://localhost") as client:
                response = client.get("/login")
                self.assertEqual(response.status_code, 200)
                self.assertIn("; secure", response.headers["set-cookie"].lower())


class WebAuthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.password_a = secrets.token_urlsafe(24)
        cls.password_b = secrets.token_urlsafe(24)
        cls.hash_a = hash_password(cls.password_a)
        cls.hash_b = hash_password(cls.password_b)
        cls.settings = Settings(session_secret=secrets.token_urlsafe(32))
        cls.app = create_app(cls.settings)

    def setUp(self):
        self.connection = engine.connect()
        self.addCleanup(self.connection.close)
        self.transaction = self.connection.begin()
        self.addCleanup(lambda: self.transaction.rollback() if self.transaction.is_active else None)
        self.assertEqual(self.connection.scalar(text("SELECT current_database()")), "atenea_db")
        self.baseline = self._counts()
        suffix = uuid4().hex
        self.org_a = "Web A " + suffix
        self.org_b = "Web B " + suffix
        self.email = suffix + "@example.invalid"
        self.email_b = "other-" + self.email
        with Session(bind=self.connection, join_transaction_mode="create_savepoint") as db, db.begin():
            first = Organization(name=self.org_a)
            second = Organization(name=self.org_b)
            user_a = User(organization=first, email=self.email, full_name="Persona Alfa", role="admin", hashed_password=self.hash_a)
            user_b = User(organization=second, email=self.email_b, full_name="Persona Beta", role="user", hashed_password=self.hash_b)
            db.add_all([user_a, user_b])
            db.flush()
            self.org_a_id, self.org_b_id = first.id, second.id
            self.user_a_id, self.user_b_id = user_a.id, user_b.id

        def test_db():
            with Session(bind=self.connection, join_transaction_mode="create_savepoint") as db:
                yield db

        self.app.dependency_overrides[get_db] = test_db
        self.addCleanup(self.app.dependency_overrides.clear)
        self.client = TestClient(self.app, base_url="http://localhost", follow_redirects=False)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def _counts(self):
        return tuple(self.connection.scalar(select(func.count()).select_from(model)) for model in (Organization, User))

    def tearDown(self):
        self.transaction.rollback()
        self.assertEqual(self._counts(), self.baseline, "No deben quedar filas de prueba.")

    def token(self, response):
        match = re.search(r'name="csrf" value="([A-Za-z0-9_-]+)"', response.text)
        self.assertIsNotNone(match)
        return match.group(1)

    def login(self, **overrides):
        self.client.cookies.clear()
        response = self.client.get("/login")
        data = {"email": self.email, "password": self.password_a, "csrf": self.token(response)}
        data.update(overrides)
        response = self.client.post("/login", data=data)
        self.assertTrue(all(value not in response.text for value in (self.password_a, self.password_b, self.hash_a, self.hash_b)))
        return response

    def rejected(self, **overrides):
        response = self.login(**overrides)
        self.assertEqual(response.status_code, 401)
        self.assertIn(LOGIN_ERROR, response.text)
        self.assertEqual(self.client.get("/dashboard").headers.get("location"), "/login")

    def set_signed_session(self, payload, expired=False):
        signer = TimestampSigner(self.settings.session_secret)
        data = base64.b64encode(json.dumps(payload).encode())
        if expired:
            with patch.object(signer, "get_timestamp", return_value=1):
                cookie = signer.sign(data).decode()
        else:
            cookie = signer.sign(data).decode()
        self.client.cookies.clear()
        self.client.cookies.set("atenea_session", cookie)

    def test_correct_login_minimal_cookie_and_dashboard(self):
        response = self.login(email=" " + self.email.upper() + " ")
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/dashboard")
        flags = response.headers["set-cookie"].lower()
        self.assertTrue(all(flag in flags for flag in ("httponly", "samesite=lax", "max-age=28800", "path=/")))
        self.assertNotIn("; secure", flags)
        cookie = self.client.cookies.get("atenea_session")
        session = json.loads(base64.b64decode(TimestampSigner(self.settings.session_secret).unsign(cookie)))
        self.assertEqual(set(session), {"user_id", "organization_id", "csrf_token", "auth_version"})
        self.assertEqual(session["user_id"], self.user_a_id)
        self.assertEqual(session["organization_id"], self.org_a_id)
        dashboard = self.client.get("/dashboard")
        self.assertEqual(dashboard.status_code, 200)
        self.assertTrue(all(value in dashboard.text for value in ("Persona Alfa", self.org_a, "admin")))
        self.assertNotIn(self.org_b, dashboard.text)
        self.assertEqual(dashboard.headers["cache-control"], "no-store")
        self.assertEqual(self.client.get("/login").headers.get("location"), "/dashboard")

    def test_wrong_password(self):
        self.rejected(password=self.password_b)

    def test_nonexistent_user(self):
        self.rejected(email="missing-" + self.email)

    def test_wrong_organization(self):
        self.rejected(organization="missing-" + self.org_a)

    def test_inactive_user(self):
        self.connection.execute(update(User).where(User.id == self.user_a_id).values(is_active=False))
        self.rejected()

    def test_inactive_organization(self):
        self.connection.execute(update(Organization).where(Organization.id == self.org_a_id).values(is_active=False))
        self.rejected()

    def test_ambiguous_organization(self):
        self.connection.execute(Organization.__table__.insert().values(name=self.org_a.upper()))
        self.assertEqual(self.login().status_code, 303)

    def test_dashboard_without_session(self):
        response = self.client.get("/dashboard")
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/login")

    def test_logout_clears_session(self):
        self.assertEqual(self.login().status_code, 303)
        dashboard = self.client.get("/dashboard")
        response = self.client.post("/logout", data={"csrf": self.token(dashboard)})
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/login")
        self.assertIsNone(self.client.cookies.get("atenea_session"))
        self.assertEqual(self.client.get("/dashboard").status_code, 303)

    def test_csrf_login_logout_and_rotation(self):
        page = self.client.get("/login")
        old_token = self.token(page)
        self.assertEqual(self.client.post("/login", data={"email": self.email, "password": self.password_a}).status_code, 403)
        response = self.client.post("/login", data={"email": self.email, "password": self.password_a, "csrf": old_token})
        self.assertEqual(response.status_code, 303)
        self.assertNotEqual(old_token, self.token(self.client.get("/dashboard")))
        self.assertEqual(self.client.post("/logout", data={"csrf": old_token}).status_code, 403)
        self.assertEqual(self.client.get("/logout").status_code, 405)
        self.assertEqual(self.client.get("/dashboard").status_code, 200)

    def test_organization_isolation_email_resolves_owner(self):
        self.rejected(email=self.email_b)
        self.assertEqual(self.login(email=self.email_b, password=self.password_b).status_code, 303)
        dashboard = self.client.get("/dashboard")
        self.assertIn("Persona Beta", dashboard.text)
        self.assertIn(self.org_b, dashboard.text)
        self.assertNotIn("Persona Alfa", dashboard.text)
        self.assertNotIn(self.org_a, dashboard.text)

    def test_signed_mismatched_ids_are_rejected(self):
        self.set_signed_session({"user_id": self.user_a_id, "organization_id": self.org_b_id})
        self.assertEqual(self.client.get("/dashboard").headers.get("location"), "/login")

    def test_tampered_expired_and_malformed_sessions(self):
        self.client.cookies.set("atenea_session", "invalid.signature")
        self.assertEqual(self.client.get("/dashboard").status_code, 303)
        self.set_signed_session({"user_id": self.user_a_id, "organization_id": self.org_a_id}, expired=True)
        self.assertEqual(self.client.get("/dashboard").status_code, 303)
        self.set_signed_session({"user_id": "invalid", "organization_id": self.org_a_id})
        self.assertEqual(self.client.get("/dashboard").status_code, 303)

    def test_user_disabled_after_login(self):
        self.assertEqual(self.login().status_code, 303)
        self.connection.execute(update(User).where(User.id == self.user_a_id).values(is_active=False))
        self.assertEqual(self.client.get("/dashboard").headers.get("location"), "/login")

    def test_organization_disabled_after_login(self):
        self.assertEqual(self.login().status_code, 303)
        self.connection.execute(update(Organization).where(Organization.id == self.org_a_id).values(is_active=False))
        self.assertEqual(self.client.get("/dashboard").headers.get("location"), "/login")

    def test_dashboard_refreshes_role_and_escapes_html(self):
        self.assertEqual(self.login().status_code, 303)
        self.connection.execute(update(User).where(User.id == self.user_a_id).values(role="user", full_name="<script>test</script>"))
        response = self.client.get("/dashboard")
        self.assertIn("&lt;script&gt;test&lt;/script&gt;", response.text)
        self.assertNotIn("<script>", response.text)
        self.assertIn('class="role">user</span>', response.text)

    def test_validation_and_database_errors_do_not_expose_secrets(self):
        response = self.login(password=self.password_a * 4)
        self.assertEqual(response.status_code, 400)
        self.assertNotIn(self.password_a, response.text)
        page = self.client.get("/login")
        from sqlalchemy.exc import SQLAlchemyError
        with patch("sqlalchemy.orm.Session.scalars", side_effect=SQLAlchemyError(self.hash_a)), self.assertLogs("atenea", level="ERROR") as logs:
            response = self.client.post("/login", data={"email": self.email, "password": self.password_a, "csrf": self.token(page)})
        self.assertEqual(response.status_code, 503)
        self.assertTrue(all(value not in response.text + "".join(logs.output) for value in (self.password_a, self.hash_a, self.email)))


if __name__ == "__main__":
    unittest.main()
