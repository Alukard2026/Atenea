"""Flujo operativo multiempresa en PostgreSQL, sin conservar filas de prueba."""

import base64
from datetime import date, timedelta
from decimal import Decimal
import json
import re
import secrets
import unittest
from uuid import uuid4

from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner
from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from app.config import Settings
from app.database import engine, get_db
from app.main import create_app
from app.models import Organization, User, Client, Project, TimeEntry
from app.security import hash_password
from app.worklog import monday_for


class WorklogCase(unittest.TestCase):
    """Fixture compartido; las subclases aportan los casos de prueba."""
    @classmethod
    def setUpClass(cls):
        cls.password = secrets.token_urlsafe(24)
        cls.password_hash = hash_password(cls.password)
        cls.settings = Settings(session_secret=secrets.token_urlsafe(32))
        cls.app = create_app(cls.settings)

    def setUp(self):
        self.connection = engine.connect()
        self.addCleanup(self.connection.close)
        self.transaction = self.connection.begin()
        self.addCleanup(lambda: self.transaction.rollback() if self.transaction.is_active else None)
        self.assertEqual(self.connection.scalar(text("SELECT current_database()")), "atenea_db")
        self.baseline = self.counts()
        suffix = uuid4().hex
        self.org_name = "Operaciones " + suffix
        self.email = suffix + "@example.invalid"
        with Session(bind=self.connection, join_transaction_mode="create_savepoint") as db, db.begin():
            org = Organization(name=self.org_name)
            foreign_org = Organization(name="Otra organización " + suffix)
            user = User(organization=org, email=self.email, full_name="Usuario operativo", role="user", hashed_password=self.password_hash)
            colleague = User(organization=org, email="colleague-" + self.email, full_name="Otro usuario", role="admin", hashed_password=self.password_hash)
            foreign_user = User(organization=foreign_org, email="foreign-" + self.email, full_name="Usuario externo", role="admin", hashed_password=self.password_hash)
            client = Client(organization=org, name="Cliente principal", code="CLI-A")
            other_client = Client(organization=org, name="Segundo cliente", code="CLI-B")
            foreign_client = Client(organization=foreign_org, name="Cliente externo reservado", code="CLI-X")
            project = Project(organization=org, client=client, name="Expediente principal", code="EXP-A")
            other_project = Project(organization=org, client=other_client, name="Segundo expediente", code="EXP-B")
            foreign_project = Project(organization=foreign_org, client=foreign_client, name="Expediente externo reservado", code="EXP-X")
            db.add_all([user, colleague, foreign_user, project, other_project, foreign_project])
            db.flush()
            self.org_id, self.foreign_org_id = org.id, foreign_org.id
            self.user_id, self.colleague_id, self.foreign_user_id = user.id, colleague.id, foreign_user.id
            self.client_id, self.other_client_id, self.foreign_client_id = client.id, other_client.id, foreign_client.id
            self.project_id, self.other_project_id, self.foreign_project_id = project.id, other_project.id, foreign_project.id

        def test_db():
            with Session(bind=self.connection, join_transaction_mode="create_savepoint") as db:
                yield db

        self.app.dependency_overrides[get_db] = test_db
        self.addCleanup(self.app.dependency_overrides.clear)
        self.browser = TestClient(self.app, base_url="http://localhost", follow_redirects=False)
        self.browser.__enter__()
        self.addCleanup(self.browser.__exit__, None, None, None)
        self.sign_in(self.user_id, self.org_id)
        self.day = date(2025, 9, 1)

    def counts(self):
        return tuple(self.connection.scalar(select(func.count()).select_from(model)) for model in (Organization, User, Client, Project, TimeEntry))

    def tearDown(self):
        self.transaction.rollback()
        self.assertEqual(self.counts(), self.baseline, "No deben quedar datos de prueba.")

    def sign_in(self, user_id, organization_id):
        payload = {"user_id": user_id, "organization_id": organization_id, "csrf_token": secrets.token_urlsafe(32)}
        cookie = TimestampSigner(self.settings.session_secret).sign(base64.b64encode(json.dumps(payload).encode())).decode()
        self.browser.cookies.clear()
        self.browser.cookies.set("atenea_session", cookie)

    def token(self, response):
        match = re.search(r'name="csrf" value="([A-Za-z0-9_-]+)"', response.text)
        self.assertIsNotNone(match)
        return match.group(1)

    def post(self, path, data=None):
        page = self.browser.get("/dashboard")
        self.assertEqual(page.status_code, 200)
        return self.browser.post(path, data={**(data or {}), "csrf": self.token(page)})

    def entry_data(self, **overrides):
        return {"work_date": self.day.isoformat(), "client_id": str(self.client_id), "project_id": str(self.project_id),
                "activity": "Revisión contractual", "description": "Lectura y análisis", "hours": "2.25", "billable": "yes", **overrides}

    def add_entry(self, *, user_id=None, organization_id=None, client_id=None, project_id=None, day=None, hours="2.25", activity="Registro de prueba"):
        return self.connection.scalar(TimeEntry.__table__.insert().values(
            organization_id=organization_id or self.org_id, user_id=user_id or self.user_id,
            client_id=client_id or self.client_id, project_id=project_id or self.project_id,
            work_date=day or self.day, hours=Decimal(hours), activity=activity, billable=True,
        ).returning(TimeEntry.id))

    def row(self, entry_id):
        return self.connection.execute(select(TimeEntry.__table__).where(TimeEntry.id == entry_id)).mappings().one_or_none()


