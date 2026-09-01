"""
Notion paste parser for Entravision Digital Sales Planning Form output.

Notion (fed from Fillout) dumps form responses as plain text with field labels.
This parser is tolerant of:
  - blank fields
  - markdown decoration (**bold**, ##, >, italics)
  - varying separator lines (──────)
  - two known request "branches": main avails/proposal block, renewal block

It returns a ProposalRequest dataclass that the planner can review and edit
before generating the Excel proposal.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Optional


# ---------------------------------------------------------------------------
# Output schema
# ---------------------------------------------------------------------------

@dataclass
class ProductSpecifics:
    """Per-product extra fields the salesperson filled in."""
    facebook_ig: dict = field(default_factory=dict)
    sem: dict = field(default_factory=dict)
    ctv_ott: dict = field(default_factory=dict)
    dooh: dict = field(default_factory=dict)
    geofencing: dict = field(default_factory=dict)
    spotify: dict = field(default_factory=dict)
    audio_engage: dict = field(default_factory=dict)


@dataclass
class ProposalRequest:
    # Notion tracking ID — planner-entered, e.g. "EVC-4821". Drives the proposal
    # title/filename in place of the app's internal sequential counter.
    notion_id: str = ""

    # Header / requester
    requested_by: str = ""
    salesperson_market: str = ""
    salesperson_email: str = ""
    ccs: str = ""
    request_type: str = ""

    # Client
    client_name: str = ""
    client_website: str = ""
    agency_name: str = ""
    agency_fee: Optional[float] = None  # 0.0 - 0.99

    # Flight
    start_date: str = ""
    end_date: str = ""
    total_months: Optional[int] = None
    monthly_budget: Optional[float] = None
    tiered_budget: bool = False
    tier_1: str = ""
    tier_2: str = ""
    tier_3: str = ""
    tier_4: str = ""

    # Campaign
    campaign_goal: str = ""
    language: str = ""
    other_languages: str = ""
    geo: str = ""
    demo: str = ""
    behavioral: str = ""
    contextual: str = ""

    # Products
    products_selected_raw: str = ""
    products_selected: list = field(default_factory=list)
    specifics: ProductSpecifics = field(default_factory=ProductSpecifics)

    # Free text
    salesperson_comments: str = ""

    # Renewal branch
    renewal_ae_requesting: str = ""
    renewal_change_type: str = ""
    renewal_client: str = ""
    renewal_changes_description: str = ""
    renewal_campaign_dates: str = ""
    renewal_budget: str = ""
    renewal_due_date: str = ""

    # Parser warnings (e.g. "couldn't match product 'Foo' to catalog")
    warnings: list = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


def compose_target_fallback(request) -> str:
    """
    Build the campaign-level TARGET (column D) fallback used whenever a line
    item has no per-product target_override: Demo | Behavioral | Contextual,
    joined by " | " and skipping any that are blank. Falls back to "TBD" when
    none of the three are filled in. Shared by every Excel tab so the column
    never shows a bare "TBD" when the planner actually captured targeting.
    """
    if request is None:
        return "TBD"
    parts = []
    if getattr(request, "demo", ""):
        parts.append(request.demo)
    if getattr(request, "behavioral", ""):
        parts.append(request.behavioral)
    if getattr(request, "contextual", ""):
        parts.append(f"Contextual: {request.contextual}")
    return " | ".join(parts) if parts else "TBD"


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

# Strip markdown: **bold**, ## headers, > blockquotes, *italic*
_MD_STRIP_PATTERNS = [
    (re.compile(r"^\s*#{1,6}\s*", re.MULTILINE), ""),
    (re.compile(r"^\s*>\s*", re.MULTILINE), ""),
    (re.compile(r"\*\*(.+?)\*\*"), r"\1"),
    (re.compile(r"\*(.+?)\*"), r"\1"),
    (re.compile(r"_(.+?)_"), r"\1"),
]

_SEPARATOR_LINE = re.compile(r"^\s*[─-]{3,}\s*$", re.MULTILINE)


def _strip_markdown(text: str) -> str:
    """Remove markdown decoration so label matching is stable."""
    for pat, repl in _MD_STRIP_PATTERNS:
        text = pat.sub(repl, text)
    return text


def _clean(value: str) -> str:
    """Trim, collapse internal whitespace runs, remove trailing punctuation noise."""
    if value is None:
        return ""
    v = value.strip()
    # Drop trailing pipe-with-space that comes from "Budget: | something"
    v = v.strip("|").strip()
    return v


def _parse_bool(value: str) -> bool:
    v = _clean(value).lower()
    return v in ("true", "yes", "y", "1")


def _parse_int(value: str) -> Optional[int]:
    v = _clean(value)
    if not v:
        return None
    # Strip $ , and spaces
    v = v.replace("$", "").replace(",", "").strip()
    # Pull out the leading number rather than casting the whole string —
    # a planner writing "1 month" or "3 months" (trailing unit text) made
    # float(v) raise and silently fall back to the caller's default every
    # time, even though the actual number was right there.
    m = re.search(r"-?\d+(\.\d+)?", v)
    if not m:
        return None
    try:
        return int(round(float(m.group(0))))
    except ValueError:
        return None


def _parse_float(value: str) -> Optional[float]:
    v = _clean(value)
    if not v:
        return None
    v = v.replace("$", "").replace(",", "").strip()
    # Handle "15%" → 0.15
    pct = False
    if v.endswith("%"):
        pct = True
        v = v[:-1].strip()
    try:
        n = float(v)
        if pct:
            n = n / 100.0
        return n
    except ValueError:
        return None


def _parse_agency_fee(value: str) -> Optional[float]:
    """Agency fee may come as '15%', '0.15', '15', or blank."""
    v = _clean(value)
    if not v:
        return None
    n = _parse_float(v)
    if n is None:
        return None
    # If they typed "15" without %, assume percent
    if n > 1.0:
        n = n / 100.0
    if not 0.0 <= n < 1.0:
        return None
    return n


# ---------------------------------------------------------------------------
# Label-based extractor
# ---------------------------------------------------------------------------

def _extract_label(text: str, label: str) -> str:
    """
    Extract the value following a label like 'Client name:'.
    Value is everything from after the colon to end-of-line (or next labeled line).
    Returns "" if not found.
    """
    # Match label at start of line (possibly with leading whitespace),
    # capture until end of that line. Labels in Notion paste sit on their own line.
    # IMPORTANT: capture group uses [^\n]*, NOT .*?, because .*? with \s*$ at the
    # end can extend across newlines (since \s includes \n) — stealing the next
    # line's content when the field is empty.
    pattern = re.compile(
        rf"^[ \t]*{re.escape(label)}[ \t]*:[ \t]*([^\n]*?)[ \t]*$",
        re.MULTILINE | re.IGNORECASE,
    )
    m = pattern.search(text)
    if not m:
        return ""
    return _clean(m.group(1))


def _extract_label_raw(text: str, label: str) -> str:
    """
    Same match as _extract_label, but only whitespace-trimmed — WITHOUT
    _clean()'s leading/trailing "|" strip. Needed for the "Tier #1: X |
    Tier #2: Y" combined-line format: when the FIRST tier's own value is
    blank, the captured text starts with "| Tier #2: Y", and _clean()'s
    pipe-strip would delete that leading "|" before the caller ever gets to
    check for it — making the second tier's label look like it's just
    sitting there with no separator, so it gets misread as the first
    tier's value instead of being split out correctly.
    """
    pattern = re.compile(
        rf"^[ \t]*{re.escape(label)}[ \t]*:[ \t]*([^\n]*?)[ \t]*$",
        re.MULTILINE | re.IGNORECASE,
    )
    m = pattern.search(text)
    if not m:
        return ""
    return m.group(1).strip()


# Product catalog name aliases for matching the "Products selected" field
# Maps loose user phrasing → canonical catalog product name.
# Updated 2026-08 for the rate-card-derived 71-product catalog (see catalog.py
# module docstring) — several old canonical names were renamed or split into
# multiple new SKUs; where a product split into several variants, the alias
# below points at the most general one.
PRODUCT_ALIASES: dict[str, str] = {
    # Search
    "google ads": "Paid Search - SEM",
    "paid search": "Paid Search - SEM",
    "sem": "Paid Search - SEM",
    "bing ads": "Paid Search - SEM",
    "pmax": "Paid Search - SEM",             # Performance Max is now an included extension, not a separate SKU
    "performance max": "Paid Search - SEM",
    # Display
    "display": "eDigital Display",
    "edigital display": "eDigital Display",
    "display banner": "eDigital Display",
    "programmatic display": "eDigital Display",
    "precision display": "eDigital Display | Precision",
    "geofencing display": "eDigital Display | Geo Fence",
    "geo fence display": "eDigital Display | Geo Fence",
    # OLV
    "olv": "eDigital OLV",
    "online video": "eDigital OLV",
    "pre-roll": "eDigital OLV",
    "preroll": "eDigital OLV",
    "precision olv": "eDigital OLV | Precision",
    "geofencing video": "eDigital OLV | Geo Fence",
    "geo fence video": "eDigital OLV | Geo Fence",
    # YouTube
    "youtube ads": "YouTube Ads",
    "youtube preroll": "YouTube Ads",
    "youtube pre-roll": "YouTube Ads",
    "youtube trueview": "YouTube Ads",
    "trueview": "YouTube Ads",
    "youtube discovery": "YouTube Ads",
    "youtube audio": "YouTube Audio Ads",
    # CTV / OTT
    "connected tv (ott) - entravision plus": "Entravision Plus CTV | Premier",
    "connected tv (ott)": "Entravision Plus CTV | Premier",
    "ctv entravision plus": "Entravision Plus CTV | Premier",
    "entravision plus ctv": "Entravision Plus CTV | Premier",
    "ctv premier": "Entravision Plus CTV | Premier",
    "ctv sports": "Entravision Plus CTV | Premier | Live Sports",
    "sports content": "Entravision Plus CTV | Premier | Live Sports",
    "live sports": "Entravision Plus CTV | Premier | Live Sports",
    "live soccer": "Entravision Plus CTV | Premier | Live Soccer",
    "streaming sports": "Entravision Plus CTV | Premier | Streaming Sports",
    "vix": "Entravision Plus CTV | ViX",
    "roku": "Entravision Plus CTV | Roku",
    "youtube tv": "Entravision Plus CTV | YouTube TV",
    "amazon prime": "Entravision Plus CTV | Amazon Prime Video",
    "amazon prime video": "Entravision Plus CTV | Amazon Prime Video",
    "netflix": "Entravision Plus CTV | Netflix | Run of Network | :15",
    "netflix :30": "Entravision Plus CTV | Netflix | Run of Network | :30",
    "netflix age targeted": "Entravision Plus CTV | Netflix | Age Targeted | :15",
    "netflix gender targeted": "Entravision Plus CTV | Netflix | Gender Targeted | :15",
    "netflix genre targeted": "Entravision Plus CTV | Netflix | Genre Targeted | :15",
    "netflix spanish": "Entravision Plus CTV | Netflix | Hispanic Connect | :15",
    "netflix hispanic": "Entravision Plus CTV | Netflix | Hispanic Connect | :15",
    # Tentpole/FIFA/NFL package no longer has its own SKU in the 2026 rate
    # card — closest current analog is the Premier Live Sports package.
    "tentpole": "Entravision Plus CTV | Premier | Live Sports",
    "fifa": "Entravision Plus CTV | Premier | Live Soccer",
    "nfl": "Entravision Plus CTV | Premier | Live Sports",
    # Audio
    "spotify": "Spotify Ads",
    "spotify ads": "Spotify Ads",
    "audio engage": "Audio Engage",
    "audioengage": "Audio Engage",
    "audio engage precision": "Audio Engage | Precision",
    "o&o audio": "Entravision Audio | Local Stream | Station Specific",
    "station specific audio": "Entravision Audio | Local Stream | Station Specific",
    "klyy": "Entravision Audio | Local Stream | KLYY Jose | Spot",
    # Social
    "facebook": "Meta Ads",
    "instagram": "Meta Ads",
    "meta": "Meta Ads",
    "meta ads": "Meta Ads",
    "facebook/ig": "Meta Ads",
    # O&O-page Meta/TikTok products are now split by content type
    # (client-provided vs. Creative-Labs-produced) rather than by page.
    "evc radio meta": "Branded Content | Meta | Ads",
    "noticias ya": "Branded Content | Meta | Ads",
    "branded content meta": "Branded Content | Meta | Ads",
    "creative labs meta": "Creative Labs | Meta | Ads",
    "shoboy": "Talent Connect | Shoboy",
    "erazo": "Talent Connect | Erazo",
    "genio lucas": "Talent Connect | Genio Lucas",
    "piolin": "Talent Connect | Piolin",
    "talent connect": "Talent Connect | Shoboy",
    "tiktok": "TikTok Ads",
    "tiktok on radio pages": "Branded Content | TikTok | Ads",
    "linkedin": "LinkedIn Ads",
    # Email
    "email": "Email Marketing | CPP",
    "email marketing": "Email Marketing | CPP",
    "email retargeting": "Email Marketing | Display Retargeting",
    "email client list": "Email Marketing | Client List Match",
    "email hashed file": "Email Marketing | Hashed File Onboarding",
    "email matchback": "Email Marketing | Matchback",
    "email postal match": "Email Marketing | Postal Match",
    # DOOH
    "dooh": "Digital Out Of Home",
    "digital out of home": "Digital Out Of Home",
    "out-of-home": "Digital Out Of Home",
    # Services
    "landing page": "Web Services - Landing Pages",
    "web services": "Web Services - Landing Pages",
    "microsite": "Web Services - Microsite",
    "creative services": "Creative Services",
    # Measurement — brand lift / foot traffic / attribution studies now
    # consolidate under one "Measurement Study" line in the 2026 rate card.
    "brand lift": "Measurement Study",
    "foot traffic": "Measurement Study",
    "attribution": "Measurement Study",
    "measurement study": "Measurement Study",
    "call tracking": "Call Tracking Numbers",
    # Sponsorship
    "sponsorship": "Sponsorship - Services Sponsorships",
    "sponsorship display": "Sponsorship - Services Sponsorships",
    "cw sponsorship": "Sponsorship - CW Video Sponsorship",
    "nbc sports stream": "Sponsorship - NBC Sports Stream",
    "fox sports go": "Sponsorship - Fox Sports Go Video Sponsorship",
}


_STOPWORDS = {"the", "a", "an", "and", "or", "on", "in", "for", "of", "-"}


def _token_set(s: str) -> set:
    return {t for t in re.findall(r"[a-z0-9]+", s.lower()) if t not in _STOPWORDS}


def _match_product(raw: str, catalog_names: list[str]) -> Optional[str]:
    """
    Try to map a raw user-supplied product string to a catalog name.
    Strategy:
      1. exact (case-insensitive) match on catalog names
      2. alias map (case-insensitive substring)
      3. token-set match: the raw text and a catalog name overlap if one's
         tokens fully contain the other's (word order/insertions don't
         matter — e.g. "YouTube Video Ads" matches "YouTube Ads" because
         {youtube, ads} ⊆ {youtube, video, ads}); the highest-overlap-ratio
         catalog name wins, so a more specific/exact name is preferred over
         a looser one that happens to share fewer tokens
      4. substring fuzzy (legacy fallback): catalog name is a literal
         substring of the raw text, or vice versa
    """
    if not raw:
        return None
    r = raw.strip().lower()
    if not r:
        return None

    # 1. exact
    for cn in catalog_names:
        if cn.lower() == r:
            return cn

    # 2. alias
    for alias, canonical in PRODUCT_ALIASES.items():
        if alias in r:
            if canonical in catalog_names:
                return canonical

    # 3. token-set overlap — order- and insertion-tolerant
    raw_tokens = _token_set(r)
    best_name, best_score = None, 0.0
    if raw_tokens:
        for cn in catalog_names:
            cn_tokens = _token_set(cn)
            if not cn_tokens:
                continue
            if cn_tokens <= raw_tokens or raw_tokens <= cn_tokens:
                overlap = cn_tokens & raw_tokens
                score = len(overlap) / max(len(cn_tokens), len(raw_tokens))
                if score > best_score:
                    best_score, best_name = score, cn
        if best_name and best_score >= 0.5:
            return best_name

    # 4. substring fuzzy: catalog contains raw, or raw contains a distinctive catalog token
    for cn in catalog_names:
        if r in cn.lower() or cn.lower() in r:
            return cn

    return None


def _parse_products(raw: str, catalog_names: list[str]) -> tuple[list[str], list[str]]:
    """
    Parse the comma- or newline-separated list of products selected.
    Returns (matched_canonical_names, warnings_for_unmatched).
    """
    if not raw:
        return [], []
    # Split on commas, newlines, and ' AND ' which the form sometimes uses
    pieces = re.split(r"[,\n]| AND ", raw, flags=re.IGNORECASE)
    matched: list[str] = []
    warnings: list[str] = []
    for piece in pieces:
        piece = piece.strip(" -•*")
        if not piece:
            continue
        canonical = _match_product(piece, catalog_names)
        if canonical and canonical not in matched:
            matched.append(canonical)
        elif not canonical:
            warnings.append(f"Could not match product '{piece}' to AdFlo catalog — planner please add manually.")
    return matched, warnings


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_notion(text: str, catalog_names: list[str]) -> ProposalRequest:
    """
    Parse Notion paste into a ProposalRequest.

    Args:
        text: The raw Notion paste from the planner.
        catalog_names: List of canonical product names from catalog.CATALOG
                       (passed in so this parser stays decoupled).

    Returns:
        ProposalRequest with as much filled in as we could pull.
    """
    if not text:
        return ProposalRequest()

    # Normalize line endings, strip markdown
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _strip_markdown(text)

    req = ProposalRequest()

    # Header / requester
    req.requested_by = _extract_label(text, "Requested by")
    req.salesperson_market = _extract_label(text, "Salesperson market")
    req.salesperson_email = _extract_label(text, "Salesperson email")
    req.ccs = _extract_label(text, "CCs")
    req.request_type = _extract_label(text, "Request type")

    # Client
    req.client_name = _extract_label(text, "Client name")
    req.client_website = _extract_label(text, "Client website")
    req.agency_name = _extract_label(text, "Agency name")
    req.agency_fee = _parse_agency_fee(_extract_label(text, "Agency Fee"))

    # Flight
    req.start_date = _extract_label(text, "Start date")
    req.end_date = _extract_label(text, "End date")
    req.total_months = _parse_int(_extract_label(text, "Total months"))
    req.monthly_budget = _parse_float(_extract_label(text, "Monthly budget"))
    req.tiered_budget = _parse_bool(_extract_label(text, "Tiered budget?"))

    # Tier #1: foo | Tier #2: bar    — these sit on combined lines. Uses
    # _extract_label_raw (not _extract_label) so a blank first tier doesn't
    # lose its leading "|" to _clean()'s pipe-stripping before we get a
    # chance to split on it — see that function's docstring.
    tier_line_1 = _extract_label_raw(text, "Tier #1")
    if "|" in tier_line_1:
        left, _, right = tier_line_1.partition("|")
        req.tier_1 = _clean(left)
        right = right.strip()
        # right might look like "Tier #2: ..."
        m = re.match(r"Tier\s*#?2\s*:\s*(.*)", right, re.IGNORECASE)
        if m:
            req.tier_2 = _clean(m.group(1))
        else:
            req.tier_2 = _extract_label(text, "Tier #2")
    else:
        req.tier_1 = _clean(tier_line_1)
        req.tier_2 = _extract_label(text, "Tier #2")

    tier_line_3 = _extract_label_raw(text, "Tier #3")
    if "|" in tier_line_3:
        left, _, right = tier_line_3.partition("|")
        req.tier_3 = _clean(left)
        right = right.strip()
        m = re.match(r"Tier\s*#?4\s*:\s*(.*)", right, re.IGNORECASE)
        if m:
            req.tier_4 = _clean(m.group(1))
        else:
            req.tier_4 = _extract_label(text, "Tier #4")
    else:
        req.tier_3 = _clean(tier_line_3)
        req.tier_4 = _extract_label(text, "Tier #4")

    # Campaign
    req.campaign_goal = _extract_label(text, "Chosen campaign goal")
    if not req.campaign_goal:
        # Fillout sometimes formats it as just "Chosen campaign goal:" on its own line
        # followed by the value on next line — handle by walking lines.
        req.campaign_goal = _extract_after_header(text, "Chosen campaign goal")

    req.language = _extract_label(text, "Language of campaign")
    req.other_languages = _extract_label(text, "(Other languages")  # has weird parens
    req.geo = _extract_label(text, "Geo")
    req.demo = _extract_label(text, "Demo")
    req.behavioral = _extract_label(text, "Behavioral")
    req.contextual = _extract_label(text, "Contextual")

    # Products selected — value follows the label on the same line
    req.products_selected_raw = _extract_label(text, "Products selected")
    products, prod_warnings = _parse_products(req.products_selected_raw, catalog_names)
    req.products_selected = products
    req.warnings.extend(prod_warnings)

    # Product specifics — extract bundled blocks
    req.specifics.facebook_ig = {
        "goal": _extract_label(text, "Goal"),
        "strategy_type": _extract_label(text, "Strategy Type"),
        "paid": _extract_label(text, "Paid?"),
        "creative_type": _extract_label(text, "Creative Type"),
        "page_type": _extract_label(text, "Page type"),
        "selected_page": _extract_label(text, "Selected page"),
    }
    req.specifics.sem = {
        "wants_kw_forecast": _parse_bool(_extract_label(text, "Wants Google Ads KW Forecast?")),
        "keyword_language": _extract_label(text, "English or Spanish Keywords"),
        "budget": _extract_label(text, "Budget"),
        "product_to_promote": _extract_label(text, "Product client wishes to promote"),
        "is_pmax": _parse_bool(_extract_label(text, "Is it a PMax campaign?")),
        "pmax_goal": _extract_label(text, "PMax conversion goal"),
    }
    req.specifics.ctv_ott = {
        "device_type": _extract_label(text, "Device type"),
        "inventory_language": _extract_label(text, "Inventory language"),
        "netflix_targeting": _extract_label(text, "Netflix targeting"),
    }
    req.specifics.dooh = {
        "details": _extract_label(text, "(Budget, Industry, Screen Types)"),
    }
    req.specifics.geofencing = {
        "radius_fences": _extract_label(text, "Radius/Fences"),
    }
    req.specifics.spotify = {
        "industry": _extract_label(text, "Industry"),
    }
    req.specifics.audio_engage = {
        "creative_language": _extract_label(text, "Creative language"),
    }

    # Free-text salesperson comments
    req.salesperson_comments = _extract_label(text, "Additional comments from the salesperson")
    if not req.salesperson_comments:
        req.salesperson_comments = _extract_after_header(text, "Additional comments from the salesperson")

    # Renewal branch
    req.renewal_ae_requesting = _extract_label(text, "AE or AM Requesting")
    req.renewal_change_type = _extract_label(text, "Type of changes request")
    req.renewal_client = _extract_label(text, "Client")
    req.renewal_changes_description = _extract_label(text, "Changes description")
    if not req.renewal_changes_description:
        req.renewal_changes_description = _extract_after_header(text, "Changes description")
    req.renewal_campaign_dates = _extract_label(text, "Campaign dates")
    req.renewal_budget = _extract_label(text, "Renewal budget")
    req.renewal_due_date = _extract_label(text, "Due date")

    # If this is a renewal and main client_name was blank, promote renewal_client
    if req.renewal_client and not req.client_name:
        req.client_name = req.renewal_client

    return req


def _extract_after_header(text: str, header: str) -> str:
    """
    Some fields put the value on the line AFTER the header, especially when the
    salesperson wrote a multi-line answer. We grab everything until the next
    'Label:' line or a separator.
    """
    pattern = re.compile(
        rf"^\s*{re.escape(header)}\s*:\s*\n(.*?)(?=\n\s*[A-Z][^\n:]{{0,80}}:|\n\s*[─-]{{3,}}|\Z)",
        re.MULTILINE | re.IGNORECASE | re.DOTALL,
    )
    m = pattern.search(text)
    if not m:
        return ""
    return _clean(m.group(1))


# ---------------------------------------------------------------------------
# Routing helpers (used by the API layer)
# ---------------------------------------------------------------------------

REQUEST_TYPE_PROPOSAL = {
    "renewal proposal request",
    "renewal proposal/plan for an existing client",
    "proposal page just to sign (avails not included)",
    "proposal page just to sign",
    "proposal page to sign",
    "proposal page with avails / estimates included",
    "proposal with avails",
    "full presentation (deck + avails + proposal included)",
    "full presentation",
}

REQUEST_TYPE_AVAILS_ONLY = {
    "avails / estimates only (i don't need a proposal right now)",
    "avails / estimates only",
    "avails/estimates only",
    "quick strategic question / need guidance",
    "quick question",
}


def classify_output_tabs(request_type: str, products_selected: list[str], has_agency_fee: bool) -> dict:
    """
    Decide which Excel tabs to produce based on the parsed request.

    Returns dict with keys:
        net (bool)            - Proposal A
        wsections (bool)      - Proposal A (wsections) — used when complexity is high
        gross (bool)          - Proposal A (Gross) — used when there's an agency fee
        avails_only (bool)    - Avails-Only tab only
        dooh_summary (bool)
        dooh_screenlist (bool)
    """
    rt = (request_type or "").strip().lower()

    has_dooh = any("DOOH" in p or "Out-of-Home" in p for p in products_selected)
    is_avails_only = rt in REQUEST_TYPE_AVAILS_ONLY

    if is_avails_only:
        return {
            "net": False,
            "wsections": False,
            "gross": False,
            "avails_only": True,
            "dooh_summary": has_dooh,
            "dooh_screenlist": has_dooh,
        }

    # Default proposal output: always emit Net.
    # Emit wsections when there are >= 4 distinct products (complexity threshold).
    # Emit Gross when an agency fee is present.
    complex_plan = len(products_selected) >= 4
    return {
        "net": True,
        "wsections": complex_plan,
        "gross": has_agency_fee,
        "avails_only": False,
        "dooh_summary": has_dooh,
        "dooh_screenlist": has_dooh,
    }
