"""
Turns a Constanza geography (what the geography reader resolved: TV markets, states, ZIP codes, counties, cities) into the
location IDs TikTok's audience-size estimator takes, the way Ads Manager's location picker would.

TikTok identifies a place by an ID, not a name, and a planner types names. The IDs come from TikTok itself
(/tool/targeting/search/); nothing is hard-coded except the United States, whose ID is the one TikTok's own documentation uses.
Because TikTok's market names were not available to this code when it was written (they are only visible with an ad account),
a TV market is matched by its words - "Los Angeles" against "Los Angeles CA" or "Los Angeles DMA" - and a match that is not
clearly the one market is NOT used: a wrong market is worse than a missing one. Everything that could not be placed comes
back in `Mapping.unmapped` with the reason, and the caller shows it; nothing is dropped silently.

TikTok does not accept overlapping locations (the U.S. and California together, a market and a ZIP inside it): that is left to
TikTok to say, and its message is shown as is.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

from app.services.constanza_geo import GeoResolution, ResolvedArea, _STATE_NAMES_TO_ABBR, _VALID_STATE_ABBR
from app.services.tiktok_client import TTL_LOCATION, TikTokClient

log = logging.getLogger(__name__)

# The ID TikTok's documentation gives for the United States (the example in "Estimate audience size" and "Get available locations").
US_COUNTRY_ID = "6252001"
MAX_LOCATIONS = 3000                 # TikTok: location IDs and ZIP IDs together per ad group
MAX_ONE_BY_ONE = 12                  # counties / cities / fallback market look-ups made one call each, per estimate

_ABBR_TO_STATE = {}
for _name, _abbr in _STATE_NAMES_TO_ABBR.items():
    if len(_name) > len(_ABBR_TO_STATE.get(_abbr, "")):
        _ABBR_TO_STATE[_abbr] = _name.title().replace(" Of ", " of ")

_STOP = {"dma", "market", "metro", "area", "the", "of", "and", "city", "county"}


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

def _words(name: str) -> tuple[list[str], set[str]]:
    """(the words of a place name without filler or state codes, the state codes in it): "Los Angeles, CA" -> (["los",
    "angeles"], {"CA"}); "Boston MA-Manchester NH" -> (["boston", "manchester"], {"MA", "NH"})."""
    tokens = re.findall(r"[a-z0-9]+", (name or "").lower().replace("&", " and "))
    words, states = [], set()
    for t in tokens:
        if t in _STOP:
            continue
        if len(t) == 2 and t.upper() in _VALID_STATE_ABBR and words:
            states.add(t.upper())          # only after a name has started: "la crosse" keeps its "la"
            continue
        words.append(t)
    return words, states


def _score(wanted_words: list[str], wanted_state: Optional[str], candidate_name: str) -> float:
    cw, cs = _words(candidate_name)
    a, b = set(wanted_words), set(cw)
    if not a or not b:
        return 0.0
    score = len(a & b) / len(a | b)
    if wanted_state and wanted_state in cs:
        score += 0.15
    return score


def _primary(dma_name: str) -> str:
    """The first city of a market's name: "Dallas-Ft. Worth" -> "Dallas", "Phoenix (Prescott)" -> "Phoenix"."""
    return re.split(r"[-(/,]", dma_name)[0].strip() or dma_name


