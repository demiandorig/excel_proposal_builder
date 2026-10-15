"""
Turns a Constanza geography (what the geography reader resolved: TV markets, states, ZIP codes, counties, cities) into the
location IDs TikTok's audience-size estimator takes, the way Ads Manager's location picker would.

TikTok identifies a place by an ID, not a name, and a planner types names. The IDs come from TikTok itself (its market list,
/tool/region/, and its location search, /tool/targeting/search/); nothing is hard-coded except the United States, whose ID is the one
TikTok's own documentation uses. TikTok's market names were not available to this code when it was written (they are only visible with
an ad account), so a TV market is matched by its words - "Los Angeles" against "Los Angeles CA", "Saint Louis MO" against "St. Louis" -
and by its home state, and a match that is not clearly the one market is NOT used: **a wrong place is worse than a missing one**. That
rule is applied everywhere below: two of the planner's markets that land on one TikTok market are both reported, a county or city whose
state cannot be confirmed is reported, a market TikTok lists but has switched off is reported. Everything that could not be placed comes
back in `Mapping.unmapped` with the reason, and the caller shows it; nothing is dropped silently.

TikTok does not accept overlapping locations (the U.S. and California together, a market and a ZIP inside it): that is left to TikTok to
say, and its message is shown as is.
"""
from __future__ import annotations

import logging
import re
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Iterable, Optional

from app.data.constanza import load_dma_reference
from app.services.constanza_geo import GeoResolution, ResolvedArea, _STATE_NAMES_TO_ABBR, _VALID_STATE_ABBR
from app.services.tiktok_client import TTL_LOCATION, TikTokAPIError, TikTokClient

log = logging.getLogger(__name__)

# The ID TikTok's documentation gives for the United States (the example in "Estimate audience size" and "Get available locations").
US_COUNTRY_ID = "6252001"
MAX_LOCATIONS = 3000                 # TikTok: location IDs and ZIP IDs together per ad group
MAX_ONE_BY_ONE = 12                  # counties / cities / state / fallback market look-ups made one call each, per estimate
MIN_MARKETS_IN_LIST = 100            # a market list shorter than this is not TikTok's list of U.S. markets (there are 210)

_ABBR_TO_STATE = {}
for _name, _abbr in _STATE_NAMES_TO_ABBR.items():
    if len(_name) > len(_ABBR_TO_STATE.get(_abbr, "")):
        _ABBR_TO_STATE[_abbr] = _name.title().replace(" Of ", " of ")

_STOP = {"dma", "market", "metro", "area", "the", "of", "and", "city", "county", "parish", "borough"}
_SEPARATORS = re.compile(r"[-\u2010-\u2015\u2212(/,;)]+")              # a hyphen, or any of the dashes a typeset name may use
_COUNTY_WORDS = {"county", "parish", "borough"}
# A state written out after a name ("Columbus Ohio"), as the words it leaves once the filler words are gone ("district of columbia" -> district columbia)
_STATE_TAILS: dict[tuple[str, ...], str] = {tuple(t for t in re.findall(r"[a-z]+", n) if t not in _STOP): a for n, a in _STATE_NAMES_TO_ABBR.items()}


@dataclass
class Location:
    source: str            # what the planner's geography said ("Los Angeles")
    name: str              # TikTok's name for it
    tiktok_id: str
    geo_type: str          # COUNTRY | PROVINCE | CITY | DISTRICT | DMA | ZIP_CODE
    kind: str              # "location_id" | "zipcode_id" - which field of the estimate request carries it

    def to_public(self) -> dict:
        return {"source": self.source, "name": self.name, "id": self.tiktok_id, "geo_type": self.geo_type, "kind": self.kind}


