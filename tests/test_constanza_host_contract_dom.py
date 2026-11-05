"""The Constanza dock as the HOST of any avails method (app/static/constanza.js), in a REAL browser (headless Chromium through Playwright), with probe
plugins that set every flag of the contract - not TikTok, not Meta: whatever those two do, the dock must do for the next source too.

What is pinned here (docs/constanza_methods.md, design sections 2.6, 2.6.1, 2.6.2):
  * `Constanza.CONTRACT` and the validation in `registerMethod`: a plugin with a typo, a missing member or caps that need a member that is not there is
    refused (false, a console error that says why) and the dock stays usable; so does a factory that throws; a product whose method never registered is
    left out of the picker (it never falls through to the platform path);
  * the flags: `ownsGeography` (the shared Where card is swapped for the method's own, no geography text is sent, nothing is resolved, the numbering
    stays 1-5), `goState` (the pull button off with its reason), `persist` (the estimate of a Step 05 line is saved under that line - only for the line and
    the product it was pulled for - and shown again when the dock is opened for it), `lineMonths` (the line's calendar months travel until the planner edits
    the flight box), `autoPull` (what the Step 05 block offers);
  * the lifecycle: which line the dock is open for is set BEFORE any method hears of it or of the product; the same line again keeps a method's
    workspace; a method without onLine is reset exactly as before; the bubble drops the line and its result; Reset and a new proposal;
  * the staleness rules (canonical JSON, the one banner, the card behind the dock told through the host) and the guards (a plugin that throws, a saved
    estimate of any shape, a page without a host object).
The page is a small harness: the real constanza.js, the audience kit, SegmentBrowser and the real CSS, a stub `state.catalog` and a stub
`window.constanzaHost` (what app.js gives the dock). tests/test_constanza_step05_block_dom.py is the Step 05 block, tests/test_constanza_meta_dom.py Meta's
own panel. Skipped when Playwright or its Chromium is not installed."""
from __future__ import annotations

import http.server
import json
import re
import threading
from pathlib import Path

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")

from tests.fixtures import meta_snapshots as fx  # noqa: E402  (saved Meta estimates, as data)
from tests.test_constanza_dock_dom import resolution  # noqa: E402  (the shape /api/constanza/resolve answers)

STATIC = Path(__file__).resolve().parents[1] / "app" / "static"
CZ_JS = (STATIC / "constanza.js").read_text(encoding="utf-8")
OWNER, OWNER2, PLAIN, GHOST, CTOR = "Owner SKU", "Owner SKU 2", "Plain SKU", "Ghost SKU", "Constructor SKU"
META = "Facebook & Instagram Ads | Awareness"                  # the real Meta plugin is loaded into the page by `?meta=1` (the one test that needs a real method)

HARNESS = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>host contract harness</title>
<link rel="stylesheet" href="/static/styles.css"><link rel="stylesheet" href="/static/constanza.css">
<link rel="stylesheet" href="/static/audience-kit.css"><link rel="stylesheet" href="/static/segment-browser.css"></head><body>
<main style="padding:24px"><h1>The proposal page behind the dock</h1><button id="opener" type="button">Other control</button></main>
<script>
  function escapeHtml(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[c])); }
  function escapeAttr(s) { return String(s || "").replace(/[^a-zA-Z0-9._-]/g, "_"); }
  function _replaceGeoItem(t, i, r) { return r; }
  function _constanzaErrorText(d, f) { return (d && typeof d.detail === "string" && d.detail) || f || "Request failed."; }
  const item = (name, method, extra) => ({ name, family: "Probes", is_addon: false, forecast: Object.assign({ enabled: true, method }, extra) });
  var state = { lineItems: [], availsData: {}, catalog: { families: ["Entravision Plus", "Probes"], products_by_family: {
    "Entravision Plus": [{ name: "Roku", family: "Entravision Plus", is_addon: false, forecast: { enabled: true, method: "platform",
        series: { key: "roku", channel: "platform_model", data_basis: "modeled", unit: "monthly", targeting: "general", relation: "primary", title: "roku" } } }],
    "Probes": [item("Owner SKU", "probe_owner"), item("Owner SKU 2", "probe_owner"), item("Plain SKU", "probe_plain"), item("Ghost SKU", "ghost_method"),
               item("Constructor SKU", "constructor")] } } };

  // ---- what app.js gives the dock for a Step 05 line (window.constanzaHost), reduced to a stub that records what it is asked and told
  window.HOST = { lines: {}, snaps: {}, saved: [], dirty: [], inserted: [], saveResult: null };
  window.defineLine = function (id, over) {
    const line = Object.assign({ id, product_name: "Owner SKU", method: "probe_owner", label: "Owner line " + id, months: 2, flight_days: 38, has_dates: true, geo_text: "Los Angeles",
      target_text: "Adults 25-54", budget: 2000, cpm: 12, hispanic: null }, over || {});
    HOST.lines[id] = line;
    state.lineItems = state.lineItems.filter((x) => x.id !== id).concat([{ id, product_name: line.product_name }]);
    return line;
  };
  window.dropLine = function (id) { delete HOST.lines[id]; state.lineItems = state.lineItems.filter((x) => x.id !== id); };
  window.installHost = function () {
    window.constanzaHost = {
      lineFacts(lid) { return HOST.lines[lid] ? Object.assign({}, HOST.lines[lid]) : null; },
      snapshot(lid) { return HOST.snaps[lid] || null; },
      saveSnapshot(lid, snap) { HOST.saved.push([lid, snap]); if (!HOST.lines[lid]) return false; HOST.snaps[lid] = snap; return true; },
      onDirty(lid, dirty) { HOST.dirty.push([lid, dirty]); },
    };
  };
  installHost();
  function applyConstanzaEstimate(lid, imps, uniques, info, snapshot) { HOST.inserted.push({ lid, imps, uniques, info, snapshot, undef: snapshot === undefined, argc: arguments.length }); return true; }

  // ---- the probe plugins. LOG records every call the dock makes into them, in order.
  window.LOG = [];
  window.PCFG = { connected: true, reason: null, adoptThrows: false };
  const canon = (v) => JSON.stringify(v, (k, x) => (x && typeof x === "object" && !Array.isArray(x) ? Object.keys(x).sort().reduce((o, key) => { o[key] = x[key]; return o; }, {}) : x));
  const same = (a, b) => canon({ p: a.place, k: (a.picks || []).slice().sort() }) === canon({ p: b.place, k: (b.picks || []).slice().sort() });

  // a method with EVERY flag: its own Where card, one workspace per line, saves its estimates, repeats them from a Step 05 card, asks for the line's months
  window.ownerFactory = function (ctx) {
    const W = { byKey: {}, active: "dock" };
    const seed = (key) => {
      const snap = key === "dock" ? null : ctx.snapshot(key), b = snap && snap.request && snap.request.probe_owner;
      return { place: b ? b.place : "", picks: b ? (b.picks || []).slice() : [], touched: false };
    };
    const ws = () => W.byKey[W.active] || (W.byKey[W.active] = seed(W.active));
    window.OWNER = { W, ctx, ws,
      setPlace(p) { const w = ws(); w.place = p; w.touched = true; ctx.markStale(); },
      toggle(x) { const w = ws(), at = w.picks.indexOf(x); if (at >= 0) w.picks.splice(at, 1); else w.picks.push(x); w.touched = true; ctx.markStale(); } };
    return {
      caps: { ownsGeography: true, persist: true, autoPull: "saved", lineMonths: true },
      channel: "probe_owner", channelLabel: "Owner probes", sourceLabel: "Live from the owner probe", rangeLabels: ["Conservative", "Mid - default", "Upper"], goLabel: "Estimate probe →",
      formHtml: () => '<section class="aud-sec" id="po-sec"><div class="aud-sec-head"><span class="aud-step" aria-hidden="true"></span><div class="aud-sec-text"><h3 class="aud-sec-title">Who (probe)</h3></div></div>'
        + '<div class="aud-sec-body"><button type="button" id="po-add">Add a pick</button></div></section>',
      whereHtml: () => '<section class="aud-sec" id="po-where"><div class="aud-sec-head"><span class="aud-step" aria-hidden="true"></span><div class="aud-sec-text"><h3 class="aud-sec-title">Where (probe)</h3></div></div>'
        + '<div class="aud-sec-body"><input id="po-place" class="aud-input" aria-label="Probe place"></div></section>',
      bind: () => { LOG.push(["bind"]); document.getElementById("po-place").addEventListener("input", (e) => OWNER.setPlace(e.target.value)); },
      requestPart: () => { const w = ws(), line = ctx.line(); return { probe_owner: Object.assign({ place: w.place, picks: w.picks.slice().sort() }, line ? { budget: line.budget } : {}) }; },
      context: () => ({ summary: ["place " + ws().place], geo: ws().place }),
      reset: (prevLid) => { LOG.push(["reset", prevLid, ctx.S.lineId]); delete W.byKey[prevLid == null ? "dock" : prevLid]; W.active = "dock"; },
      onLine: (line) => {
        LOG.push(["onLine", line && line.id, ctx.S.lineId]);
        W.active = line ? line.id : "dock";
        if (!W.byKey[W.active]) W.byKey[W.active] = seed(W.active);
      },
      onSelect: (p) => { const l = ctx.line(); LOG.push(["onSelect", p.name, ctx.S.lineId, l && l.id]); },
      onProposalReset: () => { LOG.push(["onProposalReset"]); W.byKey = {}; W.active = "dock"; },
      onResult: () => LOG.push(["onResult"]),
      onError: (status) => LOG.push(["onError", status]),
      restore: (block) => { LOG.push(["restore", block]); const w = ws(); if (w.touched) return; w.place = block.place; w.picks = (block.picks || []).slice(); },
      choicesChanged: (lid, block) => { const w = W.byKey[lid]; return !!w && !same(w, block); },
      canPull: () => (ws().place ? { ok: true } : { ok: false, reason: "Choose where first." }),
      goState: () => (PCFG.connected === false ? { enabled: false, reason: "The probe isn't connected yet." } : { enabled: true }),
      connection: () => PCFG.connected,
      cardRequest: (lid, line, snapshot) => { const w = W.byKey[lid], b = snapshot && snapshot.request && snapshot.request.probe_owner || {}; return { place: w ? w.place : b.place, picks: w ? w.picks.slice().sort() : (b.picks || []), budget: line.budget }; },
      canAutoPull: (line, snapshot) => (snapshot && snapshot.request && snapshot.request.probe_owner ? { ok: true } : { ok: false, reason: "nothing to repeat" }),
      cardEmptyHtml: (line, snap) => '<p class="po-empty">Probe needs a place' + (line ? " for " + ctx.esc(line.label) : "") + (snap ? " (a budget-only estimate)" : "") + '.</p>',
      applyLabel: (m) => "Insert " + ctx.num(m.month.mid) + " / month",
      staleReasons: (snap, line) => (PCFG.reason ? [PCFG.reason] : []),
      snapshotLine: (data) => ({ budget: data.probe_owner.budget_used, cpm: data.probe_owner.cpm_used }),
      badgeHtml: (data) => '<span class="cz-badge po-badge">Probe live</span>',
      geoInfo: (data) => ({ method: "probe_geo", dmas: [data.probe_owner.place], unmatched_tokens: [], win_rate: null, hispanic_share: null }),
      errorText: (data, fallback, body) => "PROBE: " + ((data && typeof data.detail === "string" && data.detail) || fallback),
      headlineHtml: (data) => '<div class="cz-headline"><div class="cz-metric main po-headline"><span class="cz-mlabel">Probe</span><span class="cz-mvalue">' + ctx.esc(data.probe_owner.name) + "</span></div></div>",
      bodyHtml: (data, m, asked) => '<p class="po-body">asked: ' + ctx.esc(JSON.stringify(asked && asked.method && asked.method.summary)) + "</p>",
      aboutHtml: () => '<details class="cz-details"><summary>About the probe</summary></details>',
      cardHtml: (data, m, cardOpts) => '<p class="po-card">' + ctx.esc(data.probe_owner.name) + " stale=" + cardOpts.stale.length + " saved=" + cardOpts.fromSnapshot + " line=" + (cardOpts.line ? cardOpts.line.id : "") + "</p>",
      summaryLines: (data, asked) => ["Probe line: " + data.probe_owner.name],
      legacy: { key: "old_probe_forecast", adopt: (saved, line) => {
        if (PCFG.adoptThrows) throw new Error("boom");
        if (!saved || !saved.value) return null;
        return { v: 1, method: "probe_owner", saved_at: saved.at, request: null, result: null, line: { product_name: "Owner SKU", method: "probe_owner", months: 2, geo_text: "Los Angeles", target_text: "Adults 25-54", budget: saved.budget || 0, cpm: null } };
      } },
    };
  };

  // a method with no flag set and no per-line hooks: the dock treats it as it always treated TikTok
  window.plainFactory = function (ctx) {
    return {
      channelLabel: "Plain probes", sourceLabel: "Live from the plain probe",
      formHtml: () => '<div id="pp-panel">plain</div>', bind: () => LOG.push(["plain.bind"]),
      requestPart: () => ({ probe_plain: { x: 1 } }), context: () => ({ summary: ["plain"] }),
      reset: (prevLid) => LOG.push(["plain.reset", prevLid == null ? null : prevLid, ctx.S.lineId]),
      onSelect: (p) => LOG.push(["plain.onSelect", p.name, ctx.S.lineId]),
      headlineHtml: (data) => '<div class="cz-headline pp-headline">PLAIN ' + ctx.esc(data.product_name) + "</div>", bodyHtml: () => "", aboutHtml: () => "", cardHtml: () => '<p class="pp-card">plain card</p>', summaryLines: () => ["plain line"],
    };
  };
  window.registerProbes = function () {
    return { owner: Constanza.registerMethod("probe_owner", ownerFactory), plain: Constanza.registerMethod("probe_plain", plainFactory) };
  };
</script>
<script src="/static/segment-browser.js"></script>
<script src="/static/constanza.js"></script>
<script>%(register)s</script>
</body></html>"""


def estimate_answer(sent: dict) -> dict:
    """What /api/constanza/estimate answers for a probe product (the standard estimate, with `method` and the method's own block)."""
    name = sent["product_name"]
    method = {OWNER: "probe_owner", OWNER2: "probe_owner", PLAIN: "probe_plain", "Nowhere SKU": "probe_nowhere"}.get(name)
    months = sent.get("months") or 1
    out = {"method": method or "platform", "product_name": name, "platform": method, "confidence": "live_api", "geo_method": "probe_geo", "geo_confidence": "selected", "months": months,
           "imps_low": 4_300_000 * months, "imps_mid": 5_500_000 * months, "imps_high": 8_600_000 * months, "est_uniques": 1_000_000, "reach_universe": None, "series": None, "weeks": None,
           "win_rate": None, "notes": ["Probe note."], "unmatched_geo_tokens": [], "unmatched_geo_details": [], "ambiguous": [], "geo_interpretations": [], "geo_suggestions": [], "by_area": [],
           "hispanic_targeting": {"default": False, "on": False, "applies": False}}
    if method in ("probe_owner", "probe_nowhere"):
        block = sent.get("probe_owner") or {}
        out["probe_owner"] = {"place": block.get("place", ""), "name": "Probe <i>name</i>", "budget_used": block.get("budget") or 0, "cpm_used": 12}
    elif method == "probe_plain":
        out["probe_plain"] = {"x": 1}
    return out


class _Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=str(STATIC.parent), **k)

    def do_GET(self):
        if self.path.startswith("/harness.html"):
            register = "" if "noreg=1" in self.path else "registerProbes();"
            page = HARNESS.replace("%(register)s", register)
            if "meta=1" in self.path:                      # the real Meta plugin and one of its products, beside the probes
                page = page.replace("<script>" + register + "</script>", "<script>" + register + "</script>"
                                    + '<link rel="stylesheet" href="/static/constanza-meta.css"><script src="/static/constanza-meta.js"></script>'
                                    + "<script>state.catalog.products_by_family.Probes.push(item(" + json.dumps(META) + ", 'meta', { info: { retail_cpm: 12 } }));</script>")
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
    request_queue_size = 128


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
    """What the harness's /api answers: the estimate (parkable with `hold`, refusable with `fail`) and the shared geography matcher."""

    def __init__(self):
        self.hits: list[str] = []
        self.requests: list[dict] = []
        self.resolves: list[dict] = []
        self.hold: set[str] = set()
        self.pending: list = []
        self.fail: dict = {}
        self.reply: dict = {}                      # route name -> the answer to give instead of the harness's own (the request is still recorded)

    def release(self, name="estimate") -> None:
        for item in [p for p in self.pending if p[0] == name]:
            self.pending.remove(item)
            item[1].fulfill(status=item[2], content_type="application/json", body=json.dumps(item[3]))

    def answer(self, route) -> None:
        req = route.request
        path = req.url.split("//", 1)[1].split("/", 1)[1].split("?")[0]
        name = {"api/constanza/resolve": "resolve", "api/constanza/estimate": "estimate"}.get(path, "other")
        self.hits.append(name)
        status, body = 200, {"detail": "not found"}
        if name in self.fail:
            status, body = self.fail[name]
        elif name == "resolve":
            sent = json.loads(req.post_data or "{}")
            self.resolves.append(sent)
            body = resolution(sent.get("geo_text", ""), sent.get("geo_type_hint"))
        elif name == "estimate":
            sent = json.loads(req.post_data or "{}")
            self.requests.append(sent)
            body = self.reply["estimate"] if "estimate" in self.reply else estimate_answer(sent)
        elif name == "other":
            status = 404
        if name in self.hold:
            self.pending.append((name, route, status, body))
            return
        route.fulfill(status=status, content_type="application/json", body=json.dumps(body))


