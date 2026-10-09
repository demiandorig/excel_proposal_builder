"""
Full Flight billing period — the FRONT END (app/static/app.js, index.html, styles.css, admin.js).

There is no JS test runner in this repo, so (like test_avails_persistence.py) the real functions are pulled out of
app.js BY NAME and run in a JavaScript engine: Node when it is installed, else macOS's built-in JavaScriptCore
through `osascript -l JavaScript`, else the engine-backed tests are skipped (never errored). The page's DOM is faked
just far enough for the label/step logic, and every scenario runs in ONE engine launch (module-scoped fixture).

What is pinned here:
  * the JS period builder is byte-for-byte the Python one (monthly_allocation.flight_between): key, label,
    date_range_label, dates, day counts, period_count — for same-year, one-month, cross-year, swapped dates;
  * week/month/quarter periods are still identical to Python (regression);
  * months == 1 in Full Flight whatever the dates / legacy "saved N" flags say, for the active AND snapshot options;
  * Full Flight is ONE period: the minimum-spend scale and the SOV monthly-equivalent are 1 whatever the flight's
    dates (a flight-long line owes ONE monthly minimum; its whole budget is compared with the monthly ceiling), and
    Step 04 is seeded with the Step 02 figures exactly as typed — no month count anywhere;
  * the billing-period switch: the flight total of every line of every option is preserved in AND out of Full Flight
    (with a confirm that names an example), the legacy confirm text for week/month/quarter is untouched;
  * labels, the Step 02 select / Step 04 pill sync, the .ff-mode toggles, the Step 06 short-circuit;
  * static markup/CSS contracts that need no engine at all.
"""
import json
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

import pytest

from app.services.monthly_allocation import granularity_scale, periods_between

ROOT = Path(__file__).resolve().parent.parent
APP_JS = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
ADMIN_JS = (ROOT / "app" / "static" / "admin.js").read_text(encoding="utf-8")
INDEX_HTML = (ROOT / "app" / "templates" / "index.html").read_text(encoding="utf-8")
ADMIN_HTML = (ROOT / "app" / "templates" / "admin.html").read_text(encoding="utf-8")
STYLES_CSS = (ROOT / "app" / "static" / "styles.css").read_text(encoding="utf-8")


# --------------------------------------------------------------------------------------------------------------------
# Pulling functions/constants out of app.js by name (robust to line-number drift)
# --------------------------------------------------------------------------------------------------------------------

def _extract_fn(src: str, name: str) -> str:
    m = re.search(rf"^(?:async )?function {re.escape(name)}\(", src, re.M)
    assert m, f"function {name} not found"
    end = src.index("\n}\n", m.start())     # top-level functions close with a "}" in column 0
    return src[m.start(): end + 2]


def _extract_const(src: str, name: str) -> str:
    m = re.search(rf"^const {re.escape(name)} = ", src, re.M)
    assert m, f"const {name} not found"
    end = src.index(";\n", m.end())
    return src[m.start(): end + 1]


_APP_CONSTS = [
    "_STEP02_FF_NOTE", "FULL_FLIGHT", "FULL_FLIGHT_KEY", "TIME_UNITS", "_MB_CENT", "_MB_GRANULARITY_MIN_SCALE",
    "_SOV_MONTHLY_EQUIVALENT_SCALE", "_MB_UNIT_NOUN", "_MB_UNIT_NOUN_PLURAL", "_MB_UNIT_ADJECTIVE", "_TIME_UNIT_DISPLAY",
]
_APP_FUNCTIONS = [
    "_isFullFlight", "_round2", "_normalizeTimeUnit", "_mbUnitWord", "_mbUnitNoun", "_mbUnitNounPlural",
    "_mbUnitAdjective", "_mbParseDate", "_mbMonthKey", "_mbMonthLabel", "_mbLastDayOfMonth",
    "_mbDaysBetweenInclusive", "_mbDateRangeLabel", "_mbMonthsBetween", "_mbWeeksBetween", "_mbQuartersBetween",
    "_mbCombinedPeriodLabel", "_mbFullFlightPeriods", "_mbPeriodsBetween", "_mbApplyPeriodMerges",
    "_effectiveStartDate", "_effectiveEndDate", "_timeUnitMinimumScale", "_mbEffectiveMinimumForPeriod",
    "_sovMonthlyEquivalentBudget", "_convertBudgetForFullFlightSwitch", "_moneyExact", "_fullFlightSwitchConfirmText",
    "onTimeUnitChange", "_mixEditBlocks", "allTiersForSubmit", "parseFormattedInput", "formatBudgetInputValue",
    "_mbStampBaseline", "_mbRescaleLine", "_mbRescaleForBudgetChange", "_mbEvenDefaultAllocation",
    "_resyncAllTierMonths", "_curateDerivedMonths", "_mbEffectiveMonths", "_mbEffectiveMonthsUnmerged",
    "_normalizeFullFlightState", "_applyTimeUnitLabels", "_mbRenderFullFlightNote", "_mbSetContinueEnabled",
    "_tierDisplayName", "money", "buildGammaOutline",
    "_foldLineIntoOnePeriod", "_refreshStep02FlightNote", "_refreshMinHeaderTitle",
    "sovTier", "applySovDisplay", "escapeAttr", "escapeHtml",
]


def _app_js_bundle() -> str:
    parts = [_extract_const(APP_JS, n) for n in _APP_CONSTS] + [_extract_fn(APP_JS, n) for n in _APP_FUNCTIONS]
    # The real Step 06 renderer is exercised under its own name; the plain name is a call-recording stub (below)
    # so onTimeUnitChange can be run without dragging in the whole Step 06 renderer.
    parts.append(_extract_fn(APP_JS, "renderMonthlyBreakdown").replace(
        "function renderMonthlyBreakdown(", "function __realRenderMonthlyBreakdown(", 1))
    parts.append(_extract_const(ADMIN_JS, "_TIME_UNIT_LABELS"))
    parts.append(_extract_fn(ADMIN_JS, "_formatTimeUnitLabel"))
    return "\n".join(parts)


# --------------------------------------------------------------------------------------------------------------------
# The engine
# --------------------------------------------------------------------------------------------------------------------

def _engine_command():
    """('node', path) | ('jsc', path) | None."""
    node = shutil.which("node")
    if node:
        return ("node", node)
    osa = shutil.which("osascript")
    if osa and sys.platform == "darwin":
        return ("jsc", osa)
    return None


def _run_js(body: str) -> str:
    """Runs `body` (JS that ends by returning a JSON string from a function) and returns what it printed."""
    kind_path = _engine_command()
    if not kind_path:
        pytest.skip("neither node nor macOS JavaScriptCore (osascript) is available")
    kind, path = kind_path
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "scenario.js"
        if kind == "node":
            script.write_text(f"console.log((function () {{\n{body}\n}})());\n", encoding="utf-8")
            cmd = [path, str(script)]
        else:
            script.write_text(f"function run() {{\n return (function () {{\n{body}\n}})();\n}}\n", encoding="utf-8")
            cmd = [path, "-l", "JavaScript", str(script)]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise AssertionError(f"JS engine failed ({kind}):\n{proc.stderr}\n{proc.stdout}")
    return proc.stdout.strip()


@pytest.fixture(scope="module")
def js():
    # A trivial probe first: an engine that exists but can't run here (sandbox, no GUI session...) skips; a real
    # failure of the scenarios themselves (below) is an error.
    try:
        probe = _run_js('return JSON.stringify({ok: 1 + 1});')
    except AssertionError as exc:
        pytest.skip(f"JavaScript engine unusable here: {exc}")
    assert json.loads(probe) == {"ok": 2}
    return json.loads(_run_js(_scenarios()))


# --------------------------------------------------------------------------------------------------------------------
# The scenarios (one engine launch)
# --------------------------------------------------------------------------------------------------------------------

PERIOD_CASES = [
    ["2026-11-24", "2026-12-31"],     # same-year, two months  -> "November–December 2026"
    ["2026-11-03", "2026-11-27"],     # inside one month       -> "November 2026"
    ["2026-12-15", "2027-01-20"],     # crosses a year         -> "December 2026–January 2027"
    ["2026-12-31", "2026-11-24"],     # swapped
    ["2026-11-24", "2026-11-24"],     # a single day
    ["2026-09-28", "2026-12-12"],     # four months
    ["2026-11-01", "2027-02-28"],     # four months across a year
    ["2028-02-10", "2028-03-05"],     # leap year
]


