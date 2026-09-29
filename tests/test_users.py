"""Administración y sesiones; datos ficticios revertidos al terminar cada prueba."""
import base64
import io
import json
import logging
import secrets
from unittest.mock import patch

from itsdangerous import TimestampSigner
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from app.models import User, Task, CalendarEvent
from app.security import verify_password
from app import users
from test_worklog import WorklogCase


class UserManagementTests(WorklogCase):
    def setUp(self):
        super().setUp()
        self.sign_in(self.colleague_id, self.org_id)
        self.initial_password = secrets.token_urlsafe(24)
        self.new_email = "new-" + self.email

    def data(self, **changes):
        return {"full_name": "Nueva persona", "email": self.new_email, "role": "user", "is_active": "yes", "password": self.initial_password, "password_confirmation": self.initial_password, **changes}

    def edit_data(self, **changes):
        return {"full_name": "Usuario operativo", "email": self.email, "role": "user", "is_active": "yes", **changes}

    def row(self, identity):
        return self.connection.execute(select(User.__table__).where(User.id == identity)).mappings().one()

    def create(self, **changes):
        response = self.post("/settings/users/new", self.data(**changes))
        self.assertEqual(response.status_code, 303)
        return self.connection.scalar(select(User.id).where(User.email == self.new_email))

    def login(self, email, password):
        self.browser.cookies.clear()
        page = self.browser.get("/login")
        return self.browser.post("/login", data={"email": email, "password": password, "csrf": self.token(page)})

    def test_login_has_only_email_password_csrf(self):
        self.browser.cookies.clear()
        page = self.browser.get("/login")
        self.assertNotIn('name="organization"', page.text)
        self.assertNotIn('name="organization_id"', page.text)
        self.assertIn('name="email"', page.text)
        self.assertIn('name="password"', page.text)
        self.assertIn('name="csrf"', page.text)

    def test_admin_creates_normalized_user_and_only_hash(self):
        response = self.post("/settings/users/new", self.data(email="  " + self.new_email.upper() + "  "))
        self.assertEqual(response.status_code, 303)
        identity = self.connection.scalar(select(User.id).where(User.email == self.new_email))
        row = self.row(identity)
        self.assertEqual(row["organization_id"], self.org_id)
        self.assertTrue(verify_password(self.initial_password, row["hashed_password"]))
        self.assertNotEqual(row["hashed_password"], self.initial_password)
        self.assertEqual(row["role"], "user")

    def test_new_account_logs_in_resolving_organization(self):
        identity = self.create()
        self.assertEqual(self.login(" " + self.new_email.upper() + " ", self.initial_password).status_code, 303)
        raw = TimestampSigner(self.settings.session_secret).unsign(self.browser.cookies.get("atenea_session"))
        session = json.loads(base64.b64decode(raw))
        self.assertEqual(session["user_id"], identity)
        self.assertEqual(session["organization_id"], self.org_id)
        self.assertEqual(session["auth_version"], 0)
        for path in ("/dashboard", "/tasks", "/calendar", "/mail", "/integrations/microsoft"):
            page = self.browser.get(path)
            self.assertEqual(page.status_code, 200)
            self.assertEqual(page.context["user"].organization_id, self.org_id)

    def test_login_error_same_for_missing_wrong_inactive(self):
        from app.routers.auth import LOGIN_ERROR
        attempts = [(self.email, "wrong-password"), ("missing-" + self.email, self.password)]
        for email, password in attempts:
            result = self.login(email, password)
            self.assertEqual(result.status_code, 401)
            self.assertIn(LOGIN_ERROR, result.text)
        self.connection.execute(update(User).where(User.id == self.user_id).values(is_active=False))
        result = self.login(self.email, self.password)
        self.assertEqual(result.status_code, 401)
        self.assertIn(LOGIN_ERROR, result.text)

    def test_admin_cannot_set_organization_or_source_identity(self):
        for key in ("organization_id", "user_id", "id", "auth_version", "hashed_password"):
            self.assertEqual(self.post("/settings/users/new", self.data(**{key: str(self.foreign_org_id)})).status_code, 400)

    def test_normal_user_cannot_manage(self):
        self.sign_in(self.user_id, self.org_id)
        for path in ("/settings/users", "/settings/users/new", f"/settings/users/{self.colleague_id}/edit", f"/settings/users/{self.colleague_id}/password"):
            self.assertEqual(self.browser.get(path).status_code, 403)
        self.assertEqual(self.post("/settings/users/new", self.data()).status_code, 403)
        self.assertNotIn('href="/settings/users"', self.browser.get("/dashboard").text)

    def test_list_and_mutations_are_organization_scoped(self):
        response = self.browser.get("/settings/users")
        self.assertEqual({r.id for r in response.context["rows"]}, {self.user_id, self.colleague_id})
        self.assertNotIn("Usuario externo", response.text)
        for action in ("edit", "password"):
            path = f"/settings/users/{self.foreign_user_id}/{action}"
            self.assertEqual(self.browser.get(path).status_code, 404)
            self.assertEqual(self.post(path, self.edit_data() if action == "edit" else {"password": self.initial_password, "password_confirmation": self.initial_password}).status_code, 404)

    def test_global_duplicate_generic_rejection(self):
        for email in (self.email, "foreign-" + self.email):
            response = self.post("/settings/users/new", self.data(email=" " + email.upper() + " "))
            self.assertEqual(response.status_code, 422)
            self.assertIn(users.EMAIL_UNAVAILABLE, response.text)
            self.assertNotIn("Usuario externo", response.text)

    def test_db_unique_index_normalizes_trim_case(self):
        with self.assertRaises(IntegrityError), self.connection.begin_nested():
            self.connection.execute(User.__table__.insert().values(organization_id=self.foreign_org_id, email=" " + self.email.upper() + " ", full_name="Duplicado", hashed_password="unused"))

    def test_edit_name_email_role_and_duplicate(self):
        response = self.post(f"/settings/users/{self.user_id}/edit", self.edit_data(full_name="Nombre editado", email="  " + self.new_email.upper() + " ", role="admin"))
        self.assertEqual(response.status_code, 303)
        row = self.row(self.user_id)
        self.assertEqual((row["full_name"], row["email"], row["role"]), ("Nombre editado", self.new_email, "admin"))
        self.assertEqual(row["auth_version"], 1)
        self.assertEqual(self.post(f"/settings/users/{self.user_id}/edit", self.edit_data(email="foreign-" + self.email)).status_code, 422)

    def test_deactivation_preserves_history_and_invalidates_session(self):
        entry = self.add_entry()
        task = self.connection.scalar(Task.__table__.insert().values(organization_id=self.org_id, user_id=self.user_id, title="Histórico").returning(Task.id))
        self.assertEqual(self.login(self.email, self.password).status_code, 303)
        old_cookie = self.browser.cookies.get("atenea_session")
        self.sign_in(self.colleague_id, self.org_id)
        self.assertEqual(self.post(f"/settings/users/{self.user_id}/edit", self.edit_data(is_active="no")).status_code, 303)
        self.assertFalse(self.row(self.user_id)["is_active"])
        from app.models import TimeEntry
        self.assertEqual(self.connection.scalar(select(TimeEntry.id).where(TimeEntry.id == entry)), entry)
        self.assertEqual(self.connection.scalar(select(Task.id).where(Task.id == task)), task)
        self.assertEqual(self.post(f"/settings/users/{self.user_id}/edit", self.edit_data()).status_code, 303)
        self.browser.cookies.clear()
        self.browser.cookies.set("atenea_session", old_cookie)
        self.assertEqual(self.browser.get("/dashboard").status_code, 303)
        self.assertEqual(self.login(self.email, self.password).status_code, 303)

    def test_last_admin_cannot_deactivate_or_demote(self):
        for changes in ({"is_active": "no"}, {"role": "user"}):
            data = self.edit_data(email="colleague-" + self.email, role="admin")
            data.update(changes)
            response = self.post(f"/settings/users/{self.colleague_id}/edit", data)
            self.assertEqual(response.status_code, 422)
            self.assertIn("al menos un administrador activo", response.text)
        row = self.row(self.colleague_id)
        self.assertTrue(row["is_active"])
        self.assertEqual(row["role"], "admin")

    def test_second_admin_allows_demotion_but_inactive_admin_does_not(self):
        self.connection.execute(update(User).where(User.id == self.user_id).values(role="admin", is_active=False))
        data = self.edit_data(email="colleague-" + self.email)
        self.assertEqual(self.post(f"/settings/users/{self.colleague_id}/edit", data).status_code, 422)
        self.connection.execute(update(User).where(User.id == self.user_id).values(is_active=True))
        self.assertEqual(self.post(f"/settings/users/{self.colleague_id}/edit", data).status_code, 303)
        self.assertEqual(self.browser.get("/settings/users").status_code, 403)

    def test_password_reset_replaces_hash_revokes_cookie_and_preserves_data(self):
        self.assertEqual(self.login(self.email, self.password).status_code, 303)
        old_cookie = self.browser.cookies.get("atenea_session")
        self.sign_in(self.colleague_id, self.org_id)
        response = self.post(f"/settings/users/{self.user_id}/password", {"password": self.initial_password, "password_confirmation": self.initial_password})
        self.assertEqual(response.status_code, 303)
        row = self.row(self.user_id)
        self.assertTrue(verify_password(self.initial_password, row["hashed_password"]))
        self.assertEqual(row["auth_version"], 1)
        self.browser.cookies.clear()
        self.browser.cookies.set("atenea_session", old_cookie)
        self.assertEqual(self.browser.get("/tasks").status_code, 303)
        self.assertEqual(self.login(self.email, self.password).status_code, 401)
        self.assertEqual(self.login(self.email, self.initial_password).status_code, 303)

    def test_self_reset_clears_session(self):
        response = self.post(f"/settings/users/{self.colleague_id}/password", {"password": self.initial_password, "password_confirmation": self.initial_password})
        self.assertEqual(response.headers["location"], "/login")
        self.assertEqual(self.browser.get("/dashboard").status_code, 303)

    def test_csrf_all_mutations_and_no_get_changes(self):
        for path in ("/settings/users/new", f"/settings/users/{self.user_id}/edit", f"/settings/users/{self.user_id}/password"):
            self.assertEqual(self.browser.post(path, data=self.data()).status_code, 403)
            self.assertEqual(self.browser.post(path, data={**self.data(), "csrf": "bad"}).status_code, 403)
        before = self.row(self.user_id)
        self.browser.get(f"/settings/users/{self.user_id}/password")
        self.assertEqual(before, self.row(self.user_id))

    def test_password_policy_and_no_password_echo_in_errors_or_logs(self):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        logging.getLogger().addHandler(handler)
        try:
            for password in ("short", " " * 12, "á" * 37):
                response = self.post("/settings/users/new", self.data(password=password, password_confirmation=password))
                self.assertEqual(response.status_code, 422)
                self.assertNotIn('value="' + password + '"', response.text)
            response = self.post("/settings/users/new", self.data(password_confirmation="different"))
            self.assertEqual(response.status_code, 422)
            self.assertNotIn(self.initial_password, response.text)
            self.assertNotIn(self.initial_password, stream.getvalue())
            self.assertNotIn(self.password_hash, self.browser.get("/settings/users").text)
            self.assertNotIn(self.password_hash, self.browser.get(f"/settings/users/{self.user_id}/edit").text)
        finally:
            logging.getLogger().removeHandler(handler)

    def test_validation_and_inactive_creation(self):
        for changes in ({"email": "bad"}, {"full_name": ""}, {"role": "superadmin"}, {"is_active": "maybe"}):
            self.assertEqual(self.post("/settings/users/new", self.data(**changes)).status_code, 422)
        self.create(is_active="no")
        self.assertEqual(self.login(self.new_email, self.initial_password).status_code, 401)

    def test_query_and_large_ids_rejected(self):
        for path in ("/settings/users?organization_id=1", "/settings/users/new?organization_id=1"):
            self.assertEqual(self.browser.get(path).status_code, 400)
        self.assertEqual(self.browser.get("/settings/users/99999999999999/edit").status_code, 404)
        self.assertEqual(self.browser.get("/settings/users?page=0").status_code, 404)

    def test_integrity_race_is_generic_and_does_not_echo_password(self):
        with patch("app.users.save_user", side_effect=IntegrityError("private", {}, Exception(self.initial_password))):
            response = self.post("/settings/users/new", self.data())
        self.assertEqual(response.status_code, 422)
        self.assertIn(users.EMAIL_UNAVAILABLE, response.text)
        self.assertNotIn(self.initial_password, response.text)

    def test_forged_auth_version_rejected(self):
        payload = {"user_id": self.user_id, "organization_id": self.org_id, "auth_version": True}
        cookie = TimestampSigner(self.settings.session_secret).sign(base64.b64encode(json.dumps(payload).encode())).decode()
        self.browser.cookies.clear()
        self.browser.cookies.set("atenea_session", cookie)
        self.assertEqual(self.browser.get("/dashboard").status_code, 303)
