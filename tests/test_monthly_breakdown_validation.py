"""Tests for app/main.py's _validate_monthly_breakdown — the authoritative
server-side gate for the Monthly Breakdown feature (main.py:/api/generate).
Mocks the catalog's DB-backed pieces (overrides/custom products/deleted
names) so this runs without a real DATABASE_URL — by_name() (called for
the minimum-spend check) goes through these on every call, not just at
import time, so the patch has to cover the whole test, not just the import."""
import os

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql://fake:fake@localhost/fake")

import app.main as m  # noqa: E402 — import is safe unpatched; only by_name() calls at TEST time need the DB mocked
from app.services.proposal_generator import LineItem
from app.services.notion_parser import ProposalRequest

_REQ = ProposalRequest(start_date="2026-09-01", end_date="2026-12-31")


@pytest.fixture(autouse=True)
def _mock_catalog_db(monkeypatch):
    """by_name() -> _apply_override() -> load_rate_overrides() would
    otherwise hit a real DATABASE_URL on every call this module makes."""
    monkeypatch.setattr("app.catalog.load_rate_overrides", lambda: {})
    monkeypatch.setattr("app.catalog.load_custom_products", lambda: [])
    monkeypatch.setattr("app.catalog.load_deleted_builtin_names", lambda: set())


def _tier(line_items, start_date=None, end_date=None, label="A"):
    return {"label": label, "start_date": start_date, "end_date": end_date, "line_items": line_items}


def test_balanced_allocation_produces_no_errors():
    li = LineItem(product_name="Search - SEM", monthly_budget=1000, months=4, id="1",
                  monthly_allocations={"2026-09": 1000, "2026-10": 1000, "2026-11": 1000, "2026-12": 1000})
    errors, warnings = m._validate_monthly_breakdown([_tier([li])], _REQ, multi_tier=False)
    assert errors == []


def test_unbalanced_allocation_is_a_balance_error():
    li = LineItem(product_name="Search - SEM", monthly_budget=1000, months=4, id="2",
                  monthly_allocations={"2026-09": 500, "2026-10": 1000, "2026-11": 1000, "2026-12": 1000})
    errors, _ = m._validate_monthly_breakdown([_tier([li])], _REQ, multi_tier=False)
    assert len(errors) == 1
    assert "under-allocated" in errors[0]
    assert "Search - SEM" in errors[0]


def test_over_allocated_is_also_a_balance_error():
    li = LineItem(product_name="Search - SEM", monthly_budget=1000, months=1, id="3",
                  monthly_allocations={"2026-09": 1500})
    errors, _ = m._validate_monthly_breakdown([_tier([li], start_date="2026-09-01", end_date="2026-09-30")], _REQ, multi_tier=False)
    assert len(errors) == 1 and "over-allocated" in errors[0]


def test_line_item_not_using_the_feature_is_ignored():
    li = LineItem(product_name="Search - SEM", monthly_budget=1000, months=4, id="4")  # monthly_allocations=None
    errors, warnings = m._validate_monthly_breakdown([_tier([li])], _REQ, multi_tier=False)
    assert errors == [] and warnings == []


def test_below_minimum_month_is_a_warning_not_an_error():
    # Search - SEM's real catalog minimum_spend is used here (not mocked) —
    # this only asserts the WARNING path fires when allocated < minimum,
    # regardless of the exact catalog figure.
    product = m.by_name("Search - SEM")
    assert product is not None and (product.minimum_spend or 0) > 0, "test needs a built-in with a real minimum_spend"
    low_month_amount = 1.0  # deliberately far below any real catalog minimum
    total = low_month_amount + 999999  # keep it balanced so this isn't ALSO a balance error
    li = LineItem(product_name="Search - SEM", monthly_budget=total, months=1, id="5",
                  monthly_allocations={"2026-09": low_month_amount, "2026-10": 999999})
    errors, warnings = m._validate_monthly_breakdown(
        [_tier([li], start_date="2026-09-01", end_date="2026-10-31")], _REQ, multi_tier=False
    )
    assert errors == []  # balanced, so no hard failure
    assert any(w["month_key"] == "2026-09" for w in warnings)


def test_multi_tier_labels_included_in_messages():
    li_a = LineItem(product_name="Search - SEM", monthly_budget=1000, months=1, id="6",
                     monthly_allocations={"2026-09": 500})
    errors, _ = m._validate_monthly_breakdown(
        [_tier([li_a], start_date="2026-09-01", end_date="2026-09-30", label="A")], _REQ, multi_tier=True
    )
    assert len(errors) == 1 and "Option A" in errors[0]


def test_unparseable_dates_skip_minimum_check_but_still_validate_balance():
    li = LineItem(product_name="Search - SEM", monthly_budget=1000, months=1, id="7",
                  monthly_allocations={"2026-09": 500})  # unbalanced regardless of dates
    errors, warnings = m._validate_monthly_breakdown(
        [_tier([li], start_date="not a date", end_date="also not a date")], _REQ, multi_tier=False
    )
    assert len(errors) == 1  # balance check doesn't need parsed dates
    assert warnings == []    # minimum check DOES need them — skipped, not guessed
