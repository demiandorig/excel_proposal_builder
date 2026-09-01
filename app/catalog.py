"""
Canonical AdFlo product catalog.

Rebuilt 2026-08 from `LOCAL DIGITAL RATE CARD 2026 (2).xlsx` (sheet "Digital
Rate Card 2026") — 71 products across 11 families, replacing the prior
35-product catalog. This is the single source of truth used by:
  - The Excel proposal-template generator (excel_template.py)
  - The web app proposal builder (app/main.py loads this)

Field provenance from the rate card:
  base_rate / buying_model  <- "Rate Type" + "2026 Target NET Price"
  minimum_spend             <- "Minimum NET Spend per Line" (falls back to $0
                                for "Custom Pricing — see Sales Planning" rows)
  minimum_flight_days       <- parsed from "Suggested Minimum Duration"
  margin_upper/margin_lower <- bracketed +/-5% around "Target Margin %"
                                (the rate card gives one figure, not a range)
  sla_existing_days /
  sla_pending_days          <- SAME value for both — the upper bound of each
                                of "SLA for data submission" + "SLA/Best
                                Practices for creatives" + "SLA/Best Practices
                                to activate campaign", summed (falls back to
                                "Total SLA to go Live" when those three are
                                blank). This rate card no longer distinguishes
                                existing- vs. pending-advertiser SLA the way
                                the prior catalog did, so both fields carry
                                the same total-days-to-go-live number.
  notes / cannabis_policy /
  political_policy          <- "Restricted industries/services/categories",
                                or a reused NOTE_* constant below when the
                                product clearly matches a known platform
                                (Meta, TikTok, Netflix, Roku, YouTube, Google
                                Search, DOOH, Email) whose compliance text is
                                already curated here.
  tech_platform / wide_orbit_code / national_supported / estimated_cpm_for_imps
                            <- carried over from the prior catalog on an exact
                               (or, for tech_platform only, fuzzy) name match;
                               left blank/None for genuinely new products.

Some fields the rate card doesn't break down per-product were approximated —
spot-check margins and flight windows for high-value lines before relying on
them for margin-sensitive approvals. Use the admin rate override UI (/admin)
to correct base_rate / minimum_spend / estimated_cpm_for_imps without a code
change.
"""

import json
from dataclasses import dataclass, field, asdict, replace
from pathlib import Path
from typing import Optional, Union


@dataclass
class Product:
    """One Orderable Product (L2) in the AdFlo catalog."""
    family: str                                  # L1
    name: str                                    # L2 — also the proposal-line display name
    short_label: str                             # used in compact UI lists
    proposal_description: str                    # the "DETAILS" / column E text in the proposal
    sizes: str                                   # column H content
    buying_model: str                            # "CPM" | "CPP" | "Fixed"
    base_rate: Optional[float]                   # USD; None if rate is NA (budget-only)
    estimated_impressions: bool                  # if True, display "Est. <n>" + "Fixed" label
    discloses_impressions: bool                  # if False, hide impressions on client export
    minimum_spend: float                         # USD line minimum
    minimum_flight_days: tuple                   # (min, max)
    sla_existing_days: int
    sla_pending_days: int
    media_allocation_pct: float                  # 0..1
    margin_upper: float                          # 0..1
    margin_lower: float                          # 0..1
    tech_platform: str
    wide_orbit_code: str
    billing_interval: str = "Monthly"
    billing_calendar: str = "Standard/prorated"
    billing_source: str = "1st Party Actual"
    national_supported: bool = True
    notes: str = ""                              # planner-facing column T content
    # Optional impressions divisor for Fixed-rate products
    # (the legacy template uses concatenate("Est. ", text(L*1000/12, ...)) for Meta = 12 CPM est)
    estimated_cpm_for_imps: Optional[float] = None
    # Hispanic-targeting restrictions (some products lock this)
    hispanic_targeting_forced: Optional[bool] = None  # None = both allowed; True/False = forced
    # Category policy
    cannabis_policy: str = "not_allowed"          # "allowed" | "custom_request_only" | "mh_only" | "not_allowed"
    political_policy: str = "allowed"             # "allowed" | "restricted" | "not_allowed"


# Standard planner-facing notes (column T in the legacy template)
NOTE_CANNABIS_CUSTOM = "*Cannabis ads are allowed only thru custom request"
NOTE_CANNABIS_NOT_ALLOWED = "*Cannabis ads are not allowed"
NOTE_CANNABIS_MH_ONLY = "*Cannabis ads are only allowed thru MH"
NOTE_NETFLIX_FULL = (
    "*DCM or Innovid tags, or first-party billing only | IAS or DV measurement pixels only\n"
    "*Assets or tags must be separated by duration (:15/:30) and creative; no VAST rotations\n"
    "*Creative cannot use a click-through URL that includes a UTM code\n"
    "*7+ day flight(s)\n"
    "*Zip = 20 min / pending inventory availability, or use DMA, State or National\n"
    "Restricted categories (creatives need Netflix approval): Legal Services, Financial and banking services\n"
    "Prohibited: Dangerous products, Cannabis/CBD/THC, Tobacco, Recreational drugs, Political advocacy, "
    "Religion, Counterfeit/Illegal products"
)
NOTE_GOOGLE_VERTICALS = (
    "*Review the FAQ tab for certification prices or increased SLAs for verticals such as Healthcare, "
    "Financial or Political.\n"
    "*Cannabis Ads available only thru custom request.\n"
    "*Healthcare clients must fill out: https://support.google.com/google-ads/troubleshooter/6099627\n"
    "*Online pharmacies, telemedicine, addiction services and health insurance allowed with limitations "
    "and require an extra 2-week setup. Cannabis not allowed."
)
NOTE_META_FULL = (
    "*Click-to-WhatsApp campaigns are available only when running on the client's fan page.\n"
    "*Financial Services (Loans, Investment, Insurance, etc.), Housing, and Recruitment ads are restricted "
    "in Meta — only 18+ targeting is available; ZIP, audience exclusion, lookalikes, saved audiences "
    "and some interests are unavailable.\n"
    "*Cannabis not allowed\n"
    "*Political candidates, Gambling, Cannabis, Gun Shows are not allowed. Health-related categories may "
    "run with limitations. OTC meds allowed with care around 'medications/drugs' phrasing."
)
NOTE_META_OO_PAGES = (
    "*No click-to-WhatsApp campaigns available.\n"
    "*Financial Services (Loans, Investment, Insurance, etc.), Housing, and Recruitment ads are restricted "
    "in Meta — only 18+ targeting is available.\n"
    "*Cannabis not allowed\n"
    "*Political candidates, Gambling, Cannabis, Gun Shows are not allowed."
)
NOTE_TIKTOK = "*Cannabis ads are not allowed\n*Political content is not allowed."
NOTE_EMAIL_STATES = (
    "*Cannabis ads are allowed\n"
    "*Florida, Texas, Colorado, Connecticut, Oregon, Minnesota, Tennessee, and Vermont states can't "
    "target Hispanics due to regulations."
)
NOTE_DOOH = (
    "Retargeting not available in Mexico. "
    "Check availability: https://desk.thetradedesk.com/knowledge-portal/en/dooh.html#audience-retargeting"
)
NOTE_AUDIO_COMPANION = "Companion banners only available for site-served (no 3P tag) assets."
NOTE_YT_RADIUS = (
    "*Minimum 5-mile radius is required in the targeting.\n"
    "*Radii smaller than 10 miles, or campaigns with fewer than 20 ZIPs, must account for a $2.00 CPM uplift.\n"
    "*Cannabis ads only thru custom request."
)
NOTE_ROKU = (
    "Best for General Market; can only target Hispanic Affinity.\n"
    "Behavioral targeting is supported only at the large DMA level and can only be combined with 'OR' boolean conditions."
)
NOTE_YT_TV = (
    "40% target margin. Cannot be targeted to Hispanics and creative must be in English. "
    "Margin must be calculated against potential cost and impressions, not what the platform reports."
)


