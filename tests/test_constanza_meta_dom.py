"""The Meta panel of the Constanza dock in a REAL browser (headless Chromium through Playwright): what a string test cannot show - which card says what as
the planner chooses, where the keyboard goes, what the pop-up window applies, whether the request is what the chips on the screen say, what Reset leaves
behind, what a line keeps while the dock is open for another.

The page is a small harness (not the whole app): the real constanza.js, constanza-meta.js, the shared audience kit and the SegmentBrowser pop-up (with their
CSS) against a stub `state.catalog` and a stub `window.constanzaHost` (what app.js gives the dock for a Step 05 line). The /api/constanza/* routes are answered
in the browser by the REAL route functions of app.main over a fake Graph API shaped like Meta's reference (tests/meta_support.FakeGraph, with a richer list of
places, interests and languages), so the contract the panel relies on (status, geographies, targeting lists, languages, the estimate) is the server's own.
The Step 05 card around a Meta line is tests/test_constanza_step05_block_dom.py; saved-proposal compatibility is tests/test_constanza_snapshot_compat.py.
Skipped when Playwright or its Chromium is not installed."""
from __future__ import annotations

import copy
import http.server
import json
import os
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
from tests.fixtures import meta_snapshots as fx  # noqa: E402
from tests.meta_support import ACCOUNT, TOKEN, FakeGraph, Resp  # noqa: E402

STATIC = Path(__file__).resolve().parents[1] / "app" / "static"
SHOTS = os.environ.get("TEST_SHOTS")
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")
PRODUCT = "Facebook & Instagram Ads | Awareness"
OTHER = "Facebook & Instagram Ads | Traffic / Conversion"


def _fn(name: str) -> str:
    m = re.search(rf"^function {re.escape(name)}\(", APP_JS, re.M)
    assert m, name
    return APP_JS[m.start(): APP_JS.index("\n}\n", m.start()) + 2]


HARNESS = """<!doctype html><html><head><meta charset="utf-8"><title>harness</title>
<link rel="stylesheet" href="/static/styles.css"><link rel="stylesheet" href="/static/constanza.css">
<link rel="stylesheet" href="/static/audience-kit.css"><link rel="stylesheet" href="/static/segment-browser.css"><link rel="stylesheet" href="/static/constanza-meta.css"></head><body>
<script>
  /*FUNCTIONS*/
  function _replaceGeoItem(t, i, r) { return r; }
  function _constanzaErrorText(d, f) { const x = d && d.detail; return typeof x === "string" && x ? x : (Array.isArray(x) && x.length ? x.map((e) => e.msg).join("; ") : (f || "Request failed.")); }
  const PRODUCT = %(product)s, OTHER = %(other)s;
  const meta = (name, cpm) => ({ name, family: "Social", is_addon: false, forecast: { enabled: true, method: "meta", series: null, info: { retail_cpm: cpm } } });
  var state = { lineItems: [], availsData: {}, parsed: {}, activeTierLabel: "A", catalog: { families: ["Entravision Plus", "Social"], products_by_family: {
    "Social": [meta(PRODUCT, 12), meta(OTHER, 18)],
    "Entravision Plus": [{ name: "Roku", family: "Entravision Plus", is_addon: false, forecast: { enabled: true, method: "platform", series: { key: "roku", channel: "platform_model", data_basis: "modeled", unit: "monthly" } } }] } } };
  window.__geo = "Los Angeles"; window.__target = "Adults 25-54"; window.__months = 3; window.__dates = true; window.__inserted = []; window.__dirty = [];
  // what app.js hands the dock for a Step 05 line (constanzaHost): the line's facts, its saved estimate, a place to save a new one
  window.constanzaHost = {
    lineFacts(lid) {
      const li = state.lineItems.find((x) => x.id === lid);
      if (!li) return null;
      return { id: li.id, product_name: li.product_name, method: "meta", label: li.product_name, months: window.__months, flight_days: window.__days || null, has_dates: window.__dates,
               geo_text: window.__geo, target_text: window.__target, budget: (Number(li.monthly_budget) || 0) * Math.max(1, Number(li.months) || 1), cpm: li.cpm == null ? 12 : li.cpm, hispanic: null };
    },
    snapshot(lid) {
      const e = state.availsData[lid];
      if (!e) return null;
      if (e.forecast_snapshot && e.forecast_snapshot.v === 1 && e.forecast_snapshot.method === "meta") return e.forecast_snapshot;
      return e.meta_forecast && window.Constanza.adoptLegacy ? window.Constanza.adoptLegacy("meta", e, this.lineFacts(lid)) : null;
    },
    saveSnapshot(lid, snap) {
      if (!state.lineItems.some((x) => x.id === lid)) return false;
      const e = Object.assign({}, state.availsData[lid] || {});
      if (snap) { e.forecast_snapshot = snap; delete e.meta_forecast; } else delete e.forecast_snapshot;
      state.availsData[lid] = e;
      return true;
    },
    onDirty(lid, dirty) { window.__dirty.push([lid, dirty]); },
  };
  function applyConstanzaEstimate(lid, imps, uniques, info, snapshot) { window.__inserted.push({ lid, imps, uniques, info, snapshot }); return true; }
  window.setup = function (cfg) {
    cfg = cfg || {};
    if ("geo" in cfg) window.__geo = cfg.geo;
    if (cfg.target) window.__target = cfg.target;
    if (cfg.months) window.__months = cfg.months;
    if (cfg.days) window.__days = cfg.days;
    if ("dates" in cfg) window.__dates = cfg.dates;
    state.parsed = { demo: "demo" in cfg ? cfg.demo : "Adults 25-54", behavioral: cfg.behavioral || "", geo: window.__geo };
    state.lineItems = cfg.lines || [{ id: "li_1", product_name: PRODUCT, monthly_budget: 2000, months: 1, target_override: cfg.targetOverride }];
    state.availsData = cfg.availsData || {};
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
    document.querySelectorAll(root + ", " + root + " *").forEach((el) => {
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
</script>
<script src="/static/segment-browser.js"></script>
<script src="/static/constanza.js"></script>
<script src="/static/constanza-meta.js"></script>
</body></html>"""


def harness_html() -> str:
    functions = "\n".join(_fn(name) for name in ("escapeHtml", "escapeAttr", "_targetsHispanic"))
    return HARNESS.replace("/*FUNCTIONS*/", functions).replace("%(product)s", json.dumps(PRODUCT)).replace("%(other)s", json.dumps(OTHER))


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
    request_queue_size = 128                  # the default backlog of 5 resets a page's parallel script and style requests when the machine is busy


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


# ----------------------------------------------------------------------------------------------------------------- a fake Graph API

def _interest(i, name, lower, upper, path):
    return {"id": str(6003000000000 + i), "name": name, "type": "interests", "path": path, "audience_size_lower_bound": lower, "audience_size_upper_bound": upper}


class RichGraph(FakeGraph):
    """The shared Graph fake with the longer lists the panel's behaviours need (places of every kind, interests, behaviors, demographics, ten languages),
    deterministic. The locale ids are INVENTED: the panel must take them from the answers, never from its own code."""

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
        super().__init__()
        self.reach_error: dict | None = None
        self.geos = [dict(g) for g in self.GEOS]                  # copies: a test may add or remove rows without touching the next test's world
        self.locales = [dict(r) for r in self.LOCALES]

    @staticmethod
    def reach_of(spec: dict) -> tuple[int, int]:
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
                return Resp({"error": self.reach_error}, 400)
            lower, upper = self.reach_of(json.loads(params["targeting_spec"]))
            return Resp({"data": {"users_lower_bound": lower, "users_upper_bound": upper, "estimate_ready": True}})
        kind = params.get("type")
        q = (params.get("q") or "").lower()
        if kind == "adgeolocation":
            wanted = json.loads(params["location_types"])
            return Resp({"data": [g for g in self.geos if g["type"] in wanted and q in g["name"].lower()]})
        if kind == "adinterest":
            return Resp({"data": [r for r in self.INTERESTS if q in r["name"].lower()]})
        if kind == "adTargetingCategory":
            return Resp({"data": {"interests": self.INTERESTS, "behaviors": self.BEHAVIORS, "demographics": self.DEMOGRAPHICS}[params["class"]]})
        if kind == "adlocale":
            rows = [r for r in self.locales if r["name"].lower().startswith(q)]
            return Resp({"data": rows[: int(params.get("limit", 8))]})
        return Resp({"error": {"message": "unknown", "code": 100}}, 400)


class World:
    """What the harness's /api answers: the REAL route functions of app.main over the fake Graph. A test can park an answer (`hold`) and release it later, or
    make a route fail (`fail`)."""

    NAMES = {"api/constanza/meta/status": "status", "api/constanza/meta-geos": "geos", "api/constanza/meta-targeting": "targeting",
             "api/constanza/meta-languages": "languages", "api/constanza/estimate": "estimate", "api/constanza/interest-taxonomy": "taxonomy",
             "api/constanza/resolve": "resolve"}

    def __init__(self, graph: RichGraph):
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
            elif name == "estimate":
                body = main.constanza_estimate(main.ConstanzaEstimateRequest(**payload))
            elif name == "taxonomy":                                  # the Entravision audience block's list: not this panel's, but the dock asks for it
                body = {"groups": []}
            elif name == "resolve":
                body = {"areas": [], "ambiguous": [], "suggestions": [], "interpretations": [], "unmatched_details": [], "unmatched_tokens": [], "notes": []}
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
    for name in ("CONSTANZA_AUDIENCE_X_CONSERVATIVE", "CONSTANZA_AUDIENCE_X_MID", "CONSTANZA_AUDIENCE_X_UPPER"):
        monkeypatch.delenv(name, raising=False)
    graph = RichGraph()
    monkeypatch.setattr(meta.requests, "get", graph.get)
    yield World(graph)
    meta.reset_cache()


@pytest.fixture
def open_dock(browser, server, world):
    """`open_dock(**setup)` opens the dock on the Awareness product and waits for the connection banner. Setup keys: line (open it for the Step 05 line
    li_1 instead of from the bubble), geo, target, demo, months, days, dates, availsData, targetOverride, lines, width, height, select (False: leave the
    product alone), block_browse (the pop-up's script never arrives); or `before=fn(world)` to prepare the fake world."""
    opened = []

    def _open(width=1280, height=1000, before=None, line=False, select=True, block_browse=False, **setup):
        if before:
            before(world)
        context = browser.new_context(viewport={"width": width, "height": height})
        page = context.new_page()
        page.set_default_timeout(8000)                                # a failing wait says so in seconds, not half a minute
        page.route("**/api/**", world.answer)
        page.route("**/fonts.googleapis.com/**", lambda r: r.abort())
        page.route("**/fonts.gstatic.com/**", lambda r: r.abort())
        if block_browse:
            page.route("**/segment-browser.js", lambda r: r.abort())
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" and "Failed to load resource" not in m.text else None)
        page.on("requestfailed", lambda r: errors.append(f"request failed: {r.url} {r.failure}") if "fonts.g" not in r.url and not (block_browse and r.url.endswith("segment-browser.js")) else None)
        page.goto(f"{server}/harness.html")
        loaded = page.evaluate("() => !!(window.Constanza && Constanza._plugin('meta'))")
        assert loaded, f"the page did not load the dock and the Meta plugin: {errors}"
        page.evaluate("cfg => window.setup(cfg)", setup)
        page.world, page.errors = world, errors                      # type: ignore[attr-defined]
        opened.append((context, errors))
        if line:
            open_line(page)
        else:
            page.evaluate("Constanza.open({})")
        if select and not line:
            choose_product(page, "awareness")
        if select:
            page.wait_for_selector('#cz-mt-status[data-state="on"], #cz-mt-status[data-state="off"], #cz-mt-status[data-state="error"]')
        page.wait_for_timeout(120)                                    # the dock places the keyboard 60 ms after it opens: wait, so a test starts where a planner is (type_as_the_dock_opens types sooner)
        return page

    yield _open
    for context, errors in opened:
        context.close()
        assert errors == [], errors


@pytest.fixture
def dock(open_dock):
    """The dock opened from the bubble (no proposal line) on the Awareness product, connected."""
    return open_dock()


@pytest.fixture
def line_dock(open_dock):
    """The dock opened for the Step 05 line li_1 (a $2,000 Awareness line, 3 calendar months), connected."""
    return open_dock(line=True)


# ----------------------------------------------------------------------------------------------------------------- helpers

def choose_product(page, query):
    page.click("#cz-product-btn")
    page.fill("#cz-picker-search", query)
    page.click("#cz-picker-list .cz-option")


def open_line(page, lid="li_1", geo=None, **extra):
    """What Step 05's "Estimate in Constanza" / "Change in Constanza" does: open the dock for one of the proposal's lines."""
    opts = {"lineId": lid, "product": PRODUCT, "geo": geo if geo is not None else page.evaluate("window.__geo"), "months": page.evaluate("window.__months"), "lineLabel": "Awareness"}
    opts.update(extra)
    page.evaluate("opts => Constanza.open(opts)", opts)
    page.wait_for_timeout(120)                                        # the dock places the keyboard 60 ms after it opens: wait, so a test starts where a planner is (type_as_the_dock_opens types sooner)


def shot(page, name):
    """TEST_SHOTS=<dir> also writes a screenshot of the layouts (to look at, not to assert on)."""
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(SHOTS) / f"{name}.png"))


def plug(page, expression):
    return page.evaluate(f"(() => {{ const P = Constanza._plugin('meta'); const X = P._pure; return {expression}; }})()")


def sel(page):
    return plug(page, "X.sel()")


def active_key(page):
    return plug(page, "X.T.active")


def sec(key):
    return f"#cz-mt-sec-{key}"


def summary(page, key):
    return page.inner_text(f"#cz-mt-{key}-sum").strip()


def chips(page, which):
    return page.eval_on_selector_all(f"#cz-mt-{which}-chips .aud-chip", "els => els.map(e => e.querySelector('.aud-chip-label').textContent)")


def options(page, which):
    return page.eval_on_selector_all(f"#cz-mt-{which}-l .aud-ta-opt", "els => els.map(e => e.querySelector('.aud-ta-name').textContent)")


def focused(page, selector):
    return page.evaluate(f"document.activeElement === document.querySelector({json.dumps(selector)})")


def pick_place(page, query="Los Angeles", kind=None):
    if kind:
        page.click(f'[data-mt-act="geotype"][data-type="{kind}"]')
    page.fill("#cz-mt-geo-q", query)
    page.wait_for_selector("#cz-mt-geo-l .aud-ta-opt")
    page.click("#cz-mt-geo-l .aud-ta-opt")


def estimate(page):
    page.click("#cz-go")
    page.wait_for_selector("#cz-result .mt-scen")