class WorklogTests(WorklogCase):
    def test_create_client_and_list(self):
        self.sign_in(self.colleague_id, self.org_id)
        response = self.post("/clients", {"name": "Nuevo cliente", "code": "NEW"})
        self.assertEqual(response.status_code, 303)
        row = self.connection.execute(select(Client.__table__).where(Client.organization_id == self.org_id, Client.name == "Nuevo cliente")).mappings().one()
        self.assertEqual(row["code"], "NEW")
        self.assertTrue(row["is_active"])
        listing = self.browser.get("/clients")
        self.assertIn("Nuevo cliente", listing.text)

    def test_duplicate_clients_are_scoped_and_normalized(self):
        self.sign_in(self.colleague_id, self.org_id)
        self.assertEqual(self.post("/clients", {"name": "  CLIENTE   PRINCIPAL  "}).status_code, 422)
        self.assertEqual(self.post("/clients", {"name": "Otro nombre", "code": " cli-a "}).status_code, 422)
        self.assertEqual(self.post("/clients", {"name": "Cliente externo reservado", "code": "CLI-X"}).status_code, 303)

    def test_client_and_project_lists_are_isolated(self):
        for path in ("/clients", "/projects", "/hours"):
            response = self.browser.get(path, params={"organization_id": self.foreign_org_id})
            self.assertEqual(response.status_code, 200)
            self.assertNotIn("Cliente externo reservado", response.text)
            self.assertNotIn("Expediente externo reservado", response.text)
        self.connection.execute(update(Client).where(Client.id == self.other_client_id).values(is_active=False))
        self.assertNotIn("Segundo cliente", self.browser.get("/clients").text)
        self.assertNotIn("Segundo expediente", self.browser.get("/projects").text)

    def test_create_project_and_reject_duplicates(self):
        data = {"client_id": str(self.client_id), "name": "Nuevo expediente", "code": "EXP-NEW", "description": "Descripción del expediente"}
        self.assertEqual(self.post("/projects", data).status_code, 303)
        row = self.connection.execute(select(Project.__table__).where(Project.organization_id == self.org_id, Project.name == "Nuevo expediente")).mappings().one()
        self.assertEqual(row["client_id"], self.client_id)
        self.assertEqual(row["description"], data["description"])
        self.assertIn("Nuevo expediente", self.browser.get("/projects").text)
        self.assertEqual(self.post("/projects", dict(data, name=" NUEVO EXPEDIENTE ")).status_code, 422)
        self.assertEqual(self.post("/projects", dict(data, client_id=str(self.other_client_id))).status_code, 303)

    def test_reject_project_creation_for_foreign_client(self):
        self.assertEqual(self.post("/projects", {"client_id": str(self.foreign_client_id), "name": "Inválido"}).status_code, 422)

    def test_create_hours_with_session_identity(self):
        response = self.post("/hours", self.entry_data())
        self.assertEqual(response.status_code, 303)
        row = self.connection.execute(select(TimeEntry.__table__).where(TimeEntry.user_id == self.user_id)).mappings().one()
        self.assertEqual(row["organization_id"], self.org_id)
        self.assertEqual(row["user_id"], self.user_id)
        self.assertEqual(row["hours"], Decimal("2.25"))
        self.assertTrue(row["billable"])
        self.assertEqual(row["work_date"], self.day)
        self.assertIn("month=2025-09", response.headers["location"])

    def test_reject_foreign_project_or_client_and_mismatched_pair(self):
        changes = (
            {"project_id": str(self.foreign_project_id)},
            {"client_id": str(self.foreign_client_id), "project_id": str(self.foreign_project_id)},
            {"project_id": str(self.other_project_id)},
            {"client_id": "2147483647"},
        )
        for change in changes:
            with self.subTest(fields=tuple(change)):
                self.assertEqual(self.post("/hours", self.entry_data(**change)).status_code, 422)
        self.assertEqual(self.connection.scalar(select(func.count()).select_from(TimeEntry).where(TimeEntry.user_id == self.user_id)), 0)

    def test_reject_forged_organization_and_user_fields(self):
        self.sign_in(self.colleague_id, self.org_id)
        for field, value in (("organization_id", self.foreign_org_id), ("user_id", self.colleague_id)):
            for path, data in (("/clients", {"name": "Inválido"}), ("/projects", {"name": "Inválido", "client_id": str(self.client_id)}), ("/hours", self.entry_data())):
                self.assertEqual(self.post(path, {**data, field: str(value)}).status_code, 400)

    def test_edit_own_entry(self):
        entry_id = self.add_entry()
        page = self.browser.get(f"/hours/{entry_id}/edit")
        self.assertEqual(page.status_code, 200)
        self.assertIn('value="2.25"', page.text)
        response = self.post(f"/hours/{entry_id}/edit", self.entry_data(hours="3.50", description="Descripci?n actualizada", billable="no", work_date="2025-09-02"))
        self.assertEqual(response.status_code, 303)
        row = self.row(entry_id)
        self.assertEqual(row["hours"], Decimal("3.50"))
        self.assertEqual(row["description"], "Descripci?n actualizada")
        self.assertEqual(row["activity"], "Registro de prueba")
        self.assertEqual(row["work_date"], date(2025, 9, 2))
        self.assertFalse(row["billable"])
        self.assertEqual(row["user_id"], self.user_id)

    def test_cannot_read_edit_or_delete_others_entries(self):
        colleague_entry = self.add_entry(user_id=self.colleague_id)
        foreign_entry = self.add_entry(user_id=self.foreign_user_id, organization_id=self.foreign_org_id, client_id=self.foreign_client_id, project_id=self.foreign_project_id)
        for entry_id in (colleague_entry, foreign_entry):
            for suffix in ("edit", "delete"):
                path = f"/hours/{entry_id}/{suffix}"
                self.assertEqual(self.browser.get(path).status_code, 404)
                self.assertEqual(self.post(path, self.entry_data()).status_code, 404)
            self.assertIsNotNone(self.row(entry_id))
        own_entry = self.add_entry()
        self.sign_in(self.colleague_id, self.org_id)  # Tampoco un admin puede editar horas ajenas.
        self.assertEqual(self.post(f"/hours/{own_entry}/edit", self.entry_data()).status_code, 404)

    def test_edit_rejects_foreign_pair_without_changes(self):
        entry_id = self.add_entry()
        response = self.post(f"/hours/{entry_id}/edit", self.entry_data(project_id=str(self.foreign_project_id)))
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.row(entry_id)["project_id"], self.project_id)

    def test_delete_own_entry_requires_post_and_csrf(self):
        entry_id = self.add_entry()
        path = f"/hours/{entry_id}/delete"
        page = self.browser.get(path)
        self.assertEqual(page.status_code, 200)
        self.assertIn("Esta acción no se puede deshacer", page.text)
        self.assertIsNotNone(self.row(entry_id))
        self.assertEqual(self.browser.post(path, data={}).status_code, 403)
        self.assertIsNotNone(self.row(entry_id))
        self.assertEqual(self.post(path).status_code, 303)
        self.assertIsNone(self.row(entry_id))

    def test_hours_validation_and_daily_limit(self):
        for hours in ("0", "-1", "24.01", "1.001", "NaN", "Infinity", "1e1"):
            with self.subTest(hours=hours):
                self.assertEqual(self.post("/hours", self.entry_data(hours=hours)).status_code, 422)
        self.assertEqual(self.post("/hours", self.entry_data(work_date="invalid")).status_code, 422)
        self.assertEqual(self.post("/hours", self.entry_data(description=" ")).status_code, 422)
        self.assertEqual(self.post("/hours", self.entry_data(billable="invalid")).status_code, 422)
        first = self.add_entry(hours="23.50")
        self.assertEqual(self.post("/hours", self.entry_data(hours="0,50")).status_code, 303)
        self.assertEqual(self.post("/hours", self.entry_data(hours="0.01")).status_code, 422)
        self.assertEqual(self.post(f"/hours/{first}/edit", self.entry_data(hours="23.50")).status_code, 303)
        self.assertEqual(self.post(f"/hours/{first}/edit", self.entry_data(hours="24")).status_code, 422)

    def test_inactive_clients_and_projects_cannot_receive_new_hours(self):
        self.connection.execute(update(Project).where(Project.id == self.project_id).values(is_active=False))
        self.assertEqual(self.post("/hours", self.entry_data()).status_code, 422)
        self.connection.execute(update(Client).where(Client.id == self.other_client_id).values(is_active=False))
        self.assertEqual(self.post("/hours", self.entry_data(client_id=str(self.other_client_id), project_id=str(self.other_project_id))).status_code, 422)
        self.assertEqual(self.post("/projects", {"client_id": str(self.other_client_id), "name": "Inválido"}).status_code, 422)

    def test_daily_weekly_totals_and_weekend_preserved(self):
        self.add_entry(hours="1.50", activity="Lunes uno")
        self.add_entry(hours="2.25", activity="Lunes dos")
        self.add_entry(day=self.day + timedelta(days=2), hours="0.10")
        self.add_entry(day=self.day + timedelta(days=5), hours="0.20", activity="Trabajo sábado")
        self.add_entry(day=self.day + timedelta(days=6), hours="1.00", activity="Trabajo domingo")
        self.add_entry(user_id=self.colleague_id, hours="9", activity="Trabajo ajeno")
        self.add_entry(user_id=self.foreign_user_id, organization_id=self.foreign_org_id, client_id=self.foreign_client_id, project_id=self.foreign_project_id, hours="8", activity="Trabajo externo")
        self.add_entry(day=self.day + timedelta(days=7), activity="Siguiente semana")
        response = self.browser.get("/hours/week?week=2025-09-03")
        self.assertEqual(response.status_code, 200)
        self.assertIn('class="daily-total" data-total="3.75"', response.text)
        self.assertIn('id="week-total" data-total="5.05"', response.text)
        for label in ("Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo", "Trabajo sábado", "Trabajo domingo"):
            self.assertIn(label, response.text)
        for private in ("Trabajo ajeno", "Trabajo externo", "Siguiente semana"):
            self.assertNotIn(private, response.text)

    def test_week_navigation_crosses_year_and_defaults_to_current_week(self):
        response = self.browser.get("/hours/week?week=2026-01-01")
        self.assertIn("Del 29/12/2025 al 04/01/2026", response.text)
        for target in ("2025-12-22", "2026-01-05"):
            self.assertIn("/hours/week?week=" + target, response.text)
            self.assertEqual(self.browser.get("/hours/week?week=" + target).status_code, 200)
        current_monday = monday_for(date.today())
        self.assertIn(current_monday.strftime("%d/%m/%Y"), self.browser.get("/hours/week").text)
        self.assertEqual(self.browser.get("/hours/week?week=invalid").status_code, 400)

    def test_dashboard_current_week_total_only_mine(self):
        today = date.today()
        self.add_entry(day=today, hours="2.25")
        self.add_entry(day=monday_for(today) + timedelta(days=6), hours="1.50")
        self.add_entry(user_id=self.colleague_id, day=today, hours="8")
        response = self.browser.get("/dashboard")
        self.assertIn('id="dashboard-week-total">3.75 h', response.text)
        for link in ("/hours", "/hours/week", "/clients", "/projects"):
            self.assertIn('href="' + link + '"', response.text)

    def test_historical_entries_stay_visible_when_project_or_client_inactive(self):
        self.add_entry(activity="Historia conservada")
        self.connection.execute(update(Client).where(Client.id == self.client_id).values(is_active=False))
        self.connection.execute(update(Project).where(Project.id == self.project_id).values(is_active=False))
        response = self.browser.get("/hours/week?week=2025-09-01")
        self.assertIn("Historia conservada", response.text)
        self.assertIn('id="week-total" data-total="2.25"', response.text)

    def test_all_writes_require_csrf_and_all_pages_require_login(self):
        entry_id = self.add_entry()
        posts = (("/clients", {"name": "Inválido"}), ("/projects", {"name": "Inválido", "client_id": str(self.client_id)}),
                 ("/hours", self.entry_data()), (f"/hours/{entry_id}/edit", self.entry_data()), (f"/hours/{entry_id}/delete", {}))
        for path, data in posts:
            self.assertEqual(self.browser.post(path, data=data).status_code, 403)
        self.browser.cookies.clear()
        for path in ("/clients", "/projects", "/hours", "/hours/week", f"/hours/{entry_id}/edit", f"/hours/{entry_id}/delete"):
            self.assertEqual(self.browser.get(path).headers.get("location"), "/login")
        for path, data in posts:
            self.assertEqual(self.browser.post(path, data=data).headers.get("location"), "/login")

    def test_end_to_end_login_client_project_hours_edit_and_week(self):
        self.browser.cookies.clear()
        page = self.browser.get("/login")
        response = self.browser.post("/login", data={"email": self.email, "password": self.password, "csrf": self.token(page)})
        self.assertEqual(response.status_code, 303)
        self.sign_in(self.colleague_id, self.org_id)
        self.assertEqual(self.post("/clients", {"name": "Cliente del flujo"}).status_code, 303)
        self.sign_in(self.user_id, self.org_id)
        client_id = self.connection.scalar(select(Client.id).where(Client.organization_id == self.org_id, Client.name == "Cliente del flujo"))
        self.assertEqual(self.post("/projects", {"client_id": str(client_id), "name": "Expediente del flujo"}).status_code, 303)
        project_id = self.connection.scalar(select(Project.id).where(Project.organization_id == self.org_id, Project.name == "Expediente del flujo"))
        data = self.entry_data(client_id=str(client_id), project_id=str(project_id))
        self.assertEqual(self.post("/hours", data).status_code, 303)
        entry_id = self.connection.scalar(select(TimeEntry.id).where(TimeEntry.organization_id == self.org_id, TimeEntry.user_id == self.user_id))
        response = self.post(f"/hours/{entry_id}/edit", dict(data, hours="1.75"))
        self.assertEqual(response.status_code, 303)
        week = self.browser.get(response.headers["location"])
        self.assertIn("Cliente del flujo", week.text)
        self.assertIn("Expediente del flujo", week.text)
        self.assertIn('id="month-total" data-total="1.75"', week.text)


if __name__ == "__main__":
    unittest.main()