def pick_market(dma_name: str, state: Optional[str], candidates: list[dict]) -> tuple[Optional[dict], Optional[str]]:
    """The one TikTok market that is `dma_name`, or (None, why not). A market must share the words of the name and clearly
    beat every other candidate."""
    wanted, _ = _words(dma_name)
    scored = sorted(((_score(wanted, state, c["name"]), c) for c in candidates if c.get("enabled", True)), key=lambda x: -x[0])
    if not scored or scored[0][0] < 0.5:
        return None, "TikTok has no market with that name"
    best_score, best = scored[0]
    rivals = [c for s, c in scored[1:] if s >= best_score - 0.1 and c["geo_id"] != best["geo_id"]]
    if rivals:
        return None, "more than one TikTok market fits (" + ", ".join(sorted({best["name"]} | {r["name"] for r in rivals}))[:160] + ")"
    return best, None


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
            exact = [t for t in tags if t["enabled"] and t["geo_type"] == "COUNTRY" and t["name"].lower() == name.lower()]
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
            exact = [t for t in tags if t["enabled"] and t["name"].lower() == name.lower() and t["parent_id"] in ("", US_COUNTRY_ID)]
            if len(exact) == 1:
                found[abbr] = Location(name, exact[0]["name"], exact[0]["geo_id"], "PROVINCE", "location_id")
                _remember(("state", abbr), found[abbr])
            else:
                out.unmapped.append({"input": name, "reason": "TikTok has no single state with that name"})
    for abbr in abbrs:
        if abbr in found:
            out.locations.append(found[abbr])


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
    if todo:
        primaries = _unique(_primary(n) for n in todo)
        tags = client.search_locations(primaries, geo_types=["DMA"], region_codes=["US"])
        by_keyword: dict[str, list[dict]] = {}
        for t in tags:
            by_keyword.setdefault(t["keyword"].lower(), []).append(t)
        for name in todo:
            candidates = by_keyword.get(_primary(name).lower()) or []
            if not candidates:                         # a keyword TikTok did not echo back: try every hit
                candidates = tags
            best, why = pick_market(name, wanted[name], [c for c in candidates if c["geo_type"] in ("DMA", "")])
            if best is None and budget["one_by_one"] > 0:
                budget["one_by_one"] -= 1
                full = client.search_locations([name], geo_types=["DMA"], region_codes=["US"], search_type="FUZZY_SEARCH")
                best, why = pick_market(name, wanted[name], full)
            if best is None:
                out.unmapped.append({"input": name, "reason": f"{why} (a TikTok market named like “{name}”)"})
                continue
            found[name] = Location(name, best["name"], best["geo_id"], "DMA", "location_id")
            _remember(("dma", name.lower()), found[name])
    for name in wanted:
        if name in found:
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


def _place_name(area: ResolvedArea, kind: str) -> str:
    """"Adams County, WA" -> "Adams County"; "Sacramento, CA" -> "Sacramento"."""
    label = area.label or ""
    return re.split(r"\s*,\s*", label)[0].strip() or label


def _state_id(abbr: Optional[str], client: TikTokClient) -> Optional[str]:
    if not abbr or abbr not in _ABBR_TO_STATE:
        return None
    loc = _recall(("state", abbr))
    if loc is None:
        probe = Mapping()
        _map_states([abbr], client, probe)
        loc = probe.locations[0] if probe.locations else None
    return loc.tiktok_id if loc else None


def _map_places(areas: list[ResolvedArea], kind: str, client: TikTokClient, out: Mapping, budget: dict) -> None:
    geo_types = ["CITY"] if kind == "county" else ["CITY", "DISTRICT"]
    for a in areas:
        label = a.label or ""
        name = _place_name(a, kind)
        key = (kind, name.lower(), a.state or "")
        loc = _recall(key)
        if loc is None:
            if budget["one_by_one"] <= 0:
                out.unmapped.append({"input": label or name, "reason": "too many places to look up one by one - use the TV market or ZIP codes"})
                continue
            budget["one_by_one"] -= 1
            tags = client.search_locations([name], geo_types=geo_types, region_codes=["US"], search_type="FUZZY_SEARCH")
            same = [t for t in tags if t["enabled"] and t["name"].lower() == name.lower()]
            state_id = _state_id(a.state, client)
            narrowed = [t for t in same if state_id and t["parent_id"] == state_id] or same
            if len(narrowed) == 1:
                t = narrowed[0]
                loc = Location(label or name, t["name"], t["geo_id"], t["geo_type"] or geo_types[0], "location_id")
                _remember(key, loc)
        if loc is None:
            what = "county" if kind == "county" else "city"
            out.unmapped.append({"input": label or name, "reason": f"couldn't tell which TikTok {what} that is - use its TV market or ZIP codes"})
        else:
            out.locations.append(Location(label or name, loc.name, loc.tiktok_id, loc.geo_type, loc.kind))
