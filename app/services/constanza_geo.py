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

import contextvars
import dataclasses
import re
from dataclasses import dataclass, field
from functools import lru_cache

from app.data.constanza import DMA, load_dma_reference

_NATIONAL_WORDS = {
    "national", "nationwide", "us", "usa", "united states", "all markets", "all", "",
    "u.s.", "u.s.a.", "america", "united states of america", "the us", "the united states",
    "entire us", "all us", "us national", "usa national",
}

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
    # Why specific inputs couldn't be resolved, when we know — [{"input": str, "reason": str}].
    # unmatched_tokens lists every failed input; this adds the explanation where one exists
    # (e.g. an Alaska ZIP: recognized, but Alaska's sub-DMA boundaries aren't bundled).
    unmatched_details: list[dict] = field(default_factory=list)
    # Advisory caveats that don't block a result (mixed geography levels that may overlap,
    # a same-name place resolved to the larger one, ...).
    notes: list[str] = field(default_factory=list)
    # What the forgiving text layer understood and changed (constanza_geo_text.py), so a planner always sees how their
    # words were read: [{"input", "became", "why"}].
    interpretations: list[dict] = field(default_factory=list)
    # Places to pick from: "did you mean" candidates for text that did not resolve, and "broaden to" larger areas that
    # contain a resolved place. [{"kind", "label", "geo_type", "resolve_hint", "why", ...}]
    suggestions: list[dict] = field(default_factory=list)
    # States the planner asked for as a WHOLE ("Texas", "WA"): their home-state DMAs are in `areas`, but a series that
    # carries its own state rows (constanza_engine) uses those totals, which include the parts of the state served from
    # markets homed elsewhere.
    whole_states: list[str] = field(default_factory=list)

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
_NAME_SPLIT_RE = re.compile(r"[-/,()&]")

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


# The workbook's own spelling of a few DMA names is abbreviated or misspelled
# enough that no name segment is what a planner would type ("Rochestr-Mason
# City-Austin" — nobody types "Rochestr"), and its name carries no state code
# for the second state it reaches into. Keyed by exact workbook name.
_EXTRA_DMA_SEGMENTS = {"Rochestr-Mason City-Austin": ["Rochester"]}
_EXTRA_DMA_STATES = {"Rochestr-Mason City-Austin": {"MN", "IA"}}


def _dma_segments(d: DMA) -> list[str]:
    return _dma_candidates(d.dma)[1:] + _EXTRA_DMA_SEGMENTS.get(d.dma, [])


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
        if any(_normalize(seg) == normalized_token for seg in _dma_segments(d)):
            segment_matches.append(d)
    return segment_matches


def _all_dma_matches(token: str, dmas: tuple[DMA, ...]) -> list[DMA]:
    """Exact-name AND name-segment matches together — what a state qualifier
    chooses among ("Austin, MN" is the Rochester-Mason City-Austin DMA even
    though a bare "Austin" is exactly the Texas one)."""
    normalized_token = _normalize(token)
    if not normalized_token:
        return []
    return [
        d for d in dmas
        if _normalize(d.dma) == normalized_token
        or any(_normalize(seg) == normalized_token for seg in _dma_segments(d))
    ]


def _state_display_name(abbr: str) -> str:
    """"NJ" -> "New Jersey" (the longest spelling the name table has for it)."""
    names = [n for n, a in _STATE_NAMES_TO_ABBR.items() if a == abbr]
    return max(names, key=len).title() if names else abbr


def _match_state(token: str) -> str | None:
    stripped = token.strip()
    upper = stripped.upper()
    if len(upper) == 2 and upper in _VALID_STATE_ABBR:
        return upper
    lower = stripped.lower()
    if lower.endswith(" state"):  # "New York State", "Washington State"
        lower = lower[: -len(" state")].strip()
    return _STATE_NAMES_TO_ABBR.get(lower)


def _dma_name_variants(d: DMA) -> set[str]:
    """The full workbook name, and the same without its trailing parenthetical
    ("Washington, DC (Hagerstown)" -> "Washington, DC")."""
    variants = {d.dma}
    bare = re.sub(r"\s*\([^)]*\)", "", d.dma).strip()
    if bare:
        variants.add(bare)
    return variants


def _dma_in_state(d: DMA, state: str) -> bool:
    """True when the DMA is homed in `state` or its own name says it reaches
    into it ("Columbus, GA (Opelika, AL)" is in both GA and AL)."""
    return (
        d.state == state
        or state in re.findall(r"\b[A-Z]{2}\b", d.dma)
        or state in _EXTRA_DMA_STATES.get(d.dma, ())
    )


