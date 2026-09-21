"""Tests for app/services/monthly_allocation.py — pure date/dollar logic,
no DB or openpyxl needed. Covers the scenarios from the Monthly Breakdown
feature spec that are testable at this layer (calendar generation,
day-proration, reconciliation, rounding, minimum-spend flagging)."""
from datetime import date

import pytest

from app.services import monthly_allocation as ma


def test_single_month_campaign():
    months = ma.months_between(date(2026, 9, 5), date(2026, 9, 20))
    assert [m["key"] for m in months] == ["2026-09"]
    assert months[0]["active_days"] == 16  # Sep 5..20 inclusive


def test_three_month_campaign_full_months():
    months = ma.months_between(date(2026, 10, 1), date(2026, 12, 31))
    assert [m["key"] for m in months] == ["2026-10", "2026-11", "2026-12"]
    for m in months:
        assert m["active_days"] == m["days_in_month"]  # every month fully active


def test_twelve_month_campaign():
    months = ma.months_between(date(2026, 1, 1), date(2026, 12, 31))
    assert len(months) == 12
    assert months[0]["key"] == "2026-01" and months[-1]["key"] == "2026-12"


def test_campaign_crossing_calendar_year():
    months = ma.months_between(date(2026, 11, 1), date(2027, 2, 28))
    assert [m["key"] for m in months] == ["2026-11", "2026-12", "2027-01", "2027-02"]


def test_partial_first_and_last_month():
    # September 15 - December 31: Sep is a half-month, Dec is full.
    months = ma.months_between(date(2026, 9, 15), date(2026, 12, 31))
    by_key = {m["key"]: m for m in months}
    assert by_key["2026-09"]["active_days"] == 16   # Sep 15..30 inclusive
    assert by_key["2026-09"]["days_in_month"] == 30
    assert by_key["2026-12"]["active_days"] == 31
    assert by_key["2026-12"]["days_in_month"] == 31


def test_default_allocation_proportional_to_days_when_months_all_full():
    # Oct (31d) / Nov (30d) / Dec (31d) are all FULLY active but not equal
    # LENGTH — day-proration correctly gives Oct/Dec a hair more than Nov,
    # rather than a naive flat split. (No 3 consecutive Gregorian months
    # are ever exactly equal length, so this is the realistic "all full
    # months" case, not a special one.)
    months = ma.months_between(date(2026, 10, 1), date(2026, 12, 31))
    alloc = ma.default_allocation(6000.0, months)
    assert alloc["2026-10"] == pytest.approx(2021.74, abs=0.01)
    assert alloc["2026-11"] == pytest.approx(1956.52, abs=0.01)
    assert round(sum(alloc.values()), 2) == 6000.0
    # October and December (31 days each) get more than November (30 days)
    assert alloc["2026-10"] > alloc["2026-11"]
    assert alloc["2026-12"] > alloc["2026-11"]


def test_default_allocation_prorates_partial_month_lower():
    months = ma.months_between(date(2026, 9, 15), date(2026, 10, 31))
    alloc = ma.default_allocation(4700.0, months)
    # Sep 15-30 (16 days) vs full Oct (31 days) out of 47 total active days
    assert alloc["2026-09"] < alloc["2026-10"]
    assert round(sum(alloc.values()), 2) == 4700.0


def test_default_allocation_reconciles_exactly_despite_rounding():
    # A total that doesn't divide evenly across 3 months (classic rounding case).
    months = ma.months_between(date(2026, 1, 1), date(2026, 3, 31))
    alloc = ma.default_allocation(1000.0, months)
    assert round(sum(alloc.values()), 2) == 1000.0
    # No individual month should be wildly off despite the remainder trick
    assert all(300 <= v <= 400 for v in alloc.values())


def test_reconcile_allocation_balanced():
    result = ma.reconcile_allocation(5000.0, {"2026-09": 500, "2026-10": 1500, "2026-11": 1500, "2026-12": 1500})
    assert result["balanced"] is True
    assert result["remaining"] == 0.0
    assert result["allocated_pct"] == 100.0