def estimate_body(page):
    return page.world.of("estimate")[-1]                             # type: ignore[attr-defined]


def snap_for(**changes):
    """The native saved estimate of the fixtures, filed under the harness's product (a saved proposal's own request and result carry the product name)."""
    snapshot = fx.native()
    snapshot["request"]["product_name"] = snapshot["result"]["product_name"] = snapshot["line"]["product_name"] = PRODUCT
    snapshot["request"]["meta"].update(changes.pop("meta", {}))
    snapshot.update(changes)
    return snapshot


def legacy_for(source=None):
    old = copy.deepcopy(source or fx.LIVE_WITH_LANGUAGES)
    old["product_name"] = PRODUCT
    return old


# ----------------------------------------------------------------------------------------------------------------- the panel replaces the audience cards and the shared Where

def test_the_dock_shows_metas_where_and_its_own_cards_numbered_one_to_six_and_hides_what_they_replace(dock):
    assert dock.is_hidden("#cz-sec-where") and dock.is_hidden("#cz-aud") and dock.is_visible("#cz-sec-when") and dock.is_visible("#cz-mt")
    assert dock.inner_text("#cz-go").strip() == "Estimate audience →"
    titles = dock.eval_on_selector_all("#cz-builder .aud-sec-title", "els => els.filter(e => e.getClientRects().length).map(e => e.textContent.trim())")
    assert titles == ["Where", "When", "Who", "Language", "Audience", "Budget cross-check"]
    # the kit numbers the cards by itself (a CSS counter that steps once per rendered .aud-sec): Meta's Where takes the shared one's place, so the six
    # cards that draw are the six numbers, in the dock's order, and the two it replaces take no number at all
    drawn = dock.evaluate("""() => [...document.querySelectorAll('#cz-builder .aud-sec')].filter(e => e.getClientRects().length).map(e => e.id)""")
    assert drawn == ["cz-mt-sec-where", "cz-sec-when", "cz-mt-sec-who", "cz-mt-sec-lang", "cz-mt-sec-aud", "cz-mt-sec-budget"]
    assert dock.evaluate("""() => [...document.querySelectorAll('#cz-builder .aud-step')].filter(e => e.getClientRects().length).length""") == 6
    assert dock.evaluate("getComputedStyle(document.getElementById('cz-sec-where')).display") == "none" and dock.evaluate("getComputedStyle(document.getElementById('cz-aud')).display") == "none"
    assert dock.evaluate("getComputedStyle(document.querySelector('#cz-mt .aud-builder')).counterReset") == "none"        # the panel's nested builder does not restart the numbers
    assert [summary(dock, k) for k in ("where", "who", "lang", "aud")] == ["Choose a place", "All genders · all ages", "Any language", "No interests or behaviors yet"]
    assert dock.locator("#cz-mt .aud-sec").count() == 4 and dock.locator("#cz-mt details > summary").count() == 2     # the language caveat and the budget card fold
    assert dock.locator("#cz-mt input[type=number]").count() == 0
    choose_product(dock, "roku")                                      # another product: the shared cards are back, Meta's are gone, nothing was lost
    assert dock.is_visible("#cz-sec-where") and dock.is_visible("#cz-aud") and dock.is_hidden("#cz-mt") and dock.is_hidden("#cz-mt-sec-where")
    choose_product(dock, "awareness")
    assert dock.is_hidden("#cz-sec-where") and dock.is_visible("#cz-mt-sec-where")


def test_the_shared_geography_box_is_left_alone_and_the_request_sends_no_geography_text(dock):
    dock.evaluate("Constanza._state.geo = 'Phoenix'")
    choose_product(dock, "roku")                                      # the shared box is back for a data product: what was typed in it is matched again
    dock.wait_for_function("() => !Constanza._state.resolving")
    choose_product(dock, "awareness")
    assert dock.evaluate("Constanza._state.geo") == "Phoenix"         # what was typed for a data product is still there when the other product is chosen back
    resolved = dock.world.hits.count("resolve")                       # type: ignore[attr-defined]
    pick_place(dock)
    estimate(dock)
    body = estimate_body(dock)
    assert body["geo_text"] == "" and body["geo_type_hint"] is None and "audience" in body and body["audience"] is None
    assert dock.world.hits.count("resolve") == resolved               # type: ignore[attr-defined]  # nothing was matched for Meta: its place is the one picked from Meta's list


def test_the_budget_card_is_folded_and_optional_on_its_own(dock):
    assert dock.get_attribute("#cz-mt-sec-budget", "open") is None and summary(dock, "budget") == "None · optional"
    dock.click("#cz-mt-sec-budget > summary")
    assert dock.is_visible("#cz-mt-budget") and dock.is_hidden("#cz-mt-budget-line")
    dock.fill("#cz-mt-budget", "2,000")
    assert summary(dock, "budget") == "$2,000 for the flight"
    dock.fill("#cz-mt-budget", "lots")
    assert "dollar amount" in dock.inner_text("#cz-mt-budget-note") and summary(dock, "budget") == "None · optional"


def test_the_budget_note_on_its_own_says_what_the_amount_is_checked_at_without_a_figure_the_dock_never_gave_the_plugin(dock):
    """The dock's product object carries no planning CPM (loadProducts keeps name, family, method, series, channel and the Hispanic flags), so the note
    cannot name one; the result's cross-check sentence does."""
    assert not {"info", "forecast"} & set(plug(dock, "Object.keys(X.T.product)"))
    dock.click("#cz-mt-sec-budget > summary")
    dock.fill("#cz-mt-budget", "2,000")
    assert dock.inner_text("#cz-mt-budget-note").strip() == "Checked at the product’s planning CPM."
    dock.fill("#cz-mt-budget", "lots")
    assert dock.inner_text("#cz-mt-budget-note").strip() == "Type a dollar amount, like 2,000."
    pick_place(dock)
    dock.fill("#cz-mt-budget", "2,000")
    estimate(dock)
    assert "$12 retail CPM" in dock.inner_text("#cz-result .mt-check")


# ----------------------------------------------------------------------------------------------------------------- the connection

def test_the_banner_says_connected_with_the_ad_account_hint_and_the_rule_and_asks_once(open_dock, world):
    page = open_dock()
    banner = page.inner_text("#cz-mt-status")
    assert "Connected to Meta" in banner and "…2345" in banner
    assert "v26.0" in banner and "lower-end audience × 5.5 (4.3 – 8.6) a month" in banner and TOKEN not in banner
    assert page.get_attribute("#cz-mt-status", "class").split() == ["aud-notice", "aud-notice--connected"]
    assert world.hits.count("status") == 1
    choose_product(page, "roku")                                      # choosing again, switching away and back: still the one answer (kept 30 seconds)
    choose_product(page, "awareness")
    page.evaluate("Constanza.open({})")
    assert world.hits.count("status") == 1 and page.inner_text("#cz-go-reason").strip() == ""
    page.evaluate("Constanza._plugin('meta')._pure.T.status.at = Date.now() - 31000; Constanza._plugin('meta').connection()")           # older than the cache: asked again, once
    page.wait_for_function("() => { const s = Constanza._plugin('meta')._pure.T.status; return s && !s.pending && Date.now() - s.at < 5000; }")
    assert world.hits.count("status") == 2


def test_nothing_is_asked_of_meta_until_the_product_is_chosen_or_a_step_05_line_asks(open_dock, world):
    page = open_dock(select=False)
    assert world.hits.count("status") == 0
    page.evaluate("Constanza._state.open = true; Constanza._plugin('meta').connection(); Constanza._plugin('meta').connection()")      # the card's first connection() asks, once
    page.wait_for_function("() => Constanza._plugin('meta').connection() === true")
    assert world.hits.count("status") == 1
    assert world.hits.count("geos") == world.hits.count("targeting") == world.hits.count("languages") == 0


def test_the_panel_draws_at_once_with_a_checking_banner_while_the_status_is_on_its_way(open_dock, world):
    page = open_dock(before=lambda w: w.hold.add("status"), select=False)
    choose_product(page, "awareness")
    assert page.get_attribute("#cz-mt-status", "data-state") == "checking" and "Checking the connection to Meta" in page.inner_text("#cz-mt-status")
    assert page.get_attribute("#cz-go", "aria-disabled") == "false"                       # not known to be off: the estimate tells its own error
    world.release("status")
    page.wait_for_selector('#cz-mt-status[data-state="on"]')


def test_not_configured_names_what_is_missing_and_switches_the_estimate_off_with_the_reason(open_dock, monkeypatch):
    monkeypatch.delenv("META_ACCESS_TOKEN")
    monkeypatch.delenv("META_AD_ACCOUNT_ID")
    page = open_dock()
    page.wait_for_selector('#cz-mt-status[data-state="off"]')
    banner = page.inner_text("#cz-mt-status")
    assert "Meta isn’t connected yet" in banner and "META_ACCESS_TOKEN" in banner and "META_AD_ACCOUNT_ID" in banner
    assert page.locator("#cz-mt-status.aud-notice--warn code").count() == 2
    assert page.get_attribute("#cz-go", "aria-disabled") == "true"
    reason = page.inner_text("#cz-go-reason")
    assert "Meta isn't connected yet" in reason and "META_ACCESS_TOKEN and META_AD_ACCOUNT_ID" in reason and page.is_visible("#cz-go-reason")
    # the controls that would only ask Meta (and get this same answer) are off, with the reason beside the first of them; age and gender are local and stay usable
    assert page.is_disabled("#cz-mt-geo-q") and page.is_disabled('[data-mt-act="browse"]') and page.is_disabled('#cz-mt-lang-quick [data-mt-act="lang-quick"][data-key="spanish"]')
    assert "META_ACCESS_TOKEN" in page.inner_text("#cz-mt-sec-where [data-mt-off]") and page.is_enabled('[data-mt-act="gender"][data-gender="female"]')
    page.click("text=How an admin sets this up")
    assert "META_ACCESS_TOKEN" in page.inner_text("#cz-mt-status details p") and "Replit" in page.inner_text("#cz-mt-status details p")
    page.dispatch_event("#cz-go", "click")                            # a press on the off button does nothing (a real click would wait for it to be enabled)
    page.wait_for_timeout(300)
    assert page.world.hits.count("estimate") == 0 and page.get_attribute("#cz-go", "aria-disabled") == "true"     # type: ignore[attr-defined]


def test_an_unreachable_status_check_says_so_and_can_be_asked_again(open_dock, world):
    page = open_dock(before=lambda w: w.fail.update({"status": (503, {"detail": "The server is waking up."})}))
    page.wait_for_selector('#cz-mt-status[data-state="error"]')
    assert "Couldn’t check the Meta connection" in page.inner_text("#cz-mt-status") and "The server is waking up." in page.inner_text("#cz-mt-status")
    assert page.get_attribute("#cz-go", "aria-disabled") == "false"   # unknown is not "off": the estimate then tells its own error
    world.fail.clear()
    page.click('[data-mt-act="recheck"]')
    page.wait_for_selector('#cz-mt-status[data-state="on"]')


def test_the_connection_banner_is_a_status_region_that_stays_put_while_its_words_change(open_dock, world):
    page = open_dock(before=lambda w: w.fail.update({"status": (503, {"detail": "The server is waking up."})}))
    assert page.get_attribute("#cz-mt-status", "role") == "status"
    assert page.get_by_role("status").filter(has_text="Couldn’t check the Meta connection").count() == 1
    page.evaluate("window.__banner = document.getElementById('cz-mt-status')")
    world.fail.clear()
    page.click('[data-mt-act="recheck"]')
    page.wait_for_selector('#cz-mt-status[data-state="on"]')
    assert page.evaluate("window.__banner === document.getElementById('cz-mt-status')")      # the region is not replaced, so a screen reader announces what changed in it
    assert page.get_by_role("status").filter(has_text="Connected to Meta").count() == 1


def test_check_again_keeps_the_keyboard_on_the_new_button_while_the_check_still_fails_and_on_the_banner_once_it_works(open_dock, world):
    page = open_dock(before=lambda w: w.fail.update({"status": (503, {"detail": "The server is waking up."})}))
    page.wait_for_selector('#cz-mt-status[data-state="error"]')
    page.evaluate("window.__old = document.querySelector('[data-mt-act=\"recheck\"]')")
    page.focus('[data-mt-act="recheck"]')
    page.keyboard.press("Enter")                                      # still failing: the banner is drawn again and the button in it has the keyboard
    page.wait_for_function("() => { const b = document.querySelector('[data-mt-act=\"recheck\"]'); return !!b && b !== window.__old; }")
    assert focused(page, '[data-mt-act="recheck"]') and world.hits.count("status") == 2
    world.fail.clear()
    page.keyboard.press("Enter")                                      # it works now: there is no button any more, the banner itself holds the keyboard
    page.wait_for_selector('#cz-mt-status[data-state="on"]')
    assert page.evaluate("document.activeElement.id") == "cz-mt-status"
    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement !== document.body")  # the next Tab goes on from there, not from the top of the page


def test_a_banner_the_keyboard_is_not_in_does_not_take_it(open_dock, world):
    page = open_dock(before=lambda w: w.fail.update({"status": (503, {"detail": "The server is waking up."})}))
    page.wait_for_selector('#cz-mt-status[data-state="error"]')
    world.fail.clear()
    page.focus('[data-mt-act="gender"][data-gender="female"]')
    page.evaluate("Constanza._plugin('meta')._pure.T.status.at = 0; Constanza._plugin('meta').connection()")           # the answer is old: asked again in the background
    page.wait_for_selector('#cz-mt-status[data-state="on"]')
    assert focused(page, '[data-mt-act="gender"][data-gender="female"]')


def test_a_connection_that_changed_under_the_planner_is_shown_when_the_estimate_comes_back_refused(dock, world):
    pick_place(dock)
    world.fail["estimate"] = (503, {"detail": "Meta isn't connected yet - an admin needs to add META_ACCESS_TOKEN."})
    dock.click("#cz-go")
    dock.wait_for_selector("#cz-result .cz-error")
    assert "META_ACCESS_TOKEN" in dock.inner_text("#cz-result .cz-error")
    assert world.hits.count("status") == 2                           # the 503 asked again: the banner is never out of date for long


# ----------------------------------------------------------------------------------------------------------------- 1 Where

