"""
Creates, at startup, the tables that newer features depend on — so deploying the code is enough and nobody has to
paste SQL into the database console first.

The statements are NOT repeated here: they are lifted from `schema.sql` itself (the single source of truth), which
writes each of these as an idempotent `CREATE TABLE IF NOT EXISTS ... ;` (plus its `CREATE INDEX IF NOT EXISTS`
lines). Running them again changes nothing. Best effort and never fatal: a missing file, a database that can't be
reached, or a user without CREATE rights just leaves each feature on its own fail-safe path (the same behaviour as
before this existed) and is logged.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

from app.db import get_connection

log = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schema.sql"

# Tables added after the first database migration, each of which silently turns a feature off while it is missing.
ENSURED_TABLES = ("products", "product_aliases", "proposal_files", "faq_entries")


def statements_for(table: str, schema_text: str) -> list[str]:
    """The idempotent CREATE TABLE statement for `table` and its CREATE INDEX lines, as written in `schema_text`
    (empty when schema.sql has no `IF NOT EXISTS` definition for it)."""
    found: list[str] = []
    start = re.search(rf"^CREATE TABLE IF NOT EXISTS\s+{re.escape(table)}\s*\(", schema_text, re.MULTILINE | re.IGNORECASE)
    if start:
        end = schema_text.find("\n);", start.start())
        if end != -1:
            found.append(schema_text[start.start(): end + 3])
    for index in re.finditer(rf"^CREATE (?:UNIQUE )?INDEX IF NOT EXISTS\s+\S+\s+ON\s+{re.escape(table)}\b[^;]*;", schema_text,
                             re.MULTILINE | re.IGNORECASE):
        found.append(index.group(0))
    return found if found and found[0].upper().startswith("CREATE TABLE") else []


def ensure_tables(schema_path: Optional[Path] = None, tables: tuple = ENSURED_TABLES) -> list[str]:
    """Create any of `tables` that doesn't exist yet. Returns the tables it ran statements for. Never raises."""
    path = schema_path or SCHEMA_PATH
    try:
        schema_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        log.warning("schema bootstrap skipped, can't read %s: %s", path, exc)
        return []
    done: list[str] = []
    for table in tables:                      # one transaction per table: a failure on one never blocks the others
        statements = statements_for(table, schema_text)
        if not statements:
            log.warning("schema bootstrap: no idempotent definition of %s in %s", table, path.name)
            continue
        try:
            with get_connection() as conn:
                for statement in statements:
                    conn.execute(statement)
            done.append(table)
        except Exception as exc:  # noqa: BLE001
            log.warning("schema bootstrap of %s failed (the feature keeps its fail-safe path): %s", table, exc)
    return done
