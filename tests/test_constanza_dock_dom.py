"""The Constanza dock's own form (the Entravision side) in a REAL browser (headless Chromium through Playwright): the numbered Where / When /
Who / Language & heritage / Audience cards, their live summaries, the age presets, the Hispanic switch, the interests typeahead and the
"Browse all audiences" window - the behaviors a stub page cannot show (which control keeps the focus, what Escape closes, whether the
chips on the screen are what the estimate request will send, whether the layout fits a narrow dock, a wide one and a phone).

The page is a small harness (not the whole app): the real constanza.js, the shared audience kit and SegmentBrowser, and the real CSS, against
a stub `state.catalog` and /api/constanza/* routes answered in the browser with the shapes the server returns (the interest routes use the
real taxonomy service). Skipped when Playwright or its Chromium is not installed. TEST_SHOTS=<dir> also writes screenshots of the layouts."""
from __future__ import annotations

import http.server
import json
import os
import threading
from pathlib import Path

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")

from app.services import audience_taxonomy  # noqa: E402

STATIC = Path(__file__).resolve().parents[1] / "app" / "static"
SHOTS = os.environ.get("TEST_SHOTS")

HARNESS = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>harness</title>
<link rel="stylesheet" href="/static/styles.css"><link rel="stylesheet" href="/static/constanza.css">
<link rel="stylesheet" href="/static/audience-kit.css"><link rel="stylesheet" href="/static/segment-browser.css"></head><body>
<main style="padding:24px"><h1>The proposal page behind the dock</h1><button id="opener" type="button">Other control</button></main>
<script>
  function escapeHtml(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[c])); }
  function _replaceGeoItem(t, i, r) { return r; }
  function _constanzaErrorText(d, f) { return (d && typeof d.detail === "string" && d.detail) || f || "Request failed."; }
  const platform = (key, over) => Object.assign({ key, channel: "platform_model", data_basis: "modeled", unit: "monthly", targeting: "general", relation: "primary", title: key }, over || {});
  const prod = (name, fc) => ({ name, family: "Entravision Plus", is_addon: false, forecast: Object.assign({ enabled: true, method: "platform" }, fc) });
  var state = { lineItems: [], catalog: { families: ["Entravision Plus", "Social"], products_by_family: {
    "Entravision Plus": [
      prod("Entravision+ Connect - (Default)", { hispanic_default: true, series: platform("connect-default", { channel: "ctv", data_basis: "platform_reported", title: "Connect (default)" }) }),
      prod("Roku", { series: platform("roku") }),
      prod("Hispanic Connect", { hispanic_default: true, series: platform("hispanic-connect-default", { channel: "ctv", data_basis: "platform_reported", targeting: "hispanic" }) }),
      prod("Live Sports - MLB", { series: platform("live-mlb", { channel: "live_sports", unit: "weekly", period_label: "Regular season, ends Sep 27, 2026" }) }),
      prod("Display - Generic", { method: "generic", series: null })],
    "Social": [{ name: "Tiktok Ads", family: "Social", is_addon: false, forecast: { enabled: true, method: "tiktok", series: null } }] } } };
</script>
<script src="/static/segment-browser.js"></script>
<script src="/static/constanza.js"></script>
</body></html>"""

AREAS = {
    "los angeles": [("Los Angeles", "Los Angeles", 12_900_000, "dma")],
    "phoenix": [("Phoenix", "Phoenix", 5_100_000, "dma")],
    "texas": [("Texas · Dallas-Ft. Worth", "Dallas-Ft. Worth", 8_100_000, "state"), ("Texas · Houston", "Houston", 7_300_000, "state"),
              ("Texas · San Antonio", "San Antonio", 2_700_000, "state")],
    "adams county, wa": [("Adams County, WA", "Spokane", 20_000, "county")],
    "90210": [("90210 (Beverly Hills, CA)", "Los Angeles", 21_000, "zip")],
}


def resolution(text: str, hint: str | None = None) -> dict:
    """What /api/constanza/resolve answers for a few places (the shape of app.main._serialize_geo_resolution)."""
    key = text.strip().lower()
    base = {"method": "exact", "geo_confidence": "high", "resolved": False, "unmatched_tokens": [], "unmatched_details": [], "notes": [],
            "ambiguous": [], "interpretations": [], "suggestions": [], "areas": [], "dmas": []}
    if key in AREAS:
        areas = [{"dma": d, "area_population": pop, "dma_share": 1.0, "source_type": hint or kind, "apportionment": 1.0, "state": "", "label": label, "geo_id": "1"}
                 for label, d, pop, kind in AREAS[key]]
        return {**base, "resolved": True, "areas": areas, "geo_confidence": "medium" if key == "texas" else "high"}
    if key == "springfield":
        return {**base, "ambiguous": [{"input": "Springfield", "candidates": [
            {"label": "Springfield, MO", "population": 169_000, "resolve_hint": "Springfield, MO"},
            {"label": "Springfield, IL", "population": 114_000, "resolve_hint": "Springfield, IL"}]}]}
    return {**base, "unmatched_tokens": [text.strip()], "unmatched_details": [{"input": text.strip(), "reason": f"Couldn't match “{text.strip()}” to a place."}],
            "suggestions": [{"kind": "did_you_mean", "label": "Phoenix", "geo_type": "dma", "population": 5_100_000, "resolve_hint": "Phoenix", "replaces": text.strip()}]}


def estimate_payload(body: dict) -> dict:
    """A plausible /api/constanza/estimate answer (the numbers are made up; the shape is the server's)."""
    aud = body.get("audience")
    months = body.get("months") or 1
    out = {"product_name": body["product_name"], "platform": "x", "confidence": "modeled", "geo_method": "exact_dma", "geo_confidence": "high", "months": months,
           "imps_low": 800_000 * months, "imps_mid": 1_000_000 * months, "imps_high": 1_300_000 * months, "est_uniques": 210_000, "reach_universe": 480_000,
           "series": {"key": "connect-default", "title": "Connect (default)", "version": "2026.10", "unit": "monthly", "data_basis": "platform_reported", "relation": "primary"},
           "weeks": None, "win_rate": None, "notes": [], "unmatched_geo_tokens": [], "unmatched_geo_details": [], "ambiguous": [], "geo_interpretations": [],
           "geo_suggestions": [], "by_area": [{"area_label": "Los Angeles", "dma": "Los Angeles", "weight": 1.0, "imps_low": 800_000 * months, "imps_mid": 1_000_000 * months,
                                               "imps_high": 1_300_000 * months, "unique_viewers": None, "unique_hh": None, "reach_universe": None}]}
    if aud:
        out["audience"] = {"imps_low": 90_000 * months, "imps_mid": 120_000 * months, "imps_high": 160_000 * months, "audience_people": 60_000, "est_uniques": 24_000,
                           "underlying_population": 2_000_000, "reliability_floor_population": 10_000, "below_reliability_threshold": False, "market_population_adults": True,
                           "confidence_tier": "audience_modeled_single_cut", "interests": [], "notes": [], "by_area": out["by_area"], "hispanic": None}
    return out


class _Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=str(STATIC.parent), **k)

    def do_GET(self):
        if self.path.startswith("/harness.html"):
            page = HARNESS.replace('<script src="/static/segment-browser.js"></script>', "") if "nosb=1" in self.path else HARNESS
            raw = page.encode()
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
    request_queue_size = 128                                             # the page fetches six files at once; the default queue of 5 can refuse one under load


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


class World:
    """What the harness's /api answers. A test can park an answer (`hold`) and release it later, or make a route fail (`fail`)."""

    NAMES = {"api/constanza/resolve": "resolve", "api/constanza/estimate": "estimate", "api/constanza/interests": "interests",
             "api/constanza/interest-taxonomy": "taxonomy"}

    def __init__(self):
        self.taxonomy = audience_taxonomy.load_taxonomy()
        self.taxonomy_override: dict | None = None         # replaces the taxonomy the window and the search read (a segment called "<img onerror>")
        self.fail: dict = {}                               # route name -> (status, body)
        self.hold: set = set()
        self.pending: list = []
        self.hits: list[str] = []
        self.requests: list[dict] = []                     # the estimate requests
        self.resolves: list[dict] = []

    def release(self, name) -> None:
        for item in [p for p in self.pending if p[0] == name]:
            self.pending.remove(item)
            item[1].fulfill(status=item[2], content_type="application/json", body=json.dumps(item[3]))

    def answer(self, route) -> None:
        req = route.request
        path = req.url.split("//", 1)[1].split("/", 1)[1].split("?")[0]
        name = self.NAMES.get(path, "other")
        self.hits.append(name)
        status, body = 200, None
        if name in self.fail:
            status, body = self.fail[name]
        elif name == "resolve":
            sent = json.loads(req.post_data or "{}")
            self.resolves.append(sent)
            body = resolution(sent.get("geo_text", ""), sent.get("geo_type_hint"))
        elif name == "estimate":
            sent = json.loads(req.post_data or "{}")
            self.requests.append(sent)
            body = estimate_payload(sent)
        elif name == "interests":
            q = req.url.split("q=", 1)[1].split("&")[0]
            from urllib.parse import unquote
            if self.taxonomy_override is not None:
                words = unquote(q).lower().split()
                found = [s for g in self.taxonomy_override["groups"] for s in g["segments"] if all(w in s["label"].lower() for w in words)]
                body = {"query": unquote(q), "results": found[:8]}
            else:
                hits = self.taxonomy.search(unquote(q), limit=8)
                body = {"query": unquote(q), "results": [{**h.segment.to_public(), "score": round(h.score, 1), "how": h.how} for h in hits]}
        elif name == "taxonomy":
            body = self.taxonomy_override if self.taxonomy_override is not None else self.taxonomy.to_public()
        else:
            status, body = 404, {"detail": "not found"}
        if name in self.hold:
            self.pending.append((name, route, status, body))
            return
        route.fulfill(status=status, content_type="application/json", body=json.dumps(body))


