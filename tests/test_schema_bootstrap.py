"""
Startup schema bootstrap: every statement of schema.sql that is safe to run again (CREATE TABLE/INDEX IF NOT EXISTS,
ALTER TABLE ... ADD COLUMN IF NOT EXISTS) is applied at startup, each in its own savepoint, so a table a newer feature
needs (the admin "disclaimers" page failing with 'relation "disclaimers" does not exist') is simply created.
"""
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "postgresql://fake:fake@localhost/fake")

import pytest

from app.services import schema_bootstrap as sb

SCHEMA_TEXT = (Path(__file__).resolve().parent.parent / "schema.sql").read_text(encoding="utf-8")


# --- splitting a script ---------------------------------------------------------------------------------------------------

def test_the_splitter_respects_quotes_comments_and_dollar_blocks():
    sql = """
    -- a comment; with a semicolon
    CREATE TABLE IF NOT EXISTS a (x TEXT DEFAULT 'a;b''c');   -- trailing; comment
    DO $$ BEGIN PERFORM 1; PERFORM 2; END $$;
    INSERT INTO a VALUES (E'it''s; fine');
    CREATE INDEX IF NOT EXISTS a_idx ON a (x)
    """
    parts = sb.split_statements(sql)
    assert len(parts) == 4
    assert parts[0].startswith("CREATE TABLE IF NOT EXISTS a") and "'a;b''c'" in parts[0] and "comment" not in parts[0]
    assert parts[1].startswith("DO $$") and parts[1].count("PERFORM") == 2
    assert parts[2] == "INSERT INTO a VALUES (E'it''s; fine')"
    assert parts[3].startswith("CREATE INDEX IF NOT EXISTS a_idx")


def test_only_statements_that_are_safe_to_repeat_are_picked():
    picked = sb.idempotent_statements("""
        CREATE TABLE legacy (id INT);                                   -- bare: not idempotent
        CREATE TABLE IF NOT EXISTS newer (id INT);
        CREATE INDEX plain_idx ON newer (id);                           -- not idempotent
        CREATE UNIQUE INDEX IF NOT EXISTS newer_uq ON newer (id);
        ALTER TABLE newer ADD COLUMN IF NOT EXISTS extra TEXT;
        ALTER TABLE newer ADD COLUMN plain TEXT;                        -- not idempotent
        INSERT INTO newer VALUES (1);
        DO $$ BEGIN NULL; END $$;
        DROP TABLE IF EXISTS something;
    """)
    assert [(p["kind"], p["target"]) for p in picked] == [("table", "newer"), ("index", "newer_uq"), ("column", "newer.extra")]


@pytest.mark.parametrize("table", ["disclaimers", "restriction_categories", "restriction_sync", "products", "product_aliases",
                                   "proposal_files", "faq_entries", "users", "sessions"])
def test_every_newer_table_in_schema_sql_is_picked_up(table):
    targets = {p["target"] for p in sb.idempotent_statements(SCHEMA_TEXT) if p["kind"] == "table"}
    assert table in targets


def test_the_real_schema_yields_no_non_idempotent_statements():
    for item in sb.idempotent_statements(SCHEMA_TEXT):
        assert "IF NOT EXISTS" in " ".join(item["sql"].split()).upper(), item


def test_the_restriction_columns_for_hand_added_categories_are_in_both_the_table_and_an_alter():
    assert "restriction_categories.is_manual" in {p["target"] for p in sb.idempotent_statements(SCHEMA_TEXT)}
    assert "restriction_categories.extra_notes" in {p["target"] for p in sb.idempotent_statements(SCHEMA_TEXT)}
    create = next(p["sql"] for p in sb.idempotent_statements(SCHEMA_TEXT) if p["target"] == "restriction_categories")
    assert "is_manual" in create and "extra_notes" in create


# --- running them ---------------------------------------------------------------------------------------------------------------

class _Conn:
    def __init__(self, fail_on=()):
        self.fail_on = fail_on
        self.ran, self.sets, self.savepoints = [], 0, 0

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def transaction(self):
        conn = self

        class _Savepoint:
            def __enter__(self_inner):
                conn.savepoints += 1
                return self_inner

            def __exit__(self_inner, *a):
                return False                     # an exception inside is NOT swallowed here: ensure_tables catches it
        return _Savepoint()

    def execute(self, sql):
        if sql.startswith("SET LOCAL lock_timeout"):
            self.sets += 1
            return
        if any(token in sql for token in self.fail_on):
            raise PermissionError("permission denied")
        self.ran.append(sql)


def test_every_statement_runs_in_its_own_savepoint_with_a_lock_timeout(monkeypatch):
    conn = _Conn()
    monkeypatch.setattr(sb, "get_connection", lambda: conn)
    ran = sb.ensure_tables()
    expected = [p["target"] for p in sb.idempotent_statements(SCHEMA_TEXT)]
    assert ran == expected and len(conn.ran) == len(expected)
    assert conn.savepoints == len(expected) and conn.sets == len(expected)
    assert "disclaimers" in ran and "faq_entries" in ran and "restriction_categories.is_manual" in ran


def test_one_failing_statement_never_blocks_the_others(monkeypatch):
    conn = _Conn(fail_on=("proposal_files",))
    monkeypatch.setattr(sb, "get_connection", lambda: conn)
    ran = sb.ensure_tables()
    assert "proposal_files" not in ran and "disclaimers" in ran and "faq_entries" in ran


def test_a_missing_schema_file_or_a_down_database_is_silent(monkeypatch, tmp_path):
    assert sb.ensure_tables(schema_path=tmp_path / "nope.sql") == []

    def down():
        raise ConnectionError("no database")
    monkeypatch.setattr(sb, "get_connection", down)
    assert sb.ensure_tables() == []
