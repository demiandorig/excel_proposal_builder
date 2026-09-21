"""
End-to-end coverage for the Week/Month/Quarter toggle (Round E) through
the REAL export pipeline — generate_proposal() -> openpyxl cells — not
just the pure-Python period-generation functions (already covered by
test_monthly_allocation.py) or the validation gate (test_monthly_breakdown_validation.py).
Confirms the two layers actually wire together: a proposal generated
with time_unit="week" reads "Weekly"/"Weeks:" throughout its Excel
output, and a merged period shows as one combined column.

Same DB-mocking pattern as test_monthly_breakdown_validation.py — by_name()
(called while writing line items) goes through the rate-override cache on
every call, not just at import time.
"""
from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "postgresql://fake:fake@localhost/fake")

import pytest

from app.services.notion_parser import ProposalRequest
from app.services.proposal_generator import LineItem, generate_proposal


@pytest.fixture(autouse=True)
def _mock_catalog_db(monkeypatch):
    monkeypatch.setattr("app.catalog.load_rate_overrides", lambda: {})
    monkeypatch.setattr("app.catalog.load_custom_products", lambda: [])
    monkeypatch.setattr("app.catalog.load_deleted_builtin_names", lambda: set())
    # generate_proposal()'s full pipeline (unlike _validate_monthly_breakdown,
    # which test_monthly_breakdown_validation.py exercises) also reaches
    # market_config.get_market_address() while writing the meta block.
    monkeypatch.setattr("app.market_config.load_market_config", lambda: {})


def _req(**overrides) -> ProposalRequest:
    fields = dict(
        client_name="Acme Corp", request_type="New Business",
        start_date="2026-09-28", end_date="2026-11-30", total_months=2,
        geo="Los Angeles", agency_fee=None,
    )
    fields.update(overrides)
    return ProposalRequest(**fields)


def _sem_line(**overrides) -> LineItem:
    fields = dict(product_name="Search - SEM", monthly_budget=1200.0, months=2, id="li1")
    fields.update(overrides)
    return LineItem(**fields)


def test_weekly_export_relabels_months_cell_and_total_row(tmp_path):
    out = tmp_path / "weekly.xlsx"
    summary = generate_proposal(_req(), [_sem_line()], out, time_unit="week")
    assert summary["tabs_built"]

    import openpyxl
    wb = openpyxl.load_workbook(out)
    ws = wb["Proposal A"]
    assert ws["H10"].value == "Weeks:"
    # Grand total row's formula text — see excel_template._write_addons_grand_total_footer.
    grand_total_formulas = [c.value for row in ws.iter_rows() for c in row if isinstance(c.value, str) and "CAMPAIGN" in c.value]
    assert any("WEEK CAMPAIGN" in f for f in grand_total_formulas)
    total_row_labels = [c.value for row in ws.iter_rows() for c in row if c.value == "TOTAL DIGITAL WEEKLY"]
    assert total_row_labels


def test_quarterly_export_relabels_the_same_cells(tmp_path):
    out = tmp_path / "quarterly.xlsx"
    generate_proposal(_req(), [_sem_line()], out, time_unit="quarter")

    import openpyxl
    wb = openpyxl.load_workbook(out)
    ws = wb["Proposal A"]
    assert ws["H10"].value == "Quarters:"
    total_row_labels = [c.value for row in ws.iter_rows() for c in row if c.value == "TOTAL DIGITAL QUARTERLY"]
    assert total_row_labels


def test_default_month_export_is_byte_identical_in_wording_to_before_the_toggle(tmp_path):
    # No time_unit passed at all — every pre-existing caller/test that
    # never heard of this feature must see EXACTLY the old text.
    out = tmp_path / "default.xlsx"
    generate_proposal(_req(), [_sem_line()], out)

    import openpyxl
    wb = openpyxl.load_workbook(out)
    ws = wb["Proposal A"]
    assert ws["H10"].value == "Months:"
    total_row_labels = [c.value for row in ws.iter_rows() for c in row if c.value == "TOTAL DIGITAL MONTHLY"]
    assert total_row_labels


def test_weekly_monthly_breakdown_tab_shows_week_labels_and_merged_column(tmp_path):
    # Sep 28 start -> week 1 is a real, full 7-day week (weeks are
    # campaign-anchored, not calendar-anchored) — merge week 1 into week 2
    # anyway here purely to exercise the merge-column-collapsing path.
    li = _sem_line(
        monthly_budget=1200.0, months=2,
        monthly_allocations={
            "W1-2026-09-28+W2-2026-10-05": 400.0,
            "W3-2026-10-12": 200.0, "W4-2026-10-19": 200.0, "W5-2026-10-26": 200.0,
            "W6-2026-11-02": 200.0, "W7-2026-11-09": 200.0, "W8-2026-11-16": 200.0,
            "W9-2026-11-23": 200.0, "W10-2026-11-30": 200.0,
        },
    )
    out = tmp_path / "weekly_mb.xlsx"
    tiers = [{
        "label": "A", "line_items": [li], "avails_data": {},
        "period_merge_groups": [["W1-2026-09-28", "W2-2026-10-05"]],
    }]
    summary = generate_proposal(_req(), [li], out, tiers=tiers, time_unit="week")
    assert summary["tabs_built"]

    import openpyxl
    wb = openpyxl.load_workbook(out)
    assert "Weekly Breakdown" in wb.sheetnames
    ws = wb["Weekly Breakdown"]
    header_values = [c.value for row in ws.iter_rows() for c in row if isinstance(c.value, str) and c.value.startswith("WEEK")]
    # The merged bucket's label (from monthly_allocation._combined_label,
    # via app.js's _mbCombinedPeriodLabel mirror) reads "Week 1–Week 2",
    # not two separate "WEEK 1"/"WEEK 2" columns.
    assert any("WEEK 1" in h and "WEEK 2" in h for h in header_values)
    assert not any(h == "WEEK 2" for h in header_values)
