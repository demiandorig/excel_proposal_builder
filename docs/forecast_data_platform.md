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

**A state asked for as a whole** ("Texas", "WA") resolves to the markets homed in it, but a state is more than that: Wyoming's
own row in the Madhive exports is nearly twice what its two home markets hold (the rest is served from Denver, Billings and
Salt Lake City). When the series carries state rows, the markets are scaled so they add up to the platform's own state total
and a note says so (`whole_states` on the resolution, `_state_scales` in the engine). A ZIP, county or city inside the state
is never scaled. **Only a request that is those states and nothing else is scaled.** A state's total already holds the
households inside it that other markets serve, so "Wyoming and Denver", or "Wyoming and Albany County" (a county Denver
serves), would count them twice; when anything outside the whole states is in the request, no state is scaled and a note says
why (each market is counted as the platform reports it).

**A baseline-proxy product** (a Netflix targeted variant, VIX 360) forecasts from another product's series; the link's stored
caveat ("this targeted variant will deliver fewer impressions") travels with the product's config into every estimate.

**Audience gender** is validated (`male`, `female`, `all`; "Male" and "women" are understood, anything else is a 422, never read
as "everyone" while counted as an applied cut). An interest with no sourced male/female split applies its all-adults rate to a
gender cut and the estimate says so.

## Geography matching

Free text goes through `app/services/constanza_geo_text.py` (the forgiving layer) before the strict matchers in
`constanza_geo.py`. Every change is reported back as an interpretation; a guess is never silent, and a suggestion is always a
real place the resolver accepts.

* **"City, ST" is the city** when a real place of that name exists in that state ("Richmond, CA", "Washington, PA",
  "Kansas City, KS"): never "the Richmond market plus every California market". A list written the old way ("Los Angeles, NV",
  "Washington, Oregon", "CA, NV, AZ") is still a list; "D.C." is read as DC.
* **Containers** ("X and surrounding areas in Johnson County", "Plano in Collin County") mean the larger area. A county name
  that exists in several states is narrowed in this order: the ones in the same TV market as the place named inside it; of
  those, the ones with at least as many people as that place (a county cannot surround a bigger city: "Kansas City, MO" is
  not inside Johnson County, MO); then the one in the state typed with the place ("Warrensburg, MO" -> Johnson County, MO).
  The interpretation says which rule chose, and the other counties of the market are offered as one-click alternatives. When
  nothing separates two (no state typed with the place) the planner picks, and the inner place's state is never assumed. A
  container that cannot be matched falls back to the place inside it.
* **Lists** with "and" / "or" / "&", a state written once at the end ("Austin and Round Rock, TX"), plural counties
  ("Los Angeles and Orange counties, CA") and lists where only part resolved are all read; "or" is read like "and" (the places
  are added) and says so. **Only a lowercase `or` with spaces around it is a word: `OR` is Oregon** ("Beaverton, OR and
  Hillsboro, OR" is two cities). An Oxford ", or" before a place ("Dallas, Houston, or Austin", "Eugene, OR, or Salem, OR")
  separates the places; a lowercase "or" after a comma that is followed by "and", "&", "+", a ZIP or the end of the line is
  still the state ("portland, or and seattle", a pasted list with "portland, or" on one line). The state typed once at the end
  belongs to each place that exists in it ("Lincoln and Springfield, IL" is Lincoln, IL, not the Nebraska market), and when
  that turns a TV market into a town ("Dallas and Lostine, OR") the places as typed are offered as one click; where the
  qualified form is not a place - it would become the whole state ("Chicago, IN") or leaves the state unmatched
  ("Philadelphia, DE") - the piece stays a market of its own, and a piece inside a listed market is counted once with a note.
* **"City, ST ZIP" is the ZIP.** An address ("Plano, TX 75024", "Portland, OR 97201", "Phoenix AZ 85001") with exactly one
  place and its state before the ZIP is read as its ZIP, the exact place, and the note names the ZIP's market (so a
  contradiction - "Dallas, TX 77002" is a Houston ZIP - shows); a lowercase state code counts only after a comma ("bend, or
  97701" is Bend, OR; "Dallas or 75201" is Dallas or a ZIP). When the bundled data has no such ZIP (a PO-box ZIP has no
  residents) the city and state before it are used instead, if that city exists in that state. A comma right after a ZIP ends
  an address where the text holds several ("Chicago, IL 60601, Chicago, IL 60602" is two addresses). Places before a state and
  ZIP ("Dallas and Houston, TX 77002"), two ZIPs, a ZIP in the middle of a longer text, and any text typed with a geography
  type are left to the other readings: nothing the planner typed is dropped for a ZIP.
* **A state name before a state code** ("Delaware, OH", "Washington, PA", "Kansas, OK") is the town when one by that name
  exists in that state, at any size, with the two states offered as an alternative ("The states Kansas and Oklahoma"): the
  other reading is the one that would be silently huge when wrong. Two state names ("Washington, Oregon") or a state with no
  such town ("Oklahoma, TX") are the states.
* **Suggestions name what was typed.** `replaces` and `drop_inputs` are the text in the box ("Travus Co., TX"), not the tidied
  item, so a click edits that item only; the UI edits by whole items, never by substring.
* **Overlap offers are offers.** A city or ZIP listed beside a county it *may* sit inside (same state, same market, smaller)
  is offered as "Drop X — it may be inside Y"; the bundled data cannot prove containment (Fremont and Berkeley share a market
  with Santa Clara County but are in Alameda County), so it never says so.
