"""Shared helpers for the forecast data platform tests: the shipped source files read once, and synthetic exports."""
from __future__ import annotations

import csv
import io
import zipfile
from functools import lru_cache
from pathlib import Path

from app.services.forecast_import import geo_reference as geo
from app.services.forecast_import.manifest import load_manifest
from app.services.forecast_import.service import read_all

COLUMNS = ["Total Avails", "Daily Avails", "Weekly Avails", "Monthly Avails", "Reach Universe", "Est. Reach", "Est. Reach %"]
KINDS = {"dma": "Metros", "state": "States", "zip": "Zip Codes", "cd": "Congressional District", "device": "Devices", "publisher": "Publishers"}


@lru_cache(maxsize=1)
def shipped():
    """(specs, source_dir, {series_key: payload or SourceError}) for every series in the shipped manifest. Reading all 39
    takes about 15 seconds, so every test module shares this one read."""
    specs, source_dir = load_manifest()
    return specs, source_dir, read_all(specs, source_dir)


def payload(series_key: str):
    return shipped()[2][series_key]


def row(label, monthly, universe=None, est=None):
    universe = universe if universe is not None else max(int(monthly) // 40, 1)
    est = est if est is not None else min(universe, max(int(monthly) // 45, 1))
    return [label, int(monthly), int(monthly) // 30, int(monthly) * 7 // 30, int(monthly), universe, est, "0.10%"]


def csv_bytes(first_column, rows, header=None):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header if header is not None else [first_column] + COLUMNS)
    for r in rows:
        w.writerow(r)
    return buf.getvalue().encode("utf-8")


def make_export(path: Path, prefix="Test Product", dmas=None, states=None, zips=None, cds=None, devices=None, publishers=None,
                omit=(), header_overrides=None, extra_members=None) -> Path:
    """Write a small valid Madhive-style export zip. Each breakdown can be replaced (rows are `row(...)` lists) or omitted."""
    dmas = dmas if dmas is not None else [row("New York", 2_000_000), row("Los Angeles", 1_500_000), row("Unknown", 50_000)]
    states = states if states is not None else [row("California", 1_500_000), row("New York", 2_000_000), row("Unknown", 50_000)]
    zips = zips if zips is not None else [row("90210", 100_000), row("10001", 200_000)]
    cds = cds if cds is not None else [row("CA Congressional District 12", 40_000), row("NY Congressional District 12", 60_000), row("Unknown", 50_000)]
    devices = devices if devices is not None else [row("Connected TV", 3_000_000), row("Mobile", 550_000)]
    publishers = publishers if publishers is not None else [row("Tubi", 2_000_000), row("Roku Channel", 1_550_000)]
    members = {"dma": dmas, "state": states, "zip": zips, "cd": cds, "device": devices, "publisher": publishers}
    header_overrides = header_overrides or {}
    with zipfile.ZipFile(path, "w") as zf:
        for kind, rows in members.items():
            if kind in omit:
                continue
            first = KINDS[kind]
            zf.writestr(f"{prefix} by {first}.csv", csv_bytes(first, rows, header_overrides.get(kind)))
            zf.writestr(f"__MACOSX/._{prefix} by {first}.csv", b"junk")      # what a Mac adds; must be ignored
        for name, data in (extra_members or {}).items():
            zf.writestr(name, data)
    return path