def new_page(browser, server, world, viewport=None, query="", open_dock=True):
    context = browser.new_context(viewport=viewport or {"width": 1280, "height": 900})
    page = context.new_page()
    page.route("**/api/**", world.answer)
    page.route("**/fonts.googleapis.com/**", lambda r: r.abort())
    page.route("**/fonts.gstatic.com/**", lambda r: r.abort())
    errors: list[str] = []
    console: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: console.append(m.text) if m.type == "error" else None)
    for _ in range(3):
        page.goto(f"{server}/harness.html{query}")
        if page.evaluate("typeof Constanza") == "object":
            break
    if open_dock:
        page.evaluate("Constanza.open()")
        page.wait_for_selector("#cz-pname:not(:has-text('Loading'))")
    page.world, page.errors, page.console_errors, page.context_ = world, errors, console, context     # type: ignore[attr-defined]
    return page


@pytest.fixture
def dock(browser, server):
    world = World()
    page = new_page(browser, server, world)
    yield page
    page.context_.close()                                               # type: ignore[attr-defined]
    assert page.errors == [], page.errors                               # type: ignore[attr-defined]


# ------------------------------------------------------------------------------------------------------------------ helpers

def text(page, selector):
    return " ".join(page.inner_text(selector).split())


def log(page):
    return page.evaluate("LOG")


def clear_log(page):
    page.evaluate("LOG.length = 0")


def choose_product(page, query):
    page.click("#cz-product-btn")
    page.fill("#cz-picker-search", query)
    page.click("#cz-picker-list .cz-option")


def pull(page):
    before = len(page.world.requests)                                    # type: ignore[attr-defined]
    page.click("#cz-go")
    page.wait_for_function("document.getElementById('cz-go').getAttribute('aria-disabled') === 'false'")
    page.wait_for_selector("#cz-result .cz-headline, #cz-result .cz-error")
    assert len(page.world.requests) == before + 1                        # type: ignore[attr-defined]
    return page.world.requests[-1]                                       # type: ignore[attr-defined]


def open_for(page, lid, product=OWNER, **over):
    """What openConstanzaForLine does: the dock for one Step 05 line, with its months and days."""
    opts = {"product": product, "geo": "Los Angeles", "months": 2, "flightDays": 38, "lineId": lid, "lineLabel": "Owner line", **over}
    page.evaluate("(o) => Constanza.open(o)", opts)


def picker_names(page):
    page.click("#cz-product-btn")
    names = page.eval_on_selector_all("#cz-picker-list .cz-option-name", "els => els.map(e => e.textContent)")
    groups = page.eval_on_selector_all("#cz-picker-list .cz-group-label", "els => els.map(e => e.textContent)")
    page.keyboard.press("Escape")
    return names, groups


# ================================================================================================================== the contract

def test_the_contract_is_the_frozen_list_of_what_a_method_is(dock):
    c = dock.evaluate("Constanza.CONTRACT")
    assert c["version"] == 1
    assert c["required"] == ["formHtml", "bind", "requestPart", "context", "reset", "headlineHtml", "bodyHtml", "aboutHtml", "cardHtml", "summaryLines"]
    assert set(c["optional"]) == {"goLabel", "onSelect", "onLine", "canPull", "goState", "onError", "onResult", "onProposalReset", "errorText", "badgeHtml", "geoInfo", "whereHtml", "channel",
                                  "channelLabel", "sourceLabel", "rangeLabels", "caps", "cardRequest", "canAutoPull", "cardEmptyHtml", "applyLabel", "staleReasons", "choicesChanged",
                                  "restore", "connection", "legacy", "snapshotLine", "_pure"}
    assert c["caps"] == {"ownsGeography": False, "persist": False, "autoPull": "geo", "lineMonths": False}
    assert c["ctx"] == ["S", "$", "esc", "num", "compact", "api", "markStale", "errorText", "setMood", "announce", "summarize", "productByName", "requestBody", "targetsHispanic",
                        "line", "proposalGeo", "snapshot", "refreshGo"]
    assert dock.evaluate("Object.isFrozen(Constanza.CONTRACT) && Object.isFrozen(Constanza.CONTRACT.required) && Object.isFrozen(Constanza.CONTRACT.caps)")


def test_a_probe_that_sets_every_flag_registers_and_gets_its_own_group_in_the_picker(dock):
    assert dock.evaluate("Object.keys(Constanza.CONTRACT.caps).every((k) => k in Constanza._plugin('probe_owner').caps)")
    names, groups = picker_names(dock)
    assert OWNER in names and OWNER2 in names and PLAIN in names
    # the groups come from the plugins' own labels, in registration order, between the data groups and the population-share one; the keys cannot meet a channel's
    assert groups == ["Streaming platforms", "Owner probes", "Plain probes"]
    assert dock.evaluate("Constanza._state.products.filter((p) => p.name === 'Owner SKU')[0].channel") == "m:probe_owner"
    assert dock.evaluate("Constanza.methodState('probe_owner')") == {"kind": "plugin", "ok": True, "key": "probe_owner", "label": "Live from the owner probe",
                                                                       "caps": {"ownsGeography": True, "persist": True, "autoPull": "saved", "lineMonths": True}, "note": "",
                                                                       "legacyKey": "old_probe_forecast"}


# a plugin that must NOT be accepted: (what is wrong with it, the plugin as JS, a fragment of the console error that says so)
GOOD = "({ formHtml: () => '', bind: () => {}, requestPart: () => ({}), context: () => ({ summary: [] }), reset: () => {}, headlineHtml: () => '', bodyHtml: () => '', aboutHtml: () => '', cardHtml: () => '', summaryLines: () => [] })"
REFUSED = [
    ("a typo in a member name", "Object.assign(%s, { formHTML: () => '' })" % GOOD, 'unknown member "formHTML"'),
    ("a missing required member", "(() => { const p = %s; delete p.cardHtml; return p; })()" % GOOD, "missing cardHtml"),
    ("a member that is not a function", "Object.assign(%s, { bind: 'nope' })" % GOOD, "bind must be a function"),
    ("not an object", "'a string'", "did not return an object"),
    ("an unknown cap", "Object.assign(%s, { caps: { ownsGeometry: true } })" % GOOD, 'unknown cap "ownsGeometry"'),
    ("a cap that is not a boolean", "Object.assign(%s, { caps: { persist: 'yes', ownsGeography: false } })" % GOOD, "caps.persist must be true or false"),
    ("an autoPull nobody knows", "Object.assign(%s, { caps: { autoPull: 'sometimes' } })" % GOOD, 'caps.autoPull must be "geo", "saved" or "none"'),
    ("ownsGeography without a Where card", "Object.assign(%s, { caps: { ownsGeography: true } })" % GOOD, "caps.ownsGeography needs whereHtml"),
    ("persist without restore and choicesChanged", "Object.assign(%s, { caps: { persist: true } })" % GOOD, "caps.persist needs restore; caps.persist needs choicesChanged"),
    ("autoPull saved without cardRequest and canAutoPull", "Object.assign(%s, { caps: { autoPull: 'saved' } })" % GOOD, 'caps.autoPull "saved" needs cardRequest'),
    ("range labels that are not three words", "Object.assign(%s, { rangeLabels: ['Low', 'High'] })" % GOOD, "rangeLabels must be three words"),
    ("a legacy reader with no adopt", "Object.assign(%s, { legacy: { key: 'old' } })" % GOOD, "legacy must be { key, adopt() }"),
    ("a channel that is not a key", "Object.assign(%s, { channel: 'Not A Key' })" % GOOD, "channel must be lower-case letters, digits and _"),
    # (the page deletes legacy.key from an entry whenever it files a new estimate: it has to be a field of its own, never one of the line's figures)
    ("a legacy key that is one of the line's figures", "Object.assign(%s, { legacy: { key: 'max_imps', adopt: () => null } })" % GOOD, "legacy.key must be a field of its own"),
    ("a legacy key that is the snapshot itself", "Object.assign(%s, { legacy: { key: 'forecast_snapshot', adopt: () => null } })" % GOOD, "legacy.key must be a field of its own"),
    ("a legacy key that is not written like the older shapes", "Object.assign(%s, { legacy: { key: 'Old-Forecast', adopt: () => null } })" % GOOD, "legacy.key must be a field of its own"),
    ("a cap that only Object has", "Object.assign(%s, { caps: { toString: 1 } })" % GOOD, 'unknown cap "toString"'),
    ("a cap that only Object has, with a value", "Object.assign(%s, { caps: { constructor: 2, hasOwnProperty: 3 } })" % GOOD, 'unknown cap "constructor"'),
    ("its own places with the proposal's geography as the pull",
     "Object.assign(%s, { caps: { ownsGeography: true }, whereHtml: () => '' })" % GOOD, 'caps.ownsGeography needs caps.autoPull "saved" or "none"'),
]


@pytest.mark.parametrize("why,plugin,said", REFUSED, ids=[r[0] for r in REFUSED])
def test_a_plugin_that_does_not_match_the_contract_is_refused_and_the_dock_carries_on(dock, why, plugin, said):
    ok = dock.evaluate("(src) => Constanza.registerMethod('probe_bad', () => eval(src))", plugin)
    assert ok is False
    assert any(said in line and "probe_bad" in line for line in dock.console_errors), (said, dock.console_errors)       # type: ignore[attr-defined]
    assert dock.evaluate("Constanza._plugin('probe_bad')") is None and dock.query_selector('[data-method="probe_bad"]') is None
    # ... nothing else moved: the plugins that were there still are, and the dock still pulls
    assert dock.evaluate("[Constanza._plugin('probe_owner') !== null, Constanza._plugin('probe_plain') !== null]") == [True, True]
    dock.fill("#cz-geo", "Los Angeles")
    choose_product(dock, "roku")
    sent = pull(dock)
    assert sent["product_name"] == "Roku" and sent["geo_text"] == "Los Angeles"


def test_a_factory_that_throws_or_a_key_that_is_not_a_key_is_refused_and_a_panel_that_will_not_draw_is_taken_out_again(dock):
    assert dock.evaluate("Constanza.registerMethod('probe_throws', () => { throw new Error('no ctx today'); })") is False
    assert dock.evaluate("Constanza.registerMethod('Not A Key', () => ({}))") is False
    assert dock.evaluate("Constanza.registerMethod('platform', () => ({}))") is False                    # the two data methods' names belong to the data methods
    assert dock.evaluate("Constanza.registerMethod('generic', () => ({}))") is False
    assert dock.evaluate("Constanza.registerMethod('probe_nofn', 'not a function')") is False
    # a plugin that is well-formed but whose panel throws while it is drawn or bound: out again, never half-mounted, and the page keeps working
    bad_form = "Object.assign(%s, { formHtml: () => { throw new Error('no markup'); } })" % GOOD
    bad_bind = "Object.assign(%s, { bind: () => { throw new Error('no wiring'); } })" % GOOD
    assert dock.evaluate("(src) => Constanza.registerMethod('probe_form', () => eval(src))", bad_form) is False
    assert dock.evaluate("(src) => Constanza.registerMethod('probe_bindbad', () => eval(src))", bad_bind) is False
    assert dock.evaluate("[Constanza._plugin('probe_form'), Constanza._plugin('probe_bindbad')]") == [None, None]
    assert dock.query_selector('[data-method="probe_bindbad"]') is None
    said = " ".join(dock.console_errors)                                                      # type: ignore[attr-defined]
    for fragment in ("no ctx today", "not a usable method key", "the factory is not a function", "no markup", "no wiring"):
        assert fragment in said, fragment
    assert dock.is_visible("#constanza-dock") and dock.is_visible("#cz-sec-where")


def test_a_plugin_whose_names_are_in_order_registers_whatever_else_it_does(dock):
    """The refusals above are about names the page relies on; the same plugin with a name the page can rely on is let in."""
    ok = dock.evaluate("""() => [
        Constanza.registerMethod('probe_names', () => Object.assign(%s, { legacy: { key: 'older_probe_forecast', adopt: () => null } })),
        Constanza.registerMethod('probe_names2', () => Object.assign(%s, { caps: { ownsGeography: true, autoPull: 'none' }, whereHtml: () => '<section class="aud-sec"></section>', context: () => ({ summary: [], geo: '' }) })),
        Constanza.registerMethod('probe_names3', () => Object.assign(%s, { caps: { ownsGeography: true, autoPull: 'saved' }, whereHtml: () => '<section class="aud-sec"></section>', context: () => ({ summary: [], geo: '' }),
          cardRequest: () => ({}), canAutoPull: () => ({ ok: true }) }))]""" % (GOOD, GOOD, GOOD))
    assert ok == [True, True, True]
    assert dock.evaluate("Constanza.methodState('probe_names').legacyKey") == "older_probe_forecast"


def test_a_plugins_request_part_adds_its_own_block_and_nothing_else_to_the_request(dock):
    """requestPart() is asked for on every change of the form: whatever it returns besides `{ [its key]: ... }` (a product, months or Hispanic choice of its own) never reaches the request."""
    dock.evaluate("""() => {
        state.catalog.products_by_family.Probes.push(item('Hijack SKU', 'probe_hijack'));
        Constanza.registerMethod('probe_hijack', (c) => Object.assign(plainFactory(c), { channel: 'probe_hijack',
          requestPart: () => ({ probe_hijack: { a: 1 }, product_name: 'HIJACK', months: 99, hispanic: true, audience: { age_min: 1 }, probe_plain: { x: 1 } }) })); }""")
    choose_product(dock, "hijack sku")
    dock.fill("#cz-geo", "Los Angeles")
    sent = pull(dock)
    assert sent["probe_hijack"] == {"a": 1}
    assert sent["product_name"] == "Hijack SKU" and sent["months"] == 1 and sent["audience"] is None and "hispanic" not in sent and "probe_plain" not in sent
    # a plugin that answers with no block of its own, or with something that is not an object, adds nothing
    dock.evaluate("""() => { Constanza.registerMethod('probe_hijack', (c) => Object.assign(plainFactory(c), { channel: 'probe_hijack', requestPart: () => ({ product_name: 'HIJACK', months: 99 }) })); }""")
    assert dock.evaluate("(() => { const b = Constanza._pure.requestBody(); return [b.product_name, b.months, Object.keys(b).filter((k) => k.startsWith('probe'))]; })()") == ["Hijack SKU", 1, []]


def test_registering_a_method_again_replaces_its_panel_and_a_refused_replacement_leaves_the_first_in_place(dock):
    dock.evaluate("Constanza.registerMethod('probe_plain', plainFactory)")
    assert dock.evaluate("document.querySelectorAll('[data-method=\"probe_plain\"]').length") == 1       # not two panels of one method
    first = dock.evaluate("Constanza._plugin('probe_plain') !== null")
    assert dock.evaluate("Constanza.registerMethod('probe_plain', () => ({ formHtml: () => '' }))") is False
    assert first and dock.evaluate("Constanza._plugin('probe_plain') !== null") and dock.evaluate("document.querySelectorAll('[data-method=\"probe_plain\"]').length") == 1


def test_a_product_whose_method_never_loaded_is_left_out_and_never_falls_through_to_the_platform_path(dock):
    names, _ = picker_names(dock)
    assert GHOST not in names and CTOR not in names and "Roku" in names
    state = dock.evaluate("[Constanza.methodState('ghost_method'), Constanza.methodState('constructor'), Constanza.methodState('platform').kind, Constanza.methodState('generic').kind, Constanza.methodState(null).kind]")
    assert state[0]["kind"] == "missing" and state[0]["ok"] is False and "didn’t load" in state[0]["note"]
    assert state[1]["kind"] == "missing"                                  # "constructor" is a key like any other: it must not find Object's own
    assert state[2:] == ["data", "data", "data"]
    assert dock.evaluate("Constanza._plugin('constructor')") is None and dock.evaluate("Constanza._plugin('toString')") is None
    # an answer that names such a method draws as a data answer would, never through a plugin that is not there
    assert dock.evaluate("Constanza.badgesHtml({ method: 'ghost_method', product_name: 'x', geo_confidence: 'high' })").count("Population-share") == 1


def test_the_dock_still_opens_when_no_method_registered_at_all(browser, server):
    world = World()
    page = new_page(browser, server, world, query="?noreg=1")
    try:
        names, groups = picker_names(page)
        assert names == ["Roku"] and "Owner probes" not in groups
        assert page.is_visible("#cz-sec-where") and page.is_hidden("#cz-method-forms")
    finally:
        page.context_.close()                                           # type: ignore[attr-defined]
    assert page.errors == []                                            # type: ignore[attr-defined]


# ================================================================================================================== ownsGeography

