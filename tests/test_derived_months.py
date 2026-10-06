"""Tests for the "derived months" fix — a proposal's flight dates are now
the one true source for total_months (notion_parser.py) and each tier's own
Excel grand-total multiplier (proposal_generator.py), instead of a
planner-typed number that could silently disagree with the real calendar
range. See monthly_allocation.py's module docstring for the full picture."""
import os

os.environ.setdefault("DATABASE_URL", "postgresql://fake:fake@localhost/fake")

import pytest

from app.services.notion_parser import parse_notion, ProposalRequest
from app.services import monthly_allocation as mo
from app.services.proposal_generator import LineItem, generate_proposal


@pytest.fixture(autouse=True)
def _mock_catalog_db(monkeypatch):
    # Same DB-mocking pattern as test_time_unit_export.py — by_name() and
    # the meta block's market lookup both otherwise need a real DATABASE_URL.
    monkeypatch.setattr("app.catalog.load_rate_overrides", lambda: {})
    monkeypatch.setattr("app.catalog.load_custom_products", lambda: [])
    monkeypatch.setattr("app.catalog.load_deleted_builtin_names", lambda: set())
    monkeypatch.setattr("app.market_config.load_market_config", lambda: {})


def _text(start_date="", end_date="", total_months=""):
    return f"""Requested by: Test User
Salesperson market: Test
Salesperson email: test@entravision.com
CCs:
Request type: New Business Request
────────────
Client name: Acme Co
Client website:
Agency name:
Agency Fee:
────────────
Start date: {start_date}
End date: {end_date}
Total months: {total_months}
Monthly budget:
Tiered budget?: false
Tier #1: | Tier #2:
Tier #3: | Tier #4:
────────────
Chosen campaign goal: Traffic / Drive To Website and Clicks
────────────
Products selected: Search - SEM
"""


def test_total_months_derived_from_real_dates_overrides_mismatched_label():
    # Sep 1 - Nov 30 spans exactly 3 real calendar months; the pasted
    # "Total months: 5" is deliberately wrong — the real dates must win.
    req = parse_notion(_text(start_date="2026-09-01", end_date="2026-11-30", total_months="5"), [])
    assert req.total_months == 3


def test_total_months_falls_back_to_the_parsed_label_when_dates_are_blank():
    # No dates to derive from yet — the old free-typed behavior is the
    # only option, exactly as before this fix.
    req = parse_notion(_text(start_date="", end_date="", total_months="4"), [])
    assert req.total_months == 4


def test_tier_months_derivation_matches_real_periods_between_helper():
    # Same calendar math proposal_generator.py's per-tier total_months fix
    # relies on (tier_total_months = len(tier_months)) — this just pins
    # down that months_between() itself counts a partial trailing month as
    # a full period, matching periods_between()'s documented convention.
    start = mo.parse_flexible_date("2026-09-28")
    end = mo.parse_flexible_date("2026-11-05")
    months = mo.months_between(start, end)
    assert [m["key"] for m in months] == ["2026-09", "2026-10", "2026-11"]


def test_line_item_total_budget_uses_whatever_months_it_was_given():
    # Sanity check that LineItem.total_budget() itself is untouched by this
    # fix — it's still a pure multiplier; correctness now comes entirely
    # from what the frontend/parser feed into `months`, not from any new
    # logic in the dataclass itself.
    li = LineItem(product_name="Search - SEM", monthly_budget=1000, months=3)
    assert li.total_budget() == 3000


