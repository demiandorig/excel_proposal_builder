"""
Writes a validated payload to Postgres as an immutable VERSION, and moves the 'current' pointer.

  stage     insert the version row (status 'staged') and its values with COPY, inside one transaction
  publish   retire the current version of the series and make the staged one current, in one transaction
  rollback  make a previous version current again (the data was never deleted)
  prune     delete the values of old retired versions (keep the newest N)

Nothing here updates a number in place: a re-import is a new version, so a bad import is a rollback, not a repair.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Optional

from app.services.forecast_import import geo_reference as geo
from app.services.forecast_import.model import Figures, SeriesPayload
from app.services.forecast_import.validate import summarize

log = logging.getLogger(__name__)


@dataclass
class StageResult:
    series_id: int
    version: int
    row_counts: dict


def upsert_series_def(conn, payload: SeriesPayload) -> None:
    s = payload.spec
    conn.execute(
        "INSERT INTO forecast_series_def (series_key, title, channel, targeting_key, avails_unit, description) "
        "VALUES (%s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (series_key) DO UPDATE SET title = EXCLUDED.title, channel = EXCLUDED.channel, "
        "targeting_key = EXCLUDED.targeting_key, avails_unit = EXCLUDED.avails_unit, description = EXCLUDED.description",
        (s.series_key, s.title, s.channel, s.targeting_key, s.avails_unit, s.description))


def find_existing(conn, series_key: str, fingerprint: str) -> Optional[dict]:
    return conn.execute(
        "SELECT series_id, version, status FROM forecast_series_version "
        "WHERE series_key = %s AND import_fingerprint = %s AND status IN ('current', 'retired', 'staged') "
        "ORDER BY version DESC LIMIT 1", (series_key, fingerprint)).fetchone()


def current_total(conn, series_key: str) -> Optional[float]:
    row = conn.execute(
        "SELECT COALESCE(SUM(d.avails), 0) AS total FROM forecast_series_version v "
        "JOIN forecast_avail_dma d ON d.series_id = v.series_id WHERE v.series_key = %s AND v.status = 'current'",
        (series_key,)).fetchone()
    return float(row["total"]) if row and row["total"] else None


def _copy(conn, table: str, columns: tuple[str, ...], rows) -> int:
    n = 0
    with conn.cursor() as cur:
        with cur.copy(f"COPY {table} ({', '.join(columns)}) FROM STDIN") as copy:
            for row in rows:
                copy.write_row(row)
                n += 1
    return n


def _ensure_publishers(conn, names: list[str]) -> dict[str, int]:
    """display name -> publisher_id (one dimension row per normalized name, shared by every series)."""
    norms = {name: geo.publisher_key(name) for name in names}
    unique = sorted({(norm, name) for name, norm in norms.items()})
    if unique:
        conn.execute(
            "INSERT INTO forecast_publisher (name, name_norm) SELECT * FROM unnest(%s::text[], %s::text[]) AS t(name, name_norm) "
            "ON CONFLICT (name_norm) DO NOTHING", ([n for _, n in unique], [norm for norm, _ in unique]))
    rows = conn.execute("SELECT publisher_id, name_norm FROM forecast_publisher WHERE name_norm = ANY(%s)", (list(set(norms.values())),)).fetchall()
    by_norm = {r["name_norm"]: r["publisher_id"] for r in rows}
    return {name: by_norm[norm] for name, norm in norms.items()}


def stage(conn, payload: SeriesPayload, batch_id: Optional[int]) -> StageResult:
    """Insert one version with all its values. The caller owns the transaction."""
    s = payload.spec
    version = conn.execute("SELECT COALESCE(MAX(version), 0) + 1 AS v FROM forecast_series_version WHERE series_key = %s", (s.series_key,)).fetchone()["v"]
    period = s.period
    basis = s.data_basis
    if s.data_basis == "extrapolated" and not any(f.basis == "modeled" for f in payload.levels["dma"].values()):
        basis = "platform_reported"          # nothing was actually extended
    row = conn.execute(
        "INSERT INTO forecast_series_version (series_key, version, status, data_basis, period_label, period_start, period_end, "
        "band_low, band_high, exported_on, source_name, source_sha256, import_fingerprint, batch_id, row_counts, validation) "
        "VALUES (%s, %s, 'staged', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb) RETURNING series_id",
        (s.series_key, version, basis, period.label if period else None, period.start if period else None,
         period.end if period else None, s.band_low, s.band_high, s.exported_on, payload.source_name, payload.source_sha256,
         payload.fingerprint(), batch_id, json.dumps(payload.row_counts()), json.dumps(summarize(payload), default=str))).fetchone()
    sid = row["series_id"]

    _copy(conn, "forecast_avail_dma",
          ("series_id", "dma_key", "avails", "avails_low", "avails_high", "reach_universe", "est_reach", "unique_hh", "reach_pct", "basis"),
          ((sid, k, f.avails, f.avails_low, f.avails_high, f.reach_universe, f.est_reach, f.unique_hh, f.reach_pct, f.basis)
           for k, f in payload.levels["dma"].items()))
    _copy(conn, "forecast_avail_state", ("series_id", "state_code", "avails", "reach_universe", "est_reach"),
          ((sid, k, f.avails, f.reach_universe, f.est_reach) for k, f in payload.levels["state"].items()))
    _copy(conn, "forecast_avail_cd", ("series_id", "cd_key", "avails", "reach_universe", "est_reach"),
          ((sid, k, f.avails, f.reach_universe, f.est_reach) for k, f in payload.levels["cd"].items()))
    _copy(conn, "forecast_avail_zip", ("series_id", "zip", "avails", "reach_universe", "est_reach"),
          ((sid, k, f.avails, f.reach_universe, f.est_reach) for k, f in payload.levels["zip"].items()))
    _copy(conn, "forecast_avail_unknown", ("series_id", "level", "avails", "reach_universe", "est_reach"),
          ((sid, lvl, f.avails, f.reach_universe, f.est_reach) for lvl, f in payload.unknown.items()))
    _copy(conn, "forecast_avail_device", ("series_id", "device", "avails", "reach_universe", "est_reach"),
          ((sid, d, f.avails, f.reach_universe, f.est_reach) for d, f in payload.devices.items()))
    if payload.publishers:
        ids = _ensure_publishers(conn, list(payload.publishers))
        _copy(conn, "forecast_avail_publisher", ("series_id", "publisher_id", "avails", "reach_universe", "est_reach"),
              ((sid, ids[name], f.avails, f.reach_universe, f.est_reach) for name, f in payload.publishers.items()))
    return StageResult(sid, version, payload.row_counts())


def publish(conn, series_id: int, published_by: Optional[str]) -> None:
    """Make a staged (or retired) version the current one; the version it replaces is retired. One transaction."""
    row = conn.execute("SELECT series_key, status FROM forecast_series_version WHERE series_id = %s FOR UPDATE", (series_id,)).fetchone()
    if row is None:
        raise ValueError(f"no such series version {series_id}")
    if row["status"] == "failed":
        raise ValueError("a failed version cannot be published")
    conn.execute("UPDATE forecast_series_version SET status = 'retired', retired_at = now() "
                 "WHERE series_key = %s AND status = 'current' AND series_id <> %s", (row["series_key"], series_id))
    conn.execute("UPDATE forecast_series_version SET status = 'current', published_at = now(), published_by = %s, retired_at = NULL "
                 "WHERE series_id = %s", (published_by, series_id))


def rollback(conn, series_key: str, to_version: Optional[int] = None, by: Optional[str] = None) -> int:
    """Make the previous retired version (or `to_version`) current again. Returns the version now current."""
    current = conn.execute("SELECT version FROM forecast_series_version WHERE series_key = %s AND status = 'current'", (series_key,)).fetchone()
    if to_version is None:
        row = conn.execute(
            "SELECT series_id, version FROM forecast_series_version WHERE series_key = %s AND status = 'retired' "
            "AND (%s::int IS NULL OR version < %s) ORDER BY version DESC LIMIT 1",
            (series_key, current["version"] if current else None, current["version"] if current else None)).fetchone()
    else:
        row = conn.execute("SELECT series_id, version FROM forecast_series_version WHERE series_key = %s AND version = %s AND status IN ('retired', 'current')",
                           (series_key, to_version)).fetchone()
    if row is None:
        raise ValueError(f"no earlier version of {series_key!r} to roll back to")
    publish(conn, row["series_id"], by)
    return row["version"]


def prune(conn, keep: int = 2) -> int:
    """Delete the numbers of retired versions older than the newest `keep` retired ones per series (the version
    rows stay, marked as pruned in their validation JSON is not needed - their row_counts record what they had)."""
    rows = conn.execute(
        "SELECT series_id FROM (SELECT series_id, row_number() OVER (PARTITION BY series_key ORDER BY version DESC) AS rn "
        "FROM forecast_series_version WHERE status = 'retired') t WHERE rn > %s", (keep,)).fetchall()
    ids = [r["series_id"] for r in rows]
    if ids:
        conn.execute("DELETE FROM forecast_series_version WHERE series_id = ANY(%s)", (ids,))
    return len(ids)


def link_products(conn, payload: SeriesPayload) -> list[str]:
    """Point the catalog products named in the manifest at this series. Returns the names that are not in the catalog
    (a warning, not an error: the catalog is edited by people)."""
    return link_spec_products(conn, payload.spec)


def link_spec_products(conn, spec) -> list[str]:
    """The same from a manifest entry alone, so a series the startup import skips (it already has a version) still gets its
    products linked - on a fresh database the products table is filled only AFTER the first import, and nothing else would
    ever link them. Idempotent; these links are the importer's defaults (an admin's own mapping is a separate table)."""
    missing: list[str] = []
    for link in spec.products:
        row = conn.execute("SELECT product_key FROM products WHERE name = %s", (link.name,)).fetchone()
        if row is None:
            missing.append(link.name)
            continue
        conn.execute(
            "INSERT INTO forecast_series_product (product_key, series_key, relation, note) VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (product_key) DO UPDATE SET series_key = EXCLUDED.series_key, relation = EXCLUDED.relation, note = EXCLUDED.note",
            (row["product_key"], spec.series_key, link.relation, link.note or None))
    return missing


def start_batch(conn, requested_by: Optional[str], tool_version: str, sources: list[dict], dry_run: bool) -> int:
    return conn.execute(
        "INSERT INTO forecast_import_batch (requested_by, tool_version, status, sources) VALUES (%s, %s, %s, %s::jsonb) RETURNING batch_id",
        (requested_by, tool_version, "dry_run" if dry_run else "running", json.dumps(sources))).fetchone()["batch_id"]


def finish_batch(conn, batch_id: int, status: str, report: dict) -> None:
    conn.execute("UPDATE forecast_import_batch SET finished_at = now(), status = %s, report = %s::jsonb WHERE batch_id = %s",
                 (status, json.dumps(report, default=str), batch_id))
