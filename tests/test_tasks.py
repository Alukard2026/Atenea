"""Tareas multiempresa sobre PostgreSQL, con rollback de todos los datos."""

from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import tasks
from app.models import Client, Organization, Project, Task, User
from app.worklog import FormError
from test_worklog import WorklogCase


class TaskTests(WorklogCase):
    def counts(self):
        return super().counts() + (self.connection.scalar(select(func.count()).select_from(Task)),)

    def data(self, **overrides):
        return {"title": "Enviar informe mensual", "priority": "normal", **overrides}

    def add_task(self, **overrides):
        return self.connection.scalar(Task.__table__.insert().values(
            **{"organization_id": self.org_id, "user_id": self.user_id, "title": "Pendiente de prueba", **overrides}
        ).returning(Task.id))

    def task_row(self, identity):
        return self.connection.execute(select(Task.__table__).where(Task.id == identity)).mappings().one()

    def create(self, **overrides):
        response = self.post("/tasks/new", self.data(**overrides))
        self.assertEqual(response.status_code, 303)
        return self.connection.scalar(select(Task.id).where(Task.user_id == self.user_id).order_by(Task.id.desc()).limit(1))

    def listing(self, **filters):
        response = self.browser.get("/tasks", params=filters)
        self.assertEqual(response.status_code, 200)
        return response

    def ids(self, **filters):
        return [row["task"].id for row in self.listing(**filters).context["rows"]]

    def test_create_task_and_defaults(self):
        identity = self.create(description="Preparar y enviar el informe", client_id=str(self.client_id), project_id=str(self.project_id))
        row = self.task_row(identity)
        self.assertEqual(row["organization_id"], self.org_id)
        self.assertEqual(row["user_id"], self.user_id)
        self.assertEqual(row["status"], "pending")
        self.assertEqual(row["priority"], "normal")
        self.assertEqual(row["client_id"], self.client_id)
        self.assertEqual(row["project_id"], self.project_id)
        self.assertIsNone(row["completed_at"])
        self.assertIsNotNone(row["created_at"].tzinfo)
        self.assertIsNotNone(row["updated_at"].tzinfo)
        self.assertIsNone(row["source_type"])
        self.assertIsNone(row["source_id"])

    def test_create_without_date_time_or_associations(self):
        row = self.task_row(self.create())
        for field in ("due_date", "due_time", "client_id", "project_id", "reminder_at", "description"):
            self.assertIsNone(row[field])

    def test_date_without_hour_does_not_invent_time(self):
        row = self.task_row(self.create(due_date="2026-10-01"))
        self.assertEqual(row["due_date"], date(2026, 10, 1))
        self.assertIsNone(row["due_time"])

    def test_date_with_optional_hour(self):
        row = self.task_row(self.create(due_date="2026-10-01", due_time="14:30"))
        self.assertEqual(row["due_time"], time(14, 30))
        self.assertIn("14:30", self.listing().text)

    def test_reminder_saved_as_aware_utc_and_local_form_roundtrip(self):
        identity = self.create(reminder_at="2026-10-01T09:30")
        self.assertEqual(self.task_row(identity)["reminder_at"], datetime(2026, 10, 1, 15, 30, tzinfo=timezone.utc))
        response = self.browser.get(f"/tasks/{identity}/edit")
        self.assertIn('value="2026-10-01T09:30"', response.text)
        self.assertIn("America/El_Salvador", response.text)

    def test_all_valid_priorities(self):
        for priority in tasks.PRIORITIES:
            identity = self.create(priority=priority)
            self.assertEqual(self.task_row(identity)["priority"], priority)
            self.assertIn(tasks.PRIORITIES[priority].lower(), self.listing().text)

    def test_invalid_priority_rejected(self):
        for priority in ("", "critical", "HIGH", "<script>"):
            self.assertEqual(self.post("/tasks/new", self.data(priority=priority)).status_code, 422)

    def test_invalid_text_date_hour_and_reminder_rejected(self):
        for changes in ({"title": " "}, {"title": "x" * 256}, {"description": "x" * 5001},
                        {"due_date": "2026-02-30"}, {"due_date": "2101-01-01"}, {"due_time": "12:00"},
                        {"due_date": "2026-10-01", "due_time": "25:00"},
                        {"due_date": "2026-10-01", "due_time": "12:00+01:00"},
                        {"reminder_at": "2026-10-01T25:30"}, {"reminder_at": "2026-10-01T09:00Z"}):
            with self.subTest(fields=list(changes)):
                self.assertEqual(self.post("/tasks/new", self.data(**changes)).status_code, 422)

    def test_user_identity_and_external_source_fields_rejected(self):
        for field in ("user_id", "organization_id", "source_type", "source_id", "status", "completed_at"):
            self.assertEqual(self.post("/tasks/new", self.data(**{field: "1"})).status_code, 400)
        for field in ("user_id", "organization_id"):
            self.assertEqual(self.browser.get("/tasks", params={field: "1"}).status_code, 400)

    def test_foreign_and_inactive_client_rejected(self):
        self.assertEqual(self.post("/tasks/new", self.data(client_id=str(self.foreign_client_id))).status_code, 422)
        self.connection.execute(update(Client).where(Client.id == self.client_id).values(is_active=False))
        self.assertEqual(self.post("/tasks/new", self.data(client_id=str(self.client_id))).status_code, 422)
        self.assertNotIn("Cliente externo reservado", self.browser.get("/tasks/new").text)

    def test_project_requires_compatible_client_of_same_organization(self):
        for changes in ({"project_id": str(self.project_id)}, {"client_id": str(self.client_id), "project_id": str(self.other_project_id)},
                        {"client_id": str(self.client_id), "project_id": str(self.foreign_project_id)}):
            self.assertEqual(self.post("/tasks/new", self.data(**changes)).status_code, 422)

    def test_own_task_edit_all_fields(self):
        identity = self.create()
        response = self.post(f"/tasks/{identity}/edit", self.data(title="Título corregido", description="Detalles", due_date="2026-10-02",
                             due_time="15:30", priority="urgent", client_id=str(self.client_id), project_id=str(self.project_id), reminder_at="2026-10-02T10:00"))
        self.assertEqual(response.status_code, 303)
        row = self.task_row(identity)
        self.assertEqual(row["title"], "Título corregido")
        self.assertEqual(row["description"], "Detalles")
        self.assertEqual(row["priority"], "urgent")
        self.assertEqual(row["due_time"], time(15, 30))
        self.assertEqual(row["project_id"], self.project_id)
        self.assertIsNotNone(row["reminder_at"])

    def test_edit_can_clear_optional_fields(self):
        identity = self.create(due_date="2026-10-02", due_time="15:30", reminder_at="2026-10-02T10:00", client_id=str(self.client_id), project_id=str(self.project_id))
        self.assertEqual(self.post(f"/tasks/{identity}/edit", self.data()).status_code, 303)
        for field in ("due_date", "due_time", "reminder_at", "client_id", "project_id"):
            self.assertIsNone(self.task_row(identity)[field])

    def test_cross_user_and_tenant_tasks_invisible_and_immutable(self):
        own = self.add_task(title="Propia")
        colleague = self.add_task(user_id=self.colleague_id, title="Privado colega")
        foreign = self.add_task(user_id=self.foreign_user_id, organization_id=self.foreign_org_id, title="Privado externo")
        self.assertEqual(self.ids(), [own])
        for identity in (colleague, foreign):
            self.assertEqual(self.browser.get(f"/tasks/{identity}/edit").status_code, 404)
            self.assertEqual(self.post(f"/tasks/{identity}/edit", self.data()).status_code, 404)
            self.assertEqual(self.post(f"/tasks/{identity}/status", {"status": "completed"}).status_code, 404)
            self.assertEqual(self.task_row(identity)["status"], "pending")
        self.sign_in(self.colleague_id, self.org_id)
        self.assertEqual(self.ids(), [colleague])
        self.assertEqual(self.browser.get(f"/tasks/{own}/edit").status_code, 404)

    def test_complete_and_repeated_completion_preserves_timestamp(self):
        identity = self.create()
        for _ in range(2):
            self.assertEqual(self.post(f"/tasks/{identity}/status", {"status": "completed"}).status_code, 303)
            row = self.task_row(identity)
            self.assertEqual(row["status"], "completed")
            self.assertIsNotNone(row["completed_at"].tzinfo)
            if _ == 0:
                first = row["completed_at"]
        self.assertEqual(first, row["completed_at"])
        self.assertIn(identity, self.ids(status="completed"))

    def test_reopen_clears_completed_at(self):
        identity = self.create()
        self.post(f"/tasks/{identity}/status", {"status": "completed"})
        self.assertEqual(self.post(f"/tasks/{identity}/status", {"status": "pending"}).status_code, 303)
        self.assertIsNone(self.task_row(identity)["completed_at"])
        self.assertEqual(self.task_row(identity)["status"], "pending")

    def test_cancel_preserves_row_and_can_reopen(self):
        identity = self.create()
        self.assertEqual(self.post(f"/tasks/{identity}/status", {"status": "cancelled"}).status_code, 303)
        self.assertEqual(self.task_row(identity)["status"], "cancelled")
        self.assertIn(identity, self.ids(status="cancelled"))
        self.assertEqual(self.post(f"/tasks/{identity}/status", {"status": "pending"}).status_code, 303)
        self.assertEqual(self.task_row(identity)["status"], "pending")

    def test_invalid_status_and_extra_status_fields_rejected(self):
        identity = self.create()
        self.assertEqual(self.post(f"/tasks/{identity}/status", {"status": "deleted"}).status_code, 400)
        self.assertEqual(self.post(f"/tasks/{identity}/status", {"status": "completed", "completed_at": "2026-01-01"}).status_code, 400)

    def test_classification_all_periods_and_date_only_boundary(self):
        now = datetime(2026, 9, 9, 18, tzinfo=timezone.utc)  # mediodía Guatemala
        zone = ZoneInfo("America/Guatemala")
        examples = [(date(2026, 9, 8), None, "overdue"), (date(2026, 9, 9), time(11), "overdue"),
                    (date(2026, 9, 9), None, "today"), (date(2026, 9, 9), time(13), "today"),
                    (date(2026, 9, 10), None, "tomorrow"), (date(2026, 9, 16), None, "next7"),
                    (date(2026, 9, 17), None, "later"), (None, None, "undated")]
        for due_date, due_time, expected in examples:
            task = SimpleNamespace(due_date=due_date, due_time=due_time)
            self.assertEqual(tasks.period_for(task, now, zone), expected)
        late = datetime(2026, 9, 10, 5, 59, tzinfo=timezone.utc)
        task = SimpleNamespace(due_date=date(2026, 9, 9), due_time=None)
        self.assertEqual(tasks.period_for(task, late, zone), "today")
        self.assertEqual(tasks.period_for(task, late + timedelta(minutes=1), zone), "overdue")

    def test_period_filter_next7_includes_tomorrow_and_day_seven(self):
        now = datetime(2026, 9, 9, 18, tzinfo=timezone.utc)
        tomorrow = self.add_task(due_date=date(2026, 9, 10))
        seventh = self.add_task(due_date=date(2026, 9, 16))
        self.add_task(due_date=date(2026, 9, 17))
        undated = self.add_task()
        with patch("app.tasks.utc_now", return_value=now):
            self.assertEqual(set(self.ids(period="next7")), {tomorrow, seventh})
            self.assertEqual(self.ids(period="undated"), [undated])
            self.assertEqual(self.ids(period="tomorrow"), [tomorrow])

    def test_status_priority_client_and_combined_filters(self):
        urgent = self.add_task(priority="urgent", client_id=self.client_id)
        self.add_task(priority="normal", client_id=self.other_client_id)
        completed = self.add_task(status="completed", completed_at=tasks.utc_now(), client_id=self.client_id)
        self.assertEqual(self.ids(priority="urgent"), [urgent])
        self.assertEqual(self.ids(status="completed"), [completed])
        self.assertEqual(set(self.ids(client_id=str(self.client_id))), {urgent, completed})
        self.assertEqual(self.ids(status="pending", priority="urgent", client_id=str(self.client_id)), [urgent])

    def test_pending_first_and_history_retained(self):
        old = self.add_task(due_date=date(2020, 1, 1), status="completed", completed_at=tasks.utc_now())
        pending = self.add_task(due_date=date(2030, 1, 1))
        cancelled = self.add_task(status="cancelled")
        self.assertEqual(self.ids(), [pending, old, cancelled])

    def test_filters_reject_invalid_values_and_foreign_client(self):
        for filters in ({"status": "bad"}, {"priority": "bad"}, {"period": "bad"}, {"client_id": str(self.foreign_client_id)}):
            self.assertEqual(self.browser.get("/tasks", params=filters).status_code, 400)

    def test_due_reminder_helper_scoped_pending_aware_and_active(self):
        now = datetime(2026, 9, 9, 18, tzinfo=timezone.utc)
        due = self.add_task(reminder_at=now)
        self.add_task(reminder_at=now + timedelta(seconds=1))
        self.add_task(reminder_at=now - timedelta(days=1), status="completed", completed_at=now)
        self.add_task(reminder_at=now, status="cancelled")
        self.add_task(reminder_at=now, user_id=self.colleague_id)
        self.add_task(reminder_at=now, user_id=self.foreign_user_id, organization_id=self.foreign_org_id)
        with Session(bind=self.connection, join_transaction_mode="create_savepoint") as db:
            user = db.get(User, self.user_id)
            self.assertEqual([task.id for task in tasks.due_reminders(db, user, now=now)], [due])
            self.assertEqual([task.id for task in tasks.due_reminders(db, user, now=now)], [due])
            with self.assertRaises(ValueError):
                tasks.due_reminders(db, user, now=now.replace(tzinfo=None))
            self.connection.execute(update(User).where(User.id == self.user_id).values(is_active=False))
            self.assertEqual(tasks.due_reminders(db, user, now=now), [])

    def test_dashboard_counts_are_personal_pending_and_scoped(self):
        now = datetime(2026, 9, 9, 18, tzinfo=timezone.utc)
        self.add_task(due_date=date(2026, 9, 8))
        self.add_task(due_date=date(2026, 9, 9))
        self.add_task(due_date=date(2026, 9, 16))
        self.add_task(due_date=date(2026, 9, 9), status="cancelled")
        self.add_task(due_date=date(2026, 9, 9), user_id=self.colleague_id)
        self.add_task(due_date=date(2026, 9, 9), user_id=self.foreign_user_id, organization_id=self.foreign_org_id)
        with patch("app.tasks.utc_now", return_value=now):
            response = self.browser.get("/dashboard")
        self.assertEqual(response.context["task_summary"], {"overdue": 1, "today": 1, "upcoming": 1})
        self.assertIn('href="/tasks"', response.text)

    def test_all_posts_require_csrf_and_no_get_changes_status(self):
        identity = self.create()
        for path, data in (("/tasks/new", self.data()), (f"/tasks/{identity}/edit", self.data()),
                           (f"/tasks/{identity}/status", {"status": "completed"})):
            for csrf in (None, "wrong"):
                self.assertEqual(self.browser.post(path, data={**data, **({"csrf": csrf} if csrf else {})}).status_code, 403)
        self.assertEqual(self.browser.get(f"/tasks/{identity}/status").status_code, 405)
        self.assertEqual(self.task_row(identity)["status"], "pending")

    def test_tasks_require_active_session(self):
        identity = self.create()
        self.browser.cookies.clear()
        for path in ("/tasks", "/tasks/new", f"/tasks/{identity}/edit"):
            self.assertEqual(self.browser.get(path).headers.get("location"), "/login")
        self.assertEqual(self.browser.post("/tasks/new", data=self.data()).headers.get("location"), "/login")
        self.connection.execute(update(Organization).where(Organization.id == self.org_id).values(is_active=False))
        self.sign_in(self.user_id, self.org_id)
        self.assertEqual(self.browser.get("/tasks").headers.get("location"), "/login")

    def test_archived_associations_retained_but_not_newly_selected(self):
        identity = self.create(client_id=str(self.client_id), project_id=str(self.project_id))
        self.connection.execute(update(Client).where(Client.id == self.client_id).values(is_active=False))
        self.connection.execute(update(Project).where(Project.id == self.project_id).values(is_active=False))
        self.assertEqual(self.post(f"/tasks/{identity}/edit", self.data(client_id=str(self.client_id), project_id=str(self.project_id))).status_code, 303)
        self.assertIn(identity, self.ids(client_id=str(self.client_id)))
        self.assertEqual(self.post("/tasks/new", self.data(client_id=str(self.client_id), project_id=str(self.project_id))).status_code, 422)

    def test_html_escapes_title_description_and_preserves_unicode(self):
        identity = self.create(title='<script>alert("x")</script>', description="Revisión jurídica: José 日本語")
        text = self.listing().text
        self.assertNotIn('<script>alert("x")</script>', text)
        self.assertIn("&lt;script&gt;", text)
        self.assertIn("Revisión jurídica: José 日本語", text)

    def test_dst_ambiguous_and_nonexistent_hours_rejected(self):
        zone = ZoneInfo("America/New_York")
        for value in (datetime(2026, 3, 8, 2, 30), datetime(2026, 11, 1, 1, 30)):
            with self.assertRaises(FormError):
                tasks.local_instant(value, zone, "reminder_at")
        self.assertEqual(tasks.local_instant(datetime(2026, 7, 1, 12), zone, "reminder_at"), datetime(2026, 7, 1, 16, tzinfo=timezone.utc))

    def test_database_constraints_prevent_cross_tenant_and_invalid_states(self):
        cases = ({"user_id": self.foreign_user_id}, {"client_id": self.foreign_client_id},
                 {"client_id": self.client_id, "project_id": self.other_project_id}, {"project_id": self.project_id},
                 {"priority": "invalid"}, {"status": "invalid"}, {"status": "completed"},
                 {"completed_at": tasks.utc_now()}, {"due_time": time(10)}, {"title": " "}, {"source_type": "external"})
        for values in cases:
            with self.assertRaises(IntegrityError):
                with self.connection.begin_nested():
                    self.add_task(**values)

    def test_external_reference_preserved_on_web_edit(self):
        identity = self.add_task(source_type="external", source_id="opaque-reference")
        self.assertEqual(self.post(f"/tasks/{identity}/edit", self.data()).status_code, 303)
        self.assertEqual(self.task_row(identity)["source_type"], "external")
        self.assertEqual(self.task_row(identity)["source_id"], "opaque-reference")
