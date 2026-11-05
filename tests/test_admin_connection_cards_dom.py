"""Admin -> Forecast in a REAL browser (headless Chromium through Playwright): the connection cards, the "What it computes" select and the summary cards
are all drawn from the method list the server keeps (GET /api/admin/constanza/methods, and `methods` in the forecast map), so a new live-API method
appears with no edit to the page.

What is pinned, for every API method (TikTok and Meta, and a made-up third one): the card says what it knows without waiting for the product table,
it still does when the product table fails, the "Test the connection" button keeps the keyboard focus while it runs and a second press does nothing,
one card's test does not hold another card's button, nothing the server sends is run as HTML, and the cards can be told apart and heard (a named region and
a named button per card, the state and the outcome of a test are status regions, and the hint text reads at 4.5:1). The real admin page and admin.js, with
/api/** answered in the browser. Skipped when Playwright or its Chromium is not installed."""
from __future__ import annotations

import http.server
import json
import threading
from pathlib import Path

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")

ROOT = Path(__file__).resolve().parents[1]

METHODS = [
    {"key": "platform", "label": "Platform data", "short_label": "Platform data", "kind": "dataset", "builtin": True, "description": "x", "secrets": [],
     "status_url": None, "test_url": None, "default_products": [], "summary_text": "modeled from the DMA avails data"},
    {"key": "generic", "label": "Population share", "short_label": "Population share", "kind": "dataset", "builtin": True, "description": "x", "secrets": [],
     "status_url": None, "test_url": None, "default_products": [], "summary_text": "the line's budget, spread by population"},
    {"key": "tiktok", "label": "TikTok API (live)", "short_label": "TikTok", "kind": "api", "builtin": False,
     "description": "The Tiktok Ads product asks TikTok's own audience-size estimator how many people match a targeting.",
     "secrets": ["TIKTOK_ACCESS_TOKEN", "TIKTOK_ADVERTISER_ID"], "status_url": "/api/constanza/tiktok/status", "test_url": "/api/admin/constanza/tiktok/test",
     "default_products": ["Tiktok Ads"], "summary_text": "a live audience-size estimate from TikTok"},
    {"key": "meta", "label": "Meta API (live)", "short_label": "Meta", "kind": "api", "builtin": False,
     "description": "The paid Facebook & Instagram products ask Meta's own audience estimator how many accounts match a targeting.",
     "secrets": ["META_ACCESS_TOKEN", "META_AD_ACCOUNT_ID"], "status_url": "/api/constanza/meta/status", "test_url": "/api/admin/constanza/meta/test",
     "default_products": ["Facebook & Instagram Ads | Awareness"], "summary_text": "a live audience-size estimate from Meta"},
]
ECHO = {"key": "echo", "label": "Echo API (live)", "short_label": "Echo", "kind": "api", "builtin": False, "description": "A made-up method for this test.",
        "secrets": ["ECHO_TOKEN"], "status_url": "/api/constanza/echo/status", "test_url": "/api/admin/constanza/echo/test", "default_products": [], "summary_text": "an echo"}

PRODUCTS = [
    {"name": "Tiktok Ads", "family": "Social", "enabled": True, "method": "tiktok", "platform": None, "hispanic_weighted": False, "win_rate_override": None,
     "default": {"enabled": True, "method": "tiktok", "platform": None}, "customized": False},
    {"name": "Facebook & Instagram Ads | Awareness", "family": "Social", "enabled": True, "method": "meta", "platform": None, "hispanic_weighted": False,
     "win_rate_override": None, "default": {"enabled": True, "method": "meta", "platform": None}, "customized": False},
    {"name": "Facebook & Instagram Ads on EVC Radio Pages", "family": "Social", "enabled": True, "method": "meta", "platform": None, "hispanic_weighted": False,
     "win_rate_override": None, "default": {"enabled": False, "method": "generic", "platform": None}, "customized": True},
    {"name": "Entravision Plus - Roku Ads", "family": "Entravision Plus", "enabled": True, "method": "platform", "platform": "roku", "hispanic_weighted": False,
     "win_rate_override": None, "default": {"enabled": True, "method": "platform", "platform": "roku"}, "customized": False},
]


def forecast_payload(methods=None, products=None) -> dict:
    return {"products": products if products is not None else PRODUCTS, "coverage": [], "platforms": [{"key": "roku", "label": "Roku"}], "families": ["Entravision Plus", "Social"],
            "methods": methods if methods is not None else METHODS,
            "summary": {"enabled": 4, "total": 4, "platform_modeled": 1, "generic": 0, "tiktok": 1, "meta": 2, "echo": 0, "customized": 1}}


