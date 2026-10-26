"""
Constanza's Hispanic targeting layer — the real Hispanic share of each geography, from the Census Bureau's ACS.

WHY
---
Most Entravision campaigns are Hispanic-targeted, but the avails behind a platform series describe everyone the platform can
reach in a market. A Hispanic cut multiplies them by the Hispanic share of the people who live there — about 95% in Laredo,
47% in Los Angeles, 25% in New York — so a Hispanic-targeted plan is sized to the audience it can actually buy, per area, not
by a state-wide percentage.

DATA
----
ACS 5-year table B01001I ("Sex by Age, Hispanic or Latino") for ZIP (ZCTA), county, congressional district, city (Census
Place), and DMA / state aggregated from counties (scripts/extract_constanza_hispanic.py). Every geography carries the Hispanic
count by sex and age band next to the area's total population, so a Hispanic cut combines with an age / gender cut from ONE
table: "Hispanic adults 18-34" is those people over everyone, not the Hispanic share times the 18-34 share (the Hispanic
population is younger than the rest, and the product of two shares would understate it).

WHICH NUMBER AN AREA GETS (the first that exists)
------------------------------------------------
  0. a market of a WHOLE-STATE request whose avails were scaled up to the platform's own state total (`state_scaled`): its STATE's
     row — `level` "state_total". That total holds households served from markets homed elsewhere (Northern Virginia is served from
     Washington), so cutting it at the home markets' shares would misstate the state;
  1. the area's OWN row (a ZIP's, a county's, a city's, a district's, a whole DMA's) — `level` "area";
  2. its parent DMA's row, when the area's own is missing or too small to trust (ACS estimates for a few dozen people are noise,
     see MIN_AREA_POPULATION) — `level` "dma";
  3. its STATE's row (the same joint age x sex table) — `level` "state": the three Alaska markets, which neither the Nielsen polygons nor
     the DMA bundles cover;
  4. the older state-level Hispanic % proxy file (hispanic_pct_by_state.json), with no age detail — `level` "state_proxy"; only reachable
     when the state bundle lacks the state;
  5. nothing: the cut is NOT applied to that area, and the estimate says so.

WHAT IT DOES NOT KNOW
---------------------
How much Hispanic households use a given platform compared with everyone else. Streaming platforms over- and under-index
(free ad-supported services tend to over-index among Hispanic viewers), and no public source says by how much per platform and market.
So the cut assumes Hispanic households use the platform in proportion to their population; the estimate says so, and its band is
widened for it (constanza_audience._CV_HISPANIC).

A series that is ALREADY Hispanic-targeted (the Hispanic Connect exports, VIX / Spanish-language) is not cut again: its avails
describe those audiences, so the Hispanic share would count the same restriction twice. With one of those, an age / gender cut is
read against the Hispanic population (conditional on Hispanic) rather than everyone.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

from app.data.constanza import load_dma_reference, load_hispanic_pct_by_state, read_json_maybe_gz

_DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "constanza"

# source_type of a resolved area -> the bundle name (acs_hispanic_<name>.json.gz). A whole-DMA area (source_type "dma", geo_id =
# the DMA name) reads the DMA bundle; a state asked for as a whole arrives as its DMAs.
_BUNDLE_BY_SOURCE_TYPE = {
    "zip": "zcta", "county": "county", "congressional_district": "cd", "city": "place", "dma": "dma",
}

# An ACS row for fewer people than this is not used by itself: the estimate of a share from a few dozen people is mostly sampling
# noise (a ZCTA of 40 residents can read 0% or 100%), so the area borrows its parent DMA's share instead.
MIN_AREA_POPULATION = 200

# An age / gender mix read against an area's HISPANIC population (an already-Hispanic series) needs this many Hispanic people to mean anything: a
# ZIP with 3 Hispanic residents has no age structure to read (and 0 would divide by zero). Below it the area borrows its parent DMA's mix.
MIN_CONDITIONAL_HISPANIC = 200

# A planner's "adult" is 18 and over (the same rule as constanza_audience.ADULT_AGE).
_ADULT_AGE = 18

# series.targeting values whose avails already describe Hispanic (or Spanish-language) audiences — see the module docstring.
_ALREADY_HISPANIC_PREFIXES = ("hispanic", "spanish")


def series_is_hispanic(series: Optional[dict]) -> bool:
    """Is this series (the public dict on an estimate, or None for the bundled workbook) already a Hispanic-targeted audience?"""
    targeting = str((series or {}).get("targeting") or "").lower()
    return targeting.startswith(_ALREADY_HISPANIC_PREFIXES)


@dataclass(frozen=True)
class HispanicEntry:
    """One geography's row: its total population, its Hispanic population, and the Hispanic count by sex and age band."""
    total_population: float
    hispanic: float
    male: tuple
    female: tuple
    bands: tuple                      # ((age_min, age_max or None), ...) — the 14 bands both lists follow

    @property
    def fraction(self) -> float:
        return self.hispanic / self.total_population if self.total_population else 0.0

    def matching(self, age_min: Optional[int], age_max: Optional[int], gender: Optional[str], adults_only: bool = False) -> float:
        """Hispanic people in the age range / gender. A band that is only partly inside the range counts its overlapping share
        (uniform within a band) — the same treatment constanza_audience gives the age x sex table."""
        lo_req = age_min if age_min is not None else 0
        if adults_only:
            lo_req = max(lo_req, _ADULT_AGE)
        hi_req = age_max if age_max is not None else 200
        matched = 0.0
        for i, (lo, hi) in enumerate(self.bands):
            band_hi = hi if hi is not None else 200
            if band_hi < lo_req or lo > hi_req:
                continue
            width = (hi if hi is not None else lo + 15) - lo + 1
            overlap = min(hi_req, band_hi if hi is not None else hi_req) - max(lo_req, lo) + 1
            frac = max(0.0, min(1.0, overlap / width))
            if gender == "male":
                matched += self.male[i] * frac
            elif gender == "female":
                matched += self.female[i] * frac
            else:
                matched += (self.male[i] + self.female[i]) * frac
        return matched

    def share(self, age_min: Optional[int], age_max: Optional[int], gender: Optional[str],
              adults_only: bool = False, conditional: bool = False) -> float:
        """Hispanic people in the age range / gender over the area's TOTAL population — or, `conditional`, over its Hispanic
        population (the share of a Hispanic audience that is in the range: what an already-Hispanic series is cut by)."""
        base = self.hispanic if conditional else self.total_population
        return self.matching(age_min, age_max, gender, adults_only) / base if base else 0.0


