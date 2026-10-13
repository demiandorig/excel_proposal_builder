"""
Validation rules for a read series. Every rule has a stable id (listed in docs/forecast_data_platform.md) so a report
line can be discussed, and a severity: an `error` blocks publishing, a `warning` is reported and kept in the version's
record, `info` is context. Tolerances are calibrated to the real exports (values are rounded to tens/hundreds and the
ZIP breakdown drops rows under ~180 avails per month), not guessed.

Reader-level problems (bad headers, unmapped labels, non-numeric cells) were already recorded by the readers as S***,
G*** and L*** findings; this module adds the whole-series rules.
"""
from __future__ import annotations

from typing import Optional

from app.services.forecast_import import geo_reference as geo
from app.services.forecast_import.model import Finding, SeriesPayload

EXPECTED_DMAS = 210
RECONCILE_TOLERANCE = 0.005          # DMA / state / district / device / publisher totals agree within 0.5%
RECONCILE_TOLERANCE_ZIP = 0.015      # the ZIP breakdown drops sub-floor rows and rounds each row
UNKNOWN_WARN = 0.10
UNKNOWN_ERROR = 0.25
LARGE_CHANGE = 0.40                  # a new version this far from the current one needs an explicit OK


def _level_total(payload: SeriesPayload, level: str) -> Optional[float]:
    rows = payload.levels.get(level) or {}
    if not rows and level not in payload.unknown:
        return None
    return sum(f.avails for f in rows.values()) + (payload.unknown[level].avails if level in payload.unknown else 0.0)


