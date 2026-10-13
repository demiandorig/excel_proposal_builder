# Forecast data platform

How avails data lives in Postgres, how it gets there, and how to change it. Everything described here is in the pull
request that introduced it: SQL migrations a person reviews, a manifest of what feeds what, import rules that run
before anything is published, and tests for all of it.

## At a glance

```
 source files (a team export, a workbook, a CSV)         app/data/forecast/sources/<date>/...
        │                                               app/data/forecast/manifest.json   what feeds which series, and
        ▼                                                                                 which catalog products use it
  readers  ──►  one neutral shape, CANONICAL keys       app/services/forecast_import/readers.py
        │       (one spelling per DMA / state / district)   geo_reference.py + dma_aliases.csv
        ▼
  validation ──► written rules with ids (S/G/N/L/X/M/P)  app/services/forecast_import/validate.py
        │       an error blocks publishing; warnings are recorded
        ▼
  stage ──► an immutable VERSION (status 'staged')       migrations/0001_forecast_platform.sql
        ▼
  publish ──► the version becomes 'current'; the one it replaces is 'retired' (rollback = publish it again)
        ▼
  forecast_store (cached metadata, narrow reads) ──► constanza_engine ──► /api/constanza/estimate, /api/catalog, /api/forecast/series
```

Nothing is edited in place. A refresh is a new version; a bad import is a rollback, not a repair.

## Tables

