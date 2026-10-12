"""
Canonical AdFlo product catalog.

Rebuilt 2026-09 from `LOCAL DIGITAL RATE CARD 2026 (3).xlsx` (sheet "2026")
— 85 products across 12 families, replacing the prior 71-product catalog,
which had drifted into a mix of two different rate cards. THIS file is
built ONLY from that sheet's column B ("Product (Colloquial)"); column C
("Product Wide Orbit Name") is intentionally not used for naming — it's
carried into `wide_orbit_code` only, per explicit instruction.

Field provenance from the rate card:
  base_rate / buying_model <- "Rate Type" + "Target NET Price". A numeric
                               price with rate_type CPM/CPP maps directly.
                               A non-numeric price ("Starts at $X", "See
                               Sales Planning", "Custom Pricing") maps to
                               buying_model="Fixed", base_rate=None — the
                               dollar figure (when present) becomes
                               minimum_spend instead of a per-unit rate.
  minimum_spend             <- "Minimum NET Spend per Line", forward-filled
                               within a contiguous same-"Wide Orbit Name"
                               run when a row doesn't restate it (e.g. a
                               Netflix :30s variant sharing its :15s
                               sibling's line minimum).
  minimum_flight_days       <- parsed from "Suggested Minimum Duration"
  sla_data_days /
  sla_creative_days /
  sla_activate_days /
  sla_total_days            <- the rate card's own "SLA for data
                                submission" / "SLA/Best Practices for
                                creatives" / "SLA/Best Practices to
                                activate campaign" / "Total SLA to go Live"
                                columns, each parsed to the UPPER bound of
                                its business-day range. These replace the
                                prior catalog's single existing/pending
                                sla_*_days pair (this rate card doesn't
                                distinguish existing- vs. pending-advertiser
                                SLA, and nothing else in the app read those
                                two fields) — feeds the Activation Timeline
                                feature. A row with no SLA of its own
                                inherits the nearest preceding non-blank
                                value within the same family section (the
                                rate card states it once per family/group,
                                not per line).
  notes / cannabis_policy /
  political_policy          <- "Restricted industries/services/categories"
                                text, or a reused curated NOTE_* constant
                                below (kept from the prior catalog rebuild)
                                for platforms whose compliance text is
                                already well-documented there (Meta,
                                TikTok, Netflix, Google/YouTube, Email).
  estimated_cpm_for_imps    <- for the 3 Meta/Facebook & Instagram Ads
                                objective variants specifically: the rate
                                card has one general CPP line with no
                                per-objective breakdown, so these 3 use an
                                explicitly-given estimated CPM per
                                objective (Awareness $13, Traffic/
                                Conversion $18, Lead Gen/Calls $35) purely
                                for the impressions estimate — same
                                mechanism as any other Fixed-model product.
  tech_platform             <- left blank; not broken out per-product in
                                this rate card.

Some fields the rate card doesn't give per-product were approximated or
left at sensible defaults — spot-check margins, flight windows, and
Fixed-model products with no dollar figure at all (pure "See Sales
Planning" lines) before relying on them for margin-sensitive approvals.
THIS LIST IS ONLY THE SEED. Once the app has run, the `products` table is
the one and only product list — edit, add, duplicate and delete products in
Admin -> Product Catalog; nothing here is consulted again except to fill an empty
table and as the fail-safe when the database can't be read.
"""

import logging
import math
import re
import time
from dataclasses import dataclass, field, asdict, replace
from typing import Optional, Union

