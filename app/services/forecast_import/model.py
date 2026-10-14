"""The neutral in-memory shape every source is read into, before validation and loading."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Optional

SEVERITIES = ("error", "warning", "info")
LEVELS = ("dma", "state", "cd", "zip")


@dataclass(frozen=True)
class Finding:
    rule: str                  # stable id, e.g. "G001" - listed in docs/forecast_data_platform.md
    severity: str              # "error" blocks publishing; "warning" is reported; "info" is for the record
    message: str
    where: str = ""            # file / sheet / level the finding is about

    def as_dict(self) -> dict:
        return {"rule": self.rule, "severity": self.severity, "message": self.message, "where": self.where}


@dataclass
class Figures:
    """One row of one breakdown. `avails` is in the series' unit (monthly or weekly)."""
    avails: float
    reach_universe: Optional[float] = None
    est_reach: Optional[float] = None
    avails_low: Optional[float] = None
    avails_high: Optional[float] = None
    unique_hh: Optional[float] = None
    reach_pct: Optional[float] = None
    basis: str = "reported"    # "reported" | "modeled"


@dataclass
class ProductLink:
    name: str                  # the catalog product's name
    relation: str = "primary"  # "primary" | "baseline_proxy"
    note: str = ""


@dataclass
class PeriodInfo:
    label: str = ""            # shown to the team: "Oct 2025 - Apr 2026 (6 months)"
    start: Optional[str] = None
    end: Optional[str] = None
    source_note: str = ""      # the verbatim cell/text the label was derived from


@dataclass
class SeriesSpec:
    series_key: str
    title: str
    channel: str
    avails_unit: str
    reader: str
    data_basis: str = "platform_reported"
    targeting_key: str = "general"
    description: str = ""
    source: Optional[str] = None                 # file name inside the source directory
    params: dict = field(default_factory=dict)   # reader-specific (sheet, length, platform, extrapolate_from, ...)
    band_low: float = 0.7
    band_high: float = 1.3
    exported_on: Optional[str] = None
    period: Optional[PeriodInfo] = None
    products: list[ProductLink] = field(default_factory=list)

    def fingerprint_payload(self) -> dict:
        return {
            "series_key": self.series_key, "title": self.title, "channel": self.channel, "avails_unit": self.avails_unit,
            "reader": self.reader, "data_basis": self.data_basis, "targeting_key": self.targeting_key,
            "params": self.params, "band": [self.band_low, self.band_high], "exported_on": self.exported_on,
            "period": None if self.period is None else vars(self.period),
        }


@dataclass
class SeriesPayload:
    spec: SeriesSpec
    levels: dict[str, dict[str, Figures]] = field(default_factory=lambda: {lvl: {} for lvl in LEVELS})
    unknown: dict[str, Figures] = field(default_factory=dict)
    devices: dict[str, Figures] = field(default_factory=dict)
    publishers: dict[str, Figures] = field(default_factory=dict)      # display name -> figures
    source_name: str = ""
    source_sha256: str = ""
    findings: list[Finding] = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    # A series extended from another one (Netflix, from Connect) is computed from THAT series' market distribution too, so
    # its identity must change when the distribution does; otherwise a new Connect export would leave it "unchanged".
    reference_hash: str = ""

    def add(self, rule: str, severity: str, message: str, where: str = "") -> None:
        self.findings.append(Finding(rule, severity, message, where))

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "error"]

    def fingerprint(self) -> str:
        blob = json.dumps(self.spec.fingerprint_payload(), sort_keys=True, default=str)
        base = f"{self.source_sha256}:{blob}" + (f":ref={self.reference_hash}" if self.reference_hash else "")
        return hashlib.sha256(base.encode("utf-8")).hexdigest()

    def row_counts(self) -> dict:
        counts = {lvl: len(rows) for lvl, rows in self.levels.items() if rows}
        if self.devices:
            counts["device"] = len(self.devices)
        if self.publishers:
            counts["publisher"] = len(self.publishers)
        return counts

    def national_total(self) -> float:
        """Sum of every DMA the source placed (the unplaced 'Unknown' share is reported separately)."""
        return sum(f.avails for f in self.levels["dma"].values())


class SourceError(Exception):
    """A source file that cannot be read at all (missing, corrupt, wrong format)."""