def new_page(browser, server, world, viewport=None, open_dock=True, query=""):
    context = browser.new_context(viewport=viewport or {"width": 1280, "height": 900})
    page = context.new_page()
    page.route("**/api/**", world.answer)
    page.route("**/fonts.googleapis.com/**", lambda r: r.abort())
    page.route("**/fonts.gstatic.com/**", lambda r: r.abort())
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    for _ in range(3):                                                   # a script that did not arrive (a refused connection) is a harness hiccup: ask again
        page.goto(f"{server}/harness.html{query}")
        if page.evaluate("typeof Constanza") == "object":
            break
    if open_dock:
        page.evaluate("Constanza.open({})")
        page.wait_for_selector("#cz-pname:not(:has-text('Loading'))")
    page.world, page.errors, page.context_ = world, errors, context     # type: ignore[attr-defined]
    return page


@pytest.fixture
def dock(browser, server):
    world = World()
    page = new_page(browser, server, world)
    yield page
    page.context_.close()                                               # type: ignore[attr-defined]
    assert page.errors == [], page.errors                               # type: ignore[attr-defined]


def shot(page, name):
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(SHOTS) / f"{name}.png"))


def test_the_harness_opens_the_dock(dock):
    assert dock.is_visible("#constanza-dock") and dock.is_visible("#cz-sec-where")
    shot(dock, "dock_smoke")


# ------------------------------------------------------------------------------------------------------------------ helpers

def text(page, selector):
    return " ".join(page.inner_text(selector).split())


def last_request(page):
    return page.world.requests[-1]                                       # type: ignore[attr-defined]


def pull(page):
    before = len(page.world.requests)                                    # type: ignore[attr-defined]
    page.click("#cz-go")
    page.wait_for_function(f"document.getElementById('cz-go').getAttribute('aria-disabled') === 'false'")
    page.wait_for_selector("#cz-result .cz-headline, #cz-result .cz-error")
    assert len(page.world.requests) == before + 1                        # type: ignore[attr-defined]
    return last_request(page)


def choose_product(page, query):
    page.click("#cz-product-btn")
    page.fill("#cz-picker-search", query)
    page.click("#cz-picker-list .cz-option")


def geo(page, value, wait=True):
    page.fill("#cz-geo", value)
    if wait:
        page.wait_for_function("!document.getElementById('cz-where-sum').textContent.startsWith('Matching')")


SECTIONS = [("where", "Where"), ("when", "When"), ("who", "Who"), ("lang", "Language & heritage"), ("aud", "Audience")]


# ------------------------------------------------------------------------------------------------------------------ the five cards

def test_the_form_is_five_numbered_cards_in_order_and_each_says_in_one_line_what_it_holds(dock):
    titles = dock.eval_on_selector_all("#cz-form .aud-sec-title", "els => els.map(e => e.textContent.trim())")
    assert titles == [t for _, t in SECTIONS]
    assert dock.eval_on_selector_all("#cz-form .aud-sec .aud-step", "els => els.length") == 5
    assert dock.eval_on_selector_all("#cz-form .cz-prod .aud-step", "els => els.length") == 0              # the product is the card above the numbers
    sums = {key: text(dock, f"#cz-{key}-sum") for key, _ in SECTIONS}
    assert sums == {"where": "Type a place to start", "when": "1 month", "who": "Everyone, all ages", "lang": "Hispanic targeting on · default",
                    "aud": "No interests chosen"}
    # "has a value" is the card's own state: When always holds a choice, Where does not until a place is typed
    assert dock.get_attribute("#cz-sec-when", "class").count("has-value") == 1 and "has-value" not in dock.get_attribute("#cz-sec-where", "class")
    assert "has-value" in dock.get_attribute("#cz-sec-lang", "class")                                      # Hispanic targeting is on by default for this product
    # Language & heritage is open (most campaigns need it); Who and Audience are folded, and their summaries still say what they hold
    assert dock.eval_on_selector("#cz-sec-lang", "e => e.open") and not dock.eval_on_selector("#cz-sec-who", "e => e.open") and not dock.eval_on_selector("#cz-sec-aud", "e => e.open")
    shot(dock, "dock_cards")


def test_the_cards_are_labelled_and_the_controls_have_names(dock):
    for key in ("where", "when"):
        assert dock.get_attribute(f"#cz-sec-{key}", "aria-labelledby") == f"cz-{key}-t"
    for sel in ("#cz-geo", "#cz-flight", "#cz-age-min", "#cz-age-max", "#cz-interest-q", "#cz-total"):
        name = dock.evaluate(f"""() => {{ const e = document.querySelector({json.dumps(sel)}); return e.getAttribute('aria-label') || (e.labels && e.labels.length ? e.labels[0].textContent : '') }}""")
        assert name.strip(), sel
    for group in ("#cz-geotype", "#cz-unit", "#cz-gender", "#cz-age-presets"):
        assert dock.evaluate(f"""() => {{ const g = document.querySelector({json.dumps(group)}); return g.getAttribute('role') === 'group' && !!(g.getAttribute('aria-label') || document.getElementById(g.getAttribute('aria-labelledby'))) }}""")
    assert dock.get_attribute("#cz-hispanic", "role") == "switch"


# ------------------------------------------------------------------------------------------------------------------ Where

def test_typing_a_place_shows_what_it_matched_in_the_card_and_clear_starts_over(dock):
    geo(dock, "Los Angeles")
    assert text(dock, "#cz-where-sum") == "1 area · about 13M people"
    assert dock.get_attribute("#cz-where-sum", "class").count("is-set") == 1 and "has-value" in dock.get_attribute("#cz-sec-where", "class")
    assert text(dock, "#cz-geo-chips .aud-chip") == "✓ Los Angeles TV market · 13M"
    assert dock.is_visible("#cz-where-clear") and dock.is_hidden("#cz-geo-quick")
    dock.click("#cz-where-clear")
    assert dock.input_value("#cz-geo") == "" and dock.inner_text("#cz-where-sum").strip() == "Type a place to start"
    assert dock.is_hidden("#cz-where-clear") and dock.locator("#cz-geo-chips .aud-chip").count() == 0
    assert dock.evaluate("document.activeElement.id") == "cz-geo"                                         # the Clear link went away: the box has the focus
    assert dock.is_visible("#cz-geo-quick")                                                              # the examples are back


def test_the_summary_says_matching_from_the_first_keystroke_and_a_place_that_is_not_found_says_so(dock):
    dock.world.hold.add("resolve")                                       # type: ignore[attr-defined]
    dock.fill("#cz-geo", "Phoenix")
    assert dock.inner_text("#cz-where-sum").strip() == "Matching what you typed…"
    dock.world.hold.discard("resolve")                                   # type: ignore[attr-defined]
    dock.world.release("resolve")                                        # type: ignore[attr-defined]
    dock.wait_for_function("document.getElementById('cz-where-sum').textContent.startsWith('1 area')")
    geo(dock, "zzzz")
    assert text(dock, "#cz-where-sum") == "No match yet — see the suggestions"
    assert "Did you mean" in text(dock, "#cz-geo-help") and "has-value" not in dock.get_attribute("#cz-sec-where", "class")
    dock.click("#cz-geo-help .cz-chip.cz-sugg")                           # "Phoenix"
    assert dock.input_value("#cz-geo") == "Phoenix"
    dock.wait_for_function("document.getElementById('cz-where-sum').textContent.startsWith('1 area')")


