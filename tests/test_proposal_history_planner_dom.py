"""The planner's side of "My Proposal History" and "Duplicate for a new client" on the REAL page (templates/index.html + static/app.js, headless Chromium
through Playwright), with every /api/** call answered by the REAL route functions over an in-memory table (tests/duplicate_flow_support.py).

  the button     opens ProposalHistory's dialog (the old modal and its ids are gone), Escape closes it and the keyboard goes back to the button
  Open           the same tab when nothing is in progress here (nothing parsed, pasted or typed), a new tab otherwise
  Delete         the inline confirmation, then DELETE /api/proposal/<id>; never a native confirm()
  Duplicate      hands the row to DuplicateProposal (proposal, trigger, prepare, onDone); the open draft's unsaved edits are saved first, and a copy
                 that cannot be started from them says why
  the notice     "Copied from ..." with what to check on Step 02: drawn from a copy's reopen payload, escaped, dismissable, gone on a new proposal
  Notion ID      Step 02's new field normalises to EVC-##### and keeps the pill and Step 01's box in step
Every test also asserts the page threw nothing."""
from __future__ import annotations

import copy
import re

import pytest

from tests import dom_support as dom
from tests import duplicate_flow_support as S
from tests.fixtures import duplicate_sources as fx

playwright_sync = pytest.importorskip("playwright.sync_api")

SRC, GEN = "SRCdraft000000000001", "GENproposal00000001"
HOSTILE = '<img src=x onerror="window.__pwned=1">'


def make_backend(monkeypatch, *, source_step: int = 2) -> S.Backend:
    state = fx.jsonable(fx.source_monthly())
    state["wizard_step"] = source_step
    weekly = fx.jsonable(fx.source_weekly())
    backend = S.Backend(
        S.table_row(SRC, state, client="Acme Motors", title="EVC-48213 | Acme Motors - Fall Push | Entravision | Sep26 | Digital Media Proposal", minutes_ago=30),
        S.table_row(GEN, weekly, client="Beta Co", title="EVC-47000 | Beta Co | Entravision | Jan27 | Digital Media Proposal", status="generated", notion="EVC-47000",
                    minutes_ago=300, summary={"total_net": 120000.0, "tabs_built": ["net"]}),
    )
    return backend.install(monkeypatch)


@pytest.fixture(scope="module")
def server():
    with dom.serve({"/": S.INDEX}) as base:
        yield base


@pytest.fixture(scope="module")
def browser():
    with dom.launch(playwright_sync) as b:
        yield b


@pytest.fixture
def env(browser, server, monkeypatch):
    """(backend, open) where open(path) is the real page; every page opened is checked for errors at the end."""
    backend = make_backend(monkeypatch)
    pages = []

    def open_page(path="/", **kw):
        page = S.open_app(browser, server, backend, path, **kw)
        pages.append(page)
        return page

    yield backend, open_page
    for page in pages:
        S.close_app(page)


def open_history(page):
    page.evaluate("() => document.getElementById('my-proposals-btn').focus({ preventScroll: true })")
    page.click("#my-proposals-btn")
    page.wait_for_selector(".ph-dialog .ph-row")


def active(page):
    return page.evaluate("() => document.activeElement ? (document.activeElement.id || document.activeElement.tagName.toLowerCase() + '.' + document.activeElement.className) : ''")


# --------------------------------------------------------------------------------------------- the files

def test_the_page_loads_the_shared_component_before_app_js_and_has_no_trace_of_the_old_modal():
    html = S.INDEX
    for needle in ("my-proposals-modal", "my-proposals-body", "my-proposals-search", "my-proposals-prev-btn", "my-proposals-next-btn", "my-proposals-close-btn",
                   "my-proposals-count", "my-proposals-page-info", "my-proposals-table"):
        assert needle not in html, needle
    assert 'id="my-proposals-btn"' in html
    order = [html.index(f'"/static/{name}"') for name in ("proposal-history.css", "duplicate-proposal.css")]
    assert order == sorted(order)
    scripts = [html.index(f'<script src="/static/{name}"') for name in ("session-guard.js", "proposal-history.js", "duplicate-proposal.js", "app.js")]
    assert scripts == sorted(scripts)                                          # the component and the dialog before the code that uses them
    app = (dom.APP / "static" / "app.js").read_text(encoding="utf-8")
    for dead in ("state.myProposals", "loadMyProposals", "renderMyProposalsTable", "onDeleteDraft", "closeMyProposalsModal"):
        assert dead not in app, dead
    assert "function formatDate(" in app                                       # still used by the Notion search table


# --------------------------------------------------------------------------------------------- the button and the list

