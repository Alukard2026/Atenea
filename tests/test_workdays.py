"""Configuración y reportes por organización, usando transacciones revertidas."""

from datetime import timedelta
from decimal import Decimal
from html.parser import HTMLParser

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import Organization, TimeEntry, User
from app.workdays import WORKDAY_FIELDS, workday_flags
from app.worklog import weekly_view
from test_worklog import WorklogCase


class CheckboxParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.checkboxes = {}

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "input" and attributes.get("type") == "checkbox":
            self.checkboxes[attributes["name"]] = "checked" in attributes


class WorkdaysTests(WorklogCase):
    def flags(self, org_id=None):
        with Session(bind=self.connection, join_transaction_mode="create_savepoint") as db:
            organization = db.scalar(select(Organization).where(Organization.id == (org_id or self.org_id)))
            return workday_flags(organization)

    def configure(self, indices):
        self.sign_in(self.colleague_id, self.org_id)
        response = self.post("/settings/workdays", {WORKDAY_FIELDS[index]: "on" for index in indices})
        self.assertEqual(response.status_code, 303)
        self.sign_in(self.user_id, self.org_id)

    def summary(self):
        with Session(bind=self.connection, join_transaction_mode="create_savepoint") as db:
            user = db.scalar(select(User).where(User.id == self.user_id, User.organization_id == self.org_id))
            return weekly_view(db, user, self.day)

    def seed_every_day(self):
        for index in range(7):
            self.add_entry(day=self.day + timedelta(days=index), hours=str(index + 1))

    def test_new_organization_defaults_in_orm_and_database(self):
        expected = (True, True, True, True, True, False, False)
        self.assertEqual(self.flags(), expected)
        result = self.connection.execute(Organization.__table__.insert().values(name="Defaults de prueba").returning(
            *(getattr(Organization, field) for field in WORKDAY_FIELDS),
        )).one()
        self.assertEqual(tuple(result), expected)

    def test_admin_can_save_and_configuration_persists_in_new_request(self):
        self.sign_in(self.colleague_id, self.org_id)
        page = self.browser.get("/settings/workdays")
        self.assertEqual(page.status_code, 200)
        parser = CheckboxParser()
        parser.feed(page.text)
        self.assertEqual(tuple(parser.checkboxes), WORKDAY_FIELDS)
        self.assertEqual(tuple(parser.checkboxes.values()), self.flags())
        self.assertEqual(self.post("/settings/workdays", {"workday_tuesday": "on", "workday_sunday": "on"}).status_code, 303)
        self.assertEqual(self.flags(), (False, True, False, False, False, False, True))
        self.sign_in(self.colleague_id, self.org_id)  # Nueva cookie y sesión ORM.
        parser = CheckboxParser()
        parser.feed(self.browser.get("/settings/workdays").text)
        self.assertEqual(tuple(parser.checkboxes.values()), self.flags())

    def test_normal_user_cannot_read_or_change_workdays(self):
        original = self.flags()
        self.assertEqual(self.browser.get("/settings/workdays").status_code, 403)
        self.assertEqual(self.post("/settings/workdays", {"workday_sunday": "on"}).status_code, 403)
        self.assertEqual(self.flags(), original)
        self.assertNotIn('href="/settings/workdays"', self.browser.get("/dashboard").text)

    def test_role_is_revalidated_after_admin_is_demoted(self):
        self.sign_in(self.colleague_id, self.org_id)
        page = self.browser.get("/settings/workdays")
        self.assertEqual(page.status_code, 200)
        self.connection.execute(update(User).where(User.id == self.colleague_id).values(role="user"))
        response = self.browser.post("/settings/workdays", data={"csrf": self.token(page), "workday_sunday": "on"})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(self.flags()[6])

    def test_configuration_is_isolated_and_browser_cannot_choose_organization(self):
        original_other = self.flags(self.foreign_org_id)
        self.configure(range(7))
        self.assertEqual(self.flags(self.foreign_org_id), original_other)
        self.sign_in(self.colleague_id, self.org_id)
        for field in ("organization_id", "user_id"):
            self.assertEqual(self.post("/settings/workdays", {field: str(self.foreign_org_id)}).status_code, 400)
        self.assertEqual(self.flags(), (True,) * 7)
        # Un parámetro de URL no cambia la organización seleccionada por sesión.
        self.assertEqual(self.post(f"/settings/workdays?organization_id={self.foreign_org_id}", {"workday_monday": "on"}).status_code, 303)
        self.assertEqual(self.flags(self.foreign_org_id), original_other)
        self.assertEqual(self.flags(), (True, False, False, False, False, False, False))
        self.sign_in(self.foreign_user_id, self.foreign_org_id)
        page = self.browser.get("/settings/workdays")
        parser = CheckboxParser()
        parser.feed(page.text)
        self.assertEqual(tuple(parser.checkboxes.values()), original_other)

    def test_seven_workdays(self):
        self.configure(range(7))
        self.seed_every_day()
        summary = self.summary()
        self.assertTrue(all(day["is_workday"] for day in summary["days"]))
        self.assertEqual(summary["working_total"], Decimal("28.00"))
        self.assertEqual(summary["non_working_total"], Decimal("0.00"))
        self.assertEqual(summary["week_total"], Decimal("28.00"))

    def test_monday_through_saturday(self):
        self.configure(range(6))
        self.seed_every_day()
        summary = self.summary()
        self.assertEqual([day["is_workday"] for day in summary["days"]], [True] * 6 + [False])
        self.assertEqual(summary["working_total"], Decimal("21.00"))
        self.assertEqual(summary["non_working_total"], Decimal("7.00"))
        self.assertEqual(summary["week_total"], Decimal("28.00"))

    def test_arbitrary_workdays_and_no_workdays(self):
        self.seed_every_day()
        self.configure({1, 3, 6})
        summary = self.summary()
        self.assertEqual(summary["working_total"], Decimal("13.00"))
        self.assertEqual(summary["non_working_total"], Decimal("15.00"))
        self.configure(set())
        summary = self.summary()
        self.assertEqual(summary["working_total"], Decimal("0.00"))
        self.assertEqual(summary["non_working_total"], summary["week_total"])

    def test_register_hours_on_sunday(self):
        sunday = self.day + timedelta(days=6)
        response = self.post("/hours", self.entry_data(work_date=sunday.isoformat(), hours="1.50"))
        self.assertEqual(response.status_code, 303)
        summary = self.summary()
        self.assertEqual(summary["days"][6]["total"], Decimal("1.50"))
        self.assertEqual(summary["non_working_total"], Decimal("1.50"))

    def test_register_hours_on_nonworking_weekday(self):
        self.configure({6})
        self.assertEqual(self.post("/hours", self.entry_data()).status_code, 303)
        summary = self.summary()
        self.assertFalse(summary["days"][0]["is_workday"])
        self.assertEqual(summary["non_working_total"], Decimal("2.25"))

    def test_week_renders_seven_days_including_empty_days(self):
        response = self.browser.get(f"/hours/week?week={self.day.isoformat()}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text.count('data-workday="'), 7)
        self.assertEqual(response.text.count('data-workday="true"'), 5)
        self.assertEqual(response.text.count('data-workday="false"'), 2)
        self.assertIn("Sábado", response.text)
        self.assertIn("Domingo", response.text)
        self.assertEqual(response.text.count('class="daily-total" data-total="0.00"'), 7)

    def test_daily_working_nonworking_and_general_totals(self):
        self.add_entry(hours="1.50")
        self.add_entry(hours="2.25")
        self.add_entry(day=self.day + timedelta(days=5), hours="0.10")
        self.add_entry(day=self.day + timedelta(days=6), hours="0.20")
        self.add_entry(user_id=self.colleague_id, hours="8")
        self.add_entry(user_id=self.foreign_user_id, organization_id=self.foreign_org_id, client_id=self.foreign_client_id, project_id=self.foreign_project_id, hours="9")
        self.add_entry(day=self.day + timedelta(days=7), hours="5")
        summary = self.summary()
        self.assertEqual(summary["days"][0]["total"], Decimal("3.75"))
        self.assertEqual(summary["working_total"], Decimal("3.75"))
        self.assertEqual(summary["non_working_total"], Decimal("0.30"))
        self.assertEqual(summary["week_total"], Decimal("4.05"))
        self.assertEqual(sum(len(day["entries"]) for day in summary["days"]), 4)
        response = self.browser.get(f"/hours/week?week={self.day.isoformat()}")
        for element, total in (("working-total", "3.75"), ("non-working-total", "0.30"), ("week-total", "4.05")):
            self.assertIn(f'id="{element}" data-total="{total}"', response.text)

    def test_configuration_change_reclassifies_history_without_changing_entries(self):
        entry_id = self.add_entry(day=self.day + timedelta(days=6), hours="2.25")
        original = dict(self.row(entry_id))
        self.assertEqual(self.summary()["non_working_total"], Decimal("2.25"))
        self.configure(range(7))
        self.assertEqual(self.summary()["working_total"], Decimal("2.25"))
        self.assertEqual(dict(self.row(entry_id)), original)

    def test_csrf_required_and_invalid_values_rejected_without_changes(self):
        original = self.flags()
        self.sign_in(self.colleague_id, self.org_id)
        for csrf in (None, "invalid-token"):
            data = {"workday_sunday": "on"}
            if csrf is not None:
                data["csrf"] = csrf
            self.assertEqual(self.browser.post("/settings/workdays", data=data).status_code, 403)
        for data in ({"workday_sunday": "false"}, {"unexpected_day": "on"}):
            self.assertEqual(self.post("/settings/workdays", data).status_code, 400)
        self.assertEqual(self.flags(), original)
        self.browser.cookies.clear()
        self.assertEqual(self.browser.get("/settings/workdays").headers.get("location"), "/login")
        self.assertEqual(self.browser.post("/settings/workdays", data={}).headers.get("location"), "/login")

    def test_hours_form_has_all_fields_and_describes_unrestricted_days(self):
        response = self.browser.get("/hours")
        for field in ("work_date", "hours", "client_id", "project_id", "description", "billable"):
            self.assertIn(f'name="{field}"', response.text)
        self.assertIn("Fecha trabajada", response.text)
        self.assertIn("Descripción de qué se hizo", response.text)
        self.assertIn("Puedes registrar cualquier día", response.text)