| Table | What it holds |
|---|---|
| `geo_state`, `geo_dma`, `geo_congressional_district` | The canonical geography. 51 states (incl. DC), 210 DMAs (slug key, Nielsen-style name, rank), 436 districts (`GA-05`, at-large `AK-AL`, DC delegate `DC-AL`). |
| `geo_dma_alias` | Every spelling a source has used for a DMA, mapped to exactly one DMA. Seeded from `dma_aliases.csv`. |
| `products.product_key` | A stable key for every catalog product, assigned once by the database (never changes when the name is edited). |
| `forecast_series_def` | One row per forecastable series (`connect-default`, `live-sports-mlb`, `netflix-15s`, `roku`, ...): title, channel, targeting, **unit** (`monthly` or `weekly`). |
| `forecast_series_version` | One row per import of a series: `staged` → `current` → `retired`. A partial unique index guarantees **at most one `current` version per series**. Also holds data basis, period (live sports), planning band, source file + sha256, an import fingerprint, row counts and the validation result. |
| `forecast_avail_dma` / `_state` / `_cd` / `_zip` | The numbers: one narrow table per geography level, primary key `(series_id, key)`. DMA rows may also carry a planning range, reach and a `reported`/`modeled` flag. |
| `forecast_avail_unknown` | The "Unknown" row of each breakdown (avails the platform could not place), kept so totals reconcile. |
| `forecast_avail_device`, `forecast_avail_publisher` (+ `forecast_publisher`) | Device and publisher breakdowns per series version. |
| `forecast_series_product` | Which catalog product is forecast from which series, and how (`primary`, or `baseline_proxy` for a product that uses another product's avails as its baseline). |
| `forecast_import_batch` | One row per import run: who, tool version, files + hashes, and the report. |
| `forecast_product_map` | (existing) Admin → Forecast overrides, keyed by product name. An admin row beats the default from `forecast_series_product`. |
| `audience_group`, `audience_segment`, `audience_incidence` | The interest taxonomy and its sourced rates (see "Audience incidence"). |
| `schema_migrations` | The migration ledger. |

`schema.sql` stays the baseline (the original tables). Everything after it is a numbered file in `migrations/`.

## Migrations

```bash
python -m app.services.migrations status        # applied / pending / drifted
python -m app.services.migrations apply         # also runs automatically when the app starts
python -m app.services.migrations apply --dry-run
python -m app.services.migrations new add_thing # creates migrations/NNNN_add_thing.sql
```

* One plain SQL file per change, `NNNN_snake_case.sql`, applied in order, **each in its own transaction**.
* Recorded in `schema_migrations` with a SHA-256. **An applied migration is never edited**: a file changed after it was
  applied is reported as `drifted` and not re-run. Change the schema with the next file.
* An advisory lock serialises the runner, so several app instances starting at once cannot race.
* A failure rolls that migration back whole and stops; earlier ones stay applied.

## Importing data

```bash
python -m app.services.forecast_import plan                 # read + validate everything; changes nothing; exit 1 on any error
python -m app.services.forecast_import plan --only connect-default -v
python -m app.services.forecast_import import              # stage, validate, publish (idempotent)
python -m app.services.forecast_import import --no-publish  # stage only; the current version keeps being served
python -m app.services.forecast_import import --accept-large-change   # after confirming a >40% move is real
python -m app.services.forecast_import status
python -m app.services.forecast_import rollback connect-default [--to 2]
python -m app.services.forecast_import prune --keep 2       # delete the numbers of old retired versions
python -m app.services.forecast_import seed-products        # add catalog products that an older database lacks
```

* **Idempotent.** A series is skipped (`unchanged`) when its source file *and* its spec in the manifest are the same as an
  existing version (the "import fingerprint"). Running it twice does nothing.
* **One bad series never blocks the others.** It is reported with its findings and skipped; the rest load.
* **At startup** the app loads only what has *never been imported* (a fresh database, or a series newly added to the
  manifest) and the 30 forecast products, in a background thread, once across instances. Refreshing a series that already
  has a version is always a deliberate `import`.
* `scripts/post-merge.sh` runs the same import after a merge on Replit, so a pull request that changes a source file is
  loaded when it is merged. A failure there is reported but never fails the hook.

### Adding or refreshing a product's avails

1. Put the export in `app/data/forecast/sources/<yyyy-mm>/` (keep the original file name).
2. Add/adjust its entry in `manifest.json`: `series_key`, title, channel, unit, reader, source, and the catalog `products`
   it forecasts (relation `primary`, or `baseline_proxy`).
3. A new catalog product goes in `app/data/forecast/products.json` (then `seed-products` adds it to a database that already
   has the catalog).
4. `plan` → read the findings → `import`. Review the diff of the manifest and data files in the pull request.

A DMA label the tool does not know is an **error** with the closest suggestions, never a guess. Fix it with one reviewed
row in `app/data/forecast/dma_aliases.csv` (`alias`, canonical `dma`).

## Import rules

Severities: **error** blocks publishing, **warning** is recorded with the version, **info** is context.

| Id | Sev. | Rule |
|---|---|---|
| S001 | error | An export has exactly the six breakdown files (Metros, States, Zip Codes, Congressional District, Devices, Publishers), each once, with one product name. Macintosh resource forks are ignored; any other file is flagged; an entry with an unsafe path is refused. |
| S002 | error | Column headers are exactly as expected. |
| S003 | error | Every row has the right number of columns and a key; a geography appears **once** (two spellings of one market count as twice). A repeated publisher is merged with a warning. |
| S004 | error | Every numeric cell is a non-negative number. |
| S005 | error | At least one market row was read. |
| G001 | error | Every DMA label is in `dma_aliases.csv`. |
| G002 | error | Every state, district, device is known; every ZIP is exactly five digits (leading zeros kept). |
| N001 | error | *Total Avails* equals *Monthly Avails* (within rounding) on ≥ 99.9% of rows. |
| N002 | warning | Daily × 30 ≈ Monthly in total (0.97–1.05; the export rounds each row). |
| N003 | error | *Est. Reach* never exceeds *Reach Universe* (≥ 99.9% of rows). |
| N004 | error | The breakdowns describe the same inventory: market / state / district / device / publisher totals agree within 0.5%, ZIP within 1.5% (the ZIP file drops rows under ~180 a month). |
| N005 | warn / error | "Unknown" share per level: > 10% warns, > 25% fails. |
| N006 | warn / error | Market coverage: 200–209 of 210 warns and names the missing ones (counted as zero); fewer than 200 fails. |
| N007 | error | Not every market is zero. |
| N010 | warning | New York or Los Angeles should be among the five largest markets of a national series (a mis-keyed file is not). |
| N011 | warning | A ZIP level with fewer than 1,000 rows. |
| L002 | warning | Live Sports: cells outside the market and number columns are reported, not loaded. |
| L005 | warning | Live Sports: a measure header that is not "Weekly …" is **treated as weekly and flagged** (see decisions below). |
| P001 | warning | Live Sports: the sheet's period note changed since the manifest was written. |
| X001 | error | Netflix: at least 8 reported markets overlap the reference series to extend from. |
| X002 | info | Netflix: how many markets were reported / extended, the factor and the fit. |
| X003 | warning | Netflix: a market whose :30 / :15 relationship differs from the rest (New York in the sample). |
| X004 | error | Netflix: the extension misses the reported markets by more than 35% on average. |
| M002 | warn / error | The national total moved more than 40% from the current version: an **error** unless `--accept-large-change`. |
| M003 | warning | A product named in the manifest is not in the catalog (not linked). |

### What the source files taught us (and the rules encode)

* **The exports are rounded** to tens or hundreds, and drop ZIPs under about 180 avails a month. A ZIP with no row means
  "negligible", so it counts as zero, and the estimate says so.
* **DMA names differ by source**: the Madhive exports use verbose names ("Tampa - Saint Petersburg (Sarasota)"), the Live
  Sports workbook another style ("Albany, GA"), the Netflix file Nielsen abbreviations ("Sacramnto-Stkton-Modesto"). All map
  to the canonical names through `dma_aliases.csv`; 40 of them needed more than a case change.
* **Publisher identity keeps symbols**: `AMC` and `AMC+`, `Discovery` and `Discovery+` are different publishers.
* **Every geographic breakdown has an "Unknown" row** (2–4% of avails), kept apart so totals reconcile and excluded from
  geo-targeted estimates (a national estimate says how much is unplaced).

## Netflix: a sample, extended

`Netflix Baseline Avails.csv` lists 17 markets (two creative lengths). To give Netflix the same shape as the other
products, the remaining 193 DMAs are **extended** (never presented as reported):

> avails(market) = k × the market's avails in *Entravision+ Connect - (Default)*, where k is the **median** of
> sample ÷ reference over the 17 reported markets.

The Connect market distribution fits the reported markets better than population or TV households do (mean error about
14% versus about 20%). Extended rows are flagged `modeled` in the database, the series is `extrapolated`, and the planning
range for an extended market is wider (0.55×–1.45× instead of 0.7×–1.3×). The fit is stored with the version and the import
**fails** if it is worse than 35%. New York's :15 and :30 figures are identical in the source while every other market's :30
is about 92.5% of its :15; that is flagged (X003), not corrected.

The ten Netflix products map to the two series: the run-of-network products are `primary`; the age-, gender-, genre- and
Spanish-targeted variants are `baseline_proxy` (an upper bound, and the estimate says so).

## Live sports

Each tab of `Live Sports Estimates- Entravision.xlsx` is its own series and its own catalog product. The values are
**weekly** estimates by DMA; the estimate multiplies by the flight's weeks (`flight_days / 7` when the caller sends it,
else months × 4.35) and shows the period next to the product (`period_label`, with dates where the source gives them).
Flight dates are **not** checked against the window: seasons repeat each year and the source describes one season.

