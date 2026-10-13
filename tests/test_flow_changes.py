"""
Two front-end flow changes (app/static/app.js, index.html, styles.css):

  1. Step 06 (Monthly Breakdown): a MASTER "Apply the default split to all lines" control beside the Default split
     toggle (both views), and an inline hint when the toggle is flipped while lines still hold the other split.
  2. Step 07 (Roadblocks Check): the restricted category (the sheet's categories, or "None") is chosen FIRST and that
     pick auto-starts the AI run; nothing runs on arrival; picks are debounced and a stale answer can never overwrite a
     newer pick's; plus the guidelines callout at the top of the step.

There is no JS test runner in this repo, so (like test_full_flight_frontend.py) the real functions are pulled out of
app.js BY NAME and run in Node with a faked DOM, fake timers and a controllable fetch. Without Node the engine-backed
tests are skipped (never errored). The static markup/CSS contracts and the backend contract need no engine.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql://fake:fake@localhost/fake")

ROOT =Path(__file__).resolve().parent.parent
APP_JS = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
INDEX_HTML = (ROOT / "app" / "templates" / "index.html").read_text(encoding="utf-8")
STYLES_CSS = (ROOT / "app" / "static" / "styles.css").read_text(encoding="utf-8")

GUIDELINES_URL = "https://entravision-digital-ad-guidelines.netlify.app/"


# --------------------------------------------------------------------------------------------------------------------
# Pulling functions / constants out of app.js by name
# --------------------------------------------------------------------------------------------------------------------

def _extract_fn(src: str, name: str) -> str:
    m = re.search(rf"^(?:async )?function {re.escape(name)}\(", src, re.M)
    assert m, f"function {name} not found"
    end = src.index("\n}\n", m.start())     # top-level functions close with a "}" in column 0
    return src[m.start(): end + 2]


def _extract_decl(src: str, kind: str, name: str) -> str:
    """`const NAME = ...;` / `let NAME = ...;` — up to the first ';' (the initialisers used here contain none)."""
    m = re.search(rf"^{kind} {re.escape(name)} = ", src, re.M)
    assert m, f"{kind} {name} not found"
    end = src.index(";", m.end())
    return src[m.start(): end + 1]


def _fn_source(name: str) -> str:
    return _extract_fn(APP_JS, name)


def _code_only(src: str) -> str:
    """The source with every full-line // comment blanked out (same length, so positions still line up)."""
    return re.sub(r"^[ \t]*//[^\n]*", lambda m: " " * len(m.group(0)), src, flags=re.M)


APP_JS_CODE = _code_only(APP_JS)


def _owner_of(pos: int):
    """The top-level function whose body contains APP_JS[pos] (None before the first one)."""
    starts = list(re.finditer(r"^(?:async )?function (\w+)\(", APP_JS_CODE[:pos], re.M))
    return starts[-1].group(1) if starts else None


STEP06_CONSTS = ["FULL_FLIGHT", "FULL_FLIGHT_KEY", "TIME_UNITS", "_MB_CENT", "_MB_DEFAULT_SLACK",
                 "_MB_UNIT_NOUN", "_MB_UNIT_NOUN_PLURAL", "_MB_UNIT_ADJECTIVE"]
STEP06_FUNCTIONS = [
    "_isFullFlight", "_round2", "_mbUnitWord", "_mbUnitNoun", "_mbUnitNounPlural", "_mbUnitAdjective",
    "_mbParseDate", "_mbMonthKey", "_mbMonthLabel", "_mbLastDayOfMonth", "_mbDaysBetweenInclusive", "_mbDateRangeLabel",
    "_mbMonthsBetween", "_mbWeeksBetween", "_mbQuartersBetween", "_mbCombinedPeriodLabel", "_mbFullFlightPeriods",
    "_mbPeriodsBetween", "_mbApplyPeriodMerges", "_effectiveStartDate", "_effectiveEndDate",
    "_mbEvenDefaultAllocation", "_mbProratedDefaultAllocation", "_mbDefaultAllocation", "_mbEffectiveMonths",
    "_mbSetAllocation", "_mbAllocationsMatch", "_mbPlanRedistribution", "_mbModeWord", "_mbRedistributeConfirmText",
    "onMbApplyAll", "_mbRenderApplyControls",
]

STEP07_LETS = ["_rbDebounceTimer", "_rbRunToken", "_rbAbort", "_rbInFlight", "_rvUsingBuiltin", "_rvLoadFailed", "_rvLoading"]
STEP07_FUNCTIONS = [
    "_cancelRoadblocksRun", "_rbBusy", "_resetRoadblocksUI", "_showRoadblocksPending", "onRvPick", "onRoadblocksGenerate",
    "_runRoadblocks", "_loadRestrictedVerticals", "_renderRestrictedVerticals", "_renderRvChips", "_updateRvNote",
    "renderRoadblocks", "_enterRoadblocksStep", "_readJsonResponse", "_roadblockMatrixHtml", "setText", "escapeHtml", "escapeAttr",
    # the read-only per-line notes under each product card (see tests/test_line_note.py) — renderRoadblocks calls them
    "_refreshRoadblockLineNotes", "_lineNotesForProduct", "_lineNote", "_cleanLineModifier", "_LINE_MODIFIER_UNWANTED",
    "_rbPlanKey", "_rbCacheKey",
]


def _step06_bundle() -> str:
    return "\n".join([_extract_decl(APP_JS, "const", n) for n in STEP06_CONSTS] + [_fn_source(n) for n in STEP06_FUNCTIONS])


def _step07_bundle() -> str:
    parts = [_extract_decl(APP_JS, "const", "_RB_DEBOUNCE_MS"), _extract_decl(APP_JS, "const", "LINE_MODIFIER_MAX"),
             _extract_decl(APP_JS, "const", "_rbResultCache")]
    parts += [_extract_decl(APP_JS, "let", n) for n in STEP07_LETS]
    parts += [_fn_source(n) for n in STEP07_FUNCTIONS]
    return "\n".join(parts)


# --------------------------------------------------------------------------------------------------------------------
# The engine (Node only: the Step 07 scenarios are async)
# --------------------------------------------------------------------------------------------------------------------

def _run_js(body: str, *, is_async: bool = False) -> str:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not available")
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "scenario.js"
        if is_async:
            script.write_text(
                "(async function () {\n" + body + "\n})().then(function (r) { console.log(r); }, "
                "function (e) { console.error(e && e.stack || e); process.exit(1); });\n", encoding="utf-8")
        else:
            script.write_text(f"console.log((function () {{\n{body}\n}})());\n", encoding="utf-8")
        proc = subprocess.run([node, str(script)], capture_output=True, text=True, encoding="utf-8", timeout=120)
    if proc.returncode != 0:
        raise AssertionError(f"node failed:\n{proc.stderr}\n{proc.stdout}")
    return proc.stdout.strip()


# --------------------------------------------------------------------------------------------------------------------
# Step 06 scenarios
# --------------------------------------------------------------------------------------------------------------------

