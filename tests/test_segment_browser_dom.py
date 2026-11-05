"""SegmentBrowser (app/static/segment-browser.js + .css) and the audience kit (audience-kit.css) in a REAL browser (headless Chromium
through Playwright): the things a stub page cannot show - where the focus goes, what Escape leaves behind, whether 10,000 rows stay
quick, whether the page behind can still scroll, whether a segment called `<img onerror=...>` runs.

The page is a small harness (not the app): styles.css, constanza.css, audience-kit.css, segment-browser.css and segment-browser.js, an
opener button, and `window.openSB(cfg)`, which opens the dialog over synthetic items. Skipped when Playwright or its Chromium is absent."""
from __future__ import annotations

import http.server
import json
import threading
from pathlib import Path

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")

STATIC = Path(__file__).resolve().parents[1] / "app" / "static"

# Hand-built audience builder: every .aud- component once (the markup the three panels will produce, so the layout, the heights and the focus
# rings can be checked without them).
SAMPLE = """
<div class="aud-builder" id="sample">
  <section class="aud-sec has-value" aria-labelledby="s-where">
    <div class="aud-sec-head">
      <span class="aud-step" aria-hidden="true"></span>
      <div class="aud-sec-text"><h3 class="aud-sec-title" id="s-where">Where</h3><p class="aud-sec-sum is-set">Los Angeles TV market · Orange County, CA</p></div>
      <span class="aud-count">2</span><button type="button" class="aud-clear">Clear</button>
    </div>
    <div class="aud-sec-body">
      <div class="aud-field"><label class="aud-label" for="geo">Locations <span class="aud-label-aside">cities, ZIPs, TV markets</span></label>
        <div class="aud-ta"><input class="aud-input" id="geo" placeholder="Type a place, e.g. Phoenix" role="combobox" aria-expanded="false"></div>
        <p class="aud-hint">Paste a list, one per line, to add several at once.</p></div>
      <div class="aud-chips">
        <span class="aud-chip aud-chip--geo"><span class="aud-chip-label">Los Angeles</span><span class="aud-chip-meta">TV market</span><button type="button" class="aud-chip-x" aria-label="Remove Los Angeles">×</button></span>
        <span class="aud-chip aud-chip--geo"><span class="aud-chip-label">Orange County, CA</span><span class="aud-chip-meta">county</span><button type="button" class="aud-chip-x" aria-label="Remove Orange County">×</button></span>
      </div>
    </div>
  </section>
  <section class="aud-sec has-value" aria-labelledby="s-who">
    <div class="aud-sec-head">
      <span class="aud-step" aria-hidden="true"></span>
      <div class="aud-sec-text"><h3 class="aud-sec-title" id="s-who">Who</h3><p class="aud-sec-sum is-set">Women · 25–44</p></div>
      <button type="button" class="aud-clear">Clear</button>
    </div>
    <div class="aud-sec-body">
      <div class="aud-field"><span class="aud-label" id="g-l">Gender</span>
        <div class="aud-seg" role="group" aria-labelledby="g-l"><button type="button" aria-pressed="false">All</button><button type="button" aria-pressed="true">Women</button><button type="button" aria-pressed="false">Men</button></div></div>
      <div class="aud-field"><span class="aud-label" id="a-l">Age</span>
        <div class="aud-range"><div class="aud-range-selects"><select class="aud-select" aria-label="From age"><option>25</option></select><span class="aud-range-sep">to</span><select class="aud-select" aria-label="To age"><option>44</option></select></div>
        <div class="aud-quick"><button type="button">18–24</button><button type="button">25–44</button><button type="button">45–64</button><button type="button">65+</button></div></div></div>
      <label class="aud-check"><input type="checkbox" checked> Hispanic households only</label>
    </div>
  </section>
  <section class="aud-sec has-value" aria-labelledby="s-lang">
    <div class="aud-sec-head">
      <span class="aud-step" aria-hidden="true"></span>
      <div class="aud-sec-text"><h3 class="aud-sec-title" id="s-lang">Language</h3><p class="aud-sec-sum is-set">Spanish</p></div>
      <span class="aud-count">1</span><button type="button" class="aud-clear">Clear</button>
    </div>
    <div class="aud-sec-body">
      <div class="aud-quick"><span class="aud-quick-label">Quick picks</span><button type="button" aria-pressed="true">Spanish speakers</button><button type="button" aria-pressed="false">English</button><button type="button" aria-pressed="false">Bilingual</button></div>
      <div class="aud-field"><label class="aud-label" for="lang">Other languages</label><div class="aud-ta"><input class="aud-input" id="lang" placeholder="Search languages" role="combobox" aria-expanded="false"></div></div>
      <div class="aud-chips"><span class="aud-chip aud-chip--lang"><span class="aud-chip-label">Spanish</span><button type="button" class="aud-chip-x" aria-label="Remove Spanish">×</button></span></div>
    </div>
  </section>
  <section class="aud-sec" aria-labelledby="s-aud">
    <div class="aud-sec-head">
      <span class="aud-step" aria-hidden="true"></span>
      <div class="aud-sec-text"><h3 class="aud-sec-title" id="s-aud">Audience</h3><p class="aud-sec-sum">Interests and behaviors · none yet</p></div>
    </div>
    <div class="aud-sec-body">
      <div class="aud-field"><label class="aud-label" for="int">Interests</label>
        <div class="aud-ta"><input class="aud-input" id="int" value="skin" role="combobox" aria-expanded="true" aria-controls="ta1">
          <div class="aud-ta-list" id="ta1" role="listbox">
            <div class="aud-ta-group" role="presentation">Beauty &amp; Personal Care</div>
            <div class="aud-ta-opt is-active" role="option" aria-selected="true"><span class="aud-ta-text"><span class="aud-ta-name"><mark>Skin</mark>care</span><span class="aud-ta-path">Beauty › Skincare</span></span><span class="aud-ta-size">1.2M – 1.5M</span></div>
            <div class="aud-ta-opt" role="option" aria-selected="false"><span class="aud-ta-text"><span class="aud-ta-name">Sensitive <mark>skin</mark> products</span><span class="aud-ta-path">Beauty › Skincare › Sensitive</span></span><span class="aud-ta-size">340K</span></div>
            <div class="aud-ta-group" role="presentation">Health</div>
            <div class="aud-ta-opt" role="option" aria-selected="false"><span class="aud-ta-text"><span class="aud-ta-name">Dermatology</span></span><span class="aud-ta-size">—</span></div>
            <div class="aud-ta-foot"><button type="button" class="aud-link">Browse all 1,204 interests</button></div>
          </div></div></div>
      <div class="aud-chips"><span class="aud-chip aud-chip--interest"><span class="aud-chip-label">Makeup</span><button type="button" class="aud-chip-x" aria-label="Remove Makeup">×</button></span>
        <span class="aud-chip aud-chip--interest"><span class="aud-chip-label">Fashion &amp; accessories</span><button type="button" class="aud-chip-x" aria-label="Remove Fashion">×</button></span>
        <span class="aud-chip aud-chip--excl"><span class="aud-chip-label">Luxury travel</span><button type="button" class="aud-chip-x" aria-label="Remove Luxury travel">×</button></span>
        <span class="aud-chip aud-chip--demo"><span class="aud-chip-label">Top 10% of ZIPs</span><button type="button" class="aud-chip-x" aria-label="Remove Top 10%">×</button></span></div>
      <button type="button" class="aud-browse"><span class="aud-browse-label">Browse all interests</span><span class="aud-browse-count">1,204</span></button>
    </div>
  </section>
  <section class="aud-sec aud-sec--wide" aria-labelledby="s-status">
    <div class="aud-sec-head"><span class="aud-step" aria-hidden="true"></span><div class="aud-sec-text"><h3 class="aud-sec-title" id="s-status">Status banners and states</h3><p class="aud-sec-sum">All notice variants, loading, empty</p></div></div>
    <div class="aud-sec-body">
      <div class="aud-notice aud-notice--connected" role="status"><div class="aud-notice-body"><span class="aud-notice-title">Connected to Meta</span> Ad account <code>act_…4821</code></div></div>
      <div class="aud-notice aud-notice--info"><div class="aud-notice-body">Meta counts people 18 and over; the audience is a range, not one number.</div></div>
      <div class="aud-notice aud-notice--ok"><div class="aud-notice-body"><span class="aud-notice-title">Estimate ready</span> 1.2M to 1.5M people.</div></div>
      <div class="aud-notice aud-notice--warn"><div class="aud-notice-body"><span class="aud-notice-title">This audience is very narrow</span> Try adding a market or removing an interest.<button type="button" class="aud-notice-action">Show me how</button></div></div>
      <div class="aud-notice aud-notice--error" role="alert"><div class="aud-notice-body"><span class="aud-notice-title">Meta isn’t connected yet</span> An admin needs to add the access token to the app’s secrets.</div></div>
      <div class="aud-skeleton" aria-hidden="true"><span></span><span></span><span></span></div>
      <div class="aud-empty"><span class="aud-empty-title">No interests picked yet</span>Search above or browse the full list.</div>
      <div class="aud-seg aud-seg--wrap" role="group" aria-label="Age groups"><button type="button" aria-pressed="false">18–24</button><button type="button" aria-pressed="true">25–34</button><button type="button" aria-pressed="true">35–44</button><button type="button" aria-pressed="false">45–54</button><button type="button" aria-pressed="false">55+</button></div>
      <div class="aud-actions"><button type="button" class="btn-primary">Estimate audience →</button><button type="button" class="btn-secondary">Reset</button></div>
    </div>
  </section>
</div>
"""

HARNESS = """<!doctype html><html><head><meta charset="utf-8"><title>harness</title>
<link rel="stylesheet" href="/static/styles.css"><link rel="stylesheet" href="/static/constanza.css">
<link rel="stylesheet" href="/static/audience-kit.css"><link rel="stylesheet" href="/static/segment-browser.css"></head><body>
<main style="padding:24px"><h1>The page behind</h1><button id="opener" type="button">Browse</button> <button id="other" type="button">Other</button>
<div id="kit-host"></div><div style="height:2400px"></div></main>
<script src="/static/segment-browser.js"></script>
<script>
  window.__applied = []; window.__loads = []; window.__docKeys = 0; window.__failLeft = {};
  document.addEventListener("keydown", () => { window.__docKeys++; });          // a bubble-phase listener, like the dock's Escape handler

  // Deterministic synthetic items: "Segment 1".."Segment N", 8 groups, a size on 9 of 10 rows (the rest null), an upper bound on a quarter.
  window.makeItems = function (n, tag) {
    const groups = ["Apparel", "Beauty", "Business", "Education", "Family", "Food", "Gaming", "Health"];
    return Array.from({ length: n }, (_, i) => ({
      id: tag + i, name: "Segment " + (i + 1), group: groups[i % 8], path: ["All", groups[i % 8]],
      size: i % 10 === 0 ? null : (i * 7919) % 1000003 + 1, sizeUpper: i % 4 === 1 ? ((i * 7919) % 1000003 + 1) * 2 : undefined,
      description: i % 5 === 0 ? "Description number " + (i + 1) : undefined, extra: { n: i },
    }));
  };

  // cfg: n, items {tab: [...]}, describe (a description on every item), tabs, activeTab, delay, fail {tab: message | {error}}, failTimes, chosen ["tab:id"], max, maxPerTab,
  // maxNoteText {tab: text} (becomes maxNote(tab)), chosenCount {tab: n}, returnFocus selector, + pass-through
  window.openSB = function (cfg) {
    cfg = cfg || {};
    window.__applied = []; window.__loads = []; window.__failLeft = {};
    Object.keys(cfg.fail || {}).forEach((t) => { window.__failLeft[t] = cfg.failTimes || 1; });
    const opts = Object.assign({}, cfg, {
      title: cfg.title || "Browse segments",
      load: (tab) => {
        window.__loads.push(tab);
        return new Promise((resolve, reject) => setTimeout(() => {
          if (window.__failLeft[tab] > 0) {
            window.__failLeft[tab]--;
            const f = cfg.fail[tab];
            if (typeof f === "string") reject(new Error(f)); else resolve(f);
            return;
          }
          const list = (cfg.items && cfg.items[tab]) || window.makeItems(cfg.n == null ? 3000 : cfg.n, tab === "all" ? "s" : tab);
          resolve(cfg.describe ? list.map((i) => Object.assign({}, i, { description: "A description line for " + i.name })) : list);
        }, cfg.delay || 0));
      },
      isChosen: (tab, item) => (cfg.chosen || []).indexOf(tab + ":" + item.id) >= 0,
      chosenCount: cfg.chosenCount ? (tab) => cfg.chosenCount[tab] || 0 : undefined,
      maxNote: cfg.maxNoteText ? (tab) => cfg.maxNoteText[tab] : undefined,
      returnFocus: cfg.returnFocus ? document.querySelector(cfg.returnFocus) : undefined,
      onApply: (r) => { window.__applied.push(r); },
    });
    window.__handle = SegmentBrowser.open(opts);
  };

  // Text colours against their real backgrounds (WCAG AA: 4.5, 3 for large text) for everything under `root`.
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
        if (parseFloat(cs.opacity) < 1 || e.disabled || e.getAttribute("aria-disabled") === "true" || cs.visibility === "hidden") return;   // disabled controls are exempt
      }
      const cs = getComputedStyle(el), bg = backdrop(el), fg = over(parse(cs.color), bg);
      const hi = Math.max(lum(fg), lum(bg)), lo = Math.min(lum(fg), lum(bg)), ratio = (hi + 0.05) / (lo + 0.05);
      const size = parseFloat(cs.fontSize), large = size >= 24 || (size >= 18.66 && parseInt(cs.fontWeight, 10) >= 700);
      if (ratio < (large ? 3 : 4.5)) out.push({ text: el.textContent.trim().slice(0, 40), cls: String(el.className), ratio: Math.round(ratio * 100) / 100 });
    });
    return out;
  };
</script></body></html>"""


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