def test_the_button_opens_the_shared_dialog_escape_closes_it_and_the_keyboard_goes_back(env):
    backend, open_page = env
    page = open_page()
    open_history(page)
    assert page.locator("#my-proposals-modal").count() == 0
    dialog = page.locator(".ph-dialog")
    assert dialog.get_attribute("role") == "dialog" and dialog.get_attribute("aria-modal") == "true"
    assert page.inner_text(".ph-title") == "My Proposal History"
    assert page.inner_text(".ph-sub") == "Open or continue a proposal, or copy one for a new client."
    assert page.eval_on_selector_all(".ph-row .ph-client-name", "els => els.map(e => e.textContent)") == ["Acme Motors", "Beta Co"]
    assert ("GET", "/api/my-proposals") in backend.calls and page.evaluate("() => performance.getEntriesByType('resource').filter(r => r.name.includes('/api/my-proposals')).map(r => new URL(r.name).searchParams.get('page_size'))") == ["25"]
    assert active(page) == "input.ph-search-input"
    page.keyboard.press("Escape")
    page.wait_for_selector(".ph-dialog", state="detached")
    assert active(page) == "my-proposals-btn"


def test_the_empty_state_button_puts_the_keyboard_in_the_notion_id_box(env):
    backend, open_page = env
    backend.rows.clear()
    page = open_page()
    page.click("#my-proposals-btn")
    page.wait_for_selector(".ph[data-state='empty']")
    page.click(".ph-empty-action")
    page.wait_for_selector(".ph-dialog", state="detached")
    assert active(page) == "notion-id-input"


# --------------------------------------------------------------------------------------------- Open: this tab or a new one

def test_open_stays_in_this_tab_when_nothing_is_in_progress_and_opens_a_new_tab_otherwise(env):
    backend, open_page = env
    page = open_page()
    open_history(page)
    link = page.locator(f".ph-row[data-id='{GEN}'] .ph-open")
    assert link.get_attribute("href") == f"/?reopen={GEN}" and link.get_attribute("target") is None
    page.keyboard.press("Escape")

    page.evaluate("() => { state.parsed = { client_name: 'In progress' }; }")                       # a parsed request
    open_history(page)
    blank = page.locator(f".ph-row[data-id='{GEN}'] .ph-open")
    assert blank.get_attribute("target") == "_blank" and blank.get_attribute("rel") == "noopener"
    page.keyboard.press("Escape")
    page.evaluate("() => { state.parsed = null; document.getElementById('notion-input').value = 'Client name: Pasted but not parsed'; }")
    open_history(page)
    assert page.locator(f".ph-row[data-id='{GEN}'] .ph-open").get_attribute("target") == "_blank"   # pasted text would be lost by leaving
    page.keyboard.press("Escape")
    page.evaluate("() => { document.getElementById('notion-input').value = ''; document.getElementById('notion-id-input').value = '48213'; }")
    open_history(page)
    assert page.locator(f".ph-row[data-id='{GEN}'] .ph-open").get_attribute("target") == "_blank"   # so would a typed Notion ID
    page.keyboard.press("Escape")
    page.evaluate("() => { document.getElementById('notion-id-input').value = ''; }")
    open_history(page)
    assert page.locator(f".ph-row[data-id='{GEN}'] .ph-open").get_attribute("target") is None       # blank again: this tab


def test_continue_in_this_tab_reopens_the_draft_on_its_step(env):
    backend, open_page = env
    page = open_page()
    open_history(page)
    with page.expect_navigation():
        page.click(f".ph-row[data-id='{SRC}'] .ph-open")
    page.wait_for_selector(".step.active[data-step='2']")
    assert page.evaluate("() => state.proposalId") == SRC and page.input_value("[data-field='client_name']") == "Acme Motors"
    assert len(page.context.pages) == 1                                       # no tab pile


# --------------------------------------------------------------------------------------------- Delete

def test_deleting_a_draft_asks_in_the_row_then_sends_the_delete(env):
    backend, open_page = env
    page = open_page()
    open_history(page)
    page.click(f".ph-row[data-id='{SRC}'] .ph-kebab")
    page.click(".ph-menu-item[data-action='delete']")
    assert page.locator(".ph-confirm").count() == 1
    assert ("DELETE", f"/api/proposal/{SRC}") not in backend.calls            # asking sends nothing
    page.click(".ph-confirm-delete")
    page.wait_for_selector(f".ph-row[data-id='{SRC}']", state="detached")
    assert ("DELETE", f"/api/proposal/{SRC}") in backend.calls and SRC not in backend.rows and GEN in backend.rows
    # a generated proposal has no delete item at all
    page.click(f".ph-row[data-id='{GEN}'] .ph-kebab")
    assert page.locator(".ph-menu-item[data-action='delete']").count() == 0


