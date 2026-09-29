"""Reglas puras y flujo de correo con Microsoft simulado y rollback de datos."""

from copy import deepcopy
import html
from io import StringIO
import logging
import re
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import unquote, urlsplit

from sqlalchemy import event, func, select, update

from app import mail_rules, microsoft
from app.client_domains import DomainSuggestion
from app.graph import MAIL_SCOPES
from app.models import Client, Task, User
import test_mail


class MailRuleTests(unittest.TestCase):
    def classify(self, subject="", preview="", address="person@example.invalid", **kwargs):
        return mail_rules.classify(mail_rules.MailInput(address, subject, preview), **kwargs)

    def test_government_domain_is_institution_not_urgency(self):
        result = self.classify(address="notificaciones@defensoria.gob.sv")
        self.assertEqual(result.categories, ("institucion_publica",))
        self.assertEqual(result.priority, "normal")
        self.assertEqual(result.entity.name, "Defensoría del Consumidor")

    def test_known_client_and_saved_public_institution(self):
        suggestion = DomainSuggestion("example.invalid", "Institución registrada", "institucion_publica", SimpleNamespace(is_active=True))
        result = self.classify(suggestion=suggestion)
        self.assertEqual(result.categories, ("cliente", "institucion_publica"))
        self.assertEqual(result.priority, "normal")

    def test_public_providers_do_not_identify_client_even_legacy_association(self):
        for domain in ("gmail.com", "hotmail.com", "outlook.com", "yahoo.com", "icloud.com", "sub.gmail.com"):
            suggestion = DomainSuggestion(domain, "Cliente erróneo", "empresa", object())
            result = self.classify(address="person@" + domain, suggestion=suggestion, internal_domains=(domain,))
            self.assertEqual(result.categories, ("sin_clasificar",))
            self.assertEqual(result.entity.name, "")
            self.assertTrue(result.entity.public_provider)

    def test_audience_alone_is_legal_normal(self):
        result = self.classify("audiencia")
        self.assertIn("legal", result.categories)
        self.assertEqual(result.priority, "normal")

    def test_invoice_alone_is_billing_normal(self):
        result = self.classify("factura")
        self.assertIn("cobro_facturacion", result.categories)
        self.assertEqual(result.priority, "normal")

    def test_explicit_urgency_and_precedence(self):
        for subject in ("urgente", "inmediato", "último día", "urgente hoy", "requerimiento urgente"):
            self.assertEqual(self.classify(subject).priority, "urgent")
        self.assertEqual(self.classify("Boletín informativo urgente sobre factura").priority, "urgent")

    def test_audience_and_deadline(self):
        result = self.classify("Audiencia", "El plazo vence hoy")
        self.assertEqual(result.priority, "urgent")
        self.assertIn("legal", result.categories)
        self.assertIn("urgente", result.categories)

    def test_high_signals_without_forcing_all_legal_to_urgent(self):
        for subject in ("vencimiento", "cuanto antes", "suspensión", "incumplimiento", "factura vencida"):
            self.assertEqual(self.classify(subject).priority, "high")
        self.assertEqual(self.classify("Requerimiento del tribunal").priority, "normal")

    def test_meeting_teams(self):
        self.assertIn("reunion_cita", self.classify("Reunión Teams").categories)

    def test_followup_pending(self):
        self.assertIn("seguimiento", self.classify("seguimiento pendiente").categories)

    def test_case_accents_and_unicode_combining_forms(self):
        results = [self.classify(subject) for subject in ("CITACIÓN REUNIÓN", "citacion reunion", "Citacio\u0301n Reunio\u0301n")]
        self.assertTrue(all(result.categories == ("legal", "reunion_cita") for result in results))

    def test_explanations_and_signals_have_static_terms_and_source(self):
        result = self.classify("Texto privado 725", "audiencia", address="notice@defensoria.gob.sv")
        self.assertTrue(any("audiencia" in reason for reason in result.reasons))
        self.assertTrue(any(".gob.sv" in reason for reason in result.reasons))
        self.assertNotIn("Texto privado 725", " ".join(result.reasons))
        self.assertIn(mail_rules.Signal("legal", "vista previa", "audiencia"), result.signals)

    def test_word_boundaries_avoid_trivial_false_positives(self):
        result = self.classify("Solicita información sobre apagones y demora de la microfactura")
        self.assertEqual(result.categories, ("sin_clasificar",))
        self.assertEqual(result.priority, "normal")

    def test_simple_negation_suppresses_urgency(self):
        for subject in ("No es urgente", "Sin requerimiento urgente", "no hay vencimiento"):
            self.assertEqual(self.classify(subject).priority, "normal")
        self.assertEqual(self.classify("No es urgente", "Necesario inmediato").priority, "urgent")

    def test_phrase_does_not_span_subject_and_preview(self):
        self.assertEqual(self.classify("vence", "hoy").priority, "normal")

    def test_informative_low_unclassified_normal_and_repetition_does_not_escalate(self):
        self.assertEqual(self.classify("boletín informativo").priority, "low")
        self.assertEqual(self.classify("Saludos").categories, ("sin_clasificar",))
        self.assertEqual(self.classify("factura " * 100).priority, "normal")
        self.assertEqual(self.classify("boletín informativo sobre audiencia").priority, "normal")

    def test_internal_requires_explicit_exact_nonpublic_domain(self):
        self.assertNotIn("interno", self.classify().categories)
        self.assertIn("interno", self.classify(internal_domains=("EXAMPLE.INVALID",)).categories)
        self.assertNotIn("interno", self.classify(address="person@sub.example.invalid", internal_domains=("example.invalid",)).categories)
        self.assertNotIn("institucion_publica", self.classify(address="person@defensoria.gob.sv.evil.example").categories)

    def test_limits_missing_values_and_body_are_ignored(self):
        raw = {"subject": "x" * 1000 + " urgente", "bodyPreview": "x" * 240 + " urgente", "importance": "high",
               "from": {"emailAddress": {"name": "notice@defensoria.gob.sv", "address": "invalid"}},
               "body": {"content": "URGENTE VENCE HOY"}}
        result = mail_rules.classify(mail_rules.message_input(raw))
        self.assertEqual(result.priority, "normal")
        self.assertEqual(result.categories, ("sin_clasificar",))
        for bad in (None, [], {"emailAddress": []}):
            result = mail_rules.classify(mail_rules.message_input({"from": bad, "subject": {}, "bodyPreview": []}))
            self.assertEqual(result.categories, ("sin_clasificar",))

    def test_central_rules_are_replaceable_without_router_changes(self):
        rule = mail_rules.Rule("ejemplo", ("seguimiento",), "high", ("palabra configurable",))
        result = self.classify("Palabra configurable", rules=(rule,))
        self.assertEqual(result.categories, ("seguimiento",))
        self.assertEqual(result.priority, "high")


class MailRuleIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test_mail.MailTests.setUpClass()

    def setUp(self):
        self.fx = test_mail.MailTests()
        self.addCleanup(self.fx.doCleanups)
        self.fx.setUp()
        self.db, self.browser, self.f = self.fx.db, self.fx.browser, self.fx.f
        self.messages = [self.fx.message(index) for index in range(3)]
        self.messages[0].update(subject="Audiencia: vence hoy", bodyPreview="Texto limitado privado", importance="low")
        self.messages[0]["from"]["emailAddress"]["address"] = "notificaciones@defensoria.gob.sv"
        self.messages[1].update(subject="Factura", importance="high")
        self.messages[2].update(subject="Seguimiento pendiente")
        self.f.graph.side_effect = self.graph
        self.path = "/mail/message-0=/create-task"

    def tearDown(self):
        self.fx.tearDown()

    def graph(self, url, **kwargs):
        index = self.fx.tokens.index(kwargs["headers"]["Authorization"].removeprefix("Bearer "))
        path = unquote(urlsplit(url).path)
        if path.endswith("/messages"):
            payload = {"value": [self.messages[index]]}
        elif path.endswith("/" + self.messages[index]["id"]):
            payload = self.messages[index]
        else:
            return self.fx.response(404)
        return self.fx.response(payload=deepcopy(payload))

    def client(self, index=0, name="Cliente propio", domain="defensoria.gob.sv"):
        return self.db.execute(Client.__table__.insert().values(
            organization_id=self.f.owners[index].organization_id, email_domain=domain, name=name, client_type="empresa",
        ).returning(Client.id)).scalar_one()

    def form(self, page=None, **changes):
        page = page if page is not None else self.browser.get(self.path)
        self.assertEqual(page.status_code, 200)
        intent = html.unescape(re.search(r'name="intent" value="([^"]+)"', page.text).group(1))
        return {"csrf": self.f.csrf, "intent": intent, "action": "create_task", "title": "Título confirmado",
                "description": "Nota manual", "priority": page.context["data"]["priority"], **changes}

    def test_list_detail_and_form_share_analysis(self):
        self.client()
        listing = self.browser.get("/mail")
        detail = self.browser.get("/mail/message-0=")
        form = self.browser.get(self.path)
        analyses = [listing.context["messages"][0]["analysis"], detail.context["message"]["analysis"], form.context["mail_context"]["analysis"]]
        self.assertTrue(all(result.priority == "urgent" for result in analyses))
        self.assertTrue(all(result.categories == analyses[0].categories for result in analyses))
        for response in (listing, detail, form):
            self.assertEqual(response.status_code, 200)
            self.assertIn("Cliente propio", response.text)
        self.assertIn("Sugerida: Urgente", listing.text)
        self.assertIn("Importancia baja", listing.text)
        self.assertIn("Análisis por reglas", detail.text)
        self.assertIn("audiencia", detail.text)

    def test_gmail_no_client_but_text_rules_still_apply(self):
        self.messages[0]["from"]["emailAddress"]["address"] = "persona@gmail.com"
        analysis = self.browser.get("/mail").context["messages"][0]["analysis"]
        self.assertNotIn("cliente", analysis.categories)
        self.assertEqual(analysis.priority, "urgent")
        self.assertEqual(analysis.entity.name, "")

    def test_task_initial_priority_and_confirmation(self):
        before = self.db.scalar(select(func.count()).select_from(Task))
        page = self.browser.get(self.path)
        self.assertEqual(page.context["data"]["priority"], "urgent")
        self.assertEqual(page.context["data"]["description"], "")
        self.assertEqual(self.db.scalar(select(func.count()).select_from(Task)), before)
        response = self.browser.post(self.path, data=self.form(page))
        self.assertEqual(response.status_code, 303)
        task_id = int(response.headers["location"].split("/")[2])
        self.assertEqual(self.db.scalar(select(Task.priority).where(Task.id == task_id)), "urgent")

    def test_user_changes_priority_and_validation_error_keeps_it(self):
        page = self.browser.get(self.path)
        invalid = self.browser.post(self.path, data=self.form(page, title="", priority="low"))
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(invalid.context["data"]["priority"], "low")
        response = self.browser.post(self.path, data=self.form(page, priority="low"))
        self.assertEqual(response.status_code, 303)
        task_id = int(response.headers["location"].split("/")[2])
        row = self.db.execute(select(Task.__table__).where(Task.id == task_id)).mappings().one()
        self.assertEqual(row["priority"], "low")
        self.assertNotIn("Texto limitado privado", str(row))
        self.assertNotIn("Cuerpo confidencial", str(row))
        self.assertEqual(row["description"], "Nota manual")

    def test_client_creation_keeps_manual_priority(self):
        page = self.browser.get(self.path)
        response = self.browser.post(self.path, data=self.form(page, action="create_client", priority="low",
            email_domain="defensoria.gob.sv", new_client_name="Defensoría revisada", new_client_type="institucion_publica"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["data"]["priority"], "low")

    def test_real_time_classification_without_database_writes_or_cache(self):
        statements = []
        def record(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement.strip().split()[0].upper())
        event.listen(self.db, "before_cursor_execute", record)
        try:
            for path in ("/mail", "/mail/message-0=", self.path):
                self.assertEqual(self.browser.get(path).status_code, 200)
            self.messages[0].update(subject="Boletín informativo", bodyPreview="")
            self.assertEqual(self.browser.get("/mail").context["messages"][0]["analysis"].priority, "low")
        finally:
            event.remove(self.db, "before_cursor_execute", record)
        self.assertFalse({"INSERT", "UPDATE", "DELETE", "CREATE", "ALTER"}.intersection(statements))

    def test_classification_never_uses_full_body_or_outlook_importance(self):
        self.messages[0].update(subject="Saludos", bodyPreview="", importance="high", body={"contentType": "text", "content": "urgente vence hoy"})
        self.messages[0]["from"]["emailAddress"]["address"] = "sender@example.invalid"
        response = self.browser.get("/mail/message-0=")
        self.assertIn("urgente vence hoy", response.context["message"]["body"])
        self.assertEqual(response.context["message"]["analysis"].priority, "normal")
        self.assertEqual(self.browser.get(self.path).context["data"]["priority"], "normal")
        selected = self.f.graph.call_args.kwargs["params"]["$select"].split(",")
        self.assertIn("bodyPreview", selected)
        self.assertNotIn("body", selected)

    def test_owner_tokens_and_analysis_are_isolated_even_for_admin(self):
        self.db.execute(update(User).where(User.id == self.f.owners[2].id).values(role="admin"))
        for index, priority in enumerate(("urgent", "normal", "normal")):
            self.f.sign_in(index)
            response = self.browser.get("/mail")
            self.assertEqual(response.context["messages"][0]["analysis"].priority, priority)
            self.assertTrue(self.f.graph.call_args.kwargs["headers"]["Authorization"] == "Bearer " + self.fx.tokens[index])
            for other in set(range(3)) - {index}:
                self.assertNotIn(self.messages[other]["subject"], response.text)
                self.assertEqual(self.browser.get(f"/mail/message-{other}=").status_code, 404)

    def test_domain_client_associations_scoped_to_organization(self):
        self.client(name="Entidad propia")
        self.client(index=2, name="Entidad ajena")
        self.messages[2]["from"] = deepcopy(self.messages[0]["from"])
        for index, expected, forbidden in ((0, "Entidad propia", "Entidad ajena"), (2, "Entidad ajena", "Entidad propia")):
            self.f.sign_in(index)
            response = self.browser.get("/mail")
            self.assertEqual(response.context["messages"][0]["analysis"].entity.name, expected)
            self.assertNotIn(forbidden, response.text)

    def test_internal_domains_configured_per_organization_only(self):
        self.messages[2]["from"] = deepcopy(self.messages[0]["from"])
        with patch.dict(mail_rules.INTERNAL_DOMAINS_BY_ORGANIZATION, {self.f.owners[0].organization_id: frozenset({"defensoria.gob.sv"})}):
            self.assertIn("interno", self.browser.get("/mail").context["messages"][0]["analysis"].categories)
            self.f.sign_in(2)
            self.assertNotIn("interno", self.browser.get("/mail").context["messages"][0]["analysis"].categories)

    def test_safe_html_and_no_sensitive_content_in_logs(self):
        self.client(name='<script>alert("cliente")</script>')
        self.messages[0]["subject"] = '<img src=x onerror="alert(1)"> audiencia vence hoy'
        stream = StringIO()
        handler = logging.StreamHandler(stream)
        logging.getLogger().addHandler(handler)
        try:
            for path in ("/mail", "/mail/message-0=", self.path):
                response = self.browser.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertNotIn('<script>alert("cliente")', response.text)
                self.assertNotIn('<img src=x', response.text)
                self.assertIn("&lt;script&gt;", response.text)
        finally:
            logging.getLogger().removeHandler(handler)
        sensitive = [self.messages[0]["subject"], self.messages[0]["bodyPreview"], "Cuerpo confidencial", *self.fx.tokens, *self.fx.refresh_tokens]
        self.assertTrue(all(value not in stream.getvalue() for value in sensitive))

    def test_graph_read_only_no_new_scopes(self):
        with patch("requests.post", side_effect=AssertionError("Escritura prohibida")), patch("requests.patch", side_effect=AssertionError("Escritura prohibida")), patch("requests.delete", side_effect=AssertionError("Escritura prohibida")):
            for path in ("/mail", "/mail/message-0=", self.path):
                self.assertEqual(self.browser.get(path).status_code, 200)
        self.assertEqual(MAIL_SCOPES, ["Mail.Read"])
        self.assertEqual(set(microsoft.SCOPES), {"User.Read", "Mail.Read"})
        self.assertEqual(self.f.graph.call_count, 3)
        self.assertTrue(all("/v1.0/me/messages" in call.args[0] for call in self.f.graph.call_args_list))

    def test_one_client_query_per_page_and_quiet_unclassified_ui(self):
        statements = []
        def record(conn, cursor, statement, parameters, context, executemany):
            if "FROM clients" in statement:
                statements.append(statement)
        event.listen(self.db, "before_cursor_execute", record)
        try:
            message = deepcopy(self.messages[1])
            message.update(subject="Saludos", bodyPreview="")
            self.f.graph.side_effect = lambda *a, **kw: self.fx.response(payload={"value": [dict(message, id=f"batch-{i}") for i in range(20)]})
            response = self.browser.get("/mail")
        finally:
            event.remove(self.db, "before_cursor_execute", record)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(statements), 1)
        self.assertNotIn('aria-label="Sugerencias por reglas"', response.text)