def _new_page(browser, server, width=1280, height=900):
    context = browser.new_context(viewport={"width": width, "height": height})
    page = context.new_page()
    page.route("**/fonts.googleapis.com/**", lambda r: r.abort())
    page.route("**/fonts.gstatic.com/**", lambda r: r.abort())
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.goto(f"{server}/harness.html")
    page.errors, page.context_ = errors, context                    # type: ignore[attr-defined]
    return page


@pytest.fixture
def page(browser, server):
    p = _new_page(browser, server)
    yield p
    p.context_.close()
    assert p.errors == [], p.errors


def open_sb(page, wait=".segb-row", **cfg):
    page.focus("#opener")
    page.evaluate("cfg => window.openSB(cfg)", cfg)
    page.wait_for_selector(".segb-dialog")
    if wait:
        page.wait_for_selector(wait)


def ids(page):
    return page.eval_on_selector_all(".segb-row", "els => els.map(e => e.dataset.id)")


def names(page):
    return page.eval_on_selector_all(".segb-row", "els => els.map(e => e.querySelector('.segb-cell').textContent)")


def count_text(page):
    return page.inner_text(".segb-count").strip()


def selected_ids(page):
    return page.eval_on_selector_all('.segb-row[aria-selected="true"]', "els => els.map(e => e.dataset.id)")


def sort_state(page):
    return page.eval_on_selector_all(".segb-th[aria-sort]", "els => els.map(e => [e.querySelector('.segb-sort').dataset.key, e.getAttribute('aria-sort')])")


def active(page):
    return page.evaluate("document.activeElement && (document.activeElement.className + '|' + document.activeElement.id)")


def in_dialog(page):
    return page.evaluate("!!document.activeElement && !!document.activeElement.closest('.segb-dialog')")


def size_of(i):                                                   # mirrors makeItems()
    return None if i % 10 == 0 else (i * 7919) % 1000003 + 1


# --------------------------------------------------------------------------------------------- the dialog is a real dialog

def test_it_opens_as_an_accessible_modal_dialog(page):
    open_sb(page, title="Browse interests", subtitle="Pick as many as you like")
    dlg = page.locator(".segb-dialog")
    assert dlg.get_attribute("role") == "dialog" and dlg.get_attribute("aria-modal") == "true"
    label = dlg.get_attribute("aria-labelledby")
    assert page.inner_text(f"#{label}") == "Browse interests"
    assert page.inner_text(f"#{dlg.get_attribute('aria-describedby')}") == "Pick as many as you like"
    assert in_dialog(page) and "segb-search-input" in active(page)              # focus moved in, to the search box
    assert page.evaluate("getComputedStyle(document.body).overflow") == "hidden"  # the page behind cannot scroll


def test_the_page_behind_gets_its_scroll_and_inline_style_back_exactly(page):
    page.evaluate("document.body.setAttribute('style', 'margin: 0px; overflow-y: auto')")
    before = page.evaluate("document.body.getAttribute('style')")
    open_sb(page)
    assert page.evaluate("document.body.style.overflow") == "hidden"
    page.keyboard.press("Escape")
    assert page.evaluate("document.body.getAttribute('style')") == before
    page.evaluate("document.body.removeAttribute('style')")                    # and with no inline style at all, none is left behind
    open_sb(page)
    page.click(".segb-cancel")
    assert page.evaluate("document.body.hasAttribute('style')") is False


def test_escape_discards_and_gives_the_focus_back_to_the_opener(page):
    """With a tick pending the first Escape asks ("Close without applying?"), the second one discards: it is never silent. With nothing ticked it closes at once."""
    open_sb(page)
    page.keyboard.press("Escape")
    assert page.locator(".segb-dialog").count() == 0 and page.evaluate("document.activeElement.id") == "opener"          # nothing to lose: one Escape
    open_sb(page)
    page.click('.segb-row[data-id="s1"]')
    assert selected_ids(page) == ["s1"]
    page.keyboard.press("Escape")
    assert page.locator(".segb-dialog").count() == 1 and selected_ids(page) == ["s1"]
    page.keyboard.press("Escape")
    assert page.locator(".segb-dialog").count() == 0
    assert page.evaluate("window.__applied.length") == 0
    assert page.evaluate("document.activeElement.id") == "opener"


def test_escape_does_not_reach_a_listener_behind_the_dialog(page):
    """The dock closes on its own Escape handler; one keypress must close the dialog only (and asking "close without applying?" is the dialog's business too)."""
    open_sb(page)
    page.evaluate("window.__docKeys = 0")
    page.keyboard.press("Escape")
    assert page.evaluate("window.__docKeys") == 0
    open_sb(page)
    page.click('.segb-row[data-id="s1"]')
    page.evaluate("window.__docKeys = 0")
    page.keyboard.press("Escape")
    page.keyboard.press("Escape")
    assert page.locator(".segb-dialog").count() == 0 and page.evaluate("window.__docKeys") == 0


def test_the_focus_goes_to_returnFocus_when_one_is_given(page):
    open_sb(page, returnFocus="#other")
    page.click(".segb-cancel")
    assert page.evaluate("document.activeElement.id") == "other"


def test_tab_and_shift_tab_stay_inside_the_dialog(page):
    open_sb(page)
    for _ in range(40):
        page.keyboard.press("Tab")
        assert in_dialog(page), active(page)
    for _ in range(40):
        page.keyboard.press("Shift+Tab")
        assert in_dialog(page), active(page)
    page.focus(".segb-x")
    page.keyboard.press("Shift+Tab")                                           # from the first control it wraps to the last one (Apply is disabled)
    assert "segb-cancel" in active(page)
    page.keyboard.press("Tab")                                                 # and from the last one back to the first
    assert "segb-x" in active(page)
    page.evaluate("document.getElementById('other').focus()")                  # focus pushed out is pulled back in
    assert in_dialog(page)


def test_cancel_and_the_close_button_discard_and_return_focus(page):
    open_sb(page)
    page.click(".segb-x")
    assert page.locator(".segb-dialog").count() == 0 and page.evaluate("document.activeElement.id") == "opener"
    open_sb(page)
    page.click('.segb-row[data-id="s2"]')
    page.click(".segb-cancel")
    assert page.evaluate("window.__applied.length") == 0


def test_a_click_on_the_backdrop_closes_only_when_nothing_is_ticked(page):
    open_sb(page)
    page.mouse.click(5, 5)
    assert page.locator(".segb-dialog").count() == 0
    open_sb(page)
    page.click('.segb-row[data-id="s2"]')
    page.mouse.click(5, 5)
    assert page.locator(".segb-dialog").count() == 1                           # a pick is not thrown away by a stray click


def test_opening_a_second_dialog_closes_the_first_and_keeps_one_scroll_lock(page):
    open_sb(page, title="First")
    first = page.evaluate("window.__handle")
    page.evaluate("cfg => window.openSB(cfg)", {"title": "Second", "n": 5})
    page.wait_for_selector(".segb-dialog")
    assert page.locator(".segb-dialog").count() == 1 and page.inner_text(".segb-title") == "Second"
    page.evaluate("SegmentBrowser.isOpen()")
    assert page.evaluate("SegmentBrowser.isOpen()") is True
    page.keyboard.press("Escape")
    assert page.evaluate("SegmentBrowser.isOpen()") is False
    assert page.evaluate("document.body.hasAttribute('style')") is False       # one lock, one unlock
    del first


def test_a_closed_handle_cannot_close_a_newer_dialog(page):
    open_sb(page, title="First")
    page.evaluate("window.__old = window.__handle")
    page.evaluate("cfg => window.openSB(cfg)", {"title": "Second", "n": 5})
    page.wait_for_selector(".segb-row")
    page.evaluate("window.__old.close()")
    assert page.locator(".segb-dialog").count() == 1 and page.inner_text(".segb-title") == "Second"


# --------------------------------------------------------------------------------------------- rows are drawn in chunks

def test_three_thousand_rows_are_drawn_a_hundred_at_a_time(page):
    open_sb(page, n=3000)
    assert page.locator(".segb-row").count() == 100
    assert count_text(page) == "3,000 segments"
    assert page.inner_text(".segb-more").strip() == "Show 100 more"
    assert page.get_attribute(".segb-table", "aria-rowcount") == "3001"
    page.click(".segb-more")
    assert page.locator(".segb-row").count() == 200
    assert page.get_attribute('.segb-row:nth-child(150)', "aria-rowindex") == "151"      # the header is row 1


def test_scrolling_near_the_end_draws_the_next_chunk(page):
    open_sb(page, n=3000)
    page.evaluate("document.querySelector('.segb-body').scrollTop = document.querySelector('.segb-body').scrollHeight")
    page.wait_for_function("document.querySelectorAll('.segb-row').length > 100")
    assert page.locator(".segb-row").count() in (200, 300)


def test_the_last_chunk_is_just_what_is_left_and_the_button_goes_away(page):
    open_sb(page, n=230)
    assert page.inner_text(".segb-more").strip() == "Show 100 more"
    page.click(".segb-more")
    assert page.inner_text(".segb-more").strip() == "Show 30 more" and page.locator(".segb-row").count() == 200
    page.click(".segb-more")
    assert page.locator(".segb-row").count() == 230 and page.locator(".segb-more").count() == 0


def test_the_keyboard_focus_survives_the_list_growing(page):
    """Tabbing to "Show 100 more" scrolls the list to its end, which draws the next chunk: the button must keep the focus."""
    open_sb(page, n=3000)
    page.focus(".segb-more")
    page.wait_for_function("document.querySelectorAll('.segb-row').length > 100")
    assert "segb-more" in active(page)
    page.keyboard.press("Enter")                                                # activated from the keyboard: the focus moves on to the first new row
    assert "segb-row" in active(page)


def test_ten_thousand_items_sort_and_filter_without_drawing_more_than_a_chunk(page):
    open_sb(page, n=10000, columns=[{"key": "name", "label": "Name"}, {"key": "group", "label": "Category"}, {"key": "size", "label": "Audience size", "numeric": True}])
    timing = page.evaluate("""() => {
      const t = [];
      for (const key of ["group", "size", "size", "name", "name"]) {
        const t0 = performance.now(); document.querySelector('.segb-sort[data-key="' + key + '"]').click(); t.push(performance.now() - t0);
      }
      return { t, rows: document.querySelectorAll('.segb-row').length };
    }""")
    assert timing["rows"] == 100 and max(timing["t"]) < 1500, timing         # a 10,000-row sort + redraw; ~100ms on a laptop, the bound is generous
    page.fill(".segb-search-input", "beauty 99")
    page.wait_for_function("document.querySelector('.segb-count').textContent.includes('match')")
    assert page.locator(".segb-row").count() <= 100