@dataclass
class Mapping:
    locations: list[Location] = field(default_factory=list)
    unmapped: list[dict] = field(default_factory=list)       # [{"input", "reason"}]
    notes: list[str] = field(default_factory=list)
    market_source: str = ""                                  # "list" (TikTok's market list) | "search" - for the diagnostics

    @property
    def location_ids(self) -> list[str]:
        return _unique(l.tiktok_id for l in self.locations if l.kind == "location_id")

    @property
    def zipcode_ids(self) -> list[str]:
        return _unique(l.tiktok_id for l in self.locations if l.kind == "zipcode_id")

    @property
    def usable(self) -> bool:
        return bool(self.locations)


def _unique(items) -> list[str]:
    seen, out = set(), []
    for i in items:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


# ------------------------------------------------------------------------------------------------------------- name matching

def _fold(text: str) -> str:
    """Lower case, accents off ("Doña Ana" -> "dona ana"), "&" read as "and", "D.C." as "DC"."""
    plain = "".join(c for c in unicodedata.normalize("NFKD", text or "") if not unicodedata.combining(c))
    return re.sub(r"\bd\.\s?c\b\.?", "dc", plain.lower().replace("&", " and "))


def _segment_words(segment: str, states: Optional[set], started: bool, tails: bool = False) -> list[str]:
    """The words of one piece of a name. "St." / "Ft." are read as Saint / Fort ("Mt." as Mount at the start of a longer piece); a
    two-letter state code that follows a name is collected into `states` instead of being a word. With `tails` (a TV market's name), a
    state written out after a word of the same piece ("Columbus Ohio") is one too; a piece that IS a state's name ("Washington") stays a word."""
    toks = re.findall(r"[a-z0-9]+", segment)
    words: list[str] = []
    for i, t in enumerate(toks):
        if t in _STOP:
            continue
        if t == "st":
            words.append("saint")
        elif t == "ft":
            words.append("fort")
        elif t == "mt" and i == 0 and len(toks) > 1:
            words.append("mount")
        elif states is not None and len(t) == 2 and t.upper() in _VALID_STATE_ABBR and (words or started):
            states.add(t.upper())                         # only after a name has started: "la crosse" keeps its "la"
        else:
            words.append(t)
    if tails and states is not None:
        for n in (3, 2, 1):
            if len(words) > n and tuple(words[-n:]) in _STATE_TAILS:
                states.add(_STATE_TAILS[tuple(words[-n:])])
                del words[-n:]
                break
    return words


@lru_cache(maxsize=4096)
def _words(name: str, tails: bool = False) -> tuple[tuple[str, ...], frozenset]:
    """(the words of a place name without filler or state codes, the state codes in it): "Los Angeles, CA" -> (("los", "angeles"),
    {"CA"}); "Boston MA-Manchester NH" -> (("boston", "manchester"), {"MA", "NH"}); "St. Louis" -> (("saint", "louis"), {})."""
    words: list[str] = []
    states: set = set()
    for segment in _SEPARATORS.split(_fold(name)):
        words.extend(_segment_words(segment, states, bool(words), tails))
    return tuple(words), frozenset(states)


@lru_cache(maxsize=4096)
def _lead(name: str, tails: bool = False) -> tuple[str, ...]:
    """The words of the first city of a market's name: "Dallas-Ft. Worth" -> ("dallas",), "Phoenix (Prescott)" -> ("phoenix",)."""
    for segment in _SEPARATORS.split(_fold(name)):
        words = _segment_words(segment, set(), False, tails)    # a state code after the city ("Columbus GA") is not part of the city
        if words:
            return tuple(words)
    return ()


def _same_word(a: str, b: str) -> bool:
    """The same word, or one written short for the other: "ashevll" for "asheville", "lncstr" for "lancaster" (same first letter, the
    letters in order, at least three of them)."""
    if a == b:
        return True
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    if len(short) < 3 or short[0] != long_[0]:
        return False
    letters = iter(long_)
    return all(ch in letters for ch in short)


