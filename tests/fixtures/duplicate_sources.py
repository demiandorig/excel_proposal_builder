"""Realistic SOURCE proposals for the "duplicate for a new client" tests: the `reopen_state` a saved draft or a generated proposal holds.

Every one is built through the REAL models (LineItemModel, AvailsEntry, TierModel, AddonItemModel) and `main._build_reopen_state`, the builder
every draft save and every generate uses, so each has exactly the shape the database stores (and the transform's output, which goes back
through the same builder, can be compared with it key for key). They all belong to the same old opportunity, so the "the old client's name
is nowhere in the copy" checks work across every billing period:

    client  Acme Motors    (website acmemotors.com)       agency  Initiative Media, 15%       AE  Ana Torres, Phoenix

A call returns a FRESH dict each time (a test may change it). Wave-2 tests import the six unit fixtures by these names:

    source_monthly()            three lines in option A (a hand-made split, an even split, none), Constanza and hand-typed avails, option B with
                                its own flight window and a combined period; billing period month, Sep 1 to Nov 30, 2026
    source_weekly()             billing period week: 13 weeks, one hand-made split and one even split
    source_quarterly()          billing period quarter: 2 quarters (Mar 1 to Aug 31, 2026), one hand-made split and one even split
    source_full_flight()        billing period full flight: lines hold the whole-flight total, one legacy line still says months 3
    source_legacy_single_tier() saved before options existed: `tiers` is None and the lines and avails live at the top level
    source_without_dates()      the request's dates are "TBD" and blank: nothing to re-key a split against

and these extras: source_monthly_merged() (a combined period in option A), source_four_tiers() (four options with their own windows).
"""
from __future__ import annotations

import json

from app import main

OLD_CLIENT = "Acme Motors"
OLD_AGENCY = "Initiative Media"
OLD_WEBSITE = "https://www.acmemotors.com"
# Words that identify the old opportunity in anything the page shows (lower case): a leak scan looks for these.
LEAK_WORDS = ("acme", "initiative", "acmemotors")

FB = "Facebook & Instagram Ads | Awareness"
CTV = "Entravision Plus - Roku Ads (Includes The Roku Channel and the popular Espacio Latino Hub)"
DISPLAY = "eDigital Display"


def _line(id_: str, name: str, budget: float, months: int, **extra) -> "main.LineItemModel":
    return main.LineItemModel(id=id_, product_name=name, monthly_budget=budget, months=months, **extra)


def _request(**over) -> dict:
    request = {
        "notion_id": "EVC-48213", "requested_by": "Ana Torres", "salesperson_market": "Phoenix", "salesperson_email": "ana@entravision.com",
        "ccs": "boss@entravision.com", "request_type": "Full Presentation (Deck + Avails + Proposal Included)",
        "client_name": OLD_CLIENT, "client_website": OLD_WEBSITE, "agency_name": OLD_AGENCY, "agency_fee": 0.15,
        "start_date": "2026-09-01", "end_date": "2026-11-30", "total_months": 3, "monthly_budget": 23000.0, "tiered_budget": True,
        "tier_1": "23000", "tier_2": "8000", "tier_3": "", "tier_4": "", "campaign_goal": "Awareness", "language": "Spanish", "other_languages": "",
        "geo": "Phoenix DMA", "demo": "Hispanic adults 25-54", "behavioral": "In-market auto buyers", "contextual": "",
        "products_selected_raw": "Meta, CTV, Display", "products_selected": [FB, CTV, DISPLAY],
        "specifics": {"facebook_ig": {"selected_page": "Acme Motors Phoenix"}, "sem": {"product_to_promote": "Acme trucks"}},
        "salesperson_comments": "Acme wants to beat Ford", "question_details": "", "warnings": ["Could not match product 'Foo'"],
        "renewal_client": OLD_CLIENT, "renewal_budget": "$20k",
    }
    request.update(over)
    return request


def _estimate_snapshot() -> dict:
    """What a Constanza Meta estimate leaves on a line (the page's forecast_snapshot)."""
    return {"v": 1, "method": "meta", "saved_at": "2026-08-20T12:00:00Z", "request": {"meta": {"geo": "Phoenix"}},
            "result": {"method": "meta", "month": {"mid": 1_200_000}},
            "line": {"product_name": FB, "method": "meta", "months": 3, "geo_text": "Phoenix", "target_text": "", "budget": 30000, "cpm": 12}}


def _build(*, request: dict, tiers=None, line_items=None, avails=None, time_unit="month", mode="even", wizard_step=6, addons=None) -> dict:
    """The stored reopen_state of a saved proposal that went as far as `wizard_step`, with the AI work a planner would have done by then."""
    return main._build_reopen_state(
        request_dict=request, line_items=line_items or [], tiers=tiers, avails_data=avails,
        strategy_brief={"headline": f"{OLD_CLIENT} should dominate Phoenix", "tactics": []}, roadblocks={"summary": f"{OLD_CLIENT} is auto: ok"},
        force_tabs={"net": True, "gross": True, "_v": 2},
        addons=addons if addons is not None else [main.AddonItemModel(product_name="Brand Safety", amount=1500.0, notes_override="Acme ask")],
        raw_notion_text=f"Client name: {OLD_CLIENT}\nAgency name: {OLD_AGENCY}",
        enrichment={"client_email_body": "Dear Acme team", "product_blurbs": []},
        time_unit=time_unit, monthly_distribution_mode=mode, campaign_name_override=f"{OLD_CLIENT} - Fall Push", wizard_step=wizard_step,
    )