@lru_cache(maxsize=1)
def _punctuated_dma_names() -> tuple[tuple[re.Pattern, DMA], ...]:
    """Name variants that contain the characters the list splitter treats as
    separators — commas, "&", parentheses, " and " ("Columbus, OH",
    "Hartford & New Haven", "Birmingham (Ann and Tusc)", "Tri-Cities, TN-VA").
    Longest first, so the fullest spelling in a string wins."""
    out = []
    for d in load_dma_reference():
        for v in _dma_name_variants(d):
            if re.search(r"[,&()]|\band\b", v, re.IGNORECASE):
                out.append((v, d))
    out.sort(key=lambda t: -len(t[0]))
    return tuple((re.compile(r"(?<!\w)" + re.escape(v) + r"(?!\w)", re.IGNORECASE), d) for v, d in out)


def _extract_punctuated_dmas(text: str) -> tuple[list[DMA], str]:
    """Pulls complete DMA names that contain separator characters out of a
    string BEFORE it is split into tokens — otherwise picking "Columbus, OH"
    from the ambiguity picker re-splits into "Columbus" (ambiguous again) +
    "OH" (the whole state), and "Hartford & New Haven" never matches at all."""
    found: list[DMA] = []
    for pattern, d in _punctuated_dma_names():
        if pattern.search(text):
            if d not in found:
                found.append(d)
            text = pattern.sub(" ; ", text)
    return found, text


def _dma_area(d: DMA) -> ResolvedArea:
    return ResolvedArea(
        dma=d.dma, area_population=d.population, dma_share=1.0,
        source_type="dma", apportionment="exact", state=d.state, label=d.dma,
        geo_id=d.dma,  # acs_age_sex_dma.json (scripts/aggregate_constanza_acs_dma_state.py) is keyed by DMA name
    )


def _is_place_in_state(prev: dict, state: str, state_token: str) -> bool:
    """A name that matched no market in `state`: is it a real city / town there? Then "Name, ST" is that place. When the
    name is itself a state's name ("Washington", "Indiana"), only a two-letter code after it counts ("Washington, PA"):
    "Washington, Oregon" stays two states. The town is the default at any size, because the other reading ("the two whole
    states") is the one that would be silently huge when wrong; the two states are always offered as the alternative
    (`_state_pair_alternative`)."""
    from app.services import constanza_geo_crosswalk
    if prev["is_state"] and not (len(state_token) == 2 and state_token.isupper()):
        return False
    return constanza_geo_crosswalk.place_in_state(prev["token"], state)


def _state_pair_alternative(item: str, res: "GeoResolution") -> dict | None:
    """"Delaware, OH" resolved as the TOWN: offer the other reading, the two states, so the planner can switch."""
    m = re.match(r"^\s*([A-Za-z][A-Za-z .]*?)\s*,\s*([A-Z]{2})\s*$", item)
    if not m or res.method != "city":
        return None
    first = _match_state(m.group(1))
    second = m.group(2)
    if not first or second not in _VALID_STATE_ABBR or first == second:
        return None
    return {"kind": "did_you_mean", "label": f"The states {_state_display_name(first)} and {_state_display_name(second)}",
            "geo_type": "state", "resolve_hint": f"{first}; {second}", "state": None, "population": 0,
            "why": f"read as the town of {item}; these are the two states instead", "replaces": item}


