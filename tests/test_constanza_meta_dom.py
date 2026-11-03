"""The Meta panel of Step 05 in a REAL browser (headless Chromium through Playwright): what a string test cannot show - which section says what as
the planner chooses, where the keyboard goes, what the pop-up window applies, whether the request is what the chips on the screen say.

The page is a small harness (not the whole app): the real Meta panel code lifted out of app.js (between its "META PANEL: begin / end" markers, plus
escapeHtml / escapeAttr / _targetsHispanic), the real CSS (styles.css, constanza.css, the audience kit, the browse dialog, meta-audience.css) and
the real SegmentBrowser, against a stub `state`. The /api/constanza/meta* routes are answered in the browser by the REAL route functions of
app.main over a fake Graph API shaped like Meta's reference, so the contract the panel relies on (status, geographies, targeting lists, languages,
the forecast and its snapshot) is the server's own. Skipped when Playwright or its Chromium is not installed."""
from __future__ import annotations

import http.server
import json
import re
import threading
import urllib.parse
from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

playwright_sync = pytest.importorskip("playwright.sync_api")

import app.main as main  # noqa: E402
from app.services import constanza_meta as meta  # noqa: E402

STATIC = Path(__file__).resolve().parents[1] / "app" / "static"
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")
PRODUCT = "Facebook & Instagram Ads | Awareness"
TOKEN = "EAAB-dom-test-token-0123456789"
ACCOUNT = "act_7000000000004821"


def _region() -> str:
    start = APP_JS.index("// META PANEL: begin")
    end = APP_JS.index("// META PANEL: end")
    return APP_JS[start:end]


def _fn(name: str) -> str:
    m = re.search(rf"^function {re.escape(name)}\(", APP_JS, re.M)
    assert m, name
    return APP_JS[m.start(): APP_JS.index("\n}\n", m.start()) + 2]


HARNESS = """<!doctype html><html><head><meta charset="utf-8"><title>harness</title>
<link rel="stylesheet" href="/static/styles.css"><link rel="stylesheet" href="/static/constanza.css">
<link rel="stylesheet" href="/static/audience-kit.css"><link rel="stylesheet" href="/static/segment-browser.css"><link rel="stylesheet" href="/static/meta-audience.css"></head>
<body><main style="max-width:1080px;margin:0 auto;padding:20px"><div class="avails-card" id="card"><div class="avails-card-head"><h3>Facebook &amp; Instagram Ads | Awareness</h3></div>
<div id="host"></div></div><div style="height:900px"></div></main>
<script src="/static/segment-browser.js"></script>
<script>
  /*FUNCTIONS*/
  /*REGION*/
  const PRODUCT = %(product)s;
  var state = { lineItems: [], availsData: {}, productIndex: { [PRODUCT]: { meta_retail_cpm: 12 } }, activeTierLabel: "A", parsed: {},
                catalog: { audience_multipliers: { conservative: 4.3, mid: 5.5, upper: 8.6 } } };
  window.__geo = "Los Angeles"; window.__target = "Adults 25-54"; window.__months = 3; window.__dates = true; window.__inserted = null;
  function _mbParseDate(x) { return x ? new Date(x) : null; }
  function _effectiveStartDate() { return window.__dates ? "2026-11-01" : ""; }
  function _effectiveEndDate() { return window.__dates ? "2026-12-31" : ""; }
  function _effectiveProduct() { return { estimated_cpm_for_imps: 12 }; }
  function _constanzaFlightMonths() { return window.__months; }
  function _constanzaGeoFor() { return window.__geo; }
  function _effectiveTargetText() { return window.__target; }
  function _constanzaErrorText(d, f) { const x = d && d.detail; return typeof x === "string" && x ? x : (Array.isArray(x) && x.length ? x.map((e) => e.msg).join("; ") : (f || "Request failed.")); }
  function applyConstanzaEstimate(lid, imps, uniques, info) { window.__inserted = { lid, imps, uniques, info }; renderAvailsGrid(); return true; }
  // what renderAvailsGrid does for the Meta card: build the panel, then bind each one
  function renderAvailsGrid() {
    const host = document.getElementById("host");
    host.innerHTML = state.lineItems.map((li) => _metaForecastPanelHtml(li, state.productIndex[li.product_name] || {}, state.availsData[li.id] || {})).join("");
    host.querySelectorAll(".meta-panel").forEach(_metaBind);
  }
  window.setup = function (cfg) {
    cfg = cfg || {};
    if ("geo" in cfg) window.__geo = cfg.geo;
    if (cfg.target) window.__target = cfg.target;
    if (cfg.months) window.__months = cfg.months;
    if ("dates" in cfg) window.__dates = cfg.dates;
    state.parsed = { demo: "demo" in cfg ? cfg.demo : "Adults 25-54", behavioral: cfg.behavioral || "", geo: window.__geo };
    state.lineItems = cfg.lines || [{ id: "li_1", product_name: PRODUCT, monthly_budget: 2000, months: 1, target_override: cfg.targetOverride }];
    state.availsData = {};
    if (cfg.saved) state.availsData.li_1 = { meta_forecast: cfg.saved };
    renderAvailsGrid();
  };
  // Text colours against their real backgrounds (WCAG AA: 4.5, 3 for large text) for everything under `root` (disabled controls are exempt).
  window.contrastReport = function (root) {
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
    document.querySelectorAll(root + " *").forEach((el) => {
      if (![...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim()) || !el.getClientRects().length) return;
      for (let e = el; e && e !== document.body; e = e.parentElement) {
        const cs = getComputedStyle(e);
        if (parseFloat(cs.opacity) < 1 || e.disabled || e.getAttribute("aria-disabled") === "true" || cs.visibility === "hidden") return;
      }
      const cs = getComputedStyle(el), bg = backdrop(el), fg = over(parse(cs.color), bg);
      const hi = Math.max(lum(fg), lum(bg)), lo = Math.min(lum(fg), lum(bg)), ratio = (hi + 0.05) / (lo + 0.05);
      const size = parseFloat(cs.fontSize), large = size >= 24 || (size >= 18.66 && parseInt(cs.fontWeight, 10) >= 700);
      if (ratio < (large ? 3 : 4.5)) out.push({ text: el.textContent.trim().slice(0, 40), cls: String(el.className), ratio: Math.round(ratio * 100) / 100 });
    });
    return out;
  };
</script></body></html>"""


def harness_html() -> str:
    functions = "\n".join(_fn(name) for name in ("escapeHtml", "escapeAttr", "_targetsHispanic"))
    page = HARNESS.replace("/*FUNCTIONS*/", functions).replace("/*REGION*/", _region())
    return page.replace("%(product)s", json.dumps(PRODUCT))


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


# ----------------------------------------------------------------------------------------------------------------- a fake Graph API

def _interest(i, name, lower, upper, path):
    return {"id": str(6003000000000 + i), "name": name, "type": "interests", "path": path, "audience_size_lower_bound": lower, "audience_size_upper_bound": upper}


class Graph:
    """Meta's Graph API as far as the panel touches it, shaped like its reference and deterministic. The locale ids are INVENTED: the panel must
    take them from the answers, never from its own code."""

    GEOS = [
        {"key": "803", "name": "Los Angeles", "type": "geo_market", "country_code": "US", "region": "California"},
        {"key": "804", "name": "Los Angeles-Long Beach", "type": "geo_market", "country_code": "US"},
        {"key": "753", "name": "Phoenix", "type": "geo_market", "country_code": "US", "region": "Arizona"},
        {"key": "2420379", "name": "Los Angeles", "type": "city", "country_code": "US", "region": "California"},
        {"key": "US:90210", "name": "90210", "type": "zip", "country_code": "US", "region": "California"},
        {"key": "TX", "name": "Texas", "type": "region", "country_code": "US"},
        {"key": "US", "name": "United States", "type": "country", "country_code": "US"},
    ]
    INTERESTS = [
        _interest(1, "Soccer", 120_000_000, 140_000_000, ["Interests", "Sports and outdoors", "Soccer"]),
        _interest(2, "Soccer balls", 4_000_000, 4_800_000, ["Interests", "Shopping and fashion", "Sporting goods"]),
        _interest(3, "Telenovelas", 22_000_000, 26_000_000, ["Interests", "Entertainment", "TV"]),
        _interest(4, "Cooking", 310_000_000, 350_000_000, ["Interests", "Food and drink", "Cooking"]),
        _interest(5, "Reggaeton", 18_000_000, 21_000_000, ["Interests", "Entertainment", "Music"]),
        _interest(6, "Small business", 55_000_000, 65_000_000, ["Interests", "Business and industry"]),
        {"id": "6003000000007", "name": "Mystery interest", "type": "interests", "path": ["Interests", "Other"]},
    ]
    BEHAVIORS = [
        {"id": "6002714895372", "name": "Frequent travelers", "type": "behaviors", "path": ["Behaviors", "Travel"], "audience_size_lower_bound": 2_000_000, "audience_size_upper_bound": 2_400_000},
        {"id": "6004386044572", "name": "Small business owners", "type": "behaviors", "path": ["Behaviors", "Business"], "audience_size_lower_bound": 9_000_000, "audience_size_upper_bound": 11_000_000},
        {"id": "6019964221783", "name": "Soccer fans (engaged)", "type": "behaviors", "path": ["Behaviors", "Sports"], "audience_size_lower_bound": 700_000, "audience_size_upper_bound": 900_000},
    ]
    DEMOGRAPHICS = [
        {"id": "6002714398172", "name": "Newlyweds (1 year)", "type": "life_events", "path": ["Demographics", "Life events", "Newlywed"], "audience_size_lower_bound": 300_000, "audience_size_upper_bound": 400_000},
        {"id": "6008888954983", "name": "Technology", "type": "industries", "path": ["Demographics", "Work", "Industries"], "audience_size_lower_bound": 5_000_000, "audience_size_upper_bound": 6_000_000},
    ]
    LOCALES = [{"key": 6, "name": "English (US)"}, {"key": 24, "name": "English (UK)"}, {"key": 1001, "name": "Spanish"}, {"key": 1002, "name": "Spanish (Mexico)"},
               {"key": 1003, "name": "Spanish (Spain)"}, {"key": 1004, "name": "Portuguese (Brazil)"}, {"key": 1005, "name": "Portuguese (Portugal)"},
               {"key": 1006, "name": "French (France)"}, {"key": 1007, "name": "Tagalog"}, {"key": 1008, "name": "Español"}]

    def __init__(self):
        self.calls: list[dict] = []
        self.reach_error: dict | None = None
        self.geos = [dict(g) for g in self.GEOS]                  # copies: a test may add or remove rows without touching the next test's world
        self.locales = [dict(r) for r in self.LOCALES]

    @staticmethod
    def reach(spec: dict) -> tuple[int, int]:
        """Deterministic: 3M everyone; x span/48 for an age range, x0.5 one gender, x(0.1 + 0.2 per language), x0.5 a detailed-targeting group."""
        factor = 1.0
        if "age_min" in spec or "age_max" in spec:
            factor *= (spec.get("age_max", 65) - spec.get("age_min", 18) + 1) / 48
        if "genders" in spec:
            factor *= 0.5
        if "locales" in spec:
            factor *= 0.1 + 0.2 * len(spec["locales"])
        if "flexible_spec" in spec:
            factor *= 0.5
        lower = int(3_000_000 * factor)
        return lower, int(lower * 1.2)

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": dict(params or {})})
        params = params or {}
        rest = url.split("graph.facebook.com/", 1)[1].split("/", 1)[1]
        if rest.endswith("/reachestimate"):
            if self.reach_error:
                return _Resp({"error": self.reach_error}, 400)
            lower, upper = self.reach(json.loads(params["targeting_spec"]))
            return _Resp({"data": {"users_lower_bound": lower, "users_upper_bound": upper, "estimate_ready": True}})
        kind = params.get("type")
        q = (params.get("q") or "").lower()
        if kind == "adgeolocation":
            wanted = json.loads(params["location_types"])
            return _Resp({"data": [g for g in self.geos if g["type"] in wanted and q in g["name"].lower()]})
        if kind == "adinterest":
            return _Resp({"data": [r for r in self.INTERESTS if q in r["name"].lower()]})
        if kind == "adTargetingCategory":
            return _Resp({"data": {"interests": self.INTERESTS, "behaviors": self.BEHAVIORS, "demographics": self.DEMOGRAPHICS}[params["class"]]})
        if kind == "adlocale":
            rows = [r for r in self.locales if r["name"].lower().startswith(q)]
            return _Resp({"data": rows[: int(params.get("limit", 8))]})
        return _Resp({"error": {"message": "unknown", "code": 100}}, 400)