def validate(payload: SeriesPayload, previous_total: Optional[float] = None, accept_large_change: bool = False) -> list[Finding]:
    """Run the whole-series rules; findings are appended to `payload.findings` and returned (just the new ones)."""
    before = len(payload.findings)
    spec = payload.spec
    dma_rows = payload.levels["dma"]

    # --- S005: something to load at all
    if not dma_rows:
        payload.add("S005", "error", "no market (DMA) rows were read - the series would be empty", payload.source_name)
        return payload.findings[before:]

    # --- N001 / N002 / N003: the export's own derived columns agree with each other
    for level, st in (payload.stats.get("derived") or {}).items():
        rows = max(st["rows"], 1)
        if st["total_ne_monthly"] / rows > 0.001:
            payload.add("N001", "error", f"Total Avails differs from Monthly Avails on {st['total_ne_monthly']} of {rows} rows", level)
        if st["monthly_sum"] > 0:
            ratio = st["daily_sum"] * 30 / st["monthly_sum"]
            if not 0.97 <= ratio <= 1.05:
                payload.add("N002", "warning", f"Daily Avails x 30 is {ratio:.3f}x Monthly Avails (expected about 1.0)", level)
        if st["reach_gt_universe"] / rows > 0.001:
            payload.add("N003", "error", f"Est. Reach exceeds Reach Universe on {st['reach_gt_universe']} of {rows} rows", level)

    # --- N004: the breakdowns describe the same inventory
    totals = {lvl: _level_total(payload, lvl) for lvl in ("dma", "state", "cd", "zip")}
    for dev in (payload.devices,):
        if dev:
            totals["device"] = sum(f.avails for f in dev.values())
    if payload.publishers:
        totals["publisher"] = sum(f.avails for f in payload.publishers.values())
    present = {k: v for k, v in totals.items() if v}
    if len(present) >= 2:
        anchor = present.get("dma") or max(present.values())
        for lvl, value in present.items():
            tol = RECONCILE_TOLERANCE_ZIP if lvl == "zip" else RECONCILE_TOLERANCE
            if abs(value - anchor) / anchor > tol:
                payload.add("N004", "error", f"the {lvl} breakdown totals {value:,.0f} but the market breakdown totals {anchor:,.0f} "
                                              f"({(value - anchor) / anchor:+.2%}; allowed +/-{tol:.1%})", lvl)
    payload.stats["level_totals"] = {k: round(v) for k, v in present.items()}

    # --- N005: avails the source could not place
    for lvl, fig in payload.unknown.items():
        total = _level_total(payload, lvl) or 0
        share = fig.avails / total if total else 0
        if share > UNKNOWN_ERROR:
            payload.add("N005", "error", f"{share:.0%} of the {lvl} avails are 'Unknown' (limit {UNKNOWN_ERROR:.0%}) - the export is unusable for geo targeting", lvl)
        elif share > UNKNOWN_WARN:
            payload.add("N005", "warning", f"{share:.0%} of the {lvl} avails are 'Unknown'", lvl)
        payload.stats.setdefault("unknown_share", {})[lvl] = round(share, 4)

    # --- N006: market coverage
    n = len(dma_rows)
    if n < 200:
        payload.add("N006", "error", f"only {n} of {EXPECTED_DMAS} DMAs have figures (at least 200 are required)", "dma")
    elif n < EXPECTED_DMAS:
        missing = sorted(d.name for d in geo.dma_table() if d.key not in dma_rows)
        payload.add("N006", "warning", f"{EXPECTED_DMAS - n} DMA(s) have no figures: {', '.join(missing)} - treated as zero", "dma")

    # --- N007: not all zero
    total_dma = payload.national_total()
    if total_dma <= 0:
        payload.add("N007", "error", "every market is zero", "dma")

    # --- N010: a national series whose biggest markets are not the biggest markets is probably mis-keyed
    if n >= 100 and total_dma > 0:
        top5 = {k for k, _ in sorted(((k, f.avails) for k, f in dma_rows.items()), key=lambda kv: -kv[1])[:5]}
        if not top5 & {"new-york", "los-angeles"}:
            payload.add("N010", "warning", "neither New York nor Los Angeles is among the five largest markets - check the file's market labels", "dma")

    # --- N011: ZIP level present but thin
    zips = payload.levels["zip"]
    if zips and len(zips) < 1000:
        payload.add("N011", "warning", f"only {len(zips)} ZIPs have figures", "zip")

    # --- P001: live-sports period note still the one the manifest was written against
    if spec.period and spec.period.source_note:
        seen = (payload.stats.get("header_note") or "").strip()
        if seen != spec.period.source_note.strip():
            payload.add("P001", "warning", f"the sheet's period note is now {seen!r} but the manifest was written for {spec.period.source_note!r} - review the period shown to planners", payload.source_name)

    # --- M002: a new version far from the one being replaced
    if previous_total and total_dma > 0:
        change = (total_dma - previous_total) / previous_total
        payload.stats["change_vs_current"] = round(change, 4)
        if abs(change) > LARGE_CHANGE:
            sev = "warning" if accept_large_change else "error"
            payload.add("M002", sev, f"national total is {change:+.0%} vs the current version ({previous_total:,.0f} -> {total_dma:,.0f})"
                                     + ("" if accept_large_change else "; re-run with --accept-large-change once the source is confirmed"), "series")

    # --- context
    if payload.devices:
        mix = {k: f.avails for k, f in payload.devices.items()}
        tot = sum(mix.values()) or 1
        payload.stats["device_mix"] = {k: round(v / tot, 4) for k, v in mix.items()}
    if payload.publishers:
        top = sorted(payload.publishers.items(), key=lambda kv: -kv[1].avails)[:5]
        payload.stats["top_publishers"] = [(name, round(f.avails / (sum(p.avails for p in payload.publishers.values()) or 1), 4)) for name, f in top]
    return payload.findings[before:]


def summarize(payload: SeriesPayload, limit: int = 200) -> dict:
    """The JSON stored with the version: counts, the findings, and the facts a reviewer wants."""
    by_sev = {"error": 0, "warning": 0, "info": 0}
    for f in payload.findings:
        by_sev[f.severity] = by_sev.get(f.severity, 0) + 1
    return {
        "counts": by_sev,
        "findings": [f.as_dict() for f in payload.findings[:limit]],
        "stats": {k: v for k, v in payload.stats.items() if k not in ("derived", "sample_other_length")},
    }
