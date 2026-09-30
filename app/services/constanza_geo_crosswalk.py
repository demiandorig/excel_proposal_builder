"""
Constanza's US sub-DMA geo resolver — ZIP code, county, congressional
district, and Census Place (city). Consumes the real, spatially-derived
crosswalks built by scripts/extract_constanza_geo_crosswalks.py (area-
weighted DMA shares from actual Nielsen DMA boundary polygons overlaid
against Census TIGER/Line county/ZCTA/place shapefiles — not an
assumption of even distribution) and scripts/extract_constanza_acs.py
(real Census ACS population/age/sex tables at each geography level).

Design principle, matching constanza_geo.py's own conservative philosophy:
resolve confidently or say so — a geography whose bundled crosswalk data
is missing, or a name that matches more than one place, comes back
`unresolved`/ambiguous rather than a guess.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from app.data.constanza import dma_by_key, read_json_maybe_gz
from app.services.constanza_geo import GeoResolution, ResolvedArea, _STATE_NAMES_TO_ABBR, _VALID_STATE_ABBR

_GEO_DIR = Path(__file__).resolve().parent.parent / "data" / "constanza" / "geo"
_DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "constanza"

# Standard 2-digit state FIPS -> USPS abbreviation (Census's own numbering,
# stable since it was assigned — see census.gov/library/reference/code-lists).
_STATE_FIPS_TO_ABBR = {
    "01": "AL", "02": "AK", "04": "AZ", "05": "AR", "06": "CA", "08": "CO", "09": "CT",
    "10": "DE", "11": "DC", "12": "FL", "13": "GA", "15": "HI", "16": "ID", "17": "IL",
    "18": "IN", "19": "IA", "20": "KS", "21": "KY", "22": "LA", "23": "ME", "24": "MD",
    "25": "MA", "26": "MI", "27": "MN", "28": "MS", "29": "MO", "30": "MT", "31": "NE",
    "32": "NV", "33": "NH", "34": "NJ", "35": "NM", "36": "NY", "37": "NC", "38": "ND",
    "39": "OH", "40": "OK", "41": "OR", "42": "PA", "44": "RI", "45": "SC", "46": "SD",
    "47": "TN", "48": "TX", "49": "UT", "50": "VT", "51": "VA", "53": "WA", "54": "WV",
    "55": "WI", "56": "WY", "72": "PR",
}
_STATE_ABBR_TO_FIPS = {v: k for k, v in _STATE_FIPS_TO_ABBR.items()}

_ZIP_RE = re.compile(r"^\d{5}$")
_COUNTY_SUFFIX_RE = re.compile(r"\b(county|parish|borough)\b\.?", re.IGNORECASE)
# "CA-06", "CA06", "TX-6" — a common shorthand for a congressional district.
_CD_SHORT_RE = re.compile(r"^([A-Za-z]{2})-?(\d{1,2})$")
# "California's 6th district", "6th congressional district of California",
# "California district 6" — looser free-text phrasings.
_CD_LONG_RE = re.compile(
    r"(?:([A-Za-z ]+?)'?s?\s+)?(\d{1,2})(?:st|nd|rd|th)?\s+(?:congressional\s+)?district"
    r"(?:\s+of\s+([A-Za-z ]+))?",
    re.IGNORECASE,
)


@lru_cache(maxsize=None)  # a handful of distinct filenames ever passed — maxsize=1 would thrash, re-reading multi-MB files every call
def _load_geo(name: str) -> dict:
    return read_json_maybe_gz(_GEO_DIR / name)


@lru_cache(maxsize=None)
def _load_acs(name: str) -> dict:
    return read_json_maybe_gz(_DATA_DIR / name)


def _population_for(acs_entry: dict | None) -> float:
    return acs_entry["total_population"] if acs_entry else 0.0


def _areas_from_dma_shares(dma_shares: list[dict], area_population: float, source_type: str,
                            apportionment: str, state: str | None, label: str, geo_id: str | None = None) -> list[ResolvedArea]:
    """One ResolvedArea per DMA this geography overlaps.

    `entry["share"]` (from the bundled crosswalk JSON) is the geometric
    fraction of THIS geography's own extent that falls inside that DMA —
    e.g. Solano County is 90% inside Sacramento-Stockton-Modesto, 10%
    inside SF-Oakland-San Jose. That fraction times this geography's own
    population gives each DMA's slice of this geography's population —
    correct, and used for `area_population` below.

    But `ResolvedArea.dma_share` means something else entirely (per its
    own docstring): this area's population AS A SHARE OF ITS PARENT DMA'S
    TOTAL population — because constanza_engine.py scales the DMA's
    NATIONAL-avails-derived total by `dma_share` to get just this area's
    slice of it. A ZIP of 12,355 people is nowhere near 100% of a DMA of
    4.3M people, even though it might be (geometrically) 100% CONTAINED
    within that one DMA — conflating the two fields here previously
    caused every sub-DMA query to be scaled as if it covered the WHOLE
    parent DMA. Verified live: ZIP 95814 (pop. 12,355) was returning the
    exact same avails figure as the whole city of Sacramento (pop.
    524,802) before this fix.
    """
    dmas = dma_by_key()
    areas = []
    for entry in dma_shares:
        area_pop_in_dma = area_population * entry["share"]
        dma = dmas.get(entry["dma"].strip().lower())
        dma_total_pop = dma.population if dma else 0.0
        pop_share_of_dma = (area_pop_in_dma / dma_total_pop) if dma_total_pop else 0.0
        areas.append(ResolvedArea(
            dma=entry["dma"], area_population=area_pop_in_dma,
            dma_share=pop_share_of_dma, source_type=source_type, apportionment=apportionment,
            state=state, label=label, geo_id=geo_id,
        ))
    return areas


def _resolve_zip(token: str) -> GeoResolution | None:
    if not _ZIP_RE.match(token):
        return None
    zcta_dma = _load_geo("zcta_dma.json")
    dma_shares = zcta_dma.get(token)
    if not dma_shares:
        return None
    acs = _load_acs("acs_age_sex_zcta.json").get(token)
    pop = _population_for(acs)
    method = "exact" if len(dma_shares) == 1 else "hud_ratio"
    areas = _areas_from_dma_shares(dma_shares, pop, "zip", method, state=None, label=f"ZIP {token}", geo_id=token)
    return GeoResolution(input_text=token, method="zip", areas=areas, geo_confidence="high")


@lru_cache(maxsize=1)
def _county_name_index() -> dict[str, list[tuple[str, str]]]:
    """normalized "name|state_abbr_or_blank" -> [(fips, state_abbr), ...],
    plus a bare-name-only index for when no state was given."""
    names = _load_geo("county_names.json")
    by_full: dict[str, list[tuple[str, str]]] = {}
    for fips, info in names.items():
        state_abbr = _STATE_FIPS_TO_ABBR.get(info["state_fips"], "")
        norm_name = _COUNTY_SUFFIX_RE.sub("", info["name"]).strip().lower()
        by_full.setdefault(norm_name, []).append((fips, state_abbr))
    return by_full


def _resolve_county(token: str) -> GeoResolution | None:
    if not _COUNTY_SUFFIX_RE.search(token):
        return None
    parts = [p.strip() for p in token.split(",")]
    name_part = _COUNTY_SUFFIX_RE.sub("", parts[0]).strip().lower()
    state_hint = None
    if len(parts) > 1:
        s = parts[1].strip()
        state_hint = s.upper() if len(s) == 2 else _STATE_NAMES_TO_ABBR.get(s.lower())

    index = _county_name_index()
    candidates = index.get(name_part, [])
    if state_hint:
        candidates = [c for c in candidates if c[1] == state_hint]
    if not candidates:
        return None
    display_name = name_part.title()
    if len(candidates) > 1:
        return GeoResolution(
            input_text=token, method="unresolved", areas=[], geo_confidence="unresolved",
            ambiguous=[{
                "input": token,
                "candidates": [
                    {"label": f"{display_name} County, {abbr}", "source_type": "county", "resolve_hint": f"{display_name} County, {abbr}"}
                    for _, abbr in candidates
                ],
            }],
        )

    fips, state_abbr = candidates[0]
    county_dma = _load_geo("county_dma.json")
    dma_shares = county_dma.get(fips)
    if not dma_shares:
        return None
    acs = _load_acs("acs_age_sex_county.json").get(fips)
    pop = _population_for(acs)
    method = "exact" if len(dma_shares) == 1 else "tract_overlap"
    areas = _areas_from_dma_shares(dma_shares, pop, "county", method, state=state_abbr, label=f"{display_name} County, {state_abbr}", geo_id=fips)
    return GeoResolution(input_text=token, method="county", areas=areas, geo_confidence="high")


def _parse_cd(token: str) -> tuple[str, int] | None:
    """Returns (state_abbr, district_number) or None."""
    m = _CD_SHORT_RE.match(token.strip())
    if m and m.group(1).upper() in _VALID_STATE_ABBR:
        return m.group(1).upper(), int(m.group(2))

    m = _CD_LONG_RE.search(token)
    if m:
        district = int(m.group(2))
        state_text = (m.group(1) or m.group(3) or "").strip().lower()
        abbr = _STATE_NAMES_TO_ABBR.get(state_text)
        if abbr:
            return abbr, district
    return None


def _resolve_congressional_district(token: str) -> GeoResolution | None:
    parsed = _parse_cd(token)
    if parsed is None:
        return None
    state_abbr, district = parsed
    state_fips = _STATE_ABBR_TO_FIPS.get(state_abbr)
    if state_fips is None:
        return None
    cd_geoid = f"{state_fips}{district:02d}"

    cd_dma_bundle = _load_geo("cd_dma.json")
    dma_shares = cd_dma_bundle.get("cd_dma", {}).get(cd_geoid)
    if not dma_shares:
        return None
    acs = _load_acs("acs_age_sex_cd.json").get(cd_geoid)
    pop = _population_for(acs)
    weighting = cd_dma_bundle.get("_meta", {}).get("weighting", "population")
    method = "census_relationship_file" if weighting == "population" else "tract_overlap"
    label = f"{state_abbr} congressional district {district}"
    areas = _areas_from_dma_shares(dma_shares, pop, "congressional_district", method, state=state_abbr, label=label, geo_id=cd_geoid)
    return GeoResolution(input_text=token, method="congressional_district", areas=areas, geo_confidence="medium")


@lru_cache(maxsize=1)
def _place_name_index() -> dict[str, list[tuple[str, str]]]:
    """normalized place name -> [(geoid, state_abbr), ...]."""
    names = _load_geo("place_dma.json")["names"]
    by_name: dict[str, list[tuple[str, str]]] = {}
    for geoid, info in names.items():
        state_abbr = _STATE_FIPS_TO_ABBR.get(info["state_fips"], "")
        norm = re.sub(r"\b(city|town|village|cdp|municipality|borough)\b\.?", "", info["name"], flags=re.IGNORECASE).strip().lower()
        by_name.setdefault(norm, []).append((geoid, state_abbr))
    return by_name


def _resolve_city(token: str) -> GeoResolution | None:
    parts = [p.strip() for p in token.split(",")]
    name_part = parts[0].strip().lower()
    state_hint = None
    if len(parts) > 1:
        s = parts[1].strip()
        state_hint = s.upper() if len(s) == 2 else _STATE_NAMES_TO_ABBR.get(s.lower())

    index = _place_name_index()
    candidates = index.get(name_part, [])
    if state_hint:
        candidates = [c for c in candidates if c[1] == state_hint]
    if not candidates:
        return None
    if len(candidates) > 1:
        return GeoResolution(
            input_text=token, method="unresolved", areas=[], geo_confidence="unresolved",
            ambiguous=[{
                "input": token,
                "candidates": [
                    {"label": f"{parts[0].strip()}, {abbr}", "source_type": "city", "resolve_hint": f"{parts[0].strip()}, {abbr}"}
                    for _, abbr in candidates
                ],
            }],
        )

    geoid, state_abbr = candidates[0]
    place_dma = _load_geo("place_dma.json")["shares"]
    dma_shares = place_dma.get(geoid)
    if not dma_shares:
        return None
    acs = _load_acs("acs_age_sex_place.json").get(geoid)
    pop = _population_for(acs)
    method = "exact" if len(dma_shares) == 1 else "single_county_place"
    label = f"{parts[0].strip()}, {state_abbr}"
    areas = _areas_from_dma_shares(dma_shares, pop, "city", method, state=state_abbr, label=label, geo_id=geoid)
    return GeoResolution(input_text=token, method="city", areas=areas, geo_confidence="high")


def resolve_strongly_typed(text: str) -> GeoResolution | None:
    """ZIP/county/congressional-district only — patterns with an
    unambiguous textual signal (all-digits, a "county"/"parish"/"borough"
    keyword, a "ST-##" shape) that can't false-positive against plain
    DMA/state/city text. constanza_geo.py's default (no geo_type_hint)
    dispatch tries this BEFORE the DMA/state matcher specifically so a
    query like "Sacramento County, CA" doesn't get swallowed by the
    DMA/state matcher just because it contains a trailing "CA" token —
    that's a real bug this ordering exists to prevent (verified: without
    it, "Sacramento County, CA" silently resolved as "the whole state of
    California" instead of the county)."""
    token = text.strip()
    for resolver in (_resolve_zip, _resolve_county, _resolve_congressional_district):
        result = resolver(token)
        if result is not None:
            return result
    return None


def resolve_city_fallback(text: str) -> GeoResolution | None:
    """The weakest/most collision-prone signal (a bare name with no
    keyword or shape to distinguish it from a DMA/state) — tried only
    after DMA/state matching has already failed."""
    return _resolve_city(text.strip())


def resolve(text: str) -> GeoResolution | None:
    """All sub-DMA resolvers, in most-specific-first order. Kept for any
    caller that wants "just try everything sub-DMA" without the DMA/state-
    priority ordering constanza_geo.resolve_geo() uses by default."""
    token = text.strip()
    for resolver in (_resolve_zip, _resolve_county, _resolve_congressional_district, _resolve_city):
        result = resolver(token)
        if result is not None:
            return result
    return None