def test_each_tiers_excel_grand_total_uses_its_own_derived_months(tmp_path):
    # Two options with genuinely different date overrides — before this
    # fix, both sheets' I10 (and every live formula reading it) would have
    # used the SAME single request-level total_months regardless. Now each
    # tier's own real calendar span drives its own sheet.
    req = ProposalRequest(
        client_name="Acme Corp", request_type="New Business",
        start_date="2026-09-01", end_date="2026-09-30", total_months=99,  # deliberately wrong for both tiers
        agency_fee=None,
    )
    li_a = LineItem(product_name="Search - SEM", monthly_budget=1000.0, months=3, id="a1")
    li_b = LineItem(product_name="Search - SEM", monthly_budget=1000.0, months=6, id="b1")
    tiers = [
        {"label": "A", "line_items": [li_a], "avails_data": {},
         "start_date": "2026-09-01", "end_date": "2026-11-30"},   # 3 real months
        {"label": "B", "line_items": [li_b], "avails_data": {},
         "start_date": "2026-09-01", "end_date": "2027-02-28"},   # 6 real months
    ]
    out = tmp_path / "multi_tier.xlsx"
    summary = generate_proposal(req, [], out, tiers=tiers)
    assert summary["tabs_built"]

    import openpyxl
    wb = openpyxl.load_workbook(out)
    ws_a = next(ws for ws in wb.worksheets if "Option A" in ws.title)
    ws_b = next(ws for ws in wb.worksheets if "Option B" in ws.title)
    assert ws_a["I10"].value == 3
    assert ws_b["I10"].value == 6


def test_tier_falls_back_to_campaign_total_months_when_its_own_dates_dont_parse(tmp_path):
    req = ProposalRequest(
        client_name="Acme Corp", request_type="New Business",
        start_date="2026-09-01", end_date="2026-11-30", total_months=3,
        agency_fee=None,
    )
    li = LineItem(product_name="Search - SEM", monthly_budget=1000.0, months=3, id="a1")
    tiers = [{"label": "A", "line_items": [li], "avails_data": {}, "start_date": "not a date", "end_date": "also not a date"}]
    out = tmp_path / "unparseable.xlsx"
    generate_proposal(req, [], out, tiers=tiers)

    import openpyxl
    wb = openpyxl.load_workbook(out)
    assert wb["Proposal A"]["I10"].value == 3


def test_a_legacy_proposal_keeps_its_saved_line_months_in_the_grand_total(tmp_path):
    # A proposal reopened from before months were derived from the dates: every line says 3 months while the
    # flight spans 4 calendar months. Step 04 leaves those lines as "saved" and total_net uses them, so the
    # workbook's grand-total multiplier must agree with that — not silently jump to the calendar count.
    req = ProposalRequest(client_name="Acme Corp", request_type="New Business",
                          start_date="2026-10-01", end_date="2027-01-31", total_months=3, agency_fee=None)
    li = LineItem(product_name="Search - SEM", monthly_budget=10000.0, months=3, id="a1")
    tiers = [{"label": "A", "line_items": [li], "avails_data": {},
              "start_date": "2026-10-01", "end_date": "2027-01-31"}]
    out = tmp_path / "legacy.xlsx"
    summary = generate_proposal(req, [], out, tiers=tiers)

    import openpyxl
    assert openpyxl.load_workbook(out)["Proposal A"]["I10"].value == 3
    assert summary["total_net"] == 30000.0


def test_lines_that_disagree_on_months_fall_back_to_the_calendar_span(tmp_path):
    req = ProposalRequest(client_name="Acme Corp", request_type="New Business",
                          start_date="2026-09-01", end_date="2026-11-30", total_months=3, agency_fee=None)
    lis = [LineItem(product_name="Search - SEM", monthly_budget=1000.0, months=3, id="a1"),
           LineItem(product_name="eDigital Network Display - Standard IAB", monthly_budget=1000.0, months=2, id="a2")]
    tiers = [{"label": "A", "line_items": lis, "avails_data": {},
              "start_date": "2026-09-01", "end_date": "2026-11-30"}]
    out = tmp_path / "mixed.xlsx"
    generate_proposal(req, [], out, tiers=tiers)

    import openpyxl
    assert openpyxl.load_workbook(out)["Proposal A"]["I10"].value == 3
