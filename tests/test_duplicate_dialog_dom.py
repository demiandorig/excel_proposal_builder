"""The "Duplicate for a new client" dialog (app/static/duplicate-proposal.js + .css) in a REAL browser (headless Chromium through Playwright): where the
keyboard goes, what a double click sends, what a failed request leaves behind, whether a client called `<img onerror=...>` runs, whether Escape and
the backdrop can throw away a request in flight.

The page is a small harness (not the app): the REAL styles.css, proposal-history.css, duplicate-proposal.css, proposal-history.js (the dialog is built
on its shell) and duplicate-proposal.js, an opener button, and a programmable fake API in place of window.fetch (duplicate-defaults and duplicate, a
request log, answers that can be held back, failed or malformed). Skipped when Playwright or its Chromium is absent. The real pages are
tests/test_proposal_history_planner_dom.py (the button, the hand-off) and tests/test_duplicate_flow_dom.py (the whole flow over the real routes)."""
from __future__ import annotations

import json
import re

import pytest

from tests import dom_support as dom

playwright_sync = pytest.importorskip("playwright.sync_api")

SOURCE = {
    "proposal_id": "SRC0000000000000001", "title": "EVC-48213 | Acme Motors - Fall Push | Entravision | Sep26 | Digital Media Proposal", "client_name": "Acme Motors",
    "status": "draft", "start_date": "2026-09-01", "end_date": "2026-11-30", "time_unit": "month", "periods": 3, "option_count": 2, "line_count": 5,
}
DEFAULTS = {
    "requested_by": "Ana Torres", "salesperson_email": "ana@entravision.com", "salesperson_market": "Phoenix",
    "request_type": "Full Presentation (Deck + Avails + Proposal Included)", "agency_name": "Initiative Media", "agency_fee": 0.15, "client_website": "https://www.acmemotors.com",
}
NEW_ID = "NEWDRAFT0000000000001"
DONE = {
    "proposal_id": NEW_ID, "created": True, "proposal_title": "EVC-50001 | Beta Burgers | Entravision | Feb27 | Digital Media Proposal", "open_url": "/?reopen=" + NEW_ID,
    "wizard_step": 2, "duplicated_from": {"proposal_id": SOURCE["proposal_id"], "title": SOURCE["title"], "client_name": "Acme Motors"},
    "notes": [
        {"code": "dates_moved", "level": "info", "text": "Flight moved from Sep 1, 2026 - Nov 30, 2026 to Feb 1, 2027 - Apr 30, 2027."},
        {"code": "avails_cleared", "level": "check", "text": "Avails estimates from Constanza were cleared on 1 line(s)."},
        {"code": "rates_carried", "level": "check", "text": "2 lines carry a custom rate from the original deal."},
    ],
    "stats": {"totals_before": {"A": 69000.0}, "totals_after": {"A": 69000.0}, "periods": 3, "time_unit": "month"},
}

HARNESS_JS = r"""
(function () {
  const api = window.__api = {
    log: [], defaults: null, defaultsStatus: 200, defaultsBody: null, delay: 0, hold: false, pending: [], post: null, posts: 0,
  };
  const json = (data, status) => new Response(JSON.stringify(data), { status: status || 200, headers: { "Content-Type": "application/json" } });
  window.fetch = function (url, init) {
    init = init || {};
    const u = new URL(String(url), location.href), method = (init.method || "GET").toUpperCase();
    const req = { method, path: u.pathname, body: init.body ? JSON.parse(init.body) : null, headers: init.headers || {}, aborted: false, status: null };
    api.log.push(req);
    return new Promise((resolve, reject) => {
      const respond = () => {
        let out;
        if (method === "GET" && /\/duplicate-defaults$/.test(u.pathname)) {
          if (api.defaultsStatus === "offline") return reject(new TypeError("Failed to fetch"));
          if (api.defaultsStatus === "html") out = new Response("<html>Please sign in</html>", { status: 200, headers: { "Content-Type": "text/html" } });
          else out = json(api.defaultsStatus === 200 ? (api.defaultsBody || { source: api.source, defaults: api.defaults }) : { detail: "SECRET-SERVER-DETAIL" }, api.defaultsStatus);
        } else if (method === "POST" && /\/duplicate$/.test(u.pathname)) {
          const n = ++api.posts;
          const a = typeof api.post === "function" ? api.post(req, n) : { status: 200, body: api.done };
          if (a === "offline") return reject(new TypeError("Failed to fetch"));
          if (a && a.html) out = new Response("<html>Please sign in</html>", { status: 200, headers: { "Content-Type": "text/html" } });
          else out = json(a.body, a.status);
        } else out = json({ detail: "no such route" }, 404);
        req.status = out.status;
        resolve(out);
      };
      const slot = { req, respond };
      const wait = typeof api.delay === "function" ? api.delay(req) : api.delay;
      const t = setTimeout(() => { if (method === "POST" && api.hold) api.pending.push(slot); else respond(); }, wait || 0);
      if (init.signal) init.signal.addEventListener("abort", () => { clearTimeout(t); req.aborted = true; const i = api.pending.indexOf(slot); if (i >= 0) api.pending.splice(i, 1); reject(new DOMException("The operation was aborted.", "AbortError")); });
    });
  };
  api.release = () => { const slot = api.pending.shift(); if (slot) slot.respond(); return !!slot; };

  window.__done = []; window.__prepared = 0;
  window.openDup = function (cfg) {
    cfg = cfg || {};
    window.__done = [];
    const opts = { proposal: cfg.proposal === undefined ? { proposal_id: api.source.proposal_id, client_name: api.source.client_name, notion_id: "EVC-48213" } : cfg.proposal,
      trigger: document.getElementById("opener"), onDone: (r) => window.__done.push(r) };
    if (cfg.prepare !== undefined) opts.prepare = () => { window.__prepared++; return typeof cfg.prepare === "function" ? cfg.prepare(window.__prepared) : cfg.prepare; };
    window.__handle = DuplicateProposal.open(opts);
    return !!window.__handle;
  };
  /*CONTRAST*/
})();
"""