def test_a_place_that_means_two_places_asks_which_and_a_pick_replaces_the_phrase(dock):
    geo(dock, "Springfield")
    assert text(dock, "#cz-where-sum") == "Pick the place you mean"
    assert "matches more than one place" in text(dock, "#cz-geo-help")
    dock.click('#cz-geo-help [data-act="candidate"] >> nth=0')            # Springfield, MO
    assert dock.input_value("#cz-geo") == "Springfield, MO"


def test_the_treat_as_control_goes_to_the_server_as_the_geography_type(dock):
    geo(dock, "Los Angeles")
    dock.click('#cz-geotype [data-type="county"]')
    assert dock.get_attribute('#cz-geotype [data-type="county"]', "aria-pressed") == "true" and dock.get_attribute('#cz-geotype [data-type=""]', "aria-pressed") == "false"
    dock.wait_for_function("document.getElementById('cz-where-sum').textContent.startsWith('1 area')")
    dock.wait_for_timeout(150)
    assert dock.world.resolves[-1]["geo_type_hint"] == "county"           # type: ignore[attr-defined]
    assert pull(dock)["geo_type_hint"] == "county"


def test_the_examples_and_the_proposals_geography_are_one_click_picks(dock):
    assert text(dock, "#cz-geo-quick") == "Try National Texas Phoenix ZIP 90210"
    dock.click('#cz-geo-quick [data-example="Texas"]')
    assert dock.input_value("#cz-geo") == "Texas" and dock.evaluate("document.activeElement.id") == "cz-geo"
    dock.wait_for_function("document.getElementById('cz-where-sum').textContent.startsWith('3 areas')")
    assert text(dock, "#cz-where-sum") == "3 areas · about 18M people"
    assert dock.is_hidden("#cz-geo-quick") and dock.locator("#cz-geo-chips .aud-chip").count() == 3
    assert "Spans more than one county or market." in text(dock, "#cz-geo-chips")
    # a proposal is loaded and says another place: the pill puts it back
    dock.evaluate("""() => { state.parsed = {}; state.activeTierLabel = "t"; window._effectiveGeo = () => "Phoenix; Tucson"; Constanza.open({}); }""")
    dock.fill("#cz-geo", "Texas")
    assert "Use the proposal’s geography · Phoenix; Tucson" in text(dock, "#cz-geo-quick")
    dock.click("#cz-geo-quick [data-use-proposal]")
    assert dock.input_value("#cz-geo") == "Phoenix; Tucson" and dock.is_hidden("#cz-geo-quick")


def test_a_resolve_that_fails_says_so_in_the_card_and_the_pull_is_still_possible(dock):
    dock.world.fail["resolve"] = (500, {"detail": "boom"})               # type: ignore[attr-defined]
    geo(dock, "Phoenix")
    assert text(dock, "#cz-where-sum") == "Couldn’t check this place just now"
    assert pull(dock)["geo_text"] == "Phoenix"


# ------------------------------------------------------------------------------------------------------------------ When

def test_the_flight_card_follows_the_stepper_the_unit_and_the_proposals_dates(dock):
    assert text(dock, "#cz-when-sum") == "1 month" and dock.is_hidden("#cz-dates-quick")
    dock.click("#cz-flight-inc"); dock.click("#cz-flight-inc")
    assert text(dock, "#cz-when-sum") == "3 months" and dock.input_value("#cz-flight") == "3"
    dock.fill("#cz-flight", "6")                                         # typing: the summary follows without rewriting the box
    assert text(dock, "#cz-when-sum") == "6 months" and dock.input_value("#cz-flight") == "6"
    dock.click('#cz-unit [data-unit="days"]')
    assert text(dock, "#cz-when-sum") == "180 days"
    geo(dock, "Los Angeles")
    sent = pull(dock)
    assert sent["flight_days"] == 180 and sent["months"] == 6
    # the proposal's dates: one pill, which shows as chosen once the flight IS those days
    dock.evaluate("""() => { state.parsed = {}; window._effectiveStartDate = () => "2026-11-02"; window._effectiveEndDate = () => "2026-12-20";
        window._mbParseDate = (s) => new Date(s + "T00:00:00Z"); Constanza.open({}); }""")
    assert dock.is_visible("#cz-use-dates") and "Nov 2 – Dec 20 (49 days)" in text(dock, "#cz-use-dates")
    assert dock.get_attribute("#cz-use-dates", "aria-pressed") == "false"
    dock.click("#cz-use-dates")
    assert text(dock, "#cz-when-sum") == "49 days" and dock.get_attribute("#cz-use-dates", "aria-pressed") == "true"
    dock.click("#cz-flight-dec")
    assert dock.get_attribute("#cz-use-dates", "aria-pressed") == "false"


def test_the_impressions_to_spread_field_shows_only_for_a_product_that_needs_it(dock):
    assert dock.is_hidden("#cz-total-field")
    choose_product(dock, "display")
    assert dock.is_visible("#cz-total") and "no platform avails data" in text(dock, "#cz-total-note")
    geo(dock, "Los Angeles")
    dock.fill("#cz-total", "2500000")
    assert pull(dock)["national_monthly_total"] == 2_500_000


# ------------------------------------------------------------------------------------------------------------------ Who

def open_who(page):
    page.click("#cz-sec-who > summary")
    assert page.eval_on_selector("#cz-sec-who", "e => e.open")


def test_the_age_presets_fill_the_two_boxes_and_the_boxes_still_take_any_age(dock):
    open_who(dock)
    assert dock.eval_on_selector_all("#cz-age-presets button", "els => els.map(e => e.textContent.trim())") == ["18+", "18–34", "18–49", "25–54", "35–54", "55+"]
    dock.click('#cz-age-presets [data-min="25"][data-max="54"]')
    assert dock.input_value("#cz-age-min") == "25" and dock.input_value("#cz-age-max") == "54"
    assert dock.get_attribute('#cz-age-presets [data-min="25"]', "aria-pressed") == "true"
    assert text(dock, "#cz-who-sum") == "25–54" and text(dock, "#cz-who-count") == "1" and dock.is_visible("#cz-who-clear")
    dock.click('#cz-age-presets [data-min="55"]')                         # open-ended: 55 and over
    assert dock.input_value("#cz-age-min") == "55" and dock.input_value("#cz-age-max") == "" and text(dock, "#cz-who-sum") == "55+"
    dock.click('#cz-age-presets [data-min="55"]')                         # pressing the chosen range again lets go of it
    assert dock.input_value("#cz-age-min") == "" and text(dock, "#cz-who-sum") == "Everyone, all ages" and dock.is_hidden("#cz-who-count")
    dock.fill("#cz-age-min", "21"); dock.fill("#cz-age-max", "29")        # any age: not one of the presets
    assert text(dock, "#cz-who-sum") == "21–29" and all(dock.get_attribute(f"#cz-age-presets button >> nth={i}", "aria-pressed") == "false" for i in range(6))
    geo(dock, "Los Angeles")
    assert pull(dock)["audience"] == {"age_min": 21, "age_max": 29, "gender": None, "interest_terms": []}


def test_gender_and_age_make_the_who_summary_and_one_clear_undoes_both(dock):
    open_who(dock)
    dock.click('#cz-gender [data-g="female"]')
    assert text(dock, "#cz-who-sum") == "Women" and text(dock, "#cz-who-count") == "1"
    dock.click('#cz-age-presets [data-min="18"][data-max="34"]')
    assert text(dock, "#cz-who-sum") == "Women · 18–34" and text(dock, "#cz-who-count") == "2"
    geo(dock, "Los Angeles")
    assert pull(dock)["audience"]["gender"] == "female"
    dock.click("#cz-who-clear")
    assert dock.eval_on_selector("#cz-sec-who", "e => e.open")            # the Clear link is not the fold's toggle
    assert text(dock, "#cz-who-sum") == "Everyone, all ages" and dock.get_attribute('#cz-gender [data-g=""]', "aria-pressed") == "true"
    assert dock.evaluate("document.activeElement === document.querySelector('#cz-sec-who > summary')")      # the head keeps the focus
    assert pull(dock)["audience"] is None


def test_clear_works_from_the_keyboard_without_folding_the_card(dock):
    open_who(dock)
    dock.click('#cz-gender [data-g="male"]')
    dock.focus("#cz-who-clear")
    dock.keyboard.press("Enter")
    assert text(dock, "#cz-who-sum") == "Everyone, all ages" and dock.eval_on_selector("#cz-sec-who", "e => e.open")
    dock.click('#cz-gender [data-g="male"]')
    dock.focus("#cz-who-clear")
    dock.keyboard.press("Space")
    assert text(dock, "#cz-who-sum") == "Everyone, all ages" and dock.eval_on_selector("#cz-sec-who", "e => e.open")