CATALOG: list[Product] = [
    # ===========================================================================
    # SEARCH
    # ===========================================================================
    Product(
        family='Search',
        name='Paid Search - SEM',
        short_label='Click-To-Call / Website Conversion',
        proposal_description='Mininum necessry spend is 30 days. Entravision provides creative and keyword strategy. Keywords need to be approved prior to launching.',
        sizes='Custom',
        buying_model='Fixed',
        base_rate=None,
        estimated_impressions=True,
        discloses_impressions=False,
        minimum_spend=1000.0,
        minimum_flight_days=(30, 365),
        sla_existing_days=7,
        sla_pending_days=7,
        media_allocation_pct=0.0,
        margin_upper=0.35,
        margin_lower=0.3,
        tech_platform='',
        wide_orbit_code='Paid Search - SEM',
        notes=NOTE_GOOGLE_VERTICALS,
        cannabis_policy='not_allowed',
        political_policy='restricted',
    ),

    # ===========================================================================
    # DISPLAY
    # ===========================================================================
    Product(
        family='Display',
        name='eDigital Display',
        short_label='Awareness / Retargeting',
        proposal_description='Includes: DMA geo targeting and device targeting (mobile and desktop).',
        sizes='Custom',
        buying_model='CPM',
        base_rate=8.0,
        estimated_impressions=False,
        discloses_impressions=True,
        minimum_spend=750.0,
        minimum_flight_days=(30, 90),
        sla_existing_days=7,
        sla_pending_days=7,
        media_allocation_pct=0.0,
        margin_upper=0.7,
        margin_lower=0.65,
        tech_platform='TTD',
        wide_orbit_code='eDigital Network Display - Standard IAB',
        notes='*Restricted categories: Financial services, personal loans, binary options, credit repair services, Cryptocurrencies. Prescription medications and information about prescription medications, Promotion of clinical trial recruitment, Promotion of clinical trial recruitment. Promotion of clinical trial recruitment. Physical casinos that explicitly promote gambling. Personal…',
        cannabis_policy='custom_request_only',
        political_policy='restricted',
    ),
    Product(
        family='Display',
        name='eDigital Display | Hispanic Connect',
        short_label='Awareness / Retargeting',
        proposal_description='Includes: Ethnicity, DMA geo targeting, and device targeting (mobile and desktop).',
        sizes='Custom',
        buying_model='CPM',
        base_rate=8.0,
        estimated_impressions=False,
        discloses_impressions=True,
        minimum_spend=750.0,
        minimum_flight_days=(30, 90),
        sla_existing_days=7,
        sla_pending_days=7,
        media_allocation_pct=0.0,
        margin_upper=0.7,
        margin_lower=0.65,
        tech_platform='TTD',
        wide_orbit_code='eDigital Display | Hispanic Connect',
        hispanic_targeting_forced=True,
        notes='*Restricted categories: Financial services, personal loans, binary options, credit repair services, Cryptocurrencies. Prescription medications and information about prescription medications, Promotion of clinical trial recruitment, Promotion of clinical trial recruitment. Promotion of clinical trial recruitment. Physical casinos that explicitly promote gambling. Personal…',
        cannabis_policy='not_allowed',
        political_policy='restricted',
    ),
    Product(
        family='Display',
        name='eDigital Display | Precision',
        short_label='Awareness / Precision Retargeting',
        proposal_description='Includes: age targeting, geo targeting, and one behavioral segment. Each additional behavioral segment: +$1.00 CPM. English content only: +$1.00 CPM.',
        sizes='Custom',
        buying_model='CPM',
        base_rate=10.0,
        estimated_impressions=False,
        discloses_impressions=True,
        minimum_spend=750.0,
        minimum_flight_days=(30, 90),
        sla_existing_days=7,
        sla_pending_days=7,
        media_allocation_pct=0.0,
        margin_upper=0.7,
        margin_lower=0.65,
        tech_platform='TTD',
        wide_orbit_code='eDigital Display | Precision',
        notes='*Restricted categories: Financial services, personal loans, binary options, credit repair services, Cryptocurrencies. Prescription medications and information about prescription medications, Promotion of clinical trial recruitment, Promotion of clinical trial recruitment. Promotion of clinical trial recruitment. Physical casinos that explicitly promote gambling. Personal…',
        cannabis_policy='not_allowed',
        political_policy='restricted',
    ),
    Product(
        family='Display',
        name='eDigital Display | Hispanic Connect + Precision',
        short_label='Awareness / Precision Retargeting',
        proposal_description='Includes: Ethnicity, age targeting, geo targeting, and one behavioral segment. Each additional behavioral segment: +$1.00 CPM. Spanish Content ONLY: +$2.00 CPM',
        sizes='Custom',
        buying_model='CPM',
        base_rate=10.0,
        estimated_impressions=False,
        discloses_impressions=True,
        minimum_spend=750.0,
        minimum_flight_days=(30, 90),
        sla_existing_days=7,
        sla_pending_days=7,
        media_allocation_pct=0.0,
        margin_upper=0.7,
        margin_lower=0.65,
        tech_platform='TTD',
        wide_orbit_code='eDigital Display | Hispanic Connect + Precision',
        hispanic_targeting_forced=True,
        notes='*Restricted categories: Financial services, personal loans, binary options, credit repair services, Cryptocurrencies. Prescription medications and information about prescription medications, Promotion of clinical trial recruitment, Promotion of clinical trial recruitment. Promotion of clinical trial recruitment. Physical casinos that explicitly promote gambling. Personal…',
        cannabis_policy='not_allowed',
        political_policy='restricted',
    ),
    Product(
        family='Display',
        name='eDigital Display | Geo Fence',
        short_label='Conquesting / Local Targeting',
        proposal_description='Display ads served within a defined perimeter around an address, ZIP code, event, neighborhood, or point of interest. Demo targeting is not available. Visitation measurement can be added. Point-of-interest and geo targeting only.',
        sizes='Custom',
        buying_model='CPM',
        base_rate=10.0,
        estimated_impressions=False,
        discloses_impressions=True,
        minimum_spend=1000.0,
        minimum_flight_days=(30, 90),
        sla_existing_days=6,
        sla_pending_days=6,
        media_allocation_pct=0.0,
        margin_upper=0.7,
        margin_lower=0.65,
        tech_platform='TTD',
        wide_orbit_code='eDigital Display | Geo Fence',
        notes='',
        cannabis_policy='not_allowed',
    ),

    # ===========================================================================
    # ONLINE VIDEO
    # ===========================================================================
    Product(
        family='Online Video',
        name='eDigital OLV',
        short_label='Awareness / Consideration',
        proposal_description=':15 and :30 second creatives. - Geo Only (DMA). Device (mobile and desktop).',
        sizes='Custom',
        buying_model='CPM',
        base_rate=18.0,
        estimated_impressions=False,
        discloses_impressions=True,
        minimum_spend=750.0,
        minimum_flight_days=(30, 90),
        sla_existing_days=7,
        sla_pending_days=7,
        media_allocation_pct=0.0,
        margin_upper=0.61,
        margin_lower=0.56,
        tech_platform='TTD',
        wide_orbit_code='eDigital OLV',
        notes='*Restricted categories: Financial services, personal loans, binary options, credit repair services, Cryptocurrencies. Prescription medications and information about prescription medications, Promotion of clinical trial recruitment, Promotion of clinical trial recruitment. Promotion of clinical trial recruitment. Physical casinos that explicitly promote gambling. Personal…',
        cannabis_policy='not_allowed',
        political_policy='restricted',
    ),
    Product(
        family='Online Video',
        name='eDigital OLV | Hispanic Connect',
        short_label='Awareness / Consideration',
        proposal_description=':15 and :30 second creatives. - Ethnicity + Geo Only (DMA). Device (mobile and desktop).',
        sizes='Custom',
        buying_model='CPM',
        base_rate=18.0,
        estimated_impressions=False,
        discloses_impressions=True,
        minimum_spend=750.0,
        minimum_flight_days=(30, 90),
        sla_existing_days=7,
        sla_pending_days=7,
        media_allocation_pct=0.0,
        margin_upper=0.61,
        margin_lower=0.56,
        tech_platform='TTD',
        wide_orbit_code='eDigital OLV | Hispanic Connect',
        hispanic_targeting_forced=True,
        notes='*Restricted categories: Financial services, personal loans, binary options, credit repair services, Cryptocurrencies. Prescription medications and information about prescription medications, Promotion of clinical trial recruitment, Promotion of clinical trial recruitment. Promotion of clinical trial recruitment. Physical casinos that explicitly promote gambling. Personal…',
        cannabis_policy='not_allowed',
        political_policy='restricted',
    ),
    Product(
        family='Online Video',
        name='eDigital OLV | Precision',
        short_label='Awareness / Precision Retargeting',
        proposal_description='Geo (DMA, 20 Zips or other), 2 target layers. Device (mobile and desktop). Additional behavioral targeting beyind the first two +$2.00 English Content ONLY: +$2.00 CPM.',
        sizes='Custom',
        buying_model='CPM',
        base_rate=23.0,
        estimated_impressions=False,
        discloses_impressions=True,
        minimum_spend=750.0,
        minimum_flight_days=(30, 90),
        sla_existing_days=7,
        sla_pending_days=7,
        media_allocation_pct=0.0,
        margin_upper=0.61,
        margin_lower=0.56,
        tech_platform='TTD',
        wide_orbit_code='eDigital OLV | Precision',
        notes='*Restricted categories: Financial services, personal loans, binary options, credit repair services, Cryptocurrencies. Prescription medications and information about prescription medications, Promotion of clinical trial recruitment, Promotion of clinical trial recruitment. Promotion of clinical trial recruitment. Physical casinos that explicitly promote gambling. Personal…',
        cannabis_policy='not_allowed',
        political_policy='restricted',
    ),
    Product(
        family='Online Video',
        name='eDigital OLV | Hispanic Connect + Precision',
        short_label='Awareness / Precision Retargeting',
        proposal_description='Geo (DMA, 20 Zips or other) + Ethnicity, 2 target layers. Device (mobile and desktop). Additional behavioral targeting beyind the first two +$2.00 Spanish Content ONLY: +$3.00 CPM',
        sizes='Custom',
        buying_model='CPM',
        base_rate=23.0,
        estimated_impressions=False,
        discloses_impressions=True,
        minimum_spend=750.0,
        minimum_flight_days=(30, 90),
        sla_existing_days=7,
        sla_pending_days=7,
        media_allocation_pct=0.0,
        margin_upper=0.61,
        margin_lower=0.56,
        tech_platform='TTD',
        wide_orbit_code='eDigital OLV | Hispanic Connect + Precision',
        hispanic_targeting_forced=True,
        notes='*Restricted categories: Financial services, personal loans, binary options, credit repair services, Cryptocurrencies. Prescription medications and information about prescription medications, Promotion of clinical trial recruitment, Promotion of clinical trial recruitment. Promotion of clinical trial recruitment. Physical casinos that explicitly promote gambling. Personal…',
        cannabis_policy='not_allowed',
        political_policy='restricted',
    ),
    Product(
        family='Online Video',
        name='YouTube Ads',
        short_label='Awareness / Consideration',
        proposal_description='Skippable or NonSkippable or Bumper ads are available, each ad type requires a separate campaign (WO line ID) Each language target will need separate line items / campaigns (ie WO line IDs) - Maximum of 6 creatives running concurrently.',
        sizes='Custom',
        buying_model='Fixed',
        base_rate=None,
        estimated_impressions=True,
        discloses_impressions=False,
        estimated_cpm_for_imps=15.0,
        minimum_spend=500.0,
        minimum_flight_days=(30, 90),
        sla_existing_days=11,
        sla_pending_days=11,
        media_allocation_pct=0.0,
        margin_upper=0.48,
        margin_lower=0.43,
        tech_platform='Google Ads',
        wide_orbit_code='YouTube Ads',
        notes=NOTE_YT_RADIUS,
        cannabis_policy='custom_request_only',
        political_policy='restricted',
    ),
    Product(
        family='Online Video',
        name='eDigital OLV | Geo Fence',
        short_label='Conquesting / Local Targeting',
        proposal_description='Serve OLV ads within a virtual perimeter around any address, zip code, event, neighborhood, etc. No demo targeting available. Can attribute visits to targeted perimeters. POI and Geo targeting ONLY.',
        sizes='Custom',
        buying_model='CPM',
        base_rate=23.0,
        estimated_impressions=False,
        discloses_impressions=True,
        minimum_spend=1000.0,
        minimum_flight_days=(30, 90),
        sla_existing_days=6,
        sla_pending_days=6,
        media_allocation_pct=0.0,
        margin_upper=0.65,
        margin_lower=0.6,
        tech_platform='TTD',
        wide_orbit_code='eDigital OLV | Geo Fence',
        notes='',
        cannabis_policy='not_allowed',
    ),

    # ===========================================================================
    # CTV / OTT
    # ===========================================================================
    Product(
        family='CTV / OTT',
        name='Entravision Plus CTV | Premier',
        short_label='Awareness / Reach',
        proposal_description='Geo targeting ONLY. All content. Additional behavioral targeting segments require Addressable OTT. 15 and 30 second creatives only. 60 second+ creatives are not allowed. Specific Publisher delivery is not allowed English Content ONLY: +$2.00 CPM.',
        sizes='Custom',
        buying_model='CPM',
        base_rate=34.0,
        estimated_impressions=False,
        discloses_impressions=True,
        minimum_spend=1000.0,
        minimum_flight_days=(30, 90),
        sla_existing_days=7,
        sla_pending_days=7,
        media_allocation_pct=0.0,
        margin_upper=0.38,
        margin_lower=0.33,
        tech_platform='TTD, DV360, Madhive',
        wide_orbit_code='OTT - Connected TV',
        notes='',
        cannabis_policy='not_allowed',
    ),
    Product(
        family='CTV / OTT',
        name='Entravision Plus CTV | Premier | Hispanic Connect',
        short_label='Awareness / Reach',
        proposal_description='Ethnicity Targeting included. All content. GEO targeting ONLY, but must be approved by Sales Planning for inventory availability. Additional behavioral targeting segments beyond the first: + $2 CPM 15 and 30 second creatives only. 60 second+ creatives are not allowed. Specific Publisher delivery is not guaranteed Spanish Content ONLY: +$6.00 CPM',
        sizes='Custom',
        buying_model='CPM',
        base_rate=34.0,
        estimated_impressions=False,
        discloses_impressions=True,
        minimum_spend=1000.0,
        minimum_flight_days=(30, 90),
        sla_existing_days=7,
        sla_pending_days=7,
        media_allocation_pct=0.0,
        margin_upper=0.38,
        margin_lower=0.33,
        tech_platform='TTD, DV360, Madhive',
        wide_orbit_code='Entravision Plus CTV | Premier | Hispanic Connect',
        hispanic_targeting_forced=True,
        notes='',
        cannabis_policy='not_allowed',
    ),
    Product(
        family='CTV / OTT',
        name='Entravision Plus CTV | Premier | Precision',
        short_label='Awareness / Reach',
        proposal_description='All content. Includes two demo or behavioral targeting segments, but must be approved by Sales Planning for inventory availability. Additional behavioral targeting segments beyond the first: + $2 CPM 15 and 30 second creatives only. 60 second+ creatives are not allowed. Specific Publisher delivery is not allowed English Content ONLY: +$2.00 CPM',
        sizes='Custom',
        buying_model='CPM',
        base_rate=36.0,
        estimated_impressions=False,
        discloses_impressions=True,
        minimum_spend=1000.0,
        minimum_flight_days=(30, 90),
        sla_existing_days=7,
        sla_pending_days=7,
        media_allocation_pct=0.0,
        margin_upper=0.4,
        margin_lower=0.35,
        tech_platform='TTD, DV360, Madhive',
        wide_orbit_code='Entravision Plus CTV | Premier | Precision',
        notes='',
        cannabis_policy='not_allowed',
    ),
    Product(
        family='CTV / OTT',
        name='Entravision Plus CTV | Premier | Hispanic Connect + Precision',
        short_label='Awareness / Reach',
        proposal_description='Ethnicity Targeting included. All content. Includes two demo or behavioral targeting segments, but must be approved by Sales Planning for inventory availability. Additional behavioral targeting segments beyond the first: + $2 CPM 15 and 30 second creatives only. 60 second+ creatives are not allowed. Specific Publisher delivery is not guaranteed Spanish Content ONLY: +$6.00 CPM',
        sizes='Custom',
        buying_model='CPM',
        base_rate=36.0,
        estimated_impressions=False,
        discloses_impressions=True,
        minimum_spend=1000.0,
        minimum_flight_days=(30, 90),
        sla_existing_days=7,
        sla_pending_days=7,
        media_allocation_pct=0.0,
        margin_upper=0.38,
        margin_lower=0.33,
        tech_platform='TTD, DV360, Madhive',
        wide_orbit_code='Entravision Plus CTV | Premier | Hispanic Connect + Precision',
        hispanic_targeting_forced=True,
        notes='',
        cannabis_policy='not_allowed',
    ),
    Product(
        family='CTV / OTT',
        name='Entravision Plus CTV | ViX',
        short_label='Awareness / Hispanic Premium Reach',
        proposal_description='A premium video ecosystem spanning all of TelevisaUnivision (VIX 360) on CTV devices, FAST platforms and social video sites.',
        sizes='Custom',
        buying_model='CPM',
        base_rate=39.0,
        estimated_impressions=False,
        discloses_impressions=True,
        minimum_spend=1000.0,
        minimum_flight_days=(30, 90),
        sla_existing_days=7,
        sla_pending_days=7,
        media_allocation_pct=0.0,
        margin_upper=0.45,
        margin_lower=0.4,
        tech_platform='TTD',
        wide_orbit_code='OTT - Vix',
        national_supported=False,
        notes='*Restricted categories: Gambling, Lottery, Alcohol, CBD Oil/Cannabis, Medical Marijuana, Vaping/eCigs, Male Enhancement, Bail Bonds, Pharmaceuticals, Political*, National Geography, COVID Related (Health Masks, testing, etc) **Amazon Restrictions May Vary** Advertiser/Creative approval generally takes 2-3 business days upon receipt of actual creative files. Depending on…',
        cannabis_policy='not_allowed',
        political_policy='restricted',
    ),
    Product(
        family='CTV / OTT',
        name='Entravision Plus CTV | Roku',
        short_label='Awareness / Reach',
        proposal_description="A premium video ecosystem spanning all of Roku's owned and operated channels and apps on CTV and mobile devices. Runs in all content, English creative is mandatory. Spanish subtitles are optional but recommended when targeting Hispanic channel viewership audiences.",
        sizes='Custom',
        buying_model='CPM',
        base_rate=16.0,
        estimated_impressions=False,
        discloses_impressions=True,
        minimum_spend=1000.0,
        minimum_flight_days=(30, 90),
        sla_existing_days=7,
        sla_pending_days=7,
        media_allocation_pct=0.0,
        margin_upper=0.55,
        margin_lower=0.5,
        tech_platform='Roku Ads Manager',
        wide_orbit_code='OTT - Roku',
        national_supported=False,
        notes=NOTE_ROKU,
        cannabis_policy='not_allowed',
        political_policy='restricted',
    ),
    Product(
        family='CTV / OTT',
        name='Entravision Plus CTV | Premier | Live Sports',
        short_label='Awareness / Live Sports Sponsorship',
        proposal_description='Ethnicity Targeting included. All content. Includes two demo or behavioral targeting segments, but must be approved by Sales Planning for inventory availability. Additional behavioral targeting segments beyond the first: + $2 CPM 15 and 30 second creatives only. 60 second+ creatives are not allowed. Specific Publisher delivery is not guaranteed Spanish Content ONLY: +$6.00 CPM',
        sizes='Custom',
        buying_model='CPM',
        base_rate=65.0,
        estimated_impressions=False,
        discloses_impressions=True,
        minimum_spend=1000.0,
        minimum_flight_days=(30, 90),
        sla_existing_days=7,
        sla_pending_days=7,
        media_allocation_pct=0.0,
        margin_upper=0.38,
        margin_lower=0.33,
        tech_platform='TTD, DV360, Madhive',
        wide_orbit_code='Entravision Plus CTV | Premier | Live Sports',
        notes='',
        cannabis_policy='not_allowed',
    ),
    Product(
        family='CTV / OTT',
        name='Entravision Plus CTV | Premier | Live Soccer',
        short_label='Awareness / Live Sports Sponsorship',
        proposal_description='Ethnicity Targeting included. All content. Includes two demo or behavioral targeting segments, but must be approved by Sales Planning for inventory availability. Additional behavioral targeting segments beyond the first: + $2 CPM 15 and 30 second creatives only. 60 second+ creatives are not allowed. Specific Publisher delivery is not guaranteed Spanish Content ONLY: +$6.00 CPM',
        sizes='Custom',
        buying_model='CPM',
        base_rate=71.0,
        estimated_impressions=False,
        discloses_impressions=True,
        minimum_spend=1000.0,
        minimum_flight_days=(30, 90),
        sla_existing_days=7,
        sla_pending_days=7,
        media_allocation_pct=0.0,
        margin_upper=0.38,
        margin_lower=0.33,
        tech_platform='TTD, DV360, Madhive',
        wide_orbit_code='Entravision Plus CTV | Premier | Live Soccer',
        notes='',
        cannabis_policy='not_allowed',
    ),
    Product(
        family='CTV / OTT',
        name='Entravision Plus CTV | Premier | Streaming Sports',
        short_label='Awareness / Live Sports Sponsorship',
        proposal_description='Ethnicity Targeting included. All content. Includes two demo or behavioral targeting segments, but must be approved by Sales Planning for inventory availability. Additional behavioral targeting segments beyond the first: + $2 CPM 15 and 30 second creatives only. 60 second+ creatives are not allowed. Specific Publisher delivery is not guaranteed Spanish Content ONLY: +$6.00 CPM',
        sizes='Custom',
        buying_model='CPM',
        base_rate=33.0,
        estimated_impressions=False,
        discloses_impressions=True,
        minimum_spend=1000.0,
        minimum_flight_days=(30, 90),
        sla_existing_days=7,
        sla_pending_days=7,
        media_allocation_pct=0.0,
        margin_upper=0.38,
        margin_lower=0.33,
        tech_platform='TTD, DV360, Madhive',
        wide_orbit_code='Entravision Plus CTV | Premier | Streaming Sports',
        notes='',
        cannabis_policy='not_allowed',
    ),
    Product(
        family='CTV / OTT',
        name='Entravision Plus CTV | YouTube TV',
        short_label='Awareness / Reach',
        proposal_description='Ethnicity Targeting included. All content. Includes two demo or behavioral targeting segments, but must be approved by Sales Planning for inventory availability. Additional behavioral targeting segments beyond the first: + $2 CPM 15 and 30 second creatives only. 60 second+ creatives are not allowed. Specific Publisher delivery is not guaranteed Spanish Content ONLY: +$6.00 CPM',
        sizes='Custom',
        buying_model='CPM',
        base_rate=36.0,
        estimated_impressions=False,
        discloses_impressions=True,
        minimum_spend=1000.0,
        minimum_flight_days=(30, 90),
        sla_existing_days=7,
        sla_pending_days=7,
        media_allocation_pct=0.0,
        margin_upper=0.38,
        margin_lower=0.33,
        tech_platform='TTD, DV360, Madhive',
        wide_orbit_code='OTT - Connected TV',
        national_supported=False,
        notes=NOTE_YT_TV,
        cannabis_policy='not_allowed',
    ),
    Product(
        family='CTV / OTT',
        name='Entravision Plus CTV | Amazon Prime Video',
        short_label='Awareness / Reach',
        proposal_description='Ethnicity Targeting included. All content. Includes two demo or behavioral targeting segments, but must be approved by Sales Planning for inventory availability. Additional behavioral targeting segments beyond the first: + $2 CPM 15 and 30 second creatives only. 60 second+ creatives are not allowed. Specific Publisher delivery is not guaranteed Spanish Content ONLY: +$6.00 CPM',
        sizes='Custom',
        buying_model='CPM',
        base_rate=40.0,
        estimated_impressions=False,
        discloses_impressions=True,
        minimum_spend=1000.0,
        minimum_flight_days=(30, 90),
        sla_existing_days=7,
        sla_pending_days=7,
        media_allocation_pct=0.0,
        margin_upper=0.38,
        margin_lower=0.33,
        tech_platform='TTD, DV360, Madhive',
        wide_orbit_code='OTT - Connected TV',
        national_supported=False,
        notes="*Restricted categories: Prohibited Ad Categories: Political, Cannabis/CBD/THC, Tobacco/vaping, Bail bonds, Gentlemen's clubs/adult entertainment. RESTRICTED (require confirmation): Gambling/casinos (Amazon pre-auth + state license required), Legal/attorney (confirm with Jellyfish), Healthcare Cat. 3 (ED, GLP-1, reproductive health). Restricted with geo/age rules:…",
        cannabis_policy='not_allowed',
        political_policy='not_allowed',
    ),
    Product(
        family='CTV / OTT',
        name='Entravision Plus CTV | Netflix | Run of Network | :15',
        short_label='Premium Awareness',
        proposal_description=':15s Ads Only. General market targeting only.',
        sizes='Custom',
        buying_model='CPM',
        base_rate=44.0,
        estimated_impressions=False,
        discloses_impressions=True,
        minimum_spend=2500.0,
        minimum_flight_days=(7, 90),
        sla_existing_days=10,
        sla_pending_days=10,
        media_allocation_pct=0.0,
        margin_upper=0.4,
        margin_lower=0.35,
        tech_platform='TTD, DV360, Madhive',
        wide_orbit_code='Entravision Plus CTV | Netflix | Run of Network | :15',
        notes=NOTE_NETFLIX_FULL,
        cannabis_policy='not_allowed',
        political_policy='restricted',
    ),
    Product(
        family='CTV / OTT',
        name='Entravision Plus CTV | Netflix | Run of Network | :30',
        short_label='Premium Awareness',
        proposal_description=':30s Ads Only. General market targeting only.',
        sizes='Custom',
        buying_model='CPM',
        base_rate=54.0,
        estimated_impressions=False,
        discloses_impressions=True,
        minimum_spend=2500.0,
        minimum_flight_days=(7, 90),
        sla_existing_days=10,
        sla_pending_days=10,
        media_allocation_pct=0.0,
        margin_upper=0.4,
        margin_lower=0.35,
        tech_platform='TTD, DV360, Madhive',
        wide_orbit_code='Entravision Plus CTV | Netflix | Run of Network | :30',
        notes=NOTE_NETFLIX_FULL,
        cannabis_policy='not_allowed',
        political_policy='restricted',
    ),
    Product(
        family='CTV / OTT',
        name='Entravision Plus CTV | Netflix | Age Targeted | :15',
        short_label='Premium Awareness',
        proposal_description=':15s Ads Only. General market with age targeting only.',
        sizes='Custom',
        buying_model='CPM',
        base_rate=51.0,
        estimated_impressions=False,
        discloses_impressions=True,
        minimum_spend=2500.0,
        minimum_flight_days=(7, 90),
        sla_existing_days=10,
        sla_pending_days=10,
        media_allocation_pct=0.0,
        margin_upper=0.4,
        margin_lower=0.35,
        tech_platform='TTD, DV360, Madhive',
        wide_orbit_code='Entravision Plus CTV | Netflix | Age Targeted | :15',
        notes=NOTE_NETFLIX_FULL,
        cannabis_policy='not_allowed',
        political_policy='restricted',
    ),
    Product(
        family='CTV / OTT',
        name='Entravision Plus CTV | Netflix | Age Targeted | :30',
        short_label='Premium Awareness',
        proposal_description=':30s Ads Only. General market with age targeting only.',
        sizes='Custom',
        buying_model='CPM',
        base_rate=63.0,
        estimated_impressions=False,
        discloses_impressions=True,
        minimum_spend=2500.0,
        minimum_flight_days=(7, 90),
        sla_existing_days=10,
        sla_pending_days=10,
        media_allocation_pct=0.0,
        margin_upper=0.4,
        margin_lower=0.35,
        tech_platform='TTD, DV360, Madhive',
        wide_orbit_code='Entravision Plus CTV | Netflix | Age Targeted | :30',
        notes=NOTE_NETFLIX_FULL,
        cannabis_policy='not_allowed',
        political_policy='restricted',
    ),
    Product(
        family='CTV / OTT',
        name='Entravision Plus CTV | Netflix | Gender Targeted | :15',
        short_label='Premium Awareness',
        proposal_description=':15s Ads Only. General market with gender targeting only.',
        sizes='Custom',
        buying_model='CPM',
        base_rate=51.0,
        estimated_impressions=False,
        discloses_impressions=True,
        minimum_spend=2500.0,
        minimum_flight_days=(7, 90),
        sla_existing_days=10,
        sla_pending_days=10,
        media_allocation_pct=0.0,
        margin_upper=0.4,
        margin_lower=0.35,
        tech_platform='TTD, DV360, Madhive',
        wide_orbit_code='Entravision Plus CTV | Netflix | Gender Targeted | :15',
        notes=NOTE_NETFLIX_FULL,
        cannabis_policy='not_allowed',
        political_policy='restricted',
    ),
    Product(
        family='CTV / OTT',
        name='Entravision Plus CTV | Netflix | Gender Targeted | :30',
        short_label='Premium Awareness',
        proposal_description=':30s Ads Only. General market with gender targeting only.',
        sizes='Custom',
        buying_model='CPM',
        base_rate=63.0,
        estimated_impressions=False,
        discloses_impressions=True,
        minimum_spend=2500.0,
        minimum_flight_days=(7, 90),
        sla_existing_days=10,
        sla_pending_days=10,
        media_allocation_pct=0.0,
        margin_upper=0.4,
        margin_lower=0.35,
        tech_platform='TTD, DV360, Madhive',
        wide_orbit_code='Entravision Plus CTV | Netflix | Gender Targeted | :30',
        notes=NOTE_NETFLIX_FULL,
        cannabis_policy='not_allowed',
        political_policy='restricted',
    ),
    Product(
        family='CTV / OTT',
        name='Entravision Plus CTV | Netflix | Genre Targeted | :15',
        short_label='Premium Awareness',
        proposal_description=':15s Ads Only. General market with genre targeting only. Can target the following genres: Drama, Comedy, Unscripted, Thriller/Horror, or Action.',
        sizes='Custom',
        buying_model='CPM',
        base_rate=49.0,
        estimated_impressions=False,
        discloses_impressions=True,
        minimum_spend=2500.0,
        minimum_flight_days=(7, 90),
        sla_existing_days=10,
        sla_pending_days=10,
        media_allocation_pct=0.0,
        margin_upper=0.4,
        margin_lower=0.35,
        tech_platform='TTD, DV360, Madhive',
        wide_orbit_code='Entravision Plus CTV | Netflix | Genre Targeted | :15',
        notes=NOTE_NETFLIX_FULL,
        cannabis_policy='not_allowed',
        political_policy='restricted',
    ),
    Product(
        family='CTV / OTT',
        name='Entravision Plus CTV | Netflix | Genre Targeted | :30',
        short_label='Premium Awareness',
        proposal_description=':30s Ads Only. General market with genre targeting only. Can target the following genres: Drama, Comedy, Unscripted, Thriller/Horror, or Action.',
        sizes='Custom',
        buying_model='CPM',
        base_rate=61.0,
        estimated_impressions=False,
        discloses_impressions=True,
        minimum_spend=2500.0,
        minimum_flight_days=(7, 90),
        sla_existing_days=10,
        sla_pending_days=10,
        media_allocation_pct=0.0,
        margin_upper=0.4,
        margin_lower=0.35,
        tech_platform='TTD, DV360, Madhive',
        wide_orbit_code='Entravision Plus CTV | Netflix | Genre Targeted | :30',
        notes=NOTE_NETFLIX_FULL,
        cannabis_policy='not_allowed',
        political_policy='restricted',
    ),
    Product(
        family='CTV / OTT',
        name='Entravision Plus CTV | Netflix | Hispanic Connect | :15',
        short_label='Premium Awareness',
        proposal_description=':15s Ads Only. Targeting Spanish Content Programming.',
        sizes='Custom',
        buying_model='CPM',
        base_rate=50.0,
        estimated_impressions=False,
        discloses_impressions=True,
        minimum_spend=30000.0,
        minimum_flight_days=(7, 90),
        sla_existing_days=13,
        sla_pending_days=13,
        media_allocation_pct=0.0,
        margin_upper=0.4,
        margin_lower=0.35,
        tech_platform='TTD, DV360, Madhive',
        wide_orbit_code='Entravision Plus CTV | Netflix | Hispanic Connect | :15',
        hispanic_targeting_forced=True,
        notes=NOTE_NETFLIX_FULL,
        cannabis_policy='not_allowed',
        political_policy='restricted',
    ),
    Product(
        family='CTV / OTT',
        name='Entravision Plus CTV | Netflix | Hispanic Connect | :30',
        short_label='Premium Awareness',
        proposal_description=':30s Ads Only. Targeting Spanish Content Programming.',
        sizes='Custom',
        buying_model='CPM',
        base_rate=60.0,
        estimated_impressions=False,
        discloses_impressions=True,
        minimum_spend=30000.0,
        minimum_flight_days=(7, 90),
        sla_existing_days=13,
        sla_pending_days=13,
        media_allocation_pct=0.0,
        margin_upper=0.4,
        margin_lower=0.35,
        tech_platform='TTD, DV360, Madhive',
        wide_orbit_code='Entravision Plus CTV | Netflix | Hispanic Connect | :30',
        hispanic_targeting_forced=True,
        notes=NOTE_NETFLIX_FULL,
        cannabis_policy='not_allowed',
        political_policy='restricted',
    ),

    # ===========================================================================
    # AUDIO
    # ===========================================================================
    Product(
        family='Audio',
        name='Entravision Audio | Local Stream | Station Specific',
        short_label='Awareness / Frequency Building',
        proposal_description='Audio spots of 15, 30 or 60 seconds, streamed on one specific local radio station from our own O&O properties.. Dayparting: +$3.00 CPM',
        sizes='Custom',
        buying_model='CPP',
        base_rate=5.0,
        estimated_impressions=False,
        discloses_impressions=True,
        minimum_spend=300.0,
        minimum_flight_days=(30, 90),
        sla_existing_days=3,
        sla_pending_days=3,
        media_allocation_pct=0.0,
        margin_upper=1.0,
        margin_lower=1.0,
        tech_platform='',
        wide_orbit_code='Entravision Audio | Local Stream | Station Specific',
        notes='',
        cannabis_policy='not_allowed',
    ),
    Product(
        family='Audio',
        name='Entravision Audio | Local Stream | KLYY Jose | Spot',
        short_label='Awareness / Hispanic Local Reach',
        proposal_description='Audio spots of 15, 30 or 60 seconds, streamed on KLYY Jose Station',
        sizes='Custom',
        buying_model='CPP',
        base_rate=15.0,
        estimated_impressions=False,
        discloses_impressions=True,
        minimum_spend=600.0,
        minimum_flight_days=(30, 90),
        sla_existing_days=3,
        sla_pending_days=3,
        media_allocation_pct=0.0,
        margin_upper=1.0,
        margin_lower=1.0,
        tech_platform='',
        wide_orbit_code='Entravision Audio | Local Stream | KLYY Jose | Spot',
        notes='',
        cannabis_policy='not_allowed',
    ),
    Product(
        family='Audio',
        name='Entravision Audio | Local Stream | KLYY Jose | CPM',
        short_label='Awareness / Hispanic Local Reach',
        proposal_description='Audio impressions for 15, 30 or 60 seconds, streamed on our own O&O KLYY Jose Station. *Dayparting must be approved by Sales Planning',
        sizes='Custom',
        buying_model='CPM',
        base_rate=14.0,
        estimated_impressions=False,
        discloses_impressions=True,
        minimum_spend=600.0,
        minimum_flight_days=(30, 90),
        sla_existing_days=3,
        sla_pending_days=3,
        media_allocation_pct=0.0,
        margin_upper=1.0,
        margin_lower=1.0,
        tech_platform='',
        wide_orbit_code='Entravision Audio | Local Stream | KLYY Jose | CPM',
        notes='',
        cannabis_policy='not_allowed',
    ),
    Product(
        family='Audio',
        name='Audio Engage',
        short_label='Awareness / Frequency Building',
        proposal_description=':15 or :30 second ads. Includes: Geo targeting (state, DMA) + Hispanic ONLY. Cross Device (desktop, tablet and mobile) offering. :60s ads: +$5.00 CPM',
        sizes='Custom',
        buying_model='CPM',
        base_rate=12.0,
        estimated_impressions=False,
        discloses_impressions=True,
        minimum_spend=500.0,
        minimum_flight_days=(30, 90),
        sla_existing_days=4,
        sla_pending_days=4,
        media_allocation_pct=0.0,
        margin_upper=0.65,
        margin_lower=0.6,
        tech_platform='Adswizz, Triton',
        wide_orbit_code='Audio - Audio Engage',
        notes=NOTE_AUDIO_COMPANION,
        cannabis_policy='custom_request_only',
    ),
    Product(
        family='Audio',
        name='Audio Engage | Precision',
        short_label='Awareness / Frequency Building',
        proposal_description='Includes: Geo targeting (state, DMA) + Ethnicity + Demo targeting and 1 Additional Targeting Layer. Cross Device (desktop and mobile) offering. Conversion and retargeting strategy.',
        sizes='Custom',
        buying_model='CPM',
        base_rate=15.0,
        estimated_impressions=False,
        discloses_impressions=True,
        minimum_spend=500.0,
        minimum_flight_days=(30, 90),
        sla_existing_days=4,
        sla_pending_days=4,
        media_allocation_pct=0.0,
        margin_upper=0.65,
        margin_lower=0.6,
        tech_platform='Triton',
        wide_orbit_code='Audio Engage | Precision',
        national_supported=False,
        notes=NOTE_AUDIO_COMPANION,
        cannabis_policy='not_allowed',
    ),
    Product(
        family='Audio',
        name='Spotify Ads',
        short_label='Awareness / Frequency Building',
        proposal_description='30 seconds promotional messages that play in between songs. Includes: geo (DMA, city, zipcodes), gender, language & age Each language target will need separate campaigns (ie WO Line IDs)',
        sizes='Custom',
        buying_model='Fixed',
        base_rate=None,
        estimated_impressions=True,
        discloses_impressions=False,
        estimated_cpm_for_imps=35.0,
        minimum_spend=500.0,
        minimum_flight_days=(30, 90),
        sla_existing_days=3,
        sla_pending_days=3,
        media_allocation_pct=0.0,
        margin_upper=0.55,
        margin_lower=0.5,
        tech_platform='Spotify Ad Manager',
        wide_orbit_code='Spotify Ads',
        national_supported=False,
        notes='*Restricted categories: Finance and insurance: payday and emergency loans, digital goods and currency Fireworks Sexual content and paraphernalia Tobacco, cigarettes and related accessories Vaporizers Recreational drugs and related accessories Weapons and firearms Politics: election, political issues',
        cannabis_policy='not_allowed',
        political_policy='restricted',
    ),
    Product(
        family='Audio',
        name='YouTube Audio Ads',
        short_label='Awareness / Frequency Building',
        proposal_description='15 seconds promotional messages that play in between songs and podcasts. Includes: geo (DMA, city, zipcodes), gender, language & age Each language target will need separate campaigns (ie WO Line IDs)',
        sizes='Custom',
        buying_model='Fixed',
        base_rate=None,
        estimated_impressions=True,
        discloses_impressions=False,
        estimated_cpm_for_imps=20.0,
        minimum_spend=500.0,
        minimum_flight_days=(30, 90),
        sla_existing_days=3,
        sla_pending_days=3,
        media_allocation_pct=0.0,
        margin_upper=0.55,
        margin_lower=0.5,
        tech_platform='DV360',
        wide_orbit_code='YouTube Audio Ads',
        national_supported=False,
        notes=NOTE_YT_RADIUS,
        cannabis_policy='not_allowed',
        political_policy='restricted',
    ),

    # ===========================================================================
    # SOCIAL
    # ===========================================================================
    Product(
        family='Social',
        name='Branded Content | Meta | Organic Post',
        short_label='Brand Integration / Engagement',
        proposal_description="Video or image with logo integration. Sponsored or custom content. It will run in: Noticias Ya Page Feed + Facebook/instagram. NO CLIENT ONBOARDING, but Client's FB page & approval for tagging is MANDATORY. Branded Video Production isn't Included, it requires a 'Creative Labs | Meta | Ads' investment of +$1,300",
        sizes='Custom',
        buying_model='Fixed',
        base_rate=None,
        estimated_impressions=True,
        discloses_impressions=False,
        minimum_spend=300.0,
        minimum_flight_days=(1, 90),
        sla_existing_days=8,
        sla_pending_days=8,
        media_allocation_pct=0.0,
        margin_upper=1.0,
        margin_lower=1.0,
        tech_platform='',
        wide_orbit_code='Branded Content | Meta | Organic Post',
        notes=NOTE_META_FULL,
        cannabis_policy='not_allowed',
    ),
    Product(
        family='Social',
        name='Branded Content | Meta | Ads',
        short_label='Brand Integration / Engagement',
        proposal_description="Client Static Images OR Client Videos (created by the client). It will run in: Facebook Network. NO CLIENT ONBOARDING, but Client's Facebook page & approval for tagging is RECOMMENDED",
        sizes='Custom',
        buying_model='Fixed',
        base_rate=None,
        estimated_impressions=True,
        discloses_impressions=False,
        minimum_spend=500.0,
        minimum_flight_days=(5, 90),
        sla_existing_days=7,
        sla_pending_days=7,
        media_allocation_pct=0.0,
        margin_upper=0.7,
        margin_lower=0.65,
        tech_platform='',
        wide_orbit_code='Branded Content | Meta | Ads',
        notes=NOTE_META_FULL,
        cannabis_policy='not_allowed',
    ),
    Product(
        family='Social',
        name='Creative Labs | Meta | Ads',
        short_label='Brand Integration / Engagement',
        proposal_description="Static Images OR Videos (created by our Creative Labs team). It will run in: Facebook Network. NO CLIENT ONBOARDING, but Client's Facebook page & approval for tagging is RECOMMENDED",
        sizes='Custom',
        buying_model='Fixed',
        base_rate=None,
        estimated_impressions=True,
        discloses_impressions=False,
        minimum_spend=1300.0,
        minimum_flight_days=(5, 90),
        sla_existing_days=7,
        sla_pending_days=7,
        media_allocation_pct=0.0,
        margin_upper=0.7,
        margin_lower=0.65,
        tech_platform='',
        wide_orbit_code='Creative Labs | Meta | Ads',
        notes=NOTE_META_FULL,
        cannabis_policy='not_allowed',
    ),
    Product(
        family='Social',
        name='Creative Labs | Meta | Live',
        short_label='Brand Integration / Engagement',
        proposal_description="Live content video logo integration or Live On client site + 1 static image post for promotion OR Instagram video Post on feed. It will run in: Noticias Ya Page Feed + Facebook/instagram amplification. Client's FB page & approval for tagging is MANDATORY Live or prerecorded (Look Live) From 7 to 30 minutes",
        sizes='Custom',
        buying_model='Fixed',
        base_rate=None,
        estimated_impressions=True,
        discloses_impressions=False,
        minimum_spend=1500.0,
        minimum_flight_days=(5, 90),
        sla_existing_days=8,
        sla_pending_days=8,
        media_allocation_pct=0.0,
        margin_upper=0.7,
        margin_lower=0.65,
        tech_platform='',
        wide_orbit_code='Creative Labs | Meta | Live',
        notes=NOTE_META_FULL,
        cannabis_policy='not_allowed',
    ),
    Product(
        family='Social',
        name='Talent Fee',
        short_label='Brand Integration / Awareness',
        proposal_description='Want a non SMC on-camera influencer. See sales planning',
        sizes='Custom',
        buying_model='Fixed',
        base_rate=None,
        estimated_impressions=True,
        discloses_impressions=False,
        minimum_spend=0.0,
        minimum_flight_days=(3, 90),
        sla_existing_days=5,
        sla_pending_days=5,
        media_allocation_pct=0.0,
        margin_upper=1.0,
        margin_lower=1.0,
        tech_platform='',
        wide_orbit_code='Talent Fee',
        notes=NOTE_META_OO_PAGES,
        cannabis_policy='not_allowed',
    ),
    Product(
        family='Social',
        name='Talent Connect | Shoboy',
        short_label='Brand Integration / Awareness',
        proposal_description="Video or image with Branded Content Tool and logo integration. It will run in: Radio pages Facebook/instagram. NO CLIENT ONBOARDING, but Client's FB page & approval for tagging is MANDATORY.",
        sizes='Custom',
        buying_model='Fixed',
        base_rate=None,
        estimated_impressions=True,
        discloses_impressions=False,
        estimated_cpm_for_imps=15.0,
        minimum_spend=0.0,
        minimum_flight_days=(5, 90),
        sla_existing_days=7,
        sla_pending_days=7,
        media_allocation_pct=0.0,
        margin_upper=0.8,
        margin_lower=0.75,
        tech_platform='Meta Business Manager',
        wide_orbit_code='Talent Connect | Shoboy',
        notes=NOTE_META_OO_PAGES,
        cannabis_policy='not_allowed',
    ),
    Product(
        family='Social',
        name='Talent Connect | Erazo',
        short_label='Brand Integration / Awareness',
        proposal_description="Video or image with Branded Content Tool and logo integration. It will run in: Radio pages Facebook/instagram. NO CLIENT ONBOARDING, but Client's FB page & approval for tagging is MANDATORY.",
        sizes='Custom',
        buying_model='Fixed',
        base_rate=None,
        estimated_impressions=True,
        discloses_impressions=False,
        estimated_cpm_for_imps=15.0,
        minimum_spend=0.0,
        minimum_flight_days=(5, 90),
        sla_existing_days=7,
        sla_pending_days=7,
        media_allocation_pct=0.0,
        margin_upper=0.8,
        margin_lower=0.75,
        tech_platform='Meta Business Manager',
        wide_orbit_code='Talent Connect | Erazo',
        notes=NOTE_META_OO_PAGES,
        cannabis_policy='not_allowed',
    ),
    Product(
        family='Social',
        name='Talent Connect | Genio Lucas',
        short_label='Brand Integration / Awareness',
        proposal_description="Video or image with Branded Content Tool and logo integration. It will run in: Radio pages Facebook/instagram. NO CLIENT ONBOARDING, but Client's FB page & approval for tagging is MANDATORY.",
        sizes='Custom',
        buying_model='Fixed',
        base_rate=None,
        estimated_impressions=True,
        discloses_impressions=False,
        estimated_cpm_for_imps=15.0,
        minimum_spend=0.0,
        minimum_flight_days=(5, 90),
        sla_existing_days=7,
        sla_pending_days=7,
        media_allocation_pct=0.0,
        margin_upper=0.8,
        margin_lower=0.75,
        tech_platform='Meta Business Manager',
        wide_orbit_code='Talent Connect | Genio Lucas',
        notes=NOTE_META_OO_PAGES,
        cannabis_policy='not_allowed',
    ),
    Product(
        family='Social',
        name='Talent Connect | Piolin',
        short_label='Brand Integration / Awareness',
        proposal_description="Video or image with Branded Content Tool and logo integration. It will run in: Radio pages Facebook/instagram. NO CLIENT ONBOARDING, but Client's FB page & approval for tagging is MANDATORY.",
        sizes='Custom',
        buying_model='Fixed',
        base_rate=None,
        estimated_impressions=True,
        discloses_impressions=False,
        estimated_cpm_for_imps=15.0,
        minimum_spend=0.0,
        minimum_flight_days=(5, 90),
        sla_existing_days=7,
        sla_pending_days=7,
        media_allocation_pct=0.0,
        margin_upper=0.8,
        margin_lower=0.75,
        tech_platform='Meta Business Manager',
        wide_orbit_code='Talent Connect | Piolin',
        notes=NOTE_META_OO_PAGES,
        cannabis_policy='not_allowed',
    ),
    Product(
        family='Social',
        name='Meta Ads',
        short_label='Awareness / Conversion / Lead-Gen',
        proposal_description='Monthly ad campaign, placement: Facebook + Instagram, 3 creative variations, up to 4 creative change per month, reporting dashboard, real time budget optimization. Optional: A/B Testing and Pixel Implementation for re targeting. Client page access is required for this product.',
        sizes='Custom',
        buying_model='Fixed',
        base_rate=None,
        estimated_impressions=True,
        discloses_impressions=False,
        estimated_cpm_for_imps=12.0,
        minimum_spend=500.0,
        minimum_flight_days=(30, 90),
        sla_existing_days=7,
        sla_pending_days=7,
        media_allocation_pct=0.0,
        margin_upper=0.55,
        margin_lower=0.5,
        tech_platform='Meta Business Manager',
        wide_orbit_code='Facebook & Instagram Ads',
        national_supported=False,
        notes=NOTE_META_FULL,
        cannabis_policy='not_allowed',
        political_policy='not_allowed',
    ),
    Product(
        family='Social',
        name='TikTok Ads',
        short_label='Awareness / Conversion / Lead-Gen',
        proposal_description='Includes: Monthly ad campaign, 4 creative variations, reporting dashboard. Objectives: Reach, Traffic, Video views, Conversions. One Language per Campaign (English or Spanish) Client page access is required for this product.',
        sizes='Custom',
        buying_model='Fixed',
        base_rate=None,
        estimated_impressions=True,
        discloses_impressions=False,
        estimated_cpm_for_imps=13.0,
        minimum_spend=600.0,
        minimum_flight_days=(14, 90),
        sla_existing_days=9,
        sla_pending_days=9,
        media_allocation_pct=0.0,
        margin_upper=0.55,
        margin_lower=0.5,
        tech_platform='TikTok Ads Manager',
        wide_orbit_code='TikTok - TikTok Ads',
        national_supported=False,
        notes=NOTE_TIKTOK,
        cannabis_policy='not_allowed',
        political_policy='not_allowed',
    ),
    Product(
        family='Social',
        name='Branded Content | TikTok | Ads',
        short_label='Brand Integration / Engagement',
        proposal_description='Includes: Monthly ad campaign, 4 creative variations, reporting dashboard. Objectives: Reach, Traffic, Video views, Conversions. One Language per Campaign (English or Spanish). NO CLIENT TIKTOK PAGE ONBOARDING REQUIRED.',
        sizes='Custom',
        buying_model='Fixed',
        base_rate=None,
        estimated_impressions=True,
        discloses_impressions=False,
        minimum_spend=600.0,
        minimum_flight_days=(14, 90),
        sla_existing_days=9,
        sla_pending_days=9,
        media_allocation_pct=0.0,
        margin_upper=0.55,
        margin_lower=0.5,
        tech_platform='',
        wide_orbit_code='Branded Content | TikTok | Ads',
        notes=NOTE_TIKTOK,
        cannabis_policy='not_allowed',
        political_policy='not_allowed',
    ),
    Product(
        family='Social',
        name='Creative Labs | TikTok | Ads',
        short_label='Brand Integration / Engagement',
        proposal_description='Includes: Monthly ad campaign, 4 creative variations, reporting dashboard. Objectives: Reach, Traffic, Video views, Conversions. One Language per Campaign (English or Spanish). NO CLIENT TIKTOK PAGE ONBOARDING REQUIRED.',
        sizes='Custom',
        buying_model='Fixed',
        base_rate=None,
        estimated_impressions=True,
        discloses_impressions=False,
        minimum_spend=600.0,
        minimum_flight_days=(14, 90),
        sla_existing_days=9,
        sla_pending_days=9,
        media_allocation_pct=0.0,
        margin_upper=0.55,
        margin_lower=0.5,
        tech_platform='',
        wide_orbit_code='Creative Labs | TikTok | Ads',
        notes=NOTE_TIKTOK,
        cannabis_policy='not_allowed',
        political_policy='not_allowed',
    ),
    Product(
        family='Social',
        name='LinkedIn Ads',
        short_label='Awareness / Conversion / Lead-Gen',
        proposal_description='You can reach a reach a qualified audience based on job title, industry, company name, and more.',
        sizes='Custom',
        buying_model='Fixed',
        base_rate=None,
        estimated_impressions=True,
        discloses_impressions=False,
        estimated_cpm_for_imps=39.0,
        minimum_spend=2000.0,
        minimum_flight_days=(30, 90),
        sla_existing_days=6,
        sla_pending_days=6,
        media_allocation_pct=0.0,
        margin_upper=0.55,
        margin_lower=0.5,
        tech_platform='LinkedIn Campaign Manager',
        wide_orbit_code='Social - LinkedIn Ads',
        national_supported=False,
        notes='*Restricted categories: Prohibited: Tobacco, Drugs, Gambling and Sweepstakes, Health: related to diet and weight loss, are prohibited, Political, Sensitive Events. Restricted: Alcohol, Dating Services, Medical Devices and Medical Treatments, Short-term Loans and Financial Services, Cryptocurrency.',
        cannabis_policy='not_allowed',
        political_policy='restricted',
    ),

    # ===========================================================================
    # EMAIL
    # ===========================================================================
    Product(
        family='Email',
        name='Email Marketing | CPP',
        short_label='Lead Generation / Retargeting',
        proposal_description='No minimum email count',
        sizes='Custom',
        buying_model='CPM',
        base_rate=450.0,
        estimated_impressions=False,
        discloses_impressions=True,
        minimum_spend=450.0,
        minimum_flight_days=(1, 365),
        sla_existing_days=7,
        sla_pending_days=7,
        media_allocation_pct=0.0,
        margin_upper=0.65,
        margin_lower=0.6,
        tech_platform='Site Impact, LeadMe Media',
        wide_orbit_code='Email Marketing | CPP',
        national_supported=False,
        notes=NOTE_EMAIL_STATES,
        cannabis_policy='not_allowed',
    ),
    Product(
        family='Email',
        name='Email Marketing | Display Retargeting',
        short_label='Retargeting / Conversion',
        proposal_description='Minimum email count: 25K',
        sizes='Custom',
        buying_model='CPM',
        base_rate=14.0,
        estimated_impressions=False,
        discloses_impressions=True,
        minimum_spend=375.0,
        minimum_flight_days=(1, 365),
        sla_existing_days=6,
        sla_pending_days=6,
        media_allocation_pct=0.0,
        margin_upper=0.87,
        margin_lower=0.82,
        tech_platform='Site Impact',
        wide_orbit_code='Email Campaigns - Re-targeting',
        national_supported=False,
        notes=NOTE_EMAIL_STATES,
        cannabis_policy='allowed',
    ),
    Product(
        family='Email',
        name='Email Marketing | Client List Match',
        short_label='Lead Generation / Retargeting',
        proposal_description='Include client provided list of recipients to our email blast',
        sizes='Custom',
        buying_model='CPM',
        base_rate=7.0,
        estimated_impressions=False,
        discloses_impressions=True,
        minimum_spend=350.0,
        minimum_flight_days=(1, 365),
        sla_existing_days=4,
        sla_pending_days=4,
        media_allocation_pct=0.0,
        margin_upper=0.87,
        margin_lower=0.82,
        tech_platform='Site Impact, LeadMe Media',
        wide_orbit_code='Email Marketing | Client List Match',
        national_supported=False,
        notes=NOTE_EMAIL_STATES,
        cannabis_policy='not_allowed',
    ),
    Product(
        family='Email',
        name='Email Marketing | Hashed File Onboarding',
        short_label='Lead Generation / Retargeting',
        proposal_description='No minimum email count: 0 - 50,000. More than 50,000 emails a $7 CPM would applied. Formula: Amount of emails * CPM / 1,000',
        sizes='Custom',
        buying_model='CPM',
        base_rate=7.0,
        estimated_impressions=False,
        discloses_impressions=True,
        minimum_spend=350.0,
        minimum_flight_days=(1, 365),
        sla_existing_days=5,
        sla_pending_days=5,
        media_allocation_pct=0.0,
        margin_upper=0.87,
        margin_lower=0.82,
        tech_platform='Site Impact, LeadMe Media',
        wide_orbit_code='Email Marketing | Hashed File Onboarding',
        national_supported=False,
        notes=NOTE_EMAIL_STATES,
        cannabis_policy='not_allowed',
    ),
    Product(
        family='Email',
        name='Email Marketing | Matchback',
        short_label='Attribution & Measurement',
        proposal_description='Deployment data available for 90 days.',
        sizes='Custom',
        buying_model='Fixed',
        base_rate=None,
        estimated_impressions=False,
        discloses_impressions=True,
        minimum_spend=150.0,
        minimum_flight_days=(5, 365),
        sla_existing_days=5,
        sla_pending_days=5,
        media_allocation_pct=0.0,
        margin_upper=0.87,
        margin_lower=0.82,
        tech_platform='Site Impact, LeadMe Media',
        wide_orbit_code='Email Marketing | Matchback',
        national_supported=False,
        notes=NOTE_EMAIL_STATES,
        cannabis_policy='not_allowed',
    ),
    Product(
        family='Email',
        name='Email Marketing | Matchback Analysis',
        short_label='Attribution & Measurement',
        proposal_description='No minimum email count: 0 - 50,000. More than 50,000 emails a $15 CPM would applied. Formula: Amount of emails * CPM / 1,000',
        sizes='Custom',
        buying_model='CPP',
        base_rate=375.0,
        estimated_impressions=False,
        discloses_impressions=True,
        minimum_spend=375.0,
        minimum_flight_days=(1, 365),
        sla_existing_days=5,
        sla_pending_days=5,
        media_allocation_pct=0.0,
        margin_upper=0.87,
        margin_lower=0.82,
        tech_platform='Site Impact, LeadMe Media',
        wide_orbit_code='Email Marketing | Matchback Analysis',
        national_supported=False,
        notes=NOTE_EMAIL_STATES,
        cannabis_policy='not_allowed',
    ),
    Product(
        family='Email',
        name='Email Marketing | Postal Match',
        short_label='Lead Generation / Retargeting',
        proposal_description='No minimum email count: 0 - 50,000. More than 50,000 emails a $15 CPM would applied. Formula: Amount of emails * CPM / 1,000',
        sizes='Custom',
        buying_model='CPP',
        base_rate=30.0,
        estimated_impressions=False,
        discloses_impressions=True,
        minimum_spend=500.0,
        minimum_flight_days=(1, 365),
        sla_existing_days=5,
        sla_pending_days=5,
        media_allocation_pct=0.0,
        margin_upper=0.87,
        margin_lower=0.82,
        tech_platform='Site Impact, LeadMe Media',
        wide_orbit_code='Email Marketing | Postal Match',
        national_supported=False,
        notes=NOTE_EMAIL_STATES,
        cannabis_policy='not_allowed',
    ),

    # ===========================================================================
    # DOOH
    # ===========================================================================
    Product(
        family='DOOH',
        name='Digital Out Of Home',
        short_label='Awareness / Conquesting',
        proposal_description='Publisher specific delivery is not guaranteed. Minimum of 20 screens are required to achieve feasibility. 3+ publisher networks or screen types are recommended.',
        sizes='Custom',
        buying_model='Fixed',
        base_rate=None,
        estimated_impressions=False,
        discloses_impressions=True,
        minimum_spend=2000.0,
        minimum_flight_days=(30, 90),
        sla_existing_days=10,
        sla_pending_days=10,
        media_allocation_pct=0.0,
        margin_upper=0.55,
        margin_lower=0.5,
        tech_platform='TTD',
        wide_orbit_code='Digital Out Of Home',
        notes=NOTE_DOOH,
        cannabis_policy='not_allowed',
    ),

    # ===========================================================================
    # SERVICES
    # ===========================================================================
    Product(
        family='Services',
        name='Web Services - Landing Pages',
        short_label='Conversion Support',
        proposal_description='See LP examples: https://entravision-digital.lpages.co/lp/',
        sizes='Custom',
        buying_model='Fixed',
        base_rate=None,
        estimated_impressions=True,
        discloses_impressions=False,
        minimum_spend=99.0,
        minimum_flight_days=(1, 365),
        sla_existing_days=6,
        sla_pending_days=6,
        media_allocation_pct=0.0,
        margin_upper=0.95,
        margin_lower=0.9,
        tech_platform='',
        wide_orbit_code='Web Services - Landing Pages',
        notes='',
        cannabis_policy='not_allowed',
    ),
    Product(
        family='Services',
        name='Web Services - Microsite',
        short_label='Conversion Support',
        proposal_description='Only 2-5 submenus included.',
        sizes='Custom',
        buying_model='Fixed',
        base_rate=None,
        estimated_impressions=True,
        discloses_impressions=False,
        minimum_spend=300.0,
        minimum_flight_days=(1, 365),
        sla_existing_days=13,
        sla_pending_days=13,
        media_allocation_pct=0.0,
        margin_upper=0.95,
        margin_lower=0.9,
        tech_platform='',
        wide_orbit_code='Web Services - Microsite',
        notes='',
        cannabis_policy='not_allowed',
    ),
    Product(
        family='Services',
        name='Creative Services',
        short_label='Conversion Support',
        proposal_description='Creative Services',
        sizes='Custom',
        buying_model='Fixed',
        base_rate=None,
        estimated_impressions=False,
        discloses_impressions=True,
        minimum_spend=0.0,
        minimum_flight_days=(1, 365),
        sla_existing_days=5,
        sla_pending_days=5,
        media_allocation_pct=0.0,
        margin_upper=1.0,
        margin_lower=1.0,
        tech_platform='',
        wide_orbit_code='Creative Services',
        notes='',
        cannabis_policy='allowed',
    ),

    # ===========================================================================
    # MEASUREMENT
    # ===========================================================================
    Product(
        family='Measurement',
        name='Measurement Study',
        short_label='Attribution & Measurement',
        proposal_description='Works only with programmatic media (OTT, OLV, Display, Audio, DOOH)',
        sizes='Custom',
        buying_model='Fixed',
        base_rate=None,
        estimated_impressions=True,
        discloses_impressions=False,
        minimum_spend=0.0,
        minimum_flight_days=(30, 90),
        sla_existing_days=8,
        sla_pending_days=8,
        media_allocation_pct=0.0,
        margin_upper=0.55,
        margin_lower=0.5,
        tech_platform='',
        wide_orbit_code='Measurement Study',
        notes='',
        cannabis_policy='not_allowed',
    ),
    Product(
        family='Measurement',
        name='Call Tracking Numbers',
        short_label='Attribution & Measurement',
        proposal_description='Works best for products that offer direct attribution, e.g. SEM, Social, YouTube, Retargeting Display',
        sizes='Custom',
        buying_model='Fixed',
        base_rate=None,
        estimated_impressions=True,
        discloses_impressions=False,
        minimum_spend=10.0,
        minimum_flight_days=(30, 365),
        sla_existing_days=2,
        sla_pending_days=2,
        media_allocation_pct=0.0,
        margin_upper=1.0,
        margin_lower=1.0,
        tech_platform='',
        wide_orbit_code='Call Tracking Numbers',
        notes='',
        cannabis_policy='not_allowed',
    ),

    # ===========================================================================
    # SPONSORSHIP DISPLAY
    # ===========================================================================
    Product(
        family='Sponsorship Display',
        name='Sponsorship - CW Video Sponsorship',
        short_label='Awareness / Sponsorship',
        proposal_description='Available for McAllen Market only.',
        sizes='Custom',
        buying_model='Fixed',
        base_rate=None,
        estimated_impressions=True,
        discloses_impressions=False,
        minimum_spend=0.0,
        minimum_flight_days=(30, 90),
        sla_existing_days=5,
        sla_pending_days=5,
        media_allocation_pct=0.0,
        margin_upper=1.0,
        margin_lower=1.0,
        tech_platform='',
        wide_orbit_code='Sponsorship - CW Video Sponsorship',
        notes='',
        cannabis_policy='not_allowed',
    ),
    Product(
        family='Sponsorship Display',
        name='Sponsorship - NBC Sports Stream',
        short_label='Awareness / Sponsorship',
        proposal_description='Available for Palm Springs Only',
        sizes='Custom',
        buying_model='Fixed',
        base_rate=None,
        estimated_impressions=True,
        discloses_impressions=False,
        minimum_spend=0.0,
        minimum_flight_days=(30, 90),
        sla_existing_days=5,
        sla_pending_days=5,
        media_allocation_pct=0.0,
        margin_upper=1.0,
        margin_lower=1.0,
        tech_platform='',
        wide_orbit_code='Sponsorship - NBC Sports Stream',
        notes='',
        cannabis_policy='not_allowed',
    ),
    Product(
        family='Sponsorship Display',
        name='Sponsorship - Services Sponsorships',
        short_label='Awareness / Sponsorship',
        proposal_description='Sponsorships for our Noticias Ya pages Available for McAllen Market and Palm Springs Only',
        sizes='Custom',
        buying_model='Fixed',
        base_rate=None,
        estimated_impressions=True,
        discloses_impressions=False,
        minimum_spend=0.0,
        minimum_flight_days=(30, 90),
        sla_existing_days=5,
        sla_pending_days=5,
        media_allocation_pct=0.0,
        margin_upper=1.0,
        margin_lower=1.0,
        tech_platform='',
        wide_orbit_code='Sponsorship - Services Sponsorships',
        notes='',
        cannabis_policy='not_allowed',
    ),
    Product(
        family='Sponsorship Display',
        name='Sponsorship - Fox Sports Go Video Sponsorship',
        short_label='Awareness / Sponsorship',
        proposal_description='Available for KFXV, KCBA and KXOF only',
        sizes='Custom',
        buying_model='Fixed',
        base_rate=None,
        estimated_impressions=True,
        discloses_impressions=False,
        minimum_spend=0.0,
        minimum_flight_days=(30, 90),
        sla_existing_days=5,
        sla_pending_days=5,
        media_allocation_pct=0.0,
        margin_upper=1.0,
        margin_lower=1.0,
        tech_platform='',
        wide_orbit_code='Sponsorship - Fox Sports Go Video Sponsorship',
        notes='',
        cannabis_policy='not_allowed',
    ),

]



