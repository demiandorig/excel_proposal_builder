-- 0002_audience_taxonomy.sql
--
-- Why: Constanza's interest targeting ("likes sports", "dental care seekers") multiplies an avails figure by an INCIDENCE:
-- the share of US adults who match the segment. Those rates used to be a two-entry JSON file. This is the standard home
-- for the full taxonomy: groups (searchable navigation), segments (the audiences the sales team uses, with the names
-- they use for them), and one sourced incidence record per segment - the rate, what it measures, where it came from,
-- the evidence, how it was verified and how much to trust it.
--
-- Rules the CHECK constraints enforce (docs/forecast_data_platform.md, "Audience incidence"):
--   * a rate exists exactly when the status says one does (sourced / derived); a segment with no defensible public number
--     is kept in the taxonomy as 'no_public_rate' with no rate, so it is searchable but never guessed;
--   * every rate is a share of US adults 18+, strictly between 0 and 1;
--   * a sourced or derived rate names its source.
-- The seed data (app/data/constanza/audience_taxonomy.json) is loaded by the import tooling, not by this migration. Rows
-- an admin later edits are marked origin = 'admin' and a re-seed leaves them alone.

CREATE TABLE audience_group (
    group_key   TEXT PRIMARY KEY CHECK (group_key ~ '^[a-z0-9_]+$'),
    label       TEXT NOT NULL,
    sort_order  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE audience_segment (
    segment_id     TEXT PRIMARY KEY CHECK (segment_id ~ '^[a-z0-9_]+$'),
    group_key      TEXT NOT NULL REFERENCES audience_group(group_key),
    parent_id      TEXT REFERENCES audience_segment(segment_id) ON DELETE SET NULL,
    label          TEXT NOT NULL,
    company_names  TEXT[] NOT NULL DEFAULT '{}',          -- how the sales team names it (searchable)
    keywords       TEXT[] NOT NULL DEFAULT '{}',          -- other words a planner might type
    description    TEXT NOT NULL DEFAULT '',
    sort_order     INTEGER NOT NULL DEFAULT 0,
    is_active      BOOLEAN NOT NULL DEFAULT TRUE,
    origin         TEXT NOT NULL DEFAULT 'seed' CHECK (origin IN ('seed', 'admin')),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX audience_segment_group_idx ON audience_segment (group_key, sort_order);

CREATE TABLE audience_incidence (
    segment_id          TEXT PRIMARY KEY REFERENCES audience_segment(segment_id) ON DELETE CASCADE,
    status              TEXT NOT NULL CHECK (status IN ('sourced', 'derived', 'no_public_rate')),
    rate_all            NUMERIC(8, 6),
    rate_male           NUMERIC(8, 6),
    rate_female         NUMERIC(8, 6),
    definition          TEXT NOT NULL DEFAULT '',
    denominator         TEXT NOT NULL DEFAULT '',
    time_window         TEXT NOT NULL DEFAULT '',
    source_title        TEXT,
    source_publisher    TEXT,
    source_url          TEXT,
    source_date         TEXT,
    evidence_quote      TEXT,
    derivation          TEXT,
    confidence          TEXT NOT NULL DEFAULT 'none' CHECK (confidence IN ('high', 'moderate', 'low', 'none')),
    caveats             TEXT NOT NULL DEFAULT '',
    supporting_sources  JSONB NOT NULL DEFAULT '[]',
    verification        JSONB NOT NULL DEFAULT '{}',
    origin              TEXT NOT NULL DEFAULT 'seed' CHECK (origin IN ('seed', 'admin')),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK ((status = 'no_public_rate') = (rate_all IS NULL)),
    CHECK (rate_all IS NULL OR (rate_all > 0 AND rate_all <= 1)),
    CHECK (rate_male IS NULL OR (rate_male > 0 AND rate_male <= 1)),
    CHECK (rate_female IS NULL OR (rate_female > 0 AND rate_female <= 1)),
    CHECK (status = 'no_public_rate' OR (source_url IS NOT NULL AND source_url <> '' AND source_title IS NOT NULL))
);

COMMENT ON TABLE audience_incidence IS 'One sourced incidence record per audience segment: the share of US adults 18+ who match it, and the evidence for that number.';
