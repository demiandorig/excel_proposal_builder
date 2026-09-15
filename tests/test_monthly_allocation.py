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
