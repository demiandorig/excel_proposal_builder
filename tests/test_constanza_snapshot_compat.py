"""What saved proposals need when Meta became a Constanza method and every method's last estimate moved into ONE generic field.

A Step 05 line used to keep a Meta estimate under `availsData[line].meta_forecast`; every method now keeps its last estimate under `availsData[line].forecast_snapshot`
(`{v, method, saved_at, request, result, line}`), and the Meta plugin still READS the old shape (`legacy.adopt`). Proposals saved by the old page, by the new page, by a
tab left open across the deploy (both shapes) and by anyone with a hand-edited file must all save, reopen, draw and export exactly as before. The design's list of
compatibility cases (4.4) is below, with where each one is pinned. This file holds the cases NOT covered elsewhere: the entry's round trip through the real draft route and
the reopen route (with the database's key re-ordering), the generated workbook, and the judgement of a snapshot that came back from the database.

  #  The case                                                                          Where it is pinned
  1  AvailsEntry keeps legacy-only / native-only / both, drops oversized / malformed     here (the real saved shapes, test_an_entry_*) and tests/test_constanza_methods.py
     and unknown keys, and is stable under model_dump -> AvailsEntry                      (test_an_avails_entry_keeps_the_legacy_meta_field..., test_a_saved_estimate_*)
  2  POST /api/proposal/draft -> reopen_state -> the reopen route, key order shuffled    here (test_the_draft_route_*, test_the_reopen_route_*, test_over_http_*)
  3  Excel invariance: the workbook is the same for {numbers}, + meta_forecast,          here (test_the_workbook_*)
     + forecast_snapshot, + both
  4  every old shape adopts and renders with the old texts                               tests/test_constanza_meta_frontend.py (test_every_old_saved_shape_is_adopted...,
                                                                                         test_an_old_estimate_without_languages...) and test_constanza_step05_block_dom.py
                                                                                         (test_an_older_live_estimate_is_read..., test_an_estimate_entered_by_hand...,
                                                                                         test_a_budget_only_older_estimate_asks_for_an_audience...)
  5  stale parity: the six conditions the old card compared                              tests/test_constanza_meta_frontend.py (test_an_adopted_old_estimate_through_the_dock_is_fresh_...)
                                                                                         and test_constanza_step05_block_dom.py (test_an_older_saved_estimate_goes_stale_for_the_same_six_reasons)
  6  Insert from an adopted card writes what the old Insert wrote, leaves it as saved   tests/test_constanza_step05_block_dom.py (test_insert_from_an_older_saved_estimate_leaves_it_exactly_as_saved)
  7  Recalculate from an adopted estimate rebuilds the request and stores the new shape  tests/test_constanza_step05_block_dom.py (test_recalculate_from_an_older_saved_estimate_rebuilds_...)
  8  snapshot precedence: the newer shape wins; another method's is kept, never drawn    tests/test_constanza_step05_block_dom.py (test_the_newer_of_the_saved_shape_and_the_older_one_is_the_one_drawn,
                                                                                         test_a_line_only_reads_an_estimate_of_its_own_method_and_never_deletes_another)
  9  key order: a snapshot re-sorted by the database shows no "form has changed";        here (test_a_snapshot_that_came_back_from_the_database_*) for the whole path, and
     a changed choice, month count or budget does                                         tests/test_constanza_meta_dom.py (test_a_saved_estimate_whose_keys_were_reordered_...)
 10  corrupt snapshots on Step 05 and in the dock                                         tests/test_constanza_step05_block_dom.py (test_a_corrupt_or_hostile_saved_estimate_takes_...),
                                                                                         test_constanza_meta_dom.py (test_a_corrupt_saved_estimate_does_not_break_the_dock...) and
                                                                                         tests/test_constanza_method_contract.py (C14, for every method)
 11  Insert from an adopted manual estimate says "entered audience"; a budget-only one   tests/test_constanza_step05_block_dom.py (test_insert_from_an_estimate_entered_by_hand_..., test_a_budget_only_older_estimate_offers_no_insert)
     offers no Insert
 12  months: a 2-calendar-month, 22-day flight sends 2, files fresh, until the box is     tests/test_constanza_step05_block_dom.py (test_a_pull_from_the_dock_for_the_lines_calendar_months_is_filed_fresh_under_the_line)
     edited
 13  cross-line Insert, a pull for another SKU, a line deleted while pending              tests/test_constanza_step05_block_dom.py (test_insert_from_the_dock_goes_to_the_chosen_line_..., test_an_answer_for_a_product_the_line_no_longer_has_...,
                                                                                         test_an_answer_for_a_line_that_was_deleted_leaves_no_entry_behind)

`test_the_cross_referenced_tests_still_exist` keeps the table honest: it fails when a test named above is renamed or removed.

The interface facts these tests rely on (from the packages that built them): for an inserted live Meta estimate `geo_resolution.dmas` holds the FULL Meta place label ("Los Angeles · California · US"), not
"Los Angeles"; an Insert from an adopted older card hands applyConstanzaEstimate `null` (not undefined) as its fifth argument, so nothing is rewritten; a manual / budget-only adopt gives
dmas ["entered audience"]; adopt adds a non-stored key `legacy_budget_check`; a snapshot is drawn only when snapshot.result.method === snapshot.method; onDirty(null) redraws every Step 05 card.

Needs no Postgres: the draft and reopen handlers run with their two database functions replaced, the workbook is generated with the offline product list (conftest), and the
JavaScript half runs in a JS engine (Node or macOS JavaScriptCore; set REQUIRE_JS_ENGINE=1 to make a missing one a failure, as tests/test_constanza_method_contract.py does)."""
from __future__ import annotations

