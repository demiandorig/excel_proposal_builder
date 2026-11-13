"""DuplicateProposal (app/static/duplicate-proposal.js): the module's surface and the pure helpers behind the "Duplicate for a new client" form
(validation, the request body, the 422 mapper, the Notion ID and percent normalisers, the flight hint, the failure texts). Like
test_proposal_history_frontend.py, the JavaScript runs in a JavaScript engine (Node when installed, else macOS's JavaScriptCore) with a stub `window`:
no browser, no DOM. The dialog in a real browser is tests/test_duplicate_dialog_dom.py.

The helpers that mirror the SERVER are checked against the server: the form's messages are compared with what `DuplicateProposalRequest` really says
for the same input, the period counts with `monthly_allocation.periods_between`, the Notion ID rule with the model and with app.js's own copy of it."""
from __future__ import annotations

import importlib.util
import json
import re
from datetime import date, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from app import main
from app.services import monthly_allocation

ROOT = Path(__file__).resolve().parents[1]
DUP_JS = (ROOT / "app" / "static" / "duplicate-proposal.js").read_text(encoding="utf-8")
DUP_CSS = (ROOT / "app" / "static" / "duplicate-proposal.css").read_text(encoding="utf-8")
APP_JS = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")


def _harness():
    path = ROOT / "tests" / "test_full_flight_frontend.py"
    spec = importlib.util.spec_from_file_location("_ff_frontend_harness_dup", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


H = _harness()


def run(snippet: str, *, prelude: str = ""):
    """Loads duplicate-proposal.js against a stub `window` and runs `snippet` (which returns a JSON string); DP is the module, P is DuplicateProposal._pure."""
    body = "var window = {};\n" + prelude + "\n" + DUP_JS + "\nvar DP = window.DuplicateProposal;\nvar P = DP._pure;\n" + snippet
    try:
        out = H._run_js(body)
    except AssertionError as exc:
        if "JS engine failed" in str(exc) and "osascript" in str(exc).lower():
            pytest.skip(f"JavaScript engine unusable here: {exc}")
        raise
    return json.loads(out)


# --------------------------------------------------------------------------------------------- the public surface

def test_the_module_exposes_open_and_the_pure_helpers_and_nothing_else():
    assert run("return JSON.stringify(Object.keys(DP).sort());") == ["_pure", "open"]
    assert run("return JSON.stringify(Object.keys(P).sort());") == sorted([
        "plainText", "normalizeNotionId", "percentToFraction", "fractionToPercent", "checkDate", "validate", "toRequestBody", "serverSentence", "mapServerErrors",
        "failureText", "isFinalFailure", "formatRange", "countPeriods", "flightHint", "checkCount", "openUrlFor", "newRequestId"])


def test_loading_the_file_does_not_touch_the_page_and_open_is_inert_without_a_document():
    out = run("""
      var h = DP.open({ proposal: { proposal_id: 'abc' } });
      return JSON.stringify({ doc: typeof document, close: typeof h.close, closed: h.close() === undefined });""")
    assert out == {"doc": "undefined", "close": "function", "closed": True}


# --------------------------------------------------------------------------------------------- one engine launch for the helpers

START, END = "2027-02-01", "2027-04-30"
LONG = "x" * 201

# Every form is the TYPED values (agency fee left out: the server takes a fraction, see the percent tests). Each one is run through the page's
# validate() AND through the real request model, and the messages must agree word for word.
FORMS = {
    "valid": {"client_name": "Beta Burgers", "start_date": START, "end_date": END},
    "valid with everything": {"client_name": " Beta   Burgers ", "start_date": START, "end_date": END, "notion_id": "evc 48213", "agency_name": "Initiative Media",
                              "client_website": "https://beta.example", "requested_by": "Ana", "salesperson_email": "ana@entravision.com", "salesperson_market": "Phoenix",
                              "request_type": "Full Presentation", "campaign_name": "Spring Push"},
    "nothing typed": {},
    "blank client with invisible characters": {"client_name": "​ \t\u0007", "start_date": START, "end_date": END},
    "client too long": {"client_name": LONG, "start_date": START, "end_date": END},
    "client exactly 200": {"client_name": "x" * 200, "start_date": START, "end_date": END},
    "start in another format": {"client_name": "B", "start_date": "02/01/2027", "end_date": END},
    "start not a real day": {"client_name": "B", "start_date": "2027-02-30", "end_date": END},
    "end not a real day": {"client_name": "B", "start_date": START, "end_date": "2027-13-01"},
    "year out of range": {"client_name": "B", "start_date": "2207-02-01", "end_date": "2207-03-01"},
    "end before start": {"client_name": "B", "start_date": END, "end_date": START},
    "one day flight": {"client_name": "B", "start_date": START, "end_date": START},
    "flight of exactly 3660 days": {"client_name": "B", "start_date": "2027-01-01", "end_date": (date(2027, 1, 1) + timedelta(days=3659)).isoformat()},
    "flight of 3661 days": {"client_name": "B", "start_date": "2027-01-01", "end_date": (date(2027, 1, 1) + timedelta(days=3660)).isoformat()},
    "start blank, end fine": {"client_name": "B", "end_date": END},
    "end blank, start fine": {"client_name": "B", "start_date": START},
    "notion: text": {"client_name": "B", "start_date": START, "end_date": END, "notion_id": "abc"},
    "notion: three digits": {"client_name": "B", "start_date": START, "end_date": END, "notion_id": "EVC-123"},
    "notion: six digits": {"client_name": "B", "start_date": START, "end_date": END, "notion_id": "EVC-481234"},
    "notion: bare digits": {"client_name": "B", "start_date": START, "end_date": END, "notion_id": "48213"},
    "notion: lower case with dash": {"client_name": "B", "start_date": START, "end_date": END, "notion_id": "evc-4821"},
    "notion: far too long": {"client_name": "B", "start_date": START, "end_date": END, "notion_id": "9" * 300},
    "email: no at sign": {"client_name": "B", "start_date": START, "end_date": END, "salesperson_email": "nope"},
    "email: no dot": {"client_name": "B", "start_date": START, "end_date": END, "salesperson_email": "a@b"},
    "email: spaces": {"client_name": "B", "start_date": START, "end_date": END, "salesperson_email": "a b@c.com"},
    "email: too long": {"client_name": "B", "start_date": START, "end_date": END, "salesperson_email": "a" * 201 + "@c.com"},
    "website too long": {"client_name": "B", "start_date": START, "end_date": END, "client_website": "w" * 301},
    "other texts too long": {"client_name": "B", "start_date": START, "end_date": END, "agency_name": LONG, "requested_by": LONG, "salesperson_market": LONG,
                             "request_type": LONG, "campaign_name": LONG},
}

# Agency fee as typed -> what the page converts it to, or the message it shows. The server's own words for the same bad number are compared below.
FEES = ["", " ", "15", "15%", " 12.5 % ", "0", "0.5", "99", "99.99", "100", "150", "-5", "abc", "1e2", "0x10", "12,5", "15.", ".5", "NaN"]

PERIOD_CASES = [
    ("month", "2027-02-01", "2027-04-30"), ("month", "2027-02-17", "2027-04-30"), ("month", "2026-11-15", "2027-06-20"), ("month", "2027-02-01", "2027-02-01"),
    ("month", "2028-02-29", "2028-03-01"), ("month", "2027-01-31", "2027-02-01"),
    ("week", "2027-02-01", "2027-04-30"), ("week", "2027-02-01", "2027-02-07"), ("week", "2027-02-01", "2027-02-08"), ("week", "2027-02-01", "2027-02-01"),
    ("week", "2026-12-28", "2027-01-31"),
    ("quarter", "2027-03-01", "2027-08-31"), ("quarter", "2027-03-01", "2027-09-30"), ("quarter", "2027-01-01", "2027-01-31"), ("quarter", "2026-11-01", "2027-04-30"),
    ("full_flight", "2027-02-01", "2027-04-30"), ("full_flight", "2027-02-01", "2027-02-01"),
]


def _server_messages(form: dict) -> dict:
    """What the real request model says about this form: {field: message} (pydantic's "Value error, " prefix is stripped, as the dialog does)."""
    body = {"request_id": "dialog-request-0123456789", "client_name": "", "start_date": "", "end_date": "", **form}
    try:
        main.DuplicateProposalRequest(**body)
    except ValidationError as exc:
        return {str(err["loc"][-1]): err["msg"].removeprefix("Value error, ") for err in exc.errors()}
    return {}


SCENARIOS = r"""
var FORMS = %(forms)s, FEES = %(fees)s, PERIODS = %(periods)s;
var out = { validate: {}, bodies: {}, fees: {}, periods: [] };
Object.keys(FORMS).forEach(function (k) { out.validate[k] = P.validate(FORMS[k]); });
Object.keys(FORMS).forEach(function (k) { if (!Object.keys(out.validate[k]).length) out.bodies[k] = P.toRequestBody(Object.assign({ request_id: 'dialog-request-0123456789' }, FORMS[k])); });
FEES.forEach(function (f) { out.fees[f] = P.percentToFraction(f); });
PERIODS.forEach(function (c) {
  var s = P.checkDate(c[1], 'start date'), e = P.checkDate(c[2], 'end date');
  out.periods.push(P.countPeriods(c[0], s, e));
});
return JSON.stringify(out);
"""


@pytest.fixture(scope="module")
def scenarios():
    snippet = SCENARIOS % {"forms": json.dumps(FORMS), "fees": json.dumps(FEES), "periods": json.dumps(PERIOD_CASES)}
    return run(snippet)


@pytest.mark.parametrize("name", list(FORMS))
def test_validate_says_what_the_server_would_say_for_the_same_form(scenarios, name):
    """The page checks first so the planner gets the message at once; the server has the last word. They must not disagree about WHETHER a form is
    valid, which field is wrong, or the words."""
    assert scenarios["validate"][name] == _server_messages(FORMS[name]), name


def test_validate_gives_the_documented_messages():
    v = {k: val for k, val in ((n, run("return JSON.stringify(P.validate(%s));" % json.dumps(f))) for n, f in FORMS.items())}
    assert v["nothing typed"] == {"client_name": "Enter the new client's name.", "start_date": "Enter the start date as YYYY-MM-DD.", "end_date": "Enter the end date as YYYY-MM-DD."}
    assert v["start not a real day"] == {"start_date": "The start date isn't a real calendar date."}
    assert v["end before start"] == {"end_date": "The end date can't be before the start date."}
    assert v["flight of 3661 days"] == {"end_date": "The flight can't be longer than 10 years."}
    assert v["notion: text"] == {"notion_id": "The Notion ID is a 4 or 5 digit number, for example EVC-48213."}
    assert v["email: no at sign"] == {"salesperson_email": "That doesn't look like an email address."}
    assert v["year out of range"]["start_date"] == "The start date must be in a year from 2000 to 2100."
    for ok in ("valid", "valid with everything", "client exactly 200", "one day flight", "flight of exactly 3660 days", "notion: bare digits", "notion: lower case with dash"):
        assert v[ok] == {}, ok


def test_a_valid_form_becomes_the_exact_request_body_and_the_server_accepts_it(scenarios):
    body = scenarios["bodies"]["valid with everything"]
    assert body == {
        "request_id": "dialog-request-0123456789", "client_name": "Beta Burgers", "start_date": START, "end_date": END, "notion_id": "EVC-48213",
        "agency_name": "Initiative Media", "agency_fee": None, "client_website": "https://beta.example", "requested_by": "Ana", "salesperson_email": "ana@entravision.com",
        "salesperson_market": "Phoenix", "request_type": "Full Presentation", "campaign_name": "Spring Push", "keep_avails": False,
    }
    accepted = main.DuplicateProposalRequest(**body)
    assert accepted.client_name == "Beta Burgers" and accepted.notion_id == "EVC-48213" and accepted.requested_by == "Ana"
    for name, sent in scenarios["bodies"].items():
        main.DuplicateProposalRequest(**sent)                       # nothing the page sends is ever refused for its shape


def test_blank_optional_fields_are_sent_blank_not_left_out(scenarios):
    """An OMITTED requested_by / email / market / request type means "keep the original's"; the form's blank one means blank."""
    body = scenarios["bodies"]["valid"]
    assert body["requested_by"] == "" and body["salesperson_email"] == "" and body["salesperson_market"] == "" and body["request_type"] == "" and body["notion_id"] == ""
    assert body["agency_fee"] is None and body["keep_avails"] is False
    accepted = main.DuplicateProposalRequest(**body)
    assert accepted.requested_by == "" and accepted.salesperson_email == "" and accepted.request_type == ""


def test_percent_to_fraction_matches_the_servers_strict_fraction(scenarios):
    fees = scenarios["fees"]
    expected = {"": None, " ": None, "15": 0.15, "15%": 0.15, " 12.5 % ": 0.125, "0": 0.0, "0.5": 0.005, "99": 0.99, "99.99": 0.9999, "12,5": 0.125, "15.": 0.15, ".5": None}
    for typed, fraction in expected.items():
        if typed == ".5":
            continue                                                  # a bare ".5" is not a number the box accepts: covered with the errors below
        assert fees[typed] == {"fraction": fraction}, typed
        if fraction is not None:                                     # and the server takes exactly what the page converted it to
            assert main.DuplicateProposalRequest(request_id="dialog-request-0123456789", client_name="B", start_date=START, end_date=END, agency_fee=fees[typed]["fraction"])
    out_of_range, not_a_number = "The agency fee must be between 0% and 99%.", "The agency fee must be a number between 0% and 99%."
    for typed in ("100", "150", "1e2"):
        assert fees[typed] == {"error": out_of_range if typed != "1e2" else not_a_number}, typed
    for typed in ("-5", "abc", "0x10", "NaN", ".5"):
        assert fees[typed] == {"error": not_a_number}, typed
    # The server's own words for the same two failures
    assert _fee_message(1.0) == out_of_range and _fee_message("abc") == not_a_number


def _fee_message(value) -> str:
    try:
        main.DuplicateProposalRequest(request_id="dialog-request-0123456789", client_name="B", start_date=START, end_date=END, agency_fee=value)
    except ValidationError as exc:
        return exc.errors()[0]["msg"].removeprefix("Value error, ")
    return ""


def test_fraction_to_percent_is_what_a_person_types():
    out = run("return JSON.stringify([0.15, 0.125, 0.5, 0.99, 0.0001, 0, 1, -1, null, 'x', NaN].map(P.fractionToPercent));")
    assert out == ["15", "12.5", "50", "99", "0.01", "", "", "", "", "", ""]


def test_period_counts_equal_the_servers_for_every_billing_period(scenarios):
    """The live hint under the flight dates counts periods itself (the dialog needs no app.js global): it must agree with monthly_allocation."""
    for (unit, start, end), counted in zip(PERIOD_CASES, scenarios["periods"]):
        want = len(monthly_allocation.periods_between(date.fromisoformat(start), date.fromisoformat(end), unit))
        assert counted == want, (unit, start, end)


# --------------------------------------------------------------------------------------------- Notion ID: one rule in three places

NOTION_TABLE = ["", "  ", "EVC-48213", "evc 48213", "evc48213", "48213", "4821", "EVC-4821", "EVC-482", "EVC-482134", "abc", "EVC-", "EVC-4821x", "12 345", " 48213 ", "EVC–48213"]


def test_the_notion_id_rule_is_the_same_in_the_dialog_in_step_02_and_on_the_server():
    dialog = run("return JSON.stringify(%s.map(P.normalizeNotionId));" % json.dumps(NOTION_TABLE))
    step02 = run("return JSON.stringify(%s.map(_normalizeNotionId));" % json.dumps(NOTION_TABLE), prelude=H._extract_fn(APP_JS, "_normalizeNotionId"))
    assert dialog == step02
    for typed, got in zip(NOTION_TABLE, dialog):
        try:
            server = main.DuplicateProposalRequest(request_id="dialog-request-0123456789", client_name="B", start_date=START, end_date=END, notion_id=typed).notion_id
        except ValidationError:
            server = None
        assert got == server, typed
    assert dialog[:5] == ["", "", "EVC-48213", "EVC-48213", "EVC-48213"]


# --------------------------------------------------------------------------------------------- the 422 mapper and the failure texts

def test_a_422_is_mapped_to_the_field_it_names_and_pydantic_s_own_words_are_not_shown():
    out = run("""
      return JSON.stringify([
        P.mapServerErrors({ detail: [{ loc: ['body', 'end_date'], msg: "Value error, The end date can't be before the start date.", type: 'value_error' }] }),
        P.mapServerErrors({ detail: [
          { loc: ['body', 'client_name'], msg: "Value error, Enter the new client's name." },
          { loc: ['body', 'client_name'], msg: 'Value error, second message for the same field is dropped' },
          { loc: ['body', 'start_date'], msg: 'Field required' },
          { loc: ['body', 'request_id'], msg: "Value error, The page didn't send a valid request id. Reload the page and try again." },
        ] }),
        P.mapServerErrors({ detail: [{ loc: ['body'], msg: 'JSON decode error' }] }),
        P.mapServerErrors({ detail: 'a plain string' }), P.mapServerErrors(null), P.mapServerErrors({ detail: [null, 5, { loc: 'x' }] }),
        P.serverSentence('Value error, Hello.'), P.serverSentence('Input should be a valid string'), P.serverSentence(undefined),
      ]);""")
    assert out[0] == {"fields": {"end_date": "The end date can't be before the start date."}, "general": ""}
    assert out[1] == {"fields": {"client_name": "Enter the new client's name.", "start_date": "Check this and try again."},
                      "general": "The page didn't send a valid request id. Reload the page and try again."}
    assert out[2] == {"fields": {}, "general": "We couldn't create the copy. Please try again."}
    assert out[3] == out[4] == {"fields": {}, "general": ""}                  # no list of problems: nothing to map (the caller shows its own failure text)
    assert out[5] == {"fields": {}, "general": "We couldn't create the copy. Please try again."}      # an item that names no field is a general problem
    assert out[6:] == ["Hello.", "Check this and try again.", "Check this and try again."]


def test_the_422_the_real_app_sends_maps_back_onto_the_fields():
    """The shape FastAPI really produces for the model's errors (not a hand-written one)."""
    try:
        main.DuplicateProposalRequest(request_id="dialog-request-0123456789", client_name="", start_date="2027-02-30", end_date="2027-01-01", notion_id="abc")
    except ValidationError as exc:
        detail = [{"loc": ["body", *err["loc"]], "msg": err["msg"], "type": err["type"]} for err in exc.errors()]
    mapped = run("return JSON.stringify(P.mapServerErrors({ detail: %s }));" % json.dumps(detail))
    assert set(mapped["fields"]) == {"client_name", "start_date", "notion_id"} and mapped["general"] == ""
    assert mapped["fields"]["start_date"] == "The start date isn't a real calendar date."


def test_failures_are_told_in_plain_words_never_with_the_servers_raw_text():
    out = run("""
      var rows = {};
      [[0, 'defaults', true], [0, 'submit', true], [401, 'submit'], [403, 'defaults'], [404, 'submit'], [409, 'defaults'], [409, 'submit'], [410, 'defaults'],
       [429, 'submit'], [500, 'submit'], [500, 'defaults'], [503, 'submit'], [503, 'defaults'], [502, 'submit'], [502, 'defaults'], [418, 'submit'], [418, 'defaults']]
        .forEach(function (c) { rows[c.slice(0, 3).join('|')] = P.failureText(c[0], c[1], c[2]); });
      return JSON.stringify({ rows: rows, final: [403, 404, 409, 410, 500, 503, 0].map(P.isFinalFailure) });""")
    rows = out["rows"]
    assert rows["404|submit"] == "We couldn't find that proposal. It may have been deleted."
    assert rows["403|defaults"] == "You can only duplicate your own proposals."
    assert rows["410|defaults"] == "This proposal was saved before copying was supported, so it can't be duplicated."
    assert rows["409|defaults"] == "There's nothing to copy yet: this draft has no request details."
    assert rows["500|submit"] == "We couldn't create the copy. Nothing was saved."
    assert rows["503|submit"] == "The database isn't reachable right now. Nothing was created. Try again in a moment."
    assert "second copy" in rows["0|submit|true"] and "second copy" not in rows["0|defaults|true"]
    assert rows["502|submit"].startswith("The server had a problem.") and "Nothing was created" in rows["502|submit"]
    assert all(text and "http" not in text.lower() and "{" not in text and "undefined" not in text for text in rows.values())      # a sentence, never a status line or raw data
    assert out["final"] == [True, True, True, True, False, False, False]


# --------------------------------------------------------------------------------------------- the flight hint

def test_the_flight_hint_counts_the_new_flight_and_says_when_totals_will_move():
    month = {"time_unit": "month", "periods": 3, "start_date": "2026-09-01", "end_date": "2026-11-30"}
    out = run("""
      var month = %s;
      var week = { time_unit: 'week', periods: 13, start_date: '2027-02-01', end_date: '2027-04-30' };
      var quarter = { time_unit: 'quarter', periods: 2, start_date: '2027-03-01', end_date: '2027-08-31' };
      var full = { time_unit: 'full_flight', periods: 1, start_date: '2026-09-01', end_date: '2026-11-30' };
      var unknown = { time_unit: 'month', periods: null, start_date: null, end_date: null };
      return JSON.stringify({
        same: P.flightHint(month, '2027-02-01', '2027-04-30'),
        longer: P.flightHint(month, '2027-02-01', '2027-05-31'),
        one: P.flightHint(month, '2027-02-01', '2027-02-01'),
        acrossYears: P.flightHint(month, '2026-11-15', '2027-06-20'),
        beforeDates: P.flightHint(month, '', ''),
        halfTyped: P.flightHint(month, '2027-02-01', ''),
        endBeforeStart: P.flightHint(month, '2027-05-01', '2027-02-01'),
        weeks: P.flightHint(week, '2027-02-01', '2027-04-30'), weeksMore: P.flightHint(week, '2027-02-01', '2027-05-14'),
        quarters: P.flightHint(quarter, '2027-03-01', '2027-08-31'), quartersMore: P.flightHint(quarter, '2027-03-01', '2027-11-30'),
        full: P.flightHint(full, '2027-02-01', '2027-04-30'), fullBefore: P.flightHint(full, '', ''),
        unknownAfter: P.flightHint(unknown, '2027-02-01', '2027-04-30'), unknownBefore: P.flightHint(unknown, '', ''),
        nothing: P.flightHint(null, '2027-02-01', '2027-04-30'), proto: P.flightHint({ time_unit: 'constructor', periods: 3 }, '2027-02-01', '2027-04-30'),
      });""" % json.dumps(month))
    assert out["same"] == "3 months (Feb 1 to Apr 30, 2027). The original was 3 months."
    assert out["longer"] == "4 months (Feb 1 to May 31, 2027). The original was 3 months. Budgets stay per month, so the totals will change."
    assert out["one"].startswith("1 month (Feb 1 to Feb 1, 2027).") and "Budgets stay per month" in out["one"]
    assert out["acrossYears"].startswith("8 months (Nov 15, 2026 to Jun 20, 2027).")
    assert out["beforeDates"] == out["halfTyped"] == out["endBeforeStart"] == "The original ran Sep 1 to Nov 30, 2026 (3 months)."
    assert out["weeks"] == "13 weeks (Feb 1 to Apr 30, 2027). The original was 13 weeks."
    assert out["weeksMore"].endswith("The original was 13 weeks. Budgets stay per week, so the totals will change.")
    assert out["quarters"] == "2 quarters (Mar 1 to Aug 31, 2027). The original was 2 quarters."
    assert "3 quarters" in out["quartersMore"] and "Budgets stay per quarter" in out["quartersMore"]
    assert out["full"] == "Billed as one period for the whole flight (Feb 1 to Apr 30, 2027). The budgets are flight totals, so they stay the same."
    assert out["fullBefore"] == "The original ran Sep 1 to Nov 30, 2026."
    assert out["unknownAfter"] == "3 months (Feb 1 to Apr 30, 2027)." and out["unknownBefore"] == ""
    assert out["nothing"] == "3 months (Feb 1 to Apr 30, 2027)."
    assert out["proto"].startswith("3 months")                     # an odd unit name falls back to months, it does not read the object's own properties


# --------------------------------------------------------------------------------------------- the small ones

def test_the_success_helpers_and_the_request_id():
    out = run("""
      var ids = []; for (var i = 0; i < 50; i++) ids.push(P.newRequestId());
      return JSON.stringify({
        checks: [P.checkCount([{ level: 'check' }, { level: 'info' }, { level: 'check' }, null, 'x', {}]), P.checkCount(null), P.checkCount([])],
        urls: [P.openUrlFor({ open_url: '/?reopen=abc', proposal_id: 'abc' }), P.openUrlFor({ open_url: '//evil.example/x', proposal_id: 'a b' }),
               P.openUrlFor({ open_url: 'https://evil.example', proposal_id: 'abc' }), P.openUrlFor({ open_url: '/\\\\evil', proposal_id: 'abc' }),
               P.openUrlFor({ proposal_id: 'a/b?c' }), P.openUrlFor(null)],
        ids: ids, unique: new Set(ids).size,
      });""")
    assert out["checks"] == [2, 0, 0]
    assert out["urls"] == ["/?reopen=abc", "/?reopen=a%20b", "/?reopen=abc", "/?reopen=abc", "/?reopen=a%2Fb%3Fc", "/?reopen="]
    assert out["unique"] == 50 and all(re.fullmatch(r"[A-Za-z0-9_-]{16,64}", i) for i in out["ids"])


def test_plain_text_cleans_what_the_server_cleans():
    out = run("return JSON.stringify([P.plainText('  a \\u200b b\\t\\tc \\u0007 '), P.plainText(null), P.plainText(12), P.plainText('\\ud800x')]);")
    assert out == ["a b c", "", "12", "x"]


# --------------------------------------------------------------------------------------------- static contracts of the files

def test_the_dialog_never_writes_html_from_data():
    """Everything dynamic reaches the page as a text node: no innerHTML / outerHTML / insertAdjacentHTML / document.write anywhere in the file."""
    code = re.sub(r"/\*.*?\*/", "", DUP_JS, flags=re.S)
    code = re.sub(r"//.*", "", code)                                  # (the header comment says "no innerHTML": only code counts)
    assert not re.search(r"innerHTML|outerHTML|insertAdjacentHTML|document\.write|\beval\(|new Function", code)


def test_the_dialog_uses_no_app_js_global():
    """It loads on admin.html too: only window.fetch, ProposalHistory.shell and the DOM."""
    for name in ("state", "escapeHtml", "escapeAttr", "_debounce", "_readJsonResponse", "_mbPeriodsBetween", "money"):
        assert not re.search(rf"(?<![\w.$]){re.escape(name)}\(", DUP_JS), name
    assert "window.ProposalHistory" in DUP_JS and "PH.shell(" in DUP_JS


def test_every_selector_in_the_stylesheet_belongs_to_this_feature():
    selectors = re.findall(r"^([.#][\w.#:\[\]=\"\- >,()]+?)\s*[{,]", DUP_CSS, re.M)
    assert selectors, "no rules found"
    for sel in selectors:
        assert re.match(r"\.(dup|duplicate-notice|field-note|form-grid)|#dup", sel), sel
