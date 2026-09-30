"""
Constanza's geo resolver — turns a campaign's free-text geo-targeting
string (ProposalRequest.geo / TierModel.geo, e.g. "National",
"Los Angeles DMA", "CA, NV, AZ", "95814", "Sacramento the city") into a
structured list of weighted areas to run the avails math against.

Geo has never been anything but an opaque string here (see
notion_parser.py's `_extract_label(text, "Geo")` and TierModel.geo) — no
DMA/state validation existed before Constanza. This resolver is
deliberately conservative: anything it can't confidently match comes back
as `unresolved` rather than a silent guess, and anything genuinely
ambiguous (same-named city in two states, "Sacramento" the city vs. the
DMA) surfaces candidates for the planner to pick rather than guessing.

Phase 2 replaces the flat `dmas: list[str]` from Phase 1 with a list of
ResolvedArea — because a ZIP/county/congressional-district/city is
usually a FRACTION of a DMA's population, not the whole thing, and the
avails math (constanza_engine.py) needs that fraction, not just a yes/no
"in scope" flag. Sub-DMA and international resolution (ZIP, county,
congressional district, city, country) live in constanza_geo_crosswalk.py
and constanza_geo_international.py — this module keeps its original
DMA/state matching untouched and dispatches to those for everything else.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.data.constanza import DMA, load_dma_reference

_NATIONAL_WORDS = {"national", "nationwide", "us", "usa", "united states", "all markets", "all", ""}

_STATE_NAMES_TO_ABBR = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA",
    "colorado": "CO", "connecticut": "CT", "delaware": "DE", "district of columbia": "DC",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID", "illinois": "IL",
    "indiana": "IN", "iowa": "IA", "kansas": "KS", "kentucky": "KY", "louisiana": "LA",
    "maine": "ME", "maryland": "MD", "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC", "south dakota": "SD",
    "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT", "virginia": "VA",
    "washington": "WA", "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
}
_VALID_STATE_ABBR = set(_STATE_NAMES_TO_ABBR.values())

# Trailing words that don't change what market someone means (
# "Los Angeles DMA" / "Los Angeles market" / "Los Angeles metro" all mean
# the DMA "Los Angeles").
_DMA_SUFFIX_RE = re.compile(r"\b(dma|market|metro|area|designated market area)\b\.?", re.IGNORECASE)

_SPLIT_RE = re.compile(r"\s*(?:,|;|/|&|\band\b|\n)\s*", re.IGNORECASE)


@dataclass(frozen=True)
class ResolvedArea:
    """One geography contributing to a GeoResolution — a whole DMA/state
    match (dma_share=1.0), or a fraction of one (a ZIP/county/CD/city that
    represents only part of its parent DMA's population), or a country
    with no DMA concept at all (dma=None).
    """
    dma: str | None              # None only for non-US, country-level areas
    area_population: float       # THIS area's own population — never the parent DMA's full population
    dma_share: float             # area_population / parent DMA's total bundled population; 1.0 for a whole DMA/state match
    source_type: str             # "dma" | "state" | "zip" | "county" | "congressional_district" | "city" | "country"
    apportionment: str           # "exact" | "hud_ratio" | "census_relationship_file" | "single_county_place" | "tract_overlap" | "population_share_national"
    state: str | None = None     # primary state (2-letter), for state-level proxies (Hispanic %, etc.) — None for country areas
    label: str | None = None     # human-readable area name when it differs from `dma` (e.g. "Sacramento (city)" vs. dma="Sacramento-Stockton-Modesto")
    geo_id: str | None = None    # the exact geography's own key into its ACS bundle (ZCTA/county FIPS/place GEOID/CD GEOID) — lets constanza_audience.py use THIS area's real age/sex data instead of a national average. None for whole-DMA/state/country areas, which have no single ACS geo_id of their own.


@dataclass
class GeoResolution:
    input_text: str
    # "national" | "exact_dma" | "state_list" | "mixed" | "zip" | "county" |
    # "congressional_district" | "city" | "country" | "unresolved"
    method: str
    areas: list[ResolvedArea] = field(default_factory=list)
    unmatched_tokens: list[str] = field(default_factory=list)
    # "high" | "medium" | "low_confidence_international" | "unresolved"
    geo_confidence: str = "unresolved"
    # Candidates needing a planner pick — same-type name collisions
    # ("Springfield" in IL/MO/MA/...) or cross-type collisions ("Sacramento"
    # the DMA vs. "Sacramento" the city). Each entry:
    # {"input": str, "candidates": [{"label": str, "source_type": str, "resolve_hint": str}]}
    ambiguous: list[dict] = field(default_factory=list)

    @property
    def resolved(self) -> bool:
        return bool(self.areas)

    @property
    def dmas(self) -> list[str]:
        """Back-compat: unique DMA names, deduped, in first-seen order.
        Any caller that only needs "which DMAs are in scope" (not the
        sub-DMA apportionment) keeps working unchanged. Do NOT use this
        for weighting math — see constanza_engine._area_weights, which
        consumes `areas` directly so sub-DMA fractions aren't lost."""
        seen = set()
        return [a.dma for a in self.areas if a.dma and not (a.dma in seen or seen.add(a.dma))]


def _clean_token(token: str) -> str:
    token = _DMA_SUFFIX_RE.sub("", token)
    return token.strip(" .")


# Nielsen DMA names are frequently compound ("Dallas-Ft. Worth",
# "San Francisco-Oakland-San Jose", "Phoenix (Prescott)") — a planner
# typically types just the one city they mean ("Fresno", not
# "Fresno-Visalia"), so match candidates include both the full name and
# each of its component segments, not the full name alone.
_NAME_SPLIT_RE = re.compile(r"[-/,()]")

# Common abbreviation variants ("Ft. Worth" / "Fort Worth", "St. Louis" /
# "Saint Louis") — expanded to the same full word on BOTH sides before
# comparing, so matching stays a deterministic exact-match-after-
# normalization rather than a fuzzy similarity score. A pure similarity
# score (e.g. difflib) is too permissive here: unrelated short words can
# score high purely by sharing letters (e.g. "Neverland" ~ "Cleveland"),
# which is exactly the silent-wrong-guess this resolver is meant to avoid.
_ABBR_PAIRS = [
    (re.compile(r"\bft\.?\b", re.IGNORECASE), "fort"),
    (re.compile(r"\bst\.?\b", re.IGNORECASE), "saint"),
    (re.compile(r"\bmt\.?\b", re.IGNORECASE), "mount"),
]


def _normalize(s: str) -> str:
    s = s.lower()
    for pat, repl in _ABBR_PAIRS:
        s = pat.sub(repl, s)
    s = re.sub(r"[.\-/,()]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _dma_candidates(dma_name: str) -> list[str]:
    segments = [p.strip(" .") for p in _NAME_SPLIT_RE.split(dma_name) if len(p.strip(" .")) >= 3]
    return [dma_name] + segments


def _match_dma_candidates(token: str, dmas: tuple[DMA, ...]) -> list[DMA]:
    """Every DMA the token could mean. A short common first word
    ("Columbus", "Greenville", "Tri-Cities") is legitimately a name
    segment of SEVERAL of the 210 DMAs (Columbus, OH / Columbus, GA
    (Opelika, AL) / Columbus-Tupelo-W Pnt-Hstn) — an exact full-name match
    always wins outright (a token that IS a complete DMA name isn't
    ambiguous just because it's also a substring of some other DMA's
    compound name), but a bare segment match with more than one candidate
    is genuinely ambiguous and must be surfaced, not silently resolved to
    whichever happens to come first."""
    normalized_token = _normalize(token)
    if not normalized_token:
        return []
    exact = [d for d in dmas if _normalize(d.dma) == normalized_token]
    if exact:
        return exact
    segment_matches = []
    for d in dmas:
        if any(_normalize(seg) == normalized_token for seg in _dma_candidates(d.dma)[1:]):
            segment_matches.append(d)
    return segment_matches


def _match_state(token: str) -> str | None:
    stripped = token.strip()
    upper = stripped.upper()
    if len(upper) == 2 and upper in _VALID_STATE_ABBR:
        return upper
    return _STATE_NAMES_TO_ABBR.get(stripped.lower())


def _dma_area(d: DMA) -> ResolvedArea:
    return ResolvedArea(
        dma=d.dma, area_population=d.population, dma_share=1.0,
        source_type="dma", apportionment="exact", state=d.state, label=d.dma,
        geo_id=d.dma,  # acs_age_sex_dma.json (scripts/aggregate_constanza_acs_dma_state.py) is keyed by DMA name
    )


def _resolve_dma_or_state(text: str) -> GeoResolution | None:
    """The original Phase 1 matcher — DMA names and US states only.
    Returns None (never a GeoResolution) when nothing matches at all, so
    the caller can fall through to the sub-DMA/international resolvers."""
    dmas = load_dma_reference()

    if text.lower() in _NATIONAL_WORDS:
        return GeoResolution(
            input_text=text, method="national",
            areas=[_dma_area(d) for d in dmas], geo_confidence="high",
        )

    tokens = [t for t in (_SPLIT_RE.split(text)) if t.strip()]
    named_dmas: list[DMA] = []
    matched_states: set[str] = set()
    unmatched: list[str] = []
    ambiguous: list[dict] = []

    for raw_token in tokens:
        token = _clean_token(raw_token)
        if not token:
            continue
        state = _match_state(token)
        if state:
            matched_states.add(state)
            continue
        candidates = _match_dma_candidates(token, dmas)
        if len(candidates) == 1:
            named_dmas.append(candidates[0])
            continue
        if len(candidates) > 1:
            ambiguous.append({
                "input": raw_token.strip(),
                "candidates": [
                    {"label": d.dma, "source_type": "dma", "resolve_hint": d.dma}
                    for d in candidates
                ],
            })
            continue
        unmatched.append(raw_token.strip())

    state_dmas = [d for d in dmas if d.state in matched_states]

    # Stable de-dupe (a state list and a named DMA in the same string could
    # both resolve to the same market).
    seen = set()
    deduped: list[DMA] = []
    for d in named_dmas + state_dmas:
        if d.dma not in seen:
            seen.add(d.dma)
            deduped.append(d)

    if not deduped:
        if ambiguous:
            # Nothing resolved outright, but we know exactly why — surface
            # the candidates instead of falling through to the sub-DMA/
            # international resolvers (which would just report the whole
            # string "unresolved" and silently drop this information).
            return GeoResolution(
                input_text=text, method="unresolved", areas=[],
                unmatched_tokens=unmatched, geo_confidence="unresolved", ambiguous=ambiguous,
            )
        return None  # nothing DMA/state-shaped matched — let the caller try sub-DMA/international

    if named_dmas and matched_states:
        method = "mixed"
    elif matched_states:
        method = "state_list"
    else:
        method = "exact_dma"

    return GeoResolution(
        input_text=text, method=method,
        areas=[_dma_area(d) for d in deduped],
        unmatched_tokens=unmatched, geo_confidence="high", ambiguous=ambiguous,
    )


_CROSSWALK_HINT_RESOLVERS = {"zip", "county", "congressional_district", "city"}


def resolve_geo(geo_text: str | None, geo_type_hint: str | None = None) -> GeoResolution:
    """geo_type_hint lets a caller that KNOWS what kind of geography this
    is (the UI's geography-type selector — see the Phase 2 plan's §1.5)
    skip straight to the right resolver, bypassing the DMA/state-first
    default below. This matters because plain free text is genuinely
    ambiguous for a name like "Sacramento" — DMA/state matching runs
    FIRST by default (matching every DMA/state text this resolver has
    ever accepted, for backward compatibility with existing saved
    proposals), so a bare "Sacramento" always means the
    Sacramento-Stockton-Modesto DMA unless the planner explicitly picks
    "City" as the geography type, which routes here with
    geo_type_hint="city" and reaches the actual city of Sacramento
    instead. `geo_type_hint="dma"` or "state" (or None) keeps the default
    DMA/state-first behavior; "country" routes straight to the
    international resolver.
    """
    text = (geo_text or "").strip()

    # Imported lazily to avoid a hard import-time dependency for callers
    # that only ever pass DMA/state text (and to dodge any circular-import
    # risk, since these modules don't need anything from here beyond
    # ResolvedArea/GeoResolution).
    from app.services import constanza_geo_crosswalk, constanza_geo_international

    if geo_type_hint in _CROSSWALK_HINT_RESOLVERS:
        hinted = getattr(constanza_geo_crosswalk, f"_resolve_{geo_type_hint}")(text)
        if hinted is not None:
            return hinted
    elif geo_type_hint == "country":
        hinted = constanza_geo_international.resolve(text)
        if hinted is not None:
            return hinted

    if geo_type_hint is None:
        # Strongly-typed sub-DMA patterns (ZIP digits, a "county"/"parish"
        # keyword, a "ST-##" congressional-district shape) run FIRST — their
        # textual signal can't false-positive against DMA/state text, so
        # checking them first avoids the DMA/state matcher's token-splitter
        # swallowing e.g. "Sacramento County, CA" by matching the trailing
        # "CA" as if the whole string just meant the state of California.
        strongly_typed = constanza_geo_crosswalk.resolve_strongly_typed(text)
        if strongly_typed is not None:
            return strongly_typed

        dma_or_state = _resolve_dma_or_state(text)
        if dma_or_state is not None:
            return dma_or_state

        # A real country name takes priority over the city fallback below —
        # a planner typing "Mexico" overwhelmingly means the country, not
        # the small towns of Mexico, ME / Mexico, NY that would otherwise
        # tie with it in the weak city-name match.
        international = constanza_geo_international.resolve(text)
        if international is not None:
            return international

        city_fallback = constanza_geo_crosswalk.resolve_city_fallback(text)
        if city_fallback is not None:
            return city_fallback

    return GeoResolution(
        input_text=text, method="unresolved", areas=[],
        unmatched_tokens=[text], geo_confidence="unresolved",
    )
