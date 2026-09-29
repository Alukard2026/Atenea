"""Pantallas reales, cuentas ficticias y rollback; no entrega contraseñas al navegador QA."""
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_users import UserManagementTests


def main():
    UserManagementTests.setUpClass()
    fixture = UserManagementTests()
    fixture.setUp()
    pages = {}
    try:
        def save(key, response):
            assert response.status_code in (200, 422)
            assert fixture.initial_password not in response.text
            pages[key] = {"html": response.text, "csp": response.headers["content-security-policy"]}
        fixture.browser.cookies.clear()
        save("/login", fixture.browser.get("/login"))
        fixture.sign_in(fixture.colleague_id, fixture.org_id)
        for path in ("/settings/users", "/settings/users/new", f"/settings/users/{fixture.user_id}/edit", f"/settings/users/{fixture.user_id}/password"):
            save(path, fixture.browser.get(path))
        save("invalid", fixture.post("/settings/users/new", fixture.data(password_confirmation="mismatch")))
        fixture.sign_in(fixture.user_id, fixture.org_id)
        save("/dashboard", fixture.browser.get("/dashboard"))
        sys.stdout.buffer.write(json.dumps(pages, ensure_ascii=False).encode("utf-8"))
    finally:
        fixture.tearDown()
        fixture.doCleanups()


if __name__ == "__main__":
    main()
