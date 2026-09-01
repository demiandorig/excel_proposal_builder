"""End-to-end: parse → curate → generate → recalc."""
import sys
sys.path.insert(0, "/home/claude/webapp")

from pathlib import Path
from app.catalog import CATALOG
from app.services.notion_parser import parse_notion
from app.services.proposal_generator import LineItem, generate_proposal


# Fronteras Del Norte renewal — total $7,500/mo for 1 month per the notion paste
EX2 = """Requested by: Camilo Arias
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
(Other languages: )
Geo:
Demo: Hispanic A21+
Behavioral:
Contextual:
────────────
Products selected:
────────────
> AE or AM Requesting: Account Executive / DSM / SVP
> Type of changes request: Renewal Proposal With Minor Changes Request
> Client: Fronteras Del Norte
> Changes description: Returning client, monthly budget $7,500. Grow Meta + Google SEM, add retargeting, possibly add Email marketing.
> Campaign dates: 2026-06-01 - 2026-06-30
> Renewal budget: 7500
> Due date: 2026-05-15
"""

catalog_names = [p.name for p in CATALOG]
req = parse_notion(EX2, catalog_names)
print("PARSED:")
print(f"  Client: {req.client_name}")
print(f"  Request type: {req.request_type}")
print(f"  Renewal budget: {req.renewal_budget}")
print(f"  Renewal dates: {req.renewal_campaign_dates}")
print(f"  Salesperson: {req.salesperson_email}")
print()

# Simulate planner curation: $7,500/mo split per the salesperson's note
line_items = [
    LineItem(
        product_name="Facebook & Instagram Ads | Awareness",
        monthly_budget=3000.0,
        months=1,
        target_override="Hispanic A21+ — Los Angeles",
        notes_override="Add retargeting layer per renewal request",
    ),
    LineItem(
        product_name="Search - AdWords - SEM",
        monthly_budget=2500.0,
        months=1,
        target_override="Hispanic A21+ — Los Angeles",
    ),
    LineItem(
        product_name="Geo targeting only + Hispanic",
        monthly_budget=1500.0,
        months=1,
        target_override="Hispanic A21+ — Northern California",
    ),
    LineItem(
        product_name="Number of emails: 0 - 15,000",
        monthly_budget=500.0,
        months=1,
        notes_override="Client has list to upload — coordinate intake",
    ),
]
print(f"PLANNER CURATED {len(line_items)} line items, total monthly = ${sum(l.monthly_budget for l in line_items):,.2f}")
print()

out = Path("/tmp/test_fronteras_proposal.xlsx")
summary = generate_proposal(req, line_items, out)
print("GENERATED:")
for k, v in summary.items():
    print(f"  {k}: {v}")
