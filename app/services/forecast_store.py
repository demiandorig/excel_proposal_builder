"""
Read access to the forecast data in Postgres (see migrations/0001_forecast_platform.sql and
docs/forecast_data_platform.md).

Design rules, all about speed and memory:

  * Only the relevant rows are read. A request for three ZIPs reads three rows (`WHERE series_id = X AND zip = ANY(...)`,
    served by the table's primary key); the 25,000-row ZIP table is never loaded.
  * Series METADATA (which series is current, its unit, period, coverage) is tiny and changes only when someone
    publishes, so it is cached for a minute. A published version is immutable, so anything keyed by `series_id` (a
    series' 210-row DMA table) can be cached for as long as the process lives.
  * Reads are fail-open: with the database unreachable every function returns "nothing" (None / {}), and the callers fall
    back to the bundled data or the population-share estimate exactly as before this module existed.
  * Every value query runs with a short statement timeout so a runaway query can never hold a pooled connection.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Iterable, Optional

from app.db import get_connection

log = logging.getLogger(__name__)

_META_TTL = 60.0
_RETRY_AFTER_FAILURE = 15.0
_STATEMENT_TIMEOUT = "4s"
_LEVELS = ("dma", "state", "cd", "zip")

# The queries the forecast API runs, by name. scripts/explain_forecast_queries.py runs EXPLAIN (ANALYZE, BUFFERS) on these
# exact strings, so the measured plans are the real ones.
SQL_CURRENT_SERIES = (
    "SELECT v.series_id, v.series_key, v.version, v.data_basis, v.period_label, v.period_start, v.period_end, "
    "v.band_low, v.band_high, v.row_counts, v.published_at, v.exported_on, v.validation, "
    "d.title, d.channel, d.avails_unit, d.targeting_key "
    "FROM forecast_series_version v JOIN forecast_series_def d ON d.series_key = v.series_key "
    "WHERE v.status = 'current'")
SQL_PRODUCT_LINKS = (
    "SELECT p.name, sp.series_key, sp.relation, sp.note FROM forecast_series_product sp "
    "JOIN products p ON p.product_key = sp.product_key WHERE p.is_deleted = FALSE")
SQL_DMA_TABLE = ("SELECT dma_key, avails, avails_low, avails_high, reach_universe, est_reach, unique_hh, reach_pct, basis "
                 "FROM forecast_avail_dma WHERE series_id = %s")
SQL_UNKNOWN = "SELECT level, avails FROM forecast_avail_unknown WHERE series_id = %s"
LEVEL_TABLES = {"zip": ("forecast_avail_zip", "zip"), "cd": ("forecast_avail_cd", "cd_key"), "state": ("forecast_avail_state", "state_code")}


def sql_rows(level: str) -> str:
    """The lookup of specific keys in one series: `series_id = X AND key = ANY(keys)`."""
    table, col = LEVEL_TABLES[level]
    return f"SELECT {col} AS k, avails, reach_universe, est_reach FROM {table} WHERE series_id = %s AND {col} = ANY(%s)"


@dataclass(frozen=True)
class SeriesRef:
    """The current published version of one series, plus how a product reached it."""
    series_id: int
    series_key: str
    title: str
    channel: str
    avails_unit: str                   # "monthly" | "weekly"
    targeting_key: str
    version: int
    data_basis: str                    # platform_reported | modeled | extrapolated | mixed
    period_label: Optional[str]
    period_start: Optional[str]
    period_end: Optional[str]
    band_low: float
    band_high: float
    levels: frozenset                  # which geography levels have rows: dma / state / cd / zip
    row_counts: dict = field(default_factory=dict, compare=False, hash=False)
    published_at: Optional[str] = None
    exported_on: Optional[str] = None
    validation_counts: dict = field(default_factory=dict, compare=False, hash=False)
    device_mix: dict = field(default_factory=dict, compare=False, hash=False)
    top_publishers: list = field(default_factory=list, compare=False, hash=False)
    relation: str = "primary"          # how the product reaches this series: primary | baseline_proxy
    link_note: Optional[str] = None

    def with_link(self, relation: str, note: Optional[str]) -> "SeriesRef":
        from dataclasses import replace
        return replace(self, relation=relation, link_note=note)

    def to_public(self) -> dict:
        return {
            "key": self.series_key, "title": self.title, "channel": self.channel, "unit": self.avails_unit,
            "targeting": self.targeting_key, "version": self.version, "data_basis": self.data_basis,
            "period_label": self.period_label, "period_start": self.period_start, "period_end": self.period_end,
            "levels": sorted(self.levels), "published_at": self.published_at, "exported_on": self.exported_on,
            "relation": self.relation, "note": self.link_note,
            "device_mix": self.device_mix or None, "top_publishers": self.top_publishers or None,
        }


# --------------------------------------------------------------------------------------------- series metadata

_lock = threading.Lock()
_cache: dict[str, Any] = {"series": None, "links": None, "at": 0.0, "retry_at": 0.0}


def invalidate() -> None:
    with _lock:
        _cache.update(series=None, links=None, at=0.0, retry_at=0.0)


def _fetch_catalog() -> Optional[tuple[dict[str, SeriesRef], dict[str, tuple[str, str, Optional[str]]]]]:
    """({series_key: SeriesRef}, {product name: (series_key, relation, note)}) from the database, or None."""
    try:
        with get_connection() as conn:
            rows = conn.execute(SQL_CURRENT_SERIES).fetchall()
            links = conn.execute(SQL_PRODUCT_LINKS).fetchall()
    except Exception as exc:  # noqa: BLE001 - no database / tables not created yet
        log.warning("forecast series unavailable (the bundled data and the population-share estimate stay in use): %s", exc)
        return None
    series: dict[str, SeriesRef] = {}
    for r in rows:
        counts = r["row_counts"] or {}
        stats = ((r["validation"] or {}).get("stats")) or {}
        series[r["series_key"]] = SeriesRef(
            series_id=r["series_id"], series_key=r["series_key"], title=r["title"], channel=r["channel"],
            avails_unit=r["avails_unit"], targeting_key=r["targeting_key"], version=r["version"], data_basis=r["data_basis"],
            period_label=r["period_label"], period_start=r["period_start"].isoformat() if r["period_start"] else None,
            period_end=r["period_end"].isoformat() if r["period_end"] else None,
            band_low=float(r["band_low"]), band_high=float(r["band_high"]),
            levels=frozenset(lvl for lvl in _LEVELS if counts.get(lvl)), row_counts=dict(counts),
            published_at=r["published_at"].isoformat() if r["published_at"] else None,
            exported_on=r["exported_on"].isoformat() if r["exported_on"] else None,
            validation_counts=((r["validation"] or {}).get("counts")) or {},
            device_mix=stats.get("device_mix") or {}, top_publishers=stats.get("top_publishers") or [])
    by_product = {r["name"]: (r["series_key"], r["relation"], r["note"]) for r in links if r["series_key"] in series}
    return series, by_product


def _meta() -> tuple[dict[str, SeriesRef], dict[str, tuple[str, str, Optional[str]]]]:
    now = time.monotonic()
    with _lock:
        series, links, at, retry_at = _cache["series"], _cache["links"], _cache["at"], _cache["retry_at"]
    if series is not None and now - at < _META_TTL:
        return series, links
    if now < retry_at:
        return series or {}, links or {}
    fetched = _fetch_catalog()
    with _lock:
        if fetched is None:
            _cache.update(retry_at=now + _RETRY_AFTER_FAILURE)
            return _cache["series"] or {}, _cache["links"] or {}
        _cache.update(series=fetched[0], links=fetched[1], at=now, retry_at=0.0)
        return fetched


def current_series() -> dict[str, SeriesRef]:
    return dict(_meta()[0])


def series_by_key(series_key: Optional[str]) -> Optional[SeriesRef]:
    return _meta()[0].get(series_key) if series_key else None


def series_for_product(names: Iterable[str]) -> Optional[SeriesRef]:
    """The series a catalog product is forecast from by default, found through any of the names it has answered to."""
    series, links = _meta()
    for name in names:
        link = links.get(name)
        if link and link[0] in series:
            return series[link[0]].with_link(link[1], link[2])
    return None


def products_for_series() -> dict[str, list[tuple[str, str]]]:
    """{series_key: [(product name, relation)]} of the default links."""
    out: dict[str, list[tuple[str, str]]] = {}
    for name, (key, relation, _note) in _meta()[1].items():
        out.setdefault(key, []).append((name, relation))
    return out


# --------------------------------------------------------------------------------------------- values


@dataclass(frozen=True)
class Row:
    key: str
    avails: float
    avails_low: Optional[float] = None
    avails_high: Optional[float] = None
    reach_universe: Optional[float] = None
    est_reach: Optional[float] = None
    unique_hh: Optional[float] = None
    reach_pct: Optional[float] = None
    basis: str = "reported"


def _query(sql: str, params: tuple) -> list[dict]:
    with get_connection() as conn:
        conn.execute(f"SET LOCAL statement_timeout = '{_STATEMENT_TIMEOUT}'")
        return list(conn.execute(sql, params).fetchall())


@lru_cache(maxsize=96)
def dma_table(series_id: int) -> dict[str, Row]:
    """Every DMA row of a series, keyed by dma_key. A published version never changes, so this is cached by series_id
    (about 210 small rows each); a failure is not cached."""
    rows = _query(SQL_DMA_TABLE, (series_id,))
    return {r["dma_key"]: Row(r["dma_key"], r["avails"], r["avails_low"], r["avails_high"], r["reach_universe"],
                              r["est_reach"], r["unique_hh"], r["reach_pct"], r["basis"]) for r in rows}


def _rows(level: str, series_id: int, keys: Iterable[str]) -> dict[str, Row]:
    wanted = sorted({k for k in keys if k})
    if not wanted:
        return {}
    rows = _query(sql_rows(level), (series_id, wanted))
    return {r["k"]: Row(r["k"], r["avails"], reach_universe=r["reach_universe"], est_reach=r["est_reach"]) for r in rows}


def zip_rows(series_id: int, zips: Iterable[str]) -> dict[str, Row]:
    return _rows("zip", series_id, zips)


def cd_rows(series_id: int, cd_keys: Iterable[str]) -> dict[str, Row]:
    return _rows("cd", series_id, cd_keys)


def state_rows(series_id: int, states: Iterable[str]) -> dict[str, Row]:
    return _rows("state", series_id, states)


def unknown_share(series_id: int) -> dict[str, float]:
    """Share of each level's avails the source could not place geographically (informational)."""
    rows = _query(SQL_UNKNOWN, (series_id,))
    return {r["level"]: r["avails"] for r in rows}


