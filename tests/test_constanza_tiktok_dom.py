"""The TikTok panel in a REAL browser (headless Chromium through Playwright): the behaviors a stub page cannot show - which button keeps
the keyboard focus, whether the chips on the screen are the picks the request will send, what Escape does, what Reset leaves behind.

The page is a small harness (not the whole app): the real constanza.js, constanza-tiktok.js, the shared audience kit and the SegmentBrowser pop-up
(with their CSS) against a stub `state.catalog`, with the /api/constanza/* routes answered in the browser by the same service functions the server
uses, over the in-memory fake of TikTok's API.
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
<link rel="stylesheet" href="/static/styles.css"><link rel="stylesheet" href="/static/constanza.css">
<link rel="stylesheet" href="/static/audience-kit.css"><link rel="stylesheet" href="/static/segment-browser.css"><link rel="stylesheet" href="/static/constanza-tiktok.css"></head><body>
<script>
  function escapeHtml(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[c])); }
  function _replaceGeoItem(t, i, r) { return r; }
  function _constanzaErrorText(d, f) { return (d && typeof d.detail === "string" && d.detail) || f || "Request failed."; }
  // the page's own reading of a line's targeting text (app.js), reduced to what the suggestion test needs
  function _targetsHispanic(li) { return /hispanic|latino/i.test(String((li && li.target_override) || "")); }
  var state = { lineItems: [{ id: "L-HISP", product_name: "Tiktok Ads", target_override: "Hispanic adults 25-44" }, { id: "L-PLAIN", product_name: "Tiktok Ads", target_override: "Adults 18+" }], catalog: { families: ["Social", "Entravision Plus"], products_by_family: {
    "Social": [{ name: "Tiktok Ads", family: "Social", is_addon: false, forecast: { enabled: true, method: "tiktok", series: null } }],
    "Entravision Plus": [{ name: "Roku", family: "Entravision Plus", is_addon: false, forecast: { enabled: true, method: "platform", series: { key: "roku", channel: "platform_model", data_basis: "modeled", unit: "monthly" } } }] } } };
</script>
<script src="/static/segment-browser.js"></script>
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


NOT_CONNECTED = "TikTok isn't connected yet — an admin needs to add TIKTOK_ACCESS_TOKEN and TIKTOK_ADVERTISER_ID to the app's secrets."


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
             "api/constanza/estimate": "estimate", "api/constanza/resolve": "resolve", "api/constanza/interest-taxonomy": "taxonomy"}

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
        if not self.configured and name in ("targeting", "search"):                  # the real routes refuse until the secrets are there
            route.fulfill(status=503, content_type="application/json", body=json.dumps({"detail": NOT_CONNECTED}))
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
        elif path == "api/constanza/interest-taxonomy":                              # the Entravision audience block's list: not this panel's, but the dock asks for it
            body = {"groups": []}
        elif path == "api/constanza/resolve":
            body = {"areas": [], "ambiguous": [], "suggestions": [], "interpretations": [], "unmatched_details": [], "unmatched_tokens": [], "notes": []}
        elif path == "api/constanza/estimate":
            self.requests.append(json.loads(req.post_data or "{}"))
            if not self.configured:
                status, body = 503, {"detail": NOT_CONNECTED}
            else:
                body = ESTIMATE
        else:
            status, body = 404, {"detail": "not found"}
        if name in self.hold:
            self.pending.append((name, route, status, body))
            return
        route.fulfill(status=status, content_type="application/json", body=json.dumps(body))


@pytest.fixture
def open_dock(browser, server):
    """Opens the dock on the TikTok product. `open_dock(configured=False, catalog_override={...}, hold={"targeting"}, ...)` sets up the fake world
    BEFORE the panel is shown, which matters because the panel asks for TikTok's language list the moment it is shown."""
    opened = []

    def _open(**world_setup):
        world = World()
        for key, value in world_setup.items():
            setattr(world, key, value)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()
        page.route("**/api/**", world.answer)
        page.route("**/fonts.googleapis.com/**", lambda r: r.abort())
        page.route("**/fonts.gstatic.com/**", lambda r: r.abort())
        if getattr(world, "block_segment_browser", False):          # the pop-up's script never arrives (a blocked script, a stale cache)
            page.route("**/segment-browser.js", lambda r: r.abort())
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{server}/harness.html")
        page.evaluate("Constanza.open({})")
        page.click("#cz-product-btn")
        page.fill("#cz-picker-search", "tiktok")
        page.click("#cz-picker-list .cz-option")
        page.wait_for_selector('#cz-tt-status[data-state="on"], #cz-tt-status[data-state="off"], #cz-tt-status[data-state="error"]')
        page.world, page.errors = world, errors                      # type: ignore[attr-defined]
        opened.append((context, errors))
        return page

    yield _open
    for context, errors in opened:
        context.close()
        assert errors == [], errors


@pytest.fixture
def dock(open_dock):
    return open_dock()


def chips(page):
    return page.eval_on_selector_all("#cz-tt-chips .aud-chip", "els => els.map(e => e.querySelector('.aud-chip-label').textContent.trim())")


def lang_chips(page):
    return page.eval_on_selector_all("#cz-tt-lang-chips .aud-chip", "els => els.map(e => e.querySelector('.aud-chip-label').textContent.trim())")


def picks(page):
    return page.evaluate("Constanza._plugin('tiktok')._pure.T.picks.map(p => p.name)")


def asked(page):
    """The targeting the panel would send right now."""
    return page.evaluate("Constanza._plugin('tiktok').requestPart().tiktok")


def sums(page):
    return page.eval_on_selector_all("#cz-tt .aud-sec-sum", "els => els.map(e => e.textContent.trim())")


def open_browse(page, tab="general_interest"):
    page.click("#cz-tt-browse")
    page.wait_for_selector(".segb-dialog")
    if tab != "general_interest":
        page.click(f'.segb-tab[data-tab="{tab}"]')
    page.wait_for_selector(".segb-row")                              # the table is hidden while a tab loads


def tick(page, *ids):
    for i in ids:
        page.click(f'.segb-row[data-id="{i}"]')


def apply_browse(page):
    page.click(".segb-apply")
    page.wait_for_selector(".segb-dialog", state="detached")


def browse_pick(page, tab, *ids):
    open_browse(page, tab)
    tick(page, *ids)
    apply_browse(page)


def open_line(page, line_id):
    """What Step 05's "Refine in Constanza" does: open the dock for one of the proposal's lines."""
    page.evaluate("(id) => Constanza.open({ lineId: id, geo: 'Los Angeles', product: 'Tiktok Ads', lineLabel: 'Tiktok Ads' })", line_id)


# --------------------------------------------------------------------------------------------- the panel is there and works

def test_the_tiktok_product_swaps_in_its_panel_and_hides_the_entravision_audience(dock):
    assert dock.is_visible("#cz-tt") and not dock.is_visible("#cz-aud")
    assert dock.inner_text("#cz-go").strip() == "Estimate audience →"
    assert "Connected to TikTok" in dock.inner_text("#cz-tt-status")
    assert dock.get_attribute("#cz-tt-status", "class").split() == ["aud-notice", "aud-notice--connected"]
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
    dock.click("#cz-tt-results .aud-ta-opt")
    dock.click("#cz-go")
    dock.wait_for_selector("#cz-result .cz-headline")
    sent = dock.world.requests[-1]["tiktok"]                       # type: ignore[attr-defined]
    assert sent["gender"] == "female" and sent["age_groups"] == ["AGE_25_34", "AGE_35_44"] and sent["interest_category_ids"] == ["12100"]
    assert "216,000" in dock.inner_text("#cz-result")


# --------------------------------------------------------------------------------------------- the four sections

def test_the_panel_is_four_numbered_sections_after_the_geography_the_dock_already_asks(dock):
    assert dock.eval_on_selector_all("#cz-tt .aud-sec-title", "els => els.map(e => e.textContent.trim())") == ["Who", "Language", "Audience", "More targeting"]
    # the kit numbers the badges by itself (a CSS counter over the sections of one builder): empty badges, one builder, four sections in it
    assert dock.eval_on_selector_all("#cz-tt .aud-step", "els => els.map(e => e.textContent + '|' + e.getAttribute('aria-hidden'))") == ["|true"] * 4
    assert dock.locator("#cz-tt .aud-builder").count() == 1 and dock.locator("#cz-tt .aud-builder .aud-sec").count() == 4
    assert dock.locator("#cz-tt .aud-builder > .aud-sec").count() == 3 and dock.locator("#cz-tt .aud-builder > .cz-fold > .aud-sec#cz-tt-more").count() == 1      # the folding one in its wrapper
    assert dock.locator("#cz-tt .aud-sec").count() == 4 and dock.locator("#cz-tt #cz-geo").count() == 0     # the geography is the dock's own box, not repeated
    assert dock.evaluate("document.getElementById('cz-geo').getBoundingClientRect().top < document.getElementById('cz-tt').getBoundingClientRect().top")
    assert dock.evaluate("document.getElementById('cz-tt-more').tagName") == "DETAILS" and not dock.evaluate("document.getElementById('cz-tt-more').open")