STEP06_PRELUDE = r"""
var __warns = [];
var console = { warn: function () { __warns.push(Array.prototype.slice.call(arguments).join(" ")); }, log: function () {}, error: function () {} };
var __confirms = [], __confirmAnswer = true, __renders = 0;
function confirm(m) { __confirms.push(String(m)); return __confirmAnswer; }
function makeEl(id) {
  var cls = {};
  return { id: id, textContent: "", title: "", disabled: false, dataset: {}, style: {},
    classList: { add: function (c) { cls[c] = true; }, remove: function (c) { delete cls[c]; },
                 toggle: function (c, force) { var on = force === undefined ? !cls[c] : !!force; if (on) cls[c] = true; else delete cls[c]; return on; },
                 contains: function (c) { return !!cls[c]; } } };
}
var __els = {};
var document = { getElementById: function (id) { return __els[id] || (__els[id] = makeEl(id)); } };
var state;
function renderMonthlyBreakdown() { __renders++; }
"""

STEP06_HELPERS = r"""
function mkState(o) {
  o = o || {};
  return { timeUnit: o.unit || "month", parsed: { start_date: o.start === undefined ? "2026-09-01" : o.start, end_date: o.end === undefined ? "2026-11-30" : o.end },
           activeTierLabel: "A", activeTierStartDate: null, activeTierEndDate: null, tiers: o.tiers || [],
           activeTierPeriodMergeGroups: o.merges || [], lineItems: o.lines || [], mbDistributionMode: o.mode || "even", mbModeNotice: !!o.notice };
}
function mkLine(id, budget, alloc, extra) {
  var li = { id: id, product_name: "P" + id, monthly_budget: budget, months: 3, monthly_allocations: alloc, is_added_value: false };
  Object.keys(extra || {}).forEach(function (k) { li[k] = extra[k]; });
  return li;
}
function reset() { __els = {}; __confirms = []; __confirmAnswer = true; __renders = 0; }
function cloneAlloc(a) { return a ? JSON.parse(JSON.stringify(a)) : a; }
function sum(a) { return Math.round(Object.keys(a).reduce(function (s, k) { return s + a[k]; }, 0) * 100) / 100; }
function round2(n) { return Math.round(n * 100) / 100; }
function el(id) { return document.getElementById(id); }
"""

