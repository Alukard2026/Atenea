"""QA con Chromium instalado: python tests/check_mail_ai_browser.py."""

import argparse
import json
from pathlib import Path
import subprocess
from urllib.parse import unquote, urlsplit, parse_qs

from playwright.sync_api import sync_playwright, expect


ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--screenshots", type=Path)
    args = parser.parse_args()
    python = ROOT / ".venv" / "Scripts" / "python.exe"
    if not python.exists():
        python = ROOT / ".venv" / "bin" / "python"
    result = subprocess.run([str(python), str(ROOT / "tests" / "mail_ai_browser_fixture.py")],
                            cwd=ROOT, capture_output=True, check=True)
    pages = json.loads(result.stdout)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            for width in (1440, 768, 390, 320):
                context = browser.new_context(viewport={"width": width, "height": 950})
                page = context.new_page()
                errors, analyzed, seeded = [], [], []
                disabled = False
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
                def route_request(route):
                    path = unquote(urlsplit(route.request.url).path)
                    if path == "/notifications/status":
                        route.fulfill(json={"pending": 0, "overdue": 0, "next_at": None})
                        return
                    if path in ("/static/styles.css", "/static/workspace.js", "/static/hours.js"):
                        route.fulfill(path=str(ROOT / "app" / path.lstrip("/")), content_type="text/css" if path.endswith("css") else "text/javascript")
                        return
                    if path == "/mail":
                        key = "list"
                    elif path == "/mail/message-0=":
                        key = "disabled" if disabled else "enabled"
                    elif path == "/mail/message-0=/analyze":
                        assert route.request.method == "POST"
                        assert set(parse_qs(route.request.post_data)) == {"csrf"}
                        analyzed.append(path)
                        key = "result"
                    elif path == "/mail/message-0=/ai/create-task":
                        assert route.request.method == "POST"
                        assert set(parse_qs(route.request.post_data)) == {"csrf", "draft"}
                        seeded.append(path)
                        key = "form"
                    else:
                        route.abort()
                        return
                    route.fulfill(body=pages[key]["html"], content_type="text/html", headers={"Content-Security-Policy": pages[key]["csp"]})
                page.route("**/*", route_request)
                page.goto("http://atenea.test/mail")
                assert not analyzed
                page.get_by_role("link", name="Audiencia: vence hoy", exact=True).click()
                expect(page.get_by_text("Este correo será enviado al proveedor de IA configurado para generar el análisis.")).to_be_visible()
                assert not analyzed
                expect(page.get_by_role("button", name="Analizar con IA", exact=True)).to_be_enabled()
                page.get_by_role("button", name="Analizar con IA", exact=True).click()
                expect(page.get_by_role("heading", name="Resumen", exact=True)).to_be_visible()
                expect(page.get_by_text("La IA y las reglas sugieren prioridades distintas. Tú decides cuál usar.")).to_be_visible()
                expect(page.get_by_text("Confirmar asistencia", exact=False).first).to_be_visible()
                assert len(analyzed) == 1
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                if args.screenshots:
                    args.screenshots.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(args.screenshots / f"ai-result-{width}.png"), full_page=True)
                page.get_by_role("button", name="Crear tarea con esta sugerencia", exact=True).click()
                expect(page.locator("#title")).to_have_value("Confirmar audiencia")
                expect(page.locator("#priority")).to_have_value("high")
                expect(page.locator("#due_date")).to_have_value("2026-10-02")
                page.locator("#priority").select_option("low")
                page.locator("#description").fill("Nota revisada")
                expect(page.locator("#priority")).to_have_value("low")
                assert len(analyzed) == len(seeded) == 1
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                disabled = True
                page.goto("http://atenea.test/mail/message-0=")
                expect(page.get_by_role("button", name="Analizar con IA", exact=True)).to_be_disabled()
                assert len(analyzed) == 1
                assert not errors, errors
                context.close()
        finally:
            browser.close()
    print("Chromium: 4 escenarios aprobados; IA manual, privacidad, comparación, formulario editable y flag desactivado.")


if __name__ == "__main__":
    main()