def test_each_section_says_what_is_chosen_in_one_line_with_a_count_and_clears_itself(dock):
    assert sums(dock) == ["Everyone 18+", "Any language", "Any interest or behavior", "TikTok placement · Reach objective"]
    assert all(dock.is_hidden(f"#cz-tt-count-{k}") and dock.is_hidden(f"#cz-tt-clear-{k}") for k in ("who", "lang", "aud", "more"))
    dock.click('#cz-tt-gender [data-v="female"]')
    dock.click('#cz-tt-age [data-v="AGE_25_34"]')
    dock.click('#cz-tt-age [data-v="AGE_35_44"]')
    dock.click('#cz-tt-lang-quick [data-v="es"]')
    dock.fill("#cz-tt-q", "skin")
    dock.click("#cz-tt-results .aud-ta-opt")
    dock.click("#cz-tt-more > summary")
    dock.click('#cz-tt-spend [data-v="HIGH"]')
    dock.click('#cz-tt-os [data-v="IOS"]')
    dock.click('#cz-tt-obj [data-v="traffic"]')
    assert sums(dock) == ["Women · 25–44", "Spanish", "Skincare", "High spenders · iOS · Traffic"]
    assert [dock.inner_text(f"#cz-tt-count-{k}").strip() for k in ("who", "lang", "aud", "more")] == ["2", "1", "1", "3"]
    assert all(dock.evaluate(f"document.getElementById('{i}').classList.contains('has-value')") for i in ("cz-tt-sec-who", "cz-tt-sec-lang", "cz-tt-sec-aud", "cz-tt-more"))
    dock.click("#cz-tt-clear-who")
    assert sums(dock)[0] == "Everyone 18+" and dock.is_hidden("#cz-tt-count-who") and dock.is_hidden("#cz-tt-clear-who")
    sent = asked(dock)
    assert sent["gender"] is None and sent["age_groups"] == [] and sent["languages"] == ["es"]       # only that section was cleared
    assert dock.evaluate("document.activeElement === document.querySelector('#cz-tt-age button')")        # its Clear is gone: the focus goes to the section's first control
    dock.click("#cz-tt-clear-lang")
    dock.click("#cz-tt-clear-aud")
    dock.click("#cz-tt-clear-more")
    assert sums(dock) == ["Everyone 18+", "Any language", "Any interest or behavior", "TikTok placement · Reach objective"]
    assert asked(dock)["objective"] == "reach" and asked(dock)["operating_system"] is None and asked(dock)["languages"] == [] and picks(dock) == []
    assert "More targeting cleared" in dock.inner_text("#cz-status")                                    # each Clear is announced


def test_the_clear_link_of_the_folded_section_clears_without_opening_or_folding_it(dock):
    dock.click("#cz-tt-more > summary")
    dock.click('#cz-tt-spend [data-v="HIGH"]')
    dock.click("#cz-tt-clear-more")
    assert dock.evaluate("document.getElementById('cz-tt-more').open") is True and asked(dock)["spending_power"] is None     # still open: Clear is not a toggle
    dock.click('#cz-tt-spend [data-v="HIGH"]')
    dock.click("#cz-tt-more > summary")                                                                                     # fold it
    assert dock.evaluate("document.getElementById('cz-tt-more').open") is False and dock.is_visible("#cz-tt-clear-more")
    dock.click("#cz-tt-clear-more")
    assert dock.evaluate("document.getElementById('cz-tt-more').open") is False and asked(dock)["spending_power"] is None   # cleared while folded: still folded


def test_the_age_presets_pick_the_brackets_they_cover_and_press_again_to_undo(dock):
    labels = dock.eval_on_selector_all("#cz-tt-age-presets button", "els => els.map(e => e.textContent.trim())")
    assert labels == ["18–34", "25–54", "35+"]
    dock.click('#cz-tt-age-presets button:has-text("25–54")')
    assert asked(dock)["age_groups"] == ["AGE_25_34", "AGE_35_44", "AGE_45_54"] and sums(dock)[0] == "25–54"
    assert dock.get_attribute('#cz-tt-age-presets button:has-text("25–54")', "aria-pressed") == "true"
    assert dock.get_attribute('#cz-tt-age [data-v="AGE_35_44"]', "aria-pressed") == "true" and dock.get_attribute('#cz-tt-age [data-v="AGE_18_24"]', "aria-pressed") == "false"
    dock.click('#cz-tt-age [data-v="AGE_55_100"]')                                                                     # a bracket more: the preset no longer describes it
    assert dock.get_attribute('#cz-tt-age-presets button:has-text("25–54")', "aria-pressed") == "false" and sums(dock)[0] == "25+"
    dock.click('#cz-tt-age-presets button:has-text("35+")')
    assert asked(dock)["age_groups"] == ["AGE_35_44", "AGE_45_54", "AGE_55_100"]
    dock.click('#cz-tt-age-presets button:has-text("35+")')
    assert asked(dock)["age_groups"] == [] and sums(dock)[0] == "Everyone 18+"


# --------------------------------------------------------------------------------------------- language

def test_tiktoks_language_list_is_asked_when_the_panel_is_shown_and_only_once(dock):
    world = dock.world                                                                                  # type: ignore[attr-defined]
    assert world.hits.count("targeting") == 1                                                          # the language list, before anyone typed or browsed
    dock.click("#cz-product-btn"); dock.fill("#cz-picker-search", "roku"); dock.click("#cz-picker-list .cz-option")
    dock.click("#cz-product-btn"); dock.fill("#cz-picker-search", "tiktok"); dock.click("#cz-picker-list .cz-option")
    dock.click("#cz-tt-lang-q")
    dock.wait_for_selector("#cz-tt-lang-results .aud-ta-opt")
    assert world.hits.count("targeting") == 1                                                          # cached: shown again, typed in, not asked again


def test_the_spanish_quick_pick_adds_spanish_from_tiktoks_list_and_a_second_press_takes_it_out(dock):
    pill = '#cz-tt-lang-quick [data-v="es"]'
    assert dock.inner_text(pill).strip() == "Spanish speakers" and not dock.is_disabled(pill) and dock.get_attribute(pill, "aria-pressed") == "false"
    assert [t.strip() for t in dock.eval_on_selector_all("#cz-tt-lang-quick button", "els => els.map(e => e.textContent)")] == ["Spanish speakers", "English", "Spanish + English"]
    dock.focus(pill)
    dock.keyboard.press("Enter")
    assert lang_chips(dock) == ["Spanish"] and asked(dock)["languages"] == ["es"] and sums(dock)[1] == "Spanish"
    assert dock.get_attribute(pill, "aria-pressed") == "true"
    assert dock.evaluate(f"document.activeElement === document.querySelector({json.dumps(pill)})")       # flipped in place: the keyboard focus stays on the pill
    dock.keyboard.press("Enter")
    assert lang_chips(dock) == [] and asked(dock)["languages"] == [] and dock.get_attribute(pill, "aria-pressed") == "false"


def test_the_english_and_the_bilingual_quick_picks_and_the_request_carries_the_codes_in_order(dock):
    dock.click('#cz-tt-lang-quick [data-v="es+en"]')
    assert lang_chips(dock) == ["Spanish", "English"] and asked(dock)["languages"] == ["en", "es"]
    assert all(dock.get_attribute(f'#cz-tt-lang-quick [data-v="{k}"]', "aria-pressed") == "true" for k in ("es", "en", "es+en"))
    dock.click('#cz-tt-lang-quick [data-v="en"]')                                                      # English off: Spanish stays, the bilingual pick is no longer "all there"
    assert lang_chips(dock) == ["Spanish"] and dock.get_attribute('#cz-tt-lang-quick [data-v="es+en"]', "aria-pressed") == "false"
    dock.click('#cz-tt-lang-quick [data-v="es+en"]')                                                   # adds only the one that is missing
    assert lang_chips(dock) == ["Spanish", "English"]


def test_a_quick_pick_looks_for_the_language_in_the_list_by_code_then_name_and_adds_what_the_list_calls_it(open_dock):
    page = open_dock(catalog_override={"languages": [{"code": "spa", "name": "Spanish (Latin America)"}, {"code": "eng", "name": "English"}, {"code": "por", "name": "Portuguese"}]})
    page.click('#cz-tt-lang-quick [data-v="es"]')
    assert asked(page)["languages"] == ["spa"]                                                         # nothing is hard-coded: the code is the list's own
    page.click('#cz-tt-lang-quick [data-v="es+en"]')
    assert asked(page)["languages"] == ["eng", "spa"]


def test_a_quick_pick_for_a_language_the_list_lacks_is_disabled_with_the_reason_and_the_bilingual_pick_is_not_offered(open_dock):
    page = open_dock(catalog_override={"languages": [{"code": "pt", "name": "Portuguese"}, {"code": "en", "name": "English"}]})
    spanish = '#cz-tt-lang-quick [data-v="es"]'
    assert page.is_disabled(spanish) and "no Spanish" in page.get_attribute(spanish, "title")
    assert not page.is_disabled('#cz-tt-lang-quick [data-v="en"]')
    assert page.locator('#cz-tt-lang-quick [data-v="es+en"]').count() == 0
    page.click('#cz-tt-lang-quick [data-v="en"]')
    assert asked(page)["languages"] == ["en"]


def test_the_quick_picks_are_disabled_with_a_reason_while_tiktoks_list_is_unavailable_and_work_after_try_again(open_dock):
    page = open_dock(fail={"targeting": (503, {"detail": "TikTok is down"})})
    page.wait_for_selector("#cz-tt-lang-quick-note:not([hidden])")
    assert all(page.is_disabled(f'#cz-tt-lang-quick [data-v="{k}"]') for k in ("es", "en", "es+en"))
    assert "TikTok is down" in page.get_attribute('#cz-tt-lang-quick [data-v="es"]', "title")
    assert "Quick picks need TikTok’s language list" in page.inner_text("#cz-tt-lang-quick-note") and "TikTok is down" in page.inner_text("#cz-tt-lang-quick-note")
    page.click("#cz-tt-lang-quick [data-v='es']", force=True)                                          # a disabled pill does nothing
    assert lang_chips(page) == []
    page.world.fail.clear()                                                                            # type: ignore[attr-defined]
    page.click('#cz-tt-lang-quick-note [data-tt="lang-retry"]')
    page.wait_for_selector('#cz-tt-lang-quick [data-v="es"]:not([disabled])')
    assert page.is_hidden("#cz-tt-lang-quick-note")
    page.click('#cz-tt-lang-quick [data-v="es"]')
    assert asked(page)["languages"] == ["es"]