STEP06_BODY = r"""
var R = {};

// ---- 1. the plan: who is eligible, who changes, who counts as customized ----
state = mkState();
var months = _mbEffectiveMonths();
var K = months.map(function (m) { return m.key; });
R.keys = K;
R.evenDefault = _mbEvenDefaultAllocation(3000, months);
R.proratedDefault = _mbProratedDefaultAllocation(3000, months);
function planLines() {
  return [
    mkLine("a", 1000, _mbEvenDefaultAllocation(3000, months)),                                    // plainly the even default
    mkLine("b", 1000, (function () { var x = {}; x[K[0]] = 1000; x[K[1]] = 1500; x[K[2]] = 500; return x; })()),   // typed numbers
    mkLine("c", 1000, null, { mb_off: true }),                                                    // breakdown off for this line
    mkLine("d", 0, null, { is_added_value: true }),
    mkLine("e", 0, (function () { var x = {}; x[K[0]] = 0; x[K[1]] = 0; x[K[2]] = 0; return x; })()),             // zero budget
    mkLine("f", 1000, (function () { var x = {}; x[K[0]] = 1000.03; x[K[1]] = 999.97; x[K[2]] = 1000; return x; })())  // even, a few cents of rounding residue
  ];
}
function summarize(plan) {
  return { eligible: plan.eligible, changeCount: plan.changeCount, customCount: plan.customCount,
           rows: plan.rows.map(function (r) { return [r.li.id, r.changes, r.custom]; }) };
}
state = mkState({ mode: "even", lines: planLines() });
R.planEven = summarize(_mbPlanRedistribution(state.lineItems, months));
state = mkState({ mode: "prorated", lines: planLines() });
R.planProrated = summarize(_mbPlanRedistribution(state.lineItems, months));
R.planEmpty = summarize(_mbPlanRedistribution([], months));
R.match = {
  same: _mbAllocationsMatch({ a: 1, b: 2 }, { a: 1, b: 2 }),
  halfCent: _mbAllocationsMatch({ a: 1 }, { a: 1.004 }),
  cent: _mbAllocationsMatch({ a: 1 }, { a: 1.01 }),
  missingKey: _mbAllocationsMatch({ a: 1 }, { a: 1, b: 5 }),
  slack: _mbAllocationsMatch({ a: 1 }, { a: 1.9 }, 1)
};

// ---- 2. apply (even): confirm because b is custom; cancel changes nothing; accept rewrites exactly the changing lines ----
state = mkState({ mode: "even", lines: planLines(), notice: true });
var before = state.lineItems.map(function (l) { return cloneAlloc(l.monthly_allocations); });
reset(); __confirmAnswer = false;
onMbApplyAll();
R.cancel = { confirms: __confirms.slice(), renders: __renders, unchanged: JSON.stringify(state.lineItems.map(function (l) { return l.monthly_allocations; })) === JSON.stringify(before), notice: state.mbModeNotice };
reset(); __confirmAnswer = true;
var baselinesBefore = state.lineItems.map(function (l) { return l._mbBaseline; });
onMbApplyAll();
var evenAfter = _mbEvenDefaultAllocation(3000, months);
R.accept = {
  confirms: __confirms.slice(), renders: __renders, notice: state.mbModeNotice,
  b: state.lineItems[1].monthly_allocations, f: state.lineItems[5].monthly_allocations, a: state.lineItems[0].monthly_allocations,
  c: state.lineItems[2].monthly_allocations, d: state.lineItems[3].monthly_allocations, e: state.lineItems[4].monthly_allocations,
  evenAfter: evenAfter,
  baselines: state.lineItems.map(function (l) { return l._mbBaseline === undefined ? null : l._mbBaseline; }),
  budgets: state.lineItems.map(function (l) { return l.monthly_budget; }),
  sums: state.lineItems.map(function (l) { return l.monthly_allocations ? sum(l.monthly_allocations) : null; })
};
// the same click again: everything already matches -> nothing to do, no confirm, no rewrite (but the hint flag is cleared)
reset();
onMbApplyAll();
R.idempotent = { confirms: __confirms.slice(), renders: __renders };

// ---- 3. prorated, nothing customized: the OTHER default is overwritten without a question ----
state = mkState({ mode: "prorated", lines: [mkLine("a", 1000, _mbEvenDefaultAllocation(3000, months)), mkLine("b", 500, _mbEvenDefaultAllocation(1500, months))] });
reset();
onMbApplyAll();
R.evenToProrated = { confirms: __confirms.slice(), a: state.lineItems[0].monthly_allocations, expected: _mbProratedDefaultAllocation(3000, months), b: state.lineItems[1].monthly_allocations, expectedB: _mbProratedDefaultAllocation(1500, months) };

// ---- 4. merged periods: the merged key, prorated by the merged day count; total kept ----
state = mkState({ mode: "prorated", merges: [["2026-09", "2026-10"]] });
var mmonths = _mbEffectiveMonths();
state.lineItems = [mkLine("a", 1000, (function () { var x = {}; x[mmonths[0].key] = 5; x[mmonths[1].key] = 2995; return x; })())];
reset(); onMbApplyAll();
R.merged = { keys: mmonths.map(function (m) { return m.key; }), days: mmonths.map(function (m) { return m.active_days; }), alloc: state.lineItems[0].monthly_allocations,
             sum: sum(state.lineItems[0].monthly_allocations), confirms: __confirms.slice() };

// ---- 5. the billing unit: weeks and quarters ----
function unitRun(unit, start, end) {
  state = mkState({ unit: unit, start: start, end: end, mode: "even" });
  var ms = _mbEffectiveMonths();
  var x = {}; ms.forEach(function (m, i) { x[m.key] = i === 0 ? 3000 : 0; });
  state.lineItems = [mkLine("a", 1000, x)];
  reset(); onMbApplyAll();
  var a = state.lineItems[0].monthly_allocations;
  return { periods: ms.length, keys: Object.keys(a), sum: sum(a), expected: _mbEvenDefaultAllocation(3000, ms), got: a, confirms: __confirms.length };
}
R.week = unitRun("week", "2026-09-01", "2026-10-12");
R.quarter = unitRun("quarter", "2026-09-01", "2027-03-31");

// ---- 6. Full Flight: inert ----
state = mkState({ unit: "full_flight", mode: "even", lines: [mkLine("a", 3000, { "2026-09": 1 })] });
reset(); onMbApplyAll();
R.fullFlight = { confirms: __confirms.length, renders: __renders, alloc: state.lineItems[0].monthly_allocations };
// ... and no dates at all
state = mkState({ start: "", end: "", mode: "even", lines: [mkLine("a", 3000, { "2026-09": 1 })] });
reset(); onMbApplyAll();
R.noDates = { confirms: __confirms.length, renders: __renders, alloc: state.lineItems[0].monthly_allocations };

// ---- 7. the confirm text ----
function ct(n, k, tiers) {
  state = mkState({ mode: "even", tiers: tiers ? [{ label: "B" }] : [] });
  return _mbRedistributeConfirmText({ changeCount: n, customCount: k });
}
R.confirmText = { one: ct(1, 1), all: ct(3, 3), some: ct(3, 2), oneOfMany: ct(3, 1), scoped: ct(2, 1, true) };
state = mkState({ mode: "prorated" });
R.confirmTextProrated = _mbRedistributeConfirmText({ changeCount: 2, customCount: 2 });

// ---- 8. the button and the hint follow the lines ----
function controls(o) {
  state = mkState(o); reset();
  _mbRenderApplyControls(_mbEffectiveMonths());
  return { text: el("mb-apply-all-btn").textContent, disabled: el("mb-apply-all-btn").disabled, title: el("mb-apply-all-btn").title,
           noticeHidden: el("mb-mode-notice").classList.contains("hidden"), noticeText: el("mb-notice-text").textContent, flag: state.mbModeNotice };
}
function evenLines() { return [mkLine("a", 1000, _mbEvenDefaultAllocation(3000, months)), mkLine("b", 500, _mbEvenDefaultAllocation(1500, months))]; }
R.ctlIdle = controls({ mode: "even", lines: evenLines(), notice: true });                       // nothing differs: disabled, and the hint flag clears
R.ctlProrated = controls({ mode: "prorated", lines: evenLines(), notice: true });                // flipped: enabled, hint with counts
R.ctlProratedNoFlag = controls({ mode: "prorated", lines: evenLines(), notice: false });         // not flipped just now: enabled, no hint
R.ctlCustom = controls({ mode: "even", lines: planLines(), notice: true });
R.ctlNone = controls({ mode: "even", lines: [mkLine("c", 1000, null, { mb_off: true })], notice: true });
R.ctlTiers = controls({ mode: "prorated", lines: evenLines(), tiers: [{ label: "B" }], notice: true });
R.ctlWeek = controls({ unit: "week", end: "2026-10-12", mode: "prorated", lines: [] });

// ---- 9. the total of every line is kept to the cent, whatever the budget, flight and mode ----
var seed = 20261009;
function rnd() { seed = (seed * 16807) % 2147483647; return seed / 2147483647; }
function isoPlus(d, days) { var x = new Date(d + "T00:00:00Z"); x.setUTCDate(x.getUTCDate() + days); return x.toISOString().slice(0, 10); }
var bad = [], runs = 0;
for (var it = 0; it < 120; it++) {
  var start = isoPlus("2026-01-01", Math.floor(rnd() * 300)), end = isoPlus(start, 5 + Math.floor(rnd() * 330));
  var unit = ["week", "month", "quarter"][Math.floor(rnd() * 3)];
  var mode = rnd() < 0.5 ? "even" : "prorated";
  var lines = [];
  state = mkState({ unit: unit, start: start, end: end, mode: mode });
  var ms = _mbEffectiveMonths();
  for (var k = 0; k < 3; k++) {
    var budget = Math.round(rnd() * 999999) / 100 + 0.01;
    var months2 = ms.length;
    var li = mkLine("L" + k, budget, null);
    li.months = months2;
    var x = {}; var total = round2(budget * months2);
    ms.forEach(function (m, i) { x[m.key] = i === 0 ? total : 0; });       // customized: everything in the first period
    li.monthly_allocations = x;
    lines.push(li);
  }
  state.lineItems = lines;
  reset(); onMbApplyAll();
  state.lineItems.forEach(function (l) {
    runs++;
    var want = round2(l.monthly_budget * l.months);
    if (Math.abs(sum(l.monthly_allocations) - want) > 0.005 || Object.keys(l.monthly_allocations).length !== ms.length) bad.push([unit, mode, start, end, l.monthly_budget, l.months, sum(l.monthly_allocations), want]);
  });
}
R.fuzz = { runs: runs, bad: bad.slice(0, 3) };

return JSON.stringify(R);
"""


@pytest.fixture(scope="module")
def mb():
    return json.loads(_run_js(STEP06_PRELUDE + "\n" + _step06_bundle() + "\n" + STEP06_HELPERS + "\n" + STEP06_BODY))


def test_plan_picks_only_lines_the_per_line_reset_works_on(mb):
    keys = mb["keys"]
    assert keys == ["2026-09", "2026-10", "2026-11"]
    # Even mode: a already even (no change), b typed (change + custom), f even +/- cents (already follows the default:
    # not counted as different, though Apply still tidies its cents), and the breakdown-off line (c), the Added Value
    # line (d) and the zero-budget line (e) are not eligible at all.
    p = mb["planEven"]
    assert p["eligible"] == 3 and p["changeCount"] == 1 and p["customCount"] == 1
    assert p["rows"] == [["a", False, False], ["b", True, True], ["f", False, False]]
    # Prorated mode: the plain even lines change too, but only b is "customized".
    q = mb["planProrated"]
    assert q["eligible"] == 3 and q["changeCount"] == 3 and q["customCount"] == 1
    assert q["rows"] == [["a", True, False], ["b", True, True], ["f", True, False]]
    assert mb["planEmpty"] == {"eligible": 0, "changeCount": 0, "customCount": 0, "rows": []}


def test_allocation_comparison_is_per_period_to_the_cent(mb):
    assert mb["match"] == {"same": True, "halfCent": True, "cent": False, "missingKey": False, "slack": True}


def test_cancelled_confirm_changes_nothing(mb):
    c = mb["cancel"]
    assert len(c["confirms"]) == 1 and c["confirms"][0].startswith("Apply the even split to 1 line? Its custom numbers will be overwritten.")
    assert c["renders"] == 0 and c["unchanged"] is True
    assert c["notice"] is True                     # still raised: the planner didn't act on it