def test_the_proposals_geography_is_a_one_click_search_not_an_automatic_one(open_dock, world):
    page = open_dock(line=True, geo="Phoenix, AZ and Los Angeles DMA")
    pills = page.eval_on_selector_all("#cz-mt-geo-pills button", "els => els.map(e => e.textContent)")
    assert pills == ["Search “Phoenix”", "Search “Los Angeles”"]
    assert world.hits.count("geos") == 0                              # nothing asked of Meta until the planner asks
    page.click('#cz-mt-geo-pills button >> text=Los Angeles')
    page.wait_for_selector("#cz-mt-geo-l .aud-ta-opt")
    assert world.of("geos") == [{"query": "Los Angeles", "location_type": "geo_market"}]
    assert page.input_value("#cz-mt-geo-q") == "Los Angeles"
    assert options(page, "geo") == ["Los Angeles", "Los Angeles-Long Beach"]
    assert focused(page, "#cz-mt-geo-q")


def test_no_pill_when_the_proposal_names_no_place_and_none_from_the_bubble(open_dock):
    assert open_dock(line=True, geo="TBD").locator("#cz-mt-geo-pills button").count() == 0
    assert open_dock().locator("#cz-mt-geo-pills button").count() == 0


def test_picking_a_place_shows_a_chip_with_its_type_and_the_summary_names_it(dock):
    pick_place(dock)
    assert chips(dock, "geo") == ["Los Angeles"]
    assert dock.inner_text("#cz-mt-geo-chips .aud-chip-meta") == "TV market · California"
    assert summary(dock, "where") == "Los Angeles · TV market"
    assert dock.get_attribute(sec("where"), "class").count("has-value") == 1 and dock.is_visible(f'{sec("where")} .aud-clear')
    assert dock.is_hidden("#cz-mt-geo-l") and dock.input_value("#cz-mt-geo-q") == ""
    assert "Comscore" in dock.inner_text("#cz-mt-geohint")
    dock.click("#cz-mt-geo-chips .aud-chip-x")
    assert chips(dock, "geo") == [] and summary(dock, "where") == "Choose a place" and focused(dock, "#cz-mt-geo-q")


def test_the_kind_of_place_decides_what_is_searched_and_what_is_chosen_keeps_its_own_type(dock, world):
    pick_place(dock, "Los Angeles", kind="city")
    assert world.of("geos")[-1] == {"query": "Los Angeles", "location_type": "city"}
    assert summary(dock, "where") == "Los Angeles · City"
    assert dock.get_attribute('[data-mt-act="geotype"][data-type="city"]', "aria-pressed") == "true"
    assert "City, e.g." in dock.get_attribute("#cz-mt-geo-q", "placeholder") and dock.inner_text("#cz-mt-geohint").strip() == ""
    dock.click('[data-mt-act="geotype"][data-type="zip"]')            # switching the kind does not drop what was chosen
    assert summary(dock, "where") == "Los Angeles · City" and "ZIP code" in dock.get_attribute("#cz-mt-geo-q", "placeholder")
    estimate(dock)
    assert estimate_body(dock)["meta"]["geo"] == {"type": "city", "key": "2420379", "label": "Los Angeles · California · US"}


def test_the_place_search_works_from_the_keyboard(dock):
    dock.focus("#cz-mt-geo-q")
    dock.keyboard.type("los")
    dock.wait_for_selector("#cz-mt-geo-l .aud-ta-opt")
    assert dock.get_attribute("#cz-mt-geo-q", "aria-expanded") == "true"
    dock.keyboard.press("ArrowDown")
    dock.keyboard.press("ArrowDown")
    active = dock.get_attribute("#cz-mt-geo-q", "aria-activedescendant")
    assert dock.inner_text(f"#{active} .aud-ta-name") == "Los Angeles-Long Beach" and dock.get_attribute(f"#{active}", "aria-selected") == "true"
    dock.keyboard.press("Escape")                                     # closes the list, nothing else (the dock stays)
    assert dock.is_hidden("#cz-mt-geo-l") and dock.get_attribute("#cz-mt-geo-q", "aria-expanded") == "false" and dock.is_visible("#constanza-dock")
    dock.keyboard.press("ArrowDown")                                  # opens it again, on the same hits
    assert dock.is_visible("#cz-mt-geo-l")
    dock.keyboard.press("ArrowDown")
    dock.keyboard.press("Enter")
    assert summary(dock, "where") == "Los Angeles-Long Beach · TV market" and focused(dock, "#cz-mt-geo-q")


def test_a_place_that_meta_does_not_know_and_a_failed_search_say_so_in_the_list(dock, world):
    dock.fill("#cz-mt-geo-q", "zzzz")
    dock.wait_for_selector("#cz-mt-geo-l >> text=Meta found no matching place")
    assert "Comscore" in dock.inner_text("#cz-mt-geo-l")
    world.fail["geos"] = (503, {"detail": "Meta rejected the access token."})
    dock.fill("#cz-mt-geo-q", "phoenix")
    dock.wait_for_selector("#cz-mt-geo-l >> text=Meta rejected the access token.")
    dock.fill("#cz-mt-geo-q", "p")
    assert "at least two letters" in dock.inner_text("#cz-mt-geo-l")


def test_a_slow_answer_for_an_earlier_search_never_replaces_the_newer_one(dock, world):
    world.hold.add("geos")
    dock.fill("#cz-mt-geo-q", "Los Angeles")
    dock.wait_for_timeout(450)
    dock.fill("#cz-mt-geo-q", "Phoenix")
    dock.wait_for_timeout(450)
    assert len(world.pending) == 2
    world.release("geos", newest_first=True)                          # Phoenix answers first, then the older Los Angeles one
    dock.wait_for_timeout(300)
    assert options(dock, "geo") == ["Phoenix"]


def test_a_place_name_with_markup_is_shown_as_text_everywhere(dock, world):
    world.graph.geos.append({"key": "9", "name": "<img src=x onerror=alert(1)>", "type": "geo_market", "country_code": "US", "region": "<b>CA</b>"})
    pick_place(dock, "<img")
    assert dock.locator("#cz-mt img, #cz-mt-sec-where img, #cz-mt-sec-where b").count() == 0
    assert chips(dock, "geo") == ["<img src=x onerror=alert(1)>"] and "<img src=x onerror=alert(1)>" in summary(dock, "where")
    estimate(dock)
    assert dock.locator("#cz-result img, #cz-result b").count() == 0 and "<img src=x onerror=alert(1)>" in dock.inner_text("#cz-result")


# ----------------------------------------------------------------------------------------------------------------- 3 Who

def test_age_and_gender_choices_update_the_summary_and_the_presets(dock):
    dock.select_option("#cz-mt-age-min", "25")
    dock.select_option("#cz-mt-age-max", "44")
    dock.click('[data-mt-act="gender"][data-gender="female"]')
    assert summary(dock, "who") == "Women · 25–44"
    assert dock.get_attribute('[data-mt-act="gender"][data-gender="female"]', "aria-pressed") == "true"
    assert dock.get_attribute('[data-mt-act="gender"][data-gender="all"]', "aria-pressed") == "false"
    assert dock.inner_text("#cz-mt-count-who") == "2"
    dock.click('[data-mt-act="age-preset"] >> text=55+')
    assert summary(dock, "who") == "Women · 55+" and dock.input_value("#cz-mt-age-min") == "55" and dock.input_value("#cz-mt-age-max") == "65"
    assert dock.get_attribute('[data-mt-act="age-preset"] >> text=55+', "aria-pressed") == "true"
    assert dock.inner_text('#cz-mt-age-max option[value="65"]') == "65+"
    dock.click('[data-mt-act="gender"][data-gender="male"]')
    assert summary(dock, "who") == "Men · 55+"
    dock.click("#cz-mt-clear-who")                                    # clears both, and the keyboard goes to the card, not to the page
    assert summary(dock, "who") == "All genders · all ages" and dock.is_hidden("#cz-mt-clear-who")
    assert dock.evaluate("document.activeElement && document.activeElement.closest('#cz-mt-sec-who') !== null")


def test_the_oldest_age_cannot_be_set_below_the_youngest(dock):
    dock.select_option("#cz-mt-age-min", "40")
    assert dock.is_disabled('#cz-mt-age-max option[value="39"]') and dock.is_enabled('#cz-mt-age-max option[value="40"]')
    dock.select_option("#cz-mt-age-max", "50")
    assert dock.is_disabled('#cz-mt-age-min option[value="51"]') and dock.is_enabled('#cz-mt-age-min option[value="50"]')
    assert summary(dock, "who") == "All genders · 40–50"


# ----------------------------------------------------------------------------------------------------------------- 4 Language

def test_the_spanish_quick_pick_is_looked_up_live_and_takes_every_locale_called_spanish(dock, world):
    dock.click('#cz-mt-lang-quick [data-mt-act="lang-quick"][data-key="spanish"]')
    dock.wait_for_selector("#cz-mt-lang-chips .aud-chip")
    assert world.of("languages") == [{"q": "Spanish"}]                # asked of Meta by name: no locale id is kept in the page
    assert chips(dock, "lang") == ["Spanish"] and dock.inner_text("#cz-mt-lang-chips .aud-chip-meta") == "3 versions"
    assert "Spanish (Mexico)" in dock.get_attribute("#cz-mt-lang-chips .aud-chip", "title")
    assert [lang["id"] for lang in sel(dock)["languages"]] == ["1001", "1002", "1003"]      # ids as Meta's list gave them (the invented ones)
    assert summary(dock, "lang") == "Spanish" and dock.get_attribute('#cz-mt-lang-quick [data-mt-act="lang-quick"][data-key="spanish"]', "aria-pressed") == "true"
    assert dock.get_attribute('#cz-mt-lang-quick [data-mt-act="lang-quick"][data-key="english"]', "aria-pressed") == "false"
    assert not any(c.startswith("Español") for c in chips(dock, "lang"))
    dock.click('#cz-mt-lang-quick [data-mt-act="lang-quick"][data-key="spanish"]')      # pressed: a second click takes them out
    assert chips(dock, "lang") == [] and summary(dock, "lang") == "Any language"
    dock.click('#cz-mt-lang-quick [data-mt-act="lang-quick"][data-key="spanish"]')
    dock.wait_for_selector("#cz-mt-lang-chips .aud-chip")
    assert len(world.of("languages")) == 1                            # remembered for the session: no second lookup


def test_english_and_the_spanish_plus_english_combination(dock):
    dock.click('#cz-mt-lang-quick [data-mt-act="lang-quick"][data-key="english"]')
    dock.wait_for_selector("#cz-mt-lang-chips .aud-chip")
    assert chips(dock, "lang") == ["English"] and [lang["name"] for lang in sel(dock)["languages"]] == ["English (UK)", "English (US)"]
    dock.click('#cz-mt-lang-quick [data-mt-act="lang-quick"][data-key="both"]')
    dock.wait_for_function("() => Constanza._plugin('meta')._pure.sel().languages.length === 5")
    assert chips(dock, "lang") == ["English", "Spanish"] and summary(dock, "lang") == "English · Spanish"
    assert dock.get_attribute('#cz-mt-lang-quick [data-mt-act="lang-quick"][data-key="both"]', "aria-pressed") == "true" and dock.inner_text("#cz-mt-count-lang") == "2"
    dock.click("#cz-mt-lang-chips .aud-chip >> nth=0 >> .aud-chip-x")     # removing the English group leaves Spanish, keyboard on the next chip
    assert chips(dock, "lang") == ["Spanish"] and dock.evaluate("document.activeElement.classList.contains('aud-chip-x')")
    dock.click("#cz-mt-clear-lang")
    assert chips(dock, "lang") == [] and summary(dock, "lang") == "Any language"


def test_a_language_meta_does_not_list_or_a_failed_lookup_says_so_and_changes_nothing(dock, world):
    every = list(world.graph.locales)
    world.graph.locales[:] = [r for r in world.graph.locales if not r["name"].startswith("Spanish")]
    dock.click('#cz-mt-lang-quick [data-mt-act="lang-quick"][data-key="spanish"]')
    dock.wait_for_selector(f"{sec('lang')} .aud-notice--error:visible")
    assert "Meta lists no language called Spanish" in dock.inner_text(f"{sec('lang')} .aud-notice--error")
    assert chips(dock, "lang") == [] and dock.is_enabled('#cz-mt-lang-quick [data-mt-act="lang-quick"][data-key="spanish"]')
    world.graph.locales[:] = every                                    # Meta lists it after all: an empty answer is not remembered, the next press asks again
    dock.click('#cz-mt-lang-quick [data-mt-act="lang-quick"][data-key="spanish"]')
    dock.wait_for_selector("#cz-mt-lang-chips .aud-chip")
    assert chips(dock, "lang") == ["Spanish"] and dock.is_hidden(f"{sec('lang')} .aud-notice--error") and len(world.of("languages")) == 2
    dock.click("#cz-mt-clear-lang")
    world.fail["languages"] = (503, {"detail": "Meta rejected the access token."})
    dock.click('#cz-mt-lang-quick [data-mt-act="lang-quick"][data-key="english"]')
    dock.wait_for_selector(f"{sec('lang')} .aud-notice--error:visible >> text=Meta rejected the access token.")
    assert chips(dock, "lang") == []


SPANISH = '#cz-mt-lang-quick [data-mt-act="lang-quick"][data-key="spanish"]'
TWO_LINES = [{"id": "li_1", "product_name": PRODUCT, "monthly_budget": 2000, "months": 1}, {"id": "li_2", "product_name": PRODUCT, "monthly_budget": 1000, "months": 1}]


def quick_busy(page):
    return plug(page, "!!X.T.quickBusy.spanish")


def languages_by_line(page):
    """The languages chosen in each Step 05 line's workspace (the dock-on-its-own workspace, which the bubble's first select leaves behind, is not a line)."""
    return plug(page, "Object.fromEntries(Object.entries(X.T.byKey).filter(([k]) => k !== 'dock').map(([k, w]) => [k, w.sel.languages.map(l => l.name)]))")


def test_a_quick_language_answer_that_comes_back_after_the_dock_moved_to_another_line_is_dropped(open_dock, world):
    page = open_dock(line=True, lines=TWO_LINES)
    world.hold.add("languages")                                       # Meta is slow: the first press of the session is still being answered
    page.click(SPANISH)
    page.wait_for_function("() => Constanza._plugin('meta')._pure.T.quickBusy.spanish === true")
    open_line(page, "li_2")                                           # "Change in Constanza" on the other card, before the answer
    asked = page.evaluate("window.__dirty.length")
    world.hold.discard("languages")
    world.release("languages")
    page.wait_for_function("() => !Constanza._plugin('meta')._pure.T.quickBusy.spanish")
    assert languages_by_line(page) == {"li_1": [], "li_2": []}        # it went to neither line: not to the one on screen, and not to the one it was asked for
    assert chips(page, "lang") == [] and summary(page, "lang") == "Any language" and plug(page, "X.peek('li_2').touched") is False
    assert page.evaluate("window.__dirty.length") == asked            # nothing told the dock that line B's request changed
    assert page.get_attribute(SPANISH, "aria-busy") is None and page.get_attribute(SPANISH, "aria-disabled") is None     # the pick is free again
    open_line(page, "li_1")
    assert chips(page, "lang") == [] and summary(page, "lang") == "Any language"             # the line it was pressed on is as it was
    page.click(SPANISH)                                               # pressing it again finds the lookup remembered
    page.wait_for_selector("#cz-mt-lang-chips .aud-chip")
    assert languages_by_line(page)["li_1"] == ["Spanish", "Spanish (Mexico)", "Spanish (Spain)"] and len(world.of("languages")) == 1


