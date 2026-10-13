# Entravision Proposal Builder

Web app that turns a Notion request paste-in into a fully-built AdFlo
proposal workbook (Net + With-Sections + Gross + Avails tabs, plus the DOOH Deliverables
when a DOOH product is in the plan).

Stage 2 deliverable of the TapClicks / AdFlo proposal pipeline. The
catalog, pricing rules, and Excel template are shared with the Stage 1
business-logic spec. The product list lives in the database and is edited in **Admin → Product Catalog**
(`app/catalog.py` only holds the starting list that seeds an empty table).

---

## Quick start

```bash
cd webapp
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
./run.sh                # serves on http://127.0.0.1:8000
```

Or directly:

```bash
uvicorn app.main:app --reload
```

Open <http://127.0.0.1:8000> and follow the 5-step UI:

1. **Paste** the Notion request text.
2. **Review** the parsed fields — every field is editable; parser warnings
   are listed.
3. **Pick line items** — start from "Suggest mix" or build from scratch.
4. **Avails inputs** — one card per product for max impressions, max spend,
   estimated uniques. Columns N/O/P (or P/Q/R on the Gross sheet) stay
   blank if you skip this step, so the planner can fill them in by hand.
5. **Generate** — preview the tabs that will be built, override if needed,
   download the `.xlsx`. Optionally push to the seller's Google Drive
   folder.

---

## Architecture

```
webapp/
├── app/
│   ├── catalog.py                 # product model + the starting list; the live list is the `products` table
│   ├── excel_template.py          # builds the empty template (Stage 1 generator)
│   ├── main.py                    # FastAPI app + JSON endpoints
│   ├── services/
│   │   ├── notion_parser.py       # text → ProposalRequest dataclass
│   │   ├── dooh_pivot.py          # DOOH Deliverables: Screenlist table columns + the Summary pivot table injector
│   │   ├── proposal_generator.py  # ProposalRequest + line items → xlsx
│   │   ├── recommender.py         # rule-based product mix suggester
│   │   └── drive_uploader.py      # optional Google Drive sink
│   ├── templates/index.html       # single-page UI
│   └── static/{styles.css, app.js}
├── tests/
│   ├── test_parser.py             # both real Notion examples parse cleanly
│   └── test_generator.py          # end-to-end parse→generate, 0 formula errors
├── requirements.txt
└── run.sh
```

### The product list is one freeform list

Products are rows in the `products` table, all equal: an admin adds, edits, duplicates (to make a variant) and
deletes them in **Admin → Product Catalog**, and every other module — the Excel builder, parser, recommender, generator —
reads that list through `catalog.effective_catalog()`. `CATALOG` in `app/catalog.py` is only the *seed*: the first
time the app finds the table empty it copies that list in (folding in anything the retired `rate_overrides` /
`custom_products` tables held), and it is the read-only fallback if the table can't be reached. A renamed product keeps resolving under its old name (`product_aliases`); a deleted one stays
hidden-but-resolvable so saved proposals still open.

### Tables created at startup