def _overlap(a: tuple, b: tuple) -> float:
    """How much of two names' words are the same words (shared / all), each word counting once."""
    left = list(b)
    shared = 0
    for w in a:
        for i, v in enumerate(left):
            if _same_word(w, v):
                shared += 1
                del left[i]
                break
    total = len(a) + len(b) - shared
    return shared / total if total else 0.0


def _same_lead(a: tuple, b: tuple) -> bool:
    return bool(a) and len(a) == len(b) and all(_same_word(x, y) for x, y in zip(a, b))


def _primary(dma_name: str) -> str:
    """The first city of a market's name as typed: "Dallas-Ft. Worth" -> "Dallas", "Phoenix (Prescott)" -> "Phoenix"."""
    return re.split(r"[-(/,]", dma_name)[0].strip() or dma_name


@lru_cache(maxsize=1)
def _reference_markets() -> tuple[tuple[str, Optional[str]], ...]:
    """Every TV market the app knows, as (name, home state): what a TikTok market name must be told apart from."""
    return tuple((d.dma, d.state) for d in load_dma_reference())


@lru_cache(maxsize=1 << 16)
def _fit(name: str, state: Optional[str], tiktok_name: str) -> Optional[float]:
    """How well a TikTok market name fits a TV market, or None when it does not fit at all.

    It must lead with the same first city (or share most of the words) and must not name a state the market is not in. The very same
    first city beats one written short ("Idaho Fals") or long, so "Charlotte" is never confused with "Charlottesville"; naming one of the
    market's own states is a small plus. The market's states are its home state and any state its own name carries
    ("Washington, DC (Hagerstown)": DC and MD)."""
    wanted, wanted_codes = _words(name, True)
    words, codes = _words(tiktok_name, True)
    if not words or not wanted:
        return None
    home = {c for c in (set(wanted_codes) | {state}) if c}
    if codes and home and not (codes & home):
        return None                                       # it names another state: not this market
    overlap = _overlap(wanted, words)
    wanted_lead, lead = _lead(name, True), _lead(tiktok_name, True)
    if wanted_lead and lead == wanted_lead:
        score = 1.0 + overlap                             # the very same first city
    elif _same_lead(wanted_lead, lead):
        score = 0.5 + overlap                             # written short or long: weaker than the very same city, but a whole name that matches
                                                          # word for word ("Rochestr-Mason City-Austin") still beats a lone "Rochester"
    elif overlap >= 0.6:
        score = overlap
    else:
        return None
    if codes & home:
        score += 0.15
    return score


def pick_market(dma_name: str, state: Optional[str], candidates: list[dict],
                rivals: Optional[Iterable[tuple[str, Optional[str]]]] = None) -> tuple[Optional[dict], Optional[str]]:
    """The one TikTok market that is `dma_name`, or (None, why not).

    A candidate must fit the name (see `_fit`) and clearly beat every other candidate; one TikTok has switched off is never chosen. And it
    must be THIS market's: `rivals` are the other TV markets the app knows, and when one of them fits the candidate at least as well, the
    candidate is that market's, not this one's - so a market TikTok does not list is reported as missing instead of being handed the
    neighbour that shares its first city (Chicago / Chico-Redding, Albany GA / Albany-Schenectady-Troy, Columbus OH / Columbus-Tupelo)."""
    ranked = []
    for c in candidates:
        score = _fit(dma_name, state, c["name"])
        if score is not None:
            ranked.append((score, c))
    if not ranked:
        return None, "TikTok has no market with that name"
    ranked.sort(key=lambda x: -x[0])
    best_score, best = ranked[0]
    rival_candidates = [c for s, c in ranked[1:] if s >= best_score - 0.1 and c["geo_id"] != best["geo_id"]]
    if rival_candidates:
        return None, "more than one TikTok market fits (" + ", ".join(sorted({best["name"]} | {r["name"] for r in rival_candidates}))[:160] + ")"
    for other, other_state in (rivals or ()):
        if other == dma_name:
            continue
        theirs = _fit(other, other_state, best["name"])
        if theirs is not None and theirs >= best_score - 1e-9:
            return None, f"TikTok has no market that is clearly this one: its “{best['name']}” fits {other} at least as well"
    if not best.get("enabled", True):
        return None, f"TikTok lists “{best['name']}” but it is not available for targeting"
    return best, None


