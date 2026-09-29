"""Pantallas reales de IA con proveedor mock y rollback de PostgreSQL."""

from dataclasses import replace
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_mail_ai import MailAITests


def main():
    MailAITests.setUpClass()
    fixture = MailAITests()
    fixture.setUp()
    try:
        fixture.fx.client(name="Defensoría del Consumidor")
        pages = {}
        def save(key, response):
            assert response.status_code == 200
            pages[key] = {"html": response.text, "csp": response.headers["content-security-policy"]}
        save("list", fixture.browser.get("/mail"))
        save("enabled", fixture.browser.get("/mail/message-0="))
        assert fixture.provider.analyze_email.call_count == 0
        result = fixture.analyze()
        save("result", result)
        save("form", fixture.seed(fixture.token(result)))
        assert fixture.provider.analyze_email.call_count == 1
        fixture.f.app.state.settings = replace(fixture.settings, ai_enabled=False)
        save("disabled", fixture.browser.get("/mail/message-0="))
        sys.stdout.buffer.write(json.dumps(pages, ensure_ascii=False).encode("utf-8"))
    finally:
        fixture.tearDown()
        fixture.doCleanups()


if __name__ == "__main__":
    main()