def source_monthly() -> dict:
    a_lines = [
        _line("li_fb", FB, 10000, 3, monthly_allocations={"2026-09": 5000.0, "2026-10": 15000.0, "2026-11": 10000.0},
              notes_override="Acme launch weekend promo", line_modifier="Hispanic, Phoenix only", objective_override="Awareness"),
        _line("li_ctv", CTV, 8000, 3, monthly_allocations={"2026-09": 8000.0, "2026-10": 8000.0, "2026-11": 8000.0}, line_modifier="Spanish-language"),
        _line("li_disp", DISPLAY, 5000, 3, monthly_allocations=None, notes_override="Run-of-network",
              target_override="Adults 25-54 who shop at Acme dealerships"),
    ]
    b_lines = [
        _line("li_b1", FB, 6000, 4, monthly_allocations={"2026-09+2026-10": 9000.0, "2026-11": 6000.0, "2026-12": 9000.0}),
        _line("li_b2", DISPLAY, 2000, 4, monthly_allocations=None),
    ]
    avails_a = {
        "li_fb": main.AvailsEntry(max_imps=1_200_000, max_spend=14400, est_uniques=250_000, max_imps_estimated=True, basis="imps",
                                  geo_resolution={"label": "Phoenix"}, win_rate=0.15, forecast_snapshot=_estimate_snapshot()),
        "li_ctv": main.AvailsEntry(max_imps=400_000, max_spend=8000, basis="imps"),
    }
    tiers = [
        main.TierModel(label="A", name="Acme Core", geo=None, line_items=a_lines, avails_data=avails_a, period_merge_groups=None),
        main.TierModel(label="B", name="Stretch", geo="Phoenix, AZ", start_date="2026-09-01", end_date="2026-12-31", line_items=b_lines,
                       avails_data={}, period_merge_groups=[["2026-09", "2026-10"]]),
    ]
    return _build(request=_request(), tiers=tiers, line_items=a_lines, avails=avails_a)


def source_monthly_merged() -> dict:
    """A combined period in option A: September and October are one bucket ("2026-09+2026-10"), November stands alone."""
    lines = [
        _line("li_m1", FB, 10000, 3, monthly_allocations={"2026-09+2026-10": 20000.0, "2026-11": 10000.0}),      # hand-made: 2/3 and 1/3
        _line("li_m2", CTV, 8000, 3, monthly_allocations={"2026-09+2026-10": 12000.0, "2026-11": 12000.0}),      # the even split of 24,000 over two buckets
        _line("li_m3", DISPLAY, 5000, 3, monthly_allocations=None),
    ]
    tiers = [main.TierModel(label="A", name=None, geo=None, line_items=lines, avails_data={}, period_merge_groups=[["2026-09", "2026-10"]])]
    return _build(request=_request(tiered_budget=False), tiers=tiers, line_items=lines, avails={})


def source_weekly() -> dict:
    """Billing period week, Sep 1 to Nov 30, 2026: 13 whole weeks (W1-2026-09-01 ... W13-2026-11-24)."""
    keys = [f"W{i}-{d}" for i, d in enumerate(
        ["2026-09-01", "2026-09-08", "2026-09-15", "2026-09-22", "2026-09-29", "2026-10-06", "2026-10-13", "2026-10-20", "2026-10-27",
         "2026-11-03", "2026-11-10", "2026-11-17", "2026-11-24"], start=1)]
    front_loaded = dict(zip(keys, [3000.0] * 4 + [1500.0] * 8 + [2000.0]))                  # 26,000: heavy in September, a closing push
    even = dict(zip(keys, [1000.0] * 13))                                                 # 13,000: the even split
    lines = [
        _line("li_w1", FB, 2000, 13, monthly_allocations=front_loaded, notes_override="Acme weekend burst"),
        _line("li_w2", DISPLAY, 1000, 13, monthly_allocations=even),
    ]
    avails = {"li_w2": main.AvailsEntry(freeform=True, max_imps_text="about 50,000 clicks", est_uniques_text="Acme shoppers, estimated")}
    tiers = [main.TierModel(label="A", name="Weekly", geo=None, line_items=lines, avails_data=avails)]
    return _build(request=_request(tiered_budget=False, monthly_budget=3000.0), tiers=tiers, line_items=lines, avails=avails, time_unit="week")