def _scenarios() -> str:
    prelude = r"""
var __warns = [];
var console = { warn: function () { __warns.push(Array.prototype.slice.call(arguments).join(" ")); }, log: function () {}, error: function () {} };
var __alerts = [], __confirms = [], __confirmAnswer = true;
function alert(m) { __alerts.push(String(m)); }
function confirm(m) { __confirms.push(String(m)); return __confirmAnswer; }

function makeEl(id) {
  var cls = {};
  return {
    id: id, textContent: "", value: "", title: "", dataset: {}, style: {}, disabled: false, checked: false, indeterminate: false,
    removeAttribute: function (a) { if (a === "title") this.title = ""; },
    classList: {
      add: function (c) { cls[c] = true; }, remove: function (c) { delete cls[c]; },
      toggle: function (c, force) { var on = force === undefined ? !cls[c] : !!force; if (on) cls[c] = true; else delete cls[c]; return on; },
      contains: function (c) { return !!cls[c]; }
    }
  };
}
var __els = {};
var __sliderOptions = ["week", "month", "quarter", "full_flight"].map(function (u) { var e = makeEl("opt-" + u); e.dataset.unit = u; return e; });
var document = {
  getElementById: function (id) { return __els[id] || (__els[id] = makeEl(id)); },
  querySelector: function (sel) { return document.getElementById("sel:" + sel); },
  querySelectorAll: function (sel) { return sel.indexOf("time-unit-slider-option") >= 0 ? __sliderOptions : []; }
};
function resetDom() { __els = {}; __sliderOptions.forEach(function (e) { e.classList.remove("active"); }); }

var state;
var __calls = [];
function renderLineItems() { __calls.push("renderLineItems"); }
function updateTotals() { __calls.push("updateTotals"); }
function renderMonthlyBreakdown() { __calls.push("renderMonthlyBreakdown"); }
"""
    helpers = r"""
function iso(d) { return d.toISOString().slice(0, 10); }
function plain(p) {
  return { key: p.key, label: p.label, date_range_label: p.date_range_label, start: iso(p.start), end: iso(p.end),
           days_in_month: p.days_in_month, active_days: p.active_days, period_count: p.period_count };
}
function mkState(unit, o) {
  o = o || {};
  return {
    timeUnit: unit,
    parsed: { start_date: o.start === undefined ? "2026-11-24" : o.start, end_date: o.end === undefined ? "2026-12-31" : o.end, agency_fee: 0.15,
              monthly_budget: o.monthlyBudget === undefined ? null : o.monthlyBudget, total_months: o.totalMonths },
    activeTierLabel: "A", activeTierName: null, activeTierGeo: null,
    activeTierStartDate: o.tierStart || null, activeTierEndDate: o.tierEnd || null,
    lineItems: o.lines || [], tiers: o.tiers || [], activeTierPeriodMergeGroups: o.merges || [],
    mixEdit: o.mixEdit || null, productIndex: {}, availsData: {}, addons: {}, strategyBrief: null, roadblocks: null,
    enrichment: null, finalProposalTitle: null
  };
}
function L(name, budget, months, extra) {
  var li = { id: "li_" + name, product_name: name, monthly_budget: budget, months: months, monthly_allocations: null, is_added_value: false };
  if (extra) Object.keys(extra).forEach(function (k) { li[k] = extra[k]; });
  return li;
}
function tierSnapshot(label, lines, o) {
  o = o || {};
  return { label: label, name: null, geo: null, startDate: o.start || null, endDate: o.end || null, lineItems: lines, availsData: {}, periodMergeGroups: o.merges || [] };
}
function total(lines) { return lines.reduce(function (s, li) { return s + li.monthly_budget * li.months; }, 0); }
function round2(n) { return Math.round(n * 100) / 100; }
function texts(ids) { var out = {}; ids.forEach(function (id) { out[id] = document.getElementById(id).textContent; }); return out; }
"""
    body = r"""
var R = {};
var CASES = __CASES__;

// ---- 1. the period builder vs Python ----
R.periods = CASES.map(function (c) {
  var sd = _mbParseDate(c[0]), ed = _mbParseDate(c[1]);
  return {
    input: c,
    full_flight: _mbFullFlightPeriods(sd, ed).map(plain),
    dispatched: _mbPeriodsBetween(sd, ed, "full_flight").map(plain),
    week: _mbPeriodsBetween(sd, ed, "week").map(plain),
    month: _mbPeriodsBetween(sd, ed, "month").map(plain),
    quarter: _mbPeriodsBetween(sd, ed, "quarter").map(plain)
  };
});
R.noDates = {
  nulls: _mbFullFlightPeriods(null, null).length,
  garbage: _mbPeriodsBetween(_mbParseDate("garbage"), _mbParseDate("nope"), "full_flight").length
};
var warnsBefore = __warns.length;
var unknownKeys = _mbPeriodsBetween(_mbParseDate("2026-11-24"), _mbParseDate("2026-12-31"), "bogus").map(function (p) { return p.key; });
var warnsMid = __warns.length;
_mbPeriodsBetween(_mbParseDate("2026-11-24"), _mbParseDate("2026-12-31"), "month");
R.unknownGranularity = { keys: unknownKeys, warned: warnsMid - warnsBefore, monthWarned: __warns.length - warnsMid };
R.constants = { FULL_FLIGHT: FULL_FLIGHT, FULL_FLIGHT_KEY: FULL_FLIGHT_KEY, TIME_UNITS: TIME_UNITS };
R.normalizeTimeUnit = ["full_flight", " Full_Flight ", "week", "", null, undefined, "bogus", 7].map(_normalizeTimeUnit);

// ---- 2. minimum-spend scale and SOV equivalent: Full Flight is ONE period, so both are 1 ----
var ffPeriod = _mbFullFlightPeriods(_mbParseDate("2026-11-24"), _mbParseDate("2026-12-31"))[0];
var monthPeriod = _mbMonthsBetween(_mbParseDate("2026-11-24"), _mbParseDate("2026-12-31"))[0];
R.scale = {};
["week", "month", "quarter", "full_flight"].forEach(function (u) {
  state = mkState(u);
  R.scale[u] = { scale: _timeUnitMinimumScale(), periodMin: _mbEffectiveMinimumForPeriod(1000, u === "full_flight" ? ffPeriod : monthPeriod) };
});
// ... whatever the flight's dates: one month, no dates at all, four months, an option's own override, nothing set
R.scaleFullFlightAnyDates = [
  { tierStart: "2026-11-03", tierEnd: "2026-11-27" }, { start: "", end: "" }, { start: "2026-09-28", end: "2026-12-12" }, {}
].map(function (o) {
  state = mkState("full_flight", o);
  return { scale: _timeUnitMinimumScale(), sov: _sovMonthlyEquivalentBudget(6000) };
});
R.sov = {};
["week", "month", "quarter", "full_flight"].forEach(function (u) { state = mkState(u); R.sov[u] = _sovMonthlyEquivalentBudget(6000); });

R.convertMath = {
  into: _convertBudgetForFullFlightSwitch(2000, 3, null),
  intoOne: _convertBudgetForFullFlightSwitch(680, 1, null),
  out: _convertBudgetForFullFlightSwitch(6000, 1, 3),
  outCents: _convertBudgetForFullFlightSwitch(5000, 1, 3),
  zero: _convertBudgetForFullFlightSwitch(0, 3, null)
};

// ---- 3. months are 1 in Full Flight ----
state = mkState("full_flight", { start: "", end: "", lines: [L("a", 100, 3, { _monthsReconciled: false }), L("b", 100, 3), L("c", 100, 5, { _monthsReconciled: true })],
                                 tiers: [tierSnapshot("B", [L("d", 100, 4, { _monthsReconciled: false })])] });
var derivedNoDates = _curateDerivedMonths();
_resyncAllTierMonths();
R.monthsFullFlightNoDates = {
  derived: derivedNoDates,
  active: state.lineItems.map(function (l) { return [l.months, l._monthsReconciled]; }),
  snapshot: state.tiers[0].lineItems.map(function (l) { return [l.months, l._monthsReconciled]; })
};
state = mkState("full_flight", { lines: [L("a", 100, 3, { _monthsReconciled: false })] });
R.monthsFullFlightWithDates = { derived: _curateDerivedMonths() };
_resyncAllTierMonths();
R.monthsFullFlightWithDates.active = state.lineItems.map(function (l) { return [l.months, l._monthsReconciled]; });
// unchanged behavior for the other units: derived from the dates, a legacy "saved" line is left alone
state = mkState("month", { lines: [L("a", 100, 3, { _monthsReconciled: true }), L("b", 100, 3, { _monthsReconciled: false })] });
R.monthsMonthly = { derived: _curateDerivedMonths() };
_resyncAllTierMonths();
R.monthsMonthly.lines = state.lineItems.map(function (l) { return [l.months, l._monthsReconciled]; });
state = mkState("month", { start: "", end: "" });
R.monthsMonthly.noDates = _curateDerivedMonths();

// ---- 4. the payload normalizer ----
state = mkState("full_flight", { lines: [L("a", 100, 3, { monthly_allocations: { "2026-11": 50, "2026-12": 250 }, mb_off: true, _mbBaseline: 300 }),
                                         L("Bonus", 0, 3, { is_added_value: true }), L("ok", 700, 1, {})],
                                 merges: [["2026-11", "2026-12"]],
                                 tiers: [tierSnapshot("B", [L("b", 100, 2, { monthly_allocations: { "2026-11": 200 } })], { merges: [["x", "y"]] })] });
_normalizeFullFlightState();
R.normalizer = {
  // [months, allocations, mb_off, baseline dropped, reconciled, monthly_budget]
  lines: state.lineItems.map(function (l) { return [l.months, l.monthly_allocations, l.mb_off, l._mbBaseline === undefined, l._monthsReconciled, l.monthly_budget]; }),
  snapshotLines: state.tiers[0].lineItems.map(function (l) { return [l.months, l.monthly_allocations, l.monthly_budget]; }),
  merges: [state.activeTierPeriodMergeGroups, state.tiers[0].periodMergeGroups]
};
_normalizeFullFlightState();      // idempotent: a second pass must not fold again
R.normalizerAgain = state.lineItems.map(function (l) { return [l.months, l.monthly_budget]; });
state = mkState("month", { lines: [L("a", 100, 3, { monthly_allocations: { "2026-11": 300 }, mb_off: true })], merges: [["2026-11", "2026-12"]] });
_normalizeFullFlightState();
R.normalizerOtherUnit = { alloc: state.lineItems[0].monthly_allocations, months: state.lineItems[0].months, off: state.lineItems[0].mb_off, merges: state.activeTierPeriodMergeGroups };

// ---- 5. onTimeUnitChange: conversions ----
function runSwitch(st, newUnit, o) {
  o = o || {};
  state = st; resetDom(); __calls = []; __confirms = []; __alerts = []; __confirmAnswer = o.answer === undefined ? true : o.answer;
  document.getElementById("total-budget-target").value = o.target === undefined ? "" : o.target;
  onTimeUnitChange(newUnit);
  return { unit: state.timeUnit, confirms: __confirms.slice(), alerts: __alerts.slice(), calls: __calls.slice(), target: document.getElementById("total-budget-target").value };
}
function snap(st) {
  var all = [{ label: st.activeTierLabel, lines: st.lineItems }].concat(st.tiers.map(function (t) { return { label: t.label, lines: t.lineItems }; }));
  return all.map(function (t) { return { label: t.label, budgets: t.lines.map(function (l) { return l.monthly_budget; }), months: t.lines.map(function (l) { return l.months; }), flight: round2(total(t.lines)),
                                         allocations: t.lines.map(function (l) { return l.monthly_allocations; }) }; });
}
function plan() {
  return mkState("month", {
    lines: [L("Display", 2000, 2, { _monthsReconciled: true }), L("Email", 500, 2, { _monthsReconciled: true }), L("Bonus", 0, 2, { is_added_value: true, _monthsReconciled: true })],
    tiers: [tierSnapshot("B", [L("Display", 1000, 3, { _monthsReconciled: true })], { start: "2026-11-01", end: "2027-01-31" })]
  });
}

var st = plan();
var before = snap(st);
R.into = { before: before, result: runSwitch(st, "full_flight", { target: "2,500" }), after: snap(st) };
R.into.flagsActive = st.lineItems.map(function (l) { return [l._monthsReconciled, l.mb_off]; });

// back out again: month, same dates -> the exact original numbers
R.out = { result: runSwitch(st, "month"), after: snap(st) };

// out to weekly: each option divides by ITS OWN week count
st = plan(); runSwitch(st, "full_flight");
R.outWeek = { result: runSwitch(st, "week"), after: snap(st),
              counts: { A: _mbPeriodsBetween(_mbParseDate("2026-11-24"), _mbParseDate("2026-12-31"), "week").length,
                        B: _mbPeriodsBetween(_mbParseDate("2026-11-01"), _mbParseDate("2027-01-31"), "week").length } };

// out with an option that has no readable dates: its numbers are left alone and the confirm says so
st = mkState("full_flight", { start: "", end: "", lines: [L("Display", 4000, 1, { _monthsReconciled: true })],
                              tiers: [tierSnapshot("B", [L("Email", 3000, 1, { _monthsReconciled: true })], { start: "2026-11-01", end: "2027-01-31" })] });
R.outSkipped = { result: runSwitch(st, "month"), after: snap(st) };

// declined confirm: nothing changes
st = plan(); var beforeCancel = snap(st);
R.cancelled = { result: runSwitch(st, "full_flight", { answer: false }), after: snap(st), before: beforeCancel };

// a mix edit in progress blocks the switch
st = mkState("month", { lines: [L("Display", 2000, 2, { _monthsReconciled: true })], mixEdit: { base: 2000 } });
R.mixBlocked = { result: runSwitch(st, "full_flight"), after: snap(st) };

// same unit / unknown unit: ignored without a prompt
st = plan();
R.ignored = { same: runSwitch(st, "month"), bogus: runSwitch(st, "bogus") };

// allocations: switching into Full Flight clears them (and says so); a legacy week switch keeps the old text
st = plan();
st.lineItems[0].monthly_allocations = { "2026-11": 1000, "2026-12": 3000 };
st.lineItems[0].mb_off = true;
st.activeTierPeriodMergeGroups = [["2026-11", "2026-12"]];
R.intoWithAllocations = { result: runSwitch(st, "full_flight"), alloc: st.lineItems[0].monthly_allocations, merges: st.activeTierPeriodMergeGroups, off: st.lineItems[0].mb_off };
st = plan();
st.lineItems[0].monthly_allocations = { "2026-11": 1000, "2026-12": 3000 };
R.monthToWeek = { result: runSwitch(st, "week"), budgets: snap(st).map(function (t) { return t.budgets; }) };
st = plan();
R.monthToWeekNoAllocations = runSwitch(st, "quarter");
// a one-month-flight plan has nothing to multiply: into Full Flight with no allocations asks nothing
st = mkState("month", { start: "2026-11-03", end: "2026-11-27", lines: [L("Display", 2000, 1, { _monthsReconciled: true })] });
R.intoOneMonthFlight = { result: runSwitch(st, "full_flight"), after: snap(st) };

// ---- 6. labels / controls ----
var LABEL_IDS = ["budget-target-label", "monthly-total-label", "monthly-total-gross-label", "col-budget-header", "col-months-header",
                 "mb-step-title", "mb-step-name", "mb-mode-even-btn", "nav-step-6-label"];
R.labels = {};
["week", "month", "quarter", "full_flight"].forEach(function (u) {
  state = mkState(u); resetDom();
  _applyTimeUnitLabels();
  var t = texts(LABEL_IDS);
  t.thumb = document.getElementById("time-unit-slider-thumb").style.transform;
  t.active = __sliderOptions.map(function (e) { return e.classList.contains("active"); });
  t.select = document.getElementById("step02-billing-period").value;
  t.hintHidden = document.getElementById("step02-billing-period-hint").classList.contains("hidden");
  t.noteHidden = document.getElementById("mb-fullflight-note").classList.contains("hidden");
  t.ffMode = ["step-2", "step-4", "step-6"].map(function (id) { return document.getElementById(id).classList.contains("ff-mode"); });
  t.navTitle = document.getElementById("nav-step-6").title;
  R.labels[u] = t;
});
// the same function must tolerate a fresh page (no parsed request) and an unknown unit
state = mkState("full_flight"); state.parsed = null; resetDom(); _applyTimeUnitLabels();
R.labelsNoParsed = { select: document.getElementById("step02-billing-period").value, thumb: document.getElementById("time-unit-slider-thumb").style.transform };
state = mkState("bogus"); resetDom(); var w0 = __warns.length; _applyTimeUnitLabels();
R.labelsUnknown = { thumb: document.getElementById("time-unit-slider-thumb").style.transform, select: document.getElementById("step02-billing-period").value, warned: __warns.length > w0 };

// ---- 7. Step 06 ----
state = mkState("full_flight", { lines: [L("Display", 4000, 3, { _monthsReconciled: false }), L("Email", 1000, 1, {})] });
resetDom();
document.getElementById("mb-no-dates").classList.remove("hidden");
__realRenderMonthlyBreakdown();
R.step06FullFlight = {
  noteHidden: document.getElementById("mb-fullflight-note").classList.contains("hidden"),
  contentHidden: document.getElementById("mb-content").classList.contains("hidden"),
  noDatesHidden: document.getElementById("mb-no-dates").classList.contains("hidden"),
  masterHidden: document.getElementById("sel:.mb-master-toggle").classList.contains("hidden"),
  continueDisabled: document.getElementById("monthly-breakdown-continue-btn").disabled,
  detail: document.getElementById("mb-fullflight-detail").textContent,
  allocations: state.lineItems.map(function (l) { return [l.monthly_allocations, l._mbBaseline === undefined, l.months]; })
};
state = mkState("month", { start: "", end: "" });
document.getElementById("mb-fullflight-note").classList.remove("hidden");
__realRenderMonthlyBreakdown();
R.step06Monthly = { noteHidden: document.getElementById("mb-fullflight-note").classList.contains("hidden"),
                    noDatesHidden: document.getElementById("mb-no-dates").classList.contains("hidden") };

// ---- 8. the Gamma outline ----
function gamma(unit, flightMonths) {
  state = mkState(unit, { lines: [L("Display", unit === "full_flight" ? 4000 : 2000, unit === "full_flight" ? 1 : 2, {})], totalMonths: flightMonths });
  state.parsed.client_name = "Acme"; state.parsed.start_date = "2026-11-24"; state.parsed.end_date = "2026-12-31";
  return buildGammaOutline();
}
R.gamma = { full_flight: gamma("full_flight", 2), month: gamma("month", 2) };

// ---- 9. admin analytics label ----
R.adminLabels = ["full_flight", "week", "month", "quarter", "something_else", null].map(_formatTimeUnitLabel);

// ---- 10. the lossless round trip through Weekly / Monthly / Quarterly ----
function ffLines(spec) {
  return spec.map(function (s) { return L(s[0], s[1], 1, { _monthsReconciled: true, is_added_value: !!s[2] }); });
}
function ffPlan2() {
  return mkState("full_flight", {
    lines: ffLines([["Display", 1000.01], ["Email", 333.33], ["Video", 7777.77], ["Bonus", 0, true]]),
    tiers: [tierSnapshot("B", ffLines([["Display", 2500.55], ["Social", 99.99]]), { start: "2026-10-05", end: "2027-02-14" })]
  });
}
function budgetsOf(st) { return snap(st).map(function (t) { return { label: t.label, budgets: t.budgets, months: t.months }; }); }
function allLines(st) { return st.lineItems.concat(st.tiers.reduce(function (acc, t) { return acc.concat(t.lineItems); }, [])); }
function memosLeft(st) { return allLines(st).some(function (l) { return l._ffBase !== undefined; }); }

R.roundTrip = {};
["week", "month", "quarter"].forEach(function (u) {
  var st = ffPlan2();
  var original = budgetsOf(st);
  var out = runSwitch(st, u, { target: "12,000" });
  var mid = budgetsOf(st);
  var memoWhileOut = memosLeft(st);
  var back = runSwitch(st, "full_flight", { target: out.target });
  R.roundTrip[u] = { original: original, mid: mid, memoWhileOut: memoWhileOut, back: budgetsOf(st), memoAfter: memosLeft(st),
                     outTarget: out.target, backTarget: back.target, backConfirm: back.confirms[0] || null,
                     unit: state.timeUnit, flags: allLines(st).map(function (l) { return [l.months, l._monthsReconciled]; }),
                     targetMemo: state._ffTargetBase };
});

// A fuzz of the same thing: random flights for the campaign and for option B, random cent-precise budgets, 1-3 round
// trips through a random unit in a row — every one must give back the exact numbers it started with.
var fseed = 20261;
function rnd() { fseed = (fseed * 16807) % 2147483647; return fseed / 2147483647; }
function isoPlus(isoDate, days) { var d = new Date(isoDate + "T00:00:00Z"); d.setUTCDate(d.getUTCDate() + days); return iso(d); }
var fuzzBad = [], fuzzRuns = 0;
for (var it = 0; it < 80; it++) {
  var s1 = isoPlus("2026-01-01", Math.floor(rnd() * 330)), e1 = isoPlus(s1, 2 + Math.floor(rnd() * 300));
  var s2 = isoPlus("2026-01-01", Math.floor(rnd() * 330)), e2 = isoPlus(s2, 2 + Math.floor(rnd() * 300));
  var specs = [], nLines = 1 + Math.floor(rnd() * 4);
  for (var k = 0; k < nLines; k++) specs.push(["P" + k, Math.round(rnd() * 5000000) / 100 + 0.01]);
  var stF = mkState("full_flight", { start: s1, end: e1, lines: ffLines(specs),
                                     tiers: [tierSnapshot("B", ffLines([["Q", Math.round(rnd() * 5000000) / 100 + 0.01]]), { start: s2, end: e2 })] });
  var orig = JSON.stringify(budgetsOf(stF));
  var hops = 1 + Math.floor(rnd() * 3);
  for (var h = 0; h < hops; h++) {
    var u2 = ["week", "month", "quarter"][Math.floor(rnd() * 3)];
    runSwitch(stF, u2);
    runSwitch(stF, "full_flight");
    fuzzRuns++;
    if (JSON.stringify(budgetsOf(stF)) !== orig || memosLeft(stF)) {
      fuzzBad.push({ s1: s1, e1: e1, s2: s2, e2: e2, unit: u2, orig: orig, now: JSON.stringify(budgetsOf(stF)) });
      break;
    }
  }
}
R.fuzz = { runs: fuzzRuns, bad: fuzzBad.slice(0, 3) };

// Not a straight return -> no memo: the planner edited a line (its per-period budget x the period count), or the dates moved
// (the per-period budget x the NEW period count), or typed a different Total budget (scaled like any other conversion).
var stE = ffPlan2();
runSwitch(stE, "week");
var weeksA = stE.lineItems[0].months;
stE.lineItems[0].monthly_budget = 250;
runSwitch(stE, "full_flight");
R.editedLine = { weeksA: weeksA, active: stE.lineItems.map(function (l) { return l.monthly_budget; }), optionB: stE.tiers[0].lineItems.map(function (l) { return l.monthly_budget; }) };

var stD = ffPlan2();
runSwitch(stD, "month");
var perMonth = stD.lineItems.map(function (l) { return l.monthly_budget; });
var monthsA = stD.lineItems[0].months;
stD.parsed.end_date = "2027-01-31";
runSwitch(stD, "full_flight");
R.changedDates = { perMonth: perMonth, monthsBefore: monthsA, active: stD.lineItems.map(function (l) { return l.monthly_budget; }), optionB: stD.tiers[0].lineItems.map(function (l) { return l.monthly_budget; }) };

var stT = ffPlan2();
var t1 = runSwitch(stT, "week", { target: "12,000" });
var perWeekSum = stT.lineItems.reduce(function (s, l) { return s + l.monthly_budget; }, 0);
var t2 = runSwitch(stT, "full_flight", { target: "9,000" });
R.editedTarget = { first: t1.target, second: t2.target, perWeekSum: perWeekSum, flightSum: 1000.01 + 333.33 + 7777.77, memoAfter: state._ffTargetBase };

// Switching INTO Full Flight multiplies (exact), so a plain unit -> Full Flight needs no memo and leaves none behind.
var stP = plan();
var p1 = runSwitch(stP, "full_flight", { target: "2,500" });
R.intoLeavesNoMemo = { memos: memosLeft(stP), targetMemo: state._ffTargetBase, target: p1.target };

// ---- 11. an honest confirm: rounding to the cent can move the total, and the text says so ----
var weeksCount = _mbPeriodsBetween(_mbParseDate("2026-11-24"), _mbParseDate("2026-12-31"), "week").length;
function findTotal(count, wantMoved) {
  for (var t = 1000; t < 1300; t++) {
    var moved = Math.abs(round2(round2(t / count) * count) - t) >= 0.005;
    if (moved === wantMoved) return t;
  }
  return null;
}
function honest(total) {
  var st = mkState("full_flight", { lines: [L("Display", total, 1, { _monthsReconciled: true })] });
  var out = runSwitch(st, "week");
  var perWeek = st.lineItems[0].monthly_budget;
  var back = runSwitch(st, "full_flight");
  return { total: total, perWeek: perWeek, outConfirm: out.confirms[0], backConfirm: back.confirms[0] || null, restored: st.lineItems[0].monthly_budget };
}
var tMoved = findTotal(weeksCount, true), tExact = findTotal(weeksCount, false);
R.honest = { weeks: weeksCount, moved: honest(tMoved), exact: honest(tExact) };

// ---- 12. options with no readable dates stay flagged, never silently multiplied ----
st = mkState("full_flight", { start: "", end: "", lines: [L("Display", 4000, 1, { _monthsReconciled: true }), L("Bonus", 0, 1, { is_added_value: true, _monthsReconciled: true })],
                              tiers: [tierSnapshot("B", [L("Email", 3000, 1, { _monthsReconciled: true })], { start: "2026-11-01", end: "2027-01-31" })] });
function lineState(l) { return [l.monthly_budget, l.months, l._monthsReconciled]; }
var skipOut = runSwitch(st, "month");
var skipAfter = { active: st.lineItems.map(lineState), optionB: st.tiers[0].lineItems.map(lineState) };
st.parsed.start_date = "2026-11-24"; st.parsed.end_date = "2026-12-31";
_resyncAllTierMonths();
R.skippedFlags = { confirm: skipOut.confirms[0], after: skipAfter, afterDates: { active: st.lineItems.map(lineState), optionB: st.tiers[0].lineItems.map(lineState) } };

// ---- 13. Step 02's Full Flight note and relabeled budget fields ----
function noteFor(unit) {
  state = mkState(unit); resetDom();
  _refreshStep02FlightNote();
  var h = document.getElementById("step02-billing-period-hint");
  return { hidden: h.classList.contains("hidden"), text: h.textContent,
           label: document.getElementById("step02-budget-label").textContent,
           tierNote: document.getElementById("step02-tier-note").textContent };
}
R.note = { week: noteFor("week"), month: noteFor("month"), quarter: noteFor("quarter"), full_flight: noteFor("full_flight") };
state = mkState("full_flight"); resetDom(); _refreshStep02FlightNote();
state.timeUnit = "month"; _refreshStep02FlightNote();
R.noteBackToMonth = { hidden: document.getElementById("step02-billing-period-hint").classList.contains("hidden"),
                      label: document.getElementById("step02-budget-label").textContent,
                      tierNote: document.getElementById("step02-tier-note").textContent };

// ---- 14. the SOV helper line ----
function sovHelper(unit, o, budget, pct, lid) {
  state = mkState(unit, o); resetDom();
  state.lineItems = [L("Display", budget, 1, {})];
  var id = lid || "li_Display";
  applySovDisplay(id, pct);
  var helper = document.getElementById("sov-helper-" + id), badge = document.getElementById("sov-badge-" + id);
  return { helper: helper.textContent, helperClass: helper.className, badge: badge.textContent, badgeClass: badge.className };
}
R.sovHelper = {
  month: sovHelper("month", {}, 12000, 85),
  week: sovHelper("week", {}, 12000, 85),
  ffTwoMonths: sovHelper("full_flight", {}, 12000, 62.4),
  ffFourMonths: sovHelper("full_flight", { start: "2026-09-28", end: "2026-12-12" }, 10000, 91),
  ffOneMonth: sovHelper("full_flight", { tierStart: "2026-11-03", tierEnd: "2026-11-27" }, 12000, 62.4),
  ffZeroBudget: sovHelper("full_flight", {}, 0, 62.4),
  ffUnknownLine: sovHelper("full_flight", {}, 12000, 62.4, "li_Other"),
  ffNull: sovHelper("full_flight", {}, 12000, null)
};

// ---- 15. the Min column's tooltip ----
function minTitle(unit) {
  state = mkState(unit); resetDom();
  var h = document.getElementById("col-min-header"); h.title = "stale";
  _refreshMinHeaderTitle();
  return h.title;
}
R.minTitle = { week: minTitle("week"), month: minTitle("month"), quarter: minTitle("quarter"), full_flight: minTitle("full_flight") };

// ---- 16. a stale line (months > 1) is folded wherever Full Flight re-derives months ----
state = mkState("full_flight", { lines: [L("a", 100, 3, { _monthsReconciled: false }), L("Bonus", 0, 3, { is_added_value: true }), L("ok", 700, 1, {}),
                                         L("alloc", 100, 3, { monthly_allocations: { "2026-11": 100, "2026-12": 200 }, _monthsReconciled: true })],
                                 tiers: [tierSnapshot("B", [L("b", 50, 4, { _monthsReconciled: false })])] });
_resyncAllTierMonths();
R.resyncFold = {
  active: state.lineItems.map(function (l) { return [l.monthly_budget, l.months, l._monthsReconciled, l.monthly_budget * l.months]; }),
  optionB: state.tiers[0].lineItems.map(function (l) { return [l.monthly_budget, l.months, l._monthsReconciled]; })
};
var foldLine = L("x", 100.004, 3, {});
_foldLineIntoOnePeriod(foldLine);
var foldAgain = L("x", 100, 1, {}); _foldLineIntoOnePeriod(foldAgain);
var foldFree = L("y", 0, 3, { is_added_value: true }); _foldLineIntoOnePeriod(foldFree);
var foldBad = L("z", 100, 0, {}); _foldLineIntoOnePeriod(foldBad);
var foldNaN = L("w", undefined, 3, {}); _foldLineIntoOnePeriod(foldNaN);
R.foldLine = { cents: [foldLine.monthly_budget, foldLine.months], again: [foldAgain.monthly_budget, foldAgain.months],
               addedValue: [foldFree.monthly_budget, foldFree.months], zeroMonths: [foldBad.monthly_budget, foldBad.months], noBudget: [foldNaN.monthly_budget, foldNaN.months] };

return JSON.stringify(R);
"""
    body = body.replace("__CASES__", json.dumps(PERIOD_CASES))
    return prelude + "\n" + _app_js_bundle() + "\n" + helpers + "\n" + body


