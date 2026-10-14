"""
Reviewable, versioned schema migrations.

`schema.sql` stays the baseline (the tables the app was first built on, kept idempotent by `schema_bootstrap`). Everything
added after it lives in `migrations/NNNN_name.sql`: one plain SQL file per change, applied in order, each in its own
transaction, recorded in `schema_migrations` with a checksum. A change to the database is therefore a file in a pull
request that someone reads, not a statement pasted into a console.

    python -m app.services.migrations status      # what is applied / pending / drifted
    python -m app.services.migrations apply       # apply every pending migration (also runs at app startup)
    python -m app.services.migrations apply --dry-run

Rules (also in docs/forecast_data_platform.md):
  * Files are named `NNNN_snake_case.sql` (four digits, no gaps expected, never renumbered).
  * An applied migration is NEVER edited. To change something, add the next file. The ledger stores each file's
    SHA-256; a file edited after it was applied is reported as `drifted` and is not re-run.
  * Migrations must be safe on a database that already has data and must not need CONCURRENTLY (they run in a
    transaction); the tables they create are new, so plain CREATE INDEX is fine.
  * Data that belongs to the application (geography reference rows, the product seed, the audience taxonomy, avails
    imports) is NOT a migration: it is loaded by the import tooling, which is idempotent and validated.

Concurrency: several app instances can start at once (Autoscale). A session-level advisory lock serialises the runner;
the instance that loses the race simply waits, then finds nothing pending.
"""
from __future__ import annotations

import hashlib
import logging
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from app.db import get_connection, release_advisory_lock

log = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"
LOCK_KEY = 726_551_001            # arbitrary, app-wide constant for pg_advisory_lock
_FILE_RE = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")

LEDGER = "schema_migrations"
_LEDGER_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def _ledger_ddl(table: str) -> str:
    return (f"CREATE TABLE IF NOT EXISTS {table} (version TEXT PRIMARY KEY, name TEXT NOT NULL, checksum TEXT NOT NULL, "
            "applied_at TIMESTAMPTZ NOT NULL DEFAULT now(), duration_ms INTEGER)")


def _check_ledger(table: str) -> str:
    if not _LEDGER_NAME.match(table):
        raise ValueError(f"invalid ledger table name {table!r}")
    return table


@dataclass(frozen=True)
class Migration:
    version: str
    name: str
    path: Path
    sql: str
    checksum: str


@dataclass
class MigrationState:
    version: str
    name: str
    state: str                       # "applied" | "pending" | "drifted"
    applied_at: Optional[str] = None


def discover(directory: Optional[Path] = None) -> list[Migration]:
    """Every migration file, in version order. A badly named .sql file or a duplicated version is an error."""
    directory = directory or MIGRATIONS_DIR
    found: list[Migration] = []
    seen: dict[str, Path] = {}
    if not directory.is_dir():
        return found
    for path in sorted(directory.glob("*.sql")):
        match = _FILE_RE.match(path.name)
        if not match:
            raise ValueError(f"migration file {path.name!r} must be named NNNN_snake_case.sql")
        version, name = match.groups()
        if version in seen:
            raise ValueError(f"migration version {version} is used by both {seen[version].name} and {path.name}")
        seen[version] = path
        sql = path.read_text(encoding="utf-8")
        found.append(Migration(version, name, path, sql, hashlib.sha256(sql.encode("utf-8")).hexdigest()))
    return found


def _read_ledger(conn, table: str = LEDGER) -> dict[str, dict]:
    exists = conn.execute("SELECT to_regclass(%s) AS t", (table,)).fetchone()["t"]
    if not exists:
        return {}
    return {r["version"]: r for r in conn.execute(f"SELECT version, name, checksum, applied_at FROM {table}")}


def status(directory: Optional[Path] = None, ledger_table: str = LEDGER) -> list[MigrationState]:
    migrations = discover(directory)
    with get_connection() as conn:
        ledger = _read_ledger(conn, _check_ledger(ledger_table))
    out: list[MigrationState] = []
    for m in migrations:
        row = ledger.get(m.version)
        if row is None:
            out.append(MigrationState(m.version, m.name, "pending"))
        elif row["checksum"] != m.checksum:
            out.append(MigrationState(m.version, m.name, "drifted", str(row["applied_at"])))
        else:
            out.append(MigrationState(m.version, m.name, "applied", str(row["applied_at"])))
    return out


def apply_pending(directory: Optional[Path] = None, dry_run: bool = False, ledger_table: str = LEDGER) -> list[str]:
    """Apply every pending migration in order and return the versions applied (or that would be, for a dry run).
    Stops at the first failure and raises: later migrations may depend on it."""
    migrations = discover(directory)
    if not migrations:
        return []
    ledger_table = _check_ledger(ledger_table)
    applied_now: list[str] = []
    with get_connection() as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (LOCK_KEY,))
        try:
            conn.execute(_ledger_ddl(ledger_table))
            conn.commit()
            ledger = _read_ledger(conn, ledger_table)
            for m in migrations:
                row = ledger.get(m.version)
                if row is not None:
                    if row["checksum"] != m.checksum:
                        log.warning("migration %s_%s was edited after it was applied (checksum drift) - not re-run; "
                                    "add a new migration instead", m.version, m.name)
                    continue
                if dry_run:
                    applied_now.append(m.version)
                    continue
                started = time.monotonic()
                try:
                    with conn.transaction():
                        conn.execute(m.sql)
                        conn.execute(
                            f"INSERT INTO {ledger_table} (version, name, checksum, duration_ms) VALUES (%s, %s, %s, %s)",
                            (m.version, m.name, m.checksum, int((time.monotonic() - started) * 1000)),
                        )
                except Exception:
                    log.exception("migration %s_%s failed and was rolled back", m.version, m.name)
                    raise
                conn.commit()                    # durable on its own: a later failure never undoes an earlier one
                applied_now.append(m.version)
                log.info("applied migration %s_%s in %d ms", m.version, m.name, int((time.monotonic() - started) * 1000))
        finally:
            release_advisory_lock(conn, LOCK_KEY)
    return applied_now


def apply_pending_safely() -> list[str]:
    """Startup entry point: never raises. A migration that cannot run leaves the app on its fail-safe paths."""
    try:
        return apply_pending()
    except Exception as exc:  # noqa: BLE001
        log.warning("schema migrations skipped: %s: %s", type(exc).__name__, exc)
        return []


def new_migration_path(name: str, directory: Optional[Path] = None) -> Path:
    directory = directory or MIGRATIONS_DIR
    if not re.fullmatch(r"[a-z0-9_]+", name):
        raise ValueError("a migration name is lower-case letters, digits and underscores")
    existing = [int(m.version) for m in discover(directory)]
    return directory / f"{(max(existing) + 1 if existing else 1):04d}_{name}.sql"


def _main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    command = argv[0] if argv else "status"
    if command == "status":
        rows = status()
        if not rows:
            print("no migration files")
        for r in rows:
            print(f"{r.version}  {r.state:8s}  {r.name}" + (f"   (applied {r.applied_at})" if r.applied_at else ""))
        return 1 if any(r.state == "drifted" for r in rows) else 0
    if command == "apply":
        dry = "--dry-run" in argv
        versions = apply_pending(dry_run=dry)
        print(("would apply: " if dry else "applied: ") + (", ".join(versions) if versions else "nothing (up to date)"))
        return 0
    if command == "new" and len(argv) > 1:
        path = new_migration_path(argv[1])
        path.write_text(f"-- {path.name}\n-- Why: \n\n", encoding="utf-8")
        print(path)
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
