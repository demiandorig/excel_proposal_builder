from pathlib import Path


SCHEMA_SQL = Path(__file__).resolve().parents[1] / "schema.sql"


def test_proposals_schema_retains_created_by_email():
    schema = SCHEMA_SQL.read_text(encoding="utf-8")
    proposals_start = schema.index("CREATE TABLE proposals")
    proposals_end = schema.index("CREATE INDEX idx_proposals_generated_at", proposals_start)
    proposals_definition = schema[proposals_start:proposals_end]

    # Keep this in the CREATE TABLE definition so a fresh development database
    # and Replit's publish-time schema diff both see the column.
    assert "created_by_email" in proposals_definition
    assert "created_by_email                                   TEXT" in proposals_definition

    # Keep the additive migration too: existing databases must gain the column
    # without dropping or rewriting any proposal rows.
    assert "ALTER TABLE proposals ADD COLUMN IF NOT EXISTS created_by_email TEXT;" in schema