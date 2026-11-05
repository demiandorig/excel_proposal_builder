# Meta (Facebook & Instagram) audience estimates in Constanza

The three paid **Facebook & Instagram Ads** products (Awareness, Traffic / Conversion, Lead Gen / Calls; the branded-content / Page SKUs are excluded) are forecast by asking
Meta itself, through its Marketing API, how many accounts match a targeting. Meta is a **Constanza method** (`key = "meta"`, [constanza_methods.md](constanza_methods.md)):
it lives in the Constanza dock, which works on its own, **and** on Step 05, where each Meta line shows one generic Constanza block like every other product. This page is the
worked example of a method that **owns its geography** and **saves its estimates**; [tiktok_forecasting.md](tiktok_forecasting.md) is the smaller one.

```
planner (dock, with or without a Step 05 line open)                       Step 05 line
 place · age · gender · language · interests  ──►  POST /api/constanza/estimate  ─►  Meta  GET act_<id>/reachestimate
 (all picked from Meta's own lists)                 { product_name, months, meta: {...} }   ◄─ { users_lower_bound, users_upper_bound }
                                                         │
              LOWER-end audience  ×  4.3 / 5.5 / 8.6  =  impressions a month (conservative / mid = default / upper)
                                                         │  × months = the flight;  est. uniques = the lower end  →  Avails frequency 5.5
                                                         ▼
                 dock result  ·  Step 05 card (saved as availsData[line].forecast_snapshot)  ·  Insert the mid-case into Avails
```

## What it answers, and what it does not

* **Audience size, not avails.** Meta's account-scoped `reachestimate` returns the range of accounts (`users_lower_bound` – `users_upper_bound`) that match a targeting, not what a
  flight can deliver.
* **Impressions are Entravision's planning rule**, shared with TikTok (`app/services/platform_scenarios.py`), taken from the **lower end** of that range:

  | Scenario | Monthly impressions | |
  |---|---|---|
  | Conservative | lower-end audience × **4.3** | the Low |
  | **Mid** | lower-end audience × **5.5** | **the default**: what *Insert into Avails* writes |
  | Upper | lower-end audience × **8.6** | the High |

  A flight of *N* months is *N* times the monthly figure. The lower end is the base on purpose; the upper end of Meta's range is shown as context and is never a base. *Insert* writes the
  monthly mid impressions and the **lower-end audience as est. uniques**, so the Avails step's frequency reads 5.5. Change the multipliers (they must stay in order) with
  `CONSTANZA_AUDIENCE_X_CONSERVATIVE` / `_MID` / `_UPPER`.
* **A lower end of 0** is Meta saying the audience is under its reporting floor, not an error and not an empty audience: the numbers stay what the rule gives (0 impressions), and the estimate's first note says
  that Meta reports fewer than 1,000 accounts and the audience is too narrow to plan on. (A lower end of -1 is Meta giving no figure at all, which is refused with a 422.)
* **Two things the figure is not.** It counts people active on Meta's **default placements** (Facebook, Instagram, Messenger, Audience Network ...), the same as Ads Manager's "Estimated audience
  size", not only Facebook and Instagram. And Meta's **TV markets are Comscore markets** (Meta moved off Nielsen DMAs on 2026-06-22), so a market's footprint can differ from the Nielsen
  market the rest of Constanza uses; the result says so.