def test_accepted_apply_rewrites_exactly_the_changing_lines_and_keeps_every_total(mb):
    a = mb["accept"]
    even = a["evenAfter"]
    assert a["b"] == even and a["f"] == even and a["a"] == even
    assert a["c"] is None and a["d"] is None                              # breakdown off / Added Value: untouched
    assert a["e"] == {"2026-09": 0, "2026-10": 0, "2026-11": 0}           # zero budget: untouched
    assert len(a["confirms"]) == 1 and a["renders"] == 1 and a["notice"] is False
    # totals: the line budgets are untouched and each rewritten line still sums to budget x months
    assert a["budgets"] == [1000, 1000, 1000, 0, 0, 1000]
    assert a["sums"][0] == a["sums"][1] == a["sums"][5] == 3000
    # _mbSetAllocation re-stamped the baseline the Curate-total rescale compares against
    assert a["baselines"][1] == 3000 and a["baselines"][5] == 3000


def test_applying_again_asks_nothing_and_rewrites_nothing(mb):
    assert mb["idempotent"] == {"confirms": [], "renders": 1}


def test_switching_a_plain_default_to_the_other_default_needs_no_confirm(mb):
    r = mb["evenToProrated"]
    assert r["confirms"] == []
    assert r["a"] == r["expected"] and r["b"] == r["expectedB"]
    assert r["a"] != mb["evenDefault"]                                    # prorated really is different for Sep/Oct/Nov (30/31/30 days)
    assert round(sum(r["a"].values()), 2) == 3000 and round(sum(r["b"].values()), 2) == 1500


def test_merged_periods_use_the_merged_key_and_day_count(mb):
    m = mb["merged"]
    assert m["keys"] == ["2026-09+2026-10", "2026-11"] and m["days"] == [61, 30]
    assert list(m["alloc"].keys()) == m["keys"]
    assert m["alloc"]["2026-09+2026-10"] == pytest.approx(3000 * 61 / 91, abs=0.01)
    assert m["sum"] == 3000
    assert len(m["confirms"]) == 1                                         # the line held custom numbers (5 / 2995)


@pytest.mark.parametrize("unit,periods", [("week", 6), ("quarter", 3)])
def test_weeks_and_quarters_use_the_units_periods(mb, unit, periods):
    r = mb[unit]
    assert r["periods"] == periods and len(r["keys"]) == periods
    assert r["got"] == r["expected"] and r["sum"] == 3000
    assert r["confirms"] == 1                                              # the line held everything in the first period


def test_full_flight_and_a_missing_flight_make_the_control_inert(mb):
    assert mb["fullFlight"] == {"confirms": 0, "renders": 0, "alloc": {"2026-09": 1}}
    assert mb["noDates"] == {"confirms": 0, "renders": 0, "alloc": {"2026-09": 1}}


def test_confirm_text_names_how_many_lines_change_and_how_many_were_customized(mb):
    t = mb["confirmText"]
    tail = " Each line's total stays the same. Continue?"
    assert t["one"] == "Apply the even split to 1 line? Its custom numbers will be overwritten." + tail
    assert t["all"] == "Apply the even split to 3 lines? Their custom numbers will be overwritten." + tail
    assert t["some"] == "Apply the even split to 3 lines? 2 of them have custom numbers that will be overwritten." + tail
    assert t["oneOfMany"] == "Apply the even split to 3 lines? 1 of them has custom numbers that will be overwritten." + tail
    assert t["scoped"].startswith("Apply the even split to 2 lines in this option? ")
    assert mb["confirmTextProrated"].startswith("Apply the day-prorated split to 2 lines? ")


def test_button_label_enabled_state_and_hint_follow_the_lines(mb):
    idle = mb["ctlIdle"]
    assert idle["text"] == "↺ Apply even split to all lines" and idle["disabled"] is True
    assert "already follows the even split" in idle["title"]
    assert idle["noticeHidden"] is True and idle["flag"] is False          # nothing left to offer: the hint flag clears itself

    flipped = mb["ctlProrated"]
    assert flipped["text"] == "↺ Apply day-prorated split to all lines" and flipped["disabled"] is False
    assert flipped["noticeHidden"] is False
    assert flipped["noticeText"] == "The default is now day-prorated, but 2 of 2 lines still hold a different split."
    assert "Redistribute 2 lines across the months" in flipped["title"]

    assert mb["ctlProratedNoFlag"]["disabled"] is False and mb["ctlProratedNoFlag"]["noticeHidden"] is True   # no hint unless the toggle was just flipped

    custom = mb["ctlCustom"]
    assert custom["noticeText"] == "The default is now even, but 1 of 3 lines still hold a different split (1 with custom numbers)."

    none = mb["ctlNone"]
    assert none["disabled"] is True and "No line is using the breakdown" in none["title"] and none["noticeHidden"] is True

    scoped = mb["ctlTiers"]
    assert "in this option" in scoped["title"] and "in this option" in scoped["noticeText"]
    assert "weeks" in mb["ctlWeek"]["title"] or "No line" in mb["ctlWeek"]["title"]


def test_every_lines_total_is_kept_to_the_cent_for_any_budget_flight_unit_and_mode(mb):
    assert mb["fuzz"]["runs"] >= 300
    assert mb["fuzz"]["bad"] == []


# --------------------------------------------------------------------------------------------------------------------
# Step 07 scenarios
# --------------------------------------------------------------------------------------------------------------------