# --------------------------------------------------------------------------------------------------------------------
# Python-side expectations
# --------------------------------------------------------------------------------------------------------------------

def _py_plain(p):
    return {
        "key": p["key"], "label": p["label"], "date_range_label": p["date_range_label"],
        "start": p["start"].isoformat(), "end": p["end"].isoformat(),
        "days_in_month": p["days_in_month"], "active_days": p["active_days"], "period_count": p["period_count"],
    }


def _py_periods(case, granularity):
    s, e = date.fromisoformat(case[0]), date.fromisoformat(case[1])
    return [_py_plain(p) for p in periods_between(s, e, granularity)]


# --------------------------------------------------------------------------------------------------------------------
# 1. The period builder mirrors Python byte for byte
# --------------------------------------------------------------------------------------------------------------------

def test_js_full_flight_period_is_identical_to_python_for_every_case(js):
    for row in js["periods"]:
        expected = _py_periods(row["input"], "full_flight")
        assert row["full_flight"] == expected, row["input"]
        assert row["dispatched"] == expected, row["input"]
        assert len(expected) == 1


def test_full_flight_period_labels_for_the_reference_flights(js):
    by_input = {tuple(r["input"]): r["full_flight"][0] for r in js["periods"]}
    nov_dec = by_input[("2026-11-24", "2026-12-31")]
    assert nov_dec["key"] == "full_flight"
    assert nov_dec["label"] == "November–December 2026"
    assert nov_dec["date_range_label"] == "Nov 24 – Dec 31, 2026"
    assert nov_dec["period_count"] == 1                    # two calendar months, but ONE period
    assert (nov_dec["start"], nov_dec["end"]) == ("2026-11-24", "2026-12-31")
    one_month = by_input[("2026-11-03", "2026-11-27")]
    assert one_month["label"] == "November 2026"          # NOT "November–November 2026"
    assert one_month["period_count"] == 1
    cross_year = by_input[("2026-12-15", "2027-01-20")]
    assert cross_year["label"] == "December 2026–January 2027"
    assert cross_year["period_count"] == 1
    swapped = by_input[("2026-12-31", "2026-11-24")]
    assert swapped == nov_dec                              # swapped dates are put in order, like the Python builder
    four = by_input[("2026-09-28", "2026-12-12")]
    assert four["label"] == "September–December 2026" and four["period_count"] == 1
    assert by_input[("2026-11-24", "2026-11-24")]["period_count"] == 1