def _dma_from_alias(text: str, dmas: list) -> DMA | None:
    try:
        from app.services.forecast_import import geo_reference as geo_ref
        key = geo_ref.dma_key_for_label(text) or geo_ref.dma_key_for_label(_clean_token(text))
        if key is None:
            return None
        name = next((d.name for d in geo_ref.dma_table() if d.key == key), None)
    except Exception:  # noqa: BLE001 - the alias file is an enhancement; never let it break matching
        return None
    return next((d for d in dmas if d.dma == name), None)


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

    notes: list[str] = []

    # A string that IS one complete DMA name is that DMA — before any state
    # matching ("New York" is the NYC market; the whole state is "NY" or
    # "New York State").
    whole = [d for d in dmas if _normalize(_clean_token(text)) in {_normalize(v) for v in _dma_name_variants(d)}]
    if len(whole) == 1:
        if _match_state(_clean_token(text)):
            notes.append(
                f"'{text}' matched the {whole[0].dma} DMA. For the whole state, enter its abbreviation "
                f"({whole[0].state}) or '{_clean_token(text)} State'."
            )
        return GeoResolution(
            input_text=text, method="exact_dma", areas=[_dma_area(whole[0])],
            geo_confidence="high", notes=notes,
        )

    # A market named the way a Nielsen / Madhive export spells it ("Cedar Rapids - Waterloo & Dubuque", "Tampa - Saint
    # Petersburg (Sarasota)"): the curated alias file (app/data/forecast/dma_aliases.csv) knows every such spelling.
    alias = _dma_from_alias(text, dmas)
    if alias is not None:
        return GeoResolution(input_text=text, method="exact_dma", areas=[_dma_area(alias)], geo_confidence="high", notes=notes)

    punctuated, remainder = _extract_punctuated_dmas(text)
    tokens = [t for t in (_SPLIT_RE.split(remainder)) if t.strip()]
    named_dmas: list[DMA] = list(punctuated)
    matched_states: set[str] = set()
    unmatched: list[str] = []
    ambiguous: list[dict] = []

    # "Sacramento, CA" / "Columbus, OH" / "Washington, DC": a state right
    # after a place name QUALIFIES that place (when the place has a DMA in
    # that state) instead of also adding every DMA in the state. Without a
    # DMA in that state ("Los Angeles, NV"), they remain two separate items.
    units: list[dict] = []
    for raw_token in tokens:
        token = _clean_token(raw_token)
        if not token:
            continue
        state = _match_state(token)
        if state and units and units[-1]["state"] is None:
            prev = units[-1]
            prev_matches = _all_dma_matches(prev["token"], dmas)
            if any(_dma_in_state(d, state) for d in prev_matches):
                # Also when the place is itself a state's name: "New York, NY" is the New York market, as it always was.
                prev["state"] = state
                continue
            if "unqualified_state" not in prev and (
                (not prev_matches and not prev["is_state"])
                or _is_place_in_state(prev, state, token)
            ):
                # "Santa Monica, CA": a place that is no DMA's name, with the state that qualifies IT. That is a city
                # (or county / ZIP) — never "the place plus every DMA in the state", which is what a bare state token
                # means. It is left unmatched here so the city/county resolvers get the whole string.
                # The same when the name IS a market elsewhere ("Richmond, CA" is the city in California, not the Richmond
                # VA market plus every California market) or a state's name ("Washington, PA", "Indiana, PA").
                prev["unqualified_state"] = state
                prev["is_state"] = False
                continue
        units.append({"token": token, "raw": raw_token.strip(), "state": None, "is_state": state is not None})

    unmatched_details: list[dict] = []
    for unit in units:
        token, raw = unit["token"], unit["raw"]
        if unit["state"] is None and unit["is_state"]:
            matched_states.add(_match_state(token))
            continue
        if unit.get("unqualified_state"):
            place_and_state = f"{raw}, {unit['unqualified_state']}"
            unmatched.append(place_and_state)
            unmatched_details.append({
                "input": place_and_state,
                "reason": f"'{place_and_state}' isn't a DMA name — choose Geography type = City (or County / ZIP) to use it.",
            })
            continue
        if unit["state"] is not None:
            candidates = [d for d in _all_dma_matches(token, dmas) if _dma_in_state(d, unit["state"])]
            raw = f"{raw}, {unit['state']}"
        else:
            candidates = _match_dma_candidates(token, dmas)
        if len(candidates) == 1:
            named_dmas.append(candidates[0])
            continue
        if len(candidates) > 1:
            ambiguous.append({
                "input": raw,
                "candidates": [
                    {"label": d.dma, "source_type": "dma", "resolve_hint": d.dma}
                    for d in candidates
                ],
            })
            continue
        unmatched.append(raw)

    state_dmas = [d for d in dmas if d.state in matched_states]
    # A few states (DC, DE, NH, NJ) have no DMA headquartered in them — their counties belong to markets based in a
    # neighbouring state. Saying nothing would drop them from a list and let a lone "DE" fall through to Germany.
    empty_states = sorted(a for a in matched_states if not any(d.state == a for d in dmas))
    for abbr in empty_states:
        name = _state_display_name(abbr)
        unmatched.append(abbr)
        unmatched_details.append({
            "input": abbr,
            "reason": f"{name} has no TV market (DMA) headquartered in it — its counties belong to DMAs based in neighboring "
                      "states. Enter those DMAs, or choose Geography type = County, ZIP or City.",
        })

    # Stable de-dupe (a state list and a named DMA in the same string could
    # both resolve to the same market).
    seen = set()
    deduped: list[DMA] = []
    for d in named_dmas + state_dmas:
        if d.dma not in seen:
            seen.add(d.dma)
            deduped.append(d)

    if not deduped:
        if empty_states and not ambiguous and all(u in empty_states for u in unmatched):
            # Only no-DMA states were named: report exactly that rather than letting another resolver reinterpret
            # them ("DE" is not Germany; "New Hampshire" is not a village in Ohio).
            return GeoResolution(
                input_text=text, method="unresolved", areas=[], unmatched_tokens=unmatched,
                geo_confidence="unresolved", unmatched_details=unmatched_details,
            )
        if ambiguous:
            # Nothing resolved outright, but we know exactly why — surface
            # the candidates instead of falling through to the sub-DMA/
            # international resolvers (which would just report the whole
            # string "unresolved" and silently drop this information).
            return GeoResolution(
                input_text=text, method="unresolved", areas=[],
                unmatched_tokens=unmatched, geo_confidence="unresolved", ambiguous=ambiguous,
                unmatched_details=unmatched_details,
            )
        return None  # nothing DMA/state-shaped matched — let the caller try sub-DMA/international

    if named_dmas and matched_states:
        method = "mixed"
    elif matched_states:
        method = "state_list"
    else:
        method = "exact_dma"

    if matched_states:
        notes.append(
            "A state is covered as the whole DMAs headquartered in it — all of each market, including its "
            "out-of-state counties, and none of another state's DMA that reaches in — so the population can "
            "differ from the state's own, by a lot where a market spans several states. Use counties for an "
            "exact state boundary."
        )
    return GeoResolution(
        input_text=text, method=method,
        areas=[_dma_area(d) for d in deduped],
        unmatched_tokens=unmatched, geo_confidence="high", ambiguous=ambiguous, notes=notes,
        unmatched_details=unmatched_details,
        whole_states=sorted(a for a in matched_states if a not in empty_states),
    )