# --------------------------------------------------------------------------------------------- search

def test_search_is_debounced_matches_every_word_and_highlights(page):
    open_sb(page, n=300)
    after = page.evaluate("""() => {
      const i = document.querySelector('.segb-search-input'); i.value = 'beauty segment'; i.dispatchEvent(new Event('input', { bubbles: true }));
      return document.querySelector('.segb-count').textContent;
    }""")
    assert after == "300 segments"                                           # nothing yet: the 120 ms wait has not passed
    page.wait_for_function("document.querySelector('.segb-count').textContent.includes('match')")
    assert count_text(page) == "38 of 300 segments match"                    # "Beauty" is every 8th row, offset 1: 38 of 300
    assert all(g == "Beauty" for g in page.eval_on_selector_all(".segb-row", "els => els.map(e => e.children[2].textContent)"))
    marks = page.eval_on_selector_all(".segb-row .segb-mark", "els => els.map(e => e.textContent.toLowerCase())")
    assert marks and set(marks) <= {"beauty", "segment"}
    page.fill(".segb-search-input", "description number 11")                  # words match the description too
    page.wait_for_function("document.querySelector('.segb-count').textContent !== '38 of 300 segments match'")
    assert "Segment 11" in names(page) and "Segment 12" not in names(page)
    assert all("Description number" in d for d in page.eval_on_selector_all(".segb-row .segb-desc", "els => els.map(e => e.textContent)"))


def test_search_ignores_case_and_accents(page):
    items = {"all": [{"id": "1", "name": "Español", "group": "Idioma"}, {"id": "2", "name": "English"}, {"id": "3", "name": "ESPAÑOL (MX)"}]}
    open_sb(page, items=items)
    page.fill(".segb-search-input", "espanol")
    page.wait_for_function("document.querySelector('.segb-count').textContent.includes('match')")
    assert sorted(ids(page)) == ["1", "3"]
    page.fill(".segb-search-input", "ESPAÑOL mx")
    page.wait_for_function("document.querySelector('.segb-count').textContent.startsWith('1 of')")
    assert ids(page) == ["3"]


def test_nothing_matching_says_so_and_clear_search_brings_the_list_back(page):
    open_sb(page, n=50)
    page.fill(".segb-search-input", "xyz")
    page.wait_for_selector(".segb-state--nomatch")
    assert "Nothing matches “xyz”" in page.inner_text(".segb-state") and "Nothing matches “xyz”" in count_text(page)
    assert page.locator(".segb-table").is_hidden()
    page.click(".segb-clear-search")
    page.wait_for_selector(".segb-row")
    assert page.input_value(".segb-search-input") == "" and count_text(page) == "50 segments"


def test_the_clear_button_in_the_search_box_shows_with_text_and_empties_it(page):
    open_sb(page, n=50)
    assert page.locator(".segb-search-clear").is_hidden()
    page.fill(".segb-search-input", "beauty")
    assert page.locator(".segb-search-clear").is_visible()
    page.wait_for_function("document.querySelector('.segb-count').textContent.includes('match')")
    page.click(".segb-search-clear")
    assert page.input_value(".segb-search-input") == "" and count_text(page) == "50 segments" and page.locator(".segb-search-clear").is_hidden()
    assert "segb-search-input" in active(page)                                 # the focus is back in the box, ready for the next word


def test_a_list_that_came_back_empty_shows_the_empty_text(page):
    open_sb(page, wait=".segb-state--empty", items={"all": []}, emptyText="There are no languages for this account.")
    assert page.inner_text(".segb-state").strip() == "There are no languages for this account."
    assert page.locator(".segb-table").is_hidden()


# --------------------------------------------------------------------------------------------- sorting

def test_default_sort_is_name_ascending_and_natural(page):
    open_sb(page, n=3000)
    assert sort_state(page) == [["name", "ascending"], ["category", "none"]]
    assert names(page)[:4] == ["Segment 1", "Segment 2", "Segment 3", "Segment 4"]
    assert names(page)[9:12] == ["Segment 10", "Segment 11", "Segment 12"]     # 2 before 10: numbers inside names compare as numbers


def test_clicking_a_header_toggles_the_direction(page):
    open_sb(page, n=3000)
    page.click('.segb-sort[data-key="name"]')
    assert sort_state(page)[0] == ["name", "descending"]
    assert names(page)[:3] == ["Segment 3000", "Segment 2999", "Segment 2998"]
    page.click('.segb-sort[data-key="name"]')
    assert sort_state(page)[0] == ["name", "ascending"] and names(page)[0] == "Segment 1"


def test_a_number_column_sorts_biggest_first_then_smallest_with_blanks_always_last(page):
    items = {"all": [{"id": "a", "name": "A", "size": 10}, {"id": "b", "name": "B"}, {"id": "c", "name": "C", "size": 5},
                     {"id": "d", "name": "D", "size": 20}, {"id": "e", "name": "E", "size": None}, {"id": "f", "name": "F", "size": 5}]}
    open_sb(page, items=items, columns=[{"key": "name", "label": "Name"}, {"key": "size", "label": "Audience size", "numeric": True}])
    page.click('.segb-sort[data-key="size"]')
    assert sort_state(page)[1] == ["size", "descending"]                        # a number column starts with the biggest
    assert ids(page) == ["d", "a", "c", "f", "b", "e"]                          # ties keep their order; blanks (b, e) last
    page.click('.segb-sort[data-key="size"]')
    assert sort_state(page)[1] == ["size", "ascending"]
    assert ids(page) == ["c", "f", "a", "d", "b", "e"]                          # blanks are still last
    cells = page.eval_on_selector_all(".segb-row", "els => els.map(e => e.children[2].textContent)")
    assert cells == ["5", "5", "10", "20", "—", "—"]


def test_the_size_column_shows_the_lower_bound_a_range_or_a_dash(page):
    items = {"all": [{"id": "a", "name": "A", "size": 1234567}, {"id": "b", "name": "B", "size": 1200000, "sizeUpper": 1500000},
                     {"id": "c", "name": "C"}, {"id": "d", "name": "D", "size": 950}]}
    open_sb(page, items=items, columns=[{"key": "name", "label": "Name"}, {"key": "size", "label": "Audience size", "numeric": True}])
    cells = dict(zip(ids(page), page.eval_on_selector_all(".segb-row", "els => els.map(e => e.children[2].textContent)")))
    assert cells == {"a": "1.2M", "b": "1.2M – 1.5M", "c": "—", "d": "950"}
    assert page.get_attribute('.segb-row[data-id="b"] td:nth-child(3)', "title") == "1,200,000 to 1,500,000 people"


def test_size_order_over_3000_rows_matches_the_data(page):
    open_sb(page, n=3000, columns=[{"key": "name", "label": "Name"}, {"key": "size", "label": "Audience size", "numeric": True}])
    page.click('.segb-sort[data-key="size"]')
    expected = sorted((i for i in range(3000) if size_of(i) is not None), key=lambda i: (-size_of(i), i))[:100]
    assert ids(page) == [f"s{i}" for i in expected]
    page.click('.segb-sort[data-key="size"]')
    expected = sorted((i for i in range(3000) if size_of(i) is not None), key=lambda i: (size_of(i), i))[:100]
    assert ids(page) == [f"s{i}" for i in expected]


def test_sorting_keeps_the_search_and_the_search_keeps_the_sort(page):
    open_sb(page, n=300)
    page.fill(".segb-search-input", "food")
    page.wait_for_function("document.querySelector('.segb-count').textContent.includes('match')")
    page.click('.segb-sort[data-key="name"]')                                  # name desc inside the filtered list
    assert count_text(page) == "37 of 300 segments match"
    n = [int(x.split()[1]) for x in names(page)]
    assert n == sorted(n, reverse=True) and len(n) == 37
    page.fill(".segb-search-input", "beauty")                                    # a new search keeps the descending sort
    page.wait_for_function("document.querySelector('.segb-count').textContent === '38 of 300 segments match'")
    assert sort_state(page)[0] == ["name", "descending"]
    n = [int(x.split()[1]) for x in names(page)]
    assert n == sorted(n, reverse=True) and len(n) == 38


def test_the_sort_survives_a_tab_switch_and_each_tab_loads_once(page):
    tabs = [{"key": "a", "label": "Interests"}, {"key": "b", "label": "Behaviors"}]
    cols = [{"key": "name", "label": "Name"}, {"key": "size", "label": "Audience size", "numeric": True}]
    open_sb(page, n=400, tabs=tabs, columns=cols)
    assert page.inner_text('.segb-tab[data-tab="a"] .segb-tab-count') == "400" and page.inner_text('.segb-tab[data-tab="b"] .segb-tab-count') == ""
    page.click('.segb-sort[data-key="size"]')
    page.click('.segb-tab[data-tab="b"]')
    page.wait_for_selector('.segb-row[data-id^="b"]')
    assert sort_state(page)[1] == ["size", "descending"]
    assert page.inner_text('.segb-tab[data-tab="b"] .segb-tab-count') == "400"
    assert page.get_attribute('.segb-tab[data-tab="b"]', "aria-selected") == "true" and page.get_attribute('.segb-tab[data-tab="a"]', "aria-selected") == "false"
    page.click('.segb-tab[data-tab="a"]')
    page.wait_for_selector('.segb-row[data-id^="a"]')
    assert page.evaluate("window.__loads") == ["a", "b"]                         # not asked for again
    assert page.get_attribute(".segb-tablist, .segb-tabs", "role") == "tablist"


def test_tabs_move_with_the_arrow_keys(page):
    tabs = [{"key": "a", "label": "One"}, {"key": "b", "label": "Two"}, {"key": "c", "label": "Three"}]
    open_sb(page, n=20, tabs=tabs)
    page.focus('.segb-tab[data-tab="a"]')
    page.keyboard.press("ArrowRight")
    assert page.get_attribute('.segb-tab[data-tab="b"]', "aria-selected") == "true" and "segb-tab" in active(page)
    page.keyboard.press("End")
    assert page.get_attribute('.segb-tab[data-tab="c"]', "aria-selected") == "true"
    page.keyboard.press("ArrowRight")                                           # wraps
    assert page.get_attribute('.segb-tab[data-tab="a"]', "aria-selected") == "true"


# --------------------------------------------------------------------------------------------- selection

def test_ticking_rows_updates_the_footer_and_the_apply_button(page):
    open_sb(page, n=200)
    assert page.is_disabled(".segb-apply") and page.inner_text(".segb-apply").strip() == "Add selected"
    assert page.inner_text(".segb-sel-n").strip() == "0 selected" and page.is_disabled(".segb-clear")
    for i in (1, 2, 3):
        page.click(f'.segb-row[data-id="s{i}"]')
    assert page.inner_text(".segb-apply").strip() == "Add 3 selected" and not page.is_disabled(".segb-apply")
    assert page.inner_text(".segb-sel-n").strip() == "3 selected" and page.inner_text(".segb-sel-delta").strip() == "3 to add"
    page.click('.segb-row[data-id="s2"]')                                       # un-tick
    assert selected_ids(page) == ["s1", "s3"] and page.inner_text(".segb-apply").strip() == "Add 2 selected"
    assert page.is_checked('.segb-row[data-id="s1"] .segb-check') and not page.is_checked('.segb-row[data-id="s2"] .segb-check')
    page.click(".segb-clear")
    assert selected_ids(page) == [] and page.is_disabled(".segb-apply")


def test_the_checkbox_itself_toggles_once(page):
    open_sb(page, n=20)
    page.click('.segb-row[data-id="s3"] .segb-check')
    assert selected_ids(page) == ["s3"]
    page.click('.segb-row[data-id="s3"] .segb-check')
    assert selected_ids(page) == []


def test_space_and_enter_tick_the_focused_row_and_the_arrows_move(page):
    open_sb(page, n=20)
    page.keyboard.press("ArrowDown")                                            # from the search box into the list
    assert page.evaluate("document.activeElement.dataset.id") == "s0"
    page.keyboard.press("Space")
    assert selected_ids(page) == ["s0"]
    page.keyboard.press("ArrowDown")
    page.keyboard.press("Enter")
    assert selected_ids(page) == ["s0", "s1"] and page.evaluate("document.activeElement.dataset.id") == "s1"
    page.keyboard.press("End")
    assert page.evaluate("document.activeElement.dataset.id") == "s19"
    page.keyboard.press("Home")
    assert page.evaluate("document.activeElement.dataset.id") == "s0"
    page.keyboard.press("ArrowUp")                                              # off the top: back to the search box
    assert "segb-search-input" in active(page)