from app.db import get_connection


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
    # Activation-timeline SLA breakdown (business days, upper bound of the
    # rate card's stated range) — data submission / creative / campaign
    # activation / total to go live. None where the rate card gives no SLA
    # for that product (e.g. custom-quote-only lines).
    sla_data_days: Optional[int]
    sla_creative_days: Optional[int]
    sla_activate_days: Optional[int]
    sla_total_days: Optional[int]
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
    # True for the Services/Measurement-style fixed-price extras (landing
    # pages, call tracking, brand lift studies, ...) that aren't really
    # "products" a campaign is built around — no suggested budget, picked
    # via Step 04's separate Add-Ons module instead of the main product
    # picker/recommender, and rolled into the proposal's ADD-ONS / ONE-TIME
    # FEES export block rather than the line-items table.
    is_addon: bool = False



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
        name='Search - SEM',
        short_label='Click-To-Call / Website Conversion',
        proposal_description='Text ads on Google Search that appear when people look for what you offer, driving calls and website visits. Variants (contact planning for exact tier): Search - AdWords - SEM: $1000 CPP; Search - SEM PRO: $5000 CPP.',
        sizes='N/A (text/responsive ads)',
        buying_model='CPP',
        base_rate=1000.0,
        estimated_impressions=False,
        discloses_impressions=True,
        minimum_spend=1000.0,
        minimum_flight_days=[30, 90],
        sla_data_days=1,
        sla_creative_days=None,
        sla_activate_days=6,
        sla_total_days=7,
        media_allocation_pct=0.0,
        margin_upper=0.4,
        margin_lower=0.3,
        tech_platform='',
        wide_orbit_code='Search - SEM',
        notes=NOTE_GOOGLE_VERTICALS,
        estimated_cpm_for_imps=None,
        hispanic_targeting_forced=None,
        cannabis_policy='not_allowed',
        political_policy='not_allowed',
    ),
    # ===========================================================================
    # ONLINE VIDEO
    # ===========================================================================
    Product(
        family='Online Video',
        name='Video - Pre-roll (OLV)',
        short_label='Awareness / Retargeting',
        proposal_description='Video messages shown before video content selected by the user that can last between 15 and 30 seconds. - Geo Only (DMA) + Hispanic. Device (mobile and desktop) Variants (contact planning for exact tier): Video - Pre-roll + 1 additional targeting layer: $24 CPM; Video - Pre-roll + 2 additional targeting layers: $25 CPM.',
        sizes=':15s/:30s, 16:9',
        buying_model='CPM',
        base_rate=23.0,
        estimated_impressions=False,
        discloses_impressions=True,
        minimum_spend=750.0,
        minimum_flight_days=[30, 90],
        sla_data_days=1,
        sla_creative_days=3,
        sla_activate_days=3,
        sla_total_days=7,
        media_allocation_pct=0.0,
        margin_upper=0.4,
        margin_lower=0.3,
        tech_platform='',
        wide_orbit_code='Video - Pre-roll (OLV)',
        notes='',
        estimated_cpm_for_imps=None,
        hispanic_targeting_forced=None,
        cannabis_policy='not_allowed',
        political_policy='not_allowed',
    ),
    Product(
        family='Online Video',
        name='YouTube Ads - Skippable',
        short_label='Awareness / Retargeting',
        proposal_description='Skippable in-stream and video discovery ads on YouTube; viewers can skip after five seconds. Targeting availability: Demographics, Interests, Video Remarketing, Topics, Keywords (keyword targeting option depends on your ad format). 6 creatives max. Each language target will need separate campaigns (ie WO line IDs)',
        sizes=':15s/:30s (skippable), 16:9',
        buying_model='Fixed',
        base_rate=None,
        estimated_impressions=True,
        discloses_impressions=False,
        minimum_spend=500.0,
        minimum_flight_days=[30, 90],
        sla_data_days=1,
        sla_creative_days=3,
        sla_activate_days=7,
        sla_total_days=9,
        media_allocation_pct=0.0,
        margin_upper=0.4,
        margin_lower=0.3,
        tech_platform='',
        wide_orbit_code='YouTube Ads - Skippable',
        notes=NOTE_GOOGLE_VERTICALS,
        estimated_cpm_for_imps=15.0,
        hispanic_targeting_forced=None,
        cannabis_policy='not_allowed',
        political_policy='not_allowed',
    ),
    Product(
        family='Online Video',
        name='YouTube Ads - Non-Skippable',
        short_label='Awareness / Retargeting',
        proposal_description='Non-skippable in-stream ads (up to :15s) and bumper ads (:6s) on YouTube that play in full before the video. Targeting availability: Demographics, Interests, Video Remarketing, Topics, Keywords (keyword targeting option depends on your ad format). 6 creatives max. Each language target will need separate campaigns (ie WO line IDs)',
        sizes=':15s non-skippable / :6s bumper, 16:9',
        buying_model='Fixed',
        base_rate=None,
        estimated_impressions=True,
        discloses_impressions=False,
        minimum_spend=500.0,
        minimum_flight_days=[30, 90],
        sla_data_days=1,
        sla_creative_days=3,
        sla_activate_days=7,
        sla_total_days=9,
        media_allocation_pct=0.0,
        margin_upper=0.4,
        margin_lower=0.3,
        tech_platform='',
        wide_orbit_code='YouTube Ads - Non-Skippable',
        notes=NOTE_GOOGLE_VERTICALS,
        estimated_cpm_for_imps=15.0,
        hispanic_targeting_forced=None,
        cannabis_policy='not_allowed',
        political_policy='not_allowed',
    ),
    # ===========================================================================
    # DISPLAY
    # ===========================================================================
    Product(
        family='Display',
        name='eDigital Network Display - Standard IAB',
        short_label='Awareness / Retargeting',
        proposal_description='Entravision hispanic display network. Access to 1st party data and premium quality inventory. Standard IAB units Desktop and/or Mobile. Variants (contact planning for exact tier): Geo targeting only + Hispanic: $9 CPM; Geo targeting only + Hispanic + 1 additional targeting layer: $10 CPM; Geo targeting only + Hispanic + 2 additional targeting layer: $11 CPM.',
        sizes='300x250, 728x90, 320x50, 160x600',
        buying_model='CPM',
        base_rate=9.0,
        estimated_impressions=False,
        discloses_impressions=True,
        minimum_spend=750.0,
        minimum_flight_days=[30, 90],
        sla_data_days=1,
        sla_creative_days=3,
        sla_activate_days=3,
        sla_total_days=8,
        media_allocation_pct=0.0,
        margin_upper=0.4,
        margin_lower=0.3,
        tech_platform='',
        wide_orbit_code='eDigital Network Display - Standard IAB',
        notes='',
        estimated_cpm_for_imps=None,
        hispanic_targeting_forced=None,
        cannabis_policy='not_allowed',
        political_policy='not_allowed',
    ),
    Product(
        family='Display',
        name='Display - Geo Fence',
        short_label='Awareness / Retargeting',
        proposal_description='Serve your banner ads within a virtual perimeter around any address, zip code, event, neighborhood, etc. No demo targeting available. No foot traffic attribution',
        sizes='300x250, 320x50, 728x90',
        buying_model='CPM',
        base_rate=11.0,
        estimated_impressions=False,
        discloses_impressions=True,
        minimum_spend=1000.0,
        minimum_flight_days=[30, 90],
        sla_data_days=1,
        sla_creative_days=2,
        sla_activate_days=3,
        sla_total_days=6,
        media_allocation_pct=0.0,
        margin_upper=0.4,
        margin_lower=0.3,
        tech_platform='',
        wide_orbit_code='Display - Geo Fence',
        notes='',
        estimated_cpm_for_imps=None,
        hispanic_targeting_forced=None,
        cannabis_policy='not_allowed',
        political_policy='allowed',
    ),
    # ===========================================================================
    # ENTRAVISION PLUS
    # ===========================================================================
    Product(
        family='Entravision Plus',
        name='Entravision Plus CTV/OTT - English Content',
        short_label='Awareness',
        proposal_description="Includes One Demo or Behavioral Targeting Segment. A connected TV (CTV) is any internet-connected television that allows viewers to watch television content. OTT (Over the Top) refers to the content that can be streamed with an internet connection on devices like CTV's, mobiles, and tablets. If required to exclude see Sales Planning. Premium content partners. Additional behavioral targeting segments beyond the first: + $2 CPM 15 and 30 second creatives only. 60 second+ creatives are not allowed. Specific Publisher delivery is not guaranteed",
        sizes=':15s/:30s, 16:9',
        buying_model='CPM',
        base_rate=38.0,
        estimated_impressions=False,
        discloses_impressions=True,
        minimum_spend=1000.0,
        minimum_flight_days=[30, 90],
        sla_data_days=1,
        sla_creative_days=3,
        sla_activate_days=3,
        sla_total_days=7,
        media_allocation_pct=0.0,
        margin_upper=0.4,
        margin_lower=0.3,
        tech_platform='',
        wide_orbit_code='OTT - Connected TV',
        notes='',
        estimated_cpm_for_imps=None,
        hispanic_targeting_forced=None,
        cannabis_policy='not_allowed',
        political_policy='restricted',
    ),
    Product(
        family='Entravision Plus',
        name='Entravision Plus CTV/OTT- Spanish Content only',
        short_label='Awareness',
        proposal_description="Spanish Content Only - One Demo or behavioral targeting segment is included in rate, but must be approved by Sales Planning for inventory availability. A connected TV (CTV) is any internet-connected television that allows viewers to watch television content. OTT (Over the Top) refers to the content that can be streamed with an internet connection on devices like CTV's, mobiles, and tablets. Additional behavioral targeting segments beyond the first: + $2 CPM 15 and 30 second creatives only. 60 second+ creatives are not allowed. Specific Publisher delivery is not guaranteed",
        sizes=':15s/:30s, 16:9',
        buying_model='CPM',
        base_rate=39.0,
        estimated_impressions=False,
        discloses_impressions=True,
        minimum_spend=1000.0,
        minimum_flight_days=[7, 90],
        sla_data_days=None,
        sla_creative_days=None,
        sla_activate_days=None,
        sla_total_days=None,
        media_allocation_pct=0.0,
        margin_upper=0.4,
        margin_lower=0.3,
        tech_platform='',
        wide_orbit_code='OTT - Connected TV',
        notes='',
        estimated_cpm_for_imps=None,
        hispanic_targeting_forced=None,
        cannabis_policy='not_allowed',
        political_policy='allowed',
    ),
    Product(
        family='Entravision Plus',
        name='Entravision Plus - Hispanics CTV/OTT',
        short_label='Awareness',
        proposal_description="Hispanic Audience Targeting included. One demo or behavioral targeting segment is included in rate, but must be approved by Sales Planning for inventory availability. A connected TV (CTV) is any internet-connected television that allows viewers to watch television content. OTT (Over the Top) refers to the content that can be streamed with an internet connection on devices like CTV's, mobiles, and tablets. Additional behavioral targeting segments beyond the first: + $2 CPM 15 and 30 second creatives only. 60 second+ creatives are not allowed. Specific Publisher delivery is not guaranteed",
        sizes=':15s/:30s, 16:9',
        buying_model='CPM',
        base_rate=33.0,
        estimated_impressions=False,
        discloses_impressions=True,
        minimum_spend=1000.0,
        minimum_flight_days=[7, 90],
        sla_data_days=1,
        sla_creative_days=3,
        sla_activate_days=3,
        sla_total_days=7,
        media_allocation_pct=0.0,
        margin_upper=0.4,
        margin_lower=0.3,
        tech_platform='',
        wide_orbit_code='OTT - Connected TV',
        notes='',
        estimated_cpm_for_imps=None,
        hispanic_targeting_forced=None,
        cannabis_policy='not_allowed',
        political_policy='allowed',
    ),
    Product(
        family='Entravision Plus',
        name='Entravision Plus - CTV/OTT Reach',
        short_label='Awareness',
        proposal_description="One demo or behavioral targeting segment is included in rate, but must be approved by Sales Planning for inventory availability. A connected TV (CTV) is any internet-connected television that allows viewers to watch television content. OTT (Over the Top) refers to the content that can be streamed with an internet connection on devices like CTV's, mobiles, and tablets. All content partners. Additional behavioral targeting segments beyond the first: + $2 CPM 15 and 30 second creatives only. 60 second+ creatives are not allowed. Specific Publisher delivery is not guaranteed",
        sizes=':15s/:30s, 16:9',
        buying_model='CPM',
        base_rate=32.0,
        estimated_impressions=False,
        discloses_impressions=True,
        minimum_spend=1000.0,
        minimum_flight_days=[7, 90],
        sla_data_days=None,
        sla_creative_days=None,
        sla_activate_days=None,
        sla_total_days=None,
        media_allocation_pct=0.0,
        margin_upper=0.4,
        margin_lower=0.3,
        tech_platform='',
        wide_orbit_code='OTT - Connected TV',
        notes='',
        estimated_cpm_for_imps=None,
        hispanic_targeting_forced=None,
        cannabis_policy='not_allowed',
        political_policy='allowed',
    ),
    Product(
        family='Entravision Plus',
        name='Entravision Plus - Online Video (OLV) Blend Reach',
        short_label='Awareness',
        proposal_description='Includes One Demo or Behavioral Targeting Segment. Optional to substitute it with Hispanic Targeting. Appears across all devices (CTVs, Mobile, Tablets, Desktops) and all Online Video OLV content. Additional behavioral targeting segments beyond the first: + $2 CPM 15 and 30 second creatives only. 60 second+ creatives are not allowed. Specific Publisher delivery is not guaranteed',
        sizes=':15s/:30s, 16:9',
        buying_model='CPM',
        base_rate=29.0,
        estimated_impressions=False,
        discloses_impressions=True,
        minimum_spend=1000.0,
        minimum_flight_days=[30, 90],
        sla_data_days=None,
        sla_creative_days=None,
        sla_activate_days=None,
        sla_total_days=None,
        media_allocation_pct=0.0,
        margin_upper=0.4,
        margin_lower=0.3,
        tech_platform='',
        wide_orbit_code='CTV/Pre-Roll Video Blend',
        notes='',
        estimated_cpm_for_imps=None,
        hispanic_targeting_forced=None,
        cannabis_policy='not_allowed',
        political_policy='restricted',
    ),
    Product(
        family='Entravision Plus',
        name='Entravision Plus - VIX 360 (Includes VIX + UnivisionNow + Univision.com / Univision Apps)',
        short_label='Awareness',
        proposal_description='A premium video ecosystem spanning all of TelevisaUnivision (VIX 360) on CTV devices, FAST platforms and social video sites.',
        sizes=':15s/:30s, 16:9',
        buying_model='CPM',
        base_rate=39.0,
        estimated_impressions=False,
        discloses_impressions=True,
        minimum_spend=1000.0,
        minimum_flight_days=[30, 90],
        sla_data_days=1,
        sla_creative_days=3,
        sla_activate_days=3,
        sla_total_days=7,
        media_allocation_pct=0.0,
        margin_upper=0.4,
        margin_lower=0.3,
        tech_platform='',
        wide_orbit_code='OTT - Vix',
        notes='',
        estimated_cpm_for_imps=None,
        hispanic_targeting_forced=None,
        cannabis_policy='not_allowed',
        political_policy='restricted',
    ),
    Product(
        family='Entravision Plus',
        name='Entravision Plus - Roku Ads (Includes The Roku Channel and the popular Espacio Latino Hub)',
        short_label='Awareness',
        proposal_description="A premium video ecosystem spanning all of Roku's owned and operated channels and apps on CTV and mobile devices. In Market capabilities available - check with planning",
        sizes=':15s/:30s, 16:9',
        buying_model='CPM',
        base_rate=15.0,
        estimated_impressions=False,
        discloses_impressions=True,
        minimum_spend=1000.0,
        minimum_flight_days=[30, 90],
        sla_data_days=1,
        sla_creative_days=3,
        sla_activate_days=3,
        sla_total_days=7,
        media_allocation_pct=0.0,
        margin_upper=0.4,
        margin_lower=0.3,
        tech_platform='',
        wide_orbit_code='OTT - Roku',
        notes='',
        estimated_cpm_for_imps=None,
        hispanic_targeting_forced=None,
        cannabis_policy='not_allowed',
        political_policy='restricted',
    ),
    Product(
        family='Entravision Plus',
        name='Entravision Plus - Amazon Prime Video',
        short_label='Awareness',
        proposal_description="Premium first-party targeting on the most-watched streaming platform in the US. Best for brand awareness campaigns targeting Hispanic households and in-market audiences using Amazon's actual purchase data — not third-party estimates. Audience types: In-Market, Lifestyle, Interest, Demographic household segments, Lookalike, and Device. All content. Audience targeting requires a rate increase of + $5 CPM. Additional segments beyond the first have no additional cost so long these are Amazon FIrst Party audiences. 15 and 30 second creatives only. 60 second+ creatives are not allowed.",
        sizes=':15s/:30s, 16:9',
        buying_model='CPM',
        base_rate=50.0,
        estimated_impressions=False,
        discloses_impressions=True,
        minimum_spend=1000.0,
        minimum_flight_days=[30, 90],
        sla_data_days=1,
        sla_creative_days=3,
        sla_activate_days=3,
        sla_total_days=7,
        media_allocation_pct=0.0,
        margin_upper=0.4,
        margin_lower=0.3,
        tech_platform='',
        wide_orbit_code='Entravision Plus CTV | Amazon Prime Video',
        notes='',
        estimated_cpm_for_imps=None,
        hispanic_targeting_forced=None,
        cannabis_policy='not_allowed',
        political_policy='not_allowed',
    ),
    Product(
        family='Entravision Plus',
        name='Netflix - Run Of Network Untargeted :15s Ads',
        short_label='Awareness',
        proposal_description="Ads served in Netflix's premium ad network. Leverages Netflix's ad-supported subscription plan, which includes commercials before and during select TV shows and movies. Served across TVs and mobile streaming devices. Premium ad environment that ensures lower-than-average ad saturation, aiding with higher ad attention levels. General market targeting only.",
        sizes=':15s, 16:9',
        buying_model='CPM',
        base_rate=44.0,
        estimated_impressions=False,
        discloses_impressions=True,
        minimum_spend=2500.0,
        minimum_flight_days=[210, 630],
        sla_data_days=2,
        sla_creative_days=5,
        sla_activate_days=3,
        sla_total_days=10,
        media_allocation_pct=0.0,
        margin_upper=0.4,
        margin_lower=0.3,
        tech_platform='',
        wide_orbit_code='OTT - Netflix',
        notes=NOTE_NETFLIX_FULL,
        estimated_cpm_for_imps=None,
        hispanic_targeting_forced=None,
        cannabis_policy='not_allowed',
        political_policy='not_allowed',
    ),
    Product(
        family='Entravision Plus',
        name='Netflix - Run Of Network Untargeted :30s Ads',
        short_label='Awareness',
        proposal_description='',
        sizes=':30s, 16:9',
        buying_model='CPM',
        base_rate=54.0,
        estimated_impressions=False,
        discloses_impressions=True,
        minimum_spend=0.0,
        minimum_flight_days=[7, 90],
        sla_data_days=None,
        sla_creative_days=None,
        sla_activate_days=None,
        sla_total_days=None,
        media_allocation_pct=0.0,
        margin_upper=0.4,
        margin_lower=0.3,
        tech_platform='',
        wide_orbit_code='OTT - Netflix',
        notes=NOTE_NETFLIX_FULL,
        estimated_cpm_for_imps=None,
        hispanic_targeting_forced=None,
        cannabis_policy='not_allowed',
        political_policy='allowed',
    ),
    Product(
        family='Entravision Plus',
        name='Netflix - Age Targeted :15s Ads',
        short_label='Awareness',
        proposal_description="Ads served in Netflix's premium ad network. Leverages Netflix's ad-supported subscription plan, which includes commercials before and during select TV shows and movies. Served across TVs and mobile streaming devices. Premium ad environment that ensures lower-than-average ad saturation, aiding with higher ad attention levels. General market with age targeting only.",
        sizes=':15s, 16:9',
        buying_model='CPM',
        base_rate=51.0,
        estimated_impressions=False,
        discloses_impressions=True,
        minimum_spend=0.0,
        minimum_flight_days=[7, 90],
        sla_data_days=None,
        sla_creative_days=None,
        sla_activate_days=None,
        sla_total_days=None,
        media_allocation_pct=0.0,
        margin_upper=0.4,
        margin_lower=0.3,
        tech_platform='',
        wide_orbit_code='OTT - Netflix',
        notes=NOTE_NETFLIX_FULL,
        estimated_cpm_for_imps=None,
        hispanic_targeting_forced=None,
        cannabis_policy='not_allowed',
        political_policy='allowed',
    ),
    Product(
        family='Entravision Plus',
        name='Netflix - Age Targeted :30s Ads',
        short_label='Awareness',
        proposal_description='',
        sizes=':30s, 16:9',
        buying_model='CPM',
        base_rate=63.0,
        estimated_impressions=False,
        discloses_impressions=True,
        minimum_spend=0.0,
        minimum_flight_days=[7, 90],
        sla_data_days=None,
        sla_creative_days=None,
        sla_activate_days=None,
        sla_total_days=None,
        media_allocation_pct=0.0,
        margin_upper=0.4,
        margin_lower=0.3,
        tech_platform='',
        wide_orbit_code='OTT - Netflix',
        notes=NOTE_NETFLIX_FULL,
        estimated_cpm_for_imps=None,
        hispanic_targeting_forced=None,
        cannabis_policy='not_allowed',
        political_policy='allowed',
    ),
    Product(
        family='Entravision Plus',
        name='Netflix - Gender Targeted :15s Ads',
        short_label='Awareness',
        proposal_description="Ads served in Netflix's premium ad network. Leverages Netflix's ad-supported subscription plan, which includes commercials before and during select TV shows and movies. Served across TVs and mobile streaming devices. Premium ad environment that ensures lower-than-average ad saturation, aiding with higher ad attention levels. General market with gender targeting only.",
        sizes=':15s, 16:9',
        buying_model='CPM',
        base_rate=51.0,
        estimated_impressions=False,
        discloses_impressions=True,
        minimum_spend=0.0,
        minimum_flight_days=[7, 90],
        sla_data_days=None,
        sla_creative_days=None,
        sla_activate_days=None,
        sla_total_days=None,
        media_allocation_pct=0.0,
        margin_upper=0.4,
        margin_lower=0.3,
        tech_platform='',
        wide_orbit_code='OTT - Netflix',
        notes=NOTE_NETFLIX_FULL,
        estimated_cpm_for_imps=None,
        hispanic_targeting_forced=None,
        cannabis_policy='not_allowed',
        political_policy='allowed',
    ),
    Product(
        family='Entravision Plus',
        name='Netflix - Gender Targeted :30s Ads',
        short_label='Awareness',
        proposal_description='',
        sizes=':30s, 16:9',
        buying_model='CPM',
        base_rate=63.0,
        estimated_impressions=False,
        discloses_impressions=True,
        minimum_spend=0.0,
        minimum_flight_days=[7, 90],
        sla_data_days=None,
        sla_creative_days=None,
        sla_activate_days=None,
        sla_total_days=None,
        media_allocation_pct=0.0,
        margin_upper=0.4,
        margin_lower=0.3,
        tech_platform='',
        wide_orbit_code='OTT - Netflix',
        notes=NOTE_NETFLIX_FULL,
        estimated_cpm_for_imps=None,
        hispanic_targeting_forced=None,
        cannabis_policy='not_allowed',
        political_policy='allowed',
    ),
    Product(
        family='Entravision Plus',
        name='Netflix - Content Genre Targeted :15s Ads',
        short_label='Awareness',
        proposal_description="Ads served in Netflix's premium ad network. Leverages Netflix's ad-supported subscription plan, which includes commercials before and during select TV shows and movies. Served across TVs and mobile streaming devices. Can target the following genres: Drama, Comedy, Unscripted, Thriller/Horror, or Action",
        sizes=':15s, 16:9',
        buying_model='CPM',
        base_rate=49.0,
        estimated_impressions=False,
        discloses_impressions=True,
        minimum_spend=0.0,
        minimum_flight_days=[7, 90],
        sla_data_days=None,
        sla_creative_days=None,
        sla_activate_days=None,
        sla_total_days=None,
        media_allocation_pct=0.0,
        margin_upper=0.4,
        margin_lower=0.3,
        tech_platform='',
        wide_orbit_code='OTT - Netflix',
        notes=NOTE_NETFLIX_FULL,
        estimated_cpm_for_imps=None,
        hispanic_targeting_forced=None,
        cannabis_policy='not_allowed',
        political_policy='allowed',
    ),
    Product(
        family='Entravision Plus',
        name='Netflix - Content Genre Targeted :30s Ads',
        short_label='Awareness',
        proposal_description='',
        sizes=':30s, 16:9',
        buying_model='CPM',
        base_rate=61.0,
        estimated_impressions=False,
        discloses_impressions=True,
        minimum_spend=0.0,
        minimum_flight_days=[7, 90],
        sla_data_days=None,
        sla_creative_days=None,
        sla_activate_days=None,
        sla_total_days=None,
        media_allocation_pct=0.0,
        margin_upper=0.4,
        margin_lower=0.3,
        tech_platform='',
        wide_orbit_code='OTT - Netflix',
        notes=NOTE_NETFLIX_FULL,
        estimated_cpm_for_imps=None,
        hispanic_targeting_forced=None,
        cannabis_policy='not_allowed',
        political_policy='allowed',
    ),
    Product(
        family='Entravision Plus',
        name='Netflix - Spanish Content :15s Ads',
        short_label='Awareness',
        proposal_description="Ads served in Netflix's premium ad network targeting Spanish Content Programming. Leverages Netflix's ad-supported subscription plan, which includes commercials before and during select TV shows and movies. Served across TVs and mobile streaming devices.",
        sizes=':15s, 16:9',
        buying_model='CPM',
        base_rate=50.0,
        estimated_impressions=False,
        discloses_impressions=True,
        minimum_spend=2500.0,
        minimum_flight_days=[7, 90],
        sla_data_days=4,
        sla_creative_days=None,
        sla_activate_days=4,
        sla_total_days=15,
        media_allocation_pct=0.0,
        margin_upper=0.4,
        margin_lower=0.3,
        tech_platform='',
        wide_orbit_code='OTT - Netflix',
        notes=NOTE_NETFLIX_FULL,
        estimated_cpm_for_imps=None,
        hispanic_targeting_forced=None,
        cannabis_policy='not_allowed',
        political_policy='allowed',
    ),
    Product(
        family='Entravision Plus',
        name='Netflix - Spanish Content :30s Ads',
        short_label='Awareness',
        proposal_description='',
        sizes=':30s, 16:9',
        buying_model='CPM',
        base_rate=60.0,
        estimated_impressions=False,
        discloses_impressions=True,
        minimum_spend=0.0,
        minimum_flight_days=[7, 90],
        sla_data_days=None,
        sla_creative_days=None,
        sla_activate_days=None,
        sla_total_days=None,
        media_allocation_pct=0.0,
        margin_upper=0.4,
        margin_lower=0.3,
        tech_platform='',
        wide_orbit_code='OTT - Netflix',
        notes=NOTE_NETFLIX_FULL,
        estimated_cpm_for_imps=None,
        hispanic_targeting_forced=None,
        cannabis_policy='not_allowed',
        political_policy='allowed',
    ),
    # ===========================================================================
    # AUDIO
    # ===========================================================================
    Product(
        family='Audio',
        name='Audio - EVC Audio Streaming',
        short_label='Awareness',
        proposal_description='Audio Streaming is the digital side of radio. All of our radio properties are also available online and end users can access them via different means: Our Local Radio Websites, Entravision Radio Apps, Third Party Apps, etc. Variants (contact planning for exact tier): Station Specific: $3 CPP; 12 hours Day Parting: $4 CPP; Day Parting Specific (Less than 12 hours): $6 CPP; KLYY Jose Station LA - Spot: $15 CPP; KLYY Jose Station LA - CPM: $14 CPM.',
        sizes=':15s/:30s (audio spot)',
        buying_model='CPP',
        base_rate=3.0,
        estimated_impressions=False,
        discloses_impressions=True,
        minimum_spend=300.0,
        minimum_flight_days=[30, 90],
        sla_data_days=1,
        sla_creative_days=None,
        sla_activate_days=2,
        sla_total_days=None,
        media_allocation_pct=0.0,
        margin_upper=0.4,
        margin_lower=0.3,
        tech_platform='',
        wide_orbit_code='Audio - EVC Audio Streaming',
        notes='',
        estimated_cpm_for_imps=None,
        hispanic_targeting_forced=None,
        cannabis_policy='not_allowed',
        political_policy='allowed',
    ),
    Product(
        family='Audio',
        name='AudioEngage',
        short_label='Awareness',
        proposal_description='Use our mix of Internet radio, podcasts, and streaming channels with enhanced targeting and reporting capabilities to reach more listeners. Variants (contact planning for exact tier): Standard: $13 CPM; Custom Audience: $15 CPM.',
        sizes=':15s/:30s (audio spot)',
        buying_model='CPM',
        base_rate=13.0,
        estimated_impressions=False,
        discloses_impressions=True,
        minimum_spend=500.0,
        minimum_flight_days=[30, 90],
        sla_data_days=1,
        sla_creative_days=None,
        sla_activate_days=3,
        sla_total_days=None,
        media_allocation_pct=0.0,
        margin_upper=0.4,
        margin_lower=0.3,
        tech_platform='',
        wide_orbit_code='AudioEngage',
        notes='',
        estimated_cpm_for_imps=None,
        hispanic_targeting_forced=None,
        cannabis_policy='not_allowed',
        political_policy='allowed',
    ),
    Product(
        family='Audio',
        name='Spotify',
        short_label='Awareness',
        proposal_description="Spotify ads are part of the experience when people use Spotify for free. They're 30 seconds promotional messages that play in between songs. Includes: geo (DMA, city, zipcodes), gender, language & age Each language target will need separate campaigns (ie WO Line IDs)",
        sizes=':15s/:30s (audio spot)',
        buying_model='Fixed',
        base_rate=None,
        estimated_impressions=True,
        discloses_impressions=False,
        minimum_spend=500.0,
        minimum_flight_days=[30, 90],
        sla_data_days=1,
        sla_creative_days=None,
        sla_activate_days=2,
        sla_total_days=3,
        media_allocation_pct=0.0,
        margin_upper=0.4,
        margin_lower=0.3,
        tech_platform='',
        wide_orbit_code='Audio - Spotify Ads',
        notes='Contact Sales Planning for pricing.',
        estimated_cpm_for_imps=36.0,
        hispanic_targeting_forced=None,
        cannabis_policy='not_allowed',
        political_policy='not_allowed',
    ),
    # ===========================================================================
    # BRANDED CONTENT
    # ===========================================================================
    Product(
        family='Branded Content',
        name='Meta Branded Content | Colaborative Ads',
        short_label='Awareness / Retargeting',
        proposal_description="Sponsored or custom content. It will run in: Noticias Ya Page Feed + Facebook/instagram. NO CLIENT ONBOARDING, but Client's FB page & approval for tagging is MANDATORY. Branded Video Production isn't Included, only static images can be produced| Meta | Organic posts only",
        sizes='1:1, 4:5, 9:16',
        buying_model='Fixed',
        base_rate=None,
        estimated_impressions=True,
        discloses_impressions=False,
        minimum_spend=300.0,
        minimum_flight_days=[1, 6],
        sla_data_days=1,
        sla_creative_days=1,
        sla_activate_days=3,
        sla_total_days=8,
        media_allocation_pct=0.0,
        margin_upper=0.4,
        margin_lower=0.3,
        tech_platform='',
        wide_orbit_code='Meta Branded Content | Colaborative Ads',
        notes='',
        estimated_cpm_for_imps=15.0,
        hispanic_targeting_forced=None,
        cannabis_policy='not_allowed',
        political_policy='allowed',
    ),
    Product(
        family='Branded Content',
        name='Meta Branded Content | Sponsorship Ads',
        short_label='Lead Generation',
        proposal_description="Single objective based campaign, either Awareness, Traffic, Engagement, Leads or Sales. Client Creatives( Images and/or Videos) OR Entravision Creatives (Images and/or Videos). It will run on: Facebook and/or InstagramNetwork. NO CLIENT ONBOARDING, but Client's Facebook/IG page & approval for tagging is RECOMMENDED. Branded Video Production is included at a +$1000 minimum for one day of video production. If you need more than 1 creative asset please specify in the creative form.",
        sizes='1:1, 4:5, 9:16',
        buying_model='Fixed',
        base_rate=None,
        estimated_impressions=True,
        discloses_impressions=False,
        minimum_spend=500.0,
        minimum_flight_days=[150, 450],
        sla_data_days=1,
        sla_creative_days=3,
        sla_activate_days=3,
        sla_total_days=8,
        media_allocation_pct=0.0,
        margin_upper=0.4,
        margin_lower=0.3,
        tech_platform='',
        wide_orbit_code='Meta Branded Content | Sponsorship Ads',
        notes='',
        estimated_cpm_for_imps=15.0,
        hispanic_targeting_forced=None,
        cannabis_policy='not_allowed',
        political_policy='allowed',
    ),
    Product(
        family='Branded Content',
        name='Meta Branded Content | Partnership Ads',
        short_label='Lead Generation',
        proposal_description="Creative Strategic approach for Omni-channel campaigns. Ads do appear organically in the page's feed. Entravision Creatives (Images and/or Videos) . Can use Client Creatives if requested. It will run in: Facebook Network. CLIENT ONBOARDING, but Client's Facebook page & approval for tagging is RECOMMENDED",
        sizes='1:1, 4:5, 9:16',
        buying_model='Fixed',
        base_rate=None,
        estimated_impressions=True,
        discloses_impressions=False,
        minimum_spend=5000.0,
        minimum_flight_days=[30, 90],
        sla_data_days=1,
        sla_creative_days=4,
        sla_activate_days=3,
        sla_total_days=15,
        media_allocation_pct=0.0,
        margin_upper=0.4,
        margin_lower=0.3,
        tech_platform='',
        wide_orbit_code='Meta Branded Content | Partnership Ads',
        notes='',
        estimated_cpm_for_imps=15.0,
        hispanic_targeting_forced=None,
        cannabis_policy='not_allowed',
        political_policy='allowed',
    ),
    Product(
        family='Branded Content',
        name='Meta Branded Content Live| Sponsorship Ads',
        short_label='Awareness / Sponsorship',
        proposal_description="Live content video logo integration or Live On client site + 1 static image post for promotion OR Instagram video Post on feed. It will run in: Radio or Noticias Ya Page Feed + Facebook with amplification. Client's FB page & approval for tagging is MANDATORY ; Live or prerecorded Live ; From 2 to 10 minutes (recommended) can do longer if requested.",
        sizes='9:16 (live video)',
        buying_model='Fixed',
        base_rate=None,
        estimated_impressions=True,
        discloses_impressions=False,
        minimum_spend=1000.0,
        minimum_flight_days=[150, 450],
        sla_data_days=1,
        sla_creative_days=2,
        sla_activate_days=3,
        sla_total_days=8,
        media_allocation_pct=0.0,
        margin_upper=0.4,
        margin_lower=0.3,
        tech_platform='',
        wide_orbit_code='Meta Branded Content Live| Sponsorship Ads',
        notes='',
        estimated_cpm_for_imps=15.0,
        hispanic_targeting_forced=None,
        cannabis_policy='not_allowed',
        political_policy='allowed',
    ),
    # ===========================================================================
    # SOCIAL
    # ===========================================================================
    Product(
        family='Social',
        name='Social Media Posts - Noticias Ya',
        short_label='Click-To-Call / Website Conversion',
        proposal_description='Social Media Branded Content - Social Media Posts & Facebook Lives Packages on Noticias Ya Facebook Pages. Variants (contact planning for exact tier): Social Media Post Noticias Ya: $500 min (custom quote); Social Media Post - NoticiasYA Branded Video: $1500 min (custom quote); Noticias Ya Live: $1000 min (custom quote); Talent endorsement / fee: custom quote — contact Sales Planning.',
        sizes='1:1, 4:5',
        buying_model='Fixed',
        base_rate=None,
        estimated_impressions=True,
        discloses_impressions=False,
        minimum_spend=500.0,
        minimum_flight_days=[150, 450],
        sla_data_days=1,
        sla_creative_days=4,
        sla_activate_days=3,
        sla_total_days=8,
        media_allocation_pct=0.0,
        margin_upper=0.4,
        margin_lower=0.3,
        tech_platform='',
        wide_orbit_code='Social Media Posts - Noticias Ya',
        notes=NOTE_META_OO_PAGES,
        estimated_cpm_for_imps=15.0,
        hispanic_targeting_forced=None,
        cannabis_policy='not_allowed',
        political_policy='allowed',
    ),
    Product(
        family='Social',
        name='Facebook & Instagram Ads on Noticias Ya',
        short_label='Awareness / Engagement',
        proposal_description="Client Static Images OR Client Videos (created by the client). It will run in: Facebook Network. NO CLIENT ONBOARDING, but Client's Facebook page & approval for tagging is RECOMMENDED",
        sizes='1:1, 4:5, 9:16',
        buying_model='Fixed',
        base_rate=None,
        estimated_impressions=True,
        discloses_impressions=False,
        minimum_spend=500.0,
        minimum_flight_days=[150, 450],
        sla_data_days=1,
        sla_creative_days=3,
        sla_activate_days=3,
        sla_total_days=7,
        media_allocation_pct=0.0,
        margin_upper=0.4,
        margin_lower=0.3,
        tech_platform='',
        wide_orbit_code='Facebook & Instagram Ads on Noticias Ya Pages',
        notes=NOTE_META_OO_PAGES,
        estimated_cpm_for_imps=15.0,
        hispanic_targeting_forced=None,
        cannabis_policy='not_allowed',
        political_policy='allowed',
    ),
    Product(
        family='Social',
        name='Social Media Posts - Radio Pages',
        short_label='Awareness / Engagement',
        proposal_description='Social Media Branded Content - Social Media Posts & Facebook Lives Packages on Radio Stations Facebook Pages. Variants (contact planning for exact tier): Social Media Post Radio: $300 min (custom quote); EVC Radio Live: $1000 min (custom quote); SHOBOY Page: custom quote — contact Sales Planning; ERAZNO Page: custom quote — contact Sales Planning; GENIO LUCAS Page: custom quote — contact Sales Planning; PIOLIN Page: custom quote — contact Sales Planning.',
        sizes='1:1, 4:5',
        buying_model='Fixed',
        base_rate=None,
        estimated_impressions=True,
        discloses_impressions=False,
        minimum_spend=300.0,
        minimum_flight_days=[150, 450],
        sla_data_days=1,
        sla_creative_days=3,
        sla_activate_days=3,
        sla_total_days=7,
        media_allocation_pct=0.0,
        margin_upper=0.4,
        margin_lower=0.3,
        tech_platform='',
        wide_orbit_code='Social Media Post Radio',
        notes='',
        estimated_cpm_for_imps=15.0,
        hispanic_targeting_forced=None,
        cannabis_policy='not_allowed',
        political_policy='allowed',
    ),
    Product(
        family='Social',
        name='Facebook & Instagram Ads on EVC Radio Pages',
        short_label='Awareness / Engagement',
        proposal_description="Client Static Images OR Client Videos (created by the client). It will run in: Facebook Network. NO CLIENT ONBOARDING, but Client's Facebook page & approval for tagging is RECOMMENDED",
        sizes='1:1, 4:5, 9:16',
        buying_model='Fixed',
        base_rate=None,
        estimated_impressions=True,
        discloses_impressions=False,
        minimum_spend=500.0,
        minimum_flight_days=[150, 450],
        sla_data_days=1,
        sla_creative_days=3,
        sla_activate_days=2,
        sla_total_days=6,
        media_allocation_pct=0.0,
        margin_upper=0.4,
        margin_lower=0.3,
        tech_platform='',
        wide_orbit_code='Facebook & Instagram Ads on EVC Radio pages',
        notes=NOTE_META_OO_PAGES,
        estimated_cpm_for_imps=15.0,
        hispanic_targeting_forced=None,
        cannabis_policy='not_allowed',
        political_policy='allowed',
    ),
    Product(
        family='Social',
        name='Facebook & Instagram Ads | Awareness',
        short_label='Awareness',
        proposal_description="Text, image and video Ads on Facebook's Network. Includes: Targeting capabilities and placements (Facebook feed, Right hand rail, Instagram feed, Instagram Stories) Monthly ad campaign, placement: Facebook + Instagram, 3 creative variations, up to 4 creative change per month, reporting dashboard, real time budget optimization. Optional: A/B Testing and Pixel Implementation for re targeting. Suggested goals: Traffic and Reach",
        sizes='1:1, 4:5, 9:16',
        buying_model='Fixed',
        base_rate=None,
        estimated_impressions=True,
        discloses_impressions=False,
        minimum_spend=500.0,
        minimum_flight_days=[30, 90],
        sla_data_days=1,
        sla_creative_days=3,
        sla_activate_days=3,
        sla_total_days=7,
        media_allocation_pct=0.0,
        margin_upper=0.4,
        margin_lower=0.3,
        tech_platform='',
        wide_orbit_code='Facebook & Instagram Ads',
        notes=NOTE_META_FULL,
        estimated_cpm_for_imps=13.0,
        hispanic_targeting_forced=None,
        cannabis_policy='not_allowed',
        political_policy='allowed',
    ),
    Product(
        family='Social',
        name='Facebook & Instagram Ads | Traffic / Conversion',
        short_label='Traffic / Conversion',
        proposal_description="Text, image and video Ads on Facebook's Network. Includes: Targeting capabilities and placements (Facebook feed, Right hand rail, Instagram feed, Instagram Stories) Monthly ad campaign, placement: Facebook + Instagram, 3 creative variations, up to 4 creative change per month, reporting dashboard, real time budget optimization. Optional: A/B Testing and Pixel Implementation for re targeting. Suggested goals: Traffic and Reach",
        sizes='1:1, 4:5, 9:16',
        buying_model='Fixed',
        base_rate=None,
        estimated_impressions=True,
        discloses_impressions=False,
        minimum_spend=500.0,
        minimum_flight_days=[30, 90],
        sla_data_days=1,
        sla_creative_days=3,
        sla_activate_days=3,
        sla_total_days=7,
        media_allocation_pct=0.0,
        margin_upper=0.4,
        margin_lower=0.3,
        tech_platform='',
        wide_orbit_code='Facebook & Instagram Ads',
        notes=NOTE_META_FULL,
        estimated_cpm_for_imps=18.0,
        hispanic_targeting_forced=None,
        cannabis_policy='not_allowed',
        political_policy='allowed',
    ),
    Product(
        family='Social',
        name='Facebook & Instagram Ads | Lead Gen / Calls',
        short_label='Lead Generation',
        proposal_description="Text, image and video Ads on Facebook's Network. Includes: Targeting capabilities and placements (Facebook feed, Right hand rail, Instagram feed, Instagram Stories) Monthly ad campaign, placement: Facebook + Instagram, 3 creative variations, up to 4 creative change per month, reporting dashboard, real time budget optimization. Optional: A/B Testing and Pixel Implementation for re targeting. Suggested goals: Traffic and Reach",
        sizes='1:1, 4:5, 9:16',
        buying_model='Fixed',
        base_rate=None,
        estimated_impressions=True,
        discloses_impressions=False,
        minimum_spend=500.0,
        minimum_flight_days=[30, 90],
        sla_data_days=1,
        sla_creative_days=3,
        sla_activate_days=3,
        sla_total_days=7,
        media_allocation_pct=0.0,
        margin_upper=0.4,
        margin_lower=0.3,
        tech_platform='',
        wide_orbit_code='Facebook & Instagram Ads',
        notes=NOTE_META_FULL,
        estimated_cpm_for_imps=35.0,
        hispanic_targeting_forced=None,
        cannabis_policy='not_allowed',
        political_policy='allowed',
    ),
    Product(
        family='Social',
        name='Tiktok Ads',
        short_label='Lead Generation',
        proposal_description="Short-form mobile video ads, work best with 10 - 15 sec duration with music or original sounds. The client doesn't need an account in the platform to advertise. Variants (contact planning for exact tier): In-Feed Ads: $600 min (custom quote); In-Feed Lead Gen Ads: $1200 min (custom quote).",
        sizes='9:16 (:9-:15s video)',
        buying_model='Fixed',
        base_rate=None,
        estimated_impressions=True,
        discloses_impressions=False,
        minimum_spend=600.0,
        minimum_flight_days=[14, 84],
        sla_data_days=1,
        sla_creative_days=3,
        sla_activate_days=5,
        sla_total_days=9,
        media_allocation_pct=0.0,
        margin_upper=0.4,
        margin_lower=0.3,
        tech_platform='',
        wide_orbit_code='Tiktok Ads',
        notes=NOTE_TIKTOK,
        estimated_cpm_for_imps=15.0,
        hispanic_targeting_forced=None,
        cannabis_policy='not_allowed',
        political_policy='not_allowed',
    ),
    Product(
        family='Social',
        name='Tik Tok Ads on Entravision News and Radio Pages',
        short_label='Lead Generation',
        proposal_description="Short-form mobile video ads, work best with 10 - 15 sec duration with music or original sounds. ADVERTISERS' CAMPAIGN WILL RUN ON ENTRAVISION'S NEWS AND RADIO TIKTOK PAGES. Variants (contact planning for exact tier): In-Feed Ads: $600 min (custom quote); In-Feed Lead Gen Ads: $1200 min (custom quote).",
        sizes='9:16 (:9-:15s video)',
        buying_model='Fixed',
        base_rate=None,
        estimated_impressions=True,
        discloses_impressions=False,
        minimum_spend=600.0,
        minimum_flight_days=[14, 84],
        sla_data_days=1,
        sla_creative_days=3,
        sla_activate_days=5,
        sla_total_days=9,
        media_allocation_pct=0.0,
        margin_upper=0.4,
        margin_lower=0.3,
        tech_platform='',
        wide_orbit_code='Tik Tok Ads on Entravision News and Radio Pages',
        notes=NOTE_TIKTOK,
        estimated_cpm_for_imps=15.0,
        hispanic_targeting_forced=None,
        cannabis_policy='not_allowed',
        political_policy='not_allowed',
    ),
    Product(
        family='Social',
        name='LinkedIn',
        short_label='Awareness / Engagement / Website Conversion / Lead Generation',
        proposal_description='LinkedIn is a professional social networking platform for B2B marketing. It is also a good place to reach decision-makers. You can reach a qualified audience based on job title, industry, company name, and more.',
        sizes='1:1, 1.91:1',
        buying_model='Fixed',
        base_rate=None,
        estimated_impressions=True,
        discloses_impressions=False,
        minimum_spend=2000.0,
        minimum_flight_days=[30, 90],
        sla_data_days=1,
        sla_creative_days=3,
        sla_activate_days=2,
        sla_total_days=6,
        media_allocation_pct=0.0,
        margin_upper=0.4,
        margin_lower=0.3,
        tech_platform='',
        wide_orbit_code='Social - LinkedIn Ads',
        notes='',
        estimated_cpm_for_imps=38.0,
        hispanic_targeting_forced=None,
        cannabis_policy='not_allowed',
        political_policy='not_allowed',
    ),
    # ===========================================================================
    # EMAIL
    # ===========================================================================
    Product(
        family='Email',
        name='Email Campaigns and/or Email Campaigns - Re-Drop',
        short_label='Lead Generation',
        proposal_description='Your message is sent to a targeted database provided by a third party data partner. Creative included. Variants (contact planning for exact tier): Number of emails: 0 - 15,000: $450 CPM; Number of emails: 0 - 25,000: $750 CPM; Number of emails: 25,001 - 49,999: $30 CPM; Number of emails: 50,000 - 74,999: $28 CPM; Number of emails: 75,000 - 99,999: $27 CPM; Number of emails: 100,000 - 149,999: $26 CPM; Number of emails: 150,000+: $24 CPM.',
        sizes='600px wide HTML',
        buying_model='CPM',
        base_rate=24.0,
        estimated_impressions=False,
        discloses_impressions=True,
        minimum_spend=3600.0,
        minimum_flight_days=[7, 90],
        sla_data_days=1,
        sla_creative_days=3,
        sla_activate_days=3,
        sla_total_days=7,
        media_allocation_pct=0.0,
        margin_upper=0.4,
        margin_lower=0.3,
        tech_platform='',
        wide_orbit_code='Email Campaigns - Re-Drop\nEmail Campaigns\nEmail Campaigns CPM\nEmail Campaigns - Re-Drop CPM',
        notes=NOTE_EMAIL_STATES + "\n\n" + 'Firearms and Ammunition, Gambling, Adult Content, Cigarretes. Marijuana, Hemp and CBD in certain states require prior approval, Buying alcohol online',
        estimated_cpm_for_imps=None,
        hispanic_targeting_forced=None,
        cannabis_policy='not_allowed',
        political_policy='allowed',
    ),
    Product(
        family='Email',
        name='Email Campaigns - Display Re-targeting',
        short_label='Click-To-Call / Website Conversion',
        proposal_description='Retargeting strategy targets users who have already opened or clicked on your e-mail by displaying a banner campaing ad as they visit other websites. Minimum email count: 25K',
        sizes='300x250, 728x90',
        buying_model='CPM',
        base_rate=14.0,
        estimated_impressions=False,
        discloses_impressions=True,
        minimum_spend=375.0,
        minimum_flight_days=[7, 90],
        sla_data_days=1,
        sla_creative_days=2,
        sla_activate_days=3,
        sla_total_days=7,
        media_allocation_pct=0.0,
        margin_upper=0.4,
        margin_lower=0.3,
        tech_platform='',
        wide_orbit_code='Email Campaigns - Re-targeting',
        notes=NOTE_EMAIL_STATES,
        estimated_cpm_for_imps=None,
        hispanic_targeting_forced=None,
        cannabis_policy='not_allowed',
        political_policy='allowed',
    ),
    Product(
        family='Email',
        name='Email Campaigns - Client List Inclusion',
        short_label='Lead Generation',
        proposal_description='Include client provided list of recipients to our email blast',
        sizes='N/A (data service)',
        buying_model='CPM',
        base_rate=7.0,
        estimated_impressions=False,
        discloses_impressions=True,
        minimum_spend=350.0,
        minimum_flight_days=[7, 90],
        sla_data_days=None,
        sla_creative_days=None,
        sla_activate_days=None,
        sla_total_days=4,
        media_allocation_pct=0.0,
        margin_upper=0.4,
        margin_lower=0.3,
        tech_platform='',
        wide_orbit_code='Email Campaigns - Hashed Email File',
        notes=NOTE_EMAIL_STATES,
        estimated_cpm_for_imps=None,
        hispanic_targeting_forced=None,
        cannabis_policy='not_allowed',
        political_policy='allowed',
    ),
    Product(
        family='Email',
        name='Email Campaigns - Hashed Email File',
        short_label='Click-To-Call / Website Conversion',
        proposal_description="The hashed file includes the full email deployment's encrypted email addresses that can then be un-encrypted through any DSP that you are using for retargeting purposes. These are the different formats of encryption that we can generate (SHA256, SHA512, MD5) We give the client a file with ONLY hashed email addresses. No minimum email count: 0 - 50,000. More than 50,000 emails a $7 CPM would applied. Formula: Amount of emails * CPM / 1,000",
        sizes='N/A (data service)',
        buying_model='CPM',
        base_rate=7.0,
        estimated_impressions=False,
        discloses_impressions=True,
        minimum_spend=350.0,
        minimum_flight_days=[7, 90],
        sla_data_days=None,
        sla_creative_days=None,
        sla_activate_days=None,
        sla_total_days=None,
        media_allocation_pct=0.0,
        margin_upper=0.4,
        margin_lower=0.3,
        tech_platform='',
        wide_orbit_code='Email Campaigns - Hashed Email File',
        notes=NOTE_EMAIL_STATES,
        estimated_cpm_for_imps=None,
        hispanic_targeting_forced=None,
        cannabis_policy='not_allowed',
        political_policy='allowed',
    ),
    Product(
        family='Email',
        name='Email Campaigns - Matchback',
        short_label='Attribution & Measurement',
        proposal_description='Analyze and indicate any matching records from the email database against the file provided by client. We will cross-reference a provided customer file (must contain email, name and last name) against the email deployment and flag every record where a match occurs. Deployment data available for 90 days. Variants (contact planning for exact tier): Email Campaigns - Matchback Analysis: $375 CPP.',
        sizes='N/A (data service)',
        buying_model='CPP',
        base_rate=375.0,
        estimated_impressions=False,
        discloses_impressions=True,
        minimum_spend=375.0,
        minimum_flight_days=[45, 270],
        sla_data_days=None,
        sla_creative_days=None,
        sla_activate_days=None,
        sla_total_days=None,
        media_allocation_pct=0.0,
        margin_upper=0.4,
        margin_lower=0.3,
        tech_platform='',
        wide_orbit_code='Email Campaigns - Matchback Analysis',
        notes=NOTE_EMAIL_STATES,
        estimated_cpm_for_imps=None,
        hispanic_targeting_forced=None,
        cannabis_policy='not_allowed',
        political_policy='allowed',
    ),
    Product(
        family='Email',
        name='Email Campaigns - Postal Matching',
        short_label='Lead Generation',
        proposal_description='Include client provided list of target names and physical addresses',
        sizes='N/A (data service)',
        buying_model='CPP',
        base_rate=30.0,
        estimated_impressions=False,
        discloses_impressions=True,
        minimum_spend=500.0,
        minimum_flight_days=[7, 90],
        sla_data_days=None,
        sla_creative_days=None,
        sla_activate_days=None,
        sla_total_days=None,
        media_allocation_pct=0.0,
        margin_upper=0.4,
        margin_lower=0.3,
        tech_platform='',
        wide_orbit_code='Email Campaigns - Postal Matching',
        notes=NOTE_EMAIL_STATES,
        estimated_cpm_for_imps=None,
        hispanic_targeting_forced=None,
        cannabis_policy='not_allowed',
        political_policy='allowed',
    ),
    # ===========================================================================
    # DOOH
    # ===========================================================================
    Product(
        family='DOOH',
        name='Digital Out of Home',
        short_label='Awareness',
        proposal_description='DOOH is the term used for the ad environment made up of outdoor digital ad placements like digital billboards and signs in a variety of locations, including gas stations, airports, freeways, the sides of buildings, and so on. Publisher specific delivery is not guaranteed.',
        sizes='16:9 HD (per-venue spec)',
        buying_model='Fixed',
        base_rate=None,
        estimated_impressions=True,
        discloses_impressions=False,
        minimum_spend=2000.0,
        minimum_flight_days=[30, 90],
        sla_data_days=1,
        sla_creative_days=3,
        sla_activate_days=6,
        sla_total_days=10,
        media_allocation_pct=0.0,
        margin_upper=0.4,
        margin_lower=0.3,
        tech_platform='',
        wide_orbit_code='Digital Out Of Home',
        notes='Contact Sales Planning for pricing.',
        estimated_cpm_for_imps=None,
        hispanic_targeting_forced=None,
        cannabis_policy='not_allowed',
        political_policy='allowed',
    ),
    # ===========================================================================
    # SERVICES
    # ===========================================================================
    Product(
        family='Services',
        name='Web Services - Landing Pages',
        short_label='Conversion Support',
        proposal_description='Optimized landing pages. Includes: Hosting and dedicated URL. Responsive design. Free royalty-free stock images.',
        sizes='N/A (custom build)',
        buying_model='Fixed',
        base_rate=None,
        estimated_impressions=True,
        discloses_impressions=False,
        minimum_spend=99.0,
        minimum_flight_days=[7, 90],
        sla_data_days=1,
        sla_creative_days=3,
        sla_activate_days=2,
        sla_total_days=4,
        media_allocation_pct=0.0,
        margin_upper=0.4,
        margin_lower=0.3,
        tech_platform='',
        wide_orbit_code='Web Services - Landing Pages',
        notes='',
        estimated_cpm_for_imps=None,
        hispanic_targeting_forced=None,
        cannabis_policy='not_allowed',
        political_policy='allowed',
        is_addon=True,
    ),
    Product(
        family='Services',
        name='Microsite',
        short_label='Conversion Support',
        proposal_description='',
        sizes='N/A (custom build)',
        buying_model='Fixed',
        base_rate=None,
        estimated_impressions=True,
        discloses_impressions=False,
        minimum_spend=300.0,
        minimum_flight_days=[7, 90],
        sla_data_days=None,
        sla_creative_days=None,
        sla_activate_days=None,
        sla_total_days=None,
        media_allocation_pct=0.0,
        margin_upper=0.4,
        margin_lower=0.3,
        tech_platform='',
        wide_orbit_code='Web Services - Microsite',
        notes='',
        estimated_cpm_for_imps=None,
        hispanic_targeting_forced=None,
        cannabis_policy='not_allowed',
        political_policy='allowed',
        is_addon=True,
    ),
    Product(
        family='Services',
        name='Creatives Development',
        short_label='Conversion Support',
        proposal_description='',
        sizes='N/A (production service)',
        buying_model='Fixed',
        base_rate=None,
        estimated_impressions=True,
        discloses_impressions=False,
        minimum_spend=0.0,
        minimum_flight_days=[7, 90],
        sla_data_days=None,
        sla_creative_days=None,
        sla_activate_days=None,
        sla_total_days=None,
        media_allocation_pct=0.0,
        margin_upper=0.4,
        margin_lower=0.3,
        tech_platform='',
        wide_orbit_code='Creatives Development',
        notes='',
        estimated_cpm_for_imps=None,
        hispanic_targeting_forced=None,
        cannabis_policy='not_allowed',
        political_policy='allowed',
        is_addon=True,
    ),
    Product(
        # Re-added as a real catalog entry (Round 10) — previously only existed
        # as a hardcoded line in excel_template.py's ADD-ONS block, with no
        # catalog product backing it, when that block became planner-driven
        # via Step 04's Add-Ons module instead of a fixed list.
        family='Services',
        name='Email Database Match - Hashed File Onboarding',
        short_label='Conversion Support',
        proposal_description='Upload your email database to match opted-in users in our database for precise targeting.',
        sizes='N/A (data service)',
        buying_model='Fixed',
        base_rate=None,
        estimated_impressions=False,
        discloses_impressions=False,
        minimum_spend=150.0,
        minimum_flight_days=[7, 90],
        sla_data_days=None,
        sla_creative_days=None,
        sla_activate_days=None,
        sla_total_days=None,
        media_allocation_pct=0.0,
        margin_upper=0.4,
        margin_lower=0.3,
        tech_platform='',
        wide_orbit_code='Email Database Match - Hashed File Onboarding',
        notes='',
        estimated_cpm_for_imps=None,
        hispanic_targeting_forced=None,
        cannabis_policy='not_allowed',
        political_policy='allowed',
        is_addon=True,
    ),
    # ===========================================================================
    # SPONSORSHIPS
    # ===========================================================================
    Product(
        family='Sponsorships',
        name='CW Sponsorship',
        short_label='Lead Generation',
        proposal_description='Available for McAllen Market only.',
        sizes='Custom (sponsorship package)',
        buying_model='Fixed',
        base_rate=None,
        estimated_impressions=True,
        discloses_impressions=False,
        minimum_spend=0.0,
        minimum_flight_days=[7, 90],
        sla_data_days=None,
        sla_creative_days=None,
        sla_activate_days=None,
        sla_total_days=None,
        media_allocation_pct=0.0,
        margin_upper=0.4,
        margin_lower=0.3,
        tech_platform='',
        wide_orbit_code='Sponsorship - CW Video Sponsorship',
        notes='Contact Sales Planning for pricing.',
        estimated_cpm_for_imps=None,
        hispanic_targeting_forced=None,
        cannabis_policy='not_allowed',
        political_policy='allowed',
    ),
    Product(
        family='Sponsorships',
        name='NBC Sports Stream Sponsorship',
        short_label='Awareness / Sponsorship',
        proposal_description='Available for Palm Springs Only',
        sizes='Custom (sponsorship package)',
        buying_model='Fixed',
        base_rate=None,
        estimated_impressions=True,
        discloses_impressions=False,
        minimum_spend=0.0,
        minimum_flight_days=[7, 90],
        sla_data_days=None,
        sla_creative_days=None,
        sla_activate_days=None,
        sla_total_days=None,
        media_allocation_pct=0.0,
        margin_upper=0.4,
        margin_lower=0.3,
        tech_platform='',
        wide_orbit_code='Sponsorship - NBC Sports Stream',
        notes='',
        estimated_cpm_for_imps=None,
        hispanic_targeting_forced=None,
        cannabis_policy='not_allowed',
        political_policy='allowed',
    ),
    Product(
        family='Sponsorships',
        name='Service Sponsorships - McAllen and Palm Springs',
        short_label='Lead Generation',
        proposal_description='Sponsorships for our Noticias Ya pages Available for McAllen Market and Palm Springs Only',
        sizes='Custom (sponsorship package)',
        buying_model='Fixed',
        base_rate=None,
        estimated_impressions=True,
        discloses_impressions=False,
        minimum_spend=0.0,
        minimum_flight_days=[7, 90],
        sla_data_days=None,
        sla_creative_days=None,
        sla_activate_days=None,
        sla_total_days=None,
        media_allocation_pct=0.0,
        margin_upper=0.4,
        margin_lower=0.3,
        tech_platform='',
        wide_orbit_code='Sponsorship - Services Sponsorships',
        notes='',
        estimated_cpm_for_imps=None,
        hispanic_targeting_forced=None,
        cannabis_policy='not_allowed',
        political_policy='allowed',
    ),
    Product(
        family='Sponsorships',
        name='Fox Sports Go Video Sponsorship',
        short_label='Awareness / Sponsorship',
        proposal_description='Available for KFXV, KCBA and KXOF only',
        sizes='Custom (sponsorship package)',
        buying_model='Fixed',
        base_rate=None,
        estimated_impressions=True,
        discloses_impressions=False,
        minimum_spend=0.0,
        minimum_flight_days=[7, 90],
        sla_data_days=None,
        sla_creative_days=None,
        sla_activate_days=None,
        sla_total_days=None,
        media_allocation_pct=0.0,
        margin_upper=0.4,
        margin_lower=0.3,
        tech_platform='',
        wide_orbit_code='Sponsorship - Fox Sports Go Video Sponsorship',
        notes='',
        estimated_cpm_for_imps=None,
        hispanic_targeting_forced=None,
        cannabis_policy='not_allowed',
        political_policy='allowed',
    ),
    # ===========================================================================
    # MEASUREMENT
    # ===========================================================================
    Product(
        family='Measurement',
        name='Non Media Offering - Brand Lift',
        short_label='Attribution & Measurement',
        proposal_description='',
        sizes='N/A (measurement study)',
        buying_model='Fixed',
        base_rate=None,
        estimated_impressions=True,
        discloses_impressions=False,
        minimum_spend=0.0,
        minimum_flight_days=[7, 90],
        sla_data_days=None,
        sla_creative_days=None,
        sla_activate_days=None,
        sla_total_days=None,
        media_allocation_pct=0.0,
        margin_upper=0.4,
        margin_lower=0.3,
        tech_platform='',
        wide_orbit_code='Non Media Offering - Brand Lift',
        notes='Contact Sales Planning for pricing.',
        estimated_cpm_for_imps=None,
        hispanic_targeting_forced=None,
        cannabis_policy='not_allowed',
        political_policy='allowed',
        is_addon=True,
    ),
    Product(
        family='Measurement',
        name='Call Tracking',
        short_label='Attribution & Measurement',
        proposal_description='',
        sizes='N/A (measurement service)',
        buying_model='CPP',
        base_rate=10.0,
        estimated_impressions=False,
        discloses_impressions=True,
        minimum_spend=10.0,
        minimum_flight_days=[7, 90],
        sla_data_days=1,
        sla_creative_days=None,
        sla_activate_days=1,
        sla_total_days=2,
        media_allocation_pct=0.0,
        margin_upper=0.4,
        margin_lower=0.3,
        tech_platform='',
        wide_orbit_code='Call Tracking Numbers\nCall Tracking',
        notes='',
        estimated_cpm_for_imps=None,
        hispanic_targeting_forced=None,
        cannabis_policy='not_allowed',
        political_policy='allowed',
        is_addon=True,
    ),
]