Every statement in `schema.sql` that is safe to re-run (`CREATE TABLE/INDEX IF NOT EXISTS`, `ADD COLUMN IF NOT EXISTS`) is applied
when the app starts (`app/services/schema_bootstrap.py`). That is what creates the tables and columns newer features depend on
(disclaimers, the product list, Drive's shared file copy, FAQ rows, hand-added restriction categories), so a deploy never waits on
someone pasting SQL into the database console. Each statement runs in its own savepoint with a short lock timeout; best effort:
if the database user can't create tables, each feature keeps its fail-safe path and the log says so — applying `schema.sql` by
hand still works.

### Process FAQs (Admin → FAQ)

The exports' **Process FAQs** tab is built from the rows in the `faq_entries` table, edited in **Admin → FAQ**: the FAQ table
(SERVICES, Frequently Asked Question, Resource Link Title, Resource Link) and the "Category and product restrictions" block
under it. A row's title is written as a real hyperlink to its link (plain text when it has none); the tab's header always
links to Entravision's Ad Guidelines site. The first admin visit seeds the rows the tab has always had; if the table is empty
or unreachable the export falls back to those built-in rows.

### Restricted Verticals sheet

Roadblocks reads a stored copy of the "Restricted Verticals by Platforms" Google Sheet (`restriction_categories`, filled by
**Admin → Restrictions → Sync**). So Roadblocks judges against the current sheet, a copy an admin already synced from Google is
re-read automatically when it is older than `RESTRICTIONS_AUTO_SYNC_MINUTES` (default 60; 0 turns it off) — at the Step 07
chooser and before each check, through the app's Google Drive connection. A failed refresh keeps the stored copy; an uploaded
workbook and the built-in snapshot are never replaced automatically.

Admins can also add to it directly in **Admin → Restrictions**: **+ Add a category** creates a restricted vertical with its
own rules (guidance the AI should apply, "not accepted on any product", or an approved-products list) and **Extra notes** adds
rules to any category. Step 07 offers hand-added categories as choices, their keywords suggest them, and the Roadblocks AI
reads them like a sheet tab (marked "added in the app"). A sync never deletes them (`restriction_categories.is_manual` /
`extra_notes`); if the sheet later gains a tab of the same name, the sheet's version wins and the notes are carried over.

### Create in Gamma

Step 07's **Create in Gamma** button sends the outline to Gamma's public API from the server (the key never reaches
the browser) and opens the finished deck in a new tab. Set `GAMMA_API_KEY` (Replit Secrets / `.env`; optional
`GAMMA_THEME_ID`, `GAMMA_FOLDER_ID`, `GAMMA_WORKSPACE_ACCESS`). Without a key the button copies the outline and
opens Gamma's own create page instead.

### Why the recommender is deterministic

The mix suggester is a rule-based scorer keyed on the campaign goal and
budget band, not an LLM. Two reasons:

- **Auditability.** Every recommended dollar can be traced to a rule.
- **Billing accuracy.** Catalog minimums and category restrictions are
  hard constraints, not best-effort guidance.

If goal-based rules don't fit, the planner can replace the entire
suggested list before generation.

### What the parser handles

The Fillout/Notion paste has 5 request types — Renewal, Proposal to Sign,
Proposal with Avails, Full Presentation, Avails / Estimates Only — and
the parser routes each to the right set of tabs:

| Request type            | Net | wsections | Gross | Avails-Only | DOOH Deliverables |
|-------------------------|:---:|:---------:|:-----:|:-----------:|:---------:|
| Renewal Proposal        |  ✓  |     ✓     |   ✓   |             |  if DOOH  |
| Proposal page to sign   |  ✓  |     ✓     |   ✓   |             |  if DOOH  |
| Proposal with Avails    |  ✓  |     ✓     |   ✓   |      ✓      |  if DOOH  |
| Full Presentation       |  ✓  |     ✓     |   ✓   |             |  if DOOH  |
| Avails / Estimates Only |     |           |       |      ✓      |  if DOOH  |

DOOH is detected from the products in the plan: a product whose family is DOOH (today
"Digital Out of Home") switches **DOOH Deliverables** on; Step 08 can override it.

### DOOH Deliverables

One export choice builds two sheets, always together:

- **DOOH Screenlist** — an Excel table (`DOOHScreenlist`) with the platform export's 40 columns in a fixed
  order plus two calculated columns, `max_avails` (`four_week_impressions * 0.4`) and `max_spend`
  (`(four_week_impressions * 0.4) * cpm / 1000` — CPM is per thousand impressions), and one sample row. The planner pastes the real inventory
  over the sample row; the table (and its calculated columns) grows with it.
- **DOOH Summary** — a real Excel pivot table over that table: Venue Type > Network with Number of Screens,
  Max. Avails and Max. Spend. It refreshes when the workbook opens (or Data > Refresh All).

openpyxl cannot write pivot tables, so `app/services/dooh_pivot.py` attaches the pivot to the saved file
(`add_dooh_pivot`); it has to be the last thing that touches the workbook. The `/1000` in `max_spend` is the
constant `DOOH_SPEND_DIVISOR` in that module.
The old `dooh_summary` / `dooh_screenlist` tab keys are still accepted (either one means both sheets); the
canonical key is `dooh`.

---

## Google Drive integration (optional)

By default `/api/drive/upload` returns a structured "not configured" stub.
To enable real uploads:

1. Create a Google Cloud service account with Drive API access.
2. Share your root proposals folder with the service-account email
   (Editor permission).
3. Set two environment variables before starting the server:

   ```bash
   export GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json
   export DRIVE_ROOT_FOLDER_ID=1AbCdEfGh...        # Drive folder ID
   ```

   Or drop them in a `.env` file in the `webapp/` directory; `run.sh`
   picks it up.

Folder layout in Drive becomes:

```
<DRIVE_ROOT>/
  <seller_email>/
    Client_Name_<id>.xlsx
```

Files auto-convert to native Google Sheets on upload.

**Reliability.** The generated `.xlsx` is also kept in the `proposal_files` table (created at startup, see `schema.sql`), because on
Autoscale the file written to `data/proposals/` lives on one instance's disk only and the upload can be served by
another. Every Google call retries transient failures (429 / 5xx / timeouts / resets) with bounded backoff; failures
come back as JSON `{detail, retryable, code}` (503 retryable, 404/409/410 regenerate, 424 an admin must fix Drive
access or quota, 500 with a `ref` that is also in the server log) and the page retries retryable ones twice.

---

## API surface

| Method | Path                          | Body                                              | Returns                                   |
|--------|-------------------------------|---------------------------------------------------|-------------------------------------------|
| GET    | `/`                           | —                                                 | HTML UI                                   |
| GET    | `/api/health`                 | —                                                 | `{status, catalog_size}`                  |
| GET    | `/api/catalog`                | —                                                 | Products grouped by family                |
| POST   | `/api/parse`                  | `{notion_text}`                                   | `{request, suggested_tabs, warnings}`     |
| POST   | `/api/recommend`              | `{request, monthly_budget}`                       | `{line_items}`                            |
| POST   | `/api/generate`               | `{request, line_items, force_tabs?}`              | `{proposal_id, filename, summary}`        |
| GET    | `/api/download/{proposal_id}` | —                                                 | The `.xlsx` file                          |
| POST   | `/api/drive/upload`           | `{proposal_id, seller_email}`                     | Drive link or "not configured" stub       |

Full schema is live at `/docs` (Swagger UI) when the server is running.

---

## Tests

```bash
cd webapp
python tests/test_parser.py        # both real Notion examples
python tests/test_generator.py     # full parse→generate, recalc check
```

Both pass with 0 formula errors when libreoffice is available for recalc;
without it, the tests still verify the structure of the generated file.

---

## What's intentionally manual

- **Avails columns** (N/O/P on the Net sheet, P/Q/R on Gross) are left
  blank when no avails input is provided. The planner fills them in.
- **Spanish duplicate tabs** are deferred — out of scope for v1.
- **Wide Orbit reconciliation codes** show up in the business-logic spec
  but are surfaced only as catalog metadata in the app, not as a separate
  workflow.