HARNESS = (
    """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>harness</title>
<link rel="stylesheet" href="/static/styles.css"><link rel="stylesheet" href="/static/proposal-history.css"><link rel="stylesheet" href="/static/duplicate-proposal.css"></head><body>
<main style="padding:24px"><h1>The page behind</h1><button id="opener" type="button">Duplicate for new client</button> <button id="other" type="button">Other</button><div style="height:2400px"></div></main>
<script src="/static/proposal-history.js"></script><script src="/static/duplicate-proposal.js"></script>
<script>%(js)s</script></body></html>"""
)
PAGES = {"/harness.html": HARNESS % {"js": HARNESS_JS.replace("/*CONTRAST*/", dom.CONTRAST_JS)}}


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
    p.evaluate("([source, defaults, done]) => { __api.source = source; __api.defaults = defaults; __api.done = done; }", [SOURCE, DEFAULTS, DONE])
    yield p
    p.context_.close()
    # The dialog logs ONE line per failed request on purpose ("DuplicateProposal: ..."): those are not page errors.
    assert [e for e in p.errors if not e.startswith("DuplicateProposal:")] == [], p.errors
    assert p.dialogs == [], f"a native confirm()/alert() opened: {p.dialogs}"


# --------------------------------------------------------------------------------------------- helpers

def open_dialog(page, wait="#dup-client-name", **cfg):
    page.evaluate("() => document.getElementById('opener').focus({ preventScroll: true })")
    page.evaluate("cfg => openDup(cfg)", cfg)
    page.wait_for_selector("#duplicate-dialog")
    if wait:
        page.wait_for_selector(wait)


def fill_valid(page, client="Beta Burgers", start="2027-02-01", end="2027-04-30", notion=None, agency=None, fee=None):
    page.fill("#dup-client-name", client)
    page.fill("#dup-start", start)
    page.fill("#dup-end", end)
    for selector, value in (("#dup-notion", notion), ("#dup-agency", agency), ("#dup-fee", fee)):
        if value is not None:
            page.fill(selector, value)


def tab_to(page, element_id, limit=8):
    """Presses Tab until the keyboard is on `element_id` (a date input has the day, month and year parts, and in Chrome its calendar button, before Tab leaves it)."""
    for _ in range(limit):
        if active(page) == element_id:
            return
        page.keyboard.press("Tab")
    assert active(page) == element_id, active(page)


def posts(page):
    return page.evaluate("() => __api.log.filter(r => r.method === 'POST').map(r => ({ path: r.path, body: r.body, aborted: r.aborted, status: r.status }))")


def gets(page):
    return page.evaluate("() => __api.log.filter(r => r.method === 'GET').map(r => r.path)")


def active(page):
    return page.evaluate("() => document.activeElement ? (document.activeElement.id || document.activeElement.tagName.toLowerCase() + '.' + document.activeElement.className) : ''")


def dialog_open(page):
    return page.locator("#duplicate-dialog").count() == 1


def set_post(page, js_function):
    page.evaluate("f => { __api.post = new Function('req', 'n', f); }", js_function)


def visible_errors(page):
    return page.evaluate("() => Object.fromEntries([...document.querySelectorAll('#duplicate-dialog .ph-ferror')].filter(e => !e.hidden).map(e => [e.id.replace(/-err$/, ''), e.textContent]))")


# --------------------------------------------------------------------------------------------- opening and the defaults

def test_it_opens_as_a_modal_dialog_that_loads_the_defaults_then_shows_the_form(page):
    page.evaluate("() => { __api.delay = 150; }")
    open_dialog(page, wait=None)
    dlg = page.locator("#duplicate-dialog")
    assert dlg.get_attribute("role") == "dialog" and dlg.get_attribute("aria-modal") == "true"
    assert page.inner_text("#duplicate-dialog .ph-title") == "Duplicate for a new client"
    assert page.locator(".ph-overlay--top").count() == 1                       # above the history list, which is z-index 600
    assert page.locator("#duplicate-dialog .dup-state[role='status']").count() == 1 and page.locator("#dup-client-name").count() == 0      # loading, no form yet
    page.wait_for_selector("#dup-client-name")
    assert gets(page) == ["/api/proposal/SRC0000000000000001/duplicate-defaults"]
    sub = page.inner_text("#duplicate-dialog .ph-sub")
    assert sub.startswith("Starts from Acme Motors (EVC-48213).") and "change everything in the steps that follow" in sub
    assert page.get_attribute("#duplicate-dialog", "aria-labelledby") and page.get_attribute("#duplicate-dialog", "aria-describedby")
    assert active(page) == "dup-client-name"                                   # the keyboard starts in the first field, not on the page behind


