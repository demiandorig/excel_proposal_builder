"""Smoke-test the Notion parser with both real examples."""
import sys
sys.path.insert(0, "/home/claude/webapp")

from app.catalog import CATALOG
from app.services.notion_parser import parse_notion, classify_output_tabs

catalog_names = [p.name for p in CATALOG]

# ---------------------------------------------------------------------------
# Example 1: Bill Luke Auto Group — Avails Only
# ---------------------------------------------------------------------------
EX1 = """Requested by: Carlos Renteria
Salesperson market: Phoenix (Tier 1) (R ana.gomez@entravision.com)
Salesperson email: carlos.renteria@entravision.com
CCs:
Request type: Avails / Estimates Only (I don't need a proposal right now)
────────────
>>> Avails/Presentation/Proposal Page details <<<
Client name: Bill Luke Auto Group
Client website: https://billluke.com/servicedepartment
Agency name:
Agency Fee:
────────────
Start date:
End date:
Total months: 3
Monthly budget:
Tiered budget?: false
Tier #1: | Tier #2:
Tier #3: | Tier #4:
────────────
Chosen campaign goal: Traffic / Drive To Website and Clicks
────────────
Target details:
Language of campaign: English AND Spanish (Separate) (You'll get two proposals, one for each language: one targets English Speakers and the other one targets Spanish Speakers)
(Other languages: )
Geo: 15 Mile Radius from 2425 W Camelback Rd, Phoenix AZ 85015
Demo: A21+
Behavioral: Owners of Chrysler, Jeep, Dodge, and Ram vehicles
Contextual: Entertainment & Sports
────────────
Products selected: Connected TV (OTT) - Entravision Plus
Facebook/IG specifics:
Goal:
Strategy Type:
Paid?:
Creative Type:
Page type:
Selected page:
SEM specifics:
Wants Google Ads KW Forecast? false
English or Spanish Keywords:
Budget:
Product client wishes to promote:
Is it a PMax campaign?: false
PMax conversion goal:
CTV/OTT specifics:
Device type: Connected TV (Large Screens) AND OTT (Mobile Devices)
Inventory language: Spanish & English Content targeting Hispanics. (Recommended: Spanish or English creative)
Netflix targeting: -- --
DOOH specifics:
(Budget, Industry, Screen Types):
Geo-fencing specifics:
Radius/Fences:
Spotify Ads specifics:
Industry:
AudioEngage specifics:
Creative language:
────────────
Additional comments from the salesperson:
Client wants to promote their service department to brand owners in a 15 mile radius around their main store. They're interested in 2 lines: Spanish Language only and General Market (English) ads so they can chose one or the other, or both simultaneously. Looking for Max avails, recommended budget, and they are CPM sensitive.
Attachment:
"""

# ---------------------------------------------------------------------------
# Example 2: Fronteras Del Norte — Renewal with markdown
# ---------------------------------------------------------------------------
EX2 = """Requested by: Camilo  Arias
Salesperson market: Los Angeles (Tier 1) (R amartindelcampo@entravision.com)
Salesperson email: camilo.arias@entravision.com
CCs:
Request type: Renewal Proposal Request
────────────
## **>>> Avails/Presentation/Proposal Page details <<<**
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
Tier #1:  | Tier #2:
Tier #3:  | Tier #4:
────────────
### **Chosen campaign goal:**
────────────
**Target details:**
Language of campaign:
(Other languages: )
Geo:
Demo:
Behavioral:
Contextual:
────────────
**Products selected:**
***Facebook/IG specifics:***
> Goal:
> Strategy Type:
> Paid?:
> Creative Type:
> Page type:
> Selected page:
────────────
## **>>> Renewal Request <<<**
> AE or AM Requesting: Account Executive / DSM / SVP
> Type of changes request: Renewal Proposal With Minor Changes Request
> Client: Fronteras Del Norte
> Changes description: Fronteras Del Norte is returning and has increased their monthly budget from $5k to $7.5k. The $5k is currently split evenly with $2.5k focused on Los Angeles and $2.5k on Northern California (Pls see attached proposals.) I believe the new budget should be $5k Los Angeles and keep the $2.5k in Northern California.
> Original Proposal link:
> Campaign dates: 2026-06-01 - 2026-06-30
> Renewal budget: 7500 | $5k for Los Angeles and $2.5k for Northern California
> Former AE/AM:
> Additional comments: Lets meet if needed!
> Client's budget change: The total budget increased slightly.
> Due date: 2026-05-15
"""

print("=" * 80)
print("EXAMPLE 1: Bill Luke Auto Group — Avails/Estimates Only")
print("=" * 80)
r1 = parse_notion(EX1, catalog_names)
print(f"requested_by:        {r1.requested_by!r}")
print(f"salesperson_email:   {r1.salesperson_email!r}")
print(f"request_type:        {r1.request_type!r}")
print(f"client_name:         {r1.client_name!r}")
print(f"client_website:      {r1.client_website!r}")
print(f"agency_fee:          {r1.agency_fee!r}")
print(f"total_months:        {r1.total_months!r}")
print(f"monthly_budget:      {r1.monthly_budget!r}")
print(f"campaign_goal:       {r1.campaign_goal!r}")
print(f"geo:                 {r1.geo!r}")
print(f"demo:                {r1.demo!r}")
print(f"behavioral:          {r1.behavioral!r}")
print(f"products_raw:        {r1.products_selected_raw!r}")
print(f"products_matched:    {r1.products_selected!r}")
print(f"ctv specifics:       {r1.specifics.ctv_ott}")
print(f"comments[:80]:       {r1.salesperson_comments[:80]!r}")
print(f"warnings:            {r1.warnings}")
print()
tabs1 = classify_output_tabs(r1.request_type, r1.products_selected, r1.agency_fee is not None)
print(f"OUTPUT TABS:         {tabs1}")

print()
print("=" * 80)
print("EXAMPLE 2: Fronteras Del Norte — Renewal Proposal Request")
print("=" * 80)
r2 = parse_notion(EX2, catalog_names)
print(f"requested_by:                {r2.requested_by!r}")
print(f"salesperson_email:           {r2.salesperson_email!r}")
print(f"request_type:                {r2.request_type!r}")
print(f"client_name (promoted):      {r2.client_name!r}")
print(f"renewal_ae_requesting:       {r2.renewal_ae_requesting!r}")
print(f"renewal_change_type:         {r2.renewal_change_type!r}")
print(f"renewal_client:              {r2.renewal_client!r}")
print(f"renewal_changes_desc[:100]:  {r2.renewal_changes_description[:100]!r}")
print(f"renewal_campaign_dates:      {r2.renewal_campaign_dates!r}")
print(f"renewal_budget:              {r2.renewal_budget!r}")
print(f"renewal_due_date:            {r2.renewal_due_date!r}")
print(f"products_matched:            {r2.products_selected!r}")
print(f"warnings:                    {r2.warnings}")
print()
tabs2 = classify_output_tabs(r2.request_type, r2.products_selected, r2.agency_fee is not None)
print(f"OUTPUT TABS:                 {tabs2}")