def test_items_the_caller_already_holds_open_ticked_and_do_not_count_as_added(page):
    open_sb(page, n=50, chosen=["all:s1", "all:s2"])
    assert selected_ids(page) == ["s1", "s2"]
    assert page.inner_text(".segb-sel-n").strip() == "2 selected" and page.is_disabled(".segb-apply")


def test_max_counts_what_is_already_held_and_disables_the_rest_with_a_note(page):
    open_sb(page, n=50, chosen=["all:s1"], max=3)
    assert page.inner_text(".segb-sel-n").strip() == "1 of 3 selected" and page.locator(".segb-limit").is_hidden()
    page.click('.segb-row[data-id="s2"]')
    page.click('.segb-row[data-id="s3"]')                                       # now at the limit
    assert page.inner_text(".segb-limit").strip() == "You’ve reached the limit of 3. Untick one to add another."
    assert page.locator(".segb-limit").is_visible()
    assert page.locator('.segb-row[aria-disabled="true"]').count() == 47        # every unticked row
    assert page.is_disabled('.segb-row[data-id="s9"] .segb-check')
    page.click('.segb-row[data-id="s9"]', force=True)                           # (Playwright will not click what is aria-disabled; a person can)
    assert "s9" not in selected_ids(page) and len(selected_ids(page)) == 3
    page.click('.segb-row[data-id="s1"]')                                       # a ticked row can always be unticked (and frees a slot)
    assert page.locator('.segb-row[aria-disabled="true"]').count() == 0 and page.locator(".segb-limit").is_hidden()
    page.click('.segb-row[data-id="s9"]')
    assert sorted(selected_ids(page)) == ["s2", "s3", "s9"]


def test_max_counts_picks_held_in_a_tab_that_has_not_loaded_yet(page):
    tabs = [{"key": "a", "label": "Interests"}, {"key": "b", "label": "Hashtags"}]
    open_sb(page, n=30, tabs=tabs, max=3, chosenCount={"b": 2})
    assert page.inner_text(".segb-sel-n").strip() == "2 of 3 selected"
    page.click('.segb-row[data-id="a1"]')                                       # the third and last
    assert page.locator('.segb-row[aria-disabled="true"]').count() == 29
    assert page.evaluate("window.__loads") == ["a"]                             # tab b was never asked for just to count it


def test_a_pick_the_list_does_not_contain_still_counts_toward_max(page):
    open_sb(page, n=30, chosen=["all:s1"], chosenCount={"all": 3}, max=4)       # the caller holds 3 in this tab, only one of them is in the list
    assert page.inner_text(".segb-sel-n").strip() == "3 of 4 selected"
    page.click('.segb-row[data-id="s2"]')
    assert page.locator('.segb-row[aria-disabled="true"]').count() == 28
    page.click(".segb-clear")                                                    # Clear removes what the list can show, never the unseen pick
    assert page.inner_text(".segb-sel-n").strip() == "2 of 4 selected"


def test_the_header_box_ticks_everything_that_matches_and_shows_a_dash_for_some(page):
    open_sb(page, n=300)
    assert page.eval_on_selector(".segb-all", "e => e.indeterminate || e.checked") is False
    page.click('.segb-row[data-id="s1"]')
    assert page.eval_on_selector(".segb-all", "e => e.indeterminate") is True
    page.fill(".segb-search-input", "beauty")
    page.wait_for_function("document.querySelector('.segb-count').textContent.includes('match')")
    assert page.get_attribute(".segb-all", "aria-label") == "Select all 38 shown"
    page.click(".segb-all")                                                      # all 38, including the 138 not yet drawn... and s1 was already one of them
    assert page.inner_text(".segb-sel-n").strip() == "38 selected"
    assert page.eval_on_selector(".segb-all", "e => e.checked && !e.indeterminate") is True
    page.click(".segb-all")
    assert page.inner_text(".segb-sel-n").strip() == "0 selected"


def test_the_header_box_stops_at_max_and_unticks_when_there_is_no_room(page):
    open_sb(page, n=300, max=5)
    page.fill(".segb-search-input", "beauty")
    page.wait_for_function("document.querySelector('.segb-count').textContent.includes('match')")
    page.click(".segb-all")
    assert page.inner_text(".segb-sel-n").strip() == "5 of 5 selected"
    assert page.eval_on_selector(".segb-all", "e => e.indeterminate") is True
    assert page.locator(".segb-limit").is_visible()
    page.click(".segb-all")                                                      # at the limit nothing can be added: the click unticks instead
    assert page.inner_text(".segb-sel-n").strip() == "0 of 5 selected"


# --------------------------------------------------------------------------------------------- apply

def test_apply_sends_only_the_changes_once_and_closes(page):
    open_sb(page, n=50, chosen=["all:s1", "all:s2"])
    page.click('.segb-row[data-id="s1"]')                                       # un-tick a held one
    page.click('.segb-row[data-id="s7"]')
    page.click('.segb-row[data-id="s5"]')
    page.click('.segb-row[data-id="s2"]')                                       # un-tick and re-tick: no change
    page.click('.segb-row[data-id="s2"]')
    assert page.inner_text(".segb-apply").strip() == "Add 2, remove 1"
    assert page.inner_text(".segb-sel-delta").strip() == "2 to add · 1 to remove"
    page.click(".segb-apply")
    assert page.locator(".segb-dialog").count() == 0
    applied = page.evaluate("window.__applied")
    assert len(applied) == 1
    assert [(a["tab"], a["item"]["id"]) for a in applied[0]["added"]] == [("all", "s7"), ("all", "s5")]      # in the order they were ticked
    assert [(a["tab"], a["item"]["id"]) for a in applied[0]["removed"]] == [("all", "s1")]
    assert applied[0]["added"][0]["item"]["extra"] == {"n": 7}                  # the item as load() returned it, extras untouched
    assert page.evaluate("document.activeElement.id") == "opener"


def test_apply_reports_the_tab_each_change_came_from(page):
    tabs = [{"key": "a", "label": "One"}, {"key": "b", "label": "Two"}]
    open_sb(page, n=20, tabs=tabs, chosen=["b:b3"])
    page.click('.segb-row[data-id="a2"]')
    page.click('.segb-tab[data-tab="b"]')
    page.wait_for_selector('.segb-row[data-id="b3"]')
    assert "b3" in selected_ids(page)
    page.click('.segb-row[data-id="b3"]')
    page.click('.segb-row[data-id="b4"]')
    page.click(".segb-apply")
    r = page.evaluate("window.__applied[0]")
    assert [(a["tab"], a["item"]["id"]) for a in r["added"]] == [("a", "a2"), ("b", "b4")]
    assert [(a["tab"], a["item"]["id"]) for a in r["removed"]] == [("b", "b3")]


def test_a_custom_apply_label_shows_the_number_of_changes(page):
    open_sb(page, n=20, applyLabel="Add to audience")
    assert page.inner_text(".segb-apply").strip() == "Add to audience" and page.is_disabled(".segb-apply")
    page.click('.segb-row[data-id="s1"]')
    page.click('.segb-row[data-id="s2"]')
    assert page.inner_text(".segb-apply").strip() == "Add to audience (2)"


# --------------------------------------------------------------------------------------------- loading, errors

def test_a_rejected_load_shows_the_message_and_retry_loads_again(page):
    open_sb(page, wait=".segb-state--error", n=20, fail={"all": "The server is down."})
    assert "The server is down." in page.inner_text(".segb-state")
    assert page.get_attribute(".segb-state", "role") == "alert" and page.locator(".segb-table").is_hidden()
    page.click(".segb-retry")
    page.wait_for_selector(".segb-row")
    assert page.evaluate("window.__loads") == ["all", "all"] and count_text(page) == "20 segments"


def test_an_error_result_shows_its_message_and_can_be_retried(page):
    open_sb(page, wait=".segb-state--error", n=20, fail={"all": {"error": "Meta didn’t answer."}}, failTimes=2)
    assert "Meta didn’t answer." in page.inner_text(".segb-state")
    page.click(".segb-retry")
    page.wait_for_selector(".segb-state--error")                                # failed again: still an error, still retryable
    page.click(".segb-retry")
    page.wait_for_selector(".segb-row")
    assert page.evaluate("window.__loads") == ["all", "all", "all"]


def test_a_slow_load_shows_a_skeleton_first(page):
    open_sb(page, wait=".segb-state--loading", n=20, delay=400)
    assert page.locator(".segb-skel").count() >= 3 and page.get_attribute(".segb-body", "aria-busy") == "true"
    page.wait_for_selector(".segb-row")
    assert page.get_attribute(".segb-body", "aria-busy") == "false"


def test_a_load_that_finishes_after_the_dialog_closed_does_nothing(page):
    open_sb(page, wait=".segb-state--loading", n=20, delay=300)
    page.keyboard.press("Escape")
    page.wait_for_timeout(450)
    assert page.locator(".segb-dialog").count() == 0


# --------------------------------------------------------------------------------------------- never trust item text

def test_item_text_is_never_parsed_as_html(page):
    evil = '<img src=x onerror="window.__pwned=1">'
    items = {"t": [{"id": "e1", "name": evil, "group": evil, "path": [evil, "x"], "description": evil + " <b>bold</b>", "size": 5},
                   {"id": "e2", "name": "</td><script>window.__pwned=2</script>", "group": "g"}]}
    open_sb(page, items=items, tabs=[{"key": "t", "label": evil}], title=evil, subtitle=evil, noun=evil,
            columns=[{"key": "name", "label": evil}, {"key": "group", "label": "Category"}])
    page.fill(".segb-search-input", "img")                                       # the highlight is built from text nodes too
    page.wait_for_function("document.querySelector('.segb-count').textContent.includes('match')")
    assert page.locator(".segb-mark").count() >= 1
    page.fill(".segb-search-input", "")
    page.wait_for_function("document.querySelectorAll('.segb-row').length === 2 && !document.querySelector('.segb-mark')")
    assert page.evaluate("window.__pwned") is None
    assert page.locator(".segb-dialog img, .segb-dialog script, .segb-dialog b").count() == 0
    assert evil in names(page) and page.inner_text(".segb-title") == evil
    assert page.inner_text('.segb-tab[data-tab="t"] .segb-tab-label') == evil
    page.fill(".segb-search-input", "zzz" + evil)                               # even the "nothing matches" text echoes the query as text
    page.wait_for_selector(".segb-state--nomatch")
    assert page.locator(".segb-dialog img").count() == 0 and page.evaluate("window.__pwned") is None


def test_a_load_error_message_is_text_too(page):
    open_sb(page, wait=".segb-state--error", fail={"all": '<img src=x onerror="window.__pwned=1">'})
    assert page.locator(".segb-dialog img").count() == 0 and page.evaluate("window.__pwned") is None


# --------------------------------------------------------------------------------------------- layout and look

def test_the_dialog_is_a_full_screen_sheet_on_a_phone_and_has_no_sideways_scroll(browser, server):
    p = _new_page(browser, server, 390, 780)
    try:
        open_sb(p, n=300, tabs=[{"key": "a", "label": "Interests"}, {"key": "b", "label": "Hashtags"}, {"key": "c", "label": "Creators"}],
                columns=[{"key": "name", "label": "Name"}, {"key": "group", "label": "Category"}, {"key": "size", "label": "Audience size", "numeric": True}])
        box = p.evaluate("(() => { const r = document.querySelector('.segb-dialog').getBoundingClientRect(); return [r.x, r.y, r.width, r.height]; })()")
        assert box == [0, 0, 390, 780]
        assert p.evaluate("(() => { const b = document.querySelector('.segb-body'); return b.scrollWidth <= b.clientWidth; })()")
        assert p.evaluate("document.querySelector('.segb-foot').getBoundingClientRect().bottom") <= 780
        assert "segb-dialog" in active(p) or p.evaluate("document.activeElement.className") == "segb-dialog"   # the keyboard stays down on a phone
    finally:
        p.context_.close()
    assert p.errors == [], p.errors