def status_for(key: str, configured: bool = True) -> dict:
    base = {"configured": configured, "missing": [] if configured else {"tiktok": ["TIKTOK_ACCESS_TOKEN", "TIKTOK_ADVERTISER_ID"],
            "meta": ["META_ACCESS_TOKEN (it has a space or a character a token cannot hold: copy it again)", "META_AD_ACCOUNT_ID"]}.get(key, ["ECHO_TOKEN"]),
            "account": "…0001", "setup_hint": "x"}
    base["scenarios"] = {"multipliers": {"conservative": 4.3, "mid": 5.5, "upper": 8.6}}
    return base


def checks_for(key: str) -> dict:
    return {"ok": True, "checks": [{"name": "Token", "ok": True, "warn": False, "seconds": 0.4, "detail": "fine"},
                                   {"name": "Estimate", "ok": True, "warn": True, "seconds": 1.2, "detail": "slow"}], "status": status_for(key)}


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


class _Server(http.server.ThreadingHTTPServer):
    """A page asks for a dozen files at once: the default backlog (5) drops some of them now and then, and a script that never arrives looks like a bug."""
    request_queue_size = 128
    daemon_threads = True


@pytest.fixture(scope="module")
def server():
    httpd = _Server(("127.0.0.1", 0), _Handler)
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
    """What /api answers for the admin page. A request is named after the route: `methods`, `forecast`, `status:<key>`, `test:<key>`, `save`."""

    def __init__(self, methods=None):
        self.methods = (200, {"methods": list(methods if methods is not None else METHODS)})
        self.forecast = (200, forecast_payload(methods))
        self.status = {key: (200, status_for(key)) for key in ("tiktok", "meta", "echo")}
        self.test = {key: (200, checks_for(key)) for key in ("tiktok", "meta", "echo")}
        self.hold: set = set()
        self.pending: list = []
        self.hits: list[str] = []
        self.saved: list[dict] = []

    def name_of(self, route) -> str:
        req = route.request
        path = req.url.split("//", 1)[1].split("/", 1)[1].split("?")[0]
        if path == "api/admin/constanza/methods":
            return "methods"
        if path == "api/admin/forecast-map":
            return "save" if req.method == "PUT" else "forecast"
        parts = path.split("/")
        if len(parts) == 4 and parts[:3] == ["api", "constanza", parts[2]] and parts[3] == "status":
            return f"status:{parts[2]}"
        if len(parts) == 5 and parts[:3] == ["api", "admin", "constanza"] and parts[4] == "test":
            return f"test:{parts[3]}"
        return "other"

    def answer(self, route) -> None:
        name = self.name_of(route)
        self.hits.append(name)
        if name == "methods":
            status, body = self.methods
        elif name == "forecast":
            status, body = self.forecast
        elif name == "save":
            self.saved.append(json.loads(route.request.post_data or "{}"))
            status, body = 200, self.forecast[1]
        elif name.startswith("status:"):
            status, body = self.status.get(name.split(":")[1], (404, {"detail": "no such method"}))
        elif name.startswith("test:"):
            status, body = self.test.get(name.split(":")[1], (404, {"detail": "no such method"}))
        else:
            status, body = 200, {"items": [], "total": 0, "page": 1, "pages": 1, "proposals": []}
        if name in self.hold:
            self.pending.append((name, route, status, body))
            return
        route.fulfill(status=status, content_type="application/json", body=json.dumps(body))

    def release(self, name) -> None:
        self.hold.discard(name)
        for item in [p for p in self.pending if p[0] == name]:
            self.pending.remove(item)
            item[1].fulfill(status=item[2], content_type="application/json", body=json.dumps(item[3]))


def open_admin(browser, server, admin, viewport=None):
    context = browser.new_context(viewport=viewport or {"width": 1280, "height": 900})
    page = context.new_page()
    page.route("**/api/**", admin.answer)
    page.route("**/fonts.googleapis.com/**", lambda r: r.abort())
    page.route("**/fonts.gstatic.com/**", lambda r: r.abort())
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{server}/admin_page.html")
    page.evaluate("document.querySelector('.admin-tab[data-tab=\"forecast\"]').click()")
    return context, page, errors