def test_a_quick_language_answer_that_comes_back_after_reset_does_not_put_spanish_in_the_clean_dock(open_dock, world):
    page = open_dock()
    world.hold.add("languages")
    page.click(SPANISH)
    page.wait_for_function("() => Constanza._plugin('meta')._pure.T.quickBusy.spanish === true")
    page.click("#cz-reset")
    world.hold.discard("languages")
    world.release("languages")
    page.wait_for_function("() => !Constanza._plugin('meta')._pure.T.quickBusy.spanish")
    page.wait_for_timeout(100)
    assert plug(page, "X.sel().languages.length") == 0 and chips(page, "lang") == [] and summary(page, "lang") == "Any language"
    assert plug(page, "X.peek('dock').touched") is False
    assert page.get_attribute(SPANISH, "aria-busy") is None and page.get_attribute(SPANISH, "aria-disabled") is None


def test_a_failed_quick_language_lookup_that_comes_back_after_a_line_switch_is_not_shown_on_the_other_line(open_dock, world):
    page = open_dock(line=True, lines=TWO_LINES)
    world.fail["languages"] = (503, {"detail": "Meta rejected the access token."})          # the answer, already decided, is held until released
    world.hold.add("languages")
    page.click(SPANISH)
    page.wait_for_function("() => Constanza._plugin('meta')._pure.T.quickBusy.spanish === true")
    open_line(page, "li_2")
    world.hold.discard("languages")
    world.release("languages")
    page.wait_for_function("() => !Constanza._plugin('meta')._pure.T.quickBusy.spanish")
    page.wait_for_timeout(100)
    assert page.is_hidden(f"{sec('lang')} .aud-notice--error") and languages_by_line(page) == {"li_1": [], "li_2": []}
    assert page.get_attribute(SPANISH, "aria-busy") is None and page.get_attribute(SPANISH, "aria-disabled") is None


def test_a_quick_pick_keeps_the_keyboard_while_meta_is_asked_and_a_second_press_does_nothing(dock, world):
    world.hold.add("languages")
    dock.focus(SPANISH)
    dock.keyboard.press("Enter")
    dock.wait_for_function("() => Constanza._plugin('meta')._pure.T.quickBusy.spanish === true")
    assert focused(dock, SPANISH)                                     # a `disabled` button would have dropped the keyboard to the page
    assert dock.get_attribute(SPANISH, "aria-disabled") == "true" and dock.get_attribute(SPANISH, "aria-busy") == "true" and dock.evaluate(f"document.querySelector('{SPANISH}').disabled") is False
    dock.keyboard.press("Enter")
    dock.keyboard.press("Space")
    dock.dispatch_event(SPANISH, "click")
    dock.wait_for_timeout(100)
    assert len(world.of("languages")) == 1                            # still the one question
    world.hold.discard("languages")
    world.release("languages")
    dock.wait_for_selector("#cz-mt-lang-chips .aud-chip")
    assert focused(dock, SPANISH) and dock.get_attribute(SPANISH, "aria-busy") is None and dock.get_attribute(SPANISH, "aria-disabled") is None
    assert dock.get_attribute(SPANISH, "aria-pressed") == "true" and chips(dock, "lang") == ["Spanish"]
    dock.keyboard.press("Enter")                                      # the keyboard is still on the pick, so a second Enter takes the languages out
    assert chips(dock, "lang") == [] and dock.get_attribute(SPANISH, "aria-pressed") == "false" and focused(dock, SPANISH)


def test_a_quick_pick_that_cannot_be_answered_keeps_the_keyboard_too(dock, world):
    world.fail["languages"] = (503, {"detail": "Meta rejected the access token."})
    dock.focus(SPANISH)
    dock.keyboard.press("Enter")
    dock.wait_for_selector(f"{sec('lang')} .aud-notice--error:visible")
    assert focused(dock, SPANISH) and dock.get_attribute(SPANISH, "aria-busy") is None and dock.get_attribute(SPANISH, "aria-disabled") is None
    world.fail.clear()
    dock.keyboard.press("Enter")                                      # and a second try goes through
    dock.wait_for_selector("#cz-mt-lang-chips .aud-chip")
    assert chips(dock, "lang") == ["Spanish"]


def test_any_other_language_is_searched_in_metas_list_with_accents_ignored(dock, world):
    dock.fill("#cz-mt-lang-q", "portu")
    dock.wait_for_selector("#cz-mt-lang-l .aud-ta-opt")
    assert world.of("languages") == [{}]                              # the whole list, asked once
    assert options(dock, "lang") == ["Portuguese (Brazil)", "Portuguese (Portugal)"]
    assert dock.inner_html("#cz-mt-lang-l .aud-ta-name >> nth=0").startswith("<mark>Portu</mark>")
    dock.keyboard.press("ArrowDown")
    dock.keyboard.press("Enter")
    assert chips(dock, "lang") == ["Portuguese (Brazil)"] and summary(dock, "lang") == "Portuguese (Brazil)"
    dock.fill("#cz-mt-lang-q", "espanol")                             # "Español" is found by "espanol"
    dock.wait_for_selector("#cz-mt-lang-l .aud-ta-opt")
    assert options(dock, "lang") == ["Español"]
    dock.fill("#cz-mt-lang-q", "portugu")
    dock.wait_for_selector("#cz-mt-lang-l .aud-ta-opt.is-disabled")
    assert "Added" in dock.inner_text("#cz-mt-lang-l .aud-ta-opt.is-disabled")      # what is already chosen cannot be added twice
    assert len(world.of("languages")) == 1
    dock.fill("#cz-mt-lang-q", "klingon")
    assert "Meta lists no language matching" in dock.inner_text("#cz-mt-lang-l")


def test_the_language_caveat_is_the_servers_own_text_short_first_and_in_full_on_demand(dock):
    note = main.constanza_meta_status()["language_note"]
    short = dock.inner_text("[data-mt-langshort]").strip()
    assert short == note.split(". ")[0] + "." and "not a count of who speaks it" in short
    assert dock.is_hidden("[data-mt-langfull]")
    dock.click("#cz-mt .mt-langnote summary")
    assert dock.inner_text("[data-mt-langfull]").strip() == note and "smaller than the Spanish-speaking population" in note


def test_a_line_that_targets_hispanic_audiences_is_offered_spanish_but_never_given_it(open_dock):
    page = open_dock(line=True, demo="Hispanic adults 25-54")
    pill = "#cz-mt-suggest button"
    assert page.inner_text(pill) == "This line targets Hispanic audiences — add Spanish?" and page.is_visible(pill)
    assert chips(page, "lang") == [] and sel(page)["languages"] == []        # offered, never applied silently
    assert page.world.hits.count("languages") == 0                            # type: ignore[attr-defined]
    page.click(pill)
    page.wait_for_selector("#cz-mt-lang-chips .aud-chip")
    assert chips(page, "lang") == ["Spanish"] and page.is_hidden("#cz-mt-suggest")
    page.click("#cz-mt-lang-chips .aud-chip-x")                       # taken out again: the offer is back
    assert page.is_visible(pill)


@pytest.mark.parametrize("setup", [{"demo": "Adults 25-54"}, {"demo": "Adults 18+", "targetOverride": "Women 25-54"},
                                   {"demo": "Hispanic adults", "targetOverride": "Moms 25-44"}, {"demo": "Non-Hispanic adults"}, {"demo": "Spanish-language creative"}])
def test_no_spanish_offer_when_the_line_does_not_target_hispanic_audiences(open_dock, setup):
    assert open_dock(line=True, **setup).is_hidden("#cz-mt-suggest")


def test_a_line_with_its_own_hispanic_target_is_offered_spanish_even_if_the_campaign_is_not_and_the_bubble_never(open_dock):
    assert open_dock(line=True, demo="Adults 25-54", targetOverride="Hispanic moms 25-44").is_visible("#cz-mt-suggest")
    assert open_dock(demo="Hispanic adults 25-54").is_hidden("#cz-mt-suggest")             # no line: nothing to say about


def test_browse_all_languages_opens_the_pop_up_and_applies_what_was_ticked(dock):
    dock.click('[data-mt-act="browse-lang"]')
    dock.wait_for_selector(".segb-row")
    assert dock.inner_text(".segb-dialog .segb-title").strip() == "Browse languages"
    assert dock.locator(".segb-row").count() == len(RichGraph.LOCALES) and dock.locator(".segb-tab").count() == 0
    dock.click('.segb-row[data-id="1007"]')
    dock.click('.segb-row[data-id="1006"]')
    assert "(2)" in dock.inner_text(".segb-apply")
    dock.click(".segb-apply")
    dock.wait_for_selector(".segb-dialog", state="detached")
    assert sorted(chips(dock, "lang")) == ["French (France)", "Tagalog"]
    assert dock.evaluate("document.activeElement && document.activeElement.dataset.mtAct") == "browse-lang"     # the keyboard is back where it was
    dock.click('[data-mt-act="browse-lang"]')
    dock.wait_for_selector(".segb-row")
    assert dock.get_attribute('.segb-row[data-id="1007"]', "aria-selected") == "true"                           # what is chosen opens ticked
    dock.keyboard.press("Escape")


def test_the_language_dialog_has_its_own_search_words_and_counts_languages(dock):
    dock.click('[data-mt-act="browse-lang"]')
    dock.wait_for_selector(".segb-row")
    assert dock.get_attribute(".segb-search-input", "placeholder") == "Search languages"
    assert f"{len(RichGraph.LOCALES)} languages" in dock.inner_text(".segb-count")
    dock.keyboard.press("Escape")
    dock.click("#cz-mt-browse")                                       # the audience window keeps its own, which has categories to search
    dock.wait_for_selector(".segb-row")
    assert "category" in dock.get_attribute(".segb-search-input", "placeholder")


# ----------------------------------------------------------------------------------------------------------------- 5 Audience

def test_the_audience_search_groups_hits_by_kind_with_sizes_and_choosing_adds_a_chip(dock, world):
    dock.fill("#cz-mt-aud-q", "soccer")
    dock.wait_for_selector("#cz-mt-aud-l .aud-ta-opt")
    assert world.of("targeting") == [{"q": "soccer"}]
    assert dock.eval_on_selector_all("#cz-mt-aud-l .aud-ta-group", "els => els.map(e => e.textContent)") == ["Interests", "Behaviors"]
    assert options(dock, "aud") == ["Soccer", "Soccer balls", "Soccer fans (engaged)"]
    first = dock.inner_text("#cz-mt-aud-l .aud-ta-opt >> nth=0")
    assert "Interests › Sports and outdoors › Soccer" in first and "120M – 140M" in first
    assert dock.inner_html("#cz-mt-aud-l .aud-ta-name >> nth=0") == "<mark>Soccer</mark>"
    assert "Browse all interests, behaviors & demographics" in dock.inner_text("#cz-mt-aud-l .aud-ta-foot")
    dock.click("#cz-mt-aud-l .aud-ta-opt >> nth=0")
    dock.fill("#cz-mt-aud-q", "soccer")
    dock.wait_for_selector("#cz-mt-aud-l .aud-ta-opt")
    assert chips(dock, "aud") == ["Soccer"] and dock.inner_text("#cz-mt-aud-chips .aud-chip-meta") == "Interest"
    assert "Added" in dock.inner_text("#cz-mt-aud-l .aud-ta-opt.is-disabled")
    dock.click("#cz-mt-aud-l .aud-ta-opt >> nth=2")                   # a behavior
    assert chips(dock, "aud") == ["Soccer", "Soccer fans (engaged)"] and summary(dock, "aud") == "Soccer, Soccer fans (engaged)"
    assert dock.inner_text("#cz-mt-count-aud") == "2" and dock.is_hidden("[data-mt-none]")


def test_the_note_that_several_picks_widen_the_audience_stays(dock):
    hint = dock.inner_text("[data-mt-audhint]")
    assert "audience gets wider" in hint and "match any of them" in hint and "None chosen yet" in hint


def test_browse_all_opens_the_pop_up_with_three_tabs_sortable_columns_and_applies_the_changes(dock):
    dock.fill("#cz-mt-aud-q", "cooking")
    dock.wait_for_selector("#cz-mt-aud-l .aud-ta-opt")
    dock.click("#cz-mt-aud-l .aud-ta-opt")                            # Cooking is already held when the window opens
    dock.click("#cz-mt-browse")
    dock.wait_for_selector(".segb-row")
    assert dock.eval_on_selector_all(".segb-tab", "els => els.map(e => e.textContent.replace(/[0-9,]+/g, '').trim())") == ["Interests", "Behaviors", "Demographics"]
    assert dock.eval_on_selector_all(".segb-sort", "els => els.map(e => e.textContent.trim())") == ["Name", "Category", "Audience size"]
    assert dock.get_attribute('.segb-row[data-id="interests:6003000000004"]', "aria-selected") == "true"              # held: opens ticked
    cat = dock.inner_text('.segb-row[data-id="interests:6003000000003"]')
    assert "Telenovelas" in cat and "Interests › Entertainment › TV" in cat and "22M – 26M" in cat
    assert "—" in dock.inner_text('.segb-row[data-id="interests:6003000000007"]')                                       # a list item with no size says so
    dock.click(".segb-sort >> text=Audience size")                                                                      # numeric: biggest first
    names = dock.eval_on_selector_all(".segb-row", "els => els.map(e => e.querySelector('.segb-cell').textContent)")
    assert names[:3] == ["Cooking", "Soccer", "Small business"] and names[-1] == "Mystery interest"
    assert dock.get_attribute('th:has(.segb-sort:has-text("Audience size"))', "aria-sort") == "descending"
    dock.click('.segb-row[data-id="interests:6003000000001"]')                                                          # Soccer
    dock.click(".segb-tab >> nth=1")
    dock.wait_for_selector('.segb-row[data-id="behaviors:6002714895372"]')
    dock.click('.segb-row[data-id="behaviors:6002714895372"]')
    dock.click(".segb-tab >> nth=2")
    dock.wait_for_selector('.segb-row[data-id="life_events:6002714398172"]')
    dock.click('.segb-row[data-id="life_events:6002714398172"]')
    assert "Add to audience (3)" in dock.inner_text(".segb-apply")
    dock.click(".segb-apply")
    dock.wait_for_selector(".segb-dialog", state="detached")
    assert chips(dock, "aud") == ["Cooking", "Soccer", "Frequent travelers", "Newlyweds (1 year)"]
    assert dock.eval_on_selector_all("#cz-mt-aud-chips .aud-chip-meta", "els => els.map(e => e.textContent)") == ["Interest", "Interest", "Behavior", "Life event"]
    assert [d["type"] for d in sel(dock)["detailed"]] == ["interests", "interests", "behaviors", "life_events"]
    assert summary(dock, "aud") == "Cooking, Soccer +2 more" and dock.inner_text("[data-mt-browsecount]") == "4 chosen"
    assert dock.evaluate("document.activeElement && document.activeElement.dataset.mtAct") == "browse"
    dock.click("#cz-mt-browse")                                       # untick Cooking: removed
    dock.wait_for_selector(".segb-row")
    dock.click('.segb-row[data-id="interests:6003000000004"]')
    dock.click(".segb-apply")
    dock.wait_for_selector(".segb-dialog", state="detached")
    assert chips(dock, "aud") == ["Soccer", "Frequent travelers", "Newlyweds (1 year)"]