STEP07_PRELUDE = r"""
var __logs = [];
var console = { warn: function () {}, log: function () {}, error: function () {} };
var _resetEpoch = 0;
var state;

// fake timers
var __timers = [], __now = 0, __tid = 0;
function setTimeout(fn, ms) { var id = ++__tid; __timers.push({ id: id, at: __now + ms, fn: fn }); return id; }
function clearTimeout(id) { __timers = __timers.filter(function (t) { return t.id !== id; }); }
function advance(ms) {
  var end = __now + ms;
  for (;;) {
    var due = __timers.filter(function (t) { return t.at <= end; }).sort(function (a, b) { return a.at - b.at; })[0];
    if (!due) break;
    __timers = __timers.filter(function (t) { return t !== due; });
    __now = due.at; due.fn();
  }
  __now = end;
}
function tick() { return new Promise(function (r) { setImmediate(r); }); }

// a controllable fetch: every /api/roadblocks call waits for the test to resolve / reject it
var __fetches = [], __ignoreAbort = false, __catPayload = null, __catFail = false;
function fetch(url, opts) {
  if (url.indexOf("/api/restrictions/categories") === 0) {
    if (__catFail) return Promise.resolve({ ok: false, status: 500, statusText: "boom", json: function () { return Promise.resolve({}); } });
    return Promise.resolve({ ok: true, status: 200, statusText: "OK", json: function () { return Promise.resolve(__catPayload); } });
  }
  return new Promise(function (resolve, reject) {
    var f = { url: url, body: JSON.parse(opts.body), aborted: false };
    f.resolve = function (payload) { resolve({ ok: true, status: 200, statusText: "OK", json: function () { return Promise.resolve(payload); } }); };
    f.fail = function (status, detail) { resolve({ ok: false, status: status, statusText: "ERR", json: function () { return Promise.resolve({ detail: detail }); } }); };
    __fetches.push(f);
    if (opts.signal) opts.signal.addEventListener("abort", function () {
      f.aborted = true;
      if (!__ignoreAbort) { var e = new Error("The operation was aborted"); e.name = "AbortError"; reject(e); }
    });
  });
}
function rbCalls() { return __fetches.filter(function (f) { return f.url === "/api/roadblocks"; }); }

// a generic fake DOM (the elements the markup ships hidden start hidden here too)
var __els = {};
var __startsHidden = { "roadblocks-loading": 1, "roadblocks-error": 1, "roadblocks-content": 1, "roadblocks-download-link": 1, "roadblocks-search-note": 1,
                       "restricted-verticals": 1, "rv-suggest": 1, "rv-note": 1 };
function makeEl(id) {
  var cls = __startsHidden[id] ? { hidden: true } : {};
  return { id: id, textContent: "", innerHTML: "", href: "", title: "", disabled: false, dataset: {}, style: {},
    classList: { add: function (c) { cls[c] = true; }, remove: function (c) { delete cls[c]; },
                 toggle: function (c, force) { var on = force === undefined ? !cls[c] : !!force; if (on) cls[c] = true; else delete cls[c]; return on; },
                 contains: function (c) { return !!cls[c]; } },
    querySelectorAll: function () { return []; }, querySelector: function () { return null; }, addEventListener: function () {} };
}
var document = { getElementById: function (id) { return __els[id] || (__els[id] = makeEl(id)); } };
function el(id) { return document.getElementById(id); }
function shown(id) { return !el(id).classList.contains("hidden"); }

function allTiersForSubmit() { return [{ line_items: [{ product_name: "Search - SEM" }] }]; }
function _briefWithSelectedTactics() { return null; }

function mkState(o) {
  o = o || {};
  return { parsed: { client_name: "Acme Co" }, rvSelected: o.selected === undefined ? null : o.selected, roadblocks: null, step: 7,
           rvCategories: o.categories || [{ name: "Political", suggested: true, summary: "" }, { name: "Cannabis", suggested: false, summary: "" }] };
}
function fresh(o) {
  __timers = []; __now = 0; __fetches = []; __els = {}; __ignoreAbort = false; __catFail = false;
  _rbDebounceTimer = null; _rbRunToken = 0; _rbAbort = null; _rbInFlight = false; _rvUsingBuiltin = false; _rvLoadFailed = false; _rvLoading = false; _rbResultCache.clear(); _resetEpoch = 0;
  state = mkState(o);
}
function payload(cats, summary, extra) {
  var p = { overall_summary: summary, product_roadblocks: [{ product_name: "P", risk_level: "low", risks: [] }], categories: cats,
            used_web_search: true, unresolved_categories: [], error: null, doc_token: null };
  Object.keys(extra || {}).forEach(function (k) { p[k] = extra[k]; });
  return p;
}
function summaryText() { return el("roadblocks-summary").textContent; }
"""