def test_a_backwards_age_range_is_flagged_where_it_is_typed(dock):
    open_who(dock)
    dock.fill("#cz-age-min", "40"); dock.fill("#cz-age-max", "30")
    assert "“from” age is above the “to” age" in text(dock, "#cz-age-note") and "cz-warn-text" in dock.get_attribute("#cz-age-note", "class")
    dock.fill("#cz-age-max", "45")
    assert text(dock, "#cz-age-note") == "Leave both boxes empty for all ages."


# ------------------------------------------------------------------------------------------------------------------ Language & heritage

def test_the_hispanic_switch_follows_the_products_default_and_only_a_real_choice_is_sent(dock):
    assert dock.is_checked("#cz-hispanic") and text(dock, "#cz-lang-sum") == "Hispanic targeting on · default"
    assert "on by default for this product" in text(dock, "#cz-hispanic-note")
    geo(dock, "Los Angeles")
    assert "hispanic" not in pull(dock)                                  # the product's own default: the server decides
    dock.click("#cz-hispanic")                                           # the planner turns it off: that IS a choice
    assert not dock.is_checked("#cz-hispanic") and text(dock, "#cz-lang-sum") == "Not applied" and dock.is_hidden("#cz-lang-clear")
    assert "has-value" not in dock.get_attribute("#cz-sec-lang", "class")
    assert pull(dock)["hispanic"] is False
    dock.click("#cz-hispanic")                                           # back to the default: no preference, nothing looks changed
    assert text(dock, "#cz-lang-sum") == "Hispanic targeting on · default" and "hispanic" not in pull(dock)
    dock.click("#cz-lang-clear")                                         # "Turn off" in the head does the same as the switch
    assert not dock.is_checked("#cz-hispanic") and dock.evaluate("document.activeElement === document.querySelector('#cz-sec-lang > summary')")
    assert dock.eval_on_selector("#cz-sec-lang", "e => e.open")


def test_a_product_without_a_default_starts_off_and_turning_it_on_is_the_planners_choice(dock):
    choose_product(dock, "roku")
    assert not dock.is_checked("#cz-hispanic") and text(dock, "#cz-lang-sum") == "Not applied"
    assert "Narrow the avails to the Hispanic audience" in text(dock, "#cz-hispanic-note")
    dock.click("#cz-hispanic")
    assert text(dock, "#cz-lang-sum") == "Hispanic targeting on" and "on by default" not in text(dock, "#cz-hispanic-note")
    assert dock.is_visible("#cz-lang-clear")
    geo(dock, "Los Angeles")
    assert pull(dock)["hispanic"] is True
    choose_product(dock, "connect")                                      # a choice was about the product it was made for
    assert dock.is_checked("#cz-hispanic") and text(dock, "#cz-lang-sum") == "Hispanic targeting on · default"


def test_a_product_built_from_hispanic_audiences_says_so_and_cannot_be_cut_twice(dock):
    choose_product(dock, "hispanic connect")
    assert dock.is_checked("#cz-hispanic") and dock.is_disabled("#cz-hispanic")
    assert text(dock, "#cz-lang-sum") == "Built from Hispanic audiences" and dock.is_hidden("#cz-lang-clear")
    assert "already built from Hispanic audiences" in text(dock, "#cz-hispanic-note")
    geo(dock, "Los Angeles")
    assert "hispanic" not in pull(dock)


def test_the_card_says_in_plain_words_that_the_data_has_no_language_field(dock):
    why = text(dock, "#cz-lang-why")
    assert "no language field" in why and "Hispanic share" in why and "Census ACS" in why and "Spanish-speaking" in why
    assert "Spanish-speaking audiences" in text(dock, "#cz-hispanic-desc")
    assert dock.get_attribute("#cz-hispanic", "aria-describedby").split() == ["cz-hispanic-desc", "cz-hispanic-note"]
    # no language picker is invented for Entravision's own products: the request has no language field
    geo(dock, "Los Angeles")
    assert not any("language" in key for key in pull(dock))


# ------------------------------------------------------------------------------------------------------------------ Audience: the typeahead

def open_aud(page):
    page.click("#cz-sec-aud > summary")
    assert page.eval_on_selector("#cz-sec-aud", "e => e.open")


def chips(page):
    return page.eval_on_selector_all("#cz-interest-chips .aud-chip-label", "els => els.map(e => e.textContent.trim())")


def sent_terms(page):
    geo_filled = page.input_value("#cz-geo")
    if not geo_filled:
        geo(page, "Los Angeles")
    return pull(page)["audience"]["interest_terms"]


def search(page, q):
    page.fill("#cz-interest-q", q)
    page.wait_for_selector("#cz-interest-results .aud-ta-opt, #cz-interest-results .aud-ta-note")


def test_the_interest_typeahead_lists_matches_with_their_rate_and_group_and_a_pick_becomes_a_chip(dock):
    open_aud(dock)
    dock.wait_for_function("document.getElementById('cz-browse-count').textContent === '90'")
    search(dock, "soccer")
    assert dock.get_attribute("#cz-interest-q", "aria-expanded") == "true" and dock.get_attribute("#cz-interest-results", "role") == "listbox"
    first = text(dock, "#cz-interest-results .aud-ta-opt")
    assert first.startswith("Sports Fans – Soccer") and "Sports Fans" in first and "of adults" in first
    assert dock.get_attribute("#cz-interest-results .aud-ta-opt", "aria-selected") == "true"
    assert dock.get_attribute("#cz-interest-q", "aria-activedescendant") == "cz-int-0"
    assert dock.eval_on_selector("#cz-interest-results mark", "e => e.textContent.toLowerCase()") == "soccer"
    dock.keyboard.press("Enter")
    assert chips(dock) == ["Sports Fans – Soccer"] and dock.input_value("#cz-interest-q") == "" and dock.is_hidden("#cz-interest-results")
    assert dock.get_attribute("#cz-interest-q", "aria-expanded") == "false" and dock.evaluate("document.activeElement.id") == "cz-interest-q"
    assert text(dock, "#cz-aud-sum") == "Sports Fans – Soccer" and text(dock, "#cz-aud-count") == "1" and text(dock, "#cz-interest-hint") == "1 of 10 chosen"
    assert sent_terms(dock) == ["Sports Fans – Soccer"]


def test_the_arrow_keys_wrap_around_the_options_and_enter_takes_the_active_one(dock):
    open_aud(dock)
    search(dock, "auto")
    n = dock.locator("#cz-interest-results .aud-ta-opt").count()
    assert n >= 3
    dock.keyboard.press("ArrowUp")                                       # from the first: wraps to the last
    assert dock.get_attribute("#cz-interest-q", "aria-activedescendant") == f"cz-int-{n - 1}"
    assert dock.get_attribute(f"#cz-int-{n - 1}", "aria-selected") == "true" and dock.get_attribute("#cz-int-0", "aria-selected") == "false"
    dock.keyboard.press("ArrowDown")
    assert dock.get_attribute("#cz-interest-q", "aria-activedescendant") == "cz-int-0"
    dock.keyboard.press("ArrowDown")
    wanted = text(dock, "#cz-int-1 .aud-ta-name")
    dock.keyboard.press("Enter")
    assert chips(dock) == [wanted]


def test_an_audience_with_no_public_rate_can_be_picked_for_reference_and_says_so(dock):
    open_aud(dock)
    search(dock, "toyota")                                               # a keyword of "Auto Intenders - New Import Brands", which has no sourced rate
    assert "cz-norate" in dock.get_attribute("#cz-interest-results .aud-ta-opt", "class") and "no public rate" in text(dock, "#cz-interest-results .aud-ta-opt")
    dock.click("#cz-interest-results .aud-ta-opt")
    assert "aud-chip--warn" in dock.get_attribute("#cz-interest-chips .aud-chip", "class") and "no public rate" in text(dock, "#cz-interest-chips .aud-chip")
    assert "listed for reference only" in text(dock, "#cz-interest-note")


def test_escape_closes_the_popup_alone_even_when_it_only_says_nothing_matches_and_then_the_dock(dock):
    open_aud(dock)
    search(dock, "zzzzqq")
    assert "Nothing matches" in text(dock, "#cz-interest-results") and dock.is_visible("#cz-interest-results")
    dock.keyboard.press("Escape")
    assert dock.is_hidden("#cz-interest-results") and dock.is_visible("#constanza-dock")
    assert dock.get_attribute("#cz-interest-q", "aria-expanded") == "false"
    search(dock, "soccer")
    dock.keyboard.press("Escape")
    assert dock.is_hidden("#cz-interest-results") and dock.is_visible("#constanza-dock")
    dock.keyboard.press("Escape")                                        # nothing open any more: the dock closes as before
    assert dock.is_hidden("#constanza-dock")


