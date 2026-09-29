"""QA opcional: python tests/check_mail_rules_browser.py [--screenshots DIR].

Playwright/Chromium en el intérprete de QA; no requiere Microsoft ni Internet.
El fixture usa el venv de Atenea. La suite comprueba el POST real por separado.
"""

import argparse
import json
from pathlib import Path
import subprocess
from urllib.parse import unquote, urlsplit

from playwright.sync_api import sync_playwright, expect


ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--screenshots", type=Path)
    args = parser.parse_args()
    python = ROOT / ".venv" / "Scripts" / "python.exe"
    if not python.exists():
        python = ROOT / ".venv" / "bin" / "python"
    fixture = subprocess.run([str(python), str(ROOT / "tests" / "mail_rules_browser_fixture.py")],
                             cwd=ROOT, capture_output=True, check=True)
    pages = json.loads(fixture.stdout)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            for width in (1440, 768, 390, 320):
                context = browser.new_context(viewport={"width": width, "height": 950})
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)

                def route_request(route):
                    path = unquote(urlsplit(route.request.url).path)
                    if path == "/notifications/status":
                        route.fulfill(json={"pending": 0, "overdue": 0, "next_at": None})
                    elif path in ("/static/styles.css", "/static/corporate.css", "/static/workspace.js", "/static/hours.js"):
                        route.fulfill(path=str(ROOT / "app" / path.lstrip("/")),
                                      content_type="text/css" if path.endswith("css") else "text/javascript")
                    elif path in pages:
                        route.fulfill(body=pages[path]["html"], content_type="text/html",
                                      headers={"Content-Security-Policy": pages[path]["csp"]})
                    else:
                        route.abort()

                page.route("**/*", route_request)
                page.goto("http://atenea.test/mail")
                expect(page.locator('[aria-label="Sugerencias por reglas"]')).to_contain_text("Sugerida: Urgente")
                expect(page.locator('[aria-label="Sugerencias por reglas"]')).to_contain_text("Legal")
                expect(page.locator('[aria-label="Sugerencias por reglas"]')).to_contain_text("Defensoría del Consumidor")
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                if args.screenshots:
                    args.screenshots.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(args.screenshots / f"mail-{width}.png"), full_page=True)
                page.get_by_role("link", name="Audiencia: vence hoy", exact=True).click()
                expect(page.get_by_role("heading", name="Análisis por reglas")).to_be_visible()
                expect(page.locator('.mail-rule-analysis')).to_contain_text("audiencia")
                expect(page.locator('.mail-rule-analysis')).to_contain_text("vence hoy")
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                if args.screenshots:
                    page.screenshot(path=str(args.screenshots / f"detail-{width}.png"), full_page=True)
                page.get_by_role("link", name="Crear tarea desde este correo", exact=True).click()
                expect(page.locator("#priority")).to_have_value("urgent")
                expect(page.locator("#description")).to_have_value("")
                page.locator(".mail-rule-context summary").click()
                expect(page.get_by_role("heading", name="Análisis por reglas")).to_be_visible()
                page.locator("#priority").select_option("low")
                expect(page.locator("#priority")).to_have_value("low")
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                assert not errors, errors
                context.close()
        finally:
            browser.close()
    print("Chromium: 4 escenarios aprobados; bandeja, badges, motivos, prioridad editable y responsive.")


if __name__ == "__main__":
    main()
