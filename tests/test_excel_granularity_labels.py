"""
Covers the Week/Month/Quarter granularity toggle's Excel-facing labels:
a multi-tier proposal's standalone Breakdown tab used to be titled e.g.
"Option A (Monthly)" even for a Weekly/Quarterly plan (proposal_generator.py
hardcoded the literal "(Monthly)" instead of using the already-computed
adjective) — plus the new billing-cadence note (a real client/ops
communication: breakdown granularity != invoicing cadence, which is
always monthly) added to both places a Breakdown appears.

Deliberately does NOT go through generate_proposal()/catalog.by_name() —
those hit a real Postgres connection not reachable from a plain local dev
machine (see test_monthly_breakdown_layout.py's own comment on this).
Everything tested here is pure openpyxl/string logic once given a
worksheet and a product list.
"""
from __future__ import annotations

from openpyxl import Workbook

from app.catalog import Product
from app.services.proposal_generator import LineItem, _safe_sheet_name
from app import excel_template as et


def _product(name: str, family: str = "Display") -> Product:
    return Product(
        family=family, name=name, short_label=name, proposal_description="",
        sizes="", buying_model="CPM", base_rate=10.0, estimated_impressions=False,
        discloses_impressions=True, minimum_spend=0.0, minimum_flight_days=(0, 0),
        sla_data_days=None, sla_creative_days=None, sla_activate_days=None, sla_total_days=None,
        media_allocation_pct=1.0, margin_upper=0.5, margin_lower=0.5,
        tech_platform="", wide_orbit_code="",
    )


def _months(*keys: str) -> list[dict]:
    return [{"key": k, "label": k} for k in keys]


# --- The proposal_generator.py:429 bug fix (multi-tier tab naming) --------

def test_multi_tier_sheet_title_uses_the_real_granularity_adjective():
    # Exactly the line that was fixed: previously always "(Monthly)"
    # regardless of time_unit; now built from the same adjective the
    # sibling mb_sheet_name/tab-content already used correctly.
    for time_unit, expected_adjective in (("week", "Weekly"), ("month", "Monthly"), ("quarter", "Quarterly")):
        adjective = et.unit_labels(time_unit)["adjective"]
        assert adjective == expected_adjective
        title = _safe_sheet_name("Option A", f"({adjective})", set())
        assert title == f"Option A ({expected_adjective})"
        if time_unit != "month":
            assert "Monthly" not in title


# --- write_monthly_breakdown_header (in-proposal block) -------------------

def test_inline_header_banner_and_comment_are_granularity_aware():
    for time_unit, expected_upper in (("week", "WEEKLY"), ("month", "MONTHLY"), ("quarter", "QUARTERLY")):
        wb = Workbook()
        ws = wb.active
        et.write_monthly_breakdown_header(ws, row=19, months=_months("p1", "p2"), start_col=20, time_unit=time_unit)
        banner = ws.cell(row=18, column=20)
        assert banner.value == f"{expected_upper} BREAKDOWN"
        # A comment, not an extra row — this block's row positions are
        # load-bearing elsewhere (aligned with the sheet's own line-item
        # and TOTAL DIGITAL {UNIT} rows), so the billing note can't be a
        # visible row here without risking that alignment.
        assert banner.comment is not None
        assert "30-day" in banner.comment.text
        assert "monthly" in banner.comment.text.lower()


# --- build_monthly_breakdown_tab (standalone tab) --------------------------

def test_standalone_tab_title_and_note_are_granularity_aware():
    products = [_product("Programmatic Display")]
    for time_unit, expected_adjective in (("week", "Weekly"), ("month", "Monthly"), ("quarter", "Quarterly")):
        line_items = [LineItem(product_name="Programmatic Display", monthly_budget=3000, months=3)]
        wb = Workbook()
        sheet_name = f"Option A ({expected_adjective})" if time_unit != "month" else "Weekly Breakdown"
        ws = et.build_monthly_breakdown_tab(wb, products, line_items, _months("p1", "p2", "p3"),
                                            sheet_name=sheet_name, time_unit=time_unit)
        assert expected_adjective in ws["B2"].value
        note = ws["B3"].value
        assert "monthly (30-day) cycle" in note
        assert "breakdown granularity" in note


def test_standalone_tab_note_names_the_right_granularity_specifics():
    products = [_product("Programmatic Display")]
    line_items = [LineItem(product_name="Programmatic Display", monthly_budget=3000, months=3)]

    wb = Workbook()
    ws = et.build_monthly_breakdown_tab(wb, products, line_items, _months("p1"), time_unit="week")
    assert "paced by week" in ws["B3"].value
    assert "each month's invoice totals" in ws["B3"].value
    assert "booked for the quarter" not in ws["B3"].value

    wb2 = Workbook()
    ws2 = et.build_monthly_breakdown_tab(wb2, products, line_items, _months("p1"), time_unit="quarter")
    assert "booked for the quarter" in ws2["B3"].value
    assert "paced by week" not in ws2["B3"].value

    wb3 = Workbook()
    ws3 = et.build_monthly_breakdown_tab(wb3, products, line_items, _months("p1"), time_unit="month")
    # Monthly's breakdown already coincides with its billing cycle — no
    # extra clause needed, just the base sentence.
    assert "paced by week" not in ws3["B3"].value
    assert "booked for the quarter" not in ws3["B3"].value


def test_billing_cadence_note_is_identical_wording_in_both_locations():
    # Both call sites read from the SAME helper — this pins that they
    # can't drift apart on what the note actually says.
    products = [_product("Programmatic Display")]
    line_items = [LineItem(product_name="Programmatic Display", monthly_budget=3000, months=3)]
    wb = Workbook()
    ws = et.build_monthly_breakdown_tab(wb, products, line_items, _months("p1"), time_unit="quarter")

    wb2 = Workbook()
    ws2 = wb2.active
    et.write_monthly_breakdown_header(ws2, row=19, months=_months("p1"), start_col=20, time_unit="quarter")

    assert ws["B3"].value == ws2.cell(row=18, column=20).comment.text