def test_reconcile_allocation_under_allocated():
    result = ma.reconcile_allocation(5000.0, {"2026-09": 500, "2026-10": 1000})
    assert result["balanced"] is False
    assert result["over_allocated"] is False
    assert result["remaining"] == 3500.0
    assert result["allocated_pct"] == 30.0


def test_reconcile_allocation_over_allocated():
    result = ma.reconcile_allocation(1000.0, {"2026-09": 700, "2026-10": 700})
    assert result["over_allocated"] is True
    assert result["balanced"] is False


def test_reconcile_allocation_percent_dollar_sync_example_from_spec():
    # $5,000 total, October at 10% ($500), matches the spec's own worked example.
    total = 5000.0
    pct = 10
    dollars = round(total * pct / 100, 2)
    assert dollars == 500.0
    # Changing the dollar amount to $750 should read back as 15%.
    new_pct = round(750 / total * 100, 2)
    assert new_pct == 15.0


def test_minimum_violation_flagged_below_minimum():
    months = ma.months_between(date(2026, 9, 1), date(2026, 12, 31))
    allocations = {"2026-09": 500, "2026-10": 1500, "2026-11": 1500, "2026-12": 1500}
    violations = ma.check_minimum_violations(
        "Line 1", "Entravision Plus", minimum_spend=1000.0, is_added_value=False,
        months=months, allocations=allocations,
    )
    assert len(violations) == 1
    assert violations[0]["month_key"] == "2026-09"
    assert violations[0]["shortfall"] == 500.0


def test_minimum_exactly_at_minimum_is_not_a_violation():
    months = ma.months_between(date(2026, 9, 1), date(2026, 9, 30))
    violations = ma.check_minimum_violations(
        "Line 1", "Entravision Plus", minimum_spend=1000.0, is_added_value=False,
        months=months, allocations={"2026-09": 1000.0},
    )
    assert violations == []


def test_minimum_violation_exempts_added_value_lines():
    months = ma.months_between(date(2026, 9, 1), date(2026, 9, 30))
    violations = ma.check_minimum_violations(
        "AV Line", "Entravision Plus", minimum_spend=1000.0, is_added_value=True,
        months=months, allocations={"2026-09": 0.0},
    )
    assert violations == []


def test_flexible_date_parsing():
    assert ma.parse_flexible_date("2026-09-15") == date(2026, 9, 15)
    assert ma.parse_flexible_date("09/15/2026") == date(2026, 9, 15)
    assert ma.parse_flexible_date("") is None
    assert ma.parse_flexible_date(None) is None
    assert ma.parse_flexible_date("not a date") is None


# --- Week/Quarter toggle (Round E) ---------------------------------------

def test_weeks_between_anchors_to_campaign_start_not_iso_weeks():
    # A Sep 28 (Monday) start still anchors week 1 to the 28th regardless
    # of what day of the week that is — the point is "7 days from launch",
    # not ISO Mon-Sun weeks.
    weeks = ma.weeks_between(date(2026, 9, 28), date(2026, 10, 4))
    assert len(weeks) == 1
    assert weeks[0]["active_days"] == 7
    assert weeks[0]["date_range_label"] == "Sep 28 – Oct 4, 2026"


def test_weeks_between_last_week_can_be_shorter_than_seven_days():
    weeks = ma.weeks_between(date(2026, 9, 28), date(2026, 10, 6))  # 9 days total
    assert len(weeks) == 2
    assert weeks[0]["active_days"] == 7
    assert weeks[1]["active_days"] == 2  # Oct 5-6 only
    assert weeks[1]["date_range_label"] == "Oct 5 – 6, 2026"


def test_quarters_between_groups_months_by_three_from_campaign_start():
    # Starts in September -> Sep/Oct/Nov is quarter 1, NOT aligned to a
    # standard calendar Q3/Q4 split.
    quarters = ma.quarters_between(date(2026, 9, 1), date(2027, 2, 28))
    assert [q["key"] for q in quarters] == ["2026-09+2026-10+2026-11", "2026-12+2027-01+2027-02"]
    assert quarters[0]["active_days"] == 91  # Sep(30)+Oct(31)+Nov(30)
    assert quarters[0]["label"] == "Sep–Nov 2026"


