"""
Extracts the Hispanic / Latino population of every geography Constanza resolves — the data behind its Hispanic targeting layer
(constanza_audience.py / constanza_hispanic.py).

Source: US Census Bureau ACS 5-year estimates, table B01001I ("Sex by Age (Hispanic or Latino)"), pulled for the same
geography levels and the same ACS vintage as the age x sex bundles (extract_constanza_acs.py): county, ZCTA (ZIP proxy),
congressional district and Census Place (city). It carries the Hispanic count by sex and age band, so a Hispanic cut
combines with an age / gender cut from ONE table (a Hispanic 18-34 audience is not the Hispanic share times the
18-34 share: the Hispanic population is younger than the rest). DMA and state bundles are aggregated from the county one
exactly as aggregate_constanza_acs_dma_state.py does for age x sex (a split county contributes its DMA-share).

Each geography is one list, not a dict, so the bundles stay small and quick to parse:

    "<geo id>": [total_population, hispanic_total, male band 0..13, female band 0..13]

with the 14 bands in `_meta.bands` (B01001I publishes coarser bands above 34 than B01001 does). `total_population` is
B01001_001E, so a share needs no second bundle.

Output (gzipped, under app/data/constanza/): acs_hispanic_county / _zcta / _cd / _place / _dma / _state .json.gz

Requires a free Census API key in CENSUS_API_KEY (https://api.census.gov/data/key_signup.html). Never commit the key.

Usage:
    CENSUS_API_KEY=... python scripts/extract_constanza_hispanic.py
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = REPO_ROOT / "app" / "data" / "constanza"
GEO_DIR = OUT_DIR / "geo"
sys.path.insert(0, str(REPO_ROOT))
from app.data.constanza import read_json_maybe_gz, write_json_gz  # noqa: E402

ACS_YEAR = "2023"                      # keep equal to extract_constanza_acs.ACS_YEAR: one vintage across the bundles
ACS_BASE = f"https://api.census.gov/data/{ACS_YEAR}/acs/acs5"

# B01001I: 001 total Hispanic; 002 "Male:" subtotal; 003-016 the 14 male bands; 017 "Female:" subtotal; 018-031 the female bands.
BANDS: list[tuple[int, int | None]] = [
    (0, 4), (5, 9), (10, 14), (15, 17), (18, 19), (20, 24), (25, 29), (30, 34),
    (35, 44), (45, 54), (55, 64), (65, 74), (75, 84), (85, None),
]
_MALE_FIRST, _FEMALE_FIRST = 3, 18
VARIABLES = (["B01001_001E", "B01001I_001E"]
             + [f"B01001I_{_MALE_FIRST + i:03d}E" for i in range(len(BANDS))]
             + [f"B01001I_{_FEMALE_FIRST + i:03d}E" for i in range(len(BANDS))])

# What a complete run holds (2023 ACS: 3,222 counties, 33,772 ZCTAs, 440 districts, 32,325 places): far less means the Census API answered with an error page
# or a state's request kept failing, and publishing that would silently ship a bundle with holes in it.
SANITY_MINIMUMS = {"counties": 3000, "ZCTAs": 30000, "congressional districts": 430, "places": 30000}
FAILED: list[str] = []          # requests that failed outright (not "this state has none"), filled by _fetch

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


def _fetch_json(url: str, retries: int = 3):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=120) as resp:
                body = resp.read()
            if not body.strip():
                return None
            return json.loads(body)
        except urllib.error.HTTPError as e:
            if e.code in (204, 400, 404):        # no data / not a valid geography for this state (e.g. no CDs)
                return None
            if attempt == retries - 1:
                raise
            time.sleep(2)
        except json.JSONDecodeError:
            return None
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(2)


def _num(value) -> float:
    """An ACS cell as a number: the Bureau's missing / suppressed sentinels (-666666666 ...) are 0, never a negative population."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0
    return v if v > -1e8 else 0.0


def _rows_to_lists(rows: list[list[str]], header: list[str], geo_id_cols: list[str]) -> dict[str, list]:
    idx = {name: i for i, name in enumerate(header)}
    out: dict[str, list] = {}
    for row in rows:
        geo_id = "".join(row[idx[c]] for c in geo_id_cols)
        out[geo_id] = [int(_num(row[idx[v]])) for v in VARIABLES]
    return out


