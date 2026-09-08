# Postgres migration brief — Proposal Builder admin data

**Purpose:** move this app's admin-editable data off local JSON files (which
get wiped on every Replit redeploy) onto the Postgres database already
provisioned in Replit (the one shown in your Database tool, connection
string starting `postgresql://postgres:...`). Hand this file + `schema.sql`
to whoever implements the migration — the Replit Agent, most likely — as
the spec for what's changing and why. A copy-pasteable kickoff prompt for
the Replit Agent is in the chat message this file was sent with.

**Freshness note:** refreshed after this session's PowerPoint export,
Tag T1 escalation, and admin full-edit-product work — `schema.sql`'s
`proposals` table now includes the PPTX deck filenames, and `market_config`
already accounts for the T1 CC list. If more app features land after this,
re-check this table and `schema.sql` against the current code before
treating either as authoritative — they're a snapshot, not a live doc.

**This migration just went from "nice to have" to "actually broken":**
live testing surfaced Google Drive uploads stuck in a genuine auth loop —
authorize, get redirected back as authorized, click Upload again, get told
authorization is needed *again*. Root cause: `drive_uploader.py` stores its
OAuth token in a local file (`~/.entravision_drive_token.json`), and this
app's Replit deployment target is **autoscale** — which runs stateless
instances with no shared, persistent filesystem. A token written by
whichever instance handled the OAuth callback simply isn't there for
whichever instance handles the next request. No amount of app-level retry
logic can fix that; it needs a real shared store. `drive_tokens` (below) is
that fix, and it should be treated as part of this same migration, not a
follow-up — it's the one piece here actively blocking a feature today
rather than just risking data loss on the next redeploy.

## Current state (what's being replaced)

Four JSON files under `app/data/` (all gitignored — real per-deployment
data, not code), read fresh on every request and rewritten whenever an
admin saves a change — plus one more file outside `app/data/` entirely
that has the exact same problem:

| File | Shape | Read/written by |
|---|---|---|
| `rate_overrides.json` | `{product_name: {field: value, ...}}` — only fields that differ from the built-in catalog | `app/catalog.py`: `load_rate_overrides`, `save_rate_overrides`, `clear_rate_override`, `_apply_override` |
| `custom_products.json` | `[{...every Product field...}, ...]` — admin-added products, full records | `app/catalog.py`: `load_custom_products`, `add_custom_product`, `update_custom_product`, `delete_custom_product` |
| `market_config.json` | `{"__default__": {address_line1, address_line2, ccs}, "__base_ccs__": [...], "__t1_ccs__": [...], "<market name>": {address_line1, address_line2, ccs}, ...}` | `app/market_config.py`: `load_market_config`, `save_market_config`, plus the `get_*`/`set_*` helpers built on those two |
| `data/proposals/*.json` (a different top-level `data/` dir, not `app/data/`) | one file per generated proposal — `{proposal_id, client_name, seller_email, requested_by, notion_id, proposal_title, filename, email_doc_filename, pptx_net_filename, pptx_gross_filename, generated_at, requester_ip, requester_user_agent, summary: {...}, reopen_state: {...}}` | `app/main.py`: written in `/api/generate`, read by `/api/admin/proposals` (list), `/api/proposal/{id}/reopen`, and the `/api/download*` family of endpoints |
| `~/.entravision_drive_token.json` (home dir, not `app/data/` — a pre-existing oversight, not a deliberate choice) | `{token, refresh_token, token_uri, client_id, client_secret, scopes}` — the one stored Google OAuth2 token for Drive uploads | `app/services/drive_uploader.py`: written by `exchange_code` (right after the OAuth callback), read/refreshed by `_load_credentials` |

The `.xlsx` files themselves stay on disk / Drive — only the JSON *metadata*
needs to move.

## Target schema (Postgres)

See `schema.sql` alongside this file for the actual `CREATE TABLE`
statements. Summary:

- **`rate_overrides`** — one row per overridden product (`product_name`
  primary key, one nullable column per overridable field). Replaces the
  single big JSON blob with real rows so multiple admins editing at once
  don't clobber each other's unrelated changes (a real risk with the
  current "read the whole file, write the whole file back" pattern).