def test_every_control_in_the_dialog_is_at_least_36px_tall_and_has_a_focus_ring(page):
    open_sb(page, n=100, tabs=[{"key": "a", "label": "Interests"}, {"key": "b", "label": "Hashtags"}])
    page.click('.segb-row[data-id="a1"]')
    page.fill(".segb-search-input", "seg")                                     # (shows the clear button too)
    page.wait_for_function("document.querySelector('.segb-count').textContent.includes('match')")
    small = page.evaluate("""() => [...document.querySelectorAll('.segb-dialog button, .segb-dialog input[type=search]')]
      .filter((e) => e.getClientRects().length && e.getBoundingClientRect().height < 35.9).map((e) => e.className)""")
    assert small == []
    page.focus(".segb-search-input")
    seen = set()
    for _ in range(14):
        page.keyboard.press("Tab")
        info = page.evaluate("(() => { const e = document.activeElement, s = getComputedStyle(e); return [e.className, s.outlineStyle, parseFloat(s.outlineWidth)]; })()")
        seen.add(info[0])
        if "segb-row" in info[0]:
            continue                                                            # a row draws its ring as an inset bar on its cells
        assert info[1] != "none" and info[2] >= 2, info
    assert any("segb-sort" in c for c in seen) and any("segb-apply" in c or "segb-cancel" in c for c in seen)


def test_text_in_the_dialog_meets_aa_contrast_in_every_state(page):
    open_sb(page, n=100, chosen=["all:s1"], max=3, describe=True)
    page.fill(".segb-search-input", "beauty")
    page.wait_for_function("document.querySelector('.segb-count').textContent.includes('match')")
    page.click('.segb-row:nth-child(1)')
    page.click('.segb-row:nth-child(2)')                                        # at the limit: the note shows
    assert page.evaluate("contrastReport('.segb-dialog')") == []
    page.fill(".segb-search-input", "nothing-like-this")
    page.wait_for_selector(".segb-state--nomatch")
    assert page.evaluate("contrastReport('.segb-dialog')") == []


def test_an_error_and_an_empty_state_meet_aa_contrast(page):
    open_sb(page, wait=".segb-state--error", fail={"all": "Meta didn’t answer."})
    assert page.evaluate("contrastReport('.segb-dialog')") == []
    page.keyboard.press("Escape")
    open_sb(page, wait=".segb-state--empty", items={"all": []})
    assert page.evaluate("contrastReport('.segb-dialog')") == []


# --------------------------------------------------------------------------------------------- the audience kit

def _kit(browser, server, width):
    p = _new_page(browser, server, width + 40, 1000)
    p.evaluate("([html, w]) => { const h = document.getElementById('kit-host'); h.style.width = w + 'px'; h.innerHTML = html; }", [SAMPLE, width])
    return p


@pytest.mark.parametrize("width", [348, 1000])
def test_the_kit_has_no_sideways_scroll_at_dock_and_card_width(browser, server, width):
    p = _kit(browser, server, width)
    try:
        over = p.evaluate("""() => { const host = document.getElementById('kit-host'), r = host.getBoundingClientRect();
          return [...host.querySelectorAll('*')].filter((e) => e.getClientRects().length && e.getBoundingClientRect().right > r.right + 1 && !e.closest('.aud-ta-list'))
            .map((e) => e.className); }""")
        assert over == []
        assert p.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
    finally:
        p.context_.close()
    assert p.errors == [], p.errors


def test_the_sections_stack_in_the_dock_and_sit_side_by_side_in_a_wide_card(browser, server):
    dock = _kit(browser, server, 348)
    card = _kit(browser, server, 1000)
    try:
        tops = "[...document.querySelectorAll('#sample > .aud-sec')].map((e) => { const r = e.getBoundingClientRect(); return [Math.round(r.top), Math.round(r.left), Math.round(r.width)]; })"
        d, c = dock.evaluate(tops), card.evaluate(tops)
        assert len({t[0] for t in d[:4]}) == 4 and len({t[1] for t in d}) == 1                       # one column
        assert c[0][0] == c[1][0] and c[2][0] == c[3][0] and c[0][1] != c[1][1]                      # two per row
        assert c[4][2] > c[0][2] * 1.9                                                                # the wide section spans the row
    finally:
        dock.context_.close()
        card.context_.close()


@pytest.mark.parametrize("width", [348, 1000])
def test_every_kit_control_is_at_least_36px_tall(browser, server, width):
    p = _kit(browser, server, width)
    try:
        small = p.evaluate("""() => [...document.querySelectorAll('#sample button, #sample select, #sample input:not([type=checkbox])')]
          .filter((e) => e.getClientRects().length && e.getBoundingClientRect().height < 35.9).map((e) => e.className || e.id)""")
        assert small == []
        box = p.evaluate("(() => { const r = document.querySelector('.aud-check').getBoundingClientRect(); return r.height; })()")
        assert box >= 35.9                                                      # the checkbox row (the label is the target)
    finally:
        p.context_.close()


def test_every_kit_control_shows_a_focus_ring_for_the_keyboard(browser, server):
    p = _kit(browser, server, 1000)
    try:
        p.evaluate("document.getElementById('geo').focus()")
        n_controls = p.evaluate("[...document.querySelectorAll('#sample button, #sample select, #sample input')].filter((e) => e.getClientRects().length).length")
        bad = []
        for _ in range(n_controls + 2):
            info = p.evaluate("""(() => { const e = document.activeElement, s = getComputedStyle(e);
              return [e.tagName + '.' + e.className, s.outlineStyle, parseFloat(s.outlineWidth), !!e.closest('#sample')]; })()""")
            if info[3] and (info[1] == "none" or info[2] < 2):
                bad.append(info)
            p.keyboard.press("Tab")
        assert bad == []
    finally:
        p.context_.close()


@pytest.mark.parametrize("width", [348, 1000])
def test_kit_text_meets_aa_contrast(browser, server, width):
    p = _kit(browser, server, width)
    try:
        assert p.evaluate("contrastReport('#sample')") == []
    finally:
        p.context_.close()


def test_a_pressed_choice_does_not_rely_on_colour_alone(browser, server):
    p = _kit(browser, server, 1000)
    try:
        assert p.evaluate("getComputedStyle(document.querySelector('.aud-seg--wrap [aria-pressed=true]'), '::before').content") == '"✓"'
        assert p.evaluate("getComputedStyle(document.querySelector('.aud-quick [aria-pressed=true]'), '::before').content") == '"✓"'
        assert p.evaluate("getComputedStyle(document.querySelector('.aud-notice--error'), '::before').content") == '"!"'
    finally:
        p.context_.close()


def test_the_step_badges_number_themselves_from_a_counter(browser, server):
    p = _kit(browser, server, 1000)
    try:
        assert p.evaluate("getComputedStyle(document.querySelector('.aud-step'), '::before').content") == "counter(aud-step)"
        assert p.evaluate("getComputedStyle(document.querySelector('.aud-sec')).counterIncrement") == "aud-step 1"
        assert p.evaluate("getComputedStyle(document.querySelector('.aud-builder')).counterReset") == "aud-step 0"
    finally:
        p.context_.close()


# =============================================================================================== review fixes
# Each test below failed before its fix (checked against the previous segment-browser.js / .css / audience-kit.css).

TABS2 = [{"key": "a", "label": "Interests"}, {"key": "b", "label": "Behaviors"}]
TABS3 = TABS2 + [{"key": "c", "label": "Life events"}]


def live(page):
    return (page.text_content(".segb-live") or "").strip()


def wait_live(page, text):
    page.wait_for_function("t => document.querySelector('.segb-live').textContent === t", arg=text)


def sel_n(page):
    return page.inner_text(".segb-sel-n").strip()


def tag(page):
    return page.evaluate("document.activeElement.tagName")


# --------------------------------------------------------------------------------------------- Esc and the X never throw ticks away silently

def test_escape_in_a_search_box_with_text_clears_it_first_and_keeps_the_ticks(page):
    open_sb(page, n=500)
    page.click('.segb-row[data-id="s1"]')
    page.click('.segb-row[data-id="s2"]')
    page.fill(".segb-search-input", "segment 1")
    page.wait_for_function("document.querySelector('.segb-count').textContent.includes('match')")
    page.focus(".segb-search-input")
    page.keyboard.press("Escape")                                                # empties the box, like any search box
    assert page.locator(".segb-dialog").count() == 1 and page.input_value(".segb-search-input") == ""
    page.wait_for_function("document.querySelector('.segb-count').textContent === '500 segments'")
    assert selected_ids(page) == ["s1", "s2"] and "segb-search-input" in active(page) and page.locator(".segb-discard").is_hidden()
    wait_live(page, "Search cleared.")
    page.keyboard.press("Escape")                                                # now it is about the dialog, and two ticks are pending: it asks
    assert page.locator(".segb-dialog").count() == 1 and page.locator(".segb-discard").is_visible()
    page.keyboard.press("Escape")
    assert page.locator(".segb-dialog").count() == 0 and page.evaluate("window.__applied.length") == 0


def test_with_changes_pending_escape_asks_and_says_how_many_and_a_new_pick_asks_again(page):
    open_sb(page, chosen=["all:s1"])
    page.click('.segb-row[data-id="s1"]')                                        # un-tick a held one: one change
    page.keyboard.press("Escape")
    bar = page.locator(".segb-discard")
    assert page.locator(".segb-dialog").count() == 1 and bar.is_visible() and page.get_attribute(".segb-discard", "role") == "alert"
    assert page.inner_text(".segb-discard-text").strip() == "Close without applying your 1 change? Press Esc again to close, or keep picking."
    page.click('.segb-row[data-id="s2"]')                                        # the picks changed: the question is over, the next Esc asks afresh
    assert bar.is_hidden()
    page.keyboard.press("Escape")
    assert bar.is_visible() and "your 2 changes?" in page.inner_text(".segb-discard-text")
    page.click('.segb-row[data-id="s2"]')                                        # back to one change...
    page.click('.segb-row[data-id="s1"]')                                        # ...and to none: nothing left to lose
    assert bar.is_hidden()
    page.keyboard.press("Escape")
    assert page.locator(".segb-dialog").count() == 0 and page.evaluate("window.__applied.length") == 0


def test_the_x_with_changes_pending_asks_and_offers_both_ways_out(page):
    open_sb(page)
    page.click('.segb-row[data-id="s1"]')
    page.click(".segb-x")
    assert page.locator(".segb-dialog").count() == 1 and page.locator(".segb-discard").is_visible()
    assert "segb-keep" in active(page)                                           # the safe answer has the focus
    page.click(".segb-x")                                                        # a second click on the X is not "yes": the choice stays on screen
    assert page.locator(".segb-dialog").count() == 1 and "segb-keep" in active(page)
    page.click(".segb-keep")
    assert page.locator(".segb-discard").is_hidden() and selected_ids(page) == ["s1"] and in_dialog(page)
    page.click(".segb-x")
    page.click(".segb-discard-btn")
    assert page.locator(".segb-dialog").count() == 0 and page.evaluate("window.__applied.length") == 0
    assert page.evaluate("document.activeElement.id") == "opener"
    open_sb(page)
    page.click(".segb-x")                                                        # nothing ticked: the X just closes
    assert page.locator(".segb-dialog").count() == 0


def test_cancel_is_the_explicit_discard_and_needs_no_second_step(page):
    open_sb(page)
    page.click('.segb-row[data-id="s1"]')
    page.keyboard.press("Escape")                                                # the question is up...
    page.click(".segb-cancel")                                                   # ...and Cancel answers it
    assert page.locator(".segb-dialog").count() == 0 and page.evaluate("window.__applied.length") == 0
    open_sb(page)
    page.click('.segb-row[data-id="s1"]')
    page.click(".segb-cancel")
    assert page.locator(".segb-dialog").count() == 0 and page.evaluate("window.__applied.length") == 0


def test_escape_that_ends_an_ime_composition_does_not_close_or_ask(page):
    open_sb(page)
    page.click('.segb-row[data-id="s1"]')
    page.focus(".segb-search-input")
    page.evaluate("""() => document.querySelector('.segb-search-input').dispatchEvent(
      new KeyboardEvent('keydown', { key: 'Escape', isComposing: true, bubbles: true, cancelable: true }))""")
    assert page.locator(".segb-dialog").count() == 1 and page.locator(".segb-discard").is_hidden()