# --------------------------------------------------------------------------------------------- Duplicate: the hand-off

def test_duplicate_hands_the_row_to_the_copy_form_with_what_it_needs(env):
    backend, open_page = env
    page = open_page()
    page.evaluate("""() => { window.__opened = []; window.DuplicateProposal = { open: (o) => { window.__opened.push({ id: o.proposal.proposal_id, client: o.proposal.client_name,
        trigger: o.trigger && o.trigger.getAttribute('data-action'), prepare: typeof o.prepare, onDone: typeof o.onDone }); window.__onDone = o.onDone; return { close() {} }; } }; }""")
    open_history(page)
    page.click(f".ph-row[data-id='{GEN}'] .ph-dup")
    assert page.evaluate("() => __opened") == [{"id": GEN, "client": "Beta Co", "trigger": "duplicate", "prepare": "function", "onDone": "function"}]
    before = len([c for c in backend.calls if c == ("GET", "/api/my-proposals")])
    page.evaluate("id => __onDone({ proposal_id: id })", "NEWCOPY0000000000001")               # what the form does when the copy exists: the list reloads
    page.wait_for_function("n => performance.getEntriesByType('resource').filter(r => r.name.includes('/api/my-proposals')).length > n", arg=before)
    assert len([c for c in backend.calls if c == ("GET", "/api/my-proposals")]) == before + 1


def test_a_real_copy_shows_up_at_the_top_of_the_history_marked_as_new(env):
    backend, open_page = env
    page = open_page()
    open_history(page)
    page.click(f".ph-row[data-id='{GEN}'] .ph-dup")
    page.wait_for_selector("#dup-client-name")
    page.fill("#dup-client-name", "Gamma Foods")
    page.fill("#dup-start", "2027-03-01")
    page.fill("#dup-end", "2027-05-31")
    page.click("#dup-submit")
    page.wait_for_selector(".dup-done")
    (copy_row,) = backend.copies(GEN)
    copy_id = copy_row["proposal_id"]
    page.wait_for_selector(f".ph-row[data-id='{copy_id}']")
    ids = page.eval_on_selector_all(".ph-row", "els => els.map(e => e.dataset.id)")
    assert ids[0] == copy_id and set(ids) == {copy_id, SRC, GEN}                # newest first
    assert page.inner_text(f".ph-row[data-id='{copy_id}'] .ph-lineage .ph-chip").strip().endswith("Copied from Beta Co")
    assert page.inner_text(f".ph-row[data-id='{copy_id}'] .ph-client-name") == "Gamma Foods"
    assert page.get_attribute("#dup-open-link", "href") == f"/?reopen={copy_id}" and active(page) == "dup-open-link"
    page.keyboard.press("Escape")                                              # closes the copy form; the history is still there, on the new row
    page.wait_for_selector("#duplicate-dialog", state="detached")
    assert page.locator(".ph-dialog").count() == 1
    assert page.evaluate("id => !!document.activeElement.closest(`.ph-row[data-id='${id}']`)", copy_id)
    assert ("POST", "/api/proposal/draft") not in backend.calls                # the source was not the open draft: nothing was saved first


def test_duplicating_the_draft_that_is_open_here_saves_its_unsaved_edits_first(env):
    backend, open_page = env
    page = open_page(f"/?reopen={SRC}")
    page.wait_for_selector(".step.active[data-step='2']")
    page.fill("[data-field='salesperson_comments']", "Edited after the last save")
    page.click(".step[data-step='1']")                                         # the 01 pill is reachable from any step: History opens mid-wizard
    open_history(page)
    assert page.locator(f".ph-row[data-id='{SRC}'] .ph-open").get_attribute("target") == "_blank"   # a wizard with work in it never loses it to Open
    page.click(f".ph-row[data-id='{SRC}'] .ph-dup")
    page.wait_for_selector("#dup-client-name")
    saves = backend.sent("POST /api/proposal/draft")
    assert len(saves) == 1 and saves[0]["proposal_id"] == SRC and saves[0]["request"]["salesperson_comments"] == "Edited after the last save"
    posts = [i for i, c in enumerate(backend.calls) if c == ("POST", "/api/proposal/draft")]
    defaults = [i for i, c in enumerate(backend.calls) if c[1].endswith("/duplicate-defaults")]
    assert posts and defaults and posts[0] < defaults[0]                       # saved BEFORE the copy started
    assert backend.rows[SRC]["reopen_state"]["request"]["salesperson_comments"] == "Edited after the last save"


