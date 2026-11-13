"""The DOOH line's "Upload screen list" control on Step 05, in a REAL browser (headless Chromium through Playwright).

The page is a small harness, not the whole app, but nothing under test is a stand-in: the real renderAvailsGrid (and the helpers it needs) are lifted
out of app.js, next to the real dooh-screenlist.js and the real CSS; only the proposal around them (`state`) is stubbed. POST /api/dooh/screenlist
is answered by the REAL route function of app.main (a fake Request carrying the browser's own bytes and headers), over an in-memory store, so every
number the page shows is the server's own and the page is never handed a number the server did not compute.

Pinned: the control is on a DOOH line and nowhere else; an upload fills Max Recommended Monthly Imps / Spend with the server's totals and shows the
counts; the proposal state keeps the small reference and never the rows; a file name, a skipped-row note or a column name that is markup runs
nothing; editing a number offers "Recalculate from list", which puts the list's totals back; a refused file / a dead server shows the message and
changes nothing; Remove keeps the numbers; a reopened proposal shows its list again. Skipped when Playwright or its Chromium is not installed."""
from __future__ import annotations

import asyncio
import concurrent.futures
import http.server
import json
import os
import re
import threading
from pathlib import Path
from urllib.parse import quote

os.environ.setdefault("DATABASE_URL", "postgresql://fake:fake@localhost/fake")

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")

import app.main as main  # noqa: E402
from app.services import dooh_pivot as dp  # noqa: E402
from app.services import dooh_screenlist as ds  # noqa: E402
from tests import dooh_support as sup  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "static"
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")
SHOTS = os.environ.get("DOOH_SHOTS_DIR")                 # a folder to leave screenshots in (for looking at; nothing asserts on them)

DOOH = "Digital Out of Home"
SEARCH = "Search - SEM"
EDIGITAL = "eDigital Display"

LIFTED = ("escapeHtml", "escapeAttr", "_mbParseDate", "_mbMonthKey", "_mbMonthLabel", "_mbLastDayOfMonth", "_mbMonthsBetween", "_effectiveStartDate",
          "_effectiveEndDate", "_effectiveGeo", "_effectiveTargetText", "_effectiveProduct", "_isFullFlight", "parseFormattedInput", "formatImpsDisplay",
          "formatSpendDisplay", "formatPlainDisplay", "calcMaxImpsFromSpend", "calcMaxSpendFromImps", "_defaultFrequency", "newLineItemId",
          "_isEstimateCpmProduct", "_forecastEnabled", "renderAvailsGrid")


def _extract_fn(name: str) -> str:
    m = re.search(rf"^(?:async )?function {re.escape(name)}\(", APP_JS, re.M)
    assert m, f"function {name} not found"
    return APP_JS[m.start(): APP_JS.index("\n}\n", m.start()) + 2]


def _extract_const(name: str) -> str:
    m = re.search(rf"^const {re.escape(name)} = ", APP_JS, re.M)
    assert m, f"const {name} not found"
    return APP_JS[m.start(): APP_JS.index(";\n", m.end()) + 1]


HARNESS = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>harness</title>
<link rel="stylesheet" href="/static/styles.css"><link rel="stylesheet" href="/static/dooh-screenlist.css"></head><body>
<main style="max-width:980px;margin:0 auto;padding:20px"><span id="avails-status-pill"></span><p id="avails-blurb"></p><div id="avails-grid" class="avails-grid"></div></main>
<script>
  // the proposal around the code under test: the tabs, the totals and the share-of-voice badge are not what is being checked
  function renderAllTierTabStrips() {}
  function updateSovBadge() {}
  function applySovDisplay() {}
  function computeSovPct() { return null; }
  function _lineNoteHtml() { return ""; }
  function _applyFrequencyTriangle() {}
  function _constanzaBlockHtml() { return ""; }
  function _constanzaMountBlocks() {}
  window.__PRODUCTS = %(products)s;
  var state = { parsed: {}, tiers: [{ label: "A" }], activeTierLabel: "A", activeTierName: null, activeTierGeo: null, activeTierStartDate: null, activeTierEndDate: null,
                timeUnit: "month", lineItems: [], availsData: {}, productIndex: {}, catalog: null };
  /*LIFTED*/
  window.setup = function (cfg) {
    cfg = cfg || {};
    state.parsed = { request_type: "avails", demo: "Adults 25-54", behavioral: "", geo: "Houston", start_date: "2026-11-01", end_date: "2026-12-31" };
    state.productIndex = {};
    Object.keys(window.__PRODUCTS).forEach(function (name) { state.productIndex[name] = Object.assign({ name: name }, window.__PRODUCTS[name]); });
    state.lineItems = (cfg.lines || []).map(function (l) { return Object.assign({ months: 1, monthly_budget: 2000, target_override: "" }, l); });
    state.availsData = cfg.avails || {};
    renderAvailsGrid();
  };