import asyncio
import copy
import json
import re
from pathlib import Path
from types import SimpleNamespace

import openpyxl
import pytest

import app.main as main
from app.services.notion_parser import ProposalRequest
from app.services.proposal_generator import LineItem, generate_proposal
from tests import test_constanza_method_contract as contract
from tests.fixtures import meta_snapshots as fx

ROOT = Path(__file__).resolve().parents[1]
AWARENESS = "Facebook & Instagram Ads | Awareness"

LEGACY_NAMES = list(fx.LEGACY)                                      # live+languages, live, manual, budget-only
NUMBERS = {                                                         # what a Meta line holds in Avails after an Insert (the Excel writer reads only these)
    "max_imps": 5_500_000, "max_imps_estimated": True, "basis": "imps", "est_uniques": 1_000_000, "frequency": 5.5, "max_spend": 66000, "max_spend_estimated": True,
    "geo_resolution": {"method": "meta_audience", "dmas": ["Los Angeles · California · US"], "unmatched_tokens": [], "win_rate": None, "hispanic_share": None},
}


def jsonb(value):
    """What Postgres's JSONB column hands back: every object's keys re-ordered (shorter keys first, then by bytes), duplicates gone. Nothing may depend on key order."""
    def order(pairs):
        return dict(sorted(pairs, key=lambda kv: (len(kv[0].encode("utf-8")), kv[0].encode("utf-8"))))
    return json.loads(json.dumps(value), object_pairs_hook=order)


def oversized_native():
    snap = fx.native()
    snap["result"]["pad"] = "x" * (100 * 1024 + 10)                 # the limit is 100 KB per entry's snapshot (written as a number here, not read from the code under test)
    return snap


def malformed_native():
    snap = fx.native()
    del snap["v"]
    return snap


# every way an entry can arrive: name -> (the entry's snapshot fields, what the server must keep of them)
SHAPES = {
    "numbers only": ({}, {"meta_forecast": None, "forecast_snapshot": None}),
    **{f"legacy only ({name})": ({"meta_forecast": copy.deepcopy(fx.LEGACY[name])}, {"meta_forecast": fx.LEGACY[name], "forecast_snapshot": None}) for name in LEGACY_NAMES},
    "native only": ({"forecast_snapshot": fx.native()}, {"meta_forecast": None, "forecast_snapshot": fx.native()}),
    "both (a tab left open across the deploy)": ({"meta_forecast": copy.deepcopy(fx.LIVE_WITH_LANGUAGES), "forecast_snapshot": fx.native()},
                                                {"meta_forecast": fx.LIVE_WITH_LANGUAGES, "forecast_snapshot": fx.native()}),
    "native is null": ({"meta_forecast": copy.deepcopy(fx.MANUAL), "forecast_snapshot": None}, {"meta_forecast": fx.MANUAL, "forecast_snapshot": None}),
    "native is over 100 KB": ({"meta_forecast": copy.deepcopy(fx.LIVE_WITH_LANGUAGES), "forecast_snapshot": oversized_native()},
                              {"meta_forecast": fx.LIVE_WITH_LANGUAGES, "forecast_snapshot": None}),
    "native has no version": ({"forecast_snapshot": malformed_native()}, {"meta_forecast": None, "forecast_snapshot": None}),
    "native names no method": ({"forecast_snapshot": {**fx.native(), "method": None}}, {"meta_forecast": None, "forecast_snapshot": None}),
    "native holds a request that is a list": ({"forecast_snapshot": {**fx.native(), "request": ["x"]}}, {"meta_forecast": None, "forecast_snapshot": None}),
}


