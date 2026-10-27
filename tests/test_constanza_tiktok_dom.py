"""The TikTok panel in a REAL browser (headless Chromium through Playwright): the behaviors a stub page cannot show - which button keeps
the keyboard focus, whether the chips on the screen are the picks the request will send, what Escape does, what Reset leaves behind.

The page is a small harness (not the whole app): the real constanza.js, constanza-tiktok.js and CSS against a stub `state.catalog`, with the
/api/constanza/* routes answered in the browser by the same service functions the server uses, over the in-memory fake of TikTok's API.
Skipped when Playwright or its Chromium is not installed."""
from __future__ import annotations

import http.server
import json
import threading
from pathlib import Path

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")

from app.services import constanza_tiktok as ct  # noqa: E402
from tests.tiktok_support import make_client  # noqa: E402

STATIC = Path(__file__).resolve().parents[1] / "app" / "static"

HARNESS = """<!doctype html><html><head><meta charset="utf-8"><title>harness</title>
<link rel="stylesheet" href="/static/styles.css"><link rel="stylesheet" href="/static/constanza.css"></head><body>
<script>
  function escapeHtml(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[c])); }
  function _replaceGeoItem(t, i, r) { return r; }
  function _constanzaErrorText(d, f) { return (d && typeof d.detail === "string" && d.detail) || f || "Request failed."; }
  var state = { lineItems: [], catalog: { families: ["Social", "Entravision Plus"], products_by_family: {
    "Social": [{ name: "Tiktok Ads", family: "Social", is_addon: false, forecast: { enabled: true, method: "tiktok", series: null } }],
    "Entravision Plus": [{ name: "Roku", family: "Entravision Plus", is_addon: false, forecast: { enabled: true, method: "platform", series: { key: "roku", channel: "platform_model", data_basis: "modeled", unit: "monthly" } } }] } } };
</script>
<script src="/static/constanza.js"></script>
<script src="/static/constanza-tiktok.js"></script>
</body></html>"""

ESTIMATE = {
    "product_name": "Tiktok Ads", "platform": "tiktok", "confidence": "live_api", "geo_method": "exact_dma", "geo_confidence": "high", "months": 1,
    "imps_low": 928_800, "imps_mid": 1_188_000, "imps_high": 1_857_600, "est_uniques": 216_000, "reach_universe": None, "series": None, "weeks": None,
    "notes": [], "unmatched_geo_tokens": [], "unmatched_geo_details": [], "ambiguous": [], "geo_interpretations": [], "geo_suggestions": [], "by_area": [],
    "tiktok": {"audience": {"low": 216_000, "mid": 240_000, "high": 264_000}, "stage": 3, "stage_label": "Balanced", "stage_note": "20% to 80%",
               "impressions_per_person": 5.5, "monthly": {"low": 1, "mid": 2, "high": 3}, "locations": [], "unmapped": [], "objective": "Reach",
               "placements": ["PLACEMENT_TIKTOK"], "request_id": "R", "cached": False},
}


class _Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=str(STATIC.parent), **k)

    def do_GET(self):
        if self.path.startswith("/harness.html"):
            raw = HARNESS.encode()
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


class World:
    """What the harness's /api answers: the TikTok panel's routes through the real service over the fake TikTok.

    A test can park an answer (`hold`) and release it later, make a route fail (`fail`), or replace what a catalog / the search returns."""

    NAMES = {"api/constanza/tiktok/status": "status", "api/constanza/tiktok/targeting": "targeting", "api/constanza/tiktok/search": "search",
             "api/constanza/estimate": "estimate", "api/constanza/resolve": "resolve"}

    def __init__(self):
        self.client = make_client()
        self.configured = True
        self.search_override = None
        self.catalog_override: dict = {}                   # kind -> items
        self.fail: dict = {}                               # route name -> (status, body)
        self.hold: set = set()
        self.pending: list = []
        self.hits: list[str] = []
        self.requests: list[dict] = []

    def release(self, name) -> None:
        for item in [p for p in self.pending if p[0] == name]:
            self.pending.remove(item)
            item[1].fulfill(status=item[2], content_type="application/json", body=json.dumps(item[3]))

    def answer(self, route) -> None:
        req = route.request
        path = req.url.split("//", 1)[1].split("/", 1)[1].split("?")[0]
        url = req.url
        body = None
        status = 200
        name = self.NAMES.get(path, "other")
        self.hits.append(name)
        if name in self.fail:
            status, body = self.fail[name]
            route.fulfill(status=status, content_type="application/json", body=json.dumps(body))
            return
        if path == "api/constanza/tiktok/status":
            body = {"configured": self.configured, "missing": [] if self.configured else ["TIKTOK_ACCESS_TOKEN", "TIKTOK_ADVERTISER_ID"],
                    "advertiser": "…0001", "impressions_per_person": 5.5, "setup_hint": "x", "options": ct.static_options()}
        elif path == "api/constanza/tiktok/targeting":
            kind = url.split("kind=", 1)[1].split("&")[0]
            body = {"kind": kind, "items": self.catalog_override[kind] if kind in self.catalog_override else ct.catalog(kind, client=self.client)}
        elif path == "api/constanza/tiktok/search":
            q = url.split("q=", 1)[1].split("&")[0]
            body = {"query": q, "results": self.search_override if self.search_override is not None else ct.search(q, client=self.client)}
        elif path == "api/constanza/resolve":
            body = {"areas": [], "ambiguous": [], "suggestions": [], "interpretations": [], "unmatched_details": [], "unmatched_tokens": [], "notes": []}
        elif path == "api/constanza/estimate":
            self.requests.append(json.loads(req.post_data or "{}"))
            if not self.configured:
                status, body = 503, {"detail": "TikTok isn't connected yet — an admin needs to add TIKTOK_ACCESS_TOKEN and TIKTOK_ADVERTISER_ID to the app's secrets."}
            else:
                body = ESTIMATE
        else:
            status, body = 404, {"detail": "not found"}
        if name in self.hold:
            self.pending.append((name, route, status, body))
            return
        route.fulfill(status=status, content_type="application/json", body=json.dumps(body))