@lru_cache(maxsize=None)
def _bundle(name: str) -> dict:
    """{geo id: HispanicEntry}; {} when the bundle file is missing (the layer then degrades to the state proxy)."""
    try:
        raw = read_json_maybe_gz(_DATA_DIR / f"acs_hispanic_{name}.json")
    except (FileNotFoundError, OSError):
        return {}
    meta = raw.get("_meta") or {}
    bands = tuple((int(lo), None if hi is None else int(hi)) for lo, hi in (meta.get("bands") or []))
    n = len(bands)
    out: dict[str, HispanicEntry] = {}
    for geo_id, row in raw.items():
        if geo_id == "_meta" or not bands or len(row) != 2 + 2 * n:
            continue
        out[geo_id] = HispanicEntry(float(row[0]), float(row[1]), tuple(row[2:2 + n]), tuple(row[2 + n:]), bands)
    return out


def vintage() -> Optional[str]:
    try:
        return (read_json_maybe_gz(_DATA_DIR / "acs_hispanic_dma.json").get("_meta") or {}).get("vintage")
    except (FileNotFoundError, OSError):
        return None


def _parent_dma_entry(area) -> Optional[HispanicEntry]:
    return _bundle("dma").get(area.dma) if getattr(area, "dma", None) else None


@lru_cache(maxsize=1)
def _home_state_by_dma() -> dict[str, str]:
    return {d.key: d.state for d in load_dma_reference()}


def state_proxy_pct(area) -> Optional[float]:
    """The state's Hispanic % (the older proxy file) for an area with nothing better: its own state, else its parent DMA's home state."""
    pct = load_hispanic_pct_by_state()
    state = getattr(area, "state", None) or (_home_state_by_dma().get(area.dma.strip().lower()) if getattr(area, "dma", None) else None)
    return pct.get(state) if state else None


def _state_entry(area) -> Optional[HispanicEntry]:
    """The state's row: the area's own state, else its parent DMA's home state."""
    state = getattr(area, "state", None) or (_home_state_by_dma().get(area.dma.strip().lower()) if getattr(area, "dma", None) else None)
    entry = _bundle("state").get(state) if state else None
    return entry if entry is not None and entry.total_population >= MIN_AREA_POPULATION else None


def _usable(entry: Optional[HispanicEntry], conditional: bool) -> bool:
    if entry is None or entry.total_population < MIN_AREA_POPULATION:
        return False
    return not conditional or entry.hispanic >= MIN_CONDITIONAL_HISPANIC


def entry_for(area, conditional: bool = False) -> tuple[Optional[HispanicEntry], Optional[str]]:
    """(row, level) for an area — the first that exists, see the module docstring: "state_total" (a market of a whole-state request scaled to the
    platform's state total), "area" (its own row, also a whole DMA's), "dma" (its parent DMA's), "state" (its state's); (None, None) when none
    does (the caller then falls back to `state_proxy_pct`). `conditional`: the row will be read against its HISPANIC population (an already-Hispanic
    series' age / gender cut), so it must have enough Hispanic people to carry an age structure."""
    if getattr(area, "state_scaled", False):
        st = _state_entry(area)
        if _usable(st, conditional):
            return st, "state_total"
    own_bundle = _BUNDLE_BY_SOURCE_TYPE.get(getattr(area, "source_type", None))
    if own_bundle and getattr(area, "geo_id", None):
        own = _bundle(own_bundle).get(area.geo_id)
        if _usable(own, conditional):
            return own, "area"
    parent = _parent_dma_entry(area)
    if _usable(parent, conditional):
        return parent, "dma"
    st = _state_entry(area)
    if _usable(st, conditional):
        return st, "state"
    return None, None


def fraction(area) -> tuple[Optional[float], Optional[str]]:
    """(Hispanic share of the area's population, level) — level "state_total" | "area" | "dma" | "state" | "state_proxy"; (None, None) when no source has the area."""
    entry, level = entry_for(area)
    if entry is not None:
        return entry.fraction, level
    pct = state_proxy_pct(area)
    return (pct, "state_proxy") if pct is not None else (None, None)


def hispanic_population(area) -> Optional[float]:
    """The area's own Hispanic population: its population x its Hispanic share (None when no source has the area)."""
    frac, _ = fraction(area)
    return None if frac is None else float(area.area_population) * frac