def entry_of(name: str) -> dict:
    return {**copy.deepcopy(NUMBERS), **copy.deepcopy(SHAPES[name][0])}


def kept_of(name: str) -> dict:
    return {**copy.deepcopy(NUMBERS), **copy.deepcopy(SHAPES[name][1])}


# ================================================================================================================================ 1. the entry

@pytest.mark.parametrize("name", list(SHAPES))
def test_an_entry_keeps_the_saved_shapes_that_are_well_formed_and_drops_the_rest_without_failing_the_save(name):
    dumped = main.AvailsEntry(**entry_of(name)).model_dump()
    expected = kept_of(name)
    for field, value in expected.items():
        assert dumped[field] == value, (name, field)
    assert dumped["max_imps"] == NUMBERS["max_imps"] and dumped["est_uniques"] == NUMBERS["est_uniques"]          # the rest of the entry is never touched
    assert main.AvailsEntry(**dumped).model_dump() == dumped                                                         # a save -> reopen -> save round trip is stable


def test_an_entry_still_drops_a_key_it_does_not_declare_which_is_why_the_snapshot_field_is_declared():
    dumped = main.AvailsEntry(**{**NUMBERS, "forecast_snapshot": fx.native(), "something_a_later_release_adds": {"x": 1}}).model_dump()
    assert "something_a_later_release_adds" not in dumped and dumped["forecast_snapshot"] == fx.native()


# ================================================================================================================================ 2. the draft route and the reopen route

def _request() -> SimpleNamespace:
    return SimpleNamespace(state=SimpleNamespace(user={"email": "planner@entravision.com", "is_admin": False}), client=SimpleNamespace(host="127.0.0.1"), headers={"user-agent": "pytest"})


def _draft(avails: dict | None = None, tiers: list | None = None, **extra) -> main.DraftSaveRequest:
    line = {"id": "L1", "product_name": AWARENESS, "monthly_budget": 2000, "months": 3}
    fields = {"request": {"client_name": "Acme", "salesperson_email": "ae@entravision.com"}, "wizard_step": 5, "line_items": [] if tiers else [line],
              "avails_data": avails, "tiers": tiers, "proposal_title": "Acme draft"}
    fields.update(extra)
    return main.DraftSaveRequest(**fields)


def _saved_then_reopened(monkeypatch, body: main.DraftSaveRequest) -> tuple[dict, dict]:
    """POST /api/proposal/draft's handler with its one database write captured, then the reopen route's handler over what the database would hand back
    (JSON with every object's keys re-ordered). -> (the reopen_state that was saved, the reopen route's answer)."""
    saved: dict = {}

    def fake_save(*, reopen_state, **kwargs):
        saved.update(copy.deepcopy(reopen_state))

    monkeypatch.setattr(main, "_save_proposal_metadata", fake_save)
    result = asyncio.run(main.save_draft(body, _request()))
    assert result["proposal_id"]
    stored = jsonb(saved)                                                                  # what Postgres would return for it
    monkeypatch.setattr(main, "_get_proposal_metadata", lambda pid: {"status": "draft", "proposal_title": "Acme draft", "reopen_state": copy.deepcopy(stored)})
    return saved, asyncio.run(main.reopen_proposal(result["proposal_id"]))


