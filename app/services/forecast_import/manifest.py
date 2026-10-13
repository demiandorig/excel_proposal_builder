"""The manifest: which source file feeds which series, and which catalog products are forecast from it.

app/data/forecast/manifest.json is data a human reviews in a pull request. Loading it validates the shape, so a typo
(a duplicate series key, an unknown reader, an impossible date) fails here with a clear message instead of halfway
through an import.
"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Optional

from app.services.forecast_import.model import PeriodInfo, ProductLink, SeriesSpec

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "forecast"
MANIFEST_PATH = DATA_DIR / "manifest.json"

CHANNELS = ("ctv", "olv", "display", "pause_ads", "live_sports", "netflix", "platform_model")
UNITS = ("monthly", "weekly")
READERS = ("madhive_zip", "netflix_baseline_csv", "live_sports_xlsx", "bundled_platform_json")
BASES = ("platform_reported", "modeled", "extrapolated", "mixed")
RELATIONS = ("primary", "baseline_proxy")
_KEY = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class ManifestError(ValueError):
    pass


def _date(value, where: str) -> Optional[str]:
    if value in (None, ""):
        return None
    try:
        date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ManifestError(f"{where}: {value!r} is not an ISO date (YYYY-MM-DD)") from exc
    return value


def parse_manifest(data: dict) -> list[SeriesSpec]:
    if not isinstance(data, dict) or data.get("manifest_version") != 1:
        raise ManifestError("manifest_version must be 1")
    specs: list[SeriesSpec] = []
    seen: set[str] = set()
    for i, raw in enumerate(data.get("series") or []):
        where = f"series[{i}] ({raw.get('series_key', '?')})"
        key = raw.get("series_key", "")
        if not _KEY.match(key):
            raise ManifestError(f"{where}: series_key must be lower-case letters, digits, '-' or '_'")
        if key in seen:
            raise ManifestError(f"{where}: series_key is used twice")
        seen.add(key)
        for field, allowed in (("channel", CHANNELS), ("avails_unit", UNITS), ("reader", READERS)):
            if raw.get(field) not in allowed:
                raise ManifestError(f"{where}: {field} {raw.get(field)!r} is not one of {allowed}")
        basis = raw.get("data_basis", "platform_reported")
        if basis not in BASES:
            raise ManifestError(f"{where}: data_basis {basis!r} is not one of {BASES}")
        if not raw.get("title"):
            raise ManifestError(f"{where}: title is required")
        low, high = float(raw.get("band_low", 0.7)), float(raw.get("band_high", 1.3))
        if not (0 < low <= 1 <= high):
            raise ManifestError(f"{where}: the planning band must satisfy 0 < band_low <= 1 <= band_high")
        period = None
        if raw.get("period"):
            p = raw["period"]
            start, end = _date(p.get("start"), f"{where} period.start"), _date(p.get("end"), f"{where} period.end")
            if start and end and start > end:
                raise ManifestError(f"{where}: period starts after it ends")
            period = PeriodInfo(label=p.get("label", ""), start=start, end=end, source_note=p.get("source_note", ""))
        links = []
        for link in raw.get("products", []):
            if link.get("relation", "primary") not in RELATIONS:
                raise ManifestError(f"{where}: product link relation {link.get('relation')!r} is not one of {RELATIONS}")
            if not link.get("name"):
                raise ManifestError(f"{where}: a product link has no name")
            links.append(ProductLink(name=link["name"], relation=link.get("relation", "primary"), note=link.get("note", "")))
        if raw["reader"] != "bundled_platform_json" and not raw.get("source"):
            raise ManifestError(f"{where}: source file is required for reader {raw['reader']}")
        specs.append(SeriesSpec(
            series_key=key, title=raw["title"], channel=raw["channel"], avails_unit=raw["avails_unit"], reader=raw["reader"],
            data_basis=basis, targeting_key=raw.get("targeting_key", "general"), description=raw.get("description", ""),
            source=raw.get("source"), params=raw.get("params") or {}, band_low=low, band_high=high,
            exported_on=_date(raw.get("exported_on"), f"{where} exported_on"), period=period, products=links,
        ))
    for spec in specs:
        ref = spec.params.get("extrapolate_from")
        if ref and ref not in seen:
            raise ManifestError(f"series {spec.series_key!r} extends from {ref!r}, which is not in the manifest")
    return specs


def load_manifest(path: Optional[Path] = None) -> tuple[list[SeriesSpec], Path]:
    path = path or MANIFEST_PATH
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"cannot read manifest {path}: {exc}") from exc
    return parse_manifest(data), path.parent / data.get("source_dir", "sources")