# --------------------------------------------------------------------------------------------- Clear reaches every tab

def test_clear_also_clears_the_picks_held_in_tabs_that_have_not_loaded(page):
    open_sb(page, n=20, tabs=TABS3, chosen=["b:b1", "c:c2"], chosenCount={"b": 1, "c": 1})
    assert sel_n(page) == "2 selected" and page.evaluate("window.__loads") == ["a"]
    assert page.is_enabled(".segb-clear")                                        # nothing is ticked on screen, yet Clear has work to do
    page.click(".segb-clear")
    page.wait_for_function("document.querySelector('.segb-clear').textContent === 'Clear'")        # (while it waits for b and c it says "Clearing…")
    assert page.evaluate("window.__loads") == ["a", "b", "c"] and sel_n(page) == "0 selected"
    assert page.inner_text(".segb-apply").strip() == "Remove 2" and page.inner_text(".segb-sel-delta").strip() == "2 to remove"
    assert page.is_disabled(".segb-clear") and "segb-search-input" in active(page)   # nothing left to clear: the focus is handed to the search box
    page.click(".segb-tab[data-tab='b']")
    page.wait_for_selector('.segb-row[data-id="b1"]')
    assert selected_ids(page) == []                                              # the tab comes in cleared, not re-ticked
    page.click(".segb-apply")
    r = page.evaluate("window.__applied[0]")
    assert r["added"] == [] and sorted((x["tab"], x["item"]["id"]) for x in r["removed"]) == [("b", "b1"), ("c", "c2")]


def test_while_clear_waits_for_the_other_tabs_apply_is_off_and_the_button_says_so(page):
    open_sb(page, n=10, tabs=TABS2, chosen=["b:b1"], chosenCount={"b": 1}, delay=250)
    page.click(".segb-clear")
    assert page.inner_text(".segb-clear").strip() == "Clearing…" and page.is_disabled(".segb-apply")
    page.wait_for_function("document.querySelector('.segb-clear').textContent === 'Clear'")
    assert sel_n(page) == "0 selected" and page.inner_text(".segb-apply").strip() == "Remove 1" and not page.is_disabled(".segb-apply")
    wait_live(page, "Selection cleared.")


def test_clear_without_chosencount_loads_every_tab_it_has_not_seen(page):
    """TikTok's pop-up has five tabs and no chosenCount: Clear must not leave the picks of the four it never opened."""
    open_sb(page, n=10, tabs=TABS3, chosen=["b:b1"])
    page.click('.segb-row[data-id="a1"]')
    page.click(".segb-clear")
    page.wait_for_function("window.__loads.length === 3 && document.querySelector('.segb-apply').textContent.trim() === 'Remove 1'")
    page.click(".segb-apply")
    r = page.evaluate("window.__applied[0]")
    assert r["added"] == [] and [(x["tab"], x["item"]["id"]) for x in r["removed"]] == [("b", "b1")]


def test_clear_goes_on_with_the_tabs_that_load_and_says_which_one_did_not(page):
    open_sb(page, n=10, tabs=TABS3, chosen=["b:b1", "c:c2"], chosenCount={"b": 1, "c": 1}, fail={"c": "Down."})
    page.click(".segb-clear")
    wait_live(page, "Selection cleared, except Life events, which couldn’t be loaded.")
    assert sel_n(page) == "1 selected" and page.is_enabled(".segb-clear")          # the footer still counts it, and Clear is still there for it
    assert page.inner_text(".segb-apply").strip() == "Remove 1"
    page.click(".segb-clear")                                                    # the second try loads it
    page.wait_for_function("document.querySelector('.segb-clear').textContent === 'Clear' && document.querySelector('.segb-clear').disabled")
    assert sel_n(page) == "0 selected" and page.inner_text(".segb-apply").strip() == "Remove 2"


def test_clear_never_drops_the_focus_to_the_page_behind(page):
    open_sb(page, n=20)
    page.click('.segb-row[data-id="s1"]')
    page.focus(".segb-clear")
    page.keyboard.press("Enter")
    assert selected_ids(page) == [] and page.is_disabled(".segb-clear")
    assert in_dialog(page) and tag(page) != "BODY" and "segb-search-input" in active(page)


# --------------------------------------------------------------------------------------------- Retry keeps the focus

def test_retry_keeps_the_keyboard_focus_inside_the_dialog(page):
    open_sb(page, wait=".segb-state--error", n=20, fail={"all": "The server is down."}, failTimes=2)
    page.focus(".segb-retry")
    page.keyboard.press("Enter")                                                 # the Retry button is rebuilt away: the focus must not fall to <body>
    assert in_dialog(page) and tag(page) != "BODY"
    page.wait_for_selector(".segb-state--error")                                 # failed again
    assert in_dialog(page) and tag(page) != "BODY"
    page.focus(".segb-retry")
    page.keyboard.press("Enter")
    page.wait_for_selector(".segb-row")
    assert in_dialog(page) and tag(page) != "BODY"
    page.keyboard.press("Tab")                                                   # and Tab continues from where it was, not from the X
    assert in_dialog(page)


# --------------------------------------------------------------------------------------------- the Apply button tells the truth

def test_a_custom_apply_label_says_remove_when_it_only_removes_and_apply_changes_for_a_mix(page):
    open_sb(page, n=20, applyLabel="Add to audience", chosen=["all:s1", "all:s2"])
    assert page.inner_text(".segb-apply").strip() == "Add to audience" and page.is_disabled(".segb-apply")
    page.click('.segb-row[data-id="s1"]')
    assert page.inner_text(".segb-apply").strip() == "Remove 1"
    page.click('.segb-row[data-id="s2"]')
    assert page.inner_text(".segb-apply").strip() == "Remove 2" and page.inner_text(".segb-sel-delta").strip() == "2 to remove"
    page.click('.segb-row[data-id="s5"]')
    assert page.inner_text(".segb-apply").strip() == "Apply changes (3)"
    page.click('.segb-row[data-id="s1"]')
    assert page.inner_text(".segb-apply").strip() == "Apply changes (2)"
    page.click('.segb-row[data-id="s2"]')
    assert page.inner_text(".segb-apply").strip() == "Add to audience (1)"        # only adding again: the caller's own words


# --------------------------------------------------------------------------------------------- the search covers what the columns show

def test_the_search_finds_a_value_that_a_text_column_shows(page):
    items = {"all": [{"id": "1", "name": "Portuguese", "code": "pt"}, {"id": "2", "name": "Chinese (Simplified)", "code": "zh"},
                     {"id": "3", "name": "Spanish", "code": "es"}, {"id": "4", "name": "Japanese", "code": "ja", "size": 7777}]}
    open_sb(page, items=items, columns=[{"key": "name", "label": "Language"}, {"key": "code", "label": "TikTok code"}, {"key": "size", "label": "Size", "numeric": True}])
    for query, found in (("pt", ["1"]), ("zh", ["2"]), ("ja", ["4"]), ("7777", [])):
        page.fill(".segb-search-input", query)
        if found:
            page.wait_for_function("exp => JSON.stringify([...document.querySelectorAll('.segb-row')].map((e) => e.dataset.id)) === JSON.stringify(exp)", arg=found)
        else:
            page.wait_for_selector(".segb-state--nomatch")                       # a number column is not searched: nothing matches 7777 although a row shows it
        assert found == [] or ids(page) == found, query
    page.fill(".segb-search-input", "pt")
    page.wait_for_function("document.querySelector('.segb-count').textContent === '1 of 4 segments match'")
    assert page.eval_on_selector_all(".segb-row .segb-mark", "els => els.map(e => e.textContent)") == ["pt"]    # highlighted where it is visible


# --------------------------------------------------------------------------------------------- focus ring, hover, contrast

def test_a_search_highlight_inside_a_selected_rows_description_meets_aa_contrast(page):
    open_sb(page, n=100, chosen=["all:s1"], describe=True)
    page.fill(".segb-search-input", "description line")
    page.wait_for_function("document.querySelector('.segb-count').textContent.includes('match')")
    assert page.get_attribute('.segb-row[data-id="s1"]', "aria-selected") == "true"
    assert page.locator('.segb-row[data-id="s1"] .segb-desc .segb-mark').count() >= 1
    assert page.evaluate("contrastReport('.segb-dialog')") == []


def test_the_dialogs_clear_link_reacts_to_the_mouse_only_while_it_can_be_used(page):
    open_sb(page, n=20)
    where = "(() => { const r = document.querySelector('.segb-clear').getBoundingClientRect(); return [r.x + r.width / 2, r.y + r.height / 2]; })()"
    bg = "getComputedStyle(document.querySelector('.segb-clear')).backgroundColor"
    page.mouse.move(*page.evaluate(where))
    assert page.is_disabled(".segb-clear") and page.evaluate(bg) == "rgba(0, 0, 0, 0)"      # nothing to clear: no reaction
    page.mouse.move(5, 5)
    page.click('.segb-row[data-id="s1"]')
    page.mouse.move(*page.evaluate(where))
    assert page.evaluate(bg) == "rgb(243, 229, 244)"                                         # --accent-soft, like the Clear links in the section heads


def test_the_forced_colors_mode_still_shows_the_focused_row_and_the_sort_arrows(browser, server):
    context = browser.new_context(viewport={"width": 1280, "height": 900}, forced_colors="active")
    p = context.new_page()
    errors: list[str] = []
    p.on("pageerror", lambda e: errors.append(str(e)))
    p.route("**/fonts.g*/**", lambda r: r.abort())
    try:
        p.goto(f"{server}/harness.html")
        open_sb(p, n=30)
        p.keyboard.press("ArrowDown")                                            # from the search box onto the first row
        ring = p.evaluate("(() => { const s = getComputedStyle(document.activeElement); return [document.activeElement.className, s.outlineStyle, parseFloat(s.outlineWidth)]; })()")
        assert "segb-row" in ring[0] and ring[1] != "none" and ring[2] >= 2, ring            # a row's inset shadows are gone in this mode: the outline is the indicator
        arrow = p.evaluate("""(() => { const ico = document.querySelector('.segb-th[aria-sort="ascending"] .segb-sort-ico'), s = getComputedStyle(ico, '::before');
          return [s.borderLeftWidth, s.backgroundColor, getComputedStyle(document.querySelector('.segb-dialog')).backgroundColor, s.clipPath]; })()""")
        assert arrow[0] == "0px" and arrow[1] != arrow[2] and arrow[3] != "none", arrow       # a painted triangle, not transparent borders (they turn into solid bars)
    finally:
        context.close()
    assert errors == [], errors


# --------------------------------------------------------------------------------------------- the opener's second click

def _opener_at(page, x, y, **cfg):
    page.evaluate("""([cfg, x, y]) => { const o = document.getElementById('opener'); o.style.cssText = 'position:fixed;left:' + x + 'px;top:' + y + 'px;z-index:1';
      o.addEventListener('click', () => window.openSB(cfg)); }""", [cfg, x, y])
    box = page.locator("#opener").bounding_box()
    return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2


def test_a_double_click_on_the_opener_does_not_close_the_dialog_it_just_opened(page):
    x, y = _opener_at(page, 30, 200, n=50)                                      # at 1280 wide the dialog starts at x=170: the opener is under the BACKDROP
    page.mouse.dblclick(x, y)
    page.wait_for_selector(".segb-row")
    assert page.locator(".segb-dialog").count() == 1 and sel_n(page) == "0 selected"


def test_a_double_click_on_the_opener_does_not_tick_the_row_under_the_pointer(page):
    x, y = _opener_at(page, 600, 400, n=50)                                     # under the dialog's list
    page.mouse.move(x, y)
    page.mouse.down(click_count=1)
    page.mouse.up(click_count=1)                                                # the first click opens the dialog...
    page.wait_for_selector(".segb-row")
    page.mouse.down(click_count=2)                                              # ...a moment later (the list has drawn) the second one lands on a row
    page.mouse.up(click_count=2)
    page.wait_for_timeout(100)
    assert page.locator(".segb-dialog").count() == 1 and sel_n(page) == "0 selected" and selected_ids(page) == []
    page.mouse.click(x, y)                                                      # a later, single click of the person's own is theirs: it ticks
    assert len(selected_ids(page)) == 1


