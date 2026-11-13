"""ProposalHistory (app/static/proposal-history.js + .css) in a REAL browser (headless Chromium through Playwright): the things a stub page cannot
show - where the focus goes, what Escape leaves behind, whether a client called `<img onerror=...>` runs, whether a slow answer can overwrite a
quick one, whether the page behind can still scroll, whether the layout holds from a 360px phone to a 1440px admin screen.

The page is a small harness (not the app): the REAL styles.css, proposal-history.css and proposal-history.js, an opener button, a programmable
fake API in place of window.fetch (filters, sorts, pages and counts like the real list endpoint does, plus delays, failures and a request log),
a stubbed clipboard, and `window.openPH(cfg)` / `window.mountPH(cfg)`. Skipped when Playwright or its Chromium is absent.
The two real pages are wired in a later step; their own tests (planner and admin) live in test_proposal_history_planner_dom.py / ..._admin_dom.py."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from tests import dom_support as dom

playwright_sync = pytest.importorskip("playwright.sync_api")

NOW = datetime(2026, 11, 11, 15, 30, tzinfo=timezone.utc)
ME = "planner@entravision.com"
CHROME_MAC = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"


def ago(**kw) -> str:
    return (NOW - timedelta(**kw)).isoformat()


def row(i: int, client: str, status: str = "generated", updated: str | None = None, **kw) -> dict:
    """One list row as GET /api/my-proposals returns it (design B 12.1), with sensible defaults."""
    draft = status == "draft"
    r = {
        "proposal_id": f"pid{i:03d}", "client_name": client, "seller_email": "ae@entravision.com", "requested_by": "Jane AE", "notion_id": "",
        "proposal_title": "", "filename": "", "generated_at": "" if draft else (updated or ago(hours=i)), "requester_ip": f"10.0.0.{i % 250}",
        "requester_user_agent": CHROME_MAC, "total_net": None if draft else 10000.0 * i, "total_gross": None, "tabs_built": [] if draft else ["Proposal A"],
        "status": status, "updated_at": updated or ago(hours=i), "created_by_email": ME,
        "flight_start": "2027-01-05", "flight_end": "2027-03-31", "flight_text": "", "line_count": 3, "time_unit": "month",
        "wizard_step": 4 if draft else 8, "duplicated_from": None, "can_reopen": True, "option_count": 1,
    }
    r.update(kw)
    return r


def standard_rows() -> list[dict]:
    """Fourteen rows that cover every shape the list has to draw (the same cast as design B's prototype)."""
    return [
        row(1, "Fronteras Del Norte", "draft", ago(minutes=7), notion_id="EVC-48213", proposal_title="EVC-48213_FronterasDelNorte_Proposal_20261105", line_count=6, wizard_step=4),
        row(2, "Banco Popular de Puerto Rico & Subsidiaries (Consumer Banking Division)", "generated", ago(hours=3), notion_id="EVC-48102",
            proposal_title="EVC-48102_BancoPopularDePuertoRico_Proposal_20261104", total_net=412500.0, line_count=9, time_unit="week", flight_start="2026-12-01", flight_end="2027-02-28",
            duplicated_from={"proposal_id": "x", "client_name": "Banco Popular (2025 flight)", "notion_id": "EVC-40110", "title": "t"}, tabs_built=["Proposal A", "Proposal A (Gross)"]),
        row(3, "Acme HVAC", "generated", ago(days=1, hours=2), notion_id="EVC-48055", proposal_title="EVC-48055_AcmeHVAC_Proposal_20261103", total_net=86400.0, line_count=3,
            flight_start="2026-11-16", flight_end="2027-01-31"),
        row(4, "", "draft", ago(days=1, hours=5), line_count=0, wizard_step=2, flight_start=None, flight_end=None),
        row(5, "Toyota Dealers Association of Southern California", "generated", ago(days=3), notion_id="EVC-47990", proposal_title="Toyota 2027 Plan",
            total_net=1250000.0, line_count=14, time_unit="quarter", flight_start="2027-01-01", flight_end="2027-12-31"),
        row(6, "Casa Bonita", "draft", ago(days=5), notion_id="EVC-47888", proposal_title="EVC-47888_CasaBonita_Proposal_20261030", line_count=2, wizard_step=5,
            flight_start="2026-11-20", flight_end="2026-12-31", duplicated_from={"proposal_id": "y", "client_name": "Casa Grande", "notion_id": "EVC-47001"}),
        row(7, "Hostile & Sons", "generated", ago(days=6), proposal_title="EVC-47801_Hostile_Proposal_20261029", notion_id="EVC-47801", total_net=12000.0, line_count=1,
            time_unit="full_flight", flight_start="2027-02-01", flight_end="2027-02-28"),
        row(8, "Luna Dental Group", "generated", ago(days=14), total_net=54000.0, line_count=4, flight_start="2026-10-05", flight_end="2026-12-27"),
        row(9, "Supermercados El Rey", "generated", ago(days=21), total_net=0.0, line_count=0, flight_start="2026-09-01", flight_end="2026-11-30"),
        row(10, "Univision Retail Co-op", "draft", ago(days=27), notion_id="EVC-47510", line_count=5, wizard_step=3, flight_start="2027-04-01", flight_end="2027-06-30"),
        row(11, "Pacific Coast Roofing", "generated", ago(days=40), total_net=28750.0, line_count=2, flight_start="2026-10-12", flight_end="2026-11-30"),
        row(12, "Metro PCS", "generated", ago(days=75), total_net=220000.0, line_count=None, time_unit=None, wizard_step=None, flight_start=None, flight_end=None, can_reopen=False),
        row(13, "Telemundo Local Auto Group", "generated", ago(days=82), total_net=640000.0, line_count=11, flight_start="2026-09-01", flight_end="2026-12-31"),
        row(14, "Rivera & Sons Landscaping", "generated", ago(days=88), total_net=18400.0, line_count=1, flight_start="2026-08-20", flight_end="2026-09-30"),
    ]


def many_rows(n: int) -> list[dict]:
    """n rows, newest first by `updated_at` (row 1 is the newest); every 4th is a draft."""
    return [row(i, f"Client {i:03d}", "draft" if i % 4 == 0 else "generated", ago(minutes=i * 5), notion_id=f"EVC-{40000 + i}", proposal_title=f"EVC-{40000 + i}_Client{i:03d}")
            for i in range(1, n + 1)]


HARNESS_JS = r"""
(function () {
  const NATURAL = { updated: "desc", client: "asc", value: "desc" };
  const api = window.__api = {
    data: [], log: [], now: Date.parse("2026-11-11T15:30:00Z"), delay: 0, fail: null, facets: true, raw: null, html: false, ignoreAbort: false,
    deleteStatus: 200, me: { email: "planner@entravision.com", is_admin: true },
  };
  const body = (data, status) => new Response(JSON.stringify(data), { status: status || 200, headers: { "Content-Type": "application/json" } });
  const words = (q) => String(q || "").toLowerCase().split(/\s+/).filter(Boolean);
  const matches = (r, ws) => { const hay = [r.client_name, r.seller_email, r.requested_by, r.notion_id, r.proposal_title].join("\n").toLowerCase(); return ws.every((w) => hay.indexOf(w) >= 0); };
  function sorted(rows, sort, dir) {
    const sign = dir === "asc" ? 1 : -1;
    const key = (r) => sort === "client" ? ((r.client_name || "").trim().toLowerCase() || null) : sort === "value" ? (typeof r.total_net === "number" ? r.total_net : null) : r.updated_at;
    return rows.slice().sort((a, b) => {
      const ka = key(a), kb = key(b);
      if ((ka === null) !== (kb === null)) return ka === null ? 1 : -1;              // blanks and drafts last in BOTH directions
      if (ka !== kb) return (ka < kb ? -1 : 1) * sign;
      if (a.updated_at !== b.updated_at) return a.updated_at < b.updated_at ? 1 : -1;
      return a.proposal_id < b.proposal_id ? 1 : -1;
    });
  }
  function list(u, admin) {
    const q = u.searchParams;
    const size = +q.get("page_size") || 10, page = +q.get("page") || 1;
    const sort = q.get("sort") || "updated", dir = q.get("dir") || NATURAL[sort], status = q.get("status") || "all";
    let rows = api.data.slice();
    if (admin && q.get("mine") === "true") rows = rows.filter((r) => String(r.created_by_email || "").toLowerCase() === api.me.email.toLowerCase());
    rows = rows.filter((r) => matches(r, words(q.get("search"))));
    const facets = { all: rows.length, draft: rows.filter((r) => r.status === "draft").length, generated: rows.filter((r) => r.status !== "draft").length };
    if (status === "draft") rows = rows.filter((r) => r.status === "draft"); else if (status === "generated") rows = rows.filter((r) => r.status !== "draft");
    rows = sorted(rows, sort, dir);
    const chunk = rows.slice((page - 1) * size, page * size);
    return { proposals: chunk, count: chunk.length, total_count: chunk.length ? rows.length : 0, page, page_size: size,
             total_pages: Math.max(1, Math.ceil(rows.length / size)), facets: api.facets ? facets : null, applied: { status, sort, dir } };
  }
  window.fetch = async function (url, init) {
    init = init || {};
    const u = new URL(String(url), location.href), method = (init.method || "GET").toUpperCase();
    const req = { method, url: u.pathname + u.search, path: u.pathname, params: Object.fromEntries(u.searchParams), aborted: false, status: null };
    api.log.push(req);
    const wait = typeof api.delay === "function" ? api.delay(req) : api.delay;
    await new Promise((resolve, reject) => {
      const t = setTimeout(resolve, wait || 0);
      const abort = () => { clearTimeout(t); req.aborted = true; reject(new DOMException("The operation was aborted.", "AbortError")); };
      if (init.signal && !api.ignoreAbort) { if (init.signal.aborted) abort(); else init.signal.addEventListener("abort", abort); }    // (ignoreAbort: a transport that never cancels)
    });
    const f = api.fail ? api.fail(req) : null;
    if (f === "offline") throw new TypeError("Failed to fetch");
    if (f) { req.status = f.status; return body(f.body || { detail: "SECRET-SERVER-DETAIL " + f.status }, f.status); }
    let out;
    if (api.html && method === "GET" && u.pathname === "/api/my-proposals") out = new Response("<html>Please sign in</html>", { status: 200, headers: { "Content-Type": "text/html" } });   // a login page where JSON was expected
    else if (api.raw && method === "GET" && u.pathname === "/api/my-proposals") out = body(api.raw);              // an answer of any shape, for the malformed-data tests
    else if (method === "GET" && u.pathname === "/api/my-proposals") out = body(list(u, false));
    else if (method === "GET" && u.pathname === "/api/admin/proposals") out = body(list(u, true));
    else if (method === "GET" && u.pathname === "/api/me") out = body(api.me);
    else if (method === "DELETE" && u.pathname.indexOf("/api/proposal/") === 0) {
      const id = decodeURIComponent(u.pathname.slice("/api/proposal/".length));
      const code = typeof api.deleteStatus === "function" ? api.deleteStatus(id) : api.deleteStatus;
      if (code === 200) { api.data = api.data.filter((r) => r.proposal_id !== id); out = body({ ok: true }); }
      else out = body({ detail: "SECRET-SERVER-DETAIL " + code }, code);
    } else out = body({ detail: "no such route" }, 404);
    req.status = out.status;
    return out;
  };

  // The clipboard and the old copy command, recorded.
  window.__clip = { writes: [], mode: "ok" };
  Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: (t) => { __clip.writes.push(t); return __clip.mode === "reject" ? Promise.reject(new Error("denied")) : Promise.resolve(); } } });
  window.__exec = { calls: 0, ok: true, text: null };
  document.execCommand = function (cmd) { __exec.calls++; const ta = document.querySelector("textarea[readonly]"); __exec.text = ta ? ta.value : null; return __exec.ok; };

  // A keydown listener on the document in the BUBBLE phase, like the Constanza dock's Escape handler: it must never see what the dialog uses.
  window.__docKeys = 0;
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") window.__docKeys++; });

  // The relative-time refresh is a setInterval: record it so a test can run it without waiting a minute.
  window.__intervals = [];
  const realSetInterval = window.setInterval.bind(window);
  window.setInterval = function (fn, ms) { const id = realSetInterval.apply(window, arguments); window.__intervals.push({ fn, ms, id }); return id; };
  window.__cleared = [];
  const realClearInterval = window.clearInterval.bind(window);
  window.clearInterval = function (id) { window.__cleared.push(id); return realClearInterval(id); };

  window.__dups = [];
  window.openPH = function (cfg) {
    cfg = cfg || {};
    window.__cfg = cfg;
    window.__dups = [];
    const opts = Object.assign({ now: () => api.now, returnFocus: document.getElementById("opener") }, cfg.opts || {});
    if (cfg.dup !== false) opts.onDuplicate = (ctx) => {
      window.__dups.push({ id: ctx.proposal.proposal_id, trigger: ctx.trigger, keys: Object.keys(ctx).sort().join(","), proposal: ctx.proposal }); window.__lastDup = ctx;
      if (typeof window.__dupHook === "function") window.__dupHook(ctx);                                    // what a real host does next (a test sets it)
    };
    window.__closed = 0;
    window.__fj = [];
    if (cfg.fetchJson) opts.fetchJson = (url) => { window.__fj.push(url); return Promise.resolve({ proposals: [{ proposal_id: "fjpid", client_name: "From the seam", status: "generated", updated_at: "2026-11-11T15:00:00+00:00", total_net: 1, can_reopen: true }],
      total_count: 1, facets: { all: 1, draft: 0, generated: 1 }, applied: { status: "all", sort: "updated", dir: "desc" } }); };
    if (cfg.onClose) opts.onClose = () => { window.__closed++; };
    if (cfg.target) opts.linkTarget = () => cfg.target;
    if (cfg.canDelete === "none") opts.canDelete = () => false;
    if (cfg.emptyAction) opts.emptyAction = { label: "Paste a Notion request", run: () => { window.__emptyRan = (window.__emptyRan || 0) + 1; document.getElementById("other").focus(); } };
    window.__handle = ProposalHistory.openDialog(opts);
    return !!window.__handle;
  };
  window.mountPH = function (cfg) {
    cfg = cfg || {};
    const opts = Object.assign({ now: () => api.now, surface: "admin", endpoint: "/api/admin/proposals", pageSize: 50,
      scopes: [{ key: "mine", label: "My proposals" }, { key: "all", label: "All proposals" }], scope: "mine", params: (scope) => ({ mine: scope === "mine" }), linkTarget: () => "_blank" }, cfg.opts || {});
    if (cfg.dup !== false) opts.onDuplicate = (ctx) => { window.__dups.push({ id: ctx.proposal.proposal_id, trigger: ctx.trigger }); };
    window.__ctl = ProposalHistory.mount(document.getElementById("host"), opts);
    return true;
  };
  // A throwaway form built from the form primitives, in a top shell: what the duplicate dialog will look like. `errors` shows the error states.
  window.buildForm = function (errors) {
    const el = (tag, props, ...kids) => { const n = document.createElement(tag); Object.entries(props || {}).forEach(([k, v]) => n.setAttribute(k, v)); kids.forEach((k) => n.append(k)); return n; };
    const field = (id, label, o) => { o = o || {}; return el('div', { class: 'ph-field' + (o.wide ? ' ph-field--wide' : '') }, el('label', { class: 'ph-flabel', for: id }, label, o.req ? el('span', { class: 'ph-req' }, ' *') : ''),
      el('input', Object.assign({ class: 'ph-input', id, type: o.type || 'text' }, o.err ? { 'aria-invalid': 'true', 'aria-describedby': id + '-e' } : {})), o.err ? el('p', { class: 'ph-ferror', id: id + '-e' }, o.err) : '', o.hint ? el('p', { class: 'ph-hint' }, o.hint) : ''); };
    const sh = ProposalHistory.shell({ title: 'Duplicate for a new client', subtitle: 'Starts from Acme HVAC (EVC-48055).', top: true, narrow: true });
    sh.body.append(el('form', { class: 'ph-form', novalidate: '', id: 'ff' }, errors ? el('div', { class: 'ph-form-alert', role: 'alert' }, 'We couldn\u2019t create the copy. Nothing was saved.') : '',
      el('div', { class: 'ph-form-grid' }, field('f1', 'Client name', { req: true, wide: true, err: errors ? 'Enter the new client\u2019s name.' : '' }), field('f2', 'Notion request ID', { hint: 'Digits only, like 48213.' }),
        field('f5', 'Salesperson email', { type: 'email' }), field('f3', 'Flight start', { req: true, type: 'date' }), field('f4', 'Flight end', { req: true, type: 'date', err: errors ? 'The end date can\u2019t be before the start date.' : '' })),
      el('label', { class: 'ph-check' }, el('input', { type: 'checkbox' }), 'Keep the avails estimates'),
      el('details', { class: 'ph-details' }, el('summary', {}, 'More details'), el('button', { type: 'button', class: 'ph-linkbtn' }, 'Same as the original: Initiative Media, 15%'))),
      el('div', { class: 'ph-form-foot' }, el('button', { type: 'button', class: 'ph-btn', id: 'cancel' }, 'Cancel'), el('button', { type: 'submit', class: 'ph-btn ph-btn--primary', form: 'ff' }, 'Create copy')));
    return sh;
  };
  /*CONTRAST*/
})();
"""

HARNESS = (
    """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>harness</title>
<link rel="stylesheet" href="/static/styles.css">%(extra_css)s<link rel="stylesheet" href="/static/proposal-history.css"></head><body>
<main style="padding:24px"><h1>The page behind</h1><button id="opener" type="button" onclick="openPH(window.__cfg)">My Proposal History</button> <button id="other" type="button">Other</button>
<div id="host"></div><div style="height:2400px"></div></main>
<script src="/static/proposal-history.js"></script>
<script>%(js)s</script></body></html>"""
)
PAGES = {
    "/harness.html": HARNESS % {"extra_css": "", "js": HARNESS_JS.replace("/*CONTRAST*/", dom.CONTRAST_JS)},
    # The admin console's own stylesheet loaded too, to prove it does not reach into the component.
    "/harness_admin.html": HARNESS % {"extra_css": '<link rel="stylesheet" href="/static/admin.css">', "js": HARNESS_JS.replace("/*CONTRAST*/", dom.CONTRAST_JS)},
}


@pytest.fixture(scope="module")
def server():
    with dom.serve(PAGES) as base:
        yield base


@pytest.fixture(scope="module")
def browser():
    with dom.launch(playwright_sync) as b:
        yield b


@pytest.fixture
def page(browser, server):
    p = dom.new_page(browser, server, "/harness.html")
    p.dialogs = []                                                 # type: ignore[attr-defined]
    p.on("dialog", lambda d: (p.dialogs.append(d.message), d.dismiss()))
    yield p
    p.context_.close()
    assert p.errors == [], p.errors
    assert p.dialogs == [], f"a native confirm()/alert() opened: {p.dialogs}"


# --------------------------------------------------------------------------------------------- helpers

def use(page, rows=None, **api):
    """Puts the rows (and any __api settings such as `facets=False`) behind the fake endpoint."""
    page.evaluate("([rows, api]) => { if (rows) window.__api.data = rows; Object.assign(window.__api, api); }", [rows, api])


EMPTY = ".ph[data-state='empty']"


def open_history(page, rows=None, wait=".ph-row", focus_opener=True, **cfg):
    if rows is not None:
        use(page, rows)
    if focus_opener:
        page.evaluate("() => document.getElementById('opener').focus({ preventScroll: true })")      # (page.focus() would scroll the page to the button)
    page.evaluate("cfg => openPH(cfg)", cfg)
    page.wait_for_selector(".ph-dialog")
    if wait:
        page.wait_for_selector(wait)


def mount_admin(page, rows=None, wait=".ph-row", **cfg):
    if rows is not None:
        use(page, rows)
    page.evaluate("cfg => mountPH(cfg)", cfg)
    if wait:
        page.wait_for_selector(wait)


def requests(page, path="/api/my-proposals"):
    return page.evaluate("p => window.__api.log.filter(r => r.path === p).map(r => ({ url: r.url, params: r.params, aborted: r.aborted, status: r.status }))", path)


def ids(page):
    return page.eval_on_selector_all(".ph-row", "els => els.map(e => e.dataset.id)")


def texts(page, selector):
    return page.eval_on_selector_all(selector, "els => els.map(e => e.textContent.trim())")


def state(page):
    return page.get_attribute(".ph", "data-state")


def count_text(page):
    return page.inner_text(".ph-count").strip()


def active(page):
    """A short description of the element that has the focus."""
    return page.evaluate("""() => { const a = document.activeElement; if (!a) return ''; const li = a.closest && a.closest('.ph-row');
        return (a.tagName.toLowerCase() + '.' + String(a.className).split(' ').join('.')) + (li ? '@' + li.dataset.id : ''); }""")


def focus_in_dialog(page):
    return page.evaluate("() => !!document.activeElement.closest('.ph-dialog, .ph-menu')")


def wait_ready(page):
    page.wait_for_function("() => document.querySelector('.ph') && document.querySelector('.ph').dataset.state !== 'loading' && !document.querySelector('.ph.is-loading')")


def press_n(page, key, n):
    for _ in range(n):
        page.keyboard.press(key)


def row_text(page, pid, selector=""):
    """The text of an element in a row as textContent, whitespace collapsed (inner_text would break lines at the screen-reader-only spans and flex items)."""
    return " ".join(page.text_content(f".ph-row[data-id='{pid}'] {selector}".strip()).split())


def tabs_to(page, selector, limit=80):
    """Presses Tab until the focus is on `selector`, and returns how many presses it took."""
    for n in range(1, limit + 1):
        page.keyboard.press("Tab")
        if page.evaluate("s => document.activeElement.matches(s)", selector):
            return n
    raise AssertionError(f"Tab never reached {selector}; focus is on {active(page)}")


# --------------------------------------------------------------------------------------------- D1: the dialog

def test_it_opens_as_an_accessible_modal_dialog(page):
    open_history(page, standard_rows())
    d = page.eval_on_selector(".ph-dialog", """e => ({ role: e.getAttribute('role'), modal: e.getAttribute('aria-modal'), tab: e.getAttribute('tabindex'),
        label: document.getElementById(e.getAttribute('aria-labelledby')).textContent, desc: document.getElementById(e.getAttribute('aria-describedby')).textContent })""")
    assert d["role"] == "dialog" and d["modal"] == "true" and d["tab"] == "-1"
    assert d["label"] == "My Proposal History"
    assert d["desc"] == "Open or continue a proposal, or copy one for a new client."
    assert active(page).startswith("input.ph-search-input")
    assert page.is_visible(".ph-x")


def test_without_a_duplicate_hook_the_subtitle_and_the_buttons_do_not_promise_a_copy(page):
    open_history(page, standard_rows(), dup=False)
    assert page.inner_text(".ph-sub") == "Open or continue a proposal."
    assert page.locator(".ph-dup").count() == 0
    assert page.locator(".ph-kebab").count() == 14


def test_on_a_phone_the_focus_starts_on_the_dialog_so_the_keyboard_stays_down(browser, server):
    p = dom.new_page(browser, server, "/harness.html", width=390, height=800)
    open_history(p, standard_rows())
    assert p.evaluate("() => document.activeElement.classList.contains('ph-dialog')")
    dom.close_page(p)


def test_the_page_behind_gets_its_scroll_and_inline_style_back_exactly(page):
    page.evaluate("() => { document.body.setAttribute('style', 'color: rgb(1, 2, 3)'); window.scrollTo(0, 120); }")
    open_history(page, standard_rows())
    assert page.evaluate("() => getComputedStyle(document.body).overflow") == "hidden"
    page.keyboard.press("Escape")
    assert page.evaluate("() => document.body.getAttribute('style')") == "color: rgb(1, 2, 3)"
    page.evaluate("() => document.body.removeAttribute('style')")
    open_history(page)
    page.click(".ph-x")
    assert page.evaluate("() => document.body.getAttribute('style')") is None
    assert page.evaluate("() => window.scrollY") == 120


def test_the_page_behind_is_padded_by_the_width_of_the_scrollbar_the_lock_takes_away(page):
    # Headless Chromium draws overlay scrollbars that take no room, so a classic 15px one is stood in for: the page "has" a viewport 15px narrower than the window.
    page.evaluate("() => Object.defineProperty(document.documentElement, 'clientWidth', { configurable: true, get: () => innerWidth - 15 })")
    open_history(page, standard_rows())
    assert page.evaluate("() => document.body.style.paddingRight") == "15px"                     # without it the page would jump 15px to the right when the scrollbar goes
    page.keyboard.press("Escape")
    assert page.evaluate("() => document.body.getAttribute('style')") is None                    # and the padding goes with the lock
    page.evaluate("() => { document.body.style.paddingRight = '8px'; }")
    open_history(page)
    assert page.evaluate("() => document.body.style.paddingRight") == "23px"                     # an existing padding is added to, not replaced
    page.keyboard.press("Escape")
    assert page.evaluate("() => document.body.getAttribute('style')") == "padding-right: 8px;"


def test_an_escape_that_only_ends_an_ime_composition_does_not_close_the_dialog(page):
    open_history(page, standard_rows())
    page.evaluate("""() => { const box = document.querySelector('.ph-search-input');
        box.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', isComposing: true, bubbles: true, cancelable: true }));
        box.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', keyCode: 229, bubbles: true, cancelable: true })); }""")
    assert page.locator(".ph-dialog").count() == 1
    page.keyboard.press("Escape")
    page.wait_for_selector(".ph-dialog", state="detached")


def test_closing_gives_the_focus_back_to_the_opener_or_to_returnFocus(page):
    open_history(page, standard_rows())
    page.click(".ph-x")
    assert page.locator(".ph-dialog").count() == 0
    assert page.evaluate("() => document.activeElement.id") == "opener"
    page.evaluate("() => openPH({ opts: { returnFocus: document.getElementById('other') } })")
    page.wait_for_selector(".ph-row")
    page.keyboard.press("Escape")
    assert page.evaluate("() => document.activeElement.id") == "other"


def test_a_second_click_of_a_double_click_on_the_opener_does_not_close_the_dialog(page):
    use(page, standard_rows())
    page.evaluate("() => { document.getElementById('opener').style.cssText = 'position: fixed; left: 4px; top: 4px;'; }")      # in the corner: the dialog's backdrop will be under the second click
    page.dblclick("#opener")
    page.wait_for_selector(".ph-row")
    page.wait_for_timeout(150)
    assert page.locator(".ph-dialog").count() == 1


def test_a_click_on_the_backdrop_closes_and_a_click_inside_does_not(page):
    open_history(page, standard_rows())
    page.mouse.click(640, 400)
    page.mouse.click(5, 5)
    page.wait_for_selector(".ph-dialog", state="detached")


def test_opening_the_dialog_again_replaces_the_first_one(page):
    open_history(page, standard_rows())
    page.evaluate("() => openPH(window.__cfg)")
    page.wait_for_selector(".ph-row")
    assert page.locator(".ph-dialog").count() == 1
    assert page.evaluate("() => ProposalHistory.isOpen()") is True
    page.evaluate("() => ProposalHistory.close()")
    assert page.evaluate("() => [ProposalHistory.isOpen(), document.querySelectorAll('.ph-overlay').length]") == [False, 0]
    assert page.evaluate("() => document.body.getAttribute('style')") is None


def test_a_closed_dialogs_late_answer_does_nothing_and_leaks_nothing(page):
    use(page, standard_rows(), delay=400)
    open_history(page, wait=None)
    page.keyboard.press("Escape")
    page.wait_for_timeout(700)
    assert page.locator(".ph, .ph-menu").count() == 0
    assert requests(page)[0]["aborted"] is True


# --------------------------------------------------------------------------------------------- D2: loading and the states

def test_a_slow_first_load_shows_a_skeleton_then_the_rows(page):
    use(page, standard_rows(), delay=500)
    open_history(page, wait=None)
    assert state(page) == "loading"
    assert page.get_attribute(".ph-body", "aria-busy") == "true"
    assert page.is_visible(".ph-skel") and page.locator(".ph-skel .ph-skel-row").count() == 6
    assert page.get_attribute(".ph-skel", "aria-hidden") == "true"
    assert page.locator(".ph-skel-row > span").first.bounding_box()["height"] >= 8                  # the placeholder bars are drawn, not collapsed to nothing
    assert page.locator(".ph-row").count() == 0
    page.wait_for_selector(".ph-row")
    assert state(page) == "ready" and page.get_attribute(".ph-body", "aria-busy") == "false"
    assert not page.is_visible(".ph-skel")
    assert page.locator(".ph-row").count() == 14


def test_a_refresh_keeps_the_rows_on_screen_dimmed_instead_of_blanking_the_list(page):
    open_history(page, standard_rows())
    use(page, delay=600)
    page.click(".ph-seg--status button[data-status='draft']")
    page.wait_for_selector(".ph.is-loading")
    assert page.locator(".ph-row").count() == 14                                       # still the old rows
    assert state(page) == "ready"
    assert page.evaluate("() => getComputedStyle(document.querySelector('.ph-list')).opacity") == "0.55"
    assert page.get_attribute(".ph-body", "aria-busy") == "true"
    assert page.evaluate("() => getComputedStyle(document.querySelector('.ph-progress'), '::after').width") != "0px"
    wait_ready(page)
    assert ids(page) == ["pid001", "pid004", "pid006", "pid010"]


def test_the_five_states_change_data_state(page):
    open_history(page, [], wait=EMPTY)
    assert state(page) == "empty"
    open_history(page, standard_rows())
    assert state(page) == "ready"
    page.fill(".ph-search-input", "zzzz-nothing")
    page.wait_for_selector(".ph[data-state='no-results']")
    use(page, fail=None)
    page.evaluate("() => { window.__api.fail = () => ({ status: 500 }); }")
    page.fill(".ph-search-input", "acme")
    page.wait_for_selector(".ph[data-state='error']")
    page.evaluate("() => { window.__api.fail = null; }")
    page.click(".ph-retry")
    page.wait_for_selector(".ph[data-state='ready']")
    assert ids(page) == ["pid003"]
    assert page.logged and all(m.startswith("ProposalHistory:") for m in page.logged)


def test_the_count_line_is_a_polite_live_region_and_the_actions_have_their_own(page):
    open_history(page, standard_rows())
    c = page.eval_on_selector(".ph-count", "e => [e.getAttribute('role'), e.getAttribute('aria-live'), e.getAttribute('aria-atomic')]")
    assert c == ["status", "polite", "true"]
    assert count_text(page) == "14 proposals"
    live = page.eval_on_selector(".ph-live", "e => [e.getAttribute('role'), e.getAttribute('aria-live')]")
    assert live == ["status", "polite"]


# --------------------------------------------------------------------------------------------- D3: what a row says

def test_a_draft_row_shows_flight_a_dash_for_the_value_and_how_far_it_got(page):
    open_history(page, standard_rows())
    assert row_text(page, "pid001", ".ph-client-name") == "Fronteras Del Norte"
    assert row_text(page, "pid001", ".ph-flight .ph-main") == "Jan 5 – Mar 31, 2027"
    assert row_text(page, "pid001", ".ph-flight .ph-minor") == "Billed monthly"
    assert row_text(page, "pid001", ".ph-value .ph-main").startswith("—")
    assert row_text(page, "pid001", ".ph-value .ph-minor") == "6 products"
    assert row_text(page, "pid001", ".ph-badge") == "Draft"
    assert row_text(page, "pid001", ".ph-status .ph-minor") == "Step 4 of 8"
    assert page.get_attribute(".ph-row[data-id='pid001'] .ph-value .ph-none", "title") == "A draft has no total until the proposal is generated"
    assert page.get_attribute(".ph-row[data-id='pid001']", "data-status") == "draft"
    assert row_text(page, "pid001", ".ph-open") == "Continue Fronteras Del Norte"


def test_a_draft_never_shows_a_value_even_if_the_server_sent_a_number(page):
    # A draft has no summary, so the app never writes a total for one; if one ever arrives it must not read as the proposal's price.
    open_history(page, [row(1, "Stray Total", "draft", total_net=5000.0, line_count=2)])
    assert row_text(page, "pid001", ".ph-value .ph-main").startswith("—")
    assert "$" not in row_text(page, "pid001", ".ph-value")


def test_generated_rows_show_the_net_value_the_product_count_and_a_zero_as_zero(page):
    open_history(page, standard_rows())
    assert row_text(page, "pid002", ".ph-value .ph-main") == "$412,500"
    assert row_text(page, "pid002", ".ph-value .ph-minor") == "9 products"
    assert row_text(page, "pid002", ".ph-badge") == "Generated"
    assert row_text(page, "pid002", ".ph-flight .ph-main") == "Dec 1, 2026 – Feb 28, 2027"
    assert row_text(page, "pid002", ".ph-flight .ph-minor") == "Billed weekly"
    assert row_text(page, "pid009", ".ph-value .ph-main") == "$0"
    assert row_text(page, "pid009", ".ph-value .ph-minor") == "0 products"
    assert row_text(page, "pid007", ".ph-value .ph-minor") == "1 product"
    assert row_text(page, "pid007", ".ph-flight .ph-minor") == "One full-flight bill"
    assert row_text(page, "pid005", ".ph-flight .ph-minor") == "Billed quarterly"
    assert row_text(page, "pid002", ".ph-open") == "Open Banco Popular de Puerto Rico & Subsidiaries (Consumer Banking Division)"


def test_a_blank_client_a_missing_flight_and_a_legacy_row_are_each_said_plainly(page):
    open_history(page, standard_rows())
    assert row_text(page, "pid004", ".ph-client-name") == "No client name yet"
    assert "is-blank" in page.get_attribute(".ph-row[data-id='pid004'] .ph-client-name", "class")
    assert row_text(page, "pid004", ".ph-flight .ph-main") == "No dates yet"
    assert page.locator(".ph-row[data-id='pid004'] .ph-subline").count() == 0                      # no title and no ID: no subline
    assert page.locator(".ph-row[data-id='pid012'] .ph-flight .ph-minor, .ph-row[data-id='pid012'] .ph-value .ph-minor").count() == 0   # nothing known: no minor lines
    legacy = page.eval_on_selector(".ph-row[data-id='pid012'] .ph-open-off", "e => [e.tagName, e.textContent, e.getAttribute('aria-disabled'), e.getAttribute('title'), e.hasAttribute('href')]")
    assert legacy[0] == "BUTTON" and legacy[1].startswith("Can’t reopen") and legacy[2] == "true" and legacy[3] == "Saved before reopening was supported" and legacy[4] is False
    dup = page.eval_on_selector(".ph-row[data-id='pid012'] .ph-dup", "e => [e.getAttribute('aria-disabled'), e.getAttribute('title')]")
    assert dup == ["true", "Saved before copying was supported"]
    assert page.locator(".ph-row[data-id='pid012'] .ph-open").count() == 0
    page.click(".ph-row[data-id='pid012'] .ph-dup", force=True)                                  # (Playwright will not click an aria-disabled control otherwise)
    assert page.evaluate("() => window.__dups.length") == 0


def test_the_notion_id_is_shown_only_when_the_title_does_not_already_contain_it(page):
    open_history(page, standard_rows())
    assert row_text(page, "pid001", ".ph-subline") == "EVC-48213_FronterasDelNorte_Proposal_20261105"        # the title already starts with it
    assert row_text(page, "pid005", ".ph-subline") == "EVC-47990 Toyota 2027 Plan"                           # it does not: the ID goes first
    assert page.get_attribute(".ph-row[data-id='pid005'] .ph-subline", "title") == "EVC-47990 Toyota 2027 Plan"
    assert row_text(page, "pid010", ".ph-subline") == "EVC-47510"                                            # an ID and no title


def test_a_copied_proposal_says_where_it_came_from(page):
    open_history(page, standard_rows())
    assert row_text(page, "pid006", ".ph-lineage .ph-chip") == "⧉ Copied from Casa Grande"
    assert page.get_attribute(".ph-row[data-id='pid006'] .ph-chip", "title") == "Copied from Casa Grande (EVC-47001)"
    assert row_text(page, "pid002", ".ph-lineage .ph-chip") == "⧉ Copied from Banco Popular (2025 flight)"
    assert page.locator(".ph-row[data-id='pid003'] .ph-lineage").count() == 0
    rows = standard_rows()
    rows[2]["duplicated_from"] = "just-an-id"                                                             # a bare id string is a lineage too
    rows[3]["duplicated_from"] = {"proposal_id": "x", "notion_id": "EVC-9"}
    open_history(page, rows)
    assert row_text(page, "pid003", ".ph-lineage .ph-chip") == "⧉ Copied from another proposal"
    assert row_text(page, "pid004", ".ph-lineage .ph-chip") == "⧉ Copied from EVC-9"


def test_last_edited_reads_relative_with_the_exact_time_under_it_and_a_date_after_a_week(page):
    open_history(page, standard_rows())
    assert row_text(page, "pid001", ".ph-edited .ph-main") == "7 min ago"
    assert row_text(page, "pid001", ".ph-edited .ph-minor") == "Nov 11, 3:23 PM"
    assert row_text(page, "pid002", ".ph-edited .ph-main") == "3 hr ago"
    assert row_text(page, "pid003", ".ph-edited .ph-main") == "Yesterday"
    assert row_text(page, "pid005", ".ph-edited .ph-main") == "3 days ago"
    assert row_text(page, "pid008", ".ph-edited .ph-main") == "Oct 28, 2026"
    assert page.locator(".ph-row[data-id='pid008'] .ph-edited .ph-minor").count() == 0
    t = page.eval_on_selector(".ph-row[data-id='pid001'] time", "e => [e.getAttribute('datetime'), e.getAttribute('title')]")
    assert t == [ago(minutes=7), "Wednesday, November 11, 2026 at 3:23 PM"]


def test_a_row_that_arrives_malformed_is_drawn_from_what_is_usable(page):
    rows = [
        {"proposal_id": "ok1", "client_name": 42, "status": "draft", "updated_at": "not a time", "total_net": "12", "flight_start": "2027-02-31", "line_count": "x", "wizard_step": 99,
         "tabs_built": "Proposal A", "duplicated_from": ["list"], "time_unit": {"a": 1}},
        {"proposal_id": "", "client_name": "no id"},
        None, "text", 5, {"client_name": "no id either"},
        row(5, "Fine Client"),
    ]
    page.evaluate("rows => { window.__api.raw = { proposals: rows, total_count: 7, facets: { all: 7, draft: 1, generated: 6 } }; }", rows)
    open_history(page)
    assert ids(page) == ["ok1", "pid005"]
    assert row_text(page, "ok1", ".ph-client-name") == "No client name yet"
    assert row_text(page, "ok1", ".ph-flight .ph-main") == "No dates yet"
    assert row_text(page, "ok1", ".ph-edited .ph-main") == "—"
    assert page.locator(".ph-row[data-id='ok1'] time").count() == 0
    assert page.locator(".ph-row[data-id='ok1'] .ph-minor, .ph-row[data-id='ok1'] .ph-lineage").count() == 0
    assert row_text(page, "ok1", ".ph-value .ph-main").startswith("—")


# --------------------------------------------------------------------------------------------- D4: hostile data (mandatory)

HOSTILE_ID = 'a/b?c=d&e#f"g'


def hostile_rows():
    return [
        row(1, "<img src=x onerror=window.__pwned=1>", "generated", ago(minutes=5), proposal_title='"><script>window.__pwned=2</script>', notion_id='EVC-1" onmouseover="window.__pwned=3',
            requested_by="<svg onload=window.__pwned=6>", requester_user_agent="<img src=z onerror=window.__pwned=5> UA", total_net=5.0,
            duplicated_from={"proposal_id": "p", "client_name": "<b onmouseover=window.__pwned=7>x</b><img src=y onerror=window.__pwned=4>", "notion_id": "<u>1</u>"}, tabs_built=["<i>tab</i>"]),
        row(2, "a.c* corp", "draft", ago(minutes=9), proposal_title="A.C* Holdings (a.c*)"),
        row(3, "abc corp", "generated", ago(minutes=11), total_net=7.0),
        {**row(4, "Odd id client", "generated", ago(minutes=12)), "proposal_id": HOSTILE_ID},
    ]


def test_hostile_text_is_never_markup_in_the_planner_dialog(page):
    open_history(page, hostile_rows())
    assert page.locator(".ph img, .ph script, .ph svg, .ph b, .ph u, .ph i, .ph-menu img").count() == 0
    assert page.evaluate("() => window.__pwned") is None
    assert row_text(page, "pid001", ".ph-client-name") == "<img src=x onerror=window.__pwned=1>"
    assert page.get_attribute(".ph-row[data-id='pid001'] .ph-client-name", "title") == "<img src=x onerror=window.__pwned=1>"
    assert row_text(page, "pid001", ".ph-subline") == 'EVC-1" onmouseover="window.__pwned=3 "><script>window.__pwned=2</script>'   # the ID goes first: the title lacks it
    assert "<b onmouseover=window.__pwned=7>x</b>" in row_text(page, "pid001", ".ph-chip")
    assert page.get_attribute(".ph-row[data-id='pid001'] .ph-dup", "aria-label") == "Duplicate for new client: <img src=x onerror=window.__pwned=1>"
    assert page.get_attribute(".ph-row[data-id='pid001'] .ph-kebab", "aria-label") == "More actions for <img src=x onerror=window.__pwned=1>"
    page.click(".ph-row[data-id='pid001'] .ph-kebab")                                          # the menu is built from text too
    assert page.locator(".ph-menu img, .ph-menu script").count() == 0
    page.keyboard.press("Escape")
    assert page.evaluate("() => window.__pwned") is None


def test_ids_are_percent_encoded_in_every_link_and_never_break_out_of_the_attribute(page):
    open_history(page, hostile_rows())
    href = page.evaluate("id => [...document.querySelectorAll('.ph-row')].find(r => r.dataset.id === id).querySelector('.ph-open').getAttribute('href')", HOSTILE_ID)
    assert href == "/?reopen=a%2Fb%3Fc%3Dd%26e%23f%22g"
    page.evaluate("id => [...document.querySelectorAll('.ph-row')].find(r => r.dataset.id === id).querySelector('.ph-kebab').click()", HOSTILE_ID)
    page.click(".ph-menu-item[data-action='copy-link']")
    page.wait_for_function("() => window.__clip.writes.length === 1")
    assert page.evaluate("() => window.__clip.writes[0]").endswith("/?reopen=a%2Fb%3Fc%3Dd%26e%23f%22g")


def test_search_words_with_regex_characters_are_highlighted_literally(page):
    open_history(page, hostile_rows())
    page.fill(".ph-search-input", "a.c*")
    page.wait_for_function("() => window.__api.log.some(r => r.params.search === 'a.c*')")
    wait_ready(page)
    assert ids(page) == ["pid002"]                                                              # "abc corp" does not match the literal "a.c*"
    marks = texts(page, ".ph-row[data-id='pid002'] mark.ph-mark")
    assert marks == ["a.c*", "A.C*", "a.c*"]                                                   # client, then the title twice
    assert page.evaluate("() => document.querySelector('.ph-row .ph-client-name').textContent") == "a.c* corp"


def test_the_admin_mount_escapes_the_planner_the_ae_and_the_device_too(browser, server):
    p = dom.new_page(browser, server, "/harness_admin.html")
    mount_admin(p, hostile_rows(), opts={"scope": "all"})
    assert p.locator(".ph img, .ph script, .ph svg, .ph b").count() == 0
    assert p.evaluate("() => window.__pwned") is None
    assert "<svg onload=window.__pwned=6>" in row_text(p, "pid001", ".ph-who")
    assert row_text(p, "pid001", ".ph-device").startswith("<img src=z onerror=window.__pwned=5>")
    assert p.get_attribute(".ph-row[data-id='pid001'] .ph-device", "title") == "<img src=z onerror=window.__pwned=5> UA"
    dom.close_page(p)


# --------------------------------------------------------------------------------------------- D5: search

def test_typing_sends_one_request_after_the_pause_not_one_per_key(page):
    open_history(page, standard_rows())
    before = len(requests(page))
    page.keyboard.type("acme", delay=40)
    assert len(requests(page)) == before                                                       # nothing yet: still inside the 250 ms pause
    page.wait_for_function("() => window.__api.log.length > %d" % before)
    wait_ready(page)
    rs = requests(page)
    assert len(rs) == before + 1 and rs[-1]["params"]["search"] == "acme"
    assert ids(page) == ["pid003"]
    assert count_text(page) == "1 proposal matches “acme”"


def test_enter_searches_at_once(page):
    open_history(page, standard_rows())
    page.fill(".ph-search-input", "toyota")
    page.press(".ph-search-input", "Enter")
    page.wait_for_function("() => window.__api.log.some(r => r.params.search === 'toyota')")
    wait_ready(page)
    assert ids(page) == ["pid005"]


def test_a_slow_answer_to_an_older_search_cannot_overwrite_the_newer_one(page):
    open_history(page, standard_rows())
    page.evaluate("() => { window.__api.delay = (r) => (r.params.search === 'a' ? 700 : 40); }")
    page.fill(".ph-search-input", "a")
    page.wait_for_function("() => window.__api.log.some(r => r.params.search === 'a')")
    page.fill(".ph-search-input", "acme")                                                       # sent while the answer for "a" is still on its way
    page.wait_for_function("() => window.__api.log.some(r => r.params.search === 'acme')")
    page.wait_for_timeout(1000)                                                                 # "a" has answered by now (late)
    assert ids(page) == ["pid003"]
    assert count_text(page) == "1 proposal matches “acme”"
    rs = requests(page)
    assert [r["aborted"] for r in rs if r["params"].get("search") == "a"] == [True]            # the superseded request was cancelled
    assert state(page) == "ready"


def test_the_counter_alone_drops_a_stale_answer_even_when_the_transport_cannot_be_cancelled(page):
    open_history(page, standard_rows())
    page.evaluate("() => { window.__api.ignoreAbort = true; window.__api.delay = (r) => (r.params.search === 'a' ? 700 : 40); }")
    page.fill(".ph-search-input", "a")
    page.wait_for_function("() => window.__api.log.some(r => r.params.search === 'a')")
    page.fill(".ph-search-input", "acme")
    page.wait_for_function("() => window.__api.log.some(r => r.params.search === 'acme')")
    page.wait_for_timeout(1000)                                                                 # the answer for "a" (every row) arrived last and must not show
    assert [r["aborted"] for r in requests(page) if r["params"].get("search") == "a"] == [False]   # it really was not cancelled: only the counter stood in its way
    assert ids(page) == ["pid003"] and count_text(page) == "1 proposal matches “acme”"


def test_a_whole_query_is_normalised_before_it_is_sent(page):
    open_history(page, standard_rows())
    page.fill(".ph-search-input", "   evc    48213   ")
    page.press(".ph-search-input", "Enter")
    page.wait_for_function("() => window.__api.log.some(r => r.params.search)")
    assert requests(page)[-1]["params"]["search"] == "evc 48213"
    wait_ready(page)
    assert ids(page) == ["pid001"]
    before = len(requests(page))
    page.fill(".ph-search-input", "evc 48213  ")                                                # the same words: no new request
    page.press(".ph-search-input", "Enter")
    page.wait_for_timeout(200)
    assert len(requests(page)) == before


def test_the_typed_words_are_highlighted_in_the_client_and_the_title(page):
    open_history(page, standard_rows())
    page.fill(".ph-search-input", "acme 48055")
    page.press(".ph-search-input", "Enter")
    wait_ready(page)
    assert texts(page, ".ph-row .ph-client-name mark") == ["Acme"]
    assert texts(page, ".ph-row .ph-subline mark") == ["48055", "Acme"]                          # in the order they stand in the title: EVC-48055_AcmeHVAC_...


def test_escape_empties_a_search_box_that_has_text_before_it_closes_anything(page):
    open_history(page, standard_rows())
    page.fill(".ph-search-input", "acme")
    page.press(".ph-search-input", "Enter")
    wait_ready(page)
    page.keyboard.press("Escape")
    assert page.input_value(".ph-search-input") == ""
    assert page.locator(".ph-dialog").count() == 1
    page.wait_for_function("() => document.querySelectorAll('.ph-row').length === 14")
    assert active(page).startswith("input.ph-search-input")
    page.wait_for_function("() => document.querySelector('.ph-live').textContent === 'Search cleared.'")
    page.keyboard.press("Escape")
    page.wait_for_selector(".ph-dialog", state="detached")


def test_the_clear_button_shows_with_text_and_empties_the_box(page):
    open_history(page, standard_rows())
    assert not page.is_visible(".ph-search-clear")
    page.fill(".ph-search-input", "acme")
    assert page.is_visible(".ph-search-clear")
    page.click(".ph-search-clear")
    assert page.input_value(".ph-search-input") == "" and not page.is_visible(".ph-search-clear")
    assert active(page).startswith("input.ph-search-input")


def test_nothing_matching_says_so_and_clear_search_brings_the_list_back_with_the_focus_in_the_box(page):
    open_history(page, standard_rows())
    page.fill(".ph-search-input", "zzzz")
    page.press(".ph-search-input", "Enter")
    page.wait_for_selector(".ph[data-state='no-results']")
    assert page.inner_text(".ph-state-title") == "Nothing matches “zzzz”"
    assert page.inner_text(".ph-state-text") == "Check the spelling, or try a client name or a Notion ID like EVC-48213."
    assert count_text(page) == "No proposals match “zzzz”"
    assert page.locator(".ph-row").count() == 0 and not page.is_visible(".ph-cols")
    page.click(".ph-clear-search")
    page.wait_for_selector(".ph[data-state='ready']")
    assert page.locator(".ph-row").count() == 14
    assert active(page).startswith("input.ph-search-input")


def test_a_pasted_novel_is_echoed_back_shortened(page):
    open_history(page, standard_rows())
    long = "x" * 120
    page.fill(".ph-search-input", long)
    page.press(".ph-search-input", "Enter")
    page.wait_for_selector(".ph[data-state='no-results']")
    assert page.inner_text(".ph-state-title") == "Nothing matches “" + "x" * 40 + "…”"
    assert len(requests(page)[-1]["params"]["search"]) == 120                                  # what is SENT is the whole text (the server caps it)


def test_arrow_down_in_the_search_box_goes_to_the_first_row(page):
    open_history(page, standard_rows())
    page.press(".ph-search-input", "ArrowDown")
    assert active(page) == "a.ph-btn.ph-btn--primary.ph-open@pid001"


# --------------------------------------------------------------------------------------------- D6: the status filter

def test_the_status_buttons_show_counts_that_follow_the_search_but_not_the_status(page):
    open_history(page, standard_rows())
    assert texts(page, ".ph-seg--status button") == ["All 14", "Drafts 4", "Generated 10"]
    assert texts(page, ".ph-seg-n") == ["14", "4", "10"]
    pressed = lambda: page.eval_on_selector_all(".ph-seg--status button", "els => els.map(e => e.getAttribute('aria-pressed'))")   # noqa: E731
    assert pressed() == ["true", "false", "false"]
    page.click(".ph-seg--status button[data-status='draft']")
    wait_ready(page)
    assert pressed() == ["false", "true", "false"]
    assert texts(page, ".ph-seg-n") == ["14", "4", "10"]                                        # the other counts did not collapse to the status
    assert count_text(page) == "4 drafts"
    assert requests(page)[-1]["params"]["status"] == "draft"
    page.fill(".ph-search-input", "acme")
    page.press(".ph-search-input", "Enter")
    wait_ready(page)
    assert texts(page, ".ph-seg-n") == ["1", "0", "1"]                                          # now they follow the search ("Drafts 0": the draft filter is still on)
    assert requests(page)[-1]["params"]["search"] == "acme" and requests(page)[-1]["params"]["status"] == "draft"


def test_a_filter_with_no_rows_says_so_and_show_all_undoes_it(page):
    open_history(page, [r for r in standard_rows() if r["status"] != "draft"])
    assert texts(page, ".ph-seg-n") == ["10", "0", "10"]                                        # "Drafts 0" is visible before the click
    page.click(".ph-seg--status button[data-status='draft']")
    page.wait_for_selector(".ph[data-state='no-results']")
    assert page.inner_text(".ph-state-title") == "No drafts match"
    assert page.inner_text(".ph-state-text") == "Nothing is saved as a draft right now."
    assert count_text(page) == "No drafts"
    page.click(".ph-show-all")
    page.wait_for_selector(".ph[data-state='ready']")
    assert page.locator(".ph-row").count() == 10
    assert page.eval_on_selector(".ph-seg--status button[data-status='all']", "e => e.getAttribute('aria-pressed')") == "true"


def test_the_counts_are_left_out_when_the_server_sent_none(page):
    use(page, standard_rows(), facets=False)
    open_history(page)
    assert texts(page, ".ph-seg--status button") == ["All", "Drafts", "Generated"]
    assert page.locator(".ph-seg-n:not([hidden])").count() == 0
    assert count_text(page) == "14 proposals"


def test_the_filters_name_both_a_status_and_a_search_when_both_are_on(page):
    open_history(page, standard_rows())
    page.click(".ph-seg--status button[data-status='draft']")
    wait_ready(page)
    page.fill(".ph-search-input", "toyota")
    page.press(".ph-search-input", "Enter")
    page.wait_for_selector(".ph[data-state='no-results']")
    assert page.inner_text(".ph-state-title") == "No drafts match “toyota”"
    assert page.locator(".ph-clear-search").count() == 1 and page.locator(".ph-show-all").count() == 1
    page.click(".ph-show-all")                                                                  # all statuses, the search stays
    wait_ready(page)
    assert ids(page) == ["pid005"] and page.input_value(".ph-search-input") == "toyota"


# --------------------------------------------------------------------------------------------- D7: sort

def sort_state(page):
    return page.eval_on_selector_all(".ph-sort", "els => els.map(e => [e.dataset.sort, e.getAttribute('aria-pressed'), e.getAttribute('data-dir')])")


def test_the_header_buttons_sort_and_a_second_click_flips_the_direction(page):
    open_history(page, standard_rows())
    assert sort_state(page) == [["client", "false", None], ["value", "false", None], ["updated", "true", "desc"]]
    page.click(".ph-sort[data-sort='client']")
    wait_ready(page)
    assert sort_state(page)[0] == ["client", "true", "asc"] and sort_state(page)[2] == ["updated", "false", None]
    assert requests(page)[-1]["params"]["sort"] == "client" and "dir" not in requests(page)[-1]["params"]     # a first click is the natural direction
    assert texts(page, ".ph-row .ph-client-name")[:3] == ["Acme HVAC", "Banco Popular de Puerto Rico & Subsidiaries (Consumer Banking Division)", "Casa Bonita"]
    assert texts(page, ".ph-row .ph-client-name")[-1] == "No client name yet"                    # a blank client is last
    page.click(".ph-sort[data-sort='client']")
    wait_ready(page)
    assert sort_state(page)[0] == ["client", "true", "desc"]
    assert requests(page)[-1]["params"]["dir"] == "desc"
    assert texts(page, ".ph-row .ph-client-name")[-1] == "No client name yet"                    # and still last the other way round
    assert "sorted Z to A" in page.text_content(".ph-sort[data-sort='client']")                  # screen-reader text (inner_text shows the upper-cased look)
    page.click(".ph-sort[data-sort='value']")
    wait_ready(page)
    assert requests(page)[-1]["params"]["sort"] == "value"
    assert ids(page)[:3] == ["pid005", "pid013", "pid002"]                                       # biggest first; drafts have no value and come last


def test_the_sort_select_shows_the_same_state_as_the_header_buttons_at_a_narrow_width(browser, server):
    p = dom.new_page(browser, server, "/harness.html", width=800)
    open_history(p, standard_rows())
    assert p.is_visible(".ph-select") and not p.is_visible(".ph-cols")
    opts = p.eval_on_selector_all(".ph-select option", "els => els.map(e => [e.value, e.textContent])")
    assert opts == [["updated:desc", "Last edited · newest first"], ["updated:asc", "Last edited · oldest first"], ["client:asc", "Client · A to Z"],
                    ["client:desc", "Client · Z to A"], ["value:desc", "Net value · high to low"], ["value:asc", "Net value · low to high"]]
    assert p.input_value(".ph-select") == "updated:desc"
    p.select_option(".ph-select", "value:asc")
    wait_ready(p)
    r = requests(p)[-1]["params"]
    assert r["sort"] == "value" and r["dir"] == "asc"
    assert sort_state(p)[1] == ["value", "true", "asc"]                                          # one state, two views
    assert p.input_value(".ph-select") == "value:asc"
    p.wait_for_function("() => document.querySelector('.ph-live').textContent === 'Sorted by net value, low to high.'")
    dom.close_page(p)


def test_exactly_one_of_the_header_strip_and_the_select_is_visible_at_every_width(browser, server):
    for width in (360, 640, 900, 1079, 1100, 1280):
        p = dom.new_page(browser, server, "/harness.html", width=width)
        open_history(p, standard_rows())
        strip, select = p.is_visible(".ph-cols"), p.is_visible(".ph-select")
        comp = p.evaluate("() => document.querySelector('.ph').getBoundingClientRect().width")
        assert strip != select, (width, comp, strip, select)
        assert strip == (comp >= 1080), (width, comp)
        dom.close_page(p)


def test_in_the_admin_list_too_exactly_one_of_the_header_strip_and_the_select_is_visible(browser, server):
    for width in (360, 640, 940, 990, 1010, 1280):
        p = dom.new_page(browser, server, "/harness_admin.html", width=width)
        mount_admin(p, standard_rows(), opts={"scope": "all"})
        strip, select = p.is_visible(".ph-cols"), p.is_visible(".ph-select")
        comp = p.evaluate("() => document.querySelector('.ph').getBoundingClientRect().width")
        assert strip != select, (width, comp, strip, select)
        assert strip == (comp >= 950), (width, comp)                                               # the admin's strip starts at 950, the planner's at 1080
        dom.close_page(p)


def test_sorting_by_value_says_that_drafts_come_last_when_there_are_drafts(page):
    open_history(page, standard_rows())
    page.click(".ph-sort[data-sort='value']")
    wait_ready(page)
    assert count_text(page) == "14 proposals. Drafts have no total yet, so they come last."
    page.click(".ph-sort[data-sort='client']")
    wait_ready(page)
    assert count_text(page) == "14 proposals"


# --------------------------------------------------------------------------------------------- D8: show more

def test_show_more_appends_without_rebuilding_the_rows_already_there(page):
    open_history(page, many_rows(60))
    assert page.locator(".ph-row").count() == 25
    assert page.inner_text(".ph-more-btn") == "Show 25 more"
    assert page.inner_text(".ph-more-left") == "Showing 25 of 60"
    assert count_text(page) == "Showing 25 of 60 proposals"
    page.evaluate("() => { document.querySelectorAll('.ph-row').forEach((e) => { e.__mine = true; }); }")
    page.click(".ph-more-btn")
    page.wait_for_function("() => document.querySelectorAll('.ph-row').length === 50")
    assert page.evaluate("() => [...document.querySelectorAll('.ph-row')].slice(0, 25).every((e) => e.__mine === true)") is True
    assert page.evaluate("() => [...document.querySelectorAll('.ph-row')].slice(25).every((e) => e.__mine === undefined)") is True
    assert active(page) == "a.ph-btn.ph-btn--primary.ph-open@pid026"                             # the keyboard lands on the first NEW row
    page.wait_for_function("() => document.querySelector('.ph-live').textContent.length > 0")
    assert page.inner_text(".ph-live") == "25 more proposals loaded. Showing 50 of 60."
    assert page.inner_text(".ph-more-btn") == "Show 10 more"
    assert requests(page)[-1]["params"]["page"] == "2"
    page.click(".ph-more-btn")
    page.wait_for_function("() => document.querySelectorAll('.ph-row').length === 60")
    assert page.locator(".ph-more").is_hidden()
    assert len(set(ids(page))) == 60
    assert count_text(page) == "60 proposals"


def test_show_more_never_repeats_a_row_when_the_list_shifted_in_between(page):
    open_history(page, many_rows(60))
    page.evaluate("() => { window.__api.data.unshift({ ...window.__api.data[0], proposal_id: 'pidNEW', client_name: 'Brand new', updated_at: '2026-11-11T15:29:00+00:00' }); }")
    page.click(".ph-more-btn")                                                                   # page 2 now starts with a row that is already shown
    page.wait_for_function("() => document.querySelectorAll('.ph-row').length > 25")
    got = ids(page)
    assert len(got) == len(set(got)) and got.count("pid025") == 1
    assert page.locator(".ph-row").count() == 49
    # The count follows the server's newest total (61 now, not the 60 the first page said), in the line, the button and the announcement.
    assert count_text(page) == "Showing 49 of 61 proposals"
    assert page.inner_text(".ph-more-left") == "Showing 49 of 61"
    page.wait_for_function("() => document.querySelector('.ph-live').textContent.length > 0")
    assert page.inner_text(".ph-live") == "24 more proposals loaded. Showing 49 of 61."


def test_show_more_asks_for_the_right_page_after_a_delete(page):
    open_history(page, many_rows(60))
    page.click(".ph-row[data-id='pid002'] .ph-kebab")
    page.click(".ph-menu-item[data-action='copy-link']")                                         # a menu can be used and closed without side effects
    page.click(".ph-row[data-id='pid004'] .ph-kebab")
    page.click(".ph-menu-item[data-action='delete']")
    page.click(".ph-confirm-delete")
    page.wait_for_function("() => document.querySelectorAll('.ph-row').length === 24")
    page.click(".ph-more-btn")
    page.wait_for_function("() => document.querySelectorAll('.ph-row').length === 25")
    assert "page" not in requests(page)[-1]["params"]                                           # 24 held, 25 per page: page 1 again, not page 2 (which would skip a row)
    got = ids(page)
    assert len(got) == len(set(got)) and "pid026" in got and "pid004" not in got                # page 1 held only one row that was not on screen: the one the delete pulled up
    page.click(".ph-more-btn")
    page.wait_for_function("() => document.querySelectorAll('.ph-row').length === 50")
    assert requests(page)[-1]["params"]["page"] == "2"                                          # 25 held now: the next page is the second one
    assert len(set(ids(page))) == 50


def test_a_failed_show_more_keeps_the_rows_and_offers_it_again(page):
    open_history(page, many_rows(60))
    page.evaluate("() => { window.__api.fail = (r) => (r.params.page === '2' ? { status: 500 } : null); }")
    page.click(".ph-more-btn")
    page.wait_for_selector(".ph-more-error", state="visible")
    assert page.inner_text(".ph-more-error") == "Couldn’t load more. Try again."
    assert page.locator(".ph-row").count() == 25 and state(page) == "ready"
    page.evaluate("() => { window.__api.fail = null; }")
    page.click(".ph-more-btn")
    page.wait_for_function("() => document.querySelectorAll('.ph-row').length === 50")
    assert not page.is_visible(".ph-more-error")


def test_a_page_past_the_end_just_ends_the_list_it_does_not_empty_it(page):
    open_history(page, many_rows(60))
    page.evaluate("() => { window.__api.data = window.__api.data.slice(0, 25); }")              # the rest was deleted somewhere else
    page.click(".ph-more-btn")
    page.wait_for_function("() => document.querySelector('.ph-more').hidden")
    assert page.locator(".ph-row").count() == 25 and state(page) == "ready"
    assert count_text(page) == "25 proposals"


# --------------------------------------------------------------------------------------------- D9: the keyboard

def test_tab_and_shift_tab_stay_inside_the_dialog(page):
    open_history(page, standard_rows())
    for _ in range(60):
        page.keyboard.press("Tab")
        assert focus_in_dialog(page), active(page)
    for _ in range(60):
        page.keyboard.press("Shift+Tab")
        assert focus_in_dialog(page), active(page)
    page.evaluate("() => document.getElementById('other').focus()")                              # focus pushed outside comes straight back
    assert focus_in_dialog(page)


def test_the_arrow_keys_and_home_end_move_between_the_open_links(page):
    open_history(page, standard_rows())
    page.focus(".ph-row[data-id='pid001'] .ph-open")
    page.keyboard.press("ArrowDown")
    assert active(page).endswith("@pid002")
    page.keyboard.press("ArrowDown")
    assert active(page).endswith("@pid003")
    page.keyboard.press("ArrowUp")
    assert active(page).endswith("@pid002")
    page.keyboard.press("End")
    assert active(page).endswith("@pid014")
    page.keyboard.press("ArrowDown")                                                             # nothing after the last row: it stays
    assert active(page).endswith("@pid014")
    page.keyboard.press("Home")
    assert active(page).endswith("@pid001")
    page.keyboard.press("ArrowUp")                                                               # above the first row is the search box
    assert active(page).startswith("input.ph-search-input")
    page.focus(".ph-row[data-id='pid012'] .ph-open-off")                                         # a legacy row's disabled button is a stop on the same path
    page.keyboard.press("ArrowDown")
    assert active(page).endswith("@pid013")


def test_escape_closes_one_level_at_a_time_and_never_reaches_the_page_behind(page):
    open_history(page, standard_rows())
    page.fill(".ph-search-input", "casa")                                                          # Casa Bonita, a draft
    page.press(".ph-search-input", "Enter")
    wait_ready(page)
    assert ids(page) == ["pid006"]
    page.evaluate("() => { window.__docKeys = 0; }")
    open_delete(page, "pid006")                                                                    # a menu, then a delete confirmation, with text in the search box
    page.keyboard.press("Escape")                                                                  # 1: the delete confirmation
    assert page.locator(".ph-confirm").count() == 0 and page.locator(".ph-dialog").count() == 1
    assert page.input_value(".ph-search-input") == "casa" and active(page) == "button.ph-btn.ph-kebab@pid006"
    page.click(".ph-row[data-id='pid006'] .ph-kebab")
    assert page.locator(".ph-menu").is_visible()
    page.keyboard.press("Escape")                                                                  # 2: the menu (the confirmation is not open any more)
    assert not page.locator(".ph-menu").is_visible() and page.locator(".ph-dialog").count() == 1
    assert page.input_value(".ph-search-input") == "casa" and active(page) == "button.ph-btn.ph-kebab@pid006"
    page.focus(".ph-search-input")
    page.keyboard.press("Escape")                                                                  # 3: the search text (the focus is in the box)
    assert page.input_value(".ph-search-input") == "" and page.locator(".ph-dialog").count() == 1
    page.keyboard.press("Escape")                                                                  # 4: the dialog
    page.wait_for_selector(".ph-dialog", state="detached")
    assert page.evaluate("() => window.__docKeys") == 0                                           # not one Escape leaked to a listener behind the dialog


# --------------------------------------------------------------------------------------------- D10: the kebab menu

def menu_items(page):
    return texts(page, ".ph-menu:not([hidden]) .ph-menu-item")


def test_the_kebab_opens_with_enter_space_or_arrow_down_and_focuses_the_first_item(page):
    open_history(page, standard_rows())
    for key in ("Enter", "Space", "ArrowDown"):
        page.focus(".ph-row[data-id='pid001'] .ph-kebab")
        page.keyboard.press(key)
        assert page.locator(".ph-menu").is_visible(), key
        assert active(page) == "button.ph-menu-item", key
        assert menu_items(page) == ["Copy link", "Copy proposal name", "Delete draft…"], key
        assert page.get_attribute(".ph-row[data-id='pid001'] .ph-kebab", "aria-expanded") == "true"
        page.keyboard.press("Escape")
        assert page.get_attribute(".ph-row[data-id='pid001'] .ph-kebab", "aria-expanded") == "false"
        assert active(page) == "button.ph-btn.ph-kebab@pid001"


def test_the_menu_is_one_element_in_the_body_and_has_the_right_roles(page):
    open_history(page, standard_rows())
    page.click(".ph-row[data-id='pid001'] .ph-kebab")
    info = page.evaluate("""() => { const m = document.querySelector('.ph-menu'); return { parent: m.parentElement.tagName, count: document.querySelectorAll('.ph-menu').length, role: m.getAttribute('role'),
        items: [...m.querySelectorAll('.ph-menu-item')].map((e) => e.getAttribute('role')), inside: !!m.closest('.ph'), haspopup: document.querySelector('.ph-kebab').getAttribute('aria-haspopup'),
        controls: document.querySelector('.ph-row .ph-kebab[aria-expanded=true]').getAttribute('aria-controls') === m.id }; }""")
    assert info == {"parent": "BODY", "count": 1, "role": "menu", "items": ["menuitem"] * 3, "inside": False, "haspopup": "menu", "controls": True}
    assert page.locator(".ph-menu-item--danger").inner_text() == "Delete draft…"


def test_arrow_keys_home_end_wrap_inside_the_menu_and_tab_closes_it(page):
    open_history(page, standard_rows())
    page.click(".ph-row[data-id='pid001'] .ph-kebab")
    page.keyboard.press("ArrowDown")
    page.keyboard.press("ArrowDown")
    assert page.evaluate("() => document.activeElement.dataset.action") == "delete"
    page.keyboard.press("ArrowDown")
    assert page.evaluate("() => document.activeElement.dataset.action") == "copy-link"            # wraps
    page.keyboard.press("ArrowUp")
    assert page.evaluate("() => document.activeElement.dataset.action") == "delete"
    page.keyboard.press("Home")
    assert page.evaluate("() => document.activeElement.dataset.action") == "copy-link"
    page.keyboard.press("End")
    assert page.evaluate("() => document.activeElement.dataset.action") == "delete"
    page.keyboard.press("Tab")
    assert not page.locator(".ph-menu").is_visible()
    assert focus_in_dialog(page)


def test_a_click_outside_a_scroll_or_a_resize_closes_the_menu(page):
    open_history(page, standard_rows())
    page.click(".ph-row[data-id='pid001'] .ph-kebab")
    page.click(".ph-title")
    assert not page.locator(".ph-menu").is_visible()
    page.click(".ph-row[data-id='pid001'] .ph-kebab")
    page.evaluate("() => { document.querySelector('.ph-body').scrollTop = 40; }")
    page.wait_for_function("() => document.querySelector('.ph-menu').hidden")
    assert active(page) == "button.ph-btn.ph-kebab@pid001"                                        # not left on a menu item that is gone: a next Escape would close the dialog
    page.click(".ph-row[data-id='pid002'] .ph-kebab")
    page.set_viewport_size({"width": 1200, "height": 700})
    page.wait_for_function("() => document.querySelector('.ph-menu').hidden")
    assert active(page) == "button.ph-btn.ph-kebab@pid002"
    page.click(".ph-row[data-id='pid002'] .ph-kebab")
    page.click(".ph-row[data-id='pid002'] .ph-kebab")                                             # the kebab again closes it
    assert not page.locator(".ph-menu").is_visible()


def test_the_menu_stays_inside_the_window_even_for_the_last_row(browser, server):
    p = dom.new_page(browser, server, "/harness.html", width=1280, height=600)
    open_history(p, standard_rows())
    p.evaluate("() => { document.querySelector('.ph-body').scrollTop = 99999; }")
    p.click(".ph-row[data-id='pid014'] .ph-kebab")
    box = p.evaluate("() => { const r = document.querySelector('.ph-menu').getBoundingClientRect(); const k = document.querySelector('.ph-row[data-id=pid014] .ph-kebab').getBoundingClientRect(); return { l: r.left, t: r.top, r: r.right, b: r.bottom, kt: k.top, vw: innerWidth, vh: innerHeight }; }")
    assert 0 <= box["l"] and box["r"] <= box["vw"] and 0 <= box["t"] and box["b"] <= box["vh"], box
    assert box["b"] <= box["kt"] + 1, box                                                        # it opened UPWARD: below the kebab there was no room
    p.keyboard.press("Escape")
    dom.close_page(p)


def test_a_generated_proposal_has_no_delete_item_and_canDelete_can_turn_it_off(page):
    open_history(page, standard_rows())
    page.click(".ph-row[data-id='pid003'] .ph-kebab")
    assert menu_items(page) == ["Copy link", "Copy proposal name"]
    page.keyboard.press("Escape")
    page.click(".ph-row[data-id='pid005'] .ph-kebab")
    assert menu_items(page) == ["Copy link", "Copy proposal name"]
    page.keyboard.press("Escape")
    page.click(".ph-x")
    open_history(page, canDelete="none")
    page.click(".ph-row[data-id='pid001'] .ph-kebab")
    assert menu_items(page) == ["Copy link", "Copy proposal name"]
    page.keyboard.press("Escape")
    page.click(".ph-row[data-id='pid004'] .ph-kebab")                                             # a draft with no title: no "Copy proposal name" either
    assert menu_items(page) == ["Copy link"]


def test_copy_link_and_copy_name_write_to_the_clipboard_and_say_so(page):
    open_history(page, standard_rows())
    origin = page.evaluate("() => location.origin")
    page.click(".ph-row[data-id='pid003'] .ph-kebab")
    page.click(".ph-menu-item[data-action='copy-link']")
    page.wait_for_function("() => document.querySelector('.ph-live').textContent === 'Link copied.'")
    assert page.evaluate("() => window.__clip.writes") == [f"{origin}/?reopen=pid003"]
    assert page.inner_text(".ph-toast") == "Link copied." and page.is_visible(".ph-toast")
    t, d = page.locator(".ph-toast").bounding_box(), page.locator(".ph-dialog").bounding_box()
    assert t["y"] >= d["y"] and t["y"] + t["height"] <= d["y"] + d["height"] and t["x"] >= d["x"] and t["x"] + t["width"] <= d["x"] + d["width"]    # on the dialog, not clipped off its edge
    assert active(page) == "button.ph-btn.ph-kebab@pid003"                                         # the focus went back to where it was
    page.click(".ph-row[data-id='pid003'] .ph-kebab")
    page.click(".ph-menu-item[data-action='copy-name']")
    page.wait_for_function("() => window.__clip.writes.length === 2")
    assert page.evaluate("() => window.__clip.writes[1]") == "EVC-48055_AcmeHVAC_Proposal_20261103"
    page.wait_for_function("() => document.querySelector('.ph-live').textContent === 'Name copied.'")


def test_a_refused_clipboard_falls_back_to_the_old_copy_command_then_to_showing_the_text(page):
    open_history(page, standard_rows())
    page.evaluate("() => { window.__clip.mode = 'reject'; }")
    page.click(".ph-row[data-id='pid003'] .ph-kebab")
    page.click(".ph-menu-item[data-action='copy-link']")
    page.wait_for_function("() => window.__exec.calls === 1")
    assert page.evaluate("() => window.__exec.text").endswith("/?reopen=pid003")                   # the fallback copied the same text
    page.wait_for_function("() => document.querySelector('.ph-live').textContent === 'Link copied.'")
    assert page.locator("textarea[readonly]").count() == 0                                         # the helper textarea is gone again
    page.evaluate("() => { window.__exec.ok = false; }")                                           # and when that fails too
    page.click(".ph-row[data-id='pid003'] .ph-kebab")
    page.click(".ph-menu-item[data-action='copy-link']")
    page.wait_for_selector(".ph-toast-input")
    assert page.input_value(".ph-toast-input").endswith("/?reopen=pid003")
    assert "Select the text instead" in page.inner_text(".ph-toast")
    page.wait_for_function("() => document.querySelector('.ph-live').textContent.startsWith('Couldn’t copy. Select the text instead.')")


def test_where_the_page_has_no_clipboard_api_the_old_copy_command_does_the_job(page):
    # An insecure (plain http) page or an old browser has no navigator.clipboard at all: the copy must still work, not fail without trying.
    open_history(page, standard_rows())
    page.evaluate("() => Object.defineProperty(navigator, 'clipboard', { configurable: true, value: undefined })")
    page.click(".ph-row[data-id='pid003'] .ph-kebab")
    page.click(".ph-menu-item[data-action='copy-link']")
    page.wait_for_function("() => window.__exec.calls === 1")
    assert page.evaluate("() => window.__exec.text").endswith("/?reopen=pid003")
    page.wait_for_function("() => document.querySelector('.ph-live').textContent === 'Link copied.'")
    assert page.evaluate("() => window.__clip.writes") == []


# --------------------------------------------------------------------------------------------- D11: deleting a draft

def open_delete(page, pid):
    page.click(f".ph-row[data-id='{pid}'] .ph-kebab")
    page.click(".ph-menu-item[data-action='delete']")


def test_delete_asks_inside_the_row_and_the_safe_choice_has_the_focus(page):
    open_history(page, standard_rows())
    open_delete(page, "pid001")
    assert page.locator(".ph-confirm").count() == 1
    assert page.locator(".ph-row[data-id='pid001'] .ph-confirm").count() == 1
    assert "is-confirming" in page.get_attribute(".ph-row[data-id='pid001']", "class")
    assert page.inner_text(".ph-confirm-text") == "Delete the draft for Fronteras Del Norte? This can’t be undone."
    assert page.locator(".ph-confirm-text strong").inner_text() == "Fronteras Del Norte"
    assert active(page) == "button.ph-btn.ph-confirm-keep@pid001"
    assert texts(page, ".ph-confirm button") == ["Keep draft", "Delete draft"]
    assert page.evaluate("() => window.__api.log.filter((r) => r.method === 'DELETE').length") == 0
    open_delete(page, "pid004")                                                                    # a draft with no name
    assert page.locator(".ph-confirm").count() == 1                                               # only one question at a time
    assert page.inner_text(".ph-confirm-text") == "Delete this draft? This can’t be undone."


def test_escape_or_keep_draft_puts_the_row_back_and_the_focus_on_its_menu_button(page):
    open_history(page, standard_rows())
    open_delete(page, "pid001")
    page.keyboard.press("Escape")
    assert page.locator(".ph-confirm").count() == 0 and page.locator(".ph-dialog").count() == 1
    assert "is-confirming" not in page.get_attribute(".ph-row[data-id='pid001']", "class")
    assert active(page) == "button.ph-btn.ph-kebab@pid001"
    open_delete(page, "pid001")
    page.click(".ph-confirm-keep")
    assert page.locator(".ph-confirm").count() == 0 and active(page) == "button.ph-btn.ph-kebab@pid001"
    assert page.evaluate("() => window.__api.log.filter((r) => r.method === 'DELETE').length") == 0


def test_confirming_deletes_the_draft_updates_the_counts_and_moves_the_focus_to_the_next_row(page):
    open_history(page, standard_rows())
    open_delete(page, "pid001")
    page.click(".ph-confirm-delete")
    page.wait_for_selector(".ph-row[data-id='pid001']", state="detached")
    d = page.evaluate("() => window.__api.log.filter((r) => r.method === 'DELETE').map((r) => r.path)")
    assert d == ["/api/proposal/pid001"]
    assert ids(page)[0] == "pid002" and len(ids(page)) == 13
    assert texts(page, ".ph-seg-n") == ["13", "3", "10"] and count_text(page) == "13 proposals"
    assert active(page) == "a.ph-btn.ph-btn--primary.ph-open@pid002"
    page.wait_for_function("() => document.querySelector('.ph-live').textContent === 'Draft deleted.'")
    assert page.inner_text(".ph-toast") == "Draft deleted."
    page.focus(".ph-row[data-id='pid014'] .ph-open")
    page.click(".ph-row[data-id='pid010'] .ph-kebab")                                               # a draft in the middle of the list: the focus goes to the row after it
    page.click(".ph-menu-item[data-action='delete']")
    page.click(".ph-confirm-delete")
    page.wait_for_selector(".ph-row[data-id='pid010']", state="detached")
    assert active(page) == "a.ph-btn.ph-btn--primary.ph-open@pid011"


def test_deleting_the_last_row_of_the_list_moves_the_focus_to_the_row_before(page):
    rows = standard_rows()
    rows[13].update(status="draft", total_net=None, tabs_built=[], wizard_step=3)                  # pid014, the oldest, is a draft: nothing comes after it
    open_history(page, rows)
    assert ids(page)[-1] == "pid014"
    page.evaluate("() => { document.querySelector('.ph-body').scrollTop = 99999; }")                # (scrolled there first: a scroll that is still settling would close the menu)
    open_delete(page, "pid014")
    page.click(".ph-confirm-delete")
    page.wait_for_selector(".ph-row[data-id='pid014']", state="detached")
    assert active(page) == "a.ph-btn.ph-btn--primary.ph-open@pid013"


def test_a_draft_with_an_odd_id_is_deleted_by_its_encoded_address(page):
    odd = {**row(4, "Odd id draft", "draft", ago(minutes=12)), "proposal_id": HOSTILE_ID}
    open_history(page, [row(1, "Plain", "generated", ago(minutes=5)), odd])
    page.evaluate("id => [...document.querySelectorAll('.ph-row')].find(r => r.dataset.id === id).querySelector('.ph-kebab').click()", HOSTILE_ID)
    page.click(".ph-menu-item[data-action='delete']")
    page.click(".ph-confirm-delete")
    page.wait_for_function("() => document.querySelectorAll('.ph-row').length === 1")
    sent = page.evaluate("() => window.__api.log.filter((r) => r.method === 'DELETE').map((r) => r.path)")
    assert sent == ["/api/proposal/a%2Fb%3Fc%3Dd%26e%23f%22g"]                                       # one path segment, whatever the id holds


def test_deleting_the_only_draft_in_a_draft_view_shows_the_empty_filter_state(page):
    rows = [r for r in standard_rows() if r["status"] != "draft"] + [row(30, "Only Draft", "draft", ago(minutes=3))]
    open_history(page, rows)
    page.click(".ph-seg--status button[data-status='draft']")
    wait_ready(page)
    open_delete(page, "pid030")
    page.click(".ph-confirm-delete")
    page.wait_for_selector(".ph[data-state='no-results']")
    assert page.inner_text(".ph-state-title") == "No drafts match"
    assert texts(page, ".ph-seg-n") == ["10", "0", "10"]
    assert focus_in_dialog(page)


def test_deleting_the_last_row_of_a_page_fetches_the_list_again_when_more_remain(page):
    rows = many_rows(30)
    rows[0].update(status="draft", total_net=None, tabs_built=[])                                   # the newest row is a draft, and it is alone on the first page
    open_history(page, rows, opts={"pageSize": 1})
    assert ids(page) == ["pid001"]
    before = len(requests(page))
    open_delete(page, "pid001")
    page.click(".ph-confirm-delete")
    page.wait_for_selector(".ph-row[data-id='pid002']")                                            # the page was used up, the list was not: page 1 is fetched again
    assert len(requests(page)) == before + 1 and "page" not in requests(page)[-1]["params"]
    assert ids(page) == ["pid002"] and state(page) == "ready"
    assert active(page) == "a.ph-btn.ph-btn--primary.ph-open@pid002"                                # the keyboard is on the row that took its place


def test_a_refused_delete_says_why_inside_the_row_and_keeps_it(page):
    open_history(page, standard_rows())
    for code, message in ((403, "You can only delete your own drafts."), (400, "Only drafts can be deleted."), (500, "Couldn’t delete this draft. Try again.")):
        page.evaluate("code => { window.__api.deleteStatus = code; }", code)
        open_delete(page, "pid001")
        page.click(".ph-confirm-delete")
        page.wait_for_function("m => document.querySelector('.ph-confirm-text').textContent === m", arg=message)
        assert page.locator(".ph-row[data-id='pid001']").count() == 1
        assert texts(page, ".ph-confirm button") == ["Keep draft", "Try again"]
        assert page.get_attribute(".ph-confirm-text", "role") == "alert"
        assert active(page) == "button.ph-btn.ph-btn--danger.ph-confirm-delete@pid001"              # the keyboard is on Try again, where the person just was
        assert "SECRET-SERVER-DETAIL" not in page.inner_text(".ph-dialog")
        assert page.evaluate("() => document.querySelector('.ph-row').classList.contains('is-busy')") is False
        page.click(".ph-confirm-keep")
    page.evaluate("() => { window.__api.deleteStatus = 200; }")
    open_delete(page, "pid001")
    page.click(".ph-confirm-delete")
    page.wait_for_selector(".ph-row[data-id='pid001']", state="detached")
    assert page.evaluate("() => window.__api.log.filter((r) => r.method === 'DELETE').length") == 4


def test_a_failed_delete_hands_the_keyboard_to_try_again_even_if_it_was_lost_while_waiting(page):
    open_history(page, standard_rows())
    page.evaluate("() => { window.__api.deleteStatus = 500; window.__api.delay = (r) => (r.method === 'DELETE' ? 300 : 0); }")
    open_delete(page, "pid001")
    page.click(".ph-confirm-delete")
    page.evaluate("() => document.activeElement.blur()")                                           # the answer takes a while; meanwhile the focus fell to the page
    page.wait_for_function("() => document.querySelector('.ph-confirm-text').textContent.startsWith('Couldn')")
    assert active(page) == "button.ph-btn.ph-btn--danger.ph-confirm-delete@pid001"


def test_a_draft_that_is_already_gone_is_simply_removed(page):
    open_history(page, standard_rows())
    page.evaluate("() => { window.__api.deleteStatus = 404; }")
    open_delete(page, "pid001")
    page.click(".ph-confirm-delete")
    page.wait_for_selector(".ph-row[data-id='pid001']", state="detached")
    page.wait_for_function("() => document.querySelector('.ph-live').textContent === 'That draft was already deleted.'")


def test_a_failed_delete_request_that_never_reached_the_server_offers_a_retry(page):
    open_history(page, standard_rows())
    page.evaluate("() => { window.__api.fail = (r) => (r.method === 'DELETE' ? 'offline' : null); }")
    open_delete(page, "pid001")
    page.click(".ph-confirm-delete")
    page.wait_for_function("() => document.querySelector('.ph-confirm-text').textContent.startsWith('Couldn')")
    assert page.inner_text(".ph-confirm-delete") == "Try again"
    page.evaluate("() => { window.__api.fail = null; }")
    page.click(".ph-confirm-delete")
    page.wait_for_selector(".ph-row[data-id='pid001']", state="detached")


# --------------------------------------------------------------------------------------------- D12: the duplicate slot and the shell

def test_the_duplicate_button_calls_the_hook_once_per_click_with_the_row_and_the_button(page):
    open_history(page, standard_rows())
    page.click(".ph-row[data-id='pid003'] .ph-dup")
    d = page.evaluate("() => window.__dups.map((x) => ({ id: x.id, keys: x.keys, trigger: x.trigger.className, status: x.proposal.status, client: x.proposal.client_name, opt: x.proposal.option_count }))")
    assert d == [{"id": "pid003", "keys": "controller,proposal,trigger", "trigger": "ph-btn ph-dup", "status": "generated", "client": "Acme HVAC", "opt": 1}]
    assert page.evaluate("() => window.__lastDup.controller === window.__handle.controller") is True
    assert page.locator(".ph-dialog").count() == 1                                                 # it does nothing else: the host decides what happens next
    page.click(".ph-row[data-id='pid003'] .ph-dup")
    assert page.evaluate("() => window.__dups.length") == 2
    page.focus(".ph-row[data-id='pid001'] .ph-dup")
    page.keyboard.press("Enter")
    assert page.evaluate("() => window.__dups.map((x) => x.id)") == ["pid003", "pid003", "pid001"]


def test_the_duplicate_button_is_named_for_the_row_and_its_visible_words_are_in_the_name(page):
    open_history(page, standard_rows())
    label = page.get_attribute(".ph-row[data-id='pid003'] .ph-dup", "aria-label")
    visible = page.eval_on_selector(".ph-row[data-id='pid003'] .ph-dup", "e => [...e.querySelectorAll('.ph-dup-long, .ph-dup-short')].filter((x) => x.offsetParent).map((x) => x.textContent)")
    assert label == "Duplicate for new client: Acme HVAC"
    assert visible == ["Duplicate for new client"] and visible[0] in label                         # the accessible name contains the visible text (WCAG 2.5.3)


def test_a_dialog_opened_by_the_hook_owns_the_keyboard_and_gives_the_focus_back(page):
    open_history(page, standard_rows())
    page.evaluate("""() => { window.__shClosed = []; window.__dupHook = (ctx) => {          // what the duplicate package does: a top dialog on ProposalHistory.shell
        const sh = ProposalHistory.shell({ title: 'Duplicate for a new client', subtitle: 'From ' + ctx.proposal.client_name, top: true, narrow: true, returnFocus: ctx.trigger,
          onClose: (r) => window.__shClosed.push(r === undefined ? 'cancelled' : r), initialFocus: () => sh.body.querySelector('input') });
        sh.body.append(Object.assign(document.createElement('input'), { id: 'dup-name', className: 'ph-input' }));
        window.__sh = sh; }; }""")
    page.click(".ph-row[data-id='pid003'] .ph-dup")
    page.wait_for_selector(".ph-overlay--top .ph-dialog")
    assert page.locator(".ph-overlay").count() == 2
    assert page.evaluate("() => document.activeElement.id") == "dup-name"                          # initialFocus is asked after the body was filled
    page.evaluate("() => { window.__docKeys = 0; }")
    page.keyboard.press("Escape")                                                                  # only the TOP dialog closes
    page.wait_for_selector(".ph-overlay--top", state="detached")
    assert page.locator(".ph-dialog").count() == 1 and page.locator(".ph-row").count() == 14
    assert active(page) == "button.ph-btn.ph-dup@pid003"                                           # back on the button that opened it
    assert page.evaluate("() => window.__shClosed") == ["cancelled"]
    assert page.evaluate("() => getComputedStyle(document.body).overflow") == "hidden"            # the history dialog still holds the scroll lock
    assert page.evaluate("() => window.__docKeys") == 0
    page.keyboard.press("Escape")
    page.wait_for_selector(".ph-dialog", state="detached")
    assert page.evaluate("() => document.body.getAttribute('style')") is None


def test_while_a_top_dialog_is_open_the_history_yields_escape_tab_and_focus(page):
    open_history(page, standard_rows())
    page.evaluate("""() => { const sh = ProposalHistory.shell({ title: 'Top', top: true, narrow: true, returnFocus: document.querySelector('.ph-dup') });
        sh.body.append(Object.assign(document.createElement('input'), { id: 'a-field', className: 'ph-input' }), Object.assign(document.createElement('button'), { id: 'a-btn', type: 'button', textContent: 'Go' })); window.__sh = sh; }""")
    page.wait_for_function("() => document.activeElement.id === 'a-field'")
    for _ in range(12):                                                                             # Tab cycles inside the TOP dialog only
        page.keyboard.press("Tab")
        assert page.evaluate("() => !!document.activeElement.closest('.ph-overlay--top')"), active(page)
    page.evaluate("() => document.querySelector('.ph-search-input').focus()")                       # the history's own box cannot take the focus now
    assert page.evaluate("() => !!document.activeElement.closest('.ph-overlay--top')")
    page.keyboard.press("Escape")
    assert page.locator(".ph-overlay--top").count() == 0 and page.locator(".ph-dialog").count() == 1


def test_the_shell_stack_closes_the_top_one_first_and_keeps_the_scroll_lock_to_the_last(page):
    page.evaluate("""() => { window.__a = ProposalHistory.shell({ title: 'A' }); window.__b = ProposalHistory.shell({ title: 'B', top: true });
        window.__a.body.append(Object.assign(document.createElement('button'), { textContent: 'a' })); window.__b.body.append(Object.assign(document.createElement('button'), { textContent: 'b' })); }""")
    assert page.evaluate("() => getComputedStyle(document.body).overflow") == "hidden"
    page.keyboard.press("Escape")
    assert page.locator(".ph-dialog").count() == 1 and page.evaluate("() => document.querySelector('.ph-title').textContent") == "A"
    assert page.evaluate("() => getComputedStyle(document.body).overflow") == "hidden"
    page.evaluate("() => window.__b.close()")                                                       # closing an already closed one does nothing
    page.keyboard.press("Escape")
    assert page.locator(".ph-dialog").count() == 0
    assert page.evaluate("() => [document.body.getAttribute('style'), getComputedStyle(document.body).overflow]") == [None, "visible"]


def test_shell_busy_blocks_escape_the_x_and_the_backdrop_and_a_pending_form_blocks_only_the_backdrop(page):
    page.evaluate("() => { window.__r = []; window.__s = ProposalHistory.shell({ title: 'Busy', narrow: true, pending: () => window.__pending === true, onClose: (v) => window.__r.push(String(v)) }); window.__s.body.append(Object.assign(document.createElement('input'), { id: 'f' })); }")
    page.evaluate("() => { window.__s.setBusy(true); }")
    assert page.get_attribute(".ph-dialog", "aria-busy") == "true"
    page.keyboard.press("Escape")
    page.click(".ph-x")
    page.mouse.click(4, 4)
    assert page.locator(".ph-dialog").count() == 1
    page.evaluate("() => { window.__s.setBusy(false); window.__pending = true; }")
    assert page.get_attribute(".ph-dialog", "aria-busy") is None
    page.mouse.click(4, 4)                                                                          # work to lose: the backdrop does not close
    assert page.locator(".ph-dialog").count() == 1
    page.keyboard.press("Escape")                                                                   # the explicit actions still do
    page.wait_for_selector(".ph-dialog", state="detached")
    assert page.evaluate("() => window.__r") == ["undefined"]


def test_shell_close_passes_the_result_to_onClose_once(page):
    page.evaluate("() => { window.__r = []; window.__s = ProposalHistory.shell({ title: 'R', onClose: (v) => window.__r.push(v) }); }")
    page.evaluate("() => { window.__s.close({ id: 7 }); window.__s.close({ id: 8 }); }")
    assert page.evaluate("() => window.__r") == [{"id": 7}]
    assert page.evaluate("() => window.__s.dialog.isConnected") is False


def test_reload_with_a_highlight_puts_the_filters_back_flashes_the_row_and_focuses_it(page):
    open_history(page, standard_rows())
    page.click(".ph-seg--status button[data-status='generated']")
    wait_ready(page)
    page.fill(".ph-search-input", "toyota")
    page.press(".ph-search-input", "Enter")
    wait_ready(page)
    page.evaluate("() => window.__api.data.unshift({ ...window.__api.data[0], proposal_id: 'pidNEW', client_name: 'Fresh Copy', status: 'draft', updated_at: '2026-11-11T15:29:30+00:00', total_net: null })")
    page.evaluate("() => window.__handle.controller.reload({ highlightId: 'pidNEW' })")
    page.wait_for_selector(".ph-row[data-id='pidNEW'].is-new")
    assert ids(page)[0] == "pidNEW"
    assert page.input_value(".ph-search-input") == ""
    assert page.eval_on_selector(".ph-seg--status button[data-status='all']", "e => e.getAttribute('aria-pressed')") == "true"
    assert active(page) == "a.ph-btn.ph-btn--primary.ph-open@pidNEW"
    page.wait_for_function("() => !document.querySelector('.ph-row.is-new')", timeout=5000)


def test_the_controller_reports_its_state_and_can_be_driven(page):
    open_history(page, standard_rows())
    s = page.evaluate("() => window.__handle.controller.getState()")
    assert s["phase"] == "ready" and s["status"] == "all" and s["sort"] == "updated" and s["dir"] == "desc" and s["query"] == ""
    assert s["total"] == 14 and len(s["rows"]) == 14 and s["facets"] == {"all": 14, "draft": 4, "generated": 10}
    page.evaluate("() => { const s = window.__handle.controller.getState(); s.rows.length = 0; s.facets.all = 99; }")           # a copy: changing it changes nothing
    assert page.evaluate("() => window.__handle.controller.getState().rows.length") == 14
    page.evaluate("() => window.__handle.controller.focusSearch()")
    assert active(page).startswith("input.ph-search-input")
    before = len(requests(page))
    page.evaluate("() => window.__handle.controller.reload()")
    wait_ready(page)
    assert len(requests(page)) == before + 1
    page.evaluate("() => window.__handle.controller.destroy()")                                     # for a dialog, destroy closes it
    assert page.locator(".ph-dialog").count() == 0
    page.evaluate("() => window.__handle.controller.reload()")                                      # and a destroyed controller ignores calls
    assert len(requests(page)) == before + 1


# --------------------------------------------------------------------------------------------- D13: where Open goes

def test_open_stays_in_this_tab_when_the_host_says_self_and_opens_a_new_one_when_it_says_blank(page):
    open_history(page, standard_rows(), target="_self")
    a = page.eval_on_selector(".ph-row[data-id='pid003'] .ph-open", "e => [e.getAttribute('href'), e.hasAttribute('target'), e.hasAttribute('rel')]")
    assert a == ["/?reopen=pid003", False, False]
    page.click(".ph-x")
    open_history(page, target="_blank")
    a = page.eval_on_selector(".ph-row[data-id='pid003'] .ph-open", "e => [e.getAttribute('href'), e.getAttribute('target'), e.getAttribute('rel')]")
    assert a == ["/?reopen=pid003", "_blank", "noopener"]


def test_the_target_is_decided_again_at_the_click(page):
    page.evaluate("() => { window.__t = '_self'; }")
    use(page, standard_rows())
    page.evaluate("() => { window.__handle = ProposalHistory.openDialog({ now: () => window.__api.now, linkTarget: () => window.__t }); }")
    page.wait_for_selector(".ph-row")
    assert page.eval_on_selector(".ph-row[data-id='pid003'] .ph-open", "e => e.hasAttribute('target')") is False
    page.evaluate("() => { window.__t = '_blank'; document.querySelector('.ph-row[data-id=pid003] .ph-open').addEventListener('click', (e) => e.preventDefault()); }")
    page.click(".ph-row[data-id='pid003'] .ph-open")
    assert page.eval_on_selector(".ph-row[data-id='pid003'] .ph-open", "e => [e.getAttribute('target'), e.getAttribute('rel')]") == ["_blank", "noopener"]


# --------------------------------------------------------------------------------------------- D14: errors

def test_a_server_error_says_so_in_plain_words_without_the_servers_text_and_try_again_recovers(page):
    use(page, standard_rows())
    page.evaluate("() => { window.__api.fail = () => ({ status: 500 }); }")
    open_history(page, wait=".ph-state")
    page.wait_for_selector(".ph[data-state='error']")
    assert page.get_attribute(".ph-state", "role") == "alert"
    assert page.inner_text(".ph-state-title") == "Couldn’t load your proposals"
    assert page.inner_text(".ph-state-text") == "The server had a problem. Try again in a moment. Nothing has been lost."
    assert "SECRET-SERVER-DETAIL" not in page.inner_text(".ph-dialog") and "500" not in page.inner_text(".ph-dialog")
    assert page.logged == ["ProposalHistory: the list request failed (HTTP 500)"]
    assert count_text(page) == "" and page.locator(".ph-row").count() == 0
    page.evaluate("() => { window.__api.fail = null; }")
    page.click(".ph-retry")
    page.wait_for_selector(".ph[data-state='ready']")
    assert page.locator(".ph-row").count() == 14 and active(page).startswith("input.ph-search-input")


def test_each_kind_of_failure_has_its_own_sentence(page):
    use(page, standard_rows())
    cases = [({"status": 403}, "You don’t have access to this list."), ({"status": 429}, "Too many requests. Wait a moment, then try again."),
             ({"status": 422}, "Something went wrong. Try again in a moment."), ({"status": 503}, "The server had a problem. Try again in a moment. Nothing has been lost."),
             ("offline", "Check your connection and try again.")]
    open_history(page, wait=None)
    for fail, text in cases:
        page.evaluate("f => { window.__api.fail = () => f; }", fail)
        page.evaluate("() => window.__handle.controller.reload({ keepRows: false })")
        page.wait_for_function("t => (document.querySelector('.ph-state-text') || {}).textContent === t", arg=text)


def test_a_401_is_quiet_because_the_session_guard_is_already_signing_the_person_in_again(page):
    use(page, standard_rows())
    page.evaluate("() => { window.__api.fail = () => ({ status: 401 }); }")
    open_history(page, wait=".ph-state")
    assert page.inner_text(".ph-state-text") == "Your session ended. Signing you in again…"
    assert page.get_attribute(".ph-state", "role") == "status" and "ph-state--quiet" in page.get_attribute(".ph-state", "class")


def test_a_failed_refresh_replaces_the_rows_it_can_no_longer_vouch_for(page):
    open_history(page, standard_rows())
    page.evaluate("() => { window.__api.fail = () => 'offline'; }")
    page.click(".ph-seg--status button[data-status='draft']")
    page.wait_for_selector(".ph[data-state='error']")
    assert page.locator(".ph-row").count() == 0
    assert page.inner_text(".ph-state-text") == "Check your connection and try again."


def test_an_answer_that_is_not_the_expected_shape_is_an_empty_list_not_a_crash(page):
    use(page, standard_rows())
    page.evaluate("() => { const f = window.fetch; window.fetch = async () => new Response(JSON.stringify({ proposals: 'nope', facets: { all: 'x' }, total_count: 'many' }), { status: 200 }); }")
    open_history(page, wait=".ph-state")
    assert state(page) == "empty"


# --------------------------------------------------------------------------------------------- D15: the first-time state

def test_the_first_time_empty_state_hides_the_tools_and_its_button_runs_the_action(page):
    open_history(page, [], wait=EMPTY, emptyAction=True)
    assert state(page) == "empty"
    assert page.inner_text(".ph-state-title") == "No proposals yet"
    assert page.inner_text(".ph-state-text") == "Proposals you save or generate show up here, newest first. Drafts save by themselves while you work."
    assert not page.is_visible(".ph-search-input") and not page.is_visible(".ph-seg--status") and not page.is_visible(".ph-select")
    page.click(".ph-empty-action")
    assert page.locator(".ph-dialog").count() == 0                                                  # it closed first ...
    assert page.evaluate("() => [window.__emptyRan, document.activeElement.id]") == [1, "other"]    # ... so the action's own focus is the one that stays


def test_the_empty_state_has_no_button_when_the_host_gave_no_action(page):
    open_history(page, [], wait=EMPTY)
    assert page.locator(".ph-state .ph-btn").count() == 0
    assert focus_in_dialog(page)


# --------------------------------------------------------------------------------------------- D16: layout at every width

def layout_facts(page):
    return page.evaluate("""() => {
      const q = (s) => [...document.querySelectorAll(s)];
      const vis = (e) => e.getClientRects().length > 0;
      const comp = document.querySelector('.ph').getBoundingClientRect().width;
      const body = document.querySelector('.ph-body');
      const small = [];
      q('.ph button, .ph a.ph-btn, .ph select, .ph input, .ph-x').forEach((e) => { if (!vis(e) || e.closest('.ph-sr')) return; const r = e.getBoundingClientRect(); if (r.height < 35.5 || r.width < 35.5) small.push(e.className + ' ' + Math.round(r.width) + 'x' + Math.round(r.height)); });
      const row = document.querySelector('.ph-row');
      const cols = row ? getComputedStyle(row).gridTemplateColumns.split(' ').length : 0;
      const strip = document.querySelector('.ph-cols');
      let aligned = null;
      if (strip && vis(strip) && row) {
        const heads = [...strip.children].filter(vis).map((e) => e.getBoundingClientRect().left);
        const who = strip.querySelector('.ph-col--who');                                     // "who" is a column of its own only from 1180px (admin); below, it sits under the client
        const names = ['ph-client', ...(who && vis(who) ? ['ph-who'] : []), 'ph-flight', 'ph-value', 'ph-status', 'ph-edited', 'ph-actions'];
        const cells = names.map((n) => row.querySelector('.' + n)).filter((e) => e && vis(e)).map((e) => e.getBoundingClientRect().left);
        aligned = { heads: heads.map(Math.round), cells: cells.map(Math.round), ok: heads.length === cells.length && heads.every((x, i) => Math.abs(x - cells[i]) <= 1) };
      }
      return { comp: Math.round(comp), cols, strip: !!(strip && vis(strip)), pageH: document.documentElement.scrollWidth - document.documentElement.clientWidth, bodyH: body.scrollWidth - body.clientWidth, small, aligned,
               rowH: row ? Math.round(row.getBoundingClientRect().height) : 0 };
    }""")


def expected_cols(comp, admin):
    if admin:
        return 2 if comp < 520 else 3 if comp < 950 else 7 if comp >= 1180 else 6
    return 2 if comp < 520 else 3 if comp < 1080 else 6


BIG_FACETS = {"all": 12044, "draft": 3120, "generated": 8924}


@pytest.mark.parametrize("width", [360, 390, 640, 768, 1024, 1100, 1280, 1440])
def test_the_planner_dialog_holds_together_at_every_width(browser, server, width):
    p = dom.new_page(browser, server, "/harness.html", width=width, height=860)
    open_history(p, standard_rows())
    f = layout_facts(p)
    assert f["pageH"] <= 0 and f["bodyH"] <= 0, f
    assert f["cols"] == expected_cols(f["comp"], False), f
    assert f["strip"] == (f["comp"] >= 1080), f
    assert f["small"] == [], f
    if f["aligned"]:
        assert f["aligned"]["ok"], f["aligned"]
    dom.close_page(p)


@pytest.mark.parametrize("width", [320, 360, 390])
def test_five_digit_counts_do_not_push_the_filter_off_a_phone(browser, server, width):
    p = dom.new_page(browser, server, "/harness.html", width=width, height=800)
    use(p, standard_rows())
    p.evaluate("() => { const o = window.fetch; window.fetch = async (u, i) => { const r = await o(u, i); const d = await r.clone().json(); d.facets = { all: 12044, draft: 3120, generated: 8924 }; d.total_count = 12044; return new Response(JSON.stringify(d)); }; }")
    open_history(p)
    assert texts(p, ".ph-seg-n") == ["12,044", "3,120", "8,924"]
    f = layout_facts(p)
    assert f["pageH"] <= 0 and f["bodyH"] <= 0 and f["small"] == [], f
    right = p.evaluate("() => Math.max(...[...document.querySelectorAll('.ph-seg--status button')].map((b) => b.getBoundingClientRect().right)) <= document.querySelector('.ph').getBoundingClientRect().right")
    assert right is True
    dom.close_page(p)


@pytest.mark.parametrize("width", [360, 390, 640, 768, 1024, 1100, 1280, 1440, 1600])
def test_the_admin_mount_holds_together_at_every_width_and_admin_css_does_not_reach_into_it(browser, server, width):
    p = dom.new_page(browser, server, "/harness_admin.html", width=width, height=860)
    mount_admin(p, standard_rows(), opts={"scope": "all"})
    f = layout_facts(p)
    assert f["pageH"] <= 0 and f["bodyH"] <= 0, f
    assert f["cols"] == expected_cols(f["comp"], True), f
    assert f["small"] == [], f
    if f["aligned"]:
        assert f["aligned"]["ok"], f["aligned"]
    dom.close_page(p)
    q = dom.new_page(browser, server, "/harness.html", width=width, height=860)               # the same page without admin.css: the same layout
    mount_admin(q, standard_rows(), opts={"scope": "all"})
    g = layout_facts(q)
    assert (g["cols"], g["rowH"], g["comp"]) == (f["cols"], f["rowH"], f["comp"]), (f, g)
    dom.close_page(q)


def test_the_dialog_is_a_full_screen_sheet_on_a_phone_and_a_card_on_a_laptop(browser, server):
    p = dom.new_page(browser, server, "/harness.html", width=390, height=800)
    open_history(p, standard_rows())
    d = p.eval_on_selector(".ph-dialog", "e => { const r = e.getBoundingClientRect(); return [Math.round(r.width), Math.round(r.height), getComputedStyle(e).borderRadius]; }")
    assert d == [390, 800, "0px"]
    dom.close_page(p)
    p = dom.new_page(browser, server, "/harness.html", width=1280, height=900)
    open_history(p, standard_rows())
    d = p.eval_on_selector(".ph-dialog", "e => { const r = e.getBoundingClientRect(); return [Math.round(r.width), Math.round(r.height)]; }")
    assert d == [1120, 820]                                                                        # 1120 x 820 at most; a shorter window leaves 24px around it
    assert p.evaluate("() => document.querySelector('.ph-body').scrollHeight > document.querySelector('.ph-body').clientHeight") is True     # only the list scrolls ...
    assert p.evaluate("() => { const t = document.querySelector('.ph-tools').getBoundingClientRect().top; document.querySelector('.ph-body').scrollTop = 300; return document.querySelector('.ph-tools').getBoundingClientRect().top === t; }") is True   # ... the search box stays
    dom.close_page(p)


def test_the_header_strip_sticks_while_the_list_scrolls(browser, server):
    p = dom.new_page(browser, server, "/harness.html", width=1280, height=700)
    open_history(p, many_rows(25))
    top = p.evaluate("() => document.querySelector('.ph-cols').getBoundingClientRect().top")
    p.evaluate("() => { document.querySelector('.ph-body').scrollTop = 500; }")
    assert p.evaluate("() => document.querySelector('.ph-cols').getBoundingClientRect().top") == top
    dom.close_page(p)


def test_the_wide_row_is_one_line_tall_enough_for_two_text_lines_and_the_actions_fit(browser, server):
    p = dom.new_page(browser, server, "/harness.html", width=1280, height=860)
    open_history(p, standard_rows())
    fit = p.evaluate("""() => [...document.querySelectorAll('.ph-row')].every((r) => { const a = r.querySelector('.ph-actions').getBoundingClientRect(); const c = r.getBoundingClientRect();
        return a.right <= c.right && a.height <= 40 && [...r.querySelectorAll('.ph-actions > *')].every((e) => e.getBoundingClientRect().right <= a.right + 0.5 && e.getBoundingClientRect().left >= a.left - 0.5); })""")
    assert fit is True
    heights = p.evaluate("() => [...document.querySelectorAll('.ph-row')].map((r) => Math.round(r.getBoundingClientRect().height))")
    assert 60 <= min(heights) and max(heights) <= 140, heights
    dom.close_page(p)


# --------------------------------------------------------------------------------------------- D17: contrast, focus rings

def contrast(page, selector=".ph"):
    return page.evaluate("s => contrastReport(s)", selector)


def test_text_meets_aa_contrast_in_every_state_of_the_planner_dialog(page):
    open_history(page, standard_rows())
    assert contrast(page) == []
    assert contrast(page, ".ph-head") == []
    page.click(".ph-seg--status button[data-status='draft']")
    wait_ready(page)
    assert contrast(page) == []
    page.fill(".ph-search-input", "toyota")
    page.press(".ph-search-input", "Enter")
    page.wait_for_selector(".ph[data-state='no-results']")
    assert contrast(page) == []
    page.fill(".ph-search-input", "")
    page.press(".ph-search-input", "Enter")
    page.click(".ph-seg--status button[data-status='all']")
    wait_ready(page)
    open_delete(page, "pid001")
    assert contrast(page) == []
    page.keyboard.press("Escape")
    page.click(".ph-row[data-id='pid001'] .ph-kebab")
    assert contrast(page, ".ph-menu") == []
    page.keyboard.press("Escape")
    page.click(".ph-row[data-id='pid003'] .ph-kebab")
    page.click(".ph-menu-item[data-action='copy-link']")
    page.wait_for_selector(".ph-toast")
    assert contrast(page) == []


def test_text_meets_aa_contrast_while_loading_when_empty_and_when_it_failed(page):
    use(page, standard_rows(), delay=800)
    open_history(page, wait=None)
    assert state(page) == "loading" and contrast(page) == []
    page.wait_for_selector(".ph-row")
    page.evaluate("() => { window.__api.delay = 0; window.__api.fail = () => ({ status: 500 }); }")
    page.evaluate("() => window.__handle.controller.reload({ keepRows: false })")
    page.wait_for_selector(".ph[data-state='error']")
    assert contrast(page) == []
    page.evaluate("() => { window.__api.fail = null; window.__api.data = []; }")
    page.click(".ph-retry")
    page.wait_for_selector(".ph[data-state='empty']")
    assert contrast(page) == []


def test_text_meets_aa_contrast_in_the_admin_mount_and_the_form_primitives(browser, server):
    p = dom.new_page(browser, server, "/harness_admin.html")
    mount_admin(p, standard_rows(), opts={"scope": "all"})
    assert contrast(p) == []
    p.close()
    q = dom.new_page(browser, server, "/harness.html")
    q.evaluate("() => { window.buildForm(true); }")
    assert contrast(q, ".ph-dialog") == []
    q.close()


def test_every_control_shows_a_focus_ring_for_the_keyboard(page):
    open_history(page, standard_rows())
    stops = page.evaluate("() => [...document.querySelectorAll('.ph-dialog button, .ph-dialog input, .ph-dialog select, .ph-dialog a[href]')].filter((e) => e.offsetParent && !e.disabled).length")
    seen, bare = {}, []
    for _ in range(stops):                                                                         # one full lap of the Tab order (the focus starts in the search box)
        page.keyboard.press("Tab")
        info = page.evaluate("""() => { const a = document.activeElement, s = getComputedStyle(a);
            return { cls: (a.className.split(' ')[0] || a.tagName), ring: s.outlineStyle !== 'none' && parseFloat(s.outlineWidth) >= 2, glow: s.boxShadow !== 'none' }; }""")
        seen[info["cls"]] = info
        if not (info["ring"] or info["glow"]):
            bare.append(info["cls"])
    assert bare == [], bare                                                                        # an outline or a glow on every stop, never nothing
    assert {"ph-x", "ph-search-input", "ph-sort", "ph-btn"} <= set(seen), sorted(seen)
    assert "BUTTON" in seen                                                                        # the status filter buttons carry no class of their own


# --------------------------------------------------------------------------------------------- D18: reduced motion

def anim_names(page):
    return page.evaluate("""() => {
      const n = (e, p) => e ? getComputedStyle(e, p).animationName : 'missing';
      return { overlay: n(document.querySelector('.ph-overlay')), skel: n(document.querySelector('.ph-skel-row > span')),
               progress: n(document.querySelector('.ph-progress'), '::after'), flash: n(document.querySelector('.ph-row.is-new')),
               bar: getComputedStyle(document.querySelector('.ph-progress'), '::after').width, barOpacity: getComputedStyle(document.querySelector('.ph-progress'), '::after').opacity,
               strip: getComputedStyle(document.querySelector('.ph-progress')).width };
    }""")


def run_motion_probe(page):
    use(page, standard_rows(), delay=300)
    open_history(page, wait=None)
    page.wait_for_selector(".ph-skel-row")
    during_load = anim_names(page)
    page.wait_for_selector(".ph-row")
    page.evaluate("() => { window.__api.delay = 600; }")
    page.evaluate("() => window.__handle.controller.reload()")
    page.wait_for_selector(".ph.is-loading")
    during_refresh = anim_names(page)
    wait_ready(page)
    page.evaluate("() => { window.__api.delay = 0; window.__handle.controller.reload({ highlightId: 'pid003' }); }")
    page.wait_for_selector(".ph-row.is-new")
    return during_load, during_refresh, anim_names(page)


def test_with_motion_allowed_the_skeleton_pulses_the_bar_slides_and_the_new_row_flashes(page):
    load, refresh, flash = run_motion_probe(page)
    assert load["skel"] == "ph-pulse" and load["overlay"] == "ph-fade"
    assert refresh["progress"] == "ph-slide"
    assert flash["flash"] == "ph-flash"


def test_for_people_who_ask_for_less_motion_nothing_animates(browser, server):
    p = dom.new_page(browser, server, "/harness.html", reduced_motion="reduce")
    load, refresh, flash = run_motion_probe(p)
    assert load["skel"] == "none" and load["overlay"] == "none"
    assert refresh["progress"] == "none"
    assert refresh["bar"] == refresh["strip"] and refresh["barOpacity"] == "0.5"                  # no sliding, but a still, softer bar across the whole strip: something is loading
    assert flash["flash"] == "none"
    dom.close_page(p)


# --------------------------------------------------------------------------------------------- D19: relative time

def test_relative_times_refresh_every_minute_while_the_list_is_open(page):
    open_history(page, standard_rows())
    ticks = page.evaluate("() => window.__intervals.map((i) => i.ms)")
    assert 60000 in ticks
    assert row_text(page, "pid001", ".ph-edited .ph-main") == "7 min ago"
    page.evaluate("() => { window.__api.now += 5 * 60 * 1000; window.__intervals.filter((i) => i.ms === 60000).forEach((i) => i.fn()); }")
    assert row_text(page, "pid001", ".ph-edited .ph-main") == "12 min ago"
    assert row_text(page, "pid001", ".ph-edited .ph-minor") == "Nov 11, 3:23 PM"                 # the exact time does not move
    page.evaluate("() => { window.__api.now += 50 * 3600 * 1000; window.__intervals.filter((i) => i.ms === 60000).forEach((i) => i.fn()); }")
    assert row_text(page, "pid001", ".ph-edited .ph-main") == "2 days ago"
    page.keyboard.press("Escape")
    page.evaluate("() => { window.__api.now += 99 * 3600 * 1000; }")                                # once closed the timer is gone: nothing left to throw


# --------------------------------------------------------------------------------------------- D20: the admin mount

def test_the_admin_mount_has_a_scope_switch_a_who_cell_compact_buttons_and_no_dialog(browser, server):
    p = dom.new_page(browser, server, "/harness_admin.html", width=1440, height=900)
    rows = standard_rows()
    rows[1]["created_by_email"] = "maria.lopez@entravision.com"
    rows[2]["created_by_email"] = "jorge.r@entravision.com"
    mount_admin(p, rows)                                                                            # scope "mine" first: only the signed-in planner's rows
    assert p.locator(".ph-dialog, .ph-overlay").count() == 0
    assert p.get_attribute(".ph", "class") == "ph ph--admin ph--compact"
    assert p.evaluate("() => getComputedStyle(document.querySelector('.ph-body')).overflowY") == "visible"   # the admin page scrolls as a whole; there is no scroll box inside it
    assert requests(p, "/api/admin/proposals")[0]["params"] == {"page_size": "50", "mine": "true"}
    assert len(ids(p)) == 12 and "pid002" not in ids(p)
    assert texts(p, ".ph-seg--scope button") == ["My proposals", "All proposals"]
    p.click(".ph-seg--scope button[data-scope='all']")
    wait_ready(p)
    assert requests(p, "/api/admin/proposals")[-1]["params"]["mine"] == "false"
    assert len(ids(p)) == 14
    assert p.eval_on_selector_all(".ph-seg--scope button", "els => els.map((e) => e.getAttribute('aria-pressed'))") == ["false", "true"]
    # the who cell: planner, AE, device
    assert row_text(p, "pid002", ".ph-who .ph-main") == "maria.lopez@entravision.com"
    assert row_text(p, "pid002", ".ph-who .ph-minor") == "AE: Jane AE"
    assert row_text(p, "pid002", ".ph-who .ph-device") == "Mac · Chrome 141 · 10.0.0.2"
    assert p.get_attribute(".ph-row[data-id='pid002'] .ph-device", "title") == CHROME_MAC
    assert row_text(p, "pid002", ".ph-status .ph-minor") == "Proposal A · Proposal A (Gross)"       # what was built, for a generated row
    assert p.get_attribute(".ph-row[data-id='pid002'] .ph-status .ph-minor", "title") == "Proposal A, Proposal A (Gross)"
    # compact labels, the full phrase kept for assistive tech
    assert p.eval_on_selector(".ph-row[data-id='pid003'] .ph-dup", "e => [...e.querySelectorAll('.ph-dup-long, .ph-dup-short')].filter((x) => x.offsetParent).map((x) => x.textContent)") == ["Duplicate"]
    assert p.get_attribute(".ph-row[data-id='pid003'] .ph-dup", "aria-label") == "Duplicate for new client: Acme HVAC"
    # new tab by default, and the search box and the sort work here too
    assert p.eval_on_selector(".ph-row[data-id='pid003'] .ph-open", "e => [e.getAttribute('target'), e.getAttribute('rel')]") == ["_blank", "noopener"]
    p.fill(".ph-search-input", "acme")
    p.press(".ph-search-input", "Enter")
    wait_ready(p)
    assert ids(p) == ["pid003"]
    assert requests(p, "/api/admin/proposals")[-1]["params"]["search"] == "acme" and requests(p, "/api/admin/proposals")[-1]["params"]["mine"] == "false"
    dom.close_page(p)


def test_the_admin_mount_offers_delete_only_for_the_signed_in_admins_own_drafts(browser, server):
    p = dom.new_page(browser, server, "/harness_admin.html", width=1440, height=900)
    rows = standard_rows()
    rows[5]["created_by_email"] = "someone.else@entravision.com"                                    # pid006 is a draft of somebody else's
    mount_admin(p, rows, opts={"scope": "all"})
    p.wait_for_function("() => window.__api.log.some((r) => r.path === '/api/me')")
    p.wait_for_timeout(100)
    p.click(".ph-row[data-id='pid001'] .ph-kebab")
    assert menu_items(p) == ["Copy link", "Copy proposal name", "Delete draft…"]
    p.keyboard.press("Escape")
    p.click(".ph-row[data-id='pid006'] .ph-kebab")
    assert menu_items(p) == ["Copy link", "Copy proposal name"]
    p.keyboard.press("Escape")
    p.click(".ph-row[data-id='pid002'] .ph-kebab")                                                  # generated: never
    assert menu_items(p) == ["Copy link", "Copy proposal name"]
    p.keyboard.press("Escape")
    p.click(".ph-row[data-id='pid001'] .ph-kebab")                                                  # and the delete flow works inline here as well
    p.click(".ph-menu-item[data-action='delete']")
    p.click(".ph-confirm-delete")
    p.wait_for_selector(".ph-row[data-id='pid001']", state="detached")
    dom.close_page(p)


def test_whose_draft_it_is_does_not_depend_on_the_case_of_the_email(browser, server):
    # Postgres compares the owner case-insensitively and so does the server's own-draft rule: "Planner@..." and "planner@..." are one person.
    p = dom.new_page(browser, server, "/harness_admin.html", width=1440, height=900)
    p.evaluate("() => { window.__api.me = { email: 'Planner@ENTRAVISION.com', is_admin: true }; }")
    rows = standard_rows()
    rows[0]["created_by_email"] = "PLANNER@entravision.COM"                                         # pid001: the same person, written another way
    mount_admin(p, rows, opts={"scope": "all"})
    p.wait_for_function("() => window.__api.log.some((r) => r.path === '/api/me')")
    p.wait_for_timeout(100)
    p.click(".ph-row[data-id='pid001'] .ph-kebab")
    assert menu_items(p) == ["Copy link", "Copy proposal name", "Delete draft…"]
    dom.close_page(p)


def test_without_an_answer_from_api_me_the_admin_mount_offers_no_delete(browser, server):
    p = dom.new_page(browser, server, "/harness_admin.html", width=1440, height=900)
    use(p, standard_rows())
    p.evaluate("() => { const f = window.fetch; window.fetch = (u, i) => (String(u).indexOf('/api/me') >= 0 ? Promise.reject(new TypeError('x')) : f(u, i)); }")
    mount_admin(p, opts={"scope": "all"})
    p.click(".ph-row[data-id='pid001'] .ph-kebab")
    assert menu_items(p) == ["Copy link", "Copy proposal name"]
    dom.close_page(p)


def test_a_host_can_say_who_is_signed_in_and_a_host_canDelete_wins(browser, server):
    p = dom.new_page(browser, server, "/harness_admin.html", width=1440, height=900)
    use(p, standard_rows())
    p.evaluate("() => { window.mountPH({ opts: { scope: 'all', me: 'PLANNER@entravision.com' } }); }")
    p.wait_for_selector(".ph-row")
    assert [r for r in requests(p, "/api/me")] == []                                                # it did not have to ask
    p.click(".ph-row[data-id='pid001'] .ph-kebab")
    assert "Delete draft…" in menu_items(p)                                                         # the comparison ignores case
    p.keyboard.press("Escape")
    p.evaluate("() => { window.__ctl.destroy(); }")
    assert p.locator(".ph").count() == 0 and p.locator(".ph-menu").count() == 0
    p.evaluate("() => { window.mountPH({ opts: { scope: 'all', canDelete: () => false } }); }")
    p.wait_for_selector(".ph-row")
    p.click(".ph-row[data-id='pid001'] .ph-kebab")
    assert "Delete draft…" not in menu_items(p)
    dom.close_page(p)


def test_a_mounted_list_closes_its_own_menu_and_confirmation_with_escape_and_mounting_again_replaces_it(browser, server):
    p = dom.new_page(browser, server, "/harness_admin.html", width=1440, height=900)
    mount_admin(p, standard_rows(), opts={"scope": "all", "me": "planner@entravision.com"})
    p.click(".ph-row[data-id='pid001'] .ph-kebab")
    p.keyboard.press("Escape")
    assert not p.locator(".ph-menu").is_visible() and active(p) == "button.ph-btn.ph-kebab@pid001"
    p.click(".ph-row[data-id='pid001'] .ph-kebab")
    p.click(".ph-menu-item[data-action='delete']")
    p.keyboard.press("Escape")
    assert p.locator(".ph-confirm").count() == 0
    p.fill(".ph-search-input", "acme")
    p.keyboard.press("Escape")
    assert p.input_value(".ph-search-input") == ""
    p.evaluate("() => window.mountPH({ opts: { scope: 'all' } })")                                  # the same host again: one list, not two
    p.wait_for_selector(".ph-row")
    assert p.locator(".ph").count() == 1
    dom.close_page(p)


# --------------------------------------------------------------------------------------------- miscellany

def test_the_stylesheet_alone_is_enough_the_component_needs_no_page_script(browser, server):
    p = dom.new_page(browser, server, "/harness.html")
    assert p.evaluate("() => [typeof app, typeof adminState, typeof state, typeof escapeHtml, typeof money]") == ["undefined"] * 5
    open_history(p, standard_rows())
    assert p.locator(".ph-row").count() == 14
    dom.close_page(p)


def test_a_slow_hand_off_of_focus_never_leaves_it_on_the_body_after_a_refresh_rebuilt_the_row(page):
    open_history(page, standard_rows())
    page.focus(".ph-row[data-id='pid003'] .ph-dup")
    page.evaluate("() => window.__handle.controller.reload()")
    wait_ready(page)
    assert focus_in_dialog(page)


# --------------------------------------------------------------------------------------------- what the layout rules promise (each is a rule a stylesheet edit could silently break)

def test_screen_reader_text_takes_no_room_and_is_never_drawn(page):
    open_history(page, standard_rows())
    boxes = page.eval_on_selector_all(".ph-sr", "els => els.map((e) => { const r = e.getBoundingClientRect(); return [Math.round(r.width), Math.round(r.height), getComputedStyle(e).position]; })")
    assert len(boxes) > 50 and all(w <= 1 and h <= 1 and pos == "absolute" for w, h, pos in boxes), [b for b in boxes if b[0] > 1 or b[1] > 1][:5]


def test_each_status_badge_carries_a_shape_as_well_as_a_colour(page):
    open_history(page, standard_rows())
    draft = page.eval_on_selector(".ph-badge--draft", "e => { const s = getComputedStyle(e, '::before'); return [s.content, s.width, s.height, s.borderTopStyle, s.backgroundColor]; }")
    done = page.eval_on_selector(".ph-badge--generated", "e => { const s = getComputedStyle(e, '::before'); return [s.content, s.width, s.height, s.borderTopStyle, s.backgroundColor]; }")
    assert draft[:4] == ['""', "8px", "8px", "solid"] and draft[4] == "rgba(0, 0, 0, 0)"          # an open ring ...
    assert done[:3] == ['""', "8px", "8px"] and done[3] == "none" and done[4] != "rgba(0, 0, 0, 0)"   # ... or a filled dot: the two differ without any colour


def test_a_long_copied_from_name_is_cut_with_an_ellipsis_not_clipped_mid_letter(browser, server):
    p = dom.new_page(browser, server, "/harness.html", width=1280, height=860)
    rows = standard_rows()
    rows[1]["duplicated_from"] = {"proposal_id": "x", "client_name": "An extraordinarily long client name that cannot possibly fit inside one chip on one row", "notion_id": "EVC-1"}
    open_history(p, rows)
    info = p.eval_on_selector(".ph-row[data-id='pid002'] .ph-chip-text", "e => [getComputedStyle(e).textOverflow, e.scrollWidth > e.clientWidth, e.getBoundingClientRect().right <= e.closest('.ph-chip').getBoundingClientRect().right]")
    assert info == ["ellipsis", True, True]
    assert p.get_attribute(".ph-row[data-id='pid002'] .ph-chip", "title").startswith("Copied from An extraordinarily long client name")     # the whole name stays in the tooltip
    dom.close_page(p)


def test_the_menu_is_on_top_of_the_dialog_and_of_the_rows(page):
    open_history(page, standard_rows())
    page.click(".ph-row[data-id='pid002'] .ph-kebab")
    top = page.evaluate("""() => { const m = document.querySelector('.ph-menu'), r = m.querySelector('.ph-menu-item').getBoundingClientRect();
        const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2); return [!!hit && m.contains(hit), getComputedStyle(m).position, getComputedStyle(m).zIndex]; }""")
    assert top == [True, "fixed", "800"]


def test_a_resize_that_changes_nothing_leaves_the_menu_open(page):
    open_history(page, standard_rows())
    page.click(".ph-row[data-id='pid001'] .ph-kebab")
    page.evaluate("() => window.dispatchEvent(new Event('resize'))")                               # mobile browsers send these when their address bar slides
    page.wait_for_timeout(100)
    assert page.locator(".ph-menu").is_visible() and active(page) == "button.ph-menu-item"


def test_the_copy_link_item_can_be_left_out(page):
    open_history(page, standard_rows(), opts={"copyLink": False})
    page.click(".ph-row[data-id='pid001'] .ph-kebab")
    assert menu_items(page) == ["Copy proposal name", "Delete draft…"]


def test_the_confirmation_covers_exactly_the_row_it_asks_about(page):
    open_history(page, standard_rows())
    open_delete(page, "pid001")
    r = page.evaluate("""() => { const row = document.querySelector('.ph-row[data-id=pid001]').getBoundingClientRect(), c = document.querySelector('.ph-confirm').getBoundingClientRect();
        const hit = document.elementFromPoint(row.left + 300, row.top + row.height / 2); return [Math.round(c.left - row.left), Math.round(c.top - row.top), Math.round(c.width - row.width), Math.round(c.height - row.height), !!hit.closest('.ph-confirm')]; }""")
    assert r[:3] == [0, 0, 0] and abs(r[3]) <= 1 and r[4] is True                                  # same box as the row (less its 1px bottom rule); the cells under it cannot be clicked while it asks


def test_the_form_dialog_is_above_the_history_and_a_tall_form_scrolls_inside_a_short_window(browser, server):
    p = dom.new_page(browser, server, "/harness.html", width=1280, height=420)
    open_history(p, standard_rows())
    p.evaluate("""() => { const sh = ProposalHistory.shell({ title: 'Duplicate for a new client', top: true, narrow: true });
        const form = Object.assign(document.createElement('form'), { className: 'ph-form' });
        for (let i = 0; i < 12; i++) form.append(Object.assign(document.createElement('input'), { className: 'ph-input', id: 'f' + i }));
        const foot = Object.assign(document.createElement('div'), { className: 'ph-form-foot' }); foot.append(Object.assign(document.createElement('button'), { className: 'ph-btn', id: 'cancel', type: 'button', textContent: 'Cancel' }));
        sh.body.append(form, foot); }""")
    p.wait_for_selector(".ph-overlay--top .ph-form")
    f = p.evaluate("""() => { const d = document.querySelector('.ph-overlay--top .ph-dialog').getBoundingClientRect(), form = document.querySelector('.ph-overlay--top .ph-form'),
        foot = document.querySelector('.ph-form-foot').getBoundingClientRect(), field = document.getElementById('f0').getBoundingClientRect();
        const hit = document.elementFromPoint(field.left + 10, field.top + 10);
        return { fits: d.top >= 0 && d.bottom <= innerHeight, scrolls: form.scrollHeight > form.clientHeight, footInside: foot.bottom <= d.bottom + 0.5 && foot.top >= d.top,
                 onTop: !!hit && !!hit.closest('.ph-overlay--top'), fieldW: Math.round(field.width), dialogW: Math.round(d.width) }; }""")
    assert f["fits"] and f["scrolls"] and f["footInside"] and f["onTop"], f
    reach = p.evaluate("""() => { const form = document.querySelector('.ph-overlay--top .ph-form'); form.scrollTop = form.scrollHeight;     // scrolled to the end, the last field is in view
        const last = document.getElementById('f11').getBoundingClientRect(), box = form.getBoundingClientRect();
        return { moved: form.scrollTop > 0, lastInView: last.bottom <= box.bottom + 1, overflowY: getComputedStyle(form).overflowY }; }""")
    assert reach == {"moved": True, "lastInView": True, "overflowY": "auto"}, reach
    assert f["dialogW"] == 560                                                                      # the narrow shell is a 560px card
    dom.close_page(p)


def test_on_a_phone_the_form_dialog_is_a_sheet_sitting_on_the_bottom_edge(browser, server):
    p = dom.new_page(browser, server, "/harness.html", width=390, height=800)
    p.evaluate("""() => { const sh = ProposalHistory.shell({ title: 'Duplicate for a new client', top: true, narrow: true });
        sh.body.append(Object.assign(document.createElement('input'), { className: 'ph-input', id: 'only' })); }""")
    p.wait_for_selector(".ph-dialog--form")
    d = p.eval_on_selector(".ph-dialog--form", "e => { const r = e.getBoundingClientRect(), s = getComputedStyle(e); return [Math.round(r.width), Math.round(r.bottom), s.borderTopLeftRadius, s.borderBottomLeftRadius]; }")
    assert d == [390, 800, "24px", "0px"]
    assert p.evaluate("() => document.activeElement.classList.contains('ph-dialog')")               # on a phone the dialog itself takes the focus (the on-screen keyboard stays down), even if the host named a field
    dom.close_page(p)


def test_the_two_tools_of_the_planner_dialog_are_the_search_and_the_filter_and_both_are_hidden_on_first_use(page):
    open_history(page, [], wait=EMPTY)
    hidden = page.evaluate("() => ['.ph-tools', '.ph-search', '.ph-seg--status', '.ph-status-row', '.ph-select-wrap', '.ph-progress'].map((s) => document.querySelector(s).getClientRects().length === 0)")
    assert hidden == [True, True, True, True, True, True]
    assert page.evaluate("() => getComputedStyle(document.querySelector('.ph-body')).borderTopWidth") == "0px"


def test_the_admin_list_keeps_its_scope_switch_when_there_is_nothing_to_list_and_says_what_to_do(browser, server):
    p = dom.new_page(browser, server, "/harness_admin.html", width=1440, height=900)
    mount_admin(p, [], wait=EMPTY)
    assert p.inner_text(".ph-state-text") == "You haven’t saved a proposal yet. Choose All proposals to see everyone’s."
    assert p.is_visible(".ph-seg--scope") and not p.is_visible(".ph-search-input")
    p.evaluate("() => window.__ctl.setScope('all')")                                                # the controller can drive the switch too
    p.wait_for_function("() => window.__api.log.some((r) => r.params.mine === 'false')")
    p.wait_for_function("() => document.querySelector('.ph-state-text') && document.querySelector('.ph-state-text').textContent.startsWith('No proposal has been saved')")
    assert p.evaluate("() => window.__ctl.getState().scope") == "all"
    p.evaluate("() => window.__ctl.setScope('nonsense')")                                           # an unknown scope is ignored
    assert p.evaluate("() => window.__ctl.getState().scope") == "all"
    dom.close_page(p)


# --------------------------------------------------------------------------------------------- layout rules, continued

def test_the_three_layers_stack_history_then_the_form_then_the_menu(page):
    open_history(page, standard_rows())
    page.click(".ph-row[data-id='pid001'] .ph-kebab")                                              # the menu element is created by its first use
    page.keyboard.press("Escape")
    page.evaluate("() => ProposalHistory.shell({ title: 'Top', top: true, narrow: true })")
    z = page.evaluate("() => [...[...document.querySelectorAll('.ph-overlay')].map((e) => getComputedStyle(e).zIndex), getComputedStyle(document.querySelector('.ph-menu')).zIndex]")
    assert z == ["600", "700", "800"]


@pytest.mark.parametrize("vw, cols", [(519, 2), (520, 3), (1127, 3), (1128, 6)])
def test_the_planner_layout_switches_at_exactly_the_documented_widths(browser, server, vw, cols):
    p = dom.new_page(browser, server, "/harness.html", width=vw, height=860)
    open_history(p, standard_rows())
    f = layout_facts(p)
    assert f["comp"] == (vw if vw <= 640 else vw - 48) and f["cols"] == cols and f["strip"] == (cols == 6), f      # 1080px of component = 1128px window less the dialog's 24px margins
    dom.close_page(p)


@pytest.mark.parametrize("w, cols", [(519, 2), (520, 3), (949, 3), (950, 6), (1179, 6), (1180, 7)])
def test_the_admin_layout_switches_at_exactly_the_documented_widths(browser, server, w, cols):
    p = dom.new_page(browser, server, "/harness_admin.html", width=1600, height=860)
    p.evaluate("w => { document.getElementById('host').style.width = w + 'px'; }", w)
    mount_admin(p, standard_rows(), opts={"scope": "all"})
    f = layout_facts(p)
    assert f["comp"] == w and f["cols"] == cols and f["pageH"] <= 0 and f["small"] == [], f
    if f["aligned"]:
        assert f["aligned"]["ok"], f["aligned"]
    dom.close_page(p)


@pytest.mark.parametrize("width", [360, 390, 414, 640])
def test_open_duplicate_and_the_menu_button_share_one_row_on_a_phone(browser, server, width):
    p = dom.new_page(browser, server, "/harness.html", width=width, height=860)
    open_history(p, standard_rows())
    tops = p.evaluate("() => [...document.querySelectorAll('.ph-row')].map((r) => [...r.querySelectorAll('.ph-actions > *')].map((e) => Math.round(e.getBoundingClientRect().top)))")
    assert all(max(t) - min(t) <= 1 for t in tops), tops
    assert p.evaluate("w => document.querySelector('.ph-dup > [aria-hidden]').offsetParent !== null", width) == (width >= 400)       # below 400px the glyph gives its room to the label
    dom.close_page(p)


def test_the_admins_buttons_share_one_row_in_a_narrow_column_too(browser, server):
    p = dom.new_page(browser, server, "/harness_admin.html", width=390, height=860)
    mount_admin(p, standard_rows(), opts={"scope": "all"})
    tops = p.evaluate("() => [...document.querySelectorAll('.ph-row')].map((r) => [...r.querySelectorAll('.ph-actions > *')].map((e) => Math.round(e.getBoundingClientRect().top)))")
    assert all(max(t) - min(t) <= 1 for t in tops), tops
    dom.close_page(p)


@pytest.mark.parametrize("width", [390, 1280])
def test_a_very_long_client_name_takes_two_lines_and_the_title_line_takes_one(browser, server, width):
    p = dom.new_page(browser, server, "/harness.html", width=width, height=860)
    rows = standard_rows()
    rows[0]["client_name"] = "Extraordinarily " * 25
    rows[0]["proposal_title"] = "EVC-48213_" + "VeryLongProposalTitle" * 12
    open_history(p, rows)
    lines = p.evaluate("""() => { const n = document.querySelector('.ph-row .ph-client-name'), t = document.querySelector('.ph-row .ph-subline');
        return [Math.round(n.getBoundingClientRect().height / parseFloat(getComputedStyle(n).lineHeight)), Math.round(t.getBoundingClientRect().height / parseFloat(getComputedStyle(t).lineHeight)), n.scrollHeight > n.clientHeight, t.scrollWidth > t.clientWidth]; }""")
    assert lines == [2, 1, True, True]                                                             # cut at two lines / one line (with an ellipsis); the whole text stays in the tooltip
    assert p.get_attribute(".ph-row .ph-client-name", "title").startswith("Extraordinarily Extraordinarily")
    f = layout_facts(p)
    assert f["pageH"] <= 0 and f["bodyH"] <= 0, f
    dom.close_page(p)


def test_the_typed_words_are_marked_so_they_show(page):
    open_history(page, standard_rows())
    page.fill(".ph-search-input", "acme")
    page.press(".ph-search-input", "Enter")
    wait_ready(page)
    mark = page.eval_on_selector(".ph-row .ph-client-name mark", "e => { const s = getComputedStyle(e); return [s.backgroundColor, s.textDecorationLine, s.color === getComputedStyle(e.parentElement).color]; }")
    assert mark[0] != "rgba(0, 0, 0, 0)" and mark[1] == "underline" and mark[2] is True              # a tint and an underline, and the text colour stays the page's


def test_the_active_sort_shows_one_arrow_the_way_it_sorts_and_the_others_a_faint_pair(page):
    open_history(page, standard_rows())
    ico = """() => { const b = document.querySelector('.ph-sort[aria-pressed=true]'), i = b.querySelector('.ph-sort-ico');
        return [getComputedStyle(i, '::before').display, getComputedStyle(i, '::after').display, getComputedStyle(b).fontWeight, getComputedStyle(i).opacity]; }"""
    assert page.evaluate(ico) == ["none", "block", "700", "1"]                                      # newest first: the arrow points down
    page.click(".ph-sort[data-sort='updated']")
    wait_ready(page)
    assert page.evaluate(ico) == ["block", "none", "700", "1"]                                      # oldest first: it points up
    other = page.eval_on_selector(".ph-sort[data-sort='client']", "e => { const i = e.querySelector('.ph-sort-ico'); return [getComputedStyle(i, '::before').display, getComputedStyle(i, '::after').display, getComputedStyle(i).opacity, getComputedStyle(e).fontWeight]; }")
    assert other == ["block", "block", "0.45", "400"]


def test_a_row_being_deleted_cannot_be_clicked_and_a_second_delete_is_not_sent(page):
    open_history(page, standard_rows())
    page.evaluate("() => { window.__api.delay = (r) => (r.method === 'DELETE' ? 600 : 0); }")
    open_delete(page, "pid001")
    page.click(".ph-confirm-delete")
    page.wait_for_selector(".ph-row.is-busy")
    assert page.evaluate("() => getComputedStyle(document.querySelector('.ph-row.is-busy')).pointerEvents") == "none"
    assert page.eval_on_selector_all(".ph-confirm button", "els => els.map((e) => e.getAttribute('aria-disabled'))") == ["true", "true"]
    page.keyboard.press("Escape")                                                                  # the request is running: Escape does not pretend to cancel it
    assert page.locator(".ph-confirm").count() == 1 and page.locator(".ph-dialog").count() == 1
    page.keyboard.press("Enter")                                                                   # the focused Delete button, pressed again
    page.wait_for_selector(".ph-row[data-id='pid001']", state="detached")
    assert page.evaluate("() => window.__api.log.filter((r) => r.method === 'DELETE').length") == 1


def test_on_a_phone_the_focused_sheet_shows_no_ring_and_the_confirmation_text_sits_next_to_its_buttons(browser, server):
    p = dom.new_page(browser, server, "/harness.html", width=390, height=800)
    open_history(p, standard_rows())
    assert p.evaluate("() => getComputedStyle(document.activeElement).outlineStyle") == "none"      # the dialog itself has the focus here: a ring around the whole screen would be noise
    open_delete(p, "pid001")
    gap = p.evaluate("""() => { const t = document.querySelector('.ph-confirm-text').getBoundingClientRect(), b = document.querySelector('.ph-confirm-actions').getBoundingClientRect(),
        c = document.querySelector('.ph-confirm').getBoundingClientRect(); return [Math.round(b.top - t.bottom), Math.round(c.height)]; }""")
    assert 0 <= gap[0] <= 24, gap                                                                  # text and buttons are one block in the middle of the strip, not pushed to its two ends
    dom.close_page(p)


def test_in_forced_colors_mode_the_pressed_filter_and_the_badges_keep_their_shapes(browser, server):
    p = dom.new_page(browser, server, "/harness.html", forced_colors="active")
    open_history(p, standard_rows())
    info = p.evaluate("""() => { const b = document.querySelector('.ph-seg--status button[aria-pressed=true]'), s = getComputedStyle(b), u = getComputedStyle(document.querySelector('.ph-badge--draft'), '::before');
        return { adjust: s.forcedColorAdjust, differs: s.backgroundColor !== s.color, shape: [u.width, u.borderTopStyle], badgeAdjust: u.forcedColorAdjust, icoAdjust: getComputedStyle(document.querySelector('.ph-sort-ico'), '::before').forcedColorAdjust }; }""")
    assert info == {"adjust": "none", "differs": True, "shape": ["8px", "solid"], "badgeAdjust": "none", "icoAdjust": "none"}, info
    dom.close_page(p)


def test_the_form_primitives_make_two_columns_a_wide_field_and_a_foot_with_its_buttons_at_the_right(browser, server):
    p = dom.new_page(browser, server, "/harness.html", width=1280, height=900)
    p.evaluate("() => { window.buildForm(false); }")
    facts = p.evaluate("""() => { const r = (id) => document.getElementById(id).getBoundingClientRect(), grid = document.querySelector('.ph-form-grid').getBoundingClientRect(),
        foot = document.querySelector('.ph-form-foot').getBoundingClientRect(), last = document.querySelector('.ph-form-foot .ph-btn--primary').getBoundingClientRect(), dialog = document.querySelector('.ph-dialog--form').getBoundingClientRect();
      const label = getComputedStyle(document.querySelector('.ph-flabel'));
      return { wideFull: Math.round(r('f1').width) === Math.round(grid.width), pairs: Math.round(r('f2').top) === Math.round(r('f5').top) && r('f5').left > r('f2').right, third: Math.round(r('f3').top) === Math.round(r('f4').top) && r('f3').top > r('f2').top,
        input: [Math.round(r('f1').height), Math.round(r('f2').height)], footRight: Math.round(foot.right - last.right), footFlush: Math.round(dialog.bottom - foot.bottom),
        label: [label.textTransform, label.fontFamily.indexOf('JetBrains') >= 0 || label.fontFamily.indexOf('monospace') >= 0], required: getComputedStyle(document.querySelector('.ph-req')).color }; }""")
    assert facts["wideFull"] and facts["pairs"] and facts["third"], facts
    assert facts["input"] == [40, 40]
    assert facts["footRight"] == 20 and facts["footFlush"] == 0, facts                              # Cancel and Create copy sit at the right with the dialog's 20px margin; the band runs to the card's bottom edge
    assert facts["label"] == ["uppercase", True] and facts["required"] == "rgb(138, 10, 37)"        # mono uppercase labels; the asterisk is the dark red, not the brand red
    p.set_viewport_size({"width": 390, "height": 800})
    narrow = p.evaluate("() => { const r = (id) => document.getElementById(id).getBoundingClientRect(); return r('f3').top > r('f2').top && r('f5').top > r('f2').top && Math.round(r('f2').width) === Math.round(document.querySelector('.ph-form-grid').getBoundingClientRect().width); }")
    assert narrow is True                                                                           # one column on a phone
    dom.close_page(p)


# --------------------------------------------------------------------------------------------- the shell: the corners (each was found by a mutation the tests above let through)

def test_if_the_page_changed_its_own_body_style_meanwhile_only_what_the_lock_set_is_taken_back(page):
    page.evaluate("() => { document.body.style.color = 'rgb(1, 2, 3)'; }")
    open_history(page, standard_rows())
    page.evaluate("() => { document.body.style.backgroundColor = 'rgb(9, 9, 9)'; }")              # something else touched the body while the dialog was open
    page.keyboard.press("Escape")
    s = page.evaluate("() => [document.body.style.overflow, document.body.style.color, document.body.style.backgroundColor, document.body.style.paddingRight]")
    assert s == ["", "rgb(1, 2, 3)", "rgb(9, 9, 9)", ""]                                          # its change and the page's own style stay; our overflow lock is gone


def test_shift_tab_from_the_first_control_wraps_to_the_last_and_tab_from_the_last_to_the_first(page):
    open_history(page, standard_rows())
    page.evaluate("""() => { const f = [...document.querySelector('.ph-dialog').querySelectorAll('button, input, select, textarea, a[href], [tabindex]')].filter((e) => !e.disabled && e.tabIndex >= 0 && e.getClientRects().length);
        window.__first = f[0]; window.__last = f[f.length - 1]; }""")
    assert page.evaluate("() => window.__first.classList.contains('ph-x') && window.__last.classList.contains('ph-kebab')")
    page.evaluate("() => window.__first.focus()")
    page.keyboard.press("Shift+Tab")
    assert page.evaluate("() => document.activeElement === window.__last")                          # round the back, not pulled in from outside
    page.keyboard.press("Tab")
    assert page.evaluate("() => document.activeElement === window.__first")


def test_escape_is_handled_by_the_dialog_so_the_browser_does_not_also_act_on_it(page):
    open_history(page, standard_rows())
    page.evaluate("""() => { const e = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true });
        document.querySelector('.ph-search-input').dispatchEvent(e); window.__prevented = e.defaultPrevented; }""")
    assert page.evaluate("() => window.__prevented") is True                                       # a type=search box would clear itself on Escape, a full-screen page would leave it


def test_a_drag_that_starts_inside_the_dialog_and_ends_on_the_backdrop_does_not_close_it(page):
    open_history(page, standard_rows())
    box = page.locator(".ph-title").bounding_box()
    page.mouse.move(box["x"] + 5, box["y"] + 5)
    page.mouse.down()
    page.mouse.move(5, 5, steps=4)                                                                 # selecting text and letting go outside: the browser sends a click to the backdrop
    page.mouse.up()
    page.wait_for_timeout(100)
    assert page.locator(".ph-dialog").count() == 1


def test_a_top_dialog_that_was_not_built_by_shell_still_owns_the_keyboard(page):
    open_history(page, standard_rows())
    page.evaluate("""() => { const o = document.createElement('div'); o.className = 'ph-overlay ph-overlay--top';
        const d = document.createElement('div'); d.setAttribute('role', 'dialog'); d.tabIndex = -1;
        d.append(Object.assign(document.createElement('input'), { id: 'foreign' }), Object.assign(document.createElement('button'), { id: 'foreign-btn', type: 'button', textContent: 'x' }));
        o.append(d);
        o.addEventListener('keydown', (e) => { if (e.key === 'Escape') { window.__foreignEsc = (window.__foreignEsc || 0) + 1; o.remove(); } });
        document.body.append(o); document.getElementById('foreign').focus(); }""")
    assert page.evaluate("() => document.activeElement.id") == "foreign"                           # the history does not pull the focus back into itself
    page.keyboard.press("Tab")
    assert page.evaluate("() => document.activeElement.id") == "foreign-btn"                       # nor trap Tab for itself
    page.keyboard.press("Escape")
    assert page.evaluate("() => window.__foreignEsc") == 1 and page.locator(".ph-overlay--top").count() == 0
    assert page.locator(".ph-dialog").count() == 1 and page.locator(".ph-row").count() == 14      # the Escape was the top dialog's: the history is still open


def test_closing_the_last_dialog_takes_its_document_listeners_off_again(page):
    page.evaluate("""() => { window.__l = { add: 0, remove: 0 }; const a = document.addEventListener.bind(document), r = document.removeEventListener.bind(document), mine = (t) => t === 'keydown' || t === 'focusin' || t === 'mousedown';
        document.addEventListener = (t, f, o) => { if (mine(t)) window.__l.add++; return a(t, f, o); };
        document.removeEventListener = (t, f, o) => { if (mine(t)) window.__l.remove++; return r(t, f, o); }; }""")
    open_history(page, standard_rows())
    page.click(".ph-row[data-id='pid001'] .ph-kebab")                                              # the menu adds its own and takes them off when it closes
    page.keyboard.press("Escape")
    page.keyboard.press("Escape")
    page.wait_for_selector(".ph-dialog", state="detached")
    listeners = page.evaluate("() => window.__l")
    assert listeners["add"] >= 3 and listeners["add"] == listeners["remove"], listeners


def test_the_host_names_where_the_keyboard_starts_as_an_element_or_as_a_function(page):
    page.evaluate("""() => { const second = Object.assign(document.createElement('input'), { id: 'second-el' });
        const sh = ProposalHistory.shell({ title: 'T', narrow: true, initialFocus: second });
        sh.body.append(Object.assign(document.createElement('button'), { id: 'first-el', type: 'button' }), second); window.__sh = sh; }""")
    page.wait_for_function("() => document.activeElement.id === 'second-el'")                       # not the first control: the one the host named
    page.evaluate("() => window.__sh.close()")
    page.evaluate("""() => { const sh = ProposalHistory.shell({ title: 'T', narrow: true, initialFocus: () => sh.body.querySelector('#second-fn') });
        sh.body.append(Object.assign(document.createElement('button'), { id: 'first-fn', type: 'button' }), Object.assign(document.createElement('input'), { id: 'second-fn' })); window.__sh = sh; }""")
    page.wait_for_function("() => document.activeElement.id === 'second-fn'")
    page.evaluate("() => window.__sh.close()")
    page.evaluate("""() => { const sh = ProposalHistory.shell({ title: 'T', narrow: true });
        sh.body.append(Object.assign(document.createElement('button'), { id: 'only-first', type: 'button' }), Object.assign(document.createElement('input'), { id: 'only-second' })); }""")
    page.wait_for_function("() => document.activeElement.id === 'only-first'")                      # no host preference: the first control in the body


# --------------------------------------------------------------------------------------------- loading: the corners

def test_a_late_failure_of_an_older_search_does_not_replace_the_newer_answer(page):
    open_history(page, standard_rows())
    page.evaluate("""() => { window.__api.ignoreAbort = true; window.__api.delay = (r) => (r.params.search === 'a' ? 600 : 30);
        window.__api.fail = (r) => (r.params.search === 'a' ? { status: 500 } : null); }""")
    page.fill(".ph-search-input", "a")
    page.wait_for_function("() => window.__api.log.some(r => r.params.search === 'a')")
    page.fill(".ph-search-input", "acme")
    page.wait_for_function("() => window.__api.log.some(r => r.params.search === 'acme')")
    page.wait_for_timeout(900)
    assert state(page) == "ready" and ids(page) == ["pid003"]
    assert page.logged == []                                                                        # a superseded request's failure is not even reported


def test_reload_without_keeprows_blanks_the_list_while_it_loads(page):
    open_history(page, standard_rows())
    page.evaluate("() => { window.__api.delay = 600; window.__handle.controller.reload({ keepRows: false }); }")
    assert state(page) == "loading" and page.locator(".ph-row").count() == 0 and page.is_visible(".ph-skel")
    wait_ready(page)
    assert page.locator(".ph-row").count() == 14


def test_a_double_click_on_show_more_asks_for_the_next_page_once(page):
    open_history(page, many_rows(60))
    page.evaluate("() => { window.__api.delay = 400; }")
    page.dblclick(".ph-more-btn")
    page.wait_for_function("() => document.querySelectorAll('.ph-row').length === 50")
    assert [r["params"].get("page") for r in requests(page)] == [None, "2"]


# --------------------------------------------------------------------------------------------- filters, scope, the server's own reading of the request

def test_choosing_a_filter_while_a_search_is_still_waiting_out_its_pause_sends_one_request_with_both(page):
    open_history(page, standard_rows())
    before = len(requests(page))
    page.fill(".ph-search-input", "acme")                                                          # inside the 250 ms pause ...
    page.click(".ph-seg--status button[data-status='generated']")                                  # ... when the filter is chosen
    wait_ready(page)
    page.wait_for_timeout(450)                                                                     # the pause would have ended by now
    sent = requests(page)[before:]
    assert len(sent) == 1 and sent[0]["params"]["search"] == "acme" and sent[0]["params"]["status"] == "generated", sent
    assert ids(page) == ["pid003"]
    page.fill(".ph-search-input", "toyota")                                                        # the same for a sort
    page.click(".ph-sort[data-sort='client']")
    wait_ready(page)
    page.wait_for_timeout(450)
    sent = requests(page)[before + 1:]
    assert len(sent) == 1 and sent[0]["params"]["search"] == "toyota" and sent[0]["params"]["sort"] == "client", sent


def test_choosing_the_scope_that_is_already_chosen_asks_for_nothing(browser, server):
    p = dom.new_page(browser, server, "/harness_admin.html", width=1440, height=900)
    mount_admin(p, standard_rows())
    before = len(requests(p, "/api/admin/proposals"))
    p.click(".ph-seg--scope button[data-scope='mine']")
    p.evaluate("() => window.__ctl.setScope('mine')")
    p.wait_for_timeout(300)
    assert len(requests(p, "/api/admin/proposals")) == before
    dom.close_page(p)


def test_the_drafts_come_last_note_is_said_only_when_it_is_true(page):
    open_history(page, standard_rows())
    page.click(".ph-sort[data-sort='value']")
    wait_ready(page)
    assert count_text(page).endswith("Drafts have no total yet, so they come last.")
    page.click(".ph-seg--status button[data-status='generated']")                                  # only generated proposals: all of them have a total
    wait_ready(page)
    assert count_text(page) == "10 generated proposals"
    page.click(".ph-x")
    open_history(page, [r for r in standard_rows() if r["status"] != "draft"])                     # no drafts at all
    page.click(".ph-sort[data-sort='value']")
    wait_ready(page)
    assert count_text(page) == "10 proposals"
    page.click(".ph-x")
    use(page, standard_rows(), facets=False)                                                       # no counts from the server: the rows on screen decide
    open_history(page)
    page.click(".ph-sort[data-sort='value']")
    wait_ready(page)
    assert count_text(page).endswith("Drafts have no total yet, so they come last.")


def test_the_servers_own_reading_of_status_and_sort_is_what_the_buttons_then_show(page):
    open_history(page, standard_rows())
    page.evaluate("""() => { const a = window.__api; a.raw = { proposals: a.data.slice(0, 3), total_count: 3, facets: { all: 14, draft: 4, generated: 10 },
        applied: { status: 'generated', sort: 'client', dir: 'asc' } }; }""")
    page.click(".ph-seg--status button[data-status='draft']")                                      # asked for drafts; the server says it applied "generated", by client A to Z
    wait_ready(page)
    st = page.evaluate("() => window.__handle.controller.getState()")
    assert (st["status"], st["sort"], st["dir"]) == ("generated", "client", "asc")
    assert page.eval_on_selector_all(".ph-seg--status button", "els => els.map((e) => e.getAttribute('aria-pressed'))") == ["false", "false", "true"]
    assert sort_state(page)[0] == ["client", "true", "asc"]


@pytest.mark.parametrize("facets", [{"all": "x", "draft": 1, "generated": 1}, {"all": -1, "draft": 0, "generated": 0}, {"all": 3, "draft": 1}, [1, 2, 3], "nope", 7, None])
def test_counts_that_arrive_malformed_are_left_out_not_shown_wrong(page, facets):
    open_history(page, standard_rows())
    page.evaluate("f => { const a = window.__api; a.raw = { proposals: a.data.slice(0, 2), total_count: 2, facets: f }; }", facets)
    page.evaluate("() => window.__handle.controller.reload()")
    wait_ready(page)
    assert page.locator(".ph-row").count() == 2
    assert page.locator(".ph-seg-n:not([hidden])").count() == 0 and texts(page, ".ph-seg--status button") == ["All", "Drafts", "Generated"]


# --------------------------------------------------------------------------------------------- timers, hooks and options (found by the mutation checks)

def tick_timer_ids(page):
    return page.evaluate("() => window.__intervals.filter((i) => i.ms === 60000).map((i) => i.id)")


def was_cleared(page, timer_id):
    return page.evaluate("id => window.__cleared.includes(id)", timer_id)


def test_closing_the_dialog_stops_its_minute_timer_and_tells_the_host_once(page):
    open_history(page, standard_rows(), onClose=True)
    (tick,) = tick_timer_ids(page)
    assert not was_cleared(page, tick)
    page.keyboard.press("Escape")
    page.wait_for_selector(".ph-dialog", state="detached")
    assert was_cleared(page, tick)                                                                  # no timer keeps running behind a closed dialog
    assert page.evaluate("() => window.__closed") == 1
    open_history(page, onClose=True)                                                                # the X and ProposalHistory.close() tell the host as well
    page.click(".ph-x")
    assert page.evaluate("() => window.__closed") == 1
    open_history(page, onClose=True)
    page.evaluate("() => ProposalHistory.close()")
    assert page.evaluate("() => window.__closed") == 1 and page.evaluate("() => ProposalHistory.isOpen()") is False


def test_destroying_a_mounted_list_stops_its_minute_timer_and_takes_it_off_the_page(browser, server):
    p = dom.new_page(browser, server, "/harness_admin.html", width=1280, height=900)
    mount_admin(p, standard_rows(), opts={"scope": "all"})
    (tick,) = tick_timer_ids(p)
    assert not was_cleared(p, tick) and p.locator(".ph").count() == 1
    p.evaluate("() => window.__ctl.destroy()")
    assert was_cleared(p, tick) and p.locator(".ph").count() == 0
    dom.close_page(p)


def test_an_unknown_scope_falls_back_to_the_first_one_and_asks_for_that(browser, server):
    p = dom.new_page(browser, server, "/harness_admin.html", width=1280, height=900)
    use(p, standard_rows())
    p.evaluate("() => window.mountPH({ opts: { scope: 'nonsense' } })")
    p.wait_for_selector(".ph-row")
    assert requests(p, "/api/admin/proposals")[-1]["params"]["mine"] == "true"
    assert texts(p, ".ph-seg--scope button[aria-pressed='true']") == ["My proposals"]
    dom.close_page(p)


def test_the_admin_list_asks_for_fifty_at_a_time_unless_the_host_says_otherwise(browser, server):
    p = dom.new_page(browser, server, "/harness_admin.html", width=1280, height=900)
    use(p, many_rows(60))
    p.evaluate("() => window.mountPH({ opts: { scope: 'all', pageSize: null } })")
    p.wait_for_selector(".ph-row")
    assert requests(p, "/api/admin/proposals")[-1]["params"]["page_size"] == "50"
    assert p.locator(".ph-row").count() == 50
    dom.close_page(p)


def test_a_page_size_above_what_the_server_allows_is_capped_so_show_more_still_adds_up(page):
    open_history(page, many_rows(150), opts={"pageSize": 500})
    assert requests(page)[0]["params"]["page_size"] == "100" and page.locator(".ph-row").count() == 100
    assert page.inner_text(".ph-more-btn") == "Show 50 more"
    page.click(".ph-more-btn")
    page.wait_for_function("() => document.querySelectorAll('.ph-row').length === 150")
    assert requests(page)[-1]["params"]["page"] == "2" and len(set(ids(page))) == 150


def test_a_host_can_supply_its_own_way_to_fetch_the_list(page):
    open_history(page, standard_rows(), fetchJson=True)
    assert ids(page) == ["fjpid"] and row_text(page, "fjpid", ".ph-client-name") == "From the seam"
    assert requests(page) == []                                                                     # window.fetch was never asked for the list
    assert page.evaluate("() => window.__fj") == ["/api/my-proposals?page_size=25"]


def test_the_admin_mount_knows_its_own_endpoint(browser, server):
    p = dom.new_page(browser, server, "/harness_admin.html", width=1280, height=900)
    use(p, standard_rows())
    p.evaluate("() => window.mountPH({ opts: { scope: 'all', endpoint: null } })")
    p.wait_for_selector(".ph-row")
    assert len(requests(p, "/api/admin/proposals")) == 1 and requests(p, "/api/my-proposals") == []
    dom.close_page(p)


def test_a_page_that_is_not_json_is_an_error_with_a_way_back_not_a_blank_list(page):
    use(page, standard_rows(), html=True)                                                           # a proxy's sign-in page answering 200 where the list should be
    open_history(page, wait=".ph[data-state='error']")
    assert page.inner_text(".ph-state-text") == "Something went wrong. Try again in a moment."
    assert "Please sign in" not in page.inner_text(".ph-dialog")
    assert page.logged == ["ProposalHistory: the list request failed (HTTP 0)"]
    page.evaluate("() => { window.__api.html = false; }")
    page.click(".ph-retry")
    page.wait_for_selector(".ph-row")


def test_the_sort_menu_follows_a_sort_chosen_with_the_header_buttons(page):
    open_history(page, standard_rows())
    page.click(".ph-sort[data-sort='client']")
    wait_ready(page)
    assert page.eval_on_selector(".ph-select", "e => e.value") == "client:asc"                      # the narrow view shows what the wide one chose
    page.click(".ph-sort[data-sort='client']")
    wait_ready(page)
    assert page.eval_on_selector(".ph-select", "e => e.value") == "client:desc"


def test_the_same_announcement_twice_is_read_out_twice(page):
    open_history(page, standard_rows())
    page.evaluate("""() => { window.__liveLog = []; const el = document.querySelector('.ph-live');
        new MutationObserver(() => window.__liveLog.push(el.textContent)).observe(el, { childList: true, characterData: true, subtree: true }); }""")
    for n in (1, 2):
        page.click(".ph-row[data-id='pid003'] .ph-kebab")
        page.click(".ph-menu-item[data-action='copy-link']")
        page.wait_for_function("n => window.__liveLog.filter((t) => t === 'Link copied.').length === n", arg=n)
    assert page.evaluate("() => window.__liveLog") == ["Link copied.", "", "Link copied."]             # emptied between, or a screen reader would hear nothing the second time


def test_the_copied_toast_goes_away_by_itself(page):
    open_history(page, standard_rows())
    page.click(".ph-row[data-id='pid003'] .ph-kebab")
    page.click(".ph-menu-item[data-action='copy-link']")
    page.wait_for_selector(".ph-toast", state="visible")
    page.wait_for_selector(".ph-toast", state="hidden", timeout=6000)                               # (it stays 3.2 seconds)


def test_the_clear_button_in_the_search_box_is_a_full_size_target(page):
    open_history(page, standard_rows())
    page.fill(".ph-search-input", "acme")                                                           # (the button only exists on screen once there is text)
    box = page.locator(".ph-search-clear").bounding_box()
    assert box["width"] >= 36 and box["height"] >= 36


def test_the_error_text_and_the_invalid_border_in_the_form_primitives_use_the_deep_red(browser, server):
    p = dom.new_page(browser, server, "/harness.html", width=1280, height=900)
    p.evaluate("() => buildForm(true)")
    p.wait_for_selector(".ph-ferror")
    assert p.eval_on_selector(".ph-ferror", "e => getComputedStyle(e).color") == "rgb(138, 10, 37)"      # #8a0a25, 9.8:1 on white
    assert p.eval_on_selector(".ph-input[aria-invalid='true']", "e => getComputedStyle(e).borderTopColor") == "rgb(138, 10, 37)"
    assert p.eval_on_selector(".ph-req", "e => getComputedStyle(e).color") == "rgb(138, 10, 37)"
    dom.close_page(p)


def test_in_the_card_tiers_the_last_edited_cell_is_one_muted_line_and_in_the_grid_it_is_two(browser, server):
    for path, threshold in (("/harness.html", 1080), ("/harness_admin.html", 950)):                  # where each surface turns from cards into a grid
        for width in (700, 990, 1100, 1130, 1280):
            p = dom.new_page(browser, server, path, width=width)
            if path == "/harness.html":
                open_history(p, standard_rows())
            else:
                mount_admin(p, standard_rows(), opts={"scope": "all"})
            f = p.evaluate("() => ({ comp: document.querySelector('.ph').getBoundingClientRect().width, display: getComputedStyle(document.querySelector('.ph-edited .ph-minor')).display })")
            assert (f["display"] == "inline") == (f["comp"] < threshold), (path, width, f)
            dom.close_page(p)


def test_in_the_card_layout_the_status_badge_sits_at_the_right_edge_of_the_card(browser, server):
    p = dom.new_page(browser, server, "/harness.html", width=390, height=800)
    open_history(p, standard_rows())
    f = p.evaluate("() => { const r = document.querySelector('.ph-row').getBoundingClientRect(), b = document.querySelector('.ph-badge').getBoundingClientRect(); return { gap: r.right - b.right, left: b.left - r.left, width: r.width }; }")
    assert f["gap"] <= 24 and f["left"] > f["width"] / 2, f
    dom.close_page(p)


def test_the_search_box_turns_purple_when_it_has_the_focus(page):
    open_history(page, standard_rows(), focus_opener=True)
    page.evaluate("() => document.getElementById('other').focus()")                                  # (the dialog put the focus in the search box: take it away first)
    before = page.eval_on_selector(".ph-search-input", "e => getComputedStyle(e).borderTopColor")
    page.focus(".ph-search-input")
    after = page.eval_on_selector(".ph-search-input", "e => getComputedStyle(e).borderTopColor")
    accent = page.evaluate("() => { const e = document.createElement('i'); e.style.color = 'var(--accent)'; document.body.append(e); const c = getComputedStyle(e).color; e.remove(); return c; }")
    assert before != after and after == accent, (before, after, accent)
