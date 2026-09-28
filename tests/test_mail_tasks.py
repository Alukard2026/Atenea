"""Dominio → cliente y correo → tarea, sin Microsoft real ni datos persistentes de prueba."""

import html
import re
import unittest
from unittest.mock import patch

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.client_domains import normalize_domain, sender_domain
from app.models import Client, Task
import test_mail


class MailTaskTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test_mail.MailTests.setUpClass()

    def setUp(self):
        self.fixture = test_mail.MailTests()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.fx = self.fixture
        self.db, self.browser, self.f = self.fx.db, self.fx.browser, self.fx.f
        self.path = "/mail/message-0=/create-task"
        self.address = "notificaciones@dma.com.sv"
        def graph(url, **kwargs):
            payload = self.fx.message()
            payload["from"]["emailAddress"]["address"] = self.address
            return self.fx.response(payload=payload)
        self.f.graph.side_effect = graph

    def tearDown(self):
        self.fixture.tearDown()

    def add_client(self, domain="dma.com.sv", name="DMA", owner=0, active=True):
        return self.db.execute(Client.__table__.insert().values(
            organization_id=self.f.owners[owner].organization_id, name=name, email_domain=domain,
            client_type="empresa", is_active=active,
        ).returning(Client.id)).scalar_one()

    def count(self, model):
        return self.db.scalar(select(func.count()).select_from(model))

    def form(self, response=None, **overrides):
        page = response if response is not None else self.browser.get(self.path)
        self.assertEqual(page.status_code, 200)
        match = re.search(r'name="intent" value="([^"]+)"', page.text)
        self.assertIsNotNone(match)
        result = {"csrf": self.f.csrf, "intent": html.unescape(match.group(1)), "action": "create_task",
                  "title": "Revisar notificación", "priority": "normal", "description": "Nota manual del usuario"}
        result.update(overrides)
        return result

    def create(self, response=None, **overrides):
        data = self.form(response, action="create_client", email_domain=self.address.rsplit("@", 1)[1].lower(),
                         new_client_name="Nombre corregido", new_client_type="empresa")
        data.update(overrides)
        return self.browser.post(self.path, data=data)

    def test_existing_dma_preselected_and_database_name_wins(self):
        identity = self.add_client(name="DMA registrada")
        page = self.browser.get(self.path)
        self.assertEqual(page.status_code, 200)
        self.assertIn("DMA registrada", page.text)
        self.assertIn(f'value="{identity}" selected', page.text)
        self.assertNotIn("No existe todavía en Atenea", page.text)

    def test_duplicate_creation_reuses_existing_without_renaming(self):
        data = self.form(action="create_client", email_domain="dma.com.sv", new_client_name="Duplicado", new_client_type="otro")
        identity = self.add_client()
        before = self.count(Client)
        for _ in range(2):
            page = self.browser.post(self.path, data=data)
            self.assertEqual(page.status_code, 200)
            self.assertIn(f'value="{identity}" selected', page.text)
        self.assertEqual(self.count(Client), before)
        self.assertEqual(self.db.scalar(select(Client.name).where(Client.id == identity)), "DMA")

    def test_known_and_unknown_government_and_corporate_suggestions(self):
        for address, name, kind in (
            ("notificaciones@defensoria.gob.sv", "Defensoría del Consumidor", "institucion_publica"),
            ("contacto@ministerioejemplo.gob.sv", "Ministerioejemplo", "institucion_publica"),
            ("contacto@empresa-ejemplo.com", "Empresa ejemplo", "empresa"),
            ("usuario@corporativa.cr", "Corporativa", "empresa"),
            ("usuario@dma.com.sv", "DMA", "empresa"),
        ):
            self.address = address
            page = self.browser.get(self.path)
            self.assertIn(name, page.text)
            self.assertIn(f'value="{kind}" selected', page.text)
            self.assertIn("No existe todavía en Atenea", page.text)

    def test_public_providers_leave_client_empty_and_manual_selection_available(self):
        self.add_client(domain="manual.example", name="Cliente manual")
        for domain in ("gmail.com", "outlook.com", "hotmail.com", "yahoo.com", "icloud.com", "sub.gmail.com"):
            self.address = "persona@" + domain
            page = self.browser.get(self.path)
            self.assertIn("proveedor de correo público", page.text)
            self.assertIn("Cliente manual", page.text)
            self.assertNotIn('name="new_client_name"', page.text)
            self.assertNotRegex(page.text, r'<option value="\d+" selected')
            self.assertEqual(self.create().status_code, 422)

    def test_explicit_mapping_precedes_inference_and_is_centralized(self):
        with patch.dict("app.client_domains.DOMAIN_NAMES", {"dma.com.sv": "Nombre configurable"}):
            self.assertIn("Nombre configurable", self.browser.get(self.path).text)

    def test_corrected_name_domain_type_and_new_client_selected_in_task(self):
        self.address = "notificaciones@defensoria.gob.sv"
        before_tasks = self.count(Task)
        page = self.create(new_client_name="Defensoría — oficina central", new_client_type="institucion_publica")
        self.assertEqual(page.status_code, 200)
        self.assertEqual(self.count(Task), before_tasks)
        row = self.db.execute(select(Client.id, Client.name, Client.email_domain, Client.client_type).where(
            Client.organization_id == self.f.owner.organization_id, Client.email_domain == "defensoria.gob.sv")).one()
        self.assertEqual(row.name, "Defensoría — oficina central")
        self.assertEqual(row.client_type, "institucion_publica")
        self.assertIn(f'value="{row.id}" selected', page.text)
        response = self.browser.post(self.path, data=self.form(page, client_id=str(row.id)))
        self.assertEqual(response.status_code, 303)
        task_id = int(response.headers["location"].split("/")[2])
        task = self.db.execute(select(Task.client_id, Task.description, Task.source_type).where(Task.id == task_id)).one()
        self.assertEqual(task.client_id, row.id)
        self.assertEqual(task.description, "Nota manual del usuario")
        self.assertEqual(task.source_type, "microsoft_mail")

    def test_creation_requires_csrf_and_never_happens_on_get(self):
        before_clients, before_tasks = self.count(Client), self.count(Task)
        for _ in range(2):
            self.assertEqual(self.browser.get("/mail/message-0=").status_code, 200)
            self.assertEqual(self.browser.get(self.path).status_code, 200)
        data = self.form(action="create_client", email_domain="dma.com.sv", new_client_name="DMA", new_client_type="empresa")
        data.pop("csrf")
        self.assertEqual(self.browser.post(self.path, data=data).status_code, 403)
        self.assertEqual(self.count(Client), before_clients)
        self.assertEqual(self.count(Task), before_tasks)

    def test_organization_isolation_and_same_domain_in_different_organizations(self):
        foreign = self.add_client(owner=2, name="Cliente ajeno")
        page = self.browser.get(self.path)
        self.assertNotIn("Cliente ajeno", page.text)
        self.assertIn("No existe todavía en Atenea", page.text)
        self.assertEqual(self.create().status_code, 200)
        rows = self.db.execute(select(Client.id, Client.organization_id).where(Client.email_domain == "dma.com.sv")).all()
        self.assertEqual(len(rows), 2)
        response = self.browser.post(self.path, data=self.form(client_id=str(foreign)))
        self.assertEqual(response.status_code, 422)

    def test_uppercase_sender_matches_normalized_domain(self):
        identity = self.add_client()
        self.address = "persona@DMA.COM.SV"
        self.assertIn(f'value="{identity}" selected', self.browser.get(self.path).text)

    def test_domain_normalized_on_orm_and_catalog_creation(self):
        self.assertEqual(normalize_domain(" DMA.COM.SV. "), "dma.com.sv")
        with Session(bind=self.db, join_transaction_mode="create_savepoint") as db:
            client = Client(organization_id=self.f.owner.organization_id, name="ORM", email_domain="OTRA.EXAMPLE")
            db.add(client)
            db.commit()
            self.assertEqual(client.email_domain, "otra.example")
        response = self.browser.post("/clients", data={"csrf": self.f.csrf, "name": "Catálogo", "email_domain": "CATALOGO.EXAMPLE", "client_type": "otro"})
        self.assertEqual(response.status_code, 303)
        self.assertEqual(self.db.scalar(select(Client.email_domain).where(Client.name == "Catálogo", Client.organization_id == self.f.owner.organization_id)), "catalogo.example")

    def test_archived_domain_never_duplicated_or_preselected(self):
        identity = self.add_client(active=False)
        page = self.browser.get(self.path)
        self.assertIn("archivado", page.text)
        self.assertNotIn(f'value="{identity}" selected', page.text)
        before = self.count(Client)
        self.assertEqual(self.create(page).status_code, 422)
        self.assertEqual(self.count(Client), before)

    def test_normal_user_can_select_existing_but_not_create_client(self):
        identity = self.add_client()
        self.f.sign_in(1)
        page = self.browser.get(self.path)
        self.assertIn(f'value="{identity}" selected', page.text)
        self.assertEqual(self.create(page).status_code, 403)
        response = self.browser.post(self.path, data=self.form(page, client_id=str(identity)))
        self.assertEqual(response.status_code, 303)

    def test_forged_organization_domain_type_and_untrusted_fields_rejected(self):
        before = self.count(Client)
        for name in ("organization_id", "user_id", "source_id", "body", "microsoft_account_id"):
            data = self.form()
            data[name] = "forged"
            self.assertEqual(self.browser.post(self.path, data=data).status_code, 400)
        self.assertEqual(self.create(email_domain="forged.example").status_code, 422)
        self.assertEqual(self.create(new_client_type="unknown").status_code, 422)
        self.assertEqual(self.count(Client), before)

    def test_task_duplicate_submission_is_idempotent_and_does_not_copy_body(self):
        data = self.form()
        first = self.browser.post(self.path, data=data)
        second = self.browser.post(self.path, data=data)
        self.assertEqual(first.status_code, 303)
        self.assertEqual(first.headers["location"], second.headers["location"])
        task_id = int(first.headers["location"].split("/")[2])
        row = self.db.execute(select(Task.__table__).where(Task.id == task_id)).mappings().one()
        self.assertNotIn("Cuerpo confidencial", str(row))
        self.assertTrue(all("body" not in c.kwargs["params"]["$select"].split(",") for c in self.f.graph.call_args_list))

    def test_cross_user_context_rejected_before_graph(self):
        data = self.form()
        self.f.sign_in(1)
        data["csrf"] = self.f.csrf
        count = self.f.graph.call_count
        self.assertEqual(self.browser.post(self.path, data=data).status_code, 400)
        self.assertEqual(self.f.graph.call_count, count)

    def test_invalid_sender_and_html_name_are_safe(self):
        self.address = "not-an-email"
        self.assertIn("dominio válido", self.browser.get(self.path).text)
        self.address = "persona@dma.com.sv"
        page = self.create(new_client_name='<script>alert("x")</script>')
        self.assertEqual(page.status_code, 200)
        self.assertNotIn('<script>alert("x")</script>', page.text)
        self.assertIn("&lt;script&gt;", page.text)
        for value in ("https://evil.example", "a@evil.example", "a..example", "example", "evil.example/path"):
            with self.assertRaises(ValueError):
                normalize_domain(value)
        self.assertIsNone(sender_domain("Name <a@dma.com.sv>"))

    def test_requires_authentication_and_connected_microsoft(self):
        self.browser.cookies.clear()
        self.assertEqual(self.browser.get(self.path).headers["location"], "/login")
        self.f.sign_in(0)
        self.browser.post("/integrations/microsoft/disconnect", data={"csrf": self.f.csrf})
        page = self.browser.get(self.path)
        self.assertIn("Microsoft no conectado", page.text)