def test_the_browse_window_is_given_the_lists_the_columns_the_limit_and_the_picks_already_held(dock):
    dock.evaluate("""() => {
      window.__sb = null; SegmentBrowser.open = (o) => { window.__sb = o; return { close() {} }; };
      const d = Constanza._plugin('meta')._pure.sel().detailed;
      d.push({ id: '6003000000004', name: 'Cooking', type: 'interests' }, { id: '6002714398172', name: 'Newlyweds (1 year)', type: 'life_events' });
    }""")
    dock.click("#cz-mt-browse")
    got = dock.evaluate("""async () => {
      const o = window.__sb; const items = await o.load('interests'); const demo = await o.load('demographics'); const soccer = items.find(i => i.name === 'Soccer');
      return { tabs: o.tabs.map(t => t.key), columns: o.columns.map(c => c.key), max: o.max, active: o.activeTab,
               counts: { interests: o.chosenCount('interests'), behaviors: o.chosenCount('behaviors'), demographics: o.chosenCount('demographics') },
               names: items.map(i => i.name), soccer, demoFirst: demo[0], heldTicked: items.filter(i => o.isChosen('interests', i)).map(i => i.name), demoTicked: demo.filter(i => o.isChosen('demographics', i)).map(i => i.name),
               categoryOfSoccer: o.columns[1].value(soccer), returnFocus: o.returnFocus && o.returnFocus.dataset.mtAct };
    }""")
    assert got["tabs"] == ["interests", "behaviors", "demographics"] and got["columns"] == ["name", "category", "size"] and got["max"] == 50 and got["active"] == "interests"
    assert got["counts"] == {"interests": 1, "behaviors": 0, "demographics": 1}
    assert got["names"][0] == "Cooking" and len(got["names"]) == len(RichGraph.INTERESTS)                       # the server's own list, name order
    assert got["soccer"]["size"] == 120_000_000 and got["soccer"]["sizeUpper"] == 140_000_000 and got["soccer"]["type"] == "interests"      # audience_lower / audience_upper
    assert got["soccer"]["id"] == "interests:6003000000001" and got["soccer"]["metaId"] == "6003000000001"
    assert got["demoFirst"]["type"] == "life_events" and got["categoryOfSoccer"] == "Interests › Sports and outdoors › Soccer"
    assert got["heldTicked"] == ["Cooking"] and got["demoTicked"] == ["Newlyweds (1 year)"] and got["returnFocus"] == "browse"


def test_a_list_that_cannot_be_loaded_says_so_in_the_window_and_can_be_retried(open_dock, world):
    page = open_dock(before=lambda w: w.fail.update({"targeting": (503, {"detail": "Meta rejected the access token."})}))
    page.click("#cz-mt-browse")
    page.wait_for_selector(".segb-state >> text=Meta rejected the access token.")
    world.fail.clear()
    page.click(".segb-retry")
    page.wait_for_selector(".segb-row")


def test_at_fifty_picks_the_search_and_the_window_stop_adding(dock):
    dock.evaluate("""() => { const P = Constanza._plugin('meta'), d = P._pure.sel().detailed; for (let i = 0; i < 49; i++) d.push({ id: String(1000 + i), name: 'Pick ' + i, type: 'interests' }); P._pure.changed(); }""")
    dock.fill("#cz-mt-aud-q", "cooking")
    dock.wait_for_selector("#cz-mt-aud-l .aud-ta-opt")
    dock.click("#cz-mt-aud-l .aud-ta-opt")
    assert len(sel(dock)["detailed"]) == 50
    dock.fill("#cz-mt-aud-q", "soccer")
    dock.wait_for_selector("#cz-mt-aud-l .aud-ta-opt")
    assert "limit of 50" in dock.inner_text("#cz-mt-aud-l") and dock.locator("#cz-mt-aud-l .aud-ta-opt:not(.is-disabled)").count() == 0


def test_removing_a_chip_puts_the_keyboard_on_the_neighbour_chip(dock):
    dock.evaluate("""() => { const P = Constanza._plugin('meta'); P._pure.sel().detailed.push({ id: '1', name: 'One', type: 'interests' }, { id: '2', name: 'Two', type: 'behaviors' }, { id: '3', name: 'Three', type: 'interests' }); P._pure.changed(); }""")
    dock.focus("#cz-mt-aud-chips .aud-chip >> nth=1 >> .aud-chip-x")
    dock.keyboard.press("Enter")
    assert chips(dock, "aud") == ["One", "Three"] and dock.evaluate("document.activeElement.getAttribute('aria-label')") == "Remove Three"
    dock.click("#cz-mt-aud-chips .aud-chip-x >> nth=1")
    assert dock.evaluate("document.activeElement.getAttribute('aria-label')") == "Remove One"
    dock.keyboard.press("Enter")
    assert chips(dock, "aud") == [] and focused(dock, "#cz-mt-aud-q")


# ----------------------------------------------------------------------------------------------------------------- the request

def test_the_request_is_what_the_cards_say_and_the_result_is_three_scenarios_from_the_lower_end(dock, world):
    pick_place(dock)
    dock.select_option("#cz-mt-age-min", "25")
    dock.select_option("#cz-mt-age-max", "44")
    dock.click('[data-mt-act="gender"][data-gender="female"]')
    dock.click('#cz-mt-lang-quick [data-mt-act="lang-quick"][data-key="spanish"]')
    dock.wait_for_selector("#cz-mt-lang-chips .aud-chip")
    dock.fill("#cz-mt-aud-q", "telenovela")
    dock.wait_for_selector("#cz-mt-aud-l .aud-ta-opt")
    dock.click("#cz-mt-aud-l .aud-ta-opt")
    estimate(dock)
    body = estimate_body(dock)
    assert body["product_name"] == PRODUCT and body["geo_text"] == "" and body["months"] == 1
    assert body["meta"] == {
        "geo": {"type": "geo_market", "key": "803", "label": "Los Angeles · California · US"}, "age_min": 25, "age_max": 44, "gender": "female",
        "languages": [{"id": "1001", "name": "Spanish"}, {"id": "1002", "name": "Spanish (Mexico)"}, {"id": "1003", "name": "Spanish (Spain)"}],
        "targeting": [{"id": "6003000000003", "name": "Telenovelas", "type": "interests"}],
    }                                                                  # no budget field: the dock is used on its own and none was typed
    lower, upper = RichGraph.reach_of({"age_min": 25, "age_max": 44, "genders": [2], "locales": [1, 2, 3], "flexible_spec": [{}]})
    result = dock.inner_text("#cz-result")
    assert f"{lower:,}" in result and f"{upper:,}" in result and "audience size · lower end" in result.lower() and "(from Meta)" in result
    assert "Women" in result and "Ages 25–44" in result and "Language set to Spanish" in result and "Any of: Telenovelas" in result
    assert "Los Angeles · California · US" in result and "Meta API · live" in result
    cards = dock.eval_on_selector_all("#cz-result .mt-sc", "els => els.map(e => ({ name: e.querySelector('.cz-mlabel').textContent, mid: e.classList.contains('mt-mid'), big: e.querySelector('.cz-mvalue').textContent }))")
    assert [c["name"] for c in cards] == ["Conservative · × 4.3", "Mid · default · × 5.5", "Upper · × 8.6"] and [c["mid"] for c in cards] == [False, True, False]
    assert [c["big"] for c in cards] == [f"{round(lower * 4.3):,}", f"{round(lower * 5.5):,}", f"{round(lower * 8.6):,}"]
    assert "set on Facebook or Instagram, not who speaks it" in result and "Budget cross-check" not in result.split("Notes")[0]        # no budget typed: no cross-check
    assert dock.is_enabled("#cz-go") and dock.inner_text("#cz-go").strip() == "Estimate audience →"


def test_a_pull_with_nothing_narrowed_sends_no_age_and_no_languages(dock):
    pick_place(dock)
    estimate(dock)
    meta_block = estimate_body(dock)["meta"]
    assert meta_block["age_min"] is None and meta_block["age_max"] is None and meta_block["gender"] == "all" and meta_block["targeting"] == [] and meta_block["languages"] == []
    assert "retail_budget" not in meta_block and "retail_cpm" not in meta_block
    assert "entered manually" not in dock.inner_text("#cz-result")


def test_request_equals_the_chips_in_any_click_order_and_language_ids_come_only_from_metas_list(dock):
    pick_place(dock)
    dock.click('#cz-mt-lang-quick [data-mt-act="lang-quick"][data-key="both"]')
    dock.wait_for_function("() => Constanza._plugin('meta')._pure.sel().languages.length === 5")
    sent = plug(dock, "P.requestPart().meta.languages.map(l => l.id)")
    assert sent == sorted(sent) and sorted(sent) == sorted(["6", "24", "1001", "1002", "1003"])          # Meta's own list gave these; the page invents none
    assert plug(dock, "P.requestPart().meta.geo.key") == "803"
    dock.click('[data-mt-chips="lang"] >> nth=0')                     # (a chip body is not a button: nothing happens)
    assert plug(dock, "P.requestPart().meta.languages.length") == 5


def test_a_pull_without_a_place_says_what_to_do_and_puts_the_keyboard_in_the_search(dock, world):
    dock.click("#cz-go")
    assert "Choose where first" in dock.inner_text("#cz-result .cz-error") and focused(dock, "#cz-mt-geo-q") and world.hits.count("estimate") == 0


def test_a_pull_needs_no_budget_and_the_cross_check_appears_when_one_is_typed(dock):
    pick_place(dock)
    dock.click("#cz-mt-sec-budget > summary")
    dock.fill("#cz-mt-budget", "2,000")
    estimate(dock)
    assert estimate_body(dock)["meta"]["retail_budget"] == 2000.0 and "retail_cpm" not in estimate_body(dock)["meta"]
    result = dock.inner_text("#cz-result")
    assert "Budget cross-check" in result and "$2,000 retail → $1,000 media at a $12 retail CPM buys about 166,667 impressions over the 1-month flight" in result
    dock.fill("#cz-mt-budget", "")                                    # a changed request: the host says the result is for another one
    assert dock.locator("#cz-result .cz-stale").count() == 1


def test_a_meta_error_is_shown_in_the_result_pane_and_the_form_stays_usable(dock, world):
    pick_place(dock)
    world.graph.reach_error = {"message": "Invalid parameter", "code": 100}
    dock.click("#cz-go")
    dock.wait_for_selector("#cz-result .cz-error")
    assert "Meta refused these settings" in dock.inner_text("#cz-result .cz-error")
    assert dock.get_attribute("#cz-go", "aria-disabled") == "false" and dock.is_enabled("#cz-mt-geo-q")


def test_the_button_is_busy_while_asking_and_a_second_click_cannot_double_send(dock, world):
    pick_place(dock)
    world.hold.add("estimate")
    dock.click("#cz-go")
    assert "Pulling" in dock.inner_text("#cz-go") and dock.get_attribute("#cz-go", "aria-disabled") == "true"
    dock.dispatch_event("#cz-go", "click")                            # (a real click waits for an enabled button: this one is deliberately off)
    world.release("estimate")
    dock.wait_for_selector("#cz-result .mt-scen")
    assert dock.inner_text("#cz-go").strip() == "Estimate audience →" and len(world.of("estimate")) == 1


def test_a_refusal_that_names_a_field_says_which_pick_it_was_about(dock, world):
    pick_place(dock)
    dock.fill("#cz-mt-aud-q", "soccer")
    dock.wait_for_selector("#cz-mt-aud-l .aud-ta-opt")
    dock.click("#cz-mt-aud-l .aud-ta-opt")
    world.fail["estimate"] = (422, {"detail": [{"type": "string_too_long", "loc": ["body", "meta", "targeting", 0, "type"], "msg": "String should have at most 20 characters"}]})
    dock.click("#cz-go")
    dock.wait_for_selector("#cz-result .cz-error")
    text = dock.inner_text("#cz-result .cz-error")
    assert "“Soccer” (Interest): String should have at most 20 characters." in text and "Change that choice and estimate again." in text
    assert dock.get_attribute("#cz-go", "aria-disabled") == "false"


def test_a_household_demographic_ticked_in_the_browse_window_can_be_estimated(open_dock, world):
    page = open_dock(before=lambda w: setattr(w.graph, "DEMOGRAPHICS", RichGraph.DEMOGRAPHICS + [{
        "id": "6023005404983", "name": "Parents (all)", "type": "household_composition", "path": ["Demographics", "Parents", "Parents (all)"],
        "audience_size_lower_bound": 20_000_000, "audience_size_upper_bound": 24_000_000}]))
    pick_place(page)
    page.click("#cz-mt-browse")
    page.wait_for_selector(".segb-row")
    page.click(".segb-tab >> nth=2")
    page.wait_for_selector('.segb-row[data-id="household_composition:6023005404983"]')
    page.click('.segb-row[data-id="household_composition:6023005404983"]')
    page.click(".segb-apply")
    page.wait_for_selector(".segb-dialog", state="detached")
    assert chips(page, "aud") == ["Parents (all)"] and page.inner_text("#cz-mt-aud-chips .aud-chip-meta") == "Household"
    estimate(page)                                                    # was a 422 "String should have at most 20 characters"
    assert estimate_body(page)["meta"]["targeting"] == [{"id": "6023005404983", "name": "Parents (all)", "type": "household_composition"}]
    assert "Any of: Parents (all)" in page.inner_text("#cz-result")


# ----------------------------------------------------------------------------------------------------------------- the result: stale, copy, insert