* **Nothing about a real campaign beyond the targeting is represented** (exclusions, account restrictions): check those in Ads Manager. Chosen interests / behaviors form **one `flexible_spec`
  group, so the audience is people who match ANY of them**. Hispanic targeting is not applied to a Meta estimate (use a language and Meta's own interests); the result says so when it was asked for.

## The budget cross-check (server side, kept apart from avails)

Beside the scenarios the result shows the retail budget ÷ the retail CPM × 1,000 (Awareness **$12**, Traffic / Conversion **$18**, Lead Gen / Calls **$35**; retail spend is **2× platform
media spend**), and the retail spend each scenario's flight would imply at that CPM. It is a reality check against the line's budget, **not an avails number**.

* It is computed on the server, by `constanza_meta.model_auction`, from two optional fields of the request block: `meta.retail_budget` (the whole flight at retail, dollars) and
  `meta.retail_cpm` (default: the product's planning CPM, `forecast.info.retail_cpm` in the catalog). **No cross-check is ever computed in the browser.**
* From a Step 05 line they come from the line (its full-flight budget and its CPM); standalone, the dock's folded *Budget cross-check* card offers one optional amount. With no budget there is
  simply no `meta.budget_check`; the scenarios do not depend on a budget (an estimate without one is allowed).
* A product that is not one of the three paid SKUs but is mapped to the method in Admin has no planning CPM of its own: the CPM is the one the request gives, or none.
* The Awareness catalog seed and migration `0003` changed the old $13 default to $12 while preserving an admin-edited CPM. A data migration does not run in a published deployment: set the
  rate in Admin → Catalog if it still reads $13.

## In the dock

Pick one of the three SKUs (group *Meta · live audience estimate*). The dock's shared **Where** card is **replaced by Meta's own** (Meta owns its geography; the shared geography text is left
alone and comes back when another product is chosen), the Entravision audience block is replaced by the plugin's cards (`app/static/constanza-meta.js`, styled by `constanza-meta.css`; element
ids `cz-mt-*`), and the cards number on from Where: **1 Where · 2 When** (the dock's) **· 3 Who · 4 Language · 5 Audience · 6 Budget cross-check** (folded, optional).

A **connection banner** (`GET /api/constanza/meta/status`, asked when the product is chosen or when a Step 05 line needs it, at most once per 30 seconds, **never at page load**): *Connected to
Meta* with the ad account's last four digits, or the settings an admin must add, in which case **Estimate audience is switched off with that reason** beside it (and the controls that would only
ask Meta are off too).

1. **Where**: the kind of place (TV market, city, state / region, ZIP, country) and a search over Meta's own geographies (`POST /api/constanza/meta-geos`, debounced, never run on a redraw).
   The proposal's own places are offered as one-click *Search "Los Angeles"* pills, which only run a search - the planner chooses the result; a place is never guessed. The chosen place is a chip with
   its type. Meta estimates one place at a time.
2. **When**: the dock's own card. Opened for a Step 05 line, a Meta pull sends the line's **calendar months** (`caps.lineMonths`): a 22-day flight that touches two months is 2 months here, not the
   dock's rounded 1; editing the flight box hands control back to the box.
3. **Who**: age (18–65+, with presets; sent only when it narrows) and gender (All / Women / Men).
4. **Language** (below), 5. **Audience**: a search over interests, behaviors and demographics (`GET /api/constanza/meta-targeting?q=`, with the audience size beside each hit), chips, and *Browse all*,
   which opens the shared **SegmentBrowser** pop-up (sortable, searchable, multi-select; Interests / Behaviors / Demographics tabs; 50 picks at most across the tabs). A list Meta answers with nothing is
   **never remembered** (a *Retry* is offered). If the pop-up's script did not load, *Browse all* is switched off with the reason shown.
6. **Budget cross-check**: with a line open, read-only "from this line (Step 04): $2,000 over 3 months at a $12 retail CPM"; standalone, one optional field.

What the planner chose is kept **per line** for the page's life (a Step 05 line, or "dock" for the dock on its own): two lines of one product keep separate audiences, *Reset* forgets the line it was
open for, a new proposal forgets all, opening the same line again keeps its workspace, and a line opened for the first time starts from its saved estimate's choices (languages included).

The result: the lower-end audience as the headline with Meta's range; the three scenarios as the metric row (mid highlighted as the default, the flight total when more than one month, the retail spend at
the CPM); what was asked of Meta; the budget cross-check; "About this estimate"; *Copy summary*. With a line open, *Insert into Avails* writes to that line.

## On Step 05

Step 05 draws the **one generic Constanza block** under a Meta line (`Constanza.blockButtons(...)` and `Constanza.renderCard(...)`); `app.js` names no method. A Meta line cannot pull from the
proposal's geography (there is nothing that picks the Meta place), so the planner chooses the audience in the dock with the line open and sees, inserts and recalculates the result on the card.