## How an estimate is built

For each resolved area the most direct row wins:

1. a **ZIP** area uses its series' own ZIP row; a ZIP with no row is zero (and listed in a note);
2. a **congressional-district** area uses its district row;
3. a whole **DMA** uses its DMA row;
4. anything finer (county, city) is its parent DMA's row × the area's population share (as before).

Then × months (or weeks), with the planning range from the row (legacy series) or the series band. Every estimate carries
its series, version, data basis, unit and period, and notes in plain language. For the seven original model series the
database path reproduces the old bundled-workbook numbers **exactly** (a test compares them across 14 geographies).

## Indexes (measured)

The rule: **add an index only when `EXPLAIN (ANALYZE, BUFFERS)` shows a need.** The queries are the exact strings in
`app/services/forecast_store.py`; `scripts/explain_forecast_queries.py` runs them against any database (use `--check` to fail
on a sequential scan of a big table, `--sizes` for table and index sizes, `--plans` for full plans).

Every value table has one index: its primary key `(series_id, geography key)`, which is exactly the filter every lookup uses
(`series_id = X AND key = ANY(...)`: equality column first, then the key). Measured on Postgres 16:

| Query | Today (276 K ZIP rows) | 20× (5.0 M ZIP rows, 538 MB) | Plan |
|---|---|---|---|
| ZIP lookup, 3 ZIPs | 0.006 ms | 0.006 ms | index scan on the primary key |
| ZIP lookup, 50 ZIPs | 0.055 ms | 0.060 ms | index / bitmap scan on the primary key |
| ZIP lookup, 500 ZIPs | 0.66 ms | 0.67 ms | bitmap scan on the primary key |
| congressional-district lookup | 0.008 ms | 0.007 ms | primary key |
| state lookup | 0.010 ms | 0.005 ms | primary key |
| a series' 210-row DMA table | 0.029 ms | 0.068 ms | bitmap scan on the primary key (cached per series afterwards) |
| current series / product links | 0.03–0.08 ms | 0.03 ms | sequential scan of a 40–90 row table (what the planner should do; cached for 60 s) |

**Considered and not added:** an index on `forecast_avail_zip (zip)` for the question "which series carry ZIP X". Without it
that query takes about 111 ms at 5 M rows; with it 0.02 ms, at a cost of 33 MB. The application never asks that question, so
the index is not created; if a feature needs it, it is one `CREATE INDEX` in the next migration with these numbers behind it.
Partitioning the ZIP table is not needed until tens of millions of rows.

Re-measure on Replit after the first import:

```bash
python scripts/explain_forecast_queries.py --check --sizes
```