@pytest.mark.parametrize("name", list(SHAPES))
def test_the_draft_route_saves_every_shape_and_the_reopen_route_gives_it_back_though_the_database_reorders_keys(monkeypatch, name):
    saved, reopened = _saved_then_reopened(monkeypatch, _draft({"L1": entry_of(name)}))
    got = reopened["avails_data"]["L1"]
    expected = kept_of(name)
    for field, value in expected.items():
        assert got[field] == value, (name, field)                                          # equal as parsed JSON: key order is not a thing that survives or matters
    assert got["max_imps"] == NUMBERS["max_imps"] and got["geo_resolution"] == NUMBERS["geo_resolution"]
    assert saved["avails_data"]["L1"]["forecast_snapshot"] == expected["forecast_snapshot"]


def test_the_reopen_state_carries_each_option_tabs_snapshots_too(monkeypatch):
    """Add Option: every tier's own avails travel with it (`tiers[].avails_data`), and the first tier's also fills the proposal-wide field."""
    tiers = [main.TierModel(label="A", line_items=[main.LineItemModel(id="L1", product_name=AWARENESS, monthly_budget=2000)], avails_data={"L1": main.AvailsEntry(**entry_of("native only"))}),
             main.TierModel(label="B", line_items=[main.LineItemModel(id="L2", product_name=AWARENESS, monthly_budget=3000)],
                            avails_data={"L2": main.AvailsEntry(**entry_of("legacy only (live+languages)"))})]
    saved, reopened = _saved_then_reopened(monkeypatch, _draft(tiers=tiers))
    assert reopened["tiers"][0]["avails_data"]["L1"]["forecast_snapshot"] == fx.native() and reopened["tiers"][0]["avails_data"]["L1"]["meta_forecast"] is None
    assert reopened["tiers"][1]["avails_data"]["L2"]["meta_forecast"] == fx.LIVE_WITH_LANGUAGES and reopened["tiers"][1]["avails_data"]["L2"]["forecast_snapshot"] is None
    assert reopened["avails_data"]["L1"]["forecast_snapshot"] == fx.native()               # (the proposal-wide field is the first tier's)


def test_a_hostile_saved_snapshot_is_stored_as_text_and_comes_back_as_text(monkeypatch):
    """Nothing in a snapshot is interpreted on the server: markup in every string goes in and comes out as the same text (the page escapes it; C14 proves that)."""
    hostile = fx.hostile(fx.native())
    saved, reopened = _saved_then_reopened(monkeypatch, _draft({"L1": {**NUMBERS, "forecast_snapshot": hostile}}))
    assert reopened["avails_data"]["L1"]["forecast_snapshot"] == hostile


@pytest.fixture
def http(monkeypatch):
    """The real app over HTTP (routing, request models, the login rule) with a stubbed session and no database, as tests/test_constanza_methods.py does."""
    try:
        from fastapi.testclient import TestClient
    except (ImportError, RuntimeError) as exc:
        pytest.skip(f"no HTTP test client here: {exc}")
    monkeypatch.setattr(main.auth_svc, "get_user_by_session", lambda token: {"id": 1, "email": "planner@entravision.com", "is_admin": False})
    monkeypatch.setattr(main.auth_svc, "refresh_session", lambda token: None)
    return TestClient(main.app, cookies={main.SESSION_COOKIE_NAME: "test-session"})


def test_over_http_the_draft_body_with_either_shape_is_accepted_and_what_is_stored_is_the_declared_fields_only(http, monkeypatch):
    stored: dict = {}
    monkeypatch.setattr(main, "_save_proposal_metadata", lambda *, reopen_state, **kw: stored.update(copy.deepcopy(reopen_state)))
    body = json.loads(_draft({"L1": entry_of("both (a tab left open across the deploy)")}).model_dump_json())
    body["avails_data"]["L1"]["unknown_key"] = {"x": 1}                                    # a field the model does not declare
    answer = http.post("/api/proposal/draft", json=body)
    assert answer.status_code == 200, answer.text
    entry = stored["avails_data"]["L1"]
    assert entry["forecast_snapshot"] == fx.native() and entry["meta_forecast"] == fx.LIVE_WITH_LANGUAGES and "unknown_key" not in entry


