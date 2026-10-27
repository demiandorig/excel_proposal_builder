"""The TikTok connection card of Admin -> Forecast in a REAL browser (headless Chromium through Playwright): it says what it knows without
waiting for the product table, the "Test the connection" button keeps the keyboard focus while it runs, and nothing the server sends is run
as HTML. The real admin page and admin.js, with /api/** answered in the browser. Skipped when Playwright or its Chromium is not installed."""
from __future__ import annotations

import http.server
import json
import threading
from pathlib import Path

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")

ROOT = Path(__file__).resolve().parents[1]
FORECAST = {"products": [], "coverage": [], "platforms": [], "families": [], "summary": {"enabled": 1, "total": 1, "platform_modeled": 0, "generic": 0, "tiktok": 1, "customized": 0}}
STATUS = {"configured": True, "missing": [], "advertiser": "…0001", "impressions_per_person": 5.5, "setup_hint": "x", "options": {}}
CHECKS = {"ok": True, "checks": [{"name": "Token", "ok": True, "warn": False, "seconds": 0.4, "detail": "fine"},
                                 {"name": "Estimate", "ok": True, "warn": True, "seconds": 1.2, "detail": "slow"}], "status": STATUS}


class _Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=str(ROOT / "app"), **k)

    def do_GET(self):
        if self.path.startswith("/admin_page.html"):
            raw = (ROOT / "app" / "templates" / "admin.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        else:
            super().do_GET()

    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def server():
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


@pytest.fixture(scope="module")
def browser():
    with playwright_sync.sync_playwright() as pw:
        try:
            b = pw.chromium.launch()
        except Exception as exc:                                   # noqa: BLE001 - no browser installed here
            pytest.skip(f"Chromium is not available: {exc}")
        yield b
        b.close()


class Admin:
    NAMES = {"api/constanza/tiktok/status": "status", "api/admin/forecast-map": "forecast", "api/admin/tiktok/test": "test"}

    def __init__(self):
        self.status = (200, dict(STATUS))
        self.forecast = (200, FORECAST)
        self.test = (200, CHECKS)
        self.hold: set = set()
        self.pending: list = []
        self.hits: list[str] = []

    def answer(self, route) -> None:
        path = route.request.url.split("//", 1)[1].split("/", 1)[1].split("?")[0]
        name = self.NAMES.get(path, "other")
        self.hits.append(name)
        status, body = {"status": self.status, "forecast": self.forecast, "test": self.test}.get(
            name, (200, {"items": [], "total": 0, "page": 1, "pages": 1, "proposals": []}))
        if name in self.hold:
            self.pending.append((name, route, status, body))
            return
        route.fulfill(status=status, content_type="application/json", body=json.dumps(body))

    def release(self, name) -> None:
        self.hold.discard(name)
        for item in [p for p in self.pending if p[0] == name]:
            self.pending.remove(item)
            item[1].fulfill(status=item[2], content_type="application/json", body=json.dumps(item[3]))


def open_admin(browser, server, admin):
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    page = context.new_page()
    page.route("**/api/**", admin.answer)
    page.route("**/fonts.googleapis.com/**", lambda r: r.abort())
    page.route("**/fonts.gstatic.com/**", lambda r: r.abort())
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{server}/admin_page.html")
    page.evaluate("document.querySelector('.admin-tab[data-tab=\"forecast\"]').click()")
    return context, page, errors


def test_the_card_says_its_status_without_waiting_for_the_product_table(browser, server):
    admin = Admin()
    admin.hold.add("forecast")                                      # the product table is slow
    context, page, errors = open_admin(browser, server, admin)
    page.wait_for_selector("#fc-tt-state .fc-badge")
    assert "Connected" in page.inner_text("#fc-tt-state")
    admin.release("forecast")
    context.close()
    assert errors == []


def test_the_card_still_says_what_it_can_when_the_product_table_fails(browser, server):
    admin = Admin()
    admin.forecast = (500, {"detail": "boom"})
    context, page, errors = open_admin(browser, server, admin)
    page.wait_for_selector("#fc-tt-state .fc-badge")
    assert "Connected" in page.inner_text("#fc-tt-state") and "Failed to load" in page.inner_text("#fc-body")
    context.close()
    assert errors == []


def test_the_test_button_keeps_the_keyboard_focus_while_the_test_runs_and_a_second_press_does_nothing(browser, server):
    admin = Admin()
    context, page, errors = open_admin(browser, server, admin)
    page.wait_for_selector("#fc-tt-state .fc-badge")
    page.wait_for_function("!document.getElementById('fc-tt-test').disabled")
    admin.hold.add("test")
    page.focus("#fc-tt-test")
    page.keyboard.press("Enter")
    page.wait_for_function("document.getElementById('fc-tt-test').getAttribute('aria-disabled') === 'true'")
    assert page.evaluate("document.activeElement.id") == "fc-tt-test"
    page.keyboard.press("Enter")
    assert admin.hits.count("test") == 1
    admin.release("test")
    page.wait_for_selector("#fc-tt-checks li")
    assert page.evaluate("document.activeElement.id") == "fc-tt-test" and page.get_attribute("#fc-tt-test", "aria-disabled") is None
    assert "Works, with something to look at" in page.inner_text("#fc-tt-note")
    context.close()
    assert errors == []


def test_text_from_the_server_in_the_checks_runs_nothing_even_the_seconds(browser, server):
    admin = Admin()
    evil = '<img src=x onerror="window.__xss=1">'
    admin.test = (200, {"ok": True, "checks": [{"name": evil, "ok": True, "warn": False, "seconds": evil, "detail": evil}], "status": STATUS})
    context, page, errors = open_admin(browser, server, admin)
    page.wait_for_function("!document.getElementById('fc-tt-test').disabled")
    page.click("#fc-tt-test")
    page.wait_for_selector("#fc-tt-checks li")
    page.wait_for_timeout(300)
    assert page.evaluate("window.__xss === undefined")
    assert page.locator("#fc-tt-checks img").count() == 0 and evil in page.inner_text("#fc-tt-checks")
    context.close()
    assert errors == []
