"""Avisos sobre PostgreSQL: reloj fijo y rollback de todos los datos."""

from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy import select, update

from app.models import Organization, Task
from app import tasks
from test_worklog import WorklogCase


class NotificationTests(WorklogCase):
    NOW = datetime(2026, 9, 28, 5, 30, tzinfo=timezone.utc)

    def setUp(self):
        super().setUp()
        clock = patch("app.tasks.utc_now", return_value=self.NOW)
        clock.start()
        self.addCleanup(clock.stop)

    def add_task(self, **changes):
        return self.connection.scalar(Task.__table__.insert().values(**{
            "organization_id": self.org_id, "user_id": self.user_id,
            "title": "Recordatorio privado", "reminder_at": self.NOW - timedelta(minutes=5), **changes,
        }).returning(Task.id))

    def row(self, identity):
        return self.connection.execute(select(Task.__table__).where(Task.id == identity)).mappings().one()

    def test_authentication_on_pages_status_and_actions(self):
        self.browser.cookies.clear()
        for path in ("/notifications", "/notifications/status"):
            self.assertEqual(self.browser.get(path).headers.get("location"), "/login")
        for action in ("snooze", "complete"):
            self.assertEqual(self.browser.post(f"/notifications/1/{action}").status_code, 303)

    def test_empty_center_and_status(self):
        self.assertIn("Sin recordatorios pendientes", self.browser.get("/notifications").text)
        self.assertEqual(self.browser.get("/notifications/status").json(), {"pending": 0, "overdue": 0, "next_at": None})

    def test_private_rows_and_counter_including_admin(self):
        own = self.add_task(title="Mi aviso")
        other = self.add_task(user_id=self.colleague_id, title="Aviso reservado del colega")
        foreign = self.add_task(user_id=self.foreign_user_id, organization_id=self.foreign_org_id, title="Aviso de otra empresa")
        response = self.browser.get("/notifications")
        self.assertEqual([r["task"].id for r in response.context["rows"]], [own])
        self.assertNotIn("Aviso reservado", response.text)
        self.assertNotIn("Aviso de otra empresa", response.text)
        self.assertEqual(self.browser.get("/notifications/status").json()["pending"], 1)
        for identity in (other, foreign):
            for action, data in (("snooze", {"delay": "15m"}), ("complete", {})):
                self.assertEqual(self.post(f"/notifications/{identity}/{action}", data).status_code, 404)
            self.assertEqual(self.row(identity)["status"], "pending")
        self.sign_in(self.colleague_id, self.org_id)
        self.assertEqual([r["task"].id for r in self.browser.get("/notifications").context["rows"]], [other])
        self.assertEqual(self.post(f"/notifications/{own}/complete").status_code, 404)
        self.sign_in(self.foreign_user_id, self.foreign_org_id)
        self.assertEqual([r["task"].id for r in self.browser.get("/notifications").context["rows"]], [foreign])

    def test_counter_boundaries_and_terminal_tasks(self):
        for offset in (-1, 0, 15, 16, 24 * 60):
            self.add_task(reminder_at=self.NOW + timedelta(minutes=offset))
        self.add_task(reminder_at=None)
        self.add_task(status="cancelled")
        self.add_task(status="completed", completed_at=self.NOW)
        self.assertEqual(self.browser.get("/notifications/status").json(), {
            "pending": 3, "overdue": 1, "next_at": self.NOW.isoformat(),
        })
        self.assertEqual(len(self.browser.get("/notifications").context["rows"]), 5)

    def test_order_overdue_then_urgent_then_nearest(self):
        late = self.add_task(priority="normal", reminder_at=self.NOW - timedelta(days=1))
        urgent_late = self.add_task(priority="urgent")
        near = self.add_task(reminder_at=self.NOW + timedelta(minutes=1))
        urgent = self.add_task(priority="urgent", reminder_at=self.NOW + timedelta(days=2))
        rows = self.browser.get("/notifications").context["rows"]
        self.assertEqual([row["task"].id for row in rows], [urgent_late, late, urgent, near])
        self.assertEqual([row["category"] for row in rows], ["Vencido", "Vencido", "Próximo", "Hoy"])

    def test_today_and_dates_use_organization_zone(self):
        self.add_task(reminder_at=self.NOW + timedelta(minutes=15))
        self.add_task(reminder_at=self.NOW + timedelta(hours=1))
        response = self.browser.get("/notifications")
        self.assertEqual([row["category"] for row in response.context["rows"]], ["Hoy", "Próximo"])
        self.assertIn("27/09/2026 23:45", response.text)
        self.connection.execute(update(Organization).where(Organization.id == self.org_id).values(timezone="Asia/Tokyo"))
        response = self.browser.get("/notifications")
        self.assertEqual([row["category"] for row in response.context["rows"]], ["Hoy", "Hoy"])
        self.assertIn("28/09/2026 14:45", response.text)

    def test_metadata_and_html_escaping(self):
        self.add_task(title='<script>alert("x")</script>', priority="urgent", client_id=self.client_id, project_id=self.project_id)
        response = self.browser.get("/notifications")
        for label in ("Cliente principal", "Expediente principal", "Urgente", "Pendiente", "&lt;script&gt;"):
            self.assertIn(label, response.text)
        self.assertNotIn('<script>alert("x")', response.text)

    def test_snooze_15_minutes(self):
        identity = self.add_task(due_date=date(2026, 9, 28))
        self.assertEqual(self.post(f"/notifications/{identity}/snooze", {"delay": "15m"}).status_code, 303)
        self.assertEqual(self.row(identity)["reminder_at"], self.NOW + timedelta(minutes=15))
        self.assertEqual(self.row(identity)["due_date"], date(2026, 9, 28))

    def test_snooze_one_hour(self):
        identity = self.add_task()
        self.assertEqual(self.post(f"/notifications/{identity}/snooze", {"delay": "1h"}).status_code, 303)
        self.assertEqual(self.row(identity)["reminder_at"], self.NOW + timedelta(hours=1))
        self.assertEqual(self.browser.get("/notifications/status").json()["pending"], 0)

    def test_snooze_tomorrow_local_nine(self):
        identity = self.add_task()
        self.assertEqual(self.post(f"/notifications/{identity}/snooze", {"delay": "tomorrow"}).status_code, 303)
        self.assertEqual(self.row(identity)["reminder_at"], datetime(2026, 9, 28, 15, tzinfo=timezone.utc))

    def test_tomorrow_respects_dst_calendar_not_24_hours(self):
        self.connection.execute(update(Organization).where(Organization.id == self.org_id).values(timezone="America/New_York"))
        identity = self.add_task()
        with patch("app.tasks.utc_now", return_value=datetime(2026, 3, 7, 17, tzinfo=timezone.utc)):
            self.assertEqual(self.post(f"/notifications/{identity}/snooze", {"delay": "tomorrow"}).status_code, 303)
        self.assertEqual(self.row(identity)["reminder_at"], datetime(2026, 3, 8, 13, tzinfo=timezone.utc))

    def test_csrf_required_on_both_actions(self):
        identity = self.add_task()
        original = self.row(identity)["reminder_at"]
        for action in ("snooze", "complete"):
            for data in ({}, {"csrf": "invalid"}):
                self.assertEqual(self.browser.post(f"/notifications/{identity}/{action}", data=data).status_code, 403)
        self.assertEqual(self.row(identity)["reminder_at"], original)
        self.assertEqual(self.row(identity)["status"], "pending")

    def test_untrusted_fields_query_and_delay_rejected(self):
        identity = self.add_task()
        for field in ("user_id", "organization_id", "title", "reminder_at"):
            self.assertEqual(self.post(f"/notifications/{identity}/snooze", {"delay": "1h", field: "1"}).status_code, 400)
            self.assertEqual(self.post(f"/notifications/{identity}/complete", {field: "1"}).status_code, 400)
            for path in ("/notifications", "/notifications/status"):
                self.assertEqual(self.browser.get(path, params={field: "1"}).status_code, 400)
        self.assertEqual(self.post(f"/notifications/{identity}/snooze", {"delay": "forever"}).status_code, 400)
        self.assertEqual(self.browser.get("/notifications?page=1&page=2").status_code, 400)
        self.assertEqual(self.post(f"/notifications/{identity}/complete?user_id=1").status_code, 400)

    def test_complete_uses_existing_logic_and_is_idempotent(self):
        identity = self.add_task()
        with patch("app.routers.notifications.tasks.set_status", wraps=tasks.set_status) as complete:
            self.assertEqual(self.post(f"/notifications/{identity}/complete").status_code, 303)
            complete.assert_called_once()
        completed_at = self.row(identity)["completed_at"]
        self.assertEqual(self.post(f"/notifications/{identity}/complete").status_code, 303)
        self.assertEqual(self.row(identity)["completed_at"], completed_at)
        self.assertEqual(self.browser.get("/notifications/status").json()["pending"], 0)

    def test_complete_recurring_task_generates_one_successor(self):
        identity = self.add_task(due_date=date(2026, 9, 28), recurrence_type="daily", recurrence_anchor_date=date(2026, 9, 28))
        for _ in range(2):
            self.assertEqual(self.post(f"/notifications/{identity}/complete").status_code, 303)
        children = self.connection.execute(select(Task.__table__).where(Task.parent_task_id == identity)).mappings().all()
        self.assertEqual(len(children), 1)
        self.assertEqual(children[0]["due_date"], date(2026, 9, 29))
        self.assertEqual(children[0]["reminder_at"], self.row(identity)["reminder_at"] + timedelta(days=1))
        self.assertEqual(children[0]["user_id"], self.user_id)
        self.assertEqual(children[0]["organization_id"], self.org_id)

    def test_snooze_rejects_terminal_or_removed_reminder(self):
        for changes in ({"status": "cancelled"}, {"status": "completed", "completed_at": self.NOW}, {"reminder_at": None}):
            identity = self.add_task(**changes)
            self.assertEqual(self.post(f"/notifications/{identity}/snooze", {"delay": "15m"}).status_code, 409)
        self.assertEqual(self.post(f"/notifications/{identity}/complete").status_code, 409)

    def test_status_minimal_private_no_store_and_read_only(self):
        identity = self.add_task(title="Texto confidencial", description="Descripción privada")
        original = dict(self.row(identity))
        for _ in range(2):
            result = self.browser.get("/notifications/status")
            self.assertEqual(set(result.json()), {"pending", "overdue", "next_at"})
            self.assertEqual(result.headers["cache-control"], "no-store")
            self.assertNotIn("confidencial", result.text)
            self.assertNotIn("privada", result.text)
            self.assertEqual(self.browser.get("/notifications").status_code, 200)
        self.assertEqual(dict(self.row(identity)), original)

    def test_center_paginates_without_losing_rows(self):
        identities = [self.add_task() for _ in range(31)]
        first = self.browser.get("/notifications")
        second = self.browser.get("/notifications?page=2")
        self.assertEqual([row["task"].id for row in first.context["rows"] + second.context["rows"]], identities)
        self.assertEqual(self.browser.get("/notifications/status").json()["pending"], 31)
        for page in (0, -1, 3, 2**80):
            self.assertEqual(self.browser.get(f"/notifications?page={page}").status_code, 404)

    def test_navigation_permissions_and_existing_routes(self):
        for path in ("/dashboard", "/tasks", "/hours", "/hours/history", "/hours/week", "/clients", "/projects", "/reports/month", "/mail", "/integrations/microsoft"):
            response = self.browser.get(path)
            self.assertEqual(response.status_code, 200, path)
            self.assertIn('href="/notifications"', response.text)
            self.assertNotIn('href="/settings/timezone"', response.text)
            self.assertNotIn('href="/settings/workdays"', response.text)
        self.assertEqual(self.browser.get("/settings/timezone").status_code, 403)
        self.sign_in(self.colleague_id, self.org_id)
        for path in ("/settings/timezone", "/settings/workdays"):
            response = self.browser.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertIn('href="/settings/timezone"', response.text)
            self.assertIn('href="/settings/workdays"', response.text)

    def test_navigation_active_section_accessible_markup(self):
        for path, label in (("/mail", "Bandeja de correo"), ("/tasks/new", "Tareas"), ("/hours/week", "Reporte semanal"), ("/notifications", "Avisos")):
            response = self.browser.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertIn('aria-current="page"', response.text)
            self.assertIn(label, response.text)
            self.assertIn('aria-controls="workspace-navigation"', response.text)
            self.assertIn('type="button" class="nav-trigger"', response.text)
            self.assertIn('/static/workspace.js', response.text)
            self.assertIn("connect-src 'self'", response.headers["content-security-policy"])
        self.assertIn('class="nav-group nav-active"', self.browser.get("/mail").text)

    def test_dashboard_pending_reminders_microsoft_and_timezone_hours(self):
        self.add_task(reminder_at=self.NOW + timedelta(minutes=10))
        self.add_task(user_id=self.colleague_id)
        # UTC lunes; en El Salvador todavía es domingo de la semana anterior.
        self.add_entry(day=date(2026, 9, 27), hours="2.00")
        self.add_entry(day=date(2026, 9, 28), hours="5.00")
        response = self.browser.get("/dashboard")
        self.assertEqual(response.context["pending_tasks"], 1)
        self.assertEqual(response.context["notification_status"]["pending"], 1)
        self.assertFalse(response.context["microsoft_connected"])
        self.assertIn('id="dashboard-week-total">2.00 h', response.text)
        self.assertIn("27/09 23:40", response.text)