def step_numbers(page, ids):
    """The number each card shows in its head. The kit counts (CSS counter `aud-step`, reset once on the builder) every .aud-sec that is displayed, in document
    order, and a computed style cannot give a counter's value, so it is the position among the displayed cards - with the check that nothing between the
    builder and a card restarts the count."""
    return page.evaluate("""(ids) => {
        const shown = [...document.querySelectorAll('#cz-builder .aud-sec')].filter((el) => el.getClientRects().length);
        const restarts = [...document.querySelectorAll('#cz-builder *')].filter((el) => el.id !== 'cz-builder' && getComputedStyle(el).counterReset.indexOf('aud-step') >= 0).length;
        return ids.map((id) => { const at = shown.findIndex((el) => el.id === id); return restarts ? -1 : (at < 0 ? null : at + 1); }); }""", ids)


def test_a_method_that_owns_its_geography_swaps_the_shared_where_card_for_its_own_and_the_numbers_stay_in_order(dock):
    assert dock.is_visible("#cz-sec-where") and dock.is_hidden("#cz-method-where")             # no method on show: no empty cell where its card would sit
    assert step_numbers(dock, ["cz-sec-where", "cz-sec-when", "cz-sec-who", "cz-sec-lang", "cz-sec-aud"]) == [1, 2, 3, 4, 5]
    choose_product(dock, "owner sku")
    assert dock.is_hidden("#cz-sec-where") and dock.is_visible("#po-where") and dock.is_visible("#cz-method-where")
    assert dock.is_visible('#cz-method-where > .cz-method-where-panel[data-method="probe_owner"]') and dock.is_visible("#po-sec")
    assert dock.query_selector("#cz-method-where .aud-builder") is None                       # a bare card: a nested builder would start the numbers again
    # Where (the method's) = 1, When = 2, the method's own card = 3
    assert step_numbers(dock, ["po-where", "cz-sec-when", "po-sec"]) == [1, 2, 3]
    assert text(dock, "#cz-go") == "Estimate probe →"
    choose_product(dock, "plain sku")                                                          # another method: its Where card goes, the shared one is back
    assert dock.is_hidden("#po-where") and dock.is_visible("#cz-sec-where") and dock.is_hidden("#cz-method-where")
    choose_product(dock, "roku")
    assert dock.is_visible("#cz-sec-where") and dock.is_hidden("#cz-method-where") and dock.is_visible("#cz-aud")


def test_a_method_that_owns_its_geography_sends_no_geography_and_nothing_is_ever_matched(dock):
    choose_product(dock, "owner sku")
    dock.evaluate("Constanza.open({ geo: 'Dallas', product: 'Owner SKU' })")                   # the dock is handed a geography: it is seeded, never sent, never matched
    dock.evaluate("OWNER.setPlace('Marseille')")
    sent = pull(dock)
    assert sent["geo_text"] == "" and sent["geo_type_hint"] is None and sent["probe_owner"]["place"] == "Marseille"
    assert sent["product_name"] == OWNER and sent["audience"] is None and sent["national_monthly_total"] is None
    dock.wait_for_timeout(500)
    assert dock.world.resolves == [] and "resolve" not in dock.world.hits                      # type: ignore[attr-defined]
    assert dock.evaluate("Constanza._state.geo") == "Dallas"                                    # what was in the shared box is untouched


def test_the_shared_box_is_back_with_what_was_typed_when_a_product_of_the_platform_is_chosen_again(dock):
    dock.fill("#cz-geo", "Phoenix")
    dock.wait_for_function("!document.getElementById('cz-where-sum').textContent.startsWith('Matching')")
    assert len(dock.world.resolves) == 1                                                       # type: ignore[attr-defined]
    choose_product(dock, "owner sku")
    dock.evaluate("OWNER.setPlace('Marseille')")
    choose_product(dock, "roku")
    assert dock.input_value("#cz-geo") == "Phoenix" and dock.is_visible("#cz-geo")
    assert text(dock, "#cz-where-sum").startswith("1 area")
    assert pull(dock)["geo_text"] == "Phoenix"
    assert dock.world.resolves[0]["geo_text"] == "Phoenix"                                      # type: ignore[attr-defined]


def test_the_geography_guard_is_skipped_for_a_method_that_owns_it_and_its_own_refusal_is_said_instead(dock):
    choose_product(dock, "owner sku")
    assert dock.input_value("#cz-geo") == ""
    dock.click("#cz-go")
    dock.wait_for_selector("#cz-result .cz-error")
    said = text(dock, "#cz-result .cz-error")
    assert "Choose where first." in said and "Type a geography first" not in said               # (canPull of the method, not the shared box's rule)
    assert dock.world.requests == []                                                            # type: ignore[attr-defined]
    dock.evaluate("OWNER.setPlace('Marseille')")
    assert pull(dock)["probe_owner"]["place"] == "Marseille"
    choose_product(dock, "roku")                                                                # a platform product still wants its geography
    dock.click("#cz-go")
    dock.wait_for_selector("#cz-result .cz-error")
    assert "Type a geography first" in text(dock, "#cz-result .cz-error")


def test_the_result_says_which_place_it_was_pulled_for_and_keeps_saying_it_when_the_form_moves_on(dock):
    choose_product(dock, "owner sku")
    dock.evaluate("OWNER.setPlace('Marseille')")
    pull(dock)
    assert text(dock, "#cz-result .cz-card-geo") == "Marseille"                                # the place the method said at pull time (context().geo)
    assert dock.evaluate("Constanza._state.resultCtx.geo") == "Marseille"
    dock.evaluate("OWNER.setPlace('Nice')")                                                    # the form moves on: the result does not
    assert text(dock, "#cz-result .cz-card-geo") == "Marseille"
    assert dock.is_visible("#cz-result .cz-stale") and "form has changed" in text(dock, "#cz-result .cz-stale")
    summary = dock.evaluate("Constanza._pure.summaryText(Constanza._state.result)")
    assert "Geography: Marseille" in summary and "Probe line: Probe <i>name</i>" in summary and "Unique IDs" not in summary


def test_a_method_that_owns_its_geography_but_does_not_say_where_is_told_so_once_and_still_works(dock):
    dock.evaluate("""() => {
        state.catalog.products_by_family.Probes.push(item('Nowhere SKU', 'probe_nowhere'));
        Constanza.registerMethod('probe_nowhere', (c) => Object.assign(ownerFactory(c), { context: () => ({ summary: [] }), channel: 'probe_nowhere', canPull: () => ({ ok: true }) })); }""")
    choose_product(dock, "nowhere")
    pull(dock)
    pull(dock)
    said = [line for line in dock.console_errors if "context() must say where" in line]          # type: ignore[attr-defined]
    assert len(said) == 1 and "probe_nowhere" in said[0]
    assert dock.evaluate("Constanza._state.resultCtx.geo") == "" and dock.is_visible("#cz-result .po-headline")


# ================================================================================================================== goState

def test_a_method_that_is_not_ready_keeps_the_pull_button_off_says_why_and_sends_nothing(dock):
    dock.evaluate("PCFG.connected = false")
    choose_product(dock, "owner sku")
    assert dock.get_attribute("#cz-go", "aria-disabled") == "true"
    assert dock.is_visible("#cz-go-reason") and "isn't connected yet" in text(dock, "#cz-go-reason")
    assert dock.get_attribute("#cz-go", "aria-describedby") == "cz-go-reason"
    dock.evaluate("OWNER.setPlace('Marseille')")
    dock.click("#cz-go", force=True)                                                           # (a button that says aria-disabled is pressed anyway: Playwright would wait for it)
    dock.wait_for_timeout(200)
    assert dock.world.requests == [] and "isn't connected yet" in text(dock, "#cz-status")      # type: ignore[attr-defined]  (a press says it again, to a screen reader)
    dock.evaluate("PCFG.connected = true; OWNER.ctx.refreshGo()")                              # a connection answer came in: the dock reads the state again
    assert dock.get_attribute("#cz-go", "aria-disabled") == "false" and dock.is_hidden("#cz-go-reason")
    assert pull(dock)["probe_owner"]["place"] == "Marseille"


def test_the_pull_button_is_off_only_for_the_product_whose_method_is_not_ready(dock):
    dock.evaluate("PCFG.connected = false")
    choose_product(dock, "owner sku")
    assert dock.get_attribute("#cz-go", "aria-disabled") == "true"
    choose_product(dock, "plain sku")
    assert dock.get_attribute("#cz-go", "aria-disabled") == "false" and dock.is_hidden("#cz-go-reason")
    choose_product(dock, "roku")
    assert dock.get_attribute("#cz-go", "aria-disabled") == "false"
    choose_product(dock, "owner sku")
    assert dock.get_attribute("#cz-go", "aria-disabled") == "true"


def test_a_connection_that_drops_during_a_pull_keeps_the_button_off_when_the_pull_ends(dock):
    choose_product(dock, "owner sku")
    dock.evaluate("OWNER.setPlace('Marseille')")
    dock.world.hold.add("estimate")                                                            # type: ignore[attr-defined]
    dock.click("#cz-go")
    dock.wait_for_function("document.getElementById('cz-go').getAttribute('aria-disabled') === 'true'")
    dock.evaluate("PCFG.connected = false; OWNER.ctx.refreshGo()")
    dock.world.hold.clear()                                                                    # type: ignore[attr-defined]
    dock.world.release()                                                                       # type: ignore[attr-defined]
    dock.wait_for_selector("#cz-result .cz-headline")
    assert dock.get_attribute("#cz-go", "aria-disabled") == "true" and "isn't connected yet" in text(dock, "#cz-go-reason")


# ================================================================================================================== the lifecycle (2.6.1)

def test_the_dock_says_which_line_it_is_open_for_before_a_method_hears_of_its_product(dock):
    dock.evaluate("defineLine('L1')")
    clear_log(dock)
    open_for(dock, "L1")
    entries = log(dock)
    kinds = [e[0] for e in entries]
    assert kinds.index("onLine") < kinds.index("onSelect")                                      # the line first, then the product
    assert ["onLine", "L1", "L1"] in entries                                                    # (the line's facts, and ctx.S.lineId is already that line inside the call)
    assert ["onSelect", OWNER, "L1", "L1"] in entries                                           # so inside onSelect ctx.line() is the NEW line
    assert ["plain.reset", None, "L1"] in entries                                               # a method with no onLine is reset, as it always was (prevLid: none before)
    assert ["onSelect", "Roku", "L1", "L1"] not in entries and dock.evaluate("Constanza._state.product") == OWNER


def test_opening_the_same_line_again_keeps_a_per_line_workspace_while_a_plain_method_is_reset_as_before(dock):
    dock.evaluate("defineLine('L1'); defineLine('L2')")
    open_for(dock, "L1")
    dock.evaluate("OWNER.toggle('soccer')")
    clear_log(dock)
    open_for(dock, "L1")                                                                         # the SAME line
    assert ["onLine", "L1", "L1"] in log(dock) and ["plain.reset", "L1", "L1"] in log(dock)       # (reset(prevLid): the line the dock WAS open for)
    assert dock.evaluate("OWNER.ws().picks") == ["soccer"]                                       # kept
    open_for(dock, "L2")
    assert dock.evaluate("OWNER.ws().picks") == [] and dock.evaluate("OWNER.W.byKey.L1.picks") == ["soccer"]      # a line of its own; the first one's is still there
    assert ["onLine", "L2", "L2"] in log(dock) and ["plain.reset", "L1", "L2"] in log(dock)


def test_a_method_that_throws_when_told_about_a_line_never_takes_the_dock_down(dock):
    dock.evaluate("""() => {
        state.catalog.products_by_family.Probes.push(item('Throwy SKU', 'probe_throwy'));
        Constanza.registerMethod('probe_throwy', (c) => Object.assign(plainFactory(c), { channel: 'probe_throwy', onLine: () => { throw new Error('line trouble'); },
          onSelect: () => { throw new Error('select trouble'); }, reset: () => { throw new Error('reset trouble'); } })); }""")
    dock.evaluate("defineLine('L1')")
    open_for(dock, "L1")
    open_for(dock, "L1", product="Throwy SKU")
    dock.click("#cz-reset")
    assert dock.is_visible("#constanza-dock") and dock.evaluate("Constanza._state.lineId") is None
    said = " ".join(dock.console_errors)                                                          # type: ignore[attr-defined]
    assert "line trouble" in said and "select trouble" in said and "reset trouble" in said
    choose_product(dock, "roku")
    dock.fill("#cz-geo", "Los Angeles")
    assert pull(dock)["product_name"] == "Roku"


def test_the_bubble_opens_the_dock_on_its_own_and_drops_a_line_and_the_result_a_saving_method_brought_for_it(dock):
    dock.evaluate("defineLine('L1')")
    open_for(dock, "L1")
    dock.evaluate("OWNER.setPlace('Marseille')")
    pull(dock)
    assert dock.evaluate("Constanza._state.resultLid") == "L1"
    dock.evaluate("Constanza.close()")
    clear_log(dock)
    dock.evaluate("Constanza.open()")                                                             # the bubble: no options
    assert dock.evaluate("Constanza._state.lineId") is None and dock.evaluate("Constanza._state.result") is None
    assert dock.evaluate("Constanza._state.resultLid") is None and dock.query_selector("#cz-result .cz-headline") is None and dock.is_visible("#cz-result .cz-welcome")
    assert ["onLine", None, None] in log(dock)                                                    # the method is told the line is gone
    assert dock.evaluate("Constanza._state.monthsOverride") is None


def test_the_bubble_leaves_the_result_of_a_method_that_does_not_save_as_it_always_did(dock):
    dock.evaluate("defineLine('L1')")
    open_for(dock, "L1", product=PLAIN)
    pull(dock)
    assert dock.is_visible("#cz-result .pp-headline")
    dock.evaluate("Constanza.close()")
    clear_log(dock)
    dock.evaluate("Constanza.open()")
    assert dock.evaluate("Constanza._state.lineId") is None and dock.is_visible("#cz-result .pp-headline")
    assert [e for e in log(dock) if e[0] == "plain.reset"] == []                                   # (a method with no onLine is not reset when the dock only changes hands)


def test_reset_tells_a_method_which_line_the_dock_was_open_for_after_the_dock_forgot_it(dock):
    dock.evaluate("defineLine('L1')")
    open_for(dock, "L1")
    dock.evaluate("OWNER.toggle('soccer')")
    clear_log(dock)
    dock.click("#cz-reset")
    assert ["reset", "L1", None] in log(dock) and ["plain.reset", "L1", None] in log(dock)         # prevLid = L1, and ctx.S.lineId is already null inside the call
    assert dock.evaluate("'L1' in OWNER.W.byKey") is False                                          # that line's workspace is gone: its card compares "no workspace" = unchanged
    clear_log(dock)
    dock.click("#cz-reset")
    assert ["reset", None, None] in log(dock)                                                      # the dock on its own: no line


def test_a_new_proposal_drops_every_workspace_and_resets_the_dock(dock):
    dock.evaluate("defineLine('L1'); defineLine('L2')")
    open_for(dock, "L1")
    open_for(dock, "L2")
    clear_log(dock)
    dock.evaluate("Constanza.onProposalReset()")
    assert log(dock)[0] == ["onProposalReset"] and ["reset", None, None] in log(dock)
    assert dock.evaluate("Object.keys(OWNER.W.byKey)") == []
    assert dock.evaluate("Constanza._state.lineId") is None and dock.evaluate("Constanza._state.result") is None


def test_an_answer_that_arrives_after_the_dock_moved_on_is_dropped_and_never_saved(dock):
    dock.evaluate("defineLine('L1')")
    open_for(dock, "L1")
    dock.evaluate("OWNER.setPlace('Marseille')")
    dock.world.hold.add("estimate")                                                                 # type: ignore[attr-defined]
    dock.click("#cz-go")
    dock.wait_for_function("document.getElementById('cz-go').getAttribute('aria-disabled') === 'true'")
    dock.click("#cz-reset")
    dock.world.hold.clear()                                                                         # type: ignore[attr-defined]
    dock.world.release()                                                                            # type: ignore[attr-defined]
    dock.wait_for_timeout(300)
    assert dock.evaluate("HOST.saved") == [] and dock.evaluate("Constanza._state.result") is None


# ================================================================================================================== persist: restore on open, save on pull

def native(result_for, **over):
    """A saved estimate as the page keeps it (availsData[lid].forecast_snapshot), with its request's keys in an order of their own."""
    request = {"probe_owner": {"picks": ["a", "b"], "place": "Nice", "budget": 2000}, "months": 2, "geo_type_hint": None, "geo_text": "", "flight_days": 38, "product_name": OWNER,
               "audience": None, "national_monthly_total": None}
    snap = {"v": 1, "method": "probe_owner", "saved_at": "2026-11-01T12:00:00+00:00", "request": request, "result": result_for(request),
            "line": {"product_name": OWNER, "method": "probe_owner", "months": 2, "geo_text": "Los Angeles", "target_text": "Adults 25-54", "budget": 2000, "cpm": 12}}
    snap.update(over)
    return snap


def saved_estimate(request):
    return estimate_answer({"product_name": OWNER, "months": 2, "probe_owner": request["probe_owner"]})