class _Resp:
    def __init__(self, body, status=200):
        self._body, self.status_code = body, status

    @property
    def ok(self):
        return self.status_code < 400

    def json(self):
        return self._body


class World:
    """What the harness's /api answers: the REAL route functions of app.main over the fake Graph. A test can park an answer (`hold`) and release it
    later, or make a route fail (`fail`)."""

    NAMES = {"api/constanza/meta/status": "status", "api/constanza/meta-geos": "geos", "api/constanza/meta-targeting": "targeting",
             "api/constanza/meta-languages": "languages", "api/constanza/meta-forecast": "forecast"}

    def __init__(self, graph: Graph):
        self.graph = graph
        self.hits: list[str] = []
        self.requests: list[tuple[str, object]] = []         # (route name, JSON body or query dict)
        self.hold: set[str] = set()
        self.pending: list[tuple] = []
        self.fail: dict[str, tuple[int, dict]] = {}

    def of(self, name: str) -> list:
        return [body for n, body in self.requests if n == name]

    def release(self, name: str, newest_first: bool = False) -> None:
        items = [p for p in self.pending if p[0] == name]
        for item in (reversed(items) if newest_first else items):
            self.pending.remove(item)
            item[1].fulfill(status=item[2], content_type="application/json", body=json.dumps(item[3]))

    def answer(self, route) -> None:
        req = route.request
        parsed = urllib.parse.urlparse(req.url)
        path = parsed.path.lstrip("/")
        query = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
        name = self.NAMES.get(path, "other")
        self.hits.append(name)
        body, status = None, 200
        try:
            payload = json.loads(req.post_data) if req.post_data else None
            self.requests.append((name, payload if payload is not None else query))
            if name in self.fail:
                status, body = self.fail[name]
            elif name == "status":
                body = main.constanza_meta_status()
            elif name == "geos":
                body = main.constanza_meta_geos(main.MetaGeoSearchRequest(**payload))
            elif name == "targeting":
                body = main.constanza_meta_targeting(kind=query.get("kind", ""), q=query.get("q", ""))
            elif name == "languages":
                body = main.constanza_meta_languages(q=query.get("q", ""))
            elif name == "forecast":
                body = main.constanza_meta_forecast(main.MetaAuctionForecastRequest(**payload))
            else:
                status, body = 404, {"detail": "not found"}
        except HTTPException as exc:
            status, body = exc.status_code, {"detail": exc.detail}
        except ValidationError as exc:                           # what FastAPI answers for a body its model refuses: 422, one entry per field, `loc` led by "body"
            status, body = 422, {"detail": [{"type": e["type"], "loc": ["body", *e["loc"]], "msg": e["msg"]} for e in exc.errors()]}
        if name in self.hold:
            self.pending.append((name, route, status, body))
            return
        route.fulfill(status=status, content_type="application/json", body=json.dumps(body))


@pytest.fixture
def world(monkeypatch):
    meta.reset_cache()
    monkeypatch.setenv("META_ACCESS_TOKEN", TOKEN)
    monkeypatch.setenv("META_AD_ACCOUNT_ID", ACCOUNT)
    monkeypatch.delenv("META_GRAPH_API_VERSION", raising=False)
    graph = Graph()
    monkeypatch.setattr(meta.requests, "get", graph.get)
    yield World(graph)
    meta.reset_cache()


@pytest.fixture
def open_panel(browser, server, world):
    """`open_panel(**setup)` opens the harness with one Awareness line and the panel drawn. Setup keys: geo, target, demo, months, dates, saved,
    targetOverride, lines, width, height; or `before=fn(world)` to prepare the fake world."""
    opened = []

    def _open(width=1100, height=1000, before=None, **setup):
        if before:
            before(world)
        context = browser.new_context(viewport={"width": width, "height": height})
        page = context.new_page()
        page.route("**/api/**", world.answer)
        page.route("**/fonts.googleapis.com/**", lambda r: r.abort())
        page.route("**/fonts.gstatic.com/**", lambda r: r.abort())
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" and "Failed to load resource" not in m.text else None)
        page.goto(f"{server}/harness.html")
        page.evaluate("cfg => window.setup(cfg)", setup)
        page.wait_for_selector(".meta-panel")
        page.world, page.errors = world, errors                      # type: ignore[attr-defined]
        opened.append((context, errors))
        return page

    yield _open
    for context, errors in opened:
        context.close()
        assert errors == [], errors


@pytest.fixture
def panel(open_panel):
    page = open_panel()
    page.wait_for_selector(".aud-notice--connected")
    return page


# ----------------------------------------------------------------------------------------------------------------- helpers

def sec(key):
    return f'[data-meta-sec="{key}"]'


def summary(page, key):
    return page.inner_text(f"{sec(key)} [data-meta-sum]").strip()


def chips(page, which):
    return page.eval_on_selector_all(f'[data-meta-chips="{which}"] .aud-chip', "els => els.map(e => e.querySelector('.aud-chip-label').textContent)")


def options(page, which):
    return page.eval_on_selector_all(f'[data-meta-ta-list="{which}"] .aud-ta-opt', "els => els.map(e => e.querySelector('.aud-ta-name').textContent)")


def pick_place(page, query="Los Angeles", kind=None):
    if kind:
        page.click(f'[data-meta-act="geotype"][data-type="{kind}"]')
    page.fill('[data-meta-ta-input="geo"]', query)
    page.wait_for_selector('[data-meta-ta-list="geo"] .aud-ta-opt')
    page.click('[data-meta-ta-list="geo"] .aud-ta-opt')


def estimate(page):
    page.click('[data-meta-act="estimate"]')
    page.wait_for_selector(".meta-result .meta-scen")


def meta_result(lower=1_200_000, upper=1_440_000, geo_name="Los Angeles · California · US", languages=(), detailed=(), age_min=25, age_max=44, gender="female",
                source="meta_api", geo_type="geo_market", geo_key="803", with_targeting=True, **extra):
    out = meta.model_auction(product_name=PRODUCT, retail_budget=2000, audience_lower=lower, audience_upper=upper, months=3, audience_source=source, retail_cpm=12)
    if with_targeting:
        out["meta_targeting"] = {"geo_type": geo_type, "geo_key": geo_key, "geo_name": geo_name, "age_min": age_min, "age_max": age_max, "gender": gender,
                                 "detailed": list(detailed), "languages": list(languages)}
    out.update({"proposal_geo": "Los Angeles", "target_text": "Adults 25-54"})
    out.update(extra)
    return out


def forecast_body(page):
    return page.world.of("forecast")[-1]                              # type: ignore[attr-defined]


# ----------------------------------------------------------------------------------------------------------------- the panel and its four sections

def test_four_numbered_sections_a_plan_strip_and_no_manual_audience_form(panel):
    titles = panel.eval_on_selector_all(".meta-builder .aud-sec-title", "els => els.map(e => e.textContent)")
    assert titles == ["Where", "Who", "Language", "Audience"]
    # the kit numbers the badges by itself (an empty, aria-hidden .aud-step in each section, counted by the CSS)
    assert panel.eval_on_selector_all(".meta-builder .aud-sec .aud-step", "els => els.map(e => [e.textContent, e.getAttribute('aria-hidden')])") == [["", "true"]] * 4
    assert [summary(panel, k) for k in ("where", "who", "lang", "aud")] == ["Choose a place", "All genders · all ages", "Any language", "No interests or behaviors yet"]
    strip = panel.inner_text(".meta-plan")
    assert "$2,000" in strip and "over 3 months" in strip and "$12" in strip and "lower end × 4.3 / 5.5 / 8.6" in strip and "Meta’s defaults" in strip
    assert "default placements (Facebook, Instagram, Messenger, Audience Network)" in panel.inner_text(".meta-plan-note")
    assert panel.inner_text('[data-meta-act="estimate"]') == "Estimate audience"
    # the manual path is gone from the screen: no typed audience size, no "Calculate scenarios", no collapsed "pull from Meta" details
    text = panel.inner_text(".meta-panel")
    assert "Calculate scenarios" not in text and "Audience size, lower end" not in text and "Upper end" not in text
    assert panel.locator(".meta-panel details.meta-live-details, .meta-panel input[type=number]").count() == 0
    assert panel.locator(".meta-panel .aud-sec").count() == 4 and panel.locator(".meta-panel details > summary").count() == 1      # only the language caveat folds


def test_a_line_without_flight_dates_says_the_one_month_is_a_stand_in(open_panel):
    page = open_panel(dates=False, months=1)
    assert "No flight dates on this option: the flight length is not known, so 1 month is assumed." in page.inner_text(".meta-plan-note")
    assert "over 1 month" in page.inner_text(".meta-plan") and "months" not in page.inner_text(".meta-plan")


def test_the_panel_quotes_the_multipliers_the_server_uses(open_panel):
    page = open_panel()
    page.evaluate("state.catalog.audience_multipliers = { conservative: 3, mid: 6, upper: 10 }; renderAvailsGrid()")
    assert "lower end × 3 / 6 / 10" in page.inner_text(".meta-plan") and "audience × 3 (conservative), 6 (mid, the default) or 10 (upper)" in page.inner_text(".meta-plan-note")


# ----------------------------------------------------------------------------------------------------------------- the connection