def source_quarterly() -> dict:
    """Billing period quarter, Mar 1 to Aug 31, 2026: two quarters of three months."""
    q1, q2 = "2026-03+2026-04+2026-05", "2026-06+2026-07+2026-08"
    lines = [
        _line("li_q1", FB, 15000, 2, monthly_allocations={q1: 20000.0, q2: 10000.0}),     # hand-made
        _line("li_q2", CTV, 12000, 2, monthly_allocations={q1: 12000.0, q2: 12000.0}),     # even
    ]
    tiers = [main.TierModel(label="A", name="Quarterly", geo=None, line_items=lines, avails_data={})]
    return _build(request=_request(start_date="2026-03-01", end_date="2026-08-31", total_months=6, tiered_budget=False, monthly_budget=27000.0),
                  tiers=tiers, line_items=lines, avails={}, time_unit="quarter")


def source_full_flight() -> dict:
    """Billing period full flight: each line's budget is the WHOLE flight's amount, months 1. One line still says months 3, as a state
    saved by an older page can (the page and the server fold it into one period without changing its total)."""
    lines = [
        _line("li_f1", FB, 30000, 1, monthly_allocations=None),
        _line("li_f2", CTV, 24000, 1, monthly_allocations=None, line_modifier="Spanish-language"),
        _line("li_f3", DISPLAY, 5000, 3, monthly_allocations=None),                      # legacy: 5,000 x 3 months = 15,000 for the flight
    ]
    tiers = [main.TierModel(label="A", name=None, geo=None, line_items=lines, avails_data={})]
    return _build(request=_request(tiered_budget=False, monthly_budget=69000.0), tiers=tiers, line_items=lines, avails={}, time_unit="full_flight")


def source_legacy_single_tier() -> dict:
    """Saved before budget options existed: no `tiers`, the lines and their avails are at the top level."""
    lines = [
        _line("li_l1", FB, 9000, 3, monthly_allocations={"2026-09": 3000.0, "2026-10": 6000.0, "2026-11": 18000.0}, notes_override="Acme grand opening"),
        _line("li_l2", DISPLAY, 4000, 3, monthly_allocations=None),
    ]
    avails = {
        "li_l1": main.AvailsEntry(max_imps=900_000, max_spend=10800, max_imps_estimated=True, basis="imps", win_rate=0.1, forecast_snapshot=_estimate_snapshot()),
        "li_l2": main.AvailsEntry(max_imps=300_000, max_spend=3600, basis="imps"),
    }
    return _build(request=_request(tiered_budget=False, monthly_budget=13000.0), tiers=None, line_items=lines, avails=avails, wizard_step=4)


def source_without_dates() -> dict:
    """The planner never typed the flight ("TBD"): months are whatever the lines said, and no split can be keyed to a calendar."""
    lines = [
        _line("li_n1", FB, 7000, 3, monthly_allocations={"2026-09": 1000.0, "2026-10": 2000.0, "2026-11": 18000.0}),     # keys with no dates to explain them
        _line("li_n2", DISPLAY, 3000, 3, monthly_allocations=None),
    ]
    tiers = [main.TierModel(label="A", name="Acme Core", geo=None, line_items=lines, avails_data={})]
    return _build(request=_request(start_date="TBD", end_date="", total_months=None, tiered_budget=False, monthly_budget=10000.0),
                  tiers=tiers, line_items=lines, avails={}, wizard_step=3)


def source_four_tiers() -> dict:
    """Four options, three of them with their own flight window (D only a start): the per-option windows are cleared in a copy."""
    def lines_for(prefix: str, months: int) -> list:
        return [_line(f"{prefix}1", FB, 4000, months, monthly_allocations=None), _line(f"{prefix}2", DISPLAY, 1000, months, monthly_allocations=None)]
    a, b, c, d = lines_for("li_a", 3), lines_for("li_b", 4), lines_for("li_c", 2), lines_for("li_d", 3)
    tiers = [
        main.TierModel(label="A", name="Base", line_items=a, avails_data={}),
        main.TierModel(label="B", name="Longer", start_date="2026-09-01", end_date="2026-12-31", line_items=b, avails_data={}),
        main.TierModel(label="C", name="Short", start_date="2026-10-01", end_date="2026-11-30", line_items=c, avails_data={}),
        main.TierModel(label="D", name="Late start", start_date="2026-09-15", line_items=d, avails_data={}),
    ]
    return _build(request=_request(tiered_budget=True, tier_1="15000", tier_2="20000", tier_3="10000", tier_4="15000"), tiers=tiers, line_items=a, avails={})


def all_sources() -> dict:
    """name -> a fresh state, for every billing period and shape (the unit fixtures plus the extras)."""
    return {
        "monthly": source_monthly(), "monthly_merged": source_monthly_merged(), "weekly": source_weekly(), "quarterly": source_quarterly(),
        "full_flight": source_full_flight(), "legacy_single_tier": source_legacy_single_tier(), "without_dates": source_without_dates(),
        "four_tiers": source_four_tiers(),
    }


def jsonable(state: dict) -> dict:
    """What the database would hand back for `state` (a JSON round trip), for a test that wants stored-looking data."""
    return json.loads(json.dumps(state))