def test_a_failed_search_says_so_and_the_popup_still_closes_on_escape(dock):
    open_aud(dock)
    dock.world.fail["interests"] = (500, {"detail": "boom"})             # type: ignore[attr-defined]
    search(dock, "soccer")
    assert "audience search isn’t available right now" in text(dock, "#cz-interest-results")
    dock.keyboard.press("Escape")
    assert dock.is_hidden("#cz-interest-results") and dock.is_visible("#constanza-dock")


def test_a_chip_removes_itself_and_the_focus_moves_to_its_neighbour(dock):
    open_aud(dock)
    for q in ("soccer", "dentist", "renters"):
        search(dock, q)
        dock.keyboard.press("Enter")
    assert len(chips(dock)) == 3 and text(dock, "#cz-aud-sum").endswith("+1") and text(dock, "#cz-aud-count") == "3"
    names = chips(dock)
    dock.click('#cz-interest-chips [data-rm="1"]')
    assert chips(dock) == [names[0], names[2]]
    assert dock.evaluate("document.activeElement.getAttribute('data-rm')") == "1"                        # the next chip's x
    dock.click('#cz-interest-chips [data-rm="1"]')
    assert chips(dock) == [names[0]] and dock.evaluate("document.activeElement.getAttribute('data-rm')") == "0"
    dock.click('#cz-interest-chips [data-rm="0"]')
    assert chips(dock) == [] and dock.evaluate("document.activeElement.id") == "cz-interest-q"
    assert text(dock, "#cz-aud-sum") == "No interests chosen" and dock.is_hidden("#cz-aud-clear")


def test_clear_in_the_audience_card_drops_every_interest(dock):
    open_aud(dock)
    for q in ("soccer", "dentist"):
        search(dock, q)
        dock.keyboard.press("Enter")
    dock.click("#cz-aud-clear")
    assert chips(dock) == [] and dock.eval_on_selector("#cz-sec-aud", "e => e.open")
    assert dock.evaluate("document.activeElement === document.querySelector('#cz-sec-aud > summary')")


def test_an_interest_name_is_text_never_markup(browser, server):
    world = World()
    hostile = '<img src=x onerror="window.__pwned=1">Evil & co'
    world.taxonomy_override = {"groups": [{"key": "g", "label": '<b>Group</b>', "segments": [
        {"id": "evil", "label": hostile, "group": "g", "supported": True, "rate_all": 0.2, "source": "S", "confidence": "high", "source_date": "2026",
         "keywords": [], "company_names": []}]}]}
    page = new_page(browser, server, world)
    try:
        open_aud(page)
        search(page, "evil")
        assert page.evaluate("window.__pwned") is None and page.locator("#cz-interest-results img").count() == 0
        page.keyboard.press("Enter")
        assert page.locator("#cz-interest-chips img").count() == 0 and chips(page) == [hostile]
        page.click("#cz-browse")
        page.wait_for_selector(".segb-row")
        assert page.locator(".segb-dialog img").count() == 0 and page.evaluate("window.__pwned") is None
        assert page.inner_text(".segb-row .segb-td-name").strip().startswith(hostile)
        assert page.evaluate("document.getElementById('cz-interest-results').innerHTML.indexOf('<img') === -1")
    finally:
        page.context_.close()
    assert page.errors == []


# ------------------------------------------------------------------------------------------------------------------ Audience: the window

def open_window(page):
    page.click("#cz-browse")
    page.wait_for_selector(".segb-dialog .segb-row")


def narrow_to(page, query, first):
    """Types in the window's search box and waits for the (debounced) filter to show: `first` is the name of the row it must lead with."""
    page.fill(".segb-search-input", query)
    page.wait_for_function("""(first) => { const c = document.querySelector('.segb-count').textContent, r = document.querySelector('.segb-row .segb-td-name');
        return c.includes(' match') && r && r.textContent.includes(first); }""", arg=first)


def row_names(page, n=None):
    names = page.eval_on_selector_all(".segb-row .segb-td-name .segb-cell", "els => els.map(e => e.textContent.trim())")
    return names if n is None else names[:n]


def row_cells(page, row=0):
    return page.eval_on_selector_all(f".segb-row >> nth={row} >> td", "els => els.map(e => e.textContent.trim())")


def test_browse_all_audiences_opens_the_window_with_the_whole_list_and_the_three_columns(dock):
    open_aud(dock)
    assert text(dock, "#cz-browse") == "Browse all audiences 90"
    open_window(dock)
    assert dock.get_attribute(".segb-dialog", "role") == "dialog" and text(dock, ".segb-title") == "Browse all audiences"
    assert "share of U.S. adults" in text(dock, ".segb-sub")
    assert dock.eval_on_selector_all(".segb-sort-label", "els => els.map(e => e.textContent)") == ["Name", "Group", "Incidence rate"]
    assert text(dock, ".segb-count") == "90 audiences"
    assert dock.locator("#cz-interest-results:visible").count() == 0
    shot(dock, "dock_browse_dialog")
    dock.keyboard.press("Escape")                                        # the window closes alone: the dock stays open
    assert not dock.locator(".segb-dialog").count() and dock.is_visible("#constanza-dock")
    assert dock.evaluate("document.activeElement.id") == "cz-browse"
    dock.keyboard.press("Escape")                                        # and the next Escape is the dock's own
    assert dock.is_hidden("#constanza-dock")


def test_the_window_sorts_by_incidence_rate_and_keeps_audiences_without_one_visible_and_last(dock):
    open_aud(dock)
    open_window(dock)
    assert dock.get_attribute(".segb-th >> nth=3", "aria-sort") == "none" and dock.get_attribute(".segb-th >> nth=1", "aria-sort") == "ascending"      # by name to begin with
    dock.click('.segb-sort[data-key="rate"]')                             # a number column starts with the biggest
    assert dock.get_attribute(".segb-th >> nth=3", "aria-sort") == "descending"
    top = row_cells(dock, 0)
    assert top[-1] == "80%" and top[1].strip()                           # the most common audience first
    rates = dock.eval_on_selector_all(".segb-row td:last-child", "els => els.map(e => e.textContent.trim())")
    with_rate = [r for r in rates if r != "No public rate"]
    assert len(with_rate) == 54 and rates[: len(with_rate)] == with_rate and set(rates[len(with_rate):]) == {"No public rate"}
    assert [float(r.rstrip("%")) for r in with_rate] == sorted((float(r.rstrip("%")) for r in with_rate), reverse=True)
    dock.click('.segb-sort[data-key="rate"]')                             # ascending: the smallest first, the unrated STILL last
    rates = dock.eval_on_selector_all(".segb-row td:last-child", "els => els.map(e => e.textContent.trim())")
    assert rates[0] == "0.5%" and rates[-1] == "No public rate" and rates[53] != "No public rate"
    assert len(dock.locator(".segb-row.is-blocked").all()) == 0          # an unrated audience is not blocked: it can be picked, as in the typeahead


def test_ticking_audiences_in_the_window_and_applying_makes_the_chips_and_the_requests_interests(dock):
    open_aud(dock)
    open_window(dock)
    narrow_to(dock, "soccer", "Soccer")
    dock.click(".segb-row")
    narrow_to(dock, "renter", "Renters")
    dock.click(".segb-row >> nth=0")
    assert text(dock, ".segb-apply") == "Add 2 selected"
    dock.click(".segb-apply")
    assert not dock.locator(".segb-dialog").count()
    got = chips(dock)
    assert len(got) == 2 and "Sports Fans – Soccer" in got and dock.evaluate("document.activeElement.id") == "cz-browse"
    assert text(dock, "#cz-aud-count") == "2" and "Several interests are multiplied together" in text(dock, "#cz-interest-note")
    assert "2 of 10 interests chosen" in dock.inner_text("#cz-status")
    assert sorted(sent_terms(dock)) == sorted(got)
    # reopened: what is held opens ticked; unticking one removes it
    open_window(dock)
    assert text(dock, ".segb-sel-n") == "2 of 10 selected"
    narrow_to(dock, "soccer", "Soccer")
    assert dock.get_attribute(".segb-row", "aria-selected") == "true"
    dock.click(".segb-row")
    assert text(dock, ".segb-apply") == "Remove 1"
    dock.click(".segb-apply")
    assert "Sports Fans – Soccer" not in chips(dock) and len(chips(dock)) == 1


def test_cancel_discards_the_ticks(dock):
    open_aud(dock)
    open_window(dock)
    dock.click(".segb-row >> nth=0")
    dock.click(".segb-cancel")
    assert chips(dock) == [] and text(dock, "#cz-aud-sum") == "No interests chosen"