def test_the_banner_says_connected_with_the_ad_account_hint_and_asks_once_across_redraws(open_panel, world):
    page = open_panel()
    page.wait_for_selector(".aud-notice--connected")
    banner = page.inner_text(".meta-status")
    assert "Connected to Meta" in banner and "…4821" in banner and "v26.0" in banner and TOKEN not in banner
    for _ in range(3):                                               # a redraw of the Avails grid must not ask again (cached ~30 s)
        page.evaluate("renderAvailsGrid()")
    assert "Connected to Meta" in page.inner_text(".meta-status") and world.hits.count("status") == 1
    assert page.is_enabled('[data-meta-act="estimate"]')
    page.evaluate("state.metaStatus.at = Date.now() - 31000; renderAvailsGrid()")           # older than the cache: asked again, once
    page.wait_for_function("() => state.metaStatus && !state.metaStatus.pending && Date.now() - state.metaStatus.at < 5000")
    assert world.hits.count("status") == 2


def test_the_panel_draws_at_once_with_a_checking_banner_while_the_status_is_on_its_way(open_panel, world):
    page = open_panel(before=lambda w: w.hold.add("status"))
    assert "Checking the connection to Meta" in page.inner_text(".meta-status") and page.is_enabled('[data-meta-act="estimate"]')
    world.release("status")
    page.wait_for_selector(".aud-notice--connected")


def test_not_configured_names_what_is_missing_and_switches_the_estimate_off_with_the_reason(open_panel, monkeypatch):
    monkeypatch.delenv("META_ACCESS_TOKEN")
    monkeypatch.delenv("META_AD_ACCOUNT_ID")
    page = open_panel()
    page.wait_for_selector(".meta-status .aud-notice--warn")
    banner = page.inner_text(".meta-status")
    assert "Meta isn’t connected yet" in banner and "META_ACCESS_TOKEN" in banner and "META_AD_ACCOUNT_ID" in banner
    assert page.locator(".meta-status .aud-notice--warn code").count() == 2
    assert page.is_disabled('[data-meta-act="estimate"]')
    reason = page.inner_text("[data-meta-reason]")
    assert "Meta isn't connected yet" in reason and "META_ACCESS_TOKEN and META_AD_ACCOUNT_ID" in reason and page.is_visible("[data-meta-reason]")
    assert page.get_attribute('[data-meta-act="estimate"]', "aria-describedby") == page.get_attribute("[data-meta-reason]", "id")
    # the controls that would only ask Meta (and get this same answer) are off; age and gender are local and stay usable
    assert page.is_disabled('[data-meta-ta-input="geo"]') and page.is_disabled('[data-meta-act="browse"]') and page.is_disabled('[data-meta-act="lang-quick"][data-key="spanish"]')
    assert page.is_enabled('[data-meta-act="gender"][data-gender="female"]')
    page.click("text=How an admin sets this up")
    assert "META_ACCESS_TOKEN" in page.inner_text(".meta-status details p") and "Replit" in page.inner_text(".meta-status details p")
    assert [n for n in page.world.hits if n == "forecast"] == []      # type: ignore[attr-defined]


def test_an_unreachable_status_check_says_so_and_can_be_asked_again(open_panel, world):
    page = open_panel(before=lambda w: w.fail.update({"status": (503, {"detail": "The server is waking up."})}))
    page.wait_for_selector(".meta-status .aud-notice--warn")
    assert "Couldn’t check the Meta connection" in page.inner_text(".meta-status") and "The server is waking up." in page.inner_text(".meta-status")
    assert page.is_enabled('[data-meta-act="estimate"]')            # unknown is not "off": the estimate then tells its own error
    world.fail.clear()
    page.click('[data-meta-act="recheck"]')
    page.wait_for_selector(".aud-notice--connected")


# ----------------------------------------------------------------------------------------------------------------- 1 Where

def test_the_proposals_geography_is_a_one_click_search_not_an_automatic_one(open_panel, world):
    page = open_panel(geo="Phoenix, AZ and Los Angeles DMA")
    page.wait_for_selector(".aud-notice--connected")
    pills = page.eval_on_selector_all("[data-meta-geosuggest] button", "els => els.map(e => e.textContent)")
    assert pills == ["Search “Phoenix”", "Search “Los Angeles”"]
    for _ in range(2):
        page.evaluate("renderAvailsGrid()")
    assert world.hits.count("geos") == 0                              # nothing asked of Meta until the planner asks
    page.click('[data-meta-geosuggest] button >> text=Los Angeles')
    page.wait_for_selector('[data-meta-ta-list="geo"] .aud-ta-opt')
    assert world.of("geos") == [{"query": "Los Angeles", "location_type": "geo_market"}]
    assert page.input_value('[data-meta-ta-input="geo"]') == "Los Angeles"
    assert options(page, "geo") == ["Los Angeles", "Los Angeles-Long Beach"]
    assert page.evaluate("document.activeElement === document.querySelector('[data-meta-ta-input=\"geo\"]')")


def test_no_pill_when_the_proposal_names_no_place(open_panel):
    page = open_panel(geo="TBD")
    assert page.locator("[data-meta-geosuggest]").count() == 0


def test_picking_a_place_shows_a_chip_with_its_type_and_the_summary_names_it(panel):
    pick_place(panel)
    assert chips(panel, "geo") == ["Los Angeles"]
    assert panel.inner_text('[data-meta-chips="geo"] .aud-chip-meta') == "TV market · California"
    assert summary(panel, "where") == "Los Angeles · TV market"
    assert panel.get_attribute(f'{sec("where")}', "class").count("has-value") == 1 and panel.is_visible(f'{sec("where")} .aud-clear')
    assert panel.is_hidden('[data-meta-ta-list="geo"]') and panel.input_value('[data-meta-ta-input="geo"]') == ""
    assert panel.is_hidden("[data-meta-geosuggest]")                  # the shortcut is not needed once a place is chosen
    panel.click('[data-meta-chips="geo"] .aud-chip-x')
    assert chips(panel, "geo") == [] and summary(panel, "where") == "Choose a place"
    assert panel.evaluate("document.activeElement === document.querySelector('[data-meta-ta-input=\"geo\"]')")


def test_the_kind_of_place_decides_what_is_searched_and_what_is_chosen_keeps_its_own_type(panel, world):
    pick_place(panel, "Los Angeles", kind="city")
    assert world.of("geos")[-1] == {"query": "Los Angeles", "location_type": "city"}
    assert summary(panel, "where") == "Los Angeles · City"
    assert panel.get_attribute('[data-meta-act="geotype"][data-type="city"]', "aria-pressed") == "true"
    assert "City, e.g." in panel.get_attribute('[data-meta-ta-input="geo"]', "placeholder")
    panel.click('[data-meta-act="geotype"][data-type="zip"]')         # switching the kind does not drop what was chosen
    assert summary(panel, "where") == "Los Angeles · City" and "ZIP code" in panel.get_attribute('[data-meta-ta-input="geo"]', "placeholder")
    estimate(panel)
    assert forecast_body(panel)["meta_geo_type"] == "city" and forecast_body(panel)["meta_geo_key"] == "2420379"


def test_the_place_search_works_from_the_keyboard(panel):
    panel.focus('[data-meta-ta-input="geo"]')
    panel.keyboard.type("los")
    panel.wait_for_selector('[data-meta-ta-list="geo"] .aud-ta-opt')
    assert panel.get_attribute('[data-meta-ta-input="geo"]', "aria-expanded") == "true"
    panel.keyboard.press("ArrowDown")
    panel.keyboard.press("ArrowDown")
    active = panel.get_attribute('[data-meta-ta-input="geo"]', "aria-activedescendant")
    assert panel.inner_text(f"#{active} .aud-ta-name") == "Los Angeles-Long Beach" and panel.get_attribute(f"#{active}", "aria-selected") == "true"
    panel.keyboard.press("Escape")                                    # closes the list, nothing else
    assert panel.is_hidden('[data-meta-ta-list="geo"]') and panel.get_attribute('[data-meta-ta-input="geo"]', "aria-expanded") == "false"
    panel.keyboard.press("ArrowDown")                                 # opens it again, on the same hits
    assert panel.is_visible('[data-meta-ta-list="geo"]')
    panel.keyboard.press("ArrowDown")
    panel.keyboard.press("Enter")
    assert summary(panel, "where") == "Los Angeles-Long Beach · TV market"
    assert panel.evaluate("document.activeElement === document.querySelector('[data-meta-ta-input=\"geo\"]')")


def test_a_place_that_meta_does_not_know_and_a_failed_search_say_so_in_the_list(panel, world):
    panel.fill('[data-meta-ta-input="geo"]', "zzzz")
    panel.wait_for_selector("[data-meta-ta-list='geo'] >> text=Meta found no matching place")
    assert "Comscore" in panel.inner_text('[data-meta-ta-list="geo"]')
    world.fail["geos"] = (503, {"detail": "Meta rejected the access token."})
    panel.fill('[data-meta-ta-input="geo"]', "phoenix")
    panel.wait_for_selector("[data-meta-ta-list='geo'] >> text=Meta rejected the access token.")
    panel.fill('[data-meta-ta-input="geo"]', "p")
    assert "at least two letters" in panel.inner_text('[data-meta-ta-list="geo"]')


def test_a_slow_answer_for_an_earlier_search_never_replaces_the_newer_one(open_panel, world):
    page = open_panel()
    page.wait_for_selector(".aud-notice--connected")
    world.hold.add("geos")
    page.fill('[data-meta-ta-input="geo"]', "Los Angeles")
    page.wait_for_timeout(450)
    page.fill('[data-meta-ta-input="geo"]', "Phoenix")
    page.wait_for_timeout(450)
    assert len(world.pending) == 2
    world.release("geos", newest_first=True)                          # Phoenix answers first, then the older Los Angeles one
    page.wait_for_timeout(300)
    assert options(page, "geo") == ["Phoenix"]


def test_a_place_name_with_markup_is_shown_as_text(panel, world):
    meta_geo = {"key": "9", "name": "<img src=x onerror=alert(1)>", "type": "geo_market", "country_code": "US", "region": "<b>CA</b>"}
    world.graph.geos.append(meta_geo)
    pick_place(panel, "<img")
    assert panel.locator(".meta-panel img").count() == 0 and panel.locator(".meta-panel b").count() == 0
    assert chips(panel, "geo") == ["<img src=x onerror=alert(1)>"]


# ----------------------------------------------------------------------------------------------------------------- 2 Who

def test_age_and_gender_choices_update_the_summary_and_the_presets(panel):
    panel.select_option('[data-meta-age="min"]', "25")
    panel.select_option('[data-meta-age="max"]', "44")
    panel.click('[data-meta-act="gender"][data-gender="female"]')
    assert summary(panel, "who") == "Women · 25–44"
    assert panel.get_attribute('[data-meta-act="gender"][data-gender="female"]', "aria-pressed") == "true"
    assert panel.get_attribute('[data-meta-act="gender"][data-gender="all"]', "aria-pressed") == "false"
    assert panel.inner_text(f'{sec("who")} .aud-count') == "2"
    panel.click('[data-meta-act="age-preset"] >> text=55+')
    assert summary(panel, "who") == "Women · 55+" and panel.input_value('[data-meta-age="min"]') == "55" and panel.input_value('[data-meta-age="max"]') == "65"
    assert panel.get_attribute('[data-meta-act="age-preset"] >> text=55+', "aria-pressed") == "true"
    assert panel.inner_text('[data-meta-age="max"] option[value="65"]') == "65+"
    panel.click('[data-meta-act="gender"][data-gender="male"]')
    assert summary(panel, "who") == "Men · 55+"
    panel.click(f'{sec("who")} .aud-clear')                           # clears both, and the keyboard goes to the section, not to the page
    assert summary(panel, "who") == "All genders · all ages" and panel.is_hidden(f'{sec("who")} .aud-clear')
    assert panel.evaluate("document.activeElement && document.activeElement.closest('[data-meta-sec=\"who\"]') !== null")