KEYS = pytest.mark.parametrize("key", ["tiktok", "meta"])


def ready(page, *keys: str) -> None:
    """Waits until each card is drawn (the method list arrives first) and its test button is on."""
    for key in keys:
        page.wait_for_function(f"(() => {{ const b = document.getElementById('fc-conn-{key}-test'); return !!b && !b.disabled; }})()")


def ids(key: str) -> dict:
    return {"state": f"#fc-conn-{key}-state", "test": f"fc-conn-{key}-test", "note": f"#fc-conn-{key}-note", "checks": f"#fc-conn-{key}-checks"}


# --------------------------------------------------------------------------------------------------------------------------------
# The cards come from the method list
# --------------------------------------------------------------------------------------------------------------------------------

def test_one_card_per_api_method_and_none_for_the_data_methods(browser, server):
    admin = Admin()
    context, page, errors = open_admin(browser, server, admin)
    page.wait_for_selector("#fc-conn-meta-state .fc-badge")
    cards = page.eval_on_selector_all("#fc-connections [data-conn]", "els => els.map(e => e.dataset.conn)")
    assert cards == ["tiktok", "meta"]
    heads = page.eval_on_selector_all("#fc-connections h2", "els => els.map(e => e.textContent)")
    assert heads == ["TikTok connection", "Meta connection"]
    text = page.inner_text("#fc-connections")
    assert "META_ACCESS_TOKEN" in text and "TIKTOK_ADVERTISER_ID" in text          # the secrets are named, from the registry
    context.close()
    assert errors == []


def test_a_method_added_to_the_server_list_gets_its_card_with_no_change_to_the_page(browser, server):
    admin = Admin(methods=METHODS + [ECHO])
    context, page, errors = open_admin(browser, server, admin)
    page.wait_for_selector("#fc-conn-echo-state .fc-badge")
    assert "Connected" in page.inner_text("#fc-conn-echo-state")
    assert page.inner_text("#fc-connections h2:has-text('Echo')") == "Echo connection"
    assert page.locator("#fc-conn-echo-test").is_enabled()
    page.click("#fc-conn-echo-test")
    page.wait_for_selector("#fc-conn-echo-checks li")
    assert admin.hits.count("test:echo") == 1
    context.close()
    assert errors == []


def test_the_page_names_no_live_method_itself():
    """The generation is real: nothing in the page or its script spells out a method's key in an id, a class or an option."""
    html = (ROOT / "app" / "templates" / "admin.html").read_text(encoding="utf-8")
    js = (ROOT / "app" / "static" / "admin.js").read_text(encoding="utf-8")
    for key in ("tiktok", "meta"):
        assert f'value="{key}"' not in html and f'value="{key}"' not in js
    assert "fc-tt" not in html and "fc-tt" not in js
    assert 'id="fc-connections"' in html


@KEYS
def test_the_card_says_its_status_without_waiting_for_the_product_table(browser, server, key):
    admin = Admin()
    admin.hold.add("forecast")                                      # the product table is slow
    context, page, errors = open_admin(browser, server, admin)
    page.wait_for_selector(f"{ids(key)['state']} .fc-badge")
    assert "Connected" in page.inner_text(ids(key)["state"])
    assert "ad account …0001" in page.inner_text(ids(key)["state"]) and "4.3 / 5.5 / 8.6" in page.inner_text(ids(key)["state"])
    admin.release("forecast")
    context.close()
    assert errors == []


@KEYS
def test_the_card_still_says_what_it_can_when_the_product_table_fails(browser, server, key):
    admin = Admin()
    admin.forecast = (500, {"detail": "boom"})
    context, page, errors = open_admin(browser, server, admin)
    page.wait_for_selector(f"{ids(key)['state']} .fc-badge")
    assert "Connected" in page.inner_text(ids(key)["state"]) and "Failed to load" in page.inner_text("#fc-body")
    context.close()
    assert errors == []


def test_when_the_method_list_fails_the_page_says_so_and_the_table_still_loads(browser, server):
    admin = Admin()
    admin.methods = (500, {"detail": "no registry"})
    context, page, errors = open_admin(browser, server, admin)
    page.wait_for_selector("#fc-connections [data-conn-error]")
    assert "Couldn't read the connections: no registry" in page.inner_text("#fc-connections")
    page.wait_for_selector("#fc-body tr[data-fc-product]")                   # the table, whose select comes from the forecast payload, is unaffected
    assert page.locator("#fc-body tr").first.locator("select[data-fc=method] option").count() == 4
    context.close()
    assert errors == []