# --------------------------------------------------------------------------------------------- admin overview

def data_overview() -> dict:
    """Admin -> Forecast -> Data: each series' versions (with validation counts) and the latest import batches."""
    with get_connection() as conn:
        versions = conn.execute(
            "SELECT v.series_key, d.title, d.channel, d.avails_unit, v.version, v.status, v.data_basis, v.published_at, v.published_by, "
            "v.exported_on, v.period_label, v.row_counts, v.source_name, v.created_at, (v.validation->'counts') AS counts "
            "FROM forecast_series_version v JOIN forecast_series_def d USING (series_key) "
            "ORDER BY d.channel, v.series_key, v.version DESC").fetchall()
        batches = conn.execute(
            "SELECT batch_id, started_at, finished_at, requested_by, tool_version, status, "
            "jsonb_array_length(sources) AS sources FROM forecast_import_batch ORDER BY batch_id DESC LIMIT 10").fetchall()
    by_series: dict[str, dict] = {}
    for v in versions:
        entry = by_series.setdefault(v["series_key"], {"series_key": v["series_key"], "title": v["title"], "channel": v["channel"],
                                                       "unit": v["avails_unit"], "versions": []})
        entry["versions"].append({
            "version": v["version"], "status": v["status"], "data_basis": v["data_basis"], "period_label": v["period_label"],
            "published_at": v["published_at"].isoformat() if v["published_at"] else None, "published_by": v["published_by"],
            "exported_on": v["exported_on"].isoformat() if v["exported_on"] else None, "row_counts": v["row_counts"],
            "source": v["source_name"], "counts": v["counts"],
        })
    return {
        "series": list(by_series.values()),
        "batches": [{**b, "started_at": b["started_at"].isoformat(), "finished_at": b["finished_at"].isoformat() if b["finished_at"] else None} for b in batches],
    }
