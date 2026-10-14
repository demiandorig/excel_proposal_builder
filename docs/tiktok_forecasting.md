# TikTok audience-size forecasting

Constanza's first **live** data connection. The **Tiktok Ads** product is forecast by asking TikTok itself — the same audience-size
estimator TikTok Ads Manager shows while you set up an ad group — how many people match a targeting. Everything else in Constanza
reads avails data that was loaded into the app; this one is asked of TikTok each time (and remembered for ten minutes).

```
planner sets up the audience in the dock ─► /api/constanza/estimate ─► TikTok  POST /ad/audience_size/estimate/
   places · age · gender · languages ·          │                         ◄─ { user_count: { lower_end, upper_end }, user_count_stage }
   interests & behaviors · spending ·           ▼
   income · device · placement · objective    audience (people)  ×  5.5  =  impressions a month   (× months = the flight)
                                              est. uniques = the audience   →  Avails step: frequency = 5.5
```

## What it answers, and what it does not

* **Audience size** is TikTok's own estimate of the TikTok *accounts* (18 and over) that match the targeting, as a range
  (`lower_end` – `upper_end`) plus TikTok's verdict on how narrow it is (*Too narrow / Narrow / Balanced / Fairly broad*, which is
  how big the audience is against everyone TikTok could reach in those places: under 10,000 and under 20% = too narrow; under 20% =
  narrow; 20%–80% = balanced; 80% or more = fairly broad). TikTok leaves out under-18s.
* **Impressions** are **audience × 5.5 a month** — Entravision's planning factor, *not* a delivery forecast from TikTok (real
  delivery depends on budget, bid and creative). A flight of N months is N times the monthly figure. Change the factor with
  `TIKTOK_IMPRESSIONS_PER_PERSON`; the card always says which factor it used.
* **Est. uniques** is the audience itself, so **Insert into Avails** writes monthly impressions, est. uniques and a frequency of
  exactly the factor.

## Setting it up (an admin, once)

