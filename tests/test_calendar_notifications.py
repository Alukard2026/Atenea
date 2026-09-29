"""Recordatorios combinados sobre PostgreSQL, con reloj fijo y rollback."""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

from sqlalchemy import select, update

from app.models import CalendarEvent, Organization, Task
from test_worklog import WorklogCase


class CalendarNotificationTests(WorklogCase):
    NOW = datetime(2026, 10, 5, 15, tzinfo=timezone.utc)

    def setUp(self):
        super().setUp()
        clock = patch("app.tasks.utc_now", return_value=self.NOW)
        clock.start()
        self.addCleanup(clock.stop)

    def event(self, **changes):
        return self.connection.scalar(CalendarEvent.__table__.insert().values(**{
            "organization_id": self.org_id, "user_id": self.user_id,
            "event_type": "hearing", "title": "Audiencia privada",
            "start_at": self.NOW + timedelta(hours=1),
            "reminder_at": self.NOW - timedelta(minutes=5), **changes,
        }).returning(CalendarEvent.id))

    def task(self, **changes):
        return self.connection.scalar(Task.__table__.insert().values(**{
            "organization_id": self.org_id, "user_id": self.user_id,
            "title": "Tarea privada", "reminder_at": self.NOW, **changes,
        }).returning(Task.id))

    def row(self, identity):
        return dict(self.connection.execute(select(CalendarEvent.__table__).where(CalendarEvent.id == identity)).mappings().one())

    def snooze(self, identity, delay="1h", **extra):
        return self.post(f"/notifications/events/{identity}/snooze", {"delay": delay, **extra})

    def test_center_combines_types_and_escapes_private_metadata(self):
        event = self.event(title='<script>alert("event")</script>', client_id=self.client_id, project_id=self.project_id)
        task = self.task()
        response = self.browser.get("/notifications")
        self.assertEqual([(r["kind"], r["item"].id) for r in response.context["rows"]], [("event", event), ("task", task)])
        for label in ("Audiencia", "Programado", "Cliente principal", "Expediente principal", "&lt;script&gt;", f'/calendar/{event}', f'/notifications/events/{event}/snooze', f'/notifications/{task}/complete'):
            self.assertIn(label, response.text)
        self.assertNotIn('<script>alert("event")', response.text)
        self.assertNotIn(f'/notifications/events/{event}/complete', response.text)

    def test_status_boundaries_ignore_cancelled_and_unset_reminders(self):
        for offset in (-1, 0, 15, 16):
            self.event(reminder_at=self.NOW + timedelta(minutes=offset))
        self.event(status="cancelled")
        self.event(reminder_at=None)
        self.task()
        self.assertEqual(self.browser.get("/notifications/status").json(), {
            "pending": 4, "overdue": 1, "next_at": self.NOW.isoformat(),
        })
        self.assertEqual(self.browser.get("/notifications").context["total"], 5)

    def test_owner_and_organization_scope_includes_admin(self):
        own = self.event()
        colleague = self.event(user_id=self.colleague_id, title="Reservado del colega")
        foreign = self.event(user_id=self.foreign_user_id, organization_id=self.foreign_org_id, title="Reservado otra organización")
        for identity, user, org in ((own, self.user_id, self.org_id), (colleague, self.colleague_id, self.org_id), (foreign, self.foreign_user_id, self.foreign_org_id)):
            self.sign_in(user, org)
            response = self.browser.get("/notifications")
            self.assertEqual([r["event"].id for r in response.context["rows"]], [identity])
            self.assertEqual(self.browser.get("/notifications/status").json()["pending"], 1)
            for other in {own, colleague, foreign} - {identity}:
                before = self.row(other)
                self.assertEqual(self.snooze(other).status_code, 404)
                self.assertEqual(self.row(other), before)

    def test_combined_order_and_pagination_without_duplicate_identity(self):
        # Igual ID en tablas distintas no debe colisionar ni compartir acciones.
        event = self.event()
        task = self.task(id=event, priority="urgent", reminder_at=self.NOW - timedelta(minutes=1))
        future = [self.event(reminder_at=self.NOW + timedelta(minutes=i + 1)) for i in range(30)]
        first = self.browser.get("/notifications")
        second = self.browser.get("/notifications?page=2")
        rows = first.context["rows"] + second.context["rows"]
        self.assertEqual([(r["kind"], r["item"].id) for r in rows], [("task", task), ("event", event)] + [("event", i) for i in future])
        self.assertEqual(first.context["total"], 32)
        self.assertEqual(len(first.context["rows"]), 30)
        self.assertEqual(self.browser.get("/notifications?page=3").status_code, 404)

    def test_snooze_delays_only_reminder_and_updated_at(self):
        identity = self.event()
        for choice, delta in (("15m", timedelta(minutes=15)), ("1h", timedelta(hours=1)), ("tomorrow", timedelta(days=1))):
            before = self.row(identity)
            response = self.snooze(identity, choice)
            self.assertEqual(response.status_code, 303)
            self.assertEqual(response.headers["location"], "/notifications")
            after = self.row(identity)
            self.assertEqual(after["reminder_at"], self.NOW + delta)
            for key in before.keys() - {"reminder_at", "updated_at"}:
                self.assertEqual(after[key], before[key], key)

    def test_tomorrow_respects_dst_and_organization_zone(self):
        self.connection.execute(update(Organization).where(Organization.id == self.org_id).values(timezone="America/New_York"))
        identity = self.event()
        with patch("app.tasks.utc_now", return_value=datetime(2026, 3, 7, 17, tzinfo=timezone.utc)):
            self.assertEqual(self.snooze(identity, "tomorrow").status_code, 303)
        self.assertEqual(self.row(identity)["reminder_at"], datetime(2026, 3, 8, 13, tzinfo=timezone.utc))
        self.assertIn("08/03/2026 09:00", self.browser.get("/notifications").text)

    def test_snooze_requires_authentication_csrf_and_post(self):
        identity = self.event()
        path = f"/notifications/events/{identity}/snooze"
        for data in ({"delay": "1h"}, {"delay": "1h", "csrf": "invalid"}):
            self.assertEqual(self.browser.post(path, data=data).status_code, 403)
        self.assertEqual(self.browser.get(path).status_code, 405)
        self.browser.cookies.clear()
        self.assertEqual(self.browser.post(path).headers["location"], "/login")

    def test_untrusted_fields_queries_and_delay_rejected(self):
        identity = self.event()
        before = self.row(identity)
        for key in ("user_id", "organization_id", "status", "start_at", "reminder_at"):
            self.assertEqual(self.snooze(identity, **{key: "1"}).status_code, 400)
        for delay in ("", "forever"):
            self.assertEqual(self.snooze(identity, delay).status_code, 400)
        self.assertEqual(self.post(f"/notifications/events/{identity}/snooze?user_id=1", {"delay": "1h"}).status_code, 400)
        self.assertEqual(self.row(identity), before)

    def test_missing_cancelled_or_cleared_event_cannot_be_snoozed(self):
        for changes in ({"status": "cancelled"}, {"reminder_at": None}):
            self.assertEqual(self.snooze(self.event(**changes)).status_code, 409)
        for identity in (0, -1, 2**80):
            self.assertEqual(self.snooze(identity).status_code, 404)

    def test_cancel_removes_event_from_center_and_counter(self):
        identity = self.event()
        self.assertEqual(self.post(f"/calendar/{identity}/cancel").status_code, 303)
        self.assertIsNone(self.row(identity)["reminder_at"])
        self.assertEqual(self.browser.get("/notifications").context["total"], 0)
        self.assertEqual(self.browser.get("/notifications/status").json()["pending"], 0)
        self.assertEqual(self.snooze(identity).status_code, 409)

    def test_gets_are_read_only_and_status_contains_no_metadata(self):
        identity = self.event(title="Confidencial", description="Nota privada")
        before = self.row(identity)
        for _ in range(2):
            response = self.browser.get("/notifications/status")
            self.assertEqual(set(response.json()), {"pending", "overdue", "next_at"})
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertNotIn("Confidencial", response.text)
            self.assertNotIn("Nota privada", response.text)
            self.assertEqual(self.browser.get("/notifications").status_code, 200)
        self.assertEqual(self.row(identity), before)

    def test_dashboard_uses_combined_counter_and_new_event_action(self):
        self.event()
        self.task()
        self.event(user_id=self.colleague_id)
        response = self.browser.get("/dashboard")
        self.assertEqual(response.context["notification_status"]["pending"], 2)
        self.assertIn('href="/calendar/new">Nuevo evento', response.text)

    def test_edit_preserves_snoozed_reminder_after_start_and_can_clear_it(self):
        identity = self.event()
        precise_now = (self.NOW + timedelta(hours=1)).replace(second=32, microsecond=123456)
        with patch("app.tasks.utc_now", return_value=precise_now):
            self.assertEqual(self.snooze(identity, "1h").status_code, 303)
        reminder = self.row(identity)["reminder_at"]
        self.assertEqual(reminder.second, 32)
        data = {"event_type": "hearing", "title": "Título corregido", "date": "2026-10-05", "start_time": "10:00", "reminder_at": reminder.astimezone(ZoneInfo("America/El_Salvador")).strftime("%Y-%m-%dT%H:%M")}
        self.assertEqual(self.post(f"/calendar/{identity}/edit", data).status_code, 303)
        self.assertEqual(self.row(identity)["reminder_at"], reminder)
        # Una nueva fecha posterior al inicio sigue rechazándose en el formulario.
        self.assertEqual(self.post(f"/calendar/{identity}/edit", {**data, "reminder_at": "2026-10-07T12:00"}).status_code, 422)
        self.assertEqual(self.post(f"/calendar/{identity}/edit", {**data, "reminder_at": ""}).status_code, 303)
        self.assertEqual(self.browser.get("/notifications").context["total"], 0)

    def test_snooze_updates_calendar_feed_without_changing_event_start(self):
        identity = self.event()
        self.assertEqual(self.snooze(identity, "tomorrow").status_code, 303)
        response = self.browser.get("/calendar/events", params={"start": "2026-10-01", "end": "2026-11-01"})
        entries = {r["id"]: r for r in response.json()}
        self.assertEqual(datetime.fromisoformat(entries[f"event-{identity}"]["start"]), self.NOW + timedelta(hours=1))
        self.assertEqual(datetime.fromisoformat(entries[f"event-{identity}-reminder"]["start"]), self.NOW + timedelta(days=1))