def test_defaults_are_filled_in_but_never_the_old_client_dates_notion_id_or_agency(page):
    open_dialog(page)
    value = lambda sel: page.input_value(sel)                                  # noqa: E731
    assert value("#dup-client-name") == "" and value("#dup-start") == "" and value("#dup-end") == "" and value("#dup-notion") == ""
    assert value("#dup-agency") == "" and value("#dup-fee") == "" and value("#dup-website") == "" and value("#dup-campaign") == ""
    assert value("#dup-email") == "ana@entravision.com" and value("#dup-requested-by") == "Ana Torres" and value("#dup-market") == "Phoenix"
    assert value("#dup-request-type").startswith("Full Presentation")
    assert not page.is_checked("#dup-keep-avails")
    assert page.locator("#duplicate-dialog details.ph-details").get_attribute("open") is None          # More details starts closed
    labels = page.eval_on_selector_all("#duplicate-dialog .ph-flabel", "els => els.map(e => e.textContent.replace(/\\s+/g, ' ').trim())")
    assert labels == ["New client name * (required)", "Flight start * (required)", "Flight end * (required)", "Notion request ID", "Salesperson email", "Agency", "Agency fee (%)",
                      "Client website", "Requested by", "Market", "Request type", "Campaign name"]
    assert page.get_attribute("#dup-client-name", "aria-required") == "true"
    assert page.inner_text("#dup-flight-hint") == "The original ran Sep 1 to Nov 30, 2026 (3 months)."        # something to go by before any date is typed


def test_same_as_the_original_fills_the_agency_and_the_fee(page):
    open_dialog(page)
    link = page.locator("#dup-same-agency")
    assert link.inner_text() == "Same as the original: Initiative Media, 15%"
    link.click()
    assert page.input_value("#dup-agency") == "Initiative Media" and page.input_value("#dup-fee") == "15"
    assert active(page) == "dup-agency"
    fill_valid(page)
    page.click("#dup-submit")
    page.wait_for_selector(".dup-done")
    body = posts(page)[0]["body"]
    assert body["agency_name"] == "Initiative Media" and body["agency_fee"] == 0.15           # the percent became the fraction the server wants


def test_no_same_as_link_when_the_original_had_no_agency(page):
    page.evaluate("() => { __api.defaults = Object.assign({}, __api.defaults, { agency_name: '', agency_fee: null }); }")
    open_dialog(page)
    assert page.locator("#dup-same-agency").count() == 0


def test_the_flight_hint_follows_the_dates(page):
    open_dialog(page)
    page.fill("#dup-start", "2027-02-01")
    assert page.inner_text("#dup-flight-hint") == "The original ran Sep 1 to Nov 30, 2026 (3 months)."       # one date is not enough
    page.fill("#dup-end", "2027-04-30")
    assert page.inner_text("#dup-flight-hint") == "3 months (Feb 1 to Apr 30, 2027). The original was 3 months."
    page.fill("#dup-end", "2027-05-31")
    assert page.inner_text("#dup-flight-hint") == "4 months (Feb 1 to May 31, 2027). The original was 3 months. Budgets stay per month, so the totals will change."
    assert page.get_attribute("#dup-end", "aria-describedby") == "dup-flight-hint"


# --------------------------------------------------------------------------------------------- validation

def test_submitting_an_empty_form_shows_every_message_inline_and_focuses_the_first_field(page):
    open_dialog(page)
    page.click("#dup-submit")
    assert visible_errors(page) == {"dup-client-name": "Enter the new client's name.", "dup-start": "Enter the start date as YYYY-MM-DD.", "dup-end": "Enter the end date as YYYY-MM-DD."}
    assert posts(page) == []                                                   # nothing was sent
    assert active(page) == "dup-client-name"
    for field in ("dup-client-name", "dup-start", "dup-end"):
        assert page.get_attribute("#" + field, "aria-invalid") == "true"
        assert page.get_attribute("#" + field, "aria-describedby").split()[-1] == field + "-err"
    assert page.get_attribute("#dup-notion", "aria-invalid") is None
    page.wait_for_function("() => document.getElementById('dup-live').textContent.includes('3 things need fixing')")


def test_focus_goes_to_the_first_invalid_field_in_form_order_and_typing_clears_its_message(page):
    open_dialog(page)
    fill_valid(page)
    page.fill("#dup-notion", "abc")
    page.fill("#dup-email", "nope")
    page.click("#dup-submit")
    assert active(page) == "dup-notion"                                        # the Notion ID comes before the email in the form
    assert visible_errors(page) == {"dup-notion": "The Notion ID is a 4 or 5 digit number, for example EVC-48213.", "dup-email": "That doesn't look like an email address."}
    page.type("#dup-notion", "1")
    assert "dup-notion" not in visible_errors(page) and page.get_attribute("#dup-notion", "aria-invalid") is None
    assert page.get_attribute("#dup-notion", "aria-describedby") == "dup-notion-hint"       # back to the hint only


