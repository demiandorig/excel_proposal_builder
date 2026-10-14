"""
Canonical geography for the forecast tables, and the one place where "what does this label mean" is decided.

  * states + congressional districts: fixed reference (the 119th Congress apportionment: 435 districts + DC's delegate)
  * DMAs: the 210 markets of app/data/constanza/dma_reference.json, keyed by a slug of the canonical name
  * aliases: app/data/forecast/dma_aliases.csv - every spelling a source file has used for a DMA. The file is the
    reviewable authority; `suggest_dma` only ever SUGGESTS (for the error message of an unmapped label), it never maps.
"""
from __future__ import annotations

import csv
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Optional

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "forecast"
ALIAS_CSV = DATA_DIR / "dma_aliases.csv"

# (code, name, FIPS) - the 50 states and DC. Territories are not in the avails exports.
STATES: list[tuple[str, str, str]] = [
    ("AL", "Alabama", "01"), ("AK", "Alaska", "02"), ("AZ", "Arizona", "04"), ("AR", "Arkansas", "05"),
    ("CA", "California", "06"), ("CO", "Colorado", "08"), ("CT", "Connecticut", "09"), ("DE", "Delaware", "10"),
    ("DC", "District of Columbia", "11"), ("FL", "Florida", "12"), ("GA", "Georgia", "13"), ("HI", "Hawaii", "15"),
    ("ID", "Idaho", "16"), ("IL", "Illinois", "17"), ("IN", "Indiana", "18"), ("IA", "Iowa", "19"),
    ("KS", "Kansas", "20"), ("KY", "Kentucky", "21"), ("LA", "Louisiana", "22"), ("ME", "Maine", "23"),
    ("MD", "Maryland", "24"), ("MA", "Massachusetts", "25"), ("MI", "Michigan", "26"), ("MN", "Minnesota", "27"),
    ("MS", "Mississippi", "28"), ("MO", "Missouri", "29"), ("MT", "Montana", "30"), ("NE", "Nebraska", "31"),
    ("NV", "Nevada", "32"), ("NH", "New Hampshire", "33"), ("NJ", "New Jersey", "34"), ("NM", "New Mexico", "35"),
    ("NY", "New York", "36"), ("NC", "North Carolina", "37"), ("ND", "North Dakota", "38"), ("OH", "Ohio", "39"),
    ("OK", "Oklahoma", "40"), ("OR", "Oregon", "41"), ("PA", "Pennsylvania", "42"), ("RI", "Rhode Island", "44"),
    ("SC", "South Carolina", "45"), ("SD", "South Dakota", "46"), ("TN", "Tennessee", "47"), ("TX", "Texas", "48"),
    ("UT", "Utah", "49"), ("VT", "Vermont", "50"), ("VA", "Virginia", "51"), ("WA", "Washington", "53"),
    ("WV", "West Virginia", "54"), ("WI", "Wisconsin", "55"), ("WY", "Wyoming", "56"),
]
_STATE_BY_NAME = {name.lower(): code for code, name, _ in STATES}
_STATE_BY_NAME.update({"washington, d.c.": "DC", "washington dc": "DC", "washington, dc": "DC", "d.c.": "DC"})
_STATE_BY_FIPS = {fips: code for code, _, fips in STATES}

# Voting districts per state (119th Congress, from the 2020 census apportionment). DC has a delegate.
CD_COUNTS = {
    "AL": 7, "AK": 1, "AZ": 9, "AR": 4, "CA": 52, "CO": 8, "CT": 5, "DE": 1, "FL": 28, "GA": 14, "HI": 2, "ID": 2,
    "IL": 17, "IN": 9, "IA": 4, "KS": 4, "KY": 6, "LA": 6, "ME": 2, "MD": 8, "MA": 9, "MI": 13, "MN": 8, "MS": 4,
    "MO": 8, "MT": 2, "NE": 3, "NV": 4, "NH": 2, "NJ": 12, "NM": 3, "NY": 26, "NC": 14, "ND": 1, "OH": 15, "OK": 5,
    "OR": 6, "PA": 17, "RI": 2, "SC": 7, "SD": 1, "TN": 9, "TX": 38, "UT": 4, "VT": 1, "VA": 11, "WA": 10,
    "WV": 2, "WI": 8, "WY": 1, "DC": 1,
}
AT_LARGE_STATES = {code for code, n in CD_COUNTS.items() if n == 1}


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def normalize_label(label: str) -> str:
    """The comparison form of a geography / publisher label: accents folded, lower case, '&' read as 'and', every other
    symbol a single space. Two spellings are the same label only if they normalize to the same string."""
    text = unicodedata.normalize("NFKD", label).encode("ascii", "ignore").decode("ascii")
    text = text.lower().replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def publisher_key(name: str) -> str:
    """Identity of a publisher within and across series: case and runs of spaces do not matter, nothing else does.
    Symbols are deliberately KEPT: 'AMC' and 'AMC+', 'Discovery' and 'Discovery+' are different publishers, and
    'Solitaire: Classic Card Game' is not assumed to be 'Solitaire - Classic Card Game'."""
    return re.sub(r"\s+", " ", name).strip().lower()