@KEYS
def test_not_connected_names_what_to_add_or_fix_and_keeps_the_button_off(browser, server, key):
    admin = Admin()
    admin.status[key] = (200, status_for(key, configured=False))
    context, page, errors = open_admin(browser, server, admin)
    page.wait_for_selector(f"{ids(key)['state']} .fc-badge")
    state = page.inner_text(ids(key)["state"])
    assert "Not connected" in state and "in the app's secrets" in state
    assert page.locator(f"#{ids(key)['test']}").is_disabled()
    if key == "meta":
        # one secret is set but cannot be right: its name stays in <code>, the reason follows it in words
        assert page.locator(f"{ids(key)['state']} code").all_inner_texts() == ["META_ACCESS_TOKEN", "META_AD_ACCOUNT_ID"]
        assert "fix META_ACCESS_TOKEN (it has a space" in state and "add META_AD_ACCOUNT_ID" in state
    else:
        assert page.locator(f"{ids(key)['state']} code").all_inner_texts() == ["TIKTOK_ACCESS_TOKEN", "TIKTOK_ADVERTISER_ID"]
        assert "add TIKTOK_ACCESS_TOKEN and add TIKTOK_ADVERTISER_ID" in state
    context.close()
    assert errors == []


def test_a_status_in_the_older_tiktok_shape_still_reads(browser, server):
    """Before the status shape was standardised TikTok said `advertiser` (not `account`) and one `impressions_per_person`: a card still reads it."""
    admin = Admin()
    admin.status["tiktok"] = (200, {"configured": True, "missing": [], "advertiser": "…0001", "impressions_per_person": 5.5, "setup_hint": "x", "options": {}})
    context, page, errors = open_admin(browser, server, admin)
    page.wait_for_selector("#fc-conn-tiktok-state .fc-badge")
    assert "ad account …0001 · impressions = lower-end audience × 5.5" in page.inner_text("#fc-conn-tiktok-state")
    context.close()
    assert errors == []


def test_the_select_still_offers_every_method_when_the_forecast_map_carries_no_list(browser, server):
    """The forecast map's own `methods` is what the select reads; if a server answers without it, the light list the cards came from stands in."""
    admin = Admin()
    admin.forecast = (200, {k: v for k, v in forecast_payload().items() if k != "methods"})
    context, page, errors = open_admin(browser, server, admin)
    page.wait_for_selector("#fc-body tr[data-fc-product]")
    options = page.locator("#fc-body tr").first.locator("select[data-fc=method] option").all_inner_texts()
    assert options == ["Platform data", "Population share", "TikTok API (live)", "Meta API (live)"]
    context.close()
    assert errors == []


@KEYS
def test_a_status_that_fails_to_load_says_so_in_words(browser, server, key):
    admin = Admin()
    admin.status[key] = (503, {"detail": "down for now"})
    context, page, errors = open_admin(browser, server, admin)
    page.wait_for_selector(ids(key)["state"])
    page.wait_for_function(f"document.querySelector('{ids(key)['state']}').textContent.includes('down for now')")
    assert f"Couldn't read the {'TikTok' if key == 'tiktok' else 'Meta'} status: down for now" in page.inner_text(ids(key)["state"])
    context.close()
    assert errors == []


# --------------------------------------------------------------------------------------------------------------------------------
# The test button
# --------------------------------------------------------------------------------------------------------------------------------

@KEYS
def test_the_test_button_keeps_the_keyboard_focus_while_the_test_runs_and_a_second_press_does_nothing(browser, server, key):
    admin = Admin()
    context, page, errors = open_admin(browser, server, admin)
    test_id = ids(key)["test"]
    page.wait_for_selector(f"{ids(key)['state']} .fc-badge")
    ready(page, key)
    admin.hold.add(f"test:{key}")
    page.focus(f"#{test_id}")
    page.keyboard.press("Enter")
    page.wait_for_function(f"document.getElementById('{test_id}').getAttribute('aria-disabled') === 'true'")
    assert page.evaluate("document.activeElement.id") == test_id
    page.keyboard.press("Enter")
    assert admin.hits.count(f"test:{key}") == 1
    admin.release(f"test:{key}")
    page.wait_for_selector(f"{ids(key)['checks']} li")
    assert page.evaluate("document.activeElement.id") == test_id and page.get_attribute(f"#{test_id}", "aria-disabled") is None
    assert "Works, with something to look at" in page.inner_text(ids(key)["note"])
    context.close()
    assert errors == []