STEP07_BODY = r"""
var R = {};

// ---- 1. one pick: spinner now, request after the debounce, answer rendered ----
fresh();
onRvPick("Political");
R.pick = { callsAt0: rbCalls().length, spinner: shown("roadblocks-loading"), selected: Array.from(state.rvSelected), spinnerText: el("roadblocks-loading-text").textContent };
advance(599); R.pick.callsAt599 = rbCalls().length;
advance(1);   R.pick.callsAt600 = rbCalls().length; R.pick.sent = rbCalls()[0].body.categories;
R.pick.busyWhileRunning = _rbBusy();
rbCalls()[0].resolve(payload(["Political"], "Result Political"));
await tick();
R.pick.summary = summaryText(); R.pick.state = state.roadblocks && state.roadblocks.overall_summary;
R.pick.spinnerAfter = shown("roadblocks-loading"); R.pick.regenerateShown = el("roadblocks-regenerate-btn").style.display === ""; R.pick.content = shown("roadblocks-content");
R.pick.busyAfter = _rbBusy();

// ---- 2. "None" sends an empty list ----
fresh();
onRvPick(null);
advance(600);
R.none = { sent: rbCalls().map(function (f) { return f.body.categories; }), selectedSize: state.rvSelected.size };

// ---- 3. rapid picks cost one run, for the last pick ----
fresh();
onRvPick("Political"); advance(100); onRvPick("Cannabis"); advance(100); onRvPick(null);
advance(599); R.rapid = { at599: rbCalls().length };
advance(1);   R.rapid.at600 = rbCalls().length; R.rapid.sent = rbCalls().map(function (f) { return f.body.categories; });
rbCalls()[0].resolve(payload([], "Result None")); await tick();
R.rapid.summary = summaryText();

// ---- 4. a pick while a run is in flight abandons it; its late answer is never rendered (abort honoured, and ignored) ----
async function stale(ignoreAbort, lateAt) {
  fresh(); __ignoreAbort = ignoreAbort;
  onRvPick("Political"); advance(600);
  var first = rbCalls()[0];
  onRvPick("Cannabis");
  var out = { firstAborted: first.aborted, roadblocksNullAfterPick: state.roadblocks === null, busyDuringDebounce: _rbBusy(), spinnerDuringDebounce: shown("roadblocks-loading") };
  if (lateAt === "debounce") { advance(300); first.resolve(payload(["Political"], "Result Political")); await tick(); }
  out.afterLateAnswer = { summary: summaryText(), stateRoadblocks: state.roadblocks, content: shown("roadblocks-content"), spinner: shown("roadblocks-loading") };
  advance(lateAt === "debounce" ? 300 : 600);
  out.calls = rbCalls().map(function (f) { return f.body.categories; });
  if (lateAt === "after") { first.resolve(payload(["Political"], "Result Political")); await tick(); out.afterLateAnswerAfterSecondStart = { summary: summaryText(), stateRoadblocks: state.roadblocks }; }
  rbCalls()[1].resolve(payload(["Cannabis"], "Result Cannabis")); await tick();
  out.final = { summary: summaryText(), state: state.roadblocks && state.roadblocks.overall_summary, busy: _rbBusy() };
  return out;
}
R.staleAbort = await stale(false, "debounce");
R.staleIgnoredAbortInDebounce = await stale(true, "debounce");
R.staleIgnoredAbortAfterStart = await stale(true, "after");

// ---- 5. the pick that is already showing, Regenerate, and the in-flight guard ----
fresh();
onRvPick("Political"); advance(600); rbCalls()[0].resolve(payload(["Political"], "Result Political")); await tick();
var timersBefore = __timers.length;
onRvPick("Political");
R.samePick = { timers: __timers.length - timersBefore, calls: rbCalls().length, summary: summaryText(), state: state.roadblocks !== null };
onRoadblocksGenerate(); onRoadblocksGenerate();
R.regenerate = { calls: rbCalls().map(function (f) { return f.body.categories; }), noDebounce: __timers.length === 0 };
rbCalls()[1].resolve(payload(["Political"], "Result Political 2")); await tick();
R.regenerate.summary = summaryText();
onRoadblocksGenerate(); R.regenerate.callsAfter = rbCalls().length;           // finished -> a new Regenerate is allowed
// Regenerate while a pick is still in its debounce is ignored
fresh(); onRvPick("Cannabis"); onRoadblocksGenerate(); advance(600);
R.regenerateDuringDebounce = rbCalls().length;
// Regenerate with no pick at all (an old result that predates the category list) re-runs as None
fresh(); onRoadblocksGenerate();
R.regenerateNoPick = { sent: rbCalls().map(function (f) { return f.body.categories; }), selectedSize: state.rvSelected && state.rvSelected.size };

// ---- 6. a different proposal replaces this one: a pending pick never fires, an in-flight answer is dropped ----
fresh(); onRvPick("Political"); _resetEpoch++; advance(600);
R.resetDuringDebounce = rbCalls().length;
fresh(); onRvPick("Political"); advance(600); _resetEpoch++; rbCalls()[0].resolve(payload(["Political"], "Result Political")); await tick();
R.resetDuringRun = { summary: summaryText(), state: state.roadblocks, content: shown("roadblocks-content") };
fresh(); onRvPick("Political"); advance(300); _cancelRoadblocksRun(); advance(600);
R.cancel = { calls: rbCalls().length, busy: _rbBusy() };

// ---- 7. errors ----
fresh(); onRvPick("Cannabis"); advance(600); rbCalls()[0].resolve({ error: "no key", product_roadblocks: [], categories: ["Cannabis"] }); await tick();
R.errorBody = { error: shown("roadblocks-error"), text: el("roadblocks-error").textContent || el("roadblocks-error").innerHTML, regenerateShown: el("roadblocks-regenerate-btn").style.display === "", state: state.roadblocks, spinner: shown("roadblocks-loading"), busy: _rbBusy() };
fresh(); onRvPick("Cannabis"); advance(600); rbCalls()[0].fail(500, "server exploded"); await tick();
R.errorHttp = { text: el("roadblocks-error").textContent, regenerateShown: el("roadblocks-regenerate-btn").style.display === "", busy: _rbBusy() };

// ---- 8. a saved result restores the pick (and never runs) ----
function restore(selected, categories, extra) {
  fresh({ selected: selected });
  renderRoadblocks(payload(categories, "Saved", extra));
  return { selected: state.rvSelected === null ? null : Array.from(state.rvSelected), summary: summaryText(), calls: rbCalls().length, timers: __timers.length };
}
R.restore = { none: restore(null, []), political: restore(null, ["Political"]), multi: restore(null, ["Political", "Cannabis"]), keeps: restore(new Set(["Cannabis"]), ["Political"]),
              noCategoriesField: (function () { fresh(); var p = payload([], "Old"); delete p.categories; renderRoadblocks(p); return state.rvSelected === null; })() };

// ---- 9. the chips ----
function chipsFor(selected, o, roadblocks) {
  fresh(Object.assign({ selected: selected }, o || {}));
  if (roadblocks) state.roadblocks = roadblocks;
  _renderRvChips();
  return { html: el("rv-chips").innerHTML, hint: el("rv-suggest").textContent, hintHidden: el("rv-suggest").classList.contains("hidden") };
}
R.chips = {
  none_picked: chipsFor(null),
  political: chipsFor(new Set(["Political"])),
  empty_is_none: chipsFor(new Set()),
  multi: chipsFor(new Set(["Political", "Cannabis"])),
  unresolved: chipsFor(new Set(), {}, { unresolved_categories: ["Casino"] }),
  third: chipsFor(null, { categories: [{ name: "Political", suggested: false, summary: "" }, { name: "Cannabis", suggested: false, summary: "" }, { name: "Casino/Gambling", suggested: false, summary: "" }] }),
  quote: chipsFor(null, { categories: [{ name: 'Say "hi" <b>', suggested: false, summary: 'a "b" <i>' }] })
};

// ---- 10. arriving on the step: the picker loads, nothing is picked, nothing runs ----
fresh({ categories: [] });
__catPayload = { categories: [{ name: "Political", kind: "x", summary: "s", suggested: true }, { name: "Cannabis", kind: "x", summary: "s", suggested: false }], using_builtin: true };
_enterRoadblocksStep(); await tick(); await tick();
advance(10000); await tick();
R.arrive = { calls: rbCalls().length, selected: state.rvSelected, categories: state.rvCategories.map(function (c) { return c.name; }), wrapShown: shown("restricted-verticals"),
             hint: el("rv-suggest").textContent, hintHidden: el("rv-suggest").classList.contains("hidden"), note: el("rv-note").textContent, spinner: shown("roadblocks-loading"),
             regenerateShown: el("roadblocks-regenerate-btn").style.display === "", onClasses: (el("rv-chips").innerHTML.match(/rv-chip on/g) || []).length };
// ... the list cannot be loaded: still only "None", with a note, and it can run
fresh({ categories: [] }); __catFail = true;
_enterRoadblocksStep(); await tick(); await tick();
R.arriveFailed = { html: el("rv-chips").innerHTML, note: el("rv-note").textContent, noteHidden: el("rv-note").classList.contains("hidden"), wrapShown: shown("restricted-verticals"), selected: state.rvSelected };
onRvPick(null); advance(600);
R.arriveFailed.sent = rbCalls().map(function (f) { return f.body.categories; });
// ... re-entering with a pick and a result keeps both
fresh({ selected: new Set(["Political"]) }); __catPayload = { categories: state.rvCategories, using_builtin: false };
renderRoadblocks(payload(["Political"], "Saved"));
_enterRoadblocksStep(); await tick(); await tick();
R.reenter = { calls: rbCalls().length, selected: Array.from(state.rvSelected), content: shown("roadblocks-content"), summary: summaryText(), onClasses: (el("rv-chips").innerHTML.match(/rv-chip on/g) || []).length };

return JSON.stringify(R);
"""


@pytest.fixture(scope="module")
def rb():
    return json.loads(_run_js(STEP07_PRELUDE + "\n" + _step07_bundle() + "\n" + STEP07_BODY, is_async=True))


def test_a_pick_starts_exactly_one_run_after_the_debounce(rb):
    p = rb["pick"]
    assert p["callsAt0"] == 0 and p["callsAt599"] == 0                      # nothing is sent while the debounce runs
    assert p["spinner"] is True and "Acme Co (Political)" in p["spinnerText"]   # ... but the planner sees it working at once
    assert p["selected"] == ["Political"]
    assert p["callsAt600"] == 1 and p["sent"] == ["Political"] and p["busyWhileRunning"] is True
    assert p["summary"] == "Result Political" and p["state"] == "Result Political"
    assert p["spinnerAfter"] is False and p["content"] is True and p["regenerateShown"] is True and p["busyAfter"] is False


def test_none_runs_with_an_empty_category_list(rb):
    assert rb["none"] == {"sent": [[]], "selectedSize": 0}


def test_rapid_picks_cost_one_run_for_the_last_pick(rb):
    r = rb["rapid"]
    assert r["at599"] == 0 and r["at600"] == 1 and r["sent"] == [[]]       # Political -> Cannabis -> None: one call, with []
    assert r["summary"] == "Result None"


@pytest.mark.parametrize("key", ["staleAbort", "staleIgnoredAbortInDebounce", "staleIgnoredAbortAfterStart"])
def test_a_stale_answer_can_never_overwrite_a_newer_pick(rb, key):
    s = rb[key]
    assert s["firstAborted"] is True                                         # the in-flight request is abandoned at pick time
    assert s["roadblocksNullAfterPick"] is True                              # the old answer does not stay on screen / in the draft
    assert s["busyDuringDebounce"] is True and s["spinnerDuringDebounce"] is True
    # whatever the old request does afterwards (rejects, or answers anyway), nothing of it is shown
    assert s["afterLateAnswer"]["summary"] == "" and s["afterLateAnswer"]["stateRoadblocks"] is None
    assert s["afterLateAnswer"]["content"] is False
    if "afterLateAnswerAfterSecondStart" in s:
        assert s["afterLateAnswerAfterSecondStart"] == {"summary": "", "stateRoadblocks": None}
    assert s["calls"] == [["Political"], ["Cannabis"]]                       # one fresh run for the new pick, nothing more
    assert s["final"] == {"summary": "Result Cannabis", "state": "Result Cannabis", "busy": False}