def test_full_flight_key_is_a_constant_without_a_plus(js):
    assert js["constants"]["FULL_FLIGHT"] == "full_flight"
    assert js["constants"]["FULL_FLIGHT_KEY"] == "full_flight"
    assert js["constants"]["TIME_UNITS"] == ["week", "month", "quarter", "full_flight"]
    for row in js["periods"]:
        assert row["full_flight"][0]["key"] == "full_flight" and "+" not in row["full_flight"][0]["key"]


def test_week_month_quarter_periods_still_match_python(js):
    for row in js["periods"]:
        for granularity in ("week", "month", "quarter"):
            assert row[granularity] == _py_periods(row["input"], granularity), (row["input"], granularity)


def test_no_dates_gives_no_period_and_unknown_granularity_is_not_silent(js):
    assert js["noDates"] == {"nulls": 0, "garbage": 0}
    unknown = js["unknownGranularity"]
    assert unknown["keys"] == ["2026-11", "2026-12"]       # the server's own fallback (months)...
    assert unknown["warned"] >= 1                          # ...but never silently
    assert unknown["monthWarned"] == 0                     # a real "month" doesn't warn


def test_normalize_time_unit_accepts_full_flight_and_defaults_everything_else_to_month(js):
    assert js["normalizeTimeUnit"] == ["full_flight", "full_flight", "week", "month", "month", "month", "month", "month"]