# ===========================================================================
# THE PRODUCT LIST
#
# The `products` table is THE product list. Every product — whether it began
# as a row of the rate card this file was seeded from, or was typed in (or
# duplicated) by an admin — is an equal, fully editable row: there is no
# "original" list to revert to, no built-in/custom distinction and nothing an
# admin can't change or remove. CATALOG above is only the SEED: it fills an
# empty table once, and it is the fail-safe when the database can't be read
# (so a missing table never takes proposal generation down).
#
# Rows that used to live in the three legacy tables (rate_overrides,
# custom_products and the soft-delete flag) are folded in at seed time; those
# tables are only READ for that, never written, and can be dropped later.
# ===========================================================================


log = logging.getLogger(__name__)

# The legacy override layer's field list — still how a legacy rate_overrides row is read when seeding.
_OVERRIDABLE_FIELDS = (
    "name", "base_rate", "minimum_spend", "estimated_cpm_for_imps",
    "family", "short_label", "buying_model", "sizes", "tech_platform",
    "proposal_description", "notes", "is_addon",
)

# Names a product used to have in CODE (a product that was split or renamed in this file) -> its current name, so a
# saved proposal or a pasted request that still says "YouTube Ads" keeps resolving even with no database.
_STATIC_ALIASES = {"YouTube Ads": "YouTube Ads - Skippable"}
# A legacy rate override on a product that has since been split in two applies to both halves.
_LEGACY_SPLITS = {"YouTube Ads": ("YouTube Ads - Skippable", "YouTube Ads - Non-Skippable")}


