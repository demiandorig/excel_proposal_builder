"""
Regression tests for the problems an independent pre-merge review of the Constanza PR found (and that were then fixed).
Each test pins the behaviour that was wrong, so it cannot quietly come back:

  geography   "Santa Monica, CA" used to resolve to ALL of California (a ~400x overstatement, with a false "NOT INCLUDED"
              note); states with no home DMA (NJ, DE, NH, DC) vanished from lists or fell through to Germany / a village in
              Ohio; "Hartford & New Haven" style DMAs couldn't be named; "Washington, DC" with the City type looped in the
              picker; real city names containing "City" (Kansas City, Jersey City...) couldn't be matched
  audience    the range got NARROWER as targeting cuts were added; two phrases for one interest were multiplied twice; an
              area with no ACS data was silently left un-narrowed
  inputs      absurd months / totals / ages reached the math (10**400 months was a 500); the endpoints blocked the event loop
  data        interest rates now say what their cited sources say
  front end   the Avails button treated weeks / quarters / Full Flight counts as calendar months and a per-period budget as
              monthly dollars
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import time
from pathlib import Path

import pytest
from fastapi import HTTPException

import app.main as main
from app.services import constanza_forecast_map as fm
from app.services import constanza_geo_crosswalk as crosswalk
from app.services.constanza_audience import AudienceSpec, estimate_audience, load_interest_taxonomy
from app.services.constanza_engine import _area_weights, estimate_avails
from app.services.constanza_geo import _pair_states, resolve_geo

ROOT = Path(__file__).resolve().parent.parent
ROKU = "Entravision Plus - Roku Ads (Includes The Roku Channel and the popular Espacio Latino Hub)"


def labels(res):
    return [a.label for a in res.areas]


# ---------------------------------------------------------------------------------------------------------------------
# Geography
# ---------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("text,label", [("Santa Monica, CA", "Santa Monica, CA"), ("Plano, TX", "Plano, TX"), ("Lincoln, NE", "Lincoln, NE")])
def test_city_and_state_for_a_city_that_is_not_a_dma_resolves_the_city_not_the_whole_state(text, label):
    res = resolve_geo(text)                                  # no geography-type hint: what the Avails button sends
    assert res.method in ("city", "exact_dma") and not res.unmatched_tokens
    if res.method == "city":
        assert labels(res) == [label]
    state_total = estimate_avails(ROKU, resolve_geo(text.split(", ")[1]), months=1).imps_mid
    assert estimate_avails(ROKU, res, months=1).imps_mid < state_total * 0.5      # never the whole state's inventory


def test_the_santa_monica_estimate_is_the_cities_not_californias():
    city = estimate_avails(ROKU, resolve_geo("Santa Monica, CA"), months=1).imps_mid
    california = estimate_avails(ROKU, resolve_geo("California"), months=1).imps_mid
    assert city < california / 100                           # it was ~400x too high before


def test_a_place_that_matches_nothing_is_unresolved_never_expanded_to_its_state():
    for text in ("Fooville, TX", "Brooklyn, NY"):
        res = resolve_geo(text)
        assert res.method == "unresolved" and not res.areas, text
    assert resolve_geo("Fooville, TX", "city").method == "unresolved"


def test_in_a_list_a_place_that_is_no_dma_is_reported_honestly_and_the_state_is_not_added():
    res = resolve_geo("Sacramento, Santa Monica, CA")
    assert labels(res) == ["Sacramento-Stockton-Modesto"]                    # one DMA, not California's eleven
    assert res.unmatched_tokens == ["Santa Monica, CA"]
    assert "isn't a DMA name" in res.unmatched_details[0]["reason"] and "City" in res.unmatched_details[0]["reason"]
    notes = main._geo_warning_notes(res)
    assert notes[0].startswith("NOT INCLUDED") and "Santa Monica, CA" in notes[0]       # and now that is TRUE


def test_the_original_dma_and_state_forms_are_unchanged():
    assert resolve_geo("Sacramento, CA").method == "exact_dma" and len(resolve_geo("Sacramento, CA").areas) == 1
    assert len(resolve_geo("CA, NV, AZ").areas) >= 14 and resolve_geo("CA, NV, AZ").method == "state_list"
    assert resolve_geo("California").method == "state_list" and len(resolve_geo("California").areas) == 11
    mixed = resolve_geo("Los Angeles, NV")
    assert mixed.method == "mixed" and "Los Angeles" in labels(mixed)                  # a DMA plus another state, as before
    assert resolve_geo("Washington, DC").method == "exact_dma"


@pytest.mark.parametrize("text,dma", [("New York, NY", "New York"), ("Miami, FL", "Miami-Ft. Lauderdale"), ("Chicago, IL", "Chicago"),
                                      ("Houston, TX", "Houston")])
def test_a_dma_whose_name_is_also_a_states_keeps_resolving_to_the_dma(text, dma):
    """'New York, NY' once became all nine New York DMAs when the state-qualifier check was tightened for the new
    'place that is no DMA' case. The NYC market, as ever, is what that string means; the whole state is 'NY'."""
    res = resolve_geo(text)
    assert res.method == "exact_dma" and labels(res) == [dma] and not res.unmatched_tokens
    assert resolve_geo("NY").method == "state_list" and len(resolve_geo("NY").areas) > 5


@pytest.mark.parametrize("text,dma", [("Wichita Falls, TX", "Wichita Falls & Lawton"), ("Hartford, CT", "Hartford & New Haven"),
                                      ("New Haven, CT", "Hartford & New Haven")])
def test_dma_names_joined_with_an_ampersand_can_be_named_by_either_city(text, dma):
    res = resolve_geo(text)
    assert res.method == "exact_dma" and labels(res) == [dma] and not res.unmatched_tokens


@pytest.mark.parametrize("text,state", [("NJ", "New Jersey"), ("New Jersey", "New Jersey"), ("DE", "Delaware"), ("de", "Delaware"),
                                        ("Delaware", "Delaware"), ("New Hampshire", "New Hampshire"), ("NH", "New Hampshire"),
                                        ("DC", "District Of Columbia")])
def test_a_state_with_no_home_dma_is_reported_not_reinterpreted(text, state):
    res = resolve_geo(text)
    assert res.method == "unresolved" and not res.areas                     # not Germany for "DE", not an Ohio village for NH
    assert res.geo_confidence == "unresolved"
    assert state in res.unmatched_details[0]["reason"] and "no TV market" in res.unmatched_details[0]["reason"]
    detail = main._geo_failure_detail(text, res)
    assert state in detail and "DMA" in detail                                # the 422 explains why


def test_a_no_dma_state_in_a_list_is_never_silently_dropped():
    res = resolve_geo("NY, NJ, CT")
    assert res.method == "state_list" and res.areas                        # NY and CT still resolve
    assert res.unmatched_tokens == ["NJ"] and "New Jersey" in res.unmatched_details[0]["reason"]
    assert main._geo_warning_notes(res)[0].startswith("NOT INCLUDED")
    assert resolve_geo("MA, NH, ME").unmatched_tokens == ["NH"]


@pytest.mark.parametrize("text", ["Washington, DC", "New York, NY", "Indiana, PA", "Kansas City, MO", "Jersey City, NJ", "Salt Lake City, UT",
                                  "Daly City, CA", "Oklahoma City, OK"])
def test_the_city_type_finds_state_named_places_and_cities_with_city_in_their_names(text):
    res = resolve_geo(text, "city")
    assert res.method == "city" and len(res.areas) == 1 and not res.ambiguous and not res.unmatched_tokens, (text, res.unmatched_tokens)
    assert res.areas[0].label == text if text != "Oklahoma City, OK" else True


def test_picking_a_candidate_from_the_picker_round_trips():
    """The picker re-submits the candidate's resolve_hint with the same geography type; for 'Washington' under City that
    used to return the same 18-way ambiguity forever."""
    first = resolve_geo("Washington", "city")
    assert first.ambiguous
    hints = [c["resolve_hint"] for e in first.ambiguous for c in e["candidates"]]
    assert "Washington, DC" in hints
    for hint in hints[:20]:
        again = resolve_geo(hint, "city")
        assert again.method == "city" and len(again.areas) == 1, hint


def test_bare_state_names_are_still_a_state_under_the_default_type_and_a_place_only_under_city():
    assert resolve_geo("Kansas").method == "state_list"
    assert not resolve_geo("Kansas", "city").method == "state_list"


def test_pair_states_keeps_a_list_of_states_apart_unless_the_planner_said_places():
    assert _pair_states(["CA", "NV", "AZ"]) == ["CA", "NV", "AZ"]
    assert _pair_states(["Washington", "DC"]) == ["Washington", "DC"]                   # default: two items, as before
    assert _pair_states(["Washington", "DC"], places_only=True) == ["Washington, DC"]


def test_place_aliases_drop_only_a_trailing_census_type_word():
    f = crosswalk._place_aliases
    assert f("Kansas City") == ["Kansas City"] and f("Jersey City") == ["Jersey City"]
    assert f("Salt Lake City") == ["Salt Lake City"] and f("Daly City") == ["Daly City"]
    assert f("Indianapolis city (balance)") == ["Indianapolis"]
    assert f("Foo town") == ["Foo"] and f("Bar CDP") == ["Bar"]
    assert f("Nashville-Davidson metropolitan government (balance)") == ["Nashville-Davidson", "Nashville"]
    assert f("Urban Honolulu")[0] == "Urban Honolulu" and "Honolulu" in f("Urban Honolulu")


def test_oversized_input_is_refused_fast_instead_of_grinding_the_regexes():
    for text in ("x" + " " * 400 + "district", "a " * 2500):
        t0 = time.time()
        res = resolve_geo(text)
        assert time.time() - t0 < 2.0 and res.method == "unresolved"
    many = ";".join(f"{90000 + i}" for i in range(400))
    res = resolve_geo(many)
    assert res.method == "unresolved" and "at most" in res.unmatched_details[0]["reason"]
    assert resolve_geo(";".join(["95814"] * 50)).resolved                                # a sane list is fine


def test_a_sub_dma_area_never_scales_its_market_above_itself():
    areas = crosswalk._areas_from_dma_shares(
        [{"dma": "Sacramento-Stockton-Modesto", "share": 1.0}], 10_000_000_000, "zip", "exact", state="CA", label="x", geo_id="1")
    assert areas[0].dma_share == 1.0


def test_a_zip_with_no_residents_says_so():
    zero = next(z for z, e in crosswalk._load_acs("acs_age_sex_zcta.json").items()
                if not e.get("total_population") and z in crosswalk._load_geo("zcta_dma.json"))
    res = resolve_geo(zero)
    assert res.resolved and res.geo_confidence == "medium" and "no residents" in res.notes[0]
    assert resolve_geo("95814").geo_confidence == "high"


# ---------------------------------------------------------------------------------------------------------------------
# Weighting
# ---------------------------------------------------------------------------------------------------------------------

def test_hispanic_weights_use_the_parent_dmas_state_for_a_zip_instead_of_zero():
    zips = resolve_geo("90210; 94105")
    weights = _area_weights(zips.areas, "hispanic")
    assert all(w > 0 for w in weights) and sum(weights) == pytest.approx(1.0)
    assert sum(_area_weights(zips.areas, None)) == pytest.approx(1.0)


def test_the_hispanic_note_is_honest_that_impressions_are_the_blended_pool():
    est = estimate_avails("Entravision Plus - Hispanics CTV/OTT", resolve_geo("Los Angeles; Nashville"), months=1)
    note = next(n for n in est.notes if "Hispanic population %" in n)
    assert "share column" in note and "same blended Madhive pool" in note


def test_the_state_note_does_not_promise_a_few_percent():
    res = resolve_geo("California")
    assert not any("by a few percent" in n for n in res.notes) and any("by a lot" in n for n in res.notes)


# ---------------------------------------------------------------------------------------------------------------------
# Audience
# ---------------------------------------------------------------------------------------------------------------------

def test_the_audience_range_is_never_narrower_than_the_estimate_it_narrows_and_widens_with_each_cut():
    base = estimate_avails(ROKU, resolve_geo("Los Angeles"), months=1)
    base_width = (base.imps_high - base.imps_low) / base.imps_mid
    widths = []
    for spec in (AudienceSpec(), AudienceSpec(age_min=18, age_max=34),
                 AudienceSpec(age_min=18, age_max=34, interest_terms=["sports"]),
                 AudienceSpec(age_min=18, age_max=34, interest_terms=["sports", "gaming"])):
        est = estimate_audience(base, spec)
        widths.append((est.imps_high - est.imps_low) / est.imps_mid)
    assert widths[0] >= base_width * 0.99                                        # no cut at all: not tighter than the base
    assert widths == sorted(widths) and widths[-1] > widths[1]                   # every extra cut widens it


def test_two_phrases_for_one_interest_are_applied_once():
    base = estimate_avails(ROKU, resolve_geo("Los Angeles"), months=1)
    once = estimate_audience(base, AudienceSpec(interest_terms=["sports"]))
    twice = estimate_audience(base, AudienceSpec(interest_terms=["sports", "likes sports"]))      # both mean "Sports Fans"
    assert twice.imps_mid == pytest.approx(once.imps_mid) and twice.cuts_applied == once.cuts_applied == 1
    assert any("applied once" in n for n in twice.notes)


def test_a_specific_sport_is_its_own_audience_not_a_second_phrase_for_sports_fans():
    base = estimate_avails(ROKU, resolve_geo("Los Angeles"), months=1)
    both = estimate_audience(base, AudienceSpec(interest_terms=["sports", "football"]))
    assert both.cuts_applied == 2 and {i.category for i in both.interests} == {"sports_fan", "sports_football_nfl"}


def test_an_area_without_acs_data_is_called_out_while_others_are_narrowed():
    base = estimate_avails(ROKU, resolve_geo("Los Angeles; Anchorage"), months=1)
    est = estimate_audience(base, AudienceSpec(age_min=18, age_max=34))
    assert est.age_gender_share_applied
    assert any("NOT applied to Anchorage" in n and "reads high" in n for n in est.notes)


def test_the_audience_layer_and_the_resolver_share_one_parsed_copy_of_each_acs_bundle():
    from app.services import constanza_audience as audience
    assert audience._acs_bundle("acs_age_sex_zcta.json") is crosswalk._load_acs("acs_age_sex_zcta.json")


# ---------------------------------------------------------------------------------------------------------------------
# Interest data
# ---------------------------------------------------------------------------------------------------------------------

def test_interest_rates_match_what_their_cited_sources_say_and_are_internally_consistent():
    cats = {c["category"]: c for c in load_interest_taxonomy()}
    sports, gamer = cats["sports_fan"], cats["video_gamer"]
    assert sports["rate_by_gender"] == {"all": 0.59, "male": 0.66, "female": 0.51} and sports["source_date"] == "2015-06"   # Gallup, June 2015
    # ESA 2025: adults 18+ who play every week. The earlier 0.49 / 0.53 / 0.46 figures were not on the cited page and their
    # base was not adults, so the independent check replaced them; the source gives no gender split, so both fall back to 'all'.
    assert gamer["rate_by_gender"] == {"all": 0.6, "male": 0.6, "female": 0.6} and gamer["source_date"] == "2025-06"
    for c in cats.values():
        r = c["rate_by_gender"]
        assert min(r["male"], r["female"]) <= r["all"] <= max(r["male"], r["female"]), c["category"]    # 'all' can't be outside its parts
        assert c["source"] and c["source_url"] and c["source_date"]


# ---------------------------------------------------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------------------------------------------------

def test_the_constanza_handlers_do_blocking_work_so_they_are_not_coroutines():
    for fn in (main.constanza_resolve, main.constanza_estimate, main.constanza_parse_audience):
        assert not asyncio.iscoroutinefunction(fn), fn.__name__          # FastAPI then runs them in its thread pool


def _estimate(**kw):
    fields = dict(product_name=ROKU, geo_text="Sacramento", months=1)
    fields.update(kw)
    return main.ConstanzaEstimateRequest(**fields)


@pytest.mark.parametrize("fields,message", [
    (dict(months=0), "Months must be between"),
    (dict(months=121), "Months must be between"),
    (dict(months=10 ** 400), "Months must be between"),
    (dict(national_monthly_total=-1.0), "monthly impressions total"),
    (dict(national_monthly_total=float("inf")), "monthly impressions total"),
    (dict(national_monthly_total=float("nan")), "monthly impressions total"),
    (dict(national_monthly_total=1e14), "monthly impressions total"),
    (dict(geo_text="x" * 2001), "too long"),
    (dict(audience=main.ConstanzaAudienceSpecModel(age_min=-1)), "Minimum age"),
    (dict(audience=main.ConstanzaAudienceSpecModel(age_max=500)), "Maximum age"),
    (dict(audience=main.ConstanzaAudienceSpecModel(age_min=40, age_max=20)), "can't be above"),
    (dict(audience=main.ConstanzaAudienceSpecModel(interest_terms=["a"] * 11)), "at most 10"),
    (dict(audience=main.ConstanzaAudienceSpecModel(interest_terms=["x" * 101])), "under 100"),
])
def test_absurd_estimate_input_is_a_plain_422_not_a_500(fields, message):
    with pytest.raises(HTTPException) as e:
        main.constanza_estimate(_estimate(**fields))
    assert e.value.status_code == 422 and message in e.value.detail


def test_sane_estimate_input_still_works_and_the_other_handlers_have_bounds_too():
    out = main.constanza_estimate(_estimate(months=3))
    assert out["confidence"] == "platform_modeled" and out["months"] == 3
    with pytest.raises(HTTPException) as e:
        main.constanza_resolve(main.ConstanzaResolveRequest(geo_text="x" * 2001))
    assert e.value.status_code == 422
    with pytest.raises(HTTPException):
        main.constanza_parse_audience(main.ConstanzaParseAudienceRequest(text="x" * 2001))
    with pytest.raises(HTTPException) as e:
        main.constanza_estimate(_estimate(geo_text="NJ"))
    assert e.value.status_code == 422 and "no TV market" in e.value.detail


# ---------------------------------------------------------------------------------------------------------------------
# Forecast mapping and seeds
# ---------------------------------------------------------------------------------------------------------------------

def test_a_failed_refresh_with_stale_rows_backs_off_instead_of_hitting_the_database_every_request(monkeypatch):
    calls = []
    good = [{"product_name": "Search - SEM", "enabled": True, "method": "generic", "platform": None, "hispanic_weighted": False}]
    monkeypatch.setattr(fm, "_read_rows", lambda: (calls.append(1), good)[1])
    fm.invalidate()
    assert fm.resolve_forecast("Search - SEM").enabled is True
    monkeypatch.setattr(fm, "_CACHE_TTL", 0.0)
    monkeypatch.setattr(fm, "_read_rows", lambda: (calls.append(2), None)[1])
    for _ in range(6):
        assert fm.resolve_forecast("Search - SEM").enabled is True               # last good rows keep being served
    assert calls.count(2) == 1                                                  # ... and the failing read ran once


def test_the_new_sku_descriptions_carry_no_internal_text_but_the_placeholder_warning_stays_in_the_notes():
    from app import catalog
    for name in fm.DEFAULT_PLATFORM_PRODUCTS:
        if name in ("Entravision Plus - Hulu", "Entravision Plus - Peacock") or "Other FAST" in name or "vMVPD" in name:
            p = catalog.by_name(name)
            assert "Constanza" not in p.proposal_description and "PLACEHOLDER" not in p.proposal_description.upper(), name
            assert "Constanza" not in p.notes and "PLACEHOLDER RATE" in p.notes, name


# ---------------------------------------------------------------------------------------------------------------------
# Front end: the Avails button under every billing period
# ---------------------------------------------------------------------------------------------------------------------

def _frontend_harness():
    path = ROOT / "tests" / "test_full_flight_frontend.py"
    spec = importlib.util.spec_from_file_location("_ff_frontend_harness", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_js(snippet: str):
    h = _frontend_harness()
    app_js = h.APP_JS
    fns = "\n".join(h._extract_fn(app_js, n) for n in ("_constanzaFlightMonths", "_constanzaMonthlyBudget", "_constanzaErrorText"))
    body = (
        "var console = { warn: function () {}, log: function () {}, error: function () {} };\n"
        + h._app_js_bundle() + "\n" + fns + "\n"
        + """
