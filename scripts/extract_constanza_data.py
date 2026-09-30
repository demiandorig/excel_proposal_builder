"""
One-off extraction script for Project Constanza's bundled DMA reference data.

Reads the CTV avails-by-DMA workbook (Roku/Prime/Hulu/Peacock/FAST/vMVPD,
built top-down from Nielsen DMA/TV-household/population/broadband data,
self-validated against its own reconciliation + correlation checks — see
its "Validation" sheet) and writes two JSON files under app/data/constanza/:

  dma_reference.json  — the 210-DMA table (name, primary state, TV HH,
                         population, % of US population, % broadband, tier)
  platform_dma.json   — per-DMA, per-platform impressions (low/mid/high),
                         unique viewers/HHs, and reach % of population, for
                         each of: roku, prime, hulu, peacock, other_fast,
                         vmvpd, and the blended "cumulative" (Madhive) total

This deliberately snapshots the workbook's OWN already-computed, already-
validated cell values rather than re-deriving the Assumptions sheet's
formulas in Python — that avoids transcription drift from the national
inputs (Nielsen TV-home counts, Gauge shares, ad-load/fill assumptions,
market-tier skews, ...) into a second, parallel implementation. Re-run this
script whenever a refreshed workbook comes in.

Usage:
    python scripts/extract_constanza_data.py /path/to/CTV_Avails_by_DMA.xlsx
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import openpyxl

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = REPO_ROOT / "app" / "data" / "constanza"

# The workbook states these as the planning-range multipliers around its
# MID case uniformly across every platform (Assumptions sheet, "Low-case
# multiplier (impressions)" / "High-case multiplier (impressions)" rows).
# Used only where a sub-bucket's own sheet doesn't give LOW/HIGH directly
# (other_fast, vmvpd — see module docstring in the loader).
LOW_MULT = 0.7
HIGH_MULT = 1.3


def _rows(ws, header_row: int, first_data_row: int):
    """Yield (row_index, [cell values]) for every non-empty row below the header."""
    for r in range(first_data_row, ws.max_row + 1):
        vals = [ws.cell(row=r, column=c).value for c in range(1, ws.max_column + 1)]
        if all(v is None for v in vals):
            continue
        yield r, vals


def extract_dma_reference(wb) -> list[dict]:
    ws = wb["DMA Model"]
    out = []
    for _, row in _rows(ws, header_row=4, first_data_row=5):
        rank, dma = row[0], row[1]
        if rank is None or dma is None:
            continue  # skip the trailing "TOTAL U.S." row
        out.append({
            "rank": int(rank),
            "dma": dma,
            "state": row[2],
            "tv_hh": row[5],
            "population": row[8],
            "pct_us_population": row[9],
            "pct_broadband": row[10],
            "tier": int(row[11]),
        })
    return out


def _dma_key(name: str) -> str:
    return name.strip().lower()


def extract_platform_from_dma_model(wb) -> dict[str, dict]:
    """Roku + Prime Video both live on the 'DMA Model' sheet."""
    ws = wb["DMA Model"]
    roku, prime = {}, {}
    for _, row in _rows(ws, header_row=4, first_data_row=5):
        rank, dma = row[0], row[1]
        if rank is None or dma is None:
            continue
        key = _dma_key(dma)
        roku[key] = {
            "unique_viewers": row[17], "unique_hh": row[18],
            "imps_low": row[19], "imps_mid": row[20], "imps_high": row[21],
            "reach_pct": row[22],
        }
        prime[key] = {
            "unique_viewers": row[26], "unique_hh": row[27],
            "imps_low": row[28], "imps_mid": row[29], "imps_high": row[30],
            "reach_pct": row[31],
        }
    return {"roku": roku, "prime": prime}


def extract_platform_by_dma_sheet(wb, sheet_name: str) -> dict:
    """Hulu by DMA / Peacock by DMA share the same column layout."""
    ws = wb[sheet_name]
    out = {}
    for _, row in _rows(ws, header_row=4, first_data_row=5):
        rank, dma = row[0], row[1]
        if rank is None or dma is None:
            continue
        key = _dma_key(dma)
        out[key] = {
            "unique_viewers": row[10], "unique_hh": row[11],
            "imps_low": row[12], "imps_mid": row[13], "imps_high": row[14],
            "reach_pct": row[15],
        }
    return out


def extract_cumulative(wb) -> dict:
    """'Cumulative Avails by DMA' — the blended Madhive-list total (feeds the
    generic CTV/OTT English/Spanish/Hispanic/Reach catalog products), plus
    the only per-DMA figures this workbook gives for the 'other FAST'
    (Tubi/Pluto/long-tail) and vMVPD/MVPD-app sub-buckets."""
    ws = wb["Cumulative Avails by DMA"]
    cumulative, other_fast_raw, vmvpd_raw = {}, {}, {}
    for _, row in _rows(ws, header_row=4, first_data_row=5):
        rank, dma = row[0], row[1]
        if rank is None or dma is None:
            continue
        key = _dma_key(dma)
        cumulative[key] = {
            "imps_low": row[14], "imps_mid": row[15], "imps_high": row[16],
            "unique_viewers": row[18], "reach_pct": row[19],
        }
        # Only a MID impressions figure exists per-DMA for these two
        # sub-buckets — no LOW/HIGH split, and (other_fast only) the
        # workbook explicitly declines to give a standalone unique-reach
        # number ("(deduped in FAST total)" in its National Summary sheet:
        # Tubi/Pluto/long-tail reach is folded into Roku Channel's reach
        # ceiling, not additive). We keep that same honesty here rather
        # than inventing one.
        other_fast_raw[key] = {"imps_mid": row[11], "raw_weight": row[10]}
        vmvpd_raw[key] = {"imps_mid": row[13], "raw_weight": row[12]}
    return cumulative, other_fast_raw, vmvpd_raw


def derive_other_fast_and_vmvpd(other_fast_raw: dict, vmvpd_raw: dict,
                                 vmvpd_national_unique_viewers: float,
                                 vmvpd_national_unique_hh: float) -> tuple[dict, dict]:
    """other_fast gets LOW/HIGH derived from the workbook's own uniform
    +/-30% planning-range multipliers (see LOW_MULT/HIGH_MULT); no uniques
    (see extract_cumulative's docstring). vMVPD additionally apportions its
    two NATIONAL unique figures (National Summary sheet) across DMAs by
    its own raw-weight share — the same reach-from-share mechanism the
    workbook itself uses for Roku/Hulu/Peacock (unique = national_reach x
    DMA's normalized share of the platform's raw weight)."""
    vmvpd_weight_total = sum(v["raw_weight"] or 0 for v in vmvpd_raw.values())

    other_fast = {}
    for key, v in other_fast_raw.items():
        mid = v["imps_mid"] or 0
        other_fast[key] = {
            "imps_low": mid * LOW_MULT, "imps_mid": mid, "imps_high": mid * HIGH_MULT,
            "unique_viewers": None, "unique_hh": None, "reach_pct": None,
        }

    vmvpd = {}
    for key, v in vmvpd_raw.items():
        mid = v["imps_mid"] or 0
        share = (v["raw_weight"] or 0) / vmvpd_weight_total if vmvpd_weight_total else 0
        vmvpd[key] = {
            "imps_low": mid * LOW_MULT, "imps_mid": mid, "imps_high": mid * HIGH_MULT,
            "unique_viewers": vmvpd_national_unique_viewers * share,
            "unique_hh": vmvpd_national_unique_hh * share,
            "reach_pct": None,
        }
    return other_fast, vmvpd


def extract_national_summary(wb) -> dict:
    """A handful of national anchor figures, kept alongside the per-DMA data
    so tests can reconcile DMA sums back to the workbook's own declared
    totals (mirrors its 'Validation' sheet's own reconciliation checks)."""
    ws = wb["National Summary"]
    by_platform = {}
    for r in range(4, ws.max_row + 1):
        name = ws.cell(row=r, column=1).value
        if not name:
            continue
        by_platform[name] = {
            "imps_mid": ws.cell(row=r, column=3).value,
            "imps_low": ws.cell(row=r, column=4).value,
            "imps_high": ws.cell(row=r, column=5).value,
            "unique_viewers": ws.cell(row=r, column=6).value,
            "unique_hh": ws.cell(row=r, column=7).value,
        }
    return by_platform


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    src = Path(sys.argv[1]).expanduser()
    wb = openpyxl.load_workbook(src, data_only=True)

    dma_reference = extract_dma_reference(wb)
    dma_model = extract_platform_from_dma_model(wb)
    hulu = extract_platform_by_dma_sheet(wb, "Hulu by DMA")
    peacock = extract_platform_by_dma_sheet(wb, "Peacock by DMA")
    cumulative, other_fast_raw, vmvpd_raw = extract_cumulative(wb)
    national = extract_national_summary(wb)

    # The Cumulative sheet's vMVPD column is "vMVPD + MVPD-app imps" (the
    # two rows combined), so the national reach figure it's apportioned
    # against must combine the same two National Summary rows — using the
    # vMVPD-only row alone would understate the per-DMA unique figures
    # relative to the impressions they're paired with.
    vmvpd_row = national["vMVPDs (Fubo/Hulu Live, Sling, DIRECTV, Philo, Frndly, Vidgo)"]
    mvpd_app_row = national["MVPD streaming apps (Spectrum, Xfinity, Fios, Dish, DIRECTV)"]
    other_fast, vmvpd = derive_other_fast_and_vmvpd(
        other_fast_raw, vmvpd_raw,
        vmvpd_national_unique_viewers=vmvpd_row["unique_viewers"] + mvpd_app_row["unique_viewers"],
        vmvpd_national_unique_hh=vmvpd_row["unique_hh"] + mvpd_app_row["unique_hh"],
    )

    platform_dma = {
        "roku": dma_model["roku"],
        "prime": dma_model["prime"],
        "hulu": hulu,
        "peacock": peacock,
        "other_fast": other_fast,
        "vmvpd": vmvpd,
        "cumulative": cumulative,
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "dma_reference.json").write_text(json.dumps(dma_reference, indent=2))
    (OUT_DIR / "platform_dma.json").write_text(json.dumps(platform_dma, indent=2))
    (OUT_DIR / "national_totals.json").write_text(json.dumps(national, indent=2))

    print(f"Wrote {len(dma_reference)} DMAs x {len(platform_dma)} platforms to {OUT_DIR}")


if __name__ == "__main__":
    main()
