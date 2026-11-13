# Duplicate a proposal for a new client

In **My Proposal History** (Step 01) and in **Admin → Proposal History**, each proposal has a **Duplicate for new client** button. A short form asks for the
basics of the new opportunity (client, flight dates, Notion ID, agency, ...) and makes a fresh **draft** that keeps the products, prices and options of the
original. The copy opens in its own tab on **Step 02**, under a "Copied from ..." notice that lists what to check. Nothing is generated and no AI service is
called: the copy is built by one pure function over the saved state (`app/services/proposal_duplicate.py`).

## What carries over, and what resets

| Carries | Resets |
|---|---|
| Products, prices, the **per-period budget** of every line, add-ons, option letters and names | Client, Notion ID, agency and fee, website, campaign name: **from the form** (the original's are never pre-filled) |
| Billing period, split mode, targeting text (geo, demo, behavioral), goal, language | The pasted request, the strategy brief, roadblocks, emails and deck text (written for the old client) |
| The salesperson, requester and market (shown, editable) | Export-tab choices, per-option date windows (cleared and listed in the notice), Constanza estimates (kept only with "Keep the avails estimates") |
| Hand-typed avails numbers; a hand-made period split when the new flight has the same shape | A hand-made split when the shape differs (the default split is used), combined periods that no longer fit |

Free text that names the old client or agency (line notes, targets, option names) is **cleared and counted** in the notice. Per-period budgets stay, so a
flight with a different number of periods changes the totals: the notice says by how much. Full Flight totals never move.

## Routes

* `GET /api/proposal/{id}/duplicate-defaults`: facts about the original (flight, billing period, size) and the form's defaults. Also the permission probe:
  403 (not yours), 404, 410 (saved before reopening existed) and 409 (no request yet) are shown as a message instead of a form.
* `POST /api/proposal/{id}/duplicate`: body `{request_id, client_name, start_date, end_date, notion_id, agency_name, agency_fee, client_website, requested_by,
  salesperson_email, salesperson_market, request_type, campaign_name, keep_avails}`; `agency_fee` is a **fraction** (the form converts the percent). Answers
  `{proposal_id, created, proposal_title, open_url, wizard_step, duplicated_from, notes: [{code, level, text}], stats}`; 422 names the field in `loc[-1]`.
* The copy is stored with `reopen_state.duplicated_from` (lineage, kept by every later save) and `duplicate_notes` (shown once: the first autosave drops them).
  `GET /api/proposal/{id}/reopen` returns both, and the page draws the notice from them.

Who may copy: the proposal's owner, and admins (the copy belongs to whoever makes it).

## The dialog (`app/static/duplicate-proposal.js` + `.css`)

```js
DuplicateProposal.open({ proposal: { proposal_id, client_name?, notion_id? }, trigger, onDone: (result) => {}, prepare?: async () => "" | "why not yet" })  // -> { close() }
```

Loaded on `index.html` and `admin.html`, built on `ProposalHistory.shell()` and the `.ph-form` primitives; it needs no `app.js` / `admin.js` global. It fetches the
defaults, shows the form (7 fields, 5 more and a switch under "More details"), validates with the server's own messages, sends one `request_id` per dialog
(a double click or a retry after a lost answer finds the first copy: the server derives the copy's id from it), and on success offers **Open the copy** (a link,
new tab) and calls `onDone` once. Escape, the X and the backdrop do nothing while a request is in flight; a backdrop click does not discard typed text.
`prepare` runs first: the planner's page uses it to save the draft that is open in the same tab, so the copy never starts from a stale version.
`DuplicateProposal._pure` holds the helpers (`validate`, `toRequestBody`, `mapServerErrors`, `normalizeNotionId`, `percentToFraction`, `flightHint`).

Step 02 gained a **Notion ID** field (EVC-#####, shared with the pill and Step 01's box), so a copy made before the request exists can get its ID later.

## Deploying

**No schema change and no migration.** Lineage lives in the saved state's JSON; the list reads it with the paged queries of `app/services/proposal_list.py`. Publish
as usual; there is nothing to apply on the database first.

Tests: `tests/test_duplicate_*.py`, `tests/test_proposal_duplicate.py`, `tests/test_proposal_history_*.py` (the DOM ones need Playwright's Chromium and skip without it).