_CROSSWALK_HINT_RESOLVERS = {"zip", "county", "congressional_district", "city"}
_TYPED_HINTS = _CROSSWALK_HINT_RESOLVERS | {"country"}
_HINT_LABELS = {
    "zip": "ZIP code", "county": "county", "congressional_district": "congressional district",
    "city": "city", "country": "country",
}

# Items in a list are separated by newlines, semicolons, "|", or commas — commas
# only when the list is unambiguous (see _split_items), since "Sacramento, CA" /
# "Sacramento County, CA" use the comma to attach a state to ONE place.
_DELIM_RE = re.compile(r"[\n;|]+")
_ZIP_TOKEN = r"\d{5}(?:-\d{4})?"
_ZIP_SPACE_LIST_RE = re.compile(rf"^{_ZIP_TOKEN}(?:\s+{_ZIP_TOKEN})+$")


def _pair_states(pieces: list[str], places_only: bool = False) -> list[str]:
    """Re-attaches a state piece to the place before it: ["Sacramento County",
    "CA", "Placer County", "CA"] -> ["Sacramento County, CA", "Placer County, CA"].
    A state following another state (or leading the list) stays its own item
    ("CA, NV, AZ" -> three states). `places_only` is for a list the planner
    typed as cities / counties: every item is a PLACE there, so "Washington, DC"
    or "New York, NY" pairs even though the first word is also a state's name."""
    items: list[list] = []  # [text, has_state]
    for piece in pieces:
        is_state = _match_state(piece) is not None
        if items and is_state and not items[-1][1] and (places_only or _match_state(items[-1][0]) is None):
            items[-1][0] = f"{items[-1][0]}, {piece}"
            items[-1][1] = True
        else:
            items.append([piece, False])
    return [t for t, _ in items]


def _split_items(text: str, hint: str | None) -> list[str]:
    """One entry per geography the planner listed. Lists with no strong
    signal and no hint (e.g. "Los Angeles, San Diego", "CA, NV, AZ") stay
    whole — the DMA/state matcher has always split those itself."""
    from app.services.constanza_geo_crosswalk import has_strong_signal

    chunks = [c.strip() for c in _DELIM_RE.split(text) if c.strip()]
    if not chunks:
        return [text]
    items: list[str] = []
    for chunk in chunks:
        if _ZIP_SPACE_LIST_RE.match(chunk):
            items.extend(chunk.split())
            continue
        pieces = [p.strip() for p in chunk.split(",") if p.strip()]
        if len(pieces) > 1 and (hint is not None or any(has_strong_signal(p) for p in pieces)):
            items.extend(pieces if hint in ("zip", "congressional_district", "country") else _pair_states(pieces, places_only=hint in ("city", "county")))
        else:
            paired = _pair_states(pieces, places_only=True) if len(pieces) >= 4 and len(pieces) % 2 == 0 else []
            # "Plano, TX, Frisco, TX, Allen, TX": every place with its state is a list of places (only the first was read, silently).
            # Not "CA, NV, AZ, TX" (state codes pair with each other): the first piece of a pair must not be a two-letter state code.
            if paired and len(paired) * 2 == len(pieces) and not any(len(q.split(",")[0].strip()) == 2 and _match_state(q.split(",")[0].strip()) for q in paired):
                items.extend(paired)
            else:
                items.append(chunk)
    return items


_CONFIDENCE_RANK = {"high": 3, "medium": 2, "low_confidence_international": 1, "unresolved": 0}