def test_a_field_is_checked_when_it_is_left_but_an_empty_required_one_is_not_nagged_at(page):
    open_dialog(page)
    page.focus("#dup-client-name")
    page.keyboard.press("Tab")                                                 # left empty: no message yet
    assert visible_errors(page) == {}
    page.fill("#dup-start", "2027-04-30")
    page.fill("#dup-end", "2027-02-01")
    page.focus("#dup-notion")                                                  # (Tab inside a date input only moves between its day / month / year parts)
    assert visible_errors(page) == {"dup-end": "The end date can't be before the start date."}
    page.fill("#dup-start", "2027-01-01")                                      # fixing the start clears the end's message once the start is left
    page.focus("#dup-notion")
    assert visible_errors(page) == {}


def test_the_notion_id_is_tidied_when_the_field_is_left(page):
    open_dialog(page)
    page.fill("#dup-notion", "evc 50001")
    page.focus("#dup-notion")
    page.keyboard.press("Tab")
    assert page.input_value("#dup-notion") == "EVC-50001"
    fill_valid(page)
    page.click("#dup-submit")
    page.wait_for_selector(".dup-done")
    assert posts(page)[0]["body"]["notion_id"] == "EVC-50001"


def test_a_problem_in_more_details_opens_it_and_focuses_the_field(page):
    open_dialog(page)
    fill_valid(page)
    page.click("#duplicate-dialog details.ph-details > summary")
    page.fill("#dup-campaign", "c" * 201)
    page.click("#duplicate-dialog details.ph-details > summary")                # close it again: the error must open it
    assert page.locator("#duplicate-dialog details.ph-details").get_attribute("open") is None
    page.click("#dup-submit")
    assert page.locator("#duplicate-dialog details.ph-details").get_attribute("open") is not None
    assert active(page) == "dup-campaign"
    assert visible_errors(page) == {"dup-campaign": "The campaign name can be at most 200 characters."}


def test_it_can_be_completed_with_the_keyboard_alone(page):
    open_dialog(page)
    assert active(page) == "dup-client-name"
    page.keyboard.type("Beta Burgers")
    tab_to(page, "dup-start")                                                  # start: type the digits of a date input
    page.keyboard.type("02012027")
    tab_to(page, "dup-end")
    page.keyboard.type("04302027")
    page.keyboard.press("Enter")                                               # Enter in a field submits
    page.wait_for_selector(".dup-done")
    body = posts(page)[0]["body"]
    assert (body["client_name"], body["start_date"], body["end_date"]) == ("Beta Burgers", "2027-02-01", "2027-04-30")
    assert active(page) == "dup-open-link"                                     # and the next Enter opens the copy


# --------------------------------------------------------------------------------------------- the request

def test_the_request_has_the_documented_body_and_only_one_request_id_per_dialog(page):
    open_dialog(page)
    fill_valid(page, notion="50001", agency="Beta Media", fee="12.5%")
    page.click("#duplicate-dialog details.ph-details > summary")
    page.fill("#dup-campaign", "Spring Push")
    page.check("#dup-keep-avails")
    page.click("#dup-submit")
    page.wait_for_selector(".dup-done")
    (sent,) = posts(page)
    assert sent["path"] == "/api/proposal/SRC0000000000000001/duplicate"
    body = sent["body"]
    assert re.fullmatch(r"[A-Za-z0-9_-]{16,64}", body.pop("request_id"))
    assert body == {
        "client_name": "Beta Burgers", "start_date": "2027-02-01", "end_date": "2027-04-30", "notion_id": "EVC-50001", "agency_name": "Beta Media", "agency_fee": 0.125,
        "client_website": "", "requested_by": "Ana Torres", "salesperson_email": "ana@entravision.com", "salesperson_market": "Phoenix",
        "request_type": "Full Presentation (Deck + Avails + Proposal Included)", "campaign_name": "Spring Push", "keep_avails": True,
    }
    assert page.evaluate("() => __api.log.find(r => r.method === 'POST').headers['Content-Type']") == "application/json"


def test_a_double_click_sends_one_request(page):
    page.evaluate("() => { __api.hold = true; }")
    open_dialog(page)
    fill_valid(page)
    page.dblclick("#dup-submit")
    page.wait_for_function("() => __api.pending.length === 1")
    page.click("#dup-submit", force=True)                                      # and a third press while it is in flight
    page.keyboard.press("Enter")
    assert page.evaluate("() => __api.log.filter(r => r.method === 'POST').length") == 1
    assert page.get_attribute("#dup-submit", "aria-disabled") == "true" and "Creating" in page.inner_text("#dup-submit")
    assert page.get_attribute("#duplicate-dialog", "aria-busy") == "true"
    assert page.evaluate("() => document.querySelector('#duplicate-dialog fieldset').disabled")          # the form cannot be edited while it is sent
    page.evaluate("() => __api.release()")
    page.wait_for_selector(".dup-done")
    assert len(posts(page)) == 1 and page.evaluate("() => __done.length") == 1