def test_the_oldest_age_cannot_be_set_below_the_youngest(panel):
    panel.select_option('[data-meta-age="min"]', "40")
    assert panel.is_disabled('[data-meta-age="max"] option[value="39"]') and panel.is_enabled('[data-meta-age="max"] option[value="40"]')
    panel.select_option('[data-meta-age="max"]', "50")
    assert panel.is_disabled('[data-meta-age="min"] option[value="51"]') and panel.is_enabled('[data-meta-age="min"] option[value="50"]')
    assert summary(panel, "who") == "All genders · 40–50"


# ----------------------------------------------------------------------------------------------------------------- 3 Language

def test_the_spanish_quick_pick_is_looked_up_live_and_takes_every_locale_called_spanish(panel, world):
    panel.click('[data-meta-act="lang-quick"][data-key="spanish"]')
    panel.wait_for_selector('[data-meta-chips="lang"] .aud-chip')
    assert world.of("languages") == [{"q": "Spanish"}]                # asked of Meta by name: no locale id is kept in the page
    assert chips(panel, "lang") == ["Spanish"] and panel.inner_text('[data-meta-chips="lang"] .aud-chip-meta') == "3 versions"
    assert "Spanish (Mexico)" in panel.get_attribute('[data-meta-chips="lang"] .aud-chip', "title")
    assert panel.evaluate("state.metaSel.li_1.languages.map(l => l.id)") == ["1001", "1002", "1003"]      # ids as Meta's list gave them (the invented ones)
    assert summary(panel, "lang") == "Spanish" and panel.get_attribute('[data-meta-act="lang-quick"][data-key="spanish"]', "aria-pressed") == "true"
    assert panel.get_attribute('[data-meta-act="lang-quick"][data-key="english"]', "aria-pressed") == "false"
    assert not any(l.startswith("Español") for l in chips(panel, "lang"))
    panel.click('[data-meta-act="lang-quick"][data-key="spanish"]')    # pressed: a second click takes them out
    assert chips(panel, "lang") == [] and summary(panel, "lang") == "Any language"
    panel.click('[data-meta-act="lang-quick"][data-key="spanish"]')
    panel.wait_for_selector('[data-meta-chips="lang"] .aud-chip')
    assert len(world.of("languages")) == 1                            # remembered for the session: no second lookup


def test_english_and_the_spanish_plus_english_combination(panel, world):
    panel.click('[data-meta-act="lang-quick"][data-key="english"]')
    panel.wait_for_selector('[data-meta-chips="lang"] .aud-chip')
    assert chips(panel, "lang") == ["English"] and panel.evaluate("state.metaSel.li_1.languages.map(l => l.name)") == ["English (UK)", "English (US)"]
    panel.click('[data-meta-act="lang-quick"][data-key="both"]')
    panel.wait_for_function("() => state.metaSel.li_1.languages.length === 5")
    assert chips(panel, "lang") == ["English", "Spanish"] and summary(panel, "lang") == "English · Spanish"
    assert panel.get_attribute('[data-meta-act="lang-quick"][data-key="both"]', "aria-pressed") == "true"
    assert panel.inner_text(f'{sec("lang")} .aud-count') == "2"
    panel.click('[data-meta-chips="lang"] .aud-chip >> nth=0 >> .aud-chip-x')     # removing the English group leaves Spanish, keyboard on the next chip
    assert chips(panel, "lang") == ["Spanish"]
    assert panel.evaluate("document.activeElement.classList.contains('aud-chip-x')")
    panel.click(f'{sec("lang")} .aud-clear')
    assert chips(panel, "lang") == [] and summary(panel, "lang") == "Any language"


def test_a_language_meta_does_not_list_or_a_failed_lookup_says_so_and_changes_nothing(panel, world):
    world.graph.locales[:] = [r for r in world.graph.locales if not r["name"].startswith("Spanish")]
    panel.click('[data-meta-act="lang-quick"][data-key="spanish"]')
    panel.wait_for_selector(f'{sec("lang")} .aud-notice--error:visible')
    assert "Meta lists no language called Spanish" in panel.inner_text(f'{sec("lang")} .aud-notice--error')
    assert chips(panel, "lang") == [] and panel.is_enabled('[data-meta-act="lang-quick"][data-key="spanish"]')
    world.fail["languages"] = (503, {"detail": "Meta rejected the access token."})
    panel.click('[data-meta-act="lang-quick"][data-key="english"]')
    panel.wait_for_selector(f'{sec("lang")} .aud-notice--error:visible >> text=Meta rejected the access token.')
    assert chips(panel, "lang") == []


def test_any_other_language_is_searched_in_meta_list_with_accents_ignored(panel, world):
    panel.fill('[data-meta-ta-input="lang"]', "portu")
    panel.wait_for_selector('[data-meta-ta-list="lang"] .aud-ta-opt')
    assert world.of("languages") == [{}]                              # the whole list, asked once
    assert options(panel, "lang") == ["Portuguese (Brazil)", "Portuguese (Portugal)"]
    assert panel.inner_html('[data-meta-ta-list="lang"] .aud-ta-name >> nth=0').startswith("<mark>Portu</mark>")
    panel.keyboard.press("ArrowDown")
    panel.keyboard.press("Enter")
    assert chips(panel, "lang") == ["Portuguese (Brazil)"] and summary(panel, "lang") == "Portuguese (Brazil)"
    panel.fill('[data-meta-ta-input="lang"]', "espanol")              # "Español" is found by "espanol"
    panel.wait_for_selector('[data-meta-ta-list="lang"] .aud-ta-opt')
    assert options(panel, "lang") == ["Español"]
    panel.fill('[data-meta-ta-input="lang"]', "portugu")
    panel.wait_for_selector('[data-meta-ta-list="lang"] .aud-ta-opt.is-disabled')
    assert "Added" in panel.inner_text('[data-meta-ta-list="lang"] .aud-ta-opt.is-disabled')    # what is already chosen cannot be added twice
    assert len(world.of("languages")) == 1
    panel.fill('[data-meta-ta-input="lang"]', "klingon")
    assert "Meta lists no language matching" in panel.inner_text('[data-meta-ta-list="lang"]')


def test_the_language_caveat_is_the_servers_own_text_short_first_and_in_full_on_demand(panel):
    note = main.constanza_meta_status()["language_note"]
    short = panel.inner_text("[data-meta-langshort]").strip()
    assert short == note.split(". ")[0] + "." and "not a count of who speaks it" in short
    assert panel.is_hidden("[data-meta-langfull]")
    panel.click(".meta-langnote summary")
    assert panel.inner_text("[data-meta-langfull]").strip() == note and "smaller than the Spanish-speaking population" in note


def test_a_line_that_targets_hispanic_audiences_is_offered_spanish_but_never_given_it(open_panel):
    page = open_panel(demo="Hispanic adults 25-54")
    page.wait_for_selector(".aud-notice--connected")
    pill = '[data-meta-suggest] button'
    assert page.inner_text(pill) == "This line targets Hispanic audiences — add Spanish?" and page.is_visible(pill)
    assert chips(page, "lang") == [] and page.evaluate("state.metaSel.li_1.languages.length") == 0       # offered, never applied silently
    page.click(pill)
    page.wait_for_selector('[data-meta-chips="lang"] .aud-chip')
    assert chips(page, "lang") == ["Spanish"] and page.is_hidden("[data-meta-suggest]")
    page.click('[data-meta-chips="lang"] .aud-chip-x')                # taken out again: the offer is back
    assert page.is_visible(pill)


@pytest.mark.parametrize("setup", [{"demo": "Adults 25-54"}, {"demo": "Adults 18+", "targetOverride": "Women 25-54"},
                                   {"demo": "Hispanic adults", "targetOverride": "Moms 25-44"}, {"demo": "Non-Hispanic adults"}, {"demo": "Spanish-language creative"}])
def test_no_spanish_offer_when_the_line_does_not_target_hispanic_audiences(open_panel, setup):
    page = open_panel(**setup)
    assert page.locator("[data-meta-suggest]").count() == 0


def test_a_line_with_its_own_hispanic_target_is_offered_spanish_even_if_the_campaign_is_not(open_panel):
    page = open_panel(demo="Adults 25-54", targetOverride="Hispanic moms 25-44")
    assert page.locator("[data-meta-suggest]").count() == 1


def test_browse_all_languages_opens_the_pop_up_and_applies_what_was_ticked(panel):
    panel.click('[data-meta-act="browse-lang"]')
    panel.wait_for_selector(".segb-row")
    assert panel.inner_text(".segb-dialog .segb-title").strip() == "Browse languages"
    assert panel.locator(".segb-row").count() == len(Graph.LOCALES) and panel.locator(".segb-tab").count() == 0
    panel.click('.segb-row[data-id="1007"]')
    panel.click('.segb-row[data-id="1006"]')
    assert "(2)" in panel.inner_text(".segb-apply")
    panel.click(".segb-apply")
    panel.wait_for_selector(".segb-dialog", state="detached")
    assert sorted(chips(panel, "lang")) == ["French (France)", "Tagalog"]
    assert panel.evaluate("document.activeElement && document.activeElement.dataset.metaAct") == "browse-lang"     # the keyboard is back where it was
    panel.click('[data-meta-act="browse-lang"]')
    panel.wait_for_selector(".segb-row")
    assert panel.get_attribute('.segb-row[data-id="1007"]', "aria-selected") == "true"                              # what is chosen opens ticked
    panel.keyboard.press("Escape")


# ----------------------------------------------------------------------------------------------------------------- 4 Audience

