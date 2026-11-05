"""The Constanza block on a Step 05 line, for every method, in a REAL browser (headless Chromium through Playwright).

The page is a small harness, not the whole app, but nothing in it is a stand-in for the code under test: the Constanza section of app.js (between its
`CONSTANZA HOST: begin / end` markers) and the real renderAvailsGrid are lifted out of app.js and run as they are, next to the real constanza.js, the TikTok
and Meta plugins, the audience kit and the real CSS. Only the proposal around them is stubbed (`state`: the line items, their avails, the catalog). The
/api/constanza/* routes are answered by the REAL route functions of app.main, over the strict fakes of Meta's and TikTok's APIs (tests/meta_support.py,
tests/tiktok_support.py), so the answers a card draws are the server's own.

What is pinned: the block markup of a platform line is what it always was (plus two data- attributes); every state of a Meta line's card and which buttons
it has; Recalculate sends the line's CURRENT choices, budget and months and cleans the "audience changed" reason; Insert writes the numbers and keeps the
estimate they came from; two lines of one product keep their own estimates; an estimate that comes back for a line that has changed or gone is not filed;
Insert into another line removes that line's estimate; a saved estimate that is corrupt or hostile takes nothing else down; a method whose script did not
load says so; the saved shapes (an older one is read, never rewritten until the planner estimates again). Skipped when Playwright or Chromium is missing."""
from __future__ import annotations

import copy
import http.server
import importlib.util
import json
import re
import threading
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

playwright_sync = pytest.importorskip("playwright.sync_api")

import app.main as main  # noqa: E402
from tests import method_fakes  # noqa: E402
from tests.fixtures import meta_snapshots  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "static"
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")

AWARENESS = "Facebook & Instagram Ads | Awareness"
TRAFFIC = "Facebook & Instagram Ads | Traffic / Conversion"
BRANDED = "Facebook & Instagram Ads on EVC Radio Pages"
TIKTOK = "Tiktok Ads"
ROKU = "Entravision Plus - Roku Ads (Includes The Roku Channel and the popular Espacio Latino Hub)"
UNLOADED = "Some Future Source Ads"