def keep(page, lid, snapshot):
    page.evaluate("([lid, snap]) => { HOST.snaps[lid] = snap; }", [lid, snapshot])


def test_opening_the_dock_for_a_line_with_a_saved_estimate_shows_it_with_the_choices_that_made_it(dock):
    dock.evaluate("defineLine('L1')")
    keep(dock, "L1", native(saved_estimate))
    clear_log(dock)
    open_for(dock, "L1")
    entries = log(dock)
    kinds = [e[0] for e in entries]
    assert kinds.index("onLine") < kinds.index("onSelect") < kinds.index("restore")              # the line, the product, then the saved choices
    assert ["restore", {"picks": ["a", "b"], "place": "Nice", "budget": 2000}] in entries
    assert dock.evaluate("OWNER.ws().place") == "Nice" and dock.input_value("#cz-flight") == "38"
    assert text(dock, "#cz-result .po-headline .cz-mvalue") == "Probe <i>name</i>"                # shown as text, never as markup
    assert dock.query_selector("#cz-result .cz-stale") is None                                    # the form asks for what that estimate was pulled with
    assert dock.is_visible("#cz-result .cz-insert") and text(dock, "#cz-result .cz-insert") == "Insert into Avails — Owner SKU"
    assert dock.evaluate("[Constanza._state.resultLid, Constanza._state.resultCtx.geo, Constanza._state.resultSnapshot.saved_at]") == ["L1", "Nice", "2026-11-01T12:00:00+00:00"]


@pytest.mark.parametrize("change,said", [({"budget": 3000}, "the budget changed"), ({"months": 3}, "the flight length changed"), ({"geo_text": "Phoenix"}, "the proposal’s geography changed"),
                                         ({"target_text": "Women 18-34"}, "the line’s target changed"), ({"cpm": 18}, "the CPM changed")])
def test_a_saved_estimate_whose_line_has_moved_on_says_what_changed_and_offers_no_insert(dock, change, said):
    dock.evaluate("(c) => { defineLine('L1', c); }", change)
    keep(dock, "L1", native(saved_estimate))
    open_for(dock, "L1", months=change.get("months", 2))
    banner = text(dock, "#cz-result .cz-stale")
    assert said in banner and banner.startswith("The line changed since this was pulled") and banner.endswith("pull again to update.")
    assert dock.is_visible("#cz-result .po-headline")                                            # the numbers are still shown, as what they were
    assert dock.query_selector("#cz-result .cz-insert") is None                                  # ... but they cannot be inserted as current


def test_a_restored_result_whose_line_moved_on_is_said_to_be_out_of_date_once_and_a_new_pull_says_its_own_words(dock):
    status = lambda: (dock.text_content("#cz-status") or "").strip()                                # noqa: E731
    dock.evaluate("defineLine('L1', { budget: 3000 })")
    keep(dock, "L1", native(saved_estimate))
    open_for(dock, "L1")
    banner = text(dock, "#cz-result .cz-stale")
    assert banner == "The line changed since this was pulled (the budget changed) — pull again to update." and status() == banner
    dock.evaluate("document.getElementById('cz-status').textContent = ''")
    dock.evaluate("OWNER.toggle('z')")                                                            # still out of date (now for one more reason): nothing new to say
    dock.evaluate("OWNER.toggle('z')")
    dock.evaluate("Constanza._state.showAllAreas = true; Constanza._pure.markStale()")             # (a repaint of the same result, too)
    assert status() == "" and dock.query_selector("#cz-result .cz-stale") is not None
    dock.evaluate("OWNER.setPlace('Marseille')")
    pull(dock)                                                                                     # a new pull is current, and says its own words
    assert status().startswith("Pulled ") and dock.query_selector("#cz-result .cz-stale") is None
    dock.evaluate("OWNER.toggle('z')")                                                            # ... and out of date again, said again
    assert status() == "The form has changed since this was pulled — pull again to update."


def test_a_result_shown_again_for_a_line_says_its_own_state_afresh(dock):
    status = lambda: (dock.text_content("#cz-status") or "").strip()                                # noqa: E731
    dock.evaluate("defineLine('L1')")
    keep(dock, "L1", native(saved_estimate))
    open_for(dock, "L1")
    assert status() == ""                                                                          # a saved result that still matches its line and form: nothing to say
    dock.evaluate("OWNER.toggle('z')")                                                            # an edit that is never pulled: out of date, said
    assert status() == "The form has changed since this was pulled — pull again to update."
    dock.evaluate("document.getElementById('cz-status').textContent = ''; Constanza.close()")
    open_for(dock, "L1")                                                                          # the same line again: the saved result is shown anew, and it is out of date
    assert "the audience in Constanza changed" in status()


def test_a_method_can_add_its_own_reasons_a_saved_estimate_is_out_of_date(dock):
    dock.evaluate("PCFG.reason = 'the probe moved'; defineLine('L1')")
    keep(dock, "L1", native(saved_estimate))
    open_for(dock, "L1")
    assert "the probe moved" in text(dock, "#cz-result .cz-stale") and dock.query_selector("#cz-result .cz-insert") is None


def test_a_saved_estimate_of_another_method_or_with_nothing_to_show_is_not_drawn(dock):
    dock.evaluate("defineLine('L1')")
    keep(dock, "L1", native(saved_estimate, method="probe_plain"))                                # (the page does not hand such a one, but the dock does not trust it)
    open_for(dock, "L1")
    assert dock.evaluate("Constanza._state.result") is None and dock.is_visible("#cz-result .cz-welcome")
    keep(dock, "L1", native(saved_estimate, result=None))                                         # a budget-only estimate: no result, the form still starts from what is saved
    open_for(dock, "L1")
    assert dock.evaluate("Constanza._state.result") is None and dock.is_visible("#cz-result .cz-welcome")
    assert dock.evaluate("OWNER.ws().place") == "Nice"                                            # (the method seeded its workspace from the request in onLine)


def test_a_saved_estimate_that_cannot_be_read_says_so_and_leaves_the_dock_usable(dock):
    dock.evaluate("defineLine('L1')")
    keep(dock, "L1", native(saved_estimate, result={"method": "probe_owner", "product_name": OWNER, "months": "x", "notes": "x"}))
    open_for(dock, "L1")
    assert "The saved estimate couldn’t be read" in text(dock, "#cz-result .cz-error") and dock.evaluate("Constanza._state.result") is None
    dock.evaluate("OWNER.setPlace('Marseille')")
    assert pull(dock)["probe_owner"]["place"] == "Marseille" and dock.is_visible("#cz-result .po-headline")


def test_a_restored_result_is_judged_against_the_form_not_against_the_key_order_it_was_saved_in(dock):
    dock.evaluate("defineLine('L1')")
    keep(dock, "L1", native(saved_estimate))
    open_for(dock, "L1")
    assert dock.query_selector("#cz-result .cz-stale") is None
    dock.evaluate("OWNER.toggle('c')")                                                            # a different audience: the banner says the form changed
    assert "form has changed" in text(dock, "#cz-result .cz-stale")
    dock.evaluate("OWNER.toggle('c')")                                                            # ... put back: it goes
    assert dock.query_selector("#cz-result .cz-stale") is None
    dock.fill("#cz-flight", "45")                                                                 # (a different flight, too)
    assert "form has changed" in text(dock, "#cz-result .cz-stale")


def test_edits_made_in_the_dock_and_never_pulled_survive_reopening_the_line_and_are_reported(dock):
    dock.evaluate("defineLine('L1')")
    keep(dock, "L1", native(saved_estimate))
    open_for(dock, "L1")
    dock.evaluate("OWNER.toggle('z')")
    dock.evaluate("Constanza.close()")
    open_for(dock, "L1")                                                                          # the same line: the method keeps what was edited
    assert dock.evaluate("OWNER.ws().picks.slice().sort()") == ["a", "b", "z"]
    assert "the audience in Constanza changed" in text(dock, "#cz-result .cz-stale")
    assert dock.query_selector("#cz-result .cz-insert") is None


def test_a_saved_result_is_described_by_what_it_was_pulled_with_even_when_the_form_was_edited_and_never_pulled(dock):
    """The dock opened again for a line whose workspace the planner edited: the method keeps the edit (restore() leaves a touched workspace alone), the SAVED result
    is shown with the banner and no Insert - and its header, the words about what was asked and the copied summary are not the unpulled edit's."""
    dock.evaluate("defineLine('L1')")
    keep(dock, "L1", native(saved_estimate))
    open_for(dock, "L1")
    assert text(dock, ".cz-card-geo") == "Nice" and text(dock, "#cz-result .po-body") == 'asked: ["place Nice"]'
    dock.evaluate("OWNER.setPlace('Marseille')")
    dock.evaluate("Constanza.close()")
    open_for(dock, "L1")
    assert dock.evaluate("OWNER.ws().place") == "Marseille"                                       # the planner's edit is still in the form
    assert "the audience in Constanza changed" in text(dock, "#cz-result .cz-stale") and dock.query_selector("#cz-result .cz-insert") is None
    result = text(dock, "#cz-result")
    assert "Marseille" not in result and dock.query_selector(".cz-card-geo") is None              # no place to name: the form's is not the result's
    assert text(dock, "#cz-result .po-body") == "asked: null"                                     # (the method falls back to the answer's own echo)
    copied = dock.evaluate("Constanza._pure.summaryText(Constanza._state.result)")
    assert "Marseille" not in copied and "Geography:" not in copied and "Probe line:" in copied and "Flight: 2 months" in copied
    # ... and the edit put back is the saved choices again: now the form does describe the result
    dock.evaluate("OWNER.setPlace('Nice')")
    dock.evaluate("Constanza.close()")
    open_for(dock, "L1")
    assert dock.query_selector("#cz-result .cz-stale") is None and text(dock, ".cz-card-geo") == "Nice" and text(dock, "#cz-result .po-body") == 'asked: ["place Nice"]'


def test_the_card_behind_the_dock_is_told_when_the_form_starts_and_stops_differing_from_its_saved_estimate(dock):
    dock.evaluate("defineLine('L1')")
    keep(dock, "L1", native(saved_estimate))
    open_for(dock, "L1")
    dock.evaluate("HOST.dirty.length = 0")
    dock.evaluate("OWNER.toggle('z')")
    dock.evaluate("OWNER.toggle('y')")                                                            # a second edit: nothing new to say
    assert dock.evaluate("HOST.dirty") == [["L1", True]]
    dock.evaluate("OWNER.toggle('z'); OWNER.toggle('y')")
    assert dock.evaluate("HOST.dirty") == [["L1", True], ["L1", False]]
    dock.evaluate("OWNER.toggle('z')")
    dock.click("#cz-reset")                                                                       # Reset forgets the unpulled edits: the card is told it no longer differs
    assert dock.evaluate("HOST.dirty")[-1] == ["L1", False]


def test_the_card_is_told_again_after_its_own_recalculate_replaced_the_estimate_the_dock_was_judged_against(dock):
    """The Step 05 card's Recalculate saves a NEW estimate (and reads clean, its choices being the dock's); the dock was last told "differs" for the OLD one, and the next
    edit must reach the card again, or it keeps reading fresh with Insert on while the dock's audience is not its own."""
    dock.evaluate("defineLine('L1')")
    keep(dock, "L1", native(saved_estimate))
    open_for(dock, "L1")
    dock.evaluate("HOST.dirty.length = 0")
    dock.evaluate("OWNER.toggle('z')")
    assert dock.evaluate("HOST.dirty") == [["L1", True]]
    # the card's Recalculate: a snapshot whose request is the dock's choices, saved a little later
    dock.evaluate("""() => { const old = HOST.snaps.L1;
        HOST.snaps.L1 = Object.assign({}, old, { saved_at: '2026-11-01T12:05:00+00:00', request: Object.assign({}, old.request, { probe_owner: { picks: ['a', 'b', 'z'], place: 'Nice', budget: 2000 } }) });
        HOST.dirty.length = 0; }""")
    dock.evaluate("OWNER.toggle('y')")
    assert dock.evaluate("HOST.dirty") == [["L1", True]]
    dock.evaluate("OWNER.toggle('x')")                                                             # (still one telling per change: a second edit of the same estimate is not news)
    assert dock.evaluate("HOST.dirty") == [["L1", True]]


def test_no_card_is_told_anything_for_a_line_without_a_saved_estimate_or_on_a_page_without_a_host(dock):
    dock.evaluate("defineLine('L1')")
    open_for(dock, "L1")
    dock.evaluate("OWNER.setPlace('Marseille')")
    pull(dock)
    dock.evaluate("HOST.snaps = {}; HOST.dirty.length = 0")                                       # (the pull saved one: drop it, so the line has none)
    dock.evaluate("OWNER.toggle('z')")
    assert dock.evaluate("HOST.dirty") == []
    dock.evaluate("window.constanzaHost = { lineFacts: HOST.lines.L1 ? () => Object.assign({}, HOST.lines.L1) : () => null }")      # a host with no onDirty at all
    dock.evaluate("OWNER.toggle('y')")


def test_a_pull_is_judged_by_canonical_json_so_a_plugin_that_orders_its_keys_differently_each_time_never_looks_changed(dock):
    dock.evaluate("""() => {
        state.catalog.products_by_family.Probes.push(item('Order SKU', 'probe_order'));
        let flip = false;
        Constanza.registerMethod('probe_order', (c) => Object.assign(plainFactory(c), { channel: 'probe_order',
          requestPart: () => { flip = !flip; return flip ? { probe_order: { a: 1, b: { c: 1, d: [2, { e: 1, f: 2 }] } } } : { probe_order: { b: { d: [2, { f: 2, e: 1 }], c: 1 }, a: 1 } }; } })); }""")
    choose_product(dock, "order sku")
    dock.fill("#cz-geo", "Los Angeles")
    pull(dock)
    for _ in range(4):
        dock.evaluate("OWNER && Constanza._state && document.getElementById('cz-geo').dispatchEvent(new Event('input', { bubbles: true }))")
    assert dock.query_selector("#cz-result .cz-stale") is None
    assert dock.evaluate("Constanza.canonicalJson({ b: 1, a: { d: 1, c: [{ z: 1, y: 2 }] } })") == '{"a":{"c":[{"y":2,"z":1}],"d":1},"b":1}'


def pulled(page, lid="L1", place="Marseille", product=OWNER, **line):
    page.evaluate("([lid, over]) => defineLine(lid, over)", [lid, line])
    open_for(page, lid, product=product)
    page.evaluate("(p) => OWNER.setPlace(p)", place)
    return pull(page)


def test_a_pull_for_a_line_is_saved_under_that_line_with_the_line_as_it_was_when_the_request_went_out(dock):
    sent = pulled(dock, budget=2000)
    saved = dock.evaluate("HOST.saved")
    assert len(saved) == 1 and saved[0][0] == "L1"
    snap = saved[0][1]
    assert set(snap) == {"v", "method", "saved_at", "request", "result", "line"} and snap["v"] == 1 and snap["method"] == "probe_owner"
    assert snap["request"] == sent and snap["result"]["method"] == "probe_owner" and snap["result"]["probe_owner"]["place"] == "Marseille"
    assert snap["line"] == {"product_name": OWNER, "method": "probe_owner", "months": 2, "geo_text": "Los Angeles", "target_text": "Adults 25-54", "budget": 2000, "cpm": 12}
    assert dock.evaluate("HOST.snaps.L1.saved_at") == snap["saved_at"] and re.match(r"\d{4}-\d\d-\d\dT", snap["saved_at"])
    assert dock.evaluate("Constanza._state.resultSnapshot.saved_at") == snap["saved_at"]


def test_the_line_is_read_before_the_request_goes_out_not_when_the_answer_comes_back(dock):
    dock.evaluate("defineLine('L1', { budget: 2000 })")
    open_for(dock, "L1")
    dock.evaluate("OWNER.setPlace('Marseille')")
    dock.world.hold.add("estimate")                                                               # type: ignore[attr-defined]
    dock.click("#cz-go")
    dock.wait_for_function("document.getElementById('cz-go').getAttribute('aria-disabled') === 'true'")
    dock.evaluate("HOST.lines.L1.budget = 9999")                                                  # the planner edits the line while the estimate is on its way
    dock.world.hold.clear()                                                                       # type: ignore[attr-defined]
    dock.world.release()                                                                          # type: ignore[attr-defined]
    dock.wait_for_selector("#cz-result .cz-headline")
    snap = dock.evaluate("HOST.saved[0][1]")
    assert snap["line"]["budget"] == 2000 and snap["request"]["probe_owner"]["budget"] == 2000     # what the estimate was computed for: so the card will say the budget changed


def test_the_facts_an_answer_says_it_really_used_are_kept_when_the_line_had_none(dock):
    pulled(dock, budget=0, cpm=None)
    snap = dock.evaluate("HOST.saved[0][1]")
    assert snap["line"]["budget"] == 0 and snap["line"]["cpm"] == 12                              # (snapshotLine: the answer's own CPM; a budget of 0 is no budget)