def test_when_tiktok_is_not_connected_the_banner_says_so_and_the_quick_picks_point_to_the_connection(open_dock):
    page = open_dock(configured=False)
    assert page.get_attribute("#cz-tt-status", "data-state") == "off" and "aud-notice--warn" in page.get_attribute("#cz-tt-status", "class")
    text = page.inner_text("#cz-tt-status")
    assert "TikTok isn’t connected yet" in text and "TIKTOK_ACCESS_TOKEN" in text and "TIKTOK_ADVERTISER_ID" in text and "set the audience up now" in text
    page.wait_for_selector("#cz-tt-lang-quick-note:not([hidden])")
    assert page.inner_text("#cz-tt-lang-quick-note").startswith("Quick picks need the TikTok connection")
    assert page.is_disabled('#cz-tt-lang-quick [data-v="es"]')
    page.click('#cz-tt-gender [data-v="male"]')                                                        # the rest of the panel still works: the audience can be set up now
    assert asked(page)["gender"] == "male"


def test_a_line_that_targets_hispanic_audiences_is_offered_spanish_and_nothing_is_added_until_the_planner_says_so(dock):
    open_line(dock, "L-HISP")
    dock.wait_for_selector("#cz-tt-lang-suggest:not([hidden])")
    assert "This line targets Hispanic audiences — add Spanish?" in dock.inner_text("#cz-tt-lang-suggest")
    assert lang_chips(dock) == [] and asked(dock)["languages"] == []                                   # offered, never applied silently
    dock.focus('#cz-tt-lang-suggest [data-tt="lang-hispanic"]')
    dock.keyboard.press("Enter")
    assert lang_chips(dock) == ["Spanish"] and asked(dock)["languages"] == ["es"] and dock.is_hidden("#cz-tt-lang-suggest")
    assert dock.evaluate("document.activeElement === document.querySelector('#cz-tt-lang-quick [data-v=es]')")        # the pressed button is gone: the focus moves to the Spanish pick


def test_the_hispanic_suggestion_can_be_set_aside_and_a_line_that_does_not_target_hispanic_audiences_has_none(dock):
    open_line(dock, "L-PLAIN")
    dock.wait_for_timeout(150)
    assert dock.is_hidden("#cz-tt-lang-suggest")
    open_line(dock, "L-HISP")
    dock.wait_for_selector("#cz-tt-lang-suggest:not([hidden])")
    dock.click('#cz-tt-lang-suggest [data-tt="lang-dismiss"]')
    assert dock.is_hidden("#cz-tt-lang-suggest") and lang_chips(dock) == []
    dock.click('#cz-tt-gender [data-v="male"]')                                                        # later changes do not bring it back
    assert dock.is_hidden("#cz-tt-lang-suggest")
    open_line(dock, "L-HISP")                                                                          # a fresh open of the line asks again
    dock.wait_for_selector("#cz-tt-lang-suggest:not([hidden])")
    dock.click('#cz-tt-lang-quick [data-v="es"]')                                                      # Spanish chosen by hand: nothing left to suggest
    assert dock.is_hidden("#cz-tt-lang-suggest")


def test_taking_spanish_out_again_is_an_answer_so_the_suggestion_is_not_made_again_for_that_line(dock):
    open_line(dock, "L-HISP")
    dock.wait_for_selector("#cz-tt-lang-suggest:not([hidden])")
    dock.click('#cz-tt-lang-suggest [data-tt="lang-hispanic"]')
    assert lang_chips(dock) == ["Spanish"]
    dock.click('#cz-tt-lang-chips [data-tt="rm-lang"]')
    assert lang_chips(dock) == [] and dock.is_hidden("#cz-tt-lang-suggest")
    dock.click('#cz-tt-lang-quick [data-v="es"]')                                                      # on and off with the quick pick: still no nagging
    dock.click('#cz-tt-lang-quick [data-v="es"]')
    assert dock.is_hidden("#cz-tt-lang-suggest")


def test_the_dock_telling_the_panel_the_line_is_hispanic_is_enough_for_the_suggestion(dock):
    assert dock.is_hidden("#cz-tt-lang-suggest")
    dock.evaluate("() => { Constanza._state.hispanic = true; Constanza._plugin('tiktok').onSelect(); }")
    dock.wait_for_selector("#cz-tt-lang-suggest:not([hidden])")


def test_the_language_search_lists_the_whole_list_and_a_pick_becomes_a_chip(dock):
    dock.click("#cz-tt-lang-q")
    dock.wait_for_selector("#cz-tt-lang-results .aud-ta-opt")
    assert dock.eval_on_selector_all("#cz-tt-lang-results .aud-ta-name", "els => els.map(e => e.textContent)") == ["English", "Spanish", "Chinese", "Portuguese", "French"]
    dock.fill("#cz-tt-lang-q", "port")
    dock.click("#cz-tt-lang-results .aud-ta-opt")
    assert lang_chips(dock) == ["Portuguese"] and asked(dock)["languages"] == ["pt"] and dock.input_value("#cz-tt-lang-q") == ""
    assert dock.is_hidden("#cz-tt-lang-results") and dock.inner_text("#cz-tt-lang-hint").strip() == "1 chosen"


def test_the_language_pop_up_lists_every_language_sortably_and_applying_adds_and_removes_chips(dock):
    dock.click("#cz-tt-lang-browse")
    dock.wait_for_selector(".segb-row")
    assert dock.inner_text(".segb-title").strip() == "Browse TikTok languages"
    assert dock.eval_on_selector_all(".segb-row .segb-td-name .segb-cell", "els => els.map(e => e.textContent)") == ["Chinese", "English", "French", "Portuguese", "Spanish"]
    dock.click('.segb-sort[data-key="code"]')
    assert dock.eval_on_selector_all(".segb-row .segb-td-name .segb-cell", "els => els.map(e => e.textContent)") == ["English", "Spanish", "French", "Portuguese", "Chinese"]   # by code: en, es, fr, pt, zh
    dock.click('.segb-row[data-id="es"]')
    dock.click('.segb-row[data-id="fr"]')
    apply_browse(dock)
    assert lang_chips(dock) == ["Spanish", "French"] and asked(dock)["languages"] == ["es", "fr"]
    assert dock.evaluate("document.activeElement.id") == "cz-tt-lang-browse"
    dock.click("#cz-tt-lang-browse")
    dock.wait_for_selector(".segb-row")
    assert dock.get_attribute('.segb-row[data-id="es"]', "aria-selected") == "true" and dock.get_attribute('.segb-row[data-id="pt"]', "aria-selected") == "false"
    dock.click('.segb-row[data-id="es"]')                                                              # untick Spanish
    apply_browse(dock)
    assert lang_chips(dock) == ["French"] and asked(dock)["languages"] == ["fr"]


def test_at_most_twenty_languages_can_be_chosen_in_the_pop_up_and_the_search(open_dock):
    page = open_dock(catalog_override={"languages": [{"code": f"l{i:02d}", "name": f"Language {i:02d}"} for i in range(30)]})
    page.click("#cz-tt-lang-browse")
    page.wait_for_selector(".segb-row")
    page.click(".segb-all")                                                                            # tick everything: the pop-up stops at the limit
    assert "limit of 20" in page.inner_text(".segb-limit")
    apply_browse(page)
    assert len(lang_chips(page)) == 20 and len(asked(page)["languages"]) == 20
    page.click("#cz-tt-lang-q")
    page.wait_for_selector("#cz-tt-lang-results .aud-ta-opt")
    page.click("#cz-tt-lang-results .aud-ta-opt >> nth=0")                                             # the 21st by the search box: refused, the list stays as it was
    assert len(lang_chips(page)) == 20


def test_the_note_says_in_plain_words_what_tiktoks_language_setting_is(dock):
    text = dock.inner_text("#cz-tt-sec-lang")
    assert "language people have set in their TikTok app" in text and "not the language of your ad" in text


# --------------------------------------------------------------------------------------------- the browse pop-up (audience)

def test_browse_all_categories_opens_the_shared_pop_up_one_tab_per_kind_sorted_by_name(dock):
    dock.click("#cz-tt-browse")
    dock.wait_for_selector(".segb-row")
    assert dock.is_visible(".segb-dialog") and dock.get_attribute(".segb-dialog", "role") == "dialog"
    assert dock.eval_on_selector_all(".segb-tab .segb-tab-label", "els => els.map(e => e.textContent)") == ["Interests", "Purchase intent", "Video", "Creators", "Hashtags"]
    assert dock.inner_text(".segb-title").strip() == "Browse TikTok interests & behaviors" and "up to 50 of each kind" in dock.inner_text(".segb-sub")
    assert dock.eval_on_selector_all(".segb-sort-label", "els => els.map(e => e.textContent)") == ["Name", "Category"]
    names = dock.eval_on_selector_all(".segb-row .segb-td-name .segb-cell", "els => els.map(e => e.textContent)")
    expected = sorted((i["name"] for i in ct.catalog("general_interest", client=dock.world.client)), key=str.lower)           # type: ignore[attr-defined]
    assert names == expected and len(names) == 14
    assert dock.get_attribute('.segb-th:has(.segb-sort[data-key="name"])', "aria-sort") == "ascending"
    assert dock.get_attribute(".segb-tab[data-tab='general_interest']", "aria-selected") == "true"
    assert dock.evaluate("document.activeElement.classList.contains('segb-search-input')")


