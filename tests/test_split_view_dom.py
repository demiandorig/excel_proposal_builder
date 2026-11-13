"""Split view (two wizard steps side by side) in a REAL browser: headless Chromium through Playwright, against the real index.html, app.js, split-view.js,
constanza.js and the real CSS. Only /api/** is answered by the test (the real catalog from app.main, the real Constanza estimate route over the strict Meta
fake), and the proposal is put on Step 04 by the page's own functions (onNext(4) builds the lines exactly as Continue does).

What is pinned: turning Split view on and off puts the page back exactly as it was (the same sections in the same order with the same classes, ids unique, the
panes labelled regions); the "show alongside" list and what Continue does with a pinned or an automatic second step; a Step 04 budget typed with Step 05
beside it reaches Step 05 (share-of-voice badge, Constanza card) without navigation while the field keeps the keyboard, caret and element; the pane that was
scrolled keeps its scroll; a Step 06 month figure reaches Step 04; line add / remove; Step 02 dates reach Step 04; the Constanza dock follows a budget;
a narrow window falls back to one step and comes back; the preference lives in localStorage (and a blocked localStorage breaks nothing); no page error.
Skipped when Playwright or Chromium is missing. TEST_SHOTS=<dir> also writes screenshots of the layouts."""
from __future__ import annotations

import asyncio
import http.server
import json
import os
import threading
from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

playwright_sync = pytest.importorskip("playwright.sync_api")

import app.main as main  # noqa: E402
from tests import method_fakes  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "static"
INDEX = (ROOT / "app" / "templates" / "index.html").read_bytes()
SHOTS = os.environ.get("TEST_SHOTS")

CTV = "Entravision Plus CTV/OTT - English Content"
DISPLAY = "eDigital Display"
SEARCH = "Search - SEM"
AWARENESS = "Facebook & Instagram Ads | Awareness"
PRODUCTS = [CTV, DISPLAY, SEARCH, AWARENESS]

CATALOG = asyncio.run(main.get_catalog())


class _Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=str(STATIC.parent), **k)

    def do_GET(self):
        if self.path.split("?")[0] == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(INDEX)))
            self.end_headers()
            self.wfile.write(INDEX)
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


@pytest.fixture(scope="module")
def fakes():
    """The strict fake of Meta's API for the whole module (patched into the real service; undone at the end)."""
    mp = pytest.MonkeyPatch()
    try:
        yield method_fakes.FAKES["meta"].install(mp)
    finally:
        mp.undo()


def answer(route) -> None:
    """What /api/** says: the real catalog, the real Constanza routes (over the Meta fake), and an empty answer for everything else."""
    req = route.request
    path = req.url.split("//", 1)[1].split("/", 1)[1].split("?")[0]
    status, body = 200, {}
    try:
        payload = json.loads(req.post_data) if req.post_data else None
        if path == "api/me":
            body = {"email": "planner@example.com", "is_admin": False}
        elif path == "api/catalog":
            body = CATALOG
        elif path == "api/proposal/draft":
            body = {"proposal_id": "p1", "saved_at": "2026-11-12T10:00:00+00:00"}
        elif path == "api/constanza/estimate":
            body = main.constanza_estimate(main.ConstanzaEstimateRequest(**payload))
        elif path == "api/constanza/resolve":
            body = main.constanza_resolve(main.ConstanzaResolveRequest(**payload))
        elif path == "api/constanza/meta/status":
            body = main.constanza_meta_status()
        elif path == "api/constanza/tiktok/status":
            body = main.constanza_tiktok_status()
        elif path == "api/constanza/interest-taxonomy":
            body = {"groups": []}
    except HTTPException as exc:
        status, body = exc.status_code, {"detail": exc.detail}
    except ValidationError as exc:
        status, body = 422, {"detail": [{"msg": e["msg"], "loc": list(e["loc"])} for e in exc.errors()]}
    route.fulfill(status=status, content_type="application/json", body=json.dumps(body))


def open_app(browser, server, width=1440, height=900, init_script=None):
    context = browser.new_context(viewport={"width": width, "height": height})
    if init_script:
        context.add_init_script(init_script)
    page = context.new_page()
    page.route("**/api/**", answer)
    page.route("**/fonts.googleapis.com/**", lambda r: r.abort())
    page.route("**/fonts.gstatic.com/**", lambda r: r.abort())
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" and "Failed to load resource" not in m.text else None)
    page.goto(f"{server}/")
    page.wait_for_function("() => window.SplitView && state.catalog && Object.keys(state.productIndex).length > 20")
    page.errors = errors                                               # type: ignore[attr-defined]
    return context, page