def test_over_http_a_malformed_or_oversized_snapshot_never_fails_the_save(http, monkeypatch):
    stored: dict = {}
    monkeypatch.setattr(main, "_save_proposal_metadata", lambda *, reopen_state, **kw: stored.update(copy.deepcopy(reopen_state)))
    for snapshot in (malformed_native(), oversized_native(), "not an object", 7, [1, 2]):
        body = json.loads(_draft({"L1": NUMBERS}).model_dump_json())
        body["avails_data"]["L1"]["forecast_snapshot"] = snapshot
        answer = http.post("/api/proposal/draft", json=body)
        assert answer.status_code == 200, (str(snapshot)[:40], answer.text)
        assert stored["avails_data"]["L1"]["forecast_snapshot"] is None and stored["avails_data"]["L1"]["max_imps"] == NUMBERS["max_imps"]


# ================================================================================================================================ 3. the workbook

@pytest.fixture
def offline_catalog(monkeypatch):
    monkeypatch.setattr("app.catalog.load_rate_overrides", lambda: {})
    monkeypatch.setattr("app.catalog.load_custom_products", lambda: [])
    monkeypatch.setattr("app.catalog.load_deleted_builtin_names", lambda: set())
    monkeypatch.setattr("app.market_config.load_market_config", lambda: {})


def _plain(value):
    """A cell's value as something that compares: an array formula is an object, so it is read as (its range, its text)."""
    return ("array formula", value.ref, value.text) if hasattr(value, "ref") and hasattr(value, "text") else value


def _sheet_cells(path: Path) -> dict:
    ws = openpyxl.load_workbook(path)["Proposal A"]
    return {cell.coordinate: (_plain(cell.value), cell.number_format) for row in ws.iter_rows() for cell in row if cell.value is not None}


def _workbook(tmp_path: Path, name: str, entry: dict) -> tuple[Path, dict]:
    """The workbook generate_proposal writes for one Meta line whose entry is `entry` after the server's own model (AvailsEntry.model_dump is what /api/generate hands the writer)."""
    request = ProposalRequest(client_name="Acme", request_type="New Business", start_date="2026-10-01", end_date="2026-12-31", total_months=3)
    line = LineItem(product_name=AWARENESS, monthly_budget=2000.0, months=3, id="L1")
    path = tmp_path / f"{re.sub(r'[^a-z0-9]+', '_', name.lower())}.xlsx"
    generate_proposal(request, [], path, tiers=[{"label": "A", "line_items": [line], "avails_data": {"L1": main.AvailsEntry(**entry).model_dump()}}])
    return path, _sheet_cells(path)


def test_the_workbook_is_the_same_whatever_estimate_a_line_holds_beside_its_numbers(tmp_path, offline_catalog):
    """Item 3: {numbers}, {numbers + meta_forecast}, {numbers + forecast_snapshot}, {numbers + both} - and the cases the server drops - write the same Avails cells (N imps, O spend,
    P uniques, Q share of voice) and the same everything else: the Excel writer reads only the numeric and text fields of an entry."""
    reference_path, reference = _workbook(tmp_path, "numbers only", entry_of("numbers only"))
    row = next(int(re.sub(r"[A-Z]+", "", c)) for c, (v, _) in reference.items() if c.startswith("C") and isinstance(v, str) and v.startswith("Facebook"))
    assert reference[f"N{row}"][0] == 5_500_000 and reference[f"P{row}"][0] == 1_000_000
    assert str(reference[f"O{row}"][0]).startswith("=IFERROR(") and f"N{row}" in reference[f"O{row}"][0]            # spend is a live formula over the imps cell
    assert reference[f"Q{row}"][0] is not None                                                                     # the share-of-voice cell
    for name in SHAPES:
        _, cells = _workbook(tmp_path, name, entry_of(name))
        assert {c: cells.get(c) for c in (f"N{row}", f"O{row}", f"P{row}", f"Q{row}")} == {c: reference.get(c) for c in (f"N{row}", f"O{row}", f"P{row}", f"Q{row}")}, name
        assert cells == reference, f"{name}: the workbook differs from the one for the numbers alone"


def test_the_workbook_for_a_proposal_reopened_from_the_database_is_the_same_too(tmp_path, offline_catalog, monkeypatch):
    """The whole loop: the entry goes through the draft route and the reopen route (keys re-ordered by the database) and the workbook made from what comes back is the original's."""
    _, reference = _workbook(tmp_path, "reference", entry_of("numbers only"))
    for name in ("native only", "both (a tab left open across the deploy)", "legacy only (live+languages)"):
        _, reopened = _saved_then_reopened(monkeypatch, _draft({"L1": entry_of(name)}))
        _, cells = _workbook(tmp_path, f"reopened {name}", reopened["avails_data"]["L1"])
        assert cells == reference, name


