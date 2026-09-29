"""QA real de FullCalendar y layout: python tests/check_calendar_browser.py."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
from urllib.parse import urlsplit, unquote
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--screenshots", type=Path)
    args = parser.parse_args()
    result = subprocess.run([str(ROOT / ".venv/Scripts/python.exe"), str(ROOT / "tests/calendar_browser_fixture.py")], cwd=ROOT, capture_output=True)
    if result.returncode:
        raise RuntimeError(result.stderr.decode("utf-8", errors="replace"))
    pages = json.loads(result.stdout)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            for width in (320, 375, 768, 1024, 1440):
                context = browser.new_context(viewport={"width": width, "height": 1000}, timezone_id="Asia/Tokyo")
                page = context.new_page()
                page.clock.set_fixed_time(datetime(2026, 10, 5, 15, tzinfo=timezone.utc))
                errors, requests = [], []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
                def route_request(route):
                    parsed = urlsplit(route.request.url)
                    path = unquote(parsed.path)
                    if path.startswith("/static/"):
                        resource = (ROOT / "app" / path.lstrip("/")).resolve()
                        assert resource.is_relative_to(ROOT / "app/static") and resource.is_file()
                        route.fulfill(path=str(resource), content_type="text/css" if path.endswith(".css") else "text/javascript")
                    elif path == "/notifications/status":
                        route.fulfill(json={"pending": 0, "overdue": 0, "next_at": None})
                    elif path == "/calendar/events":
                        requests.append(route.request.url)
                        route.fulfill(json=pages["events"])
                    else:
                        key = path + ("?" + parsed.query if path in ("/hours/history", "/hours/week", "/reports/month") else "")
                        if path == "/calendar/new" and route.request.method == "POST":
                            key = "invalid-form"
                        assert key in pages, key
                        route.fulfill(body=pages[key]["html"], content_type="text/html", headers={"Content-Security-Policy": pages[key]["csp"]})
                page.route("**/*", route_request)
                def overflow(label):
                    issues = page.evaluate("""() => ({width: innerWidth, total: document.documentElement.scrollWidth, items: [...document.querySelectorAll('body *')].filter(e => e.getBoundingClientRect().right > innerWidth + 1 && getComputedStyle(e).position !== 'fixed').slice(0,6).map(e => e.tagName + '.' + e.className)})""")
                    assert issues["total"] <= width, (width, label, issues)
                edit_path = next(path for path in pages if path.startswith("/calendar/") and path.endswith("/edit"))
                for path in ("/login", "/dashboard", "/calendar/new", edit_path, "/tasks", "/tasks/new", "/hours", "/hours/history?month=2026-10", "/hours/week?week=2026-10-05", "/reports/month?month=2026-10", "/clients", "/projects", "/notifications", "/integrations/microsoft", "/settings/timezone", "/settings/workdays", "/settings/users", "/settings/users/new", "/mail", "/mail/message-0="):
                    page.goto("http://atenea.test" + path)
                    expect(page.locator("h1")).to_be_visible()
                    overflow(path)
                    if path == "/notifications":
                        expect(page.get_by_role("link", name="Abrir evento", exact=True)).to_be_visible()
                        expect(page.locator('form[action^="/notifications/events/"]')).to_be_visible()
                        expect(page.get_by_role("button", name="Completar tarea", exact=True)).to_have_count(0)
                    if path == edit_path:
                        expect(page.locator("#title")).to_have_value("Audiencia de revisión contractual")
                        expect(page.locator("#start_time")).to_have_value("10:00")
                    if args.screenshots and path in ("/login", "/dashboard", "/calendar/new", edit_path, "/tasks", "/mail", "/notifications", "/settings/users", "/reports/month?month=2026-10", "/hours/history?month=2026-10"):
                        args.screenshots.mkdir(parents=True, exist_ok=True)
                        page.screenshot(path=str(args.screenshots / f"{path.split('?')[0].strip('/').replace('/', '-')}-{width}.png"), full_page=True)
                page.goto("http://atenea.test/calendar")
                expect(page.locator(".fc-toolbar-title")).to_be_visible()
                expect(page.locator("#calendar-status")).to_contain_text("entradas")
                for view, name in (("dayGridMonth", "Mes"), ("timeGridWeek", "Semana"), ("timeGridDay", "Día"), ("listWeek", "Agenda")):
                    page.get_by_role("button", name=name, exact=True).click()
                    expect(page.locator(f".fc-{view}-view")).to_be_visible()
                    expect(page.locator("#calendar-status")).to_contain_text("entradas")
                    if view.startswith("timeGrid"):
                        page.wait_for_function("[...document.querySelectorAll('.fc-scroller')].some(el => el.scrollTop > 100)")
                    overflow(view)
                    if args.screenshots:
                        page.screenshot(path=str(args.screenshots / f"calendar-{view}-{width}.png"), full_page=True)
                assert requests and all("start=" in url and "end=" in url and "timeZone=America" in url for url in requests)
                # Calendar uses the organization zone even though the browser is in Tokyo.
                expect(page.locator(".fc-list-event").filter(has_text="Audiencia de revisión contractual").filter(has_text="10:00").first).to_be_visible()
                page.get_by_role("button", name="Mes", exact=True).click()
                page.locator('.fc-daygrid-day[data-date="2026-10-08"] .fc-daygrid-day-frame').click(position={"x": 10, "y": 32})
                page.wait_for_url("**/calendar/new?date=2026-10-08&time=")
                expect(page.locator("#calendar-event-form")).to_be_visible()
                page.locator("#event_type").select_option("task")
                page.wait_for_url("**/tasks/new?**")
                expect(page.locator('form[action="/tasks/new"]')).to_be_visible()
                page.goto("http://atenea.test/calendar")
                if width < 768:
                    toggle = page.get_by_role("button", name="Menú", exact=True)
                    expect(toggle).to_be_visible()
                    expect(page.locator(".sidebar")).to_be_hidden()
                    toggle.click()
                    expect(page.locator(".sidebar")).to_be_visible()
                    expect(toggle).to_have_attribute("aria-expanded", "true")
                    assert page.evaluate("document.getElementById('main-content').inert")
                    page.keyboard.press("Escape")
                    expect(page.locator(".sidebar")).to_be_hidden()
                    expect(toggle).to_be_focused()
                else:
                    expect(page.locator(".sidebar")).to_be_visible()
                assert not errors, errors
                context.close()
        finally:
            browser.close()
    print("Chromium OK: 320/375/768/1024/1440, 20 pantallas, 4 vistas, edición, Avisos de eventos, zona distinta al navegador, clic, tareas, menú, foco y sin overflow.")


if __name__ == "__main__":
    main()
