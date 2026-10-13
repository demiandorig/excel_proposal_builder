"""
Orchestration: read every source in the manifest, validate, stage and publish. Used by the command line
(`python -m app.services.forecast_import ...`), by the startup bootstrap and by the tests.

    plan(...)         read + validate, no database needed, nothing changes
    run_import(...)   the real thing; idempotent (a series whose source and spec are unchanged is skipped)
    bootstrap(...)    what the app does at startup: load only the series that have never been imported
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from app.db import get_connection
from app.services.forecast_import import TOOL_VERSION, geo_reference as geo, loader
from app.services.forecast_import.manifest import MANIFEST_PATH, load_manifest
from app.services.forecast_import.model import Finding, SeriesPayload, SeriesSpec, SourceError
from app.services.forecast_import.readers import read_series
from app.services.forecast_import.validate import summarize, validate

log = logging.getLogger(__name__)

BOOTSTRAP_LOCK_KEY = 726_551_002


@dataclass
class SeriesOutcome:
    series_key: str
    status: str                       # published | staged | unchanged | invalid | error
    version: Optional[int] = None
    findings: list[Finding] = field(default_factory=list)
    row_counts: dict = field(default_factory=dict)
    note: str = ""

    @property
    def errors(self) -> int:
        return sum(1 for f in self.findings if f.severity == "error")

    @property
    def warnings(self) -> int:
        return sum(1 for f in self.findings if f.severity == "warning")


def _select(specs: list[SeriesSpec], only: Optional[Iterable[str]]) -> list[SeriesSpec]:
    if not only:
        return specs
    wanted = set(only)
    unknown = wanted - {s.series_key for s in specs}
    if unknown:
        raise ValueError(f"not in the manifest: {', '.join(sorted(unknown))}")
    return [s for s in specs if s.series_key in wanted]


def read_all(specs: list[SeriesSpec], source_dir: Path) -> dict[str, "SeriesPayload | SourceError"]:
    """Read each series once (a series another one extends from is read first and reused)."""
    by_key = {s.series_key: s for s in specs}
    memo: dict[str, SeriesPayload | SourceError] = {}

    def get(key: str):
        if key in memo:
            return memo[key]
        spec = by_key[key]
        reference = None
        ref_key = spec.params.get("extrapolate_from")
        if ref_key:
            ref = get(ref_key)
            if isinstance(ref, SourceError):
                memo[key] = SourceError(f"{key}: its reference series {ref_key!r} could not be read ({ref})")
                return memo[key]
            reference = {k: f.avails for k, f in ref.levels["dma"].items()}
        try:
            memo[key] = read_series(spec, source_dir, reference)
        except SourceError as exc:
            memo[key] = exc
        return memo[key]

    for s in specs:
        get(s.series_key)
    return memo


def plan(source_dir: Optional[Path] = None, only: Optional[Iterable[str]] = None, manifest_path: Optional[Path] = None) -> list[SeriesOutcome]:
    specs, default_dir = load_manifest(manifest_path)
    source_dir = source_dir or default_dir
    chosen = _select(specs, only)
    payloads = read_all(specs, source_dir)          # references of a chosen series are read even when not chosen
    out: list[SeriesOutcome] = []
    for spec in chosen:
        p = payloads[spec.series_key]
        if isinstance(p, SourceError):
            out.append(SeriesOutcome(spec.series_key, "error", note=str(p)))
            continue
        validate(p)
        out.append(SeriesOutcome(spec.series_key, "invalid" if p.errors else "ok", findings=list(p.findings), row_counts=p.row_counts()))
    return out


def run_import(
    source_dir: Optional[Path] = None, only: Optional[Iterable[str]] = None, publish: bool = True,
    requested_by: Optional[str] = None, accept_large_change: bool = False, manifest_path: Optional[Path] = None,
    skip_existing_series: bool = False,
) -> list[SeriesOutcome]:
    """Stage (and by default publish) every selected series. A series that fails validation is reported and skipped;
    the others still load. `skip_existing_series` loads only series that have no version at all (startup bootstrap)."""
    specs, default_dir = load_manifest(manifest_path)
    source_dir = source_dir or default_dir
    chosen = _select(specs, only)
    payloads = read_all(specs, source_dir)
    outcomes: list[SeriesOutcome] = []
    with get_connection() as conn:
        geo.seed_geo(conn)
        from app.services.forecast_import import product_seed
        added = product_seed.apply_to_db(conn)       # catalog products the series are linked to must exist first
        if added:
            log.info("added %d catalog product(s)", added)
        conn.commit()
        have = {r["series_key"] for r in conn.execute("SELECT DISTINCT series_key FROM forecast_series_version").fetchall()}
        sources = [{"series": s.series_key, "source": s.source, "sha256": getattr(payloads.get(s.series_key), "source_sha256", None)} for s in chosen]
        batch_id = loader.start_batch(conn, requested_by, TOOL_VERSION, sources, dry_run=False)
        conn.commit()
        for spec in chosen:
            if skip_existing_series and spec.series_key in have:
                continue
            payload = payloads[spec.series_key]
            if isinstance(payload, SourceError):
                outcomes.append(SeriesOutcome(spec.series_key, "error", note=str(payload)))
                continue
            outcomes.append(_import_one(conn, payload, batch_id, publish, requested_by, accept_large_change))
            conn.commit()
        try:
            for table in ("forecast_avail_dma", "forecast_avail_state", "forecast_avail_cd", "forecast_avail_zip",
                          "forecast_avail_device", "forecast_avail_publisher"):
                conn.execute(f"ANALYZE {table}")
        except Exception:  # noqa: BLE001
            log.debug("ANALYZE after import failed (non-fatal)", exc_info=True)
        failed = sum(1 for o in outcomes if o.status in ("invalid", "error"))
        loader.finish_batch(conn, batch_id, "failed" if failed and failed == len(outcomes) else "succeeded", {
            "series": [{"series_key": o.series_key, "status": o.status, "version": o.version, "errors": o.errors, "warnings": o.warnings,
                        "findings": [f.as_dict() for f in o.findings if f.severity != "info"][:50], "note": o.note} for o in outcomes]})
        conn.commit()
    return outcomes


def _import_one(conn, payload: SeriesPayload, batch_id: int, publish: bool, by: Optional[str], accept_large_change: bool) -> SeriesOutcome:
    key = payload.spec.series_key
    try:
        loader.upsert_series_def(conn, payload)
        existing = loader.find_existing(conn, key, payload.fingerprint())
        if existing is not None:
            note = f"v{existing['version']} already imported from this exact source and spec"
            if existing["status"] == "staged" and publish:
                loader.publish(conn, existing["series_id"], by)
                note += " (published now)"
                status = "published"
            else:
                status = "unchanged"
            missing = loader.link_products(conn, payload)
            findings = [Finding("M003", "warning", f"product {n!r} is not in the catalog, not linked", "products") for n in missing]
            return SeriesOutcome(key, status, existing["version"], findings, note=note)
        validate(payload, previous_total=loader.current_total(conn, key), accept_large_change=accept_large_change)
        if payload.errors:
            return SeriesOutcome(key, "invalid", findings=list(payload.findings), row_counts=payload.row_counts())
        with conn.transaction():
            res = loader.stage(conn, payload, batch_id)
            if publish:
                loader.publish(conn, res.series_id, by)
            missing = loader.link_products(conn, payload)
        for name in missing:
            payload.add("M003", "warning", f"product {name!r} is not in the catalog, so it was not linked to this series", "products")
        return SeriesOutcome(key, "published" if publish else "staged", res.version, list(payload.findings), res.row_counts)
    except Exception as exc:  # noqa: BLE001
        log.exception("import of %s failed", key)
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass
        return SeriesOutcome(key, "error", note=f"{type(exc).__name__}: {exc}", findings=list(payload.findings))


def status() -> list[dict]:
    with get_connection() as conn:
        return conn.execute(
            "SELECT d.series_key, d.title, d.channel, d.avails_unit, v.version, v.status, v.data_basis, v.published_at, v.exported_on, "
            "v.period_label, v.row_counts, (v.validation->'counts') AS counts "
            "FROM forecast_series_def d LEFT JOIN forecast_series_version v ON v.series_key = d.series_key AND v.status = 'current' "
            "ORDER BY d.channel, d.series_key").fetchall()


def rollback_series(series_key: str, to_version: Optional[int] = None, by: Optional[str] = None) -> int:
    with get_connection() as conn:
        return loader.rollback(conn, series_key, to_version, by)


def prune_versions(keep: int = 2) -> int:
    with get_connection() as conn:
        return loader.prune(conn, keep)


def bootstrap() -> dict:
    """The startup path. Never raises. Loads what has never been loaded (a fresh database, or a series added to the
    manifest by a newer release); it does NOT touch a series that already has a version - refreshing those is a
    deliberate `import` run, reviewed like any other change."""
    result: dict = {"migrations": [], "geo": None, "products": 0, "series": []}
    try:
        from app.services import migrations
        result["migrations"] = migrations.apply_pending()
        # One instance loads at a time: the lock is held on this connection for the whole bootstrap.
        with get_connection() as lock_conn:
            got = lock_conn.execute("SELECT pg_try_advisory_lock(%s) AS ok", (BOOTSTRAP_LOCK_KEY,)).fetchone()["ok"]
            if not got:
                result["skipped"] = "another instance is loading the forecast data"
                return result
            try:
                from app.services.forecast_import import product_seed
                result["products"] = product_seed.apply_to_db(lock_conn)
                lock_conn.commit()
                outcomes = run_import(requested_by="startup", skip_existing_series=True)
                result["series"] = [(o.series_key, o.status) for o in outcomes]
            finally:
                lock_conn.execute("SELECT pg_advisory_unlock(%s)", (BOOTSTRAP_LOCK_KEY,))
                lock_conn.commit()
    except Exception as exc:  # noqa: BLE001
        log.warning("forecast data bootstrap skipped: %s: %s", type(exc).__name__, exc)
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result