@pytest.fixture
def dock(browser, server):
    world = World()
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    page = context.new_page()
    page.route("**/api/**", world.answer)
    page.route("**/fonts.googleapis.com/**", lambda r: r.abort())
    page.route("**/fonts.gstatic.com/**", lambda r: r.abort())
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{server}/harness.html")
    page.evaluate("Constanza.open({})")
    page.click("#cz-product-btn")
    page.fill("#cz-picker-search", "tiktok")
    page.click("#cz-picker-list .cz-option")
    page.wait_for_selector("#cz-tt-status.cz-tt-on")
    page.world, page.errors = world, errors                      # type: ignore[attr-defined]
    yield page
    context.close()
    assert errors == [], errors


def chips(page):
    return page.eval_on_selector_all("#cz-tt-chips .cz-chip", "els => els.map(e => e.firstChild.textContent.trim())")


def picks(page):
    return page.evaluate("Constanza._plugin('tiktok')._pure.T.picks.map(p => p.name)")


def open_browse(page, tab="general_interest"):
    page.click("#cz-tt-browse > summary")
    if tab != "general_interest":
        page.click(f'#cz-tt-tabs [data-v="{tab}"]')
    page.wait_for_selector('#cz-tt-browse-body button[data-tt="browse-add"]')


# --------------------------------------------------------------------------------------------- the panel is there and works

def test_the_tiktok_product_swaps_in_its_panel_and_hides_the_entravision_audience(dock):
    assert dock.is_visible("#cz-tt") and not dock.is_visible("#cz-aud")
    assert dock.inner_text("#cz-go").strip() == "Estimate audience →"
    assert "Connected to TikTok" in dock.inner_text("#cz-tt-status")
    dock.click("#cz-product-btn")
    dock.fill("#cz-picker-search", "roku")
    dock.click("#cz-picker-list .cz-option")
    assert dock.is_visible("#cz-aud") and not dock.is_visible("#cz-tt") and dock.inner_text("#cz-go").strip() == "Pull avails →"


def test_the_request_the_panel_sends_is_what_was_picked(dock):
    dock.fill("#cz-geo", "Los Angeles")
    dock.click('#cz-tt-gender [data-v="female"]')
    dock.click('#cz-tt-age [data-v="AGE_25_34"]')
    dock.click('#cz-tt-age [data-v="AGE_35_44"]')
    dock.fill("#cz-tt-q", "skin")
    dock.click("#cz-tt-results .cz-interest-item")
    dock.click("#cz-go")
    dock.wait_for_selector("#cz-result .cz-headline")
    sent = dock.world.requests[-1]["tiktok"]                       # type: ignore[attr-defined]
    assert sent["gender"] == "female" and sent["age_groups"] == ["AGE_25_34", "AGE_35_44"] and sent["interest_category_ids"] == ["12100"]
    assert "216,000" in dock.inner_text("#cz-result")


# --------------------------------------------------------------------------------------------- the review's findings