def test_the_audience_search_groups_hits_by_kind_with_sizes_and_choosing_adds_a_chip(panel, world):
    panel.fill('[data-meta-ta-input="aud"]', "soccer")
    panel.wait_for_selector('[data-meta-ta-list="aud"] .aud-ta-opt')
    assert world.of("targeting") == [{"q": "soccer"}]
    groups = panel.eval_on_selector_all('[data-meta-ta-list="aud"] .aud-ta-group', "els => els.map(e => e.textContent)")
    assert groups == ["Interests", "Behaviors"]
    assert options(panel, "aud") == ["Soccer", "Soccer balls", "Soccer fans (engaged)"]
    first = panel.inner_text('[data-meta-ta-list="aud"] .aud-ta-opt >> nth=0')
    assert "Interests › Sports and outdoors › Soccer" in first and "120M – 140M" in first
    assert panel.inner_html('[data-meta-ta-list="aud"] .aud-ta-name >> nth=0') == "<mark>Soccer</mark>"
    assert "Browse all interests, behaviors & demographics" in panel.inner_text('[data-meta-ta-list="aud"] .aud-ta-foot')
    panel.click('[data-meta-ta-list="aud"] .aud-ta-opt >> nth=0')
    panel.fill('[data-meta-ta-input="aud"]', "soccer")
    panel.wait_for_selector('[data-meta-ta-list="aud"] .aud-ta-opt')
    assert chips(panel, "aud") == ["Soccer"] and panel.inner_text('[data-meta-chips="aud"] .aud-chip-meta') == "Interest"
    assert "Added" in panel.inner_text('[data-meta-ta-list="aud"] .aud-ta-opt.is-disabled')
    panel.click('[data-meta-ta-list="aud"] .aud-ta-opt >> nth=2')      # a behavior
    assert chips(panel, "aud") == ["Soccer", "Soccer fans (engaged)"] and summary(panel, "aud") == "Soccer, Soccer fans (engaged)"
    assert panel.inner_text(f'{sec("aud")} .aud-count') == "2"
    assert panel.is_hidden("[data-meta-none]")


def test_the_note_that_several_picks_widen_the_audience_stays(panel):
    hint = panel.inner_text("[data-meta-audhint]")
    assert "audience gets wider" in hint and "match any of them" in hint and "None chosen yet" in hint


def test_browse_all_opens_the_pop_up_with_three_tabs_sortable_columns_and_applies_the_changes(panel):
    panel.fill('[data-meta-ta-input="aud"]', "cooking")
    panel.wait_for_selector('[data-meta-ta-list="aud"] .aud-ta-opt')
    panel.click('[data-meta-ta-list="aud"] .aud-ta-opt')               # Cooking is already held when the window opens
    panel.click(".aud-browse")
    panel.wait_for_selector(".segb-row")
    assert panel.eval_on_selector_all(".segb-tab", "els => els.map(e => e.textContent.replace(/[0-9,]+/g, '').trim())") == ["Interests", "Behaviors", "Demographics"]
    assert panel.eval_on_selector_all(".segb-sort", "els => els.map(e => e.textContent.trim())") == ["Name", "Category", "Audience size"]
    assert panel.get_attribute('.segb-row[data-id="interests:6003000000004"]', "aria-selected") == "true"              # held: opens ticked
    cat = panel.inner_text('.segb-row[data-id="interests:6003000000003"]')
    assert "Telenovelas" in cat and "Interests › Entertainment › TV" in cat and "22M – 26M" in cat
    assert "—" in panel.inner_text('.segb-row[data-id="interests:6003000000007"]')                                       # a list item with no size says so
    panel.click(".segb-sort >> text=Audience size")                                                                      # numeric: biggest first
    names = panel.eval_on_selector_all(".segb-row", "els => els.map(e => e.querySelector('.segb-cell').textContent)")
    assert names[:3] == ["Cooking", "Soccer", "Small business"] and names[-1] == "Mystery interest"
    assert panel.get_attribute('th:has(.segb-sort:has-text("Audience size"))', "aria-sort") == "descending"
    panel.click('.segb-row[data-id="interests:6003000000001"]')                                                          # Soccer
    panel.click(".segb-tab >> nth=1")
    panel.wait_for_selector('.segb-row[data-id="behaviors:6002714895372"]')
    panel.click('.segb-row[data-id="behaviors:6002714895372"]')
    panel.click(".segb-tab >> nth=2")
    panel.wait_for_selector('.segb-row[data-id="life_events:6002714398172"]')
    panel.click('.segb-row[data-id="life_events:6002714398172"]')
    assert "Add to audience (3)" in panel.inner_text(".segb-apply")
    panel.click(".segb-apply")
    panel.wait_for_selector(".segb-dialog", state="detached")
    assert chips(panel, "aud") == ["Cooking", "Soccer", "Frequent travelers", "Newlyweds (1 year)"]
    assert panel.eval_on_selector_all('[data-meta-chips="aud"] .aud-chip-meta', "els => els.map(e => e.textContent)") == ["Interest", "Interest", "Behavior", "Life event"]
    assert panel.evaluate("state.metaSel.li_1.detailed.map(d => d.type)") == ["interests", "interests", "behaviors", "life_events"]
    assert summary(panel, "aud") == "Cooking, Soccer +2 more" and panel.inner_text("[data-meta-browsecount]") == "4 chosen"
    assert panel.evaluate("document.activeElement && document.activeElement.dataset.metaAct") == "browse"
    panel.click(".aud-browse")                                                                                           # untick Cooking: removed
    panel.wait_for_selector(".segb-row")
    panel.click('.segb-row[data-id="interests:6003000000004"]')
    panel.click(".segb-apply")
    panel.wait_for_selector(".segb-dialog", state="detached")
    assert chips(panel, "aud") == ["Soccer", "Frequent travelers", "Newlyweds (1 year)"]


def test_the_browse_window_is_given_the_lists_the_columns_the_limit_and_the_picks_already_held(panel):
    panel.evaluate("""() => {
      window.__sb = null; const open = SegmentBrowser.open; SegmentBrowser.open = (o) => { window.__sb = o; return { close() {} }; };
      state.metaSel.li_1.detailed.push({ id: '6003000000004', name: 'Cooking', type: 'interests' }, { id: '6002714398172', name: 'Newlyweds (1 year)', type: 'life_events' });
    }""")
    panel.click(".aud-browse")
    got = panel.evaluate("""async () => {
      const o = window.__sb; const items = await o.load('interests'); const demo = await o.load('demographics'); const soccer = items.find(i => i.name === 'Soccer');
      return { tabs: o.tabs.map(t => t.key), columns: o.columns.map(c => c.key), max: o.max, active: o.activeTab,
               counts: { interests: o.chosenCount('interests'), behaviors: o.chosenCount('behaviors'), demographics: o.chosenCount('demographics') },
               names: items.map(i => i.name), soccer, demoFirst: demo[0], heldTicked: items.filter(i => o.isChosen('interests', i)).map(i => i.name), demoTicked: demo.filter(i => o.isChosen('demographics', i)).map(i => i.name),
               categoryOfSoccer: o.columns[1].value(soccer), returnFocus: o.returnFocus && o.returnFocus.dataset.metaAct };
    }""")
    assert got["tabs"] == ["interests", "behaviors", "demographics"] and got["columns"] == ["name", "category", "size"] and got["max"] == 50 and got["active"] == "interests"
    assert got["counts"] == {"interests": 1, "behaviors": 0, "demographics": 1}
    assert got["names"][0] == "Cooking" and len(got["names"]) == len(Graph.INTERESTS)                       # the server's own list, name order
    assert got["soccer"]["size"] == 120_000_000 and got["soccer"]["sizeUpper"] == 140_000_000 and got["soccer"]["type"] == "interests"      # audience_lower / audience_upper
    assert got["soccer"]["id"] == "interests:6003000000001" and got["soccer"]["metaId"] == "6003000000001"
    assert got["demoFirst"]["type"] == "life_events" and got["categoryOfSoccer"] == "Interests › Sports and outdoors › Soccer"
    assert got["heldTicked"] == ["Cooking"] and got["demoTicked"] == ["Newlyweds (1 year)"] and got["returnFocus"] == "browse"


def test_a_list_that_cannot_be_loaded_says_so_in_the_window_and_can_be_retried(open_panel, world):
    page = open_panel(before=lambda w: w.fail.update({"targeting": (503, {"detail": "Meta rejected the access token."})}))
    page.wait_for_selector(".aud-notice--connected")
    page.click(".aud-browse")
    page.wait_for_selector(".segb-state >> text=Meta rejected the access token.")
    world.fail.clear()
    page.click(".segb-retry")
    page.wait_for_selector(".segb-row")


def test_at_fifty_picks_the_search_and_the_window_stop_adding(panel):
    panel.evaluate("""() => { const d = state.metaSel.li_1.detailed; for (let i = 0; i < 49; i++) d.push({ id: String(1000 + i), name: 'Pick ' + i, type: 'interests' }); _metaSync(document.querySelector('.meta-panel')); }""")
    panel.fill('[data-meta-ta-input="aud"]', "cooking")
    panel.wait_for_selector('[data-meta-ta-list="aud"] .aud-ta-opt')
    panel.click('[data-meta-ta-list="aud"] .aud-ta-opt')
    assert panel.evaluate("state.metaSel.li_1.detailed.length") == 50
    panel.fill('[data-meta-ta-input="aud"]', "soccer")
    panel.wait_for_selector('[data-meta-ta-list="aud"] .aud-ta-opt')
    assert "limit of 50" in panel.inner_text('[data-meta-ta-list="aud"]') and panel.locator('[data-meta-ta-list="aud"] .aud-ta-opt:not(.is-disabled)').count() == 0


def test_removing_a_chip_puts_the_keyboard_on_the_neighbour_chip(panel):
    panel.evaluate("state.metaSel.li_1.detailed.push({ id: '1', name: 'One', type: 'interests' }, { id: '2', name: 'Two', type: 'behaviors' }, { id: '3', name: 'Three', type: 'interests' }); _metaSync(document.querySelector('.meta-panel'))")
    panel.focus('[data-meta-chips="aud"] .aud-chip >> nth=1 >> .aud-chip-x')
    panel.keyboard.press("Enter")
    assert chips(panel, "aud") == ["One", "Three"]
    assert panel.evaluate("document.activeElement.getAttribute('aria-label')") == "Remove Three"
    panel.click('[data-meta-chips="aud"] .aud-chip-x >> nth=1')
    assert panel.evaluate("document.activeElement.getAttribute('aria-label')") == "Remove One"
    panel.keyboard.press("Enter")
    assert chips(panel, "aud") == [] and panel.evaluate("document.activeElement === document.querySelector('[data-meta-ta-input=\"aud\"]')")


# ----------------------------------------------------------------------------------------------------------------- the estimate