class _TTLCache:
    """
    Tiny read-through cache for one DB-backed loader.

    WHY: effective_catalog()/by_name()/by_family() are called constantly (per product, per
    recommended tactic, per line item). Reading the table each time would mean hundreds of
    round trips per click. Every mutation below calls .invalidate() itself, so an admin's own
    edit never waits on the TTL; the TTL is only a backstop for a second app instance writing.
    `None` is the "not loaded" sentinel; only SUCCESSFUL reads are ever stored.
    """
    def __init__(self, ttl_seconds: float = 30.0):
        self._ttl = ttl_seconds
        self._value = None
        self._loaded_at = 0.0

    def peek(self):
        if self._value is not None and (time.monotonic() - self._loaded_at) <= self._ttl:
            return self._value
        return None

    def set(self, value) -> None:
        self._value = value
        self._loaded_at = time.monotonic()

    def get(self, loader):
        value = self.peek()
        if value is None:
            value = loader()
            self.set(value)
        return value

    def invalidate(self) -> None:
        self._value = None


_rate_overrides_cache = _TTLCache()
_custom_products_cache = _TTLCache()
_deleted_builtin_names_cache = _TTLCache()
_products_cache = _TTLCache()
_aliases_cache = _TTLCache()
_products_retry_at = 0.0     # after a failed read of the products table, don't retry it (and re-seed) for a few seconds
_seed_memo: dict = {"key": None, "rows": None, "aliases": None, "at": 0.0}
_last_good_rows = None       # the last rows read successfully: served (stale) if a later read fails
_fallback_aliases: dict = {}  # legacy renames, only while the list is the seed fallback


