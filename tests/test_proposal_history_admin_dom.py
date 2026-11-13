"""The Admin console's Proposal History tab (admin.html + admin.js, with the shared list in proposal-history.js) in a REAL browser (headless Chromium
through Playwright): the real admin page and admin.js, with /api/** answered by a small fake of the server in this file. The component itself is
covered by test_proposal_history_dom.py; what is pinned here is the wiring that only the real page has:

  * the first tab is the shared list (the old toolbar, table and pager are gone), landing on "My proposals", 50 rows at a time;
  * the who cell (planner, AE, device), the My / All switch (All asks the server for mine=false) and the search box;
  * Duplicate hands the row to window.DuplicateProposal.open(...) and, when the copy exists, the list reloads with the new row highlighted;
  * Delete is offered for the signed-in admin's OWN drafts only (the email compared without regard to case), and never when /api/me fails;
  * hostile strings (a client called <img onerror=...>) stay text, and nothing on the page throws.

The Duplicate dialog is a stub (a script answered in place of /static/duplicate-proposal.js) so these tests do not depend on the dialog's own markup;
one more test opens the real dialog from the real page when duplicate-proposal.js exists. Skipped when Playwright or its Chromium is absent."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest

from tests import dom_support as dom

playwright_sync = pytest.importorskip("playwright.sync_api")

ME = "admin@entravision.com"
OTHER = "maria.lopez@entravision.com"
CHROME_MAC = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
FIREFOX_WIN = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:132.0) Gecko/20100101 Firefox/132.0"
ADMIN_HTML = (dom.ROOT / "app" / "templates" / "admin.html").read_text(encoding="utf-8")
HAS_REAL_DIALOG = all((dom.APP / "static" / name).exists() for name in ("duplicate-proposal.js", "duplicate-proposal.css"))

# What the Duplicate dialog looks like to the page when it is a stub: it records how it was called and does nothing else.
STUB_DIALOG_JS = r"""
window.DuplicateProposal = {
  calls: [],
  open(arg) {
    const row = arg.trigger && arg.trigger.closest ? arg.trigger.closest('.ph-row') : null;
    this.calls.push({ id: arg.proposal && arg.proposal.proposal_id, client: arg.proposal && arg.proposal.client_name, keys: Object.keys(arg).sort().join(','),
      triggerIsDuplicateButton: !!(arg.trigger && arg.trigger.matches && arg.trigger.matches('.ph-dup[data-action=duplicate]')), triggerRow: row ? row.dataset.id : null, onDone: arg.onDone });
  },
};
"""


def ago(**kw) -> str:
    return (datetime.now(timezone.utc) - timedelta(**kw)).isoformat()


def row(i: int, client: str, status: str = "generated", created_by: str = ME, **kw) -> dict:
    """One row as GET /api/admin/proposals returns it (newest first when `i` is small)."""
    draft = status == "draft"
    r = {
        "proposal_id": f"pid{i:03d}", "client_name": client, "seller_email": "ae@entravision.com", "requested_by": "Jane AE", "notion_id": f"EVC-{48000 + i}",
        "proposal_title": f"EVC-{48000 + i}_{client.replace(' ', '')}_Proposal_20261105", "filename": "", "generated_at": "" if draft else ago(hours=i),
        "requester_ip": f"10.0.0.{i}", "requester_user_agent": CHROME_MAC, "total_net": None if draft else 10000.0 * i, "total_gross": None,
        "tabs_built": [] if draft else ["Proposal A"], "status": status, "updated_at": ago(hours=i), "created_by_email": created_by,
        "flight_start": "2027-01-05", "flight_end": "2027-03-31", "flight_text": "", "line_count": 3, "time_unit": "month",
        "wizard_step": 4 if draft else 8, "duplicated_from": None, "can_reopen": True, "option_count": 1,
    }
    r.update(kw)
    return r


def standard_rows() -> list[dict]:
    return [
        row(1, "Fronteras Del Norte", "draft", ME),                                                       # the admin's own draft
        row(2, "Banco Popular de Puerto Rico", "generated", ME, total_net=412500.0),                       # the admin's own generated proposal
        row(3, "Acme HVAC", "draft", OTHER, requester_user_agent=FIREFOX_WIN),                             # somebody else's draft
        row(4, "Toyota Dealers Association", "generated", OTHER, total_net=1250000.0),                     # somebody else's generated proposal
        row(5, "Casa Bonita", "draft", ME.upper().replace("ENTRAVISION.COM", "Entravision.com")),          # the admin's own draft, the address written in another case
        row(6, "Metro PCS", "generated", OTHER, can_reopen=False, line_count=None, wizard_step=None),      # saved before reopening was supported
    ]


class Api:
    """What /api answers for the admin page, plus a log of what it was asked. A route nobody expects goes to `unexpected` (and answers 404)."""

    def __init__(self, rows, me=ME):
        self.rows = rows
        self.me_email = me
        self.me = (200, {"email": me, "is_admin": True})                 # what /api/me answers: (status, body)
        self.log: list[tuple[str, str, dict]] = []                       # (method, path, query)
        self.unexpected: list[str] = []
        self.duplicate_posts: list[tuple[str, dict]] = []                # (source id, request body) of every POST .../duplicate

    def hits(self, path: str) -> list[dict]:
        return [q for _, p, q in self.log if p == path]

    def list_rows(self, q: dict) -> dict:
        size, page = int(q.get("page_size", 10)), int(q.get("page", 1))
        rows = list(self.rows)
        if q.get("mine") == "true":
            rows = [r for r in rows if r["created_by_email"].lower() == self.me_email.lower()]
        words = q.get("search", "").lower().split()
        rows = [r for r in rows if all(w in "\n".join(str(r.get(k) or "") for k in ("client_name", "seller_email", "requested_by", "notion_id", "proposal_title")).lower() for w in words)]
        facets = {"all": len(rows), "draft": sum(r["status"] == "draft" for r in rows), "generated": sum(r["status"] != "draft" for r in rows)}
        status = q.get("status", "all")
        if status != "all":
            rows = [r for r in rows if (r["status"] == "draft") == (status == "draft")]
        rows.sort(key=lambda r: r["updated_at"], reverse=True)
        chunk = rows[(page - 1) * size: page * size]
        return {"proposals": chunk, "count": len(chunk), "total_count": len(rows) if chunk else 0, "page": page, "page_size": size,
                "total_pages": max(1, -(-len(rows) // size)), "facets": facets, "applied": {"status": status, "sort": q.get("sort", "updated"), "dir": q.get("dir", "desc")}}

    def answer(self, route) -> None:
        req = route.request
        url = urlparse(req.url)
        q = {k: v[0] for k, v in parse_qs(url.query, keep_blank_values=True).items()}
        self.log.append((req.method, url.path, q))

        def send(body, status=200):
            route.fulfill(status=status, content_type="application/json", body=json.dumps(body))

        if req.method == "GET" and url.path == "/api/admin/proposals":
            send(self.list_rows(q))
        elif req.method == "GET" and url.path == "/api/me":
            send(self.me[1], self.me[0])
        elif req.method == "DELETE" and url.path.startswith("/api/proposal/"):
            pid = url.path[len("/api/proposal/"):]
            self.rows = [r for r in self.rows if r["proposal_id"] != pid]
            send({"ok": True})
        elif req.method == "GET" and url.path.endswith("/duplicate-defaults"):
            pid = url.path.split("/")[3]
            src = next(r for r in self.rows if r["proposal_id"] == pid)
            send({"source": {"proposal_id": pid, "title": src["proposal_title"], "client_name": src["client_name"], "status": src["status"], "start_date": "2027-01-05",
                             "end_date": "2027-03-31", "time_unit": "month", "periods": 3, "option_count": 1, "line_count": 3},
                  "defaults": {"requested_by": "Jane AE", "salesperson_email": "ae@entravision.com", "salesperson_market": "", "request_type": "", "agency_name": "",
                               "agency_fee": None, "client_website": ""}})
        elif req.method == "POST" and url.path.endswith("/duplicate"):
            pid = url.path.split("/")[3]
            body = json.loads(req.post_data or "{}")
            self.duplicate_posts.append((pid, body))
            src = next(r for r in self.rows if r["proposal_id"] == pid)
            copy = row(99, body["client_name"], "draft", self.me_email, updated_at=ago(seconds=1), wizard_step=2,
                       duplicated_from={"proposal_id": pid, "client_name": src["client_name"], "title": src["proposal_title"]})
            self.rows.insert(0, copy)
            send({"proposal_id": "pid099", "created": True, "proposal_title": copy["proposal_title"], "open_url": "/?reopen=pid099", "wizard_step": 2,
                  "duplicated_from": copy["duplicated_from"], "notes": [{"code": "avails_cleared", "level": "check", "text": "The avails estimates were cleared."}],
                  "stats": {"totals_before": 1.0, "totals_after": 1.0, "periods": 3, "time_unit": "month"}})
        else:
            self.unexpected.append(f"{req.method} {url.path}")
            send({"detail": "not stubbed"}, 404)


@pytest.fixture(scope="module")
def server():
    with dom.serve({"/blank.html": "<!doctype html><title>blank</title>", "/admin_page.html": ADMIN_HTML}) as base:
        yield base


@pytest.fixture(scope="module")
def browser():
    with dom.launch(playwright_sync) as b:
        yield b


def open_admin(browser, server, rows=None, *, me=ME, width=1280, height=900, real_dialog=False, wait=".ph-row", api=None):
    """The real admin page with the fake API behind it. Returns (page, api). The page is opened empty first so the routes are in place before it loads."""
    api = api or Api(standard_rows() if rows is None else rows, me)
    page = dom.new_page(browser, server, "/blank.html", width=width, height=height)
    page.dialogs = []                                                          # type: ignore[attr-defined]
    page.on("dialog", lambda d: (page.dialogs.append(d.message), d.dismiss()))
    page.route("**/api/**", api.answer)
    page.route("**/fonts.googleapis.com/**", lambda r: r.fulfill(status=200, content_type="text/css", body=""))
    page.route("**/fonts.gstatic.com/**", lambda r: r.fulfill(status=200, content_type="font/woff2", body=""))
    if not real_dialog:                                                        # the dialog is somebody else's file: a stub records the call, and its style sheet may be absent
        page.route("**/static/duplicate-proposal.js", lambda r: r.fulfill(status=200, content_type="application/javascript", body=STUB_DIALOG_JS))
        page.route("**/static/duplicate-proposal.css", lambda r: r.fulfill(status=200, content_type="text/css", body=""))
    page.goto(server + "/admin_page.html")
    if wait:
        page.wait_for_selector(wait)
    return page, api


def finish(page, api, *, allow_errors=False):
    """Closes the page; fails on a page error, a native confirm()/alert(), or a request the fake API did not expect."""
    dialogs = list(page.dialogs)
    dom.close_page(page, allow_errors=allow_errors)
    assert dialogs == [], f"a native confirm()/alert() opened: {dialogs}"
    assert api.unexpected == [], api.unexpected


def ids(page):
    return page.eval_on_selector_all(".ph-row", "els => els.map(e => e.dataset.id)")


def texts(page, selector):
    return page.eval_on_selector_all(selector, "els => els.map(e => e.textContent.trim())")


def row_text(page, pid, selector=""):
    """The text of an element in a row as textContent, whitespace collapsed."""
    return " ".join(page.text_content(f".ph-row[data-id='{pid}'] {selector}".strip()).split())


def wait_ready(page):
    page.wait_for_function("() => document.querySelector('.ph') && document.querySelector('.ph').dataset.state !== 'loading' && !document.querySelector('.ph.is-loading')")


def wait_for_me(page, api, answers=2):
    """/api/me is asked twice on page load: by the masthead's account widget and once by the list (to know whose drafts it may delete). Waits for both
    answers, then a moment for the list to use its answer."""
    for _ in range(100):
        if len(api.hits("/api/me")) >= answers:
            break
        page.wait_for_timeout(50)
    page.wait_for_timeout(150)


def open_menu(page, pid):
    """Opens a row's menu. The row is scrolled into view FIRST and the scroll event allowed to fire: a scroll closes an open menu, and a click that makes the
    browser scroll would otherwise close it a moment after it opened."""
    kebab = page.locator(f".ph-row[data-id='{pid}'] .ph-kebab")
    kebab.scroll_into_view_if_needed()
    page.evaluate("() => new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)))")
    kebab.click()


def menu_items(page, pid):
    open_menu(page, pid)
    items = texts(page, ".ph-menu-item")
    page.keyboard.press("Escape")
    return items


def show_all(page, api):
    page.click(".ph-seg--scope button[data-scope='all']")
    wait_ready(page)
    assert api.hits("/api/admin/proposals")[-1]["mine"] == "false"


# --------------------------------------------------------------------------------------------- the first tab is the shared list

def test_the_first_tab_is_the_shared_list_landing_on_my_proposals(browser, server):
    page, api = open_admin(browser, server)
    # the old toolbar, table and pager are gone; the list and its host are there
    assert page.locator("#proposals-host .ph.ph--admin").count() == 1
    assert page.locator("#proposals-table, #proposals-body, #proposals-search, #proposals-scope-tabs, #proposals-prev-btn, #proposals-next-btn, #proposals-page-info").count() == 0
    assert page.inner_text("#tab-proposals h1").strip() == "Proposal History"
    assert page.inner_text("#tab-proposals .lede").strip() == "Every proposal and draft saved on this server, newest first."
    assert page.locator("#tab-proposals").evaluate("e => !e.classList.contains('hidden')")
    # one request, for the admin's own work, 50 rows a page
    assert api.hits("/api/admin/proposals") == [{"page_size": "50", "mine": "true"}]
    assert ids(page) == ["pid001", "pid002", "pid005"]
    assert texts(page, ".ph-seg--scope button") == ["My proposals", "All proposals"]
    assert page.eval_on_selector_all(".ph-seg--scope button", "els => els.map(e => e.getAttribute('aria-pressed'))") == ["true", "false"]
    # a row opens in a new tab, the way an admin looks at somebody else's work
    assert page.eval_on_selector(".ph-row[data-id='pid002'] .ph-open", "e => [e.getAttribute('href'), e.getAttribute('target'), e.getAttribute('rel')]") == ["/?reopen=pid002", "_blank", "noopener"]
    finish(page, api)


def test_the_who_cell_shows_the_planner_the_ae_and_the_device(browser, server):
    page, api = open_admin(browser, server)
    show_all(page, api)
    assert row_text(page, "pid003", ".ph-who .ph-main") == OTHER
    assert row_text(page, "pid003", ".ph-who .ph-minor") == "AE: Jane AE"
    assert row_text(page, "pid003", ".ph-who .ph-device") == "Windows · Firefox 132 · 10.0.0.3"
    assert page.get_attribute(".ph-row[data-id='pid003'] .ph-device", "title") == FIREFOX_WIN
    assert row_text(page, "pid002", ".ph-who .ph-device") == "Mac · Chrome 141 · 10.0.0.2"
    finish(page, api)


def test_the_my_all_switch_asks_the_server_for_everyone_and_back(browser, server):
    page, api = open_admin(browser, server)
    show_all(page, api)
    assert ids(page) == ["pid001", "pid002", "pid003", "pid004", "pid005", "pid006"]
    assert page.eval_on_selector_all(".ph-seg--scope button", "els => els.map(e => e.getAttribute('aria-pressed'))") == ["false", "true"]
    page.click(".ph-seg--scope button[data-scope='mine']")
    wait_ready(page)
    assert api.hits("/api/admin/proposals")[-1]["mine"] == "true"
    assert ids(page) == ["pid001", "pid002", "pid005"]
    finish(page, api)


def test_search_works_and_keeps_the_scope(browser, server):
    page, api = open_admin(browser, server)
    show_all(page, api)
    page.fill(".ph-search-input", "acme")
    page.press(".ph-search-input", "Enter")
    wait_ready(page)
    last = api.hits("/api/admin/proposals")[-1]
    assert last["search"] == "acme" and last["mine"] == "false"                    # the typed words go to the server and the scope stays where it was
    assert ids(page) == ["pid003"]
    page.fill(".ph-search-input", "zzz-nothing")
    page.press(".ph-search-input", "Enter")
    wait_ready(page)
    assert page.get_attribute(".ph", "data-state") == "no-results"
    page.click(".ph-clear-search")
    wait_ready(page)
    assert len(ids(page)) == 6
    finish(page, api)


# --------------------------------------------------------------------------------------------- Duplicate

def test_duplicate_hands_the_row_to_the_dialog_and_the_list_reloads_with_the_new_row_highlighted(browser, server):
    page, api = open_admin(browser, server)
    show_all(page, api)
    page.fill(".ph-search-input", "acme")                                           # a search in force: the new row must still be found, so the list clears it
    page.press(".ph-search-input", "Enter")
    wait_ready(page)
    page.click(".ph-row[data-id='pid003'] .ph-dup")
    calls = page.evaluate("() => window.DuplicateProposal.calls.map(({ onDone, ...rest }) => rest)")
    assert calls == [{"id": "pid003", "client": "Acme HVAC", "keys": "onDone,proposal,trigger", "triggerIsDuplicateButton": True, "triggerRow": "pid003"}]
    # the dialog creates the copy, then tells the page
    api.rows.insert(0, row(99, "Acme HVAC (Copy For Pinnacle)", "draft", ME, duplicated_from={"proposal_id": "pid003", "client_name": "Acme HVAC", "title": "x"}, updated_at=ago(seconds=1)))
    before = len(api.hits("/api/admin/proposals"))
    page.evaluate("() => window.DuplicateProposal.calls[0].onDone({ proposal_id: 'pid099', proposal_title: 'x', open_url: '/?reopen=pid099' })")
    page.wait_for_selector(".ph-row[data-id='pid099'].is-new")
    assert len(api.hits("/api/admin/proposals")) == before + 1
    assert api.hits("/api/admin/proposals")[-1]["mine"] == "false" and "search" not in api.hits("/api/admin/proposals")[-1]
    assert page.input_value(".ph-search-input") == ""
    assert ids(page)[0] == "pid099"
    assert page.evaluate("() => document.activeElement.matches('.ph-row[data-id=pid099] .ph-open')")
    assert row_text(page, "pid099", ".ph-lineage .ph-chip").endswith("Copied from Acme HVAC")
    finish(page, api)


def test_a_row_that_cannot_be_reopened_cannot_be_duplicated_either(browser, server):
    page, api = open_admin(browser, server)
    show_all(page, api)
    assert page.get_attribute(".ph-row[data-id='pid006'] .ph-dup", "aria-disabled") == "true"
    page.click(".ph-row[data-id='pid006'] .ph-dup", force=True)
    assert page.evaluate("() => window.DuplicateProposal.calls.length") == 0
    finish(page, api)


# --------------------------------------------------------------------------------------------- Delete

def test_delete_is_offered_only_for_the_signed_in_admins_own_drafts_whatever_the_case_of_the_email(browser, server):
    page, api = open_admin(browser, server)
    show_all(page, api)
    wait_for_me(page, api)
    offers = {pid: any(t.startswith("Delete") for t in menu_items(page, pid)) for pid in ids(page)}
    assert offers == {"pid001": True,        # own draft
                      "pid002": False,       # own, but generated: a permanent record
                      "pid003": False,       # somebody else's draft
                      "pid004": False,       # somebody else's generated proposal
                      "pid005": True,        # own draft, the address typed in another case
                      "pid006": False}
    open_menu(page, "pid001")                                                        # and the inline delete works from the admin page
    page.click(".ph-menu-item[data-action='delete']")
    page.click(".ph-confirm-delete")
    page.wait_for_selector(".ph-row[data-id='pid001']", state="detached")
    assert [(m, p) for m, p, _ in api.log if m == "DELETE"] == [("DELETE", "/api/proposal/pid001")]
    finish(page, api)                                                                # (a native confirm() would have failed the test)


def test_no_answer_from_api_me_means_no_delete_at_all(browser, server):
    api = Api(standard_rows())
    api.me = (500, {"detail": "boom"})
    page, api = open_admin(browser, server, api=api)
    show_all(page, api)
    wait_for_me(page, api)
    assert not any(t.startswith("Delete") for pid in ids(page) for t in menu_items(page, pid))
    finish(page, api, allow_errors=True)                                              # (the browser logs the 500 itself)


# --------------------------------------------------------------------------------------------- hostile data

def test_hostile_strings_in_a_row_stay_text(browser, server):
    hostile = row(1, "<img src=x onerror=window.__pwned=1>", "generated", "<svg onload=window.__pwned=2>@evil.example",
                  requested_by="<b>AE</b> <img src=y onerror=window.__pwned=3>", requester_user_agent="<img src=z onerror=window.__pwned=4> UA",
                  notion_id='EVC-1" onmouseover="window.__pwned=5', proposal_title='"><script>window.__pwned=6</script>', proposal_id='a/b?c=d&e#f"g',
                  duplicated_from={"proposal_id": "x", "client_name": "<i>Old</i> <img src=w onerror=window.__pwned=7>", "title": "t"})
    page, api = open_admin(browser, server, [hostile], wait=None)                  # not the admin's own row, so it shows under "All proposals" only
    show_all(page, api)
    assert page.locator("#proposals-host img, #proposals-host script, #proposals-host svg, #proposals-host b, #proposals-host i").count() == 0
    assert page.evaluate("() => window.__pwned") is None
    assert page.evaluate("() => document.querySelector('.ph-client-name').textContent") == "<img src=x onerror=window.__pwned=1>"
    assert row_text(page, "a/b?c=d&e#f\"g", ".ph-who .ph-main") == "<svg onload=window.__pwned=2>@evil.example"
    assert row_text(page, "a/b?c=d&e#f\"g", ".ph-who .ph-minor").startswith("AE: <b>AE</b> <img src=y")
    assert page.get_attribute(".ph-row .ph-device", "title") == "<img src=z onerror=window.__pwned=4> UA"
    assert page.get_attribute(".ph-row .ph-open", "href") == "/?reopen=a%2Fb%3Fc%3Dd%26e%23f%22g"
    assert "<i>Old</i> <img src=w onerror=window.__pwned=7>" in row_text(page, "a/b?c=d&e#f\"g", ".ph-lineage")
    page.fill(".ph-search-input", "a.c*")                                            # regular-expression characters in a search are just characters
    page.press(".ph-search-input", "Enter")
    wait_ready(page)
    assert page.get_attribute(".ph", "data-state") == "no-results"
    finish(page, api)


def test_if_the_list_script_is_missing_the_tab_says_so_instead_of_staying_blank(browser, server):
    api = Api(standard_rows())
    page = dom.new_page(browser, server, "/blank.html")
    page.dialogs = []                                                              # type: ignore[attr-defined]
    page.route("**/api/**", api.answer)
    page.route("**/fonts.googleapis.com/**", lambda r: r.fulfill(status=200, content_type="text/css", body=""))
    page.route("**/static/proposal-history.js", lambda r: r.abort())
    page.route("**/static/duplicate-proposal.js", lambda r: r.fulfill(status=200, content_type="application/javascript", body=STUB_DIALOG_JS))
    page.route("**/static/duplicate-proposal.css", lambda r: r.fulfill(status=200, content_type="text/css", body=""))
    page.goto(server + "/admin_page.html")
    page.wait_for_selector("#proposals-host .admin-empty")
    assert page.inner_text("#proposals-host").strip() == "The proposal list could not load. Reload the page to try again."
    assert api.hits("/api/admin/proposals") == []
    finish(page, api, allow_errors=True)                                           # (the browser logs the blocked script itself)


# --------------------------------------------------------------------------------------------- layout

@pytest.mark.parametrize("width", [1024, 1280, 1440])
def test_the_list_fits_the_tab_with_no_sideways_scroll(browser, server, width):
    page, api = open_admin(browser, server, width=width)
    show_all(page, api)
    # (the masthead's nine tabs and the account widget are wider than 1440px on their own; that is not this tab's doing, so only the tab is measured)
    fit = page.evaluate("""() => { const ph = document.querySelector('.ph'), panel = document.getElementById('tab-proposals').getBoundingClientRect(), view = document.documentElement.clientWidth;
        const r = ph.getBoundingClientRect(); return { ph: ph.scrollWidth - ph.clientWidth, inside: r.left >= panel.left - 1 && r.right <= panel.right + 1,
        beyondTheScreen: [...document.querySelectorAll('#tab-proposals *')].filter((e) => e.getBoundingClientRect().right > view + 1).length }; }""")
    assert fit == {"ph": 0, "inside": True, "beyondTheScreen": 0}
    finish(page, api)


# --------------------------------------------------------------------------------------------- the real dialog

@pytest.mark.skipif(not HAS_REAL_DIALOG, reason="app/static/duplicate-proposal.js is not there yet")
def test_with_the_real_dialog_duplicate_opens_it_and_cancelling_gives_the_focus_back(browser, server):
    page, api = open_admin(browser, server, real_dialog=True)
    show_all(page, api)
    page.evaluate("() => document.querySelector(\".ph-row[data-id='pid003'] .ph-dup\").focus({ preventScroll: true })")
    page.click(".ph-row[data-id='pid003'] .ph-dup")
    page.wait_for_selector(".ph-overlay--top .ph-dialog form")
    assert "Acme HVAC" in page.inner_text(".ph-overlay--top .ph-dialog")
    page.keyboard.press("Escape")
    page.wait_for_selector(".ph-overlay--top", state="detached")
    assert page.evaluate("() => document.activeElement.matches(\".ph-row[data-id='pid003'] .ph-dup\")")
    assert len(ids(page)) == 6                                                      # the list is still there, untouched
    finish(page, api)


@pytest.mark.skipif(not HAS_REAL_DIALOG, reason="app/static/duplicate-proposal.js / .css are not there yet")
def test_with_the_real_dialog_a_copy_made_here_shows_up_in_the_list_behind_it(browser, server):
    page, api = open_admin(browser, server, real_dialog=True)
    show_all(page, api)
    page.evaluate("() => document.querySelector(\".ph-row[data-id='pid003'] .ph-dup\").focus({ preventScroll: true })")
    page.click(".ph-row[data-id='pid003'] .ph-dup")
    page.wait_for_selector("#dup-client-name")
    page.fill("#dup-client-name", "Pinnacle Roofing")
    page.fill("#dup-start", "2027-02-01")
    page.fill("#dup-end", "2027-04-30")
    page.click("#dup-submit")
    page.wait_for_selector(".dup-done")
    (source, body), = api.duplicate_posts
    assert source == "pid003"
    assert (body["client_name"], body["start_date"], body["end_date"]) == ("Pinnacle Roofing", "2027-02-01", "2027-04-30")
    assert 16 <= len(body["request_id"]) <= 64                                       # the server derives the copy's id from it, so a retry cannot make a second copy
    page.wait_for_selector(".ph-row[data-id='pid099']")                              # the list behind the dialog reloaded by itself (the scope stayed on All)
    assert api.hits("/api/admin/proposals")[-1]["mine"] == "false"
    assert ids(page)[0] == "pid099" and row_text(page, "pid099", ".ph-client-name") == "Pinnacle Roofing"
    page.click("#dup-close")
    page.wait_for_selector("#duplicate-dialog", state="detached")
    # The list was redrawn behind the dialog, so the Duplicate button that opened it is gone: the keyboard goes to the new row, not to <body>.
    assert page.evaluate("() => document.activeElement.matches(\".ph-row[data-id='pid099'] .ph-open\")")
    finish(page, api)