# ================================================================================================================================ 9. a snapshot that came back from the database

LINE = dict(fx.LINE_FACTS)                                         # (the line the fixtures were pulled for)


def _judged(snapshot: dict, line: dict) -> dict:
    """The real constanza.js and the real Meta plugin in a JS engine: the saved snapshot's stale reasons for `line`, and whether the form it restores differs from it."""
    meta_js = contract.plugin_source_of("meta")
    body = (
        contract.js_page(meta_js, "meta")
        + f"var SNAP = {json.dumps(snapshot)}, LINE = {json.dumps(line)}, ORIGINAL = {json.dumps(fx.native())};\n"
        "var out = { registered: __res.meta === true, errors: __errors };\n"
        "P.onLine(LINE);\n"
        "P.restore(SNAP.request.meta);\n"
        "out.stale = Z.snapshotStale(SNAP, LINE);\n"
        "out.sameRequest = Z.canonicalJson(SNAP.request) === Z.canonicalJson(ORIGINAL.request) && JSON.stringify(SNAP.request) !== JSON.stringify(ORIGINAL.request);\n"
        "out.formDiffers = P.choicesChanged(LINE.id, SNAP.request.meta);\n"
        "P._pure.sel().gender = P._pure.sel().gender === 'male' ? 'female' : 'male';\n"
        "out.formDiffersAfterAChange = P.choicesChanged(LINE.id, SNAP.request.meta);\n"
        "return JSON.stringify(out);"
    )
    return contract.run_js(body)


def test_a_snapshot_that_came_back_from_the_database_is_judged_by_what_it_says_not_by_its_key_order(monkeypatch):
    """Item 9, through the real reopen path: the snapshot the reopen route returns has its keys re-sorted by the database. Against the line it was pulled for it is fresh and the form it
    restores equals it; a changed budget or month count marks it stale; a changed choice makes the form differ. (A JSON.stringify comparison would call it changed every time.)"""
    _, reopened = _saved_then_reopened(monkeypatch, _draft({"L1": entry_of("native only")}))
    snapshot = reopened["avails_data"]["L1"]["forecast_snapshot"]
    assert list(snapshot) != list(fx.native()) and snapshot == fx.native()                                           # re-ordered, same content
    assert json.dumps(snapshot["request"]) != json.dumps(fx.native()["request"])                                      # a string compare of the request WOULD differ
    fresh = _judged(snapshot, LINE)
    assert fresh["registered"] and fresh["errors"] == [] and fresh["stale"] == [] and fresh["formDiffers"] is False and fresh["formDiffersAfterAChange"] is True
    assert fresh["sameRequest"] is True                                                                            # the host's canonical compare calls the re-sorted request the same one
    assert _judged(snapshot, {**LINE, "budget": 3000})["stale"] == ["the budget changed"]
    assert _judged(snapshot, {**LINE, "months": 2})["stale"] == ["the flight length changed"]
    assert _judged(snapshot, {**LINE, "geo_text": "Phoenix"})["stale"] == ["the proposal’s geography changed"]
    assert _judged(snapshot, {**LINE, "cpm": 18})["stale"] == ["the CPM changed"]