# --------------------------------------------------------------------------------------------- states

def state_code(label: str) -> Optional[str]:
    """'California' / 'CA' / 'Washington, D.C.' -> 'CA' / 'CA' / 'DC'; None when it is not a state."""
    text = label.strip()
    if text.upper() in CD_COUNTS:
        return text.upper()
    return _STATE_BY_NAME.get(text.lower())


def state_code_from_fips(fips: str) -> Optional[str]:
    return _STATE_BY_FIPS.get(str(fips).zfill(2))


# --------------------------------------------------------------------------------------------- districts

_CD_LABEL = re.compile(r"^([A-Z]{2}) (?:Congressional|Delegate) District (?:(\d{2})|\(at Large\))$", re.IGNORECASE)


def cd_key(state: str, district: Optional[str]) -> str:
    return f"{state}-{'AL' if district in (None, '', 'AL') else district}"


def parse_cd_label(label: str) -> Optional[str]:
    """'GA Congressional District 05' -> 'GA-05'; 'AK Congressional District (at Large)' / 'DC Delegate District
    (at Large)' -> 'AK-AL' / 'DC-AL'. None for anything that is not a district of a known state."""
    m = _CD_LABEL.match(label.strip())
    if not m:
        return None
    st = m.group(1).upper()
    if st not in CD_COUNTS:
        return None
    return cd_key(st, m.group(2))


def cd_key_from_geoid(geoid: str) -> Optional[str]:
    """The Census GEOID the geo resolver uses (state FIPS + district: '1305') -> 'GA-05'. '00' and '98' are the
    at-large district and the delegate."""
    geoid = str(geoid)
    if len(geoid) != 4:
        return None
    st = state_code_from_fips(geoid[:2])
    if st is None:
        return None
    district = geoid[2:]
    return cd_key(st, None if district in ("00", "98") else district)


@lru_cache(maxsize=1)
def all_cd_keys() -> list[str]:
    keys = []
    for st, n in sorted(CD_COUNTS.items()):
        if n == 1:
            keys.append(cd_key(st, None))
        else:
            keys.extend(cd_key(st, f"{i:02d}") for i in range(1, n + 1))
    return keys


# --------------------------------------------------------------------------------------------- DMAs

@dataclass(frozen=True)
class DmaRef:
    key: str
    name: str
    rank: int
    home_state: str
    tv_households: Optional[float]
    population: Optional[float]


@lru_cache(maxsize=1)
def dma_table() -> list[DmaRef]:
    from app.data.constanza import load_dma_reference
    rows = [DmaRef(slug(d.dma), d.dma, int(d.rank), d.state, d.tv_hh, d.population) for d in load_dma_reference()]
    keys = [r.key for r in rows]
    if len(set(keys)) != len(keys):
        raise ValueError("two canonical DMA names produce the same key: " + ", ".join(k for k in keys if keys.count(k) > 1))
    return rows


@lru_cache(maxsize=1)
def dma_by_name() -> dict[str, DmaRef]:
    return {d.name: d for d in dma_table()}