def test_a_pull_is_not_saved_under_a_line_of_another_product_a_vanished_line_or_when_there_is_no_line(dock):
    dock.evaluate("defineLine('L1')")
    open_for(dock, "L1")
    choose_product(dock, "owner sku 2")                                                           # the product is switched while S.lineId still names the old line
    dock.evaluate("OWNER.setPlace('Marseille')")
    assert pull(dock)["product_name"] == OWNER2
    assert dock.evaluate("HOST.saved") == [] and dock.evaluate("Constanza._state.resultSnapshot") is None
    # the line is deleted while the estimate is on its way: the page is asked to save it (and refuses), nothing is kept
    open_for(dock, "L1")
    dock.evaluate("OWNER.setPlace('Marseille')")
    dock.world.hold.add("estimate")                                                               # type: ignore[attr-defined]
    dock.click("#cz-go")
    dock.wait_for_function("document.getElementById('cz-go').getAttribute('aria-disabled') === 'true'")
    dock.evaluate("dropLine('L1')")
    dock.world.hold.clear()                                                                       # type: ignore[attr-defined]
    dock.world.release()                                                                          # type: ignore[attr-defined]
    dock.wait_for_selector("#cz-result .cz-headline")
    assert dock.evaluate("HOST.snaps") == {} and dock.evaluate("Constanza._state.resultSnapshot") is None
    # the bubble: no line, so nothing to file it under
    dock.evaluate("HOST.saved.length = 0; Constanza.close(); Constanza.open()")
    choose_product(dock, "owner sku")
    dock.evaluate("OWNER.setPlace('Marseille')")
    pull(dock)
    assert dock.evaluate("HOST.saved") == [] and dock.evaluate("Constanza._state.resultLid") is None


def test_a_method_that_does_not_save_leaves_nothing_on_the_line_and_a_page_without_a_host_still_pulls(dock):
    dock.evaluate("defineLine('L1')")
    open_for(dock, "L1", product=PLAIN)
    pull(dock)
    assert dock.evaluate("HOST.saved") == []
    dock.evaluate("window.constanzaHost = undefined")                                             # no host object at all: the dock on a page with no proposal
    open_for(dock, "L1")
    dock.evaluate("OWNER.setPlace('Marseille')")
    assert pull(dock)["probe_owner"]["place"] == "Marseille"
    dock.evaluate("window.constanzaHost = { saveSnapshot: () => { throw new Error('page trouble'); }, lineFacts: (lid) => Object.assign({}, HOST.lines[lid]), snapshot: () => { throw new Error('page trouble'); } }")
    open_for(dock, "L1")
    dock.evaluate("OWNER.setPlace('Nice')")
    assert pull(dock)["probe_owner"]["place"] == "Nice" and dock.is_visible("#cz-result .po-headline")     # a page that throws is not the dock's problem


def test_a_failed_pull_says_it_in_the_methods_words_and_tells_it(dock):
    dock.evaluate("defineLine('L1')")
    open_for(dock, "L1")
    dock.evaluate("OWNER.setPlace('Marseille'); LOG.length = 0")
    dock.world.fail["estimate"] = (422, {"detail": "That place is not on Meta’s list."})          # type: ignore[attr-defined]
    dock.click("#cz-go")
    dock.wait_for_selector("#cz-result .cz-error")
    assert "PROBE: That place is not on Meta’s list." in text(dock, "#cz-result .cz-error")
    assert ["onError", 422] in log(dock) and dock.evaluate("HOST.saved") == []
    assert dock.world.resolves == []                                                              # type: ignore[attr-defined]  (no geography help for a method that owns it)


# ------------------------------------------------------------------------------------------------------------------ Insert

def test_insert_files_the_snapshot_under_the_line_the_result_was_pulled_for_and_gives_another_line_the_numbers_alone(dock):
    dock.evaluate("defineLine('L3')")
    pulled(dock)                                                                                   # L1
    buttons = dock.eval_on_selector_all("#cz-result .cz-insert", "els => els.map((e) => e.dataset.lid)")
    assert buttons[0] == "L1" and set(buttons) == {"L1", "L3"}                                    # the line the dock is open for first
    dock.click('#cz-result .cz-insert[data-lid="L1"]')
    mine = dock.evaluate("HOST.inserted[0]")
    assert mine["lid"] == "L1" and mine["imps"] == 5_500_000 and mine["uniques"] == 1_000_000 and mine["argc"] == 5
    assert mine["info"] == {"method": "probe_geo", "dmas": ["Marseille"], "unmatched_tokens": [], "win_rate": None, "hispanic_share": None}      # the method's own geoInfo
    assert mine["snapshot"]["saved_at"] == dock.evaluate("HOST.snaps.L1.saved_at") and mine["undef"] is False
    dock.click('#cz-result .cz-insert[data-lid="L3"]')
    other = dock.evaluate("HOST.inserted[1]")
    assert other["lid"] == "L3" and other["undef"] is True                                         # undefined: the page drops that line's own snapshot (it no longer describes its numbers)
    assert "Inserted into Avails" in text(dock, '#cz-result .cz-insert[data-lid="L1"]')


def test_an_insert_for_a_line_that_is_gone_says_so_and_writes_nothing(dock):
    dock.evaluate("defineLine('L3')")
    pulled(dock)
    dock.evaluate("dropLine('L3')")
    dock.click('#cz-result .cz-insert[data-lid="L3"]')
    assert dock.evaluate("HOST.inserted") == [] and "no longer in the proposal" in text(dock, '#cz-result .cz-insert[data-lid="L3"]')


def test_an_insert_from_a_restored_result_files_the_snapshot_it_was_restored_from_without_the_in_memory_marker(dock):
    dock.evaluate("defineLine('L1')")
    keep(dock, "L1", native(saved_estimate, legacy=True))                                          # (a snapshot read from an older shape in memory carries `legacy`)
    open_for(dock, "L1")
    dock.click('#cz-result .cz-insert[data-lid="L1"]')
    snap = dock.evaluate("HOST.inserted[0].snapshot")
    assert snap["method"] == "probe_owner" and "legacy" not in snap and snap["saved_at"] == "2026-11-01T12:00:00+00:00"


# ------------------------------------------------------------------------------------------------------------------ lineMonths

def test_a_line_pull_sends_the_lines_calendar_months_until_the_planner_edits_the_flight_box(dock):
    dock.evaluate("defineLine('L1', { months: 2, flight_days: 38 })")
    open_for(dock, "L1", months=2, flightDays=38)                                                 # Nov 24 - Dec 31: 38 days, two calendar months (the box would say one)
    dock.evaluate("OWNER.setPlace('Marseille')")
    assert dock.input_value("#cz-flight") == "38" and dock.get_attribute('#cz-unit [data-unit="days"]', "aria-pressed") == "true"
    sent = pull(dock)
    assert sent["months"] == 2 and sent["flight_days"] == 38
    assert dock.evaluate("HOST.saved[0][1].line.months") == 2 and dock.evaluate("HOST.saved[0][1].request.months") == 2        # never a length it was not computed for
    assert dock.query_selector("#cz-result .cz-stale") is None                                    # (and a fresh result reads fresh)
    dock.fill("#cz-flight", "45")                                                                  # the planner's own length now
    assert dock.evaluate("Constanza._state.monthsOverride") is None
    sent = pull(dock)
    assert sent["months"] == 1 and sent["flight_days"] == 45                                      # round(45 / 30.4375)


@pytest.mark.parametrize("how", ["stepper", "unit", "typing"])
def test_each_way_of_editing_the_flight_lets_go_of_the_lines_months(dock, how):
    dock.evaluate("defineLine('L1')")
    open_for(dock, "L1", months=2, flightDays=38)
    assert dock.evaluate("Constanza._state.monthsOverride") == 2
    if how == "stepper":
        dock.click("#cz-flight-inc")
    elif how == "unit":
        dock.click('#cz-unit [data-unit="months"]')
    else:
        dock.fill("#cz-flight", "40")
    assert dock.evaluate("Constanza._state.monthsOverride") is None


PROPOSAL_DATES = """() => { state.parsed = {}; window._effectiveStartDate = () => '2026-11-24'; window._effectiveEndDate = () => '2026-12-31';
    window._mbParseDate = (s) => new Date(s + 'T00:00:00Z'); window._mbMonthsBetween = (a, b) => [a, b]; }"""        # Nov 24 - Dec 31: 38 days that touch two calendar months


def test_using_the_proposals_dates_gives_the_line_its_calendar_months_back_after_any_edit_of_the_box(dock):
    """The chip's days ARE the line's flight, so a method that asks for the line's months gets them again (not the box's round(38 / 30.4) = 1) - whether the chip was
    already the chosen one or the planner had typed another length since - and a fresh result does not turn stale for it."""
    dock.evaluate(PROPOSAL_DATES)
    dock.evaluate("defineLine('L1', { months: 2, flight_days: 38 })")
    open_for(dock, "L1", months=2, flightDays=38)
    dock.evaluate("OWNER.setPlace('Marseille')")
    assert dock.get_attribute("#cz-use-dates", "aria-pressed") == "true" and pull(dock)["months"] == 2
    dock.click("#cz-use-dates")                                                                    # already the chosen one: a no-op, for the request and for the banner
    assert dock.evaluate("Constanza._state.monthsOverride") == 2 and dock.query_selector("#cz-result .cz-stale") is None
    dock.fill("#cz-flight", "45")                                                                  # the planner's own length ...
    assert dock.evaluate("Constanza._state.monthsOverride") is None and pull(dock)["months"] == 1
    dock.click("#cz-use-dates")                                                                    # ... and back to the proposal's dates
    assert dock.input_value("#cz-flight") == "38" and dock.get_attribute("#cz-use-dates", "aria-pressed") == "true"
    assert dock.evaluate("Constanza._state.monthsOverride") == 2
    sent = pull(dock)
    assert sent["months"] == 2 and sent["flight_days"] == 38
    assert dock.evaluate("HOST.saved[HOST.saved.length - 1][1].request.months") == 2 and dock.query_selector("#cz-result .cz-stale") is None


def test_the_proposals_dates_chip_hands_months_only_to_a_method_that_asks_for_them_and_only_for_a_line(dock):
    dock.evaluate(PROPOSAL_DATES)
    dock.evaluate("defineLine('L1', { product_name: 'Plain SKU', method: 'probe_plain', months: 2, flight_days: 38 })")
    open_for(dock, "L1", product=PLAIN, months=2, flightDays=38)
    dock.fill("#cz-flight", "45")
    dock.click("#cz-use-dates")
    assert dock.evaluate("Constanza._state.monthsOverride") is None                               # a method without caps.lineMonths: the box rules, as before
    dock.evaluate("Constanza.close(); Constanza.open()")                                          # the dock on its own has no line, so no months of one
    dock.fill("#cz-flight", "45")
    dock.click("#cz-use-dates")
    assert dock.evaluate("Constanza._state.monthsOverride") is None and dock.input_value("#cz-flight") == "38"


def test_the_lines_months_are_a_flag_of_the_method_a_method_without_it_keeps_the_boxes_figure(dock):
    dock.evaluate("defineLine('L1', { product_name: 'Plain SKU', method: 'probe_plain' })")
    open_for(dock, "L1", product=PLAIN, months=2, flightDays=38)
    assert dock.evaluate("Constanza._state.monthsOverride") == 2                                    # remembered ...
    sent = pull(dock)
    assert sent["months"] == 1 and sent["flight_days"] == 38                                       # ... but only a method that asks for it gets it: round(38 / 30.4375), as before
    choose_product(dock, "owner sku")                                                              # the same box, a method that does: its months again
    dock.evaluate("OWNER.setPlace('Marseille')")
    assert pull(dock)["months"] == 2


def test_opening_the_dock_without_a_line_has_no_months_of_a_line(dock):
    dock.evaluate("defineLine('L1')")
    open_for(dock, "L1", months=2, flightDays=38)
    dock.evaluate("Constanza.close(); Constanza.open()")
    assert dock.evaluate("Constanza._state.monthsOverride") is None


# ================================================================================================================== the Step 05 card and the helpers the page calls

CARD_JS = """([data, opts]) => {
    const el = document.createElement('div'); document.body.appendChild(el); window.__card = el; window.__calls = [];
    const o = Object.assign({ getText: () => 'x', onGeoText: () => {}, onApply: (...a) => __calls.push(['apply', ...a]), onRefine: () => __calls.push(['refine']),
                              onRecalc: () => __calls.push(['recalc']) }, opts);
    Constanza.renderCard(el, data, o);
    return el.innerHTML; }"""
LINE = {"id": "L1", "product_name": OWNER, "method": "probe_owner", "label": "Owner line", "months": 2, "flight_days": 38, "has_dates": True, "geo_text": "Los Angeles",
        "target_text": "Adults 25-54", "budget": 2000, "cpm": 12, "hispanic": None}


def card(page, data=None, **opts):
    snap = native(saved_estimate)
    data = data if data is not None else snap["result"]
    opts = {"line": LINE, "stale": [], "fromSnapshot": True, "snapshot": snap, **opts}
    page.evaluate(CARD_JS, [data, opts])
    return page.evaluate("__card.innerHTML")


def card_text(page, selector):
    return page.evaluate("(s) => [...__card.querySelectorAll(s)].map((e) => e.textContent.replace(/\\s+/g, ' ').trim())", selector)


AUDIENCE = {"imps_low": 1000, "imps_mid": 2000, "imps_high": 3000, "audience_people": 500, "est_uniques": 400, "underlying_population": 90_000, "reliability_floor_population": 10_000,
            "below_reliability_threshold": False, "market_population_adults": True, "confidence_tier": "audience_modeled_single_cut", "notes": [], "by_area": [], "hispanic": None}


@pytest.mark.parametrize("url,is_link", [("https://example.org/report", True), ("HTTP://EXAMPLE.ORG/report", True), ("javascript:window.__x=1", False), (" https://example.org", False),
                                         ("//example.org/report", False), ("data:text/html,<b>x</b>", False), ("ftp://example.org", False), ("", False), (None, False)])
def test_a_source_in_the_audience_box_is_a_link_only_when_it_is_a_web_address(dock, url, is_link):
    """A saved result is data anybody can have edited: whatever its source_url says, only http(s) becomes a link; the source's name is shown either way."""
    interest = {"label": "Soccer fans", "input_text": "soccer", "supported": True, "rate": 0.2, "source": "Plant Source", "source_url": url, "source_date": "2025", "confidence": "high"}
    data = {**estimate_answer({"product_name": "Roku", "months": 1}), "audience": {**AUDIENCE, "interests": [interest]}}
    card(dock, data, fromSnapshot=False, snapshot=None)
    links = dock.evaluate("[...__card.querySelectorAll('.cz-audience-box a')].map((a) => [a.getAttribute('href'), a.textContent, a.rel, a.target])")
    assert links == ([[url, "Plant Source", "noopener noreferrer", "_blank"]] if is_link else [])
    assert "Plant Source" in text_of_card(dock) and dock.evaluate("window.__x === undefined")


def text_of_card(page):
    return page.evaluate("__card.textContent.replace(/\\s+/g, ' ')")


@pytest.mark.parametrize("mid", [None, "abc", 0, -5])
def test_a_result_with_no_usable_monthly_figure_offers_no_insert_on_the_card_or_in_the_dock(dock, mid):
    """Insert writes the monthly mid into Avails: a saved estimate that was edited into one with none (or none above zero) must not write it over what the line holds."""
    snap = native(saved_estimate)
    data = {**snap["result"], "imps_mid": mid}
    assert "cz-apply" not in card(dock, data) and dock.evaluate("__card.querySelector('.cz-apply')") is None
    assert "Apply" not in text_of_card(dock) and "Insert" not in text_of_card(dock)
    assert "cz-apply" in card(dock, {**snap["result"], "imps_mid": 5_500_000})                     # (the same card with a real figure has it)
    dock.evaluate("defineLine('L1')")
    keep(dock, "L1", native(saved_estimate, result=data))
    open_for(dock, "L1")
    assert dock.is_visible("#cz-result .po-headline") and dock.query_selector("#cz-result .cz-insert") is None


def test_a_fresh_saved_estimate_draws_with_the_methods_labels_insert_recalculate_and_change_in_constanza(dock):
    card(dock)
    assert card_text(dock, ".constanza-range-table th") == ["", "Conservative", "Mid - default", "Upper"]
    assert card_text(dock, ".cz-badges .po-badge") == ["Probe live"]
    assert card_text(dock, ".po-card") == ["Probe <i>name</i> stale=0 saved=true line=L1"]       # the method's part, shown as text; it is told the line and that it is a saved one
    assert card_text(dock, ".cz-apply") == ["Insert 5,500,000 / month"] and card_text(dock, ".cz-recalc") == ["Recalculate"] and card_text(dock, ".cz-refine") == ["Change in Constanza"]
    classes = dock.evaluate("[...__card.querySelectorAll('.cz-card-actions button')].map((b) => b.className)")
    assert classes == ["btn-primary cz-apply", "btn-secondary cz-recalc", "cz-link cz-refine"]
    assert card_text(dock, ".cz-recalc-why") == [] and card_text(dock, ".cz-stale") == []
    dock.evaluate("__card.querySelector('.cz-apply').click(); __card.querySelector('.cz-recalc').click(); __card.querySelector('.cz-refine').click()")
    calls = dock.evaluate("__calls")
    assert [c[0] for c in calls] == ["apply", "recalc", "refine"]
    assert calls[0][1] == 5_500_000 and calls[0][2] == 1_000_000 and calls[0][3]["method"] == "probe_geo"
    assert calls[0][4]["saved_at"] == "2026-11-01T12:00:00+00:00"                                 # a saved one: the page gets the snapshot back, to keep on the line