def test_changing_a_choice_after_the_result_says_the_form_changed_and_putting_it_back_clears_it(dock):
    pick_place(dock)
    estimate(dock)
    assert dock.locator("#cz-result .cz-stale").count() == 0
    for change, back in (('[data-mt-act="gender"][data-gender="female"]', '[data-mt-act="gender"][data-gender="all"]'),
                         ('[data-mt-act="age-preset"] >> text=18–24', '[data-mt-act="age-preset"] >> text=All ages')):
        dock.click(change)
        assert dock.locator("#cz-result .cz-stale").count() == 1
        dock.click(back)
        assert dock.locator("#cz-result .cz-stale").count() == 0
    dock.click('#cz-mt-lang-quick [data-mt-act="lang-quick"][data-key="english"]')
    dock.wait_for_selector("#cz-mt-lang-chips .aud-chip")
    assert dock.locator("#cz-result .cz-stale").count() == 1
    dock.click("#cz-mt-clear-lang")
    assert dock.locator("#cz-result .cz-stale").count() == 0
    dock.click("#cz-mt-clear-where")
    assert dock.locator("#cz-result .cz-stale").count() == 1


def test_the_copied_summary_names_the_place_the_choices_and_the_scenarios_as_plain_text(dock):
    dock.evaluate("""() => { window.__copied = null; Object.defineProperty(navigator, 'clipboard', { value: { writeText: async (t) => { window.__copied = t; } }, configurable: true }); }""")
    pick_place(dock)
    dock.click('[data-mt-act="gender"][data-gender="female"]')
    estimate(dock)
    dock.click("#cz-copy")
    dock.wait_for_function("window.__copied")
    summary_text = dock.evaluate("window.__copied")
    assert "Audience size, lower end (Meta accounts):" in summary_text and "lower-end audience × 5.5 (4.3 – 8.6) per month" in summary_text
    assert "Asked of Meta: Los Angeles (TV market) · Women" in summary_text and "Place sent to Meta: Los Angeles · California · US (TV market)" in summary_text
    assert "Retail spend the flight implies at the planning CPM: Conservative $" in summary_text      # on its own the server uses the product's own planning CPM
    assert "Budget cross-check" not in summary_text                  # ... but no budget was typed: no cross-check


def test_the_result_pane_has_the_about_section_and_the_notes_in_full(dock):
    pick_place(dock)
    estimate(dock)
    dock.click("#cz-result .cz-details summary")
    about = dock.inner_text("#cz-result .cz-details")
    assert "estimated audience size" in about and "default placements (Facebook, Instagram, Messenger, Audience Network)" in about and "Comscore" in about
    assert "so its frequency reads 5.5" in about and "Ads Manager" in about
    notes = dock.inner_text("#cz-result .cz-list >> nth=0")
    assert "not delivered reach" in notes


def test_a_server_multiplier_change_is_what_the_banner_and_the_result_say(open_dock, monkeypatch):
    monkeypatch.setenv("CONSTANZA_AUDIENCE_X_CONSERVATIVE", "3")
    monkeypatch.setenv("CONSTANZA_AUDIENCE_X_MID", "6")
    monkeypatch.setenv("CONSTANZA_AUDIENCE_X_UPPER", "10")
    page = open_dock()
    assert "lower-end audience × 6 (3 – 10) a month" in page.inner_text("#cz-mt-status")
    pick_place(page)
    estimate(page)
    assert "× 3" in page.inner_text("#cz-result .mt-sc >> nth=0") and "× 10" in page.inner_text("#cz-result .mt-sc >> nth=2") and "4.3" not in page.inner_text("#cz-result .mt-scen")


# ----------------------------------------------------------------------------------------------------------------- a Step 05 line: its workspace, its facts, its months

def test_for_a_line_the_budget_and_cpm_come_from_the_line_and_are_sent_with_the_request(line_dock):
    line_dock.click("#cz-mt-sec-budget > summary")                    # the budget card is folded
    assert "From this line (Step 04): $2,000 over 3 months at a $12 retail CPM." in line_dock.inner_text("#cz-mt-budget-fromline") and summary(line_dock, "budget") == "$2,000 over 3 months at a $12 retail CPM"
    assert line_dock.is_hidden("#cz-mt-budget-own") and "Change it there" in line_dock.inner_text("#cz-mt-budget-fromline")
    pick_place(line_dock)
    estimate(line_dock)
    body = estimate_body(line_dock)
    assert body["meta"]["retail_budget"] == 2000.0 and body["meta"]["retail_cpm"] == 12 and body["months"] == 3
    result = line_dock.inner_text("#cz-result")
    assert "Budget cross-check" in result and "$2,000 retail → $1,000 media at a $12 retail CPM" in result and "Retail spend at the planning CPM" in result


def test_a_line_without_a_budget_is_estimated_all_the_same_with_no_budget_field(open_dock):
    page = open_dock(line=True, lines=[{"id": "li_1", "product_name": PRODUCT, "monthly_budget": 0, "months": 1}])
    page.click("#cz-mt-sec-budget > summary")
    assert "no budget yet" in page.inner_text("#cz-mt-budget-fromline") and summary(page, "budget") == "None · optional"
    pick_place(page)
    estimate(page)
    assert "retail_budget" not in estimate_body(page)["meta"] and "Budget cross-check" not in page.inner_text("#cz-result").split("Notes")[0]


def test_a_line_without_flight_dates_says_the_one_month_is_a_stand_in(open_dock):
    assert "No flight dates on this option: the flight length is not known, so 1 month is assumed." in open_dock(line=True, dates=False, months=1).inner_text("#cz-mt-dates")
    assert open_dock(line=True).is_hidden("#cz-mt-dates")


def test_a_line_with_two_calendar_months_over_twenty_two_days_is_estimated_for_two_months(open_dock):
    page = open_dock(select=False, days=22, months=2)
    open_line(page, flightDays=22)
    page.wait_for_selector('#cz-mt-status[data-state="on"]')
    pick_place(page)
    estimate(page)
    assert estimate_body(page)["months"] == 2                          # the line's calendar months, not round(22 / 30.4) = 1
    assert "Flight · 2 mo" in page.inner_text("#cz-result")
    page.click("#cz-flight-inc")                                      # the planner edits the flight box (22 -> 23 days): the box counts from then on
    page.click("#cz-go")
    page.wait_for_function("() => document.getElementById('cz-go').getAttribute('aria-disabled') === 'false'")
    body = estimate_body(page)
    assert len(page.world.of("estimate")) == 2 and body["flight_days"] == 23 and body["months"] == 1                # type: ignore[attr-defined]


def test_two_lines_keep_their_own_choices_and_the_connection_is_asked_once(open_dock, world):
    lines = [{"id": "li_1", "product_name": PRODUCT, "monthly_budget": 2000, "months": 1}, {"id": "li_2", "product_name": PRODUCT, "monthly_budget": 1000, "months": 1}]
    page = open_dock(line=True, lines=lines)
    page.click('[data-mt-act="gender"][data-gender="female"]')
    pick_place(page)
    assert active_key(page) == "li_1" and summary(page, "who") == "Women · all ages"
    open_line(page, "li_2")
    assert active_key(page) == "li_2" and summary(page, "who") == "All genders · all ages" and summary(page, "where") == "Choose a place" and chips(page, "geo") == []
    page.click('[data-mt-act="gender"][data-gender="male"]')
    open_line(page, "li_1")
    assert summary(page, "who") == "Women · all ages" and chips(page, "geo") == ["Los Angeles"]       # the first line's audience is back
    open_line(page, "li_2")
    assert summary(page, "who") == "Men · all ages"
    assert world.hits.count("status") == 1


def test_opening_the_dock_for_the_same_line_again_keeps_what_the_planner_chose(line_dock):
    line_dock.click('[data-mt-act="gender"][data-gender="female"]')
    pick_place(line_dock)
    open_line(line_dock)                                              # "Change in Constanza" pressed again
    assert summary(line_dock, "who") == "Women · all ages" and chips(line_dock, "geo") == ["Los Angeles"]


def test_reset_forgets_the_line_it_was_open_for_and_the_dock_starts_clean(line_dock):
    line_dock.click('[data-mt-act="gender"][data-gender="female"]')
    pick_place(line_dock)
    assert plug(line_dock, "Object.keys(X.T.byKey).filter(k => k !== 'dock')") == ["li_1"]
    line_dock.click("#cz-reset")
    assert plug(line_dock, "Object.keys(X.T.byKey).filter(k => k !== 'dock')") == [] and active_key(line_dock) == "dock"
    assert summary(line_dock, "who") == "All genders · all ages" and chips(line_dock, "geo") == [] and line_dock.is_hidden("#cz-mt-geo-pills")


def test_a_new_proposal_clears_every_workspace(line_dock):
    pick_place(line_dock)
    open_line(line_dock, "li_1")
    line_dock.evaluate("Constanza.onProposalReset()")
    assert plug(line_dock, "Object.keys(X.T.byKey).filter(k => k !== 'dock')") == [] and chips(line_dock, "geo") == []


def test_a_line_that_was_opened_before_starts_from_its_saved_estimates_choices(open_dock):
    saved = snap_for()
    page = open_dock(line=True, availsData={"li_1": {"forecast_snapshot": saved}})
    assert chips(page, "geo") == ["Los Angeles"] and summary(page, "where") == "Los Angeles · TV market" and summary(page, "who") == "Women · 25–44"
    assert chips(page, "lang") == ["Spanish"] and chips(page, "aud") == ["Soccer"]
    assert page.input_value("#cz-mt-age-min") == "25" and page.input_value("#cz-mt-age-max") == "44"
    assert page.is_visible("#cz-result .mt-scen") and page.locator("#cz-result .cz-stale").count() == 0          # the saved result, current: no "form changed"
    assert page.is_hidden("#cz-mt-suggest")


def test_a_saved_estimate_whose_keys_were_reordered_by_the_database_shows_no_changed_banner_but_a_changed_choice_does(open_dock):
    saved = snap_for()
    shuffled = json.loads(json.dumps(saved, sort_keys=True))          # JSONB hands objects back with their keys sorted
    page = open_dock(line=True, availsData={"li_1": {"forecast_snapshot": shuffled}})
    assert page.is_visible("#cz-result .mt-scen") and page.locator("#cz-result .cz-stale").count() == 0
    page.click('[data-mt-act="gender"][data-gender="male"]')
    assert page.locator("#cz-result .cz-stale").count() == 1
    page.click('[data-mt-act="gender"][data-gender="female"]')
    assert page.locator("#cz-result .cz-stale").count() == 0


def test_restoring_does_not_overwrite_what_the_planner_changed_and_says_the_estimate_no_longer_matches(open_dock):
    saved = snap_for()
    page = open_dock(line=True, availsData={"li_1": {"forecast_snapshot": saved}})
    page.click('[data-mt-act="gender"][data-gender="male"]')
    open_line(page)                                                   # the dock opened for the same line again: the planner's edit is still there
    assert summary(page, "who") == "Men · 25–44"
    assert page.is_visible("#cz-result .mt-scen") and page.locator("#cz-result .cz-stale").count() == 1


def test_a_pull_for_a_line_saves_the_estimate_with_the_line_it_was_pulled_for(line_dock):
    pick_place(line_dock)
    line_dock.click('#cz-mt-lang-quick [data-mt-act="lang-quick"][data-key="spanish"]')
    line_dock.wait_for_selector("#cz-mt-lang-chips .aud-chip")
    estimate(line_dock)
    saved = line_dock.evaluate("state.availsData.li_1.forecast_snapshot")
    assert saved["v"] == 1 and saved["method"] == "meta" and saved["request"]["meta"]["geo"]["key"] == "803" and saved["request"]["months"] == 3
    assert saved["result"]["est_uniques"] == saved["result"]["meta"]["audience"]["low"]
    assert saved["line"]["months"] == 3 and saved["line"]["budget"] == 2000 and saved["line"]["cpm"] == 12 and saved["line"]["product_name"] == PRODUCT
    assert [lang["id"] for lang in saved["request"]["meta"]["languages"]] == ["1001", "1002", "1003"]


def test_a_pull_for_another_product_than_the_lines_is_not_filed_under_the_line(line_dock):
    choose_product(line_dock, "traffic")                              # the dock was switched to another SKU while it is still open for the Awareness line
    pick_place(line_dock)
    estimate(line_dock)
    assert line_dock.evaluate("state.availsData.li_1 === undefined || !state.availsData.li_1.forecast_snapshot")


def test_inserting_into_the_line_writes_the_mid_case_the_lower_end_and_the_snapshot(line_dock):
    pick_place(line_dock)
    estimate(line_dock)
    lower = line_dock.evaluate("state.availsData.li_1.forecast_snapshot.result.meta.audience.low")
    line_dock.click("#cz-result .cz-insert >> nth=0")
    inserted = line_dock.evaluate("window.__inserted.at(-1)")
    assert inserted["lid"] == "li_1" and inserted["imps"] == round(lower * 5.5) and inserted["uniques"] == lower
    assert inserted["info"] == {"method": "meta_audience", "dmas": ["Los Angeles · California · US"], "unmatched_tokens": [], "win_rate": None, "hispanic_share": None}
    assert inserted["snapshot"]["method"] == "meta" and inserted["snapshot"]["request"]["meta"]["geo"]["key"] == "803"


def test_inserting_the_result_into_another_line_writes_only_the_numbers(open_dock):
    lines = [{"id": "li_1", "product_name": PRODUCT, "monthly_budget": 2000, "months": 1}, {"id": "li_2", "product_name": PRODUCT, "monthly_budget": 1000, "months": 1}]
    page = open_dock(line=True, lines=lines)
    pick_place(page)
    estimate(page)
    page.click('#cz-result .cz-insert[data-lid="li_2"]')
    inserted = page.evaluate("window.__inserted.at(-1)")
    assert inserted["lid"] == "li_2" and inserted["snapshot"] is None          # the pull was for li_1: li_2 gets the numbers and no snapshot (it would not describe them)


def test_the_dock_is_dirty_for_a_line_only_while_its_choices_differ_from_the_saved_estimate(open_dock):
    saved = snap_for()
    page = open_dock(line=True, availsData={"li_1": {"forecast_snapshot": saved}})
    assert plug(page, "P.choicesChanged('li_1', JSON.parse(%s).meta)" % json.dumps(json.dumps(saved["request"]))) is False
    page.click('[data-mt-act="gender"][data-gender="male"]')
    assert plug(page, "P.choicesChanged('li_1', JSON.parse(%s).meta)" % json.dumps(json.dumps(saved["request"]))) is True
    assert plug(page, "P.choicesChanged('li_9', JSON.parse(%s).meta)" % json.dumps(json.dumps(saved["request"]))) is False       # no workspace: never created, never "changed"
    assert plug(page, "Object.keys(X.T.byKey).includes('li_9')") is False
    assert [d for d in page.evaluate("window.__dirty") if d[0] == "li_1"] != []                # the host was told to redraw the card behind the dock