# ---------------------------------------------------------------------------
# Admin rate overrides
#
# The canonical CATALOG list above stays untouched (it's the AdFlo source of
# truth); overrides are layered on top at read time via by_name()/by_family()/
# effective_catalog(), and persisted to a small JSON file so they survive
# server restarts.
# ---------------------------------------------------------------------------

_OVERRIDABLE_FIELDS = ("base_rate", "minimum_spend", "estimated_cpm_for_imps")
_RATE_OVERRIDES_PATH = Path(__file__).resolve().parent / "data" / "rate_overrides.json"


def load_rate_overrides() -> dict:
    """Return {product_name: {field: value, ...}} from disk, or {} if none saved."""
    if not _RATE_OVERRIDES_PATH.exists():
        return {}
    try:
        return json.loads(_RATE_OVERRIDES_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_rate_overrides(overrides: dict) -> None:
    """
    Persist {product_name: {field: value, ...}} to disk. Only known catalog
    (built-in or custom) product names and overridable fields are kept —
    anything else is dropped silently so a bad admin payload can't corrupt
    the store.
    """
    valid_names = {p.name for p in CATALOG} | {p.name for p in load_custom_products()}
    cleaned: dict = {}
    for name, fields_ in (overrides or {}).items():
        if name not in valid_names or not isinstance(fields_, dict):
            continue
        kept = {k: v for k, v in fields_.items() if k in _OVERRIDABLE_FIELDS and v is not None}
        if kept:
            cleaned[name] = kept
    _RATE_OVERRIDES_PATH.parent.mkdir(parents=True, exist_ok=True)
    _RATE_OVERRIDES_PATH.write_text(json.dumps(cleaned, indent=2), encoding="utf-8")


def clear_rate_override(product_name: str) -> None:
    """Remove a single product's override (revert to catalog default)."""
    overrides = load_rate_overrides()
    overrides.pop(product_name, None)
    save_rate_overrides(overrides)


def _apply_override(p: Product) -> Product:
    """Return `p` unchanged, or a copy with admin-overridden fields applied."""
    overrides = load_rate_overrides()
    override = overrides.get(p.name)
    if not override:
        return p
    return replace(p, **{k: v for k, v in override.items() if k in _OVERRIDABLE_FIELDS})


# ---------------------------------------------------------------------------
# Admin custom products
#
# Lets the admin add brand-new catalog products from the /admin UI without a
# code change or restart — persisted separately from the built-in CATALOG
# (which stays a pure reflection of the rate card) so the two sources never
# get confused. Custom products participate in by_name()/by_family()/
# families()/effective_catalog() exactly like built-in ones, including rate
# overrides.
# ---------------------------------------------------------------------------

_CUSTOM_PRODUCTS_PATH = Path(__file__).resolve().parent / "data" / "custom_products.json"


def load_custom_products() -> list[Product]:
    """Return the admin-added products from disk, or [] if none saved."""
    if not _CUSTOM_PRODUCTS_PATH.exists():
        return []
    try:
        raw = json.loads(_CUSTOM_PRODUCTS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []
    products = []
    for d in raw:
        d = dict(d)
        if isinstance(d.get("minimum_flight_days"), list):
            d["minimum_flight_days"] = tuple(d["minimum_flight_days"])
        try:
            products.append(Product(**d))
        except TypeError:
            continue  # a hand-edited/corrupt entry — skip rather than crash the whole catalog
    return products


def _save_custom_products(products: list[Product]) -> None:
    _CUSTOM_PRODUCTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CUSTOM_PRODUCTS_PATH.write_text(
        json.dumps([asdict(p) for p in products], indent=2), encoding="utf-8"
    )


def add_custom_product(fields: dict) -> Product:
    """
    Validate and persist a new admin-added product. Raises ValueError with a
    user-facing message on any problem (missing required field, bad
    buying_model, duplicate name) — the caller (the admin API endpoint)
    surfaces that message directly to the UI.
    """
    name = (fields.get("name") or "").strip()
    family = (fields.get("family") or "").strip()
    buying_model = (fields.get("buying_model") or "").strip()
    if not name:
        raise ValueError("Product name is required.")
    if not family:
        raise ValueError("Family is required.")
    if buying_model not in ("CPM", "CPP", "Fixed"):
        raise ValueError("buying_model must be one of CPM, CPP, or Fixed.")

    existing_names = {p.name for p in CATALOG} | {p.name for p in load_custom_products()}
    if name in existing_names:
        raise ValueError(f"A product named '{name}' already exists.")

    flight_days = fields.get("minimum_flight_days") or (7, 90)
    product = Product(
        family=family,
        name=name,
        short_label=(fields.get("short_label") or "Custom Product").strip(),
        proposal_description=fields.get("proposal_description") or "",
        sizes=fields.get("sizes") or "Custom",
        buying_model=buying_model,
        base_rate=fields.get("base_rate"),
        estimated_impressions=bool(fields.get("estimated_impressions", False)),
        discloses_impressions=bool(fields.get("discloses_impressions", True)),
        minimum_spend=float(fields.get("minimum_spend") or 0.0),
        minimum_flight_days=tuple(flight_days),
        sla_existing_days=int(fields.get("sla_existing_days") or 5),
        sla_pending_days=int(fields.get("sla_pending_days") or 5),
        media_allocation_pct=float(fields.get("media_allocation_pct") or 0.0),
        margin_upper=float(fields.get("margin_upper") or 0.5),
        margin_lower=float(fields.get("margin_lower") or 0.3),
        tech_platform=fields.get("tech_platform") or "",
        wide_orbit_code=fields.get("wide_orbit_code") or name,
        national_supported=bool(fields.get("national_supported", True)),
        notes=fields.get("notes") or "",
        estimated_cpm_for_imps=fields.get("estimated_cpm_for_imps"),
        hispanic_targeting_forced=fields.get("hispanic_targeting_forced"),
        cannabis_policy=fields.get("cannabis_policy") or "not_allowed",
        political_policy=fields.get("political_policy") or "allowed",
    )

    custom = load_custom_products()
    custom.append(product)
    _save_custom_products(custom)
    return product


def delete_custom_product(name: str) -> bool:
    """Remove a custom product by name. Returns False if it wasn't found (or is a built-in)."""
    custom = load_custom_products()
    filtered = [p for p in custom if p.name != name]
    if len(filtered) == len(custom):
        return False
    _save_custom_products(filtered)
    clear_rate_override(name)  # drop any override that pointed at it
    return True


def effective_catalog() -> list[Product]:
    """Built-in + custom products, with any admin rate overrides applied — for listing/admin views."""
    all_products = list(CATALOG) + load_custom_products()
    overrides = load_rate_overrides()
    if not overrides:
        return all_products
    return [_apply_override(p) for p in all_products]


def by_name(name: str) -> Optional[Product]:
    """Find a product (built-in or custom) by its L2 name, with any admin rate override applied."""
    p = next((prod for prod in CATALOG if prod.name == name), None)
    if p is None:
        p = next((prod for prod in load_custom_products() if prod.name == name), None)
    if p is None:
        return None
    return _apply_override(p)


def by_family(family: str) -> list[Product]:
    """All products (built-in or custom) in a Family, with any admin rate overrides applied."""
    all_products = list(CATALOG) + load_custom_products()
    return [_apply_override(p) for p in all_products if p.family == family]


def families() -> list[str]:
    """Distinct families in catalog order, including any new family a custom product introduces."""
    seen, out = set(), []
    for p in list(CATALOG) + load_custom_products():
        if p.family not in seen:
            seen.add(p.family)
            out.append(p.family)
    return out


def to_dict_list() -> list[dict]:
    """Serialize for JSON / web use."""
    return [asdict(p) for p in CATALOG]


if __name__ == "__main__":
    print(f"Catalog has {len(CATALOG)} products across {len(families())} families.")
    for fam in families():
        prods = by_family(fam)
        print(f"  {fam}: {len(prods)} product(s)")
