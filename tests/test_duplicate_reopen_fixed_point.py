"""Why the duplicate is made on the server, and the proof that the page accepts what the server makes.

A copy for a new client is stored as a draft and opened with the page's own reopen code (`/?reopen=<id>`), which then does what every open page does:
saving re-derives each line's months (`_resyncAllTierMonths`), and visiting Step 06 reconciles each split's period keys with the option's flight and rescales it
(`renderMonthlyBreakdown`). The design rests on two claims, pinned here by running the REAL functions out of app.js in a JavaScript engine (Node when installed,
else macOS's JavaScriptCore) over the state the real routes store and the real reopen route returns:

  1. FIXED POINT: for every billing period and every shape of source, and for a spread of new flights, opening and saving and visiting Step 06 changes NOTHING of
     what the server made: the same months, the same splits (or none), every kept split balanced to its line total and keyed by exactly the option's periods,
     no line flagged as a legacy "saved N", and the page's totals equal the server's. Twice over (a second pass changes nothing either).
  2. THE NAIVE ROUTE FAILS: take the same source and only replace its dates, as an "edit the dates in the page" copy would, and the same page code zeroes every
     split of the shifted flight and leaves an option's own window in the old year. That is why the dates are re-keyed by position on the server.

If anyone changes the page's reconcile rules so that a freshly made copy changes when opened, claim 1 fails.

Set REQUIRE_JS_ENGINE=1 (CI does) to make a missing engine a failure instead of a skip: a skipped test enforces nothing.
"""
from __future__ import annotations

import asyncio
import copy
import importlib.util
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import main
from tests.fixtures import duplicate_sources as fx

ROOT = Path(__file__).resolve().parents[1]
FEB_APR = ("2027-02-01", "2027-04-30")