REACH_STEP_4 = """cfg => {
  state.parsed = Object.assign({ client_name: 'Acme Motors', salesperson_email: 'seller@example.com', request_type: 'Avails + Full Presentation',
    geo: 'Los Angeles, CA', start_date: '2026-11-01', end_date: '2027-01-31', monthly_budget: 12000, agency_fee: null, total_months: 3,
    demo: 'Adults 25-54', behavioral: '', notion_id: 'EVC-12345', tiered_budget: false, products_selected: cfg.products }, cfg.parsed || {});
  fillForm(state.parsed);
  state.furthestStep = 3;
  goToStep(2);
  onNext(4);                       // Continue: builds the lines from the request and the budget, exactly as the page does
}"""


@pytest.fixture
def start(browser, server, fakes):
    opened = []

    def _start(products=None, parsed=None, **kw):
        context, page = open_app(browser, server, **kw)
        opened.append((context, page))
        page.evaluate(REACH_STEP_4, {"products": products or PRODUCTS, "parsed": parsed})
        assert page.evaluate("state.step") == 4
        return page

    yield _start
    for context, page in opened:
        context.close()
        assert page.errors == [], page.errors


# --------------------------------------------------------------------------------------------------------------------------------- helpers

def ids(page) -> list[str]:
    return page.evaluate("state.lineItems.map(li => li.id)")


def toggle(page) -> None:
    page.click("#split-toggle")


def split_on(page) -> None:
    toggle(page)
    page.wait_for_function("window.SplitView.isOn()")


def shown(page) -> list[int]:
    """The steps on screen (sections not hidden), in page order."""
    return page.evaluate("[1,2,3,4,5,6,7,8].filter(i => !document.getElementById('step-' + i).classList.contains('hidden'))")


def structure(page):
    """The page's layout skeleton: what a restore must reproduce exactly."""
    return page.evaluate("""() => ({
      order: [...document.querySelector('main.canvas').children].map(el => el.id),
      classes: [...document.querySelector('main.canvas').children].map(el => el.className),
      attrs: [...document.querySelector('main.canvas').children].map(el => [el.getAttribute('role'), el.getAttribute('aria-label')]),
      canvas: document.querySelector('main.canvas').className,
      body: document.body.className,
      root: document.documentElement.getAttribute('style'),
    })""")


def sov(page, lid) -> str:
    return page.evaluate("lid => document.getElementById('sov-badge-' + lid).textContent.trim()", lid)


def put_avails(page, lid, entry) -> None:
    page.evaluate("([lid, entry]) => { state.availsData[lid] = entry; }", [lid, entry])


def type_budget(page, selector, text) -> None:
    box = page.locator(selector).first
    box.click()
    box.press("ControlOrMeta+a")
    page.keyboard.type(text, delay=25)


def shot(page, name) -> None:
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(SHOTS) / f"{name}.png"))


# =================================================================================================================================
# Turning it on and off
# =================================================================================================================================

def test_the_toggle_puts_two_real_steps_side_by_side_and_back_exactly(start):
    page = start()
    before = structure(page)
    assert before["order"][:8] == ["step-1", "step-2", "step-3", "step-4", "step-6", "step-5", "step-7", "step-8"]
    assert page.get_attribute("#split-toggle", "aria-pressed") == "false" and page.is_enabled("#split-toggle")
    assert page.is_hidden("#split-second-wrap")

    split_on(page)
    assert shown(page) == [4, 5]
    assert page.get_attribute("#split-toggle", "aria-pressed") == "true"
    assert page.evaluate("window.SplitView.pair()") == [4, 5]
    # the real sections, labelled regions, the working one marked
    assert page.get_attribute("#step-4", "aria-label") == "Step 4: Curate" and page.get_attribute("#step-5", "aria-label") == "Step 5: Avails"
    assert page.get_attribute("#step-4", "role") == "region"
    assert "split-pane-active" in page.get_attribute("#step-4", "class") and "split-pane-active" not in page.get_attribute("#step-5", "class")
    assert page.evaluate("document.querySelectorAll('[id]').length === new Set([...document.querySelectorAll('[id]')].map(e => e.id)).size")
    assert page.evaluate("document.getElementById('step-5').contains(document.getElementById('avails-grid')) && document.getElementById('avails-grid').children.length") == len(PRODUCTS)
    assert page.evaluate("getComputedStyle(document.getElementById('step-5')).gridColumnStart") == "2"
    assert page.evaluate("document.querySelector('.step[data-step=\"5\"]').classList.contains('split-shown')")
    assert page.evaluate("state.furthestStep") == 5                                   # showing a step counts as reaching it
    assert structure(page)["order"][3:6] == ["step-4", "step-6", "step-5"]            # (06 is hidden between them: the page's order is untouched for this pair)

    toggle(page)
    page.wait_for_function("!window.SplitView.isOn()")
    after = structure(page)
    assert after == before
    assert shown(page) == [4] and page.get_attribute("#split-toggle", "aria-pressed") == "false"
    assert not page.evaluate("document.querySelector('.step[data-step=\"5\"]').classList.contains('split-shown')")


