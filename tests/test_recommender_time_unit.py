"""
Covers a real gap found while building the Week/Month/Quarter toggle
(Round E): recommend_line_items() (Step 04's "Suggest Mix") seeds every
line's budget against catalog minimum_spend, which is always a flat
MONTHLY figure — without scaling, Suggest Mix run in Weekly mode would
floor every line at its full monthly minimum (a ~4.33x overshoot against
whatever weekly total the planner actually typed).
"""
import os

os.environ.setdefault("DATABASE_URL", "postgresql://fake:fake@localhost/fake")

import pytest

from app.catalog import by_name
from app.services.notion_parser import ProposalRequest
from app.services.recommender import recommend_line_items


@pytest.fixture(autouse=True)
def _mock_catalog_db(monkeypatch):
    monkeypatch.setattr("app.catalog.load_rate_overrides", lambda: {})
    monkeypatch.setattr("app.catalog.load_custom_products", lambda: [])
    monkeypatch.setattr("app.catalog.load_deleted_builtin_names", lambda: set())


def _req(**overrides) -> ProposalRequest:
    fields = dict(client_name="Acme", request_type="New Business", campaign_goal="Traffic", products_selected=[])
    fields.update(overrides)
    return ProposalRequest(**fields)


def test_month_granularity_matches_pre_toggle_behavior_exactly():
    # No time_unit passed — every pre-existing caller.
    req = _req()
    items = recommend_line_items(req, 5000.0, strategy_brief=None)
    items_explicit_month = recommend_line_items(req, 5000.0, strategy_brief=None, time_unit="month")
    assert [(li.product_name, li.monthly_budget) for li in items] == \
           [(li.product_name, li.monthly_budget) for li in items_explicit_month]


def test_weekly_recommendation_floors_lines_at_the_scaled_minimum_not_the_raw_one():
    req = _req()
    # A budget clearly too small for any product's RAW monthly minimum,
    # but plausible for weekly minimums (~1/4.33 of monthly) — if scaling
    # isn't applied, every line gets floored at its monthly minimum and
    # the result wildly overshoots this budget.
    weekly_budget = 300.0
    items = recommend_line_items(req, weekly_budget, strategy_brief=None, time_unit="week")
    assert items
    for li in items:
        p = by_name(li.product_name)
        if p and p.minimum_spend:
            weekly_min = p.minimum_spend * (12 / 52)
            assert li.monthly_budget >= weekly_min - 0.01


def test_weekly_recommendation_total_stays_near_the_typed_weekly_budget():
    req = _req()
    weekly_budget = 500.0
    items = recommend_line_items(req, weekly_budget, strategy_brief=None, time_unit="week")
    total = sum(li.monthly_budget for li in items)
    # Allow some slack (rounding to $50, minimum floors) but this must NOT
    # be anywhere near a monthly-scale total (~4.33x too high) for the
    # same nominal budget number.
    assert total <= weekly_budget * 1.5


def test_quarterly_recommendation_scales_minimum_up():
    req = _req()
    product = by_name("Search - SEM")
    assert product is not None and (product.minimum_spend or 0) > 0
    quarterly_budget = (product.minimum_spend or 0) * 3 + 500
    items = recommend_line_items(req, quarterly_budget, strategy_brief=None, time_unit="quarter")
    assert items
    sem_line = next((li for li in items if li.product_name == "Search - SEM"), None)
    if sem_line:
        assert sem_line.monthly_budget >= product.minimum_spend * 3 - 0.01


def test_brief_driven_recommendation_also_scales_minimum():
    req = _req()
    brief = {"recommended_tactics": [{"product_family": "Search", "suggested_budget_pct": 100}]}
    items = recommend_line_items(req, 300.0, strategy_brief=brief, time_unit="week")
    for li in items:
        p = by_name(li.product_name)
        if p and p.minimum_spend:
            assert li.monthly_budget >= p.minimum_spend * (12 / 52) - 0.01