def test_the_window_stops_at_ten_and_the_typeahead_says_the_same(dock):
    open_aud(dock)
    open_window(dock)
    dock.click(".segb-all")                                              # "select all": up to the limit, never past it
    assert text(dock, ".segb-sel-n") == "10 of 10 selected" and "limit of 10" in text(dock, ".segb-limit")
    assert dock.locator(".segb-row.is-blocked").count() > 0
    dock.click(".segb-apply")
    assert len(chips(dock)) == 10 and text(dock, "#cz-interest-hint") == "10 of 10 chosen"
    search(dock, "renters")                                              # an 11th, from the typeahead
    dock.keyboard.press("Enter")
    assert text(dock, "#cz-interest-note") == "At most 10 interests at a time." and len(chips(dock)) == 10
    open_window(dock)
    assert text(dock, ".segb-sel-n") == "10 of 10 selected" and dock.locator(".segb-row.is-blocked").count() > 0     # at the limit already
    dock.click(".segb-cancel")


def test_an_interest_picked_in_the_typeahead_opens_ticked_in_the_window(dock):
    open_aud(dock)
    search(dock, "soccer")
    dock.keyboard.press("Enter")
    open_window(dock)
    narrow_to(dock, "soccer", "Soccer")
    assert dock.get_attribute(".segb-row", "aria-selected") == "true" and text(dock, ".segb-sel-n") == "1 of 10 selected"
    assert dock.is_disabled(".segb-apply")                               # nothing changed yet


def test_a_list_that_cannot_load_says_so_and_retry_works_and_is_not_remembered(dock):
    open_aud(dock)
    dock.evaluate("Constanza._state.taxonomy = null")                    # (the quiet fetch at open succeeded; ask again)
    dock.world.fail["taxonomy"] = (500, {"detail": "boom"})              # type: ignore[attr-defined]
    dock.click("#cz-browse")
    dock.wait_for_selector(".segb-retry")
    assert "audience list isn’t available right now" in text(dock, ".segb-state")
    dock.world.fail.clear()                                              # type: ignore[attr-defined]
    dock.click(".segb-retry")
    dock.wait_for_selector(".segb-row")
    assert text(dock, ".segb-count") == "90 audiences"


def test_without_the_window_script_the_browse_button_steps_aside_and_the_typeahead_still_works(browser, server):
    page = new_page(browser, server, World(), query="?nosb=1")
    try:
        open_aud(page)
        assert page.is_hidden("#cz-browse")
        search(page, "soccer")
        page.keyboard.press("Enter")
        assert chips(page) == ["Sports Fans – Soccer"]
    finally:
        page.context_.close()
    assert page.errors == []


# ------------------------------------------------------------------------------------------------------------------ the dock as a whole

def test_reset_clears_every_card_and_the_result(dock):
    geo(dock, "Los Angeles")
    dock.click('#cz-unit [data-unit="days"]'); dock.fill("#cz-flight", "45"); dock.press("#cz-flight", "Tab")
    open_who(dock)
    dock.click('#cz-age-presets [data-min="18"][data-max="34"]'); dock.click('#cz-gender [data-g="female"]')
    dock.click("#cz-hispanic")
    open_aud(dock)
    search(dock, "soccer"); dock.keyboard.press("Enter")
    pull(dock)
    assert dock.is_visible("#cz-result .cz-headline")
    dock.click("#cz-reset")
    assert dock.input_value("#cz-geo") == "" and dock.input_value("#cz-flight") == "1" and dock.input_value("#cz-age-min") == "" and dock.input_value("#cz-age-max") == ""
    sums = {key: text(dock, f"#cz-{key}-sum") for key, _ in SECTIONS}
    assert sums == {"where": "Type a place to start", "when": "1 month", "who": "Everyone, all ages", "lang": "Hispanic targeting on · default", "aud": "No interests chosen"}
    assert dock.get_attribute('#cz-unit [data-unit="months"]', "aria-pressed") == "true" and dock.get_attribute('#cz-gender [data-g=""]', "aria-pressed") == "true"
    assert chips(dock) == [] and dock.locator("#cz-geo-chips .aud-chip").count() == 0 and dock.is_hidden("#cz-where-clear") and dock.is_hidden("#cz-who-clear") and dock.is_hidden("#cz-aud-clear")
    assert dock.is_checked("#cz-hispanic")                                                           # back to the product's own default
    assert dock.is_hidden("#cz-who-count") and dock.is_hidden("#cz-aud-count") and dock.is_visible("#cz-geo-quick")
    assert dock.locator("#cz-result .cz-welcome").count() == 1 and dock.locator("#cz-result .cz-headline").count() == 0
    geo(dock, "Phoenix")
    sent = pull(dock)
    assert sent["audience"] is None and sent["flight_days"] is None and sent["months"] == 1 and "hispanic" not in sent and sent["geo_type_hint"] is None


def test_the_welcome_card_says_what_to_do_and_what_the_numbers_come_with(dock):
    assert text(dock, "#cz-result") == "Pick a product and a place, then Pull avails. Numbers come with the data they were modeled from."
    assert dock.locator("#cz-result .cz-welcome [aria-hidden='true'] svg").count() == 1


def test_the_form_changed_banner_follows_the_cards_and_goes_when_the_change_is_put_back(dock):
    geo(dock, "Los Angeles")
    pull(dock)
    assert dock.locator("#cz-result .cz-stale").count() == 0
    open_who(dock)
    dock.click('#cz-age-presets [data-min="25"][data-max="54"]')
    assert dock.locator("#cz-result .cz-stale").count() == 1
    dock.click('#cz-age-presets [data-min="25"][data-max="54"]')                                    # put back
    assert dock.locator("#cz-result .cz-stale").count() == 0
    dock.click("#cz-hispanic")
    assert dock.locator("#cz-result .cz-stale").count() == 1
    dock.click("#cz-hispanic")
    assert dock.locator("#cz-result .cz-stale").count() == 0
    open_aud(dock)
    search(dock, "soccer"); dock.keyboard.press("Enter")
    assert dock.locator("#cz-result .cz-stale").count() == 1
    dock.click('#cz-interest-chips [data-rm="0"]')
    assert dock.locator("#cz-result .cz-stale").count() == 0
    dock.click('#cz-geotype [data-type="county"]')
    assert dock.locator("#cz-result .cz-stale").count() == 1


def test_enter_in_the_geography_box_pulls_and_the_pull_button_keeps_the_focus_while_it_works(dock):
    geo(dock, "Los Angeles")
    dock.focus("#cz-go")
    dock.world.hold.add("estimate")                                                                  # type: ignore[attr-defined]
    dock.keyboard.press("Enter")
    dock.wait_for_function("document.getElementById('cz-go').getAttribute('aria-disabled') === 'true'")
    assert dock.evaluate("document.activeElement.id") == "cz-go"
    dock.keyboard.press("Enter")                                                                     # a second press while busy does nothing
    assert dock.world.hits.count("estimate") == 1                                                    # type: ignore[attr-defined]
    dock.world.hold.discard("estimate")                                                              # type: ignore[attr-defined]
    dock.world.release("estimate")                                                                   # type: ignore[attr-defined]
    dock.wait_for_selector("#cz-result .cz-headline")
    assert dock.evaluate("document.activeElement.id") == "cz-go"
    before = len(dock.world.requests)                                                                # type: ignore[attr-defined]
    dock.focus("#cz-geo")
    dock.keyboard.press("Enter")
    dock.wait_for_function(f"true")
    dock.wait_for_function("document.getElementById('cz-go').getAttribute('aria-disabled') === 'false'")
    assert len(dock.world.requests) == before + 1                                                    # type: ignore[attr-defined]


def test_escape_in_the_product_list_closes_that_list_alone(dock):
    dock.click("#cz-product-btn")
    assert dock.is_visible("#cz-picker-pop")
    dock.keyboard.press("Escape")
    assert dock.is_hidden("#cz-picker-pop") and dock.is_visible("#constanza-dock") and dock.evaluate("document.activeElement.id") == "cz-product-btn"
    dock.keyboard.press("Escape")
    assert dock.is_hidden("#constanza-dock")


def test_the_cards_that_fold_remember_how_the_planner_left_them(browser, server):
    world = World()
    page = new_page(browser, server, world)
    try:
        open_who(page)
        page.click("#cz-sec-lang > summary")                                                         # fold Language & heritage
        page.wait_for_function("(JSON.parse(localStorage.getItem('constanza.v2') || '{}').sections || {})['cz-sec-lang'] === false")
        saved = page.evaluate("JSON.parse(localStorage.getItem('constanza.v2')).sections")
        assert saved["cz-sec-who"] is True and saved["cz-sec-lang"] is False
        page.reload()
        page.evaluate("Constanza.open({})")
        assert page.eval_on_selector("#cz-sec-who", "e => e.open") and not page.eval_on_selector("#cz-sec-lang", "e => e.open") and not page.eval_on_selector("#cz-sec-aud", "e => e.open")
    finally:
        page.context_.close()
    assert page.errors == []