1. In [TikTok for Business → API → My Apps](https://business-api.tiktok.com/portal) create (or use) a developer app and have the
   **ad account's owner authorize it**; exchange the `auth_code` for a **long-term access token**
   ([Authentication](https://business-api.tiktok.com/portal/docs?id=1738373164380162)). The token needs read access to the ad
   account's targeting tools and audience estimates.
2. Add two secrets (Replit → Secrets, or `.env` locally — **never in chat, code or a commit**):

   | Secret | What |
   |---|---|
   | `TIKTOK_ACCESS_TOKEN` | the long-term access token |
   | `TIKTOK_ADVERTISER_ID` | the numeric ID of the ad account the estimates are asked through |

3. Open **Admin → Forecast → TikTok connection → Test the connection**. It checks, with the real account: the token and ad
   account; the interest and behavior categories; a keyword search; **TV markets by name**; ZIP codes; a state; a U.S.-wide
   estimate; and a narrow audience. The same run is `python -m app.services.tiktok_diagnose` (add `--all-dmas` to try all 210 TV
   markets and list the ones TikTok names differently; `--json` for a machine-readable answer).

Nothing is ever written to TikTok, and no secret appears in any API answer, log line or error message (the ad account shows as its
last four digits).

If a setting is missing or the token is wrong, the dock says so in plain words and says which setting to fix; the rest of
Constanza is unaffected.

## In the dock

Pick **Tiktok Ads** (group *TikTok · live audience estimate*). The Entravision audience block is replaced by the targeting
Ads Manager offers, the same way it is laid out there:

| Section | What it sets | TikTok field |
|---|---|---|
| Geography (the dock's usual box) | markets, states, ZIPs, counties, cities, "National" | `location_ids`, `zipcode_ids` |
| Age | 18–24 · 25–34 · 35–44 · 45–54 · 55+ | `age_groups` |
| Gender | all · men · women | `gender` |
| Languages | from TikTok's own list | `languages` |
| Interests & behaviors | search, or browse every category: interests, purchase intent, video / creator / hashtag interactions, additional interests | `interest_category_ids`, `interest_keyword_ids`, `purchase_intention_keyword_ids`, `actions[]` |
| Spending power · household income (U.S.) · device (OS, connection) | | `spending_power`, `household_income`, `operating_systems`, `network_types` |
| Placement · objective | TikTok (always) + Pangle · Reach / Video views / Traffic | `placements`, `objective_type` + `optimization_goal` |

The panel refuses, before spending a call, what TikTok refuses: interactions with Pangle as a placement, purchase intent on a Reach
campaign (it offers *Use the Traffic objective*), purchase intent together with additional interests, locations that overlap (the
geography reader already folds a ZIP into a market that is also listed). The result shows the audience (range and TikTok's
verdict), the impressions, what was asked, and the places that were sent to TikTok — and anything that could not be placed,
with the reason, so nothing is dropped silently.

On **Step 05**, the line's *Auto-calculate avails* asks TikTok about the proposal's geography alone (all genders and ages, 18+) and
says so; *Refine in Constanza* opens the line in the dock to set the audience, and *Insert into Avails* writes the numbers.

## Places

TikTok identifies a place by an ID, not a name, so the geography the planner types is read by Constanza's geography reader
(`constanza_geo.py`) and then mapped to TikTok IDs by `app/services/tiktok_geo.py`:

* **National** → the United States (`6252001`, the ID in TikTok's own documentation). **A whole state** ("Texas", "CA, NV") → the
  states themselves (TikTok's state is more than the markets homed in it).
* **TV markets** are found by their words (`/tool/targeting/search/` with `geo_types: DMA`): "Los Angeles" ↔ "Los Angeles CA" /
  "Los Angeles DMA". A market that cannot be told from another (two TikTok markets fit equally) is **not guessed** — it comes back as
  "not placed", with the reason. All 210 markets are checked against four naming conventions in the tests, but **TikTok's real
  market names could only be seen with an ad account**, so the first thing to do after connecting is *Test the connection*: its
  *TV markets by name* line lists any market that did not match (run it with `--all-dmas` for the full list), and that list is
  the whole of what could still need a rule.
* **ZIP codes** → `zipcode_ids` (TikTok's ZIP search, 1,000 at a time). **Counties and cities** are looked up one at a time and told
  apart by state; if that is not unambiguous they are reported, not guessed. **Congressional districts** are not something TikTok can
  target. **Other countries** are found by name.
* TikTok takes at most 3,000 places per estimate and does not accept overlapping places (the U.S. and a state together).

## Safety and limits

* **Read-only**: the audience-size estimate and the targeting catalogs. Nothing is created in the ad account.
* **Calls** are spaced (about 8 a second at most), retried twice with a back-off on timeouts, 5xx answers and TikTok's rate-limit
  codes (`40016`, `40100`, `40133`), and cached: an identical estimate for ten minutes, the category and language lists for twelve
  hours, a market's ID for a day. A planner who pulls again does not spend another call.
* **Errors** are classified from TikTok's return codes (`40105` & co. = the token; `40001`/`40118`/`40125` = not allowed for this
  app or ad account; `40300` = the ad account; `40000`/`40002` = TikTok refused these settings, and its message is shown) and each
  has a plain-language message (HTTP 503 not connected / 502 TikTok trouble / 429 rate limit / 422 settings).
* **If TikTok refuses `REACH` / `REACH`** for an account (its own documentation example pairs the Reach objective with `CLICK`),
  *Test the connection* tries `CLICK` and says to set `TIKTOK_OPTIMIZATION_GOAL=CLICK`.
* The Admin → Forecast table lists the product with the method **TikTok API (live)**; an admin can switch it back to a
  population-share estimate, or give the method to another product (migration `0003` lets the table store it).

## Code map

| | |
|---|---|
| `app/services/tiktok_client.py` | the HTTP client: auth header, retries, caches, return-code classification, `AudienceSize` |
| `app/services/tiktok_geo.py` | Constanza geography → TikTok location IDs, and what could not be placed |
| `app/services/constanza_tiktok.py` | the validated `Targeting`, the estimate request, the audience × 5.5 numbers, the panel's menus |
| `app/services/tiktok_diagnose.py` | the end-to-end connection check (CLI and Admin button) |
| `app/main.py` | `POST /api/constanza/estimate` (routes the product), `GET /api/constanza/tiktok/{status,targeting,search}`, `POST /api/admin/tiktok/test` |
| `app/static/constanza-tiktok.js` | the dock's TikTok panel and result card (registers with `constanza.js` through `registerMethod`) |
| `tests/tiktok_support.py` | an in-memory fake of TikTok's documented API; it also refuses what TikTok refuses (overlapping places, purchase intent on Reach, interactions on Pangle, a wrong token) |

## Still to confirm with a real account

These were built from TikTok's published v1.3 documentation and tested against a fake of it; they could not be run against
TikTok without credentials. *Test the connection* answers each in one click:

1. that the token and ad account can read the targeting tools and ask for an audience size;
2. how TikTok names its TV markets (the matcher is built for the usual conventions and refuses to guess);
3. that `REACH` / `REACH` (the default pair) is accepted — otherwise `TIKTOK_OPTIMIZATION_GOAL=CLICK`;
4. that county and city look-ups find a unique place (US counties are TikTok's `CITY` level; cities' parents may be counties, in
   which case a city is reported as "couldn't tell which" and the TV market or ZIP codes are the way).