def test_the_request_is_what_the_sections_say_and_the_result_is_three_scenarios_from_the_lower_end(panel, world):
    pick_place(panel)
    panel.select_option('[data-meta-age="min"]', "25")
    panel.select_option('[data-meta-age="max"]', "44")
    panel.click('[data-meta-act="gender"][data-gender="female"]')
    panel.click('[data-meta-act="lang-quick"][data-key="spanish"]')
    panel.wait_for_selector('[data-meta-chips="lang"] .aud-chip')
    panel.fill('[data-meta-ta-input="aud"]', "telenovela")
    panel.wait_for_selector('[data-meta-ta-list="aud"] .aud-ta-opt')
    panel.click('[data-meta-ta-list="aud"] .aud-ta-opt')
    estimate(panel)
    body = forecast_body(panel)
    assert body == {
        "product_name": PRODUCT, "retail_budget": 2000.0, "retail_cpm": 12, "months": 3,
        "meta_geo_type": "geo_market", "meta_geo_key": "803", "meta_geo_label": "Los Angeles · California · US",
        "age_min": 25, "age_max": 44, "gender": "female",
        "meta_targeting": [{"id": "6003000000003", "name": "Telenovelas", "type": "interests"}],
        "meta_languages": [{"id": "1001", "name": "Spanish"}, {"id": "1002", "name": "Spanish (Mexico)"}, {"id": "1003", "name": "Spanish (Spain)"}],
    }
    lower, upper = Graph.reach({"age_min": 25, "age_max": 44, "genders": [2], "locales": [1, 2, 3], "flexible_spec": [{}]})
    result = panel.inner_text(".meta-result")
    assert f"{lower:,}" in result and f"{upper:,}" in result and "accounts at the lower end of Meta’s audience" in result and "(from Meta)" in result
    assert "Pulled for Los Angeles · California · US (TV market), women ages 25–44, language set to Spanish, any of: Telenovelas." in result
    assert "speaks" not in result.lower().replace("not who speaks it", "") and "set on Facebook or Instagram, not who speaks it" in result
    cards = panel.eval_on_selector_all(".meta-card", "els => els.map(e => ({ name: e.querySelector('.meta-card-name').textContent, mid: e.classList.contains('meta-mid'), big: e.querySelector('.meta-card-big strong').textContent }))")
    assert [c["name"] for c in cards] == ["Conservative × 4.3", "Mid · default × 5.5", "Upper × 8.6"] and [c["mid"] for c in cards] == [False, True, False]
    assert [c["big"] for c in cards] == [f"{round(lower * 4.3):,}", f"{round(lower * 5.5):,}", f"{round(lower * 8.6):,}"]
    assert "Flight · 3 mo" in result and "Budget cross-check" in result and "not avails" in result and "Planning scenarios, not a delivery forecast" in result
    assert panel.inner_text(".meta-insert-btn").startswith(f"Insert the mid-case ({round(lower * 5.5):,} / month) into Avails")
    assert panel.evaluate("state.availsData.li_1.meta_forecast.meta_targeting.languages.length") == 3                  # saved with the proposal, languages included
    assert panel.is_enabled('[data-meta-act="estimate"]') and panel.inner_text('[data-meta-act="estimate"]') == "Estimate audience"


def test_a_pull_with_nothing_narrowed_sends_no_age_and_no_languages(panel):
    pick_place(panel)
    estimate(panel)
    body = forecast_body(panel)
    assert body["age_min"] is None and body["age_max"] is None and body["gender"] == "all" and body["meta_targeting"] == [] and body["meta_languages"] == []
    assert "manual" not in panel.inner_text(".meta-result") and "audience_lower" not in body and "audience_upper" not in body


def test_estimate_without_a_place_says_what_to_do_and_puts_the_keyboard_in_the_search(panel, world):
    panel.click('[data-meta-act="estimate"]')
    assert "Choose where first" in panel.inner_text("[data-meta-error]")
    assert panel.evaluate("document.activeElement === document.querySelector('[data-meta-ta-input=\"geo\"]')") and world.hits.count("forecast") == 0


def test_estimate_without_a_budget_says_so(open_panel, world):
    page = open_panel(lines=[{"id": "li_1", "product_name": PRODUCT, "monthly_budget": 0, "months": 1}])
    pick_place(page)
    page.click('[data-meta-act="estimate"]')
    assert "positive retail budget" in page.inner_text("[data-meta-error]") and world.hits.count("forecast") == 0


def test_a_meta_error_is_shown_above_the_last_result_which_stays(panel, world):
    pick_place(panel)
    estimate(panel)
    headline = panel.inner_text(".meta-headline strong")
    world.graph.reach_error = {"message": "Invalid parameter", "code": 100}
    panel.click('[data-meta-act="gender"][data-gender="male"]')
    panel.click('[data-meta-act="estimate"]')
    panel.wait_for_selector("[data-meta-error] .aud-notice--error")
    assert "Meta refused these settings" in panel.inner_text("[data-meta-error]")
    assert panel.is_enabled('[data-meta-act="estimate"]') and panel.is_visible(".meta-result")
    assert "Inputs changed — recalculate" in panel.inner_text(".meta-result") and headline in panel.inner_text(".meta-result")      # the old numbers are still there


def test_the_button_is_busy_while_asking_and_a_second_click_cannot_double_send(panel, world):
    pick_place(panel)
    world.hold.add("forecast")
    panel.click('[data-meta-act="estimate"]')
    assert panel.inner_text('[data-meta-act="estimate"]') == "Asking Meta…" and panel.is_disabled('[data-meta-act="estimate"]')
    assert panel.get_attribute('[data-meta-act="estimate"]', "aria-busy") == "true"
    world.release("forecast")
    panel.wait_for_selector(".meta-result .meta-scen")
    assert panel.inner_text('[data-meta-act="estimate"]') == "Estimate audience" and len(world.of("forecast")) == 1


def test_changing_a_choice_marks_the_result_stale_and_withdraws_insert_and_putting_it_back_restores_it(panel):
    pick_place(panel)
    estimate(panel)
    assert panel.is_visible(".meta-insert-btn") and "Inputs changed" not in panel.inner_text(".meta-result")
    for change in ('[data-meta-act="gender"][data-gender="female"]', '[data-meta-act="age-preset"] >> text=18–24'):
        panel.click(change)
        assert "Inputs changed — recalculate" in panel.inner_text(".meta-result") and panel.locator(".meta-insert-btn").count() == 0
        assert panel.locator(".meta-result.is-stale").count() == 1
        panel.click('[data-meta-act="gender"][data-gender="all"]') if "gender" in change else panel.click('[data-meta-act="age-preset"] >> text=All ages')
        assert "Inputs changed" not in panel.inner_text(".meta-result") and panel.is_visible(".meta-insert-btn")
    panel.click('[data-meta-act="lang-quick"][data-key="english"]')
    panel.wait_for_selector('[data-meta-chips="lang"] .aud-chip')
    assert "Inputs changed — recalculate" in panel.inner_text(".meta-result") and panel.locator(".meta-insert-btn").count() == 0
    panel.click(f'{sec("lang")} .aud-clear')
    assert panel.is_visible(".meta-insert-btn")
    panel.click(f'{sec("where")} .aud-clear')
    assert "Inputs changed — recalculate" in panel.inner_text(".meta-result")


def test_the_state_of_the_result_is_said_once_not_on_every_choice(panel):
    pick_place(panel)
    estimate(panel)
    assert panel.get_attribute(".meta-feedback", "aria-live") is None and "The estimate is ready" in panel.inner_text("[data-meta-live]")
    panel.click('[data-meta-act="gender"][data-gender="female"]')
    assert "out of date" in panel.inner_text("[data-meta-live]")
    panel.evaluate("document.querySelector('[data-meta-live]').textContent = ''")
    panel.click('[data-meta-act="gender"][data-gender="male"]')          # still stale: nothing new to say
    assert panel.inner_text("[data-meta-live]") == ""
    panel.click('[data-meta-act="gender"][data-gender="all"]')
    assert "matches your choices again" in panel.inner_text("[data-meta-live]")


def test_a_changed_line_marks_the_result_stale_when_the_grid_is_drawn_again(panel):
    pick_place(panel)
    estimate(panel)
    panel.click('[data-meta-act="gender"][data-gender="male"]')
    panel.evaluate("renderAvailsGrid()")                              # a redraw (a freeform toggle, an Insert) keeps the mark: the choices differ from the result
    assert "Inputs changed — recalculate" in panel.inner_text(".meta-result") and panel.locator(".meta-insert-btn").count() == 0
    assert chips(panel, "geo") == ["Los Angeles"] and panel.get_attribute('[data-meta-act="gender"][data-gender="male"]', "aria-pressed") == "true"      # choices survive the redraw
    for change in ("state.lineItems[0].monthly_budget = 3000", "window.__months = 5", "window.__geo = 'Phoenix'", "window.__target = 'Adults 18+'"):
        panel.click('[data-meta-act="gender"][data-gender="all"]')
        panel.evaluate(f"{change}; renderAvailsGrid()")
        assert "Inputs changed — recalculate" in panel.inner_text(".meta-result") and panel.locator(".meta-insert-btn").count() == 0, change
        panel.evaluate("state.lineItems[0].monthly_budget = 2000; window.__months = 3; window.__geo = 'Los Angeles'; window.__target = 'Adults 25-54'; renderAvailsGrid()")
        assert "Inputs changed" not in panel.inner_text(".meta-result"), change


def test_insert_writes_the_mid_case_and_the_lower_end_audience_into_avails(panel):
    pick_place(panel)
    estimate(panel)
    lower = panel.evaluate("state.availsData.li_1.meta_forecast.potential_audience.lower")
    panel.click(".meta-insert-btn")
    inserted = panel.evaluate("window.__inserted")
    assert inserted["lid"] == "li_1" and inserted["imps"] == round(lower * 5.5) and inserted["uniques"] == lower
    assert inserted["info"] == {"method": "meta_audience", "dmas": ["Los Angeles · California · US"], "unmatched_tokens": [], "win_rate": None, "hispanic_share": None}
    assert panel.is_visible(".meta-result .meta-scen") and chips(panel, "geo") == ["Los Angeles"]                    # the redraw after Insert still shows the result and the choices


# ----------------------------------------------------------------------------------------------------------------- reopening a saved proposal

def test_a_saved_snapshot_restores_what_was_asked_including_languages(open_panel):
    saved = meta_result(languages=[{"id": "1001", "name": "Spanish"}, {"id": "1002", "name": "Spanish (Mexico)"}],
                     detailed=[{"id": "6003000000003", "name": "Telenovelas", "type": "interests"}, {"id": "6002714398172", "name": "Newlyweds (1 year)", "type": "life_events"}])
    page = open_panel(saved=saved)
    assert chips(page, "geo") == ["Los Angeles"] and summary(page, "where") == "Los Angeles · TV market"
    assert summary(page, "who") == "Women · 25–44" and page.input_value('[data-meta-age="min"]') == "25" and page.input_value('[data-meta-age="max"]') == "44"
    assert chips(page, "lang") == ["Spanish"] and page.locator("[data-meta-suggest]").count() == 0
    assert chips(page, "aud") == ["Telenovelas", "Newlyweds (1 year)"]
    assert "Inputs changed" not in page.inner_text(".meta-result") and page.is_visible(".meta-insert-btn")
    assert "language set to Spanish" in page.inner_text(".meta-result")
    page.click('[data-meta-act="estimate"]')                          # asking again sends exactly what was restored
    page.wait_for_selector(".meta-result .meta-scen")
    body = forecast_body(page)
    assert body["meta_geo_key"] == "803" and body["age_min"] == 25 and body["age_max"] == 44 and body["gender"] == "female"
    assert [x["id"] for x in body["meta_languages"]] == ["1001", "1002"] and [x["type"] for x in body["meta_targeting"]] == ["interests", "life_events"]


def test_the_spanish_offer_stays_hidden_when_a_reopened_proposal_already_asked_for_spanish(open_panel):
    saved = meta_result(languages=[{"id": "1001", "name": "Spanish"}])
    page = open_panel(saved=saved, demo="Hispanic adults 25-54")
    assert page.locator("[data-meta-suggest]").count() == 1 and page.is_hidden("[data-meta-suggest]") and chips(page, "lang") == ["Spanish"]


