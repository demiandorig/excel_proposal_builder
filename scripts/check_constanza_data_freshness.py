"""
Lightweight visibility check for Constanza's bundled data vintages — not a
monitoring/paging system (overkill for a small team), just something to
run occasionally (or wire into a simple periodic job later) that prints
which bundles are past their own stated refresh cadence. Every bundle
this script checks carries a "_meta": {"vintage": ..., "refresh_cadence_days": ...}
key (see the various scripts/extract_constanza_*.py + scripts/
aggregate_constanza_acs_dma_state.py that produce them).

Usage:
    python scripts/check_constanza_data_freshness.py
"""
from __future__ import annotations

import datetime
import json
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "app" / "data" / "constanza"

BUNDLES = [
    "country_reference.json",
    "acs_age_sex_county.json", "acs_age_sex_zcta.json", "acs_age_sex_cd.json",
    "acs_age_sex_place.json", "acs_age_sex_dma.json", "acs_age_sex_state.json",
    "geo/county_dma.json",  # no _meta today (extract_constanza_geo_crosswalks.py doesn't stamp one yet) — reported as unknown
]


def _file_mtime_days(path: Path) -> int:
    return (datetime.datetime.now() - datetime.datetime.fromtimestamp(path.stat().st_mtime)).days


def main():
    print("Constanza bundled-data freshness check")
    print("=" * 60)
    for rel_path in BUNDLES:
        path = DATA_DIR / rel_path
        if not path.exists():
            print(f"  MISSING  {rel_path}")
            continue
        try:
            data = json.loads(path.read_text())
            meta = data.get("_meta", {}) if isinstance(data, dict) else {}
        except Exception as e:
            print(f"  ERROR    {rel_path}: {e}")
            continue

        age_days = _file_mtime_days(path)
        cadence = meta.get("refresh_cadence_days")
        vintage = meta.get("vintage", "(no _meta.vintage stamped)")
        if cadence and age_days > cadence:
            print(f"  STALE    {rel_path} — {age_days}d old, expected refresh every {cadence}d (vintage: {vintage})")
        else:
            cadence_note = f"cadence {cadence}d" if cadence else "no stated cadence"
            print(f"  OK       {rel_path} — {age_days}d old ({cadence_note}, vintage: {vintage})")

    print()
    print("Reminder: the Census congressional-district relationship file specifically")
    print("should be re-pulled each redistricting cycle (active 2025-26 mid-decade")
    print("redistricting in multiple states) — this script can't detect a NEW map")
    print("existing, only that time has passed; re-check manually around redistricting news.")


if __name__ == "__main__":
    main()
