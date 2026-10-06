# Entravision Proposal Builder

Web app that turns a Notion request paste-in into a fully-built AdFlo
proposal workbook (Net + With-Sections + Gross + DOOH + Avails tabs).

Stage 2 deliverable of the TapClicks / AdFlo proposal pipeline. The
catalog, pricing rules, and Excel template are shared with the Stage 1
business-logic spec. The product list lives in the database and is edited in **Admin → Products**
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
deletes them in **Admin → Products**, and every other module — the Excel builder, parser, recommender, generator —
reads that list through `catalog.effective_catalog()`. `CATALOG` in `app/catalog.py` is only the *seed*: the first
time the app finds the table empty it copies that list in (folding in anything the retired `rate_overrides` /
`custom_products` tables held), and it is the read-only fallback if the table can't be reached. Apply `schema.sql`
before first use. A renamed product keeps resolving under its old name (`product_aliases`); a deleted one stays
hidden-but-resolvable so saved proposals still open.

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

| Request type            | Net | wsections | Gross | Avails-Only | DOOH tabs |
|-------------------------|:---:|:---------:|:-----:|:-----------:|:---------:|
| Renewal Proposal        |  ✓  |     ✓     |   ✓   |             |  if DOOH  |
| Proposal page to sign   |  ✓  |     ✓     |   ✓   |             |  if DOOH  |
| Proposal with Avails    |  ✓  |     ✓     |   ✓   |      ✓      |  if DOOH  |
| Full Presentation       |  ✓  |     ✓     |   ✓   |             |  if DOOH  |
| Avails / Estimates Only |     |           |       |      ✓      |  if DOOH  |

DOOH is detected from the `Products selected` line.

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