def test_an_older_estimate_adopted_in_memory_is_judged_fresh_for_its_line_whatever_the_key_order(monkeypatch):
    """The adopted older shape (re-ordered by the database like the rest of the entry) is fresh for the line it was saved on, and stale when one of the six things it compared changes."""
    _, reopened = _saved_then_reopened(monkeypatch, _draft({"L1": entry_of("legacy only (live+languages)")}))
    legacy = reopened["avails_data"]["L1"]["meta_forecast"]
    body = (
        contract.js_page(contract.plugin_source_of("meta"), "meta")
        + f"var LEGACY = {json.dumps(legacy)}, LINE = {json.dumps(LINE)};\n"
        "var snap = Z.adoptLegacy('meta', { meta_forecast: LEGACY }, LINE);\n"
        "var out = { registered: __res.meta === true, adopted: !!snap, legacy: snap && snap.legacy, method: snap && snap.method, stale: snap ? Z.snapshotStale(snap, LINE) : null,\n"
        "  budgetChanged: snap ? Z.snapshotStale(snap, Object.assign({}, LINE, { budget: 2500 })) : null,\n"
        "  texts: snap ? [P.applyLabel(Z._pure.summarize(snap.result)), P.cardHtml(snap.result, Z._pure.summarize(snap.result), { line: LINE, stale: [], fromSnapshot: true })] : null };\n"
        "return JSON.stringify(out);"
    )
    out = contract.run_js(body)
    assert out["registered"] and out["adopted"] and out["legacy"] is True and out["method"] == "meta"
    assert out["stale"] == [] and out["budgetChanged"] == ["the budget changed"]
    assert out["texts"][0] == "Insert the mid-case (5,500,000 / month) into Avails →"
    assert "language set to Spanish" in out["texts"][1]


# ================================================================================================================================ the table above stays true

CROSS_REFERENCES = {
    "tests/test_constanza_meta_frontend.py": ["test_every_old_saved_shape_is_adopted_in_memory_as_a_snapshot_of_the_dock_with_no_network",
                                              "test_an_old_estimate_without_languages_has_no_language_caveat_a_manual_one_has_no_request_and_a_budget_only_one_has_no_result",
                                              "test_an_adopted_old_estimate_through_the_dock_is_fresh_for_its_line_and_stale_for_each_of_the_six_things_the_old_card_compared"],
    "tests/test_constanza_step05_block_dom.py": ["test_an_older_live_estimate_is_read_with_its_buttons_and_nothing_is_written_back", "test_an_estimate_entered_by_hand_is_drawn_as_that_and_cannot_be_recalculated",
                                                 "test_a_budget_only_older_estimate_asks_for_an_audience_and_keeps_its_cross_check", "test_an_older_saved_estimate_goes_stale_for_the_same_six_reasons",
                                                 "test_insert_from_an_older_saved_estimate_leaves_it_exactly_as_saved", "test_recalculate_from_an_older_saved_estimate_rebuilds_its_audience_and_saves_the_current_shape",
                                                 "test_the_newer_of_the_saved_shape_and_the_older_one_is_the_one_drawn", "test_a_line_only_reads_an_estimate_of_its_own_method_and_never_deletes_another",
                                                 "test_a_corrupt_or_hostile_saved_estimate_takes_nothing_else_down_and_runs_nothing", "test_insert_from_an_estimate_entered_by_hand_names_the_audience_as_entered",
                                                 "test_a_budget_only_older_estimate_offers_no_insert", "test_a_pull_from_the_dock_for_the_lines_calendar_months_is_filed_fresh_under_the_line",
                                                 "test_insert_from_the_dock_goes_to_the_chosen_line_and_takes_the_estimate_off_a_line_it_was_not_pulled_for",
                                                 "test_an_answer_for_a_product_the_line_no_longer_has_is_neither_filed_nor_shown", "test_an_answer_for_a_line_that_was_deleted_leaves_no_entry_behind"],
    "tests/test_constanza_meta_dom.py": ["test_a_saved_estimate_whose_keys_were_reordered_by_the_database_shows_no_changed_banner_but_a_changed_choice_does",
                                         "test_a_corrupt_saved_estimate_does_not_break_the_dock_and_runs_nothing"],
    "tests/test_constanza_methods.py": ["test_an_avails_entry_keeps_the_legacy_meta_field_and_gains_the_one_snapshot_field", "test_a_saved_estimate_that_is_not_one_is_dropped_never_a_failed_save"],
    "tests/test_constanza_method_contract.py": ["test_c14_a_corrupt_or_hostile_saved_estimate_never_throws_or_runs"],
}


def test_the_cross_referenced_tests_still_exist():
    for relative, names in CROSS_REFERENCES.items():
        source = (ROOT / relative).read_text(encoding="utf-8")
        gone = [name for name in names if not re.search(rf"^(?:async )?def {re.escape(name)}\(", source, re.M)]
        assert not gone, f"{relative} no longer has {gone}: the table at the top of tests/test_constanza_snapshot_compat.py points at them - update the table (or restore the test)"