def test_the_pop_up_sorts_by_category_and_each_tab_is_loaded_through_the_real_service_when_opened(dock):
    world = dock.world                                                                                  # type: ignore[attr-defined]
    before = world.hits.count("targeting")
    dock.click("#cz-tt-browse")
    dock.wait_for_selector(".segb-row")
    assert world.hits.count("targeting") == before + 1                                                 # only the Interests tab so far
    dock.click('.segb-sort[data-key="category"]')
    cats = dock.eval_on_selector_all(".segb-row .segb-td:last-child .segb-cell", "els => els.map(e => e.textContent)")
    assert cats[0] == "Apparel & Accessories" and cats[-1] == "Top level"                              # a category with no parent sorts last either way
    dock.click('.segb-sort[data-key="category"]')
    cats = dock.eval_on_selector_all(".segb-row .segb-td:last-child .segb-cell", "els => els.map(e => e.textContent)")
    assert cats[0] == "Vehicles & Transportation"
    for tab, first in (("purchase_intention", "Automotive"), ("video_interaction", "Cars & Motorcycles"), ("creator_interaction", "DIY & Life Hacks"), ("hashtag_interaction", "christmas")):
        dock.click(f'.segb-tab[data-tab="{tab}"]')
        dock.wait_for_selector(f'.segb-tab[data-tab="{tab}"][aria-selected="true"]')
        dock.wait_for_selector(".segb-row")                                                            # the table is hidden while a tab loads
        shown = dock.eval_on_selector_all(".segb-row .segb-td-name .segb-cell", "els => els.map(e => e.textContent)")
        assert first in shown, (tab, shown)
    assert world.hits.count("targeting") == before + 5                                                 # one per tab, no more
    counts = dock.eval_on_selector_all(".segb-tab-count", "els => els.map(e => e.textContent)")
    assert counts == ["14", "3", "3", "3", "2"]


def test_ticking_in_the_pop_up_adds_chips_and_the_request_and_unticking_there_removes_them(dock):
    """A ghost chip stayed behind after unticking in Browse, and the next x then removed the WRONG pick."""
    browse_pick(dock, "general_interest", "12100", "12200")                                            # Skincare, Makeup
    assert chips(dock) == ["Skincare", "Makeup"] and picks(dock) == ["Skincare", "Makeup"]
    assert asked(dock)["interest_category_ids"] == ["12100", "12200"]
    assert dock.evaluate("document.activeElement.id") == "cz-tt-browse"                                  # the focus is back on the button that opened it
    open_browse(dock)
    assert dock.get_attribute('.segb-row[data-id="12100"]', "aria-selected") == "true" and dock.get_attribute('.segb-row[data-id="12200"]', "aria-selected") == "true"
    assert dock.get_attribute('.segb-row[data-id="14100"]', "aria-selected") == "false"
    dock.click('.segb-row[data-id="12100"]')                                                           # untick Skincare
    assert dock.inner_text(".segb-apply").strip() == "Remove 1" and "1 to remove" in dock.inner_text(".segb-sel")      # only a removal: the button does not say "Add"
    apply_browse(dock)
    assert chips(dock) == ["Makeup"] and picks(dock) == ["Makeup"] and asked(dock)["interest_category_ids"] == ["12200"]
    assert dock.inner_text("#cz-tt-count-aud").strip() == "1"
    dock.click('#cz-tt-chips [data-tt="rm-pick"]')                                                     # the only chip is Makeup: it is the one removed
    assert chips(dock) == [] and picks(dock) == []


def test_one_session_of_the_pop_up_can_pick_from_several_tabs_and_each_pick_lands_in_its_own_field(dock):
    open_browse(dock)
    tick(dock, "14100")                                                                                # Soccer (an interest)
    dock.click('.segb-tab[data-tab="hashtag_interaction"]')
    dock.wait_for_selector('.segb-row[data-id="21"]')
    tick(dock, "21")                                                                                   # #cybermonday
    apply_browse(dock)
    sent = asked(dock)
    assert sent["interest_category_ids"] == ["14100"] and sent["hashtag_ids"] == ["21"] and sent["placements"] == ["PLACEMENT_TIKTOK"]
    assert chips(dock) == ["Soccer", "cybermonday"]
    assert dock.eval_on_selector_all("#cz-tt-chips .aud-chip-meta", "els => els.map(e => e.textContent)") == ["Interest", "Hashtag"]
    open_browse(dock)                                                                                  # it opens on the tab of the first pick of the last session
    assert dock.get_attribute(".segb-tab[data-tab='general_interest']", "aria-selected") == "true"
    assert dock.get_attribute('.segb-row[data-id="14100"]', "aria-selected") == "true"


def escape_out(page):
    """Escape until the pop-up is gone: with picks ticked it first asks "Close without applying?" (a second Escape discards). The dock stays open throughout."""
    for _ in range(2):
        page.keyboard.press("Escape")
        assert page.is_visible("#constanza-dock")
        if not page.locator(".segb-dialog").count():
            return
    page.wait_for_selector(".segb-dialog", state="detached")


def test_cancel_and_escape_leave_the_picks_alone_and_escape_does_not_close_the_dock(dock):
    open_browse(dock)
    tick(dock, "12100")
    escape_out(dock)
    assert picks(dock) == [] and dock.is_visible("#constanza-dock")
    assert dock.evaluate("document.activeElement.id") == "cz-tt-browse"
    open_browse(dock)
    tick(dock, "12100")
    dock.click(".segb-cancel")
    dock.wait_for_selector(".segb-dialog", state="detached")
    assert picks(dock) == [] and asked(dock)["interest_category_ids"] == []


def test_a_full_kind_stops_taking_ticks_in_the_pop_up_and_the_panel_gets_exactly_fifty(open_dock):
    page = open_dock(catalog_override={"hashtag_interaction": [{"id": str(i), "name": f"tag{i:02d}", "path": ""} for i in range(1, 61)]})
    open_browse(page, "hashtag_interaction")
    page.click(".segb-all")                                                                            # tick everything: only 50 fit
    assert page.locator('.segb-row[aria-selected="true"]').count() == 50
    assert "at most 50" in page.inner_text(".segb-limit").lower() and page.is_visible(".segb-limit")
    assert page.is_disabled('.segb-row[data-id="60"] input[type="checkbox"]') or page.get_attribute('.segb-row[data-id="60"]', "aria-disabled") == "true"
    apply_browse(page)
    assert len(asked(page)["hashtag_ids"]) == 50 and len(chips(page)) == 50
    assert "not added" not in page.inner_text("#cz-tt-pick-note")                                      # nothing was refused: the pop-up never let a 51st be ticked


def test_the_fifty_are_counted_per_kind_so_a_full_kind_leaves_the_other_kinds_open(open_dock):
    page = open_dock(catalog_override={"hashtag_interaction": [{"id": str(i), "name": f"tag{i:02d}", "path": ""} for i in range(1, 61)]})
    open_browse(page, "hashtag_interaction")
    page.click(".segb-all")
    apply_browse(page)
    open_browse(page, "general_interest")                                                              # another kind: its rows are not blocked by the 50 hashtags
    page.click('.segb-tab[data-tab="general_interest"]')                                               # (the pop-up reopens on the kind of the last pick)
    page.wait_for_selector('.segb-row[data-id="12100"]')
    tick(page, "12100")
    assert page.get_attribute('.segb-row[data-id="12100"]', "aria-selected") == "true"
    apply_browse(page)
    assert asked(page)["interest_category_ids"] == ["12100"] and len(asked(page)["hashtag_ids"]) == 50


def test_what_the_panel_already_holds_of_a_kind_counts_towards_its_fifty_in_the_pop_up(open_dock):
    page = open_dock(catalog_override={"hashtag_interaction": [{"id": str(i), "name": f"tag{i:02d}", "path": ""} for i in range(1, 61)]})
    open_browse(page, "hashtag_interaction")
    tick(page, *[f"{i}" for i in range(1, 46)])                                                        # 45 of the 60
    apply_browse(page)
    assert len(asked(page)["hashtag_ids"]) == 45
    open_browse(page, "hashtag_interaction")
    page.click(".segb-all")                                                                            # room for 5 more, not 15
    assert page.locator('.segb-row[aria-selected="true"]').count() == 50
    apply_browse(page)
    assert len(asked(page)["hashtag_ids"]) == 50


def test_a_pick_made_in_search_shows_ticked_in_the_pop_up_and_a_removal_unticks_it(dock):
    dock.fill("#cz-tt-q", "skin")
    dock.click("#cz-tt-results .aud-ta-opt")
    open_browse(dock)
    assert dock.get_attribute('.segb-row[data-id="12100"]', "aria-selected") == "true"
    dock.keyboard.press("Escape")
    dock.click('#cz-tt-chips [data-tt="rm-pick"]')
    open_browse(dock)
    assert dock.get_attribute('.segb-row[data-id="12100"]', "aria-selected") == "false"