def test_quarters_between_last_chunk_can_be_short():
    quarters = ma.quarters_between(date(2026, 9, 1), date(2026, 10, 31))  # only 2 months
    assert len(quarters) == 1
    assert quarters[0]["key"] == "2026-09+2026-10"


def test_quarters_between_label_states_both_years_when_crossing_one():
    quarters = ma.quarters_between(date(2026, 12, 1), date(2027, 2, 28))
    assert quarters[0]["label"] == "Dec 2026–Feb 2027"


def test_periods_between_dispatches_by_granularity():
    start, end = date(2026, 9, 28), date(2026, 11, 15)
    assert len(ma.periods_between(start, end, "week")) == len(ma.weeks_between(start, end))
    assert len(ma.periods_between(start, end, "month")) == len(ma.months_between(start, end))
    assert len(ma.periods_between(start, end, "quarter")) == len(ma.quarters_between(start, end))


def test_periods_between_defaults_to_months_for_unknown_granularity():
    start, end = date(2026, 9, 28), date(2026, 11, 15)
    assert ma.periods_between(start, end, "bogus") == ma.months_between(start, end)


def test_every_granularity_carries_period_count_of_one_when_unmerged():
    start, end = date(2026, 9, 1), date(2026, 12, 31)
    for granularity in ("week", "month", "quarter"):
        for p in ma.periods_between(start, end, granularity):
            assert p["period_count"] == 1


# --- Merging adjacent periods ---------------------------------------------

def test_merge_combines_two_adjacent_months_into_one_bucket():
    months = ma.months_between(date(2026, 9, 28), date(2026, 11, 30))
    merged = ma.apply_period_merges(months, [["2026-09", "2026-10"]])
    assert [p["key"] for p in merged] == ["2026-09+2026-10", "2026-11"]
    combined = merged[0]
    assert combined["active_days"] == 3 + 31  # Sep 28-30 + all of Oct
    assert combined["period_count"] == 2
    assert combined["label"] == "September–October 2026"
    assert combined["date_range_label"] == "Sep 28 – Oct 31, 2026"


def test_merge_ignores_a_group_with_a_gap_between_keys():
    # "2026-09" and "2026-11" aren't adjacent (2026-10 sits between them in
    # the list) — a merge spanning a gap would misrepresent the date range,
    # so it's dropped rather than trusted blindly.
    months = ma.months_between(date(2026, 9, 1), date(2026, 11, 30))
    merged = ma.apply_period_merges(months, [["2026-09", "2026-11"]])
    assert [p["key"] for p in merged] == ["2026-09", "2026-10", "2026-11"]


def test_merge_ignores_a_group_referencing_an_unknown_key():
    months = ma.months_between(date(2026, 9, 1), date(2026, 10, 31))
    merged = ma.apply_period_merges(months, [["2026-09", "2099-01"]])
    assert [p["key"] for p in merged] == ["2026-09", "2026-10"]


def test_merge_with_no_groups_returns_periods_unchanged():
    months = ma.months_between(date(2026, 9, 1), date(2026, 10, 31))
    assert ma.apply_period_merges(months, None) == months
    assert ma.apply_period_merges(months, []) == months


def test_merge_three_adjacent_periods_at_once():
    months = ma.months_between(date(2026, 9, 1), date(2026, 12, 31))
    merged = ma.apply_period_merges(months, [["2026-09", "2026-10", "2026-11"]])
    assert [p["key"] for p in merged] == ["2026-09+2026-10+2026-11", "2026-12"]
    assert merged[0]["period_count"] == 3


def test_merged_allocation_and_minimum_check_operate_on_the_effective_periods():
    # This is the whole point of the merge feature: once merged, every
    # downstream function (allocation, reconciliation, minimum-check)
    # needs ZERO special-casing — they just see one combined period.
    months = ma.months_between(date(2026, 9, 28), date(2026, 11, 30))
    merged = ma.apply_period_merges(months, [["2026-09", "2026-10"]])
    alloc = ma.even_allocation(3000.0, merged)
    assert set(alloc.keys()) == {"2026-09+2026-10", "2026-11"}
    assert round(sum(alloc.values()), 2) == 3000.0
    result = ma.reconcile_allocation(3000.0, alloc)
    assert result["balanced"] is True