def test_when_the_open_draft_cannot_be_saved_the_copy_does_not_start_from_a_stale_version(env):
    backend, open_page = env
    backend.fail["POST /api/proposal/draft"] = (500, "boom")
    page = open_page(f"/?reopen={SRC}")
    page.wait_for_selector(".step.active[data-step='2']")
    page.fill("[data-field='salesperson_comments']", "Edited, but the save will fail")
    page.click(".step[data-step='1']")
    open_history(page)
    page.click(f".ph-row[data-id='{SRC}'] .ph-dup")
    page.wait_for_selector(".dup-state--blocked")
    text = page.inner_text("#duplicate-dialog [role='alert']")
    assert text.startswith("Couldn't save your latest changes (") and text.endswith("). Try again.")
    assert not any(c[1].endswith("/duplicate-defaults") for c in backend.calls)  # nothing was fetched: no form, no copy of a stale version
    backend.fail.clear()
    page.click("#dup-retry")                                                   # saved now: the form opens
    page.wait_for_selector("#dup-client-name")
    assert backend.rows[SRC]["reopen_state"]["request"]["salesperson_comments"] == "Edited, but the save will fail"


# --------------------------------------------------------------------------------------------- the notice on Step 02

def make_copy(backend: S.Backend, source_id: str = SRC, **over) -> str:
    """A real copy, made by the real route: returns its id."""
    body = {"request_id": "planner-test-request-0001", "client_name": "Beta Burgers", "start_date": "2027-02-01", "end_date": "2027-04-30", "notion_id": "50001",
            "campaign_name": "Spring Push", **over}
    status, answer = backend.answer("POST", f"/api/proposal/{source_id}/duplicate", body)
    assert status == 200, answer
    return answer["proposal_id"]


def test_a_copy_opens_on_step_02_with_a_notice_of_what_to_check(env):
    backend, open_page = env
    copy_id = make_copy(backend)
    notes = backend.rows[copy_id]["reopen_state"]["duplicate_notes"]
    checks = [n["text"] for n in notes if n["level"] == "check"]
    info = [n["text"] for n in notes if n["level"] != "check"]
    assert checks and info
    page = open_page(f"/?reopen={copy_id}")
    page.wait_for_selector(".step.active[data-step='2']")
    notice = page.locator("#duplicate-notice[data-duplicate-notice]")
    assert notice.is_visible()
    assert page.inner_text("#duplicate-notice-title") == "Copied from “EVC-48213 | Acme Motors - Fall Push | Entravision | Sep26 | Digital Media Proposal”"
    assert page.eval_on_selector_all("#duplicate-notice-checks li", "els => els.map(e => e.textContent)") == checks
    assert page.inner_text("#duplicate-notice-more-summary") == f"What else changed ({len(info)})"
    assert page.locator("#duplicate-notice-more").get_attribute("open") is None and not page.locator("#duplicate-notice-info").is_visible()   # folded away
    page.click("#duplicate-notice-more-summary")
    assert page.eval_on_selector_all("#duplicate-notice-info li", "els => els.map(e => e.textContent)") == info
    assert page.inner_text("#duplicate-notice-lede").endswith("a look before you go on:")
    # the Step 02 form holds the NEW opportunity
    assert page.input_value("[data-field='client_name']") == "Beta Burgers" and page.input_value("[data-field='start_date']") == "2027-02-01"
    page.click("#duplicate-notice-dismiss")
    assert not notice.is_visible() and page.locator("#duplicate-notice-checks li").count() == 0
    assert active(page) == "h1.display"                                        # the button went away: the keyboard is at the top of the step


def test_the_notice_is_drawn_as_text_and_gone_on_a_new_proposal(env):
    backend, open_page = env
    copy_id = make_copy(backend)
    state = backend.rows[copy_id]["reopen_state"]
    state["duplicated_from"]["title"] = HOSTILE
    state["duplicate_notes"] = [{"code": "x", "level": "check", "text": HOSTILE}, {"code": "y", "level": "info", "text": "<b>bold</b>"}]
    page = open_page(f"/?reopen={copy_id}")
    page.wait_for_selector("#duplicate-notice:not(.hidden)")
    assert page.inner_text("#duplicate-notice-title") == f"Copied from “{HOSTILE}”"
    assert page.inner_text("#duplicate-notice-checks li") == HOSTILE
    assert page.locator("#duplicate-notice img, #duplicate-notice b, #duplicate-notice script").count() == 0 and page.evaluate("() => window.__pwned") is None
    page.evaluate("() => resetAll()")                                          # "New proposal": nothing of the last one stays
    assert not page.locator("#duplicate-notice").is_visible() and page.locator("#duplicate-notice-checks li, #duplicate-notice-info li").count() == 0