@lru_cache(maxsize=4096)
def _place_key(name: str) -> str:
    """A county or city name for comparing: accents, "St." / "Saint", and the words County / Parish / Borough / City do not matter."""
    words, _ = _words(name)
    return " ".join(words)


def _place_class(name: str, description: str = "") -> Optional[str]:
    """"county" or "city" when a name or TikTok's description says which it is, else None. "Baltimore County" and "Orleans Parish" are counties;
    "Baltimore" and "Baltimore city" (a Census independent city) are not - the word that _place_key throws away is what tells them apart."""
    desc = (description or "").strip().lower()
    last = (re.findall(r"[a-z]+", _fold(name)) or [""])[-1]
    if desc in _COUNTY_WORDS or last in _COUNTY_WORDS:
        return "county"
    if desc or last == "city":
        return "city"
    return None


# ------------------------------------------------------------------------------------------------------------- a day's memory

_cache_lock = threading.Lock()
_remembered: dict[tuple, tuple[float, Location]] = {}


def _recall(key: tuple) -> Optional[Location]:
    with _cache_lock:
        hit = _remembered.get(key)
        if hit and hit[0] > time.monotonic():
            return hit[1]
        _remembered.pop(key, None)
    return None


def _remember(key: tuple, loc: Location) -> None:
    with _cache_lock:
        _remembered[key] = (time.monotonic() + TTL_LOCATION, loc)
        if len(_remembered) > 4000:
            _remembered.pop(next(iter(_remembered)))


def _forget(key: tuple) -> None:
    with _cache_lock:
        _remembered.pop(key, None)


def forget_everything() -> None:
    with _cache_lock:
        _remembered.clear()


# ------------------------------------------------------------------------------------------------------------- the mapping

def map_resolution(res: GeoResolution, client: TikTokClient) -> Mapping:
    """TikTok locations for every area of a resolved geography."""
    out = Mapping()
    if res.geo_confidence == "low_confidence_international":
        _map_countries(res.areas, client, out)
        return out
    if res.method == "national":
        out.locations.append(Location("the United States", "United States", US_COUNTRY_ID, "COUNTRY", "location_id"))
        return out

    areas = list(res.areas)
    if len(areas) > MAX_LOCATIONS:              # every area is at most one location: say so before a call is spent
        raise ValueError(f"That is {len(areas):,} places; TikTok takes at most {MAX_LOCATIONS:,} in one estimate.")
    # A state asked for as a whole ("Texas"): TikTok has the state itself, which is more than the markets homed in it.
    homed = set(res.whole_states)
    if homed and all(a.source_type == "dma" and a.state in homed for a in areas):
        _map_states(sorted(homed), client, out)
        return out

    budget = {"one_by_one": MAX_ONE_BY_ONE}
    by_type: dict[str, list[ResolvedArea]] = {}
    for a in areas:
        by_type.setdefault(a.source_type, []).append(a)
    if "dma" in by_type:
        _map_markets(by_type["dma"], client, out, budget)
    if "state" in by_type:
        _map_states([a.state for a in by_type["state"] if a.state], client, out)
    if "zip" in by_type:
        _map_zips(by_type["zip"], client, out)
    for kind in ("county", "city"):
        if kind in by_type:
            _map_places(by_type[kind], kind, client, out, budget)
    if "congressional_district" in by_type:
        for a in by_type["congressional_district"]:
            out.unmapped.append({"input": a.label or "congressional district",
                                 "reason": "TikTok can't target congressional districts - use the TV market, counties or ZIP codes instead"})
    if "country" in by_type:
        _map_countries(by_type["country"], client, out)
    if len(out.locations) > MAX_LOCATIONS:
        raise ValueError(f"That is {len(out.locations):,} places; TikTok takes at most {MAX_LOCATIONS:,} in one estimate.")
    return out