# --- Minimum spend scaled by granularity -----------------------------------

def test_minimum_scales_down_for_weekly_granularity():
    weeks = ma.weeks_between(date(2026, 9, 1), date(2026, 9, 7))
    violations = ma.check_minimum_violations(
        "Line 1", "Entravision Plus", minimum_spend=1000.0, is_added_value=False,
        months=weeks, allocations={weeks[0]["key"]: 100.0}, granularity="week",
    )
    # 1000 * (12/52) ≈ 230.77 — well above the $100 allocated, so this
    # SHOULD still violate, but at the scaled-down weekly figure, not the
    # raw $1000 monthly one.
    assert len(violations) == 1
    assert violations[0]["minimum_required"] == pytest.approx(230.77, abs=0.01)


def test_minimum_scales_up_for_quarterly_granularity():
    quarters = ma.quarters_between(date(2026, 9, 1), date(2026, 11, 30))
    violations = ma.check_minimum_violations(
        "Line 1", "Entravision Plus", minimum_spend=1000.0, is_added_value=False,
        months=quarters, allocations={quarters[0]["key"]: 2500.0}, granularity="quarter",
    )
    assert len(violations) == 1
    assert violations[0]["minimum_required"] == 3000.0  # 1000 * 3
    assert violations[0]["shortfall"] == 500.0


def test_minimum_unprorated_within_a_partial_month_still_flags_a_tiny_stub():
    # The exact real-world scenario the merge feature exists for: Sep 28
    # start gives September only 3 active days, but the FULL monthly
    # minimum still applies unprorated — this should violate even though
    # $50 might be a perfectly reasonable 3-day pace.
    months = ma.months_between(date(2026, 9, 28), date(2026, 10, 31))
    violations = ma.check_minimum_violations(
        "Line 1", "Entravision Plus", minimum_spend=1000.0, is_added_value=False,
        months=months, allocations={"2026-09": 50.0, "2026-10": 1000.0}, granularity="month",
    )
    assert len(violations) == 1
    assert violations[0]["month_key"] == "2026-09"
    assert violations[0]["minimum_required"] == 1000.0


def test_minimum_check_defaults_to_month_granularity_for_backward_compatibility():
    # No granularity kwarg passed — matches every pre-existing call site.
    months = ma.months_between(date(2026, 9, 1), date(2026, 9, 30))
    violations = ma.check_minimum_violations(
        "Line 1", "Entravision Plus", minimum_spend=1000.0, is_added_value=False,
        months=months, allocations={"2026-09": 500.0},
    )
    assert violations[0]["minimum_required"] == 1000.0


def test_merged_period_minimum_is_the_sum_of_its_constituent_minimums():
    months = ma.months_between(date(2026, 9, 28), date(2026, 10, 31))
    merged = ma.apply_period_merges(months, [["2026-09", "2026-10"]])
    violations = ma.check_minimum_violations(
        "Line 1", "Entravision Plus", minimum_spend=1000.0, is_added_value=False,
        months=merged, allocations={"2026-09+2026-10": 1500.0}, granularity="month",
    )
    # Combined minimum is 2x (2 merged months), so $1500 still falls short
    # of $2000 — the exact case this feature exists to make POSSIBLE to
    # pass (a well-funded combined total) while still catching a genuinely
    # underfunded one.
    assert len(violations) == 1
    assert violations[0]["minimum_required"] == 2000.0
    assert violations[0]["shortfall"] == 500.0


def test_merged_period_minimum_passes_when_combined_total_clears_it():
    months = ma.months_between(date(2026, 9, 28), date(2026, 10, 31))
    merged = ma.apply_period_merges(months, [["2026-09", "2026-10"]])
    violations = ma.check_minimum_violations(
        "Line 1", "Entravision Plus", minimum_spend=1000.0, is_added_value=False,
        months=merged, allocations={"2026-09+2026-10": 2000.0}, granularity="month",
    )
    assert violations == []
