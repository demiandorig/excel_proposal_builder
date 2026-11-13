"""The whole "Duplicate for a new client" flow on the REAL page: templates/index.html + static/app.js in headless Chromium (Playwright), every /api/** call answered
by the REAL route functions (`my_proposals`, `reopen_proposal`, `save_draft`, `duplicate_defaults`, `duplicate_proposal`, `get_catalog`, ...) over an in-memory
proposals table (tests/duplicate_flow_support.py). Nothing of the feature is stubbed: the planner opens an old proposal (Acme Motors, a generated one that was
sold to an agency), goes to the history, duplicates it for a NEW client, opens the copy, and the test looks at what the copy holds.

  1  reopen the Acme proposal, click the "01" pill, open the history, Duplicate, fill the form (Beta Burgers, Feb 1 to Apr 30 2027, Notion 50001), submit, open the copy
  2  in the new tab: Step 02 is active and the furthest step; the name bar; the notice lists what to check; the form holds the new client, dates, months; the agency is blank
  3  THE OLD CLIENT'S NAME IS NOWHERE in the working area (any input, textarea or select, the text of Steps 02 to 08, the name bar). The only places it may be are the
     "Copied from" notice (which names the original on purpose) and the history list's "Copied from" chip
  4  strategy, roadblocks and email panels are empty and no AI route was called
  5  the numbers: Step 04 totals, the carried custom split on Step 06, no Constanza estimate on the cleared line while the hand-typed numbers stay
  6  edit a field, let the autosave run: it saves THE COPY (not the original), with the months and splits the server stored
  7  duplicate again with a 4-month flight: the notice says the totals changed
Skipped when Playwright or its Chromium is absent."""
from __future__ import annotations

import copy
import json

import pytest

from tests import dom_support as dom
from tests import duplicate_flow_support as S
from tests.fixtures import duplicate_sources as fx

playwright_sync = pytest.importorskip("playwright.sync_api")

SRC = "ACMEgenerated0000001"
OLD_TITLE = "EVC-48213 | Acme Motors - Fall Push | Entravision | Sep26 | Digital Media Proposal"
LEAK_WORDS = fx.LEAK_WORDS                                          # ("acme", "initiative", "acmemotors")
ALLOWED_PATHS = {"/api/catalog", "/api/me", "/api/my-proposals", "/api/proposal/draft", "/api/suggest-tabs", "/api/disclaimers/preview"}      # plain reads and saves; no AI route


@pytest.fixture(scope="module")
def server():
    with dom.serve({"/": S.INDEX}) as base:
        yield base


@pytest.fixture(scope="module")
def browser():
    with dom.launch(playwright_sync) as b:
        yield b


def leaks(page) -> dict:
    """Where the old opportunity's words appear in the working area of `page`: input / textarea / select values, the text of Steps 02 to 08 (hidden steps
    included: textContent, not innerText), and the name bar. The "Copied from" notice is the one place that names the original, on purpose."""
    return page.evaluate("""(words) => {
      const hits = [];
      const has = (text) => words.filter((w) => String(text).toLowerCase().includes(w));
      document.querySelectorAll('input, textarea, select').forEach((el) => {
        if (el.closest('.ph-overlay')) return;                                       // a dialog is not the working area
        const found = has(el.value); if (found.length) hits.push({ where: 'value of #' + (el.id || el.dataset.field || el.name || el.className), words: found });
      });
      for (let n = 2; n <= 8; n++) {
        const section = document.getElementById('step-' + n); if (!section) continue;
        const clone = section.cloneNode(true);
        clone.querySelectorAll('[data-duplicate-notice]').forEach((e) => e.remove());
        const found = has(clone.textContent); if (found.length) hits.push({ where: 'text of #step-' + n, words: found, sample: clone.textContent.replace(/\\s+/g, ' ').slice(0, 80) });
      }
      const bar = document.getElementById('proposal-name-text'); if (bar && has(bar.textContent).length) hits.push({ where: 'name bar', words: has(bar.textContent) });
      return hits;
    }""", list(LEAK_WORDS))


def held_numbers(page) -> dict:
    """Every line the page holds: {option: {line id: [months, per-period budget, allocations]}}."""
    return page.evaluate("""() => { const out = {};
      const all = [{ label: state.activeTierLabel, lines: state.lineItems }].concat(state.tiers.map((t) => ({ label: t.label, lines: t.lineItems })));
      all.forEach((t) => { out[t.label] = {}; t.lines.forEach((li) => { out[t.label][li.id] = [li.months, li.monthly_budget, li.monthly_allocations || null]; }); });
      return out; }""")


def stored_numbers(row: dict) -> dict:
    out = {}
    for tier in row["reopen_state"]["tiers"]:
        out[tier["label"]] = {li["id"]: [li["months"], li["monthly_budget"], li.get("monthly_allocations") or None] for li in tier["line_items"]}
    return out


def fill_and_submit(page, client, start, end, notion):
    page.wait_for_selector("#dup-client-name")
    page.fill("#dup-client-name", client)
    page.fill("#dup-start", start)
    page.fill("#dup-end", end)
    page.fill("#dup-notion", notion)
    page.click("#dup-submit")
    page.wait_for_selector(".dup-done")


