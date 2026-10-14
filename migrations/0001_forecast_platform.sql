-- 0001_forecast_platform.sql
--
-- Why: avails/forecast data used to be bundled JSON files and a hard-coded product->platform table, each export
-- hand-converted into its own shape. This migration creates ONE standard home for it in Postgres:
--
--   geo_*                      canonical geography (DMA, state, congressional district) + the alias table that maps
--                              every spelling a source file uses onto one canonical DMA
--   products.product_key       a stable, never-renamed key for each catalog product (the display name can change)
--   forecast_series_def        one row per forecastable series (e.g. 'connect-default', 'live-sports-mlb')
--   forecast_series_version    one row per IMPORT of a series: staged -> current -> retired. Exactly one 'current'
--                              version per series is served; a bad import is rolled back by re-publishing the previous one
--   forecast_avail_*           the numbers: one narrow table per geography level, keyed (series_id, geo key)
--   forecast_series_product    which catalog product is forecast from which series
--   forecast_import_batch      what was imported, from which files (sha256), and the validation report
--
-- Index policy: the primary key of every value table is the ONLY access path the forecast API uses
-- (series_id = X AND key = ANY(...)), so those are the only indexes on them. Further indexes are added by a later
-- migration only when EXPLAIN (ANALYZE, BUFFERS) on realistic volumes shows they are needed (docs/forecast_data_platform.md).

-- ---------------------------------------------------------------------------------------------------------------
-- Geography reference
-- ---------------------------------------------------------------------------------------------------------------
CREATE TABLE geo_state (
    state_code  CHAR(2)  PRIMARY KEY,
    name        TEXT     NOT NULL UNIQUE,
    fips        CHAR(2)  NOT NULL UNIQUE
);

CREATE TABLE geo_dma (
    dma_key        TEXT              PRIMARY KEY,                 -- slug of the canonical name, e.g. 'new-york'
    name           TEXT              NOT NULL UNIQUE,             -- canonical (Nielsen-style) name used across the app
    rank           SMALLINT          NOT NULL UNIQUE,
    home_state     CHAR(2)           NOT NULL REFERENCES geo_state(state_code),
    tv_households  DOUBLE PRECISION,
    population     DOUBLE PRECISION
);

-- Every label a source file uses for a DMA ("Tampa - Saint Petersburg (Sarasota)", "Sacramnto-Stkton-Modesto") maps
-- here to exactly one canonical DMA. An import with a label that is not in this table fails, listing suggestions;
-- the fix is a reviewed row (app/data/forecast/dma_aliases.csv), never a guess.
CREATE TABLE geo_dma_alias (
    alias_norm  TEXT  PRIMARY KEY,                                -- normalized label (see forecast_import.normalize)
    alias       TEXT  NOT NULL,                                   -- the label as it appears in the source
    dma_key     TEXT  NOT NULL REFERENCES geo_dma(dma_key) ON DELETE CASCADE,
    source      TEXT  NOT NULL DEFAULT 'curated'
);
CREATE INDEX geo_dma_alias_dma_idx ON geo_dma_alias (dma_key);