def test_a_stale_card_says_why_withdraws_insert_and_makes_recalculate_the_main_button(dock):
    card(dock, stale=["the budget changed", "the product changed"])
    assert card_text(dock, ".cz-stale") == ["Inputs changed — recalculate: the budget changed, the product changed."]
    assert card_text(dock, ".cz-apply") == [] and card_text(dock, ".cz-recalc") == ["Recalculate"]
    assert dock.evaluate("__card.querySelector('.cz-recalc').className") == "btn-primary cz-recalc"
    assert "stale=2" in card_text(dock, ".po-card")[0]                                            # (the method hears the reasons too)


def test_recalculate_is_offered_only_when_there_is_a_request_to_repeat_and_otherwise_the_dock_is_the_way_forward(dock):
    card(dock, snapshot=None)                                                                      # canAutoPull(line, null): nothing to repeat
    assert card_text(dock, ".cz-recalc") == [] and card_text(dock, ".cz-refine") == ["Estimate in Constanza →"]
    assert dock.evaluate("__card.querySelector('.cz-refine').className") == "btn-primary cz-refine"
    # the page need not hand the snapshot over: the card asks the page for the line's own
    keep(dock, "L1", native(saved_estimate))
    dock.evaluate("defineLine('L1')")
    card(dock, snapshot=None)
    assert card_text(dock, ".cz-recalc") == ["Recalculate"]
    card(dock, onRecalc=None, snapshot=native(saved_estimate))                                     # the page has no Recalculate of its own: no button
    assert card_text(dock, ".cz-recalc") == []


def test_a_method_that_is_not_connected_has_its_recalculate_off_with_the_reason_beside_it(dock):
    dock.evaluate("PCFG.connected = false")
    card(dock)
    assert dock.evaluate("__card.querySelector('.cz-recalc').getAttribute('aria-disabled')") == "true"
    why = dock.evaluate("__card.querySelector('.cz-recalc-why').textContent")
    assert why == "The probe isn't connected yet." and dock.evaluate("__card.querySelector('.cz-recalc').getAttribute('aria-describedby') === __card.querySelector('.cz-recalc-why').id")
    dock.evaluate("__card.querySelector('.cz-recalc').click()")
    assert dock.evaluate("__calls") == []                                                          # pressing it does nothing
    dock.evaluate("PCFG.connected = null")                                                         # not known yet: the card does not claim it is off
    card(dock)
    assert dock.evaluate("__card.querySelector('.cz-recalc').hasAttribute('aria-disabled')") is False


def test_a_data_products_card_is_what_it_always_was(dock):
    data = estimate_answer({"product_name": "Roku", "months": 2})
    data["method"] = "platform"
    card(dock, data=data, line=None, fromSnapshot=False, snapshot=None)
    assert card_text(dock, ".constanza-range-table th") == ["", "Low", "Mid", "High"]
    assert dock.evaluate("[...__card.querySelectorAll('.cz-card-actions button')].map((b) => b.className + '|' + b.textContent)") == ["btn-secondary cz-apply|Apply monthly mid-case →", "cz-link cz-refine|Refine in Constanza"]
    dock.evaluate("__card.querySelector('.cz-apply').click()")
    assert len(dock.evaluate("__calls")[0]) == 4                                                   # (apply, mid, uniques, info): no snapshot argument


def test_a_card_that_cannot_read_what_it_was_given_says_so_shows_the_methods_empty_state_and_throws_nothing(dock):
    hostile = '<img src=x onerror="window.__pwn = 1">'
    broken = [{"method": "probe_owner", "product_name": OWNER}, {"method": "probe_owner", "product_name": OWNER, "probe_owner": None},        # the method's own code throws on these
              {"method": "probe_owner", "months": "three", "imps_mid": "lots", "probe_owner": None, "notes": "x"},
              {"method": "probe_owner", "probe_owner": {"name": "n"}, "unmatched_geo_tokens": "ab"},                                           # ... and the dock's, reading a field of the wrong shape
              {"method": "probe_owner", "probe_owner": {"name": "n"}, "audience": {"notes": "x"}}]
    for data in broken:
        html = card(dock, data=data)
        assert "The saved estimate couldn’t be read" in html and 'class="po-empty"' in html and "Estimate in Constanza →" in html
        assert card_text(dock, ".cz-apply") == []
    dock.evaluate("__card.querySelector('.cz-refine').click()")
    assert dock.evaluate("__calls") == [["refine"]]
    said = [line for line in dock.console_errors if "cardHtml" in line]                          # type: ignore[attr-defined]
    assert len(said) == 1                                                                         # logged once, however often the card is drawn again
    assert len([line for line in dock.console_errors if "could not be drawn on a card" in line]) == 1
    # a number that is a string and a note that is not a list are tolerated by the dock's own reading; markup in any string is shown, never run
    good = native(saved_estimate)["result"]
    good["probe_owner"]["name"] = hostile
    good["notes"] = "not a list"
    good["months"] = "2"
    html = card(dock, data=good)
    assert "&lt;img" in html and "<img" not in html and dock.evaluate("window.__pwn") is None
    assert card_text(dock, ".po-card") != [] and card_text(dock, ".cz-apply") != []


def test_a_method_that_throws_while_a_card_asks_it_something_never_breaks_the_card(dock):
    dock.evaluate("""() => { const p = Constanza._plugin('probe_owner'); ['choicesChanged', 'staleReasons', 'canAutoPull', 'connection', 'applyLabel'].forEach((k) => { p[k] = () => { throw new Error(k + ' trouble'); }; }); }""")
    html = card(dock)
    assert "The saved estimate couldn’t be read" in html                                          # applyLabel is part of drawing it: the card says so instead of half drawing
    dock.evaluate("""() => { const p = Constanza._plugin('probe_owner'); p.applyLabel = () => 'Insert'; }""")
    html = card(dock)
    assert card_text(dock, ".cz-apply") == ["Insert"] and card_text(dock, ".cz-recalc") == []      # canAutoPull threw: no Recalculate, never a half-guess
    assert dock.evaluate("Constanza.snapshotStale(" + json.dumps(native(saved_estimate)) + ", " + json.dumps(LINE) + ")") == []     # a throwing method adds no reason


def test_the_badge_the_summary_and_the_result_of_a_method_that_throws_are_guarded_too(dock):
    dock.evaluate("defineLine('L1')")
    dock.evaluate("() => { Constanza._plugin('probe_owner').badgeHtml = () => { throw new Error('badge trouble'); }; }")
    open_for(dock, "L1")
    dock.evaluate("OWNER.setPlace('Marseille')")
    pull(dock)
    assert "couldn’t be read" in text(dock, "#cz-result .cz-error") and dock.evaluate("Constanza._state.result") is None
    dock.evaluate("() => { Constanza.registerMethod('probe_owner', ownerFactory); }")
    open_for(dock, "L1")
    dock.evaluate("OWNER.setPlace('Marseille')")
    pull(dock)
    dock.evaluate("() => { Constanza._plugin('probe_owner').summaryLines = () => { throw new Error('summary trouble'); }; }")
    assert "Geography: Marseille" in dock.evaluate("Constanza._pure.summaryText(Constanza._state.result)")        # the lines the method would add are left out; the rest is still there
    assert "badge trouble" in " ".join(dock.console_errors) and "summary trouble" in " ".join(dock.console_errors)    # type: ignore[attr-defined]


# ------------------------------------------------------------------------------------------------------------------ what the page asks of the dock

def test_the_buttons_of_a_block_depend_on_what_the_method_is_and_whether_it_has_an_estimate(dock):
    out = dock.evaluate("""() => ({
        data: Constanza.blockButtons('platform', { id: 'L1' }, null), plain: Constanza.blockButtons('probe_plain', { id: 'L1' }, null),
        none: Constanza.blockButtons('probe_owner', { id: 'L1' }, null), has: Constanza.blockButtons('probe_owner', { id: 'L1' }, { result: { x: 1 } }),
        budgetOnly: Constanza.blockButtons('probe_owner', { id: 'L1' }, { result: null }), missing: Constanza.blockButtons('ghost_method', { id: 'L1' }, null),
        odd: Constanza.blockButtons('probe_owner', { id: 'a"b<c' }, null) })""")
    classic = ('<button type="button" class="btn-secondary constanza-autocalc-btn" data-lid="L1">%s</button>'
               '<button type="button" class="cz-link constanza-line-dock-btn" data-lid="L1">Open in Constanza</button>')
    assert re.sub(r"<svg.*?</svg>", "ICON", out["data"], flags=re.S) == classic % "ICONAuto-calculate avails"      # a data product: the two buttons it always had (the page's markup)
    assert out["plain"] == out["data"]                                                                              # a method that pulls from the proposal's geography: the same
    assert out["none"] == '<button type="button" class="btn-primary constanza-line-dock-btn" data-lid="L1">Estimate in Constanza →</button>'
    assert out["has"] == "" and out["missing"] == ""                                                                # an estimate: the card carries its own buttons; a method that did not load: none
    assert out["budgetOnly"] == out["none"]                                                                         # an estimate with no result is not one
    assert 'data-lid="a_b_c"' in out["odd"]                                                                         # an id is written as the page writes it (the page finds the block again with it)


@pytest.mark.parametrize("change,reasons", [
    ({}, []), ({"product_name": "Other"}, ["the product changed"]), ({"months": 3}, ["the flight length changed"]), ({"geo_text": "Phoenix"}, ["the proposal’s geography changed"]),
    ({"target_text": "Everyone"}, ["the line’s target changed"]), ({"budget": 2000.005}, []), ({"budget": 2000.5}, ["the budget changed"]), ({"budget": 0}, ["the budget changed"]),
    ({"cpm": 12.005}, []), ({"cpm": 12.5}, ["the CPM changed"]), ({"cpm": None}, []),
    ({"product_name": "Other", "budget": 5}, ["the product changed", "the budget changed"])])
def test_a_saved_estimate_is_stale_by_the_same_generic_rules_for_every_method(dock, change, reasons):
    line = {**LINE, **change}
    assert dock.evaluate("([s, l]) => Constanza.snapshotStale(s, l)", [native(saved_estimate), line]) == reasons


def test_the_staleness_rules_tolerate_a_saved_estimate_or_a_line_of_any_shape_and_count_the_methods_own_checks(dock):
    for snap, line in [(None, LINE), (native(saved_estimate), None), ({}, LINE), ({"line": "x", "method": "nope"}, LINE), ({"method": "probe_owner", "line": None}, LINE)]:
        assert isinstance(dock.evaluate("([s, l]) => Constanza.snapshotStale(s, l)", [snap, line]), list)
    dock.evaluate("defineLine('L1')")
    snap = native(saved_estimate)
    dock.evaluate("OWNER = undefined")                                                           # (only a registered method has choices of its own: restore its workspace and edit it)
    dock.evaluate("Constanza.registerMethod('probe_owner', ownerFactory)")
    keep(dock, "L1", snap)
    open_for(dock, "L1")
    assert dock.evaluate("([s, l]) => Constanza.snapshotStale(s, l)", [snap, LINE]) == []
    dock.evaluate("OWNER.toggle('z')")
    assert dock.evaluate("([s, l]) => Constanza.snapshotStale(s, l)", [snap, LINE]) == ["the audience in Constanza changed"]
    dock.evaluate("PCFG.reason = 'extra reason'")
    assert dock.evaluate("([s, l]) => Constanza.snapshotStale(s, l)", [snap, {**LINE, "budget": 1}]) == ["the budget changed", "extra reason", "the audience in Constanza changed"]


def test_a_snapshot_is_made_only_for_a_method_that_saves_and_records_the_length_it_was_computed_for(dock):
    body = {"product_name": OWNER, "months": 3, "probe_owner": {"place": "Nice"}}
    data = estimate_answer({"product_name": OWNER, "months": 3, "probe_owner": {"place": "Nice", "budget": 0}})
    snap = dock.evaluate("([b, d, l]) => Constanza.makeSnapshot('probe_owner', b, d, l)", [body, data, {**LINE, "months": 2, "budget": -5, "cpm": None}])
    assert snap["line"] == {"product_name": OWNER, "method": "probe_owner", "months": 3, "geo_text": "Los Angeles", "target_text": "Adults 25-54", "budget": 0, "cpm": 12}
    assert snap["request"] == body and snap["result"] == data and snap["v"] == 1
    assert dock.evaluate("([b, d, l]) => [Constanza.makeSnapshot('probe_plain', b, d, l), Constanza.makeSnapshot('platform', b, d, l), Constanza.makeSnapshot('nope', b, d, l), "
                         "Constanza.makeSnapshot('probe_owner', null, d, l), Constanza.makeSnapshot('probe_owner', b, 'x', l)]", [body, data, LINE]) == [None] * 5
    bare = dock.evaluate("([b, d]) => Constanza.makeSnapshot('probe_owner', b, d, null)", [body, data])
    assert bare["line"]["geo_text"] == "" and bare["line"]["budget"] == 0 and bare["line"]["product_name"] == OWNER      # (a line with no facts: the request's product, nothing invented)


def test_an_older_shape_is_read_through_the_method_in_memory_only_and_a_bad_reader_is_ignored(dock):
    entry = {"old_probe_forecast": {"value": 1, "at": "2026-10-01T00:00:00+00:00", "budget": 500}}
    snap = dock.evaluate("([e, l]) => Constanza.adoptLegacy('probe_owner', e, l)", [entry, LINE])
    assert snap["legacy"] is True and snap["method"] == "probe_owner" and snap["line"]["budget"] == 500 and snap["saved_at"] == "2026-10-01T00:00:00+00:00"
    assert entry == {"old_probe_forecast": {"value": 1, "at": "2026-10-01T00:00:00+00:00", "budget": 500}}                       # not written back
    assert dock.evaluate("([e, l]) => [Constanza.adoptLegacy('probe_owner', {}, l), Constanza.adoptLegacy('probe_owner', null, l), Constanza.adoptLegacy('probe_owner', {old_probe_forecast: {}}, l), "
                         "Constanza.adoptLegacy('probe_plain', e, l), Constanza.adoptLegacy('nope', e, l)]", [entry, LINE]) == [None] * 5
    dock.evaluate("PCFG.adoptThrows = true")
    assert dock.evaluate("([e, l]) => Constanza.adoptLegacy('probe_owner', e, l)", [entry, LINE]) is None
    assert any("legacy.adopt" in line and "boom" in line for line in dock.console_errors)                                # type: ignore[attr-defined]
    dock.evaluate("PCFG.adoptThrows = false; Constanza._plugin('probe_owner').legacy.adopt = () => ({ v: 1, method: 'probe_plain' })")
    assert dock.evaluate("([e, l]) => Constanza.adoptLegacy('probe_owner', e, l)", [entry, LINE]) is None              # (a reader that hands back another method's shape)


def test_what_an_insert_files_as_the_geography_is_the_methods_own_else_the_data_paths(dock):
    own = dock.evaluate("Constanza.geoInfo(" + json.dumps(native(saved_estimate)["result"]) + ")")
    assert own == {"method": "probe_geo", "dmas": ["Nice"], "unmatched_tokens": [], "win_rate": None, "hispanic_share": None}
    data = {"method": "platform", "geo_method": "exact_dma", "by_area": [{"dma": "Dallas"}, {"dma": "Dallas"}, {"dma": "Houston"}, {}], "unmatched_geo_tokens": ["x"],
            "win_rate": {"mid": 0.15}, "audience": {"hispanic": {"mode": "cut", "share": 0.4}}}
    assert dock.evaluate("(d) => Constanza.geoInfo(d)", data) == {"method": "exact_dma", "dmas": ["Dallas", "Houston"], "unmatched_tokens": ["x"], "win_rate": 0.15, "hispanic_share": 0.4}
    dock.evaluate("() => { Constanza._plugin('probe_owner').geoInfo = () => 'junk'; }")                                   # junk or a throw from the method: the default
    assert dock.evaluate("(d) => Constanza.geoInfo({ ...d, method: 'probe_owner' })", data)["dmas"] == ["Dallas", "Houston"]
    dock.evaluate("() => { Constanza._plugin('probe_owner').geoInfo = () => { throw new Error('x'); }; }")
    assert dock.evaluate("(d) => Constanza.geoInfo({ ...d, method: 'probe_owner' })", data)["method"] == "exact_dma"
    assert dock.evaluate("Constanza.geoInfo(null)")["dmas"] == []