def test_no_notice_for_an_ordinary_proposal_or_for_a_copy_that_was_saved_since(env):
    backend, open_page = env
    page = open_page(f"/?reopen={SRC}")                                       # not a copy
    page.wait_for_selector(".step.active[data-step='2']")
    assert not page.locator("#duplicate-notice").is_visible()
    copy_id = make_copy(backend)
    page.goto(page.url.split("?")[0] + f"?reopen={copy_id}")
    page.wait_for_selector("#duplicate-notice:not(.hidden)")
    page.fill("[data-field='salesperson_comments']", "first edit")
    page.evaluate("() => _autosaveTick()")                                    # the first save drops the notes (and keeps the lineage)
    page.wait_for_function("() => document.getElementById('save-draft-status').textContent.includes('Auto-saved')")
    stored = backend.rows[copy_id]["reopen_state"]
    assert "duplicate_notes" not in stored and stored["duplicated_from"]["proposal_id"] == SRC
    page.reload()
    page.wait_for_selector(".step.active[data-step='2']")
    assert not page.locator("#duplicate-notice").is_visible()                  # shown once, until the planner has edited


# --------------------------------------------------------------------------------------------- Step 02's Notion ID

def test_the_notion_id_field_on_step_02_is_filled_and_tidied_and_keeps_the_other_places_in_step(env):
    backend, open_page = env
    page = open_page(f"/?reopen={SRC}")
    page.wait_for_selector(".step.active[data-step='2']")
    field = "[data-field='notion_id']"
    assert page.input_value(field) == "EVC-48213" and page.inner_text("#notion-id-pill") == "EVC-48213" and page.input_value("#notion-id-input") == "48213"
    page.fill(field, "evc 50001")
    page.focus("[data-field='client_name']")                                   # leaving the field
    assert page.input_value(field) == "EVC-50001"
    assert page.inner_text("#notion-id-pill") == "EVC-50001" and page.input_value("#notion-id-input") == "50001"
    assert page.evaluate("() => state.parsed.notion_id") == "EVC-50001"
    assert not page.locator("#step02-notion-id-note").is_visible()
    # text that is not an ID: nothing changes and it says so
    page.fill(field, "abc")
    page.focus("[data-field='client_name']")
    assert page.inner_text("#step02-notion-id-note") == "That isn't a Notion ID (4 or 5 digits, like EVC-48213), so it was not used."
    assert page.get_attribute(field, "aria-invalid") == "true"
    assert page.evaluate("() => state.parsed.notion_id") == "EVC-50001" and page.inner_text("#notion-id-pill") == "EVC-50001"
    page.type(field, "1")                                                      # typing again clears the complaint
    assert not page.locator("#step02-notion-id-note").is_visible() and page.get_attribute(field, "aria-invalid") is None
    # a value that is still in the box when the step is left (no blur yet) is read too
    page.evaluate("() => { document.querySelector(\"[data-field='notion_id']\").value = '4821'; syncFormToParsed(); }")
    assert page.evaluate("() => state.parsed.notion_id") == "EVC-4821" and page.input_value("#notion-id-input") == "4821"
    page.evaluate("() => { document.querySelector(\"[data-field='notion_id']\").value = ''; syncFormToParsed(); }")
    assert page.evaluate("() => state.parsed.notion_id") == "" and page.inner_text("#notion-id-pill") == ""


def test_a_new_proposal_clears_the_notion_field_and_its_complaint(env):
    backend, open_page = env
    page = open_page(f"/?reopen={SRC}")
    page.wait_for_selector(".step.active[data-step='2']")
    page.fill("[data-field='notion_id']", "nope")
    page.focus("[data-field='client_name']")
    assert page.locator("#step02-notion-id-note").is_visible()
    page.evaluate("() => resetAll()")
    assert page.input_value("[data-field='notion_id']") == "" and not page.locator("#step02-notion-id-note").is_visible()
    assert page.get_attribute("[data-field='notion_id']", "aria-invalid") is None


def test_the_copy_the_server_made_has_its_notion_id_in_every_place(env):
    backend, open_page = env
    copy_id = make_copy(backend)
    page = open_page(f"/?reopen={copy_id}")
    page.wait_for_selector(".step.active[data-step='2']")
    assert page.input_value("[data-field='notion_id']") == "EVC-50001" and page.inner_text("#notion-id-pill") == "EVC-50001"
    assert "EVC-50001" in page.inner_text("#proposal-name-text") and re.search(r"Beta Burgers", page.inner_text("#proposal-name-text"))