def open_copy(page):
    """Clicks "Open the copy" and returns the new tab once it has landed on Step 02."""
    with page.context.expect_page() as opened:
        page.click("#dup-open-link")
    tab = opened.value
    tab.wait_for_selector(".step.active[data-step='2']")
    return tab


def test_the_whole_flow_from_the_history_to_a_copy_that_holds_nothing_of_the_old_client(browser, server, monkeypatch):
    source = fx.jsonable(fx.source_monthly())
    source["wizard_step"] = 8
    backend = S.Backend(S.table_row(SRC, source, client="Acme Motors", title=OLD_TITLE, status="generated", notion="EVC-48213", minutes_ago=90,
                                    summary={"total_net": 69000.0, "tabs_built": ["net"]})).install(monkeypatch)
    original = copy.deepcopy(backend.rows[SRC])
    page = S.open_app(browser, server, backend, f"/?reopen={SRC}")
    try:
        # ---------------------------------------------------------------- 1. the planner's path
        page.wait_for_selector(".step.active[data-step='4']")                    # a generated proposal reopens on Curate
        assert leaks(page) != []                                                  # (the working area of the OLD proposal does name the old client: the scan can see it)
        page.click(".step[data-step='1']")                                       # the history button is on Step 01
        page.click("#my-proposals-btn")
        page.wait_for_selector(f".ph-row[data-id='{SRC}']")
        assert page.locator(f".ph-row[data-id='{SRC}'] .ph-open").get_attribute("target") == "_blank"       # work is open here: Open uses a new tab
        page.click(f".ph-row[data-id='{SRC}'] .ph-dup")
        fill_and_submit(page, "Beta Burgers", "2027-02-01", "2027-04-30", "50001")
        (copy_row,) = backend.copies(SRC)
        copy_id = copy_row["proposal_id"]
        assert copy_row["created_by_email"] == S.PLANNER and copy_row["status"] == "draft" and copy_row["notion_id"] == "EVC-50001"
        checks = sum(1 for n in copy_row["reopen_state"]["duplicate_notes"] if n["level"] == "check")
        assert checks >= 2 and page.inner_text(".dup-done-text") == f"{checks} things to check in Step 02."
        assert page.get_attribute("#dup-open-link", "href") == f"/?reopen={copy_id}"
        before_save = copy.deepcopy(stored_numbers(copy_row))
        tab = open_copy(page)

        # ---------------------------------------------------------------- 2. the copy opens on Step 02
        assert tab.evaluate("() => [state.step, state.furthestStep, state.proposalId, state.generatedProposalId]") == [2, 2, copy_id, None]
        assert tab.url.endswith(f"/?reopen={copy_id}")
        assert tab.inner_text("#proposal-name-text") == copy_row["proposal_title"]
        assert tab.inner_text("#proposal-name-text").startswith("EVC-50001 | Beta Burgers") and "Feb27" in tab.inner_text("#proposal-name-text")
        notes = copy_row["reopen_state"]["duplicate_notes"]
        assert tab.locator("#duplicate-notice").is_visible()
        assert tab.eval_on_selector_all("#duplicate-notice-checks li", "els => els.map(e => e.textContent)") == [n["text"] for n in notes if n["level"] == "check"]
        form = tab.evaluate("() => Object.fromEntries([...document.querySelectorAll('#step-2 [data-field]')].map((e) => [e.dataset.field, e.value]))")
        assert (form["client_name"], form["start_date"], form["end_date"], form["total_months"], form["notion_id"]) == ("Beta Burgers", "2027-02-01", "2027-04-30", "3", "EVC-50001")
        assert form["agency_name"] == "" and form["agency_fee"] == "" and form["client_website"] == ""      # the original's agency, fee and website did not come along
        assert (form["requested_by"], form["salesperson_email"]) == ("Ana Torres", "ana@entravision.com")   # the same AE usually re-sells: carried, and editable
        assert tab.evaluate("() => state.parsed.agency_fee") is None and tab.inner_text("#notion-id-pill") == "EVC-50001"
        assert tab.evaluate("() => state.manualCampaignNameOverride") is None          # the old "Acme - Fall Push" campaign name did not carry: the name bar follows the new client

        # ---------------------------------------------------------------- 4. nothing the AI wrote for the old client
        assert tab.evaluate("() => [state.strategyBrief, state.roadblocks, state.enrichment, state.rawNotionText]") == [None, None, None, None]
        assert tab.input_value("#notion-input") == ""                             # Step 01's paste box: the old request is gone
        assert all(not tab.locator(sel).is_visible() for sel in ("#strategy-brief", "#roadblocks-content", "#result"))

        # ---------------------------------------------------------------- 6. an edit and the autosave: the COPY is saved, with what the server stored
        tab.fill("[data-field='salesperson_comments']", "Wants the Spanish-language radio add-on")
        tab.evaluate("() => _autosaveTick()")
        tab.wait_for_function("() => document.getElementById('save-draft-status').textContent.includes('Auto-saved')")
        (saved,) = backend.sent("POST /api/proposal/draft")
        assert saved["proposal_id"] == copy_id                                    # not a second draft, and never the original
        assert saved["request"]["salesperson_comments"] == "Wants the Spanish-language radio add-on" and saved["request"]["client_name"] == "Beta Burgers"
        sent_numbers = {t["label"]: {li["id"]: [li["months"], li["monthly_budget"], li.get("monthly_allocations") or None] for li in t["line_items"]} for t in saved["tiers"]}
        assert sent_numbers == before_save                                        # months, budgets and the carried split: exactly what the server stored
        assert backend.rows[SRC]["reopen_state"] == original["reopen_state"] and backend.rows[SRC]["status"] == "generated"   # the original was never touched
        assert "duplicate_notes" not in backend.rows[copy_id]["reopen_state"] and backend.rows[copy_id]["reopen_state"]["duplicated_from"]["proposal_id"] == SRC

        # ---------------------------------------------------------------- 5. the numbers
        tab.evaluate("() => { state.furthestStep = 8; }")                         # (the copy is a draft that has only reached Step 02: let the pills reach the rest)
        tab.click(".step[data-step='4']")
        tab.wait_for_selector("#step-4:not(.hidden)")
        assert tab.inner_text("#monthly-total") == "$23,000" and tab.inner_text("#flight-total") == "$69,000"       # per-period budgets kept, 3 months of them
        assert not tab.locator("#totals-gross-wrap").is_visible()                 # a direct client: no agency fee, so no Gross figures
        held = held_numbers(tab)
        assert held["A"]["li_fb"] == [3, 10000, {"2027-02": 5000, "2027-03": 15000, "2027-04": 10000}]      # the hand-made split moved to the new months, same dollars
        assert held["A"]["li_ctv"][0] == 3 and held["A"]["li_disp"][0] == 3
        assert held["B"]["li_b1"][0] == 3 and held["B"]["li_b1"][2] is None       # option B lost its own 4-month window with its 4-key split
        tab.click(".step[data-step='5']")
        tab.wait_for_selector("#step-5:not(.hidden)")
        avails = tab.evaluate("() => ({ fb: state.availsData['li_fb'] || null, ctv: state.availsData['li_ctv'] || null })")
        assert avails["fb"] is None                                                # the Constanza estimate (for the old client's geography) was cleared ...
        assert avails["ctv"]["max_imps"] == 400000 and avails["ctv"]["max_spend"] == 8000       # ... the numbers a planner typed by hand stay
        tab.click(".step[data-step='6']")
        tab.wait_for_selector("#step-6:not(.hidden)")
        assert "15,000" in tab.inner_text("#mb-content")                          # the carried split is what Step 06 shows for that line
        assert held_numbers(tab)["A"]["li_fb"][2] == {"2027-02": 5000, "2027-03": 15000, "2027-04": 10000}
        tab.click(".step[data-step='8']")
        tab.wait_for_selector("#step-8:not(.hidden)")

        # ---------------------------------------------------------------- 3. the old client's name is nowhere in the working area
        assert leaks(tab) == []
        assert backend.unplanned == []
        assert {path for _, path in backend.calls if not path.startswith(("/api/proposal/", "/api/my-proposals"))} <= ALLOWED_PATHS    # 4: no AI route was called
        assert not [c for c in backend.calls if c[1] in ("/api/strategy", "/api/roadblocks", "/api/generate") or "/api/gamma" in c[1] or "/api/ad-presence" in c[1]]

        # ---------------------------------------------------------------- 7. a different length: the notice says the totals changed
        page.keyboard.press("Escape")                                              # close the first copy's success message; the history is behind it
        page.wait_for_selector("#duplicate-dialog", state="detached")
        assert page.locator(f".ph-row[data-id='{copy_id}'] .ph-lineage .ph-chip").inner_text().strip().endswith("Copied from Acme Motors")   # the history names the source
        page.click(f".ph-row[data-id='{SRC}'] .ph-dup")
        fill_and_submit(page, "Beta Burgers", "2027-02-01", "2027-05-31", "50002")
        longer = next(r for r in backend.copies(SRC) if r["proposal_id"] != copy_id)
        tab2 = open_copy(page)
        shown = tab2.eval_on_selector_all("#duplicate-notice-checks li", "els => els.map(e => e.textContent)")
        assert any("so the totals changed" in text and "$92,000" in text for text in shown), shown                 # 69,000 -> 92,000: 4 months of the same per-month budgets
        assert tab2.input_value("[data-field='total_months']") == "4"
        assert tab2.evaluate("() => state.proposalId") == longer["proposal_id"] and longer["proposal_id"] != copy_id
        assert held_numbers(tab2)["A"]["li_fb"][2] is None                         # a hand-made split over 3 months cannot be carried to 4: it follows the default again
        assert leaks(tab2) == []
        assert backend.rows[SRC]["reopen_state"] == original["reopen_state"]
    finally:
        S.close_app(page)