def test_a_list_that_is_still_loading_is_not_fetched_again_when_the_pop_up_is_opened_again(dock):
    world = dock.world                                                                                  # type: ignore[attr-defined]
    world.hold.add("targeting")
    before = world.hits.count("targeting")
    dock.click("#cz-tt-browse")
    dock.wait_for_selector(".segb-dialog")
    dock.keyboard.press("Escape")
    dock.click("#cz-tt-browse")
    dock.wait_for_selector(".segb-dialog")
    assert world.hits.count("targeting") - before == 1
    world.hold.discard("targeting")
    world.release("targeting")
    dock.wait_for_selector(".segb-row")


def test_a_failed_list_shows_its_message_with_retry_in_the_pop_up_and_nothing_is_remembered(dock):
    dock.world.fail["targeting"] = (503, {"detail": "TikTok is down"})                                  # type: ignore[attr-defined]
    dock.click("#cz-tt-browse")
    dock.wait_for_selector(".segb-state--error")
    assert "TikTok is down" in dock.inner_text(".segb-state--error")
    dock.world.fail.clear()                                                                            # type: ignore[attr-defined]
    dock.click(".segb-retry")
    dock.wait_for_selector(".segb-row")


# --------------------------------------------------------------------------------------------- the review's findings

def test_a_pick_made_in_the_pop_up_does_not_outlive_its_chip_and_the_chips_always_match_the_request(dock):
    browse_pick(dock, "general_interest", "12100", "12200")
    open_browse(dock)
    dock.click('.segb-row[data-id="12100"]')
    apply_browse(dock)
    dock.fill("#cz-tt-q", "skin")
    dock.click("#cz-tt-results .aud-ta-opt")
    assert chips(dock) == ["Makeup", "Skincare"] and picks(dock) == ["Makeup", "Skincare"]
    assert asked(dock)["interest_category_ids"] == ["12100", "12200"]                                  # sorted: the request does not depend on the order of the clicks


@pytest.mark.parametrize("selector", ['#cz-tt-age [data-v="AGE_25_34"]', '#cz-tt-obj [data-v="traffic"]', '#cz-tt-gender [data-v="male"]',
                                      '#cz-tt-spend [data-v="HIGH"]', '#cz-tt-age-presets button >> nth=0', '#cz-tt-lang-quick [data-v="en"]'])
def test_the_keyboard_focus_stays_on_the_button_that_was_pressed(dock, selector):
    """The groups used to be rebuilt on every press, dropping the focus to the page."""
    dock.click("#cz-tt-more > summary")
    dock.focus(selector)
    dock.keyboard.press("Enter")
    assert dock.evaluate(f"document.activeElement === document.querySelector({json.dumps(selector.replace(' >> nth=0', ''))})")
    assert dock.get_attribute(selector, "aria-pressed") == "true"


def test_turning_pangle_on_and_then_picking_an_interaction_leaves_tiktok_as_the_only_placement(dock):
    dock.click("#cz-tt-more > summary")
    dock.click('#cz-tt-place [data-v="PLACEMENT_PANGLE"]')
    assert dock.evaluate("Constanza._plugin('tiktok').requestPart().tiktok.placements") == ["PLACEMENT_PANGLE", "PLACEMENT_TIKTOK"]
    assert sums(dock)[3] == "TikTok + Pangle"
    browse_pick(dock, "video_interaction", "1700")
    assert dock.evaluate("Constanza._plugin('tiktok').requestPart().tiktok.placements") == ["PLACEMENT_TIKTOK"]
    assert dock.get_attribute('#cz-tt-place [data-v="PLACEMENT_PANGLE"]', "aria-pressed") == "false"
    assert dock.is_disabled('#cz-tt-place [data-v="PLACEMENT_PANGLE"]')
    assert dock.is_visible("#cz-tt-period")                                                            # a video interaction asks for its look-back


def test_the_same_lock_holds_for_a_pick_made_in_search(dock):
    dock.click("#cz-tt-more > summary")
    dock.click('#cz-tt-place [data-v="PLACEMENT_PANGLE"]')
    dock.fill("#cz-tt-q", "tips")                                   # "#tipstips" and "#tips": hashtag interactions
    dock.click("#cz-tt-results .aud-ta-opt")
    assert dock.evaluate("Constanza._plugin('tiktok').requestPart().tiktok.placements") == ["PLACEMENT_TIKTOK"]


def test_reset_clears_every_section_and_leaves_the_pop_up_opening_on_the_first_tab(dock):
    browse_pick(dock, "hashtag_interaction", "21")
    dock.click('#cz-tt-lang-quick [data-v="es"]')
    dock.click('#cz-tt-gender [data-v="male"]')
    dock.click("#cz-reset")
    assert sums(dock) == ["Everyone 18+", "Any language", "Any interest or behavior", "TikTok placement · Reach objective"]
    assert picks(dock) == [] and lang_chips(dock) == [] and dock.get_attribute('#cz-tt-lang-quick [data-v="es"]', "aria-pressed") == "false"
    open_browse(dock)
    assert dock.get_attribute(".segb-tab[data-tab='general_interest']", "aria-selected") == "true"
    dock.wait_for_selector('.segb-row[data-id="12100"]')                                               # the Interests list is what shows, and it works
    tick(dock, "12100")
    apply_browse(dock)
    assert picks(dock) == ["Skincare"]


def test_escape_closes_the_popup_alone_when_it_is_on_screen_even_if_it_holds_no_option(dock):
    dock.world.search_override = {}                                  # type: ignore[attr-defined]  nothing matches: the popup says so
    dock.fill("#cz-tt-q", "zzzz")
    dock.wait_for_selector("#cz-tt-results .aud-ta-note")
    assert "Nothing in TikTok’s categories matches “zzzz”" in dock.inner_text("#cz-tt-results")
    dock.keyboard.press("Escape")
    assert dock.is_hidden("#cz-tt-results") and dock.is_visible("#constanza-dock")


def test_escape_with_no_popup_closes_the_dock_as_before(dock):
    dock.focus('#cz-tt-gender [data-v="male"]')
    dock.keyboard.press("Escape")
    assert dock.is_hidden("#constanza-dock")


def test_the_language_list_tells_a_screen_reader_which_option_is_active(dock):
    dock.click("#cz-tt-lang-q")
    dock.wait_for_selector("#cz-tt-lang-results .aud-ta-opt")
    dock.keyboard.press("ArrowDown")
    active = dock.get_attribute("#cz-tt-lang-q", "aria-activedescendant")
    assert active and dock.evaluate(f"document.getElementById({json.dumps(active)}).classList.contains('is-active')")
    assert dock.get_attribute(f"#{active}", "aria-selected") == "true" and dock.locator("#cz-tt-lang-results [aria-selected='true']").count() == 1
    dock.keyboard.press("Enter")
    assert dock.inner_text("#cz-tt-lang-chips").strip().startswith("Spanish")                 # the second option (English, Spanish, ...): the arrow moved it


def test_a_search_shows_hits_of_every_kind_under_a_heading_each(dock):
    many = {kind: [{"id": str(i), "name": f"{kind} {i}", "path": ""} for i in range(25)] for kind in
            ("general_interest", "additional_interest", "purchase_intention", "video_interaction", "creator_interaction", "hashtag_interaction")}
    dock.world.search_override = many                                # type: ignore[attr-defined]
    dock.fill("#cz-tt-q", "sports")
    dock.wait_for_selector("#cz-tt-results .aud-ta-opt")
    kinds = dock.eval_on_selector_all("#cz-tt-results .aud-ta-group", "els => els.map(e => e.textContent)")
    assert kinds == ["Interests", "Interest keywords", "Purchase intent", "Video interactions", "Creator interactions", "Hashtags"]


def test_purchase_intent_on_any_objective_but_traffic_offers_the_fix(dock):
    browse_pick(dock, "purchase_intention", "26100")
    assert "Reach campaign" in dock.inner_text("#cz-tt-pick-note")
    assert "aud-notice--warn" in dock.get_attribute("#cz-tt-pick-note .aud-notice", "class")
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
    dock.wait_for_selector('#cz-tt-status[data-state="off"]')        # the banner follows the server's answer
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
    dock.wait_for_selector("#cz-tt-results .aud-ta-opt")
    assert evil in dock.inner_text("#cz-tt-results")                # shown as text in the popup too
    dock.click("#cz-tt-results .aud-ta-opt")
    dock.click("#cz-go")
    dock.wait_for_timeout(300)
    assert dock.evaluate("window.__xss === undefined")
    assert evil in dock.inner_text("#cz-tt-chips")                  # shown as text


def test_text_from_tiktok_runs_nothing_in_the_pop_up_the_language_list_or_the_quick_picks_either(open_dock):
    evil = '<img src=x onerror="window.__xss=1">'
    page = open_dock(catalog_override={"general_interest": [{"id": "1", "name": evil, "path": evil, "has_children": False}],
                                       "languages": [{"code": "es", "name": evil}, {"code": "x", "name": "Other"}]})
    open_browse(page)
    assert evil in page.inner_text(".segb-dialog")
    tick(page, "1")
    apply_browse(page)
    page.click("#cz-tt-lang-q")
    page.wait_for_selector("#cz-tt-lang-results .aud-ta-opt")
    page.click('#cz-tt-lang-quick [data-v="es"]')                    # the "Spanish" of this list has a hostile name: it is only ever text
    page.wait_for_timeout(300)
    assert page.evaluate("window.__xss === undefined")
    assert evil in page.inner_text("#cz-tt-chips") and evil in page.inner_text("#cz-tt-lang-chips")


# --------------------------------------------------------------------------------------------- the second review: keyboard, races, focus

