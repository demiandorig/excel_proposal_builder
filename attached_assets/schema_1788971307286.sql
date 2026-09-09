-- Postgres schema for the Proposal Builder's admin-editable data.
-- See POSTGRES_MIGRATION_BRIEF.md for context on what this replaces and why.
-- Written to run cleanly on a fresh, empty database (Replit's provisioned
-- "Development Database" had 0 tables at the time this was written).

-- ---------------------------------------------------------------------------
-- rate_overrides — one row per BUILT-IN catalog product that has at least
-- one field overridden. Mirrors app/catalog.py's _OVERRIDABLE_FIELDS
-- exactly; add a column here if that tuple ever grows. Every column is
-- nullable — NULL means "not overridden, use the catalog default," the
-- same meaning `None`/an absent key has in the current JSON file.
-- ---------------------------------------------------------------------------
CREATE TABLE rate_overrides (
    product_name            TEXT PRIMARY KEY,
    base_rate                NUMERIC(12, 2),
    minimum_spend             NUMERIC(12, 2),
    estimated_cpm_for_imps    NUMERIC(12, 2),
    family                    TEXT,
    short_label               TEXT,
    buying_model              TEXT,
    sizes                     TEXT,
    tech_platform             TEXT,
    proposal_description      TEXT,
    notes                     TEXT,
    is_addon                  BOOLEAN,
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- custom_products — one row per admin-added product (full record, not a
-- diff against anything). Column set matches app/catalog.py's Product
-- dataclass field-for-field.
-- ---------------------------------------------------------------------------
CREATE TABLE custom_products (
    name                       TEXT PRIMARY KEY,
    family                     TEXT NOT NULL,
    short_label                TEXT NOT NULL DEFAULT 'Custom Product',
    proposal_description       TEXT NOT NULL DEFAULT '',
    sizes                      TEXT NOT NULL DEFAULT 'Custom',
    buying_model               TEXT NOT NULL CHECK (buying_model IN ('CPM', 'CPP', 'Fixed')),
    base_rate                  NUMERIC(12, 2),
    estimated_impressions      BOOLEAN NOT NULL DEFAULT FALSE,
    discloses_impressions      BOOLEAN NOT NULL DEFAULT TRUE,
    minimum_spend              NUMERIC(12, 2) NOT NULL DEFAULT 0,
    minimum_flight_days_min    INTEGER NOT NULL DEFAULT 7,
    minimum_flight_days_max    INTEGER NOT NULL DEFAULT 90,
    sla_data_days              INTEGER,
    sla_creative_days          INTEGER,
    sla_activate_days          INTEGER,
    sla_total_days             INTEGER,
    media_allocation_pct       NUMERIC(5, 4) NOT NULL DEFAULT 0,
    margin_upper               NUMERIC(5, 4) NOT NULL DEFAULT 0.5,
    margin_lower               NUMERIC(5, 4) NOT NULL DEFAULT 0.3,
    tech_platform               TEXT NOT NULL DEFAULT '',
    wide_orbit_code             TEXT NOT NULL,
    billing_interval             TEXT NOT NULL DEFAULT 'Monthly',
    billing_calendar             TEXT NOT NULL DEFAULT 'Standard/prorated',
    billing_source                TEXT NOT NULL DEFAULT '1st Party Actual',
    national_supported            BOOLEAN NOT NULL DEFAULT TRUE,
    notes                          TEXT NOT NULL DEFAULT '',
    estimated_cpm_for_imps         NUMERIC(12, 2),
    hispanic_targeting_forced      BOOLEAN,
    cannabis_policy                 TEXT NOT NULL DEFAULT 'not_allowed'
                                     CHECK (cannabis_policy IN ('allowed', 'custom_request_only', 'mh_only', 'not_allowed')),
    political_policy                 TEXT NOT NULL DEFAULT 'allowed'
                                     CHECK (political_policy IN ('allowed', 'restricted', 'not_allowed')),
    is_addon                          BOOLEAN NOT NULL DEFAULT FALSE,
    created_at                        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- market_config — per-market office address + CC list, plus two reserved
-- singleton rows (market_key = '__base_ccs__' / '__t1_ccs__') that only
-- ever populate `ccs` (address columns stay NULL for those two). Simpler
-- to keep this as ONE table matching the existing JSON shape 1:1 than to
-- split base/T1 CCs into their own table for what's really just two
-- always-present config rows.
-- ---------------------------------------------------------------------------
CREATE TABLE market_config (
    market_key       TEXT PRIMARY KEY,
    address_line1     TEXT,
    address_line2      TEXT,
    ccs                  TEXT[] NOT NULL DEFAULT '{}',
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Seed rows so the app has the same zero-config defaults it has today —
-- run once, on a fresh database only (ON CONFLICT DO NOTHING makes this
-- safe to re-run).
INSERT INTO market_config (market_key, address_line1, address_line2, ccs) VALUES
    ('__default__', '1 Estrella Way', 'Burbank, CA 91504', '{}'),
    ('__base_ccs__', NULL, NULL, ARRAY['salesplanning@entravision.com']),
    ('__t1_ccs__', NULL, NULL, ARRAY['jwoods@entravision.com'])
ON CONFLICT (market_key) DO NOTHING;

-- ---------------------------------------------------------------------------
-- proposals — one row per generated proposal, replacing data/proposals/*.json.
-- summary/reopen_state stay as JSONB (read back whole today, never queried
-- field-by-field) rather than being normalized into more tables.
-- ---------------------------------------------------------------------------
CREATE TABLE proposals (
    proposal_id            TEXT PRIMARY KEY,
    client_name             TEXT,
    seller_email              TEXT,
    requested_by                TEXT,
    notion_id                     TEXT,
    proposal_title                 TEXT,
    filename                         TEXT,   -- the .xlsx
    email_doc_filename                 TEXT,   -- client-email .docx, when enrichment produced one
    pptx_net_filename                    TEXT,   -- Net PowerPoint deck, when a Net tab was built
    pptx_gross_filename                     TEXT,   -- Gross PowerPoint deck, when a Gross tab was built
    generated_at                              TIMESTAMPTZ,
    requester_ip                                TEXT,
    requester_user_agent                          TEXT,
    summary                                         JSONB,
    reopen_state                                      JSONB,
    created_at                                          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_proposals_generated_at ON proposals (generated_at DESC);

-- ---------------------------------------------------------------------------
-- drive_tokens — the single stored Google OAuth2 token for Drive uploads
-- (app/services/drive_uploader.py), replacing the local file
-- ~/.entravision_drive_token.json. One row, fixed id — there's only ever
-- one Drive connection for the whole app (it authorizes as the app's own
-- service identity, not per-user), so this is a singleton row rather than
-- a real per-something table, same shape as market_config's reserved rows.
-- Moving this off local disk isn't a nice-to-have like the other three
-- tables above — it's required for Drive upload to work AT ALL on an
-- autoscale deployment (see POSTGRES_MIGRATION_BRIEF.md's freshness note).
-- ---------------------------------------------------------------------------
CREATE TABLE drive_tokens (
    id             TEXT PRIMARY KEY DEFAULT 'default',
    token          TEXT NOT NULL,
    refresh_token  TEXT,
    token_uri      TEXT NOT NULL,
    client_id      TEXT NOT NULL,
    client_secret  TEXT NOT NULL,
    scopes         TEXT[] NOT NULL DEFAULT '{}',
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