def test_a_failed_request_keeps_the_form_and_the_request_id_and_the_retry_reuses_it(page):
    set_post(page, "return n === 1 ? { status: 500, body: { detail: 'SECRET-SERVER-DETAIL boom' } } : { status: 200, body: __api.done };")
    open_dialog(page)
    fill_valid(page, notion="50001")
    page.click("#dup-submit")
    page.wait_for_selector("#dup-alert:not([hidden])")
    assert page.inner_text("#dup-alert") == "We couldn't create the copy. Nothing was saved."
    assert "SECRET" not in page.inner_text("#duplicate-dialog")               # the server's own text is never printed
    assert page.get_attribute("#dup-alert", "role") == "alert"
    assert page.input_value("#dup-client-name") == "Beta Burgers" and page.input_value("#dup-start") == "2027-02-01" and page.input_value("#dup-notion") == "EVC-50001"
    assert not page.evaluate("() => document.querySelector('#duplicate-dialog fieldset').disabled")      # editable again
    assert active(page) == "dup-submit"                                        # Enter retries
    assert page.get_attribute("#duplicate-dialog", "aria-busy") is None
    page.keyboard.press("Enter")
    page.wait_for_selector(".dup-done")
    first, second = posts(page)
    assert first["body"]["request_id"] == second["body"]["request_id"] and first["body"] == second["body"]
    assert page.locator("#dup-alert").count() == 0                              # the form (and its banner) is gone: success replaces it
    assert page.evaluate("() => __done.length") == 1                            # onDone: once, for the success only


@pytest.mark.parametrize("answer,words", [
    ("{ status: 503, body: { detail: 'x' } }", "The database isn't reachable right now. Nothing was created. Try again in a moment."),
    ("{ status: 403, body: { detail: 'x' } }", "You can only duplicate your own proposals."),
    ("{ status: 409, body: { detail: 'x' } }", "We couldn't create the copy. Please try again."),
    ("{ status: 502, body: null }", "The server had a problem. Nothing was created. Try again in a moment."),
    ("'offline'", "We couldn't reach the server. Check your connection and try again."),
    ("{ html: true }", "We couldn't create the copy. Please try again."),                    # a sign-in page where JSON was expected is NOT a success
    ("{ status: 200, body: { ok: true } }", "We couldn't create the copy. Please try again."),   # an answer without a proposal id is not a success either
])
def test_each_failure_is_told_in_plain_words_and_never_reported_as_success(page, answer, words):
    set_post(page, f"return {answer};")
    open_dialog(page)
    fill_valid(page)
    page.click("#dup-submit")
    page.wait_for_selector("#dup-alert:not([hidden])")
    assert page.inner_text("#dup-alert").startswith(words)
    assert page.locator(".dup-done").count() == 0 and page.evaluate("() => __done.length") == 0
    assert page.input_value("#dup-client-name") == "Beta Burgers"


def test_a_422_marks_the_fields_it_names_with_the_servers_sentence(page):
    set_post(page, """return { status: 422, body: { detail: [
        { loc: ['body', 'end_date'], msg: "Value error, The end date can't be before the start date.", type: 'value_error' },
        { loc: ['body', 'client_name'], msg: 'Value error, The client name can be at most 200 characters.', type: 'value_error' },
        { loc: ['body', 'campaign_name'], msg: 'Value error, The campaign name can be at most 200 characters.', type: 'value_error' } ] } };""")
    open_dialog(page)
    fill_valid(page)
    page.click("#dup-submit")
    page.wait_for_selector("#dup-alert:not([hidden])")
    assert visible_errors(page) == {"dup-client-name": "The client name can be at most 200 characters.", "dup-end": "The end date can't be before the start date.",
                                    "dup-campaign": "The campaign name can be at most 200 characters."}
    assert page.inner_text("#dup-alert") == "Some details need fixing. They are marked below."
    assert active(page) == "dup-client-name"                                   # the first one in the form
    assert page.locator("#duplicate-dialog details.ph-details").get_attribute("open") is not None      # the one inside More details is visible
    assert page.evaluate("() => __done.length") == 0


def test_a_422_about_something_that_is_not_a_field_goes_to_the_banner(page):
    set_post(page, "return { status: 422, body: { detail: [{ loc: ['body', 'request_id'], msg: \"Value error, The page didn't send a valid request id. Reload the page and try again.\" }] } };")
    open_dialog(page)
    fill_valid(page)
    page.click("#dup-submit")
    page.wait_for_selector("#dup-alert:not([hidden])")
    assert page.inner_text("#dup-alert") == "The page didn't send a valid request id. Reload the page and try again."
    assert visible_errors(page) == {}


def test_a_corrected_form_after_an_error_is_sent_with_the_same_request_id(page):
    """The id is per dialog, the server derives the copy's id from it AND the form: a corrected form is a new copy, a retry of the same one is the first."""
    set_post(page, "return n === 1 ? { status: 422, body: { detail: [{ loc: ['body', 'end_date'], msg: 'Value error, The flight can\\'t be longer than 10 years.' }] } } : { status: 200, body: __api.done };")
    open_dialog(page)
    fill_valid(page)
    page.click("#dup-submit")
    page.wait_for_selector("#dup-alert:not([hidden])")
    page.fill("#dup-end", "2027-05-31")
    assert "dup-end" not in visible_errors(page)
    page.click("#dup-submit")
    page.wait_for_selector(".dup-done")
    a, b = posts(page)
    assert a["body"]["request_id"] == b["body"]["request_id"] and a["body"]["end_date"] == "2027-04-30" and b["body"]["end_date"] == "2027-05-31"


def test_a_new_dialog_makes_a_new_request_id(page):
    for _ in range(2):
        open_dialog(page)
        fill_valid(page)
        page.click("#dup-submit")
        page.wait_for_selector(".dup-done")
        page.click("#dup-close")
        page.wait_for_selector("#duplicate-dialog", state="detached")
    ids = [p["body"]["request_id"] for p in posts(page)]
    assert len(ids) == 2 and ids[0] != ids[1]