def test_the_toggle_is_a_keyboard_button_with_a_visible_focus_ring(start):
    page = start()
    page.focus("#split-toggle")
    page.keyboard.press("Enter")
    page.wait_for_function("window.SplitView.isOn()")
    page.keyboard.press("Space")
    page.wait_for_function("!window.SplitView.isOn()")
    assert page.evaluate("document.activeElement.id") == "split-toggle"
    assert page.evaluate("document.getElementById('split-toggle').tagName") == "BUTTON"
    page.keyboard.press("Tab")
    page.keyboard.press("Shift+Tab")
    assert page.evaluate("getComputedStyle(document.activeElement, ':focus-visible').outlineStyle") != "none"


def test_there_is_nothing_to_split_before_a_second_step_is_open(browser, server, fakes):
    context, page = open_app(browser, server)
    try:
        assert page.is_hidden("#split-bar")                                           # Step 01: nothing to show beside
        page.evaluate("""() => { state.parsed = { client_name: 'A', request_type: 'Avails', products_selected: [] }; fillForm(state.parsed); state.furthestStep = 2; goToStep(2); }""")
        assert page.is_visible("#split-bar") and page.is_disabled("#split-toggle")
        assert "Open another step first" in page.inner_text("#split-hint")
    finally:
        context.close()
    assert page.errors == [], page.errors


# =================================================================================================================================
# Which step sits alongside
# =================================================================================================================================

def test_the_alongside_list_offers_open_steps_and_a_chosen_step_stays_put(start):
    page = start()
    split_on(page)
    options = page.eval_on_selector_all("#split-second-select option", "els => els.map(e => [e.value, e.textContent])")
    assert options == [["auto", "Neighbouring step (automatic)"], ["2", "Step 02 · Review"], ["3", "Step 03 · Strategy"], ["5", "Step 05 · Avails"]]
    assert page.input_value("#split-second-select") == "auto"

    page.select_option("#split-second-select", "2")
    assert shown(page) == [2, 4] and page.evaluate("window.SplitView.pair()") == [2, 4]
    assert page.evaluate("getComputedStyle(document.getElementById('step-2')).gridColumnStart") == "1"       # lower step on the left
    assert page.get_attribute("#step-2", "aria-label") == "Step 2: Review"

    # Continue in Step 04 moves THAT pane on; Step 02 stays
    page.click("#step-4 [data-next='5']")
    page.wait_for_function("window.SplitView.pair().join() === '2,5'")
    assert shown(page) == [2, 5] and page.evaluate("state.step") == 5
    assert page.input_value("#split-second-select") == "2"

    page.select_option("#split-second-select", "auto")
    assert page.evaluate("window.SplitView.pair()") == [5, 6] and shown(page) == [5, 6]
    assert structure(page)["order"][3:6] == ["step-4", "step-5", "step-6"]
    assert page.evaluate("JSON.parse(localStorage.getItem('epb.splitView'))") == {"on": True, "second": "auto"}


