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

_ZIP_RE = re.compile(r"^(?:zip(?:\s*code)?\s*[:#]?\s*)?(\d{5})(?:-\d{4})?$", re.IGNORECASE)
_COUNTY_SUFFIX_RE = re.compile(r"\b(county|parish|borough)\b\.?", re.IGNORECASE)
# Also strips the independent-city / census-area suffixes when the planner has
# said "this is a county" (hint) — "Richmond city, VA", "Baltimore city, MD".
_COUNTY_TRAILING_RE = re.compile(r"\s+(county|parish|borough|city|census area|municipio)\.?$", re.IGNORECASE)
# "CA-06", "CA06", "CA 6", "TX-6" — a common shorthand for a congressional district.
_CD_SHORT_RE = re.compile(r"^([A-Za-z]{2})[\s-]*(\d{1,2})$")
# "AK-AL", "WY at large"
_CD_ATLARGE_SHORT_RE = re.compile(r"^([A-Za-z]{2})[\s-]+(?:al|at[\s-]?large)$", re.IGNORECASE)
# "Wyoming at-large", "Alaska's at large district"
_CD_ATLARGE_LONG_RE = re.compile(
    r"^([A-Za-z .]+?)(?:'s)?\s+at[\s-]?large(?:\s+(?:congressional\s+)?district)?$", re.IGNORECASE,
)
# "California's 6th district", "6th congressional district of California",
# "California district 6" — looser free-text phrasings.
_CD_LONG_RE = re.compile(
    r"(?:([A-Za-z ]+?)'?s?\s+)?(\d{1,2})(?:st|nd|rd|th)?\s+(?:congressional\s+)?district"
    r"(?:\s+of\s+([A-Za-z ]+))?",
    re.IGNORECASE,
)

_AT_LARGE_DC = "98"  # the Census numbers DC's delegate district 98


@lru_cache(maxsize=None)  # a handful of distinct filenames ever passed — maxsize=1 would thrash, re-reading multi-MB files every call
def _load_geo(name: str) -> dict:
    return read_json_maybe_gz(_GEO_DIR / name)


@lru_cache(maxsize=None)
def _load_acs(name: str) -> dict:
    return read_json_maybe_gz(_DATA_DIR / name)


def _population_for(acs_entry: dict | None) -> float:
    return acs_entry["total_population"] if acs_entry else 0.0


def _norm_name(s: str) -> str:
    """Forgiving key for place/county names: "St. Louis" == "Saint Louis" ==
    "st louis", "De Kalb" == "DeKalb", "Prince George's" == "Prince Georges"."""
    s = s.lower().replace("’", "'")
    s = re.sub(r"\bsaint\b", "st", s)
    s = re.sub(r"\bsainte\b", "ste", s)
    s = re.sub(r"\bmount\b", "mt", s)
    s = re.sub(r"\bfort\b", "ft", s)
    return re.sub(r"[^a-z0-9]", "", s)


def _state_from_text(s: str) -> str | None:
    s = s.strip()
    if len(s) == 2:
        u = s.upper()
        return u if (u in _VALID_STATE_ABBR or u == "PR") else None
    return _STATE_NAMES_TO_ABBR.get(s.lower())


# --- why a recognized geography can still be unsupported -------------------

_AK_REASON = (
    "Alaska is supported at the DMA or state level only (Anchorage, Fairbanks, Juneau, or AK) — "
    "Nielsen's borough/census-area boundaries for those DMAs aren't in the bundled geo data, so a "
    "{what} can't be apportioned."
)
_TERRITORY_REASON = (
    "{where} isn't covered — the bundled Nielsen DMA data is the 210 U.S. markets (50 states + DC), "
    "so a {what} there can't be apportioned."
)


def _unsupported_reason(state_abbr: str | None, what: str) -> str | None:
    if state_abbr == "AK":
        return _AK_REASON.format(what=what)
    if state_abbr == "PR":
        return _TERRITORY_REASON.format(where="Puerto Rico", what=what)
    return None


def _zip_state_hint(zip5: str) -> str | None:
    """Rough state from the ZIP's 3-digit prefix — only used to explain WHY a
    ZIP has no data (Alaska / territories), never to resolve anything."""
    p = zip5[:3]
    if "995" <= p <= "999":
        return "AK"
    if "006" <= p <= "009":
        return "PR"  # also USVI
    if p == "969" or zip5 in ("96799",) or p in ("969",):
        return "GU"
    return None


