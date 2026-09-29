"""HTML y JSON reales para QA visual, datos ficticios y rollback al salir."""
from datetime import date, datetime, time, timezone
import json
from pathlib import Path
import sys
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_calendar import CalendarTests
from test_mail_rules import MailRuleIntegrationTests


def main():
    pages = {}
    CalendarTests.setUpClass()
    fx = CalendarTests()
    fx.setUp()
    try:
        fx.sign_in(fx.colleague_id, fx.org_id)
        fx.create(title="Audiencia de revisión contractual", client_id=str(fx.client_id), project_id=str(fx.project_id), institution="Juzgado de lo Mercantil", location="Sala de audiencias 2", description="Revisar documentación y preparar la comparecencia.", reminder_at="2026-10-05T09:00")
        fx.create(event_type="meeting", title="Reunión de seguimiento", date="2026-10-06", start_time="14:00", end_time="15:00", client_id=str(fx.client_id))
        # Tasks use the owner currently logged in, still entirely synthetic.
        fx.task(user_id=fx.colleague_id, title="Presentar escrito de contestación", due_time=time(15), priority="high", client_id=fx.client_id)
        fx.task(user_id=fx.colleague_id, title="Revisar expediente", due_date=date(2026, 10, 7))
        fx.add_entry(user_id=fx.colleague_id, day=date(2026, 10, 5), activity="Revisión de contrato y preparación de audiencia")
        paths = ["/dashboard", "/calendar", "/calendar/new", "/tasks", "/tasks/new", "/hours", "/hours/history?month=2026-10", "/hours/week?week=2026-10-05", "/reports/month?month=2026-10", "/clients", "/projects", "/notifications", "/integrations/microsoft", "/settings/timezone", "/settings/workdays", "/settings/users", "/settings/users/new"]
        with patch("app.main.aware_now", return_value=datetime(2026, 10, 1, tzinfo=timezone.utc)):
            for path in paths:
                response = fx.browser.get(path)
                assert response.status_code == 200, path
                pages[path] = {"html": response.text, "csp": response.headers["content-security-policy"]}
        pages["events"] = fx.feed().json()
        for event in pages["events"]:
            url = event["url"]
            response = fx.browser.get(url)
            assert response.status_code == 200
            pages[url] = {"html": response.text, "csp": response.headers["content-security-policy"]}
            if event["id"].startswith("event-"):
                response = fx.browser.get(url + "/edit")
                assert response.status_code == 200
                pages[url + "/edit"] = {"html": response.text, "csp": response.headers["content-security-policy"]}
        response = fx.post("/calendar/new", fx.data(title=""))
        pages["invalid-form"] = {"html": response.text, "csp": response.headers["content-security-policy"]}
        fx.browser.cookies.clear()
        response = fx.browser.get("/login")
        assert response.status_code == 200
        pages["/login"] = {"html": response.text, "csp": response.headers["content-security-policy"]}
    finally:
        fx.tearDown()
        fx.doCleanups()
    MailRuleIntegrationTests.setUpClass()
    mail = MailRuleIntegrationTests()
    mail.setUp()
    try:
        mail.client(name="Defensoría del Consumidor")
        for path in ("/mail", "/mail/message-0="):
            response = mail.browser.get(path)
            assert response.status_code == 200
            pages[path] = {"html": response.text, "csp": response.headers["content-security-policy"]}
    finally:
        mail.tearDown()
        mail.doCleanups()
    sys.stdout.buffer.write(json.dumps(pages, ensure_ascii=False).encode("utf-8"))


if __name__ == "__main__":
    main()