def _merge_resolutions(text: str, parts: list[GeoResolution]) -> GeoResolution:
    """Combines per-item resolutions of a list into one. Areas are de-duplicated
    (the same ZIP or DMA listed twice counts once); a ZIP / county / city whose market is ALSO listed as a whole DMA is
    covered by it and counted once (the larger geography wins); confidence is the weakest contributing item's; anything
    that failed is carried through, never dropped."""
    areas: list[ResolvedArea] = []
    seen: set = set()
    unmatched: list[str] = []
    details: list[dict] = []
    ambiguous: list[dict] = []
    notes: list[str] = []
    interpretations: list[dict] = []
    suggestions: list[dict] = []
    whole_states: list[str] = []
    for part in parts:
        whole_states.extend(st for st in part.whole_states if st not in whole_states)
        for a in part.areas:
            key = (a.source_type, a.geo_id or a.label or a.dma, a.dma)
            if key not in seen:
                seen.add(key)
                areas.append(a)
        unmatched.extend(t for t in part.unmatched_tokens if t not in unmatched)
        details.extend(d for d in part.unmatched_details if d not in details)
        ambiguous.extend(part.ambiguous)
        notes.extend(n for n in part.notes if n not in notes)
        interpretations.extend(i for i in part.interpretations if i not in interpretations)
        suggestions.extend(sg for sg in part.suggestions if sg not in suggestions)

    # The larger geography wins: a piece of a ZIP / county / city that lies in a market the planner also listed whole is
    # already inside that market's numbers. (Only the piece in that market is dropped: a county split across two markets
    # keeps the part in the other one.)
    whole_dmas = {a.dma for a in areas if a.source_type == "dma" and a.dma}
    if whole_dmas:
        covered: dict[str, str] = {}
        kept: list[ResolvedArea] = []
        for a in areas:
            if a.source_type != "dma" and a.dma in whole_dmas:
                covered[a.label or a.geo_id or a.dma] = a.dma
            else:
                kept.append(a)
        for label, dma in covered.items():
            notes.append(f"'{label}' is inside {dma}, which is also in your list, so it is counted once (as part of {dma}).")
        areas = kept

    from app.services import constanza_geo_text as gtext
    typed: dict[str, list[str]] = {}          # every spelling the planner typed for a place (the same place can be typed twice)
    for part in parts:
        for a in part.areas:
            spellings = typed.setdefault(a.geo_id or a.label or a.dma or "", [])
            if part.input_text not in spellings:
                spellings.append(part.input_text)
    for sg in gtext.overlap_suggestions(areas, typed):
        # A list item that was itself split ("X and Y") already carried its own, more precise offer up from the inner merge.
        if not any(o["kind"] == sg["kind"] and o["resolve_hint"] == sg["resolve_hint"] for o in suggestions):
            suggestions.append(sg)

    resolved_parts = [p for p in parts if p.areas]
    methods = {p.method for p in resolved_parts}
    if not areas:
        method, confidence = "unresolved", "unresolved"
    else:
        method = methods.pop() if len(methods) == 1 else "mixed"
        confidence = min((p.geo_confidence for p in resolved_parts), key=lambda c: _CONFIDENCE_RANK.get(c, 0))
        levels = sorted({a.source_type for a in areas})
        smaller = [l for l in levels if l not in ("dma", "state", "country")]
        if len(levels) > 1 and any(l != "dma" for l in levels):
            notes.append(
                "This list mixes geography levels (" + ", ".join(levels) + ") and they are added together — "
                "if one listed area sits inside another (a city inside a listed county, say), it is counted twice."
                if smaller else
                "This list mixes geography levels (" + ", ".join(levels) + ") and they are added together."
            )
    return GeoResolution(
        input_text=text, method=method, areas=areas, unmatched_tokens=unmatched,
        geo_confidence=confidence, ambiguous=ambiguous, unmatched_details=details, notes=notes,
        interpretations=interpretations, suggestions=suggestions, whole_states=whole_states,
    )


def _unresolved(text: str, hint: str | None) -> GeoResolution:
    res = GeoResolution(input_text=text, method="unresolved", areas=[], unmatched_tokens=[text], geo_confidence="unresolved")
    if hint in _HINT_LABELS:
        res.unmatched_details = [{"input": text, "reason": f"Couldn't match '{text}' as a {_HINT_LABELS[hint]}."}]
    return res


_DOTTED_DC_RE = re.compile(r"(?<![A-Za-z])D\.\s?C\.?(?![A-Za-z])", re.IGNORECASE)
_MAX_ITEM_CHARS = 200     # no real place name is longer; bounds the regex work any single item can cause
_MAX_ITEMS = 300


_MEMO: contextvars.ContextVar = contextvars.ContextVar("constanza_geo_memo", default=None)


def _clone(res: GeoResolution) -> GeoResolution:
    """A copy the caller may change (interpretations, suggestions, input_text are all edited after resolving)."""
    return dataclasses.replace(
        res, areas=list(res.areas), unmatched_tokens=list(res.unmatched_tokens),
        ambiguous=[{**e, "candidates": [dict(c) for c in e.get("candidates", [])]} for e in res.ambiguous],
        unmatched_details=[dict(d) for d in res.unmatched_details], notes=list(res.notes),
        interpretations=[dict(i) for i in res.interpretations], suggestions=[dict(sg) for sg in res.suggestions],
        whole_states=list(res.whole_states))


