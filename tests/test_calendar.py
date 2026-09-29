"""Calendario sobre PostgreSQL; transacciones de pruebas revertidas."""
from datetime import date, datetime, time, timedelta, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import calendar
from app.models import CalendarEvent, Client, Organization, Task, User
from test_worklog import WorklogCase


class CalendarTests(WorklogCase):
    def data(self, **changes):
        return {"event_type": "hearing", "title": "Audiencia de prueba", "date": "2026-10-05", "start_time": "10:00", "end_time": "11:00", **changes}

    def create(self, **changes):
        response = self.post("/calendar/new", self.data(**changes))
        self.assertEqual(response.status_code, 303, response.text[:100])
        return int(response.headers["location"].rsplit("/", 1)[1])

    def row(self, identity):
        return self.connection.execute(select(CalendarEvent.__table__).where(CalendarEvent.id == identity)).mappings().one()

    def feed(self, **changes):
        return self.browser.get("/calendar/events", params={"start": "2026-10-01T00:00:00-06:00", "end": "2026-11-01T00:00:00-06:00", **changes})

    def task(self, **changes):
        return self.connection.scalar(Task.__table__.insert().values(**{"organization_id": self.org_id, "user_id": self.user_id, "title": "Tarea existente", "due_date": date(2026, 10, 5), **changes}).returning(Task.id))

    def test_requires_login(self):
        self.browser.cookies.clear()
        for path in ("/calendar", "/calendar/events", "/calendar/new", "/calendar/1", "/calendar/tasks/1"):
            self.assertEqual(self.browser.get(path).status_code, 303)

    def test_create_hearing_and_utc_storage(self):
        identity = self.create(client_id=str(self.client_id), project_id=str(self.project_id), location="Sala 2", institution="Tribunal", description="Preparar documentos", reminder_at="2026-10-05T09:00")
        row = self.row(identity)
        self.assertEqual((row["organization_id"], row["user_id"]), (self.org_id, self.user_id))
        self.assertEqual(row["start_at"], datetime(2026, 10, 5, 16, tzinfo=timezone.utc))
        self.assertEqual(row["end_at"], datetime(2026, 10, 5, 17, tzinfo=timezone.utc))
        self.assertEqual(row["reminder_at"], datetime(2026, 10, 5, 15, tzinfo=timezone.utc))
        self.assertIsNone(row["source_id"])
        detail = self.browser.get(f"/calendar/{identity}")
        self.assertIn("05/10/2026 10:00", detail.text)
        for value in ("Audiencia", "Sala 2", "Tribunal", "Preparar documentos", "Cliente principal"):
            self.assertIn(value, detail.text)

    def test_supported_event_types(self):
        for kind in calendar.EVENT_TYPES:
            identity = self.create(event_type=kind)
            self.assertIn(f"event-{identity}", [e["id"] for e in self.feed().json()])
        self.assertEqual(self.post("/calendar/new", self.data(event_type="task")).status_code, 422)
        self.assertEqual(self.post("/calendar/new", self.data(event_type="unknown")).status_code, 422)

    def test_task_all_day_and_timed(self):
        first = self.task()
        second = self.task(due_time=time(9, 30), priority="urgent", client_id=self.client_id, project_id=self.project_id)
        entries = {e["id"]: e for e in self.feed().json()}
        self.assertTrue(entries[f"task-{first}"]["allDay"])
        self.assertEqual(entries[f"task-{first}"]["start"], "2026-10-05")
        timed = entries[f"task-{second}"]
        self.assertFalse(timed["allDay"])
        self.assertEqual(datetime.fromisoformat(timed["start"]), datetime(2026, 10, 5, 15, 30, tzinfo=timezone.utc))
        self.assertEqual(timed["extendedProps"]["priority"], "urgent")
        self.assertEqual(timed["extendedProps"]["client"], "Cliente principal")
        self.assertEqual(timed["extendedProps"]["project"], "Expediente principal")

    def test_reminder_different_and_same_instant(self):
        same = self.task(due_time=time(10), reminder_at=datetime(2026, 10, 5, 16, tzinfo=timezone.utc))
        other = self.task(due_time=time(10), reminder_at=datetime(2026, 10, 5, 15, tzinfo=timezone.utc))
        ids = [e["id"] for e in self.feed().json()]
        self.assertNotIn(f"task-{same}-reminder", ids)
        self.assertIn(f"task-{other}-reminder", ids)

    def test_undated_task_with_reminder(self):
        identity = self.task(due_date=None, reminder_at=datetime(2026, 10, 5, 15, tzinfo=timezone.utc))
        self.assertEqual([e["id"] for e in self.feed().json()], [f"task-{identity}-reminder"])

    def test_isolation_user_and_admin(self):
        identity = self.create()
        task_id = self.task()
        self.sign_in(self.colleague_id, self.org_id)
        self.assertEqual(self.feed().json(), [])
        for path in (f"/calendar/{identity}", f"/calendar/{identity}/edit", f"/calendar/tasks/{task_id}"):
            self.assertEqual(self.browser.get(path).status_code, 404)
        for action in ("edit", "cancel"):
            self.assertEqual(self.post(f"/calendar/{identity}/{action}", self.data() if action == "edit" else {}).status_code, 404)

    def test_organization_isolation(self):
        identity = self.create()
        self.sign_in(self.foreign_user_id, self.foreign_org_id)
        self.assertEqual(self.feed().json(), [])
        self.assertEqual(self.browser.get(f"/calendar/{identity}").status_code, 404)
        self.assertEqual(self.post(f"/calendar/{identity}/cancel").status_code, 404)
        self.assertEqual(self.post(f"/calendar/{identity}/edit", self.data()).status_code, 404)

    def test_forged_owner_and_unknown_fields(self):
        for field in ("user_id", "organization_id", "source_id", "source_type", "status"):
            self.assertEqual(self.post("/calendar/new", self.data(**{field: "1"})).status_code, 400)
            self.assertEqual(self.feed(**{field: "1"}).status_code, 400)

    def test_create_edit_cancel_require_csrf(self):
        identity = self.create()
        for path in ("/calendar/new", f"/calendar/{identity}/edit", f"/calendar/{identity}/cancel"):
            self.assertEqual(self.browser.post(path, data=self.data()).status_code, 403)
            self.assertEqual(self.browser.post(path, data={**self.data(), "csrf": "invalid"}).status_code, 403)
        self.assertEqual(self.browser.get(f"/calendar/{identity}/cancel").status_code, 405)

    def test_edit_hearing_and_cancel_idempotently(self):
        identity = self.create()
        response = self.post(f"/calendar/{identity}/edit", self.data(title="Audiencia corregida", start_time="13:00", end_time="14:00"))
        self.assertEqual(response.status_code, 303)
        self.assertEqual(self.row(identity)["title"], "Audiencia corregida")
        self.assertIn('value="13:00"', self.browser.get(f"/calendar/{identity}/edit").text)
        for _ in range(2):
            self.assertEqual(self.post(f"/calendar/{identity}/cancel").status_code, 303)
        self.assertEqual(self.row(identity)["status"], "cancelled")
        self.assertEqual(self.feed().json(), [])
        self.assertEqual(self.post(f"/calendar/{identity}/edit", self.data()).status_code, 409)

    def test_cross_org_client_and_wrong_project(self):
        for changes in ({"client_id": str(self.foreign_client_id)}, {"project_id": str(self.project_id)}, {"client_id": str(self.client_id), "project_id": str(self.other_project_id)}, {"client_id": str(self.client_id), "project_id": str(self.foreign_project_id)}):
            self.assertEqual(self.post("/calendar/new", self.data(**changes)).status_code, 422)

    def test_inactive_client_retain_but_cannot_assign(self):
        identity = self.create(client_id=str(self.client_id))
        self.connection.execute(update(Client).where(Client.id == self.client_id).values(is_active=False))
        self.assertEqual(self.post("/calendar/new", self.data(client_id=str(self.client_id))).status_code, 422)
        self.assertEqual(self.post(f"/calendar/{identity}/edit", self.data(client_id=str(self.client_id))).status_code, 303)

    def test_invalid_dates_fields_and_durations(self):
        for change in ({"title": ""}, {"title": "x" * 256}, {"date": "2026-02-30"}, {"date": "2101-01-01"}, {"start_time": "25:30"}, {"start_time": "10:30+04:00"}, {"end_time": "09:00"}, {"end_date": "2026-10-04"}, {"end_date": "2028-10-05"}, {"all_day": "1"}, {"reminder_at": "2026-10-06T10:00"}, {"description": "x" * 5001}):
            with self.subTest(change=tuple(change)):
                self.assertEqual(self.post("/calendar/new", self.data(**change)).status_code, 422)

    def test_all_day_civil_dates_preserved_across_timezone_change(self):
        identity = self.create(all_day="yes", end_date="2026-10-07")
        row = self.row(identity)
        self.assertIsNone(row["start_at"])
        self.assertEqual((row["start_date"], row["end_date"]), (date(2026, 10, 5), date(2026, 10, 8)))
        self.connection.execute(update(Organization).where(Organization.id == self.org_id).values(timezone="Pacific/Auckland"))
        self.assertEqual(self.feed().json()[0]["start"], "2026-10-05")

    def test_dst_rejects_ambiguous_and_nonexistent_time(self):
        self.connection.execute(update(Organization).where(Organization.id == self.org_id).values(timezone="America/New_York"))
        for day, hour in (("2026-03-08", "02:30"), ("2026-11-01", "01:30")):
            self.assertEqual(self.post("/calendar/new", self.data(date=day, start_time=hour, end_time="")).status_code, 422)

    def test_dst_changes_offset_between_start_end(self):
        self.connection.execute(update(Organization).where(Organization.id == self.org_id).values(timezone="America/New_York"))
        identity = self.create(date="2026-03-08", start_time="01:30", end_time="03:30")
        row = self.row(identity)
        self.assertEqual(row["end_at"] - row["start_at"], timedelta(hours=1))

    def test_range_validation_and_limit(self):
        for values in ({"start": ""}, {"start": "2026-10-01T00:00:00"}, {"end": "2026-09-01"}, {"end": "2027-10-01"}, {"start": "bad"}, {"start": "9" * 1000}, {"end": "9999-01-01"}):
            self.assertEqual(self.feed(**values).status_code, 400)
        self.assertEqual(self.feed(start="2026-10-01", end="2026-11-01").status_code, 200)

    def test_range_exclusive_end_and_overlapping_event(self):
        self.create(date="2026-09-30", start_time="23:00", end_date="2026-10-01", end_time="01:00")
        self.create(date="2026-11-01", start_time="00:00", end_time="01:00")
        self.assertEqual(len(self.feed().json()), 1)

    def test_filters_types_completed_client_project(self):
        hearing = self.create(client_id=str(self.client_id), project_id=str(self.project_id))
        self.create(event_type="meeting")
        self.task(status="completed", completed_at=datetime.now(timezone.utc))
        self.assertEqual(len(self.feed().json()), 2)
        self.assertEqual(len(self.feed(completed="1").json()), 3)
        self.assertEqual([e["id"] for e in self.feed(types="hearing").json()], [f"event-{hearing}"])
        self.assertEqual(len(self.feed(client_id=str(self.client_id), project_id=str(self.project_id)).json()), 1)
        self.assertEqual(self.feed(types="").json(), [])
        for filters in ({"types": "evil"}, {"completed": "yes"}, {"client_id": str(self.foreign_client_id)}, {"project_id": str(self.foreign_project_id)}, {"timeZone": "UTC"}):
            self.assertEqual(self.feed(**filters).status_code, 400)

    def test_no_task_duplication_or_background_generation(self):
        identity = self.task(recurrence_type="daily", recurrence_anchor_date=date(2026, 10, 5))
        before = self.connection.scalar(select(func.count()).select_from(Task))
        for _ in range(3):
            self.feed()
            self.browser.get("/calendar")
        self.assertEqual(self.connection.scalar(select(func.count()).select_from(Task)), before)
        self.assertEqual(self.connection.scalar(select(func.count()).select_from(CalendarEvent).where(CalendarEvent.user_id == self.user_id)), 0)
        self.assertEqual(self.post(f"/tasks/{identity}/status", {"status": "completed"}).status_code, 303)
        self.assertEqual(self.connection.scalar(select(func.count()).select_from(Task)), before + 1)

    def test_task_form_reused_with_date(self):
        response = self.browser.get("/tasks/new?date=2026-10-05&time=10:00")
        self.assertEqual(response.status_code, 200)
        self.assertIn('value="2026-10-05"', response.text)
        self.assertIn('value="10:00"', response.text)
        self.assertEqual(self.browser.get("/tasks/new?date=bad").status_code, 400)

    def test_calendar_form_date_click_and_escape(self):
        response = self.browser.get("/calendar/new?date=2026-10-05&time=10:00")
        self.assertIn('value="10:00"', response.text)
        identity = self.create(title='<script>alert("x")</script>', institution="<img src=x>")
        html = self.browser.get(f"/calendar/{identity}").text
        self.assertNotIn('<script>alert(', html)
        self.assertIn('&lt;script&gt;', html)
        self.assertEqual(self.browser.get("/calendar/new?date=bad").status_code, 400)

    def test_feed_minimal_and_no_store(self):
        self.create(description="NOTES-NOT-IN-FEED", location="PRIVATE-LOCATION")
        response = self.feed()
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertNotIn("NOTES-NOT-IN-FEED", response.text)
        self.assertNotIn("PRIVATE-LOCATION", response.text)
        self.assertFalse(response.json()[0]["editable"])

    def test_dashboard_next_events_scoped_sorted_and_bounded(self):
        self.create(client_id=str(self.client_id))
        self.task(due_date=date(2026, 10, 4))
        self.sign_in(self.colleague_id, self.org_id)
        self.create(title="Privado ajeno")
        self.sign_in(self.user_id, self.org_id)
        with patch("app.main.aware_now", return_value=datetime(2026, 10, 1, tzinfo=timezone.utc)):
            response = self.browser.get("/dashboard")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Próximos eventos", response.text)
        self.assertNotIn("Privado ajeno", response.text)
        self.assertEqual(len(response.context["upcoming_events"]), 2)
        self.assertEqual(response.context["upcoming_events"][0]["extendedProps"]["type"], "task")

    def test_navigation_permissions_and_nonce(self):
        response = self.browser.get("/calendar")
        self.assertIn('href="/calendar" aria-current="page"', response.text)
        self.assertIn('class="sidebar"', response.text)
        self.assertNotIn('href="/settings/timezone"', response.text)
        self.assertIn("'nonce-", response.headers["content-security-policy"])
        self.assertNotIn("unsafe-inline", response.headers["content-security-policy"])
        self.assertNotEqual(response.headers["content-security-policy"], self.browser.get("/calendar").headers["content-security-policy"])

    def test_database_enforces_foreign_owner_and_project(self):
        for changes in ({"user_id": self.foreign_user_id}, {"client_id": self.foreign_client_id}, {"client_id": self.client_id, "project_id": self.other_project_id}, {"project_id": self.project_id}):
            with self.assertRaises(IntegrityError), self.connection.begin_nested():
                self.connection.execute(CalendarEvent.__table__.insert().values(**{"organization_id": self.org_id, "user_id": self.user_id, "event_type": "hearing", "title": "Invalid", "start_at": datetime.now(timezone.utc), **changes}))

    def test_excessive_results_fail_safely(self):
        self.create()
        with patch("app.calendar.MAX_EVENTS", 0):
            self.assertEqual(self.feed().status_code, 422)
            self.assertEqual(self.browser.get("/dashboard").status_code, 200)

    def test_duplicate_query_and_post_rejected(self):
        self.assertEqual(self.browser.get("/calendar/events?start=2026-10-01&start=2026-10-02&end=2026-11-01").status_code, 400)
        self.assertEqual(self.post("/calendar/new?user_id=1", self.data()).status_code, 400)

    def test_workdays_from_organization(self):
        self.connection.execute(update(Organization).where(Organization.id == self.org_id).values(workday_monday=False, workday_saturday=True))
        self.assertIn('data-business-days="2,3,4,5,6"', self.browser.get("/calendar").text)

    def test_hidden_types_do_not_exhaust_feed_limit(self):
        self.create(event_type="meeting")
        with patch("app.calendar.MAX_EVENTS", 0):
            self.assertEqual(self.feed(types="task").json(), [])
            self.assertEqual(self.feed(types="").json(), [])

    def test_database_requires_end_date_for_all_day(self):
        with self.assertRaises(IntegrityError), self.connection.begin_nested():
            self.connection.execute(CalendarEvent.__table__.insert().values(organization_id=self.org_id, user_id=self.user_id, event_type="hearing", title="Invalid", all_day=True, start_date=date(2026, 10, 5)))

    def test_all_day_detail_end_is_inclusive(self):
        identity = self.create(all_day="yes", end_date="2026-10-07")
        page = self.browser.get(f"/calendar/{identity}")
        self.assertEqual(page.status_code, 200)
        self.assertIn("07/10/2026", page.text)
        self.assertNotIn("08/10/2026", page.text)