</script>
<script src="/static/dooh-screenlist.js"></script>
</body></html>"""

PRODUCTS = {
    DOOH: {"family": "DOOH", "buying_model": "Fixed", "estimated_impressions": True, "estimated_cpm_for_imps": None, "forecast": {"enabled": False}},
    SEARCH: {"family": "Search", "buying_model": "CPC", "forecast": {"enabled": False}},
    EDIGITAL: {"family": "Entravision Plus", "buying_model": "CPM", "forecast": {"enabled": False}},
}


def harness_html() -> str:
    lifted = "\n".join([_extract_const("_SOV_MONTHLY_EQUIVALENT_SCALE"), _extract_const("FULL_FLIGHT")] + [_extract_fn(n) for n in LIFTED])
    return HARNESS.replace("/*LIFTED*/", lifted).replace("%(products)s", json.dumps(PRODUCTS))


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


class World:
    """What the harness's /api answers: the real upload route, over an in-memory store."""

    def __init__(self, monkeypatch):
        self.stored: dict = {}
        self.sent: list[dict] = []                         # what the browser sent: filename header, content type, number of bytes
        self.fail: tuple | None = None                     # (status, body) to answer instead
        self.drop = False                                  # kill the connection
        monkeypatch.setattr(ds, "save", self._save)

    def _save(self, parsed, created_by=None):
        list_id = ds.content_id(parsed.rows)
        self.stored[list_id] = parsed
        return list_id

    def answer(self, route) -> None:
        req = route.request
        if self.drop:
            route.abort()
            return
        if not req.url.endswith("/api/dooh/screenlist"):
            route.fulfill(status=404, content_type="application/json", body='{"detail": "not found"}')
            return
        body = req.post_data_buffer or b""
        self.sent.append({"filename": req.headers.get("x-filename"), "type": req.headers.get("content-type"), "bytes": len(body)})
        if self.fail:
            route.fulfill(status=self.fail[0], content_type="application/json", body=json.dumps(self.fail[1]))
            return
        scope = {"type": "http", "method": "POST", "path": "/api/dooh/screenlist", "query_string": b"",
                 "headers": [(k.lower().encode(), v.encode()) for k, v in req.headers.items()], "state": {"user": {"email": "planner@example.com"}}}
        sent = {"done": False}

        async def receive():
            if not sent["done"]:
                sent["done"] = True
                return {"type": "http.request", "body": body, "more_body": False}
            return {"type": "http.disconnect"}

        from starlette.requests import Request
        from fastapi import HTTPException
        status, payload = 200, None
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:           # (Playwright's sync API owns this thread's event loop)
                payload = pool.submit(asyncio.run, main.upload_dooh_screenlist(Request(scope, receive))).result()
        except HTTPException as exc:
            status, payload = exc.status_code, {"detail": exc.detail}
        route.fulfill(status=status, content_type="application/json", body=json.dumps(payload))


