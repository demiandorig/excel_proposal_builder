"""
Covers a real, severe bug: agency_fee is documented/used everywhere as a
FRACTION (0.0-0.99, e.g. 0.15 for 15%) — proposal_generator.py and
excel_template.py both compute gross = net / (1 - agency_fee). A planner
naturally types "15" for "15%" into a field just labeled "Agency Fee" (no
visible "%"), and nothing stopped that raw whole number from reaching
those formulas: 1 - 15 = -14, so every "Gross" figure (the Step 04 preview
AND the actual exported Excel file's own Gross sheet/cells) came out
negative. notion_parser.py's own _parse_agency_fee() already normalized
this correctly for a FRESH Notion paste — the gap was every OTHER way
agency_fee reaches a ProposalRequest: the 6 `ProposalRequest(**raw)`
call sites in main.py, fed directly from client JSON with no
revalidation. Fixed with a __post_init__ on the dataclass itself — the
one construction-time choke point every one of those 6 sites (and any
future one) passes through automatically.
"""
from app.services.notion_parser import ProposalRequest, parse_notion
from app.catalog import CATALOG


def test_whole_number_percent_is_normalized_to_a_fraction():
    assert ProposalRequest(agency_fee=15).agency_fee == 0.15


def test_already_correct_fraction_is_left_unchanged():
    assert ProposalRequest(agency_fee=0.15).agency_fee == 0.15


def test_nonsensical_value_that_stays_out_of_range_after_dividing_becomes_none():
    # 150 / 100 = 1.5, still not a valid fraction — better to drop it
    # than silently use a wrong number.
    assert ProposalRequest(agency_fee=150).agency_fee is None


def test_zero_is_a_valid_no_fee_value_not_normalized_away():
    assert ProposalRequest(agency_fee=0).agency_fee == 0


def test_none_stays_none():
    assert ProposalRequest(agency_fee=None).agency_fee is None


def test_boundary_99_percent():
    assert ProposalRequest(agency_fee=99).agency_fee == 0.99


def test_every_other_field_is_unaffected_by_post_init():
    req = ProposalRequest(client_name="Acme", agency_fee=15, total_months=3)
    assert req.client_name == "Acme"
    assert req.total_months == 3
    assert req.agency_fee == 0.15


def test_fresh_notion_paste_path_still_works_via_parse_agency_fee_unaffected_by_post_init():
    # parse_notion() constructs ProposalRequest() with NO args (so
    # __post_init__ runs against agency_fee=None, a no-op), THEN assigns
    # agency_fee via the already-correct _parse_agency_fee() afterward —
    # confirms the two mechanisms don't double-process or conflict.
    text = "Agency Fee: 15%\nClient name: Acme\n"
    req = parse_notion(text, [p.name for p in CATALOG])
    assert req.agency_fee == 0.15
