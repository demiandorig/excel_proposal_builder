"""
Extracts age x sex population tables from the US Census Bureau's American
Community Survey (ACS) 5-year estimates, at the four geography levels
Constanza's audience layer needs: county, ZCTA (ZIP proxy), congressional
district, and Census Place (city). Table B01001 ("Sex by Age") gives
single/5-year age bins by sex directly from the Bureau, at every one of
these geography levels via the same API — no apportionment needed on the
demographic side (only the DMA-mapping side needs apportionment, handled
separately in constanza_geo_crosswalk.py using this script's output +
extract_constanza_geo_crosswalks.py's spatial-join crosswalks).

Requires a free Census API key (https://api.census.gov/data/key_signup.html)
in the CENSUS_API_KEY environment variable — the API has required one
since a policy change; it's free and instant.

Usage:
    CENSUS_API_KEY=... python scripts/extract_constanza_acs.py
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
ACS_YEAR = "2023"
ACS_BASE = f"https://api.census.gov/data/{ACS_YEAR}/acs/acs5"

# B01001 variable codes -> (sex, age_min, age_max). Table layout: 001 is
# the total, 002 is the "Male:" subtotal (skipped — we want age bands, not
# the subtotal), 003-025 are the 23 male age bins, 026 is the "Female:"
# subtotal (also skipped), 027-049 are the same 23 age bins for female.
# Source: Census B01001 table shell (docs.census.gov/table variable list).
_AGE_BANDS = [
    (0, 4), (5, 9), (10, 14), (15, 17), (18, 19), (20, 20), (21, 21),
    (22, 24), (25, 29), (30, 34), (35, 39), (40, 44), (45, 49), (50, 54),
    (55, 59), (60, 61), (62, 64), (65, 66), (67, 69), (70, 74), (75, 79),
    (80, 84), (85, None),
]
_MALE_START = 3    # B01001_003E is the first male age band (002 is the subtotal)
_FEMALE_START = 27  # B01001_027E is the first female age band (026 is the subtotal)


def _b01001_vars() -> tuple[list[str], dict[str, tuple[str, int, int | None]]]:
    variables = ["B01001_001E"]  # total population
    meaning: dict[str, tuple[str, int, int | None]] = {}
    for i, (lo, hi) in enumerate(_AGE_BANDS):
        male_code = f"B01001_{_MALE_START + i:03d}E"
        female_code = f"B01001_{_FEMALE_START + i:03d}E"
        variables += [male_code, female_code]
        meaning[male_code] = ("male", lo, hi)
        meaning[female_code] = ("female", lo, hi)
    return variables, meaning


def _fetch_json(url: str, retries: int = 3):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = resp.read()
            if not body.strip():
                return None  # some geography/state combos legitimately return an empty body, not an error
            return json.loads(body)
        except urllib.error.HTTPError as e:
            if e.code in (204, 400, 404):  # no data / not a valid geography for this state (e.g. no CDs)
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


def _rows_to_age_sex(rows: list[list[str]], header: list[str], geo_id_cols: list[str], meaning: dict) -> dict[str, dict]:
    idx = {name: i for i, name in enumerate(header)}
    out: dict[str, dict] = {}
    for row in rows:
        geo_id = "".join(row[idx[c]] for c in geo_id_cols)
        total = float(row[idx["B01001_001E"]] or 0)
        bands: dict[tuple[int, int | None], dict[str, float]] = {}
        for code, (sex, lo, hi) in meaning.items():
            val = row[idx[code]]
            val = float(val) if val not in (None, "", "-666666666") else 0.0
            key = (lo, hi)
            bands.setdefault(key, {"male": 0.0, "female": 0.0})[sex] = val
        out[geo_id] = {
            "total_population": total,
            "age_sex": [
                {"age_min": lo, "age_max": hi, **v}
                for (lo, hi), v in sorted(bands.items(), key=lambda kv: kv[0][0])
            ],
        }
    return out


def fetch_county(key: str) -> dict:
    print("Fetching ACS county age/sex (all states, one call)...")
    variables, meaning = _b01001_vars()
    url = f"{ACS_BASE}?get={','.join(variables)}&for=county:*&in=state:*&key={key}"
    data = _fetch_json(url)
    header, rows = data[0], data[1:]
    result = _rows_to_age_sex(rows, header, ["state", "county"], meaning)
    print(f"  {len(result)} counties")
    return result


def fetch_zcta(key: str) -> dict:
    print("Fetching ACS ZCTA age/sex (national, one call)...")
    variables, meaning = _b01001_vars()
    url = f"{ACS_BASE}?get={','.join(variables)}&for=zip%20code%20tabulation%20area:*&key={key}"
    data = _fetch_json(url)
    header, rows = data[0], data[1:]
    result = _rows_to_age_sex(rows, header, ["zip code tabulation area"], meaning)
    print(f"  {len(result)} ZCTAs")
    return result


def fetch_cd(key: str) -> dict:
    print("Fetching ACS congressional district age/sex (119th Congress, per state)...")
    variables, meaning = _b01001_vars()
    result: dict[str, dict] = {}
    for state_fips in [f"{i:02d}" for i in range(1, 79)]:
        url = f"{ACS_BASE}?get={','.join(variables)}&for=congressional%20district:*&in=state:{state_fips}&key={key}"
        try:
            data = _fetch_json(url)
        except urllib.error.HTTPError:
            continue
        if not data:
            continue
        header, rows = data[0], data[1:]
        result.update(_rows_to_age_sex(rows, header, ["state", "congressional district"], meaning))
    print(f"  {len(result)} congressional districts")
    return result


def fetch_place(key: str) -> dict:
    print("Fetching ACS Place (city) age/sex — per state, 50 states + DC + PR...")
    variables, meaning = _b01001_vars()
    result: dict[str, dict] = {}
    for state_fips in [f"{i:02d}" for i in range(1, 73)]:
        url = f"{ACS_BASE}?get={','.join(variables)}&for=place:*&in=state:{state_fips}&key={key}"
        try:
            data = _fetch_json(url)
        except urllib.error.HTTPError:
            continue
        if not data:
            continue
        header, rows = data[0], data[1:]
        result.update(_rows_to_age_sex(rows, header, ["state", "place"], meaning))
    print(f"  {len(result)} places")
    return result


def main():
    key = os.environ.get("CENSUS_API_KEY")
    if not key:
        print("CENSUS_API_KEY not set — get a free key at https://api.census.gov/data/key_signup.html")
        sys.exit(1)

    meta = {
        "source": f"US Census Bureau ACS {ACS_YEAR} 5-year estimates, table B01001 (Sex by Age)",
        "vintage": f"ACS {ACS_YEAR} 5-year",
        "refresh_cadence_days": 365,
    }

    county = fetch_county(key)
    county["_meta"] = meta
    (OUT_DIR / "acs_age_sex_county.json").write_text(json.dumps(county))

    zcta = fetch_zcta(key)
    zcta["_meta"] = meta
    (OUT_DIR / "acs_age_sex_zcta.json").write_text(json.dumps(zcta))

    cd = fetch_cd(key)
    cd["_meta"] = meta
    (OUT_DIR / "acs_age_sex_cd.json").write_text(json.dumps(cd))

    place = fetch_place(key)
    place["_meta"] = meta
    (OUT_DIR / "acs_age_sex_place.json").write_text(json.dumps(place))

    print("Done.")


if __name__ == "__main__":
    main()