@pytest.fixture
def open_step5(browser, server, monkeypatch):
    opened = []

    def _open(lines=None, avails=None, viewport=None):
        world = World(monkeypatch)
        context = browser.new_context(viewport=viewport or {"width": 1100, "height": 1000})
        page = context.new_page()
        page.route("**/api/**", world.answer)
        page.route("**/fonts.googleapis.com/**", lambda r: r.abort())
        page.route("**/fonts.gstatic.com/**", lambda r: r.abort())
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" and "Failed to load resource" not in m.text else None)
        page.goto(f"{server}/harness.html")
        page.world, page.errors = world, errors                     # type: ignore[attr-defined]
        page.evaluate("cfg => window.setup(cfg)", {"lines": lines if lines is not None else [{"id": "D1", "product_name": DOOH}], "avails": avails or {}})
        opened.append((context, page))
        return page

    yield _open
    for context, page in opened:
        context.close()
        assert page.errors == [], page.errors


def upload(page, data: bytes, name="list.csv", lid="D1"):
    page.set_input_files(f'[data-dooh-file][data-lid="{lid}"]', files=[{"name": name, "mimeType": "text/csv", "buffer": data}])


def field(page, key, lid="D1") -> str:
    return page.input_value(f'input[data-lid="{lid}"][data-key="{key}"]')


def block_text(page, lid="D1") -> str:
    """The block's text as written in the DOM (not as the CSS dresses it: the headings are upper-cased on screen), one space between parts."""
    raw = page.evaluate("""lid => { const c = document.querySelector(`[data-dooh-block][data-lid="${lid}"]`).cloneNode(true);
                                    c.querySelectorAll("dt,dd,p,div,h4,button").forEach(n => n.append(" ")); return c.textContent; }""", lid)
    return re.sub(r"\s+", " ", raw).strip()


def shot(page, name):
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(SHOTS) / name), full_page=True)


def type_over(page, key, value, lid="D1"):
    """What a planner does: click into the field, select what is there, type, tab out (the card's own focus / blur handlers do the rest)."""
    selector = f'input[data-lid="{lid}"][data-key="{key}"]'
    page.click(selector)
    page.keyboard.press("ControlOrMeta+A")
    page.keyboard.type(value)
    page.keyboard.press("Tab")


def money(n):
    return "$" + f"{n:,.2f}"


# --- the tests -----------------------------------------------------------------------------------------------------------------

def test_the_upload_control_is_on_a_dooh_line_and_on_no_other(open_step5):
    page = open_step5(lines=[{"id": "D1", "product_name": DOOH}, {"id": "S1", "product_name": SEARCH}, {"id": "E1", "product_name": EDIGITAL}])
    assert page.locator("[data-dooh-block]").count() == 1 and page.locator('[data-dooh-block][data-lid="D1"]').count() == 1
    assert "Upload screen list" in block_text(page) and "DOOH screen list" in block_text(page)
    assert page.locator('[data-dooh-action="pick"]').count() == 1
    assert page.get_attribute('[data-dooh-file]', "accept").startswith(".csv,.xlsx")
    shot(page, "dooh_01_empty.png")


def test_an_upload_fills_the_line_with_the_servers_totals_and_shows_what_was_read(open_step5):
    page = open_step5()
    rows = sup.screens(80, seed=5)
    rows[7][22] = "n/a"                                          # one unusable screen
    data = sup.csv_bytes(rows)
    upload(page, data, "Houston screens.csv")
    page.wait_for_selector(".dooh-list-facts")
    parsed = ds.parse_upload("Houston screens.csv", data)
    # what the browser sent: the raw file, its name URL-encoded in a header
    assert page.world.sent == [{"filename": quote("Houston screens.csv"), "type": "application/octet-stream", "bytes": len(data)}]
    # the fields hold the server's totals as whole numbers; the card shows the server's counts and exact totals
    assert field(page, "max_imps") == f"{round(parsed.total_impressions):,}" and field(page, "max_spend") == f"${round(parsed.total_spend):,}"
    text = block_text(page)
    assert "From the uploaded screen list (79 screens)" in text and "Houston screens.csv" in text
    assert "Screens read 79" in text and "Skipped 1" in text
    assert f"Total impressions {round(parsed.total_impressions):,}" in text and f"Total spend {money(parsed.total_spend)}" in text
    assert "row 9: four_week_impressions is not a number (n/a)" in text
    # the state: the numbers as typed values, and the small reference - never the screens
    entry = page.evaluate("state.availsData.D1")
    assert entry["max_imps"] == round(parsed.total_impressions) and entry["max_spend"] == round(parsed.total_spend)
    assert entry["freeform"] is False and entry["max_imps_estimated"] is False and "basis" not in entry
    assert entry["dooh_screenlist"]["screens"] == 79 and entry["dooh_screenlist"]["id"] in page.world.stored
    assert len(json.dumps(entry)) < 1500
    assert "Recalculate" not in text and "Replace file" in text and "Remove list" in text
    shot(page, "dooh_02_uploaded.png")