def _map_countries(areas: list[ResolvedArea], client: TikTokClient, out: Mapping) -> None:
    for a in areas:
        name = a.label or ""
        if not name:
            continue
        if name.lower() in ("united states", "usa", "us", "u.s."):
            out.locations.append(Location(name, "United States", US_COUNTRY_ID, "COUNTRY", "location_id"))
            continue
        key = ("country", name.lower())
        hit = _recall(key)
        if hit is None:
            tags = client.search_locations([name], geo_types=["COUNTRY"], region_codes=None, search_type="FUZZY_SEARCH")
            exact = [t for t in tags if t["enabled"] and t["geo_type"] == "COUNTRY" and _place_key(t["name"]) == _place_key(name)]
            if len(exact) == 1:
                hit = Location(name, exact[0]["name"], exact[0]["geo_id"], "COUNTRY", "location_id")
                _remember(key, hit)
        if hit is None:
            out.unmapped.append({"input": name, "reason": "TikTok doesn't list that country for this ad account"})
        else:
            out.locations.append(Location(name, hit.name, hit.tiktok_id, hit.geo_type, hit.kind))


def _map_states(abbrs: list[str], client: TikTokClient, out: Mapping) -> None:
    missing = []
    found: dict[str, Location] = {}
    for abbr in abbrs:
        loc = _recall(("state", abbr))
        if loc is not None:
            found[abbr] = loc
        else:
            missing.append(abbr)
    if missing:
        names = [_ABBR_TO_STATE.get(a, a) for a in missing]
        tags = client.search_locations(names, geo_types=["PROVINCE"], region_codes=["US"])
        for abbr, name in zip(missing, names):
            exact = [t for t in tags if t["enabled"] and _place_key(t["name"]) == _place_key(name) and t["parent_id"] in ("", US_COUNTRY_ID)]
            if len(exact) == 1:
                found[abbr] = Location(name, exact[0]["name"], exact[0]["geo_id"], "PROVINCE", "location_id")
                _remember(("state", abbr), found[abbr])
            else:
                out.unmapped.append({"input": name, "reason": "TikTok has no single state with that name"})
    for abbr in abbrs:
        if abbr in found:
            out.locations.append(found[abbr])


def _market_pool(client: TikTokClient) -> Optional[list[dict]]:
    """Every U.S. TV market TikTok lists, from its own market list (/tool/region/) - so every market can be compared with every
    other one, instead of through a search that shows five at a time. None when the list is missing or is not the U.S. market list."""
    try:
        regions = client.list_regions("TO_PROVINCE")
    except TikTokAPIError as exc:
        log.info("TikTok's market list could not be read (%s); searching for markets instead", exc.kind)
        return None
    markets = [r for r in regions if r["region_code"] == "US" and r["area_type"] == "METROPOLITAN_OR_DMA" and r["name"]]
    if len(markets) < MIN_MARKETS_IN_LIST:
        return None
    return [{"name": r["name"], "geo_id": r["id"], "geo_type": "DMA", "enabled": True, "parent_id": r["parent_id"]} for r in markets]