def _fetch(label: str, geo_clause: str, key: str, per_state: range | None = None, id_cols: list[str] | None = None,
           in_clause: str = "") -> dict[str, list]:
    print(f"Fetching ACS {label} Hispanic population by sex and age...")
    result: dict[str, list] = {}
    if per_state is None:
        data = _fetch_json(f"{ACS_BASE}?get={','.join(VARIABLES)}&for={geo_clause}{in_clause}&key={key}")
        if data:
            result.update(_rows_to_lists(data[1:], data[0], id_cols))
        else:
            FAILED.append(f"{label}: the Census API returned no data")
    else:
        for fips in [f"{i:02d}" for i in per_state]:
            try:
                data = _fetch_json(f"{ACS_BASE}?get={','.join(VARIABLES)}&for={geo_clause}&in=state:{fips}&key={key}")
            except urllib.error.HTTPError as exc:
                FAILED.append(f"{label}: state {fips} (HTTP {exc.code})")
                continue
            if data:
                result.update(_rows_to_lists(data[1:], data[0], id_cols))
    print(f"  {len(result)} {label}")
    return result


def _check_sizes(counts: dict[str, int], failures: list[str]) -> None:
    """Stop before anything is written when a level came back far smaller than a complete run, or any request failed outright."""
    short = {name: n for name, n in counts.items() if n < SANITY_MINIMUMS.get(name, 0)}
    if short or failures:
        problems = [f"{name}: {n:,} (a complete run has over {SANITY_MINIMUMS[name]:,})" for name, n in short.items()] + list(failures[:10])
        raise SystemExit("Refusing to write the Hispanic bundles from an incomplete Census pull:\n  " + "\n  ".join(problems))


def _aggregate(county: dict[str, list]) -> tuple[dict[str, list], dict[str, list]]:
    """DMA and state lists from the county list: a state is its counties' sum; a DMA takes each county at its DMA-share."""
    county_dma = read_json_maybe_gz(GEO_DIR / "county_dma.json")
    by_dma: dict[str, list[float]] = {}
    by_state: dict[str, list[float]] = {}
    width = len(VARIABLES)
    for fips, vals in county.items():
        if fips == "_meta":
            continue
        abbr = _STATE_FIPS_TO_ABBR.get(fips[:2])
        if abbr:
            acc = by_state.setdefault(abbr, [0.0] * width)
            for i, v in enumerate(vals):
                acc[i] += v
        for share in county_dma.get(fips, []):
            acc = by_dma.setdefault(share["dma"], [0.0] * width)
            for i, v in enumerate(vals):
                acc[i] += v * share["share"]
    rounded = lambda d: {k: [round(x, 1) for x in v] for k, v in d.items()}      # noqa: E731
    return rounded(by_dma), rounded(by_state)


def main():
    key = os.environ.get("CENSUS_API_KEY")
    if not key:
        print("CENSUS_API_KEY not set — get a free key at https://api.census.gov/data/key_signup.html")
        sys.exit(1)

    meta = {
        "source": f"US Census Bureau ACS {ACS_YEAR} 5-year estimates, table B01001I (Sex by Age, Hispanic or Latino) with B01001_001E for the total",
        "vintage": f"ACS {ACS_YEAR} 5-year",
        "refresh_cadence_days": 365,
        "layout": "[total_population, hispanic_total, male band 0..13, female band 0..13]",
        "bands": [list(b) for b in BANDS],
    }

    county = _fetch("counties", "county:*", key, id_cols=["state", "county"], in_clause="&in=state:*")
    zcta = _fetch("ZCTAs", "zip%20code%20tabulation%20area:*", key, id_cols=["zip code tabulation area"])
    cd = _fetch("congressional districts", "congressional%20district:*", key, per_state=range(1, 79), id_cols=["state", "congressional district"])
    place = _fetch("places", "place:*", key, per_state=range(1, 73), id_cols=["state", "place"])
    _check_sizes({"counties": len(county), "ZCTAs": len(zcta), "congressional districts": len(cd), "places": len(place)}, FAILED)
    dma, state = _aggregate(county)

    for name, table in (("county", county), ("zcta", zcta), ("cd", cd), ("place", place), ("dma", dma), ("state", state)):
        table = dict(table)
        table["_meta"] = meta if name not in ("dma", "state") else {
            **meta, "source": "Aggregated from the ACS county table via the county->DMA / state crosswalks (this script)"}
        path = write_json_gz(OUT_DIR / f"acs_hispanic_{name}.json", table)
        print(f"  wrote {path.name}: {len(table) - 1} geographies, {path.stat().st_size / 1e6:.2f} MB")
    print("Done.")


if __name__ == "__main__":
    main()