## Audience incidence (the interest "factors")

Interest targeting multiplies an estimate by *incidence*: the share of US adults 18+ who match an audience in a typical 12
months. The table lives in Postgres (`audience_group`, `audience_segment`, `audience_incidence`, migration `0002`) and is
seeded from `app/data/constanza/audience_taxonomy.json` (`python -m app.services.forecast_import seed-audience`, also run by
the post-merge hook and at startup). If the database cannot be read, the bundled file answers, so interest targeting never
depends on Postgres being up. The whole taxonomy is about a hundred rows, so search runs in Python
(`app/services/audience_taxonomy.py`): exact name or team wording, a keyword phrase inside what was typed ("likes sports"),
the typed text inside a name ("dental"), shared words, and near-spellings ("fitnes gym").

* **90 audiences in 15 groups**, named the way the sales team writes them (kept as `company_names`, so the team's own wording
  finds the segment) plus search keywords in English and Spanish. The specific sports (NFL, college football, NBA, WNBA, MLB,
  soccer, NHL, golf, tennis, motorsports, MMA/boxing) are separate audiences next to "Sports Fans".
* **Each rate has a status.** `sourced` (a published figure answers the definition), `derived` (computed from cited inputs; the
  arithmetic is stored in `derivation`) or `no_public_rate` (listed and searchable, **no number**: the engine says so and leaves
  the estimate unchanged rather than guess). Every rate also carries its definition, denominator, time window, source and
  URL, the verbatim evidence, a confidence grade and caveats.
* **How the numbers were produced.** One researcher per theme found a figure from a primary or reputable source; a second,
  independent agent re-opened every cited page and checked the number, the denominator (adults, not households, workers or
  ages 6+), the time window and any arithmetic. Their verdicts are stored with each record
  (`verification`): 39 sourced and 15 derived rates survived, many after corrections (for example the video-game rate was
  replaced because the cited page did not carry it and its base was not adults); **36 audiences have no defensible public
  number** and ship blank. Rebuild with `python scripts/audience_taxonomy/build.py --research DIR`.
* **Confidence feeds the range.** Each applied interest widens the low/high band by its grade (high 15%, moderate 25%, low
  40%), combined with the other factors by root-sum-of-squares. Interests are multiplied as if independent, and the result
  says so.
* **Admin edits are kept.** A segment or rate an admin changed (`origin = 'admin'`) is not overwritten by the next seed; a
  segment dropped from the file is deactivated, not deleted (saved proposals may still name it).
* **Freshness.** `scripts/check_constanza_data_freshness.py` reports the file's age against a 365-day cadence.

API: `GET /api/constanza/interest-taxonomy` (every group and segment, plus the legacy `categories` list) and
`GET /api/constanza/interests?q=` (ranked search, the same scoring the estimate uses to read an interest phrase).

## Connection pool

`app/db.py` keeps one small pool per process instead of opening a connection for every database operation (about 11× less
time per operation on localhost, 2.5 ms → 0.2 ms; more over a network). All 60 `with get_connection() as conn:` call sites
are unchanged.

| Variable | Default | Meaning |
|---|---|---|
| `DB_POOL_MAX` | 8 | most connections per process (Autoscale: multiply by instances) |
| `DB_POOL_MIN` | 1 | connections kept open when idle |
| `DB_POOL_TIMEOUT` | 10 | seconds to wait for a free connection |
| `DB_CONNECT_TIMEOUT` | 8 | seconds to wait for a new connection |
| `DB_POOL_MAX_IDLE` / `DB_POOL_MAX_LIFETIME` | 300 / 1800 | recycle idle / old connections |
| `DB_POOL_DISABLE` | – | `1` = one connection per call (the previous behaviour) |

A connection the server dropped while idle is replaced, not lent. When the database is unreachable the first call fails fast
and further calls fail instantly for 5 seconds, so every fail-open path (offline product list, forecast defaults) stays
quick. A busy pool is *not* treated as an outage.

## Operating notes (Replit)

* Pull → the post-merge hook installs `requirements.txt` (adds `psycopg-pool`), runs the tests and the import. On app start the
  migrations and the first-time load run in the background.
* **Decisions the data owners should confirm** are listed in the pull request: the unit of the Live Sports playoff tabs
  (headed "Estimates"; treated as weekly), the overlap of the new `eDigital Display` / `eDigital OLV` products with the
  existing eDigital Network Display / Video - Pre-roll products, and the Netflix rates in the CSV ($54 / $60) versus the
  catalog ($44 / $54).
* All 30 new products carry a **$40 CPM placeholder** and a "PLACEHOLDER RATE" note until the rate card is confirmed.