def test_ordinary_clicks_right_after_opening_still_work(page):
    open_sb(page, n=50)
    page.click('.segb-row[data-id="s3"]')                                        # the guard is for the SECOND click of a double-click only
    assert selected_ids(page) == ["s3"]
    page.keyboard.press("Escape")
    page.keyboard.press("Escape")
    open_sb(page, n=50)
    page.mouse.click(5, 5)
    assert page.locator(".segb-dialog").count() == 0                            # a single click on the backdrop still closes an untouched dialog


# --------------------------------------------------------------------------------------------- selecting text is not ticking

def test_dragging_across_a_names_text_to_copy_it_does_not_tick_the_row(page):
    open_sb(page, n=30)
    box = page.locator('.segb-row[data-id="s2"] .segb-td-name .segb-cell').bounding_box()
    y = box["y"] + box["height"] / 2
    page.mouse.move(box["x"] + 1, y)
    page.mouse.down()
    page.mouse.move(box["x"] + box["width"] - 1, y, steps=8)
    page.mouse.up()
    assert "egment" in page.evaluate("String(getSelection())")                  # the text is selected, ready to copy...
    assert selected_ids(page) == [] and sel_n(page) == "0 selected"             # ...and the row was left alone
    page.click('.segb-row[data-id="s2"]')                                        # a plain click still ticks
    assert selected_ids(page) == ["s2"]


def test_a_double_click_on_a_word_and_the_checkbox_and_the_keyboard_keep_working(page):
    open_sb(page, n=30)
    page.dblclick('.segb-row[data-id="s3"] .segb-td-name .segb-cell')                          # selects a word: two clicks, so the row is as it was
    assert selected_ids(page) == []
    page.evaluate("getSelection().selectAllChildren(document.querySelector('.segb-row[data-id=\"s4\"] .segb-td-name .segb-cell'))")
    page.click('.segb-row[data-id="s4"] .segb-check')                            # the box is an explicit control: it ticks whatever is selected
    assert selected_ids(page) == ["s4"]
    page.focus('.segb-row[data-id="s5"]')
    page.keyboard.press("Space")
    assert selected_ids(page) == ["s4", "s5"]


# --------------------------------------------------------------------------------------------- long text

BROKEN_WORDS = """() => {
  const out = [];
  document.querySelectorAll('.segb-row .segb-td').forEach((td) => {
    const w = document.createTreeWalker(td, NodeFilter.SHOW_TEXT);
    for (let n; (n = w.nextNode());) {
      const re = /\\S+/g; let m;
      while ((m = re.exec(n.textContent))) {
        const r = document.createRange(); r.setStart(n, m.index); r.setEnd(n, m.index + m[0].length);
        if (r.getClientRects().length > 1) out.push(m[0]);              // one word on two lines: it was broken in the middle
      }
    }
  });
  return [...new Set(out)];
}"""

GROUPS = ["Entertainment & Media", "Sports & Recreation", "Telecommunications", "Financial Services", "Health & Wellness", "Transportation"]
DOCK_LIKE = [{"key": "name", "label": "Name"}, {"key": "group", "label": "Group"}, {"key": "rate", "label": "Incidence rate", "numeric": True}]


def _dock_like_items(n=30):
    return [{"id": f"i{k}", "name": f"Soccer fans who watch every weekend {k}", "group": GROUPS[k % 6], "rate": 0.1 + k / 100} for k in range(n)]


def test_cells_wrap_at_spaces_and_a_word_is_not_broken_when_it_can_fit(browser, server):
    p = _new_page(browser, server, 390, 780)
    try:
        open_sb(p, items={"all": _dock_like_items()}, columns=DOCK_LIKE)
        assert p.evaluate(BROKEN_WORDS) == []                                    # "Entertainme/nt", "Telecommu/nications" before
        assert p.evaluate("(() => { const b = document.querySelector('.segb-body'); return b.scrollWidth <= b.clientWidth; })()")
        assert p.evaluate("document.querySelector('.segb-table').classList.contains('is-tight')") is False
    finally:
        p.context_.close()
    assert p.errors == [], p.errors


def test_a_word_longer_than_the_sheet_still_breaks_so_the_list_never_scrolls_sideways(browser, server):
    items = _dock_like_items(20)
    items[3]["name"] = "#iloveyoumoreeverydayforeverandeverseriously"
    p = _new_page(browser, server, 390, 780)
    try:
        open_sb(p, items={"all": items}, columns=DOCK_LIKE)
        fits = "(() => { const b = document.querySelector('.segb-body'); return b.scrollWidth <= b.clientWidth; })()"
        assert p.evaluate("document.querySelector('.segb-table').classList.contains('is-tight')") is True and p.evaluate(fits)
        p.fill(".segb-search-input", "soccer")                                   # the long one is filtered out: whole words fit again
        p.wait_for_function("document.querySelector('.segb-count').textContent.includes('match')")
        assert p.evaluate("document.querySelector('.segb-table').classList.contains('is-tight')") is False and p.evaluate(BROKEN_WORDS) == []
    finally:
        p.context_.close()
    assert p.errors == [], p.errors


def test_a_long_unbroken_search_is_shortened_and_never_runs_off_the_dialog(browser, server):
    p = _new_page(browser, server, 390, 780)
    try:
        open_sb(p, n=30)
        query = ",".join(str(90210 + k) for k in range(30))                      # a pasted ZIP list: 179 characters, no spaces
        p.fill(".segb-search-input", query)
        p.wait_for_selector(".segb-state--nomatch")
        expect = "Nothing matches “" + query[:40] + "…”"
        assert p.inner_text(".segb-state-title").strip() == expect and count_text(p) == expect
        inside = """() => { const d = document.querySelector('.segb-dialog').getBoundingClientRect();
          return ['.segb-count', '.segb-state-title'].every((s) => { const r = document.querySelector(s).getBoundingClientRect(); return r.left >= d.left - 0.5 && r.right <= d.right + 0.5; })
            && document.querySelector('.segb-state').scrollWidth <= document.querySelector('.segb-state').clientWidth; }"""
        assert p.evaluate(inside)
        p.set_viewport_size({"width": 320, "height": 640})                      # shorter than the cap but still wider than the line: it wraps instead of clipping
        p.fill(".segb-search-input", "9" * 38)
        p.wait_for_function("document.querySelector('.segb-count').textContent.includes('999999')")
        assert p.evaluate(inside)
    finally:
        p.context_.close()
    assert p.errors == [], p.errors


# --------------------------------------------------------------------------------------------- the tab strip

def test_the_selected_tab_is_never_left_half_outside_the_strip_on_a_phone(browser, server):
    tabs = [{"key": k, "label": label} for k, label in (("a", "Interests"), ("b", "Hashtags"), ("c", "Video"), ("d", "Creators"), ("e", "Purchase intent"))]
    out = """() => { const t = document.querySelector('.segb-tab[aria-selected="true"]'), b = t.getBoundingClientRect(), r = document.querySelector('.segb-tabs').getBoundingClientRect();
      return [Math.round(r.left - b.left), Math.round(b.right - r.right)]; }"""
    p = _new_page(browser, server, 390, 780)
    try:
        open_sb(p, n=30, tabs=tabs)
        assert p.evaluate("(() => { const s = document.querySelector('.segb-tabs'); return s.scrollWidth > s.clientWidth + 40; })()")      # it does overflow here
        p.focus('.segb-tab[data-tab="a"]')
        for n in range(1, 5):
            p.keyboard.press("ArrowRight")
            p.wait_for_function("document.querySelector('.segb-tab[aria-selected=\"true\"] .segb-tab-count').textContent !== ''")      # its count badge has widened it
            left, right = p.evaluate(out)
            assert left <= 0 and right <= 0, (n, left, right)                    # clipped by 24 to 55 px before
        p.keyboard.press("Home")
        p.wait_for_function("document.querySelector('.segb-tab[aria-selected=\"true\"]').dataset.tab === 'a'")
        left, right = p.evaluate(out)
        assert left <= 0 and right <= 0
        p.evaluate("document.querySelector('.segb-tabs').scrollLeft = 0")
        edge = p.evaluate("""() => { const bar = document.querySelector('.segb-tabs').getBoundingClientRect();
          const t = [...document.querySelectorAll('.segb-tab')].find((e) => { const r = e.getBoundingClientRect(); return r.left < bar.right - 12 && r.right > bar.right + 12; });
          return [t.dataset.tab, bar.right - 6, t.getBoundingClientRect().y + 20]; }""")                  # the tab that runs off the right edge
        p.mouse.click(edge[1], edge[2])                                          # a tap on its visible edge
        p.wait_for_function("t => document.querySelector('.segb-tab[aria-selected=\"true\"]').dataset.tab === t", arg=edge[0])
        p.wait_for_function("document.querySelector('.segb-tab[aria-selected=\"true\"] .segb-tab-count').textContent !== ''")
        left, right = p.evaluate(out)
        assert left <= 0 and right <= 0, (edge, left, right)
    finally:
        p.context_.close()
    assert p.errors == [], p.errors


# --------------------------------------------------------------------------------------------- what the screen reader is told

def test_the_header_box_says_why_nothing_happened_when_another_tab_holds_every_slot(page):
    open_sb(page, n=20, tabs=TABS2, activeTab="b", max=2, chosenCount={"a": 2})
    assert sel_n(page) == "2 of 2 selected"
    page.focus(".segb-all")
    page.keyboard.press("Space")
    wait_live(page, "Nothing was added. You’ve reached the limit of 2. Untick one to add another.")   # not "Cleared the selection shown."
    assert sel_n(page) == "2 of 2 selected" and selected_ids(page) == []


def test_a_message_that_repeats_is_announced_again(page):
    open_sb(page, n=20, max=1, chosen=["all:s1"])
    page.evaluate("""() => { window.__liveLog = []; const l = document.querySelector('.segb-live');
      new MutationObserver(() => window.__liveLog.push(l.textContent)).observe(l, { childList: true, characterData: true, subtree: true }); }""")
    for _ in range(2):
        page.focus('.segb-row[data-id="s5"]')
        page.keyboard.press("Space")                                             # a blocked row: the same limit message twice
        page.wait_for_timeout(150)
    log = page.evaluate("window.__liveLog")
    text = "You’ve reached the limit of 1. Untick one to add another."
    assert log.count(text) == 2 and "" in log                                    # the region was emptied in between, so a screen reader reads it a second time


# --------------------------------------------------------------------------------------------- maxPerTab

def blocked(page):
    return page.locator('.segb-row[aria-disabled="true"]').count()


def test_max_per_tab_disables_only_the_tab_that_is_full(page):
    open_sb(page, n=10, tabs=TABS2, maxPerTab=2)
    page.click('.segb-row[data-id="a1"]')
    assert blocked(page) == 0 and page.locator(".segb-limit").is_hidden()
    page.click('.segb-row[data-id="a2"]')
    assert blocked(page) == 8 and page.is_disabled('.segb-row[data-id="a9"] .segb-check')
    assert page.inner_text(".segb-limit").strip() == "You’ve reached the limit of 2 for Interests. Untick one to add another."
    page.click('.segb-row[data-id="a9"]', force=True)                            # (Playwright will not click what is aria-disabled; a person can)
    assert selected_ids(page) == ["a1", "a2"]
    page.click('.segb-tab[data-tab="b"]')                                        # the other tab is a different story
    page.wait_for_selector('.segb-row[data-id="b1"]')
    assert blocked(page) == 0 and page.locator(".segb-limit").is_hidden()
    page.click('.segb-row[data-id="b1"]')
    page.click('.segb-row[data-id="b2"]')
    assert blocked(page) == 8 and "for Behaviors" in page.inner_text(".segb-limit") and sel_n(page) == "4 selected"
    page.click('.segb-tab[data-tab="a"]')
    page.wait_for_selector('.segb-row[data-id="a1"]')
    assert blocked(page) == 8 and "for Interests" in page.inner_text(".segb-limit")        # coming back: still full
    page.click('.segb-row[data-id="a1"]')                                        # a ticked row can always be unticked, and frees a slot
    assert blocked(page) == 0 and page.locator(".segb-limit").is_hidden()
    page.click('.segb-row[data-id="a7"]')
    assert sorted(selected_ids(page)) == ["a2", "a7"]