def test_one_cards_test_does_not_hold_the_other_cards_button(browser, server):
    admin = Admin()
    context, page, errors = open_admin(browser, server, admin)
    ready(page, "tiktok", "meta")
    admin.hold.add("test:tiktok")
    page.click("#fc-conn-tiktok-test")
    page.wait_for_function("document.getElementById('fc-conn-tiktok-test').getAttribute('aria-disabled') === 'true'")
    assert page.get_attribute("#fc-conn-meta-test", "aria-disabled") is None
    page.click("#fc-conn-meta-test")
    page.wait_for_selector("#fc-conn-meta-checks li")
    assert admin.hits.count("test:meta") == 1 and admin.hits.count("test:tiktok") == 1
    assert page.inner_text("#fc-conn-tiktok-checks") == ""                     # the other card's checks are not mixed in
    admin.release("test:tiktok")
    page.wait_for_selector("#fc-conn-tiktok-checks li")
    context.close()
    assert errors == []


@KEYS
def test_the_test_posts_to_the_methods_own_route(browser, server, key):
    admin = Admin()
    context, page, errors = open_admin(browser, server, admin)
    seen: list[str] = []
    page.on("request", lambda r: seen.append(f"{r.method} {r.url.split('//', 1)[1].split('/', 1)[1]}") if "/test" in r.url else None)
    ready(page, key)
    page.click(f"#{ids(key)['test']}")
    page.wait_for_selector(f"{ids(key)['checks']} li")
    assert seen == [f"POST api/admin/constanza/{key}/test"]
    context.close()
    assert errors == []


def test_an_address_in_the_list_that_is_not_an_app_address_is_never_fetched(browser, server):
    admin = Admin()
    evil = [dict(m) for m in METHODS]
    for m in evil:
        if m["key"] == "meta":
            m["status_url"] = "https://evil.example/status"
            m["test_url"] = "//evil.example/test"
    admin.methods = (200, {"methods": evil})
    context, page, errors = open_admin(browser, server, admin)
    requested: list[str] = []
    page.on("request", lambda r: requested.append(r.url))
    page.wait_for_selector("#fc-conn-meta-state .fc-badge")                    # the conventional status route answered instead
    ready(page, "meta")
    page.click("#fc-conn-meta-test")
    page.wait_for_selector("#fc-conn-meta-checks li")
    assert not any("evil.example" in u for u in requested)
    assert admin.hits.count("test:meta") == 1
    context.close()
    assert errors == []


@KEYS
def test_text_from_the_server_in_the_checks_runs_nothing_even_the_seconds(browser, server, key):
    admin = Admin()
    evil = '<img src=x onerror="window.__xss=1">'
    admin.test[key] = (200, {"ok": True, "checks": [{"name": evil, "ok": True, "warn": False, "seconds": evil, "detail": evil}], "status": status_for(key)})
    context, page, errors = open_admin(browser, server, admin)
    ready(page, key)
    page.click(f"#{ids(key)['test']}")
    page.wait_for_selector(f"{ids(key)['checks']} li")
    page.wait_for_timeout(300)
    assert page.evaluate("window.__xss === undefined")
    assert page.locator(f"{ids(key)['checks']} img").count() == 0 and evil in page.inner_text(ids(key)["checks"])
    context.close()
    assert errors == []


def test_text_from_the_method_list_and_the_status_runs_nothing(browser, server):
    admin = Admin()
    evil = '<img src=x onerror="window.__xss=1">'
    bad = [dict(m) for m in METHODS]
    for m in bad:
        if m["key"] == "meta":
            m.update(short_label=evil, label=evil, description=evil, secrets=[evil], summary_text=evil)
    admin.methods = (200, {"methods": bad})
    admin.forecast = (200, forecast_payload(methods=bad))
    bare = "<img/src=x/onerror=window.__xss=1>"           # no space in it: a name is cut at the first space, so this one is a whole name that would run if it were not escaped
    admin.status["meta"] = (200, {**status_for("meta", configured=False), "missing": [bare, f"{evil} {evil}", f"NAME {evil}"], "account": evil})
    context, page, errors = open_admin(browser, server, admin)
    page.wait_for_selector("#fc-conn-meta-state .fc-badge")
    page.wait_for_selector("#fc-body tr[data-fc-product]")
    page.wait_for_timeout(300)
    assert page.evaluate("window.__xss === undefined")
    for where in ("#fc-connections", "#fc-summary", "#fc-body"):
        assert page.locator(f"{where} img").count() == 0, where
    assert evil in page.inner_text("#fc-connections") and bare in page.inner_text("#fc-conn-meta-state")
    context.close()
    assert errors == []


