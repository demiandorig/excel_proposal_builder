"""
Aggregates Constanza's county-level ACS age/sex data (extract_constanza_
acs.py) up to DMA and state level, using the county->DMA crosswalk
(extract_constanza_geo_crosswalks.py) and each county's own state FIPS
(county_names.json). Without this, a whole-DMA or whole-state geo query
(the majority of today's traffic) has NO age/gender data to narrow
against at all — constanza_audience.py's age_gender_share() falls back to
"not applied" for every area whose source_type is "dma"/"state" (see that
module's ACS_FILE_BY_SOURCE_TYPE mapping, which intentionally excludes
those two source types until this script has been run).

A county that straddles two DMAs contributes its FULL age/sex counts to
BOTH DMAs weighted by its DMA-share (the same population-weighted logic
constanza_engine.estimate_platform_modeled already uses for impressions)
— age/sex demographics don't have their own separate spatial distribution
data at the sub-county level here, so this reuses the population-based
DMA-share as the split, consistent with how the rest of Constanza treats
a split county's contribution.

Usage (run AFTER both extract_constanza_acs.py and
extract_constanza_geo_crosswalks.py have produced their output):
    python scripts/aggregate_constanza_acs_dma_state.py
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "app" / "data" / "constanza"
GEO_DIR = DATA_DIR / "geo"

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


def _blank_age_sex(template: list[dict]) -> list[dict]:
    return [{"age_min": b["age_min"], "age_max": b["age_max"], "male": 0.0, "female": 0.0} for b in template]


def _add(dst: dict, src: dict, weight: float) -> None:
    dst["total_population"] += src["total_population"] * weight
    for i, band in enumerate(src["age_sex"]):
        dst["age_sex"][i]["male"] += band["male"] * weight
        dst["age_sex"][i]["female"] += band["female"] * weight


def main():
    county_acs = json.loads((DATA_DIR / "acs_age_sex_county.json").read_text())
    county_dma = json.loads((GEO_DIR / "county_dma.json").read_text())
    county_names = json.loads((GEO_DIR / "county_names.json").read_text())

    template = next(v["age_sex"] for k, v in county_acs.items() if k != "_meta")

    by_dma: dict[str, dict] = {}
    by_state: dict[str, dict] = {}

    for fips, entry in county_acs.items():
        if fips == "_meta":
            continue
        state_fips = county_names.get(fips, {}).get("state_fips")
        state_abbr = _STATE_FIPS_TO_ABBR.get(state_fips)
        if state_abbr:
            by_state.setdefault(state_abbr, {"total_population": 0.0, "age_sex": _blank_age_sex(template)})
            _add(by_state[state_abbr], entry, 1.0)

        for share in county_dma.get(fips, []):
            dma = share["dma"]
            by_dma.setdefault(dma, {"total_population": 0.0, "age_sex": _blank_age_sex(template)})
            _add(by_dma[dma], entry, share["share"])

    meta = {
        "source": "Aggregated from ACS county data via county->DMA/state crosswalks (this script)",
        "vintage": county_acs.get("_meta", {}).get("vintage", "unknown"),
        "refresh_cadence_days": 365,
    }
    by_dma["_meta"] = meta
    by_state["_meta"] = meta

    (DATA_DIR / "acs_age_sex_dma.json").write_text(json.dumps(by_dma))
    (DATA_DIR / "acs_age_sex_state.json").write_text(json.dumps(by_state))
    print(f"Wrote {len(by_dma) - 1} DMAs and {len(by_state) - 1} states.")


if __name__ == "__main__":
    main()
