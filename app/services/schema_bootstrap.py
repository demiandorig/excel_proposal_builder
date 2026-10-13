"""
Brings the database up to date at startup — so deploying the code is enough and nobody has to paste SQL into the
database console first (a table added by a newer feature simply being absent used to surface as errors like
"relation \"disclaimers\" does not exist").

The statements are NOT repeated here: they are lifted from `schema.sql` itself (the single source of truth) — every
statement in it that is safe to run again and again:

    CREATE TABLE IF NOT EXISTS ...      CREATE [UNIQUE] INDEX IF NOT EXISTS ...      ALTER TABLE t ADD COLUMN IF NOT EXISTS ...

Statements that aren't idempotent (the original bare `CREATE TABLE`s, seed `INSERT`s, one-time `DO` blocks) are
skipped: those tables were created when the database was first set up. Each statement runs in its own savepoint with a
short lock timeout, so one that can't run (a table that doesn't exist, a user without CREATE rights, a table busy for
more than a few seconds) is logged and skipped without blocking the others or the app. If nothing here works, every
feature keeps its own fail-safe path — exactly the behaviour before this existed — and applying schema.sql by hand
still works.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

from app.db import get_connection

log = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schema.sql"
LOCK_TIMEOUT = "3s"          # an ALTER waits at most this long for a busy table instead of stalling startup

_CREATE_TABLE = re.compile(r"^CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+([\w.\"]+)", re.IGNORECASE)
_CREATE_INDEX = re.compile(r"^CREATE\s+(?:UNIQUE\s+)?INDEX\s+IF\s+NOT\s+EXISTS\s+([\w.\"]+)\s+ON\s+([\w.\"]+)", re.IGNORECASE)
_ADD_COLUMN = re.compile(r"^ALTER\s+TABLE\s+([\w.\"]+)\s+ADD\s+COLUMN\s+IF\s+NOT\s+EXISTS\s+([\w\"]+)", re.IGNORECASE)


def split_statements(sql: str) -> list[str]:
    """The statements of a SQL script, with comments removed: split on a `;` that is not inside a quoted string, a
    $$ ... $$ block or a comment."""
    out: list[str] = []
    buf: list[str] = []
    i, n = 0, len(sql)
    dollar: Optional[str] = None                  # the tag of an open $tag$ ... $tag$ block
    while i < n:
        ch = sql[i]
        if dollar is not None:
            if sql.startswith(dollar, i):
                buf.append(dollar)
                i += len(dollar)
                dollar = None
            else:
                buf.append(ch)
                i += 1
            continue
        if ch == "-" and sql.startswith("--", i):
            while i < n and sql[i] != "\n":       # a line comment (the newline itself stays as whitespace)
                i += 1
            continue
        if ch == "'":                              # a string literal; '' is an escaped quote
            j = i + 1
            while j < n:
                if sql[j] == "'":
                    if j + 1 < n and sql[j + 1] == "'":
                        j += 2
                        continue
                    break
                j += 1
            buf.append(sql[i:j + 1])
            i = j + 1
            continue
        if ch == "$":
            m = re.match(r"\$[A-Za-z_]*\$", sql[i:])
            if m:
                dollar = m.group(0)
                buf.append(dollar)
                i += len(dollar)
                continue
        if ch == ";":
            statement = "".join(buf).strip()
            if statement:
                out.append(statement)
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return out


def idempotent_statements(schema_text: str) -> list[dict]:
    """[{kind, target, sql}] — the statements of `schema_text` that are safe to run any number of times, in file
    order. kind: "table" | "index" | "column"; target: the table (or index / table.column) it concerns."""
    found: list[dict] = []
    for statement in split_statements(schema_text):
        flat = re.sub(r"\s+", " ", statement)
        m = _CREATE_TABLE.match(flat)
        if m:
            found.append({"kind": "table", "target": m.group(1).strip('"'), "sql": statement})
            continue
        m = _CREATE_INDEX.match(flat)
        if m:
            found.append({"kind": "index", "target": m.group(1).strip('"'), "sql": statement})
            continue
        m = _ADD_COLUMN.match(flat)
        if m:
            found.append({"kind": "column", "target": f"{m.group(1).strip(chr(34))}.{m.group(2).strip(chr(34))}", "sql": statement})
    return found


def ensure_tables(schema_path: Optional[Path] = None) -> list[str]:
    """Run every idempotent statement of schema.sql. Returns the targets whose statement ran without error. Never
    raises."""
    path = schema_path or SCHEMA_PATH
    try:
        statements = idempotent_statements(path.read_text(encoding="utf-8"))
    except OSError as exc:
        log.warning("schema bootstrap skipped, can't read %s: %s", path, exc)
        return []
    ran: list[str] = []
    failed: list[str] = []
    try:
        with get_connection() as conn:
            for item in statements:
                try:
                    with conn.transaction():          # a savepoint: a failed statement leaves the rest untouched
                        conn.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
                        conn.execute(item["sql"])
                    ran.append(item["target"])
                except Exception as exc:  # noqa: BLE001
                    failed.append(f"{item['target']} ({type(exc).__name__})")
                    log.warning("schema bootstrap: %s %s skipped: %s", item["kind"], item["target"], exc)
    except Exception as exc:  # noqa: BLE001 - no database to talk to
        log.warning("schema bootstrap failed (features keep their fail-safe paths): %s", exc)
        return ran
    if failed:
        log.warning("schema bootstrap: %d statement(s) could not run: %s", len(failed), ", ".join(failed))
    return ran
