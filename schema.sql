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
--
-- dsc_email / dsm_email: the market's own Digital Sales Coordinator
-- (assistant) and Digital Sales Manager — CC'd on the internal seller
-- email alongside the flat `ccs` list, kept as their own named columns
-- (not just two more entries in `ccs`) so the admin UI and the export can
-- label WHO each address is rather than an undifferentiated CC blob. NULL
-- until an admin fills them in for a given market — the real per-market
-- data hasn't been supplied yet, so every seed row below leaves these two
-- blank rather than guessing.
-- ---------------------------------------------------------------------------
CREATE TABLE market_config (
    market_key       TEXT PRIMARY KEY,
    address_line1     TEXT,
    address_line2      TEXT,
    dsc_email             TEXT,
    dsm_email              TEXT,
    ccs                       TEXT[] NOT NULL DEFAULT '{}',
    updated_at                    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Seed rows so the app has the same zero-config defaults it has today —
-- run once, on a fresh database only (ON CONFLICT DO NOTHING makes this
-- safe to re-run). The 22 named markets below are the real Entravision
-- office addresses (client-supplied, Sep 2026) — dsc_email/dsm_email and
-- ccs are left blank pending the actual per-market directory; an admin
-- can fill them in via the Markets tab at any time without a code change.
INSERT INTO market_config (market_key, address_line1, address_line2, ccs) VALUES
    ('__default__', '1 Estrella Way', 'Burbank, CA 91504', '{}'),
    ('__base_ccs__', NULL, NULL, ARRAY['salesplanning@entravision.com']),
    ('__t1_ccs__', NULL, NULL, ARRAY['jwoods@entravision.com']),
    ('Albuquerque', '5411 Jefferson St. NE, Suite 200', 'Albuquerque, NM 87109', '{}'),
    ('Boston', '5426 N. Mesa St.', 'El Paso, TX 79912', '{}'),
    ('Corpus Christi', '801 N. Jackson Road', 'McAllen, TX 78501', '{}'),
    ('Denver', '1907 Mile High Stadium W. Circle', 'Denver, CO 80204', '{}'),
    ('El Centro', '5770 Ruffin Road', 'San Diego, CA 92123', '{}'),
    ('El Paso', '5426 N. Mesa', 'El Paso, TX 79912', '{}'),
    ('Hartford', '5426 N. Mesa St.', 'El Paso, TX 79912', '{}'),
    ('Laredo', '801 N. Jackson Road', 'McAllen, TX 78501', '{}'),
    ('Las Vegas', '250 Pilot Rd. Suite 160', 'Las Vegas, NV 89119', '{}'),
    ('Los Angeles', '1 Estrella Way', 'Burbank, CA 91504', '{}'),
    ('Lubbock', '5426 N. Mesa', 'El Paso, TX 79912', '{}'),
    ('McAllen', '801 N. Jackson Road', 'McAllen, TX 78501', '{}'),
    ('Midland', '5426 N. Mesa', 'El Paso, TX 79912', '{}'),
    ('Monterey', '801 N. Jackson Road', 'McAllen, TX 78501', '{}'),
    ('Orlando', '1 Estrella Way', 'Burbank, CA 91504', '{}'),
    ('Palm Springs', '72920 Parkview Drive', 'Palm Desert, CA 92260', '{}'),
    ('Phoenix', '501 N. 44th Street, Suite 125', 'Phoenix, AZ 85008', '{}'),
    ('Reno', '250 Pilot Rd. Suite 160', 'Las Vegas, NV 89119', '{}'),
    ('Sacramento', '1792 Tribute Road #450', 'Sacramento, CA 95815', '{}'),
    ('San Diego', '5770 Ruffin Road', 'San Diego, CA 92123', '{}'),
    ('Santa Barbara', '801 N. Jackson Road', 'McAllen, TX 78501', '{}'),
    ('Stockton', '1792 Tribute Road, Suite 450', 'Sacramento, CA 95815', '{}'),
    ('Wichita', '1907 Mile High Stadium W. Circle', 'Denver, CO 80204', '{}')
ON CONFLICT (market_key) DO NOTHING;

-- ---------------------------------------------------------------------------
-- Migration for an ALREADY-created market_config table (dev/production —
-- this schema file's own CREATE TABLE above only runs on a fresh database;
-- an existing table needs its two new columns added explicitly). Safe to
-- run more than once.
-- ---------------------------------------------------------------------------
ALTER TABLE market_config ADD COLUMN IF NOT EXISTS dsc_email TEXT;
ALTER TABLE market_config ADD COLUMN IF NOT EXISTS dsm_email TEXT;

-- ---------------------------------------------------------------------------
-- Migration for an ALREADY-created rate_overrides table: adds rename +
-- soft-delete support for BUILT-IN catalog products.
--
-- name: lets an admin rename a built-in product (previously impossible —
-- _OVERRIDABLE_FIELDS excluded it). rate_overrides.product_name stays the
-- STABLE key (the product's original catalog.py literal name — that never
-- changes, a rename only ever changes this `name` column, the admin-facing
-- display value). See catalog.py's by_name()/product_aliases below for how
-- an OLD display name keeps resolving after a rename.
--
-- is_deleted: lets an admin hide a built-in product from new selection
-- without touching the CATALOG Python list (which stays a pure reflection
-- of the rate card, per catalog.py's own module docstring) — a built-in
-- can't be truly removed without a code change/deploy, but it can be
-- soft-deleted. by_name() still resolves a soft-deleted product (so an
-- already-saved proposal referencing it doesn't silently lose the line
-- item); effective_catalog()/the parser's candidate list exclude it from
-- NEW selection.
-- ---------------------------------------------------------------------------
ALTER TABLE rate_overrides ADD COLUMN IF NOT EXISTS name TEXT;
ALTER TABLE rate_overrides ADD COLUMN IF NOT EXISTS is_deleted BOOLEAN NOT NULL DEFAULT FALSE;

-- ---------------------------------------------------------------------------
-- product_aliases — maps a FORMER product display name to its CURRENT one,
-- recorded whenever an admin renames a product (built-in or custom). Lets
-- by_name() and the free-text parser keep resolving old references (a
-- planner's pasted text, or product_name on an already-saved proposal's
-- line item) after a rename, with no code change/redeploy needed. Chained
-- (alias_name -> current_name, repointed on each further rename) so a
-- product renamed more than once still resolves in one hop from any of
-- its historical names to the latest.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS product_aliases (
    alias_name     TEXT PRIMARY KEY,
    current_name   TEXT NOT NULL,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- users / sessions — internal login (this app has no external users; it's
-- meant to keep the tool off-limits to anyone who merely has the URL, not
-- to support self-service signup). See app/auth.py.
--
-- password_hash/password_salt: hashlib.scrypt (stdlib — no new pip
-- dependency, unlike bcrypt/argon2), a random salt per user, constant-time
-- compare on login.
--
-- Sessions are SERVER-SIDE (an opaque random token in an HttpOnly cookie,
-- looked up here on every request) rather than a signed/stateless cookie —
-- specifically so disabling or deleting a user takes effect on their very
-- next request instead of waiting out a token's natural expiry, which a
-- stateless JWT/signed-cookie session can't do without extra revocation
-- machinery of its own.
--
-- expires_at is refreshed (pushed forward) on every authenticated request
-- (see app/auth.py's refresh_session) — an active user is effectively
-- never interrupted, but a session nobody has used in 5 days stops
-- working, which is the "long-lived but periodically reset" balance asked
-- for rather than either a short hard timeout or a session that never expires.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    id             TEXT PRIMARY KEY,
    email          TEXT NOT NULL UNIQUE,
    password_hash  TEXT NOT NULL,
    password_salt  TEXT NOT NULL,
    is_admin       BOOLEAN NOT NULL DEFAULT FALSE,
    disabled       BOOLEAN NOT NULL DEFAULT FALSE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sessions (
    token       TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at  TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions (user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_expires_at ON sessions (expires_at);

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
    created_at                                          TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by_email                                   TEXT
);

CREATE INDEX idx_proposals_generated_at ON proposals (generated_at DESC);

-- ---------------------------------------------------------------------------
-- Migration for an ALREADY-created proposals table: adds created_by_email,
-- which fixes a real bug — "My Proposal History" (and the admin Proposals
-- tab's own "My proposals" default) filtered on `seller_email`, but that
-- column has ALWAYS held whatever the pasted Notion request's own
-- "Salesperson email:" field said (the AE the DEAL belongs to — genuinely
-- useful on its own, e.g. an ops coordinator generating on someone else's
-- behalf, and left untouched here), which is NOT necessarily the same
-- person as whoever is actually logged in and clicked Generate. A planner
-- whose own login email didn't happen to match that pasted field would
-- never see their own generated proposals in their own history. This
-- column is the actual, reliable "who was logged in when this was
-- generated" signal (request.state.user["email"], set by the auth
-- session) — _query_proposals()'s mine-filter now uses THIS, not
-- seller_email. NULL for any proposal generated before this column
-- existed (there's no way to retroactively know who that really was) —
-- those rows simply won't appear in anyone's "mine" view, which is
-- correct/expected, not a bug to work around.
-- ---------------------------------------------------------------------------
ALTER TABLE proposals ADD COLUMN IF NOT EXISTS created_by_email TEXT;

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