| State of the line | The card shows | Buttons |
|---|---|---|
| no estimate yet | "Meta needs an audience: choose the place, age, language and interests in Constanza, then insert the result here." (and "Add a budget in Step 04 to see the budget cross-check." when the line has no budget) | **Estimate in Constanza →** (opens the dock for this line) |
| fresh estimate | the badge, the range table (Conservative / Mid · default / Upper per month; the flight total when more than one month), the lower-end audience and Meta's range, "pulled for ..." (place, ages, gender, "language set to Spanish", interests), the retail spend per scenario, the budget cross-check, the language caveat, the notes | **Insert the mid-case (5,500,000 / month) into Avails →**, **Recalculate**, **Change in Constanza** |
| stale estimate | the same plus *Inputs changed — recalculate:* listing why (budget, product, months, geography, target, CPM, "the audience in Constanza changed") | **Recalculate** (primary), **Change in Constanza**; Insert is withdrawn |
| an older estimate entered by hand | the same card, "(entered manually)" in Meta's range line | **Estimate in Constanza**, Insert while fresh; no Recalculate (nothing to repeat) |
| an older budget-only estimate | "Estimate an audience to see the scenarios." and its budget cross-check | **Estimate in Constanza**; no Insert |
| Meta not connected | the card as above | Recalculate is off with "Meta isn't connected yet: an admin needs to add META_ACCESS_TOKEN and META_AD_ACCOUNT_ID" |
| the product's method did not load | "This product's estimate source didn't load. Reload the page; if it still doesn't show, ask an admin." | none |

**Recalculate** sends the line's *current* choices (the dock's workspace for the line when it holds edits that were never pulled, else the saved ones) with the line's current budget, CPM and months, so a
card that says "the audience in Constanza changed" is cleaned by its own Recalculate. After a failed Recalculate the card keeps the saved estimate and says why (no geography help: the proposal's geography was never
what Meta was asked about). Switching a Meta SKU off in Admin → Forecast (or mapping it to another method) hides its card; the saved estimate stays in the proposal and returns with the mapping.

**Insert** writes `max_imps` = the monthly mid, `est_uniques` = the lower end, `frequency` = 5.5 and the derived spend, `geo_resolution = {method: "meta_audience", dmas: [the full Meta place label, e.g.
"Los Angeles · California · US"], ...}` (`["entered audience"]` for an estimate entered by hand), and keeps the snapshot beside them. An *Insert* from an older saved shape passes no new snapshot and leaves the old one exactly as
saved. Inserting a dock result into a line **other than the one it was pulled for** writes the numbers and **removes that other line's own estimate** (it no longer describes the numbers now in its Avails fields).

## The request and the response