def test_a_saved_estimate_read_from_the_old_shape_restores_the_choices_and_shows_the_result(open_dock):
    page = open_dock(line=True, availsData={"li_1": {"meta_forecast": legacy_for()}})
    assert chips(page, "geo") == ["Los Angeles"] and summary(page, "who") == "Women · 25–44" and chips(page, "lang") == ["Spanish"] and chips(page, "aud") == ["Soccer"]
    assert page.is_visible("#cz-result .mt-scen") and "Los Angeles · California · US" in page.inner_text("#cz-result")


def test_a_manual_saved_estimate_has_no_choices_to_restore_and_the_dock_starts_empty(open_dock):
    page = open_dock(line=True, availsData={"li_1": {"meta_forecast": legacy_for(fx.MANUAL)}})
    assert summary(page, "where") == "Choose a place" and chips(page, "geo") == []
    assert "Entered manually" in page.inner_text("#cz-result .cz-badges") and "(entered manually)" in page.inner_text("#cz-result")


def test_a_corrupt_saved_estimate_does_not_break_the_dock_and_runs_nothing(open_dock):
    for name, snapshot in fx.corrupt_variants(snap_for()).items():
        page = open_dock(line=True, availsData={"li_1": {"forecast_snapshot": snapshot}})
        assert page.evaluate("window.__pwned") is None, name
        assert page.is_visible("#cz-mt") and page.locator("#cz-result img").count() == 0, name


def test_names_in_a_saved_estimate_are_shown_as_text(open_dock):
    page = open_dock(line=True, availsData={"li_1": {"forecast_snapshot": fx.hostile(snap_for())}})
    assert page.evaluate("window.__pwned") is None and page.locator("#cz-mt img, #cz-result img, #cz-mt-sec-where img").count() == 0


# ----------------------------------------------------------------------------------------------------------------- look and feel

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
    pick_place(page)
    page.select_option("#cz-mt-age-min", "25")
    page.select_option("#cz-mt-age-max", "44")
    page.click('[data-mt-act="gender"][data-gender="female"]')
    page.click('#cz-mt-lang-quick [data-mt-act="lang-quick"][data-key="both"]')
    page.wait_for_function("() => Constanza._plugin('meta')._pure.sel().languages.length === 5")
    for query in ("soccer", "telenovela", "cooking"):
        page.fill("#cz-mt-aud-q", query)
        page.wait_for_selector("#cz-mt-aud-l .aud-ta-opt")
        page.keyboard.press("Enter")
    page.click("#cz-mt-sec-budget > summary")
    page.fill("#cz-mt-budget", "33,862.50")


@pytest.mark.parametrize("name,viewport,wide", [("narrow", {"width": 1280, "height": 900}, False), ("wide", {"width": 1440, "height": 900}, True),
                                                ("phone", {"width": 375, "height": 800}, False), ("small_laptop", {"width": 1000, "height": 700}, True)])
def test_the_dock_fits_its_layout_with_every_meta_card_full_and_a_result(open_dock, name, viewport, wide):
    page = open_dock(width=viewport["width"], height=viewport["height"])
    if wide and viewport["width"] > 720:
        page.click("#cz-expand")
    fill_everything(page)
    estimate(page)
    shot(page, f"meta_dock_{name}")
    assert overflow_report(page) == {"out": [], "page": 0, "body": 0, "form": 0}, name
    tops = page.eval_on_selector_all("#cz-builder .aud-sec", "els => els.filter(e => e.getClientRects().length).map(e => Math.round(e.getBoundingClientRect().height))")
    assert all(h >= 40 for h in tops)


CARD_STAGE = """(width) => { const stage = document.createElement('div'); stage.id = 'stage'; stage.style.cssText = `max-width:${width}px;margin:0 auto;padding:16px`;
  stage.innerHTML = '<div class="constanza-block" data-lid="li_1" data-method="meta"><div class="constanza-result" id="constanza-result-li_1" style="display:none"></div></div>';
  document.body.appendChild(stage); }"""
DRAW_CARD = """([snapshot, line, stale]) => { const el = document.getElementById('constanza-result-li_1');
  Constanza.renderCard(el, snapshot.result, { line, fromSnapshot: true, snapshot, stale, onApply() {}, onRecalc() {}, onRefine() {}, getText: () => "", onGeoText() {} }); }"""


def card_contrast(page):
    """The contrast of the pieces this plugin draws on a card (the range table's header and the buttons are the dock's own look)."""
    out = []
    for cls in ("mt-headline", "mt-range", "mt-pulled", "mt-note", "mt-spend", "mt-check", "mt-foot", "mt-empty"):
        out += page.evaluate(f"contrastReport('#stage .{cls}')")
    return out


@pytest.mark.parametrize("name,width", [("card_wide", 1000), ("card_phone", 390)])
def test_the_step_05_card_pieces_fit_the_card_and_read_clearly_fresh_stale_and_not_connected(open_dock, name, width):
    page = open_dock(width=width, height=900, select=False)
    page.evaluate("Constanza.close()")                                # the card is on the Step 05 page, not under the dock
    page.evaluate(CARD_STAGE, min(width, 1000) - 32)
    snapshot = snap_for()
    line = {**fx.LINE_FACTS, "product_name": PRODUCT}
    page.evaluate(DRAW_CARD, [snapshot, line, []])
    card = page.inner_text("#constanza-result-li_1")
    assert "1,000,000" in card and "lower end of Meta’s audience" in card and "Conservative" in card and "Mid · default" in card and "Upper" in card
    assert "Pulled for Los Angeles · California · US (TV market), women ages 25–44, language set to Spanish, any of: Soccer." in card
    assert "$2,000 retail → $1,000 media" in card and "Meta API · live" in card
    assert page.locator("#constanza-result-li_1 .cz-apply").inner_text().strip() == "Insert the mid-case (5,500,000 / month) into Avails →"
    assert page.locator("#constanza-result-li_1 .cz-recalc").count() == 1 and page.locator("#constanza-result-li_1 .cz-refine").inner_text().strip() == "Change in Constanza"
    shot(page, f"meta_{name}_fresh")
    assert page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth") == 0
    assert card_contrast(page) == []
    page.evaluate(DRAW_CARD, [snapshot, line, ["the budget changed", "the audience in Constanza changed"]])
    stale = page.inner_text("#constanza-result-li_1")
    assert "Inputs changed — recalculate:" in stale and "the budget changed, the audience in Constanza changed" in stale and "Insert the mid-case" not in stale
    shot(page, f"meta_{name}_stale")
    assert page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth") == 0 and card_contrast(page) == []
    page.evaluate("Constanza._plugin('meta')._pure.T.status = { at: Date.now(), data: { configured: false, missing: ['META_ACCESS_TOKEN'] }, error: null }")
    page.evaluate(DRAW_CARD, [snapshot, line, []])
    assert page.locator("#constanza-result-li_1 .cz-recalc").get_attribute("aria-disabled") == "true"
    assert "Meta isn't connected yet: an admin needs to add META_ACCESS_TOKEN" in page.inner_text("#constanza-result-li_1")
    shot(page, f"meta_{name}_offline")
    assert page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth") == 0 and card_contrast(page) == []


def test_the_step_05_card_for_a_saved_estimate_that_is_broken_says_so_and_offers_the_way_forward(open_dock):
    page = open_dock(select=False)
    page.evaluate(CARD_STAGE, 900)
    line = {**fx.LINE_FACTS, "product_name": PRODUCT}
    for name, broken in fx.corrupt_variants(snap_for()).items():
        page.evaluate("document.getElementById('constanza-result-li_1').innerHTML = ''")
        page.evaluate("""([snapshot, line]) => Constanza.renderCard(document.getElementById('constanza-result-li_1'), snapshot.result || { method: 'meta' },
            { line, fromSnapshot: true, snapshot, stale: [], onApply() {}, onRecalc() {}, onRefine() {}, getText: () => "", onGeoText() {} })""", [broken, line])
        assert page.locator("#constanza-result-li_1 .cz-refine").count() == 1, name                 # drawn or "couldn't be read": the way to Constanza is always there
        assert page.evaluate("window.__pwned") is None and page.locator("#stage img").count() == 0, name


def test_every_control_is_at_least_36px_tall_and_shows_a_focus_ring(dock):
    pick_place(dock)
    dock.click('#cz-mt-lang-quick [data-mt-act="lang-quick"][data-key="spanish"]')
    dock.wait_for_selector("#cz-mt-lang-chips .aud-chip")
    dock.evaluate("() => { const P = Constanza._plugin('meta'); P._pure.sel().detailed.push({ id: '9', name: 'One pick', type: 'interests' }); P._pure.changed(); }")
    small = dock.evaluate("""() => [...document.querySelectorAll('#cz-mt button, #cz-mt select, #cz-mt input, #cz-mt summary, #cz-mt-sec-where button, #cz-mt-sec-where input')]
      .filter((e) => e.getClientRects().length).map((e) => ({ t: (e.textContent || e.getAttribute('aria-label') || e.id).trim().slice(0, 30), h: Math.round(e.getBoundingClientRect().height) })).filter((x) => x.h < 36)""")
    assert small == []
    for selector in ('[data-mt-act="gender"][data-gender="male"]', "#cz-mt-geo-q", "#cz-mt-aud-chips .aud-chip-x", "#cz-mt-sec-budget > summary"):
        dock.focus(selector)
        dock.keyboard.press("Shift+Tab")
        dock.keyboard.press("Tab")
        assert dock.evaluate(f"getComputedStyle(document.querySelector({json.dumps(selector)})).outlineStyle") != "none", selector


def test_text_in_the_panel_and_in_a_result_has_enough_contrast(dock):
    pick_place(dock)
    dock.click('#cz-mt-lang-quick [data-mt-act="lang-quick"][data-key="spanish"]')
    dock.wait_for_selector("#cz-mt-lang-chips .aud-chip")
    dock.click("#cz-mt-sec-budget > summary")
    estimate(dock)
    assert dock.evaluate("contrastReport('#cz-mt')") == [] and dock.evaluate("contrastReport('#cz-mt-sec-where')") == [] and dock.evaluate("contrastReport('#cz-result')") == []
    dock.click('[data-mt-act="gender"][data-gender="male"]')          # the "form changed" look is legible too
    assert dock.evaluate("contrastReport('#cz-result')") == []


def test_the_not_connected_look_is_legible_too(open_dock, monkeypatch):
    monkeypatch.delenv("META_ACCESS_TOKEN")
    page = open_dock()
    page.wait_for_selector('#cz-mt-status[data-state="off"]')
    assert page.evaluate("contrastReport('#cz-mt')") == [] and page.evaluate("contrastReport('#cz-mt-sec-where')") == []


def test_a_list_opened_in_a_typeahead_closes_when_the_keyboard_leaves_it(dock):
    dock.fill("#cz-mt-geo-q", "los")
    dock.wait_for_selector("#cz-mt-geo-l .aud-ta-opt")
    dock.focus('[data-mt-act="gender"][data-gender="male"]')
    assert dock.is_hidden("#cz-mt-geo-l")
    dock.fill("#cz-mt-aud-q", "soccer")
    dock.wait_for_selector("#cz-mt-aud-l .aud-ta-opt")
    dock.keyboard.press("Escape")
    assert dock.is_hidden("#cz-mt-aud-l") and dock.input_value("#cz-mt-aud-q") == "soccer" and dock.is_visible("#constanza-dock")      # the first Escape only closes the list
    dock.keyboard.press("Escape")
    assert dock.input_value("#cz-mt-aud-q") == "" and dock.is_visible("#constanza-dock")                                                # the second empties the box


# ----------------------------------------------------------------------------------------------------------------- an empty answer is not remembered, and the empty state has a Retry

def test_an_empty_language_list_in_the_browse_window_says_so_and_retry_asks_meta_again(open_dock, world):
    page = open_dock(before=lambda w: setattr(w.graph, "locales", []))
    page.click('[data-mt-act="browse-lang"]')
    page.wait_for_selector(".segb-state >> text=empty list of languages")
    assert page.locator(".segb-retry").count() == 1 and page.locator(".segb-row").count() == 0
    world.graph.locales = [dict(r) for r in RichGraph.LOCALES]                                      # Meta recovers
    page.click(".segb-retry")
    page.wait_for_selector(".segb-row")
    assert page.locator(".segb-row").count() == len(RichGraph.LOCALES) and len(world.of("languages")) == 2


def test_an_empty_language_list_is_asked_again_by_the_next_search_and_by_try_again_without_a_reload(open_dock, world):
    page = open_dock(before=lambda w: setattr(w.graph, "locales", []))
    page.fill("#cz-mt-lang-q", "port")
    page.wait_for_selector("#cz-mt-lang-l >> text=came back empty just now")
    assert "Meta lists no language matching" not in page.inner_text("#cz-mt-lang-l")      # an empty list is not "no match"
    world.graph.locales = [dict(r) for r in RichGraph.LOCALES]
    page.click('[data-mt-act="lang-retry"]')
    page.wait_for_selector("#cz-mt-lang-l .aud-ta-opt")
    assert options(page, "lang") == ["Portuguese (Brazil)", "Portuguese (Portugal)"] and len(world.of("languages")) == 2
    page.fill("#cz-mt-lang-q", "tag")                                                         # and now it is remembered: no third question
    page.wait_for_selector("#cz-mt-lang-l .aud-ta-opt")
    assert len(world.of("languages")) == 2


def test_an_empty_interest_list_is_not_kept_and_its_tab_can_be_retried(open_dock, world):
    page = open_dock(before=lambda w: setattr(w.graph, "INTERESTS", []))
    page.click("#cz-mt-browse")
    page.wait_for_selector(".segb-state >> text=empty list of interests")
    assert page.locator(".segb-retry").count() == 1
    world.graph.INTERESTS = list(RichGraph.INTERESTS)
    page.click(".segb-retry")
    page.wait_for_selector('.segb-row[data-id="interests:6003000000001"]')
    assert [q for q in world.of("targeting") if q.get("kind") == "interests"] == [{"kind": "interests"}, {"kind": "interests"}]
    page.keyboard.press("Escape")
    page.click("#cz-mt-browse")                                                                # a list that did load is kept for the session
    page.wait_for_selector(".segb-row")
    assert len([q for q in world.of("targeting") if q.get("kind") == "interests"]) == 2


# ----------------------------------------------------------------------------------------------------------------- the search is cancelled by Escape and by leaving the box

