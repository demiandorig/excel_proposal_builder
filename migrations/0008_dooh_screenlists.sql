-- 0008_dooh_screenlists.sql
--
-- Why: the wizard's DOOH line can take the platform's screen list (a .csv or .xlsx of thousands of rows x 40 columns). That
-- is far too big for the proposal's autosave JSON, so the parsed rows are stored once, here, under an id that is the SHA-256
-- of the rows (so a stored list never changes and a duplicated proposal can share it); the proposal keeps only the id, the
-- file name, the counts and the totals. /api/generate reads the rows back by id to build the export's DOOH tabs.
--
-- Safe on a database with data: it only creates a new table (no trigger, no data copied or rewritten), and re-running it is
-- a no-op. schema.sql carries the same statement for a new database and for startup (schema_bootstrap) and Publish's schema
-- diff, so whichever runs first, the other finds the table already there.
CREATE TABLE IF NOT EXISTS dooh_screenlists (
    id          TEXT PRIMARY KEY,
    filename    TEXT NOT NULL,
    screens     INTEGER NOT NULL,
    content     BYTEA NOT NULL,
    size_bytes  INTEGER NOT NULL,
    created_by  TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