def test_unticking_a_browse_chip_removes_that_pick_and_the_chips_always_match_the_request(dock):
    """A ghost chip stayed behind after unticking in Browse, and the next x then removed the WRONG pick."""
    open_browse(dock)
    dock.click('#cz-tt-browse-body [data-id="12100"]')             # Skincare
    dock.click('#cz-tt-browse-body [data-id="12200"]')             # Makeup
    assert chips(dock) == ["Skincare", "Makeup"] and picks(dock) == ["Skincare", "Makeup"]
    dock.click('#cz-tt-browse-body [data-id="12100"]')             # untick Skincare
    assert chips(dock) == ["Makeup"] and picks(dock) == ["Makeup"]
    assert dock.get_attribute('#cz-tt-browse-body [data-id="12100"]', "aria-pressed") == "false"
    assert dock.get_attribute('#cz-tt-browse-body [data-id="12200"]', "aria-pressed") == "true"
    assert dock.inner_text("#cz-tt-count").strip() == "1"
    dock.click('#cz-tt-chips [data-tt="rm-pick"]')                 # the only chip is Makeup: it is the one removed
    assert chips(dock) == [] and picks(dock) == []


def test_a_pick_made_in_search_shows_ticked_in_browse_and_a_removal_unticks_it(dock):
    open_browse(dock)
    dock.fill("#cz-tt-q", "skin")
    dock.click("#cz-tt-results .cz-interest-item")
    assert dock.get_attribute('#cz-tt-browse-body [data-id="12100"]', "aria-pressed") == "true"
    dock.click('#cz-tt-chips [data-tt="rm-pick"]')
    assert dock.get_attribute('#cz-tt-browse-body [data-id="12100"]', "aria-pressed") == "false"


@pytest.mark.parametrize("selector", ['#cz-tt-age [data-v="AGE_25_34"]', '#cz-tt-obj [data-v="traffic"]', '#cz-tt-gender [data-v="male"]',
                                      '#cz-tt-spend [data-v="HIGH"]'])
def test_the_keyboard_focus_stays_on_the_button_that_was_pressed(dock, selector):
    """The groups used to be rebuilt on every press, dropping the focus to the page."""
    dock.click("#cz-tt-more > summary")
    dock.focus(selector)
    dock.keyboard.press("Enter")
    assert dock.evaluate(f"document.activeElement === document.querySelector({json.dumps(selector)})")
    assert dock.get_attribute(selector, "aria-pressed") == "true"


def test_turning_pangle_on_and_then_picking_an_interaction_leaves_tiktok_as_the_only_placement(dock):
    dock.click("#cz-tt-more > summary")
    dock.click('#cz-tt-place [data-v="PLACEMENT_PANGLE"]')
    assert dock.evaluate("Constanza._plugin('tiktok').requestPart().tiktok.placements") == ["PLACEMENT_PANGLE", "PLACEMENT_TIKTOK"]
    open_browse(dock, "video_interaction")
    dock.click('#cz-tt-browse-body [data-tt="browse-add"]')
    assert dock.evaluate("Constanza._plugin('tiktok').requestPart().tiktok.placements") == ["PLACEMENT_TIKTOK"]
    assert dock.get_attribute('#cz-tt-place [data-v="PLACEMENT_PANGLE"]', "aria-pressed") == "false"
    assert dock.is_disabled('#cz-tt-place [data-v="PLACEMENT_PANGLE"]')


def test_the_same_lock_holds_for_a_pick_made_in_search(dock):
    dock.click("#cz-tt-more > summary")
    dock.click('#cz-tt-place [data-v="PLACEMENT_PANGLE"]')
    dock.fill("#cz-tt-q", "tips")                                   # "#tipstips" and "#tips": hashtag interactions
    dock.click("#cz-tt-results .cz-interest-item")
    assert dock.evaluate("Constanza._plugin('tiktok').requestPart().tiktok.placements") == ["PLACEMENT_TIKTOK"]


def test_reset_leaves_the_browse_tab_and_its_chips_in_step(dock):
    open_browse(dock, "hashtag_interaction")
    dock.click("#cz-reset")
    assert dock.get_attribute('#cz-tt-tabs [data-v="general_interest"]', "aria-pressed") == "true"
    assert dock.get_attribute('#cz-tt-tabs [data-v="hashtag_interaction"]', "aria-pressed") == "false"
    dock.wait_for_selector('#cz-tt-browse-body [data-id="12100"]')                     # the Interests list is what shows, and it works
    dock.click('#cz-tt-browse-body [data-id="12100"]')
    assert picks(dock) == ["Skincare"]


def test_escape_closes_the_popup_alone_when_it_is_on_screen_even_if_it_holds_no_option(dock):
    dock.world.search_override = {}                                  # type: ignore[attr-defined]  nothing matches: the popup says so
    dock.fill("#cz-tt-q", "zzzz")
    dock.wait_for_selector("#cz-tt-results .cz-empty")
    dock.keyboard.press("Escape")
    assert dock.is_hidden("#cz-tt-results") and dock.is_visible("#constanza-dock")


