"""QA visual del login simplificado y administración. python tests/check_users_browser.py."""
import argparse
import json
from pathlib import Path
import subprocess
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--screenshots", type=Path)
    args = parser.parse_args()
    result = subprocess.run([str(ROOT / ".venv/Scripts/python.exe"), str(ROOT / "tests/users_browser_fixture.py")], cwd=ROOT, capture_output=True, check=True)
    pages = json.loads(result.stdout)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            for width in (320, 768, 1024, 1440):
                context = browser.new_context(viewport={"width": width, "height": 950})
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
                def route_request(route):
                    path = urlsplit(route.request.url).path
                    if path in ("/static/styles.css", "/static/corporate.css", "/static/workspace.js"):
                        route.fulfill(path=str(ROOT / "app" / path.lstrip("/")), content_type="text/css" if path.endswith("css") else "text/javascript")
                    elif path == "/notifications/status":
                        route.fulfill(json={"pending": 0, "overdue": 0, "next_at": None})
                    else:
                        key = "invalid" if path == "/settings/users/new" and route.request.method == "POST" else path
                        assert key in pages
                        route.fulfill(body=pages[key]["html"], content_type="text/html", headers={"Content-Security-Policy": pages[key]["csp"]})
                page.route("**/*", route_request)
                for path in (k for k in pages if k != "invalid"):
                    page.goto("http://atenea.test" + path)
                    expect(page.locator("h1")).to_be_visible()
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (path, width)
                    if path == "/login":
                        expect(page.locator('input:not([type="hidden"])')).to_have_count(2)
                        expect(page.locator('[name="organization"]')).to_have_count(0)
                        expect(page.get_by_role("button", name="Iniciar sesión", exact=True)).to_be_visible()
                    if path == "/dashboard":
                        expect(page.locator('a[href="/settings/users"]')).to_have_count(0)
                    if args.screenshots:
                        args.screenshots.mkdir(parents=True, exist_ok=True)
                        page.screenshot(path=str(args.screenshots / f"{path.strip('/').replace('/', '-')}-{width}.png"), full_page=True)
                page.goto("http://atenea.test/settings/users/new")
                page.locator("#full_name").fill("Persona de prueba")
                page.locator("#email").fill("test@example.invalid")
                page.locator("#password").fill("synthetic-browser-value")
                page.locator("#password_confirmation").fill("synthetic-browser-other")
                page.get_by_role("button", name="Crear usuario", exact=True).click()
                expect(page.locator("#password")).to_have_value("")
                expect(page.locator("#password_confirmation")).to_have_value("")
                expect(page.locator("#password_confirmation")).to_have_attribute("aria-invalid", "true")
                expect(page.locator("#error-password_confirmation")).to_be_visible()
                assert not errors, errors
                context.close()
        finally:
            browser.close()
    print("Chromium OK: login y usuarios a 320/768/1024/1440; permisos, campos, errores accesibles y passwords sin repoblar.")


if __name__ == "__main__":
    main()