def test_the_pick_already_showing_runs_nothing_and_regenerate_reruns_it_once(rb):
    assert rb["samePick"] == {"timers": 0, "calls": 1, "summary": "Result Political", "state": True}
    r = rb["regenerate"]
    assert r["calls"] == [["Political"], ["Political"]]                      # two clicks in a row: ONE extra paid run
    assert r["noDebounce"] is True                                           # an explicit Regenerate starts at once
    assert r["summary"] == "Result Political 2" and r["callsAfter"] == 3     # allowed again once finished
    assert rb["regenerateDuringDebounce"] == 1                               # a pick waiting out its debounce is not doubled up
    assert rb["regenerateNoPick"] == {"sent": [[]], "selectedSize": 0}       # no pick at all: re-run as None, and show it


def test_a_different_proposal_never_receives_a_pending_or_in_flight_result(rb):
    assert rb["resetDuringDebounce"] == 0
    assert rb["resetDuringRun"] == {"summary": "", "state": None, "content": False}
    assert rb["cancel"] == {"calls": 0, "busy": False}


def test_run_failures_leave_a_regenerate_button_and_clear_the_busy_flags(rb):
    e = rb["errorBody"]
    assert e["error"] is True and "Roadblocks check unavailable" in e["text"] and e["regenerateShown"] is True and e["state"] is None
    assert e["spinner"] is False and e["busy"] is False
    h = rb["errorHttp"]
    assert "Request failed: server exploded" in h["text"] and h["regenerateShown"] is True and h["busy"] is False


def test_a_saved_result_restores_the_pick_without_running_anything(rb):
    r = rb["restore"]
    assert r["none"]["selected"] == [] and r["none"]["summary"] == "Saved"                     # an empty list is the "None" pick
    assert r["political"]["selected"] == ["Political"]
    assert r["multi"]["selected"] == ["Political", "Cannabis"]                                  # an old multi-select save still shows both
    assert r["keeps"]["selected"] == ["Cannabis"]                                               # an existing pick is never overwritten by a result
    assert all(r[k]["calls"] == 0 and r[k]["timers"] == 0 for k in ("none", "political", "multi", "keeps"))
    assert r["noCategoriesField"] is True                                                       # a result without the field leaves the pick unset


def test_chips_are_a_radio_group_of_the_sheets_categories_with_none_last(rb):
    c = rb["chips"]
    names = lambda html: re.findall(r'data-rv-(?:category="([^"]*)"|none="1")', html)  # noqa: E731
    assert all('role="radio"' in c[k]["html"] for k in c)
    assert names(c["none_picked"]["html"]) == ["Political", "Cannabis", ""]                    # "" = the None chip, always last
    assert names(c["third"]["html"]) == ["Political", "Cannabis", "Casino/Gambling", ""]        # data-driven
    assert "rv-chip on" not in c["none_picked"]["html"]                                         # nothing is picked for the planner
    assert c["none_picked"]["html"].count('aria-checked="false"') == 3

    def on(html):
        return re.findall(r'class="rv-chip on"[^>]*?(data-rv-category="[^"]*"|data-rv-none="1")', html)

    assert on(c["political"]["html"]) == ['data-rv-category="Political"']
    assert on(c["empty_is_none"]["html"]) == ['data-rv-none="1"']
    assert on(c["multi"]["html"]) == ['data-rv-category="Political"', 'data-rv-category="Cannabis"']
    assert on(c["unresolved"]["html"]) == []                                                    # an empty saved list + unresolved names is not "None"
    assert "<b>" not in c["quote"]["html"] and "&lt;b&gt;" in c["quote"]["html"] and "&quot;" in c["quote"]["html"]   # names are escaped
    # the suggestion is a hint shown only until the planner has picked anything
    assert c["none_picked"]["hint"] == "Suggested from this request: Political. Nothing runs until you pick." and c["none_picked"]["hintHidden"] is False
    assert c["political"]["hintHidden"] is True and c["empty_is_none"]["hintHidden"] is True
    assert c["third"]["hintHidden"] is True                                                      # no suggestion, no hint


def test_arriving_on_the_step_only_shows_the_picker(rb):
    a = rb["arrive"]
    assert a["calls"] == 0                                                   # even after 10 s of fake time
    assert a["selected"] is None and a["onClasses"] == 0                     # the suggested category is NOT picked
    assert a["categories"] == ["Political", "Cannabis"] and a["wrapShown"] is True
    assert a["hint"] == "Suggested from this request: Political. Nothing runs until you pick." and a["hintHidden"] is False
    assert "built-in snapshot" in a["note"]
    assert a["spinner"] is False and a["regenerateShown"] is False           # no spinner, and Regenerate stays as the markup has it (hidden)


def test_without_the_category_list_the_picker_still_offers_none_and_it_runs(rb):
    f = rb["arriveFailed"]
    assert f["wrapShown"] is True and f["html"].count("<button") == 1 and 'data-rv-none="1"' in f["html"]
    assert "Couldn't load the restricted categories" in f["note"] and f["noteHidden"] is False
    assert f["selected"] is None and f["sent"] == [[]]


def test_reentering_the_step_keeps_the_pick_and_the_result_without_a_new_run(rb):
    r = rb["reenter"]
    assert r == {"calls": 0, "selected": ["Political"], "content": True, "summary": "Saved", "onClasses": 1}


# --------------------------------------------------------------------------------------------------------------------
# Static contracts (no engine)
# --------------------------------------------------------------------------------------------------------------------

def _html_between(start_marker: str, end_marker: str) -> str:
    s = INDEX_HTML.index(start_marker)
    return INDEX_HTML[s: INDEX_HTML.index(end_marker, s)]


def test_step_06_markup_has_the_master_button_next_to_the_toggle_inside_the_breakdown_content():
    content = _html_between('<div id="mb-content">', 'class="mb-plan-summary"')
    assert content.index('id="mb-mode-tabs"') < content.index('id="mb-apply-all-btn"')
    assert re.search(r'<button type="button" class="[^"]*mb-apply-all-btn[^"]*" id="mb-apply-all-btn">', content)
    # one row: the toggle, the master button, the explanation, then the hint
    assert content.index('id="mb-apply-all-btn"') < content.index('id="mb-mode-hint"') < content.index('id="mb-mode-notice"')
    for needed in ('id="mb-notice-text"', 'id="mb-notice-apply-btn"', 'id="mb-notice-dismiss-btn"'):
        assert needed in content
    assert "never rewrites a line you've already customized" not in INDEX_HTML       # the old, now-wrong explanation
    # Full Flight hides all of #mb-content (and therefore the button) — the existing CSS contract, still in place
    assert re.search(r"#step-6\.ff-mode #mb-content\s*\{\s*display:\s*none\s*!important", STYLES_CSS)