def hits_of(kind, n, prefix=None):
    return [{"id": str(1000 + i), "name": f"{prefix or kind} {i}", "path": ""} for i in range(n)]


def test_tabbing_out_of_the_language_box_closes_its_list_and_escape_then_closes_the_dock_as_usual(dock):
    """The list stayed open after Tab, so Escape (pressed with the focus elsewhere in the dock) closed the whole dock instead of the list."""
    dock.click("#cz-tt-lang-q")
    dock.wait_for_selector("#cz-tt-lang-results .aud-ta-opt")
    dock.keyboard.press("Tab")
    assert dock.is_hidden("#cz-tt-lang-results") and dock.get_attribute("#cz-tt-lang-q", "aria-expanded") == "false"
    dock.keyboard.press("Escape")
    assert dock.is_hidden("#constanza-dock")


def test_tabbing_out_of_the_interest_box_closes_its_list_too(dock):
    dock.fill("#cz-tt-q", "skin")
    dock.wait_for_selector("#cz-tt-results .aud-ta-opt")
    dock.keyboard.press("Tab")
    assert dock.is_hidden("#cz-tt-results") and dock.get_attribute("#cz-tt-q", "aria-expanded") == "false"
    assert dock.get_attribute("#cz-tt-q", "aria-activedescendant") is None


def test_tabbing_into_the_language_box_does_not_open_its_list_but_arrow_down_does(dock):
    dock.focus("#cz-tt-lang-q")
    dock.wait_for_timeout(150)
    assert dock.is_hidden("#cz-tt-lang-results")
    dock.keyboard.press("ArrowDown")
    dock.wait_for_selector("#cz-tt-lang-results .aud-ta-opt")
    assert dock.get_attribute("#cz-tt-lang-q", "aria-expanded") == "true"


def test_a_mouse_click_on_an_option_still_lands_though_the_list_closes_when_the_focus_leaves(dock):
    """The list closes on focusout; choosing an option with the mouse must not move the focus out from under it first."""
    dock.fill("#cz-tt-q", "skin")
    dock.click("#cz-tt-results .aud-ta-opt")
    assert picks(dock) == ["Skincare"]
    dock.click("#cz-tt-lang-q")
    dock.click("#cz-tt-lang-results .aud-ta-opt >> nth=1")
    assert dock.inner_text("#cz-tt-lang-chips").strip().startswith("Spanish")


def visible_in(page, item_selector, box_selector):
    return page.evaluate("""([i, b]) => { const r = document.querySelector(i).getBoundingClientRect(), c = document.querySelector(b).getBoundingClientRect();
        return r.top >= c.top - 1 && r.bottom <= c.bottom + 1; }""", [item_selector, box_selector])


def test_the_highlighted_option_always_scrolls_into_view_in_the_category_list(dock):
    kinds = ("general_interest", "additional_interest", "purchase_intention", "video_interaction", "creator_interaction", "hashtag_interaction")
    dock.world.search_override = {k: hits_of(k, 8) for k in kinds}                                # type: ignore[attr-defined]  six of each kind: 36 rows
    dock.fill("#cz-tt-q", "many")
    dock.wait_for_selector("#cz-tt-results .aud-ta-opt")
    for _ in range(14):
        dock.keyboard.press("ArrowDown")
    assert visible_in(dock, "#cz-tt-results .is-active", "#cz-tt-results")
    assert dock.evaluate("document.getElementById('cz-tt-results').scrollTop") > 0


def test_the_highlighted_option_always_scrolls_into_view_in_the_language_list(open_dock):
    page = open_dock(catalog_override={"languages": [{"code": f"l{i:02d}", "name": f"Language {i:02d}"} for i in range(30)]})
    page.click("#cz-tt-lang-q")
    page.wait_for_selector("#cz-tt-lang-results .aud-ta-opt")
    for _ in range(10):
        page.keyboard.press("ArrowDown")
    assert visible_in(page, "#cz-tt-lang-results .is-active", "#cz-tt-lang-results")


def test_a_category_search_answer_for_older_text_is_never_picked(dock):
    """Results for "skin" were on screen while "soccer" was being searched for, and Enter added Skincare."""
    world = dock.world                                                                              # type: ignore[attr-defined]
    dock.fill("#cz-tt-q", "skin")
    dock.wait_for_selector("#cz-tt-results .aud-ta-opt")
    world.hold.add("search")
    dock.fill("#cz-tt-q", "soccer")                                                                 # the answer for this text is parked
    dock.keyboard.press("Enter")
    assert picks(dock) == []
    dock.click("#cz-tt-results .aud-ta-opt", force=True)                                            # a click on the old list does not pick either
    assert picks(dock) == []
    assert dock.evaluate("document.getElementById('cz-tt-results').classList.contains('is-pending')")
    world.hold.discard("search")
    world.release("search")
    dock.wait_for_function("document.querySelector('#cz-tt-results .aud-ta-opt') && !document.getElementById('cz-tt-results').classList.contains('is-pending')")
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


def test_a_slow_first_language_list_does_not_open_after_reset(open_dock):
    page = open_dock(hold={"targeting"})                                                            # the list the panel asks for when it is shown is still on its way
    page.click("#cz-tt-lang-q")
    page.click("#cz-reset")
    page.world.hold.discard("targeting")                                                            # type: ignore[attr-defined]
    page.world.release("targeting")                                                                 # type: ignore[attr-defined]
    page.wait_for_timeout(200)
    assert page.is_hidden("#cz-tt-lang-results") and page.get_attribute("#cz-tt-lang-q", "aria-expanded") == "false"
    page.wait_for_selector('#cz-tt-lang-quick [data-v="es"]:not([disabled])')                      # ... and the quick picks light up when it arrives


def test_an_empty_language_list_is_not_remembered_for_the_life_of_the_page(open_dock):
    page = open_dock(catalog_override={"languages": []})
    page.wait_for_selector("#cz-tt-lang-quick-note:not([hidden])")
    assert "came back empty" in page.inner_text("#cz-tt-lang-quick-note") and page.is_disabled('#cz-tt-lang-quick [data-v="es"]')
    page.click("#cz-tt-lang-q")
    page.wait_for_selector("#cz-tt-lang-results .aud-ta-note")
    assert "came back empty" in page.inner_text("#cz-tt-lang-results")
    page.world.catalog_override.clear()                                                             # type: ignore[attr-defined]  TikTok recovers
    page.keyboard.press("ArrowDown")
    page.wait_for_selector("#cz-tt-lang-results .aud-ta-opt")
    page.wait_for_selector('#cz-tt-lang-quick [data-v="es"]:not([disabled])')


def test_no_option_is_announced_after_the_list_was_replaced_by_an_error(dock):
    dock.fill("#cz-tt-q", "skin")
    dock.wait_for_selector("#cz-tt-results .aud-ta-opt")
    assert dock.get_attribute("#cz-tt-q", "aria-activedescendant")
    dock.world.fail["search"] = (503, {"detail": "TikTok is down"})                                  # type: ignore[attr-defined]
    dock.fill("#cz-tt-q", "skincare")
    dock.wait_for_selector("#cz-tt-results .aud-ta-note")
    assert dock.get_attribute("#cz-tt-q", "aria-activedescendant") is None


def test_a_search_that_found_more_than_it_shows_says_how_many_more(dock):
    dock.world.search_override = {"general_interest": hits_of("Interest", 25), "additional_interest": hits_of("Keyword", 8)}        # type: ignore[attr-defined]
    dock.fill("#cz-tt-q", "many")
    dock.wait_for_selector("#cz-tt-results .tt-more")
    text = dock.inner_text("#cz-tt-results .tt-more")
    assert "19 more interests" in text and "2 more interest keywords" in text and "browse all categories" in text
    assert dock.locator("#cz-tt-results .aud-ta-opt").count() == 12                                  # six of each kind; the note is not an option


def test_removing_a_chip_from_the_keyboard_moves_the_focus_to_the_next_chip_then_the_box(dock):
    browse_pick(dock, "general_interest", "12100", "12200")
    dock.focus('#cz-tt-chips .aud-chip:nth-child(1) .aud-chip-x')
    dock.keyboard.press("Enter")
    assert chips(dock) == ["Makeup"]
    assert dock.evaluate("document.activeElement === document.querySelector('#cz-tt-chips .aud-chip-x')")
    dock.keyboard.press("Enter")
    assert chips(dock) == [] and dock.evaluate("document.activeElement.id") == "cz-tt-q"


def test_removing_a_language_chip_from_the_keyboard_keeps_the_focus_in_the_field(dock):
    dock.click("#cz-tt-lang-q")
    dock.click("#cz-tt-lang-results .aud-ta-opt >> nth=0")
    dock.focus("#cz-tt-lang-chips .aud-chip-x")
    dock.keyboard.press("Enter")
    assert dock.evaluate("document.activeElement.id") == "cz-tt-lang-q"


def test_using_the_traffic_objective_from_its_warning_keeps_the_focus_in_the_panel(dock):
    browse_pick(dock, "purchase_intention", "26100")
    dock.click("#cz-tt-more > summary")                                                             # the objective is on screen: the focus goes to it
    dock.focus('#cz-tt-pick-note [data-tt="fix-objective"]')
    dock.keyboard.press("Enter")
    assert dock.evaluate("document.activeElement === document.querySelector('#cz-tt-obj [data-v=traffic]')")
    assert "Traffic" in dock.inner_text("#cz-status")


