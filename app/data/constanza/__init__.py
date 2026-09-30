"""
Project Constanza's bundled DMA reference data — the fixed anchor that
turns a free-text campaign geo into stable, DMA-weighted avails numbers.

Everything here is static data extracted from a CTV avails-by-DMA workbook
(see scripts/extract_constanza_data.py) plus a state-level Hispanic
population % proxy — no live API calls, no DB rows, refreshed only by
re-running the extraction script against an updated workbook. Mirrors how
app/catalog.py is the single source of truth for products: this is the
single source of truth for "how big is DMA X, and what's platform Y's
avails there."
"""
from __future__ import annotations

import gzip
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

_DATA_DIR = Path(__file__).resolve().parent


def read_json_maybe_gz(path: Path) -> dict | list:
    """Reads a bundled JSON file, preferring a gzip-compressed sibling
    (`path` + ".gz") when one exists. The larger Phase 2 bundles (full
    nationwide ACS age/sex tables, ~33K ZCTAs / ~32K Places) are checked
    into git gzipped — JSON's repeated key names compress ~10-13x, taking
    the Constanza data directory from ~109MB to ~11MB — while the smaller
    Phase 1 bundles (dma_reference.json, platform_dma.json, ...) are kept
    as plain .json, so this transparently supports either per-file."""
    gz_path = path.with_suffix(path.suffix + ".gz")
    if gz_path.exists():
        with gzip.open(gz_path, "rt", encoding="utf-8") as f:
            return json.load(f)
    return json.loads(path.read_text())

# The platform keys this bundle has data for. "cumulative" is the blended
# Madhive-list total (Roku Channel + Hulu + Peacock + other FAST + vMVPD,
# deduplicated) — what actually backs the generic "Entravision Plus
# CTV/OTT" English/Spanish/Hispanic/Reach catalog products.
PLATFORMS = ("roku", "prime", "hulu", "peacock", "other_fast", "vmvpd", "cumulative")


@dataclass(frozen=True)
class DMA:
    rank: int
    dma: str
    state: str
    tv_hh: float
    population: float
    pct_us_population: float
    pct_broadband: float
    tier: int

    @property
    def key(self) -> str:
        return self.dma.strip().lower()


@dataclass(frozen=True)
class PlatformFigures:
    imps_low: float
    imps_mid: float
    imps_high: float
    unique_viewers: float | None
    unique_hh: float | None
    reach_pct: float | None


@lru_cache(maxsize=1)
def load_dma_reference() -> tuple[DMA, ...]:
    raw = json.loads((_DATA_DIR / "dma_reference.json").read_text())
    return tuple(DMA(**row) for row in raw)


@lru_cache(maxsize=1)
def load_platform_dma() -> dict[str, dict[str, PlatformFigures]]:
    raw = json.loads((_DATA_DIR / "platform_dma.json").read_text())
    return {
        platform: {
            dma_key: PlatformFigures(
                imps_low=figs["imps_low"], imps_mid=figs["imps_mid"], imps_high=figs["imps_high"],
                unique_viewers=figs.get("unique_viewers"), unique_hh=figs.get("unique_hh"),
                reach_pct=figs.get("reach_pct"),
            )
            for dma_key, figs in dmas.items()
        }
        for platform, dmas in raw.items()
    }


@lru_cache(maxsize=1)
def load_hispanic_pct_by_state() -> dict[str, float]:
    raw = json.loads((_DATA_DIR / "hispanic_pct_by_state.json").read_text())
    return {k: v for k, v in raw.items() if not k.startswith("_")}


@lru_cache(maxsize=1)
def dma_by_key() -> dict[str, DMA]:
    return {d.key: d for d in load_dma_reference()}