def _resolve_single(text: str, hint: str | None) -> GeoResolution:
    """Within one resolve_geo call the same text resolves once (a list item is looked at several times: the strict pass, the
    state probe, the list reading); each caller gets its own copy."""
    memo = _MEMO.get()
    if memo is None:
        return _resolve_single_uncached(text, hint)
    key = (text, hint)
    if key not in memo:
        memo[key] = _resolve_single_uncached(text, hint)
    return _clone(memo[key])


def _resolve_single_uncached(text: str, hint: str | None) -> GeoResolution:
    if len(text) > _MAX_ITEM_CHARS:
        res = GeoResolution(input_text=text[:60] + "…", method="unresolved", areas=[], unmatched_tokens=[text[:60] + "…"], geo_confidence="unresolved")
        res.unmatched_details = [{"input": res.unmatched_tokens[0], "reason": f"That entry is too long to be a place name (over {_MAX_ITEM_CHARS} characters)."}]
        return res
    # Imported lazily to avoid a hard import-time dependency for callers
    # that only ever pass DMA/state text (and to dodge any circular-import
    # risk, since these modules don't need anything from here beyond
    # ResolvedArea/GeoResolution).
    from app.services import constanza_geo_crosswalk, constanza_geo_international

    if hint in _CROSSWALK_HINT_RESOLVERS:
        hinted = constanza_geo_crosswalk.resolve_with_hint(hint, text)
        return hinted if hinted is not None else _unresolved(text, hint)

    if hint == "country":
        # "Country" includes the US itself — "United States"/"USA" mean the
        # national DMA set, not a foreign-country row for the US's UN population.
        if text.lower() in _NATIONAL_WORDS:
            return _resolve_dma_or_state("national")
        hinted = constanza_geo_international.resolve(text)
        return hinted if hinted is not None else _unresolved(text, hint)

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

    return _unresolved(text, None)


def resolve_geo(geo_text: str | None, geo_type_hint: str | None = None) -> GeoResolution:
    """geo_type_hint lets a caller that KNOWS what kind of geography this
    is (the UI's geography-type selector) skip straight to the right
    resolver, bypassing the DMA/state-first default. This matters because
    plain free text is genuinely ambiguous for a name like "Sacramento" —
    DMA/state matching runs FIRST by default (matching every DMA/state text
    this resolver has ever accepted, for backward compatibility with
    existing saved proposals), so a bare "Sacramento" always means the
    Sacramento-Stockton-Modesto DMA unless the planner explicitly picks
    "City" as the geography type, which reaches the actual city of
    Sacramento instead. An unrecognized hint (including "dma"/"state")
    behaves exactly like no hint.

    The input may be a LIST — separated by newlines, semicolons, or commas
    (ZIPs "95814, 95815"; "Sacramento County, CA; Placer County, CA";
    "Sacramento, Fresno" with the city hint). Each item resolves on its own
    and the results are merged: every item either contributes areas or is
    reported back (unmatched / ambiguous) — nothing is silently dropped.
    """
    token = _MEMO.set({})
    try:
        return _resolve_geo(geo_text, geo_type_hint)
    finally:
        _MEMO.reset(token)


def _resolve_geo(geo_text: str | None, geo_type_hint: str | None) -> GeoResolution:
    text = _DOTTED_DC_RE.sub("DC", (geo_text or "").strip())       # "Washington, D.C." is Washington, DC
    hint = geo_type_hint if geo_type_hint in _TYPED_HINTS else None
    from app.services import constanza_geo_text as gtext
    prepared = gtext.prepare(text, hint) if len(text) <= _MAX_ITEM_CHARS * 10 else gtext.Prepared(text)
    items = _split_items(prepared.text, hint)
    if len(items) > _MAX_ITEMS:
        return GeoResolution(
            input_text=text[:60] + "…", method="unresolved", areas=[], unmatched_tokens=[], geo_confidence="unresolved",
            unmatched_details=[{"input": "list", "reason": f"That list has {len(items):,} entries — at most {_MAX_ITEMS} places at a time."}],
        )
    if len(items) > 1:
        result = _merge_resolutions(text, [_resolve_item(item, hint, context=prepared.context) for item in items])
    else:
        result = _resolve_item(items[0], hint, context=prepared.context)
        result.input_text = text if prepared.interpretations else result.input_text
    result.interpretations = prepared.interpretations + [i for i in result.interpretations if i not in prepared.interpretations]
    if result.resolved and len(items) == 1 and not result.ambiguous:
        result.suggestions.extend(sg for sg in gtext.broaden(result.areas) if sg not in result.suggestions)
    if prepared.origins:
        # What the UI replaces or removes is what the planner TYPED ("Travus Co., TX"), not the tidied item it was read as.
        for sg in result.suggestions:
            if sg.get("replaces") in prepared.origins:
                sg["replaces"] = prepared.origins[sg["replaces"]]
            if sg.get("drop_inputs"):
                sg["drop_inputs"] = list(dict.fromkeys(prepared.origins.get(x, x) for x in sg["drop_inputs"]))
        for entry in result.ambiguous:
            entry["input"] = prepared.origins.get(entry["input"], entry["input"])
    return result