def _failed(token: str, reason: str) -> GeoResolution:
    return GeoResolution(
        input_text=token, method="unresolved", areas=[], geo_confidence="unresolved",
        unmatched_tokens=[token], unmatched_details=[{"input": token, "reason": reason}],
    )


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
    m = _ZIP_RE.match(token.strip())
    if not m:
        return None
    zip5 = m.group(1)  # ZIP+4 ("95814-1234") resolves on its 5-digit ZIP
    dma_shares = _load_geo("zcta_dma.json").get(zip5)
    acs = _load_acs("acs_age_sex_zcta.json").get(zip5)
    if not dma_shares or not acs:
        reason = _unsupported_reason(_zip_state_hint(zip5), f"ZIP code ({zip5})")
        if reason is None:
            reason = (
                f"ZIP {zip5} has no Census ZIP-code-area (ZCTA) population data — PO-box-only, military, "
                "and single-organization ZIPs, and ZIPs created since 2020, don't have one. "
                "Try a nearby ZIP, the city, or the county."
            )
        return _failed(token.strip(), reason)
    pop = _population_for(acs)
    method = "exact" if len(dma_shares) == 1 else "hud_ratio"
    areas = _areas_from_dma_shares(dma_shares, pop, "zip", method, state=None, label=f"ZIP {zip5}", geo_id=zip5)
    return GeoResolution(input_text=token.strip(), method="zip", areas=areas, geo_confidence="high")


@lru_cache(maxsize=1)
def _county_name_index() -> tuple[dict, dict]:
    """(by_name, by_full): normalized suffix-less name ("sacramento") and
    normalized full LSAD name ("sacramentocounty", "richmondcity", "orleansparish")
    -> [(fips, state_abbr, full_name), ...]."""
    names = _load_geo("county_names.json")
    by_name: dict[str, list[tuple[str, str, str]]] = {}
    by_full: dict[str, list[tuple[str, str, str]]] = {}
    for fips, info in names.items():
        state_abbr = _STATE_FIPS_TO_ABBR.get(info["state_fips"], "")
        full = info.get("full_name") or f"{info['name']} County"
        entry = (fips, state_abbr, full)
        by_name.setdefault(_norm_name(info["name"]), []).append(entry)
        by_full.setdefault(_norm_name(full), []).append(entry)
    return by_name, by_full


def _split_name_state(token: str, has_name, relaxed: bool) -> tuple[str, str | None, str | None]:
    """"Sacramento County, CA" / "Sacramento, California" -> (name, state_abbr,
    bad_state_text). With `relaxed` (the planner chose the geography type),
    also "Sacramento CA" with no comma — accepted only if the name part
    really exists, so a place ending in a 2-letter word isn't mangled."""
    parts = [p.strip() for p in token.split(",") if p.strip()]
    name = parts[0] if parts else ""
    state = bad = None
    if len(parts) > 1:
        state = _state_from_text(parts[1])
        if state is None:
            bad = parts[1]
    elif relaxed:
        m = re.match(r"^(.*\S)\s+([A-Za-z]{2})$", name)
        if m and _state_from_text(m.group(2)) and has_name(m.group(1)):
            name, state = m.group(1), _state_from_text(m.group(2))
    return name, state, bad


def _resolve_county(token: str, relaxed: bool = False) -> GeoResolution | None:
    """`relaxed`: the planner picked "County" as the type, so the word
    "county" isn't required ("Sacramento, CA", "Placer")."""
    token = token.strip()
    if not relaxed and not _COUNTY_SUFFIX_RE.search(token):
        return None
    by_name, by_full = _county_name_index()

    def has_name(n: str) -> bool:
        return bool(by_name.get(_norm_name(_COUNTY_TRAILING_RE.sub("", n))) or by_full.get(_norm_name(n)))

    name_part, state_hint, bad_state = _split_name_state(token, has_name, relaxed)
    if bad_state:
        return _failed(token, f"'{bad_state}' isn't a recognized state — use a 2-letter code or full state name.")

    candidates = by_full.get(_norm_name(name_part)) or by_name.get(_norm_name(_COUNTY_TRAILING_RE.sub("", name_part)), [])
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
                    {"label": f"{full}, {abbr}", "source_type": "county", "resolve_hint": f"{full}, {abbr}"}
                    for _, abbr, full in sorted(candidates, key=lambda c: (c[1], c[2]))
                ],
            }],
        )

    fips, state_abbr, full_name = candidates[0]
    dma_shares = _load_geo("county_dma.json").get(fips)
    acs = _load_acs("acs_age_sex_county.json").get(fips)
    if not dma_shares or not acs:
        reason = _unsupported_reason(state_abbr, "county") or f"No DMA/population data is bundled for {full_name}, {state_abbr}."
        return _failed(token, reason)
    pop = _population_for(acs)
    method = "exact" if len(dma_shares) == 1 else "tract_overlap"
    areas = _areas_from_dma_shares(dma_shares, pop, "county", method, state=state_abbr, label=f"{full_name}, {state_abbr}", geo_id=fips)
    return GeoResolution(input_text=token, method="county", areas=areas, geo_confidence="high")


