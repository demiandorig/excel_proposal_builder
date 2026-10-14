"""
Understanding what a planner TYPED in the geography box — the forgiving layer in front of constanza_geo's strict matchers.

Real geography text is free text: "Othello, WA and Surrounding areas in Adams County, WA", "Adams County Washinton",
"Dallas area", "all of Orange County". This module turns that into the clean items the matchers know, SAYS WHAT IT
UNDERSTOOD (every change is reported back as an interpretation the planner can see), and when a place still cannot be
resolved, offers ranked suggestions instead of a dead end.

What it does, in the order it is used:

  prepare()              one pass over the text before matching: "X and surrounding areas in Y" -> the LARGER area Y (the
                         planner asked for everything around X inside Y), "X in Y County" -> Y, filler words dropped,
                         "Adams Cty" -> "Adams County"
  split_trailing_state() "Adams County Washington" / "Adams County Washinton" / "Plano TX" -> ("Adams County", "WA")
  fuzzy_best()           one unambiguous near-spelling of a county / city / DMA ("Othelo" -> "Othello") — accepted only when
                         it is clearly better than every alternative, and always reported
  suggest()              ranked "did you mean" candidates across counties, cities, DMAs and states (never auto-accepted)
  broaden()              the larger areas that contain a resolved place: its DMA(s) and its state

Nothing here invents a place: a suggestion is always a real row of the bundled geography, and a guess is never applied
silently.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Optional


@dataclass
class Prepared:
    text: str
    interpretations: list[dict] = field(default_factory=list)
    # cleaned item -> the text the planner TYPED for it, only where the two differ ("Travus Co., TX" became "Travus County,
    # TX"): a suggestion that stands in for the item must name what is in the box, or the UI cannot find it to replace.
    origins: dict = field(default_factory=dict)
    # cleaned item -> {"inner": the place named inside the container}. "Kansas City, MO and surrounding areas in Johnson County"
    # becomes the item "Johnson County" with the inner place remembered: the resolver uses it to pick the right Johnson County
    # (the one in Kansas City's market) instead of assuming the inner place's state, and falls back to the inner place if the
    # container cannot be matched at all.
    context: dict = field(default_factory=dict)


# --------------------------------------------------------------------------------------------- states

def _state_tables() -> tuple[dict[str, str], set[str]]:
    from app.services.constanza_geo import _STATE_NAMES_TO_ABBR, _VALID_STATE_ABBR
    return _STATE_NAMES_TO_ABBR, _VALID_STATE_ABBR


def fuzzy_state(text: str) -> tuple[Optional[str], Optional[str]]:
    """(abbr, how): how is 'exact' (a state name), 'abbr' (an upper-case two-letter code) or 'fuzzy' (a near spelling,
    clearly closer to one state than to any other). (None, None) when it is not a state."""
    names, abbrs = _state_tables()
    t = (text or "").strip().strip(".,;")
    if not t:
        return None, None
    low = re.sub(r"\s+", " ", t.lower())
    if low in names:
        return names[low], "exact"
    if len(t) == 2 and t.isupper() and t in abbrs:
        return t, "abbr"
    if len(low) >= 5:
        close = difflib.get_close_matches(low, list(names), n=2, cutoff=0.84)
        if close:
            best = difflib.SequenceMatcher(None, low, close[0]).ratio()
            runner = difflib.SequenceMatcher(None, low, close[1]).ratio() if len(close) > 1 else 0.0
            if best - runner >= 0.04 and names[close[0]] != (names[close[1]] if len(close) > 1 else None):
                return names[close[0]], "fuzzy"
    return None, None


def split_trailing_state(text: str) -> Optional[tuple[str, str, str]]:
    """'Adams County Washington' -> ('Adams County', 'WA', 'exact'); 'Adams County Washinton' -> (..., 'fuzzy');
    'Plano TX' -> ('Plano', 'TX', 'abbr'). The longest state-looking ending wins; the part before it must be non-empty."""
    tokens = text.replace(",", " ").split()
    for k in (3, 2, 1):
        if len(tokens) <= k:
            continue
        abbr, how = fuzzy_state(" ".join(tokens[-k:]))
        if abbr:
            name = " ".join(tokens[:-k]).strip()
            if name:
                return name, abbr, how or "exact"
    return None


def _trailing_state_abbr(text: str) -> Optional[str]:
    """The state a chunk ends with, when it ends with a comma-separated state ('Othello, WA')."""
    parts = [p.strip() for p in text.split(",") if p.strip()]
    if len(parts) > 1:
        abbr, how = fuzzy_state(parts[-1])
        return abbr
    return None


# --------------------------------------------------------------------------------------------- phrase cleanup

_SURROUNDING = r"(?:surrounding|nearby|neighbou?ring|outlying|adjacent|neighboring)"
_AREAS = r"(?:areas?|communities|towns?|cities|suburbs|neighborhoods|counties)"
# "X and surrounding areas in Y": the planner wants Y (it contains the surroundings of X)
_SURROUNDING_IN_RE = re.compile(
    rf"^(?P<inner>.+?)\s*(?:,|\band\b|&|\bplus\b|\+)?\s*(?:its\s+|the\s+|all\s+)?{_SURROUNDING}\s+{_AREAS}\s+(?:in|of|around|within|near|inside)\s+(?:the\s+)?(?P<outer>.+)$",
    re.IGNORECASE)
# "X and surrounding areas" with nothing after it
_SURROUNDING_TAIL_RE = re.compile(rf"\s*(?:,|\band\b|&|\bplus\b|\+)?\s*(?:its\s+|the\s+)?{_SURROUNDING}\s+{_AREAS}\s*$", re.IGNORECASE)
_VICINITY_TAIL_RE = re.compile(r"\s*(?:,|\band\b|&)?\s*(?:vicinity|environs|outskirts)\s*$", re.IGNORECASE)
# "Othello in Adams County, WA": only when the outer part names a container (county, DMA...)
_IN_CONTAINER_RE = re.compile(
    r"^(?P<inner>.+?)\s+(?:in|within|inside)\s+(?:the\s+)?(?P<outer>.*\b(?:county|parish|borough|census area|dma|metro|metropolitan)\b.*)$",
    re.IGNORECASE)
_LEADING_FILLER_RE = re.compile(
    r"^(?:(?:all|entire|whole)\s+(?:of\s+)?(?:the\s+)?|(?:areas?|cities|towns?|communities|neighborhoods)\s+(?:in|of|around|within)\s+(?:the\s+)?|greater\s+)",
    re.IGNORECASE)
_COUNTY_ABBR_RE = re.compile(r"\b((?:[A-Z][A-Za-z'.-]*\s)*[A-Z][A-Za-z'.-]*)\s+(?:Co|Cty|Cnty)\.?(?=\s*(?:,|$))")


MAX_CLEAN_CHARS = 200      # no real place name is longer; longer text is left alone (and never fed to the regexes below)

_GENERIC_INNER_RE = re.compile(r"^(?:all\s+)?(?:the\s+)?(?:areas?|cities|city|towns?|communities|neighborhoods|suburbs|places|everything)$", re.IGNORECASE)
_PLURAL_COUNTIES_RE = re.compile(r"^(?P<names>.+?)\s+(?:counties|parishes|boroughs)\b(?P<tail>[^;]*)$", re.IGNORECASE)


def _same(chunk: str) -> dict:
    return {"text": chunk, "why": None, "context": None, "src": chunk}


def _clean_chunk(chunk: str) -> list[dict]:
    """One typed list item -> the clean item(s) it means: [{"text", "why", "context", "src"}] (`why` None when nothing changed;
    `src` is the part of the typed text the item came from). Usually one entry; a few phrasings stand for several places."""
    from app.services.constanza_geo import _NATIONAL_WORDS
    chunk = re.sub(r"\s+", " ", chunk).strip()        # one space between words: keeps the patterns below linear
    if len(chunk) > MAX_CLEAN_CHARS or chunk.lower() in _NATIONAL_WORDS:
        return [_same(chunk)]                 # too long to be a place; or "all markets", "all us": complete phrases, not filler
    original = chunk
    m = _SURROUNDING_IN_RE.match(chunk)
    if m:
        inner, outer = m.group("inner").strip(" ,"), m.group("outer").strip().strip(",")
        return [{"text": outer, "src": chunk, "context": {"inner": inner},
                 "why": (f"You asked for the surrounding areas in {outer}, so the larger area is used — it already includes "
                         f"the places named before it ('{inner}').")}]
    # "X in Y County and Z in W County": each statement on its own, so one container never swallows the next place
    parts = split_conjunctions(chunk)
    if len(parts) > 1 and any(_IN_CONTAINER_RE.match(p) for p in parts):
        out: list[dict] = []
        for part in parts:
            out.extend(_clean_chunk(part))
        return out
    m = _IN_CONTAINER_RE.match(chunk)
    if m:
        inner, outer = m.group("inner").strip(" ,"), m.group("outer").strip().strip(",")
        if _GENERIC_INNER_RE.match(inner):    # "cities in Orange County, CA": no named place, just everything in the county
            return [{"text": outer, "src": chunk, "context": None, "why": "dropped the introductory words"}]
        return [{"text": outer, "src": chunk, "context": {"inner": inner}, "why": f"'{inner}' is inside {outer}, so the larger area is used."}]
    m = _PLURAL_COUNTIES_RE.match(chunk)
    if m:                                     # "Los Angeles and Orange counties, CA": one county per name
        names = split_conjunctions(m.group("names")) or [m.group("names").strip()]
        if len(names) > 1:
            tail = m.group("tail").rstrip()
            return [{"text": f"{n} County{tail}", "src": chunk, "context": None,
                     "why": "read 'counties' as one county for each name"} for n in names]
    reasons = []
    cleaned = _SURROUNDING_TAIL_RE.sub("", chunk)
    if cleaned != chunk:
        reasons.append("dropped 'and surrounding areas' (Constanza works from the places named)")
        chunk = cleaned.strip(" ,")
    cleaned = _VICINITY_TAIL_RE.sub("", chunk)
    if cleaned != chunk:
        reasons.append("dropped 'vicinity'")
        chunk = cleaned.strip(" ,")
    cleaned = _LEADING_FILLER_RE.sub("", chunk)
    if cleaned != chunk and cleaned.strip():
        reasons.append("dropped the introductory words")
        chunk = cleaned.strip()
    cleaned = _COUNTY_ABBR_RE.sub(r"\1 County", chunk)
    if cleaned != chunk:
        reasons.append("read 'Co.' as County")
        chunk = cleaned
    chunk = re.sub(r"\s+", " ", chunk).strip(" ,")
    if chunk != original.strip() and chunk:
        return [{"text": chunk, "src": original, "context": None, "why": "; ".join(reasons) or "tidied the text"}]
    return [_same(original)]


_ZIP_ONLY_RE = re.compile(r"^\d{5}(?:-\d{4})?$")
_ANY_ZIP_RE = re.compile(r"\b\d{5}(?:-\d{4})?\b")
_ZIP_COMMA_RE = re.compile(r"(\b\d{5}(?:-\d{4})?)[ \t]*,[ \t]*(?=[A-Za-z])")


def _split_addresses(chunk: str) -> list[str]:
    """'Chicago, IL 60601, Chicago, IL 60602' -> two items: a comma right after a ZIP ends an address. Only where the text holds
    several ZIPs ('Washington, DC 20001, USA' is one address)."""
    if len(_ANY_ZIP_RE.findall(chunk)) < 2:
        return [chunk]
    return [part.strip() for part in _ZIP_COMMA_RE.sub(r"\1;", chunk).split(";") if part.strip()]


_ADDRESS_ZIP_RE = re.compile(r"^(?P<rest>.*\S)\s+(?P<zip>\d{5})(?:-\d{4})?$")


def address_place(item: str) -> Optional[tuple[str, str]]:
    """'Plano, TX 75024' -> ('75024', why). A place and its state followed by a ZIP is an address, and the ZIP is the exact place;
    when the bundled data has no such ZIP the city and state before it are used instead. Only exactly ONE place and its state
    before the ZIP count: 'Dallas and Houston, TX 77002' or 'Houston, Dallas, Austin, TX 78701' are lists, left to the list
    reading (nothing the planner typed is dropped), and so are two ZIPs or a ZIP in the middle of a longer text."""
    m = _ADDRESS_ZIP_RE.match(item.strip())
    if not m:
        return None
    rest = m.group("rest").strip(" ,")
    if re.search(r"\d", rest) or len(split_conjunctions(rest)) > 1:
        return None
    rest_cased = re.sub(r",(\s*)([A-Za-z]{2})$", lambda t: "," + t.group(1) + t.group(2).upper(), rest)   # "bend, or" is Bend, OR
    split = split_trailing_state(rest_cased)                     # ("Dallas or 75201": the 'or' is a word, not Oregon)
    if split is None or len([p for p in rest.split(",") if p.strip()]) > 2:       # "Place, ST" or "Place ST", nothing more
        return None
    zip_code = m.group("zip")
    from app.services import constanza_geo_crosswalk
    shares = constanza_geo_crosswalk._load_geo("zcta_dma.json").get(zip_code)
    if shares:
        markets = " / ".join(dict.fromkeys(s["dma"] for s in shares[:3]))
        return zip_code, f"read '{item}' as ZIP {zip_code} (in the {markets} market); the city and state typed before it were not used"
    if not constanza_geo_crosswalk.place_in_state(split[0], split[1]):
        return None                                              # "Miami, OR 33101" names no place: leave it to the other readings
    return f"{split[0]}, {split[1]}", f"ZIP {zip_code} is not in the data Constanza has, so the city and state before it are used"


def _split_stateful_list(chunk: str) -> list[str]:
    """'Othello, WA and Adams County, WA' -> two places. Split on 'and' / '&' / '+' ONLY when every part clearly stands on its
    own (it ends with a state, or is a ZIP): a market name can contain 'and' ('Cedar Rapids - Waterloo & Dubuque') and
    'Texas and Oklahoma' is left to the DMA / state matcher exactly as before."""
    parts = split_conjunctions(chunk)
    if len(parts) < 2:
        return [chunk]

    def stands_alone(p: str) -> bool:
        return bool(_ZIP_ONLY_RE.match(p) or _trailing_state_abbr(p) or split_trailing_state(p) or address_place(p))

    return parts if all(stands_alone(p) for p in parts) else [chunk]


def _norm_space(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def prepare(text: str, hint: Optional[str] = None) -> Prepared:
    """Clean the text one list item at a time (items are separated by newlines, semicolons or '|'). Returns the text to
    match plus an interpretation for every item that was changed."""
    if hint in ("zip", "congressional_district", "country"):
        return Prepared(text)                                  # typed lists of codes / countries need no phrase cleanup
    items: list[str] = []
    interps: list[dict] = []
    origins: dict = {}
    context: dict = {}
    # "A, B, or C": the 'or' before the last place is part of the list; each place then goes through the cleanup on its own
    spaced = _OXFORD_OR_RE.sub("; ", text or "")
    if spaced != (text or ""):
        interps.append({"input": text, "became": spaced, "why": "read the 'or' before the last place as part of the list"})
    chunks: list[str] = []
    for raw in re.split(r"[\n;|]+", spaced):
        raw = raw.strip()
        if not raw:
            continue
        parts = _split_addresses(raw)
        if len(parts) > 1:
            interps.append({"input": raw, "became": "; ".join(parts), "why": "read the comma after each ZIP as the end of an address"})
        chunks.extend(parts)
    for chunk in chunks:
        pieces = _clean_chunk(chunk)
        # One chunk that became SEVERAL places from the same words ("Los Angeles and Orange counties"): none of them stands for
        # the whole text, so replacing "the typed text of one" would take the others with it.
        shared_source = len(pieces) > 1 and all(p["src"] == pieces[0]["src"] for p in pieces)
        for piece in pieces:
            cleaned = piece["text"]
            if piece["why"]:
                interps.append({"input": piece["src"], "became": cleaned, "why": piece["why"]})
            listed = _split_stateful_list(cleaned)
            if len(listed) > 1:
                interps.append({"input": cleaned, "became": "; ".join(listed), "why": "read as a list of places"})
            typed_parts = split_conjunctions(piece["src"]) if len(listed) > 1 else []
            for k, item in enumerate(listed):
                address = None if hint else address_place(item)
                if address:                        # "Plano, TX 75024": the ZIP is the exact place, the city before it is not needed
                    became, why = address
                    interps.append({"input": item, "became": became, "why": why})
                    items.append(became)
                    typed = piece["src"] if len(listed) == 1 else (typed_parts[k] if len(typed_parts) == len(listed) else None)
                    if typed and not shared_source:
                        origins.setdefault(became, typed)
                    continue
                items.append(item)
                # A rewritten item ("Travus Co., TX" -> "Travus County, TX", "X and surrounding areas in Johnson County" ->
                # "Johnson County") is not written in the box as it now reads: what the UI must replace or remove is the typed
                # text of THAT item - the whole phrase when it was one item, its own part when one phrase was split into
                # several ("Travus Co., TX and Dallas, TX"), nothing when the parts cannot be told apart.
                if piece["why"] and not shared_source:
                    typed = piece["src"] if len(listed) == 1 else (typed_parts[k] if len(typed_parts) == len(listed) else None)
                    if typed and _norm_space(item) != _norm_space(typed):
                        origins.setdefault(item, typed)
                if piece["context"]:
                    context.setdefault(item, piece["context"])
    return Prepared("; ".join(items) if items else (text or ""), interps, origins, context)


# "or" counts only as a lower-case word between two places ("Dallas or Houston"): the state code OR (Oregon) is upper case and
# follows a comma ("Portland, OR and Salem, OR"), and "A, B, or C" is cleaned up before the list is split.
_CONJUNCTION_RE = re.compile(r"\s+(?:and|&|plus)\s+|\s*\+\s*|(?<![,\s])\s+(?-i:or)\s+", re.IGNORECASE)
OR_WORD_RE = re.compile(r"(?<![,\s])\s+or\s+")
# ", or Austin" (a place follows): the conjunction of "A, B, or C". Not when what follows could only continue a state code - "and",
# "plus", "&", "+" or a ZIP ("portland, or and seattle", "bend, or 97701") - where the lowercase "or" is Oregon.
_OXFORD_OR_RE = re.compile(r",[ \t]+or[ \t]+(?=[A-Za-z])(?!(?:and|plus)\b)")            # (one line: "portland, or" at the end of a line is Oregon)


def split_conjunctions(item: str) -> list[str]:
    """'Othello, WA and Adams County, WA' -> ['Othello, WA', 'Adams County, WA']. Used only after the whole string failed
    to resolve as ONE place (a DMA name can contain 'and'). 'or' is read like 'and': Constanza cannot choose between places,
    it adds them."""
    return [p.strip(" ,") for p in _CONJUNCTION_RE.split(item) if p and p.strip(" ,")]


def shared_trailing_state(pieces: list[str]) -> Optional[str]:
    """'Austin and Round Rock, TX' -> 'TX': the state written once, after the last place, belongs to every place before it
    that names none. None when the last piece has no state or the earlier pieces already carry theirs."""
    if len(pieces) < 2:
        return None
    last = pieces[-1]
    abbr = _trailing_state_abbr(last)
    if abbr is None:
        split = split_trailing_state(last)
        abbr = split[1] if split else None
    if abbr is None:
        return None
    for p in pieces[:-1]:
        if _ZIP_ONLY_RE.match(p) or _trailing_state_abbr(p) or split_trailing_state(p):
            return None
    return abbr


# --------------------------------------------------------------------------------------------- fuzzy names

_COUNTY_WORDS_RE = re.compile(r"\b(county|parish|borough|census area|city and borough|municipality|co|cty)\b\.?", re.IGNORECASE)


def _name_core(text: str) -> str:
    """The distinctive part of a typed place name, for comparison ('Adams County' -> 'adams')."""
    from app.services.constanza_geo_crosswalk import _norm_name
    return _norm_name(_COUNTY_WORDS_RE.sub("", text))


def _state_of(text: str) -> tuple[str, Optional[str]]:
    """(name without its state, state abbr or None) for 'Adams County, WA' / 'Adams County Washinton' / 'Adams County'."""
    parts = [p.strip() for p in text.split(",") if p.strip()]
    if len(parts) > 1:
        abbr, _how = fuzzy_state(parts[-1])
        if abbr:
            return ", ".join(parts[:-1]), abbr
    split = split_trailing_state(text)
    if split:
        return split[0], split[1]
    return text.strip(), None


@lru_cache(maxsize=1)
def _pools() -> dict:
    """Name pools to search, built once from the bundled geography: {normalized name: [entries]} per type."""
    from app.services import constanza_geo_crosswalk as cw
    from app.data.constanza import load_dma_reference
    by_name, _by_full = cw._county_name_index()
    places = cw._place_name_index()
    dmas: dict[str, list] = {}
    for d in load_dma_reference():
        dmas.setdefault(cw._norm_name(d.dma), []).append((d.dma, d.population or 0.0))
    return {"county": by_name, "city": places, "dma": dmas}


@lru_cache(maxsize=1)
def _usable_ids() -> tuple[frozenset, frozenset]:
    """(county FIPS, place GEOIDs) that have market and population data bundled: the only places worth suggesting."""
    from app.services import constanza_geo_crosswalk as cw
    return frozenset(cw._load_geo("county_dma.json")), frozenset(cw._load_geo("place_dma.json")["shares"])


def _usable(kind: str, entry) -> bool:
    """A suggestion must be something the resolver can actually use: not a territory or an Alaska borough (their sub-DMA
    boundaries are not bundled) and not a place with no market data."""
    from app.services import constanza_geo_crosswalk as cw
    geo_id, abbr, _display = entry
    if cw._unsupported_reason(abbr, kind) is not None:
        return False
    counties, places = _usable_ids()
    return geo_id in (counties if kind == "county" else places)


def _bucket(pool: dict, key: str) -> list[str]:
    """Candidate keys worth comparing: same first letter, similar length (keeps fuzzy matching fast on 25,000 places)."""
    n = len(key)
    return [k for k in pool if k and k[0] == key[0] and abs(len(k) - n) <= 3]


def _population(kind: str, entry) -> float:
    from app.services import constanza_geo_crosswalk as cw
    try:
        if kind == "county":
            return cw._population_for(cw._load_acs("acs_age_sex_county.json").get(entry[0]))
        if kind == "city":
            return cw._population_for(cw._load_acs("acs_age_sex_place.json").get(entry[0]))
    except Exception:  # noqa: BLE001
        return 0.0
    return 0.0


def _candidates(text: str, hint: Optional[str], cutoff: float) -> list[dict]:
    from app.services import constanza_geo_crosswalk as cw
    name_text, state = _state_of(text)
    core = _name_core(name_text)
    if len(core) < 3:
        return []
    wants_county = hint == "county" or bool(re.search(r"\b(county|parish|borough|co|cty)\b\.?", name_text, re.IGNORECASE))
    kinds = ["county"] if wants_county else (["city"] if hint == "city" else ["county", "city", "dma"])
    if hint in ("zip", "congressional_district", "country"):
        return []
    pools = _pools()
    found: list[dict] = []
    for kind in kinds:
        pool = pools[kind]
        for key in difflib.get_close_matches(core, _bucket(pool, core), n=12, cutoff=cutoff):
            score = difflib.SequenceMatcher(None, core, key).ratio()
            for entry in pool[key]:
                if kind == "dma":
                    label, dma_pop = entry
                    found.append({"label": f"{label} (TV market)", "geo_type": "dma", "resolve_hint": label, "score": score,
                                  "state": None, "population": dma_pop, "key": key, "why": "similar TV-market name"})
                    continue
                geo_id, abbr, display = entry
                if (state and abbr != state) or not _usable(kind, entry):
                    continue
                label = f"{display if kind == 'city' else display}, {abbr}"
                found.append({"label": label, "geo_type": kind, "resolve_hint": label, "score": score + (0.03 if state else 0.0),
                              "state": abbr, "population": _population(kind, entry), "key": key, "why": "similar spelling"})
    return found


def fuzzy_best(text: str, hint: Optional[str]) -> Optional[dict]:
    """The ONE near-spelling to apply automatically, or None: a county / city whose name is almost identical (ratio >= 0.92),
    in the state the planner named, and clearly better than every other candidate. Anything less is only suggested."""
    name_text, state = _state_of(text)
    if state is None:
        return None
    cands = [c for c in _candidates(text, hint, 0.88) if c["geo_type"] in ("county", "city") and c["state"] == state]
    if not cands:
        return None
    cands.sort(key=lambda c: (-c["score"], -c["population"]))
    best = cands[0]
    if best["score"] < 0.92:
        return None
    others = {c["label"] for c in cands[1:] if c["score"] > best["score"] - 0.04}
    return best if not others else None


def suggest(text: str, hint: Optional[str] = None, limit: int = 6) -> list[dict]:
    """Ranked 'did you mean' candidates for text that did not resolve. Each is a real place the resolver can take as typed
    (`resolve_hint`), best spelling first, bigger places before smaller ones."""
    cands = _candidates(text, hint, 0.74)
    cands.sort(key=lambda c: (-round(c["score"], 2), -c["population"], c["label"]))
    seen: set = set()
    out: list[dict] = []
    for c in cands:
        if c["label"] in seen:
            continue
        seen.add(c["label"])
        out.append({"kind": "did_you_mean", "label": c["label"], "geo_type": c["geo_type"], "resolve_hint": c["resolve_hint"],
                    "state": c["state"], "population": round(c["population"]), "why": c["why"]})
        if len(out) >= limit:
            break
    return out


@lru_cache(maxsize=1)
def _states_with_markets() -> frozenset:
    from app.data.constanza import load_dma_reference
    return frozenset(d.state for d in load_dma_reference())


def _state_has_markets(state: str) -> bool:
    """A few states (NJ, DE, NH, DC) have no TV market homed in them: 'all of its markets' would resolve to nothing."""
    return state in _states_with_markets()


def broaden(areas: list) -> list[dict]:
    """The larger areas that contain a resolved place or ZIP: its TV market(s) and its state. Offered, never applied."""
    seen_geo: set = set()
    out: list[dict] = []
    dmas: dict[str, float] = {}
    state = None
    for a in areas:
        if a.source_type not in ("zip", "county", "city", "congressional_district"):
            return []                                           # a DMA / state / country is not 'broadened' here
        seen_geo.add(a.geo_id or a.label)
        if a.dma:
            dmas[a.dma] = dmas.get(a.dma, 0.0) + a.area_population
        state = state or a.state
    if len(seen_geo) != 1:
        return []                                               # for a list, the planner chose each place on purpose
    for dma, _pop in sorted(dmas.items(), key=lambda kv: -kv[1])[:2]:
        out.append({"kind": "broaden", "label": f"{dma} (TV market)", "geo_type": "dma", "resolve_hint": dma, "state": state,
                    "why": "the TV market that contains it"})
    if state and _state_has_markets(state):
        from app.services.constanza_geo import _STATE_NAMES_TO_ABBR
        name = next((n.title() for n, ab in _STATE_NAMES_TO_ABBR.items() if ab == state), state)
        out.append({"kind": "broaden", "label": f"{name} (all its TV markets)", "geo_type": "state", "resolve_hint": state, "state": state,
                    "why": "every TV market headquartered in the state"})
    return out


def overlap_suggestions(areas: list, typed: Optional[dict] = None) -> list[dict]:
    """A city / ZIP listed next to a county it MAY sit inside (same state, every market the small place touches is a market the
    county touches, and the county is bigger): counting both would count those people twice. The bundled data cannot prove a
    place is inside a county (Fremont and Berkeley share a market with Santa Clara County, and are in Alameda County), so this
    is an offer the planner judges - "drop these if they are inside" - never an automatic merge and never worded as fact."""
    out: list[dict] = []
    counties = {}
    for a in areas:
        if a.source_type == "county" and a.geo_id:
            counties.setdefault(a.geo_id, []).append(a)
    if not counties:
        return out
    small: dict[str, list] = {}
    for a in areas:
        if a.source_type in ("city", "zip") and a.geo_id:
            small.setdefault(a.geo_id, []).append(a)
    for county_id, pieces in counties.items():
        c_dmas = {p.dma for p in pieces}
        c_pop = sum(p.area_population for p in pieces)
        c_state = pieces[0].state
        c_label = pieces[0].label or county_id
        inside: list[str] = []
        inside_ids: list[str] = []
        for geo_id, parts in small.items():
            s_dmas = {p.dma for p in parts}
            s_pop = sum(p.area_population for p in parts)
            s_state = parts[0].state
            if s_dmas <= c_dmas and s_pop <= c_pop and (s_state is None or c_state is None or s_state == c_state):
                inside.append(parts[0].label or geo_id)
                inside_ids.append(geo_id)
        if inside:
            # `drop_inputs`: what the planner TYPED for the small places (every spelling), so the UI can take exactly those out.
            drop_inputs = list(dict.fromkeys(x for g in inside_ids for x in ((typed or {}).get(g) or [])))
            names = ", ".join(inside)
            out.append({"kind": "overlap", "label": f"Drop {names} — it may be inside {c_label}", "geo_type": "county",
                        "resolve_hint": c_label, "drops": inside, "drop_inputs": drop_inputs,
                        "why": f"{names} may already be inside {c_label} (the data can't prove it either way). If it is, listing both "
                               "counts the same people twice."})
    return out