def test_escape_with_no_popup_closes_the_dock_as_before(dock):
    dock.focus('#cz-tt-gender [data-v="male"]')
    dock.keyboard.press("Escape")
    assert dock.is_hidden("#constanza-dock")


def test_the_language_list_tells_a_screen_reader_which_option_is_active(dock):
    dock.click("#cz-tt-lang-q")
    dock.wait_for_selector("#cz-tt-lang-results .cz-interest-item")
    dock.keyboard.press("ArrowDown")
    active = dock.get_attribute("#cz-tt-lang-q", "aria-activedescendant")
    assert active and dock.evaluate(f"document.getElementById({json.dumps(active)}).classList.contains('cz-active')")
    dock.keyboard.press("Enter")
    assert dock.inner_text("#cz-tt-lang-chips").strip().startswith("Spanish")                 # the second option (English, Spanish, ...): the arrow moved it


def test_a_search_shows_hits_of_every_kind_not_only_the_first_ones(dock):
    many = {kind: [{"id": str(i), "name": f"{kind} {i}", "path": ""} for i in range(25)] for kind in
            ("general_interest", "additional_interest", "purchase_intention", "video_interaction", "creator_interaction", "hashtag_interaction")}
    dock.world.search_override = many                                # type: ignore[attr-defined]
    dock.fill("#cz-tt-q", "sports")
    dock.wait_for_selector("#cz-tt-results .cz-interest-item")
    kinds = set(dock.eval_on_selector_all("#cz-tt-results .cz-rate", "els => els.map(e => e.textContent)"))
    assert kinds == {"Interest", "Interest keyword", "Purchase intent", "Video interaction", "Creator interaction", "Hashtag"}


def test_purchase_intent_on_any_objective_but_traffic_offers_the_fix(dock):
    open_browse(dock, "purchase_intention")
    dock.click('#cz-tt-browse-body [data-tt="browse-add"]')
    assert "Reach campaign" in dock.inner_text("#cz-tt-pick-note")
    dock.click("#cz-tt-more > summary")
    dock.click('#cz-tt-obj [data-v="video_views"]')
    assert "Video views campaign" in dock.inner_text("#cz-tt-pick-note")
    dock.click('#cz-tt-pick-note [data-tt="fix-objective"]')
    assert dock.inner_text("#cz-tt-pick-note").strip() == "" and dock.get_attribute('#cz-tt-obj [data-v="traffic"]', "aria-pressed") == "true"


def test_an_admin_adding_the_secrets_lets_the_next_pull_through_without_reopening_the_dock(dock):
    """The 'not connected' answer used to be remembered client-side until the dock was closed and opened again."""
    world = dock.world                                               # type: ignore[attr-defined]
    world.configured = False
    dock.evaluate("Constanza._plugin('tiktok')._pure.T.statusAt = 0")
    dock.fill("#cz-geo", "Los Angeles")
    dock.click("#cz-go")
    dock.wait_for_selector("#cz-result .cz-error")
    assert "TikTok isn't connected yet" in dock.inner_text("#cz-result")
    dock.wait_for_selector("#cz-tt-status.cz-tt-off")                # the banner follows the server's answer
    world.configured = True                                          # the admin adds the secrets
    dock.click("#cz-go")
    dock.wait_for_selector("#cz-result .cz-headline")
    assert "216,000" in dock.inner_text("#cz-result")


def test_a_chip_button_keeps_the_chip_size_it_had(dock):
    """button.cz-chip once reset the font size to the browser's 13.3px, which grew the geography suggestion chips."""
    size = dock.evaluate("""() => { const b = document.createElement('button'); b.className = 'cz-chip cz-sugg'; b.textContent = 'x';
        document.body.appendChild(b); return getComputedStyle(b).fontSize; }""")
    assert size == "12px"


def test_a_second_registration_replaces_the_panel_so_the_screen_and_the_request_agree(dock):
    dock.click('#cz-tt-gender [data-v="male"]')
    dock.evaluate("""() => { const src = document.querySelector('script[src="/static/constanza-tiktok.js"]').src;
        return fetch(src).then(r => r.text()).then(t => { (0, eval)(t); }); }""")
    assert dock.locator("#cz-tt").count() == 1
    dock.click('#cz-tt-gender [data-v="female"]')
    assert dock.evaluate("Constanza._plugin('tiktok').requestPart().tiktok.gender") == "female"
    assert dock.get_attribute('#cz-tt-gender [data-v="female"]', "aria-pressed") == "true"


def test_text_from_tiktok_runs_nothing_even_when_it_is_hostile(dock):
    evil = '<img src=x onerror="window.__xss=1">'
    dock.world.search_override = {"general_interest": [{"id": "1", "name": evil, "path": evil}]}      # type: ignore[attr-defined]
    dock.fill("#cz-tt-q", "evil")
    dock.click("#cz-tt-results .cz-interest-item")
    dock.click("#cz-go")
    dock.wait_for_timeout(300)
    assert dock.evaluate("window.__xss === undefined")
    assert evil in dock.inner_text("#cz-tt-chips")                  # shown as text