def test_using_the_traffic_objective_while_that_part_of_the_panel_is_folded_keeps_the_focus_in_the_search_box(dock):
    browse_pick(dock, "purchase_intention", "26100")
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
    dock.wait_for_selector('#cz-tt-status[data-state="off"]')
    asked_before = world.hits.count("status")
    dock.click("#cz-product-btn"); dock.fill("#cz-picker-search", "roku"); dock.click("#cz-picker-list .cz-option")
    dock.click("#cz-product-btn"); dock.fill("#cz-picker-search", "tiktok"); dock.click("#cz-picker-list .cz-option")
    dock.wait_for_function(f"true")
    assert world.hits.count("status") > asked_before                                                # a "not connected" answer is asked again, not kept for 30 s
    world.configured = True                                                                         # the admin adds the secrets
    dock.fill("#cz-geo", "Los Angeles")
    dock.click("#cz-go")
    dock.wait_for_selector("#cz-result .cz-headline")
    dock.wait_for_selector('#cz-tt-status[data-state="on"]')                                        # the banner follows the pull that worked


def test_the_same_choices_in_another_order_do_not_raise_the_form_changed_banner(dock):
    dock.fill("#cz-geo", "Los Angeles")
    dock.click("#cz-tt-lang-q")
    dock.click("#cz-tt-lang-results .aud-ta-opt >> nth=0")                                           # English
    dock.click("#cz-tt-lang-q")
    dock.click("#cz-tt-lang-results .aud-ta-opt >> nth=0")                                           # then Spanish
    browse_pick(dock, "general_interest", "12100", "12200")
    dock.click("#cz-go")
    dock.wait_for_selector("#cz-result .cz-headline")
    assert dock.locator("#cz-result .cz-stale").count() == 0
    open_browse(dock)                                                                               # untick and tick again: the picks are now in the other order
    tick(dock, "12100")
    apply_browse(dock)
    browse_pick(dock, "general_interest", "12100")
    assert picks(dock) == ["Makeup", "Skincare"]
    dock.click('#cz-tt-lang-chips .aud-chip-x >> nth=0')                                             # and the languages too
    dock.click("#cz-tt-lang-q")
    dock.click("#cz-tt-lang-results .aud-ta-opt >> nth=0")
    assert dock.locator("#cz-result .cz-stale").count() == 0


def test_changing_any_section_after_a_pull_raises_the_form_changed_banner(dock):
    dock.fill("#cz-geo", "Los Angeles")
    dock.click("#cz-go")
    dock.wait_for_selector("#cz-result .cz-headline")
    for change, back in (('#cz-tt-lang-quick [data-v="es"]', '#cz-tt-lang-quick [data-v="es"]'), ('#cz-tt-age-presets button >> nth=0', '#cz-tt-age-presets button >> nth=0'),
                         ('#cz-tt-gender [data-v="male"]', '#cz-tt-gender [data-v=""]')):
        dock.click(change)
        assert dock.locator("#cz-result .cz-stale").count() == 1, change
        dock.click(back)                                                                            # put it back: nothing looks changed
        assert dock.locator("#cz-result .cz-stale").count() == 0, change
    browse_pick(dock, "general_interest", "12100")
    assert dock.locator("#cz-result .cz-stale").count() == 1


def test_a_long_unbreakable_name_stays_inside_its_chip_and_its_remove_button_is_a_real_target(dock):
    dock.set_viewport_size({"width": 390, "height": 800})
    long = "#" + "x" * 70
    dock.world.search_override = {"hashtag_interaction": [{"id": "7", "name": long, "path": ""}]}      # type: ignore[attr-defined]
    dock.fill("#cz-tt-q", "long")
    dock.click("#cz-tt-results .aud-ta-opt")
    box = dock.eval_on_selector("#cz-tt-chips .aud-chip", "e => { const r = e.getBoundingClientRect(); return { left: r.left, right: r.right }; }")
    x = dock.eval_on_selector("#cz-tt-chips .aud-chip-x", "e => { const r = e.getBoundingClientRect(); return { w: r.width, h: r.height, right: r.right }; }")
    assert box["right"] <= 390 and x["right"] <= 390
    assert x["w"] >= 36 and x["h"] >= 36


def test_on_a_phone_the_sections_stack_inside_the_screen_with_no_sideways_scroll(open_dock):
    page = open_dock()
    page.set_viewport_size({"width": 390, "height": 800})
    page.click("#cz-tt-more > summary")
    page.click('#cz-tt-lang-quick [data-v="es+en"]')
    boxes = page.evaluate("""() => Array.from(document.querySelectorAll('#cz-tt .aud-sec')).map((s) => { const r = s.getBoundingClientRect(); return { left: r.left, right: r.right, top: r.top }; })""")
    assert all(r["right"] <= 390 and r["left"] >= 0 for r in boxes)
    assert all(a["top"] < b["top"] for a, b in zip(boxes, boxes[1:]))                               # one column
    assert page.evaluate("document.documentElement.scrollWidth <= 390")
    assert page.evaluate("document.getElementById('cz-body').scrollWidth <= document.getElementById('cz-body').clientWidth")


def test_every_control_in_the_panel_is_a_real_touch_target_and_shows_a_focus_ring(open_dock):
    page = open_dock()
    open_line(page, "L-HISP")
    page.wait_for_selector("#cz-tt-lang-suggest:not([hidden])")
    page.click("#cz-tt-more > summary")
    page.click('#cz-tt-age [data-v="AGE_25_34"]')
    small = page.evaluate("""() => Array.from(document.querySelectorAll('#cz-tt button, #cz-tt input, #cz-tt summary'))
        .filter((e) => e.offsetParent !== null).map((e) => ({ id: e.id || e.dataset.tt + ':' + (e.dataset.v || e.textContent.trim()), h: e.getBoundingClientRect().height }))
        .filter((e) => e.h < 36)""")
    assert small == []
    page.focus('#cz-tt-age-presets button >> nth=0')
    page.keyboard.press("Shift+Tab")
    page.keyboard.press("Tab")                                                                      # a keyboard arrival: the ring must show
    outline = page.evaluate("(() => { const s = getComputedStyle(document.activeElement); return [s.outlineStyle, parseFloat(s.outlineWidth)]; })()")
    assert outline[0] != "none" and outline[1] >= 2


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


def test_a_pressed_pill_stays_readable_under_the_mouse(dock):
    """The kit's hover for a wrapped pill painted it white while its text stayed white."""
    dock.click('#cz-tt-age [data-v="AGE_25_34"]')
    dock.hover('#cz-tt-age [data-v="AGE_25_34"]')
    bg, fg = dock.evaluate("""() => { const s = getComputedStyle(document.querySelector('#cz-tt-age [data-v=AGE_25_34]'));
        const rgb = (c) => c.match(/\\d+/g).slice(0, 3).map(Number); return [rgb(s.backgroundColor), rgb(s.color)]; }""")
    assert _contrast(bg, fg) >= 4.5


def test_a_pressed_button_still_looks_pressed_in_forced_colors(dock):
    dock.emulate_media(forced_colors="active")
    dock.click('#cz-tt-gender [data-v="female"]')
    colors = dock.evaluate("""() => ['female', 'male'].map((v) => { const b = document.querySelector('#cz-tt-gender [data-v=' + v + ']');
        const s = getComputedStyle(b); return s.backgroundColor + '|' + s.color; })""")
    assert colors[0] != colors[1]


def test_the_popups_close_on_focus_alone_so_no_page_wide_listener_is_added_per_registration(dock):
    src = dock.evaluate("fetch('/static/constanza-tiktok.js').then(r => r.text())")
    assert 'document.addEventListener("mousedown"' not in src


# --------------------------------------------------------------------------------------------- the review's fixes: names, lists in view, the pop-up's buttons, the folded card

def test_each_clear_button_names_what_it_clears(dock):
    dock.click('#cz-tt-gender [data-v="female"]')
    dock.click('#cz-tt-lang-quick [data-v="es"]')
    dock.fill("#cz-tt-q", "skin")
    dock.click("#cz-tt-results .aud-ta-opt")
    dock.click("#cz-tt-more > summary")
    dock.click('#cz-tt-os [data-v="IOS"]')
    names = {k: dock.get_attribute(f"#cz-tt-clear-{k}", "aria-label") for k in ("who", "lang", "aud", "more")}
    assert names == {"who": "Clear age and gender", "lang": "Clear languages", "aud": "Clear interests and behaviors", "more": "Clear more targeting"}
    for name in names.values():                                                                      # the names a screen reader lists: each one, once, and a real button
        assert dock.get_by_role("button", name=name, exact=True).count() == 1, name
    assert {dock.inner_text(f"#cz-tt-clear-{k}").strip() for k in names} == {"Clear"}                # what is SHOWN stays "Clear" (and the name starts with it)


def test_no_control_sits_inside_a_summary_and_the_folded_cards_clear_link_is_beside_it(dock):
    nested = dock.evaluate("""() => document.querySelectorAll('#cz-tt summary button, #cz-tt summary a, #cz-tt summary input, #cz-tt summary select, #cz-tt summary [tabindex], #cz-tt summary [role=button]').length""")
    assert nested == 0
    assert dock.evaluate("""() => { const c = document.getElementById('cz-tt-clear-more'); return c.parentElement.classList.contains('cz-fold') && c.nextElementSibling === document.getElementById('cz-tt-more'); }""")