def test_the_neighbouring_step_follows_continue_and_back_and_the_pills(start):
    page = start()
    split_on(page)
    page.click("#step-5 [data-next='6']")                                             # Continue from the right pane: the pair slides
    page.wait_for_function("window.SplitView.pair().join() === '5,6'")
    assert page.evaluate("state.step") == 6 and shown(page) == [5, 6]
    page.click("#step-5 [data-back='4']")                                             # Back from the left pane
    page.wait_for_function("window.SplitView.pair().join() === '4,5'")
    assert page.evaluate("state.step") == 4
    # a pill of a step already on screen only makes it the active one
    page.evaluate("document.querySelector('.step[data-step=\"5\"]').click()")
    assert page.evaluate("state.step") == 5 and page.evaluate("window.SplitView.pair()") == [4, 5]
    assert "split-pane-active" in page.get_attribute("#step-5", "class")
    assert page.evaluate("document.querySelector('.step[data-step=\"5\"]').classList.contains('active')")
    # a pill of another step takes the pair there
    page.evaluate("document.querySelector('.step[data-step=\"2\"]').click()")
    assert page.evaluate("window.SplitView.pair()") == [2, 3] and page.evaluate("state.step") == 2


def test_clicking_in_a_pane_makes_it_the_one_back_and_continue_act_on(start):
    page = start()
    split_on(page)
    assert page.evaluate("state.step") == 4
    page.click("#step-5 .avails-card h3")
    assert page.evaluate("state.step") == 5
    assert page.evaluate("document.querySelector('.step[data-step=\"5\"]').classList.contains('active')")
    page.click("#step-4 .panel-head h1")
    assert page.evaluate("state.step") == 4 and page.evaluate("window.SplitView.pair()") == [4, 5]
    assert page.evaluate("document.querySelector('.step[data-step=\"4\"]').classList.contains('active')")
    assert "split-pane-active" in page.get_attribute("#step-4", "class")


# =================================================================================================================================
# Live: Step 04 -> Step 05, and the dock
# =================================================================================================================================

def test_a_budget_typed_in_step_04_reaches_step_05_without_navigation_and_the_field_keeps_the_keyboard(start):
    page = start(products=[CTV, DISPLAY, SEARCH])
    lid = ids(page)[0]
    put_avails(page, lid, {"max_spend": 10000})
    page.evaluate("renderAvailsGrid()")
    page.evaluate("state.lineItems[0].monthly_budget = 4000; renderLineItems()")
    split_on(page)
    assert sov(page, lid).startswith("40") or "40%" in sov(page, lid)
    budget = '#line-items-body input[data-key="monthly_budget"]'
    page.evaluate("sel => { window.__field = document.querySelector(sel); window.__card = document.querySelector('#avails-grid .avails-card'); }", budget)
    page.evaluate("document.getElementById('step-5').scrollTop = 60")

    type_budget(page, budget, "8000")
    page.wait_for_function("lid => document.getElementById('sov-badge-' + lid).textContent.includes('80')", arg=lid)
    # Step 05 was drawn again (the card is a new element) and kept its scroll; Step 04's field is the SAME element, still focused, caret at the end
    assert page.evaluate("window.__card.isConnected") is False
    assert page.evaluate("document.getElementById('step-5').scrollTop") > 0
    assert page.evaluate("document.activeElement === window.__field")
    assert page.evaluate("[window.__field.selectionStart, window.__field.selectionEnd, window.__field.value]") == [4, 4, "8000"]
    assert page.evaluate("state.lineItems[0].monthly_budget") == 8000 and page.evaluate("state.step") == 4

    # typing in the middle of the number: the caret stays where it is across the other pane's redraw
    page.evaluate("window.__field.setSelectionRange(2, 2)")
    page.keyboard.type("5")
    page.wait_for_function("lid => !document.getElementById('sov-badge-' + lid).textContent.includes('80%')", arg=lid)       # 80,500 of 10,000: the badge moved on
    page.wait_for_timeout(300)
    assert page.evaluate("document.activeElement === window.__field")
    assert page.evaluate("[window.__field.selectionStart, window.__field.value]") == [3, "80500"]
    shot(page, "step04-typing-with-step05")