def test_a_list_with_fewer_than_the_minimum_screens_says_so(open_step5):
    page = open_step5()
    upload(page, sup.csv_bytes(sup.screens(12)))
    page.wait_for_selector(".dooh-list-facts")
    assert f"Only 12 screens: the DOOH Summary asks for a minimum of {dp.DOOH_MINIMUM_SCREENS}." in block_text(page)


def test_a_file_name_a_column_name_and_a_skipped_value_that_are_markup_run_nothing_and_show_as_text(open_step5):
    page = open_step5()
    rows = sup.screens(30)
    rows[3][20] = "<img src=x onerror=window.__pwned=1>"       # a CPM cell that is markup: the screen is skipped and the value quoted back
    data = sup.csv_bytes(rows, extra=[("<script>window.__pwned=2</script>", "x")])
    upload(page, data, "<img src=x onerror=window.__pwned=3>.csv")
    page.wait_for_selector(".dooh-list-facts")
    page.wait_for_timeout(300)
    assert page.evaluate("window.__pwned") is None
    text = block_text(page)
    assert "<img src=x onerror=window.__pwned=3>.csv" in text                       # shown literally
    assert "<script>window.__pwned=" in text and "<img src=x onerror=wind" in text                    # (a quoted value or column name is cut to fit a line)
    assert page.locator(".dooh-list img, .dooh-list script").count() == 0
    # an error message from a server is text too
    page.world.fail = (400, {"detail": "<b onmouseover=window.__pwned=4>bad</b>"})
    upload(page, data, "again.csv")
    page.wait_for_selector(".dooh-list-error:not(:empty)")
    assert page.inner_text(".dooh-list-error") == "<b onmouseover=window.__pwned=4>bad</b>" and page.locator(".dooh-list-error b").count() == 0


def test_editing_a_number_offers_recalculate_and_recalculate_puts_the_lists_totals_back(open_step5):
    page = open_step5()
    upload(page, sup.csv_bytes(sup.screens(40, seed=9)))
    page.wait_for_selector(".dooh-list-facts")
    imps, spend = field(page, "max_imps"), field(page, "max_spend")
    assert page.locator('[data-dooh-action="recalc"]').count() == 0
    # the planner types over the spend (the avails card's own blur handler stores it)
    type_over(page, "max_spend", "1234")
    page.evaluate("renderAvailsGrid()")                                             # (whatever re-draws the card: a tab switch, a Constanza apply)
    assert page.evaluate("state.availsData.D1.max_spend") == 1234
    assert "no longer match the list" in block_text(page) and "From the uploaded screen list (40 screens)" in block_text(page)
    page.click('[data-dooh-action="recalc"]')
    assert field(page, "max_imps") == imps and field(page, "max_spend") == spend
    assert "no longer match" not in block_text(page) and page.locator('[data-dooh-action="recalc"]').count() == 0
    shot(page, "dooh_03_edited_then_recalculated.png")
    # editing the impressions does it too
    type_over(page, "max_imps", "5000")
    page.evaluate("renderAvailsGrid()")
    assert page.locator('[data-dooh-action="recalc"]').count() == 1