def test_a_snapshot_saved_before_languages_existed_still_reopens(open_panel):
    saved = meta_result()
    del saved["meta_targeting"]["languages"]
    page = open_panel(saved=saved)
    assert chips(page, "lang") == [] and summary(page, "lang") == "Any language" and "Inputs changed" not in page.inner_text(".meta-result")


def test_a_manual_snapshot_still_renders_labelled_entered_manually_and_there_is_no_manual_form(open_panel):
    saved = meta_result(lower=500, upper=500, source="manual", with_targeting=False)
    page = open_panel(saved=saved)
    result = page.inner_text(".meta-result")
    assert "(entered manually)" in result and "500" in result and "(from Meta)" not in result and "Pulled for" not in result
    assert page.locator(".meta-card").count() == 3 and page.is_visible(".meta-insert-btn") and "Inputs changed" not in result
    assert page.locator(".meta-panel input[type=number]").count() == 0 and "Calculate scenarios" not in page.inner_text(".meta-panel")
    assert summary(page, "where") == "Choose a place"                    # nothing was asked of Meta, so there are no choices to restore
    page.click('[data-meta-act="gender"][data-gender="female"]')
    assert "Inputs changed" not in page.inner_text(".meta-result")        # a manual figure answers none of the choices: it is not marked by them
    page.click(".meta-insert-btn")
    assert page.evaluate("window.__inserted.info.dmas") == ["entered audience"] and page.evaluate("window.__inserted.uniques") == 500


def test_a_budget_only_snapshot_says_to_estimate_an_audience(open_panel):
    saved = meta_result(with_targeting=False)
    saved["potential_audience"] = None
    saved["scenarios"] = None
    page = open_panel(saved=saved)
    assert "Estimate an audience to see the scenarios" in page.inner_text(".meta-result") and page.locator(".meta-scen, .meta-insert-btn").count() == 0
    assert "Budget cross-check" in page.inner_text(".meta-result")


def test_names_in_a_saved_snapshot_are_shown_as_text(open_panel):
    saved = meta_result(geo_name="<img src=x onerror=alert(1)> · <b>CA</b>", detailed=[{"id": "1", "name": "\"><script>window.__x=1</script>", "type": "interests"}],
                     languages=[{"id": "1001", "name": "<i>Spanish</i>"}])
    page = open_panel(saved=saved)
    assert page.locator(".meta-panel img, .meta-panel script, .meta-panel b, .meta-panel i").count() == 0 and page.evaluate("window.__x") is None
    assert "<img src=x onerror=alert(1)>" in page.inner_text(".meta-result")


def test_two_lines_keep_their_own_choices_and_the_connection_is_asked_once(open_panel, world):
    lines = [{"id": "li_1", "product_name": PRODUCT, "monthly_budget": 2000, "months": 1}, {"id": "li_2", "product_name": PRODUCT, "monthly_budget": 1000, "months": 1}]
    page = open_panel(lines=lines)
    page.wait_for_selector(".aud-notice--connected >> nth=1")
    assert page.locator(".meta-panel").count() == 2 and world.hits.count("status") == 1
    assert page.locator(".aud-notice--connected").count() == 2
    page.click('.meta-panel[data-meta-lid="li_2"] [data-meta-act="gender"][data-gender="male"]')
    assert [summary_text for summary_text in page.eval_on_selector_all('[data-meta-sec="who"] [data-meta-sum]', "els => els.map(e => e.textContent)")] == ["All genders · all ages", "Men · all ages"]
    ids = page.eval_on_selector_all(".meta-panel [id]", "els => els.map(e => e.id)")
    assert len(ids) == len(set(ids))                                   # every id (labels, listboxes) is unique across the two panels


# ----------------------------------------------------------------------------------------------------------------- look and feel

def test_the_sections_sit_two_across_in_the_card_and_stack_on_a_phone_without_a_sideways_scroll(open_panel):
    wide = open_panel(width=1280)
    wide.wait_for_selector(".aud-notice--connected")
    tops = wide.eval_on_selector_all(".meta-builder .aud-sec", "els => els.map(e => Math.round(e.getBoundingClientRect().top))")
    assert tops[0] == tops[1] and tops[2] == tops[3] and tops[2] > tops[0]
    phone = open_panel(width=390, height=900)
    phone.wait_for_selector(".aud-notice--connected")
    tops = phone.eval_on_selector_all(".meta-builder .aud-sec", "els => els.map(e => Math.round(e.getBoundingClientRect().top))")
    assert tops == sorted(set(tops)) and len(tops) == 4
    assert phone.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    pick_place(phone)
    estimate(phone)
    assert phone.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert phone.evaluate("[...document.querySelectorAll('.meta-card, .meta-insert-btn, .meta-result')].every(e => e.getBoundingClientRect().right <= window.innerWidth + 0.5)")


def test_every_control_is_at_least_36px_tall_and_shows_a_focus_ring(panel):
    pick_place(panel)
    panel.click('[data-meta-act="lang-quick"][data-key="spanish"]')
    panel.wait_for_selector('[data-meta-chips="lang"] .aud-chip')
    panel.evaluate("state.metaSel.li_1.detailed.push({ id: '9', name: 'One pick', type: 'interests' }); _metaSync(document.querySelector('.meta-panel'))")
    small = panel.evaluate("""() => [...document.querySelectorAll('.meta-panel button, .meta-panel select, .meta-panel input, .meta-panel summary')]
      .filter((e) => e.getClientRects().length).map((e) => ({ t: (e.textContent || e.getAttribute('aria-label') || e.id).trim().slice(0, 30), h: Math.round(e.getBoundingClientRect().height) })).filter((x) => x.h < 36)""")
    assert small == []
    for selector in ('[data-meta-act="estimate"]', '[data-meta-act="gender"][data-gender="male"]', '[data-meta-ta-input="geo"]', '[data-meta-chips="aud"] .aud-chip-x'):
        panel.focus(selector)
        panel.keyboard.press("Shift+Tab")
        panel.keyboard.press("Tab")
        assert panel.evaluate(f"getComputedStyle(document.querySelector({json.dumps(selector)})).outlineStyle") != "none", selector


def test_text_in_the_panel_and_in_a_result_has_enough_contrast(panel):
    pick_place(panel)
    panel.click('[data-meta-act="lang-quick"][data-key="spanish"]')
    panel.wait_for_selector('[data-meta-chips="lang"] .aud-chip')
    estimate(panel)
    assert panel.evaluate("contrastReport('.meta-panel')") == []
    panel.click('[data-meta-act="gender"][data-gender="male"]')       # the stale look is legible too
    assert panel.evaluate("contrastReport('.meta-panel')") == []


def test_the_not_connected_look_is_legible_too(open_panel, monkeypatch):
    monkeypatch.delenv("META_ACCESS_TOKEN")
    page = open_panel()
    page.wait_for_selector(".meta-status .aud-notice--warn")
    assert page.evaluate("contrastReport('.meta-panel')") == []


def test_a_list_opened_in_a_typeahead_closes_when_the_keyboard_leaves_it(panel):
    panel.fill('[data-meta-ta-input="geo"]', "los")
    panel.wait_for_selector('[data-meta-ta-list="geo"] .aud-ta-opt')
    panel.focus('[data-meta-act="gender"][data-gender="male"]')
    assert panel.is_hidden('[data-meta-ta-list="geo"]')
    panel.fill('[data-meta-ta-input="aud"]', "soccer")
    panel.wait_for_selector('[data-meta-ta-list="aud"] .aud-ta-opt')
    panel.keyboard.press("Escape")
    assert panel.is_hidden('[data-meta-ta-list="aud"]') and panel.input_value('[data-meta-ta-input="aud"]') == "soccer"      # the first Escape only closes the list
    panel.keyboard.press("Escape")
    assert panel.input_value('[data-meta-ta-input="aud"]') == ""                                                              # the second empties the box


# ----------------------------------------------------------------------------------------------------------------- review fixes: what the planner sees

HOUSEHOLD = {"id": "6023005404983", "name": "Parents (all)", "type": "household_composition", "path": ["Demographics", "Parents", "Parents (all)"],
             "audience_size_lower_bound": 20_000_000, "audience_size_upper_bound": 24_000_000}


def test_a_household_demographic_ticked_in_the_browse_window_can_be_estimated(open_panel, world):
    page = open_panel(before=lambda w: setattr(w.graph, "DEMOGRAPHICS", Graph.DEMOGRAPHICS + [HOUSEHOLD]))
    page.wait_for_selector(".aud-notice--connected")
    pick_place(page)
    page.click(".aud-browse")
    page.wait_for_selector(".segb-row")
    page.click(".segb-tab >> nth=2")
    page.wait_for_selector('.segb-row[data-id="household_composition:6023005404983"]')
    page.click('.segb-row[data-id="household_composition:6023005404983"]')
    page.click(".segb-apply")
    page.wait_for_selector(".segb-dialog", state="detached")
    assert chips(page, "aud") == ["Parents (all)"] and page.inner_text('[data-meta-chips="aud"] .aud-chip-meta') == "Household"
    estimate(page)                                                                                  # was a 422 "String should have at most 20 characters"
    assert forecast_body(page)["meta_targeting"] == [{"id": "6023005404983", "name": "Parents (all)", "type": "household_composition"}]
    assert page.locator("[data-meta-error] .aud-notice--error").count() == 0
    assert "any of: Parents (all)" in page.inner_text(".meta-result")


def test_a_refusal_that_names_a_field_says_which_pick_it_was_about(panel, world):
    pick_place(panel)
    panel.fill('[data-meta-ta-input="aud"]', "soccer")
    panel.wait_for_selector('[data-meta-ta-list="aud"] .aud-ta-opt')
    panel.click('[data-meta-ta-list="aud"] .aud-ta-opt')
    world.fail["forecast"] = (422, {"detail": [{"type": "string_too_long", "loc": ["body", "meta_targeting", 0, "type"], "msg": "String should have at most 20 characters"}]})
    panel.click('[data-meta-act="estimate"]')
    panel.wait_for_selector("[data-meta-error] .aud-notice--error")
    text = panel.inner_text("[data-meta-error]")
    assert "“Soccer” (Interest): String should have at most 20 characters." in text and "Change that choice and estimate again." in text
    assert panel.is_enabled('[data-meta-act="estimate"]')


# ---- an empty answer is not remembered, and the empty state has a Retry

def test_an_empty_language_list_in_the_browse_window_says_so_and_retry_asks_meta_again(open_panel, world):
    page = open_panel(before=lambda w: setattr(w.graph, "locales", []))
    page.wait_for_selector(".aud-notice--connected")
    page.click('[data-meta-act="browse-lang"]')
    page.wait_for_selector(".segb-state >> text=empty list of languages")
    assert page.locator(".segb-retry").count() == 1 and page.locator(".segb-row").count() == 0
    world.graph.locales = [dict(r) for r in Graph.LOCALES]                                          # Meta recovers
    page.click(".segb-retry")
    page.wait_for_selector(".segb-row")
    assert page.locator(".segb-row").count() == len(Graph.LOCALES) and len(world.of("languages")) == 2