CREATE TABLE geo_congressional_district (
    cd_key      TEXT     PRIMARY KEY,                             -- 'GA-05'; at-large districts are 'AK-AL' (DC's delegate: 'DC-AL')
    state_code  CHAR(2)  NOT NULL REFERENCES geo_state(state_code),
    district    TEXT     NOT NULL,
    at_large    BOOLEAN  NOT NULL DEFAULT FALSE,
    UNIQUE (state_code, district)
);

-- ---------------------------------------------------------------------------------------------------------------
-- A stable key for every catalog product
-- ---------------------------------------------------------------------------------------------------------------
-- products.name is what planners see and edit; forecast data must not break when it is renamed. product_key is
-- assigned once (from the name, de-duplicated) and never changes.
CREATE OR REPLACE FUNCTION product_key_slug(name TEXT) RETURNS TEXT
LANGUAGE sql IMMUTABLE AS $$
    SELECT left(trim(both '-' from lower(regexp_replace(name, '[^a-zA-Z0-9]+', '-', 'g'))), 80)
$$;

ALTER TABLE products ADD COLUMN IF NOT EXISTS product_key TEXT;

DO $$
DECLARE
    r RECORD; base TEXT; cand TEXT; n INTEGER;
BEGIN
    FOR r IN SELECT id, name FROM products WHERE product_key IS NULL ORDER BY id LOOP
        base := product_key_slug(r.name);
        IF base = '' THEN base := 'product'; END IF;
        cand := base; n := 1;
        WHILE EXISTS (SELECT 1 FROM products WHERE product_key = cand) LOOP
            n := n + 1; cand := base || '-' || n;
        END LOOP;
        UPDATE products SET product_key = cand WHERE id = r.id;
    END LOOP;
END $$;

CREATE OR REPLACE FUNCTION products_assign_key() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    base TEXT; cand TEXT; n INTEGER := 1;
BEGIN
    IF NEW.product_key IS NULL OR NEW.product_key = '' THEN
        base := product_key_slug(NEW.name);
        IF base = '' THEN base := 'product'; END IF;
        cand := base;
        WHILE EXISTS (SELECT 1 FROM products WHERE product_key = cand) LOOP
            n := n + 1; cand := base || '-' || n;
        END LOOP;
        NEW.product_key := cand;
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER products_assign_key_trg BEFORE INSERT ON products
    FOR EACH ROW EXECUTE FUNCTION products_assign_key();

ALTER TABLE products ALTER COLUMN product_key SET NOT NULL;
CREATE UNIQUE INDEX products_product_key_uq ON products (product_key);

-- ---------------------------------------------------------------------------------------------------------------
-- Import ledger
-- ---------------------------------------------------------------------------------------------------------------
CREATE TABLE forecast_import_batch (
    batch_id      INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    started_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    finished_at   TIMESTAMPTZ,
    requested_by  TEXT,
    tool_version  TEXT         NOT NULL,
    status        TEXT         NOT NULL CHECK (status IN ('running', 'succeeded', 'failed', 'dry_run')),
    sources       JSONB        NOT NULL DEFAULT '[]',     -- [{name, sha256, bytes}]
    report        JSONB        NOT NULL DEFAULT '{}'      -- validation findings per series
);

-- ---------------------------------------------------------------------------------------------------------------
-- Series: a stable definition, and an immutable version per import
-- ---------------------------------------------------------------------------------------------------------------
CREATE TABLE forecast_series_def (
    series_key     TEXT  PRIMARY KEY CHECK (series_key ~ '^[a-z0-9][a-z0-9_-]*$'),
    title          TEXT  NOT NULL,
    channel        TEXT  NOT NULL CHECK (channel IN ('ctv', 'olv', 'display', 'pause_ads', 'live_sports', 'netflix', 'platform_model')),
    targeting_key  TEXT  NOT NULL DEFAULT 'general',      -- targeting baked into the numbers: general | hispanic | spanish_language | kids_family | premium_subscription | a18_plus ...
    avails_unit    TEXT  NOT NULL CHECK (avails_unit IN ('monthly', 'weekly')),
    description    TEXT  NOT NULL DEFAULT '',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE forecast_series_version (
    series_id     INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    series_key    TEXT     NOT NULL REFERENCES forecast_series_def(series_key) ON DELETE CASCADE,
    version       INTEGER  NOT NULL,
    status        TEXT     NOT NULL DEFAULT 'staged' CHECK (status IN ('staged', 'current', 'retired', 'failed')),
    data_basis    TEXT     NOT NULL CHECK (data_basis IN ('platform_reported', 'modeled', 'extrapolated', 'mixed')),
    period_label  TEXT,                                   -- live sports: "Oct 2025 - Apr 2026, 6 months" (shown to the team)
    period_start  DATE,
    period_end    DATE,
    band_low      DOUBLE PRECISION NOT NULL DEFAULT 0.7,  -- planning range around the figure: x band_low .. x band_high
    band_high     DOUBLE PRECISION NOT NULL DEFAULT 1.3,
    exported_on   DATE,                                   -- when the source system produced the file
    source_name   TEXT,
    source_sha256 TEXT,                                   -- sha256 of the exported file itself
    import_fingerprint TEXT,                              -- sha256(file + the series spec): the same file under a changed spec is a NEW import
    batch_id      INTEGER REFERENCES forecast_import_batch(batch_id),
    row_counts    JSONB    NOT NULL DEFAULT '{}',
    validation    JSONB    NOT NULL DEFAULT '{}',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    published_at  TIMESTAMPTZ,
    published_by  TEXT,
    retired_at    TIMESTAMPTZ,
    UNIQUE (series_key, version),
    CHECK (band_low > 0 AND band_high >= band_low)
);
-- "the published version": at most one 'current' version per series, and the lookup the forecast API makes first.
CREATE UNIQUE INDEX forecast_series_version_current_uq ON forecast_series_version (series_key) WHERE status = 'current';
-- "have we already imported exactly this?" (idempotent re-runs of the import skip it)
CREATE INDEX forecast_series_version_fingerprint_idx ON forecast_series_version (series_key, import_fingerprint) WHERE import_fingerprint IS NOT NULL;

-- ---------------------------------------------------------------------------------------------------------------
-- The numbers. avails are in the series' unit (monthly or weekly); reach columns are as reported by the source.
-- ---------------------------------------------------------------------------------------------------------------
CREATE TABLE forecast_avail_dma (
    series_id       INTEGER NOT NULL REFERENCES forecast_series_version(series_id) ON DELETE CASCADE,
    dma_key         TEXT    NOT NULL REFERENCES geo_dma(dma_key),
    avails          DOUBLE PRECISION NOT NULL CHECK (avails >= 0),
    avails_low      DOUBLE PRECISION,
    avails_high     DOUBLE PRECISION,
    reach_universe  DOUBLE PRECISION,
    est_reach       DOUBLE PRECISION,
    unique_hh       DOUBLE PRECISION,
    reach_pct       DOUBLE PRECISION,
    basis           TEXT    NOT NULL DEFAULT 'reported' CHECK (basis IN ('reported', 'modeled')),
    PRIMARY KEY (series_id, dma_key)
);

CREATE TABLE forecast_avail_state (
    series_id       INTEGER NOT NULL REFERENCES forecast_series_version(series_id) ON DELETE CASCADE,
    state_code      CHAR(2) NOT NULL REFERENCES geo_state(state_code),
    avails          DOUBLE PRECISION NOT NULL CHECK (avails >= 0),
    reach_universe  DOUBLE PRECISION,
    est_reach       DOUBLE PRECISION,
    PRIMARY KEY (series_id, state_code)
);

CREATE TABLE forecast_avail_cd (
    series_id       INTEGER NOT NULL REFERENCES forecast_series_version(series_id) ON DELETE CASCADE,
    cd_key          TEXT    NOT NULL REFERENCES geo_congressional_district(cd_key),
    avails          DOUBLE PRECISION NOT NULL CHECK (avails >= 0),
    reach_universe  DOUBLE PRECISION,
    est_reach       DOUBLE PRECISION,
    PRIMARY KEY (series_id, cd_key)
);

-- ZIPs are the large table (about 25,000 rows per series). The primary key (series_id, zip) serves "this series, these
-- ZIPs". A ZIP absent from a series has no avails at or above the export's floor (about 180 per month).
CREATE TABLE forecast_avail_zip (
    series_id       INTEGER NOT NULL REFERENCES forecast_series_version(series_id) ON DELETE CASCADE,
    zip             TEXT    NOT NULL CHECK (zip ~ '^[0-9]{5}$'),
    avails          DOUBLE PRECISION NOT NULL CHECK (avails >= 0),
    reach_universe  DOUBLE PRECISION,
    est_reach       DOUBLE PRECISION,
    PRIMARY KEY (series_id, zip)
);

-- Avails the source could not place geographically (the "Unknown" row of each breakdown): kept so totals reconcile.
CREATE TABLE forecast_avail_unknown (
    series_id       INTEGER NOT NULL REFERENCES forecast_series_version(series_id) ON DELETE CASCADE,
    level           TEXT    NOT NULL CHECK (level IN ('dma', 'state', 'cd', 'zip')),
    avails          DOUBLE PRECISION NOT NULL CHECK (avails >= 0),
    reach_universe  DOUBLE PRECISION,
    est_reach       DOUBLE PRECISION,
    PRIMARY KEY (series_id, level)
);

CREATE TABLE forecast_avail_device (
    series_id       INTEGER NOT NULL REFERENCES forecast_series_version(series_id) ON DELETE CASCADE,
    device          TEXT    NOT NULL CHECK (device IN ('connected_tv', 'mobile', 'desktop', 'tablet')),
    avails          DOUBLE PRECISION NOT NULL CHECK (avails >= 0),
    reach_universe  DOUBLE PRECISION,
    est_reach       DOUBLE PRECISION,
    PRIMARY KEY (series_id, device)
);

CREATE TABLE forecast_publisher (
    publisher_id  INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name          TEXT NOT NULL,
    name_norm     TEXT NOT NULL UNIQUE
);

CREATE TABLE forecast_avail_publisher (
    series_id       INTEGER NOT NULL REFERENCES forecast_series_version(series_id) ON DELETE CASCADE,
    publisher_id    INTEGER NOT NULL REFERENCES forecast_publisher(publisher_id),
    avails          DOUBLE PRECISION NOT NULL CHECK (avails >= 0),
    reach_universe  DOUBLE PRECISION,
    est_reach       DOUBLE PRECISION,
    PRIMARY KEY (series_id, publisher_id)
);

-- ---------------------------------------------------------------------------------------------------------------
-- Which catalog product is forecast from which series (the default; Admin -> Forecast can still override a product)
-- ---------------------------------------------------------------------------------------------------------------
CREATE TABLE forecast_series_product (
    product_key  TEXT PRIMARY KEY REFERENCES products(product_key) ON UPDATE CASCADE ON DELETE CASCADE,
    series_key   TEXT NOT NULL REFERENCES forecast_series_def(series_key) ON DELETE CASCADE,
    relation     TEXT NOT NULL DEFAULT 'primary' CHECK (relation IN ('primary', 'baseline_proxy')),
    note         TEXT
);
CREATE INDEX forecast_series_product_series_idx ON forecast_series_product (series_key);

COMMENT ON TABLE forecast_series_version IS 'One import of a series. Exactly one version per series_key is status=current (served); older ones are retired and kept for rollback.';
COMMENT ON TABLE forecast_avail_zip IS 'ZIP-level avails per series version. Absent ZIP = below the export floor (~180/month).';
COMMENT ON COLUMN forecast_series_version.data_basis IS 'platform_reported: numbers exported from the platform; modeled: derived from the DMA model workbook; extrapolated: reported sample extended to all DMAs by a documented model.';