def test_step_06_wiring_is_static_and_the_hint_is_requested_by_flipping_the_toggle():
    wiring = APP_JS[APP_JS.index('querySelectorAll("#mb-mode-tabs .tier-tab")'):]
    wiring = wiring[: wiring.index("// By Product / By Month view toggle")]
    assert "state.mbModeNotice = true" in wiring
    for ident in ("mb-apply-all-btn", "mb-notice-apply-btn"):
        assert f'getElementById("{ident}").addEventListener("click", onMbApplyAll)' in wiring
    assert "mb-notice-dismiss-btn" in wiring
    # rendered for BOTH views: the call sits after the view split in renderMonthlyBreakdown
    render = _fn_source("renderMonthlyBreakdown")
    assert render.index("_mbWireLineItemBlocks(months)") < render.index("_mbRenderApplyControls(months)") < render.index("_mbRenderPlanSummary(months)")
    # a transient flag: never part of what is saved
    assert "mbModeNotice" not in _fn_source("_buildWizardStatePayload")
    assert "state.mbModeNotice = false" in _fn_source("_clearPerProposalState")


def test_the_master_apply_reuses_the_per_line_reset_math_and_writer():
    plan = _fn_source("_mbPlanRedistribution")
    assert "_mbDefaultAllocation(total, months)" in plan                  # the same dispatcher the per-line reset uses
    apply_fn = _fn_source("onMbApplyAll")
    assert "_mbSetAllocation(r.li, r.target)" in apply_fn
    assert "_isFullFlight()" in apply_fn
    # the per-line reset button is untouched
    assert "_mbSetAllocation(li, _mbDefaultAllocation(li.monthly_budget * li.months, months));" in _fn_source("_mbWireLineItemBlocks")


def test_step_07_markup_callout_first_then_the_category_chooser():
    step7 = _html_between('<section class="panel hidden" id="step-7">', "</section>")
    assert step7.index('id="rb-guidelines"') < step7.index('id="restricted-verticals"') < step7.index('id="roadblocks-loading"')
    assert step7.index('class="panel-head"') < step7.index('id="rb-guidelines"')
    link = re.search(r'<a [^>]*id="rb-guidelines-link"[^>]*>', step7)
    assert link, "the guidelines link is missing"
    tag = link.group(0)
    assert f'href="{GUIDELINES_URL}"' in tag and 'target="_blank"' in tag and 'rel="noopener noreferrer"' in tag
    assert "See latest information about Entravision's Ad Guidelines" in step7
    assert "guidelines repo" in step7
    assert INDEX_HTML.count(GUIDELINES_URL) == 1
    # the chooser: prompt, radiogroup, suggestion hint line, note
    assert "Does this client fall in a restricted category?" in step7 and "Pick one to run the check." in step7
    assert re.search(r'<div class="rv-chips" id="rv-chips" role="radiogroup"', step7)
    assert 'id="rv-suggest"' in step7 and 'id="rv-note"' in step7
    assert "tick any that apply" not in step7
    # Regenerate stays, hidden until there is something to re-run
    assert re.search(r'id="roadblocks-regenerate-btn" style="display:none"', step7)
    for nav in ('data-back="6"', 'id="roadblocks-skip-btn"', 'id="roadblocks-continue-btn"'):
        assert nav in step7


def test_step_07_never_runs_by_itself_and_the_old_confirm_loop_is_gone():
    enter = _fn_source("_enterRoadblocksStep")
    assert "_runRoadblocks" not in enter and "onRoadblocksGenerate" not in enter and "onRvPick" not in enter
    assert "autoRun" not in APP_JS and "rvUnconfirmed" not in APP_JS and "_roadblocksInFlight" not in APP_JS
    assert "Selection changed" not in APP_JS and "Run check" not in APP_JS
    # the only two starters of a run: a pick (after its debounce) and an explicit Regenerate
    owners = {_owner_of(m.start()) for m in re.finditer(r"(?<!function )\b_runRoadblocks\(", APP_JS_CODE)}
    assert owners == {"onRvPick", "onRoadblocksGenerate"}
    # nav pill / resume / Continue all just show the picker
    assert APP_JS.count("_enterRoadblocksStep()") >= 3 and "_enterRoadblocksStep(false)" not in APP_JS
    # leaving the proposal cancels a pending/in-flight run
    assert "_cancelRoadblocksRun();" in _fn_source("_clearPerProposalState")
    # the run only ever sends the single pick as a list ([] = None) and abandons stale answers by token
    run = _fn_source("_runRoadblocks")
    assert re.search(r"\n\s+categories,\s", run) and "state.rvSelected" in run
    assert "token !== _rbRunToken" in run and "signal: controller.signal" in run


def test_every_consumer_of_the_roadblocks_result_is_unchanged():
    assert "roadblocks: state.roadblocks || null" in _fn_source("_buildWizardStatePayload")      # draft + /api/generate payload
    gamma = _fn_source("buildGammaOutline")
    assert "const rb = state.roadblocks;" in gamma and "ROADBLOCKS / CONSIDERATIONS" in gamma
    # state.rvSelected is read by nothing but the picker and the run request (the exports never see it)
    owners = {_owner_of(m.start()) for m in re.finditer(r"rvSelected", APP_JS_CODE)}
    owners.discard(None)                       # the state declaration at the top of the file
    assert owners == {"_clearPerProposalState", "resetAll", "onRvPick", "onRoadblocksGenerate", "_runRoadblocks", "_renderRvChips",
                      "renderRoadblocks", "_showRoadblocksPending"}


def test_styles_for_the_new_controls_exist():
    for selector in (".mb-apply-all-btn", ".mb-mode-notice", ".mb-notice-link", ".rb-guidelines", ".rb-guidelines-link", ".rv-suggest"):
        assert re.search(rf"^{re.escape(selector)}[ ,{{:]", STYLES_CSS, re.M), selector


# --------------------------------------------------------------------------------------------------------------------
# The roadblocks endpoint contract the new flow relies on (no change was needed: [] already means "None")
# --------------------------------------------------------------------------------------------------------------------

def test_roadblocks_request_accepts_an_empty_or_missing_category_list():
    from app.main import RoadblocksRequest
    assert RoadblocksRequest(request={}, line_items=[], categories=[]).categories == []
    assert RoadblocksRequest(request={}, line_items=[]).categories is None
    assert RoadblocksRequest(request={}, line_items=[], categories=["Casino/Gambling"]).categories == ["Casino/Gambling"]


def test_none_means_no_confirmed_vertical_for_the_matrix_and_the_prompt():
    from app.services import restrictions as rs
    for empty in ([], None):
        matrix = rs.build_matrix(["Search - SEM"], empty)
        assert matrix["categories"] == [] and matrix["unresolved"] == []
        assert "NONE CONFIRMED" in rs.matrix_prompt_block(matrix)
    one = rs.build_matrix(["Search - SEM"], ["Political"])
    assert [c.name for c in one["categories"]] == ["Political"]
    assert "NONE CONFIRMED" not in rs.matrix_prompt_block(one)
    assert rs.build_matrix(["Search - SEM"], ["No Such Vertical"])["unresolved"] == ["No Such Vertical"]


def test_the_roadblocks_endpoint_forwards_the_planners_pick_unchanged():
    main_py = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    assert "strategy_brief=body.strategy_brief, categories=body.categories)" in main_py