def test_an_empty_language_list_is_asked_again_by_the_next_search_and_by_try_again_without_a_reload(open_panel, world):
    page = open_panel(before=lambda w: setattr(w.graph, "locales", []))
    page.wait_for_selector(".aud-notice--connected")
    page.fill('[data-meta-ta-input="lang"]', "port")
    page.wait_for_selector("[data-meta-ta-list='lang'] >> text=came back empty just now")
    assert "Meta lists no language matching" not in page.inner_text('[data-meta-ta-list="lang"]')      # an empty list is not "no match"
    world.graph.locales = [dict(r) for r in Graph.LOCALES]
    page.click('[data-meta-act="lang-retry"]')
    page.wait_for_selector('[data-meta-ta-list="lang"] .aud-ta-opt')
    assert options(page, "lang") == ["Portuguese (Brazil)", "Portuguese (Portugal)"] and len(world.of("languages")) == 2
    page.fill('[data-meta-ta-input="lang"]', "tag")                                                 # and now it is remembered: no third question
    page.wait_for_selector('[data-meta-ta-list="lang"] .aud-ta-opt')
    assert len(world.of("languages")) == 2


def test_an_empty_interest_list_is_not_kept_and_its_tab_can_be_retried(open_panel, world):
    page = open_panel(before=lambda w: setattr(w.graph, "INTERESTS", []))
    page.wait_for_selector(".aud-notice--connected")
    page.click(".aud-browse")
    page.wait_for_selector(".segb-state >> text=empty list of interests")
    assert page.locator(".segb-retry").count() == 1
    world.graph.INTERESTS = list(Graph.INTERESTS)
    page.click(".segb-retry")
    page.wait_for_selector('.segb-row[data-id="interests:6003000000001"]')
    assert [q for q in world.of("targeting") if q.get("kind") == "interests"] == [{"kind": "interests"}, {"kind": "interests"}]
    page.keyboard.press("Escape")
    page.click(".aud-browse")                                                                       # a list that did load is kept for the session
    page.wait_for_selector(".segb-row")
    assert len([q for q in world.of("targeting") if q.get("kind") == "interests"]) == 2


# ---- the search is cancelled by Escape and by leaving the box

def test_escape_within_the_debounce_cancels_the_search_and_the_list_stays_closed(panel, world):
    panel.focus('[data-meta-ta-input="geo"]')
    panel.keyboard.type("Phoenix")
    panel.keyboard.press("Escape")                                                                  # the 300 ms wait has not run out
    panel.wait_for_timeout(650)
    assert panel.is_hidden('[data-meta-ta-list="geo"]') and panel.get_attribute('[data-meta-ta-input="geo"]', "aria-expanded") == "false"
    assert world.hits.count("geos") == 0 and panel.input_value('[data-meta-ta-input="geo"]') == "Phoenix"
    panel.focus('[data-meta-ta-input="aud"]')
    panel.keyboard.type("soccer")
    panel.keyboard.press("Escape")
    panel.wait_for_timeout(650)
    assert panel.is_hidden('[data-meta-ta-list="aud"]') and world.hits.count("targeting") == 0


def test_leaving_the_box_within_the_debounce_cancels_the_search(panel, world):
    panel.focus('[data-meta-ta-input="geo"]')
    panel.keyboard.type("los")
    panel.keyboard.press("Tab")
    panel.wait_for_timeout(650)
    assert panel.is_hidden('[data-meta-ta-list="geo"]') and panel.get_attribute('[data-meta-ta-input="geo"]', "aria-expanded") == "false"
    assert world.hits.count("geos") == 0


def test_an_answer_that_arrives_after_escape_does_not_reopen_the_list(open_panel, world):
    page = open_panel()
    page.wait_for_selector(".aud-notice--connected")
    world.hold.add("geos")
    page.focus('[data-meta-ta-input="geo"]')
    page.keyboard.type("Phoenix")
    page.wait_for_timeout(450)                                                                       # the request is on its way
    assert len(world.pending) == 1
    page.keyboard.press("Escape")
    world.release("geos")
    page.wait_for_timeout(300)
    assert page.is_hidden('[data-meta-ta-list="geo"]') and page.get_attribute('[data-meta-ta-input="geo"]', "aria-expanded") == "false"


def test_the_interests_box_holds_no_more_than_the_server_searches_for(panel, world):
    assert [panel.get_attribute(f'[data-meta-ta-input="{w}"]', "maxlength") for w in ("geo", "aud", "lang")] == ["100", "80", "60"]
    panel.focus('[data-meta-ta-input="aud"]')
    panel.keyboard.type("s" * 88)
    assert len(panel.input_value('[data-meta-ta-input="aud"]')) == 80
    panel.wait_for_selector("[data-meta-ta-list='aud'] >> text=Nothing found")
    assert "must be 2–80" not in panel.inner_text('[data-meta-ta-list="aud"]') and len(world.of("targeting")[-1]["q"]) == 80


# ---- Insert: the keyboard stays in the panel and the insertion is said

def test_after_insert_the_keyboard_is_on_the_new_insert_button_and_the_insertion_is_announced(panel):
    pick_place(panel)
    estimate(panel)
    lower = panel.evaluate("state.availsData.li_1.meta_forecast.potential_audience.lower")
    panel.focus(".meta-insert-btn")
    panel.evaluate("window.__oldButton = document.querySelector('.meta-insert-btn')")
    panel.keyboard.press("Enter")
    assert panel.evaluate("window.__inserted.lid") == "li_1" and panel.evaluate("window.__oldButton.isConnected") is False          # the grid was drawn again
    assert panel.evaluate("document.activeElement.classList.contains('meta-insert-btn') && document.activeElement.isConnected") is True      # ...and the keyboard did not fall to <body>
    assert panel.evaluate("document.activeElement.closest('.meta-panel') === document.querySelector('.meta-panel')") is True
    panel.wait_for_function("() => document.getElementById('meta-live-announce').textContent.includes('Inserted into Avails')")
    said = panel.inner_text("#meta-live-announce")
    assert f"{round(lower * 5.5):,} impressions a month" in said and f"{lower:,} unique accounts" in said
    assert panel.locator("#meta-live-announce").count() == 1 and panel.get_attribute("#meta-live-announce", "role") == "status"
    assert panel.get_attribute("#meta-live-announce", "aria-live") == "polite" and panel.locator(".meta-panel #meta-live-announce").count() == 0       # outside the panel: it outlives the redraw
    panel.keyboard.press("Enter")                                                                    # again: the same words are said again
    panel.wait_for_function("() => document.getElementById('meta-live-announce').textContent === ''")
    panel.wait_for_function("() => document.getElementById('meta-live-announce').textContent.includes('Inserted into Avails')")
    assert panel.locator("#meta-live-announce").count() == 1


# ---- the language dialog speaks about languages

def test_the_language_dialog_has_its_own_search_words_and_counts_languages(panel):
    panel.click('[data-meta-act="browse-lang"]')
    panel.wait_for_selector(".segb-row")
    assert panel.get_attribute(".segb-search-input", "placeholder") == "Search languages"
    assert "by name, category or keyword" not in (panel.get_attribute(".segb-search-input", "placeholder") or "")
    assert f"{len(Graph.LOCALES)} languages" in panel.inner_text(".segb-count")
    panel.keyboard.press("Escape")
    panel.click(".aud-browse")                                                                        # the audience window keeps its own, which has categories to search
    panel.wait_for_selector(".segb-row")
    assert "category" in panel.get_attribute(".segb-search-input", "placeholder")


# ---- the browse controls do not pretend when the window script is missing

def test_without_the_browse_window_script_the_browse_controls_are_off_with_a_reason(panel):
    panel.evaluate("window.SegmentBrowser = undefined; renderAvailsGrid()")
    panel.wait_for_selector(".aud-notice--connected")
    assert panel.is_disabled('[data-meta-act="browse"]') and panel.is_disabled('[data-meta-act="browse-lang"]')
    reasons = panel.eval_on_selector_all("[data-meta-browse-reason]", "els => els.filter(e => !e.hidden).map(e => e.textContent)")
    assert reasons == ["The browse window didn’t load, so “Browse all” is off. Reload the page to get it back."] * 2
    assert panel.is_enabled('[data-meta-act="lang-quick"][data-key="spanish"]') and panel.is_enabled('[data-meta-ta-input="aud"]')       # the rest of the panel works
    panel.fill('[data-meta-ta-input="aud"]', "soccer")                                                # the "Browse all" row of the search list is off too
    panel.wait_for_selector('[data-meta-ta-list="aud"] .aud-ta-opt')
    assert panel.is_disabled('[data-meta-ta-list="aud"] [data-meta-act="browse"]')
    assert "didn’t load" in panel.get_attribute('[data-meta-ta-list="aud"] [data-meta-act="browse"]', "title")
    panel.click('[data-meta-act="browse"]', force=True)                                              # a click on an off control does nothing at all
    assert panel.locator(".segb-dialog").count() == 0 and panel.locator("[data-meta-error] .aud-notice").count() == 0
    assert panel.evaluate("contrastReport('.meta-panel')") == []


def test_with_the_browse_window_script_present_the_reasons_stay_hidden(panel):
    assert panel.is_enabled('[data-meta-act="browse"]') and panel.is_enabled('[data-meta-act="browse-lang"]')
    assert panel.eval_on_selector_all("[data-meta-browse-reason]", "els => els.every(e => e.hidden)")


# ---- money

def test_amounts_with_cents_show_two_decimals_and_whole_amounts_none_everywhere_in_the_panel(open_panel):
    saved = meta.model_auction(product_name=PRODUCT, retail_budget=33862.5, audience_lower=1_000_001, audience_upper=1_200_000, months=3, audience_source="meta_api", retail_cpm=12.5)
    saved["meta_targeting"] = {"geo_type": "geo_market", "geo_key": "803", "geo_name": "Los Angeles · California · US", "age_min": None, "age_max": None, "gender": "all",
                               "detailed": [], "languages": []}
    saved.update({"proposal_geo": "Los Angeles", "target_text": "Adults 25-54"})
    page = open_panel(saved=saved, lines=[{"id": "li_1", "product_name": PRODUCT, "monthly_budget": 11287.5, "months": 3}])
    page.evaluate("window._effectiveProduct = () => ({ estimated_cpm_for_imps: 12.5 }); renderAvailsGrid()")
    page.wait_for_selector(".meta-result .meta-scen")
    assert "Inputs changed" not in page.inner_text(".meta-result")
    strip, result = page.inner_text(".meta-plan"), page.inner_text(".meta-result")
    assert "$33,862.50" in strip and "$12.50" in strip and "$33,862.50 retail → $16,931.25 media at a $12.50 retail CPM" in result
    spends = page.eval_on_selector_all(".meta-card-rows dd:last-child", "els => els.map(e => e.textContent)")
    assert len(spends) == 3 and all(re.fullmatch(r"\$[\d,]+\.\d{2}|\$[\d,]+", x) for x in spends), spends
    assert not re.search(r"\$\d[\d,]*\.\d(?!\d)", page.inner_text(".meta-panel"))                  # never "$33,862.5"
