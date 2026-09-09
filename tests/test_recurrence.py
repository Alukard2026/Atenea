"""Recurrencias y zonas por organización; PostgreSQL con datos revertidos."""

from datetime import date, datetime, timezone
from unittest.mock import patch

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from app.models import Organization, Task
from app.recurrence import occurrence_date
from test_worklog import WorklogCase


class RecurrenceTests(WorklogCase):
    def counts(self):
        return super().counts() + (self.connection.scalar(select(func.count()).select_from(Task)),)

    def data(self, **overrides):
        return {"title": "Preparar informe", "priority": "normal", "due_date": "2026-09-30", "due_time": "15:00",
                "recurrence_type": "monthly", "recurrence_interval": "1", "recurrence_end_date": "", **overrides}

    def create(self, **overrides):
        response = self.post("/tasks/new", self.data(**overrides))
        self.assertEqual(response.status_code, 303)
        return self.connection.scalar(select(Task.id).where(Task.user_id == self.user_id).order_by(Task.id.desc()).limit(1))

    def row(self, task_id):
        return self.connection.execute(select(Task.__table__).where(Task.id == task_id)).mappings().one()

    def children(self, task_id):
        return self.connection.execute(select(Task.__table__).where(Task.parent_task_id == task_id)).mappings().all()

    def close(self, task_id, status="completed"):
        response = self.post(f"/tasks/{task_id}/status", {"status": status})
        self.assertEqual(response.status_code, 303)
        return self.children(task_id)

    def test_default_timezone_and_old_task_none(self):
        self.assertEqual(self.connection.scalar(select(Organization.timezone).where(Organization.id == self.org_id)), "America/El_Salvador")
        task_id = self.create(recurrence_type="none")
        self.assertEqual(self.row(task_id)["recurrence_type"], "none")
        self.assertEqual(self.close(task_id), [])

    def test_admin_can_set_zone_and_other_organization_unchanged(self):
        self.sign_in(self.colleague_id, self.org_id)
        response = self.browser.get("/settings/timezone")
        self.assertEqual(response.status_code, 200)
        self.assertIn('value="America/El_Salvador" selected', response.text)
        self.assertEqual(self.post("/settings/timezone", {"timezone": "Asia/Tokyo"}).status_code, 303)
        self.assertEqual(self.connection.scalar(select(Organization.timezone).where(Organization.id == self.org_id)), "Asia/Tokyo")
        self.assertEqual(self.connection.scalar(select(Organization.timezone).where(Organization.id == self.foreign_org_id)), "America/El_Salvador")
        self.assertIn("Asia/Tokyo", self.browser.get("/tasks/new").text)

    def test_normal_user_cannot_configure_zone(self):
        self.assertEqual(self.browser.get("/settings/timezone").status_code, 403)
        self.assertEqual(self.post("/settings/timezone", {"timezone": "UTC"}).status_code, 403)

    def test_invalid_zone_rejected_by_form_and_orm(self):
        self.sign_in(self.colleague_id, self.org_id)
        for value in ("Not/A_Zone", "../UTC", "", "GMT-6"):
            self.assertEqual(self.post("/settings/timezone", {"timezone": value}).status_code, 422)
            with self.assertRaises(ValueError):
                Organization(timezone=value)

    def test_timezone_csrf_and_forged_organization_rejected(self):
        self.sign_in(self.colleague_id, self.org_id)
        self.assertEqual(self.browser.post("/settings/timezone", data={"timezone": "UTC"}).status_code, 403)
        self.assertEqual(self.post("/settings/timezone", {"timezone": "UTC", "organization_id": str(self.foreign_org_id)}).status_code, 400)

    def test_reminder_and_dashboard_use_organization_zone(self):
        self.connection.execute(update(Organization).where(Organization.id == self.org_id).values(timezone="Asia/Tokyo"))
        identity = self.create(recurrence_type="none", due_date="2026-09-10", due_time="", reminder_at="2026-09-10T09:00")
        self.assertEqual(self.row(identity)["reminder_at"], datetime(2026, 9, 10, tzinfo=timezone.utc))
        now = datetime(2026, 9, 9, 18, tzinfo=timezone.utc)  # 10/09 en Tokio; 09/09 en El Salvador.
        with patch("app.tasks.utc_now", return_value=now):
            self.assertEqual(self.browser.get("/dashboard").context["task_summary"]["today"], 1)
            page = self.browser.get("/tasks")
            self.assertEqual(page.context["rows"][0]["period"], "today")
        self.sign_in(self.foreign_user_id, self.foreign_org_id)
        self.assertIn("America/El_Salvador", self.browser.get("/tasks/new").text)

    def test_daily_generation(self):
        identity = self.create(recurrence_type="daily")
        self.assertEqual(self.close(identity)[0]["due_date"], date(2026, 10, 1))

    def test_weekly_generation_keeps_friday(self):
        identity = self.create(recurrence_type="weekly", due_date="2026-09-04")
        self.assertEqual(self.close(identity)[0]["due_date"], date(2026, 9, 11))

    def test_monthly_generation(self):
        identity = self.create()
        child = self.close(identity)[0]
        self.assertEqual(child["due_date"], date(2026, 10, 30))
        self.assertEqual(child["recurrence_anchor_date"], date(2026, 9, 30))
        self.assertEqual(child["recurrence_index"], 1)

    def test_yearly_generation(self):
        identity = self.create(recurrence_type="yearly")
        self.assertEqual(self.close(identity)[0]["due_date"], date(2027, 9, 30))

    def test_interval_two_weeks_and_two_months(self):
        identity = self.create(recurrence_type="weekly", recurrence_interval="2", due_date="2026-09-04")
        self.assertEqual(self.close(identity)[0]["due_date"], date(2026, 9, 18))
        identity = self.create(recurrence_interval="2")
        self.assertEqual(self.close(identity)[0]["due_date"], date(2026, 11, 30))

    def test_month_31_clamps_and_returns_to_31(self):
        identity = self.create(due_date="2026-01-31")
        february = self.close(identity)[0]
        self.assertEqual(february["due_date"], date(2026, 2, 28))
        march = self.close(february["id"])[0]
        self.assertEqual(march["due_date"], date(2026, 3, 31))
        self.assertEqual(march["recurrence_index"], 2)

    def test_days_29_30_and_annual_leap_year_restore(self):
        for day in (29, 30, 31):
            anchor = date(2026, 1, day)
            self.assertEqual(occurrence_date(anchor, "monthly", 1, 1), date(2026, 2, 28))
            self.assertEqual(occurrence_date(anchor, "monthly", 1, 2), date(2026, 3, day))
        self.assertEqual(occurrence_date(date(2024, 2, 29), "yearly", 1, 1), date(2025, 2, 28))
        self.assertEqual(occurrence_date(date(2024, 2, 29), "yearly", 1, 4), date(2028, 2, 29))

    def test_end_date_inclusive_and_generation_stops(self):
        identity = self.create(recurrence_end_date="2026-10-30")
        child = self.close(identity)[0]
        self.assertEqual(child["due_date"], date(2026, 10, 30))
        self.assertEqual(self.close(child["id"]), [])

    def test_retries_reopen_and_cancel_never_duplicate(self):
        identity = self.create()
        child = self.close(identity)[0]
        completed_at = self.row(identity)["completed_at"]
        self.close(identity)
        self.assertEqual(self.row(identity)["completed_at"], completed_at)
        self.close(identity, "pending")
        self.close(identity)
        self.close(identity, "cancelled")
        self.assertEqual([row["id"] for row in self.children(identity)], [child["id"]])

    def test_cancel_continues_series_preserving_history(self):
        identity = self.create()
        child = self.close(identity, "cancelled")[0]
        self.assertEqual(self.row(identity)["status"], "cancelled")
        self.assertEqual(child["status"], "pending")
        self.assertEqual(child["user_id"], self.user_id)
        self.assertEqual(child["organization_id"], self.org_id)
        self.assertIn(f'/tasks/{child["id"]}/edit', self.browser.get("/tasks?status=cancelled").text)

    def test_relative_reminder_same_local_hours(self):
        identity = self.create(reminder_at="2026-09-30T09:00")
        child = self.close(identity)[0]
        self.assertEqual(child["reminder_at"], datetime(2026, 10, 30, 15, tzinfo=timezone.utc))
        self.assertNotEqual(child["reminder_at"], self.row(identity)["reminder_at"])

    def test_reminder_day_before_without_due_time(self):
        identity = self.create(due_time="", reminder_at="2026-09-29T09:00")
        child = self.close(identity)[0]
        self.assertIsNone(child["due_time"])
        self.assertEqual(child["reminder_at"], datetime(2026, 10, 29, 15, tzinfo=timezone.utc))

    def test_dst_reminder_keeps_wall_hour(self):
        self.connection.execute(update(Organization).where(Organization.id == self.org_id).values(timezone="America/New_York"))
        identity = self.create(due_date="2026-10-30", reminder_at="2026-10-30T09:00")
        child = self.close(identity)[0]
        self.assertEqual(self.row(identity)["reminder_at"], datetime(2026, 10, 30, 13, tzinfo=timezone.utc))
        self.assertEqual(child["reminder_at"], datetime(2026, 11, 30, 14, tzinfo=timezone.utc))

    def test_occurrence_edit_does_not_move_original_calendar_or_existing_child(self):
        identity = self.create(due_date="2026-01-31")
        self.assertEqual(self.post(f"/tasks/{identity}/edit", self.data(due_date="2026-02-02")).status_code, 303)
        child = self.close(identity)[0]
        self.assertEqual(child["due_date"], date(2026, 2, 28))
        self.assertEqual(self.post(f"/tasks/{identity}/edit", self.data(title="Cambio de esta tarea", due_date="2026-02-02")).status_code, 303)
        self.assertEqual(self.row(child["id"])["title"], "Preparar informe")

    def test_no_repeat_stops_pending_successor(self):
        child = self.close(self.create())[0]
        self.assertEqual(self.post(f"/tasks/{child['id']}/edit", self.data(due_date="2026-10-30", recurrence_type="none")).status_code, 303)
        self.assertEqual(self.close(child["id"]), [])

    def test_cannot_change_rule_after_successor_generated(self):
        identity = self.create()
        self.close(identity)
        self.assertEqual(self.post(f"/tasks/{identity}/edit", self.data(recurrence_type="daily")).status_code, 422)

    def test_recurrence_input_validation_and_derived_fields(self):
        for fields in ({"due_date": "", "due_time": ""}, {"recurrence_type": "rrule"}, {"recurrence_interval": "0"},
                       {"recurrence_interval": "366"}, {"recurrence_interval": "1.5"}, {"recurrence_end_date": "2026-09-29"}):
            self.assertEqual(self.post("/tasks/new", self.data(**fields)).status_code, 422)
        for field in ("parent_task_id", "recurrence_anchor_date", "recurrence_index", "organization_id", "user_id"):
            self.assertEqual(self.post("/tasks/new", self.data(**{field: "1"})).status_code, 400)

    def test_foreign_users_cannot_generate_or_edit(self):
        identity = self.create()
        for user_id, org_id in ((self.colleague_id, self.org_id), (self.foreign_user_id, self.foreign_org_id)):
            self.sign_in(user_id, org_id)
            self.assertEqual(self.post(f"/tasks/{identity}/status", {"status": "completed"}).status_code, 404)
            self.assertEqual(self.post(f"/tasks/{identity}/edit", self.data()).status_code, 404)
            self.assertEqual(self.children(identity), [])
            self.assertEqual(self.browser.get("/tasks").context["rows"], [])

    def test_creation_and_generation_require_csrf(self):
        identity = self.create()
        self.assertEqual(self.browser.post("/tasks/new", data=self.data()).status_code, 403)
        self.assertEqual(self.browser.post(f"/tasks/{identity}/status", data={"status": "cancelled"}).status_code, 403)
        self.assertEqual(self.children(identity), [])

    def test_generated_pending_occurrence_uses_current_dashboard_groups(self):
        now = datetime(2026, 9, 9, 18, tzinfo=timezone.utc)
        child = self.close(self.create(due_date="2026-09-08", due_time="", recurrence_type="daily"))[0]
        with patch("app.tasks.utc_now", return_value=now):
            self.assertEqual(self.browser.get("/dashboard").context["task_summary"]["today"], 1)
            rows = self.browser.get("/tasks?status=pending").context["rows"]
            self.assertEqual([(row["task"].id, row["period"]) for row in rows], [(child["id"], "today")])

    def test_database_parent_uniqueness_and_owner_constraints(self):
        identity = self.create()
        self.close(identity)
        for values in ({"organization_id": self.org_id, "user_id": self.user_id},
                       {"organization_id": self.org_id, "user_id": self.colleague_id},
                       {"organization_id": self.foreign_org_id, "user_id": self.foreign_user_id}):
            with self.assertRaises(IntegrityError):
                with self.connection.begin_nested():
                    self.connection.execute(Task.__table__.insert().values(title="Inválida", parent_task_id=identity, **values))

    def test_year_limit_stops_without_failing_completion(self):
        identity = self.create(due_date="2100-12-31", recurrence_type="daily")
        self.assertEqual(self.close(identity), [])
        self.assertEqual(self.row(identity)["status"], "completed")