def test_a_file_the_server_refuses_shows_its_message_and_changes_nothing(open_step5):
    page = open_step5(avails={"D1": {"max_imps": 777, "max_spend": 55}})
    upload(page, b"name,city\nA,B\n", "wrong.csv")
    page.wait_for_selector(".dooh-list-error:not(:empty)")
    assert "missing columns the DOOH Summary needs" in page.inner_text(".dooh-list-error")
    assert page.evaluate("state.availsData.D1") == {"max_imps": 777, "max_spend": 55} and "Upload screen list" in block_text(page)
    shot(page, "dooh_04_refused.png")
    upload(page, b"x", "inventory.xls")
    page.wait_for_function("document.querySelector('.dooh-list-error').textContent.includes('.xlsx')")
    page.world.fail = (413, {"detail": "That file is larger than 20 MB. Filter the screen list and upload it again."})
    upload(page, sup.csv_bytes(sup.screens(5)))
    page.wait_for_function("document.querySelector('.dooh-list-error').textContent.includes('larger than 20 MB')")
    page.world.fail = (500, {})
    upload(page, sup.csv_bytes(sup.screens(5)))
    page.wait_for_function("document.querySelector('.dooh-list-error').textContent.includes('could not be read just now')")
    page.world.fail, page.world.drop = None, True
    upload(page, sup.csv_bytes(sup.screens(5)))
    page.wait_for_function("document.querySelector('.dooh-list-error').textContent.includes('reach the server')")
    assert page.evaluate("state.availsData.D1") == {"max_imps": 777, "max_spend": 55}
    page.world.drop = False                                                         # and the planner can simply try again
    upload(page, sup.csv_bytes(sup.screens(25)))
    page.wait_for_selector(".dooh-list-facts")
    assert page.inner_text(".dooh-list-error") == "" and "(25 screens)" in block_text(page)


def test_replacing_the_file_refills_and_removing_the_list_keeps_the_numbers(open_step5):
    page = open_step5()
    upload(page, sup.csv_bytes(sup.screens(30, seed=1)), "first.csv")
    page.wait_for_selector(".dooh-list-facts")
    first = field(page, "max_imps")
    upload(page, sup.csv_bytes(sup.screens(55, seed=2)), "second.csv")
    page.wait_for_function("document.querySelector('.dooh-list-source').textContent.includes('55 screens')")
    assert field(page, "max_imps") != first and "second.csv" in block_text(page)
    kept = (field(page, "max_imps"), field(page, "max_spend"))
    page.click('[data-dooh-action="remove"]')
    assert "Upload screen list" in block_text(page) and "From the uploaded screen list" not in block_text(page)
    assert (field(page, "max_imps"), field(page, "max_spend")) == kept                 # they are the planner's own numbers now
    assert "dooh_screenlist" not in page.evaluate("state.availsData.D1")


def test_a_reopened_proposal_shows_its_list_again_from_the_saved_reference(open_step5):
    rows = sup.screens(64, seed=4)
    parsed = ds.parse_upload("saved.csv", sup.csv_bytes(rows))
    ref = ds.reference_for(parsed, ds.content_id(parsed.rows))
    saved = main.AvailsEntry(max_imps=round(ref["total_impressions"]), max_spend=round(ref["total_spend"]), dooh_screenlist=ref).model_dump()   # as the server returns it on reopen
    page = open_step5(avails={"D1": saved})
    text = block_text(page)
    assert "From the uploaded screen list (64 screens)" in text and "saved.csv" in text and "Recalculate" not in text
    assert field(page, "max_imps") == f"{round(ref['total_impressions']):,}"
    # a line that has no list on a reopened proposal still shows the empty control (the server sends dooh_screenlist: null)
    page2 = open_step5(avails={"D1": main.AvailsEntry(max_imps=5).model_dump()})
    assert "Upload screen list" in block_text(page2)


def test_the_control_fits_a_phone_width_and_every_button_is_reachable_by_keyboard(open_step5):
    page = open_step5(viewport={"width": 390, "height": 900})
    upload(page, sup.csv_bytes(sup.screens(30)), "a-very-long-file-name-for-the-houston-market-inventory-export-final-v2.csv")
    page.wait_for_selector(".dooh-list-facts")
    assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
    page.focus('[data-dooh-action="pick"]')
    focused = page.evaluate("document.activeElement.dataset.doohAction")
    assert focused == "pick"
    names = page.eval_on_selector_all("[data-dooh-block] button", "els => els.map(e => e.textContent.trim())")
    assert names == ["Replace file", "Remove list"]
    shot(page, "dooh_05_phone.png")