def _harness():
    spec = importlib.util.spec_from_file_location("_ff_frontend_harness", ROOT / "tests" / "test_full_flight_frontend.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


H = _harness()
LIFTED_FUNCTIONS = ("escapeHtml", "escapeAttr", "_mbParseDate", "_mbMonthKey", "_mbMonthLabel", "_mbLastDayOfMonth", "_mbMonthsBetween", "_effectiveStartDate", "_effectiveEndDate",
                    "_effectiveGeo", "_effectiveTargetText", "_effectiveProduct", "_isFullFlight", "parseFormattedInput", "formatImpsDisplay", "formatSpendDisplay",
                    "formatPlainDisplay", "calcMaxImpsFromSpend", "calcMaxSpendFromImps", "_defaultFrequency", "newLineItemId", "_isEstimateCpmProduct", "renderAvailsGrid")


def _region() -> str:
    start, end = APP_JS.index("// CONSTANZA HOST: begin"), APP_JS.index("// CONSTANZA HOST: end")
    return APP_JS[start:end]


def _product(family, method, series=None, cpm=None, **extra):
    """Priced the way the catalog prices them: platform data by CPM, an ad platform's audience product as a Fixed price with an estimated CPM."""
    forecast = {"enabled": True, "method": method, "series": series}
    forecast.update(extra.pop("forecast", {}))
    priced = {"pricing_model": "CPM", "rate": cpm} if method in ("platform", "generic") else {"pricing_model": "Fixed", "estimated_impressions": True}
    p = {"family": family, "is_addon": False, "forecast": forecast, "estimated_cpm_for_imps": cpm, "buying_model": "CPM", **priced}
    p.update(extra)
    return p


ROKU_SERIES = {"key": "roku", "channel": "platform_model", "data_basis": "modeled", "unit": "monthly", "title": "Roku"}
LIVE_SERIES = {"key": "nfl", "channel": "live_sports", "data_basis": "modeled", "unit": "weekly", "title": "NFL", "period_label": "Nov 2026"}
LIVE = "Entravision Plus - Live Sports Season"
PRODUCTS = {
    LIVE: _product("Entravision Plus", "platform", LIVE_SERIES, cpm=20),
    AWARENESS: _product("Social", "meta", cpm=12, forecast={"info": {"retail_cpm": 12}}),
    TRAFFIC: _product("Social", "meta", cpm=18, forecast={"info": {"retail_cpm": 18}}),
    BRANDED: _product("Social", "meta", forecast={"enabled": False}),
    TIKTOK: _product("Social", "tiktok", cpm=9),
    ROKU: _product("Entravision Plus", "platform", ROKU_SERIES, cpm=20),
    UNLOADED: _product("Social", "somefuture", cpm=10),
}

HARNESS = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>harness</title>
<link rel="stylesheet" href="/static/styles.css"><link rel="stylesheet" href="/static/constanza.css">
<link rel="stylesheet" href="/static/audience-kit.css"><link rel="stylesheet" href="/static/segment-browser.css">
<link rel="stylesheet" href="/static/constanza-tiktok.css"><link rel="stylesheet" href="/static/constanza-meta.css"></head><body>
<main style="max-width:1080px;margin:0 auto;padding:20px"><span id="avails-status-pill"></span><p id="avails-blurb"></p><div id="avails-grid"></div></main>
<script>
  // the proposal around the code under test: the pieces of the page that draw tabs, totals and share-of-voice are not what is being checked
  function renderAllTierTabStrips() {}
  function renderGenerateSummary() {}
  function updateSovBadge() {}
  function applySovDisplay() {}
  function computeSovPct() { return null; }
  function _lineNoteHtml() { return ""; }
  function _applyFrequencyTriangle() {}
  window.__PRODUCTS = %(products)s;
  var state = { parsed: {}, tiers: [{ label: "A" }], activeTierLabel: "A", activeTierName: null, activeTierGeo: null, activeTierStartDate: null, activeTierEndDate: null,
                timeUnit: "month", lineItems: [], availsData: {}, productIndex: {}, catalog: null };
  /*LIFTED*/
  /*REGION*/
  // what the page does when it opens Step 05 for these lines
  window.setup = function (cfg) {
    cfg = cfg || {};
    state.parsed = { request_type: "avails", demo: "demo" in cfg ? cfg.demo : "Adults 25-54", behavioral: "", geo: "geo" in cfg ? cfg.geo : "Los Angeles",
                     start_date: "start" in cfg ? cfg.start : "2026-11-01", end_date: "end" in cfg ? cfg.end : "2026-12-31" };
    state.activeTierStartDate = null; state.activeTierEndDate = null;
    state.productIndex = {}; const byFamily = {};
    Object.keys(window.__PRODUCTS).forEach(function (name) {
      const p = Object.assign({ name: name }, window.__PRODUCTS[name]);
      state.productIndex[name] = p; (byFamily[p.family] = byFamily[p.family] || []).push(p);
    });
    state.catalog = { families: Object.keys(byFamily), products_by_family: byFamily, audience_multipliers: { conservative: 4.3, mid: 5.5, upper: 8.6 } };
    state.lineItems = (cfg.lines || []).map(function (l) { return Object.assign({ months: 1, monthly_budget: 2000, target_override: "" }, l); });
    state.availsData = cfg.avails || {};
    renderAvailsGrid();
  };
</script>
<script src="/static/segment-browser.js"></script>
<script src="/static/constanza.js"></script>
<script src="/static/constanza-tiktok.js"></script>
<script src="/static/constanza-meta.js"></script>
</body></html>"""


def harness_html() -> str:
    lifted = "\n".join([H._extract_const(APP_JS, "_SOV_MONTHLY_EQUIVALENT_SCALE"), H._extract_const(APP_JS, "FULL_FLIGHT")]
                       + [H._extract_fn(APP_JS, name) for name in LIFTED_FUNCTIONS])
    return HARNESS.replace("/*LIFTED*/", lifted).replace("/*REGION*/", _region()).replace("%(products)s", json.dumps(PRODUCTS))


class _Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=str(STATIC.parent), **k)

    def do_GET(self):
        if self.path.startswith("/harness.html"):
            raw = harness_html().encode()
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


@pytest.fixture(scope="module")
def fakes():
    """The strict fakes of Meta's and TikTok's APIs for the whole module (patched into the real services; undone at the end)."""
    mp = pytest.MonkeyPatch()
    try:
        meta = method_fakes.FAKES["meta"].install(mp)
        tiktok = method_fakes.FAKES["tiktok"].install(mp)
        yield {"meta": meta, "tiktok": tiktok}
    finally:
        mp.undo()


# --------------------------------------------------------------------------------------------------------------------------------- the server's side

class World:
    """What the harness's /api answers: the real route functions of app.main. A test can park an answer (`hold`) and release it later, make a route
    fail (`fail`), or switch Meta's connection off (`meta_connected = False`: the secrets are taken away while the route runs)."""

    def __init__(self):
        self.meta_connected = True
        self.hits: list[str] = []
        self.estimates: list[dict] = []                    # the bodies the page sent to /estimate, in order
        self.fail: dict[str, tuple[int, dict]] = {}
        self.hold: set[str] = set()
        self.pending: list[tuple] = []

    def name_of(self, path: str) -> str:
        return {"api/constanza/estimate": "estimate", "api/constanza/resolve": "resolve", "api/constanza/meta/status": "meta-status",
                "api/constanza/tiktok/status": "tiktok-status"}.get(path, "other")

    def release(self, name) -> None:
        self.hold.discard(name)
        for item in [p for p in self.pending if p[0] == name]:
            self.pending.remove(item)
            item[1].fulfill(status=item[2], content_type="application/json", body=json.dumps(item[3]))

    def answer(self, route) -> None:
        req = route.request
        path = req.url.split("//", 1)[1].split("/", 1)[1].split("?")[0]
        name = self.name_of(path)
        self.hits.append(name)
        status, body = 200, {}
        try:
            payload = json.loads(req.post_data) if req.post_data else None
            if name in self.fail:
                status, body = self.fail[name]
            elif name == "estimate":
                self.estimates.append(payload)
                with pytest.MonkeyPatch.context() as mp:
                    if not self.meta_connected:
                        mp.delenv("META_ACCESS_TOKEN", raising=False)
                        mp.delenv("META_AD_ACCOUNT_ID", raising=False)
                    body = main.constanza_estimate(main.ConstanzaEstimateRequest(**payload))
            elif name == "resolve":
                body = main.constanza_resolve(main.ConstanzaResolveRequest(**payload))
            elif name == "meta-status":
                with pytest.MonkeyPatch.context() as mp:
                    if not self.meta_connected:
                        mp.delenv("META_ACCESS_TOKEN", raising=False)
                        mp.delenv("META_AD_ACCOUNT_ID", raising=False)
                    body = main.constanza_meta_status()
            elif name == "tiktok-status":
                body = main.constanza_tiktok_status()
            elif path == "api/constanza/interest-taxonomy":
                body = {"groups": []}
            elif path == "api/constanza/meta-geos":
                body = main.constanza_meta_geos(main.MetaGeoSearchRequest(**payload))
            elif path == "api/constanza/meta-languages":
                body = main.constanza_meta_languages(parse_qs(urlparse(req.url).query).get("q", [""])[0])
            elif path == "api/constanza/meta-targeting":
                query = parse_qs(urlparse(req.url).query)
                body = main.constanza_meta_targeting(query.get("kind", [""])[0], query.get("q", [""])[0])
            else:
                status, body = 404, {"detail": "not found"}
        except HTTPException as exc:
            status, body = exc.status_code, {"detail": exc.detail}
        except ValidationError as exc:
            status, body = 422, {"detail": [{"msg": e["msg"], "loc": list(e["loc"])} for e in exc.errors()]}
        if name in self.hold:
            self.pending.append((name, route, status, body))
            return
        route.fulfill(status=status, content_type="application/json", body=json.dumps(body))


def open_page(browser, server, world, cfg=None, viewport=None):
    context = browser.new_context(viewport=viewport or {"width": 1100, "height": 900})
    page = context.new_page()
    page.route("**/api/**", world.answer)
    page.route("**/fonts.googleapis.com/**", lambda r: r.abort())
    page.route("**/fonts.gstatic.com/**", lambda r: r.abort())
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" and "Failed to load resource" not in m.text else None)
    page.goto(f"{server}/harness.html")
    page.world, page.errors, page.allowed = world, errors, []        # type: ignore[attr-defined]
    if cfg is not None:
        setup(page, **cfg)
    return context, page


def setup(page, **cfg) -> None:
    page.evaluate("cfg => window.setup(cfg)", cfg)


@pytest.fixture
def open_step5(browser, server, fakes):
    opened = []

    def _open(lines=None, avails=None, connected=True, **cfg):
        world = World()
        world.meta_connected = connected
        context, page = open_page(browser, server, world)
        opened.append((context, page))
        setup(page, lines=lines if lines is not None else [{"id": "L1", "product_name": AWARENESS}], avails=avails or {}, **cfg)
        return page

    yield _open
    for context, page in opened:
        context.close()
        unexpected = [e for e in page.errors if not any(allowed in e for allowed in page.allowed)]
        assert unexpected == [], unexpected


# --------------------------------------------------------------------------------------------------------------------------------- helpers

def block(page, lid="L1") -> str:
    return page.evaluate("lid => (document.querySelector(`.constanza-block[data-lid=\"${lid}\"]`) || {}).outerHTML || ''", lid)


def squash(html: str) -> str:
    """The markup without the layout whitespace between tags, the way a browser would write it back (`display:none;` -> `display: none;`, the icon
    reduced to a marker: a browser serialises its self-closing parts differently from the string that made them)."""
    html = re.sub(r"<svg\b.*?</svg>", "<svg/>", html, flags=re.S)
    html = html.replace("display:none;", "display: none;")
    return re.sub(r"\s+", " ", re.sub(r">\s+<", "><", html)).strip()


def buttons(page, lid="L1") -> list[str]:
    return page.eval_on_selector_all(f'.constanza-block[data-lid="{lid}"] button', "els => els.map(e => e.textContent.trim().replace(/\\s+/g, ' '))")


def card_text(page, lid="L1") -> str:
    return page.inner_text(f"#constanza-result-{lid}")


def meta_answer(world_body: dict) -> dict:
    return main.constanza_estimate(main.ConstanzaEstimateRequest(**world_body))


def meta_body(page, lid="L1", **block) -> dict:
    """An estimate body for a Meta line the way the dock would send it (the place picked in Meta's own list), with the line's current facts."""
    facts = page.evaluate("lid => constanzaHost.lineFacts(lid)", lid)
    audience = {"geo": {"type": "geo_market", "key": "803", "label": "Los Angeles"}, "age_min": None, "age_max": None, "gender": "all", "languages": [], "targeting": []}
    audience.update(block)
    if facts["budget"] > 0:
        audience.setdefault("retail_budget", facts["budget"])
    if facts["cpm"] is not None:
        audience.setdefault("retail_cpm", facts["cpm"])
    return {"product_name": facts["product_name"], "geo_text": "", "geo_type_hint": None, "months": facts["months"], "flight_days": facts["flight_days"], "meta": audience}


def seed_snapshot(page, lid="L1", **block) -> dict:
    """Saves the estimate of a line the way a pull from the dock does: the real answer of the real route, wrapped by Constanza.makeSnapshot, kept by the host."""
    body = meta_body(page, lid, **block)
    data = meta_answer(body)
    page.evaluate("""([lid, body, data]) => {
        const facts = constanzaHost.lineFacts(lid);
        const snap = Constanza.makeSnapshot(facts.method, body, data, facts);
        if (!snap) throw new Error('the method keeps no snapshot');
        if (!constanzaHost.saveSnapshot(lid, snap)) throw new Error('the line is not in the option on the screen');
    }""", [lid, body, data])
    return data


def entry(page, lid="L1"):
    return page.evaluate("lid => state.availsData[lid] || null", lid)


# =================================================================================================================================
# The block of a line that pulls from the proposal's geography: what it always was
# =================================================================================================================================

def _block_of_the_old_page(lid: str, icon: str, period: str = "") -> str:
    """The markup the page drew for such a line before the block was generic (app.js at the baseline commit, `<div class="constanza-block">` ...)."""
    return (f'<div class="constanza-block">{period}<div class="cz-card-actions">'
            f'<button type="button" class="btn-secondary constanza-autocalc-btn" data-lid="{lid}">{icon}Auto-calculate avails</button>'
            f'<button type="button" class="cz-link constanza-line-dock-btn" data-lid="{lid}">Open in Constanza</button></div>'
            f'<div class="constanza-result" id="constanza-result-{lid}" style="display:none;"></div></div>')


def _with_the_two_data_attributes(markup: str, lid: str, method: str) -> str:
    return markup.replace('<div class="constanza-block">', f'<div class="constanza-block" data-lid="{lid}" data-method="{method}">', 1)


def test_a_platform_lines_block_is_what_it_was_plus_the_two_data_attributes(open_step5):
    page = open_step5(lines=[{"id": "L1", "product_name": ROKU}])
    icon = page.evaluate("Constanza.iconHtml()")
    expected = _with_the_two_data_attributes(_block_of_the_old_page("L1", icon), "L1", "platform")
    assert squash(block(page)) == squash(expected)
    assert page.is_hidden("#constanza-result-L1") and page.inner_html("#constanza-result-L1") == ""


def test_a_live_sports_lines_block_keeps_its_period_line(open_step5):
    page = open_step5(lines=[{"id": "L1", "product_name": LIVE}])
    icon = page.evaluate("Constanza.iconHtml()")
    period = page.evaluate("Constanza.periodLineHtml(state.productIndex[%s].forecast.series)" % json.dumps(LIVE))
    assert "Live sports period: Nov 2026" in period
    assert squash(block(page)) == squash(_with_the_two_data_attributes(_block_of_the_old_page("L1", icon, period), "L1", "platform"))

# --------------------------------------------------------------------------------------------------------------------------------- more helpers

def put_avails(page, avails: dict) -> None:
    """What the page does when a proposal is opened or an option is switched: new availsData, Step 05 drawn again."""
    page.evaluate("avails => { state.availsData = avails; renderAvailsGrid(); }", avails)


def redraw(page) -> None:
    page.evaluate("renderAvailsGrid()")


def click(page, selector: str) -> None:
    """A click that the dock or the bubble drawn over the page cannot intercept."""
    page.evaluate("sel => { const el = document.querySelector(sel); if (!el) throw new Error('nothing matches ' + sel); el.click(); }", selector)


def settle(page) -> None:
    """Waits until no Step 05 estimate is on its way."""
    page.wait_for_function("() => Object.values(_constanzaBusy).every(v => !v)")


def facts(page, lid="L1") -> dict:
    return page.evaluate("lid => constanzaHost.lineFacts(lid)", lid)


def legacy_entry(page, kind="live+languages", lid="L1") -> dict:
    """An older saved estimate (availsData[lid].meta_forecast) of the given kind, pulled for the line exactly as it is now: the adopted snapshot is fresh."""
    f = facts(page, lid)
    saved = copy.deepcopy(meta_snapshots.LEGACY[kind])
    saved.update(product_name=f["product_name"], months=f["months"], proposal_geo=f["geo_text"], target_text=f["target_text"], retail_budget=f["budget"], retail_cpm=f["cpm"])
    if saved.get("scenarios"):
        scenarios = saved["scenarios"]
        scenarios["months"] = f["months"]
        scenarios["flight"] = {k: v * f["months"] for k, v in scenarios["monthly"].items()}      # a flight is the monthly figure times its months
    return saved


def state_of(page, lid="L1"):
    return page.evaluate("lid => JSON.parse(JSON.stringify(state.availsData[lid] || null))", lid)


def insert_label(mid: int) -> str:
    return f"Insert the mid-case ({mid:,} / month) into Avails →"


def classes_of_buttons(page, lid="L1") -> list[str]:
    return page.eval_on_selector_all(f'.constanza-block[data-lid="{lid}"] button', "els => els.map(e => e.className)")


def open_dock(page, lid="L1") -> None:
    click(page, f'.constanza-block[data-lid="{lid}"] .constanza-line-dock-btn, .constanza-block[data-lid="{lid}"] .cz-refine')
    page.wait_for_selector("#constanza-dock:not(.hidden)")


def pull(page) -> None:
    """Presses the dock's pull button and waits until its answer has been drawn."""
    before = len(page.world.estimates)
    click(page, "#cz-go")
    for _ in range(200):
        if len(page.world.estimates) > before:
            break
        page.wait_for_timeout(25)
    assert len(page.world.estimates) > before, "the dock sent nothing"
    page.wait_for_function("() => !Constanza._state.busy")


def nothing_said(page) -> bool:
    """Has the page's polite live region (made the first time something is announced) said nothing?"""
    return page.evaluate("() => { const el = document.getElementById('constanza-live-announce'); return !el || el.textContent === ''; }")


def two_lines(open_step5, **extra):
    return open_step5(lines=[{"id": "L1", "product_name": AWARENESS}, {"id": "L2", "product_name": AWARENESS}], **extra)


# =================================================================================================================================
# Every line gets the one block, and a TikTok line's is a platform line's twin
# =================================================================================================================================

def test_a_tiktok_lines_block_is_the_platform_blocks_twin(open_step5):
    page = open_step5(lines=[{"id": "L1", "product_name": TIKTOK}])
    icon = page.evaluate("Constanza.iconHtml()")
    assert squash(block(page)) == squash(_with_the_two_data_attributes(_block_of_the_old_page("L1", icon), "L1", "tiktok"))


def test_every_block_carries_its_line_and_its_method_and_a_free_form_line_has_none(open_step5):
    page = open_step5(lines=[{"id": "L1", "product_name": AWARENESS}, {"id": "L2", "product_name": TIKTOK}, {"id": "L3", "product_name": ROKU},
                             {"id": "L4", "product_name": BRANDED}],
                      avails={"L3": {"freeform": True}})
    seen = page.eval_on_selector_all(".constanza-block", "els => els.map(e => [e.dataset.lid, e.dataset.method])")
    assert seen == [["L1", "meta"], ["L2", "tiktok"]]                      # L3 is free-form, L4's product is switched off in Admin
    assert page.eval_on_selector_all(".constanza-result", "els => els.map(e => e.id)") == ["constanza-result-L1", "constanza-result-L2"]


# =================================================================================================================================
# The states of a Meta line's card (design 3.5)
# =================================================================================================================================

def test_a_meta_line_with_no_estimate_has_one_button_and_says_what_to_do(open_step5):
    page = open_step5()
    assert buttons(page) == ["Estimate in Constanza →"]
    assert "btn-primary" in classes_of_buttons(page)[0]
    assert "Meta needs an audience" in card_text(page) and "Add a budget" not in card_text(page)
    assert "Auto-calculate" not in page.inner_text(".constanza-block")        # nothing to pull from the proposal's geography: the place is Meta's own


def test_a_meta_line_with_no_budget_says_where_to_add_one(open_step5):
    page = open_step5(lines=[{"id": "L1", "product_name": AWARENESS, "monthly_budget": 0}])
    assert "Add a budget in Step 04 to see the budget cross-check." in card_text(page)
    assert buttons(page) == ["Estimate in Constanza →"]


def test_a_fresh_estimate_shows_the_result_and_insert_recalculate_and_change(open_step5):
    page = open_step5()
    data = seed_snapshot(page)
    mid = data["meta"]["monthly"]["mid"]
    assert buttons(page) == [insert_label(mid), "Recalculate", "Change in Constanza"]
    classes = classes_of_buttons(page)
    assert "btn-primary" in classes[0] and "btn-secondary" in classes[1] and "cz-link" in classes[2]
    text = card_text(page)
    for expected in ("Conservative", "default", "Upper", f"{data['est_uniques']:,}", "Pulled for Los Angeles", "Budget cross-check", "Meta API"):
        assert expected in text, expected
    assert "Inputs changed" not in text
    assert page.is_visible("#constanza-result-L1")


STALE_CASES = {
    "budget": ("state.lineItems[0].monthly_budget = 3000", "state.lineItems[0].monthly_budget = 2000", "the budget changed"),
    "months": ("state.parsed.end_date = '2027-01-31'", "state.parsed.end_date = '2026-12-31'", "the flight length changed"),
    "geography": ("state.parsed.geo = 'Chicago'", "state.parsed.geo = 'Los Angeles'", "geography changed"),
    "target": ("state.parsed.demo = 'Adults 18-34'", "state.parsed.demo = 'Adults 25-54'", "target changed"),
    "cpm": ("state.lineItems[0].estimated_cpm_override = 15", "delete state.lineItems[0].estimated_cpm_override", "the CPM changed"),
    "product": (f"state.lineItems[0].product_name = {json.dumps(TRAFFIC)}", f"state.lineItems[0].product_name = {json.dumps(AWARENESS)}", "the product changed"),
}


@pytest.mark.parametrize("what", list(STALE_CASES))
def test_a_changed_line_marks_the_estimate_stale_withdraws_insert_and_changing_it_back_cleans_it(open_step5, what):
    change, back, reason = STALE_CASES[what]
    page = open_step5()
    seed_snapshot(page)
    page.evaluate(f"{change}; renderAvailsGrid()")
    assert reason in page.inner_text("#constanza-result-L1 .cz-stale"), what
    assert buttons(page) == ["Recalculate", "Change in Constanza"], "the Insert button is withdrawn while the estimate is stale"
    assert "btn-primary" in classes_of_buttons(page)[0]                          # Recalculate becomes the main button
    page.evaluate(f"{back}; renderAvailsGrid()")
    assert page.locator("#constanza-result-L1 .cz-stale").count() == 0 and buttons(page)[0].startswith("Insert the mid-case"), what


@pytest.mark.parametrize("what", list(STALE_CASES))
def test_an_older_saved_estimate_goes_stale_for_the_same_six_reasons(open_step5, what):
    change, back, reason = STALE_CASES[what]
    page = open_step5()
    put_avails(page, {"L1": {"meta_forecast": legacy_entry(page)}})
    assert page.locator("#constanza-result-L1 .cz-stale").count() == 0 and buttons(page)[0].startswith(insert_label(5_500_000)[:20])
    page.evaluate(f"{change}; renderAvailsGrid()")
    assert reason in page.inner_text("#constanza-result-L1 .cz-stale"), what
    page.evaluate(f"{back}; renderAvailsGrid()")
    assert page.locator("#constanza-result-L1 .cz-stale").count() == 0, what


def test_an_estimate_entered_by_hand_is_drawn_as_that_and_cannot_be_recalculated(open_step5):
    page = open_step5()
    put_avails(page, {"L1": {"meta_forecast": legacy_entry(page, "manual")}})
    assert "entered manually" in card_text(page)
    assert buttons(page) == [insert_label(5_500_000), "Estimate in Constanza →"]      # no request to repeat: the dock is the way to a pulled one


def test_a_budget_only_older_estimate_asks_for_an_audience_and_keeps_its_cross_check(open_step5):
    page = open_step5()
    put_avails(page, {"L1": {"meta_forecast": legacy_entry(page, "budget-only")}})
    assert "Estimate an audience to see the scenarios." in card_text(page) and "Budget cross-check" in card_text(page)
    assert buttons(page) == ["Estimate in Constanza →"]


def test_an_older_live_estimate_is_read_with_its_buttons_and_nothing_is_written_back(open_step5):
    page = open_step5()
    saved = legacy_entry(page)
    put_avails(page, {"L1": {"meta_forecast": saved, "max_imps": 1}})
    assert buttons(page) == [insert_label(5_500_000), "Recalculate", "Change in Constanza"]
    text = card_text(page)
    assert "Spanish" in text and "Soccer" in text and "Los Angeles" in text
    redraw(page)
    assert state_of(page) == {"meta_forecast": saved, "max_imps": 1}           # reading it in its older shape writes nothing


def test_the_card_says_when_meta_is_not_connected_and_holds_recalculate_off(open_step5):
    page = open_step5(connected=False)
    seed_snapshot(page)
    for _ in range(40):                                     # the connection check the first draw asked for has answered
        if "meta-status" in page.world.hits:
            break
        page.wait_for_timeout(50)
    page.wait_for_timeout(100)
    redraw(page)
    assert page.get_attribute(".constanza-block .cz-recalc", "aria-disabled") == "true"
    assert "isn't connected yet" in card_text(page)
    before = len(page.world.estimates)
    click(page, ".constanza-block .cz-recalc")
    page.wait_for_timeout(150)
    assert len(page.world.estimates) == before                # a held-off button asks nothing
    assert buttons(page)[0].startswith("Insert the mid-case")  # the saved estimate can still be inserted


# =================================================================================================================================
# Recalculate
# =================================================================================================================================

def test_recalculate_sends_the_saved_choices_with_the_lines_current_budget_months_and_cpm(open_step5):
    page = open_step5()
    seed_snapshot(page, age_min=25, age_max=44, gender="female", languages=[{"id": "1001", "name": "Spanish"}])
    page.evaluate("state.lineItems[0].monthly_budget = 3000; renderAvailsGrid()")
    click(page, ".constanza-block .cz-recalc")
    settle(page)
    sent = page.world.estimates[-1]
    assert sent["product_name"] == AWARENESS and sent["months"] == 2 and sent["geo_text"] == "" and sent["geo_type_hint"] is None
    assert sent["meta"]["geo"] == {"type": "geo_market", "key": "803", "label": "Los Angeles"}
    assert (sent["meta"]["age_min"], sent["meta"]["age_max"], sent["meta"]["gender"]) == (25, 44, "female")
    assert [x["id"] for x in sent["meta"]["languages"]] == ["1001"]
    assert sent["meta"]["retail_budget"] == 3000 and sent["meta"]["retail_cpm"] == 12
    assert "hispanic" not in sent and "resolve" not in page.world.hits
    snap = state_of(page)["forecast_snapshot"]
    assert snap["line"]["budget"] == 3000 and snap["line"]["months"] == 2 and snap["request"]["meta"]["retail_budget"] == 3000
    assert page.locator("#constanza-result-L1 .cz-stale").count() == 0 and buttons(page)[0].startswith("Insert the mid-case")
    assert page.locator(".cz-pulling").count() == 0 and page.get_attribute("#constanza-result-L1", "aria-busy") is None


def test_recalculate_sends_what_the_dock_holds_and_cleans_the_audience_changed_reason(open_step5):
    page = open_step5()
    seed_snapshot(page, gender="all")
    open_dock(page)                                                          # the dock opens for the line with its saved audience
    click(page, '#cz-mt-gender [data-gender="female"]')                      # an edit that is never pulled
    page.wait_for_selector("#constanza-result-L1 .cz-stale")
    assert "audience in Constanza changed" in card_text(page)
    assert buttons(page) == ["Recalculate", "Change in Constanza"]
    click(page, ".constanza-block .cz-recalc")
    settle(page)
    assert page.world.estimates[-1]["meta"]["gender"] == "female"           # the line's current choices, not the saved ones
    assert state_of(page)["forecast_snapshot"]["request"]["meta"]["gender"] == "female"
    assert page.locator("#constanza-result-L1 .cz-stale").count() == 0 and buttons(page)[0].startswith("Insert the mid-case")


def test_putting_the_dock_back_to_what_was_pulled_cleans_the_card_too(open_step5):
    page = open_step5()
    seed_snapshot(page, gender="all")
    open_dock(page)
    click(page, '#cz-mt-gender [data-gender="female"]')
    page.wait_for_selector("#constanza-result-L1 .cz-stale")
    click(page, '#cz-mt-gender [data-gender="all"]')
    page.wait_for_function("() => !document.querySelector('#constanza-result-L1 .cz-stale')")
    assert buttons(page)[0].startswith("Insert the mid-case")


def test_reset_in_the_dock_leaves_the_lines_card_alone_and_opening_it_again_brings_the_saved_audience_back(open_step5):
    page = open_step5()
    seed_snapshot(page, gender="female")
    saved = state_of(page)["forecast_snapshot"]
    open_dock(page)
    click(page, '#cz-mt-gender [data-gender="male"]')
    page.wait_for_selector("#constanza-result-L1 .cz-stale")
    click(page, "#cz-reset")
    page.wait_for_function("() => !document.querySelector('#constanza-result-L1 .cz-stale')")
    assert state_of(page)["forecast_snapshot"] == saved
    page.evaluate("Constanza.close()")
    open_dock(page)
    assert page.get_attribute('#cz-mt-gender [data-gender="female"]', "aria-pressed") == "true"


def test_recalculate_from_an_older_saved_estimate_rebuilds_its_audience_and_saves_the_current_shape(open_step5):
    page = open_step5()
    put_avails(page, {"L1": {"meta_forecast": legacy_entry(page), "max_imps": 777}})
    click(page, ".constanza-block .cz-recalc")
    settle(page)
    sent = page.world.estimates[-1]["meta"]
    assert sent["geo"]["key"] == "803" and (sent["age_min"], sent["age_max"], sent["gender"]) == (25, 44, "female")
    assert [x["id"] for x in sent["languages"]] == ["1001", "1002"] and [x["id"] for x in sent["targeting"]] == ["6003107902433"]
    assert sent["retail_budget"] == 2000 and page.world.estimates[-1]["months"] == 2
    saved = state_of(page)
    assert saved["forecast_snapshot"]["v"] == 1 and saved["forecast_snapshot"]["method"] == "meta" and "legacy" not in saved["forecast_snapshot"]
    assert "meta_forecast" not in saved and saved["max_imps"] == 777           # the older shape is replaced; the numbers in Avails are the planner's own


def test_a_failed_recalculate_keeps_the_saved_estimate_and_says_why_with_no_geography_help(open_step5):
    page = open_step5()
    seed_snapshot(page)
    saved = state_of(page)["forecast_snapshot"]
    page.world.fail["estimate"] = (503, {"detail": "Meta isn't connected yet: an admin needs to add META_ACCESS_TOKEN."})
    click(page, ".constanza-block .cz-recalc")
    settle(page)
    page.wait_for_selector("#constanza-result-L1 .constanza-error")
    text = card_text(page)
    assert "Meta isn't connected yet" in text and "Conservative" in text          # the error is said above the estimate it did not replace
    assert state_of(page)["forecast_snapshot"] == saved
    assert "resolve" not in page.world.hits and page.locator("#constanza-result-L1 .cz-geohelp .cz-chip, #constanza-result-L1 .cz-geochips").count() == 0
    assert page.get_attribute(".constanza-block .cz-recalc", "aria-disabled") is None
    del page.world.fail["estimate"]
    click(page, ".constanza-block .cz-recalc")                                   # and it can be asked again
    settle(page)
    assert state_of(page)["forecast_snapshot"]["saved_at"] != saved["saved_at"] or len(page.world.estimates) == 2
    assert page.locator("#constanza-result-L1 .constanza-error").count() == 0


def test_a_second_press_while_an_estimate_is_on_its_way_asks_nothing(open_step5):
    page = open_step5()
    seed_snapshot(page)
    page.world.hold.add("estimate")
    click(page, ".constanza-block .cz-recalc")
    page.wait_for_selector("#constanza-result-L1 .cz-pulling")
    assert page.get_attribute(".constanza-block .cz-recalc", "aria-disabled") == "true"
    click(page, ".constanza-block .cz-recalc")
    page.evaluate("() => { onConstanzaAutoCalc('L1'); }")  # the other doors to the same pull (the Hispanic switch, a geography chip) are held off too
    page.wait_for_timeout(150)
    asked = len(page.world.estimates)
    page.world.hold.discard("estimate")                     # (let whatever is waiting go before judging, so a failure ends the test instead of hanging it)
    page.world.release("estimate")
    settle(page)
    assert asked == 1
    assert page.locator(".cz-pulling").count() == 0 and page.get_attribute(".constanza-block .cz-recalc", "aria-disabled") is None


def test_a_saved_estimate_is_replaced_never_changed_in_place(open_step5):
    page = open_step5()
    seed_snapshot(page)
    page.evaluate("window.__was = state.availsData.L1.forecast_snapshot; window.__wasText = JSON.stringify(window.__was)")
    page.evaluate("state.lineItems[0].monthly_budget = 4000; renderAvailsGrid()")
    click(page, ".constanza-block .cz-recalc")
    settle(page)
    assert page.evaluate("state.availsData.L1.forecast_snapshot !== window.__was && JSON.stringify(window.__was) === window.__wasText")


# =================================================================================================================================
# What a line's estimate is filed under
# =================================================================================================================================

def test_an_answer_for_a_product_the_line_no_longer_has_is_neither_filed_nor_shown(open_step5):
    page = open_step5()
    seed_snapshot(page)
    saved = state_of(page)["forecast_snapshot"]
    page.world.hold.add("estimate")
    click(page, ".constanza-block .cz-recalc")
    page.wait_for_function("() => !!document.querySelector('.cz-pulling')")
    page.evaluate(f"state.lineItems[0].product_name = {json.dumps(TRAFFIC)}; renderAvailsGrid()")
    page.world.release("estimate")
    settle(page)
    assert page.world.estimates[-1]["product_name"] == AWARENESS
    assert state_of(page)["forecast_snapshot"] == saved                              # the Traffic line did not get an Awareness estimate
    assert "the product changed" in page.inner_text("#constanza-result-L1 .cz-stale")
    page.wait_for_timeout(200)
    assert nothing_said(page)                                                        # ... and an estimate that was not shown is not announced as ready


def test_an_answer_for_a_line_that_was_deleted_leaves_no_entry_behind(open_step5):
    page = open_step5()
    seed_snapshot(page)
    page.world.hold.add("estimate")
    click(page, ".constanza-block .cz-recalc")
    page.wait_for_function("() => !!document.querySelector('.cz-pulling')")
    page.evaluate("state.lineItems = []; delete state.availsData.L1; renderAvailsGrid()")
    page.world.release("estimate")
    settle(page)
    page.wait_for_timeout(100)
    assert page.evaluate("Object.keys(state.availsData)") == []                     # no ghost entry for the line that is gone
    assert page.locator(".constanza-block").count() == 0
    assert nothing_said(page)


def test_an_answer_for_a_data_line_that_was_deleted_meanwhile_is_not_announced_either(open_step5):
    page = open_step5(lines=[{"id": "L1", "product_name": ROKU}, {"id": "L2", "product_name": ROKU}])
    page.world.hold.add("estimate")
    click(page, '.constanza-autocalc-btn[data-lid="L1"]')
    page.wait_for_function("() => _constanzaBusy.L1")
    page.evaluate("state.lineItems = state.lineItems.filter(x => x.id !== 'L1'); renderAvailsGrid()")
    page.world.release("estimate")
    settle(page)
    page.wait_for_timeout(200)
    assert page.locator(".constanza-block").count() == 1 and nothing_said(page)


def test_the_host_saves_nothing_for_a_line_that_is_not_in_the_option_on_the_screen(open_step5):
    page = open_step5()
    snap = {"v": 1, "method": "meta", "saved_at": "2026-11-01T00:00:00Z", "request": None, "result": None, "line": {}}
    out = page.evaluate("snap => [constanzaHost.saveSnapshot('NOPE', snap), constanzaHost.saveSnapshot('L1', snap), constanzaHost.snapshot('NOPE'), constanzaHost.lineFacts('NOPE')]", snap)
    assert out == [False, True, None, None]
    assert page.evaluate("Object.keys(state.availsData)") == ["L1"] and state_of(page)["forecast_snapshot"] == snap
    assert page.evaluate("constanzaHost.saveSnapshot('L1', null)") is True and "forecast_snapshot" not in state_of(page)


def test_two_lines_of_one_product_keep_their_own_estimates(open_step5):
    page = two_lines(open_step5)
    seed_snapshot(page, "L1", gender="female")
    seed_snapshot(page, "L2", geo={"type": "geo_market", "key": "804", "label": "Los Angeles-Long Beach"}, gender="male")
    assert "Los Angeles (TV market)" in card_text(page, "L1") and "Los Angeles-Long Beach" in card_text(page, "L2")
    L1 = state_of(page, "L1")["forecast_snapshot"]
    page.evaluate("state.lineItems[1].monthly_budget = 5000; renderAvailsGrid()")
    assert page.locator("#constanza-result-L1 .cz-stale").count() == 0 and "the budget changed" in page.inner_text("#constanza-result-L2 .cz-stale")
    click(page, '.constanza-block[data-lid="L2"] .cz-recalc')
    settle(page)
    assert page.world.estimates[-1]["meta"]["geo"]["key"] == "804" and page.world.estimates[-1]["meta"]["gender"] == "male"
    assert state_of(page, "L1")["forecast_snapshot"] == L1                           # the other line's estimate was not touched
    assert page.locator("#constanza-result-L2 .cz-stale").count() == 0


def test_a_line_only_reads_an_estimate_of_its_own_method_and_never_deletes_another(open_step5):
    page = open_step5(lines=[{"id": "L1", "product_name": ROKU}, {"id": "L2", "product_name": AWARENESS}])
    seed_snapshot(page, "L2")
    saved = state_of(page, "L2")["forecast_snapshot"]
    page.evaluate("snap => { state.availsData.L1 = { forecast_snapshot: snap }; renderAvailsGrid(); }", saved)       # a Meta estimate on a platform-data line
    assert page.is_hidden("#constanza-result-L1") and page.evaluate("constanzaHost.snapshot('L1')") is None
    assert state_of(page, "L1")["forecast_snapshot"] == saved                         # kept in the data, never drawn, never deleted
    page.evaluate("state.productIndex[%s].forecast.method = 'platform'; renderAvailsGrid()" % json.dumps(AWARENESS))     # the Admin mapping moved L2 away from Meta
    assert page.evaluate("constanzaHost.snapshot('L2')") is None and state_of(page, "L2")["forecast_snapshot"] == saved


def test_the_newer_of_the_saved_shape_and_the_older_one_is_the_one_drawn(open_step5):
    page = open_step5()
    seed_snapshot(page)
    native = state_of(page)["forecast_snapshot"]
    older = legacy_entry(page)
    older["as_of"] = "2020-01-01T00:00:00+00:00"
    put_avails(page, {"L1": {"forecast_snapshot": native, "meta_forecast": older}})
    assert f"{native['result']['est_uniques']:,}" in card_text(page) and "legacy" not in (page.evaluate("constanzaHost.snapshot('L1')") or {})
    older["as_of"] = "2099-01-01T00:00:00+00:00"                                       # a tab from before the update saved this one later
    put_avails(page, {"L1": {"forecast_snapshot": native, "meta_forecast": older}})
    assert page.evaluate("constanzaHost.snapshot('L1').legacy") is True and insert_label(5_500_000) in buttons(page)


# =================================================================================================================================
# Insert
# =================================================================================================================================

def test_insert_writes_the_mid_case_the_lower_end_and_the_5_5_frequency_and_keeps_the_estimate(open_step5):
    page = open_step5()
    data = seed_snapshot(page)
    saved = state_of(page)["forecast_snapshot"]
    mid, lower = data["meta"]["monthly"]["mid"], data["est_uniques"]
    click(page, ".constanza-block .cz-apply")
    got = state_of(page)
    assert got["max_imps"] == mid and got["max_imps_estimated"] is True and got["basis"] == "imps"
    assert got["est_uniques"] == lower and got["frequency"] == round(mid / lower * 10) / 10 == 5.5
    assert got["max_spend"] == round(mid * 12 / 1000) and got["max_spend_estimated"] is True
    assert got["geo_resolution"] == {"method": "meta_audience", "dmas": ["Los Angeles"], "unmatched_tokens": [], "win_rate": None, "hispanic_share": None}
    assert "win_rate" not in got and "hispanic_share" not in got and "meta_forecast" not in got
    assert got["forecast_snapshot"] == saved                                            # the estimate behind these numbers stays on the line
    assert buttons(page)[0].startswith("Insert the mid-case") and page.locator("#constanza-result-L1 .cz-stale").count() == 0
    assert page.evaluate("document.activeElement.classList.contains('cz-apply')")        # the keyboard goes back to the card the planner was working in
    page.wait_for_function("() => (document.getElementById('constanza-live-announce') || {}).textContent")
    assert f"{mid:,} impressions a month and {lower:,} unique IDs" in page.inner_text("#constanza-live-announce")


def test_what_the_page_saves_for_a_line_survives_the_servers_model_of_an_entry(open_step5):
    """The page sends availsData whole; AvailsEntry keeps only what it declares. The estimate behind a line, and an older shape still on it, come back unchanged."""
    page = open_step5()
    seed_snapshot(page)
    click(page, ".constanza-block .cz-apply")
    mine = state_of(page)
    kept = main.AvailsEntry(**mine).model_dump()
    assert kept["forecast_snapshot"] == mine["forecast_snapshot"] and kept["max_imps"] == mine["max_imps"] and kept["geo_resolution"] == mine["geo_resolution"]
    older = legacy_entry(page)
    assert main.AvailsEntry(**{"meta_forecast": older, "max_imps": 1}).model_dump()["meta_forecast"] == older


def test_insert_from_an_older_saved_estimate_leaves_it_exactly_as_saved(open_step5):
    page = open_step5()
    saved = legacy_entry(page)
    put_avails(page, {"L1": {"meta_forecast": saved}})
    click(page, ".constanza-block .cz-apply")
    got = state_of(page)
    assert (got["max_imps"], got["est_uniques"], got["frequency"]) == (5_500_000, 1_000_000, 5.5)
    assert got["geo_resolution"]["method"] == "meta_audience" and got["geo_resolution"]["dmas"] == ["Los Angeles · California · US"]
    assert got["meta_forecast"] == saved and "forecast_snapshot" not in got            # an older shape is read, never rewritten until the planner estimates again
    assert insert_label(5_500_000) in buttons(page)                                      # and the card is still there


def test_insert_from_an_estimate_entered_by_hand_names_the_audience_as_entered(open_step5):
    page = open_step5()
    put_avails(page, {"L1": {"meta_forecast": legacy_entry(page, "manual")}})
    click(page, ".constanza-block .cz-apply")
    assert state_of(page)["geo_resolution"]["dmas"] == ["entered audience"] and state_of(page)["est_uniques"] == 1_000_000


def test_a_budget_only_older_estimate_offers_no_insert(open_step5):
    page = open_step5()
    put_avails(page, {"L1": {"meta_forecast": legacy_entry(page, "budget-only")}})
    assert page.locator(".constanza-block .cz-apply").count() == 0


# =================================================================================================================================
# The dock opened for a line
# =================================================================================================================================

def test_estimate_in_constanza_opens_the_dock_for_the_line_with_meta_choosing_the_place(open_step5):
    page = open_step5()
    click(page, ".constanza-block .constanza-line-dock-btn")
    page.wait_for_selector("#constanza-dock:not(.hidden)")
    assert page.evaluate("Constanza._state.lineId") == "L1" and "Step 05 line" in page.inner_text("#cz-sub")
    assert page.is_hidden("#cz-sec-where") and page.is_visible("#cz-mt-sec-where")           # the shared Where card gives way to Meta's own


def test_a_pull_from_the_dock_for_the_lines_calendar_months_is_filed_fresh_under_the_line(open_step5):
    """A flight of 22 days that touches 2 calendar months: the dock's own flight box says 1 month, the line says 2. The pull sends the line's, the
    estimate records it, and the card reads fresh; once the planner edits the flight box, the box rules."""
    page = open_step5(start="2026-01-20", end="2026-02-10")
    seed_snapshot(page)
    assert facts(page)["months"] == 2 and facts(page)["flight_days"] == 22
    open_dock(page)
    pull(page)
    sent = page.world.estimates[-1]
    assert sent["months"] == 2 and sent["geo_text"] == "" and sent["meta"]["retail_budget"] == 2000
    saved = state_of(page)["forecast_snapshot"]
    assert saved["line"]["months"] == saved["request"]["months"] == 2
    assert page.locator("#constanza-result-L1 .cz-stale").count() == 0 and buttons(page)[0].startswith("Insert the mid-case")
    page.fill("#cz-flight", "95")                           # the box is in days once a line opened it: 95 days is 3 months, whatever the line's dates say
    page.dispatch_event("#cz-flight", "input")
    pull(page)
    assert page.world.estimates[-1]["months"] == 3


def test_insert_from_the_dock_goes_to_the_chosen_line_and_takes_the_estimate_off_a_line_it_was_not_pulled_for(open_step5):
    page = two_lines(open_step5)
    seed_snapshot(page, "L1")
    seed_snapshot(page, "L2", gender="male")
    other = state_of(page, "L2")["forecast_snapshot"]
    open_dock(page, "L1")
    pull(page)
    page.wait_for_selector('.cz-insert[data-lid="L2"]')
    click(page, '.cz-insert[data-lid="L2"]')
    got = state_of(page, "L2")
    assert got["max_imps"] and got["est_uniques"] and "forecast_snapshot" not in got and "meta_forecast" not in got           # the numbers are not its estimate's any more
    assert "Meta needs an audience" in card_text(page, "L2") and buttons(page, "L2") == ["Estimate in Constanza →"]
    assert state_of(page, "L1")["forecast_snapshot"]["saved_at"] != other["saved_at"]                                       # the pull itself was filed under L1
    click(page, '.cz-insert[data-lid="L1"]')
    assert state_of(page, "L1")["forecast_snapshot"]["method"] == "meta" and state_of(page, "L1")["max_imps"]
    assert buttons(page, "L1")[0].startswith("Insert the mid-case")


# =================================================================================================================================
# A data product and a TikTok line work as they always did
# =================================================================================================================================

def test_a_platform_line_pulls_from_the_proposals_geography_and_files_no_estimate(open_step5):
    page = open_step5(lines=[{"id": "L1", "product_name": ROKU}])
    click(page, ".constanza-autocalc-btn")
    page.wait_for_selector("#constanza-result-L1 .constanza-range-table")
    sent = page.world.estimates[-1]
    assert sent["product_name"] == ROKU and sent["geo_text"] == "Los Angeles" and sent["months"] == 2 and "meta" not in sent and "tiktok" not in sent
    assert sent["national_monthly_total"] and sent["flight_days"] == 61
    assert "forecast_snapshot" not in (state_of(page) or {}) and page.locator(".constanza-autocalc-btn[disabled]").count() == 0
    assert "Apply monthly mid-case" in card_text(page) and "Recalculate" not in card_text(page)
    click(page, ".constanza-block .cz-apply")
    got = state_of(page)
    assert got["max_imps"] and got["max_imps_estimated"] is True and "forecast_snapshot" not in got


def test_a_tiktok_line_pulls_from_the_proposals_geography_and_files_no_estimate(open_step5):
    page = open_step5(lines=[{"id": "L1", "product_name": TIKTOK}])
    click(page, ".constanza-autocalc-btn")
    page.wait_for_selector("#constanza-result-L1 .constanza-range-table")
    sent = page.world.estimates[-1]
    assert sent["product_name"] == TIKTOK and sent["geo_text"] == "Los Angeles" and "meta" not in sent and "tiktok" not in sent
    assert "forecast_snapshot" not in (state_of(page) or {}) and "TikTok" in card_text(page)


def test_a_data_products_geography_trouble_still_offers_the_places_it_might_have_meant(open_step5):
    """The real routes answer both: /estimate refuses a misspelled place and /resolve names the places it might have meant. The card draws them as chips, and a
    chip re-estimates with the proposal's own text replaced by that pick."""
    page = open_step5(lines=[{"id": "L1", "product_name": ROKU}], geo="Phoneix")
    click(page, ".constanza-autocalc-btn")
    page.wait_for_selector("#constanza-result-L1 .cz-geohelp .cz-sugg")
    assert "resolve" in page.world.hits and "Could not resolve geo" in card_text(page) and "Phoneix" in card_text(page)
    chips = page.eval_on_selector_all("#constanza-result-L1 .cz-geohelp .cz-sugg", "els => els.map(e => e.textContent.replace(/\\s+/g, ' ').trim())")
    assert chips and chips[0].startswith("Phoenix, AZ")
    assert "Did you mean" in card_text(page)
    click(page, "#constanza-result-L1 .cz-geohelp .cz-sugg")                                  # the first pick: Phoenix, AZ
    page.wait_for_selector("#constanza-result-L1 .constanza-range-table")
    sent = page.world.estimates[-1]
    assert sent["product_name"] == ROKU and sent["geo_text"] == "Phoenix, AZ"                 # the typed word replaced by the pick
    assert "Geography for this line: Phoenix, AZ" in card_text(page)
    assert page.evaluate("state.constanzaGeoOverride") == {"L1": "Phoenix, AZ"}


# =================================================================================================================================
# A saved estimate that cannot be trusted, and a method that did not load
# =================================================================================================================================

@pytest.mark.parametrize("name", list(meta_snapshots.corrupt_variants()))
def test_a_corrupt_or_hostile_saved_estimate_takes_nothing_else_down_and_runs_nothing(open_step5, name):
    page = open_step5(lines=[{"id": "L1", "product_name": AWARENESS}, {"id": "L2", "product_name": AWARENESS}, {"id": "L3", "product_name": ROKU}])
    page.allowed = ["[constanza]"]                                                      # the page says once that it could not read it
    seed_snapshot(page, "L2")
    broken = meta_snapshots.corrupt_variants()[name]
    page.evaluate("snap => { state.availsData.L1 = { forecast_snapshot: snap, max_imps: 123 }; renderAvailsGrid(); }", broken)
    assert page.locator(".constanza-block").count() == 3                               # Step 05 was drawn
    assert "accounts at the lower end" in card_text(page, "L2")                        # the good line next to it too
    assert page.evaluate("window.__pwned === undefined") and page.locator("#constanza-result-L1 img").count() == 0
    l1 = page.inner_text(".constanza-block[data-lid=\"L1\"]")
    assert "Estimate in Constanza" in l1 or "Recalculate" in l1                         # whatever it drew, the planner can estimate again
    assert state_of(page, "L1")["max_imps"] == 123


def test_an_unreadable_saved_estimate_says_so_in_plain_words_and_offers_the_way_forward(open_step5):
    page = open_step5()
    page.allowed = ["[constanza]"]
    page.evaluate("state.availsData.L1 = { forecast_snapshot: { v: 1, method: 'meta', saved_at: 'x', request: null, result: {}, line: {} } }; renderAvailsGrid()")
    assert "couldn’t be read — estimate again" in card_text(page)
    click(page, "#constanza-result-L1 .cz-refine")
    page.wait_for_selector("#constanza-dock:not(.hidden)")
    assert page.evaluate("Constanza._state.lineId") == "L1"


def test_a_product_whose_method_did_not_load_says_so_and_has_no_buttons(open_step5):
    page = open_step5(lines=[{"id": "L1", "product_name": UNLOADED}, {"id": "L2", "product_name": AWARENESS}])
    assert buttons(page, "L1") == []
    assert "estimate source didn’t load" in card_text(page, "L1")
    assert buttons(page, "L2") == ["Estimate in Constanza →"]


def test_telling_the_host_that_something_every_card_reads_changed_redraws_every_card_but_not_one_that_is_waiting(open_step5):
    """What a connection answer is: it arrives after the cards were drawn, and every Meta card should then hold Recalculate off (`onDirty(null)`)."""
    page = two_lines(open_step5, connected=False)
    page.world.hold.add("meta-status")                                    # the answer comes after both cards are drawn
    seed_snapshot(page, "L1")
    seed_snapshot(page, "L2")
    for _ in range(60):
        if "meta-status" in page.world.hits:
            break
        page.wait_for_timeout(50)
    page.world.release("meta-status")
    page.wait_for_timeout(200)
    held = lambda lid: page.get_attribute(f'.constanza-block[data-lid="{lid}"] .cz-recalc', "aria-disabled")      # noqa: E731
    assert held("L1") == "true" and held("L2") == "true"                # the dock's `refreshGo` passed the answer on to the cards drawn before it
    page.evaluate("document.querySelectorAll('.constanza-block .cz-recalc').forEach(b => b.removeAttribute('aria-disabled'))")
    assert held("L1") is None and held("L2") is None                    # (put back by hand, so the call below is what is under test)
    page.evaluate("constanzaHost.onDirty(null)")
    assert held("L1") == "true" and held("L2") == "true"
    page.evaluate("_constanzaBusy.L2 = true; document.querySelector('#constanza-result-L2').insertAdjacentHTML('afterbegin', '<p id=\"waiting\">Asking again…</p>')")
    page.evaluate("constanzaHost.onDirty(null)")
    assert page.locator("#waiting").count() == 1                         # a card that is showing its wait is left alone
    page.evaluate("_constanzaBusy.L2 = false")


def test_a_redraw_of_one_card_keeps_the_keyboard_where_it_was_and_ignores_a_line_that_is_not_drawn(open_step5):
    page = open_step5()
    seed_snapshot(page)
    page.focus(".constanza-block .cz-refine")
    page.evaluate("constanzaHost.onDirty('L1'); constanzaHost.onDirty('NOT-A-LINE')")
    assert page.evaluate("document.activeElement.classList.contains('cz-refine')")


# =================================================================================================================================
# What the page says about a line, and the one function that writes an estimate
# =================================================================================================================================

LINE_FACT_KEYS = {"id", "product_name", "method", "label", "months", "flight_days", "has_dates", "geo_text", "target_text", "budget", "cpm", "hispanic"}


def test_line_facts_are_always_complete_and_say_what_step_04_knows(open_step5):
    page = open_step5(lines=[{"id": "L1", "product_name": AWARENESS, "months": 3, "monthly_budget": 1500}])
    f = facts(page)
    assert set(f) == LINE_FACT_KEYS
    assert (f["id"], f["product_name"], f["method"], f["months"], f["flight_days"], f["has_dates"]) == ("L1", AWARENESS, "meta", 2, 61, True)
    assert (f["geo_text"], f["target_text"], f["cpm"], f["hispanic"]) == ("Los Angeles", "Adults 25-54", 12, None)
    assert f["budget"] == 4500                                                        # the WHOLE flight: Step 04's budget x the periods it is entered for


def test_line_facts_follow_the_line_the_option_and_the_planners_overrides(open_step5):
    page = open_step5(lines=[{"id": "L1", "product_name": ROKU, "months": 6, "monthly_budget": 1000, "estimated_cpm_override": 25}], start="", end="")
    f = facts(page)
    assert f["has_dates"] is False and f["flight_days"] is None and f["months"] == 6              # no dates: the line's own count stands in
    assert f["method"] == "platform" and f["cpm"] == 25 and f["budget"] == 6000
    page.evaluate("state.constanzaGeoOverride = { L1: 'Chicago' }; state.constanzaHispanic = { L1: true }")
    f = facts(page)
    assert f["geo_text"] == "Chicago" and f["hispanic"] is True                                    # the line's own pick, and only data products are asked about Hispanic
    page.evaluate("state.timeUnit = 'full_flight'; state.lineItems[0].months = 1")
    assert facts(page)["budget"] == 1000


def test_apply_constanza_estimate_files_what_it_is_given_and_drops_what_it_no_longer_describes(open_step5):
    page = open_step5()
    snap = {"v": 1, "method": "meta", "saved_at": "2026-11-01T00:00:00Z", "request": None, "result": None, "line": {}}
    older = {"metric_type": "x"}
    run = """([args, entry]) => { state.availsData.L1 = entry; const ok = applyConstanzaEstimate(...args);
                                  return [ok, JSON.parse(JSON.stringify(state.availsData.L1))]; }"""
    geo = {"method": "meta_audience", "dmas": ["Los Angeles"], "unmatched_tokens": [], "win_rate": None, "hispanic_share": None}
    ok, got = page.evaluate(run, [["L1", 5_500_000, 1_000_000, geo, snap], {"meta_forecast": older}])
    assert ok is True and got["forecast_snapshot"] == snap and "meta_forecast" not in got and got["frequency"] == 5.5            # an object is filed, the older shape goes
    ok, got = page.evaluate(run, [["L1", 5_500_000, 1_000_000, geo, None], {"forecast_snapshot": snap, "meta_forecast": older}])
    assert "forecast_snapshot" not in got and got["meta_forecast"] == older                                                        # null deletes only the filed one
    ok, got = page.evaluate(run, [["L1", 5_500_000, 1_000_000, geo], {"forecast_snapshot": snap, "meta_forecast": older}])
    assert "forecast_snapshot" not in got and "meta_forecast" not in got                                                          # no estimate for THIS line: neither describes the numbers
    ok, got = page.evaluate(run, [["L1", 5_500_000, 1_000_000, geo], {"forecast_snapshot": snap, "win_rate": 0.2, "hispanic_share": 0.3}])
    assert "win_rate" not in got and "hispanic_share" not in got
    assert page.evaluate("applyConstanzaEstimate('GONE', 1, 1, null)") is False and page.evaluate("Object.keys(state.availsData)") == ["L1"]


# =================================================================================================================================
# What a redraw, a saved result and the announcements do to a card (the multi-lens review's findings)
# =================================================================================================================================

def pulled_cards(page, *lids):
    """Presses Auto-calculate on each of those lines and waits for its card (an estimate, with its Insert button, that is on the screen and nowhere else)."""
    for lid in lids:
        click(page, f'.constanza-autocalc-btn[data-lid="{lid}"]')
        page.wait_for_selector(f"#constanza-result-{lid} .cz-apply")
    settle(page)


def spy_on_dirty(page) -> None:
    """Records every `onDirty(lid)` the dock makes (None for "something every card reads changed"), then passes it on to the page's own."""
    page.evaluate("() => { window.__dirty = []; const real = constanzaHost.onDirty; constanzaHost.onDirty = (lid, d) => { __dirty.push(lid); return real(lid, d); }; }")


def wait_for_status_answer(page, count=1) -> None:
    for _ in range(100):
        if page.world.hits.count("meta-status") >= count:
            break
        page.wait_for_timeout(50)
    assert page.world.hits.count("meta-status") >= count, "Meta's connection was never asked about"
    page.wait_for_timeout(300)                                          # (its answer reaches the host a moment later)


def test_a_connection_answer_leaves_a_pulled_platform_card_and_a_pulled_tiktok_card_where_they_are(open_step5):
    """Opening the dock for a Meta line asks whether Meta is connected, and the answer redraws the cards that read it. A Roku or TikTok card reads neither that nor a
    saved estimate: its pulled-but-not-inserted estimate (nothing is saved for it) must still be there, with its Insert button, after the answer."""
    page = open_step5(lines=[{"id": "L1", "product_name": ROKU}, {"id": "L2", "product_name": TIKTOK}, {"id": "L3", "product_name": AWARENESS}])
    pulled_cards(page, "L1", "L2")
    before = {lid: page.inner_html(f"#constanza-result-{lid}") for lid in ("L1", "L2")}
    spy_on_dirty(page)
    open_dock(page, "L3")
    wait_for_status_answer(page)
    assert None in page.evaluate("__dirty")                              # the answer did go out to the page
    page.evaluate("constanzaHost.onDirty(null)")                         # ... and again, by hand
    for lid in ("L1", "L2"):
        assert page.is_visible(f"#constanza-result-{lid}") and page.locator(f"#constanza-result-{lid} .cz-apply").count() == 1, lid
        assert page.inner_html(f"#constanza-result-{lid}") == before[lid]
    click(page, '.constanza-block[data-lid="L1"] .cz-apply')            # and Insert still works from it
    assert state_of(page, "L1")["max_imps"]


def test_a_failed_pull_keeps_its_message_and_the_places_it_suggests_through_a_redraw_of_every_card(open_step5):
    page = open_step5(lines=[{"id": "L1", "product_name": ROKU}, {"id": "L2", "product_name": AWARENESS}], geo="Phoneix")
    click(page, '.constanza-autocalc-btn[data-lid="L1"]')
    page.wait_for_selector("#constanza-result-L1 .cz-geohelp .cz-sugg")
    page.evaluate("constanzaHost.onDirty(null)")
    assert page.locator("#constanza-result-L1 .cz-geohelp .cz-sugg").count() >= 1 and "Could not resolve geo" in card_text(page, "L1")


def test_a_card_that_exists_only_on_the_screen_is_never_redrawn_from_nothing(open_step5):
    """A method that is asked in the dock (autoPull "none") and keeps nothing: a card handed to the page by hand has no saved estimate to be drawn from again."""
    page = open_step5(lines=[{"id": "L1", "product_name": ROKU}])
    page.evaluate("p => { window.__PRODUCTS['Probe SKU'] = p; }", _product("Social", "probe_card", cpm=9))
    assert page.evaluate("""() => Constanza.registerMethod('probe_card', () => ({ caps: { autoPull: 'none' }, channel: 'probe_card', channelLabel: 'Probe', sourceLabel: 'Probe source',
        formHtml: () => '<div></div>', bind: () => {}, requestPart: () => ({ probe_card: {} }), context: () => ({ summary: [] }), reset: () => {},
        headlineHtml: () => '', bodyHtml: () => '', aboutHtml: () => '', cardHtml: () => '<p class="pc-card">probe card</p>', summaryLines: () => [] }))""")
    setup(page, lines=[{"id": "L1", "product_name": "Probe SKU"}, {"id": "L2", "product_name": "Probe SKU"}], avails={})
    assert page.locator(".pc-card").count() == 0
    page.evaluate("""() => { const data = { method: 'probe_card', product_name: 'Probe SKU', months: 1, imps_low: 1000, imps_mid: 2000, imps_high: 3000, est_uniques: null, notes: [],
                                by_area: [], hispanic_targeting: { default: false, on: false, applies: false }, probe_card: {} };
                              renderConstanzaResult('L1', data); }""")
    assert page.locator("#constanza-result-L1 .pc-card").count() == 1 and page.locator("#constanza-result-L1 .constanza-range-table").count() == 1
    page.evaluate("constanzaHost.onDirty(null)")
    assert page.locator("#constanza-result-L1 .pc-card").count() == 1 and page.is_visible("#constanza-result-L1")      # still there
    assert page.locator("#constanza-result-L2 .pc-card").count() == 0                                                 # (a line with no card is drawn as it always is)


def test_reopening_a_line_with_edits_made_in_the_dock_still_describes_its_saved_result_by_what_it_was_pulled_with(open_step5):
    """The card's Change in Constanza on a Meta line whose dock workspace was edited and never pulled: Meta keeps the edit, the dock shows the SAVED result with the
    banner and no Insert - and the header under the product, 'What was asked of Meta' and the copied summary say what that result was asked, not what the form says now."""
    page = open_step5()
    seed_snapshot(page, gender="female", age_min=25, age_max=44)
    open_dock(page)
    assert page.inner_text(".cz-card-geo").strip() == "Los Angeles"
    wait_for_status_answer(page)                                         # (Meta's connection answer redraws the panel: edit after it, not while it is on its way)
    click(page, '#cz-mt-gender [data-gender="male"]')                    # an audience and a place that are never pulled
    page.fill("#cz-mt-geo-q", "Los Angeles")
    page.wait_for_selector("#cz-mt-geo-l .aud-ta-opt")
    page.evaluate("document.querySelectorAll('#cz-mt-geo-l .aud-ta-opt')[1].click()")      # Los Angeles-Long Beach, the other market
    assert page.evaluate("Constanza._plugin('meta')._pure.sel().geo.label") != "Los Angeles"
    page.evaluate("Constanza.close()")
    open_dock(page)                                                      # Change in Constanza, with the edits still in the dock
    assert page.get_attribute('#cz-mt-gender [data-gender="male"]', "aria-pressed") == "true"      # the planner's edit was kept
    assert "audience in Constanza changed" in page.inner_text("#cz-result .cz-stale") and page.locator("#cz-result .cz-insert").count() == 0
    shown = page.inner_text("#cz-result")
    assert "Long Beach" not in shown and "Men" not in shown and "Women" in shown                   # what is on the screen is the result's own words
    assert page.locator(".cz-card-geo").count() == 0
    copied = page.evaluate("Constanza._pure.summaryText(Constanza._state.result)")
    assert "Long Beach" not in copied and "Men" not in copied and "Women" in copied and "Geography:" not in copied and "Place sent to Meta: Los Angeles" in copied
    assert "audience in Constanza changed" in card_text(page)            # the card behind the dock says the same
    page.evaluate("Constanza.close()")
    click(page, '.constanza-block .cz-refine')                           # ... and once the planner pulls, the result is described by what was pulled
    page.wait_for_selector("#constanza-dock:not(.hidden)")
    pull(page)
    assert "Long Beach" in page.inner_text(".cz-card-geo") and "Men" in page.inner_text("#cz-result")


def test_the_page_drops_an_older_shape_only_when_it_is_a_field_named_like_one(open_step5):
    """constanza.js refuses any other legacy key when a method registers; the page deletes what a key names, so it checks the name again."""
    page = open_step5()
    kept = page.evaluate("""() => {
        const li = state.lineItems[0], real = Constanza.methodState, out = {};
        for (const key of ['max_imps', 'forecast_snapshot', 'est_uniques', 'geo_resolution', 'meta_forecast', 'old_forecast']) {
          Constanza.methodState = (m) => Object.assign(real(m), { legacyKey: key });
          const entry = { max_imps: 1, forecast_snapshot: {}, est_uniques: 2, geo_resolution: {}, meta_forecast: {}, old_forecast: {} };
          _constanzaDropOlderShape(li, entry);
          out[key] = Object.keys(entry).sort();
        }
        Constanza.methodState = real;
        return out; }""")
    everything = ["est_uniques", "forecast_snapshot", "geo_resolution", "max_imps", "meta_forecast", "old_forecast"]
    assert kept["max_imps"] == kept["forecast_snapshot"] == kept["est_uniques"] == kept["geo_resolution"] == everything
    assert kept["meta_forecast"] == [k for k in everything if k != "meta_forecast"] and kept["old_forecast"] == [k for k in everything if k != "old_forecast"]


def test_an_insert_with_no_usable_monthly_figure_writes_nothing(open_step5):
    page = open_step5()
    page.evaluate("state.availsData.L1 = { max_imps: 5500000, est_uniques: 1000000, frequency: 5.5 }")
    before = page.evaluate("JSON.stringify(state.availsData.L1)")
    got = page.evaluate("() => [null, undefined, NaN, Infinity, 'abc', '5500000', 0, -3].map((mid) => [applyConstanzaEstimate('L1', mid, 1, null), JSON.stringify(state.availsData.L1)])")
    assert got == [[False, before]] * 8
    assert page.evaluate("applyConstanzaEstimate('L1', 1000, 50, null)") is True and state_of(page)["max_imps"] == 1000        # (a real figure still goes in)


@pytest.mark.parametrize("damage", ["missing", "null", "text"])
def test_a_saved_estimate_with_no_monthly_figure_is_unreadable_and_cannot_be_inserted(open_step5, damage):
    page = open_step5()
    page.allowed = ["[constanza]"]                                                      # the page says once that it could not read it
    seed_snapshot(page)
    page.evaluate("""d => { const r = state.availsData.L1.forecast_snapshot.result;
        if (d === 'missing') { delete r.imps_low; delete r.imps_mid; delete r.imps_high; } else if (d === 'null') { r.imps_mid = null; } else { r.imps_mid = 'abc'; }
        state.availsData.L1.max_imps = 5500000; renderAvailsGrid(); }""", damage)
    assert "couldn’t be read — estimate again" in card_text(page) and page.locator("#constanza-result-L1 .cz-apply").count() == 0
    assert state_of(page)["max_imps"] == 5500000                                       # what the line holds is untouched
    click(page, "#constanza-result-L1 .cz-refine")
    page.wait_for_selector("#constanza-dock:not(.hidden)")                             # and the way forward is there


def ready_message(page) -> str:
    page.wait_for_function("() => { const el = document.getElementById('constanza-live-announce'); return !!(el && el.textContent); }")
    return page.text_content("#constanza-live-announce")


def test_a_card_that_asks_again_says_when_its_estimate_is_ready_in_a_polite_region_of_the_page(open_step5):
    """Recalculate and Auto-calculate redraw the card (the button that was pressed is gone), so the result is said in the page's own live region, once per estimate."""
    page = open_step5(lines=[{"id": "L1", "product_name": AWARENESS}, {"id": "L2", "product_name": ROKU}])
    seed_snapshot(page, "L1")
    assert page.evaluate("document.getElementById('constanza-live-announce')") is None                 # nothing was said for what was merely drawn
    click(page, '.constanza-block[data-lid="L1"] .cz-recalc')
    settle(page)
    mid = page.evaluate("Math.round(Constanza.summarize(state.availsData.L1.forecast_snapshot.result).month.mid)")
    assert ready_message(page) == f"The estimate for {AWARENESS} is ready: {mid:,} impressions a month."
    assert page.get_attribute("#constanza-live-announce", "role") == "status" and page.get_attribute("#constanza-live-announce", "aria-live") == "polite"
    assert page.evaluate("!document.getElementById('constanza-live-announce').closest('.constanza-block')")      # outside the cards, which are rebuilt
    page.evaluate("document.getElementById('constanza-live-announce').textContent = ''")
    click(page, '.constanza-autocalc-btn[data-lid="L2"]')                                              # a data product says the same
    settle(page)
    said = ready_message(page)
    assert said.startswith(f"The estimate for {ROKU} is ready: ") and said.endswith(" impressions a month.")
    page.evaluate("document.getElementById('constanza-live-announce').textContent = ''")
    page.world.fail["estimate"] = (422, {"detail": "Could not resolve geo 'Los Angeles'."})
    click(page, '.constanza-autocalc-btn[data-lid="L2"]')                                              # a refusal says nothing of the kind (its message is on the card)
    page.wait_for_selector("#constanza-result-L2 .constanza-error")
    page.wait_for_timeout(200)
    assert nothing_said(page)
