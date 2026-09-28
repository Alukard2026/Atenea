"""HTML real con datos sintéticos; ejecutado por check_notifications_browser.py."""

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_notifications import NotificationTests


def main():
    NotificationTests.setUpClass()
    fixture = NotificationTests()
    fixture.setUp()
    try:
        fixture.add_task(title="Revisar expediente de prueba", priority="urgent", client_id=fixture.client_id, project_id=fixture.project_id)
        pages = {}
        for role, user_id in (("user", fixture.user_id), ("admin", fixture.colleague_id)):
            fixture.sign_in(user_id, fixture.org_id)
            pages[role] = {}
            for path in ("/dashboard", "/notifications", "/mail", "/tasks/new"):
                response = fixture.browser.get(path)
                assert response.status_code == 200
                pages[role][path] = {"html": response.text, "csp": response.headers["content-security-policy"]}
        sys.stdout.buffer.write(json.dumps(pages, ensure_ascii=False).encode("utf-8"))
    finally:
        fixture.tearDown()
        fixture.doCleanups()


if __name__ == "__main__":
    main()