def _resolve_item(item: str, hint: str | None, allow_split: bool = True, context: dict | None = None) -> GeoResolution:
    """One list item, with the forgiving readings tried only when the strict matchers found nothing, or only part of it:
    "X and Y" as two places (with the state written once at the end shared by both), a state written after the name with no
    comma or misspelled ("Adams County Washinton"), one clear near-spelling, and finally ranked suggestions. A container
    ("surrounding areas in Johnson County") is matched together with the place named inside it. Every change is reported in
    `interpretations`; a guess is never silent."""
    res = _resolve_single(item, hint)
    if len(item) > _MAX_ITEM_CHARS:
        return res
    alt = _state_pair_alternative(item, res)
    if alt:
        res.suggestions.append(alt)
    ctx = (context or {}).get(item)
    if ctx:
        done = _resolve_container(item, hint, res, ctx)
        if done is not None:
            return done
    if res.ambiguous:
        return res
    from app.services import constanza_geo_text as gtext
    if res.areas:
        if res.unmatched_tokens and allow_split:                  # PART of the item resolved: try the rest the forgiving way
            better = _resolve_as_list(item, hint, res, context)
            if better is not None:
                return better
        return res

    if allow_split:
        merged = _resolve_as_list(item, hint, res, context)
        if merged is not None:
            return merged

    split = gtext.split_trailing_state(item)
    if split:
        name, abbr, how = split
        candidate = f"{name}, {abbr}"
        sub = _resolve_single(candidate, hint)
        if sub.areas or sub.ambiguous:
            names = {v: k for k, v in _STATE_NAMES_TO_ABBR.items()}
            typed = item.replace(",", " ").split()[-1]
            if how == "fuzzy":
                why = f"read the state '{typed}' as {names.get(abbr, abbr).title()} ({abbr})"
            else:
                why = f"read '{abbr}' at the end as the state"
            sub.interpretations.append({"input": item, "became": candidate, "why": why})
            sub.input_text = item
            return sub

    best = gtext.fuzzy_best(item, hint)
    if best is not None:
        sub = _resolve_single(best["resolve_hint"], hint)
        if sub.areas:
            sub.interpretations.append({"input": item, "became": best["label"], "why": f"read as {best['label']} (the closest spelling; check it is the place you meant)"})
            sub.input_text = item
            return sub

    res.suggestions = [{**sg, "replaces": item} for sg in gtext.suggest(item, hint)]
    return res


def _resolve_as_list(item: str, hint: str | None, res: GeoResolution, context: dict | None) -> GeoResolution | None:
    """"Austin and Round Rock, TX" / "Dallas or Houston" as the places they name. The state written once at the end applies to
    each earlier place that exists there. Returned only when it understood more than the strict pass did."""
    from app.services import constanza_geo_text as gtext
    pieces = gtext.split_conjunctions(item)
    if len(pieces) < 2:
        return None
    why = "read as a list of places"
    if gtext.OR_WORD_RE.search(item):
        why += " ('or' is read like 'and': the places are added together, since Constanza cannot choose between them)"
    state = gtext.shared_trailing_state(pieces)
    if state:
        shared = []
        for p in pieces[:-1]:
            # The state typed once at the end belongs to a place that exists in it ("Lincoln and Springfield, IL" is Lincoln, IL, not
            # the Nebraska market). Where the qualified form does not name a place - it turns into "that whole state" ("Chicago, IN"
            # would add every Indiana market) or leaves the state unmatched ("Philadelphia, DE") - the piece stays as typed: it is a
            # market of its own.
            qualified = f"{p}, {state}"
            q = _resolve_single(qualified, hint)
            shared.append(qualified if q.areas and not q.whole_states and not q.unmatched_tokens else p)
        typed_pieces = gtext.split_conjunctions(item)
        qualified_pieces = [(typed, now) for typed, now in zip(typed_pieces[:-1], shared) if typed != now]
        pieces = shared + [pieces[-1]]
        if qualified_pieces:
            why += f"; the state at the end ({state}) applies to each place that needs one"
    else:
        qualified_pieces = []
    merged = _merge_resolutions(item, [_resolve_item(p, hint, allow_split=False, context=context) for p in pieces])
    if not merged.areas:
        return None
    if qualified_pieces:
        # "Dallas and Lostine, OR" read as Dallas, OR: the places as typed (Dallas as its whole TV market) are one click away - when
        # they really read differently.
        differing = []
        for typed, now in qualified_pieces:
            bare_areas = _resolve_single(typed, hint).areas
            now_areas = _resolve_single(now, hint).areas
            if bare_areas and {(a.dma, a.geo_id, a.label) for a in bare_areas} != {(a.dma, a.geo_id, a.label) for a in now_areas}:
                differing.append((bare_areas[0].label or typed, now_areas[0].label or now if now_areas else now))
        if differing:
            merged.suggestions.append({
                "kind": "did_you_mean", "label": "Each place as typed, without sharing " + state, "geo_type": "", "resolve_hint": "; ".join(typed_pieces),
                "state": None, "population": 0, "replaces": item,
                "why": f"e.g. {differing[0][0]} instead of {differing[0][1]}"})
    if res.areas and (len(merged.unmatched_tokens) > len(res.unmatched_tokens)
                      or (len(merged.unmatched_tokens) == len(res.unmatched_tokens) and len(merged.areas) <= len(res.areas))):
        return None
    merged.interpretations.insert(0, {"input": item, "became": "; ".join(pieces), "why": why})
    return merged


