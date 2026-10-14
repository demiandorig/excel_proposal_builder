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
    "imps_low": 1_188_000, "imps_mid": 1_320_000, "imps_high": 1_452_000, "est_uniques": 240_000, "reach_universe": None, "series": None, "weeks": None,
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
    """What the harness's /api answers: the TikTok panel's routes through the real service over the fake TikTok."""

    def __init__(self):
        self.client = make_client()
        self.configured = True
        self.search_override = None
        self.requests: list[dict] = []

    def answer(self, route) -> None:
        req = route.request
        path = req.url.split("//", 1)[1].split("/", 1)[1].split("?")[0]
        url = req.url
        body = None
        status = 200
        if path == "api/constanza/tiktok/status":
            body = {"configured": self.configured, "missing": [] if self.configured else ["TIKTOK_ACCESS_TOKEN", "TIKTOK_ADVERTISER_ID"],
                    "advertiser": "…0001", "impressions_per_person": 5.5, "setup_hint": "x", "options": ct.static_options()}
        elif path == "api/constanza/tiktok/targeting":
            kind = url.split("kind=", 1)[1].split("&")[0]
            body = {"kind": kind, "items": ct.catalog(kind, client=self.client)}
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
    assert "240,000" in dock.inner_text("#cz-result")


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
    assert "240,000" in dock.inner_text("#cz-result")


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