def invalidate_product_caches(keep_stale: bool = False) -> None:
    """Forget every cached product read — called by each write below, and by tests. `keep_stale` keeps the last
    good rows around as the answer to "the database blinked right after a write"; tests drop them too."""
    global _products_retry_at, _last_good_rows
    _products_cache.invalidate()
    _aliases_cache.invalidate()
    _products_retry_at = 0.0
    _seed_memo["key"] = None
    if not keep_stale:
        _last_good_rows = None


# ---------------------------------------------------------------------------
# Legacy tables — read ONLY, to seed the product list (see above)
# ---------------------------------------------------------------------------

def load_rate_overrides() -> dict:
    """{product_name: {field: value}} from the legacy rate_overrides table (seed input only)."""
    def _fetch():
        columns = ", ".join(("product_name",) + _OVERRIDABLE_FIELDS)
        with get_connection() as conn:
            rows = conn.execute(f"SELECT {columns} FROM rate_overrides").fetchall()
        numeric_fields = {"base_rate", "minimum_spend", "estimated_cpm_for_imps"}
        overrides = {}
        for row in rows:
            fields_ = {}
            for field_name in _OVERRIDABLE_FIELDS:
                value = row[field_name]
                if value is not None:
                    fields_[field_name] = float(value) if field_name in numeric_fields else value
            if fields_:
                overrides[row["product_name"]] = fields_
        return overrides
    return _rate_overrides_cache.get(_fetch)