# --------------------------------------------------------------------------------------------------------------------
# 2. Unit-keyed math
# --------------------------------------------------------------------------------------------------------------------

def test_minimum_scale_is_one_period_in_full_flight(js):
    s = js["scale"]
    assert s["week"]["scale"] == pytest.approx(12 / 52) and s["month"]["scale"] == 1 and s["quarter"]["scale"] == 3
    # Full Flight is ONE period: one monthly minimum, however many calendar months Nov 24 - Dec 31 touches ...
    assert s["full_flight"]["scale"] == 1
    # ... in Step 06's per-period check too (the period's own period_count is 1)
    assert s["full_flight"]["periodMin"] == 1000 * granularity_scale("full_flight") == 1000
    assert s["month"]["periodMin"] == 1000 and s["quarter"]["periodMin"] == 3000
    assert s["week"]["periodMin"] == pytest.approx(1000 * 12 / 52)
    # ... and whatever the flight's dates: one month, none at all, four months, an option's own override, nothing set
    assert js["scaleFullFlightAnyDates"] == [{"scale": 1, "sov": 6000}] * 4


def test_sov_monthly_equivalent_is_the_whole_flight_budget_in_full_flight(js):
    sov = js["sov"]
    assert sov["week"] == 24000 and sov["month"] == 6000 and sov["quarter"] == pytest.approx(2000)
    assert sov["full_flight"] == 6000                      # compared with the monthly ceiling exactly as it stands


def test_budget_conversion_math(js):
    c = js["convertMath"]
    assert c == {"into": 6000, "intoOne": 680, "out": 2000, "outCents": 1666.67, "zero": 0}


# --------------------------------------------------------------------------------------------------------------------
# 3. months == 1
# --------------------------------------------------------------------------------------------------------------------

def test_months_are_one_in_full_flight_even_without_dates_and_for_legacy_saved_lines(js):
    nd = js["monthsFullFlightNoDates"]
    assert nd["derived"] == 1
    assert nd["active"] == [[1, True], [1, True], [1, True]]
    assert nd["snapshot"] == [[1, True]]                   # every OTHER option too
    wd = js["monthsFullFlightWithDates"]
    assert wd["derived"] == 1 and wd["active"] == [[1, True]]   # not the flight's calendar-month count (2)


def test_other_units_still_derive_months_from_the_dates(js):
    m = js["monthsMonthly"]
    assert m["derived"] == 2
    assert m["lines"] == [[2, True], [3, False]]           # a legacy "saved 3" line is left for the planner's Fix
    assert m["noDates"] is None


def test_payload_normalizer_forces_the_full_flight_contract_on_every_line_of_every_option(js):
    n = js["normalizer"]
    # A line that still carried months = 3 is FOLDED into one period with its total kept (100 x 3 = 300) — exactly the
    # server's normalize_full_flight_lines — instead of being cut to 100 (the first version only set months to 1, so a
    # stale saved state reopened at a fraction of what the export had printed). Added Value stays $0; a line already on
    # one period is untouched; mb_off (the planner's opt-out) is NOT reset.
    assert n["lines"] == [[1, None, True, True, True, 300], [1, None, None, True, True, 0], [1, None, None, True, True, 700]]
    assert n["snapshotLines"] == [[1, None, 200]]                       # option B: 100 x 2
    assert n["merges"] == [[], []]
    assert js["normalizerAgain"] == [[1, 300], [1, 0], [1, 700]]        # idempotent
    other = js["normalizerOtherUnit"]
    assert other == {"alloc": {"2026-11": 300}, "months": 3, "off": True, "merges": [["2026-11", "2026-12"]]}


