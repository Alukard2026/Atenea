"""Registro simplificado, histórico mensual y administración de clientes."""

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select, update

from app.models import Client, TimeEntry
from test_worklog import WorklogCase


class HistoryTests(WorklogCase):
    def history(self, **filters):
        return self.browser.get("/hours/history", params={"month": self.day.strftime("%Y-%m"), **filters})

    def entries(self):
        return self.connection.execute(select(TimeEntry.__table__).where(
            TimeEntry.user_id == self.user_id, TimeEntry.organization_id == self.org_id,
        )).mappings().all()

    def test_register_past_date_without_project(self):
        data = self.entry_data(project_id="", work_date=(date.today() - timedelta(days=3)).isoformat())
        data.pop("activity", None)
        self.assertEqual(self.post("/hours", data).status_code, 303)
        row = self.entries()[0]
        self.assertIsNone(row["project_id"])
        self.assertEqual(row["description"], data["description"])
        self.assertEqual(row["activity"], data["description"])

    def test_register_previous_month_with_project(self):
        worked = date.today().replace(day=1) - timedelta(days=1)
        response = self.post("/hours", self.entry_data(work_date=worked.isoformat()))
        self.assertEqual(response.status_code, 303)
        self.assertIn("month=" + worked.strftime("%Y-%m"), response.headers["location"])
        self.assertEqual(self.entries()[0]["project_id"], self.project_id)
        self.assertEqual(self.entries()[0]["work_date"], worked)

    def test_required_client_and_description(self):
        for change in ({"client_id": ""}, {"description": "  "}):
            self.assertEqual(self.post("/hours", self.entry_data(**change)).status_code, 422)
        self.assertEqual(len(self.entries()), 0)

    def test_foreign_or_incompatible_ids_are_rejected(self):
        for change in ({"client_id": str(self.foreign_client_id), "project_id": ""},
                       {"project_id": str(self.foreign_project_id)}, {"project_id": str(self.other_project_id)}):
            self.assertEqual(self.post("/hours", self.entry_data(**change)).status_code, 422)
        self.assertEqual(len(self.entries()), 0)

    def test_future_work_date_is_rejected(self):
        self.assertEqual(self.post("/hours", self.entry_data(work_date=(date.today() + timedelta(days=1)).isoformat())).status_code, 422)

    def test_history_defaults_to_current_month_and_dashboard_total(self):
        current = self.add_entry(day=date.today(), hours="1.50")
        self.add_entry(day=date.today().replace(day=1) - timedelta(days=1), hours="3")
        response = self.browser.get("/hours/history")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["month"], date.today().strftime("%Y-%m"))
        self.assertEqual([entry.id for entry in response.context["entries"]], [current])
        self.assertIn('id="dashboard-month-total">1.50 h', self.browser.get("/dashboard").text)

    def test_month_navigation_preserves_filters_and_crosses_year(self):
        response = self.browser.get("/hours/history?month=2025-12&billable=yes")
        self.assertEqual(response.status_code, 200)
        self.assertIn("month=2025-11", response.context["previous_month_url"])
        self.assertIn("month=2026-01", response.context["next_month_url"])
        self.assertIn("billable=yes", response.context["next_month_url"])
        self.assertEqual(self.browser.get(response.context["next_month_url"]).status_code, 200)

    def test_filter_by_client(self):
        selected = self.add_entry()
        self.add_entry(client_id=self.other_client_id, project_id=self.other_project_id)
        response = self.history(client_id=str(self.client_id))
        self.assertEqual([entry.id for entry in response.context["entries"]], [selected])

    def test_filter_by_project_and_without_project(self):
        self.assertEqual(self.post("/hours", self.entry_data(project_id="")).status_code, 303)
        selected = self.add_entry()
        response = self.history(project_id=str(self.project_id))
        self.assertEqual([entry.id for entry in response.context["entries"]], [selected])
        response = self.history(project_id="none")
        self.assertEqual(len(response.context["entries"]), 1)
        self.assertIsNone(response.context["entries"][0].project_id)

    def test_billable_filters_and_monthly_totals(self):
        self.assertEqual(self.post("/hours", self.entry_data(hours="1.50", billable="yes", project_id="")).status_code, 303)
        self.assertEqual(self.post("/hours", self.entry_data(hours="2.25", billable="no")).status_code, 303)
        response = self.history()
        self.assertEqual(response.context["month_total"], Decimal("3.75"))
        self.assertEqual(response.context["billable_total"], Decimal("1.50"))
        self.assertEqual(response.context["non_billable_total"], Decimal("2.25"))
        for value, total in (("yes", "1.50"), ("no", "2.25")):
            response = self.history(billable=value)
            self.assertEqual(len(response.context["entries"]), 1)
            self.assertEqual(response.context["month_total"], Decimal(total))

    def test_history_isolated_between_users_and_organizations(self):
        own = self.add_entry(activity="Trabajo propio")
        self.add_entry(user_id=self.colleague_id, activity="Trabajo de colega")
        self.add_entry(user_id=self.foreign_user_id, organization_id=self.foreign_org_id,
                       client_id=self.foreign_client_id, project_id=self.foreign_project_id, activity="Trabajo externo")
        response = self.history(user_id=str(self.colleague_id), organization_id=str(self.foreign_org_id))
        self.assertEqual([entry.id for entry in response.context["entries"]], [own])
        self.assertNotIn("Trabajo de colega", response.text)
        self.assertNotIn("Trabajo externo", response.text)

    def test_history_rejects_foreign_and_invalid_filters(self):
        for filters in ({"client_id": str(self.foreign_client_id)}, {"project_id": str(self.foreign_project_id)},
                        {"client_id": str(self.client_id), "project_id": str(self.other_project_id)},
                        {"month": "invalid"}, {"billable": "invalid"}):
            self.assertEqual(self.history(**filters).status_code, 400)

    def test_edit_historical_record_move_month_and_remove_project(self):
        entry_id = self.add_entry(activity="Actividad original")
        prior = self.day.replace(day=1) - timedelta(days=1)
        response = self.post(f"/hours/{entry_id}/edit", self.entry_data(
            work_date=prior.isoformat(), project_id="", description="Trabajo corregido", hours="3.50", billable="no"))
        self.assertEqual(response.status_code, 303)
        row = self.row(entry_id)
        self.assertEqual(row["work_date"], prior)
        self.assertIsNone(row["project_id"])
        self.assertEqual(row["activity"], "Actividad original")
        self.assertEqual(row["description"], "Trabajo corregido")
        self.assertEqual(len(self.history().context["entries"]), 0)
        self.assertEqual(self.browser.get(response.headers["location"]).context["month_total"], Decimal("3.50"))

    def test_admin_create_edit_deactivate_reactivate_client(self):
        existing = self.add_entry()
        self.sign_in(self.colleague_id, self.org_id)
        self.assertEqual(self.post("/clients", {"name": "Cliente administrado"}).status_code, 303)
        self.assertEqual(self.browser.get(f"/clients/{self.client_id}/edit").status_code, 200)
        self.assertEqual(self.post(f"/clients/{self.client_id}/edit", {"name": "Cliente renombrado", "code": "REN"}).status_code, 303)
        self.assertEqual(self.post(f"/clients/{self.client_id}/status", {"active": "no"}).status_code, 303)
        self.assertIsNotNone(self.row(existing))
        self.assertIn("Cliente renombrado", self.browser.get("/clients").text)
        self.sign_in(self.user_id, self.org_id)
        self.assertNotIn("Cliente renombrado", self.browser.get("/hours").text)
        self.assertIn("Cliente renombrado", self.history().text)
        self.assertEqual(self.post("/hours", self.entry_data(project_id="")).status_code, 422)
        self.sign_in(self.colleague_id, self.org_id)
        self.assertEqual(self.post(f"/clients/{self.client_id}/status", {"active": "yes"}).status_code, 303)
        self.sign_in(self.user_id, self.org_id)
        self.assertIn("Cliente renombrado", self.browser.get("/hours").text)

    def test_normal_user_cannot_administer_clients(self):
        self.assertNotIn('action="/clients"', self.browser.get("/clients").text)
        self.assertEqual(self.post("/clients", {"name": "No permitido"}).status_code, 403)
        self.assertEqual(self.browser.get(f"/clients/{self.client_id}/edit").status_code, 403)
        self.assertEqual(self.post(f"/clients/{self.client_id}/edit", {"name": "No permitido"}).status_code, 403)
        self.assertEqual(self.post(f"/clients/{self.client_id}/status", {"active": "no"}).status_code, 403)

    def test_client_administration_csrf_and_tenant_scope(self):
        self.sign_in(self.colleague_id, self.org_id)
        for path, data in (("/clients", {"name": "No permitido"}),
                           (f"/clients/{self.client_id}/edit", {"name": "No permitido"}),
                           (f"/clients/{self.client_id}/status", {"active": "no"})):
            self.assertEqual(self.browser.post(path, data=data).status_code, 403)
        self.assertEqual(self.post(f"/clients/{self.foreign_client_id}/edit", {"name": "No permitido"}).status_code, 404)
        self.assertEqual(self.post(f"/clients/{self.foreign_client_id}/status", {"active": "no"}).status_code, 404)

    def test_edit_can_preserve_archived_client_without_new_inactive_selection(self):
        entry_id = self.add_entry()
        self.connection.execute(update(Client).where(Client.id == self.client_id).values(is_active=False))
        self.assertEqual(self.browser.get(f"/hours/{entry_id}/edit").status_code, 200)
        self.assertEqual(self.post(f"/hours/{entry_id}/edit", self.entry_data(description="Corrección histórica")).status_code, 303)
        self.assertEqual(self.post("/hours", self.entry_data()).status_code, 422)
        self.connection.execute(update(Client).where(Client.id == self.other_client_id).values(is_active=False))
        self.assertEqual(self.post(f"/hours/{entry_id}/edit", self.entry_data(client_id=str(self.other_client_id), project_id="")).status_code, 422)

    def test_week_and_delete_support_records_without_project(self):
        self.assertEqual(self.post("/hours", self.entry_data(project_id="")).status_code, 303)
        entry_id = self.entries()[0]["id"]
        response = self.browser.get("/hours/week", params={"week": self.day.isoformat()})
        self.assertEqual(response.status_code, 200)
        self.assertIn("Sin proyecto", response.text)
        self.assertEqual(response.context["week_total"], Decimal("2.25"))
        self.assertEqual(self.browser.get(f"/hours/{entry_id}/delete").status_code, 200)
        self.assertEqual(self.post(f"/hours/{entry_id}/delete").status_code, 303)

    def test_form_has_optional_dependent_project_and_one_description(self):
        response = self.browser.get("/hours")
        self.assertIn('type="date"', response.text)
        self.assertNotIn('name="activity"', response.text)
        self.assertIn('name="project_id" aria-describedby=', response.text)
        self.assertIn(f'data-client-id="{self.client_id}"', response.text)
        self.assertIn('/static/hours.js', response.text)
        self.assertEqual(self.browser.get('/static/hours.js').status_code, 200)

