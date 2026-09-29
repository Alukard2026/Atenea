"""Templates reales con Microsoft simulado y datos revertidos al terminar."""

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_mail_rules import MailRuleIntegrationTests


def main():
    MailRuleIntegrationTests.setUpClass()
    fixture = MailRuleIntegrationTests()
    fixture.setUp()
    try:
        fixture.client(name="Defensoría del Consumidor")
        pages = {}
        for path in ("/mail", "/mail/message-0=", fixture.path):
            response = fixture.browser.get(path)
            assert response.status_code == 200
            pages[path] = {"html": response.text, "csp": response.headers["content-security-policy"]}
        sys.stdout.buffer.write(json.dumps(pages, ensure_ascii=False).encode("utf-8"))
    finally:
        fixture.tearDown()
        fixture.doCleanups()


if __name__ == "__main__":
    main()