# --------------------------------------------------------------------------------------------------------------------
# 4. Switching the billing period
# --------------------------------------------------------------------------------------------------------------------

def test_switching_into_full_flight_preserves_every_lines_flight_total(js):
    r = js["into"]
    before, after = r["before"], r["after"]
    assert before[0]["flight"] == 5000 and before[1]["flight"] == 3000
    assert after[0]["budgets"] == [4000, 1000, 0] and after[0]["months"] == [1, 1, 1]
    assert after[1]["budgets"] == [3000] and after[1]["months"] == [1]          # option B: 1,000 x 3 months
    assert [t["flight"] for t in after] == [t["flight"] for t in before]       # totals are the plain sum, unchanged
    assert after[0]["allocations"] == [None, None, None]
    assert r["result"]["unit"] == "full_flight"
    assert r["result"]["calls"] == ["renderLineItems", "updateTotals", "renderMonthlyBreakdown"]
    # mb_off is the planner's own Step 06 opt-out: switching in never touches it (these lines never had it set)
    assert js["into"]["flagsActive"] == [[True, None]] * 3
    # the typed Total budget target moves with the plan (2,500/month x 2 months)
    assert r["result"]["target"] == "5,000"


def test_the_into_full_flight_confirm_explains_the_conversion_with_a_number_from_the_plan(js):
    confirms = js["into"]["result"]["confirms"]
    assert len(confirms) == 1
    text = confirms[0]
    assert text.startswith("Switch the billing period to Full Flight?")
    assert "Each line's budget becomes its flight total" in text
    assert "Display: $2,000/month × 2 months = $4,000" in text
    assert "Totals stay the same" in text
    assert text.endswith("Continue?")


def test_switching_back_out_restores_the_original_numbers_and_re_derives_months(js):
    r = js["out"]
    original = js["into"]["before"]
    assert r["after"] == original                          # same dates -> exactly the numbers it started with
    assert r["result"]["unit"] == "month"
    text = r["result"]["confirms"][0]
    assert "Switch the billing period to Monthly?" in text
    assert "Each line's flight total is divided across the months of its option's flight" in text
    assert "Display: $4,000 ÷ 2 months = $2,000/month" in text
    assert "Totals stay the same" in text
    assert r["result"]["target"] == ""                     # nothing typed -> nothing invented


def test_out_of_full_flight_each_option_divides_by_its_own_period_count(js):
    r = js["outWeek"]
    a, b = r["counts"]["A"], r["counts"]["B"]
    assert a != b
    after = {t["label"]: t for t in r["after"]}
    assert after["A"]["months"] == [a, a, a] and after["B"]["months"] == [b]
    assert after["A"]["budgets"][:2] == [round(4000 / a, 2), round(1000 / a, 2)]
    assert after["B"]["budgets"] == [round(3000 / b, 2)]
    # flight totals preserved to within the cent-rounding of the per-period figure
    assert abs(after["A"]["flight"] - 5000) <= 0.01 * a * 2
    assert abs(after["B"]["flight"] - 3000) <= 0.01 * b


def test_an_option_without_readable_dates_keeps_its_numbers_and_the_confirm_says_so(js):
    r = js["outSkipped"]
    after = {t["label"]: t for t in r["after"]}
    assert after["A"]["budgets"] == [4000] and after["A"]["months"] == [1]      # no dates: untouched
    assert after["B"]["budgets"] == [1000] and after["B"]["months"] == [3]      # B's own dates: 3 months
    text = r["result"]["confirms"][0]
    assert "no readable flight dates" in text and "left as they are" in text


def test_a_declined_confirm_changes_nothing(js):
    r = js["cancelled"]
    assert r["result"]["unit"] == "month"
    assert r["after"] == r["before"]
    assert r["result"]["calls"] == []
    assert len(r["result"]["confirms"]) == 1


def test_a_mix_edit_in_progress_blocks_the_switch(js):
    r = js["mixBlocked"]
    assert r["result"]["unit"] == "month"
    assert len(r["result"]["alerts"]) == 1 and "mix edit" in r["result"]["alerts"][0]
    assert r["result"]["confirms"] == [] and r["result"]["calls"] == []


def test_same_or_unknown_unit_is_ignored_silently(js):
    for key in ("same", "bogus"):
        r = js["ignored"][key]
        assert r["unit"] == "month" and r["confirms"] == [] and r["alerts"] == [] and r["calls"] == []


def test_switching_into_full_flight_clears_allocations_and_merges_but_keeps_the_planners_mb_off_opt_out(js):
    r = js["intoWithAllocations"]
    # mb_off (the planner's "don't use the breakdown for this line" choice) survives a pass through Full Flight: it is
    # meaningless there (Step 06 is a note, the server ignores it) but resetting it would silently re-enable every
    # breakdown the planner had turned off the moment they return to Weekly/Monthly/Quarterly.
    assert r["alloc"] is None and r["merges"] == [] and r["off"] is True
    assert "Every option's Step 06 breakdown and combined periods will be cleared" in r["result"]["confirms"][0]


def test_week_month_quarter_switches_keep_the_legacy_text_and_never_convert_dollars(js):
    r = js["monthToWeek"]
    assert r["result"]["confirms"] == [
        "Switching to Weekly will clear every budget option's Step 06 breakdown and combined periods — "
        "the old month-based numbers won't carry over. Curated budgets and product mix are untouched either way. Continue?"
    ]
    assert r["budgets"] == [[2000, 500, 0], [1000]]        # dollars untouched
    plain = js["monthToWeekNoAllocations"]
    assert plain["unit"] == "quarter" and plain["confirms"] == []     # nothing to clear -> no prompt, as before


def test_into_full_flight_without_anything_to_convert_or_clear_asks_nothing(js):
    r = js["intoOneMonthFlight"]
    assert r["result"]["unit"] == "full_flight" and r["result"]["confirms"] == []
    assert r["after"][0]["budgets"] == [2000] and r["after"][0]["months"] == [1]


# --------------------------------------------------------------------------------------------------------------------
# 5. Labels and controls
# --------------------------------------------------------------------------------------------------------------------

def test_day_to_day_labels_are_unchanged(js):
    expected = {
        "week": ("Weekly", "Weeks", "Weekly"),
        "month": ("Monthly", "Months", "Monthly"),
        "quarter": ("Quarterly", "Quarters", "Quarterly"),
    }
    for unit, (adj, plural, _) in expected.items():
        t = js["labels"][unit]
        assert t["budget-target-label"] == f"Total {adj.lower()} budget"
        assert t["monthly-total-label"] == f"{adj} total"
        assert t["monthly-total-gross-label"] == f"{adj} total (Gross)"
        assert t["col-budget-header"] == f"{adj} $"
        assert t["col-months-header"] == plural
        assert t["mb-step-title"] == f"{adj} breakdown"
        assert t["mb-step-name"] == f"{adj} Breakdown"
        assert t["mb-mode-even-btn"] == f"Even across {plural.lower()}"
        assert t["nav-step-6-label"] == f" {adj}" and t["navTitle"] == adj
        assert t["ffMode"] == [False, False, False]
        assert t["hintHidden"] is True and t["noteHidden"] is True


def test_full_flight_has_explicit_copy(js):
    t = js["labels"]["full_flight"]
    assert t["budget-target-label"] == "Total full-flight budget"
    assert t["monthly-total-label"] == "Full-Flight total"
    assert t["monthly-total-gross-label"] == "Full-Flight total (Gross)"
    assert t["col-budget-header"] == "Full-Flight $"
    assert t["nav-step-6-label"] == " Full-Flight"
    assert t["mb-step-title"] == "Full-Flight billing"
    assert t["mb-mode-even-btn"] == "Even split"           # not "Even across flights"
    assert t["ffMode"] == [True, True, True]
    assert t["hintHidden"] is False and t["noteHidden"] is False
    # no leaked "Full_flight" / "Flights" interpolation anywhere in the copy
    for key in ("budget-target-label", "monthly-total-label", "col-budget-header", "mb-step-title", "nav-step-6-label"):
        assert "_" not in t[key]


def test_every_billing_period_control_follows_state_time_unit(js):
    for index, unit in enumerate(["week", "month", "quarter", "full_flight"]):
        t = js["labels"][unit]
        assert t["select"] == unit                          # Step 02's select
        assert t["thumb"] == f"translateX({index * 100}%)"  # Step 04's thumb sits on option `index`
        assert t["active"] == [i == index for i in range(4)]
    assert js["labelsNoParsed"] == {"select": "full_flight", "thumb": "translateX(300%)"}   # runs before any parse
    unknown = js["labelsUnknown"]
    assert unknown["thumb"] == "translateX(100%)" and unknown["select"] == "month" and unknown["warned"]


# --------------------------------------------------------------------------------------------------------------------
# 6. Step 06
# --------------------------------------------------------------------------------------------------------------------

def test_step_06_is_a_read_only_note_in_full_flight(js):
    r = js["step06FullFlight"]
    assert r["noteHidden"] is False
    assert r["contentHidden"] is True and r["noDatesHidden"] is True and r["masterHidden"] is True
    assert r["continueDisabled"] is False
    assert "November–December 2026" in r["detail"] and "Nov 24 – Dec 31, 2026" in r["detail"]
    # Display carries a stale months = 3: it is FOLDED into one period (4,000 x 3 = 12,000, total kept — exactly what the
    # server does), not silently cut to 4,000; Email is already one period. (The first version dropped the x3.)
    assert "$13,000" in r["detail"] and "2 lines" in r["detail"]
    # returned before any reconcile/auto-create: no allocation was invented, and months were forced to 1
    assert r["allocations"] == [[None, True, 1], [None, True, 1]]