def test_max_per_tab_counts_what_the_caller_holds_in_a_tab_that_has_not_loaded(page):
    open_sb(page, n=10, tabs=TABS2, maxPerTab=3, chosenCount={"b": 3}, chosen=["b:b1", "b:b2", "b:b3"])
    assert blocked(page) == 0 and page.evaluate("window.__loads") == ["a"]       # tab a is free, and b was not loaded just to count it
    page.click('.segb-tab[data-tab="b"]')
    page.wait_for_selector('.segb-row[data-id="b1"]')
    assert selected_ids(page) == ["b1", "b2", "b3"] and blocked(page) == 7 and page.locator(".segb-limit").is_visible()


def test_max_per_tab_counts_picks_the_list_does_not_contain(page):
    open_sb(page, n=10, tabs=TABS2, activeTab="b", maxPerTab=3, chosenCount={"b": 3}, chosen=["b:b1"])      # holds 3 in b, one of them in the list
    assert selected_ids(page) == ["b1"] and blocked(page) == 9
    page.click('.segb-row[data-id="b1"]')                                        # untick the one it can show: a slot opens
    assert blocked(page) == 0 and selected_ids(page) == []
    page.click('.segb-row[data-id="b2"]')
    assert blocked(page) == 9                                                    # 2 unseen + 1 = 3 again


def test_max_per_tab_and_max_compose_and_each_row_follows_the_stricter(page):
    open_sb(page, n=10, tabs=TABS2, max=3, maxPerTab=2)
    page.click('.segb-row[data-id="a1"]')
    page.click('.segb-row[data-id="a2"]')
    assert blocked(page) == 8 and "for Interests" in page.inner_text(".segb-limit")        # the per-tab limit bites first
    page.click('.segb-tab[data-tab="b"]')
    page.wait_for_selector('.segb-row[data-id="b1"]')
    assert blocked(page) == 0 and sel_n(page) == "2 of 3 selected"
    page.click('.segb-row[data-id="b1"]')                                        # 3 of 3: now the global limit bites everywhere
    assert blocked(page) == 9 and page.inner_text(".segb-limit").strip() == "You’ve reached the limit of 3. Untick one to add another."
    page.click('.segb-tab[data-tab="a"]')
    page.wait_for_selector('.segb-row[data-id="a1"]')
    assert blocked(page) == 8 and "limit of 3." in page.inner_text(".segb-limit")             # (a1 and a2 are ticked, the others are blocked)
    page.click('.segb-tab[data-tab="b"]')
    page.wait_for_selector('.segb-row[data-id="b1"]')
    page.click('.segb-row[data-id="b1"]')                                        # frees the global slot: b is open again, a is still full
    assert blocked(page) == 0
    page.click('.segb-tab[data-tab="a"]')
    page.wait_for_selector('.segb-row[data-id="a1"]')
    assert blocked(page) == 8 and "for Interests" in page.inner_text(".segb-limit")


def test_max_note_supplies_the_per_tab_wording(page):
    open_sb(page, n=10, tabs=TABS2, maxPerTab=1, maxNoteText={"a": "TikTok allows at most 1 of one kind.", "b": "Hashtags are limited to 1."})
    page.click('.segb-row[data-id="a1"]')
    assert page.inner_text(".segb-limit").strip() == "TikTok allows at most 1 of one kind."
    page.click('.segb-tab[data-tab="b"]')
    page.wait_for_selector('.segb-row[data-id="b1"]')
    page.click('.segb-row[data-id="b1"]')
    assert page.inner_text(".segb-limit").strip() == "Hashtags are limited to 1."


def test_the_header_box_stops_at_the_per_tab_limit_and_says_so(page):
    open_sb(page, n=10, tabs=TABS2, maxPerTab=3)
    page.click(".segb-all")
    assert sel_n(page) == "3 selected" and blocked(page) == 7
    wait_live(page, "Selected 3 of 10 shown. You’ve reached the limit of 3 for Interests. Untick one to add another.")
    page.click(".segb-tab[data-tab='b']")
    page.wait_for_selector('.segb-row[data-id="b1"]')
    page.click(".segb-all")
    assert sel_n(page) == "6 selected" and blocked(page) == 7                    # the other tab got its own three


def test_max_per_tab_without_tabs_is_a_plain_limit(page):
    open_sb(page, n=10, maxPerTab=2)
    page.click('.segb-row[data-id="s1"]')
    page.click('.segb-row[data-id="s2"]')
    assert blocked(page) == 8 and page.inner_text(".segb-limit").strip() == "You’ve reached the limit of 2. Untick one to add another."


# --------------------------------------------------------------------------------------------- the kit: hidden, plain quick picks, pressed + hover

def test_hidden_wins_over_every_kit_class_that_sets_display(browser, server):
    p = _kit(browser, server, 1000)
    try:
        p.add_style_tag(content=".tt-panel .aud-notice, .tt-panel .aud-quick, .tt-panel .aud-chips, .tt-panel .aud-actions { display: flex; }")      # a panel rule outside the kit
        classes = ["aud-notice", "aud-quick", "aud-chips", "aud-chip", "aud-actions", "aud-empty", "aud-skeleton", "aud-seg", "aud-range", "aud-browse", "aud-field",
                   "aud-sec", "aud-sec-body", "aud-count", "aud-clear", "aud-btn", "aud-link", "aud-ta-list", "aud-ta-foot", "aud-input", "aud-check", "aud-row"]
        shown = p.evaluate("""(classes) => {
          const host = document.querySelector('#sample'), panel = document.createElement('div'); panel.className = 'tt-panel'; document.body.append(panel);
          const out = [];
          classes.forEach((c) => {
            [host, panel].forEach((where) => {
              const el = document.createElement('div'); el.className = 'x ' + c; el.textContent = 'x'; el.hidden = true; where.append(el);
              if (getComputedStyle(el).display !== 'none') out.push(c + ' in ' + where.className);
              el.hidden = false;
              if (getComputedStyle(el).display === 'none') out.push(c + ' never shows in ' + where.className);       // and the rule lets go when `hidden` goes
            });
          });
          const plain = document.createElement('p'); plain.hidden = true; host.append(plain);
          if (getComputedStyle(plain).display !== 'none') out.push('plain element in the builder');
          return out; }""", classes)
        assert shown == []
    finally:
        p.context_.close()
    assert p.errors == [], p.errors


def test_plain_quick_picks_drop_the_plus_but_a_pressed_one_keeps_its_tick(browser, server):
    p = _kit(browser, server, 1000)
    try:
        p.evaluate("""() => { const h = document.createElement('div'); h.id = 'qq'; document.body.append(h);
          h.innerHTML = '<div class="aud-quick" id="q1"><button type="button">One</button><button type="button" aria-pressed="true">Two</button></div>'
            + '<div class="aud-quick aud-quick--plain" id="q2"><button type="button">One</button><button type="button" aria-pressed="true">Two</button></div>'
            + '<div class="aud-range"><div class="aud-quick" id="q3"><button type="button">18–24</button></div></div>'; }""")
        before = lambda sel: p.evaluate("(s) => getComputedStyle(document.querySelector(s), '::before').content", sel)
        assert before("#q1 button:first-child") == '"+"' and before("#q1 button:last-child") == '"✓"'                    # the default is unchanged
        assert before("#q2 button:first-child") == "none" and before("#q2 button:last-child") == '"✓"'                    # plain: no plus, still a tick when pressed
        assert before("#q3 button") == "none"                                                                            # the age presets never had one
        assert p.evaluate("getComputedStyle(document.querySelector('#q2 button:first-child'), '::before').marginRight") == "0px"
    finally:
        p.context_.close()


PILLS = """
<div id="v" class="aud-builder">
  <div class="aud-seg" id="v-seg"><button type="button" aria-pressed="false">A</button><button type="button" aria-pressed="true">B</button><button type="button" aria-pressed="true" disabled>C</button><button type="button" disabled>D</button></div>
  <div class="aud-seg aud-seg--wrap" id="v-wrap"><button type="button" aria-pressed="false">A</button><button type="button" aria-pressed="true">B</button><button type="button" aria-pressed="true" disabled>C</button><button type="button" disabled>D</button></div>
  <div class="aud-quick" id="v-quick"><button type="button" aria-pressed="false">A</button><button type="button" aria-pressed="true">B</button><button type="button" aria-pressed="true" disabled>C</button><button type="button" disabled>D</button></div>
  <div class="aud-quick aud-quick--plain" id="v-plain"><button type="button" aria-pressed="false">A</button><button type="button" aria-pressed="true">B</button><button type="button" aria-pressed="true" disabled>C</button><button type="button" disabled>D</button></div>
</div>"""
LOOK = """(el) => { const s = getComputedStyle(el); return [s.color, s.backgroundColor, s.borderTopColor, s.backgroundImage, s.opacity]; }"""


def _ratio(fg: str, bg: str) -> float:
    def parse(c: str):
        return [float(x) for x in c[c.index("(") + 1:c.index(")")].replace("/", ",").replace(" ", "").split(",")[:3]]
    def lum(rgb):
        lin = lambda v: v / 12.92 if v / 255 <= 0.03928 else None
        return sum(w * ((v / 255) / 12.92 if v / 255 <= 0.03928 else (((v / 255) + 0.055) / 1.055) ** 2.4) for w, v in zip((0.2126, 0.7152, 0.0722), rgb))
    a, b = lum(parse(fg)), lum(parse(bg))
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)


@pytest.mark.parametrize("variant", ["v-seg", "v-wrap", "v-quick", "v-plain"])
def test_a_pressed_pill_stays_readable_under_the_mouse_and_a_disabled_one_ignores_it(browser, server, variant):
    """The pressed `.aud-seg--wrap` pill under the pointer turned white-on-white (hover beat pressed, the text stayed white)."""
    p = _kit(browser, server, 1000)
    try:
        p.evaluate("(html) => document.getElementById('kit-host').insertAdjacentHTML('beforeend', html)", PILLS)
        sel = f"#{variant} button"
        centre = lambda i: p.evaluate("([s, i]) => { const e = document.querySelectorAll(s)[i]; e.scrollIntoView({ block: 'center' }); const r = e.getBoundingClientRect(); return [r.x + r.width / 2, r.y + r.height / 2]; }", [sel, i])
        look = lambda i: p.evaluate("([s, i]) => (" + LOOK + ")(document.querySelectorAll(s)[i])", [sel, i])

        def hover(i):                                                                                                      # the mouse on button i, and the browser has applied :hover
            p.mouse.move(*centre(i))
            p.wait_for_function("([s, i]) => document.querySelectorAll(s)[i].matches(':hover')", arg=[sel, i], timeout=3000)

        p.mouse.move(2, 2)
        rest = [look(i) for i in range(4)]
        assert _ratio(rest[1][0], rest[1][1]) >= 4.5                                                                       # pressed, at rest
        hover(1)                                                                                                          # the mouse on the pressed pill
        hover_look = look(1)
        assert _ratio(hover_look[0], hover_look[1]) >= 4.5 and hover_look[0] != hover_look[1], (variant, hover_look)         # still readable: not white on white
        assert hover_look[1] == rest[1][1] and hover_look[2] == rest[1][2]                                                # still the purple pill (a shade deeper laid on top)
        hover(0)                                                                                                          # the mouse on an unpressed one: it does react
        assert look(0) != rest[0], (variant, look(0))
        for i in (2, 3):                                                                                                  # disabled, pressed or not: the mouse changes nothing
            p.mouse.move(*centre(i))
            p.wait_for_timeout(80)
            assert look(i) == rest[i], (variant, i, look(i), rest[i])
        p.mouse.move(2, 2)
        p.focus(f"{sel}:nth-child(1)")                                                                                    # keyboard focus on a pressed pill that is also hovered
        p.keyboard.press("Tab")
        hover(1)
        ring = p.evaluate("(() => { const s = getComputedStyle(document.activeElement); return [document.activeElement.textContent, s.outlineStyle, parseFloat(s.outlineWidth)]; })()")
        assert ring[0] == "B" and ring[1] != "none" and ring[2] >= 2, ring
    finally:
        p.context_.close()
    assert p.errors == [], p.errors