# --------------------------------------------------------------------------------------------- success

def test_success_says_what_to_check_and_offers_the_copy_in_a_new_tab(page):
    open_dialog(page)
    fill_valid(page)
    page.click("#dup-submit")
    page.wait_for_selector(".dup-done")
    assert page.inner_text(".dup-done-title") == "Copy created"
    assert page.inner_text(".dup-done-name") == DONE["proposal_title"]
    assert page.inner_text(".dup-done-text") == "2 things to check in Step 02."          # the two "check" notes; the info note is not counted
    link = page.locator("#dup-open-link")
    assert link.get_attribute("href") == "/?reopen=" + NEW_ID and link.get_attribute("target") == "_blank" and link.get_attribute("rel") == "noopener"
    assert link.inner_text().startswith("Open the copy") and "new tab" in link.inner_text()
    assert active(page) == "dup-open-link"                                     # autofocused: Enter opens the copy
    assert page.locator("#dup-form, #dup-submit").count() == 0                 # the form is gone
    assert page.evaluate("() => __done.map(r => r.proposal_id)") == [NEW_ID]
    assert page.evaluate("() => __done[0].notes.length") == 3                  # onDone gets the whole answer


def test_success_when_the_copy_already_existed_says_so(page):
    set_post(page, "return { status: 200, body: Object.assign({}, __api.done, { created: false, notes: [] }) };")
    open_dialog(page)
    fill_valid(page)
    page.click("#dup-submit")
    page.wait_for_selector(".dup-done")
    assert page.inner_text(".dup-done-title") == "This copy already exists"
    assert "no second copy was made" in page.inner_text(".dup-done-text") and "Nothing was flagged" in page.inner_text(".dup-done-text")


def test_a_hostile_open_url_in_the_answer_is_not_followed(page):
    set_post(page, "return { status: 200, body: Object.assign({}, __api.done, { open_url: 'https://evil.example/phish' }) };")
    open_dialog(page)
    fill_valid(page)
    page.click("#dup-submit")
    page.wait_for_selector(".dup-done")
    assert page.get_attribute("#dup-open-link", "href") == "/?reopen=" + NEW_ID


def test_the_keyboard_stays_on_open_the_copy_when_the_list_behind_tries_to_take_it(page):
    """A history list that reloads behind the dialog moves the keyboard to its new row; the focus trap sends it to the top of the dialog (the X)."""
    open_dialog(page)
    fill_valid(page)
    page.click("#dup-submit")
    page.wait_for_selector(".dup-done")
    page.evaluate("() => { const b = document.createElement('button'); b.id = 'behind'; b.textContent = 'row'; document.body.append(b); b.focus(); }")
    page.wait_for_function("() => document.activeElement && document.activeElement.id === 'dup-open-link'")


# --------------------------------------------------------------------------------------------- closing

def test_escape_and_the_close_button_close_it_when_idle_and_the_keyboard_goes_back_to_the_opener(page):
    open_dialog(page)
    page.keyboard.press("Escape")
    page.wait_for_selector("#duplicate-dialog", state="detached")
    assert active(page) == "opener"
    assert posts(page) == [] and page.evaluate("() => __done.length") == 0       # closed without a copy: onDone never fires
    open_dialog(page)
    page.click("#duplicate-dialog .ph-x")
    page.wait_for_selector("#duplicate-dialog", state="detached")
    open_dialog(page)
    page.click("#dup-cancel")
    page.wait_for_selector("#duplicate-dialog", state="detached")
    assert active(page) == "opener"


def test_nothing_closes_it_while_a_request_is_in_flight(page):
    page.evaluate("() => { __api.hold = true; }")
    open_dialog(page)
    fill_valid(page)
    page.click("#dup-submit")
    page.wait_for_function("() => __api.pending.length === 1")
    page.keyboard.press("Escape")
    page.click("#duplicate-dialog .ph-x", force=True)
    page.mouse.click(5, 5)                                                     # the backdrop
    assert dialog_open(page)
    page.evaluate("() => __api.release()")
    page.wait_for_selector(".dup-done")
    page.keyboard.press("Escape")                                              # idle again: it closes
    page.wait_for_selector("#duplicate-dialog", state="detached")


def test_a_stray_click_on_the_backdrop_does_not_throw_away_typed_text_but_an_untouched_form_closes(page):
    open_dialog(page)
    page.mouse.click(5, 5)                                                     # nothing typed: the backdrop closes it
    page.wait_for_selector("#duplicate-dialog", state="detached")
    open_dialog(page)
    page.fill("#dup-client-name", "Beta")
    page.mouse.click(5, 5)
    page.wait_for_timeout(150)
    assert dialog_open(page) and page.input_value("#dup-client-name") == "Beta"
    page.keyboard.press("Escape")                                              # Escape is the deliberate way out: nothing has been created
    page.wait_for_selector("#duplicate-dialog", state="detached")


def test_the_backdrop_closes_the_success_message(page):
    open_dialog(page)
    fill_valid(page)
    page.click("#dup-submit")
    page.wait_for_selector(".dup-done")
    page.mouse.click(5, 5)
    page.wait_for_selector("#duplicate-dialog", state="detached")
    assert page.evaluate("() => __done.length") == 1