def test_what_a_card_adds_to_its_request_is_the_methods_block_with_the_lines_facts_and_no_geography_text_when_the_method_owns_it(dock):
    snap = native(saved_estimate)
    out = dock.evaluate("([s, l]) => Constanza.cardRequest('probe_owner', 'L1', l, s)", [snap, {**LINE, "budget": 3000}])
    assert out == {"probe_owner": {"place": "Nice", "picks": ["a", "b"], "budget": 3000}, "geo_text": "", "geo_type_hint": None}
    assert dock.evaluate("([s, l]) => Constanza.cardRequest('probe_plain', 'L1', l, s)", [snap, LINE]) == {}                # a method that asks about the geography alone
    assert dock.evaluate("([s, l]) => [Constanza.cardRequest('platform', 'L1', l, s), Constanza.cardRequest('nope', 'L1', l, s)]", [snap, LINE]) == [{}, {}]
    dock.evaluate("defineLine('L1')")
    open_for(dock, "L1")
    dock.evaluate("OWNER.toggle('z'); OWNER.setPlace('Elsewhere')")                                                         # unpulled edits in the dock's workspace for that line: what is sent
    assert dock.evaluate("([s, l]) => Constanza.cardRequest('probe_owner', 'L1', l, s)", [snap, LINE])["probe_owner"] == {"place": "Elsewhere", "picks": ["a", "b", "z"] if False else ["z"], "budget": 2000}


def test_what_a_card_says_before_any_estimate_is_the_methods_empty_state_and_a_method_with_none_says_nothing(dock):
    dock.evaluate("() => { window.__e = document.createElement('div'); document.body.appendChild(window.__e); }")
    assert dock.evaluate("([l]) => [Constanza.renderEmpty(__e, 'probe_owner', l, null), __e.style.display, __e.innerHTML]", [LINE]) == [True, "block", '<p class="po-empty">Probe needs a place for Owner line.</p>']
    assert dock.evaluate("([l]) => [Constanza.renderEmpty(__e, 'probe_owner', l, { result: null }), __e.innerHTML]", [LINE])[1].endswith("(a budget-only estimate).</p>")
    assert dock.evaluate("([l]) => [Constanza.renderEmpty(__e, 'probe_plain', l, null), __e.style.display, __e.innerHTML]", [LINE]) == [False, "none", ""]
    assert dock.evaluate("([l]) => [Constanza.renderEmpty(__e, 'platform', l, null), Constanza.renderEmpty(__e, 'nope', l, null)]", [LINE]) == [False, False]
    dock.evaluate("() => { Constanza._plugin('probe_owner').cardEmptyHtml = () => { throw new Error('empty trouble'); }; }")
    assert dock.evaluate("([l]) => Constanza.renderEmpty(__e, 'probe_owner', l, null)", [LINE]) is False                  # a method that throws says nothing rather than break the page


# ------------------------------------------------------------------------------------------------------------------ a page with no proposal behind the dock

def test_what_the_dock_hands_a_method_on_a_page_with_no_host_is_the_standalone_answer(dock):
    dock.evaluate("window.constanzaHost = undefined")
    ctx = dock.evaluate("({ line: OWNER.ctx.line(), snap: OWNER.ctx.snapshot('L1'), geo: OWNER.ctx.proposalGeo(), s: Constanza._state.lineId })")
    assert ctx == {"line": None, "snap": None, "geo": "", "s": None}
    for host in ["{}", "{ lineFacts: () => { throw new Error('x'); }, snapshot: () => 7 }", "{ lineFacts: () => undefined, snapshot: () => ({ v: 2, method: 'probe_owner' }) }"]:
        dock.evaluate(f"window.constanzaHost = {host}")
        assert dock.evaluate("[OWNER.ctx.line(), OWNER.ctx.snapshot('L1'), OWNER.ctx.snapshot(null), OWNER.ctx.line()]") == [None] * 4


def test_the_line_a_method_sees_is_the_one_the_dock_is_open_for_read_fresh_and_null_once_the_line_is_gone(dock):
    dock.evaluate("defineLine('L1', { budget: 2000 })")
    open_for(dock, "L1")
    assert dock.evaluate("OWNER.ctx.line().budget") == 2000 and dock.evaluate("OWNER.ctx.proposalGeo()") == "Los Angeles"
    dock.evaluate("HOST.lines.L1.budget = 2500; HOST.lines.L1.geo_text = 'Phoenix'")
    assert dock.evaluate("OWNER.ctx.line().budget") == 2500 and dock.evaluate("OWNER.ctx.proposalGeo()") == "Phoenix"      # fresh on every call
    dock.evaluate("dropLine('L1')")
    assert dock.evaluate("OWNER.ctx.line()") is None and dock.evaluate("OWNER.ctx.proposalGeo()") == ""                  # a line that is no longer in the option: standalone
    dock.evaluate("defineLine('L2')")
    dock.evaluate("Constanza.open({ lineId: 'L2', product: 'Owner SKU', geo: 'x', months: 2 })")
    dock.evaluate("OWNER.setPlace('Nice')")
    sent = pull(dock)
    assert "budget" in sent["probe_owner"]
    dock.evaluate("dropLine('L2')")
    dock.evaluate("OWNER.setPlace('Cannes')")
    assert "budget" not in dock.evaluate("Constanza._state && OWNER.ctx.requestBody().probe_owner")                       # no line, no line facts in the request


# ------------------------------------------------------------------------------------------------------------------ the host names no method

def _code_only(src: str) -> str:
    """The JavaScript with its comments taken out (a comment may say 'TikTok, Meta' to explain; code may not branch on them)."""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"(^|[^:\"'`\\])//[^\n]*", r"\1", src)


def test_the_host_does_not_name_a_method_in_code_and_never_branches_on_one():
    code = _code_only(CZ_JS)
    assert not re.search(r"tiktok", code, re.I), re.findall(r".{30}tiktok.{30}", code, re.I)[:5]
    assert not re.search(r"""["'`]meta["'`]|\b(?:data|plugins|result)\.meta\b|\bmeta_(?:audience|forecast)""", code), "a method's key or block in code position"
    # the only method keys it knows are the two it is built on (data methods: no plugin may take their names); no comparison with any other word
    untyped = re.sub(r'typeof\s+[\w.\[\]]+\s*[!=]==\s*"[^"]*"', "", code)                                                 # (`typeof x.method === "string"` compares a type, not a method)
    compared = set(re.findall(r'\bmethod\s*[!=]==\s*"([^"]*)"', untyped)) | set(re.findall(r'"([^"]*)"\s*[!=]==\s*[\w.]*method\b', untyped))
    assert compared <= {"platform", "generic"}, compared
    assert "window.constanzaHost" in code and "Object.create(null)" in code                                              # read lazily; the plugin map has no prototype


# ------------------------------------------------------------------------------------------------------------------ the catalog and the line

def test_a_line_opened_before_the_product_list_has_arrived_gets_its_product_and_its_saved_estimate_when_it_does(dock):
    dock.evaluate("defineLine('L1')")
    keep(dock, "L1", native(saved_estimate))
    dock.evaluate("window.__catalog = state.catalog; state.catalog = null; Constanza.close()")
    clear_log(dock)
    open_for(dock, "L1")
    assert dock.evaluate("Constanza._state.lineId") == "L1" and ["onLine", "L1", "L1"] in log(dock)                       # the line is known at once ...
    assert dock.query_selector("#cz-result .po-headline") is None                                                         # ... the product (and what hangs on it) waits for the list
    dock.evaluate("state.catalog = window.__catalog")
    dock.wait_for_selector("#cz-result .po-headline", timeout=3000)
    kinds = [e[0] for e in log(dock)]
    assert kinds.index("onLine") < kinds.index("onSelect") < kinds.index("restore")
    assert dock.evaluate("Constanza._state.product") == OWNER and dock.input_value("#cz-flight") == "38" and dock.query_selector("#cz-result .cz-stale") is None


def test_a_line_whose_method_did_not_load_says_so_instead_of_telling_the_planner_to_turn_it_on(dock):
    dock.evaluate("defineLine('L9', { product_name: 'Ghost SKU', method: 'ghost_method' })")
    open_for(dock, "L9", product=GHOST)
    note = text(dock, "#cz-product-note")
    assert "Ghost SKU" in note and "didn’t load" in note and "turn it on" not in note
    dock.evaluate("() => { state.catalog.products_by_family.Probes.push(Object.assign(item('Off SKU', 'platform'), { forecast: { enabled: false, method: 'platform' } })); }")
    open_for(dock, "L9", product="Off SKU")
    assert "has no Constanza data enabled" in text(dock, "#cz-product-note")


def test_a_method_that_gives_no_labels_still_gets_a_group_a_note_and_no_badge(dock):
    dock.evaluate("""() => {
        state.catalog.products_by_family.Probes.push(item('Bare SKU', 'probe_bare'));
        Constanza.registerMethod('probe_bare', () => (%s)); }""" % GOOD)
    names, groups = picker_names(dock)
    assert "Bare SKU" in names and groups[-1] == "probe_bare"                                      # the group is named after the key, in registration order after the others
    assert dock.evaluate("Constanza._state.products.filter((p) => p.name === 'Bare SKU')[0].channel") == "m:probe_bare"
    choose_product(dock, "bare sku")
    assert text(dock, "#cz-product-note") == "Live estimate" and text(dock, "#cz-go") == "Pull avails →"
    assert dock.evaluate("Constanza.badgesHtml({ method: 'probe_bare', geo_confidence: 'high' })") == '<span class="cz-badge">High geo confidence</span>'
    assert dock.evaluate("Constanza.geoInfo({ method: 'probe_bare', geo_method: 'x', by_area: [{ dma: 'A' }] })")["dmas"] == ["A"]


# ------------------------------------------------------------------------------------------------------------------ layouts

def overflow_report(page):
    """Anything in the dock wider than the dock, or any horizontal scroll (of the page or of the dock's own scrollers)."""
    return page.evaluate("""() => {
        const dock = document.getElementById('constanza-dock').getBoundingClientRect(), out = [];
        document.querySelectorAll('#constanza-dock *').forEach((el) => {
          if (!el.getClientRects().length || el.closest('.cz-sr, .aud-sr')) return;
          const r = el.getBoundingClientRect();
          if (r.width && (r.right > dock.right + 0.5 || r.left < dock.left - 0.5)) out.push([el.id || el.className || el.tagName, Math.round(r.left), Math.round(r.right)]);
        });
        const b = document.getElementById('cz-body'), f = document.getElementById('cz-form');
        return { out: out.slice(0, 8), page: document.documentElement.scrollWidth - document.documentElement.clientWidth, body: b.scrollWidth - b.clientWidth, form: f.scrollWidth - f.clientWidth }; }""")


@pytest.mark.parametrize("name,viewport,wide", [("narrow", {"width": 1280, "height": 900}, False), ("wide", {"width": 1440, "height": 900}, True), ("phone", {"width": 375, "height": 812}, False)])
def test_a_method_that_owns_its_geography_fits_the_dock_with_its_reason_its_banner_and_its_result_showing(browser, server, name, viewport, wide):
    world = World()
    page = new_page(browser, server, world, viewport=viewport)
    try:
        if wide:
            page.click("#cz-expand")
        page.evaluate("defineLine('L1', { budget: 3000 }); PCFG.connected = false")
        keep(page, "L1", native(saved_estimate))
        open_for(page, "L1")                                                                       # a saved estimate whose budget moved: the banner is up
        assert page.is_visible("#po-where") and page.is_hidden("#cz-sec-where")
        assert page.is_visible("#cz-go-reason") and page.evaluate("document.getElementById('cz-go').offsetHeight") >= 44          # a 44 px button (the dock may still be scaling in: offsetHeight ignores that), reason above it
        assert overflow_report(page) == {"out": [], "page": 0, "body": 0, "form": 0}, overflow_report(page)
        page.evaluate("PCFG.connected = true; OWNER.ctx.refreshGo()")
        page.evaluate("document.getElementById('cz-result').scrollIntoView()")
        assert page.is_visible("#cz-result .cz-stale") and overflow_report(page) == {"out": [], "page": 0, "body": 0, "form": 0}
        if viewport["width"] <= 720:
            page.wait_for_function("getComputedStyle(document.getElementById('constanza-dock')).transform === 'none' || getComputedStyle(document.getElementById('constanza-dock')).transform === 'matrix(1, 0, 0, 1, 0, 0)'")
            box = page.evaluate("(() => { const r = document.getElementById('constanza-dock').getBoundingClientRect(); return [r.left, r.right, innerWidth]; })()")
            assert box[0] == 0 and box[1] == box[2]                                                  # a phone: the dock IS the screen
    finally:
        page.context_.close()                                                                       # type: ignore[attr-defined]
    assert page.errors == []                                                                        # type: ignore[attr-defined]


def test_a_method_whose_panel_would_not_draw_takes_its_products_out_of_the_picker_with_it(dock):
    dock.evaluate("""() => {
        state.catalog.products_by_family.Probes.push(item('Fragile SKU', 'probe_fragile'));
        Constanza.registerMethod('probe_fragile', () => (%s)); }""" % GOOD)
    assert "Fragile SKU" in picker_names(dock)[0]
    # registered again with a panel that cannot be wired: it is taken out, and its products with it (never listed without a panel to ask them with)
    assert dock.evaluate("""() => Constanza.registerMethod('probe_fragile', () => Object.assign(%s, { bind: () => { throw new Error('no wiring'); } }))""" % GOOD) is False
    names, _ = picker_names(dock)
    assert "Fragile SKU" not in names and OWNER in names and dock.query_selector('[data-method="probe_fragile"]') is None
    assert "Fragile SKU" not in dock.evaluate("Constanza._state.products.map((p) => p.name)")                      # (and the dock's own list and its count agree)
    assert text(dock, "#cz-product-count") == "4 with avails data"


# ================================================================================================================== findings of the second pass

# ------------------------------------------------------------------------------------------------------------------ a connection answer reaches the page's cards

def test_a_connection_answer_redraws_the_pages_step_05_cards_as_well_as_the_pull_button(dock):
    """ctx.refreshGo() is what a method calls when its connection answer arrives. The pull button reads it again, and so must the cards on Step 05, which switch
    their Recalculate off while the method is not connected: the dock asks the page through onDirty(null) ("something every card reads changed")."""
    choose_product(dock, "owner sku")
    dock.evaluate("HOST.dirty.length = 0; PCFG.connected = false")
    dock.evaluate("OWNER.ctx.refreshGo()")
    assert dock.get_attribute("#cz-go", "aria-disabled") == "true"                                  # the dock's own update still happens
    assert dock.evaluate("HOST.dirty.length") == 1 and dock.evaluate("HOST.dirty[0][0]") is None   # ... and the page was told: no line, every card
    dock.evaluate("PCFG.connected = true; OWNER.ctx.refreshGo()")
    assert dock.get_attribute("#cz-go", "aria-disabled") == "false" and dock.evaluate("HOST.dirty.length") == 2


@pytest.mark.parametrize("host", ["undefined", "{}", "{ lineFacts: () => null, onDirty: 'not a function' }", "{ onDirty: () => { throw new Error('the page is busy'); } }"])
def test_a_connection_answer_still_updates_the_dock_when_the_page_has_no_way_to_redraw_or_fails_to(dock, host):
    choose_product(dock, "owner sku")
    dock.evaluate(f"window.constanzaHost = {host}; PCFG.connected = false")
    dock.evaluate("OWNER.ctx.refreshGo()")                                                           # (a throw would be a page error: the fixture checks for none)
    assert dock.get_attribute("#cz-go", "aria-disabled") == "true" and text(dock, "#cz-go-reason") == "The probe isn't connected yet."
    dock.evaluate("OWNER.ctx.refreshGo(); OWNER.ctx.refreshGo()")
    said = [line for line in dock.console_errors if "could not redraw" in line]                       # type: ignore[attr-defined]
    assert len(said) == (1 if "throw" in host else 0)                                                # a failing page is said once, however often the answer comes


# ------------------------------------------------------------------------------------------------------------------ table headers are legible

# the contrast (WCAG) of the text of every element `selector` finds, against what it is really drawn on (the opaque background above it)
CONTRAST_JS = r"""(selector) => {
    const parse = (c) => { const m = String(c).match(/rgba?\(([^)]+)\)/); const p = m[1].split(/[ ,\/]+/).filter(Boolean).map(Number); return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 }; };
    const lin = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    const lum = (c) => 0.2126 * lin(c.r) + 0.7152 * lin(c.g) + 0.0722 * lin(c.b);
    const over = (top, under) => ({ r: top.r * top.a + under.r * (1 - top.a), g: top.g * top.a + under.g * (1 - top.a), b: top.b * top.a + under.b * (1 - top.a), a: 1 });
    const backdrop = (el) => {
      const layers = [];
      for (let e = el; e; e = e.parentElement) { const c = parse(getComputedStyle(e).backgroundColor); if (c.a > 0) { layers.push(c); if (c.a === 1) break; } }
      return layers.reduceRight((under, c) => over(c, under), { r: 255, g: 255, b: 255, a: 1 });
    };
    return [...document.querySelectorAll(selector)].filter((el) => el.getClientRects().length).map((el) => {
      const bg = backdrop(el), fg = over(parse(getComputedStyle(el).color), bg);
      const hi = Math.max(lum(fg), lum(bg)), lo = Math.min(lum(fg), lum(bg));
      return { text: el.textContent.trim(), ratio: Math.round(((hi + 0.05) / (lo + 0.05)) * 100) / 100 };
    });
}"""