* **"All its TV markets"** is offered only for a state that has one homed in it (not NJ, DE, NH or DC).

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
* **Matching never guesses.** A segment's rate is applied only when the match is strong: an exact name or team wording, a
  keyword phrase, or the typed text inside a name. One shared common word ("pet OWNERS" against "small business OWNERS") is
  shown as a suggestion, never applied; neither is a bare role word ("owners", "shoppers") or two segments that fit equally well
  ("insurance": auto or health?). Role and language words never compete with a topic: "mortgage shoppers" means "mortgage",
  "home services in Spanish" means home services; the keywords "shoppers" (Retail Shoppers) and "spanish" (Bilingual
  Speakers) apply only when nothing else in the phrase names an audience ("online shoppers", "spanish speaking"; the role word
  alone is the weaker of the two). A shopper / buyer / seeker word carries over only to an in-market kind of audience (its
  label, the sales team's names or its description say shoppers, buyers, in-market, planning to buy): "Sports Fans" are not
  "sports shoppers", so that phrase offers Sports Fans and applies nothing. A topic held back this way is not replaced by a
  segment that accounts for LESS of the typed words ("college" -> higher education for "college football shoppers"); one that
  accounts for as much is the in-market reading ("student loan shoppers" is Financial In-Market). Only a name typed in full
  holds a topic back, never a one-word overlap. Between keyword matches the one that covers more of the topic words wins
  ("luxury car" over "car shopper"); length only breaks the rest. A keyword closed up of its spaces ("videogames") is found
  when it starts on a typed word, not across two ("car shopper" is not inside "nascar shoppers"). A label may not promise more than its number measures: the
  two segments the independent review caught (a 9% "politically engaged" and a 33% "religious") now say what they are.
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
quick. A busy pool is *not* treated as an outage. This holds after the pool exists too: a pool that has no connection for 2
seconds asks one direct connect (serialised, so one failed probe fails every waiting caller) whether the database is there; if
not, the fail-fast window opens at once instead of every request waiting out `DB_POOL_TIMEOUT`. A probe that answered in the
last 2 seconds speaks for the callers queued behind it, and a server that answers "too many clients" is alive and only full: both
count as a busy pool. The probe is asked at least once however short `DB_POOL_TIMEOUT` is, and a busy pool is waited on for the
rest of the budget in 2-second slices, so a window another caller opens meanwhile ends that wait too. The window is recorded once, by whoever saw the outage first; callers that meet it do not push its
end out, so it closes after 5 seconds even under steady traffic.

A session advisory lock (the bootstrap, the migration runner) is released with `db.release_advisory_lock`: it rolls back first
(an aborted transaction cannot run the unlock) and, if it still cannot release, closes the connection so the server drops the
session and the pool replaces it. Without that, a failed step left the lock on a pooled connection and every later bootstrap
skipped for up to 30 minutes.

**First start on a fresh database.** The products table is filled by the catalog's own first-use seeding, which the product
seed now triggers *before* the first import, so the series find the products they link to; series the startup import skips
(they already have a version) still get their product links refreshed (they are the importer's defaults, not admin choices).
A series extended from another (Netflix from Connect) carries a hash of that distribution in its fingerprint, so a new Connect
export makes a new Netflix version instead of leaving it "unchanged".

## The Constanza front end

* **Launcher + dock** (`app/static/constanza.js`, `constanza.css`): a round character bottom-right opens the dock. The script
  builds its own markup, so the page only links the two files. It reads the product list from `/api/catalog` (every product
  whose Admin → Forecast setting is on, grouped by data channel, with the data basis, unit and - for live sports - the season
  period), resolves the geography as the planner types (`POST /api/constanza/resolve`, debounced), and pulls with
  `POST /api/constanza/estimate`. A pick on a suggestion edits only the item it replaces; "use X only" removes exactly the
  typed items it drops, together with the separator that joined each to the list (and an Oxford comma after it: "A, X, and
  B" -> "A and B"; after a comma only a lower-case and / or / plus is a conjunction, so "Dallas, Plus Ultra" keeps its name). Only a lowercase `or` is a word (OR is Oregon), and text with one comma is a list unless what follows it
  only qualifies the place (a state, a state and ZIP, "D.C.", the country). The "form has changed since this was pulled"
  banner compares the form with the request the result was pulled with, so reopening the dock, or changing a field and putting
  it back, shows nothing.
* **Step 05 cards** keep the per-line "Auto-calculate avails" button (still in `app.js`, because it needs the plan's state) and
  render through the same `Constanza.renderCard`, so both places read alike. A geography the planner picks for a line lives
  only in the session (`state.constanzaGeoOverride`), never in the saved proposal.
* **Live-sports period** is listed on the Step 04 row, the Step 05 card and in the dock; the flight dates are never checked
  against it (the estimate says so).
* **Layout.** The masthead is a two-row grid below 1560px; the canvas grows to 1600px with fluid padding; the Curate table
  turns into wrapped cards at 1400px and below (labels come from CSS, the two that follow the billing period from variables
  `_applyTimeUnitLabels` sets). `tests/test_constanza_frontend.py` pins these contracts and runs the pure helpers in JavaScript.

## Operating notes (Replit)

* Pull → the post-merge hook installs `requirements.txt` (adds `psycopg-pool`), runs the tests and the import. On app start the
  migrations and the first-time load run in the background.
* **Decisions the data owners should confirm** are listed in the pull request: the unit of the Live Sports playoff tabs
  (headed "Estimates"; treated as weekly), the overlap of the new `eDigital Display` / `eDigital OLV` products with the
  existing eDigital Network Display / Video - Pre-roll products, and the Netflix rates in the CSV ($54 / $60) versus the
  catalog ($44 / $54).
* All 30 new products carry a **$40 CPM placeholder** and a "PLACEHOLDER RATE" note until the rate card is confirmed.