def test_leaving_full_flight_restores_the_normal_step_06(js):
    r = js["step06Monthly"]
    assert r["noteHidden"] is True
    assert r["noDatesHidden"] is False                      # the normal no-dates notice, not the Full Flight note


# --------------------------------------------------------------------------------------------------------------------
# 7. Outputs and admin
# --------------------------------------------------------------------------------------------------------------------

def test_gamma_outline_has_no_monthly_arithmetic_in_full_flight(js):
    ff, month = js["gamma"]["full_flight"], js["gamma"]["month"]
    assert "$4,000 flat for the full flight" in ff
    assert "/mo ×" not in ff and "(2 months)" not in ff
    assert "(billed as one full-flight period)" in ff
    assert "Option total: $4,000" in ff
    assert "$2,000/mo × 2mo" in month and "(2 months)" in month and "Option total: $4,000" in month


def test_admin_analytics_label_reads_full_flight(js):
    assert js["adminLabels"] == ["Full Flight", "Week", "Month", "Quarter", "Something else", "Unknown"]


# --------------------------------------------------------------------------------------------------------------------
# 8. The lossless round trip through Weekly / Monthly / Quarterly
# --------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("unit", ["week", "month", "quarter"])
def test_full_flight_out_and_straight_back_restores_every_flight_total_to_the_cent(js, unit):
    r = js["roundTrip"][unit]
    # Leaving divides each flight total into equal per-period cents (1,000.01 / 6 weeks = 166.67), which cannot be
    # inverted by multiplying — so each converted line remembers the exact flight total it came from. Nothing edited,
    # same dates: every line of EVERY option comes back to exactly what it was, and no memo is left behind.
    assert r["back"] == r["original"]
    assert r["unit"] == "full_flight"
    assert r["memoWhileOut"] is True and r["memoAfter"] is False
    assert r["flags"] == [[1, True]] * 6                    # months 1 and "reconciled" on every line of both options
    assert r["mid"][1]["budgets"] != r["original"][1]["budgets"]     # option B really was converted on the way out
    if unit != "quarter":                                   # Nov 24 - Dec 31 is ONE calendar quarter: nothing to divide
        assert r["mid"][0]["budgets"] != r["original"][0]["budgets"]
    # the Total budget input comes back exactly too (and the memo that did it is dropped)
    assert r["backTarget"] == "12,000" and r["targetMemo"] is None
    if unit != "quarter":
        assert r["outTarget"] != "12,000"
    assert "Each line goes back to the exact flight total it had before you left Full Flight" in r["backConfirm"]


def test_repeated_round_trips_never_drift(js):
    fuzz = js["fuzz"]
    assert fuzz["runs"] >= 80                              # random flights x cent-precise budgets x 1-3 trips each
    assert fuzz["bad"] == []


def test_an_edited_line_falls_back_to_the_per_period_budget_times_the_periods(js):
    r = js["editedLine"]
    weeks = r["weeksA"]
    assert weeks == 6
    # the planner typed $250/week on Display: that is what a line billed for 6 weeks is, so it becomes 1,500 ...
    assert r["active"][0] == 250 * weeks
    # ... while every untouched line (and the whole of option B) still gets its exact original back
    assert r["active"][1:] == [333.33, 7777.77, 0]
    assert r["optionB"] == [2500.55, 99.99]


def test_changed_dates_fall_back_to_the_per_period_budget_times_the_new_period_count(js):
    r = js["changedDates"]
    assert r["monthsBefore"] == 2
    # the campaign flight grew from 2 to 3 calendar months while out of Full Flight: a monthly budget of $500.01 is now
    # a $1,500.03 flight (not the old 1,000.01 total handed back as if nothing happened)
    for per_month, now in zip(r["perMonth"], r["active"]):
        assert now == pytest.approx(per_month * 3, abs=0.0101)
    assert r["active"][0] != 1000.01
    assert r["optionB"] == [2500.55, 99.99]                 # option B's own flight dates did not move: restored exactly


def test_a_retyped_total_budget_is_converted_not_overwritten_by_the_memo(js):
    r = js["editedTarget"]
    assert r["first"] == "2,000.02" and r["second"] != "12,000"
    expected = 9000 * r["flightSum"] / r["perWeekSum"]      # the typed target is in WEEKLY dollars: scaled by what the plan moved by
    assert float(r["second"].replace(",", "")) == pytest.approx(expected, abs=0.011)
    assert r["memoAfter"] is None


def test_going_into_full_flight_from_a_unit_multiplies_and_keeps_no_memo(js):
    r = js["intoLeavesNoMemo"]
    assert r == {"memos": False, "targetMemo": None, "target": "5,000"}


# --------------------------------------------------------------------------------------------------------------------
# 9. The confirm tells the truth about rounding
# --------------------------------------------------------------------------------------------------------------------

def test_the_confirm_says_plainly_when_rounding_to_the_cent_moves_the_total(js):
    h = js["honest"]
    weeks = h["weeks"]
    assert weeks == 6
    moved = h["moved"]
    # 1,000 / 6 weeks = 166.67 -> 6 x 166.67 = 1,000.02: "Totals stay the same, to the cent." would be untrue
    assert moved["perWeek"] == 166.67
    text = moved["outConfirm"]
    assert "Totals stay the same" not in text
    m = re.search(r"Rounding each week to the cent moves the plan total by \$([\d,.]+) \(\$([\d,.]+) → \$([\d,.]+)\); "
                  r"switching straight back restores the original\.", text)
    assert m, text
    diff, before, after = (float(g.replace(",", "")) for g in m.groups())
    assert before == moved["total"] and after == pytest.approx(round(moved["perWeek"] * weeks, 2), abs=0.005)
    assert diff == pytest.approx(abs(after - before), abs=0.005) and diff > 0
    assert f"÷ {weeks} weeks = $166.67/week" in text
    # ... and the straight way back undoes it, also in plain words
    back = moved["backConfirm"]
    assert "Each line goes back to the exact flight total it had before you left Full Flight" in back
    assert "(the earlier rounding to cents is undone)" in back and "Totals stay the same" not in back
    assert moved["restored"] == moved["total"]


def test_the_confirm_keeps_totals_stay_the_same_when_they_really_do(js):
    exact = js["honest"]["exact"]
    assert exact["total"] % js["honest"]["weeks"] == 0
    assert "Totals stay the same." in exact["outConfirm"] and "Rounding each" not in exact["outConfirm"]
    assert "Totals stay the same." in exact["backConfirm"] and "undone" not in exact["backConfirm"]
    assert exact["restored"] == exact["total"]


# --------------------------------------------------------------------------------------------------------------------
# 10. Options without readable dates
# --------------------------------------------------------------------------------------------------------------------

def test_options_without_dates_are_flagged_and_never_silently_multiplied_once_dates_arrive(js):
    r = js["skippedFlags"]
    assert ("One option has no readable flight dates, so its budgets can't be divided across periods and are left as they are"
            " — once the dates are set they are flagged for review (not multiplied automatically).") in r["confirm"]
    # [monthly_budget, months, _monthsReconciled]: the money line is left alone AND flagged "saved"; the $0 Added Value
    # line has nothing to flag; the other option divided normally (3,000 / 3 months).
    assert r["after"] == {"active": [[4000, 1, False], [0, 1, True]], "optionB": [[1000, 3, True]]}
    # dates typed later: the flagged line keeps its dollars and months (so the planner sees "1 saved / Fix"),
    # the unflagged $0 line follows the calendar
    assert r["afterDates"] == {"active": [[4000, 1, False], [0, 2, True]], "optionB": [[1000, 3, True]]}


# --------------------------------------------------------------------------------------------------------------------
# 12. Step 02's live note, the SOV helper line, the Min tooltip
# --------------------------------------------------------------------------------------------------------------------

LEAD = "Billed as one period for the whole flight — the export shows Months: 1 and one total per line."


def test_step_02_note_says_the_budget_is_the_flight_total_as_typed_and_relabels_the_fields(js):
    n = js["note"]
    for unit in ("week", "month", "quarter"):              # other billing periods: nothing shown, nothing written, the labels as they were
        assert n[unit] == {"hidden": True, "text": "", "label": "Monthly budget", "tierNote": "(optional — alternate monthly options)"}, unit
    ff = n["full_flight"]
    assert ff["hidden"] is False and ff["text"].startswith(LEAD)
    assert "used exactly as typed — nothing is multiplied by months." in ff["text"]
    assert ff["label"] == "Flight budget" and ff["tierNote"] == "(optional — alternate flight budgets)"
    # no arithmetic and no per-month wording anywhere in it
    assert "/month" not in ff["text"] and "×" not in ff["text"]
    # switching back restores the monthly labels and hides the note
    assert js["noteBackToMonth"] == {"hidden": True, "label": "Monthly budget", "tierNote": "(optional — alternate monthly options)"}
    # the static markup carries the very same sentence (what is on screen before any script runs)
    html_hint = re.search(r'id="step02-billing-period-hint">([^<]+)</p>', INDEX_HTML).group(1)
    assert html_hint == ff["text"]