def test_closing_while_the_defaults_load_cancels_the_request_and_ignores_a_late_answer(page):
    page.evaluate("() => { __api.delay = 300; }")
    open_dialog(page, wait=None)
    page.keyboard.press("Escape")
    page.wait_for_selector("#duplicate-dialog", state="detached")
    page.wait_for_timeout(400)
    assert page.locator("#duplicate-dialog, #dup-client-name").count() == 0
    assert page.evaluate("() => __api.log.map(r => r.aborted)") == [True]


def test_opening_it_twice_gives_the_same_dialog(page):
    open_dialog(page)
    page.evaluate("() => { window.__second = DuplicateProposal.open({ proposal: { proposal_id: 'SRC0000000000000001' } }); }")
    assert page.locator("#duplicate-dialog").count() == 1 and page.locator(".ph-overlay").count() == 1
    assert page.evaluate("() => __api.log.filter(r => r.method === 'GET').length") == 1


def test_the_page_behind_cannot_scroll_and_comes_back_exactly(page):
    before = page.evaluate("() => document.body.getAttribute('style')")
    open_dialog(page)
    assert page.evaluate("() => getComputedStyle(document.body).overflow") == "hidden"
    page.keyboard.press("Escape")
    page.wait_for_selector("#duplicate-dialog", state="detached")
    assert page.evaluate("() => document.body.getAttribute('style')") == before


def test_tab_stays_inside_the_dialog(page):
    open_dialog(page)
    for _ in range(40):
        page.keyboard.press("Tab")
        assert page.evaluate("() => !!document.activeElement.closest('#duplicate-dialog')")
    for _ in range(40):
        page.keyboard.press("Shift+Tab")
        assert page.evaluate("() => !!document.activeElement.closest('#duplicate-dialog')")


# --------------------------------------------------------------------------------------------- the defaults call fails

@pytest.mark.parametrize("status,words,retry", [
    (403, "You can only duplicate your own proposals.", False),
    (404, "We couldn't find that proposal. It may have been deleted.", False),
    (410, "This proposal was saved before copying was supported, so it can't be duplicated.", False),
    (409, "There's nothing to copy yet: this draft has no request details.", False),
    (500, "We couldn't load this proposal's details. Try again in a moment.", True),
    (503, "The database isn't reachable right now. Try again in a moment.", True),
    ("offline", "We couldn't reach the server. Check your connection and try again.", True),
    ("html", "We couldn't load this proposal's details. Please try again.", True),
])
def test_when_the_defaults_cannot_load_the_reason_is_shown_instead_of_a_form(page, status, words, retry):
    page.evaluate("s => { __api.defaultsStatus = s; }", status)
    open_dialog(page, wait=".dup-state--blocked")
    assert page.inner_text("#duplicate-dialog [role='alert']") == words
    assert page.locator("#dup-client-name, #dup-submit").count() == 0           # no form that could never be submitted
    assert (page.locator("#dup-retry").count() == 1) == retry
    assert "SECRET" not in page.inner_text("#duplicate-dialog")
    assert active(page) == ("dup-retry" if retry else "dup-close")
    page.click("#dup-close")
    page.wait_for_selector("#duplicate-dialog", state="detached")
    assert active(page) == "opener"


def test_try_again_after_a_failed_load_shows_the_form(page):
    page.evaluate("() => { __api.defaultsStatus = 503; }")
    open_dialog(page, wait=".dup-state--blocked")
    page.evaluate("() => { __api.defaultsStatus = 200; }")
    page.click("#dup-retry")
    page.wait_for_selector("#dup-client-name")
    assert active(page) == "dup-client-name"


def test_prepare_can_block_the_form_and_runs_again_on_try_again(page):
    """The planner's page saves the open draft first; when that fails the copy must not start from a stale version."""
    open_dialog(page, wait=".dup-state--blocked", prepare="Couldn't save your latest changes (offline). Try again.")
    assert page.inner_text("#duplicate-dialog [role='alert']") == "Couldn't save your latest changes (offline). Try again."
    assert gets(page) == []                                                    # nothing was fetched: the copy never started
    page.click("#dup-close")
    page.wait_for_selector("#duplicate-dialog", state="detached")
    assert page.evaluate("() => __prepared") == 1


def test_prepare_returning_nothing_lets_the_form_open(page):
    open_dialog(page, prepare="")
    assert page.evaluate("() => __prepared") == 1 and page.locator("#dup-client-name").count() == 1
    assert gets(page) == ["/api/proposal/SRC0000000000000001/duplicate-defaults"]


def test_without_a_proposal_id_nothing_opens(page):
    page.evaluate("() => openDup({ proposal: {} })")
    page.wait_for_timeout(100)
    assert page.locator("#duplicate-dialog").count() == 0
    assert any("cannot open" in m for m in page.errors)
    page.errors.clear()


# --------------------------------------------------------------------------------------------- hostile data

HOSTILE = '<img src=x onerror="window.__pwned=1">'