# --------------------------------------------------------------------------------------------- the second review: keyboard, races, focus

def hits_of(kind, n, prefix=None):
    return [{"id": str(1000 + i), "name": f"{prefix or kind} {i}", "path": ""} for i in range(n)]


def test_tabbing_out_of_the_language_box_closes_its_list_and_escape_then_closes_the_dock_as_usual(dock):
    """The list stayed open after Tab, so Escape (pressed with the focus elsewhere in the dock) closed the whole dock instead of the list."""
    dock.click("#cz-tt-lang-q")
    dock.wait_for_selector("#cz-tt-lang-results .cz-interest-item")
    dock.keyboard.press("Tab")
    assert dock.is_hidden("#cz-tt-lang-results") and dock.get_attribute("#cz-tt-lang-q", "aria-expanded") == "false"
    dock.keyboard.press("Escape")
    assert dock.is_hidden("#constanza-dock")


def test_tabbing_out_of_the_interest_box_closes_its_list_too(dock):
    dock.fill("#cz-tt-q", "skin")
    dock.wait_for_selector("#cz-tt-results .cz-interest-item")
    dock.keyboard.press("Tab")
    assert dock.is_hidden("#cz-tt-results") and dock.get_attribute("#cz-tt-q", "aria-expanded") == "false"
    assert dock.get_attribute("#cz-tt-q", "aria-activedescendant") is None


def test_tabbing_into_the_language_box_does_not_open_its_list_but_arrow_down_does(dock):
    dock.focus("#cz-tt-lang-q")
    dock.wait_for_timeout(150)
    assert dock.is_hidden("#cz-tt-lang-results")
    dock.keyboard.press("ArrowDown")
    dock.wait_for_selector("#cz-tt-lang-results .cz-interest-item")
    assert dock.get_attribute("#cz-tt-lang-q", "aria-expanded") == "true"


def test_a_mouse_click_on_an_option_still_lands_though_the_list_closes_when_the_focus_leaves(dock):
    """The list closes on focusout; choosing an option with the mouse must not move the focus out from under it first."""
    dock.fill("#cz-tt-q", "skin")
    dock.click("#cz-tt-results .cz-interest-item")
    assert picks(dock) == ["Skincare"]
    dock.click("#cz-tt-lang-q")
    dock.click("#cz-tt-lang-results .cz-interest-item >> nth=1")
    assert dock.inner_text("#cz-tt-lang-chips").strip().startswith("Spanish")


def visible_in(page, item_selector, box_selector):
    return page.evaluate("""([i, b]) => { const r = document.querySelector(i).getBoundingClientRect(), c = document.querySelector(b).getBoundingClientRect();
        return r.top >= c.top - 1 && r.bottom <= c.bottom + 1; }""", [item_selector, box_selector])


def test_the_highlighted_option_always_scrolls_into_view_in_the_category_list(dock):
    kinds = ("general_interest", "additional_interest", "purchase_intention", "video_interaction", "creator_interaction", "hashtag_interaction")
    dock.world.search_override = {k: hits_of(k, 8) for k in kinds}                                # type: ignore[attr-defined]  six of each kind: 36 rows
    dock.fill("#cz-tt-q", "many")
    dock.wait_for_selector("#cz-tt-results .cz-interest-item")
    for _ in range(14):
        dock.keyboard.press("ArrowDown")
    assert visible_in(dock, "#cz-tt-results .cz-active", "#cz-tt-results")
    assert dock.evaluate("document.getElementById('cz-tt-results').scrollTop") > 0


def test_the_highlighted_option_always_scrolls_into_view_in_the_language_list(dock):
    dock.world.catalog_override["languages"] = [{"code": f"l{i:02d}", "name": f"Language {i:02d}"} for i in range(30)]       # type: ignore[attr-defined]
    dock.click("#cz-tt-lang-q")
    dock.wait_for_selector("#cz-tt-lang-results .cz-interest-item")
    for _ in range(10):
        dock.keyboard.press("ArrowDown")
    assert visible_in(dock, "#cz-tt-lang-results .cz-active", "#cz-tt-lang-results")