# --------------------------------------------------------------------------------------------------------------------------------
# Telling the cards apart and hearing what happened (screen readers, and text that can be read)
# --------------------------------------------------------------------------------------------------------------------------------

def test_each_card_is_a_named_region_and_its_button_says_which_connection_it_tests(browser, server):
    """Two buttons that both said "Test the connection" could not be told apart in a list of buttons. The accessible name is computed by the browser."""
    admin = Admin(methods=METHODS + [ECHO])
    context, page, errors = open_admin(browser, server, admin)
    ready(page, "tiktok", "meta", "echo")
    for key, word in (("tiktok", "TikTok"), ("meta", "Meta"), ("echo", "Echo")):
        assert page.get_by_role("region", name=f"{word} connection", exact=True).count() == 1
        button = page.get_by_role("button", name=f"Test the connection to {word}", exact=True)
        assert button.count() == 1 and button.get_attribute("id") == ids(key)["test"]
        assert button.inner_text() == "Test the connection" and button.get_attribute("aria-label").startswith(button.inner_text())      # the visible words open the name
        assert page.locator(f'[data-conn="{key}"]').get_attribute("aria-labelledby") == f"fc-conn-{key}-head"
    assert page.get_by_role("button", name="Test the connection", exact=True).count() == 0                  # none is nameless
    context.close()
    assert errors == []


def test_a_name_from_the_server_is_text_in_the_buttons_name_and_the_regions(browser, server):
    admin = Admin()
    evil = '"><img src=x onerror="window.__xss=1">'
    bad = [dict(m) for m in METHODS]
    for m in bad:
        if m["key"] == "meta":
            m.update(short_label=evil, label=evil)
    admin.methods = (200, {"methods": bad})
    context, page, errors = open_admin(browser, server, admin)
    page.wait_for_selector("#fc-conn-meta-state .fc-badge")
    page.wait_for_timeout(200)
    assert page.evaluate("window.__xss === undefined") and page.locator("#fc-connections img").count() == 0
    assert page.get_attribute("#fc-conn-meta-test", "aria-label") == f"Test the connection to {evil}"
    assert page.get_by_role("region", name=f"{evil} connection", exact=True).count() == 1
    context.close()
    assert errors == []


@KEYS
def test_the_state_and_the_outcome_lines_are_status_regions_that_stay_put_while_their_words_change(browser, server, key):
    admin = Admin()
    context, page, errors = open_admin(browser, server, admin)
    ready(page, key)
    assert page.get_attribute(ids(key)["state"], "role") == "status" and page.get_attribute(ids(key)["note"], "role") == "status"
    page.evaluate("keys => keys.forEach(id => { document.getElementById(id).__same = true; })", [ids(key)["state"][1:], ids(key)["note"][1:]])
    page.click(f"#{ids(key)['test']}")
    page.wait_for_selector(f"{ids(key)['checks']} li")
    page.wait_for_function(f"document.getElementById('fc-conn-{key}-test').getAttribute('aria-disabled') === null")
    assert "Works, with something to look at" in page.inner_text(ids(key)["note"])
    assert page.evaluate("keys => keys.every(id => document.getElementById(id).__same === true)", [ids(key)["state"][1:], ids(key)["note"][1:]])      # the same two nodes: a change in them is announced
    assert page.get_by_role("status").filter(has_text="Works, with something to look at").count() == 1
    context.close()
    assert errors == []