- **`custom_products`** — one row per admin-added product, one column per
  `Product` dataclass field (see `app/catalog.py`'s `Product` definition
  for the full field list/types).
- **`market_config`** — one row per market (`market_key` primary key),
  plus two reserved singleton rows (`__default__`, `__base_ccs__`,
  `__t1_ccs__` — three, actually) for the fallback address and the two
  always-on/threshold-triggered CC lists. One table, not split, matching
  the existing JSON file's own shape 1:1.
- **`proposals`** — one row per generated proposal, replacing the
  `data/proposals/*.json` files — includes the `.xlsx`/`.docx`/PPTX
  filenames as real columns (not buried in JSON) since those are queried
  individually today (e.g. "does this proposal have a PPTX deck").
  `summary` and `reopen_state` stay as `JSONB` columns rather than being
  fully normalized — they're read back as opaque blobs today (never
  queried field-by-field), so forcing them into more tables would add
  migration risk for no real benefit right now.
- **`drive_tokens`** — a single-row table (`id` fixed at `'default'`,
  same reserved-singleton pattern as `market_config`'s `__default__` row)
  holding the one Google OAuth2 token this app uses for Drive uploads.
  There's only one Drive connection for the whole app, not one per user,
  so a singleton row is correct here, not a shortcut.

## What actually needs to change in the app

1. Confirm `DATABASE_URL` (Replit auto-injects this for its own
   provisioned Postgres) is readable as a real env var from the running
   app — not just visible in the Database tool's UI. **Specifically
   confirm it's set the same way for BOTH the dev workspace and the
   published deployment** — Replit sometimes provisions those as separate
   database instances, which would silently defeat the whole point of this
   migration if the deployed app ends up pointed at a different, empty
   database than the one the dev workspace was tested against.
2. Add a Postgres driver + a thin connection helper (`psycopg` — the
   maintained one, not the older `psycopg2` — or SQLAlchemy Core if this
   codebase prefers query-builder style over raw SQL; either is fine, this
   app's query needs are simple).
3. Rewrite the load/save functions listed in the table above to run SQL
   against these tables instead of reading/writing JSON files. The
   function *signatures* (`load_rate_overrides() -> dict`, etc.) can stay
   the same — every caller elsewhere in the app keeps working unchanged,
   only the implementation swaps from file I/O to a DB query.
4. Same swap for `drive_uploader.py`'s `_load_credentials` (reads +
   refreshes) and `exchange_code` (writes) — `TOKEN_PATH` goes away
   entirely, replaced by a `drive_tokens` upsert keyed on `id = 'default'`.
   This one has no admin UI trigger to test through the same way the other
   three do — after the migration, verify it by actually running the Drive
   upload flow end to end on the deployed app (authorize once, then upload
   a second proposal *without* re-authorizing) rather than only checking
   that the table exists.
5. One-time data migration: if there's anything real in the current local
   `app/data/*.json` files, the deployment's current copies of them, or an
   already-authorized `~/.entravision_drive_token.json` on the deployment,
   import those rows into the new tables before cutting over (a short
   one-off script, not part of the app itself).
6. Remove `app/data/*.json` from `.gitignore`/stop writing them once the
   DB path is confirmed working; keep the old JSON-reading code path
   available behind a feature flag for one deploy cycle as a rollback
   option, or just keep a backup of the last-known-good JSON files. (The
   Drive token file was never gitignored on purpose — it's in the home
   dir, outside the repo entirely — so there's nothing to clean up there
   beyond deleting the stray local file once the DB path is confirmed.)

## Explicitly NOT changing

- The `.xlsx` proposal files themselves (stay as generated files, optionally
  uploaded to Drive — unrelated to this migration).
- Nothing about how the frontend (`app.js`/`admin.js`) talks to the
  backend — this is entirely a backend storage-layer swap, the API
  contract (`/api/admin/rates`, `/api/admin/market-config`, etc.) doesn't
  change shape.