def test_the_step_05_cards_say_the_budget_changed_and_the_dock_follows_it(start):
    page = start(products=[AWARENESS, CTV])
    lid = ids(page)[0]
    body = {"product_name": AWARENESS, "geo_text": "", "geo_type_hint": None, "months": 3, "flight_days": 92,
            "meta": {"geo": {"type": "geo_market", "key": "803", "label": "Los Angeles"}, "age_min": None, "age_max": None, "gender": "all", "languages": [],
                     "targeting": [], "retail_budget": page.evaluate("state.lineItems[0].monthly_budget * state.lineItems[0].months"), "retail_cpm": CATALOG_CPM(AWARENESS)}}
    data = main.constanza_estimate(main.ConstanzaEstimateRequest(**body))
    page.evaluate("""([lid, body, data]) => {
        const facts = constanzaHost.lineFacts(lid);
        const snap = Constanza.makeSnapshot(facts.method, body, data, facts);
        if (!snap) throw new Error('the method keeps no snapshot');
        constanzaHost.saveSnapshot(lid, snap);
        renderAvailsGrid();
    }""", [lid, body, data])
    split_on(page)
    page.evaluate("lid => openConstanzaForLine(lid)", lid)
    page.wait_for_selector("#constanza-dock:not(.hidden) .cz-card-head")
    page.wait_for_function("document.getElementById('constanza-dock').contains(document.activeElement)")       # the dock takes the keyboard when it opens; then the planner moves to Step 04
    assert page.locator("#cz-result .cz-stale").count() == 0
    assert page.locator(f"#constanza-result-{lid} .cz-stale").count() == 0

    budget = '#line-items-body input[data-key="monthly_budget"]'
    original = page.evaluate("state.lineItems[0].monthly_budget")
    type_budget(page, budget, "9000")
    page.wait_for_selector(f"#constanza-result-{lid} .cz-stale")
    assert "the budget changed" in page.inner_text(f"#constanza-result-{lid} .cz-stale")
    page.wait_for_selector("#cz-result .cz-stale")
    assert "the budget changed" in page.inner_text("#cz-result .cz-stale")
    assert page.locator("#cz-result .cz-insert").count() == 0                          # the old estimate can no longer be inserted
    assert page.evaluate("document.activeElement.dataset.key") == "monthly_budget"                  # the planner is still typing in Step 04
    shot(page, "step04-step05-dock-open")

    # putting the budget back makes both current again
    type_budget(page, budget, str(original))
    page.wait_for_function("lid => !document.querySelector('#constanza-result-' + lid + ' .cz-stale')", arg=lid)
    page.wait_for_function("() => !document.querySelector('#cz-result .cz-stale')")


def CATALOG_CPM(name):
    for products in CATALOG["products_by_family"].values():
        for p in products:
            if p["name"] == name:
                return p["meta_retail_cpm"] or p["estimated_cpm_for_imps"]
    raise KeyError(name)


def test_lines_added_and_removed_in_step_04_appear_and_go_from_step_05(start):
    page = start(products=[CTV, DISPLAY])
    split_on(page)
    assert page.locator("#avails-grid .avails-card").count() == 2
    page.select_option("#product-picker", SEARCH)
    page.click("#add-product-btn")
    page.wait_for_function("document.querySelectorAll('#avails-grid .avails-card').length === 3")
    page.click('#line-items-body .btn-remove >> nth=0')
    page.wait_for_function("document.querySelectorAll('#avails-grid .avails-card').length === 2")
    names = page.eval_on_selector_all("#avails-grid .avails-card h3", "els => els.map(e => e.childNodes[0].textContent.trim())")
    assert names == [DISPLAY, SEARCH]
    assert page.evaluate("state.lineItems.length") == 3 - 1


def test_a_line_note_and_a_target_typed_in_step_04_reach_step_05(start):
    page = start(products=[CTV, DISPLAY])
    split_on(page)
    target = '#line-items-body textarea[data-key="target_override"] >> nth=0'
    page.click(target)
    page.keyboard.type("Spanish-speaking adults 18-34", delay=10)
    page.wait_for_function("document.querySelector('#avails-grid .avails-targeting-reminder').textContent.includes('Spanish-speaking adults 18-34')")
    assert page.evaluate("document.activeElement.dataset.key") == "target_override"


# =================================================================================================================================
# Live: Step 06 -> Step 04, Step 02 -> Step 04
# =================================================================================================================================

def test_a_month_figure_committed_in_step_06_reaches_the_budgets_in_step_04(start):
    page = start(products=[CTV, DISPLAY])
    page.evaluate("state.furthestStep = 6")                                             # (as if Step 06 had been visited)
    split_on(page)
    page.select_option("#split-second-select", "6")
    assert page.evaluate("window.SplitView.pair()") == [4, 6]
    before = page.evaluate("state.lineItems[0].monthly_budget")
    first = page.locator("#mb-month-table-wrap .mb-dollar-input").first
    first.click()
    first.press("ControlOrMeta+a")
    page.keyboard.type("9999", delay=15)
    page.keyboard.press("Tab")                                                         # committed: Step 06 stores it and redraws itself
    page.wait_for_function("b => state.lineItems[0].monthly_budget !== b", arg=before)
    now = page.evaluate("state.lineItems[0].monthly_budget")
    page.wait_for_function("v => document.querySelector('#line-items-body input[data-key=\"monthly_budget\"]').value.replace(/,/g, '') === String(v)", arg=now)
    shot(page, "step04-step06")