def test_a_category_search_answer_for_older_text_is_never_picked(dock):
    """Results for "skin" were on screen while "soccer" was being searched for, and Enter added Skincare."""
    world = dock.world                                                                              # type: ignore[attr-defined]
    dock.fill("#cz-tt-q", "skin")
    dock.wait_for_selector("#cz-tt-results .cz-interest-item")
    world.hold.add("search")
    dock.fill("#cz-tt-q", "soccer")                                                                 # the answer for this text is parked
    dock.keyboard.press("Enter")
    assert picks(dock) == []
    dock.click("#cz-tt-results .cz-interest-item", force=True)                                      # a click on the old list does not pick either
    assert picks(dock) == []
    assert dock.evaluate("document.getElementById('cz-tt-results').classList.contains('cz-pending')")
    world.hold.discard("search")
    world.release("search")
    dock.wait_for_function("document.querySelector('#cz-tt-results .cz-interest-item') && !document.getElementById('cz-tt-results').classList.contains('cz-pending')")
    assert "Soccer" in dock.inner_text("#cz-tt-results")
    dock.keyboard.press("Enter")
    assert picks(dock) == ["Soccer"]


def test_an_answer_that_arrives_after_the_person_moved_on_opens_nothing(dock):
    world = dock.world                                                                              # type: ignore[attr-defined]
    world.hold.add("search")
    dock.fill("#cz-tt-q", "skin")
    dock.wait_for_function("true")
    dock.wait_for_timeout(400)                                                                      # the request is out
    dock.click('#cz-tt-age [data-v="AGE_25_34"]')                                                   # and the person has gone to something else
    world.hold.discard("search")
    world.release("search")
    dock.wait_for_timeout(200)
    assert dock.is_hidden("#cz-tt-results") and dock.get_attribute("#cz-tt-q", "aria-expanded") == "false"


def test_a_slow_first_language_list_does_not_open_after_reset(dock):
    world = dock.world                                                                              # type: ignore[attr-defined]
    world.hold.add("targeting")
    dock.click("#cz-tt-lang-q")
    dock.click("#cz-reset")
    world.hold.discard("targeting")
    world.release("targeting")
    dock.wait_for_timeout(200)
    assert dock.is_hidden("#cz-tt-lang-results") and dock.get_attribute("#cz-tt-lang-q", "aria-expanded") == "false"


def test_an_empty_language_list_is_not_remembered_for_the_life_of_the_page(dock):
    world = dock.world                                                                              # type: ignore[attr-defined]
    world.catalog_override["languages"] = []
    dock.click("#cz-tt-lang-q")
    dock.wait_for_selector("#cz-tt-lang-results .cz-empty")
    assert "came back empty" in dock.inner_text("#cz-tt-lang-results")
    world.catalog_override.clear()                                                                  # TikTok recovers
    dock.keyboard.press("ArrowDown")
    dock.wait_for_selector("#cz-tt-lang-results .cz-interest-item")


def test_no_option_is_announced_after_the_list_was_replaced_by_an_error(dock):
    dock.fill("#cz-tt-q", "skin")
    dock.wait_for_selector("#cz-tt-results .cz-interest-item")
    assert dock.get_attribute("#cz-tt-q", "aria-activedescendant")
    dock.world.fail["search"] = (503, {"detail": "TikTok is down"})                                  # type: ignore[attr-defined]
    dock.fill("#cz-tt-q", "skincare")
    dock.wait_for_selector("#cz-tt-results .cz-empty")
    assert dock.get_attribute("#cz-tt-q", "aria-activedescendant") is None


def test_a_search_that_found_more_than_it_shows_says_how_many_more(dock):
    dock.world.search_override = {"general_interest": hits_of("Interest", 25), "additional_interest": hits_of("Keyword", 8)}        # type: ignore[attr-defined]
    dock.fill("#cz-tt-q", "many")
    dock.wait_for_selector("#cz-tt-results .cz-more")
    text = dock.inner_text("#cz-tt-results .cz-more")
    assert "19 more interests" in text and "2 more interest keywords" in text
    assert dock.locator("#cz-tt-results .cz-interest-item").count() == 12                            # six of each kind; the note is not an option


def test_removing_a_chip_from_the_keyboard_moves_the_focus_to_the_next_chip_then_the_box(dock):
    open_browse(dock)
    dock.click('#cz-tt-browse-body [data-id="12100"]')
    dock.click('#cz-tt-browse-body [data-id="12200"]')
    dock.focus('#cz-tt-chips .cz-chip:nth-child(1) .cz-x')
    dock.keyboard.press("Enter")
    assert chips(dock) == ["Makeup"]
    assert dock.evaluate("document.activeElement === document.querySelector('#cz-tt-chips .cz-x')")
    dock.keyboard.press("Enter")
    assert chips(dock) == [] and dock.evaluate("document.activeElement.id") == "cz-tt-q"


def test_removing_a_language_chip_from_the_keyboard_keeps_the_focus_in_the_field(dock):
    dock.click("#cz-tt-lang-q")
    dock.click("#cz-tt-lang-results .cz-interest-item >> nth=0")
    dock.focus("#cz-tt-lang-chips .cz-x")
    dock.keyboard.press("Enter")
    assert dock.evaluate("document.activeElement.id") == "cz-tt-lang-q"


