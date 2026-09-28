"""Verificación opcional en Chromium: python tests/check_notifications_browser.py.

Requiere Playwright/Chromium en el Python que ejecuta este script. El fixture
usa el venv de Atenea, revierte las filas y no contacta Microsoft ni Internet.
"""

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
    python = ROOT / ".venv" / "Scripts" / "python.exe"
    if not python.exists():
        python = ROOT / ".venv" / "bin" / "python"
    fixture = subprocess.run([str(python), str(ROOT / "tests" / "notification_browser_fixture.py")],
                             cwd=ROOT, capture_output=True, check=True)
    pages = json.loads(fixture.stdout)
    checks = 0
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            for role in ("user", "admin"):
                for width in (1440, 1101, 1024, 768, 390, 320):
                    context = browser.new_context(viewport={"width": width, "height": 900})
                    page = context.new_page()
                    errors = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
                    status = {"pending": 12, "overdue": 10, "next_at": None}
                    requests = []

                    def route_request(route):
                        path = urlsplit(route.request.url).path
                        if path == "/notifications/status":
                            requests.append(path)
                            route.fulfill(json=status)
                        elif path in ("/static/styles.css", "/static/workspace.js"):
                            route.fulfill(path=str(ROOT / "app" / path.lstrip("/")),
                                          content_type="text/css" if path.endswith("css") else "text/javascript")
                        elif path in pages[role]:
                            fixture_page = pages[role][path]
                            route.fulfill(body=fixture_page["html"], content_type="text/html",
                                          headers={"Content-Security-Policy": fixture_page["csp"]})
                        else:
                            route.abort()

                    page.route("**/*", route_request)
                    page.goto("http://atenea.test/dashboard")
                    expect(page.locator("#notification-banner")).to_be_visible()
                    mobile = page.get_by_role("button", name="Menú")
                    if width <= 1100:
                        expect(mobile).to_be_visible()
                        expect(page.locator("#workspace-navigation")).to_be_hidden()
                        mobile.click()
                        expect(mobile).to_have_attribute("aria-expanded", "true")
                    else:
                        expect(mobile).to_be_hidden()
                    expect(page.locator("#notification-count")).to_have_text("9+")
                    expect(page.get_by_role("button", name="Configuración")).to_have_count(1 if role == "admin" else 0)
                    for trigger in page.locator(".nav-trigger").all():
                        trigger.focus()
                        page.keyboard.press("Enter")
                        expect(trigger).to_have_attribute("aria-expanded", "true")
                        page.keyboard.press("Tab")
                        assert page.locator(":focus").evaluate("element => element.matches('.nav-submenu a')")
                        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                        page.keyboard.press("Escape")
                        expect(trigger).to_be_focused()
                        expect(trigger).to_have_attribute("aria-expanded", "false")
                    page.get_by_role("button", name="Ocultar aviso de recordatorios").click()
                    page.reload()
                    expect(page.locator("#notification-count")).to_have_text("9+")
                    expect(page.locator("#notification-banner")).to_be_hidden()
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                    if args.screenshots and role == "admin" and width in (1440, 768, 390):
                        args.screenshots.mkdir(parents=True, exist_ok=True)
                        page.screenshot(path=str(args.screenshots / f"dashboard-{width}.png"), full_page=True)
                    page.goto("http://atenea.test/mail")
                    expect(page.locator(".nav-active .nav-trigger")).to_have_text("Correo ▾")
                    expect(page.locator('a[aria-current="page"]')).to_have_text("Bandeja de correo")
                    page.goto("http://atenea.test/notifications")
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                    if role == "user":
                        expect(page.get_by_role("button", name="Posponer 15 minutos")).to_be_visible()
                        expect(page.get_by_role("button", name="Completar tarea")).to_be_visible()
                        if args.screenshots and width in (1440, 390):
                            page.screenshot(path=str(args.screenshots / f"notifications-{width}.png"), full_page=True)
                    assert not errors, errors
                    checks += 1
                    context.close()

            # Reloj del navegador: una petición/minuto y se restablece el aviso
            # solamente después de desaparecer todos los pendientes.
            context = browser.new_context()
            page = context.new_page()
            status = {"pending": 1, "overdue": 1, "next_at": None}
            requests = []
            role = "user"
            page.route("**/*", route_request)
            page.clock.install()
            page.goto("http://atenea.test/dashboard")
            expect(page.locator("#notification-banner")).to_be_visible()
            assert len(requests) == 1
            page.clock.fast_forward(59000)
            assert len(requests) == 1
            status["pending"] = 0
            page.clock.fast_forward(1000)
            expect(page.locator("#notification-count")).to_be_hidden()
            expect(page.locator("#notification-banner")).to_be_hidden()
            assert len(requests) == 2
            status["pending"] = 1
            page.clock.fast_forward(60000)
            expect(page.locator("#notification-banner")).to_be_visible()
            assert len(requests) == 3
            context.close()
            checks += 1

            # Sin JavaScript, los destinos y formularios siguen disponibles.
            context = browser.new_context(java_script_enabled=False, viewport={"width": 320, "height": 900})
            page = context.new_page()
            page.route("**/*", route_request)
            page.goto("http://atenea.test/notifications")
            expect(page.get_by_role("link", name="Bandeja de correo")).to_be_visible()
            expect(page.get_by_role("button", name="Completar tarea")).to_be_visible()
            context.close()
            checks += 1
        finally:
            browser.close()
    print(f"Chromium: {checks} escenarios aprobados; responsive, teclado, permisos, badge, polling y avisos.")


if __name__ == "__main__":
    main()