def test_names_that_look_like_markup_are_only_ever_text(page):
    page.evaluate("""([h]) => { __api.source = Object.assign({}, __api.source, { client_name: h, title: h });
      __api.defaults = Object.assign({}, __api.defaults, { agency_name: h, requested_by: h });
      __api.done = Object.assign({}, __api.done, { proposal_title: h, notes: [{ level: 'check', text: h }], open_url: '/?reopen=' + encodeURIComponent(h) }); }""", [HOSTILE])
    page.evaluate("h => openDup({ proposal: { proposal_id: 'SRC0000000000000001', client_name: h, notion_id: h } })", HOSTILE)
    page.wait_for_selector("#dup-client-name")
    assert HOSTILE in page.inner_text("#duplicate-dialog .ph-sub")              # the subtitle shows the original's name as text
    assert page.inner_text("#dup-same-agency") == "Same as the original: " + HOSTILE + ", 15%"
    assert page.input_value("#dup-requested-by") == HOSTILE
    page.fill("#dup-client-name", HOSTILE)
    page.fill("#dup-start", "2027-02-01")
    page.fill("#dup-end", "2027-04-30")
    page.click("#dup-submit")
    page.wait_for_selector(".dup-done")
    assert page.inner_text(".dup-done-name") == HOSTILE
    assert posts(page)[0]["body"]["client_name"] == HOSTILE                     # sent as typed
    assert page.locator("#duplicate-dialog img, #duplicate-dialog script, #duplicate-dialog iframe").count() == 0
    assert page.evaluate("() => window.__pwned") is None
    assert page.get_attribute("#dup-open-link", "href").startswith("/?reopen=")


# --------------------------------------------------------------------------------------------- layout and contrast

@pytest.mark.parametrize("width,height", [(1280, 900), (768, 900), (390, 760), (360, 640)])
def test_no_sideways_scroll_and_every_control_is_a_comfortable_target(browser, server, width, height):
    p = dom.new_page(browser, server, "/harness.html", width=width, height=height)
    p.evaluate("([source, defaults, done]) => { __api.source = source; __api.defaults = defaults; __api.done = done; }", [SOURCE, DEFAULTS, DONE])
    try:
        open_dialog(p)
        p.click("#duplicate-dialog details.ph-details > summary")
        for state in ("form", "errors", "done"):
            if state == "errors":
                p.click("#dup-submit")
            if state == "done":
                fill_valid(p)
                p.click("#dup-submit")
                p.wait_for_selector(".dup-done")
            wide = p.evaluate("() => { const d = document.querySelector('#duplicate-dialog'); const body = document.querySelector('#duplicate-dialog .dup'); const form = document.querySelector('#duplicate-dialog .ph-form');"
                              " return { page: document.documentElement.scrollWidth - innerWidth, dialog: d.scrollWidth - d.clientWidth, form: form ? form.scrollWidth - form.clientWidth : 0 }; }")
            assert wide == {"page": 0, "dialog": 0, "form": 0}, (state, wide)
            small = p.evaluate("() => [...document.querySelectorAll('#duplicate-dialog button, #duplicate-dialog a[href], #duplicate-dialog input:not([type=checkbox]), #duplicate-dialog summary')]"
                               ".filter(e => e.getClientRects().length).map(e => { const r = e.getBoundingClientRect(); return [e.id || e.className, Math.round(r.width), Math.round(r.height)]; }).filter(x => x[2] < 36 || x[1] < 36)")
            assert small == [], (state, small)
            dlg = p.evaluate("() => { const r = document.querySelector('#duplicate-dialog').getBoundingClientRect(); return [r.top, r.bottom, innerHeight]; }")
            assert dlg[0] >= 0 and dlg[1] <= dlg[2] + 1, (state, dlg)            # the dialog (a bottom sheet on a phone) is inside the window
    finally:
        dom.close_page(p)


def test_text_contrast_in_every_state_meets_wcag_aa(page):
    set_post(page, "return n === 1 ? { status: 500, body: null } : { status: 200, body: __api.done };")
    page.evaluate("() => { __api.defaultsStatus = 503; }")
    open_dialog(page, wait=".dup-state--blocked")
    assert page.evaluate("() => contrastReport('#duplicate-dialog')") == []
    page.evaluate("() => { __api.defaultsStatus = 200; }")
    page.click("#dup-retry")
    page.wait_for_selector("#dup-client-name")
    page.click("#duplicate-dialog details.ph-details > summary")
    assert page.evaluate("() => contrastReport('#duplicate-dialog')") == []
    page.click("#dup-submit")                                                  # every error message showing
    assert page.evaluate("() => contrastReport('#duplicate-dialog')") == []
    fill_valid(page)
    page.click("#dup-submit")                                                  # a banner from the failed request
    page.wait_for_selector("#dup-alert:not([hidden])")
    assert page.evaluate("() => contrastReport('#duplicate-dialog')") == []
    page.click("#dup-submit")
    page.wait_for_selector(".dup-done")
    assert page.evaluate("() => contrastReport('#duplicate-dialog')") == []


def test_reduced_motion_stops_the_spinner(browser, server):
    p = dom.new_page(browser, server, "/harness.html", reduced_motion="reduce")
    p.evaluate("([source, defaults, done]) => { __api.source = source; __api.defaults = defaults; __api.done = done; __api.hold = true; }", [SOURCE, DEFAULTS, DONE])
    try:
        open_dialog(p)
        fill_valid(p)
        p.click("#dup-submit")
        p.wait_for_function("() => __api.pending.length === 1")
        assert p.evaluate("() => getComputedStyle(document.querySelector('#dup-submit .ph-btn-wait')).animationName") == "none"
    finally:
        dom.close_page(p)
