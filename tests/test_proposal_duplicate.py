"""The pure transform behind "Duplicate for a new client" (app/services/proposal_duplicate.py), offline: a saved proposal's state in, the copy for a
new client out. Everything here runs on the realistic sources of tests/fixtures/duplicate_sources.py (built through the real models and builder).

The numbered cases are the design's list (7.1); where a case needs a source that no fixture has, the test changes a fresh copy of one.

   1  month 3 to 3, whole months          a hand-made split is re-keyed by position, a default split is dropped, months set, totals as they were
   2  month 3 to 4                        per-period budgets stay, so the totals move; the hand-made split cannot be carried
   3  month 3 to 1, 3 to 8 across a year  months follow the flight; every key is a real period
   4  partial first / last month          a different SHAPE: the split is reset and the planner is told; the same partial shape carries
   5  merged source, same shape           the combined period moves to the same positions of the new flight
   6  merged source, different shape      the merge is dropped and counted
   7  week 13 to 13 / 15 / 4              weekly keys re-dated and carried only when the week count matches
   8  quarter 2 to 2 / 3                  quarter keys re-dated and carried only when the quarter count matches
   9  full flight                         one period, no split, a legacy "months 3" line folded with its total kept
  10  no readable source dates            months set, every split reset, "had no readable dates"
  11  legacy single option                `tiers` stays null, the top-level lines and avails are transformed
  12  four options with their own windows every window cleared, one note that lists them
  13  keys the old flight does not have   reset (and a split that does not add up)
  14  a prorated default split            reset to null, the mode kept
  15  Constanza avails                    estimates removed, hand-typed kept, counted; keep_avails keeps both
  16  free text naming the old client     cleared and counted; a sibling brand and the same agency are not
  17  a renewal request type              blank with a note; the renewal fields reset
  18  the reset set                       AI text, paste, CCs, comments, tabs ... and what the form decides
  19  custom rates                        kept, and counted in a note
  20  a product that left the catalog     stays on its line and is listed
  21  the input is never changed
  22  leak scan                           across every source and flight, nothing names the old client or agency
  23  allocation invariants               every kept split balances and is keyed by the new periods
  24  builder fixed point                 DraftSaveRequest -> _build_reopen_state gives the output back

plus the helpers one by one: describe_source, derive_proposal_id, scrub_tokens / mentions / website_token, shape_signature, translate_merges,
follows_default, option_totals, plain_text.
"""
from __future__ import annotations

import copy
import json
import re
from datetime import date

import pytest

from app import main
from app.services import monthly_allocation as mo
from app.services import proposal_duplicate as pd
from tests.fixtures import duplicate_sources as fx

FEB_APR = ("2027-02-01", "2027-04-30")          # three whole months


def basics(**over) -> pd.DuplicateBasics:
    fields = dict(client_name="Beta Burgers", start_date=FEB_APR[0], end_date=FEB_APR[1], notion_id="EVC-50001", campaign_name="Spring Push")
    fields.update(over)
    return pd.DuplicateBasics(**fields)


def run(state: dict, *, known=lambda name: True, **over) -> pd.DuplicateResult:
    return pd.transform_reopen_state(state, basics(**over), product_known=known)


def lines_by_id(state: dict) -> dict:
    return {li["id"]: li for tier in (state.get("tiers") or [{"line_items": state["line_items"]}]) for li in tier["line_items"]}


def tier(state: dict, label: str) -> dict:
    return next(t for t in state["tiers"] if t["label"] == label)


def codes(result: pd.DuplicateResult) -> list[str]:
    return [n["code"] for n in result.notes]


def note(result: pd.DuplicateResult, code: str) -> dict:
    return next(n for n in result.notes if n["code"] == code)