def test_sov_helper_line_states_the_comparison_only_in_full_flight(js):
    h = js["sovHelper"]
    plain = "Proposed product allocation uses: 85% of total avails"
    assert h["month"] == {"helper": plain, "helperClass": "sov-helper sov-orange", "badge": "85% of avails", "badgeClass": "sov-badge sov-orange"}
    assert h["week"]["helper"] == plain                    # unchanged outside Full Flight
    for key, budget, pct, tier in (("ffTwoMonths", "$12,000", "62%", "green"), ("ffOneMonth", "$12,000", "62%", "green"),
                                   ("ffFourMonths", "$10,000", "91%", "red")):
        assert h[key]["helper"] == (f"Proposed product allocation uses: {pct} of total avails · "
                                    f"Full Flight compares the whole-flight budget ({budget}) with the monthly ceiling"), key   # whatever the dates
        assert h[key]["helperClass"] == f"sov-helper sov-{tier}" and h[key]["badge"] == f"{pct} of avails"
    for key in ("ffZeroBudget", "ffUnknownLine"):          # nothing to compare / no such line
        assert h[key]["helper"] == "Proposed product allocation uses: 62% of total avails", key
    assert h["ffNull"] == {"helper": "", "badge": "", "badgeClass": "sov-badge"}         # no percentage: cleared, as before


def test_min_column_header_explains_that_the_monthly_minimum_applies_once(js):
    t = js["minTitle"]
    assert t["week"] == "" and t["month"] == "" and t["quarter"] == ""                    # no tooltip anywhere else
    assert t["full_flight"] == "Full Flight is one billing period: each line needs the product's monthly minimum once."


# --------------------------------------------------------------------------------------------------------------------
# 13. A stale multi-period line is folded, with its total kept, wherever Full Flight re-derives months
# --------------------------------------------------------------------------------------------------------------------

def test_resync_folds_stale_lines_instead_of_cutting_them(js):
    r = js["resyncFold"]
    # [monthly_budget, months, reconciled, flight total]: 100 x 3 -> 300 x 1; Added Value stays 0; a one-period line is
    # untouched; a line still carrying a stale Step 06 breakdown is folded with its total kept.
    assert r["active"] == [[300, 1, True, 300], [0, 1, True, 0], [700, 1, True, 700], [300, 1, True, 300]]
    assert r["optionB"] == [[200, 1, True]]                # option B: 50 x 4


def test_fold_helper_edge_cases(js):
    f = js["foldLine"]
    assert f == {"cents": [300.01, 1], "again": [100, 1], "addedValue": [0, 1], "zeroMonths": [100, 1], "noBudget": [0, 1]}


# --------------------------------------------------------------------------------------------------------------------
# Static contracts (no engine needed)
# --------------------------------------------------------------------------------------------------------------------

def test_step_04_pill_has_a_fourth_full_flight_option_and_the_css_is_sized_for_four():
    options = re.findall(r'<button[^>]*class="time-unit-slider-option[^"]*"[^>]*data-unit="([^"]+)"[^>]*>([^<]+)</button>', INDEX_HTML)
    assert options == [("week", "Weekly"), ("month", "Monthly"), ("quarter", "Quarterly"), ("full_flight", "Full Flight")]
    assert "--time-unit-count: 4;" in STYLES_CSS
    assert "width: calc((100% - 8px) / var(--time-unit-count));" in STYLES_CSS
    assert "/ 3);" not in STYLES_CSS.split(".time-unit-slider-thumb")[1].split("}")[0]     # the old hard-coded third
    option_block = STYLES_CSS.split(".time-unit-slider-option {")[1].split("}")[0]
    assert "min-width: 0" in option_block and "flex: 1 1 0" in option_block   # equal widths, so the thumb stays aligned


def test_step_02_has_a_billing_period_select_that_is_not_a_data_field():
    m = re.search(r'<select id="step02-billing-period"([^>]*)>(.*?)</select>', INDEX_HTML, re.S)
    assert m, "Step 02 select missing"
    assert "data-field" not in m.group(1)
    assert re.findall(r'<option value="([^"]+)"[^>]*>([^<]+)</option>', m.group(2)) == [
        ("week", "Weekly"), ("month", "Monthly"), ("quarter", "Quarterly"), ("full_flight", "Full Flight")]   # same spelling as the pill
    assert 'id="step02-billing-period-hint"' in INDEX_HTML
    assert "Months: 1" in INDEX_HTML                        # the hint's promise about the export
    # it lives inside the Flight fieldset
    flight = INDEX_HTML.split("<legend>Flight</legend>")[1].split("</fieldset>")[0]
    assert 'id="step02-billing-period"' in flight
    assert 'data-field="total_months"' in flight           # the read-only calendar month count is still there


def test_full_flight_markup_hooks_exist():
    for element_id in ("flight-total-cell", "flight-total-label", "flight-total-gross-cell", "flight-total-gross-label",
                       "monthly-total-cell", "monthly-total-gross-cell", "mb-fullflight-note", "mb-fullflight-detail"):
        assert f'id="{element_id}"' in INDEX_HTML, element_id
    # the unchanged ids other code depends on
    for element_id in ("monthly-total", "flight-total", "monthly-total-gross", "flight-total-gross", "col-months-header",
                       "mb-content", "mb-no-dates", "nav-step-6-label", "time-unit-toggle", "time-unit-slider-thumb"):
        assert f'id="{element_id}"' in INDEX_HTML, element_id
    for rule in ("#step-4.ff-mode #col-months-header", "#step-4.ff-mode .line-items .col-months", "#step-4.ff-mode #flight-total-cell",
                 "#step-6.ff-mode #mb-content"):
        assert rule in STYLES_CSS, rule


def test_admin_subtitle_lists_all_four_billing_periods():
    assert "Weekly / Monthly / Quarterly / Full Flight" in ADMIN_HTML


def test_request_bodies_carry_the_time_unit():
    strategy = _extract_fn(APP_JS, "onStrategyGenerate")
    assert "time_unit: state.timeUnit" in strategy
    rebuild = APP_JS.split("/rebuild`", 1)[1].split("}, 600)")[0]
    assert "time_unit: state.timeUnit" in rebuild
    reprompt = _extract_fn(APP_JS, "onEmailReprompt")
    assert "time_unit: state.timeUnit" in reprompt
    recommend = _extract_fn(APP_JS, "onRecommend")
    assert "time_unit: state.timeUnit" in recommend
    assert "flight_months" not in APP_JS                               # Full Flight is one period: no month count is sent
    payload = _extract_fn(APP_JS, "_buildWizardStatePayload")
    assert payload.index("_resyncAllTierMonths();") < payload.index("_normalizeFullFlightState();")
    assert "time_unit: state.timeUnit" in payload


def test_nothing_else_assumes_exactly_three_billing_periods():
    assert "translateX(" in APP_JS and "/ 3)" not in APP_JS.split("function _applyTimeUnitLabels")[1].split("\n}\n")[0]
    # no silent "|| 'Month'" lookups left behind
    for name in ("_mbUnitNoun", "_mbUnitNounPlural", "_mbUnitAdjective"):
        assert "||" not in _extract_fn(APP_JS, name)
    # the dead `months` variable in the Step 08 summary is gone
    assert "const months" not in _extract_fn(APP_JS, "renderGenerateSummary")


def test_the_line_table_folds_stale_lines_and_keeps_the_min_cells_plain():
    render = _extract_fn(APP_JS, "renderLineItems")
    assert "if (_isFullFlight()) { li._monthsReconciled = true; _foldLineIntoOnePeriod(li); }" in render
    assert "_refreshMinHeaderTitle();" in render
    # the per-row Min cell is the plain monthly minimum, exactly as in every other billing period
    assert '<td class="min">${money(minSpend)}</td>' in render
    assert 'id="col-min-header"' in INDEX_HTML


def test_skip_is_hidden_in_full_flight_and_the_select_snaps_back_when_the_switch_is_refused():
    assert re.search(r"#step-6\.ff-mode #monthly-breakdown-skip-btn \{ display: none; \}", STYLES_CSS)
    handler = APP_JS.split('step02Period.addEventListener("change", () => {', 1)[1].split("});", 1)[0]
    assert handler.index("onTimeUnitChange(step02Period.value)") < handler.index("step02Period.value = state.timeUnit")


def test_step_04_is_seeded_with_the_step_02_figures_exactly_as_typed_in_every_billing_period():
    seed = _extract_fn(APP_JS, "onNext")
    assert "const budget = state.parsed.monthly_budget || parseBudgetFromRenewal(state.parsed);" in seed
    assert "addTier(target)" in seed                                   # Tier #1-#4 as typed too
    assert "_round2" not in seed and "_isFullFlight" not in seed       # no billing-period branch (and no rounding) in the seed
    # none of the calendar-month machinery survives — a dangling reference to any of it is a ReferenceError at Step 04
    for gone in ("seedMonths", "_step04Seed", "_flightCalendarMonths", "_tierFlightMonths", "_activeTierFlightMonths",
                 "_campaignFlightMonths", "_minRuleTitle", "_tierSpendForT1Threshold"):
        assert gone not in APP_JS, gone


def test_the_t1_threshold_is_the_plain_sum_of_each_options_budgets():
    mailto = _extract_fn(APP_JS, "buildInternalEmailMailtoLink")
    assert "(t.line_items || []).reduce((sum, li) => sum + (li.monthly_budget || 0), 0)" in mailto
    assert "_isFullFlight" not in mailto                              # a Full Flight option's lines already sum to its flight total