@lru_cache(maxsize=1)
def alias_rows() -> list[dict]:
    """The curated alias file: [{alias, dma, how_matched, seen_in}]."""
    with open(ALIAS_CSV, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


@lru_cache(maxsize=1)
def alias_map() -> dict[str, str]:
    """normalized label -> DMA key: every canonical name plus every alias row. Two different DMAs claiming one
    normalized label is a data error and stops everything."""
    mapping: dict[str, str] = {}

    def put(label: str, dma_key: str, where: str) -> None:
        norm = normalize_label(label)
        if norm in mapping and mapping[norm] != dma_key:
            raise ValueError(f"DMA label {label!r} ({where}) is claimed by both {mapping[norm]!r} and {dma_key!r}")
        mapping[norm] = dma_key

    by_name = dma_by_name()
    for d in dma_table():
        put(d.name, d.key, "canonical name")
    for row in alias_rows():
        target = by_name.get(row["dma"])
        if target is None:
            raise ValueError(f"dma_aliases.csv: {row['alias']!r} points at unknown DMA {row['dma']!r}")
        put(row["alias"], target.key, "dma_aliases.csv")
    return mapping


def dma_key_for_label(label: str) -> Optional[str]:
    return alias_map().get(normalize_label(label))


# a loose similarity used ONLY to suggest candidates in an error message
_ABBREV = {"st": "saint", "ft": "fort", "mt": "mount", "bch": "beach"}


def _tokens(text: str) -> list[str]:
    return [_ABBREV.get(t, t) for t in normalize_label(text).split() if t not in ("and", "the")]


def _is_abbrev(a: str, b: str) -> bool:
    if not a or not b or a[0] != b[0]:
        return False
    i = 0
    for ch in b:
        if i < len(a) and a[i] == ch:
            i += 1
    return i == len(a)


def _similarity(label: str, candidate: str) -> float:
    lt, ct = _tokens(label), _tokens(candidate)
    if not lt or not ct:
        return 0.0

    def cover(xs: list[str], ys: list[str]) -> float:
        hit = sum(1 for x in xs if any(x == y or y.startswith(x) or x.startswith(y) or _is_abbrev(x, y) or _is_abbrev(y, x) for y in ys))
        return hit / len(xs)

    return 0.5 * cover(ct, lt) + 0.5 * cover(lt, ct)


def suggest_dma(label: str, limit: int = 3) -> list[tuple[str, float]]:
    scored = sorted(((round(_similarity(label, d.name), 3), d.name) for d in dma_table()), reverse=True)[:limit]
    return [(name, score) for score, name in scored]


# --------------------------------------------------------------------------------------------- seeding

def seed_geo(conn) -> dict[str, int]:
    """Make the geo_* tables match the reference (idempotent; safe to run on every import)."""
    for code, name, fips in STATES:
        conn.execute(
            "INSERT INTO geo_state (state_code, name, fips) VALUES (%s, %s, %s) "
            "ON CONFLICT (state_code) DO UPDATE SET name = EXCLUDED.name, fips = EXCLUDED.fips",
            (code, name, fips))
    for d in dma_table():
        conn.execute(
            "INSERT INTO geo_dma (dma_key, name, rank, home_state, tv_households, population) VALUES (%s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (dma_key) DO UPDATE SET name = EXCLUDED.name, rank = EXCLUDED.rank, home_state = EXCLUDED.home_state, "
            "tv_households = EXCLUDED.tv_households, population = EXCLUDED.population",
            (d.key, d.name, d.rank, d.home_state, d.tv_households, d.population))
    for key in all_cd_keys():
        st, district = key.split("-")
        conn.execute(
            "INSERT INTO geo_congressional_district (cd_key, state_code, district, at_large) VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (cd_key) DO NOTHING", (key, st, district, district == "AL"))
    by_name = dma_by_name()
    n_alias = 0
    seen: set[str] = set()
    pairs: list[tuple[str, str, str, str]] = []
    for d in dma_table():
        pairs.append((normalize_label(d.name), d.name, d.key, "canonical"))
    for row in alias_rows():
        pairs.append((normalize_label(row["alias"]), row["alias"], by_name[row["dma"]].key, "curated"))
    for norm, alias, key, source in pairs:
        if norm in seen:
            continue
        seen.add(norm)
        conn.execute(
            "INSERT INTO geo_dma_alias (alias_norm, alias, dma_key, source) VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (alias_norm) DO UPDATE SET alias = EXCLUDED.alias, dma_key = EXCLUDED.dma_key, source = EXCLUDED.source",
            (norm, alias, key, source))
        n_alias += 1
    return {"states": len(STATES), "dmas": len(dma_table()), "districts": len(all_cd_keys()), "aliases": n_alias}