def _map_markets(areas: list[ResolvedArea], client: TikTokClient, out: Mapping, budget: dict) -> None:
    wanted: dict[str, Optional[str]] = {}
    for a in areas:
        if a.dma:
            wanted.setdefault(a.dma, a.state)
    found: dict[str, Location] = {}
    todo = []
    for name in wanted:
        loc = _recall(("dma", name.lower()))
        if loc is not None:
            found[name] = loc
        else:
            todo.append(name)
    fresh: dict[str, Location] = {}
    if todo:
        pool = _market_pool(client)
        out.market_source = "list" if pool is not None else "search"
        tags: list[dict] = []
        by_keyword: dict[str, list[dict]] = {}
        def keywords_for(n: str) -> list[str]:
            # the first city as typed, the first city with "St." / "Ft." written out ("saint louis"), and the whole name
            seen, out = set(), []
            for k in (_primary(n), " ".join(_lead(n)) or _primary(n), n):
                if k.lower() not in seen:
                    seen.add(k.lower())
                    out.append(k)
            return out

        if pool is None:
            tags = client.search_locations(_unique(k for n in todo for k in keywords_for(n)), geo_types=["DMA"], region_codes=["US"])
            for t in tags:
                by_keyword.setdefault(t["keyword"].lower(), []).append(t)
        for name in todo:
            if pool is not None:
                candidates = pool
            else:
                candidates = [t for k in keywords_for(name) for t in by_keyword.get(k.lower(), [])] or tags
                candidates = list({c["geo_id"]: c for c in candidates if c["geo_type"] in ("DMA", "")}.values())
            best, why = pick_market(name, wanted[name], candidates, rivals=_reference_markets())
            if best is None and why == "TikTok has no market with that name" and budget["one_by_one"] > 0:
                budget["one_by_one"] -= 1
                full = client.search_locations([name], geo_types=["DMA"], region_codes=["US"], search_type="FUZZY_SEARCH")
                best, why = pick_market(name, wanted[name], [c for c in full if c["geo_type"] in ("DMA", "")], rivals=_reference_markets())
            if best is None:
                out.unmapped.append({"input": name, "reason": f"{why} (a TikTok market named like “{name}”)"})
                continue
            fresh[name] = Location(name, best["name"], best["geo_id"], "DMA", "location_id")
        found.update(fresh)

    # Two of the planner's markets that landed on ONE TikTok market cannot both be right, and which one is wrong is not known:
    # neither is used (a market quietly missing from the total is the worse error).
    by_id: dict[str, list[str]] = {}
    for name, loc in found.items():
        by_id.setdefault(loc.tiktok_id, []).append(name)
    clashing = {name for names in by_id.values() if len(names) > 1 for name in names}
    for names in (n for n in by_id.values() if len(n) > 1):
        for name in names:
            others = ", ".join(n for n in names if n != name)
            out.unmapped.append({"input": name, "reason": f"TikTok’s market “{found[name].name}” fits more than one of your markets ({name}, {others}) "
                                                         "and they cannot be told apart"})
            _forget(("dma", name.lower()))
    for name, loc in fresh.items():
        if name not in clashing:
            _remember(("dma", name.lower()), loc)
    for name in wanted:
        if name in found and name not in clashing:
            out.locations.append(found[name])


def _map_zips(areas: list[ResolvedArea], client: TikTokClient, out: Mapping) -> None:
    zips = _unique(a.geo_id for a in areas if a.geo_id)
    found: dict[str, Location] = {}
    todo = []
    for z in zips:
        loc = _recall(("zip", z))
        if loc is not None:
            found[z] = loc
        else:
            todo.append(z)
    if todo:
        tags = client.search_locations(todo, geo_types=["ZIP_CODE"], region_codes=["US"], search_type="BATCH_ZIPCODE_SEARCH")
        by_keyword = {t["keyword"]: t for t in tags if t["enabled"]}
        for z in todo:
            t = by_keyword.get(z)
            if t is None:
                out.unmapped.append({"input": f"ZIP {z}", "reason": "TikTok has no ZIP code like that"})
                continue
            found[z] = Location(f"ZIP {z}", t["name"] or z, t["geo_id"], "ZIP_CODE", "zipcode_id")
            _remember(("zip", z), found[z])
    for z in zips:
        if z in found:
            out.locations.append(found[z])


def _place_name(area: ResolvedArea) -> str:
    """"Adams County, WA" -> "Adams County"; "Sacramento, CA" -> "Sacramento"."""
    label = area.label or ""
    return re.split(r"\s*,\s*", label)[0].strip() or label