def _parse_cd(token: str) -> tuple[str, int] | None:
    """Returns (state_abbr, district_number) — district 0 means at-large — or None."""
    t = token.strip()
    m = _CD_ATLARGE_SHORT_RE.match(t)
    if m and m.group(1).upper() in _VALID_STATE_ABBR:
        return m.group(1).upper(), 0
    m = _CD_ATLARGE_LONG_RE.match(t)
    if m:
        abbr = _STATE_NAMES_TO_ABBR.get(m.group(1).strip().lower())
        if abbr:
            return abbr, 0

    m = _CD_SHORT_RE.match(t)
    if m and m.group(1).upper() in _VALID_STATE_ABBR:
        return m.group(1).upper(), int(m.group(2))

    m = _CD_LONG_RE.search(t)
    if m:
        district = int(m.group(2))
        state_text = (m.group(1) or m.group(3) or "").strip().lower()
        abbr = _STATE_NAMES_TO_ABBR.get(state_text)
        if abbr:
            return abbr, district
    return None


def _cd_not_found_reason(state_abbr: str, state_fips: str, district: int, known: dict) -> str:
    unsupported = _unsupported_reason(state_abbr, "congressional district")
    if unsupported:
        return unsupported
    numbers = sorted(int(k[2:]) for k in known if k.startswith(state_fips))
    if numbers == [0]:
        return f"{state_abbr} has a single at-large congressional district — enter it as {state_abbr}-AL (or {state_abbr}-00)."
    if numbers:
        return f"{state_abbr} has no district {district} in the 119th Congress — it has districts 1–{max(numbers)}."
    return f"No congressional-district data is bundled for {state_abbr}."


def _resolve_congressional_district(token: str) -> GeoResolution | None:
    parsed = _parse_cd(token)
    if parsed is None:
        return None
    state_abbr, district = parsed
    state_fips = _STATE_ABBR_TO_FIPS.get(state_abbr)
    if state_fips is None:
        return None
    if state_abbr == "DC" and district in (0, int(_AT_LARGE_DC)):
        cd_geoid = f"{state_fips}{_AT_LARGE_DC}"
    else:
        cd_geoid = f"{state_fips}{district:02d}"

    cd_dma_bundle = _load_geo("cd_dma.json")
    known = cd_dma_bundle.get("cd_dma", {})
    dma_shares = known.get(cd_geoid)
    acs = _load_acs("acs_age_sex_cd.json").get(cd_geoid)
    if not dma_shares or not acs:
        return _failed(token.strip(), _cd_not_found_reason(state_abbr, state_fips, district, known))
    pop = _population_for(acs)
    weighting = cd_dma_bundle.get("_meta", {}).get("weighting", "population")
    method = "census_relationship_file" if weighting == "population" else "tract_overlap"
    label = f"{state_abbr} at-large congressional district" if district == 0 else f"{state_abbr} congressional district {district}"
    areas = _areas_from_dma_shares(dma_shares, pop, "congressional_district", method, state=state_abbr, label=label, geo_id=cd_geoid)
    return GeoResolution(input_text=token.strip(), method="congressional_district", areas=areas, geo_confidence="medium")


_PLACE_TYPE_WORDS_RE = re.compile(r"\b(city|town|village|cdp|municipality|borough)\b\.?", re.IGNORECASE)
# Census names consolidated city-county governments in full
# ("Nashville-Davidson metropolitan government (balance)") — nobody types that.
_CONSOLIDATED_RE = re.compile(
    r"\s*\(balance\)|\s+(?:metropolitan government|metro government|consolidated government|unified government|urban county)\b",
    re.IGNORECASE,
)


# Consolidated governments Census names without any "government" wording.
_EXTRA_PLACE_ALIASES = {"Lexington-Fayette": "Lexington", "Macon-Bibb County": "Macon"}


