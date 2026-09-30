"""
Constanza's international (non-US) geo resolver — country-level only, no
sub-national resolution, explicitly low-confidence. See
scripts/extract_constanza_international.py for the bundled UN World
Population Prospects data this reads, and the Phase 2 plan doc for why
nothing finer than country-level is built here: there is no free, unified
worldwide source of sub-national demographic data the way the US has
Census ACS, and Nielsen-style local-market ratings data (DMA-equivalents)
in other countries is itself a bespoke, licensed asset the same way
Constanza's own US CTV avails-by-DMA workbook is — not something open
demographic data can substitute for. Revisit only on real business demand
for a specific country.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.data.constanza import read_json_maybe_gz
from app.services.constanza_geo import GeoResolution, ResolvedArea

_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "constanza" / "country_reference.json"

# Common everyday names/aliases that differ from the UN WPP's formal
# country name — the formal name, ISO2, and ISO3 code all still match
# directly via _country_index() without needing an entry here.
_ALIASES = {
    "uk": "GB", "united kingdom": "GB", "great britain": "GB", "britain": "GB",
    "south korea": "KR", "korea": "KR", "north korea": "KP",
    "russia": "RU", "iran": "IR", "syria": "SY", "venezuela": "VE",
    "bolivia": "BO", "moldova": "MD", "ivory coast": "CI", "cote d'ivoire": "CI",
    "congo": "CG", "dr congo": "CD", "democratic republic of congo": "CD",
    "tanzania": "TZ", "vietnam": "VN", "laos": "LA", "czech republic": "CZ",
    "uae": "AE", "united arab emirates": "AE", "mexico": "MX", "canada": "CA",
    "brazil": "BR", "china": "CN", "india": "IN", "japan": "JP", "germany": "DE",
    "france": "FR", "spain": "ES", "italy": "IT", "colombia": "CO", "argentina": "AR",
    "peru": "PE", "chile": "CL", "ecuador": "EC", "guatemala": "GT", "honduras": "HN",
    "el salvador": "SV", "nicaragua": "NI", "costa rica": "CR", "panama": "PA",
    "dominican republic": "DO", "puerto rico": "PR", "cuba": "CU",
}


@dataclass(frozen=True)
class Country:
    iso2: str
    iso3: str
    name: str
    population: float


@lru_cache(maxsize=1)
def _load() -> dict:
    return read_json_maybe_gz(_DATA_PATH)


@lru_cache(maxsize=1)
def _country_index() -> dict[str, str]:
    """Normalized name/code -> ISO2, covering formal name + ISO2 + ISO3 for every country."""
    data = _load()
    index: dict[str, str] = {}
    for iso2, c in data.items():
        if iso2 == "_meta":
            continue
        index[c["name"].strip().lower()] = iso2
        index[iso2.lower()] = iso2
        index[c["iso3"].strip().lower()] = iso2
    index.update(_ALIASES)
    return index


def get_country(iso2: str) -> Country | None:
    data = _load()
    c = data.get(iso2)
    if c is None:
        return None
    return Country(iso2=c["iso2"], iso3=c["iso3"], name=c["name"], population=c["population"])


def match_country(token: str) -> Country | None:
    iso2 = _country_index().get(token.strip().lower())
    return get_country(iso2) if iso2 else None


def resolve(text: str) -> GeoResolution | None:
    """Country-level only — no sub-national resolution. Returns None (not
    an 'unresolved' GeoResolution) when nothing matches, so the caller in
    constanza_geo.py can produce the final unresolved result itself."""
    country = match_country(text)
    if country is None:
        return None
    area = ResolvedArea(
        dma=None, area_population=country.population, dma_share=1.0,
        source_type="country", apportionment="population_share_national",
        state=None, label=country.name,
    )
    return GeoResolution(
        input_text=text, method="country", areas=[area],
        geo_confidence="low_confidence_international",
    )