def _resolve_container(item: str, hint: str | None, res: GeoResolution, ctx: dict) -> GeoResolution | None:
    """A container that came with the place named inside it ("Kansas City, MO and surrounding areas in Johnson County").
    A name that exists in several states is narrowed to the one in the same TV market as the inner place - and when two
    fit, the planner picks; the inner place's own state is never assumed for it. A container that cannot be matched at all
    falls back to the inner place. None: nothing to add, the strict result stands."""
    inner = ctx["inner"]
    if res.ambiguous and not res.areas:
        entry = res.ambiguous[0]
        inner_res = _resolve_single(inner, hint)
        inner_dmas = {a.dma for a in inner_res.areas if a.dma}
        if not inner_dmas:
            return None
        kept = []
        for cand in entry["candidates"]:
            cres = _resolve_single(cand["resolve_hint"], hint)
            if cres.areas and {a.dma for a in cres.areas if a.dma} & inner_dmas:
                kept.append((cand, cres))
        in_market = [c for c, _ in kept]
        how = f"the one in the same TV market as {inner}"
        # A county that is smaller than the place named beside it is not the one meant ("Kansas City, MO" has ten times the
        # people of Johnson County, MO; the Johnson County around it is the Kansas one). The place is read as the CITY here: by
        # default "Kansas City, MO" is the whole market.
        if len(kept) > 1:
            city = _resolve_single(inner, "city")
            sized = city.areas or (inner_res.areas if all(a.source_type in ("city", "zip", "county") for a in inner_res.areas) else [])
            inner_pop = sum(a.area_population for a in sized)
            if inner_pop:
                roomy = [(c, r) for c, r in kept if sum(a.area_population for a in r.areas) >= inner_pop]
                if roomy and len(roomy) < len(kept):
                    kept, how = roomy, f"the one in the same TV market as {inner} that is big enough to surround it"
        # ... and when the place was typed WITH its state ("Overland Park, KS"), the county in that state is the one meant.
        if len(kept) > 1:
            from app.services import constanza_geo_text as gtext
            typed_state = gtext._trailing_state_abbr(inner) or (gtext.split_trailing_state(inner) or (None, None, None))[1]
            same = [(c, r) for c, r in kept if typed_state and {a.state for a in r.areas} == {typed_state}]
            if len(same) == 1:
                kept, how = same, f"the one in {typed_state}, the state typed with {inner}"
        if len(kept) == 1:
            cand, cres = kept[0]
            cres.input_text = item
            cres.interpretations.append({"input": item, "became": cand["label"],
                                         "why": f"'{item}' exists in {len(entry['candidates'])} places; {cand['label']} is {how}"})
            if len(in_market) > 1:
                # chosen by size or by the typed state, not because it was the only one nearby: put the others one click away
                for other in in_market:
                    if other["label"] != cand["label"]:
                        cres.suggestions.append({"kind": "did_you_mean", "label": other["label"], "geo_type": other["geo_type"],
                                                 "resolve_hint": other["resolve_hint"], "state": other.get("state"), "population": 0,
                                                 "why": f"the other {item.rsplit(' in ', 1)[-1]} near {inner}", "replaces": item})
            return cres
        if len(kept) > 1:
            return GeoResolution(
                input_text=item, method="unresolved", areas=[], unmatched_tokens=list(res.unmatched_tokens), geo_confidence="unresolved",
                ambiguous=[{**entry, "candidates": [c for c, _ in kept]}],
                notes=[f"More than one {item} lies in the same TV market as {inner}: pick the one you mean."])
        return None
    if not res.areas and not res.ambiguous:
        sub = _resolve_item(inner, hint, allow_split=False)
        if sub.areas or sub.ambiguous:
            sub.interpretations.append({"input": item, "became": inner, "why": f"couldn't match '{item}', so only the place named inside it ('{inner}') is used"})
            return sub
    return None