def load_deleted_builtin_names() -> set[str]:
    """Names flagged deleted in the legacy rate_overrides table (seed input only)."""
    def _fetch():
        with get_connection() as conn:
            rows = conn.execute("SELECT product_name FROM rate_overrides WHERE is_deleted").fetchall()
        return {r["product_name"] for r in rows}
    return _deleted_builtin_names_cache.get(_fetch)


_PRODUCT_COLUMNS = (
    "name", "family", "short_label", "proposal_description", "sizes", "buying_model",
    "base_rate", "estimated_impressions", "discloses_impressions", "minimum_spend",
    "minimum_flight_days_min", "minimum_flight_days_max", "sla_data_days",
    "sla_creative_days", "sla_activate_days", "sla_total_days", "media_allocation_pct",
    "margin_upper", "margin_lower", "tech_platform", "wide_orbit_code", "billing_interval",
    "billing_calendar", "billing_source", "national_supported", "notes",
    "estimated_cpm_for_imps", "hispanic_targeting_forced", "cannabis_policy",
    "political_policy", "is_addon",
)
_NUMERIC_COLUMNS = {
    "base_rate", "minimum_spend", "media_allocation_pct", "margin_upper", "margin_lower",
    "estimated_cpm_for_imps",
}


def _row_to_product(row) -> Optional[Product]:
    data = {k: row[k] for k in _PRODUCT_COLUMNS}
    for field_name in _NUMERIC_COLUMNS:
        if data[field_name] is not None:
            data[field_name] = float(data[field_name])
    data["minimum_flight_days"] = (data.pop("minimum_flight_days_min"), data.pop("minimum_flight_days_max"))
    try:
        return Product(**data)
    except TypeError:
        return None        # a malformed legacy record is skipped, never fatal


def _product_params(p: Product) -> list:
    return [
        p.name, p.family, p.short_label, p.proposal_description, p.sizes, p.buying_model,
        p.base_rate, p.estimated_impressions, p.discloses_impressions, p.minimum_spend,
        p.minimum_flight_days[0], p.minimum_flight_days[1], p.sla_data_days,
        p.sla_creative_days, p.sla_activate_days, p.sla_total_days, p.media_allocation_pct,
        p.margin_upper, p.margin_lower, p.tech_platform, p.wide_orbit_code, p.billing_interval,
        p.billing_calendar, p.billing_source, p.national_supported, p.notes,
        p.estimated_cpm_for_imps, p.hispanic_targeting_forced, p.cannabis_policy,
        p.political_policy, p.is_addon,
    ]


def load_custom_products() -> list[Product]:
    """Products from the legacy custom_products table (seed input only)."""
    def _fetch():
        with get_connection() as conn:
            rows = conn.execute(f"SELECT {', '.join(_PRODUCT_COLUMNS)} FROM custom_products ORDER BY name").fetchall()
        return [p for p in (_row_to_product(r) for r in rows) if p is not None]
    return _custom_products_cache.get(_fetch)


def _apply_override(p: Product, overrides: Optional[dict] = None) -> Product:
    """`p` with its legacy admin override (if any) applied — seed time only."""
    override = (overrides if overrides is not None else load_rate_overrides()).get(p.name)
    if not override:
        return p
    return replace(p, **{k: v for k, v in override.items() if k in _OVERRIDABLE_FIELDS})


def _legacy(loader, default, strict: bool = False):
    """A legacy table read. A table that was never created (a fresh database) simply has nothing to fold in. Any
    OTHER failure (a dropped connection, a missing column) must not be mistaken for "nothing there": with
    `strict` it propagates, so the one-shot seed aborts and retries rather than permanently losing every legacy
    edit; the read-only fallback (`strict` False) just goes without."""
    try:
        return loader()
    except Exception as exc:  # noqa: BLE001
        absent = type(exc).__name__ in ("UndefinedTable", "UndefinedColumn")
        if strict and not absent:
            raise
        log.warning("legacy product table unavailable (%s): %s", getattr(loader, "__name__", loader), exc)
        return default