def _state_id(abbr: Optional[str], client: TikTokClient, budget: dict) -> Optional[str]:
    if not abbr or abbr not in _ABBR_TO_STATE:
        return None
    loc = _recall(("state", abbr))
    if loc is None:
        if budget["one_by_one"] <= 0:
            return None
        budget["one_by_one"] -= 1
        probe = Mapping()
        _map_states([abbr], client, probe)
        loc = probe.locations[0] if probe.locations else None
    return loc.tiktok_id if loc else None


def _is_in_state(tag: dict, state_id: str) -> Optional[bool]:
    """Is this place inside the state? True / False when TikTok's answer says where it sits, None when it does not (no parent chain)."""
    if tag["parent_id"] == state_id:
        return True                                       # a county's parent is its state
    for ancestor in tag.get("ancestors") or []:           # a city's parent is its county, whose parent is the state
        if ancestor["geo_id"] == state_id:
            return True
        if ancestor["geo_type"] == "PROVINCE":
            return False                                  # its state is another one
    return None


def _map_places(areas: list[ResolvedArea], kind: str, client: TikTokClient, out: Mapping, budget: dict) -> None:
    for a in areas:
        label = a.label or ""
        name = _place_name(a)
        # What the planner means: a county, or a city. A Census "independent city" (Baltimore city, Fairfax city, St. Louis city) arrives as a
        # county-sized area but IS a city: it is never the county of the same name (Fairfax city is about 24,000 people, Fairfax County
        # about 1.1 million) - the words City / County are what tell them apart, and _place_key leaves them out.
        wanted = "city" if kind == "city" or _place_class(name) == "city" else "county"
        geo_types = ["CITY"] if wanted == "county" else ["CITY", "DISTRICT"]
        keyword = name[:-5] if (wanted == "city" and kind == "county" and name.endswith(" city")) else name
        key = (kind, wanted, _place_key(name), a.state or "")
        loc = _recall(key)
        if loc is None:
            if budget["one_by_one"] <= 0:
                out.unmapped.append({"input": label or name, "reason": "too many places to look up one by one - use the TV market or ZIP codes"})
                continue
            budget["one_by_one"] -= 1
            tags = client.search_locations([keyword], geo_types=geo_types, region_codes=["US"], search_type="FUZZY_SEARCH")
            same = []
            for t in tags:
                if not (t["enabled"] and _place_key(t["name"]) == _place_key(name)):
                    continue
                if _place_class(t["name"], t["description"]) not in (wanted, None):
                    continue                              # a county is not a city of the same name, and the other way round
                same.append(t)
            state_id = _state_id(a.state, client, budget) if a.state else None
            chosen, why = None, ""
            if a.state:
                if not state_id:
                    why = "couldn't find the state in TikTok"
                else:
                    verdicts = [(t, _is_in_state(t, state_id)) for t in same]
                    inside = [t for t, v in verdicts if v is True]
                    unknown = [t for t, v in verdicts if v is None]
                    if len(inside) == 1:
                        chosen = inside[0]
                    elif len(inside) > 1:
                        why = f"more than one {wanted} with that name in the state"
                    elif unknown:
                        why = f"TikTok did not say which state its {name} is in"
                    else:
                        why = f"TikTok has no {wanted} with that name in that state"
            elif len(same) == 1:
                chosen = same[0]                          # no state was given: only a name that is unique across TikTok's answer is used
            else:
                why = f"more than one {wanted} with that name and no state was given" if same else f"TikTok has no {wanted} with that name"
            if chosen is not None:
                loc = Location(label or name, chosen["name"], chosen["geo_id"], chosen["geo_type"] or geo_types[0], "location_id")
                _remember(key, loc)
            else:
                out.unmapped.append({"input": label or name, "reason": f"couldn't tell which TikTok {wanted} that is ({why}) - use its TV market or ZIP codes"})
                continue
        out.locations.append(Location(label or name, loc.name, loc.tiktok_id, loc.geo_type, loc.kind))