function mk(unit, o) {
  o = o || {};
  return { timeUnit: unit, parsed: { start_date: o.start === undefined ? "2026-11-24" : o.start, end_date: o.end === undefined ? "2026-12-31" : o.end },
           activeTierLabel: "A", activeTierName: null, activeTierGeo: null, activeTierStartDate: null, activeTierEndDate: null, tiers: [], lineItems: [] };
}
var state;
"""
        + snippet
    )
    return json.loads(h._run_js(body))


def test_the_button_counts_calendar_months_and_monthly_dollars_under_every_billing_period():
    out = _run_js("""
var R = {};
state = mk("month");            R.monthDates = _constanzaFlightMonths({ months: 9 });               // dates win: Nov 24 - Dec 31 touches 2 months
state = mk("month", {start: "", end: ""}); R.monthNoDates = _constanzaFlightMonths({ months: 3 });  // no dates: the line's own count
state = mk("week");             R.weekDates = _constanzaFlightMonths({ months: 6 });                // NOT the 6 weeks
state = mk("week", {start: "", end: ""}); R.weekNoDates = _constanzaFlightMonths({ months: 6 });
state = mk("quarter");          R.quarterDates = _constanzaFlightMonths({ months: 1 });
state = mk("full_flight");      R.ffDates = _constanzaFlightMonths({ months: 1 });                  // NOT 1 period
state = mk("full_flight", {start: "", end: ""}); R.ffNoDates = _constanzaFlightMonths({ months: 1 });
state = mk("month", {start: "2026-01-01", end: "2046-12-31"}); R.capped = _constanzaFlightMonths({ months: 1 });
state = mk("month");            R.budgetMonth = _constanzaMonthlyBudget({ monthly_budget: 5000 }, 2);
state = mk("week");             R.budgetWeek = _constanzaMonthlyBudget({ monthly_budget: 1000 }, 2);
state = mk("quarter");          R.budgetQuarter = _constanzaMonthlyBudget({ monthly_budget: 3000 }, 2);
state = mk("full_flight");      R.budgetFF = _constanzaMonthlyBudget({ monthly_budget: 12000 }, 2);
state = mk("full_flight");      R.budgetFFOne = _constanzaMonthlyBudget({ monthly_budget: 12000 }, 1);
state = mk("month");            R.budgetNone = _constanzaMonthlyBudget({ monthly_budget: null }, 2);
return JSON.stringify(R);
""")
    assert out["monthDates"] == 2 and out["monthNoDates"] == 3
    assert out["weekDates"] == 2 and out["weekNoDates"] == 1 and out["quarterDates"] == 2
    assert out["ffDates"] == 2 and out["ffNoDates"] == 1 and out["capped"] == 120
    assert out["budgetMonth"] == 5000 and out["budgetWeek"] == 4000 and out["budgetQuarter"] == pytest.approx(1000)
    assert out["budgetFF"] == 6000 and out["budgetFFOne"] == 12000 and out["budgetNone"] == 0


def test_error_messages_from_the_server_read_as_text_even_when_the_detail_is_a_list():
    out = _run_js("""
var R = {};
R.string = _constanzaErrorText({ detail: "Months must be between 1 and 120." }, "Bad Request");
R.list = _constanzaErrorText({ detail: [{ msg: "Input should be a valid number" }, { msg: "Field required" }] }, "Unprocessable");
R.none = _constanzaErrorText({}, "Server Error");
R.nothing = _constanzaErrorText(null, "");
return JSON.stringify(R);
""")
    assert out == {"string": "Months must be between 1 and 120.", "list": "Input should be a valid number; Field required",
                   "none": "Server Error", "nothing": "Request failed."}


def test_the_button_sends_the_calendar_months_and_the_monthly_budget():
    src = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
    fn = src.split("async function onConstanzaAutoCalc(lid) {", 1)[1].split("\n}\n", 1)[0]
    assert "const flightMonths = _constanzaFlightMonths(li);" in fn
    assert "_constanzaMonthlyBudget(li, flightMonths)" in fn and "months: flightMonths," in fn
    assert "li.months" not in fn and "li.monthly_budget" not in fn           # the raw per-period fields are no longer used here