`POST /api/constanza/estimate`, for a product whose forecast method is `meta` (the method is read from the product's effective configuration, never from the body):

```jsonc
{
  "product_name": "Facebook & Instagram Ads | Awareness",
  "geo_text": "",                        // not read for Meta (it owns its geography); a data-path caller must still send one
  "months": 3,                           // the dock's When card, or the line's calendar months
  "meta": {
    "geo":        { "type": "geo_market", "key": "803", "label": "Los Angeles · California · US" },   // REQUIRED: picked from /meta-geos (country, region, city, zip, geo_market)
    "age_min": 25, "age_max": 44,        // null when not narrowing (18 and 65+ are Meta's own limits)
    "gender": "female",                  // "all" | "male" | "female"
    "languages": [ { "id": "1001", "name": "Spanish" } ],                      // picked from /meta-languages; at most 25 reach Meta
    "targeting": [ { "id": "6003107902433", "name": "Soccer", "type": "interests" } ],   // picked from /meta-targeting; at most 50, ANY of them
    "retail_budget": 2000.0,             // OPTIONAL: the whole flight at retail, dollars; omitted = no budget cross-check
    "retail_cpm": 12.0                   // OPTIONAL: default = the product's planning CPM
  }
}
```

The response is the **standard estimate** ([constanza_methods.md](constanza_methods.md#the-standard-estimate)) with `method: "meta"`, `geo_method: "meta_audience"`, `geo_confidence: "selected"`, `imps_low / mid / high` = the
conservative / mid / upper **flight** totals, `est_uniques` = the lower end, `hispanic_targeting` all false, and a `meta` block: `audience {low, high, source}`, `monthly {low, mid, high}`, `scenarios` (basis, default, multipliers,
months, monthly, flight, `retail_spend_at_flight`, `media_spend_at_flight`, `est_uniques`), `targeting` (the echo of what was asked), `budget_check` (or `null`), `language_note` (or `null`) and `as_of`.
The model refuses fields it does not know and caps every list; a missing `meta` block or place is a 422 in the planner's words ("Choose where first: pick a place from Meta's list."), a budget outside (0, 1e9] is the
existing budget message. Meta's own refusals come back in plain words with the right status (token → 503, permission → 503, rate limit → 429, refused settings → 422). A message that says something "does not exist" is read as the ad account (a
503 naming `META_AD_ACCOUNT_ID`) only when it is not Meta's "invalid parameter" (code 100): there it can be a place or an interest that Meta withdrew, which is a 422 about the settings.

## What is saved with the proposal

The line's last estimate is `availsData[line].forecast_snapshot = { v: 1, method: "meta", saved_at, request, result, line }` - the request that was sent, the answer, and the line's facts when it was pulled (what
staleness is compared with). It is autosaved, reopened and copied with the line; it is **replaced, never changed in place**; the server keeps it only when it is well formed and at most 100 KB. The Excel writer never
reads it, so a workbook is the same whatever snapshot a line holds.

**Older proposals.** Proposals saved before the move hold `availsData[line].meta_forecast` (still a declared field of `AvailsEntry`, forever). The plugin **adopts it in memory** through `legacy.adopt`: a live pull,
a live pull from before languages existed, an amount typed by hand (`source: "manual"`) and a budget-only snapshot all draw as the card above, stale or fresh by exactly the six conditions the old panel used (retail
budget, product, months, proposal geography, target text, CPM). Nothing is rewritten until the planner estimates again, which stores a native snapshot and removes `meta_forecast`. When both exist the newer
(`saved_at` / `as_of`) is the one drawn (a tab open from before the update can still write the old shape); a snapshot whose method is not the line's current method is kept in the data and never drawn.

## Language targeting

Most Entravision campaigns target Spanish speakers, so the pull takes **languages** too. They come from Meta's own list of locales: `GET /api/constanza/meta-languages` returns `{"items": [{"id", "name"}], "note"}`,
the whole list when `q` is empty (Meta's `search?type=adlocale` with a blank `q` and `limit=1000`, cached 12 hours) or what Meta's search finds for `?q=Spanish` (2–60 characters; regional variants such as
"Spanish (Mexico)" are separate locales). The chosen ones travel in `meta.languages` and go to Meta as the targeting spec's **`locales`**, a list of Meta's locale numbers (up to 25 here; Meta documents 50). No locale id is
kept in the app. The quick picks (*Spanish speakers*, *English*, *Spanish + English*) are **resolved live**: the plugin asks `meta-languages?q=Spanish` and takes every locale Meta lists whose name starts with that word.
When the proposal's or the line's target text names Hispanic, Latino or Spanish-speaking audiences, a pill *This line targets Hispanic audiences — add Spanish?* is offered; it is **never applied silently**.

Languages are **ANDed** with the geography, age, gender and interests, and **several languages are ORed** with each other: Spanish + Spanish (Mexico) in a TV market is the people there whose app is set to either, so the first
language narrows the audience compared with none, and each further language widens it again (a second language never makes the figure smaller than the first alone). **What Meta's "language" is:** the language people have set Facebook or Instagram to, **not who speaks it**: a Spanish speaker whose app is set to English
is not counted, so a Spanish figure can come out lower than the Spanish-speaking population. The dock shows this as a short hint under the language picker (the first sentence, the full text on demand) and says *language set
to Spanish*, never "speaks Spanish", in the result (the text is `language_note` of the estimate, of `GET /meta/status` and of the languages answer). Meta withdrew its Hispanic "multicultural affinity" options from detailed
targeting, so a language plus the geography is how Spanish-speaking audiences are reached; any related interest or behavior is whatever the interest search finds on this account (try "Hispanic", "Latino", "Spanish").

**Unverified, said plainly.** Meta's reference documents the locale search and the `locales` field (locale numbers from that search, up to 50), but its pages do not spell out that `locales` is ANDed with the geography or that
`reachestimate` honours it. That is how Ads Manager's Languages field behaves, and the diagnostics' **Reach estimate · with a language** check is what proves it on the real account (U.S. accounts set to Spanish, printed next to
the plain U.S. figure of the same run; flagged if it is not clearly smaller). Meta's advanced-targeting reference also describes `locales` as for a language that differs from the common language for a location: Spanish in the
U.S. is such a language, English is the common one, so the **English** and **Spanish + English** picks may narrow a U.S. audience less than expected, or not at all. The reference does not say what Meta does with a common
language; the diagnostics' **Reach estimate · English, a common language** line only reports what came back and never fails the run. Run it on the real account before relying on those two picks.

## Interests, behaviors and demographics

`GET /api/constanza/meta-targeting?kind=interests|behaviors|demographics` returns a whole list (Meta's `search?type=adTargetingCategory&class=…`, cached 12 hours; demographics are life events, industries, income, family
status ...); `?q=soccer` returns the interests Meta's keyword search finds (`type=adinterest`) plus the behaviors and demographics whose name or path contains the words. Meta does not return every option to every ad
account and retires options over time, so what is listed is what this account can use today (and demographic options depend on the token's home country). A pick's `type` is the key it has in a targeting spec; the request
model's limit for it is the longest key in `constanza_meta.TARGETING_KEYS`, never a number typed in separately.

## Setting it up (an admin, once)

Both are read-only; nothing is ever created in the ad account.

| Secret | What |
|---|---|
| `META_ACCESS_TOKEN` | a long-lived token (a business **system user's** is best) with the **`ads_read`** permission on the ad account |
| `META_AD_ACCOUNT_ID` | the numeric ad account ID the estimates are asked through, with or without `act_` |
| `META_APP_SECRET` | optional: only if the Meta app has **Require App Secret** switched on (every call then carries an `appsecret_proof`) |
| `META_GRAPH_API_VERSION` | optional, default **v26.0** (the current Marketing API version; a pinned older one stops working when Meta retires it) |

On Meta's side: in **Business Settings → Users → System users** create a system user, give it the ad account (*View performance* is enough), **Generate new token** for a Meta app that has the **`ads_read`** permission, and copy the
ad account's numeric ID from Ads Manager. The app that issued the token needs **Ads Management Standard Access** (Meta's naming moved to *Limited Access* in May 2026) with business verification when it reads accounts beyond your own
business's; a token for an app still in development works only for people with a role on the app.

Then open **Admin → Forecast → the Meta connection card → Test the connection** (the same run is `python -m app.services.meta_diagnose`, `--json` for a machine-readable answer). It checks the token and ad account, geography search,
reach estimates (a country; a TV market with age and gender; an interest; a language), the language search and list, and the three lists, and each failing line says what to fix. One test runs at a time (a second press answers
429): it makes about fourteen Graph calls and Meta rate-limits an account after a burst. **Every press asks Meta afresh**: the test first empties the web process's memory of estimates (ten minutes) and
lists (twelve hours), so a green line is never a remembered one. **No secret appears in any line**: the token, the app secret and the `appsecret_proof` are removed, and the ad account shows as its last four digits
even inside the account's own name (agencies often name an account with its id).

In **Admin → Forecast** the three paid SKUs read **Meta API (live)** by default (no row needed); the two branded-Page SKUs stay off, because their audience is Entravision's own pages. An admin can map the method to another product; the row
then says it describes Meta's ad-account audience and should be used only for a product bought there.

## Not verified against a real account

No ad account existed when this was built; the request and response shapes are Meta's published reference, exercised against a strict fake (`tests/meta_support.py`'s `FakeGraph`). Still to confirm on the real account (the
connection test answers most of it): that `geo_markets` alone satisfies `reachestimate` (Meta's page lists `countries` as required in a targeting spec), that the token can read the lists, which options each account sees, how
`locales` behaves (above), whether a very narrow audience comes back as a lower end of 0 (it is handled, above) and which wording Meta uses when it withdraws an interest or a place, and the Admin connection card and the dock banner against the real credentials. The dock and the Step 05 card were tested in a headless browser against the strict fake; none of it has run against Meta's real API, and the design's whole-app check (Admin → dock → Step 05 → Insert → save → reopen → Excel, in a real browser, once against the real account) is done by hand before the merge is announced.

## Deprecated and removed

* **Removed:** the Meta panel that used to sit in `app.js` on Step 05 (about 1,270 lines between `META PANEL: begin` and `end`), its `state.metaSel` / `state.metaStatus` state and `meta-audience.css`. Step 05 now draws the generic block.
* **Deprecated, kept for one release:** `POST /api/constanza/meta-forecast` (frozen; it is the contract of the old panel and of the older saved shape, and now runs over the same `model_auction`, so both routes give the same numbers) and
  the catalog field `meta_retail_cpm` (the new page reads `forecast.info.retail_cpm`; `audience_multipliers` stays). They exist so that a browser tab opened **before the deploy** keeps working against the new server: its old panel still
  routes by `meta_retail_cpm`. One visible side effect in such a tab only: its old dock lists the three SKUs under "Other products" and a pull answers "Choose where first..." until the page is reloaded. Remove both once no deployed tab can predate
  the change (a day) and a search of the access logs shows no caller of `/meta-forecast`; the read path for `meta_forecast` (`legacy.adopt`) stays as long as saved proposals exist.
* The helper routes keep their original paths (`/meta/status`, `/meta-geos`, `/meta-targeting`, `/meta-languages`): tabs open during the deploy and the plugin use them. New methods use `/api/constanza/<key>/<helper>`.

## After the merge (post-merge steps)

1. **Run the app once in the workspace before Publish** (it applies `migrations/*.sql` and `schema.sql` to the development database) so Publish's schema diff carries migration `0006`, which replaces the method CHECK by a key-format
   check ([forecast_data_platform.md](forecast_data_platform.md), "Deploying"). Until it is published, the three Meta SKUs work by default (no row) and only an admin override naming `meta` fails with "Couldn't save the forecast mapping" -
   including the Admin "On" toggle of a Meta SKU. Duplicating a Meta SKU in Product Catalog still creates the copy, but without the mapping (a plain population-share estimate
   until an admin maps it), and the failure is only in the server log, not on the page.
2. Put `META_ACCESS_TOKEN` and `META_AD_ACCOUNT_ID` (and `META_APP_SECRET` if the app requires it) in the **deployment's** Secrets, not only the workspace's.
3. Open Admin → Forecast → **Test the connection** on the Meta card and read every line; then one real pull in the dock (a market, Spanish) and an Insert.
4. Check the Awareness rate in Admin → Catalog if it still reads $13 (a data migration does not run in a published deployment).
5. Ask the owner the questions the design left open: Step 05 no longer carries the audience form (the planner chooses it in the dock with the line open); an estimate no longer needs a budget; a Meta SKU switched off in Admin hides its card; an
   Insert into another line removes that line's estimate; only Meta's pull uses the line's calendar months (TikTok and the data products still use the dock's rounded days).

## Code map and tests

| | |
|---|---|
| `app/services/constanza_meta.py` | the client (auth header, bounded calls, caches, error classification), `MetaArgs` (the validated `meta` block), `estimate_response`, `model_auction` (scenarios + budget cross-check), `status`, the lists, and the `register(MethodSpec(key="meta", ...))` |
| `app/services/platform_scenarios.py` | the three multipliers and their settings, shared with TikTok |
| `app/services/meta_diagnose.py` | the end-to-end connection check (CLI and the Admin card) |
| `app/services/constanza_methods.py` | the registry, the standard estimate and status ([constanza_methods.md](constanza_methods.md)) |
| `app/main.py` | `POST /api/constanza/estimate` (dispatch), `MetaEstimateModel`, `GET /api/constanza/meta/status`, `meta-geos`, `meta-targeting`, `meta-languages`, the deprecated `meta-forecast`, `AvailsEntry.forecast_snapshot` |
| `app/static/constanza-meta.js` / `.css` | the dock panel, the result, the Step 05 card's middle, `legacy.adopt`; registers with `constanza.js` |
| `app/static/constanza.js`, `app.js` | the host: the dock, and the generic Step 05 block (neither names Meta) |
| `tests/meta_support.py` | `FakeGraph`, a Graph API shaped like Meta's reference |
| `tests/test_constanza_meta.py` | the backend: estimate, lists, errors, scenarios, the budget cross-check |
| `tests/test_constanza_meta_frontend.py`, `test_constanza_meta_dom.py` | the plugin in a JavaScript engine, and in a real browser |
| `tests/test_constanza_step05_block_dom.py`, `tests/fixtures/meta_snapshots.py` | the Step 05 block for a Meta line, with saved estimates of every shape |
| `tests/test_constanza_method_contract.py`, `tests/test_constanza_snapshot_compat.py` | the contract every method meets, and what saved proposals need |