def test_a_budget_typed_in_step_04_reaches_step_06_when_it_is_committed_not_at_every_pause(start):
    """Step 06 re-divides a line's months whenever it is drawn against a changed budget: drawn at each pause in typing it would do so for every half-typed
    number and let cents drift. So it follows the field's change event (blur / Enter), and the other panes follow the typing."""
    page = start(products=[CTV, DISPLAY])
    page.evaluate("state.furthestStep = 6")
    split_on(page)
    page.select_option("#split-second-select", "6")
    before = page.inner_text("#mb-plan-summary")
    budget = '#line-items-body input[data-key="monthly_budget"]'
    type_budget(page, budget, "9000")
    page.wait_for_timeout(500)                                                          # well past the pause that redraws the other panes
    assert page.inner_text("#mb-plan-summary") == before
    page.keyboard.press("Tab")
    page.wait_for_function("t => document.getElementById('mb-plan-summary').innerText !== t", arg=before)
    assert "$" in page.inner_text("#mb-plan-summary") and page.evaluate("state.lineItems[0].monthly_budget") == 9000


def test_a_pane_that_holds_the_keyboard_is_left_alone_until_the_keyboard_leaves_it(start):
    page = start(products=[CTV, DISPLAY])
    lid = ids(page)[0]
    page.evaluate("([lid]) => { state.availsData[lid] = { max_spend: 10000 }; state.lineItems[0].monthly_budget = 4000; renderLineItems(); renderAvailsGrid(); }", [lid])
    split_on(page)
    typed = f'#avails-grid [data-lid="{lid}"][data-key="max_spend"]'
    page.click(typed)
    page.evaluate("sel => { window.__input = document.querySelector(sel); }", typed)
    page.evaluate("state.lineItems[0].monthly_budget = 7000; renderLineItems()")      # a budget that changes while the planner is in a Step 05 field
    page.wait_for_timeout(500)
    assert page.evaluate("document.activeElement === window.__input && window.__input.isConnected")
    assert "40" in sov(page, lid)                                                       # Step 05 was not drawn from under the planner's hands
    page.click("#step-4 .panel-head h1")                                                # the keyboard leaves Step 05
    page.wait_for_function("lid => document.getElementById('sov-badge-' + lid).textContent.includes('70')", arg=lid)


def test_the_rules_for_which_steps_may_sit_together_and_where_the_panes_go(start):
    page = start()
    check = """() => {
      const P = SplitView._pure, out = {};
      out.noLines = [5, 6, 8].map(n => P.eligible(n, n - 1, n - 1, false));
      out.nextWithLines = [3, 4, 5, 6, 7, 8].map(n => P.eligible(n, n - 1, n - 1, true));        // the next step of a step that has just been reached
      out.reached = P.eligible(7, 4, 8, false) && !P.eligible(4, 4, 8, true) && !P.eligible(1, 4, 8, true) && !P.eligible(9, 4, 8, true);
      const ok = n => n === 5 || n === 3;
      out.pick = [P.pickPair(4, 'auto', ok), P.pickPair(4, 3, ok), P.pickPair(6, 'auto', n => n === 5), P.pickPair(2, 'auto', () => false)];
      out.route = [P.route([4, 5], 4, true, 5), P.route([4, 5], 4, true, 6), P.route([2, 4], 4, false, 5), P.route([2, 4], 2, false, 3), P.route([2, 4], 4, false, 2)];
      return out;
    }"""
    got = page.evaluate(check)
    assert got["noLines"] == [False, False, False]
    assert got["nextWithLines"] == [False, False, True, True, False, True]            # 3 and 4 do work of their own on a first visit, 7 asks for a choice
    assert got["reached"] is True
    assert got["pick"] == [{"pair": [4, 5], "auto": True}, {"pair": [3, 4], "auto": False}, {"pair": [5, 6], "auto": True}, None]
    assert got["route"] == [[4, 5], None, [2, 5], [3, 4], [2, 4]]