def test_escape_within_the_debounce_cancels_the_search_and_the_list_stays_closed(dock, world):
    dock.focus("#cz-mt-geo-q")
    dock.keyboard.type("Phoenix")
    dock.keyboard.press("Escape")                                                              # the 300 ms wait has not run out
    dock.wait_for_timeout(650)
    assert dock.is_hidden("#cz-mt-geo-l") and dock.get_attribute("#cz-mt-geo-q", "aria-expanded") == "false"
    assert world.hits.count("geos") == 0 and dock.input_value("#cz-mt-geo-q") == "Phoenix"
    dock.focus("#cz-mt-aud-q")
    dock.keyboard.type("soccer")
    dock.keyboard.press("Escape")
    dock.wait_for_timeout(650)
    assert dock.is_hidden("#cz-mt-aud-l") and world.hits.count("targeting") == 0


def test_leaving_the_box_within_the_debounce_cancels_the_search(dock, world):
    dock.focus("#cz-mt-geo-q")
    dock.keyboard.type("los")
    dock.keyboard.press("Tab")
    dock.wait_for_timeout(650)
    assert dock.is_hidden("#cz-mt-geo-l") and dock.get_attribute("#cz-mt-geo-q", "aria-expanded") == "false" and world.hits.count("geos") == 0


def test_an_answer_that_arrives_after_escape_does_not_reopen_the_list(dock, world):
    world.hold.add("geos")
    dock.focus("#cz-mt-geo-q")
    dock.keyboard.type("Phoenix")
    dock.wait_for_timeout(450)                                                                 # the request is on its way
    assert len(world.pending) == 1
    dock.keyboard.press("Escape")
    world.release("geos")
    dock.wait_for_timeout(300)
    assert dock.is_hidden("#cz-mt-geo-l") and dock.get_attribute("#cz-mt-geo-q", "aria-expanded") == "false"


def test_the_search_boxes_hold_no_more_than_the_server_searches_for(dock, world):
    assert [dock.get_attribute(f"#cz-mt-{w}-q", "maxlength") for w in ("geo", "aud", "lang")] == ["100", "80", "60"]
    dock.focus("#cz-mt-aud-q")
    dock.keyboard.type("s" * 88)
    assert len(dock.input_value("#cz-mt-aud-q")) == 80
    dock.wait_for_selector("#cz-mt-aud-l >> text=Nothing found")
    assert "must be 2–80" not in dock.inner_text("#cz-mt-aud-l") and len(world.of("targeting")[-1]["q"]) == 80


# ----------------------------------------------------------------------------------------------------------------- the browse controls do not pretend when the window script is missing

def test_without_the_browse_window_script_the_browse_controls_are_off_with_a_reason(open_dock):
    page = open_dock(block_browse=True)
    assert page.is_disabled('[data-mt-act="browse"]') and page.is_disabled('[data-mt-act="browse-lang"]')
    reasons = page.eval_on_selector_all("[data-mt-browse-reason]", "els => els.filter(e => !e.hidden).map(e => e.textContent)")
    assert reasons == ["The browse window didn’t load, so “Browse all” is off. Reload the page to get it back."] * 2
    assert page.is_enabled('#cz-mt-lang-quick [data-mt-act="lang-quick"][data-key="spanish"]') and page.is_enabled("#cz-mt-aud-q")       # the rest of the panel works
    page.fill("#cz-mt-aud-q", "soccer")                                                       # the "Browse all" row of the search list is off too
    page.wait_for_selector("#cz-mt-aud-l .aud-ta-opt")
    assert page.is_disabled('#cz-mt-aud-l [data-mt-act="browse"]')
    assert "didn’t load" in page.get_attribute('#cz-mt-aud-l [data-mt-act="browse"]', "title")
    page.click('#cz-mt-browse', force=True)                                                   # a click on an off control does nothing at all
    assert page.locator(".segb-dialog").count() == 0
    assert page.evaluate("contrastReport('#cz-mt')") == []


def test_with_the_browse_window_script_present_the_reasons_stay_hidden(dock):
    assert dock.is_enabled('[data-mt-act="browse"]') and dock.is_enabled('[data-mt-act="browse-lang"]')
    assert dock.eval_on_selector_all("[data-mt-browse-reason]", "els => els.every(e => e.hidden)")


# ----------------------------------------------------------------------------------------------------------------- money

def test_amounts_with_cents_show_two_decimals_and_whole_amounts_none_everywhere_in_the_dock(open_dock):
    page = open_dock(line=True, lines=[{"id": "li_1", "product_name": PRODUCT, "monthly_budget": 11287.5, "months": 3, "cpm": 12.5}])
    pick_place(page)
    estimate(page)
    text = page.inner_text("#cz-result")
    assert "$33,862.50 retail → $16,931.25 media at a $12.50 retail CPM" in text
    spends = page.eval_on_selector_all("#cz-result .mt-sc-rows dd:last-child", "els => els.map(e => e.textContent)")
    assert len(spends) == 3 and all(re.fullmatch(r"\$[\d,]+\.\d{2}|\$[\d,]+", s) for s in spends), spends
    assert not re.search(r"\$\d[\d,]*\.\d(?!\d)", page.inner_text("#cz-result")) and not re.search(r"\$\d[\d,]*\.\d(?!\d)", page.inner_text("#cz-mt"))      # never "$33,862.5"


# ----------------------------------------------------------------------------------------------------------------- what is typed before Meta's connection answer, and a search that was cancelled

# box, what to type, the route it asks, the hits the fake Graph answers with
BOXES = {
    "geo": ("#cz-mt-geo-q", "Los Angeles", "geos", ["Los Angeles", "Los Angeles-Long Beach"]),
    "aud": ("#cz-mt-aud-q", "soccer", "targeting", ["Soccer", "Soccer balls", "Soccer fans (engaged)"]),
    "lang": ("#cz-mt-lang-q", "port", "languages", ["Portuguese (Brazil)", "Portuguese (Portugal)"]),
}
FIRST_CHIP = {"geo": "Los Angeles", "aud": "Soccer", "lang": "Portuguese (Brazil)"}


def status_parked(open_dock, *more):
    """The dock is open and Meta's connection answer is parked (and, with `more`, those routes too): the planner is typing before it comes."""
    return open_dock(before=lambda w: w.hold.update({"status", *more}), select=False)


def type_as_the_dock_opens(page, box, query):
    """Opens the dock on the Meta product and puts the keyboard in a box with the first letters in the SAME task: the dock's own start-up (a timer that puts
    the keyboard on its first control 60 ms after it opens) has not run yet. That is a planner who is quick, or a machine that is slow."""
    page.evaluate("""([box, query]) => { Constanza.open({ product: PRODUCT }); const el = document.querySelector(box); el.focus(); el.value = query;
        el.dispatchEvent(new Event('input', { bubbles: true })); }""", [box, query])


def wait_parked(page, world, name, count=1):
    for _ in range(100):
        if len([p for p in world.pending if p[0] == name]) >= count:
            return
        page.wait_for_timeout(30)
    raise AssertionError(f"no {name} request was parked: {world.hits}")


@pytest.mark.parametrize("which", ["geo", "aud", "lang"])
def test_a_search_typed_in_the_moment_the_dock_opens_is_still_answered_while_the_connection_check_is_on_its_way(open_dock, world, which):
    """The reported 'Searching Meta…' for good. The connection answer was never what cancelled it: the dock moved the keyboard to its first control 60 ms
    after it opened, out of the box the planner was already typing in, and a typeahead closes - and drops its search - when the keyboard leaves its box."""
    box, query, route, hits = BOXES[which]
    page = status_parked(open_dock)
    type_as_the_dock_opens(page, box, query)
    page.wait_for_timeout(150)                                         # past the dock's 60 ms start-up, inside the 300 ms the place and interest searches wait
    assert page.get_attribute("#cz-mt-status", "data-state") == "checking" and focused(page, box)
    world.release("status")
    page.wait_for_selector(f"#cz-mt-{which}-l .aud-ta-opt")
    assert options(page, which) == hits and page.is_visible(f"#cz-mt-{which}-l") and page.get_attribute(box, "aria-expanded") == "true"
    assert len(world.of(route)) == 1
    page.click(f"#cz-mt-{which}-l .aud-ta-opt")
    assert chips(page, which) == [FIRST_CHIP[which]]


@pytest.mark.parametrize("which", ["geo", "aud"])
def test_a_connection_answer_that_comes_inside_the_wait_before_the_search_is_sent_does_not_cancel_it(open_dock, world, which):
    box, query, route, hits = BOXES[which]
    page = status_parked(open_dock)
    choose_product(page, "awareness")
    page.fill(box, query)                                              # the 300 ms wait begins ...
    world.release("status")                                            # ... and the connection answer comes inside it
    page.wait_for_selector('#cz-mt-status[data-state="on"]')
    page.wait_for_selector(f"#cz-mt-{which}-l .aud-ta-opt")
    assert options(page, which) == hits and len(world.of(route)) == 1 and focused(page, box)


@pytest.mark.parametrize("which", ["geo", "aud", "lang"])
def test_a_connection_answer_that_comes_while_the_search_is_on_its_way_does_not_lose_the_search_answer(open_dock, world, which):
    box, query, route, hits = BOXES[which]
    page = status_parked(open_dock, route)
    choose_product(page, "awareness")
    page.fill(box, query)
    wait_parked(page, world, route)                                    # asked of Meta, not answered yet
    world.release("status")
    page.wait_for_selector('#cz-mt-status[data-state="on"]')
    assert page.is_visible(f"#cz-mt-{which}-l") and "Meta" in page.inner_text(f"#cz-mt-{which}-l")      # still waiting, list still there
    world.release(route)
    page.wait_for_selector(f"#cz-mt-{which}-l .aud-ta-opt")
    assert options(page, which) == hits and len(world.of(route)) == 1


@pytest.mark.parametrize("which", ["geo", "aud", "lang"])
def test_a_connection_answer_that_comes_after_the_hits_are_shown_leaves_them_there_to_pick_from(open_dock, world, which):
    box, query, route, hits = BOXES[which]
    page = status_parked(open_dock)
    choose_product(page, "awareness")
    page.fill(box, query)
    page.wait_for_selector(f"#cz-mt-{which}-l .aud-ta-opt")           # the hits came first (only the connection answer is parked)
    world.release("status")
    page.wait_for_selector('#cz-mt-status[data-state="on"]')
    assert options(page, which) == hits and page.is_visible(f"#cz-mt-{which}-l")
    page.click(f"#cz-mt-{which}-l .aud-ta-opt")
    assert chips(page, which) == [FIRST_CHIP[which]]


@pytest.mark.parametrize("which", ["aud", "lang"])
def test_browse_all_opened_before_the_connection_answer_still_shows_its_rows_and_applies_what_was_ticked(open_dock, world, which):
    page = status_parked(open_dock)
    choose_product(page, "awareness")
    opener, row, label = ("#cz-mt-browse", 'interests:6003000000001', "Soccer") if which == "aud" else ('[data-mt-act="browse-lang"]', "1007", "Tagalog")
    page.click(opener)
    page.wait_for_selector(".segb-row")
    world.release("status")
    page.wait_for_selector('#cz-mt-status[data-state="on"]')
    assert page.locator(".segb-dialog").count() == 1 and page.locator(".segb-row").count() > 3        # the window and its list are untouched by the answer
    page.click(f'.segb-row[data-id="{row}"]')
    page.click(".segb-apply")
    page.wait_for_selector(".segb-dialog", state="detached")
    assert chips(page, which) == [label]
    assert page.evaluate("document.activeElement && document.activeElement.dataset.mtAct") == ("browse" if which == "aud" else "browse-lang")


@pytest.mark.parametrize("which", ["geo", "aud", "lang"])
def test_a_connection_answer_that_says_not_connected_while_a_search_is_typed_leaves_the_reason_and_no_endless_wait(open_dock, world, monkeypatch, which):
    """Not connected switches the boxes that ask Meta off (a disabled box that held the keyboard loses it), which cancels what was typed: the banner says why
    and nothing says 'Searching Meta…' for good."""
    monkeypatch.delenv("META_ACCESS_TOKEN")
    monkeypatch.delenv("META_AD_ACCOUNT_ID")
    box, query, route, hits = BOXES[which]
    page = status_parked(open_dock)
    choose_product(page, "awareness")
    page.fill(box, query)
    world.release("status")
    page.wait_for_selector('#cz-mt-status[data-state="off"]')
    page.wait_for_timeout(500)                                         # past the wait the search would have had
    assert "META_ACCESS_TOKEN" in page.inner_text("#cz-mt-status") and page.is_disabled(box)
    shown = page.is_visible(f"#cz-mt-{which}-l")
    assert not shown or "Searching Meta" not in page.inner_text(f"#cz-mt-{which}-l")


CANCELLED = [("geo", "Escape while it waits"), ("aud", "Escape while it waits"), ("geo", "leaving the box while it waits"), ("aud", "leaving the box while it waits"),
             ("geo", "Escape while it is asked"), ("aud", "Escape while it is asked"), ("lang", "Escape while it is asked"),
             ("geo", "leaving the box while it is asked"), ("aud", "leaving the box while it is asked"), ("lang", "leaving the box while it is asked")]


@pytest.mark.parametrize("which, how", CANCELLED)
def test_a_search_that_was_cancelled_is_asked_again_by_the_arrow_key_and_never_shown_as_an_endless_wait(dock, world, which, how):
    box, query, route, hits = BOXES[which]
    asked = how.endswith("it is asked")
    if asked:
        world.hold.add(route)
    dock.focus(box)
    dock.keyboard.type(query)
    if asked:
        wait_parked(dock, world, route)                                # on its way to Meta
    if how.startswith("Escape"):
        dock.keyboard.press("Escape")
    else:
        dock.focus('[data-mt-act="gender"][data-gender="male"]')
    assert dock.is_hidden(f"#cz-mt-{which}-l")
    if asked:
        world.hold.discard(route)
        world.release(route)                                           # the old answer comes back: nobody wants it any more
    dock.wait_for_timeout(450)                                         # (past the wait a typed search had: it must not wake up by itself)
    assert dock.is_hidden(f"#cz-mt-{which}-l")
    before = len(world.of(route))
    dock.focus(box)
    dock.keyboard.press("ArrowDown")                                   # the planner asks for the list again
    dock.wait_for_selector(f"#cz-mt-{which}-l .aud-ta-opt")
    assert options(dock, which) == hits and dock.get_attribute(box, "aria-expanded") == "true"
    assert len(world.of(route)) == before + (0 if which == "lang" and asked else 1)      # (the language list that did arrive is kept, so it is searched here)
    dock.keyboard.press("ArrowDown")
    dock.keyboard.press("Enter")                                       # and the keyboard picks from it as always
    assert chips(dock, which)