def strings(value):
    """Every string in a nested structure, keys included."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from strings(item)


def with_request(state: dict, **fields) -> dict:
    state["request"].update(fields)
    return state


def only_option_a(state: dict) -> dict:
    """The monthly source without option B (B has its own window, which has its own consequences)."""
    state["tiers"] = state["tiers"][:1]
    return state


BUILDERS = {"monthly": fx.source_monthly, "monthly_merged": fx.source_monthly_merged, "weekly": fx.source_weekly, "quarterly": fx.source_quarterly,
            "full_flight": fx.source_full_flight, "legacy_single_tier": fx.source_legacy_single_tier, "without_dates": fx.source_without_dates,
            "four_tiers": fx.source_four_tiers}


def source(name: str) -> dict:
    return BUILDERS[name]()


# ====================================================================================================================== 1-3. months

def test_1_three_months_to_three_months_rekeys_the_hand_made_split_and_drops_the_default_one():
    source = fx.source_monthly()
    result = run(source)
    lines = lines_by_id(result.state)
    assert lines["li_fb"]["monthly_allocations"] == {"2027-02": 5000.0, "2027-03": 15000.0, "2027-04": 10000.0}      # period i -> period i, same dollars
    assert lines["li_ctv"]["monthly_allocations"] is None                                                           # the even split: Step 06 derives it again
    assert lines["li_disp"]["monthly_allocations"] is None
    assert [li["months"] for li in lines.values() if li["id"] in ("li_fb", "li_ctv", "li_disp")] == [3, 3, 3]
    assert result.stats["totals_before"]["A"] == result.stats["totals_after"]["A"] == 69000.0
    assert result.state["request"]["start_date"] == FEB_APR[0] and result.state["request"]["end_date"] == FEB_APR[1]
    assert result.state["request"]["total_months"] == 3
    # option A lost nothing; the one line that could not be carried is option B's (its 4-period split meets a 3-period flight, below)
    assert result.stats["split_reset"] == 1 and lines["li_b1"]["monthly_allocations"] is None


def test_1_option_b_follows_the_new_flight_once_its_own_window_is_cleared():
    result = run(fx.source_monthly())
    b = tier(result.state, "B")
    assert b["start_date"] is None and b["end_date"] is None
    assert [li["months"] for li in b["line_items"]] == [3, 3] and all(li["monthly_allocations"] is None for li in b["line_items"])
    assert b["period_merge_groups"] is None
    assert result.stats["totals_before"]["B"] == 32000.0 and result.stats["totals_after"]["B"] == 24000.0
    assert "Option B: $32,000 to $24,000" in note(result, "total_changed")["text"] and "Option A" not in note(result, "total_changed")["text"]


def test_2_three_months_to_four_keeps_the_per_period_budget_so_the_total_moves_and_resets_the_split():
    result = run(fx.source_monthly(), end_date="2027-05-31", start_date="2027-02-01")
    lines = lines_by_id(result.state)
    assert all(li["months"] == 4 for li in lines.values())
    assert lines["li_fb"]["monthly_allocations"] is None                            # 3 hand-made periods do not map onto 4
    assert result.stats["totals_before"]["A"] == 69000.0 and result.stats["totals_after"]["A"] == 92000.0
    assert result.stats["totals_after"]["B"] == 32000.0                             # option B already had four months...
    assert tier(result.state, "B")["period_merge_groups"] == [["2027-02", "2027-03"]]            # ...so its split and its combined period come along
    assert lines["li_b1"]["monthly_allocations"] == {"2027-02+2027-03": 9000.0, "2027-04": 6000.0, "2027-05": 9000.0}
    assert "Option A: $69,000 to $92,000" in note(result, "total_changed")["text"]
    assert note(result, "split_reset")["level"] == "check" and "1 line had" in note(result, "split_reset")["text"]         # li_fb only
    assert lines["li_fb"]["monthly_budget"] == 10000.0 and "merges_dropped" not in codes(result)


@pytest.mark.parametrize("start, end, months", [("2027-02-01", "2027-02-28", 1), ("2026-11-15", "2027-06-20", 8), ("2026-12-01", "2027-01-31", 2)])
def test_3_months_follow_the_flight_and_every_key_is_a_real_period(start, end, months):
    result = run(fx.source_monthly(), start_date=start, end_date=end)
    assert result.state["request"]["total_months"] == months
    for li in lines_by_id(result.state).values():
        assert li["months"] == months and li["monthly_allocations"] is None
    assert result.stats["totals_after"]["A"] == 23000.0 * months and result.stats["totals_after"]["B"] == 8000.0 * months
    assert result.stats["periods"] == months


# ====================================================================================================================== 4. partial months

@pytest.mark.parametrize("start, end", [("2027-02-17", "2027-04-30"), ("2027-02-01", "2027-04-15")])
def test_4_a_partial_first_or_last_month_is_a_different_shape_so_the_split_is_reset_and_reported(start, end):
    result = run(fx.source_monthly(), start_date=start, end_date=end)
    lines = lines_by_id(result.state)
    assert all(li["months"] == 3 for li in lines.values())                          # still three calendar months...
    assert lines["li_fb"]["monthly_allocations"] is None                            # ...but "the first month" no longer means the same thing
    assert "split_reset" in codes(result)


def test_4_the_same_partial_shape_carries_the_split_by_position():
    source = with_request(only_option_a(fx.source_monthly()), start_date="2026-09-15")            # a 16-day first month, as the new flight will have
    result = run(source, start_date="2027-02-15", end_date="2027-04-30")
    assert lines_by_id(result.state)["li_fb"]["monthly_allocations"] == {"2027-02": 5000.0, "2027-03": 15000.0, "2027-04": 10000.0}
    assert "split_reset" not in codes(result)


# ====================================================================================================================== 5-6. merged periods

def test_5_a_combined_period_moves_to_the_same_positions_of_a_flight_of_the_same_shape():
    result = run(fx.source_monthly_merged())
    a = tier(result.state, "A")
    assert a["period_merge_groups"] == [["2027-02", "2027-03"]]
    lines = lines_by_id(result.state)
    assert lines["li_m1"]["monthly_allocations"] == {"2027-02+2027-03": 20000.0, "2027-04": 10000.0}
    assert lines["li_m2"]["monthly_allocations"] is None                            # 12,000 / 12,000 is the even split of the two buckets
    assert "merges_dropped" not in codes(result) and result.stats["merges_kept"] == 1


def test_6_a_combined_period_is_dropped_when_the_new_flight_has_different_periods():
    result = run(fx.source_monthly_merged(), end_date="2027-05-31")
    a = tier(result.state, "A")
    assert a["period_merge_groups"] is None
    assert lines_by_id(result.state)["li_m1"]["monthly_allocations"] is None
    assert "1 combined period group was removed" in note(result, "merges_dropped")["text"] and result.stats["merges_dropped"] == 1


# ====================================================================================================================== 7-8. week and quarter

def test_7_weekly_keys_are_re_dated_and_carried_when_the_week_count_matches():
    result = run(fx.source_weekly())                  # Feb 1 to Apr 30, 2027: 89 days = 13 weeks (the last one short)
    li = lines_by_id(result.state)["li_w1"]
    allocation = li["monthly_allocations"]
    assert list(allocation)[0] == "W1-2027-02-01" and list(allocation)[-1] == "W13-2027-04-26" and len(allocation) == 13
    assert list(allocation.values()) == [3000.0] * 4 + [1500.0] * 8 + [2000.0]
    assert lines_by_id(result.state)["li_w2"]["monthly_allocations"] is None        # the even split
    assert li["months"] == 13 and result.stats["totals_after"]["A"] == 39000.0 and "total_changed" not in codes(result)


@pytest.mark.parametrize("end, weeks", [("2027-05-14", 15), ("2027-02-28", 4)])
def test_7_weekly_splits_are_reset_when_the_week_count_differs(end, weeks):
    result = run(fx.source_weekly(), end_date=end)
    li = lines_by_id(result.state)["li_w1"]
    assert li["monthly_allocations"] is None and li["months"] == weeks
    assert result.stats["totals_after"]["A"] == 3000.0 * weeks and "split_reset" in codes(result)


def test_8_quarterly_keys_are_re_dated_and_carried_when_the_quarter_count_matches():
    result = run(fx.source_quarterly(), start_date="2027-03-01", end_date="2027-08-31")
    li = lines_by_id(result.state)["li_q1"]
    assert li["monthly_allocations"] == {"2027-03+2027-04+2027-05": 20000.0, "2027-06+2027-07+2027-08": 10000.0}
    assert lines_by_id(result.state)["li_q2"]["monthly_allocations"] is None
    assert li["months"] == 2 and result.stats["totals_after"]["A"] == 54000.0


def test_8_a_short_last_quarter_is_still_one_period_so_the_split_is_carried_to_it():
    result = run(fx.source_quarterly(), start_date="2027-03-01", end_date="2027-07-31")     # 3 + 2 months = two quarters
    assert lines_by_id(result.state)["li_q1"]["monthly_allocations"] == {"2027-03+2027-04+2027-05": 20000.0, "2027-06+2027-07": 10000.0}


def test_8_quarterly_splits_are_reset_when_the_quarter_count_differs():
    result = run(fx.source_quarterly(), start_date="2027-03-01", end_date="2027-11-30")     # 3 quarters
    li = lines_by_id(result.state)["li_q1"]
    assert li["monthly_allocations"] is None and li["months"] == 3 and result.stats["totals_after"]["A"] == 81000.0


# ====================================================================================================================== 9-10. full flight, no dates

def test_9_full_flight_is_one_period_with_no_split_and_the_total_never_moves():
    result = run(fx.source_full_flight(), start_date="2027-02-01", end_date="2027-09-30")
    lines = lines_by_id(result.state)
    assert all(li["months"] == 1 and li["monthly_allocations"] is None for li in lines.values())
    assert [lines[i]["monthly_budget"] for i in ("li_f1", "li_f2")] == [30000.0, 24000.0]
    assert result.state["tiers"][0]["period_merge_groups"] is None and result.state["time_unit"] == "full_flight"
    assert result.stats["totals_before"] == result.stats["totals_after"] == {"A": 69000.0} and result.stats["periods"] == 1
    assert "budgets_carried" in codes(result) and "total_changed" not in codes(result)
    assert result.state["request"]["total_months"] == 8                              # the calendar months (Step 02 shows them), not the periods


def test_9_a_legacy_full_flight_line_still_saying_three_months_is_folded_with_its_total_kept():
    lines = lines_by_id(run(fx.source_full_flight()).state)
    assert lines["li_f3"]["months"] == 1 and lines["li_f3"]["monthly_budget"] == 15000.0         # 5,000 x 3 months


def test_9_a_stray_split_on_a_full_flight_line_is_dropped():
    """Full Flight has one period and no split: a line that still carries one (a state saved after a billing-period switch) must not take it along."""
    source = fx.source_full_flight()
    source["tiers"][0]["line_items"][0]["monthly_allocations"] = {"2026-09": 10000.0, "2026-10": 10000.0, "2026-11": 10000.0}
    assert all(li["monthly_allocations"] is None for li in lines_by_id(run(source, start_date="2027-02-01", end_date="2027-09-30").state).values())


def test_9_an_added_value_line_is_not_given_money_when_a_legacy_full_flight_state_is_folded():
    source = fx.source_full_flight()
    source["tiers"][0]["line_items"][2].update(is_added_value=True, monthly_budget=0.0, months=3)
    line = lines_by_id(run(source).state)["li_f3"]
    assert line["months"] == 1 and line["monthly_budget"] == 0.0


def test_10_a_source_with_no_readable_dates_gets_months_from_the_new_flight_and_every_split_reset():
    result = run(fx.source_without_dates())
    lines = lines_by_id(result.state)
    assert all(li["months"] == 3 and li["monthly_allocations"] is None for li in lines.values())
    assert "dates_set" in codes(result) and "no readable flight dates" in note(result, "dates_set")["text"]
    assert "split_reset" in codes(result)                    # the line that held a split had nothing to explain its keys


@pytest.mark.parametrize("start, end", [("TBD", ""), ("", ""), ("9/1/2026", ""), ("2026-11-30", "2026-09-01"), ("1/1/1900", "12/31/2999")])
def test_10_unreadable_backwards_or_absurd_source_flights_are_treated_as_having_no_dates(start, end):
    source = with_request(fx.source_monthly(), start_date=start, end_date=end)
    result = run(source)
    assert "dates_set" in codes(result) and lines_by_id(result.state)["li_fb"]["monthly_allocations"] is None
    assert all(li["months"] == 3 for li in lines_by_id(result.state).values())


def test_10_a_source_flight_at_the_end_of_the_calendar_does_not_crash_the_period_arithmetic():
    source = with_request(fx.source_monthly(), start_date="12/31/9998", end_date="12/31/9999")        # months_between would step past year 9999
    result = run(source)
    assert lines_by_id(result.state)["li_fb"]["monthly_allocations"] is None and all(li["months"] == 3 for li in lines_by_id(result.state).values())
    assert pd.describe_source(source)["source"]["periods"] is None


@pytest.mark.parametrize("build, periods", [(fx.source_weekly, 13), (fx.source_quarterly, 1), (fx.source_monthly, 3)])        # the new flight, Feb 1 to Apr 30 2027
def test_10_a_source_flight_at_the_end_of_the_calendar_is_no_crash_in_any_billing_period(build, periods):
    """Weekly periods overflow with an OverflowError past 9999-12-31 where monthly and quarterly ones raise a ValueError: both are "no readable dates"."""
    source = with_request(build(), start_date="12/1/9999", end_date="12/31/9999")
    result = run(source)
    assert all(li["monthly_allocations"] is None for li in lines_by_id(result.state).values())      # no periods to re-key a split against: every split is reset
    assert all(li["months"] == periods for li in lines_by_id(result.state).values()) and "dates_moved" in codes(result)      # the flight itself is readable
    assert pd.describe_source(source)["source"]["periods"] is None


def test_10_dates_that_equal_the_new_flight_say_so():
    assert "dates_same" in codes(run(fx.source_monthly(), start_date="2026-09-01", end_date="2026-11-30"))


# ====================================================================================================================== 11-12. legacy single option, many options

def test_11_a_legacy_single_option_state_keeps_tiers_null_and_transforms_the_top_level_lines_and_avails():
    result = run(fx.source_legacy_single_tier())
    state = result.state
    assert state["tiers"] is None
    lines = {li["id"]: li for li in state["line_items"]}
    assert lines["li_l1"]["monthly_allocations"] == {"2027-02": 3000.0, "2027-03": 6000.0, "2027-04": 18000.0} and lines["li_l1"]["months"] == 3
    assert lines["li_l1"]["notes_override"] is None                                                            # "Acme grand opening"
    assert list(state["avails_data"]) == ["li_l2"] and state["avails_data"]["li_l2"]["max_imps"] == 300_000   # the estimate went, the typed numbers stayed
    assert result.stats["totals_before"] == {"A": 39000.0} == result.stats["totals_after"]


def test_12_options_with_their_own_windows_lose_them_and_one_note_lists_each_old_window():
    result = run(fx.source_four_tiers())
    assert [(t["label"], t["start_date"], t["end_date"]) for t in result.state["tiers"]] == [(l, None, None) for l in "ABCD"]
    text = note(result, "tier_dates_cleared")["text"]
    assert text.startswith("3 options had their own dates and now follow the new flight")
    for expected in ("Option B ran Sep 1, 2026 – Dec 31, 2026", "Option C ran Oct 1, 2026 – Nov 30, 2026", "Option D ran Sep 15, 2026 – Nov 30, 2026"):
        assert expected in text
    assert all(li["months"] == 3 for t in result.state["tiers"] for li in t["line_items"])
    assert result.stats["totals_after"] == {"A": 15000.0, "B": 15000.0, "C": 15000.0, "D": 15000.0}


# ====================================================================================================================== 13-14. which splits are kept

def test_13_a_split_keyed_to_periods_the_old_flight_does_not_have_is_reset():
    source = fx.source_monthly()
    source["tiers"][0]["line_items"][0]["monthly_allocations"] = {"2025-01": 5000.0, "2025-02": 15000.0, "2025-03": 10000.0}
    result = run(source)
    assert lines_by_id(result.state)["li_fb"]["monthly_allocations"] is None and "split_reset" in codes(result)


def test_13_a_split_with_only_some_of_the_periods_is_reset():
    source = fx.source_monthly()
    source["tiers"][0]["line_items"][0]["monthly_allocations"] = {"2026-09": 15000.0, "2026-10": 15000.0}
    assert lines_by_id(run(source).state)["li_fb"]["monthly_allocations"] is None


def test_13_a_split_that_does_not_add_up_to_its_line_total_is_not_carried():
    source = fx.source_monthly()
    source["tiers"][0]["line_items"][0]["monthly_allocations"] = {"2026-09": 5000.0, "2026-10": 15000.0, "2026-11": 9000.0}     # 29,000 of 30,000
    result = run(source)
    assert lines_by_id(result.state)["li_fb"]["monthly_allocations"] is None and "split_reset" in codes(result)


def test_13_a_split_within_the_rounding_of_a_cent_per_period_is_carried():
    source = fx.source_monthly()
    li = source["tiers"][0]["line_items"][0]
    li["monthly_budget"], li["monthly_allocations"] = 21666.67, {"2026-09": 15000.0, "2026-10": 25000.0, "2026-11": 25000.0}      # 21666.67 x 3 = 65000.01
    assert lines_by_id(run(source).state)["li_fb"]["monthly_allocations"] == {"2027-02": 15000.0, "2027-03": 25000.0, "2027-04": 25000.0}


def test_13_a_split_balanced_for_a_stale_month_count_is_reset_because_the_copy_would_not_balance():
    source = fx.source_monthly()
    li = source["tiers"][0]["line_items"][0]
    li["months"] = 4                      # a legacy line that still said 4 while the flight is 3 months: 10,000 x 4 = 40,000
    li["monthly_allocations"] = {"2026-09": 10000.0, "2026-10": 20000.0, "2026-11": 10000.0}
    assert lines_by_id(run(source).state)["li_fb"]["monthly_allocations"] is None


def test_14_a_prorated_default_split_is_reset_and_the_mode_is_kept():
    source = with_request(only_option_a(fx.source_monthly()), start_date="2026-09-15")
    source["monthly_distribution_mode"] = "prorated"
    base = mo.periods_between(date(2026, 9, 15), date(2026, 11, 30), "month")
    source["tiers"][0]["line_items"][0]["monthly_allocations"] = mo.default_allocation(30000.0, base)
    result = run(source, start_date="2027-02-15", end_date="2027-04-30")
    assert lines_by_id(result.state)["li_fb"]["monthly_allocations"] is None
    assert result.state["monthly_distribution_mode"] == "prorated"
    assert "split_reset" not in codes(result)                      # nothing was lost: it only followed a default


def test_14_a_split_within_a_dollar_of_a_default_follows_the_default():
    source = fx.source_monthly()
    source["tiers"][0]["line_items"][0]["monthly_allocations"] = {"2026-09": 10000.4, "2026-10": 9999.8, "2026-11": 9999.8}
    assert lines_by_id(run(source).state)["li_fb"]["monthly_allocations"] is None


def test_14_an_unknown_distribution_mode_becomes_even():
    source = fx.source_monthly()
    source["monthly_distribution_mode"] = "banana"
    assert run(source).state["monthly_distribution_mode"] == "even"


def test_added_value_lines_and_lines_with_the_breakdown_off_carry_no_split():
    source = only_option_a(fx.source_monthly())
    li = source["tiers"][0]["line_items"][1]
    li.update(is_added_value=True, monthly_budget=0.0, monthly_allocations={"2026-09": 100.0, "2026-10": 300.0, "2026-11": 100.0})     # Step 06 never splits these
    source["tiers"][0]["line_items"][2].update(mb_off=True)
    result = run(source)
    lines = lines_by_id(result.state)
    assert lines["li_ctv"]["monthly_allocations"] is None and lines["li_ctv"]["is_added_value"] is True and lines["li_disp"]["mb_off"] is True
    assert "split_reset" not in codes(result)                  # nothing to report: an Added Value line holds no planner split


# ====================================================================================================================== 15. avails

def estimate(**extra) -> dict:
    return {"max_imps": 100_000, "max_spend": 1200, **extra}


@pytest.mark.parametrize("marker, value", [("forecast_snapshot", {"v": 1, "method": "meta", "request": {}, "result": {}}), ("meta_forecast", {"audience": 1}),
                                           ("geo_resolution", {"label": "Phoenix"}), ("win_rate", 0.15), ("hispanic_share", 0.4)])
def test_15_any_constanza_marker_removes_the_whole_entry(marker, value):
    source = fx.source_monthly()
    source["tiers"][0]["avails_data"] = {"li_fb": estimate(**{marker: value}), "li_ctv": estimate()}
    result = run(source)
    assert list(result.state["tiers"][0]["avails_data"]) == ["li_ctv"] and result.state["avails_data"] == result.state["tiers"][0]["avails_data"]
    assert "1 line:" in note(result, "avails_cleared")["text"] and result.stats["avails_cleared"] == 1


def test_15_an_estimated_flag_alone_is_a_typed_number_not_a_constanza_estimate():
    source = fx.source_monthly()
    source["tiers"][0]["avails_data"] = {"li_fb": estimate(max_imps_estimated=True, max_spend_estimated=True, basis="imps")}
    result = run(source)
    assert list(result.state["tiers"][0]["avails_data"]) == ["li_fb"] and "avails_cleared" not in codes(result)


def test_15_hand_typed_numbers_and_free_form_text_are_kept_and_counted():
    result = run(fx.source_weekly())
    entry = result.state["tiers"][0]["avails_data"]["li_w2"]
    assert entry["freeform"] is True and entry["max_imps_text"] == "about 50,000 clicks"
    assert "1 line still carries avails numbers typed by hand" in note(result, "avails_manual_kept")["text"]
    assert entry["est_uniques_text"] is None                                                       # "Acme shoppers, estimated" names the old client


def test_15_keep_avails_keeps_every_entry_and_says_what_they_were_calculated_for():
    result = run(fx.source_monthly(), keep_avails=True)
    avails = result.state["tiers"][0]["avails_data"]
    assert set(avails) == {"li_fb", "li_ctv"} and avails["li_fb"]["win_rate"] == 0.15 and avails["li_fb"]["forecast_snapshot"]["method"] == "meta"
    assert "avails_kept" in codes(result) and "avails_cleared" not in codes(result) and "avails_manual_kept" not in codes(result)


def test_15_keep_avails_still_drops_a_snapshot_that_names_the_old_client_but_keeps_its_numbers():
    source = fx.source_monthly()
    source["tiers"][0]["avails_data"]["li_fb"]["forecast_snapshot"]["line"]["target_text"] = "Acme dealerships in Phoenix"
    source["tiers"][0]["avails_data"]["li_ctv"]["meta_forecast"] = {"audience": "Initiative Media's list"}
    entry = run(source, keep_avails=True).state["tiers"][0]["avails_data"]
    assert entry["li_fb"]["forecast_snapshot"] is None and entry["li_fb"]["max_imps"] == 1_200_000 and entry["li_fb"]["win_rate"] == 0.15
    assert entry["li_ctv"]["meta_forecast"] is None and entry["li_ctv"]["max_imps"] == 400_000


def test_15_keep_avails_says_nothing_when_there_was_no_estimate_to_keep():
    assert "avails_kept" not in codes(run(fx.source_full_flight(), keep_avails=True))


def test_15_every_option_settles_its_own_avails():
    source = fx.source_monthly()
    source["tiers"][1]["avails_data"] = {"li_b1": estimate(win_rate=0.1), "li_b2": estimate()}
    result = run(source)
    assert list(tier(result.state, "B")["avails_data"]) == ["li_b2"] and result.stats["avails_cleared"] == 2


def test_15_a_malformed_avails_entry_is_left_alone_not_a_crash():
    source = fx.source_monthly()
    source["tiers"][0]["avails_data"]["li_junk"] = "not a dict"
    assert run(source).state["tiers"][0]["avails_data"]["li_junk"] == "not a dict"


# ====================================================================================================================== 16. scrub

def free_text_source() -> dict:
    """Option A holds free text that names the old client in every field the transform looks at."""
    source = fx.source_monthly()
    a = source["tiers"][0]
    a["name"], a["geo"] = "Acme Core", "Acme dealer territory"
    a["line_items"][0].update(notes_override="Acme launch weekend promo", line_modifier="For Acme Motors only", target_override="Acme shoppers",
                              target_secondary="Initiative Media's audience", objective_override="Beat Acme's rival")
    source["addons"][0]["notes_override"] = "Acme ask"
    source["request"].update(geo="Acme country", demo="Acme owners", behavioral="Visited acmemotors.com", campaign_goal="Awareness for Acme",
                             language="Spanish", contextual="", products_selected_raw="Acme package")
    return source


def test_16_free_text_that_names_the_old_client_or_agency_is_cleared_in_every_place_and_counted():
    result = run(free_text_source())
    a = tier(result.state, "A")
    assert a["name"] is None and a["geo"] is None
    li = lines_by_id(result.state)["li_fb"]
    assert [li[k] for k in ("notes_override", "line_modifier", "target_override", "target_secondary", "objective_override")] == [None] * 5
    assert result.state["addons"][0]["notes_override"] is None
    request = result.state["request"]
    assert [request[k] for k in ("geo", "demo", "behavioral", "campaign_goal", "products_selected_raw")] == [""] * 5
    assert request["language"] == "Spanish"                              # nothing to clear: kept
    # 2 on the option + 5 on li_fb + 1 on li_disp ("shop at Acme dealerships", in the fixture) + 1 add-on note + 5 request fields
    assert lines_by_id(result.state)["li_disp"]["target_override"] is None
    assert result.stats["texts_cleared"] == 14 and "14 notes or targeting fields" in note(result, "texts_cleared")["text"]
    assert note(result, "texts_cleared")["level"] == "check"


def test_16_a_client_named_with_a_possessive_is_recognised_without_it():
    source = with_request(fx.source_monthly(), client_name="Wendy's", agency_name="", client_website="")
    source["tiers"][0]["line_items"][0]["notes_override"] = "Wendy wants weekend flights"
    result = run(source)
    assert lines_by_id(result.state)["li_fb"]["notes_override"] is None and "texts_cleared" in codes(result)


def test_16_text_that_is_about_something_else_is_kept():
    source = fx.source_monthly()
    li = source["tiers"][0]["line_items"][0]
    li.update(notes_override="Weekend promo", line_modifier="Hispanic, Phoenix only")
    result = run(source)
    kept = lines_by_id(result.state)["li_fb"]
    assert kept["notes_override"] == "Weekend promo" and kept["line_modifier"] == "Hispanic, Phoenix only"


def test_16_a_sibling_brand_keeps_the_words_it_shares_with_the_old_client():
    result = run(fx.source_monthly(), client_name="Acme Boats", agency_name="Initiative Media")
    li = lines_by_id(result.state)["li_fb"]
    assert tier(result.state, "A")["name"] == "Acme Core" and li["notes_override"] == "Acme launch weekend promo"
    assert lines_by_id(result.state)["li_disp"]["target_override"] == "Adults 25-54 who shop at Acme dealerships"
    assert "texts_cleared" not in codes(result)


def test_16_the_whole_old_name_is_still_cleared_for_a_sibling_brand():
    source = fx.source_monthly()
    source["tiers"][0]["line_items"][0]["notes_override"] = "Acme Motors wants it live Friday"
    result = run(source, client_name="Acme Boats")
    assert lines_by_id(result.state)["li_fb"]["notes_override"] is None


def test_16_the_same_agency_keeps_its_notes_and_a_different_one_clears_them():
    source = fx.source_monthly()
    source["tiers"][0]["line_items"][0]["notes_override"] = "Initiative wants weekly reports"
    assert lines_by_id(run(copy.deepcopy(source), agency_name="Initiative Media").state)["li_fb"]["notes_override"] == "Initiative wants weekly reports"
    assert lines_by_id(run(copy.deepcopy(source), agency_name="Other Agency").state)["li_fb"]["notes_override"] is None
    assert lines_by_id(run(copy.deepcopy(source)).state)["li_fb"]["notes_override"] is None             # a direct client: no agency


def test_16_the_old_website_name_is_a_token_and_a_new_site_with_the_same_name_is_not():
    source = fx.source_monthly()
    source["tiers"][0]["line_items"][0]["notes_override"] = "Link the ad to acmemotors.com/fall"
    assert lines_by_id(run(copy.deepcopy(source)).state)["li_fb"]["notes_override"] is None
    assert lines_by_id(run(copy.deepcopy(source), client_name="Acme Boats").state)["li_fb"]["notes_override"] is None      # "acmemotors" is not "acme"


def test_16_a_client_with_no_name_or_site_clears_nothing():
    source = with_request(fx.source_monthly(), client_name="", agency_name="", client_website="")
    assert "texts_cleared" not in codes(run(source))


def test_the_notes_say_one_or_many_correctly():
    one = run(fx.source_full_flight())
    assert note(one, "texts_cleared")["text"] == "1 note or targeting field mentioned the original client or agency and was cleared."
    assert note(run(fx.source_monthly()), "tier_dates_cleared")["text"].startswith("1 option had its own dates and now follows the new flight (Option B ran ")
    assert note(run(fx.source_monthly()), "avails_cleared")["text"].startswith("Avails estimates from Constanza were cleared on 1 line:")
    assert note(run(fx.source_monthly()), "avails_manual_kept")["text"].startswith("1 line still carries avails numbers typed by hand.")
    rates = fx.source_monthly()
    rates["tiers"][0]["line_items"][0]["rate_override"] = 9.5
    assert note(run(rates), "rates_carried")["text"].startswith("1 line carries a custom rate from the original.")
    many = fx.source_monthly()
    many["tiers"][1]["period_merge_groups"] = [["2026-09", "2026-10"], ["2026-11", "2026-12"]]
    many["tiers"][1]["avails_data"] = {"li_b1": estimate(win_rate=0.1), "li_b2": estimate(), "li_b3": estimate(max_spend=None)}
    many["tiers"][0]["line_items"][0]["rate_override"] = 9.5
    many["tiers"][0]["line_items"][1]["rate_override"] = 8.5
    result = run(many, start_date="2027-02-17", end_date="2027-04-30")                  # a different shape: the splits of A and B are reset
    assert note(result, "merges_dropped")["text"].startswith("2 combined period groups were removed because")
    assert note(result, "split_reset")["text"].startswith("2 lines had a hand-made period split that couldn't be carried to the new flight; they start from")
    assert note(result, "avails_cleared")["text"].startswith("Avails estimates from Constanza were cleared on 2 lines:")
    assert note(result, "avails_manual_kept")["text"].startswith("3 lines still carry avails numbers typed by hand.")
    assert note(result, "rates_carried")["text"].startswith("2 lines carry a custom rate from the original.")
    assert note(result, "texts_cleared")["text"].endswith("were cleared.") and note(result, "tier_dates_cleared")["text"].startswith("1 option had its own dates and now follows")


# ====================================================================================================================== 17-18. request type, the reset set

def test_17_a_renewal_request_type_is_blanked_with_a_note_and_the_renewal_fields_reset():
    source = with_request(fx.source_monthly(), request_type="Digital Renewal Plan")
    result = run(source)
    request = result.state["request"]
    assert request["request_type"] == "" and note(result, "request_type_blanked")["level"] == "info"
    assert [request[k] for k in ("renewal_client", "renewal_budget", "renewal_ae_requesting", "renewal_due_date")] == [""] * 4


def test_17_a_request_type_the_form_sets_wins_and_a_renewal_one_is_blanked_too():
    assert run(fx.source_monthly(), request_type="Avails Only").state["request"]["request_type"] == "Avails Only"
    blanked = run(fx.source_monthly(), request_type="Renewal request")
    assert blanked.state["request"]["request_type"] == "" and "request_type_blanked" in codes(blanked)
    assert run(fx.source_monthly(), request_type="").state["request"]["request_type"] == ""
    assert "request_type_blanked" not in codes(run(fx.source_monthly()))


def test_18_everything_written_for_the_old_client_is_reset_and_the_form_decides_the_rest():
    result = run(with_request(fx.source_monthly(), question_details="Does the client want a CTV test in the first month?"), notion_id="EVC-50001", agency_name="Beta Media", agency_fee=0.1, client_website="https://beta.example",
                 campaign_name="  Spring Push ")
    state, request = result.state, result.state["request"]
    assert (state["strategy_brief"], state["roadblocks"], state["enrichment"], state["raw_notion_text"]) == (None, None, None, None)
    assert state["force_tabs"] == {} and state["wizard_step"] == 2 and state["campaign_name_override"] == "Spring Push"
    assert request["specifics"] == {name: {} for name in ("facebook_ig", "sem", "ctv_ott", "dooh", "geofencing", "spotify", "audio_engage")}
    assert (request["ccs"], request["salesperson_comments"], request["question_details"], request["warnings"]) == ("", "", "", [])
    assert (request["notion_id"], request["client_name"], request["client_website"], request["agency_name"], request["agency_fee"]) == (
        "EVC-50001", "Beta Burgers", "https://beta.example", "Beta Media", 0.1)
    assert request["start_date"] == FEB_APR[0] and request["end_date"] == FEB_APR[1] and request["total_months"] == 3
    # carried: what saves the planner real work and cannot be mistaken for the old client
    assert request["monthly_budget"] == 23000.0 and request["tiered_budget"] is True and request["tier_1"] == "23000"
    assert (request["geo"], request["demo"], request["behavioral"], request["campaign_goal"], request["language"]) == (
        "Phoenix DMA", "Hispanic adults 25-54", "In-market auto buyers", "Awareness", "Spanish")
    assert request["products_selected"] == fx.source_monthly()["request"]["products_selected"] and state["time_unit"] == "month"
    assert state["addons"][0]["product_name"] == "Brand Safety" and state["addons"][0]["amount"] == 1500.0


def test_18_the_requester_fields_are_the_originals_unless_the_form_says_otherwise():
    kept = run(fx.source_monthly()).state["request"]
    assert (kept["requested_by"], kept["salesperson_email"], kept["salesperson_market"]) == ("Ana Torres", "ana@entravision.com", "Phoenix")
    changed = run(fx.source_monthly(), requested_by="Luis Ortega", salesperson_email="luis@entravision.com", salesperson_market="Dallas").state["request"]
    assert (changed["requested_by"], changed["salesperson_email"], changed["salesperson_market"]) == ("Luis Ortega", "luis@entravision.com", "Dallas")
    blank = run(fx.source_monthly(), requested_by="", salesperson_email="", salesperson_market="").state["request"]
    assert (blank["requested_by"], blank["salesperson_email"], blank["salesperson_market"]) == ("", "", "")


def test_18_a_blank_campaign_name_is_no_override_and_a_blank_notion_id_is_blank():
    result = run(fx.source_monthly(), campaign_name="", notion_id="")
    assert result.state["campaign_name_override"] is None and result.state["request"]["notion_id"] == ""


def test_18_the_billing_period_and_an_unknown_one():
    assert run(fx.source_weekly()).state["time_unit"] == "week" and run(fx.source_quarterly(), start_date="2027-03-01", end_date="2027-08-31").state["time_unit"] == "quarter"
    source = fx.source_monthly()
    source["time_unit"] = "fortnight"                                   # the lenient reading reopen uses
    assert run(source).state["time_unit"] == "month"


def test_18_the_ai_work_is_reported_as_not_copied_and_nothing_calls_an_ai_service(monkeypatch):
    from app.services import ai_enricher
    monkeypatch.setattr(ai_enricher, "enrich_proposal", lambda *a, **k: pytest.fail("the transform must not call the AI"))
    assert note(run(fx.source_monthly()), "ai_dropped")["level"] == "info"


# ====================================================================================================================== 19-20. rates, missing products

def test_19_custom_rates_are_kept_and_counted():
    source = fx.source_monthly()
    source["tiers"][0]["line_items"][0]["rate_override"] = 9.5
    source["tiers"][1]["line_items"][0]["estimated_cpm_override"] = 14.0
    source["tiers"][0]["line_items"][1]["buying_model_override"] = "CPM"          # a buying model is not a rate
    result = run(source)
    assert "2 lines carry a custom rate" in note(result, "rates_carried")["text"] and note(result, "rates_carried")["level"] == "check"
    assert lines_by_id(result.state)["li_fb"]["rate_override"] == 9.5 and lines_by_id(result.state)["li_b1"]["estimated_cpm_override"] == 14.0
    assert "rates_carried" not in codes(run(fx.source_monthly()))


def test_20_a_product_that_left_the_catalog_stays_on_its_line_and_is_listed_once():
    source = fx.source_monthly()
    source["tiers"][0]["line_items"][0]["product_name"] = "Retired Product"
    source["tiers"][1]["line_items"][0]["product_name"] = "Retired Product"
    result = run(source, known=lambda name: name != "Retired Product")
    assert lines_by_id(result.state)["li_fb"]["product_name"] == "Retired Product"
    text = note(result, "products_missing")["text"]
    assert text.count("Retired Product") == 1 and note(result, "products_missing")["level"] == "check"
    assert "products_missing" not in codes(run(fx.source_monthly()))


# ====================================================================================================================== 21-24. properties over every source

SOURCES = list(fx.all_sources())
FLIGHTS = {                                                  # a spread of new flights: same length, longer, shorter, partial, across a year, one month
    "same_length": ("2027-02-01", "2027-04-30"), "longer": ("2027-02-01", "2027-06-30"), "shorter": ("2027-02-01", "2027-02-28"),
    "partial_first": ("2027-02-17", "2027-04-30"), "partial_last": ("2027-02-01", "2027-04-15"), "across_a_year": ("2026-11-15", "2027-06-20"),
    "sixty_one_days": ("2027-03-01", "2027-04-30"), "same_dates": ("2026-09-01", "2026-11-30"), "one_day": ("2027-05-05", "2027-05-05"),
}


def every_case():
    for name in SOURCES:
        for flight, (start, end) in FLIGHTS.items():
            yield pytest.param(name, start, end, id=f"{name}-{flight}")


@pytest.mark.parametrize("keep", [False, True])
def test_21_the_input_is_never_changed(keep):
    for name, source in fx.all_sources().items():
        before = json.dumps(source, sort_keys=True)
        run(source, keep_avails=keep)
        run(source, start_date="2027-02-17", end_date="2027-06-30", keep_avails=keep)
        assert json.dumps(source, sort_keys=True) == before, name


def test_21_the_output_shares_nothing_with_the_input():
    source = fx.source_monthly()
    result = run(source)
    result.state["tiers"][0]["line_items"][0]["id"] = "changed"
    result.state["request"]["geo"] = "changed"
    assert source["tiers"][0]["line_items"][0]["id"] == "li_fb" and source["request"]["geo"] == "Phoenix DMA"


@pytest.mark.parametrize("keep", [False, True])
@pytest.mark.parametrize("name, start, end", list(every_case()))
def test_22_nothing_in_the_copy_names_the_old_client_or_agency(name, start, end, keep):
    result = run(source(name), start_date=start, end_date=end, keep_avails=keep)
    leaked = [s for s in strings(result.state) if any(word in s.lower() for word in fx.LEAK_WORDS)]
    assert leaked == [], leaked


def test_22_the_scan_itself_would_notice_a_leak():
    """Guards the scan: the SOURCE does name the old client, in many places."""
    leaked = [s for s in strings(fx.source_monthly()) if any(word in s.lower() for word in fx.LEAK_WORDS)]
    assert len(leaked) >= 8


@pytest.mark.parametrize("name, start, end", list(every_case()))
def test_23_every_kept_split_balances_and_is_keyed_by_the_new_periods(name, start, end):
    result = run(source(name), start_date=start, end_date=end)
    unit = result.state["time_unit"]
    base = mo.periods_between(date.fromisoformat(start), date.fromisoformat(end), unit)
    for t in result.state["tiers"] or [{"line_items": result.state["line_items"], "period_merge_groups": None}]:
        merges = t.get("period_merge_groups") or []
        keys = [p["key"] for p in mo.apply_period_merges(base, merges)]
        for li in t["line_items"]:
            assert li["months"] == len(base)
            allocation = li["monthly_allocations"]
            if unit == mo.FULL_FLIGHT:
                assert allocation is None and not merges
            if not allocation:
                continue
            assert list(allocation) == keys, (li["id"], list(allocation), keys)
            assert abs(sum(allocation.values()) - li["monthly_budget"] * li["months"]) <= 0.005 * (len(keys) + 1), li["id"]
    assert result.state["request"]["total_months"] == len(mo.months_between(date.fromisoformat(start), date.fromisoformat(end)))


@pytest.mark.parametrize("name, start, end", list(every_case()))
def test_24_the_output_is_a_fixed_point_of_the_draft_models_and_the_reopen_builder(name, start, end):
    result = run(source(name), start_date=start, end_date=end)
    draft = main.DraftSaveRequest(**result.state)
    rebuilt = main._build_reopen_state(
        request_dict=draft.request, line_items=draft.line_items, tiers=draft.tiers, avails_data=draft.avails_data, strategy_brief=draft.strategy_brief,
        roadblocks=draft.roadblocks, force_tabs=draft.force_tabs, addons=draft.addons, raw_notion_text=draft.raw_notion_text, enrichment=draft.enrichment,
        time_unit=draft.time_unit, monthly_distribution_mode=draft.monthly_distribution_mode, campaign_name_override=draft.campaign_name_override,
        wizard_step=draft.wizard_step)
    assert json.loads(json.dumps(rebuilt)) == json.loads(json.dumps(result.state))


def test_the_frozen_note_codes_are_all_reachable_and_levels_are_info_or_check():
    seen: dict[str, str] = {}
    cases = [
        run(fx.source_monthly()), run(fx.source_monthly(), end_date="2027-05-31"), run(fx.source_monthly(), keep_avails=True),
        run(fx.source_monthly(), start_date="2026-09-01", end_date="2026-11-30"), run(fx.source_without_dates()), run(fx.source_weekly()),
        run(with_request(fx.source_monthly(), request_type="Renewal"), known=lambda n: False), run(fx.source_monthly_merged(), end_date="2027-05-31"),
    ]
    source = fx.source_monthly()
    source["tiers"][0]["line_items"][0]["rate_override"] = 3.0
    cases.append(run(source))
    for result in cases:
        for n in result.notes:
            assert set(n) == {"code", "level", "text"} and n["level"] in ("info", "check") and n["text"] and "<" not in n["text"]
            seen[n["code"]] = n["level"]
    assert set(seen) == set(pd.NOTE_CODES)
    # The level is part of the frozen contract: Step 02 lists the "check" notes up front (and the dialog counts them) and tucks the "info" ones away.
    assert seen == {
        "dates_moved": "info", "dates_same": "info", "dates_set": "info", "request_type_blanked": "info", "ai_dropped": "info",
        "total_changed": "check", "budgets_carried": "check", "rates_carried": "check", "tier_dates_cleared": "check", "split_reset": "check",
        "merges_dropped": "check", "avails_cleared": "check", "avails_manual_kept": "check", "avails_kept": "check", "texts_cleared": "check",
        "products_missing": "check",
    }


# ====================================================================================================================== bad input

def test_a_new_flight_that_is_not_a_usable_range_is_refused_loudly_not_guessed():
    for start, end in [("2027-04-30", "2027-02-01"), ("2027-02-01", "nonsense"), ("", ""), ("2027-01-01", "2040-01-01")]:
        with pytest.raises(ValueError):
            run(fx.source_monthly(), start_date=start, end_date=end)


def test_a_state_that_is_not_a_dict_is_refused():
    with pytest.raises(TypeError):
        pd.transform_reopen_state([], basics())


def test_a_damaged_source_is_copied_as_far_as_it_can_be_not_a_crash():
    source = {"request": {"client_name": "Acme Motors"}, "line_items": "junk", "tiers": [None, {"label": "A", "line_items": [None, {"id": "x", "product_name": "P", "monthly_budget": "lots", "months": None}], "avails_data": 7}],
              "addons": "junk", "time_unit": None}
    result = run(source)
    line = result.state["tiers"][0]["line_items"][0]
    assert line["months"] == 3 and line["monthly_allocations"] is None and result.state["tiers"][0]["avails_data"] == 7
    assert result.state["addons"] == [] and result.state["time_unit"] == "month"


def test_a_source_without_a_request_still_takes_the_form():
    result = run({"line_items": [], "tiers": None})
    assert result.state["request"]["client_name"] == "Beta Burgers" and result.state["request"]["total_months"] == 3


# ====================================================================================================================== describe_source

def test_describe_source_for_a_monthly_proposal():
    described = pd.describe_source(fx.source_monthly())
    assert described["source"] == {"start_date": "2026-09-01", "end_date": "2026-11-30", "time_unit": "month", "periods": 3, "option_count": 2, "line_count": 5}
    assert described["defaults"] == {
        "requested_by": "Ana Torres", "salesperson_email": "ana@entravision.com", "salesperson_market": "Phoenix",
        "request_type": "Full Presentation (Deck + Avails + Proposal Included)", "agency_name": "Initiative Media", "agency_fee": 0.15,
        "client_website": "https://www.acmemotors.com",
    }


@pytest.mark.parametrize("name, unit, periods, options, lines", [("weekly", "week", 13, 1, 2), ("quarterly", "quarter", 2, 1, 2), ("full_flight", "full_flight", 1, 1, 3),
                                                                 ("legacy_single_tier", "month", 3, 1, 2), ("four_tiers", "month", 3, 4, 8)])
def test_describe_source_counts_periods_options_and_lines_for_every_shape(name, unit, periods, options, lines):
    facts = pd.describe_source(source(name))["source"]
    assert (facts["time_unit"], facts["periods"], facts["option_count"], facts["line_count"]) == (unit, periods, options, lines)


def test_describe_source_has_no_periods_for_dates_it_cannot_read_and_reports_each_date_it_can():
    assert pd.describe_source(fx.source_without_dates())["source"]["periods"] is None
    half = pd.describe_source(with_request(fx.source_monthly(), start_date="9/1/2026", end_date="soon"))["source"]
    assert (half["start_date"], half["end_date"], half["periods"]) == ("2026-09-01", None, None)
    backwards = pd.describe_source(with_request(fx.source_monthly(), start_date="2026-11-30", end_date="2026-09-01"))["source"]
    assert backwards["periods"] is None


def test_describe_source_blanks_a_renewal_request_type_and_an_unusable_fee_and_survives_nonsense():
    renewal = pd.describe_source(with_request(fx.source_monthly(), request_type="Digital Renewal Plan", agency_fee=15, requested_by=None, geo="x"))["defaults"]
    assert renewal["request_type"] == "" and renewal["agency_fee"] is None and renewal["requested_by"] == ""
    assert pd.describe_source(with_request(fx.source_monthly(), agency_fee=True))["defaults"]["agency_fee"] is None
    for junk in (None, [], "x", {}, {"request": 5, "tiers": "x"}):
        described = pd.describe_source(junk)
        assert described["source"]["periods"] is None and described["defaults"]["agency_fee"] is None and described["source"]["option_count"] == 1


# ====================================================================================================================== derive_proposal_id

def test_derive_proposal_id_is_stable_short_and_url_safe():
    one = pd.derive_proposal_id("planner@entravision.com", "SOURCEID", "request-id-0123456789", basics())
    assert one == pd.derive_proposal_id("planner@entravision.com", "SOURCEID", "request-id-0123456789", basics())
    assert len(one) == 22 and re.fullmatch(r"[A-Za-z0-9_-]{22}", one)
    assert pd.derive_proposal_id("PLANNER@Entravision.com ", "SOURCEID", "request-id-0123456789", basics()) == one          # an email is not case sensitive


@pytest.mark.parametrize("change", [
    dict(email="other@entravision.com"), dict(source_id="OTHERSOURCE"), dict(request_id="request-id-9999999999"),
    dict(client_name="Gamma Grill"), dict(start_date="2027-02-02"), dict(end_date="2027-05-01"), dict(notion_id="EVC-50002"), dict(agency_name="X"),
    dict(agency_fee=0.1), dict(client_website="https://x.example"), dict(requested_by="Luis"), dict(salesperson_email="l@entravision.com"),
    dict(salesperson_market="Dallas"), dict(request_type="Avails Only"), dict(campaign_name="Fall"), dict(keep_avails=True),
    dict(requested_by=""),                  # "keep the original's" (None) and "blank" ("") are different requests
])
def test_derive_proposal_id_changes_with_every_input(change):
    ids = dict(email="planner@entravision.com", source_id="SOURCEID", request_id="request-id-0123456789")
    base = pd.derive_proposal_id(**ids, basics=basics())
    fields = {k: v for k, v in change.items() if k in ids}
    form = {k: v for k, v in change.items() if k not in ids}
    assert pd.derive_proposal_id(**{**ids, **fields}, basics=basics(**form)) != base


# ====================================================================================================================== the helpers

@pytest.mark.parametrize("names, expected", [
    (("Acme Motors", "Initiative Media"), ["acme motors", "acme", "initiative media", "initiative"]),
    (("Acme Motors", ""), ["acme motors", "acme"]),                                      # "motors" is a generic word
    (("Joe's Pizza",), ["joe's pizza", "joe's", "pizza"]),
    (("Wendy's",), ["wendy's", "wendy"]),                                                # a possessive also gives its root (when that is long enough)
    (("GE",), []),                                                                       # two letters would match half the language
    (("Café Ñandú",), ["cafe nandu", "cafe", "nandu"]),                                  # accents folded
    (("AT&T",), ["at&t"]),
    (("Auto Group",), ["auto group"]),                                                   # the whole name always counts, its generic words do not
    ((None, 5, ["x"]), []),
    (("  Beta   Burgers  ",), ["beta burgers", "beta", "burgers"]),
    (("Bank of the West", ""), ["bank of the west", "west"]),
])
def test_scrub_tokens(names, expected):
    assert pd.scrub_tokens(*names) == expected


@pytest.mark.parametrize("text, tokens, expected", [
    ("Acme launch weekend", ["acme"], True), ("ACME", ["acme"], True), ("Acme's promo", ["acme"], True), ("an acme-branded cap", ["acme"], True),
    ("Acmeville", ["acme"], False), ("pre-acmeish", ["acme"], False), ("macme", ["acme"], False), ("The CAFÉ opens", ["cafe"], True),
    ("see acmemotors.com", ["acmemotors"], True), ("Acme Motors, Phoenix", ["acme motors"], True), ("acme", [], False), ("", ["acme"], False),
    (None, ["acme"], False), (5, ["acme"], False), ("nothing here", ["acme", "initiative"], False), ("Initiative: weekly", ["initiative"], True),
    ("Acme", ["Acme"], True),                                          # tokens are folded too
])
def test_mentions(text, tokens, expected):
    assert pd.mentions(text, tokens) is expected


@pytest.mark.parametrize("url, expected", [
    ("https://www.acmemotors.com", ["acmemotors"]), ("http://acmemotors.com/fall?x=1#top", ["acmemotors"]), ("acmemotors.com", ["acmemotors"]),
    ("shop.acme.co.uk", ["acme"]), ("https://user@www.acme.com.mx:8080/x", ["acme"]), ("https://www.go.com", []), ("localhost", ["localhost"]),
    ("", []), (None, []), ("https://www.motors.com", []),
])
def test_website_token(url, expected):
    assert pd.website_token(url) == expected


def test_shape_signature_tells_whole_months_from_partial_ones_and_a_short_week_is_whole():
    assert pd.shape_signature(mo.periods_between(date(2027, 2, 1), date(2027, 4, 30), "month")) == (True, True, True)
    assert pd.shape_signature(mo.periods_between(date(2027, 2, 17), date(2027, 4, 15), "month")) == (False, True, False)
    assert pd.shape_signature(mo.periods_between(date(2027, 2, 1), date(2027, 4, 30), "week")) == (True,) * 13
    assert pd.shape_signature(mo.periods_between(date(2027, 3, 1), date(2027, 7, 31), "quarter")) == (True, True)
    assert pd.shape_signature([]) == ()


def test_translate_merges_moves_groups_to_the_same_positions():
    old = mo.periods_between(date(2026, 9, 1), date(2027, 1, 31), "month")        # five months
    new = mo.periods_between(date(2027, 2, 1), date(2027, 6, 30), "month")
    assert pd.translate_merges([["2026-09", "2026-10"], ["2026-12", "2027-01"]], old, new) == [["2027-02", "2027-03"], ["2027-05", "2027-06"]]
    assert pd.translate_merges([["2026-10", "2026-09"]], old, new) == [["2027-02", "2027-03"]]                 # apply_period_merges sorts a group, so does this
    assert pd.translate_merges([["2026-09", "2026-10", "2026-11"]], old, new) == [["2027-02", "2027-03", "2027-04"]]
    assert pd.translate_merges([["2026-09", "2026-10"]], old, new[:4]) == []                                      # a different period count carries nothing


@pytest.mark.parametrize("groups, expected", [
    ([["2026-09"]], []), ([["2026-09", "2026-11"]], []), ([["2026-09", "2099-01"]], []), ([["2026-09", "2026-09"]], []), ([None], []), (["2026-09"], []),
    ([[1, 2]], []), (None, []), ("x", []), ([["2026-09", "2026-10"], ["2026-10", "2026-11"]], [["2027-02", "2027-03"]]),
])
def test_translate_merges_ignores_what_apply_period_merges_would_ignore(groups, expected):
    old = mo.periods_between(date(2026, 9, 1), date(2026, 12, 31), "month")
    new = mo.periods_between(date(2027, 2, 1), date(2027, 5, 31), "month")
    carried = pd.translate_merges(groups, old, new)
    assert carried == expected
    # whatever is carried leaves the two flights with the same number of periods, which is what position-by-position carrying relies on
    position = {p["key"]: i for i, p in enumerate(new)}
    same_groups_in_the_old_flight = [[old[position[k]]["key"] for k in group] for group in carried]
    assert len(mo.apply_period_merges(old, same_groups_in_the_old_flight)) == len(mo.apply_period_merges(new, carried))


def test_translate_merges_keeps_the_first_of_two_overlapping_groups():
    old = mo.periods_between(date(2026, 9, 1), date(2026, 12, 31), "month")
    new = mo.periods_between(date(2027, 2, 1), date(2027, 5, 31), "month")
    assert pd.translate_merges([["2026-09", "2026-10"], ["2026-10", "2026-11"]], old, new) == [["2027-02", "2027-03"]]


def test_follows_default_is_the_either_default_within_a_dollar_test_of_the_page():
    periods = mo.periods_between(date(2026, 9, 15), date(2026, 11, 30), "month")
    assert pd.follows_default(mo.even_allocation(30000.0, periods), 30000.0, periods)
    assert pd.follows_default(mo.default_allocation(30000.0, periods), 30000.0, periods)                    # the day-prorated one
    even = mo.even_allocation(30000.0, periods)
    assert pd.follows_default({k: v + 1.0 for k, v in even.items()}, 30000.0, periods) is True            # exactly a dollar out
    assert pd.follows_default({k: v + 1.01 for k, v in even.items()}, 30000.0, periods) is False
    assert pd.follows_default({"2026-09": 5000.0, "2026-10": 15000.0, "2026-11": 10000.0}, 30000.0, periods) is False
    assert pd.follows_default({}, 30000.0, []) is False and pd.follows_default({}, 0.0, periods) is True


def test_option_totals_by_option_letter():
    assert pd.option_totals(fx.source_monthly()) == {"A": 69000.0, "B": 32000.0}
    assert pd.option_totals(fx.source_legacy_single_tier()) == {"A": 39000.0}
    assert pd.option_totals({}) == {"A": 0.0} and pd.option_totals(None) == {"A": 0.0}
    assert pd.option_totals({"tiers": [{"label": "C", "line_items": [{"monthly_budget": 1.234, "months": 3}, {"monthly_budget": None, "months": 2}]}]}) == {"C": 3.7}


@pytest.mark.parametrize("value, limit, expected", [
    ("  Beta \t Burgers\n", 50, "Beta Burgers"), ("a\x00b​c‮d﻿e", 50, "a b c d e"), ("lone \ud800 surrogate", 50, "lone surrogate"), (None, 5, ""),
    ("café Ñ", 50, "café Ñ"), (" spaced ", 50, "spaced"), ("x" * 10, 10, "x" * 10),
])
def test_plain_text(value, limit, expected):
    assert pd.plain_text(value, limit) == expected


def test_plain_text_refuses_an_entry_padded_to_four_times_its_limit_before_cleaning_it_and_accepts_modest_padding():
    assert pd.plain_text(" " * 100 + "ab", 10) == "ab"                                   # 102 characters: within four times the limit plus a little
    with pytest.raises(ValueError, match="at most 10 characters"):
        pd.plain_text(" " * 1000 + "ab", 10)                                               # nobody types that; it is refused without walking every character


@pytest.mark.parametrize("value", ["x" * 11, "x" * 5000, 5, ["a"], {"a": 1}, True])
def test_plain_text_refuses_what_is_too_long_or_not_text_in_plain_words(value):
    with pytest.raises(ValueError) as caught:
        pd.plain_text(value, 10, "The client name")
    assert str(caught.value) in ("The client name can be at most 10 characters.", "The client name must be text.")