def test_every_control_in_the_form_is_a_36px_target_and_has_a_visible_focus_ring(dock):
    open_who(dock); open_aud(dock)
    search(dock, "soccer"); dock.keyboard.press("Enter")                                             # a chip, with its remove button
    geo(dock, "Springfield")                                                                         # so the geography help's buttons exist too
    small = dock.evaluate("""() => {
        const out = [];
        document.querySelectorAll('#cz-form button, #cz-form input, #cz-form select, #cz-form summary').forEach((el) => {
          if (!el.getClientRects().length || el.type === 'hidden') return;
          if (el.id === 'cz-hispanic') return;                    // the switch: its whole card (a label) is the target
          const r = el.getBoundingClientRect();
          if (r.height < 35.5) out.push([el.id || el.className || el.tagName, Math.round(r.height * 10) / 10]);
        });
        return out; }""")
    assert small == [], small
    # a visible ring (an outline or the glow) on the keyboard-focused control: tab through the first controls and read the computed style
    for _ in range(6):
        dock.keyboard.press("Tab")
        ring = dock.evaluate("""() => { const el = document.activeElement, cs = getComputedStyle(el);
            return { name: el.id || el.className || el.tagName, outline: cs.outlineStyle !== 'none' && parseFloat(cs.outlineWidth) >= 2, glow: cs.boxShadow !== 'none' }; }""")
        assert ring["outline"] or ring["glow"], ring


# ------------------------------------------------------------------------------------------------------------------ a forecast method's own panel

FAKE_TIKTOK = """() => Constanza.registerMethod("tiktok", (ctx) => ({
    formHtml: () => '<div class="aud-builder" id="fake-builder"><section class="aud-sec has-value" id="fake-sec"><div class="aud-sec-head"><span class="aud-step" aria-hidden="true"></span>'
      + '<div class="aud-sec-text"><h3 class="aud-sec-title">Who (TikTok)</h3></div></div><div class="aud-sec-body"><input id="fake-q" class="aud-input" aria-label="Fake"></div></section></div>',
    bind: () => {}, onSelect: () => {}, reset: () => {}, canPull: () => ({ ok: true }), requestPart: () => ({ tiktok: { gender: "female" } }), context: () => ({}),
    headlineHtml: () => "", bodyHtml: () => "", cardHtml: () => "", summaryLines: () => [], aboutHtml: () => "", goLabel: "Estimate audience →" }))"""


def test_a_forecast_methods_panel_replaces_who_language_and_audience_and_keeps_where_and_when(dock):
    assert dock.is_hidden("#cz-method-forms") and dock.is_visible("#cz-aud")                         # no panel on show: no empty row either
    dock.evaluate(FAKE_TIKTOK)
    choose_product(dock, "tiktok")
    assert dock.is_hidden("#cz-aud") and dock.is_visible("#cz-method-forms .cz-method-panel") and dock.is_visible("#cz-sec-where") and dock.is_visible("#cz-sec-when")
    assert text(dock, "#cz-go") == "Estimate audience →"
    # the panel's own cards keep counting on from Where and When (its nested builder does not start the numbers again at 1)
    assert dock.evaluate("getComputedStyle(document.getElementById('fake-builder')).counterReset") == "none"
    assert dock.evaluate("getComputedStyle(document.getElementById('cz-builder')).counterReset").startswith("aud-step")
    geo(dock, "Los Angeles")
    sent = pull(dock)
    assert sent["tiktok"] == {"gender": "female"} and sent["audience"] is None and sent["national_monthly_total"] is None
    choose_product(dock, "roku")
    assert dock.is_visible("#cz-aud") and dock.is_hidden("#cz-method-forms")
    shot(dock, "dock_method_panel")


def test_the_dock_only_hides_the_cards_it_replaces_and_hands_the_panel_the_same_context(dock):
    keys = dock.evaluate("""() => { let got = null; Constanza.registerMethod("probe", (ctx) => { got = Object.keys(ctx).sort(); return { formHtml: () => "", bind: () => {} }; }); return got; }""")
    assert keys == sorted(["S", "$", "esc", "num", "compact", "api", "markStale", "errorText", "setMood", "announce", "summarize", "productByName", "requestBody",
                           "targetsHispanic"])                                                       # the dock answers "does this line target Hispanic audiences?" itself


def test_the_dock_tells_a_panel_whether_its_line_targets_hispanic_audiences_each_time_it_is_asked(dock):
    dock.evaluate("""() => {
        window._targetsHispanic = (li) => /hispanic/i.test(li.target_override || "");                 // the page's own reading of a line (app.js), reduced to the test
        state.lineItems = [{ id: "L-HISP", target_override: "Hispanic adults 25-44" }, { id: "L-PLAIN", target_override: "Adults 18+" }];
        Constanza.registerMethod("probe", (ctx) => { window.__ctx = ctx; return { formHtml: () => "", bind: () => {} }; }); }""")
    ask = "window.__ctx.targetsHispanic()"
    assert dock.evaluate(ask) is False                                                               # a fresh dock, no line
    dock.evaluate("Constanza.open({ lineId: 'L-HISP' })")
    assert dock.evaluate(ask) is True                                                                # the SAME function, asked again: it follows the line the dock is open for
    dock.evaluate("Constanza.open({ lineId: 'L-PLAIN' })")
    assert dock.evaluate(ask) is False
    dock.evaluate("Constanza.open({ lineId: 'L-PLAIN', hispanic: true })")                           # the dock was told (the line's own choice, or the proposal's targeting)
    assert dock.evaluate(ask) is True
    dock.evaluate("Constanza.open({ lineId: 'L-HISP' })")                                            # (opening for a line starts its request over, the told choice with it)
    dock.evaluate("() => { window._targetsHispanic = () => { throw new Error('boom'); }; }")          # a page helper that fails never breaks the answer
    assert dock.evaluate(ask) is False
    dock.evaluate("() => { delete window._targetsHispanic; }")                                       # no helper on the page at all
    assert dock.evaluate(ask) is False


# ------------------------------------------------------------------------------------------------------------------ the three layouts

def overflow_report(page):
    """Anything in the dock wider than the dock, or any horizontal scroll (of the page or of the dock's own scroller)."""
    return page.evaluate("""() => {
        const dock = document.getElementById('constanza-dock').getBoundingClientRect(), out = [];
        document.querySelectorAll('#constanza-dock *').forEach((el) => {
          if (!el.getClientRects().length || el.closest('.cz-sr, .aud-sr')) return;
          const r = el.getBoundingClientRect();
          if (r.width && (r.right > dock.right + 0.5 || r.left < dock.left - 0.5)) out.push([el.id || el.className || el.tagName, Math.round(r.left), Math.round(r.right)]);
        });
        const b = document.getElementById('cz-body'), f = document.getElementById('cz-form');
        return { out: out.slice(0, 8), page: document.documentElement.scrollWidth - document.documentElement.clientWidth, body: b.scrollWidth - b.clientWidth, form: f.scrollWidth - f.clientWidth }; }""")


def fill_everything(page):
    """Every card holding something, so the longest versions of the lines are on the screen."""
    page.fill("#cz-geo", "Texas")
    page.wait_for_function("document.getElementById('cz-where-sum').textContent.startsWith('3 areas')")
    page.click('#cz-unit [data-unit="days"]'); page.fill("#cz-flight", "45")
    page.click("#cz-sec-who > summary")
    page.click('#cz-age-presets [data-min="25"][data-max="54"]'); page.click('#cz-gender [data-g="female"]')
    page.click("#cz-sec-aud > summary")
    for q in ("soccer", "dentist", "renters"):
        search(page, q); page.keyboard.press("Enter")


@pytest.mark.parametrize("name,viewport,wide", [("narrow", {"width": 1280, "height": 900}, False), ("wide", {"width": 1440, "height": 900}, True),
                                                ("phone", {"width": 390, "height": 844}, False), ("small_laptop", {"width": 1000, "height": 700}, True)])