@pytest.mark.parametrize("width", [1000, 390])
def test_the_table_headers_of_the_step_05_card_and_of_the_docks_result_read_at_the_contrast_aa_asks_for(browser, server, width):
    """Low / Mid / High and Conservative / Mid · default / Upper measured 3.06:1 on the card's paper (faint grey, 10.5 px): under the 4.5:1 of WCAG AA."""
    world = World()
    page = new_page(browser, server, world, viewport={"width": width, "height": 900})
    try:
        page.evaluate("Constanza.close()")
        page.evaluate("""() => { document.querySelector('main').innerHTML = '<div class="constanza-block"><div class="constanza-result" id="c-method"></div></div>'
            + '<div class="constanza-block"><div class="constanza-result" id="c-data"></div></div>'; }""")
        snap = native(saved_estimate)
        data = estimate_answer({"product_name": "Roku", "months": 2})
        data["method"] = "platform"
        page.evaluate("""([snap, data, line]) => {
            const o = { getText: () => '', onGeoText() {}, onApply() {}, onRecalc() {}, onRefine() {} };
            Constanza.renderCard(document.getElementById('c-method'), snap.result, Object.assign({ line, fromSnapshot: true, snapshot: snap, stale: [] }, o));
            Constanza.renderCard(document.getElementById('c-data'), data, o); }""", [snap, data, LINE])
        seen = page.evaluate(CONTRAST_JS, ".constanza-range-table th")
        assert [t["text"] for t in seen if t["text"]] == ["Conservative", "Mid - default", "Upper", "Low", "Mid", "High"]
        assert [t for t in seen if t["ratio"] < 4.5] == [], seen
        # the dock's own result table (where the impressions are) has the same kind of header
        world.reply["estimate"] = {**data, "by_area": [{"area_label": "Los Angeles", "dma": "Los Angeles", "weight": 0.6, "imps_low": 1, "imps_mid": 3_300_000, "imps_high": 5},
                                                        {"area_label": "Orange County", "dma": "Los Angeles", "weight": 0.4, "imps_low": 1, "imps_mid": 2_200_000, "imps_high": 5}]}
        page.evaluate("Constanza.open()")
        choose_product(page, "roku")
        page.fill("#cz-geo", "Los Angeles")
        page.click("#cz-go")
        page.wait_for_selector("#cz-result .cz-table")
        heads = page.evaluate(CONTRAST_JS, "#cz-result .cz-table th")
        assert [t["text"] for t in heads] == ["Area", "Share", "Per month"] and [t for t in heads if t["ratio"] < 4.5] == [], heads
    finally:
        page.context_.close()                                                                       # type: ignore[attr-defined]
    assert page.errors == []                                                                        # type: ignore[attr-defined]


# ------------------------------------------------------------------------------------------------------------------ where the keyboard starts

def active(page):
    return page.evaluate("(() => { const a = document.activeElement; return a ? (a.id || a.tagName) : null; })()")


def test_opening_the_dock_on_a_method_that_owns_its_geography_puts_the_keyboard_in_its_where_card_not_on_the_hidden_box(dock):
    """The shared geography box is hidden for such a method (display: none cannot take focus), and open() used to focus it anyway: the keyboard stayed on the
    page behind the dock. The first control of the method's own Where card gets it, or the pull button when the method already has a place."""
    dock.evaluate("Constanza.close()")
    dock.evaluate("Constanza.open({ product: 'Owner SKU' })")                                       # the bubble: no line, nothing typed
    dock.wait_for_function("document.activeElement && document.activeElement.id === 'po-place'", timeout=2000)
    assert dock.is_hidden("#cz-geo")
    # a Step 05 line brings the proposal's geography text with it (which the old rule read as "there is a place": it went to the pull button)
    dock.evaluate("Constanza.close(); defineLine('L1')")
    open_for(dock, "L1")
    dock.wait_for_function("document.activeElement && document.activeElement.id === 'po-place'", timeout=2000)
    # ... but a line that already has its place (a saved estimate) goes straight to the pull
    keep(dock, "L1", native(saved_estimate))
    dock.evaluate("Constanza.close()")
    open_for(dock, "L1")
    dock.wait_for_function("document.activeElement && document.activeElement.id === 'cz-go'", timeout=2000)


def test_where_the_keyboard_starts_is_decided_from_the_docks_own_state_and_never_by_asking_the_method_a_moment_later(dock):
    """The focus is set a moment after open(): by then the planner may have pressed Reset or started a new proposal, and a method's context() is for the moment
    of a pull (a method may even create its workspace when it is asked). Asking it there made a workspace appear after a reset."""
    dock.evaluate("""() => { window.__asked = 0; const p = Constanza._plugin('probe_owner'), was = p.context; p.context = () => { __asked++; return was(); }; defineLine('L1'); defineLine('L2'); }""")
    open_for(dock, "L1")
    open_for(dock, "L2")
    dock.evaluate("Constanza.onProposalReset()")
    dock.wait_for_timeout(200)                                                                       # (past the focus timer of both opens)
    assert dock.evaluate("__asked") == 0 and dock.evaluate("Object.keys(OWNER.W.byKey)") == []


def test_a_method_whose_where_card_has_nothing_to_press_still_leaves_the_keyboard_inside_the_dock(dock):
    dock.evaluate("""() => {
        state.catalog.products_by_family.Probes.push(item('Blind SKU', 'probe_blind'));
        Constanza.registerMethod('probe_blind', () => Object.assign(%s, { caps: { ownsGeography: true, autoPull: 'none' },
          whereHtml: () => '<section class="aud-sec" id="blind-where"><p>A fixed place</p></section>', context: () => ({ summary: [], geo: '' }) })); }""" % GOOD)
    dock.evaluate("Constanza.close(); Constanza.open({ product: 'Blind SKU' })")
    dock.wait_for_function("document.activeElement && document.activeElement.id === 'cz-product-btn'", timeout=2000)      # the first control the form offers
    assert dock.is_visible("#blind-where") and dock.is_hidden("#cz-geo")


def test_the_start_up_never_takes_the_keyboard_from_a_control_of_the_dock_the_planner_is_already_using(dock):
    """The keyboard is placed 60 ms after open(). A planner who is quick (or a machine that is slow) has already clicked into a box of the dock by then, and
    pulling the keyboard away from it moved what they typed into a box that had lost focus: a method's typeahead closes - and drops the search it was about to
    run - when the keyboard leaves it (Meta's place search stayed on 'Searching Meta…' for good)."""
    dock.evaluate("Constanza.close()")
    dock.evaluate("""() => { Constanza.open({ product: 'Owner SKU' }); const box = document.getElementById('cz-flight'); box.focus(); }""")      # the same task: before the timer
    dock.wait_for_timeout(200)                                                                       # (past the 60 ms start-up)
    assert active(dock) == "cz-flight"
    # the start-up still works for a keyboard that is NOT in the dock (the bubble, a Step 05 button), and for a dock that was open already
    dock.evaluate("document.getElementById('opener').focus(); Constanza.open({ product: 'Owner SKU' })")
    dock.wait_for_function("document.activeElement && document.activeElement.id === 'po-place'", timeout=2000)
    dock.evaluate("document.getElementById('opener').focus(); Constanza.open({ product: 'Roku' })")
    dock.wait_for_function("document.activeElement && document.activeElement.id === 'cz-geo'", timeout=2000)


def test_a_box_that_the_product_switch_hid_no_longer_holds_the_keyboard_so_the_start_up_places_it(dock):
    """The shared geography box is hidden for a method that owns its places, and the browser takes the keyboard off a box nobody can see: the start-up of the
    open() that switched the product finds it nowhere in the dock and places it as it always did (it is only held back from a control that is still there)."""
    dock.evaluate("Constanza.close(); Constanza.open({ product: 'Roku' })")
    dock.wait_for_function("document.activeElement && document.activeElement.id === 'cz-geo'", timeout=2000)
    dock.evaluate("Constanza.open({ product: 'Owner SKU' })")                                        # the box the keyboard was on is hidden now
    dock.wait_for_function("document.activeElement && document.activeElement.id === 'po-place'", timeout=2000)


def test_the_data_products_still_start_on_the_geography_box_or_on_the_pull_when_a_place_is_there(dock):
    dock.evaluate("Constanza.close(); Constanza.open({ product: 'Roku' })")
    dock.wait_for_function("document.activeElement && document.activeElement.id === 'cz-geo'", timeout=2000)
    dock.evaluate("Constanza.close(); Constanza.open({ product: 'Roku', geo: 'Phoenix' })")
    dock.wait_for_function("document.activeElement && document.activeElement.id === 'cz-go'", timeout=2000)


# ------------------------------------------------------------------------------------------------------------------ the Step 05 card, more precisely

def test_a_card_for_a_method_that_owns_its_geography_offers_no_did_you_mean_help_and_a_data_card_keeps_it(dock):
    """The help edits the proposal's geography text (it writes constanzaGeoOverride): meaningless for a method that picks its own places."""
    snap = native(saved_estimate)
    ambiguous = [{"input": "Springfield", "candidates": [{"label": "Springfield, MO", "resolve_hint": "Springfield, MO", "population": 170000}]}]
    owner = {**snap["result"], "ambiguous": ambiguous}
    card(dock, data=owner)
    assert dock.evaluate("__card.querySelector('.cz-geohelp')") is None and dock.evaluate("__card.querySelectorAll('.cz-chip').length") == 0
    assert card_text(dock, ".cz-apply") != [] and card_text(dock, ".cz-refine") != []                # (the rest of the card is all there)
    data = {**estimate_answer({"product_name": "Roku", "months": 2}), "method": "platform", "ambiguous": ambiguous}
    card(dock, data=data, line=None, fromSnapshot=False, snapshot=None)
    assert dock.evaluate("__card.querySelectorAll('.cz-geohelp .cz-chip').length") == 1               # a data product's card still helps with its geography


def test_the_page_can_ask_which_older_shape_a_method_keeps_its_saved_estimates_under(dock):
    """app.js drops that key when it files a new estimate (_constanzaDropOlderShape reads methodState(method).legacyKey): every kind of method answers it."""
    states = dock.evaluate("""() => ({ owner: Constanza.methodState('probe_owner'), plain: Constanza.methodState('probe_plain'), data: Constanza.methodState('platform'),
        generic: Constanza.methodState('generic'), blank: Constanza.methodState(''), ghost: Constanza.methodState('ghost_method') })""")
    assert states["owner"]["legacyKey"] == "old_probe_forecast"                                                # a method that reads an older shape says its name
    assert all(states[k]["legacyKey"] is None for k in ("plain", "data", "generic", "blank", "ghost"))
    assert all("legacyKey" in st for st in states.values())                                           # (always there: null is an answer, a missing key is a different one)
    assert "legacyKey" in (STATIC / "app.js").read_text(encoding="utf-8")                             # ... and the page is what asks


def test_a_card_drawn_from_a_saved_estimate_asks_the_page_for_it_when_it_is_not_handed_the_snapshot(dock):
    """renderCard's documented contract: `fromSnapshot` with no `snapshot` = the card asks window.constanzaHost for the line's own. Its method's canAutoPull
    and the Insert button's fourth argument both get that one."""
    snap = native(saved_estimate)
    dock.evaluate("defineLine('L1')")
    keep(dock, "L1", snap)
    dock.evaluate("""() => { window.__asks = []; window.__given = [];
        const was = constanzaHost.snapshot; constanzaHost.snapshot = (lid) => { __asks.push(lid); return was(lid); };
        const p = Constanza._plugin('probe_owner'), can = p.canAutoPull; p.canAutoPull = (line, snapshot) => { __given.push(snapshot && snapshot.saved_at); return can(line, snapshot); }; }""")
    card(dock, snapshot=None)
    assert dock.evaluate("__asks") == ["L1"] and dock.evaluate("__given") == [snap["saved_at"]]
    assert card_text(dock, ".cz-recalc") == ["Recalculate"]
    dock.evaluate("__card.querySelector('.cz-apply').click()")
    assert dock.evaluate("__calls[0][4].saved_at") == snap["saved_at"]                                # Insert files the page's own snapshot on the line
    # a card that is not drawn from a saved estimate never asks, and one with no line has nobody to ask about
    dock.evaluate("__asks.length = 0; __given.length = 0")
    card(dock, snapshot=None, fromSnapshot=False)
    card(dock, snapshot=None, line=None)
    assert dock.evaluate("__asks") == [] and dock.evaluate("__given") == [None, None]


# ------------------------------------------------------------------------------------------------------------------ an estimate that has no budget to check

@pytest.fixture
def meta_dock(browser, server):
    """The dock with the REAL Meta plugin (constanza-meta.js) beside the probes: the one place this file needs a real method, because what is pinned is how
    the host and a real plugin read an answer that has less in it than the usual one."""
    world = World()
    page = new_page(browser, server, world, query="?meta=1")
    yield page
    page.context_.close()                                                                           # type: ignore[attr-defined]
    assert page.errors == [], page.errors                                                           # type: ignore[attr-defined]


def lean_estimate() -> dict:
    """What Meta answers for a line that has no budget and no CPM of its own: the server defaults the CPM for the retail-spend rows without echoing it,
    and there is no budget_check at all (the key is not there; it is not null)."""
    est = fx.estimate(product_name=META)
    meta = est["meta"]
    del meta["budget_check"]
    meta["scenarios"].pop("retail_spend_at_flight")
    meta["scenarios"].pop("media_spend_at_flight")
    est["notes"] = [n for n in est["notes"] if not n.startswith("Budget impressions")]
    return est


def lean_snapshot() -> dict:
    snap = fx.native(result=lean_estimate())
    snap["request"]["product_name"] = META
    for key in ("retail_budget", "retail_cpm"):
        snap["request"]["meta"].pop(key)
    snap["line"].update(product_name=META, budget=0, cpm=None)
    return snap


BARE = {"id": "L1", "product_name": META, "method": "meta", "label": "Awareness", "months": 3, "flight_days": 61, "has_dates": True, "geo_text": "Los Angeles",
        "target_text": "Adults 25-54", "budget": 0, "cpm": None, "hispanic": None}
NOT_SAID = ("undefined", "NaN", "null", "Infinity")


def test_a_result_with_no_budget_check_draws_the_card_header_and_summary_without_a_cross_check(meta_dock):
    snap = lean_snapshot()
    meta_dock.evaluate("() => { const el = document.createElement('div'); el.id = 'lean'; document.body.appendChild(el); }")
    meta_dock.evaluate("""([snap, line]) => Constanza.renderCard(document.getElementById('lean'), snap.result, { line, fromSnapshot: true, snapshot: snap, stale: Constanza.snapshotStale(snap, line),
        getText: () => '', onGeoText() {}, onApply() {}, onRecalc() {}, onRefine() {} })""", [snap, BARE])
    header = meta_dock.evaluate("[...document.querySelectorAll('#lean .constanza-range-table th')].map((e) => e.textContent.trim())")
    body = " ".join(meta_dock.inner_text("#lean").split())
    assert header == ["", "Conservative", "Mid · default", "Upper"] and "Meta API · live" in body                 # the header the card always has
    assert "1,000,000" in body and "Insert the mid-case (5,500,000 / month) into Avails →" in body                   # ... the lower end and the Insert it offers
    assert "Budget cross-check" not in body and "Retail spend" not in body and not [w for w in NOT_SAID if w in body], body
    assert meta_dock.evaluate("""([snap, line]) => Constanza.snapshotStale(snap, line)""", [snap, BARE]) == []        # (no CPM or budget was recorded: none can be said to differ)


def test_a_line_with_no_budget_pulls_estimates_saves_and_summarises_without_a_budget_check(meta_dock):
    page = meta_dock
    snap = lean_snapshot()
    page.evaluate("(line) => defineLine('L1', line)", BARE)
    keep(page, "L1", snap)
    page.world.reply["estimate"] = lean_estimate()                                                   # type: ignore[attr-defined]
    open_for(page, "L1", product=META, months=3, flightDays=61)
    page.wait_for_selector("#cz-result .cz-headline")                                                # the saved estimate, restored with the choices that made it
    shown = " ".join(page.inner_text("#cz-result").split())
    assert not [w for w in NOT_SAID if w in shown] and "Budget cross-check" not in shown, shown
    summary = page.evaluate("Constanza._pure.summaryText(Constanza._state.result)")
    assert summary.startswith("Constanza estimate — ") and "Los Angeles" in summary and not [w for w in NOT_SAID if w in summary], summary
    sent = pull(page)                                                                                # Estimate again: no budget, no CPM to send
    assert "retail_budget" not in sent["meta"] and "retail_cpm" not in sent["meta"] and sent["geo_text"] == "" and sent["months"] == 3
    saved = page.evaluate("HOST.saved[HOST.saved.length - 1]")
    assert saved[0] == "L1" and saved[1]["line"]["budget"] == 0 and saved[1]["line"]["cpm"] is None and saved[1]["line"]["months"] == 3
    summary = page.evaluate("Constanza._pure.summaryText(Constanza._state.result)")
    assert "Budget cross-check" not in summary and not [w for w in NOT_SAID if w in summary], summary
    assert page.evaluate("([s, l]) => Constanza.snapshotStale(s, l)", [saved[1], BARE]) == []        # saved with no budget, read back with none: fresh