def test_dates_and_the_agency_fee_typed_in_step_02_reach_step_04(start):
    page = start(products=[CTV, DISPLAY])
    toggle(page)
    page.select_option("#split-second-select", "2")
    assert page.evaluate("window.SplitView.pair()") == [2, 4]
    months = '#line-items-body td.col-months'
    page.wait_for_selector(months)
    assert page.evaluate("state.lineItems.map(li => li.months)") == [3, 3]
    page.fill('[data-field="end_date"]', "2027-02-28")
    page.wait_for_function("state.lineItems.every(li => li.months === 4)")
    assert page.evaluate("state.parsed.end_date") == "2027-02-28"                      # copied into the proposal at once, not when Step 02 is left
    page.fill('[data-field="agency_fee"]', "15")
    page.wait_for_function("document.getElementById('curate-agency-fee-input').value === '0.15'")
    assert page.evaluate("state.parsed.agency_fee") == 0.15 and page.evaluate("state.step") == 2


# =================================================================================================================================
# Width, memory, safety
# =================================================================================================================================

def test_a_narrow_window_falls_back_to_one_step_and_a_wide_one_brings_the_split_back(start):
    page = start()
    split_on(page)
    page.set_viewport_size({"width": 1000, "height": 800})
    page.wait_for_function("!window.SplitView.isOn()")
    assert shown(page) == [4]
    assert page.is_disabled("#split-toggle") and "1100" in page.get_attribute("#split-toggle", "title") and "1100" in page.inner_text("#split-hint")
    assert page.evaluate("document.querySelector('main.canvas').className") == "canvas"
    shot(page, "narrow-fallback-1000")
    page.set_viewport_size({"width": 1280, "height": 800})
    page.wait_for_function("window.SplitView.isOn()")
    assert shown(page) == [4, 5]
    shot(page, "step04-step05-1280")


def test_the_choice_is_remembered_in_localstorage_and_a_blocked_localstorage_breaks_nothing(start):
    page = start()
    split_on(page)
    page.select_option("#split-second-select", "2")
    assert page.evaluate("JSON.parse(localStorage.getItem('epb.splitView'))") == {"on": True, "second": 2}
    toggle(page)
    assert page.evaluate("JSON.parse(localStorage.getItem('epb.splitView'))") == {"on": False, "second": 2}


def test_the_choice_is_applied_when_the_page_opens_and_a_blocked_localstorage_breaks_nothing(browser, server, fakes):
    seeded = "localStorage.setItem('epb.splitView', JSON.stringify({ on: true, second: 'auto' }))"
    context, page = open_app(browser, server, init_script=seeded)
    try:
        page.evaluate(REACH_STEP_4, {"products": PRODUCTS, "parsed": None})
        page.wait_for_function("window.SplitView.isOn()")
        assert shown(page) == [4, 5] and page.get_attribute("#split-toggle", "aria-pressed") == "true"
    finally:
        context.close()
    assert page.errors == [], page.errors

    blocked = "Object.defineProperty(window, 'localStorage', { get() { throw new Error('storage is blocked'); } })"
    context, page = open_app(browser, server, init_script=blocked)
    try:
        page.evaluate(REACH_STEP_4, {"products": PRODUCTS, "parsed": None})
        split_on(page)
        assert shown(page) == [4, 5]
        toggle(page)
        page.wait_for_function("!window.SplitView.isOn()")
    finally:
        context.close()
    assert page.errors == [], page.errors


def test_a_hostile_client_name_runs_nothing_and_never_reaches_a_label(start):
    hostile = "<img src=x onerror=\"window.__pwned = 1\">"
    page = start(parsed={"client_name": hostile})
    split_on(page)
    page.select_option("#split-second-select", "2")
    page.wait_for_timeout(300)
    assert page.evaluate("window.__pwned") is None
    labels = page.evaluate("[...document.querySelectorAll('[aria-label]')].map(e => e.getAttribute('aria-label')).join('|') + [...document.querySelectorAll('#split-bar *')].map(e => e.textContent).join('|')")
    assert "<img" not in labels and "onerror" not in labels


def test_turning_it_on_after_a_new_proposal_is_not_possible_on_step_01(start):
    page = start()
    split_on(page)
    page.evaluate("goToStep(1)")
    page.wait_for_function("!window.SplitView.isOn()")
    assert shown(page) == [1] and page.is_hidden("#split-bar")
    assert page.evaluate("document.querySelector('main.canvas').className") == "canvas"