def _place_aliases(census_name: str) -> list[str]:
    """Every name a planner might type for one Census place; the first is
    the display name."""
    base = _PLACE_TYPE_WORDS_RE.sub("", census_name).strip()
    names = [base]
    consolidated = _CONSOLIDATED_RE.sub("", base).strip()
    if consolidated != base:
        names = [consolidated]
        # "Nashville-Davidson" / "Louisville/Jefferson County" -> "Nashville" / "Louisville"
        names.append(re.split(r"[-/]", consolidated)[0].strip())
    if names[0] in _EXTRA_PLACE_ALIASES:
        names.append(_EXTRA_PLACE_ALIASES[names[0]])
    if names[0].lower().startswith("urban "):  # Census calls it "Urban Honolulu"
        names.append(names[0][6:].strip())
    return names


@lru_cache(maxsize=1)
def _place_name_index() -> dict[str, list[tuple[str, str, str]]]:
    """normalized place name -> [(geoid, state_abbr, display_name), ...]."""
    names = _load_geo("place_dma.json")["names"]
    by_name: dict[str, list[tuple[str, str, str]]] = {}
    for geoid, info in names.items():
        state_abbr = _STATE_FIPS_TO_ABBR.get(info["state_fips"], "")
        aliases = _place_aliases(info["name"])
        entry = (geoid, state_abbr, aliases[0])
        for alias in dict.fromkeys(aliases):
            by_name.setdefault(_norm_name(alias), []).append(entry)
    return by_name


def _resolve_city(token: str, relaxed: bool = False) -> GeoResolution | None:
    token = token.strip()
    index = _place_name_index()
    name_part, state_hint, bad_state = _split_name_state(token, lambda n: bool(index.get(_norm_name(n))), relaxed)
    if bad_state:
        return _failed(token, f"'{bad_state}' isn't a recognized state — use a 2-letter code or full state name.")

    candidates = index.get(_norm_name(name_part), [])
    if state_hint:
        candidates = [c for c in candidates if c[1] == state_hint]
    if not candidates:
        return None

    acs_place = _load_acs("acs_age_sex_place.json")
    notes: list[str] = []
    # Census lists some names twice in one state (an incorporated city and a
    # same-named CDP, say) — keep the larger so the picker never shows two
    # identical "Goodyear, AZ" rows.
    best_per_state: dict[str, tuple[str, str, str]] = {}
    for cand in candidates:
        cur = best_per_state.get(cand[1])
        if cur is None or _population_for(acs_place.get(cand[0])) > _population_for(acs_place.get(cur[0])):
            best_per_state[cand[1]] = cand
    if len(best_per_state) < len(candidates):
        notes.append(f"More than one place is named '{name_part}' in the same state; the largest was used.")
    candidates = list(best_per_state.values())

    if len(candidates) > 1:
        ranked = sorted(candidates, key=lambda c: -_population_for(acs_place.get(c[0])))
        return GeoResolution(
            input_text=token, method="unresolved", areas=[], geo_confidence="unresolved",
            ambiguous=[{
                "input": token,
                "candidates": [
                    {"label": f"{disp}, {abbr} (pop. {_population_for(acs_place.get(gid)):,.0f})",
                     "source_type": "city", "resolve_hint": f"{disp}, {abbr}"}
                    for gid, abbr, disp in ranked
                ],
            }],
        )

    geoid, state_abbr, display = candidates[0]
    dma_shares = _load_geo("place_dma.json")["shares"].get(geoid)
    acs = acs_place.get(geoid)
    if not dma_shares or not acs:
        reason = _unsupported_reason(state_abbr, "city") or f"No DMA/population data is bundled for {display}, {state_abbr}."
        return _failed(token, reason)
    pop = _population_for(acs)
    method = "exact" if len(dma_shares) == 1 else "single_county_place"
    areas = _areas_from_dma_shares(dma_shares, pop, "city", method, state=state_abbr, label=f"{display}, {state_abbr}", geo_id=geoid)
    return GeoResolution(input_text=token, method="city", areas=areas, geo_confidence="high", notes=notes)


def has_strong_signal(piece: str) -> bool:
    """True for a list item that can only be a ZIP / county / congressional
    district (digits, a county keyword, a ST-## shape). constanza_geo's list
    splitter uses it to decide a comma-separated string is a LIST of such
    geographies rather than one "City, ST"."""
    p = piece.strip()
    return bool(_ZIP_RE.match(p) or _COUNTY_SUFFIX_RE.search(p) or _parse_cd(p))


def resolve_with_hint(hint: str, token: str) -> GeoResolution | None:
    """The planner chose the geography type, so the matching resolver runs
    alone and in its forgiving form (no "county" keyword needed)."""
    if hint == "zip":
        return _resolve_zip(token)
    if hint == "county":
        return _resolve_county(token, relaxed=True)
    if hint == "congressional_district":
        return _resolve_congressional_district(token)
    if hint == "city":
        return _resolve_city(token, relaxed=True)
    return None


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