def _seed_rows(strict: bool = False) -> list[tuple[Product, bool]]:
    """[(product, deleted)] as the legacy stores stood: the code list with every admin override applied (renames
    included), admin-added products, and the admin's deletions kept as deleted — a deleted product must still
    resolve by name or a saved proposal would lose its line."""
    overrides = _legacy(load_rate_overrides, {}, strict)
    deleted = _legacy(load_deleted_builtin_names, set(), strict)
    rows = [(_apply_override(p, overrides), p.name in deleted) for p in CATALOG]
    for legacy_name, halves in _LEGACY_SPLITS.items():
        carried = {k: v for k, v in overrides.get(legacy_name, {}).items()
                   if k in ("base_rate", "minimum_spend", "estimated_cpm_for_imps")}
        gone = legacy_name in deleted
        rows = [(replace(p, **carried) if (carried and p.name in halves) else p, d or (gone and p.name in halves))
                for p, d in rows]
    taken = {p.name for p, _ in rows}
    for custom in _legacy(load_custom_products, [], strict):
        if custom.name not in taken:
            rows.append((_apply_override(custom, overrides), False))
            taken.add(custom.name)
    return rows


def _seed_alias_map(strict: bool = False) -> dict[str, str]:
    """{name a legacy rename replaced: its new name} — so the old name keeps resolving."""
    overrides = _legacy(load_rate_overrides, {}, strict)
    code_names = {p.name for p in CATALOG}
    return {raw: ov["name"] for raw, ov in overrides.items()
            if raw in code_names and ov.get("name") and ov["name"] != raw}


def _seed_products() -> list[Product]:
    """The live (non-deleted) seed list — also the read-only fallback when the table can't be reached."""
    return [p for p, deleted in _seed_rows() if not deleted]


# ---------------------------------------------------------------------------
# Reading the list
# ---------------------------------------------------------------------------

def _seed_memo_rows() -> tuple[list[dict], dict]:
    """The seed as fallback rows, remembered for a few seconds: with the table unreadable every lookup would
    otherwise re-read the legacy tables (each a database round trip) on every call."""
    key = (id(load_rate_overrides), id(load_custom_products), id(load_deleted_builtin_names))
    now = time.monotonic()
    if _seed_memo["key"] == key and now - _seed_memo["at"] < 5.0:
        return _seed_memo["rows"], _seed_memo["aliases"]
    rows = [{"id": None, "is_deleted": deleted, "product": p} for p, deleted in _seed_rows()]
    aliases = _seed_alias_map()
    _seed_memo.update(key=key, rows=rows, aliases=aliases, at=now)
    return rows, aliases


def _read_product_rows() -> Optional[list[dict]]:
    """[{id, is_deleted, product}] in display order, or None when the table can't be read."""
    try:
        with get_connection() as conn:
            rows = conn.execute(
                f"SELECT id, is_deleted, {', '.join(_PRODUCT_COLUMNS)} FROM products ORDER BY sort_order, id"
            ).fetchall()
    except Exception as exc:  # noqa: BLE001 — a missing table / unreachable DB must never break callers
        log.warning("products table unavailable, using the built-in seed list: %s", exc)
        return None
    out = []
    for row in rows:
        product = _row_to_product(row)
        if product is not None:
            out.append({"id": row["id"], "is_deleted": bool(row["is_deleted"]), "product": product})
    return out


def _seed_products_table() -> None:
    """Fill an empty table from the code list plus everything the legacy tables held. Strict: if a legacy table
    can't be read for any reason other than not existing, nothing is written (the next read tries again) — a
    half-informed seed would be permanent."""
    rows = _seed_rows(strict=True)
    aliases = _seed_alias_map(strict=True)
    placeholders = ", ".join(["%s"] * (len(_PRODUCT_COLUMNS) + 2))
    with get_connection() as conn:
        for order, (product, deleted) in enumerate(rows):
            conn.execute(
                f"INSERT INTO products (sort_order, is_deleted, {', '.join(_PRODUCT_COLUMNS)}) VALUES ({placeholders}) "
                "ON CONFLICT (name) DO NOTHING",
                [order, deleted] + _product_params(product),
            )
        for old_name, new_name in aliases.items():
            conn.execute(
                "INSERT INTO product_aliases (alias_name, current_name) VALUES (%s, %s) ON CONFLICT (alias_name) DO NOTHING",
                (old_name, new_name),
            )


def load_product_rows() -> list[dict]:
    """Every product row, deleted ones included (they still resolve by name for saved proposals). Cached.
    Seeds an empty table on first use; with the table unreachable, the seed list (ids None)."""
    global _products_retry_at, _last_good_rows, _fallback_aliases
    rows = _products_cache.peek()
    if rows is None and time.monotonic() >= _products_retry_at:
        rows = _read_product_rows()
        if rows is not None and not rows:
            try:
                _seed_products_table()
            except Exception as exc:  # noqa: BLE001
                log.warning("couldn't seed the products table: %s", exc)
                rows = None
            else:
                rows = _read_product_rows()
        if rows is not None:
            _products_cache.set(rows)
            _last_good_rows = rows
            _fallback_aliases = {}
        else:
            _products_retry_at = time.monotonic() + 15.0
    if rows is None:
        if _last_good_rows is not None:
            return _last_good_rows           # a blip after the list was loaded: keep serving it, edits and all
        rows, _fallback_aliases = _seed_memo_rows()
    return rows


def effective_catalog(include_deleted: bool = False) -> list[Product]:
    """The product list, in display order. Deleted products are left out unless include_deleted."""
    return [r["product"] for r in load_product_rows() if include_deleted or not r["is_deleted"]]


def _db_aliases() -> dict[str, str]:
    cached = _aliases_cache.peek()
    if cached is not None:
        return cached
    try:
        found = all_product_aliases()
    except Exception:  # noqa: BLE001 — the alias table is optional context
        return {}
    _aliases_cache.set(found)
    return found


def by_name(name: str) -> Optional[Product]:
    """
    A product by its current name — or by a name it USED to have (a rename, or a product that was split in
    code). Deleted products still resolve, so a saved proposal that uses one keeps its line.
    """
    rows = load_product_rows()
    by_exact = {r["product"].name: r["product"] for r in rows}
    seen = set()
    current = name
    for _ in range(6):                                       # name -> former-name chains are short
        if current in by_exact:
            return by_exact[current]
        if current in seen:
            break
        seen.add(current)
        nxt = _STATIC_ALIASES.get(current) or _db_aliases().get(current) or _fallback_aliases.get(current)
        if not nxt or nxt == current:
            break
        current = nxt
    return None


def name_variants(name: str) -> set[str]:
    """Every name `name`'s product answers to: the name as given, its current name, and the names it used to
    have (including, for either half of a split product, the name it had before the split). Anything keyed by a
    product NAME (the Restricted Verticals mapping) is looked up through all of them, so a rename or split
    doesn't silently orphan it."""
    out = {name}
    product = by_name(name)
    if product is None:
        return out
    out.add(product.name)
    for legacy_name, halves in _LEGACY_SPLITS.items():
        if out & set(halves):
            out.add(legacy_name)
    aliases = {**_STATIC_ALIASES, **_fallback_aliases, **_db_aliases()}
    changed = True
    while changed:                                           # follow A -> B -> C to the end, whatever the order
        changed = False
        for alias, current in aliases.items():
            if current in out and alias not in out:
                out.add(alias)
                changed = True
    return out


def by_family(family: str, include_deleted: bool = False) -> list[Product]:
    return [p for p in effective_catalog(include_deleted=include_deleted) if p.family == family]


def families() -> list[str]:
    """Distinct families in list order, including any family an admin introduced."""
    seen, out = set(), []
    for p in effective_catalog():
        if p.family not in seen:
            seen.add(p.family)
            out.append(p.family)
    return out


def to_dict_list() -> list[dict]:
    """Serialize for JSON / web use."""
    return [asdict(p) for p in effective_catalog()]


# ---------------------------------------------------------------------------
# Names that changed — the alias table
# ---------------------------------------------------------------------------

def record_product_alias(old_name: str, new_name: str) -> None:
    """Record that `old_name` is now called `new_name`, repointing earlier aliases so a chain of renames
    (A -> B -> C) still resolves in one hop from any historical name."""
    if not old_name or not new_name or old_name == new_name:
        return
    with get_connection() as conn:
        conn.execute(
            "UPDATE product_aliases SET current_name = %s, updated_at = now() WHERE current_name = %s",
            (new_name, old_name),
        )
        conn.execute(
            """
            INSERT INTO product_aliases (alias_name, current_name) VALUES (%s, %s)
            ON CONFLICT (alias_name) DO UPDATE SET
                current_name = EXCLUDED.current_name, updated_at = now()
            """,
            (old_name, new_name),
        )
    _aliases_cache.invalidate()


def resolve_product_alias(name: str) -> Optional[str]:
    """If `name` is a former product name, its current one."""
    with get_connection() as conn:
        row = conn.execute("SELECT current_name FROM product_aliases WHERE alias_name = %s", (name,)).fetchone()
    return row["current_name"] if row else None


def all_product_aliases() -> dict[str, str]:
    """{former_name: current_name} for every renamed product — also fed to the free-text parser so a pasted
    request can still mention a product by a name it no longer has. Includes the names changed in code."""
    with get_connection() as conn:
        rows = conn.execute("SELECT alias_name, current_name FROM product_aliases").fetchall()
    return {**_STATIC_ALIASES, **{r["alias_name"]: r["current_name"] for r in rows}}


# ---------------------------------------------------------------------------
# Editing the list (admin)
# ---------------------------------------------------------------------------

# What the admin form can set. Everything else on a product keeps the value it has (or the default for a new one).
EDITABLE_FIELDS = (
    "name", "family", "short_label", "buying_model", "base_rate", "minimum_spend", "estimated_cpm_for_imps",
    "sizes", "tech_platform", "proposal_description", "notes", "is_addon",
    "minimum_flight_days_min", "minimum_flight_days_max", "sla_data_days", "sla_creative_days",
    "sla_activate_days", "sla_total_days", "margin_upper", "margin_lower", "wide_orbit_code",
    "estimated_impressions", "discloses_impressions", "national_supported",
)
_NULLABLE = {"base_rate", "estimated_cpm_for_imps", "sla_data_days", "sla_creative_days", "sla_activate_days", "sla_total_days"}
_INT_FIELDS = {"minimum_flight_days_min", "minimum_flight_days_max", "sla_data_days", "sla_creative_days",
               "sla_activate_days", "sla_total_days"}
_BOOL_FIELDS = {"is_addon", "estimated_impressions", "discloses_impressions", "national_supported"}
_FLOAT_FIELDS = {"base_rate", "minimum_spend", "estimated_cpm_for_imps", "margin_upper", "margin_lower"}
_TEXT_FIELDS = {"name", "family", "short_label", "sizes", "tech_platform", "proposal_description", "notes", "wide_orbit_code"}
_MAX_TEXT = {"name": 200, "family": 120, "short_label": 200, "sizes": 500, "tech_platform": 200, "wide_orbit_code": 200,
             "proposal_description": 5000, "notes": 5000}
# Text pasted from Word / PDF carries control characters that openpyxl refuses to write — one such product would
# break /api/generate for every proposal that uses it — and a NUL is rejected by Postgres.
_CONTROL_CHARS = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# What the NUMERIC columns hold (and sane business limits within them).
_RANGES = {"base_rate": (0, 1e9), "minimum_spend": (0, 1e9), "estimated_cpm_for_imps": (0, 1e6),
           "margin_upper": (0, 1), "margin_lower": (0, 1)}
_INT_MAX = 100000


def product_to_form(p: Product) -> dict:
    """A product as the admin form shows it (flat, JSON-friendly)."""
    out = {k: getattr(p, k) for k in EDITABLE_FIELDS if hasattr(p, k)}
    out["minimum_flight_days_min"], out["minimum_flight_days_max"] = p.minimum_flight_days[0], p.minimum_flight_days[1]
    return out


def _label(key: str) -> str:
    return key.replace("_", " ")


def _shown(n: float) -> str:
    return f"{n:,.0f}" if abs(n) >= 1000 else f"{n:g}"


def _number(key: str, value) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{_label(key).capitalize()} must be a number.") from None
    if not math.isfinite(number):
        raise ValueError(f"{_label(key).capitalize()} must be a real number.")
    low, high = _RANGES.get(key) or ((0, _INT_MAX) if key in _INT_FIELDS or key.startswith("minimum_flight_days") else (0, 1e9))
    if not (low <= number <= high):
        raise ValueError(f"{_label(key).capitalize()} must be between {_shown(low)} and {_shown(high)}.")
    return number