def test_using_the_traffic_objective_from_its_warning_keeps_the_focus_in_the_panel(dock):
    open_browse(dock, "purchase_intention")
    dock.click('#cz-tt-browse-body [data-tt="browse-add"]')
    dock.click("#cz-tt-more > summary")                                                             # the objective is on screen: the focus goes to it
    dock.focus('#cz-tt-pick-note [data-tt="fix-objective"]')
    dock.keyboard.press("Enter")
    assert dock.evaluate("document.activeElement === document.querySelector('#cz-tt-obj [data-v=traffic]')")
    assert "Traffic" in dock.inner_text("#cz-status")


def test_using_the_traffic_objective_while_that_part_of_the_panel_is_folded_keeps_the_focus_in_the_search_box(dock):
    open_browse(dock, "purchase_intention")
    dock.click('#cz-tt-browse-body [data-tt="browse-add"]')
    dock.focus('#cz-tt-pick-note [data-tt="fix-objective"]')
    dock.keyboard.press("Enter")
    assert dock.evaluate("document.activeElement.id") == "cz-tt-q"
    assert "Traffic" in dock.inner_text("#cz-status")


def test_the_estimate_button_keeps_the_keyboard_focus_while_it_works(dock):
    world = dock.world                                                                              # type: ignore[attr-defined]
    dock.fill("#cz-geo", "Los Angeles")
    world.hold.add("estimate")
    dock.focus("#cz-go")
    dock.keyboard.press("Enter")
    dock.wait_for_function("document.getElementById('cz-go').getAttribute('aria-disabled') === 'true'")
    assert dock.evaluate("document.activeElement.id") == "cz-go"
    dock.keyboard.press("Enter")                                                                    # a second press while busy does nothing
    assert world.hits.count("estimate") == 1
    world.hold.discard("estimate")
    world.release("estimate")
    dock.wait_for_selector("#cz-result .cz-headline")
    assert dock.evaluate("document.activeElement.id") == "cz-go" and dock.get_attribute("#cz-go", "aria-disabled") == "false"


def test_a_pressed_button_keeps_the_focus_even_when_a_menu_arrives_late_and_the_group_is_rebuilt(dock):
    dock.focus('#cz-tt-age [data-v="AGE_25_34"]')
    dock.keyboard.press("Enter")
    dock.evaluate("""() => { const T = Constanza._plugin('tiktok')._pure.T; T.status.options.age_groups[0].label = 'Eighteen'; Constanza._plugin('tiktok').onSelect(); }""")
    assert dock.inner_text('#cz-tt-age [data-v="AGE_18_24"]').strip() == "Eighteen"                  # the group WAS rebuilt ...
    assert dock.evaluate("document.activeElement === document.querySelector('#cz-tt-age [data-v=AGE_25_34]')")      # ... and the focus came back
    assert dock.get_attribute('#cz-tt-age [data-v="AGE_25_34"]', "aria-pressed") == "true"


def test_the_menus_the_panel_draws_before_the_server_answers_are_the_servers_menus_to_the_letter(dock):
    """A difference (a curly apostrophe once) made the first answer rebuild every group."""
    fallback = dock.evaluate("Constanza._plugin('tiktok')._pure.FALLBACK")
    for key, items in ct.static_options().items():
        if key in fallback:
            assert fallback[key] == [[i["id"], i["label"]] for i in items], key


def test_the_banner_stops_saying_not_connected_once_a_pull_works_and_a_bad_status_is_never_remembered(dock):
    world = dock.world                                                                              # type: ignore[attr-defined]
    world.configured = False
    dock.evaluate("() => { Constanza._plugin('tiktok')._pure.T.statusAt = 0; }")
    dock.click("#cz-product-btn"); dock.fill("#cz-picker-search", "roku"); dock.click("#cz-picker-list .cz-option")        # away ...
    dock.click("#cz-product-btn"); dock.fill("#cz-picker-search", "tiktok"); dock.click("#cz-picker-list .cz-option")      # ... and back
    dock.wait_for_selector("#cz-tt-status.cz-tt-off")
    asked = world.hits.count("status")
    dock.click("#cz-product-btn"); dock.fill("#cz-picker-search", "roku"); dock.click("#cz-picker-list .cz-option")
    dock.click("#cz-product-btn"); dock.fill("#cz-picker-search", "tiktok"); dock.click("#cz-picker-list .cz-option")
    dock.wait_for_function(f"true")
    assert world.hits.count("status") > asked                                                       # a "not connected" answer is asked again, not kept for 30 s
    world.configured = True                                                                         # the admin adds the secrets
    dock.fill("#cz-geo", "Los Angeles")
    dock.click("#cz-go")
    dock.wait_for_selector("#cz-result .cz-headline")
    dock.wait_for_selector("#cz-tt-status.cz-tt-on")                                                # the banner follows the pull that worked