@pytest.mark.parametrize("how", ["busy", "broken", "offline"])
@KEYS
def test_a_failed_test_is_said_in_the_status_region_not_only_on_the_screen(browser, server, key, how):
    """A 429 (a test already running), a 500 and no network at all: no check line appears in any of them, so the aria-live list says nothing and the
    outcome line is the only place the failure shows."""
    admin = Admin()
    admin.test[key] = {"busy": (429, {"detail": "A test is already running; wait for it to finish."}), "broken": (500, {"detail": "The check broke."}), "offline": (200, {})}[how]
    context, page, errors = open_admin(browser, server, admin)
    ready(page, key)
    if how == "offline":
        page.route(f"**/api/admin/constanza/{key}/test", lambda route: route.abort())
    page.evaluate("id => { document.getElementById(id).__same = true; }", ids(key)["note"][1:])
    page.click(f"#{ids(key)['test']}")
    page.wait_for_function("id => document.getElementById(id).classList.contains('fc-err')", arg=ids(key)["note"][1:])
    note = page.locator(ids(key)["note"])
    assert note.get_attribute("role") == "status" and page.evaluate("id => document.getElementById(id).__same === true", ids(key)["note"][1:])
    assert page.locator(f"{ids(key)['checks']} li").count() == 0
    said = {"busy": "A test is already running", "broken": "The check broke.", "offline": ""}[how]
    assert said in note.inner_text() and note.inner_text().strip() != ""
    assert page.get_by_role("status").filter(has_text=note.inner_text().strip()).count() == 1
    context.close()
    assert errors == []


def contrast_report(page, selector: str) -> list[dict]:
    """Every text under `selector` (and the element itself) that is below WCAG AA (4.5:1, 3:1 for large text), against the colour behind it as drawn."""
    return page.evaluate("""selector => {
      const parse = (c) => { const m = String(c).match(/rgba?\\(([^)]+)\\)/); if (!m) return null; const p = m[1].split(/[ ,\\/]+/).filter(Boolean).map(Number); return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 }; };
      const lin = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
      const lum = (c) => 0.2126 * lin(c.r) + 0.7152 * lin(c.g) + 0.0722 * lin(c.b);
      const over = (top, under) => ({ r: top.r * top.a + under.r * (1 - top.a), g: top.g * top.a + under.g * (1 - top.a), b: top.b * top.a + under.b * (1 - top.a), a: 1 });
      const backdrop = (el) => {
        const layers = [];
        for (let e = el; e; e = e.parentElement) { const c = parse(getComputedStyle(e).backgroundColor); if (c && c.a > 0) { layers.push(c); if (c.a === 1) break; } }
        return layers.reduceRight((under, c) => over(c, under), { r: 255, g: 255, b: 255, a: 1 });
      };
      const out = [];
      document.querySelectorAll(selector + ", " + selector + " *").forEach((el) => {
        if (![...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim()) || !el.getClientRects().length) return;
        for (let e = el; e && e !== document.body; e = e.parentElement) { if (e.disabled || parseFloat(getComputedStyle(e).opacity) < 1) return; }
        const cs = getComputedStyle(el), bg = backdrop(el), fg = over(parse(cs.color), bg);
        const hi = Math.max(lum(fg), lum(bg)), lo = Math.min(lum(fg), lum(bg)), ratio = (hi + 0.05) / (lo + 0.05);
        const size = parseFloat(cs.fontSize), large = size >= 24 || (size >= 18.66 && parseInt(cs.fontWeight, 10) >= 700);
        if (ratio < (large ? 3 : 4.5)) out.push({ text: el.textContent.trim().slice(0, 40), cls: String(el.className), ratio: Math.round(ratio * 100) / 100 });
      });
      return out;
    }""", selector)


def test_the_hint_under_a_live_methods_product_reads_at_aa_contrast(browser, server):
    """--ink-faint is 3.7:1 on white and 3.4:1 on the page, below AA for 11.5px text; the hint is the line that tells an admin the estimate describes one
    ad account only."""
    admin = Admin()
    context, page, errors = open_admin(browser, server, admin)
    page.wait_for_selector("#fc-body tr[data-fc-product]")
    assert page.locator(".fc-hint").count() == 1
    assert contrast_report(page, ".fc-hint") == []
    context.close()
    assert errors == []


def test_the_text_of_the_cards_reads_at_aa_contrast_connected_not_connected_and_with_the_checks_shown(browser, server):
    """The description with the secrets to add, the state (with its Not connected badge) and the check lines with their seconds. (The red of a failed
    test's message is the brand's own #ff003c, used across Admin: 3.95:1 on the page, a colour decision that is not made here.)"""
    admin = Admin()
    admin.status["meta"] = (200, status_for("meta", configured=False))
    context, page, errors = open_admin(browser, server, admin)
    page.wait_for_selector("#fc-conn-meta-state .fc-badge")
    ready(page, "tiktok")
    page.click("#fc-conn-tiktok-test")
    page.wait_for_selector("#fc-conn-tiktok-checks li")
    assert "Not connected" in page.inner_text("#fc-conn-meta-state") and page.locator("#fc-conn-tiktok-checks .mono").count() == 2
    assert contrast_report(page, "#fc-connections") == []
    context.close()
    assert errors == []


