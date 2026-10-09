"""
Extracts Project Constanza's international (non-US) reference data from
the UN World Population Prospects (WPP) 2024 revision — the one clearly-
best unified source for country-level population + age/sex data (free,
CC BY 3.0 IGO, covers all 237 countries/areas). See the Phase 2 plan doc
for why nothing finer than country-level is bundled for international
geographies (no free unified sub-national source exists worldwide).

Writes app/data/constanza/country_reference.json:
    {"_meta": {...}, "<ISO2>": {"name", "iso2", "iso3", "population",
                                 "age_sex": [{"age_min","age_max","male","female"}, ...]}}

Usage:
    python scripts/extract_constanza_international.py
"""
from __future__ import annotations

import csv
import gzip
import io
import json
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = REPO_ROOT / "app" / "data" / "constanza" / "country_reference.json"

TOTAL_POP_URL = (
    "https://population.un.org/wpp/assets/Excel%20Files/1_Indicator%20(Standard)/"
    "CSV_FILES/WPP2024_TotalPopulationBySex.csv.gz"
)
AGE_SEX_URL = (
    "https://population.un.org/wpp/assets/Excel%20Files/1_Indicator%20(Standard)/"
    "CSV_FILES/WPP2024_PopulationByAge5GroupSex_Medium.csv.gz"
)
YEAR = "2024"


def _fetch_csv_rows(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        raw = resp.read()
    text = gzip.decompress(raw).decode("utf-8-sig")
    return csv.DictReader(io.StringIO(text))


def extract_total_population() -> dict[str, dict]:
    countries = {}
    for row in _fetch_csv_rows(TOTAL_POP_URL):
        if row["LocTypeName"] != "Country/Area" or row["Time"] != YEAR:
            continue
        iso2 = row["ISO2_code"]
        if not iso2:
            continue
        countries[iso2] = {
            "name": row["Location"],
            "iso2": iso2,
            "iso3": row["ISO3_code"],
            # WPP reports population in thousands.
            "population": float(row["PopTotal"]) * 1000,
        }
    return countries


def extract_age_sex(countries: dict[str, dict]) -> None:
    iso3_to_iso2 = {c["iso3"]: iso2 for iso2, c in countries.items()}
    for iso2, c in countries.items():
        c["age_sex"] = []
    for row in _fetch_csv_rows(AGE_SEX_URL):
        if row["LocTypeName"] != "Country/Area" or row["Time"] != YEAR:
            continue
        iso2 = iso3_to_iso2.get(row["ISO3_code"])
        if iso2 is None:
            continue
        age_start = int(row["AgeGrpStart"])
        span = row["AgeGrpSpan"]
        age_max = None if span in ("-1", "") else age_start + int(span) - 1
        countries[iso2]["age_sex"].append({
            "age_min": age_start,
            "age_max": age_max,  # None for the open-ended top band (e.g. "100+")
            "male": float(row["PopMale"]) * 1000,
            "female": float(row["PopFemale"]) * 1000,
        })


def main():
    print("Fetching UN WPP 2024 total population by country...")
    countries = extract_total_population()
    print(f"  {len(countries)} countries/areas")

    print("Fetching UN WPP 2024 population by 5-year age group and sex...")
    extract_age_sex(countries)

    countries["_meta"] = {
        "source": "UN World Population Prospects 2024 revision (population.un.org/wpp)",
        "license": "CC BY 3.0 IGO",
        "vintage": f"WPP2024, reference year {YEAR}",
        "refresh_cadence_days": 730,
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(countries, indent=2))
    print(f"Wrote {len(countries) - 1} countries to {OUT_PATH}")


if __name__ == "__main__":
    main()
