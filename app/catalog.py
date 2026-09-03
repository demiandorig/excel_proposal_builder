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
Use the admin rate override UI (/admin) to correct base_rate /
minimum_spend / estimated_cpm_for_imps without a code change, or the admin
"Add Product" form for anything genuinely missing from this rate card.
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
        proposal_description='Variants (contact planning for exact tier): Search - AdWords - SEM: $1000 CPP; Search - SEM PRO: $5000 CPP.',
        sizes='Custom',
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
        sizes='Custom',
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
        name='YouTube Ads',
        short_label='Awareness / Retargeting',
        proposal_description='In-stream and Video discovery ad. Search results: Ad is shown when people search for a specific topic. Targeting availability: Demographics, Interests, Video Remarketing, Topics, Keywords (keyword targeting option depends on your ad format). 6 creatives max. Skippable, NonSkippable and Bumper ads are available, each ad type requires a separate campaign (ie WO line ID) Each language target will need separate campaigns (ie WO line IDs)',
        sizes='Custom',
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
        wide_orbit_code='YouTube Ads',
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
        sizes='Custom',
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
        sizes='Custom',
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
        sizes='Custom',
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
        sizes='Custom',
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
        sizes='Custom',
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
        sizes='Custom',
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
        sizes='Custom',
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
        sizes='Custom',
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
        sizes='Custom',
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
        sizes='Custom',
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
        sizes='Custom',
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
        sizes='Custom',
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
        sizes='Custom',
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
        sizes='Custom',
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
        sizes='Custom',
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
        sizes='Custom',
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
        sizes='Custom',
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
        sizes='Custom',
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
        sizes='Custom',
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
        sizes='Custom',
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
        sizes='Custom',
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
        sizes='Custom',
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
        sizes='Custom',
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
        sizes='Custom',
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
        sizes='Custom',
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
        sizes='Custom',
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
        sizes='Custom',
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
        sizes='Custom',
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
        sizes='Custom',
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
        sizes='Custom',
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
        sizes='Custom',
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
        sizes='Custom',
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
        sizes='Custom',
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
        sizes='Custom',
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
        sizes='Custom',
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
        sizes='Custom',
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
        short_label='Awareness / Engagement',
        proposal_description='LinkedIn is a professional social networking platform for B2B marketing. It is also a good place to reach decision-makers. You can reach a reach a qualified audience based on job title, industry, company name, and more.',
        sizes='Custom',
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
        sizes='Custom',
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
        sizes='Custom',
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
        sizes='Custom',
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
        sizes='Custom',
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
        sizes='Custom',
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
        sizes='Custom',
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
        sizes='Custom',
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
        sizes='Custom',
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
        sizes='Custom',
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
        sizes='Custom',
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
        sizes='Custom',
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
        sizes='Custom',
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
        sizes='Custom',
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
        sizes='Custom',
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
        sizes='Custom',
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
        sizes='Custom',
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
        sizes='Custom',
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
        # SLA breakdown: an admin-added product has no rate-card SLA columns
        # to source these from, so they stay unknown (None) unless the
        # caller explicitly provides one — matches the Optional[int] typing
        # rather than fabricating a business-day figure with no real basis.
        sla_data_days=fields.get("sla_data_days"),
        sla_creative_days=fields.get("sla_creative_days"),
        sla_activate_days=fields.get("sla_activate_days"),
        sla_total_days=fields.get("sla_total_days"),
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
        is_addon=bool(fields.get("is_addon", False)),
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
