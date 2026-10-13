"""
Lightweight visibility check for Constanza's bundled data vintages — not a
monitoring/paging system (overkill for a small team), just something to
run occasionally (or wire into a simple periodic job later) that prints
which bundles are past their own stated refresh cadence. Every bundle
this script checks carries a "_meta": {"vintage": ..., "refresh_cadence_days": ...}
key (see the various scripts/extract_constanza_*.py + scripts/
aggregate_constanza_acs_dma_state.py that produce them).

Age is measured from the bundle file's modification time, which a fresh git checkout
(a deploy) resets to "now" — so on a deployed copy it can't tell you how old the DATA is.
Read the printed vintage (the data's own stamp) there, and run this on the machine that
refreshes the data for the time-based STALE check.

Usage:
    python scripts/check_constanza_data_freshness.py
"""
from __future__ import annotations

import datetime
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
from app.data.constanza import read_json_maybe_gz  # noqa: E402

DATA_DIR = REPO_ROOT / "app" / "data" / "constanza"

BUNDLES = [
    "country_reference.json",
    "acs_age_sex_county.json", "acs_age_sex_zcta.json", "acs_age_sex_cd.json",
    "acs_age_sex_place.json", "acs_age_sex_dma.json", "acs_age_sex_state.json",
    "geo/county_dma.json", "geo/cd_dma.json",  # county_dma has no _meta today (extract_constanza_geo_crosswalks.py doesn't stamp one yet) — reported as unknown
]


def _resolve(path: Path) -> Path | None:
    """The bundle's real file: the .gz sibling when present (that's the one
    the app actually reads), else the plain .json, else None."""
    gz = path.with_suffix(path.suffix + ".gz")
    if gz.exists():
        return gz
    return path if path.exists() else None


def _file_mtime_days(path: Path) -> int:
    return (datetime.datetime.now() - datetime.datetime.fromtimestamp(path.stat().st_mtime)).days


def main():
    print("Constanza bundled-data freshness check")
    print("=" * 60)
    for rel_path in BUNDLES:
        path = _resolve(DATA_DIR / rel_path)
        if path is None:
            print(f"  MISSING  {rel_path}")
            continue
        try:
            data = read_json_maybe_gz(DATA_DIR / rel_path)
            meta = data.get("_meta", {}) if isinstance(data, dict) else {}
        except Exception as e:
            print(f"  ERROR    {rel_path}: {e}")
            continue

        age_days = _file_mtime_days(path)           # file timestamp, not the data's age — see the module docstring
        cadence = meta.get("refresh_cadence_days")
        vintage = meta.get("vintage", "(no _meta.vintage stamped)")
        if cadence and age_days > cadence:
            print(f"  STALE    {rel_path} — file is {age_days}d old, expected refresh every {cadence}d (vintage: {vintage})")
        else:
            cadence_note = f"cadence {cadence}d" if cadence else "no stated cadence"
            print(f"  OK       {rel_path} — file is {age_days}d old ({cadence_note}, vintage: {vintage})")

    print()
    print("Reminder: the Census congressional-district relationship file specifically")
    print("should be re-pulled each redistricting cycle (active 2025-26 mid-decade")
    print("redistricting in multiple states) — this script can't detect a NEW map")
    print("existing, only that time has passed; re-check manually around redistricting news.")


if __name__ == "__main__":
    main()