def _harness():
    spec = importlib.util.spec_from_file_location("_ff_frontend_harness_duplicate", ROOT / "tests" / "test_full_flight_frontend.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


H = _harness()
APP = H.APP_JS
CONSTS = ["FULL_FLIGHT", "FULL_FLIGHT_KEY", "TIME_UNITS", "_MB_CENT", "_MB_DEFAULT_SLACK"]
FUNCS = ["_isFullFlight", "_round2", "_mbParseDate", "_mbMonthKey", "_mbMonthLabel", "_mbLastDayOfMonth", "_mbDaysBetweenInclusive", "_mbDateRangeLabel",
         "_mbMonthsBetween", "_mbWeeksBetween", "_mbQuartersBetween", "_mbCombinedPeriodLabel", "_mbFullFlightPeriods", "_mbPeriodsBetween",
         "_mbApplyPeriodMerges", "_effectiveStartDate", "_effectiveEndDate", "_mbStampBaseline", "_mbRescaleLine", "_mbRescaleForBudgetChange",
         "_mbEvenDefaultAllocation", "_mbProratedDefaultAllocation", "_mbDefaultAllocation", "_resyncAllTierMonths", "_mbEffectiveMonths",
         "_mbEffectiveMonthsUnmerged", "_mbReconcileMonthsForDateChange", "_foldLineIntoOnePeriod", "_normalizeFullFlightState", "_mbAllocationsMatch",
         "_mbPlanRedistribution", "_curateDerivedMonths", "_normalizeTimeUnit"]

PRELUDE = r"""
var console = { warn: function () {}, log: function () {}, error: function () {} };
var state;

// What maybeReopenProposal does with a reopen payload (no DOM): the options become the active tier plus snapshots, the request becomes state.parsed.
function load(wire) {
  var tiers = (wire.tiers && wire.tiers.length) ? wire.tiers.slice().sort(function (a, b) { return a.label.localeCompare(b.label); })
    : [{ label: "A", name: null, geo: null, start_date: null, end_date: null, line_items: wire.line_items || [], avails_data: wire.avails_data || {} }];
  var mig = tiers.map(function (t) {
    return { label: t.label || "A", name: t.name || null, geo: t.geo || null, startDate: t.start_date || null, endDate: t.end_date || null,
             lineItems: (t.line_items || []).map(function (li) { return JSON.parse(JSON.stringify(li)); }), availsData: t.avails_data || {},
             periodMergeGroups: t.period_merge_groups || [] };
  });
  var active = mig[0], rest = mig.slice(1);
  state = { timeUnit: _normalizeTimeUnit(wire.time_unit), parsed: JSON.parse(JSON.stringify(wire.request)),
            activeTierLabel: active.label, activeTierName: active.name, activeTierGeo: active.geo,
            activeTierStartDate: active.startDate, activeTierEndDate: active.endDate, lineItems: active.lineItems, availsData: active.availsData,
            activeTierPeriodMergeGroups: active.periodMergeGroups, tiers: rest };
  _normalizeFullFlightState();
}

function switchTo(label) {                   // switchTier() without the rendering
  if (label === state.activeTierLabel) return;
  var idx = state.tiers.findIndex(function (t) { return t.label === label; });
  var target = state.tiers[idx];
  state.tiers.splice(idx, 1, { label: state.activeTierLabel, name: state.activeTierName, geo: state.activeTierGeo, startDate: state.activeTierStartDate,
    endDate: state.activeTierEndDate, lineItems: state.lineItems, availsData: state.availsData, periodMergeGroups: state.activeTierPeriodMergeGroups });
  state.activeTierLabel = label; state.activeTierName = target.name || null; state.activeTierGeo = target.geo || null;
  state.activeTierStartDate = target.startDate || null; state.activeTierEndDate = target.endDate || null;
  state.lineItems = target.lineItems; state.availsData = target.availsData; state.activeTierPeriodMergeGroups = target.periodMergeGroups || [];
}

function allLabels() { return [state.activeTierLabel].concat(state.tiers.map(function (t) { return t.label; })).sort(); }
function total(li) { return Math.round(li.monthly_budget * li.months * 100) / 100; }
function sum(o) { return Math.round(Object.keys(o).reduce(function (s, k) { return s + o[k]; }, 0) * 100) / 100; }

// What opening the draft does to the numbers: saving (_buildWizardStatePayload -> _resyncAllTierMonths), then Step 06's render loop (renderMonthlyBreakdown) for
// each option: reconcile a split's period keys to the option's periods, seed the baseline (the first visit) or rescale to a budget change (later visits).
function openAndVisitStep06() {
  var out = {}, before = {};
  allLabels().forEach(function (label) {
    switchTo(label);
    before[label] = state.lineItems.map(function (li) {
      return { id: li.id, months: li.months, alloc: li.monthly_allocations ? JSON.parse(JSON.stringify(li.monthly_allocations)) : null, total: total(li) };
    });
  });
  _resyncAllTierMonths();
  allLabels().forEach(function (label) {
    switchTo(label);
    var months = _mbEffectiveMonths();
    var rows = [];
    state.lineItems.forEach(function (li, i) {
      var b = before[label][i];
      var row = { id: li.id, months_before: b.months, months_after: li.months, reconciled: li._monthsReconciled, total_before: b.total, total_after: total(li),
                  alloc_before: b.alloc };
      if (!li.is_added_value && li.monthly_allocations && Object.keys(li.monthly_allocations).length && months && months.length) {
        li.monthly_allocations = _mbReconcileMonthsForDateChange(li, months);
        if (li._mbBaseline === undefined) li._mbBaseline = li.monthly_budget * li.months; else li.monthly_allocations = _mbRescaleForBudgetChange(li);
      }
      row.alloc_after = li.monthly_allocations ? JSON.parse(JSON.stringify(li.monthly_allocations)) : null;
      row.balanced = row.alloc_after ? Math.abs(sum(row.alloc_after) - total(li)) <= 0.005 * Math.max(1, Object.keys(row.alloc_after).length) : null;
      row.period_keys = months ? months.map(function (m) { return m.key; }) : null;
      rows.push(row);
    });
    out[label] = { start: state.activeTierStartDate, end: state.activeTierEndDate, merges: state.activeTierPeriodMergeGroups, lines: rows };
  });
  return out;
}
"""


def run_js(body: str) -> str:
    """The shared harness's engine call, with a missing or unusable engine a SKIP, or a FAILURE under REQUIRE_JS_ENGINE=1."""
    required = os.environ.get("REQUIRE_JS_ENGINE") == "1"
    if H._engine_command() is None:
        if required:
            pytest.fail("REQUIRE_JS_ENGINE=1 but there is no JavaScript engine (node, or macOS JavaScriptCore through osascript)")
        pytest.skip("neither node nor macOS JavaScriptCore (osascript) is available")
    try:
        return H._run_js(body)
    except AssertionError as exc:
        if "JS engine failed" in str(exc) and "osascript" in str(exc).lower():
            if required:
                raise
            pytest.skip(f"JavaScript engine unusable here: {exc}")
        raise


# ====================================================================================================================== the scenarios

CASES = [
    ("monthly 3 to 3", fx.source_monthly, FEB_APR), ("monthly 3 to 4", fx.source_monthly, ("2027-02-01", "2027-05-31")),
    ("monthly 3 to 1", fx.source_monthly, ("2027-02-01", "2027-02-28")), ("monthly 3 to 8 across a year", fx.source_monthly, ("2026-11-15", "2027-06-20")),
    ("monthly partial first month", fx.source_monthly, ("2027-02-17", "2027-04-30")), ("monthly same dates", fx.source_monthly, ("2026-09-01", "2026-11-30")),
    ("merged same shape", fx.source_monthly_merged, FEB_APR), ("merged different shape", fx.source_monthly_merged, ("2027-02-01", "2027-05-31")),
    ("weekly 13 to 13", fx.source_weekly, FEB_APR), ("weekly 13 to 15", fx.source_weekly, ("2027-02-01", "2027-05-14")),
    ("weekly 13 to 4", fx.source_weekly, ("2027-02-01", "2027-02-28")),
    ("quarterly 2 to 2", fx.source_quarterly, ("2027-03-01", "2027-08-31")), ("quarterly 2 to 3", fx.source_quarterly, ("2027-03-01", "2027-11-30")),
    ("full flight 3 to 8 months", fx.source_full_flight, ("2027-02-01", "2027-09-30")),
    ("legacy single option", fx.source_legacy_single_tier, FEB_APR), ("no readable source dates", fx.source_without_dates, FEB_APR),
    ("four options with their own windows", fx.source_four_tiers, FEB_APR),
]


def request_of(email: str = "planner@entravision.com") -> SimpleNamespace:
    return SimpleNamespace(state=SimpleNamespace(user={"email": email, "is_admin": False}), client=SimpleNamespace(host="127.0.0.1"), headers={"user-agent": "pytest"})


def jsonb(value):
    """What Postgres hands back: every object's keys re-ordered. Nothing may depend on key order."""
    def order(pairs):
        return dict(sorted(pairs, key=lambda kv: (len(kv[0].encode("utf-8")), kv[0].encode("utf-8"))))
    return json.loads(json.dumps(value), object_pairs_hook=order)


def made_through_the_routes(monkeypatch, source_state: dict, start: str, end: str) -> tuple[dict, dict]:
    """(what the duplicate route answered, the payload the reopen route then serves for the copy): the real routes over an in-memory table."""
    rows: dict[str, dict] = {"SRC": {"proposal_id": "SRC", "created_by_email": "planner@entravision.com", "status": "draft", "proposal_title": "t", "client_name": "Acme Motors",
                                      "reopen_state": jsonb(source_state), "updated_at": datetime(2026, 11, 1, tzinfo=timezone.utc)}}

    def get(pid):
        return main._proposal_metadata_from_row(copy.deepcopy(rows[pid])) if pid in rows else None

    def put(**kw):
        rows[kw["proposal_id"]] = {**kw, "reopen_state": jsonb(kw["reopen_state"]), "status": "draft", "updated_at": datetime.now(timezone.utc)}
        return True

    monkeypatch.setattr(main, "_get_proposal_metadata", get)
    monkeypatch.setattr(main, "_insert_draft_if_absent", put)
    body = main.DuplicateProposalRequest(request_id="dialog-request-0123456789", client_name="Beta Burgers", start_date=start, end_date=end, notion_id="50001")
    answer = asyncio.run(main.duplicate_proposal("SRC", body, request_of()))
    return answer, asyncio.run(main.reopen_proposal(answer["proposal_id"]))


@pytest.fixture(scope="module")
def engine_results():
    """Every scenario in ONE engine launch: {name: {"server": totals the server reported, "passes": [first visit, second visit]}} plus the naive-route result."""
    patch = pytest.MonkeyPatch()
    try:
        scenarios = []
        for name, build, (start, end) in CASES:
            answer, wire = made_through_the_routes(patch, build(), start, end)
            scenarios.append({"name": name, "wire": wire, "server_totals": answer["stats"]["totals_after"]})
        naive = fx.source_monthly()                                                    # only the dates replaced: no transform
        naive["request"].update(start_date=FEB_APR[0], end_date=FEB_APR[1])
        scenarios.append({"name": "naive", "wire": jsonb(naive), "server_totals": None})
    finally:
        patch.undo()
    functions = "\n".join([H._extract_const(APP, n) for n in CONSTS] + [H._extract_fn(APP, n) for n in FUNCS])
    body = (functions + PRELUDE + "\nvar scenarios = " + json.dumps(scenarios) + ";\n"
            "return JSON.stringify(scenarios.map(function (s) { load(s.wire); var first = openAndVisitStep06(); var second = openAndVisitStep06();\n"
            "  return { name: s.name, server_totals: s.server_totals, passes: [first, second] }; }));\n")
    results = {r["name"]: r for r in json.loads(run_js(body))}
    assert set(results) == {name for name, _, _ in CASES} | {"naive"}
    return results


# ====================================================================================================================== 1. the fixed point

@pytest.mark.parametrize("name", [name for name, _, _ in CASES])
def test_opening_and_visiting_step_06_changes_nothing_the_server_made(engine_results, name):
    result = engine_results[name]
    for visit, optionwise in enumerate(result["passes"], start=1):
        page_totals = {}
        for label, option in optionwise.items():
            page_totals[label] = round(sum(line["total_after"] for line in option["lines"]), 2)
            for line in option["lines"]:
                where = f"{name} / option {label} / {line['id']} / visit {visit}"
                assert line["months_after"] == line["months_before"], where                       # months re-derived by the page: the same
                assert line["reconciled"] is True, where                                         # no line flagged as a legacy "saved N"
                assert line["total_after"] == line["total_before"], where
                assert line["alloc_after"] == line["alloc_before"], where                        # the split: untouched (or still none)
                if line["alloc_after"]:
                    assert line["balanced"] is True, where                                       # adds up to the line's total
                    assert sorted(line["alloc_after"]) == sorted(line["period_keys"]), where     # keyed by exactly the option's periods
        assert page_totals == result["server_totals"], (name, visit)                              # the page's money is the server's money


def test_the_copy_keeps_a_hand_made_split_where_the_shape_allows_it(engine_results):
    """(so the fixed-point test above is not passing because there was nothing to keep)"""
    kept = {name: [l for o in engine_results[name]["passes"][0].values() for l in o["lines"] if l["alloc_after"]]
            for name in ("monthly 3 to 3", "merged same shape", "weekly 13 to 13", "quarterly 2 to 2", "legacy single option")}
    assert all(kept.values()), {n: len(v) for n, v in kept.items()}
    assert [l["alloc_after"] for l in kept["monthly 3 to 3"] if l["id"] == "li_fb"] == [{"2027-02": 5000, "2027-03": 15000, "2027-04": 10000}]
    assert all(not l["alloc_after"] for o in engine_results["full flight 3 to 8 months"]["passes"][0].values() for l in o["lines"])


def test_every_option_of_a_copy_ends_with_no_dates_of_its_own_in_the_page(engine_results):
    for name in ("monthly 3 to 3", "four options with their own windows"):
        for label, option in engine_results[name]["passes"][0].items():
            assert option["start"] is None and option["end"] is None, (name, label)


# ====================================================================================================================== 2. the naive route fails

def test_replacing_only_the_dates_makes_the_page_zero_every_split_and_leave_an_options_window_in_the_old_year(engine_results):
    """The reason the transform exists: this is what the page's own reconcile does to a flight that moved."""
    naive = engine_results["naive"]["passes"][0]
    lines = {line["id"]: line for option in naive.values() for line in option["lines"]}
    fb = lines["li_fb"]
    assert fb["alloc_before"] == {"2026-09": 5000, "2026-10": 15000, "2026-11": 10000}
    assert fb["alloc_after"] == {"2027-02": 0, "2027-03": 0, "2027-04": 0}               # every dollar of the hand-made split is gone...
    assert fb["balanced"] is False and fb["total_after"] == 30000                          # ...so the line no longer adds up to its own total
    assert lines["li_ctv"]["alloc_after"] == {"2027-02": 0, "2027-03": 0, "2027-04": 0} and lines["li_ctv"]["balanced"] is False
    assert naive["B"]["start"] == "2026-09-01" and naive["B"]["end"] == "2026-12-31"       # option B still runs in 2026
    assert naive["B"]["merges"] == [["2026-09", "2026-10"]] and all(key.startswith("2026") for key in lines["li_b1"]["alloc_after"])       # and still in 2026 keys


def test_the_same_source_through_the_transform_keeps_what_the_naive_route_loses(engine_results):
    transformed = engine_results["monthly 3 to 3"]["passes"][0]
    lines = {line["id"]: line for option in transformed.values() for line in option["lines"]}
    assert lines["li_fb"]["alloc_after"] == {"2027-02": 5000, "2027-03": 15000, "2027-04": 10000} and lines["li_fb"]["balanced"] is True
    assert transformed["B"]["start"] is None


def test_the_scenario_list_covers_every_billing_period_and_every_source_shape():
    assert {fn.__name__ for _, fn, _ in CASES} == {"source_monthly", "source_monthly_merged", "source_weekly", "source_quarterly", "source_full_flight",
                                                  "source_legacy_single_tier", "source_without_dates", "source_four_tiers"}
    assert {fn()["time_unit"] for _, fn, _ in CASES} == {"month", "week", "quarter", "full_flight"}
