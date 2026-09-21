"""
Covers a real gap: a renewal proposal's REAL flight dates only ever
arrived via the "Campaign dates:" field (parsed into
request.renewal_campaign_dates, one unsplit string) — the normal
"Start date:"/"End date:" fields are blank on a renewal paste, so
request.start_date/end_date stayed empty all the way downstream (avails,
the Week/Month/Quarter breakdown, the Excel export's own date cells),
none of which has a renewal-specific fallback of its own. Fixed by
promoting renewal_campaign_dates into start_date/end_date whenever the
normal fields are blank — same "renewal field promotes into the main
field" pattern parse_notion() already uses for renewal_client -> client_name.
"""
from app.catalog import CATALOG
from app.services.notion_parser import parse_notion

_CATALOG_NAMES = [p.name for p in CATALOG]

_RENEWAL_PASTE = """Requested by: Camilo Arias
Salesperson market: Los Angeles (Tier 1)
Salesperson email: camilo.arias@entravision.com
Request type: Renewal Proposal Request
────────────
Client name:
Client website:
Agency name:
Agency Fee:
────────────
Start date:
End date:
Total months:
Monthly budget:
Tiered budget?:
────────────
Chosen campaign goal:
────────────
Language of campaign:
Geo:
Demo:
Behavioral:
Contextual:
────────────
Products selected:
────────────
> AE or AM Requesting: Account Executive
> Type of changes request: Renewal Proposal With Minor Changes Request
> Client: Cambridge Savings Bank
> Changes description: The client will be renewing their September 2026 campaign through the end of October.
> Campaign dates: 2026-10-01 - 2026-10-31
> Renewal budget: 5300
> Due date: 2026-09-20
"""

_NON_RENEWAL_PASTE = """Requested by: Jane AE
Salesperson email: jane@entravision.com
Request type: New Business Request
────────────
Client name: Acme Corp
Start date: 2026-09-01
End date: 2026-11-30
Total months: 3
Monthly budget: 5000
Tiered budget?:
────────────
Chosen campaign goal: Awareness
────────────
Geo: Los Angeles
────────────
Products selected:
"""


def test_renewal_campaign_dates_promoted_into_start_and_end_date():
    req = parse_notion(_RENEWAL_PASTE, _CATALOG_NAMES)
    assert req.renewal_campaign_dates == "2026-10-01 - 2026-10-31"
    assert req.start_date == "2026-10-01"
    assert req.end_date == "2026-10-31"


def test_client_name_still_promotes_from_renewal_client_alongside_dates():
    req = parse_notion(_RENEWAL_PASTE, _CATALOG_NAMES)
    assert req.client_name == "Cambridge Savings Bank"


def test_non_renewal_paste_with_real_start_end_dates_is_unaffected():
    req = parse_notion(_NON_RENEWAL_PASTE, _CATALOG_NAMES)
    assert req.start_date == "2026-09-01"
    assert req.end_date == "2026-11-30"
    assert req.renewal_campaign_dates == ""


def test_does_not_overwrite_real_start_end_dates_if_somehow_both_are_present():
    # Defensive case: if a paste somehow has BOTH real Start/End date
    # lines AND a Campaign dates line, the explicit Start/End fields win —
    # promotion only fires when they're genuinely blank.
    paste = _NON_RENEWAL_PASTE.replace(
        "Products selected:", "> Campaign dates: 2020-01-01 - 2020-01-02\nProducts selected:"
    )
    req = parse_notion(paste, _CATALOG_NAMES)
    assert req.start_date == "2026-09-01"
    assert req.end_date == "2026-11-30"


def test_malformed_campaign_dates_string_does_not_crash_or_populate_garbage():
    paste = _RENEWAL_PASTE.replace(
        "> Campaign dates: 2026-10-01 - 2026-10-31", "> Campaign dates: sometime in October"
    )
    req = parse_notion(paste, _CATALOG_NAMES)
    assert req.renewal_campaign_dates == "sometime in October"
    assert req.start_date == ""
    assert req.end_date == ""