def _build_product(fields: dict, base: Optional[Product]) -> Product:
    """`base` (or a blank product) with the provided `fields` laid over it, validated. ValueError carries a
    planner-readable message. A key that is absent keeps the base value; a nullable key sent as None clears it."""
    data = asdict(base) if base is not None else {
        "family": "", "name": "", "short_label": "Product", "proposal_description": "", "sizes": "Custom",
        "buying_model": "Fixed", "base_rate": None, "estimated_impressions": False, "discloses_impressions": True,
        "minimum_spend": 0.0, "minimum_flight_days": (7, 90), "sla_data_days": None, "sla_creative_days": None,
        "sla_activate_days": None, "sla_total_days": None, "media_allocation_pct": 0.0, "margin_upper": 0.5,
        "margin_lower": 0.3, "tech_platform": "", "wide_orbit_code": "", "billing_interval": "Monthly",
        "billing_calendar": "Standard/prorated", "billing_source": "1st Party Actual", "national_supported": True,
        "notes": "", "estimated_cpm_for_imps": None, "hispanic_targeting_forced": None,
        "cannabis_policy": "not_allowed", "political_policy": "allowed", "is_addon": False,
    }
    flight = list(data.get("minimum_flight_days") or (7, 90))
    for key in EDITABLE_FIELDS:
        if key not in fields:
            continue
        value = fields[key]
        if key in ("minimum_flight_days_min", "minimum_flight_days_max"):
            if value not in (None, ""):
                days = _number(key, value)
                if days != int(days):
                    raise ValueError(f"{_label(key).capitalize()} must be a whole number of days.")
                flight[0 if key.endswith("min") else 1] = int(days)
            continue
        if isinstance(value, str):
            value = _CONTROL_CHARS.sub("", value).strip()
        if value in (None, "") and key in _NULLABLE:
            data[key] = None
        elif key in _BOOL_FIELDS:
            data[key] = bool(value)
        elif key in _INT_FIELDS:
            number = _number(key, value)
            if number != int(number):
                raise ValueError(f"{_label(key).capitalize()} must be a whole number.")
            data[key] = int(number)
        elif key in _FLOAT_FIELDS:
            if value in (None, ""):
                if key == "minimum_spend":
                    data[key] = 0.0
            else:
                data[key] = _number(key, value)
        elif key in _TEXT_FIELDS:
            if value is not None:
                if len(str(value)) > _MAX_TEXT[key]:
                    raise ValueError(f"{_label(key).capitalize()} is too long (max {_MAX_TEXT[key]} characters).")
                data[key] = str(value)
        elif value is not None:
            data[key] = value
    data["minimum_flight_days"] = (flight[0], flight[1])
    name = re.sub(r"\s+", " ", data.get("name") or "").strip()
    family = re.sub(r"\s+", " ", data.get("family") or "").strip()
    if not name:
        raise ValueError("Product name is required.")
    if not family:
        raise ValueError("Family is required.")
    if data.get("buying_model") not in ("CPM", "CPP", "Fixed"):
        raise ValueError("Buying model must be one of CPM, CPP, or Fixed.")
    if flight[0] < 0 or flight[1] > _INT_MAX or flight[0] > flight[1]:
        raise ValueError("Minimum flight days: the first number can't be larger than the second (and both must be 0 or more).")
    data["name"], data["family"] = name, family
    # A new product's Wide Orbit code starts as its name; a code that's already there (an edit, or a copy of a
    # product whose code is shared) is left exactly as it is.
    if not (data.get("wide_orbit_code") or "").strip():
        data["wide_orbit_code"] = name
    return Product(**data)


def _canonical_family(family: str, known: list[str]) -> str:
    """The family as it's already spelled elsewhere in the list (case-insensitively), so "online video" files
    under "Online Video" instead of starting a look-alike family."""
    for existing in known:
        if existing.casefold() == family.casefold():
            return existing
    return family


def _require_table() -> list[dict]:
    """The product rows, for a write. Loading them also SEEDS an empty table — which must happen before the first
    write, or that write would make the table non-empty and the seed would never run. RuntimeError when the table
    can't be reached."""
    rows = load_product_rows()
    if any(r["id"] is None for r in rows):
        raise RuntimeError("The products table isn't available — apply the latest schema.sql and try again.")
    return rows


def list_products() -> list[dict]:
    """Admin view: [{id, ...form fields}] for every non-deleted product, in display order. RuntimeError when
    the table can't be reached (nothing to edit)."""
    rows = _require_table()
    return [{"id": r["id"], **product_to_form(r["product"])} for r in rows if not r["is_deleted"]]


def _row(conn, product_id: int) -> Optional[dict]:
    row = conn.execute(
        f"SELECT id, is_deleted, sort_order, {', '.join(_PRODUCT_COLUMNS)} FROM products WHERE id = %s", (product_id,)
    ).fetchone()
    if row is None:
        return None
    return {"id": row["id"], "is_deleted": bool(row["is_deleted"]), "sort_order": row["sort_order"],
            "product": _row_to_product(row)}


def _same_name(conn, name: str, exclude_id: Optional[int] = None) -> list[dict]:
    """Rows whose name matches `name` ignoring case (names are unique that way: the parser matches
    case-insensitively, so two names differing only by case could never both be picked from pasted text)."""
    rows = conn.execute("SELECT id, is_deleted, name FROM products").fetchall()
    return [r for r in rows if r["name"].casefold() == name.casefold() and r["id"] != exclude_id]


def _db_errors():
    import psycopg
    return psycopg.errors


def create_product(fields: dict, copy_from_id: Optional[int] = None) -> dict:
    """Add a product — blank, or a COPY of `copy_from_id` with `fields` laid over it (a quick variant). A copy
    sits right after its source. Returns {id, ...form fields}."""
    rows = _require_table()
    errors = _db_errors()
    try:
        with get_connection() as conn:
            source = _row(conn, copy_from_id) if copy_from_id is not None else None
            if copy_from_id is not None and source is None:
                raise ValueError("The product to copy no longer exists.")
            product = _build_product(fields, source["product"] if source else None)
            product = replace(product, family=_canonical_family(product.family, [r["product"].family for r in rows]))
            clash = _same_name(conn, product.name)
            live = [r for r in clash if not r["is_deleted"]]
            if live:
                raise ValueError(f"A product named '{product.name}' already exists.")
            revive = clash[0] if clash else None            # a deleted product of the same name: reuse its row
            if source is not None:
                order = source["sort_order"] + 1
                conn.execute("UPDATE products SET sort_order = sort_order + 1 WHERE sort_order >= %s", (order,))
            else:
                order = conn.execute("SELECT COALESCE(MAX(sort_order), -1) + 1 AS n FROM products").fetchone()["n"]
            if revive is not None:
                sets = ", ".join(f"{c} = %s" for c in _PRODUCT_COLUMNS)
                conn.execute(f"UPDATE products SET {sets}, is_deleted = FALSE, sort_order = %s, updated_at = now() WHERE id = %s",
                             _product_params(product) + [order, revive["id"]])
                new_id = revive["id"]
            else:
                placeholders = ", ".join(["%s"] * (len(_PRODUCT_COLUMNS) + 1))
                new_id = conn.execute(
                    f"INSERT INTO products (sort_order, {', '.join(_PRODUCT_COLUMNS)}) VALUES ({placeholders}) RETURNING id",
                    [order] + _product_params(product),
                ).fetchone()["id"]
    except errors.UniqueViolation:
        raise ValueError("A product with that name already exists.") from None
    except errors.DataError:
        raise ValueError("One of the numbers or texts is too large or isn't valid.") from None
    invalidate_product_caches(keep_stale=True)
    return {"id": new_id, **product_to_form(product), "copied_from": source["product"].name if source else None}


def update_product(product_id: int, fields: dict) -> dict:
    """Edit any field of a product. A new name is recorded as an alias, so the old one keeps resolving in saved
    proposals and pasted requests."""
    rows = _require_table()
    errors = _db_errors()
    try:
        with get_connection() as conn:
            row = _row(conn, product_id)
            if row is None or row["is_deleted"]:
                raise LookupError("Product not found.")
            old = row["product"]
            product = _build_product(fields, old)
            product = replace(product, family=_canonical_family(product.family, [r["product"].family for r in rows]))
            if product.name != old.name:
                clash = _same_name(conn, product.name, exclude_id=product_id)
                if any(not r["is_deleted"] for r in clash):
                    raise ValueError(f"A product named '{product.name}' already exists.")
                if clash:
                    raise ValueError(f"A deleted product already uses the name '{product.name}' (saved proposals still point at it) "
                                     "— pick another name, or add a new product with that name to bring it back.")
            sets = ", ".join(f"{c} = %s" for c in _PRODUCT_COLUMNS)
            conn.execute(f"UPDATE products SET {sets}, updated_at = now() WHERE id = %s", _product_params(product) + [product_id])
    except errors.UniqueViolation:
        raise ValueError("A product with that name already exists.") from None
    except errors.DataError:
        raise ValueError("One of the numbers or texts is too large or isn't valid.") from None
    if product.name != old.name:
        try:
            record_product_alias(old.name, product.name)
        except Exception as exc:  # noqa: BLE001 — the rename itself is saved; only the old-name lookup is lost
            log.warning("couldn't record the alias %r -> %r: %s", old.name, product.name, exc)
    invalidate_product_caches(keep_stale=True)
    return {"id": product_id, **product_to_form(product)}


def delete_product(product_id: int) -> Optional[str]:
    """Remove a product from every list. The row stays, hidden, only so a proposal that already uses it keeps
    resolving it by name. Returns the product's name, or None if it wasn't there."""
    _require_table()
    with get_connection() as conn:
        row = _row(conn, product_id)
        if row is None or row["is_deleted"]:
            return None
        conn.execute("UPDATE products SET is_deleted = TRUE, updated_at = now() WHERE id = %s", (product_id,))
    invalidate_product_caches(keep_stale=True)
    return row["product"].name


def bulk_upsert_products(items: list[dict]) -> dict:
    """Add or update many products in ONE transaction. items: [{"line": n, "fields": {...}}], matched to the
    list by name (case-insensitively): a match is updated — and left untouched when nothing in it changed — and
    anything else is added. A bad row is reported and skipped (each row runs in its own savepoint), never fatal."""
    rows = _require_table()
    errors = _db_errors()
    import psycopg
    known_families = [r["product"].family for r in rows]
    live = {r["product"].name.casefold(): r for r in rows if not r["is_deleted"]}
    tombstones = {r["product"].name.casefold(): r for r in rows if r["is_deleted"]}
    created, updated, unchanged, problems = [], [], [], []
    with get_connection() as conn:
        next_order = conn.execute("SELECT COALESCE(MAX(sort_order), -1) + 1 AS n FROM products").fetchone()["n"]
        for item in items:
            line, fields = item["line"], item["fields"]
            name = (fields.get("name") or "").strip()
            try:
                match = live.get(name.casefold())
                if match is not None:
                    fields = {k: v for k, v in fields.items() if k != "name"}     # matched by name: never a rename by case
                product = _build_product(fields, match["product"] if match else None)
                product = replace(product, family=_canonical_family(product.family, known_families))
                if match is not None and product == match["product"]:
                    unchanged.append(product.name)
                    continue
                with conn.transaction():                       # a savepoint: this row alone rolls back on a DB error
                    if match is not None:
                        sets = ", ".join(f"{c} = %s" for c in _PRODUCT_COLUMNS)
                        conn.execute(f"UPDATE products SET {sets}, updated_at = now() WHERE id = %s",
                                     _product_params(product) + [match["id"]])
                    elif product.name.casefold() in tombstones:
                        dead = tombstones.pop(product.name.casefold())
                        sets = ", ".join(f"{c} = %s" for c in _PRODUCT_COLUMNS)
                        conn.execute(f"UPDATE products SET {sets}, is_deleted = FALSE, sort_order = %s, updated_at = now() WHERE id = %s",
                                     _product_params(product) + [next_order, dead["id"]])
                        next_order += 1
                    else:
                        placeholders = ", ".join(["%s"] * (len(_PRODUCT_COLUMNS) + 1))
                        new_id = conn.execute(
                            f"INSERT INTO products (sort_order, {', '.join(_PRODUCT_COLUMNS)}) VALUES ({placeholders}) RETURNING id",
                            [next_order] + _product_params(product),
                        ).fetchone()["id"]
                        live[product.name.casefold()] = {"id": new_id, "is_deleted": False, "product": product}
                        next_order += 1
                (updated if match is not None else created).append(product.name)
                if match is not None:
                    live[product.name.casefold()] = {**match, "product": product}
            except ValueError as exc:
                problems.append(f"Row {line} ('{name}'): {exc}")
            except psycopg.Error as exc:
                problems.append(f"Row {line} ('{name}'): the database refused it ({exc.__class__.__name__}).")
    invalidate_product_caches(keep_stale=True)
    return {"created": created, "updated": updated, "unchanged": unchanged, "errors": problems}


if __name__ == "__main__":
    print(f"The product list has {len(effective_catalog())} products across {len(families())} families.")
    for fam in families():
        print(f"  {fam}: {len(by_family(fam))} product(s)")