# --------------------------------------------------------------------------------------------------------------------------------
# The select, the default text, the summary cards
# --------------------------------------------------------------------------------------------------------------------------------

def test_the_what_it_computes_select_offers_every_method_of_the_list(browser, server):
    admin = Admin(methods=METHODS + [ECHO])
    context, page, errors = open_admin(browser, server, admin)
    page.wait_for_selector("#fc-body tr[data-fc-product]")
    row = page.locator('#fc-body tr[data-fc-product="Facebook & Instagram Ads | Awareness"]')
    options = row.locator("select[data-fc=method] option").all_inner_texts()
    assert options == ["Platform data", "Population share", "TikTok API (live)", "Meta API (live)", "Echo API (live)"]
    assert row.locator("select[data-fc=method]").input_value() == "meta"
    assert row.locator("td").nth(7).inner_text() == "Default"                    # its built-in default is Meta, so it is not "Customized"
    assert "On · Meta API (live)" in (row.locator(".fc-badge").get_attribute("title") or "")
    context.close()
    assert errors == []


def test_the_summary_cards_come_from_the_list_and_an_unused_api_method_has_none(browser, server):
    admin = Admin(methods=METHODS + [ECHO])
    context, page, errors = open_admin(browser, server, admin)
    page.wait_for_selector("#fc-summary .analytics-stat-card")
    cards = page.eval_on_selector_all("#fc-summary .analytics-stat-card", "els => els.map(e => [e.querySelector('.analytics-stat-label').textContent, e.querySelector('.analytics-stat-value').textContent])")
    assert cards == [["Products with the button", "4"], ["Platform data", "1"], ["Population share", "0"], ["TikTok API", "1"], ["Meta API", "2"], ["Customized", "1"]]
    assert "a live audience-size estimate from Meta" in page.inner_text("#fc-summary")
    context.close()
    assert errors == []


def test_a_live_method_on_a_product_that_is_not_one_of_its_defaults_says_what_it_describes(browser, server):
    admin = Admin()
    context, page, errors = open_admin(browser, server, admin)
    page.wait_for_selector("#fc-body tr[data-fc-product]")
    branded = page.locator('#fc-body tr[data-fc-product="Facebook & Instagram Ads on EVC Radio Pages"]')
    assert "This estimate describes Meta's own ad account audience; use it only for a product bought there." in branded.inner_text()
    for name in ("Facebook & Instagram Ads | Awareness", "Tiktok Ads", "Entravision Plus - Roku Ads"):
        assert "own ad account audience" not in page.locator(f'#fc-body tr[data-fc-product="{name}"]').inner_text(), name
    context.close()
    assert errors == []


def test_choosing_a_live_method_saves_it_and_clears_what_only_platform_data_uses(browser, server):
    admin = Admin()
    context, page, errors = open_admin(browser, server, admin)
    page.wait_for_selector("#fc-body tr[data-fc-product]")
    row = page.locator('#fc-body tr[data-fc-product="Entravision Plus - Roku Ads"]')
    row.locator("select[data-fc=method]").select_option("meta")
    for _ in range(50):
        if admin.saved:
            break
        page.wait_for_timeout(50)
    assert admin.saved and admin.saved[0]["method"] == "meta" and admin.saved[0]["platform"] is None
    assert admin.saved[0]["hispanic_weighted"] is False and admin.saved[0]["win_rate"] == "auto" and admin.saved[0]["product_name"] == "Entravision Plus - Roku Ads"
    context.close()
    assert errors == []


def test_the_connection_cards_fit_at_a_phone_width(browser, server):
    """(The admin page is a desktop tool - its product toolbar is wider than a phone - so this checks the cards, which are new.)"""
    admin = Admin()
    context, page, errors = open_admin(browser, server, admin, viewport={"width": 390, "height": 844})
    page.wait_for_selector("#fc-conn-meta-state .fc-badge")
    page.click("#fc-conn-meta-test")
    page.wait_for_selector("#fc-conn-meta-checks li")
    over = page.evaluate("""() => [...document.querySelectorAll('#fc-connections *')].filter(e => e.getBoundingClientRect().right > window.innerWidth + 1).map(e => e.tagName + '.' + e.className)""")
    assert over == []
    context.close()
    assert errors == []