def test_the_folded_cards_clear_link_lies_on_the_room_its_head_keeps_for_it_at_every_width(open_dock):
    page = open_dock()
    page.click("#cz-tt-more > summary")
    for viewport in ({"width": 1280, "height": 900}, {"width": 390, "height": 800}):
        page.set_viewport_size(viewport)
        page.click('#cz-tt-spend [data-v="HIGH"]')
        rect = "(sel) => { const r = document.querySelector(sel).getBoundingClientRect(); return [r.left, r.top, r.right, r.bottom]; }"
        link, twin = page.evaluate(rect, "#cz-tt-clear-more"), page.evaluate(rect, "#cz-tt-more .cz-fold-twin")
        assert all(abs(a - b) <= 1 for a, b in zip(link, twin)), (viewport, link, twin)               # the link is exactly where the head left room for it
        arrow = page.evaluate("(() => { const s = document.querySelector('#cz-tt-more > summary').getBoundingClientRect(); return [s.top, s.bottom]; })()")
        assert arrow[0] <= link[1] and link[3] <= arrow[1]                                            # inside the head's height
        page.click("#cz-tt-clear-more")
        assert page.evaluate("getComputedStyle(document.querySelector('#cz-tt-more .cz-fold-twin')).display") == "none"       # no link, no room kept for it


def test_the_folded_cards_clear_link_comes_before_its_toggle_for_the_keyboard_and_pressing_it_never_folds_the_card(dock):
    dock.click("#cz-tt-more > summary")
    dock.click('#cz-tt-spend [data-v="HIGH"]')
    dock.focus("#cz-tt-clear-more")
    dock.keyboard.press("Tab")
    assert dock.evaluate("document.activeElement === document.querySelector('#cz-tt-more > summary')")      # Clear, then the toggle, then the body
    dock.keyboard.press("Shift+Tab")
    assert dock.evaluate("document.activeElement.id") == "cz-tt-clear-more"
    dock.keyboard.press("Enter")
    assert dock.evaluate("document.getElementById('cz-tt-more').open") is True and asked(dock)["spending_power"] is None
    assert dock.evaluate("document.activeElement === document.querySelector('#cz-tt-more > summary')")      # its head keeps the focus


def test_the_more_targeting_row_reacts_to_the_mouse_like_the_docks_own_folding_cards(dock):
    before = dock.evaluate("getComputedStyle(document.querySelector('#cz-tt-more > summary')).backgroundColor")
    dock.hover("#cz-tt-more > summary", position={"x": 60, "y": 28})
    after = dock.evaluate("getComputedStyle(document.querySelector('#cz-tt-more > summary')).backgroundColor")
    assert before != after and after != "rgba(0, 0, 0, 0)"


def reach_for_the_bottom_edge(page, field, gap=20):
    """Scrolls the dock's body until only `gap` px of it are left under `field`: where a field first shows up when the planner scrolls down."""
    page.evaluate("""([sel, gap]) => {
        document.getElementById('cz-form').style.paddingBottom = '600px';          // room to scroll the field that far up, wherever the panel ends
        const body = document.getElementById('cz-body'), b = body.getBoundingClientRect(), r = document.querySelector(sel).getBoundingClientRect();
        body.scrollTop += r.bottom - (b.bottom - gap);
    }""", [field, gap])


def px_out_of_view(page, selector):
    """How many px of the element lie outside the dock's scrolling body, i.e. under the action bar, below the fold or above the top edge."""
    return page.evaluate("""(sel) => { const b = document.getElementById('cz-body').getBoundingClientRect(), r = document.querySelector(sel).getBoundingClientRect();
        return r.height - Math.max(0, Math.min(r.bottom, b.bottom) - Math.max(r.top, b.top)); }""", selector)


VIEWPORTS = [pytest.param({"width": 1280, "height": 900}, id="laptop"), pytest.param({"width": 390, "height": 700}, id="phone")]


@pytest.mark.parametrize("viewport", VIEWPORTS)
@pytest.mark.parametrize("state", ["answer", "nothing", "error"])
def test_the_category_list_opens_fully_in_view_even_when_the_field_is_at_the_bottom_edge(open_dock, viewport, state):
    page = open_dock()
    page.set_viewport_size(viewport)
    world = page.world                                                                              # type: ignore[attr-defined]
    if state == "nothing":
        world.search_override = {}
    if state == "error":
        world.fail["search"] = (500, {"detail": "TikTok is not answering."})
    reach_for_the_bottom_edge(page, "#cz-tt-q")
    page.fill("#cz-tt-q", "beau")
    page.wait_for_selector("#cz-tt-results:not([hidden]) .aud-ta-opt, #cz-tt-results:not([hidden]) .aud-ta-note")
    assert px_out_of_view(page, "#cz-tt-results") < 1, state                                         # the whole list, down to its last row
    assert px_out_of_view(page, "#cz-tt-q") < 1                                                     # and the box it belongs to stays on screen
    assert {"answer": "Beauty", "nothing": "Nothing in TikTok", "error": "not answering"}[state] in page.inner_text("#cz-tt-results")


@pytest.mark.parametrize("viewport", VIEWPORTS)
@pytest.mark.parametrize("state", ["answer", "nothing", "error"])
def test_the_language_list_opens_fully_in_view_even_when_the_field_is_at_the_bottom_edge(open_dock, viewport, state):
    page = open_dock(fail={"targeting": (500, {"detail": "TikTok is not answering."})}) if state == "error" else open_dock()
    page.set_viewport_size(viewport)
    reach_for_the_bottom_edge(page, "#cz-tt-lang-q")
    page.fill("#cz-tt-lang-q", "zzzz" if state == "nothing" else "port")
    page.wait_for_selector("#cz-tt-lang-results:not([hidden]) .aud-ta-opt, #cz-tt-lang-results:not([hidden]) .aud-ta-note")
    assert px_out_of_view(page, "#cz-tt-lang-results") < 1, state
    assert px_out_of_view(page, "#cz-tt-lang-q") < 1
    assert {"answer": "Portuguese", "nothing": "No language matches", "error": "not answering"}[state] in page.inner_text("#cz-tt-lang-results")


def test_the_two_browse_buttons_are_hidden_when_the_pop_ups_script_did_not_load_and_nothing_points_at_them(open_dock):
    page = open_dock(block_segment_browser=True)
    assert page.evaluate("typeof window.SegmentBrowser") == "undefined"
    assert page.is_hidden("#cz-tt-browse") and page.is_hidden("#cz-tt-lang-browse") and page.is_hidden("#cz-browse")           # the dock's own was already hidden
    assert page.evaluate("getComputedStyle(document.getElementById('cz-tt-lang-browse').parentElement).display") == "none"      # (and the language link's row goes with it: no gap)
    page.world.search_override = {}                                                                 # type: ignore[attr-defined]
    page.fill("#cz-tt-q", "zzzz")
    page.wait_for_selector("#cz-tt-results .aud-ta-note")
    assert "Try a simpler word." in page.inner_text("#cz-tt-results") and "browse" not in page.inner_text("#cz-tt-results").lower()
    page.fill("#cz-tt-lang-q", "port")                                                              # the searches still work
    page.click("#cz-tt-lang-results .aud-ta-opt")
    assert lang_chips(page) == ["Portuguese"]


def test_with_the_pop_up_present_the_buttons_show_and_the_hint_points_at_the_category_one(dock):
    assert dock.is_visible("#cz-tt-browse") and dock.is_visible("#cz-tt-lang-browse")
    dock.world.search_override = {}                                                                 # type: ignore[attr-defined]
    dock.fill("#cz-tt-q", "zzzz")
    dock.wait_for_selector("#cz-tt-results .aud-ta-note")
    assert "Try a simpler word, or browse all categories." in dock.inner_text("#cz-tt-results")


def test_a_browse_button_that_is_pressed_after_the_pop_up_went_missing_says_so_and_steps_aside(dock):
    dock.evaluate("window.SegmentBrowser = undefined")
    dock.evaluate("document.getElementById('cz-tt-lang-browse').click()")
    assert "isn’t available" in dock.inner_text("#cz-status") and dock.is_hidden("#cz-tt-lang-browse") and dock.is_hidden("#cz-tt-browse")
    dock.evaluate("document.getElementById('cz-tt-browse').click()")
    assert "The full list isn’t available right now" in dock.inner_text("#cz-tt-pick-note")


def test_the_pop_ups_button_says_add_for_additions_remove_for_removals_and_apply_changes_for_a_mix(dock):
    dock.click("#cz-tt-lang-browse")
    dock.wait_for_selector(".segb-row")
    dock.click('.segb-row[data-id="pt"]')
    assert dock.inner_text(".segb-apply").strip() == "Add languages (1)"                              # only additions: the caller's own label
    apply_browse(dock)
    dock.click('#cz-tt-lang-quick [data-v="es+en"]')                                                 # Portuguese, Spanish, English
    dock.click("#cz-tt-lang-browse")
    dock.wait_for_selector(".segb-row")
    dock.click('.segb-row[data-id="es"]')
    dock.click('.segb-row[data-id="en"]')
    assert dock.inner_text(".segb-apply").strip() == "Remove 2"                                       # only removals: it says so, not "Add languages (2)"
    dock.click('.segb-row[data-id="fr"]')
    assert dock.inner_text(".segb-apply").strip() == "Apply changes (3)"                              # a mix
    apply_browse(dock)
    assert lang_chips(dock) == ["Portuguese", "French"]
    open_browse(dock)                                                                                 # the categories pop-up reads the same way
    tick(dock, "12100", "12200")
    assert dock.inner_text(".segb-apply").strip() == "Add to audience (2)"
    apply_browse(dock)
    open_browse(dock)
    tick(dock, "12100", "14100")                                                                      # untick Skincare, tick Soccer
    assert dock.inner_text(".segb-apply").strip() == "Apply changes (2)"