def test_the_dock_fits_its_layout_with_every_card_full(browser, server, name, viewport, wide):
    world = World()
    page = new_page(browser, server, world, viewport=viewport)
    try:
        if wide and viewport["width"] > 720:
            page.click("#cz-expand")
        fill_everything(page)
        shot(page, f"dock_{name}_full")
        report = overflow_report(page)
        assert report == {"out": [], "page": 0, "body": 0, "form": 0}, report
        dock = page.evaluate("(() => { const r = document.getElementById('constanza-dock').getBoundingClientRect(); return [r.left, r.top, r.right, r.bottom, innerWidth, innerHeight]; })()")
        assert dock[0] >= 0 and dock[2] <= dock[4] + 0.5 and dock[1] >= 0 and dock[3] <= dock[5] + 0.5                 # on the screen
        if viewport["width"] <= 720:
            assert dock[0] == 0 and dock[2] == dock[4]                                                                  # a phone: the dock IS the screen
        form_w = page.evaluate("document.getElementById('cz-form').getBoundingClientRect().width")
        if wide and viewport["width"] > 1000:
            assert abs(form_w - 420) < 1.5                                                                              # two columns: form | result
            foot = page.evaluate("""(() => { const g = document.getElementById('cz-go').getBoundingClientRect(), f = document.getElementById('cz-form').getBoundingClientRect(); return [g.left, g.right, f.right]; })()""")
            assert foot[1] <= foot[2] + 1                                                                               # the primary action sits under the form column
        # every card is as wide as the column it sits in (the kit's grid does not make a second column in a dock)
        widths = page.eval_on_selector_all("#cz-builder > .cz-prod, #cz-builder > .aud-sec, #cz-aud > .cz-fold, #cz-aud .aud-sec", "els => els.map(e => Math.round(e.getBoundingClientRect().width))")
        assert len(set(widths)) == 1 and widths[0] > 280, widths
    finally:
        page.context_.close()
    assert page.errors == []


def test_the_result_pane_and_the_form_scroll_apart_in_the_wide_layout(browser, server):
    world = World()
    page = new_page(browser, server, world, viewport={"width": 1440, "height": 700})
    try:
        page.click("#cz-expand")
        fill_everything(page)
        page.click("#cz-go"); page.wait_for_selector("#cz-result .cz-headline")
        form, body = page.evaluate("""[(() => { const f = document.getElementById('cz-form'); return [f.scrollHeight > f.clientHeight, getComputedStyle(f).overflowY]; })(),
                                       getComputedStyle(document.getElementById('cz-body')).overflow]""")
        assert form == [True, "auto"] and body == "hidden"
        assert page.is_visible("#cz-go") and page.is_visible("#cz-result .cz-headline")                                 # the action bar and the result are both on screen
        shot(page, "dock_wide_result")
    finally:
        page.context_.close()
    assert page.errors == []


# ------------------------------------------------------------------------------------------------------------------ the review's fixes: nothing inside a summary, an even geography control, lists in view

def test_no_control_sits_inside_a_folding_cards_summary_and_each_clear_link_is_the_details_sibling_before_it(dock):
    assert dock.evaluate("""() => document.querySelectorAll('#cz-aud summary button, #cz-aud summary a, #cz-aud summary input, #cz-aud summary select, #cz-aud summary [tabindex], #cz-aud summary [role=button]').length""") == 0
    for stem in ("who", "lang", "aud"):
        assert dock.evaluate(f"""() => {{ const c = document.getElementById('cz-{stem}-clear'); return c.tagName === 'BUTTON' && c.parentElement.classList.contains('cz-fold')
            && c.nextElementSibling === document.getElementById('cz-sec-{stem}') && !c.closest('summary'); }}""")
    names = [dock.get_attribute(f"#cz-{stem}-clear", "aria-label") for stem in ("who", "lang", "aud")]
    assert names == ["Clear age and gender", "Turn off Hispanic targeting", "Clear all interests"]        # (the names are unchanged by the move)


@pytest.mark.parametrize("viewport", [{"width": 1280, "height": 900}, {"width": 390, "height": 800}])
def test_each_clear_link_lies_exactly_on_the_room_its_cards_head_keeps_for_it(browser, server, viewport):
    page = new_page(browser, server, World(), viewport=viewport)
    try:
        open_who(page); open_aud(page)
        page.click('#cz-gender [data-g="male"]')
        search(page, "soccer"); page.keyboard.press("Enter")
        rect = "(sel) => { const r = document.querySelector(sel).getBoundingClientRect(); return [r.left, r.top, r.right, r.bottom]; }"
        for stem in ("who", "lang", "aud"):
            assert page.is_visible(f"#cz-{stem}-clear"), stem
            link, twin = page.evaluate(rect, f"#cz-{stem}-clear"), page.evaluate(rect, f"#cz-sec-{stem} .cz-fold-twin")
            assert all(abs(a - b) <= 1 for a, b in zip(link, twin)), (stem, link, twin)                  # on the invisible copy the head reserved: no overlap with the count, none with the arrow
            head = page.evaluate(rect, f"#cz-sec-{stem} > summary")
            assert head[1] <= link[1] and link[3] <= head[3] and link[2] <= head[2] - 14, (stem, link, head)
        page.click("#cz-who-clear"); page.click("#cz-aud-clear")
        assert page.evaluate("getComputedStyle(document.querySelector('#cz-sec-who .cz-fold-twin')).display") == "none"       # no link, no room kept
        assert page.is_visible("#cz-lang-clear")                                                         # (Hispanic targeting is still on)
    finally:
        page.context_.close()
    assert page.errors == []


def test_clear_comes_before_the_toggle_for_the_keyboard_and_pressing_it_leaves_the_card_open_with_the_head_focused(dock):
    open_who(dock)
    dock.click('#cz-gender [data-g="male"]')
    dock.focus("#cz-who-clear")
    dock.keyboard.press("Tab")
    assert dock.evaluate("document.activeElement === document.querySelector('#cz-sec-who > summary')")      # Clear, then the toggle, then the body
    dock.keyboard.press("Tab")
    assert dock.evaluate("document.activeElement.id") == "cz-age-min"
    dock.focus("#cz-who-clear")
    dock.keyboard.press("Enter")
    assert dock.eval_on_selector("#cz-sec-who", "e => e.open") and dock.evaluate("document.activeElement === document.querySelector('#cz-sec-who > summary')")


@pytest.mark.parametrize("width", [320, 360, 390, 430, 1280])
def test_the_geography_type_control_is_an_even_three_by_two_not_five_and_an_orphan(browser, server, width):
    page = new_page(browser, server, World(), viewport={"width": width, "height": 800})
    try:
        page.wait_for_function("document.getElementById('constanza-dock').getAnimations().length === 0")      # (the dock's pop-in scales it a little while it plays)
        cells = page.evaluate("""() => Array.from(document.querySelectorAll('#cz-geotype > button')).map((b) => { const r = b.getBoundingClientRect(); return { top: Math.round(r.top), left: Math.round(r.left), w: Math.round(r.width), h: Math.round(r.height) }; })""")
        assert len(cells) == 6
        rows = sorted({c["top"] for c in cells})
        assert len(rows) == 2 and [sum(1 for c in cells if c["top"] == t) for t in rows] == [3, 3], cells        # two rows of three
        assert max(c["w"] for c in cells) - min(c["w"] for c in cells) <= 1 and all(c["h"] >= 36 for c in cells)      # equal cells, each a real target
        box = page.evaluate("(() => { const r = document.getElementById('cz-geotype').getBoundingClientRect(); return [r.left, r.right]; })()")
        assert all(box[0] <= c["left"] and c["left"] + c["w"] <= box[1] + 1 for c in cells)                     # all inside the frame
    finally:
        page.context_.close()
    assert page.errors == []


def reach_for_the_bottom_edge(page, field, gap=20):
    """Scrolls the dock's body until only `gap` px of it are left under `field`: where a field first shows up when the planner scrolls down."""
    page.evaluate("""([sel, gap]) => {
        document.getElementById('cz-form').style.paddingBottom = '600px';          // room to scroll the field that far up, wherever the form ends
        const body = document.getElementById('cz-body'), b = body.getBoundingClientRect(), r = document.querySelector(sel).getBoundingClientRect();
        body.scrollTop += r.bottom - (b.bottom - gap);
    }""", [field, gap])


def px_out_of_view(page, selector):
    return page.evaluate("""(sel) => { const b = document.getElementById('cz-body').getBoundingClientRect(), r = document.querySelector(sel).getBoundingClientRect();
        return r.height - Math.max(0, Math.min(r.bottom, b.bottom) - Math.max(r.top, b.top)); }""", selector)


@pytest.mark.parametrize("state", ["answer", "nothing", "error"])
def test_the_interests_list_opens_fully_in_view_whatever_it_says(dock, state):
    open_aud(dock)
    if state == "error":
        dock.world.fail["interests"] = (500, {"detail": "boom"})         # type: ignore[attr-defined]
    reach_for_the_bottom_edge(dock, "#cz-interest-q")
    search(dock, {"answer": "soccer", "nothing": "zzzzqqqq", "error": "soccer"}[state])
    assert px_out_of_view(dock, "#cz-interest-results") < 1, state
    assert px_out_of_view(dock, "#cz-interest-q") < 1