def test_the_same_choices_in_another_order_do_not_raise_the_form_changed_banner(dock):
    dock.fill("#cz-geo", "Los Angeles")
    dock.click("#cz-tt-lang-q")
    dock.click("#cz-tt-lang-results .cz-interest-item >> nth=0")                                     # English
    dock.click("#cz-tt-lang-q")
    dock.click("#cz-tt-lang-results .cz-interest-item >> nth=0")                                     # then Spanish
    open_browse(dock)
    dock.click('#cz-tt-browse-body [data-id="12100"]')
    dock.click('#cz-tt-browse-body [data-id="12200"]')
    dock.click("#cz-go")
    dock.wait_for_selector("#cz-result .cz-headline")
    assert dock.locator("#cz-result .cz-stale").count() == 0
    dock.click('#cz-tt-browse-body [data-id="12100"]')                                              # untick and tick again: the picks are now in the other order
    dock.click('#cz-tt-browse-body [data-id="12100"]')
    dock.click('#cz-tt-lang-chips .cz-x >> nth=0')                                                   # and the languages too
    dock.click("#cz-tt-lang-q")
    dock.click("#cz-tt-lang-results .cz-interest-item >> nth=0")
    assert dock.locator("#cz-result .cz-stale").count() == 0


def test_a_long_unbreakable_name_stays_inside_its_chip_and_its_remove_button_is_a_real_target(dock):
    dock.set_viewport_size({"width": 390, "height": 800})
    long = "#" + "x" * 70
    dock.world.search_override = {"hashtag_interaction": [{"id": "7", "name": long, "path": ""}]}      # type: ignore[attr-defined]
    dock.fill("#cz-tt-q", "long")
    dock.click("#cz-tt-results .cz-interest-item")
    box = dock.eval_on_selector("#cz-tt-chips .cz-chip", "e => { const r = e.getBoundingClientRect(); return { left: r.left, right: r.right }; }")
    x = dock.eval_on_selector("#cz-tt-chips .cz-x", "e => { const r = e.getBoundingClientRect(); return { w: r.width, h: r.height, right: r.right }; }")
    assert box["right"] <= 390 and x["right"] <= 390
    assert x["w"] >= 24 and x["h"] >= 24


def _contrast(a, b):
    def lum(rgb):
        out = []
        for c in rgb:
            c = c / 255
            out.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
        return 0.2126 * out[0] + 0.7152 * out[1] + 0.0722 * out[2]
    hi, lo = max(lum(a), lum(b)), min(lum(a), lum(b))
    return (hi + 0.05) / (lo + 0.05)


@pytest.mark.parametrize("stage", [1, 2, 3, 4])
def test_the_white_text_of_the_verdict_pill_reads_on_every_verdict_color(dock, stage):
    rgb = dock.evaluate("""(s) => { const e = document.createElement('span'); e.className = 'cz-stage on s' + s; e.textContent = 'x'; document.body.appendChild(e);
        const m = getComputedStyle(e).backgroundColor.match(/\\d+/g).slice(0, 3).map(Number); e.remove(); return m; }""", stage)
    assert _contrast(rgb, (255, 255, 255)) >= 4.5


def test_a_pressed_button_still_looks_pressed_in_forced_colors(dock):
    dock.emulate_media(forced_colors="active")
    dock.click('#cz-tt-gender [data-v="female"]')
    colors = dock.evaluate("""() => ['female', 'male'].map((v) => { const b = document.querySelector('#cz-tt-gender [data-v=' + v + ']');
        const s = getComputedStyle(b); return s.backgroundColor + '|' + s.color; })""")
    assert colors[0] != colors[1]


def test_a_list_that_is_still_loading_is_not_fetched_again_by_the_next_keystroke(dock):
    world = dock.world                                                                              # type: ignore[attr-defined]
    world.hold.add("targeting")
    before = world.hits.count("targeting")
    dock.click("#cz-tt-browse > summary")
    for ch in "skin":
        dock.type("#cz-tt-filter", ch)
    assert world.hits.count("targeting") - before == 1
    world.hold.discard("targeting")
    world.release("targeting")
    dock.wait_for_selector('#cz-tt-browse-body [data-tt="browse-add"]')


def test_the_popups_close_on_focus_alone_so_no_page_wide_listener_is_added_per_registration(dock):
    src = dock.evaluate("fetch('/static/constanza-tiktok.js').then(r => r.text())")
    assert 'document.addEventListener("mousedown"' not in src
