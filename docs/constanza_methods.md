# Adding an avails source to Constanza

Constanza is the app's **avails hub**. It answers one question, "how many impressions (and unique IDs) can this product deliver in this place over this
flight?", for every source the team has: loaded platform data (Roku, Amazon, Madhive ...), a population-share fallback, and live ad-platform APIs (TikTok,
Meta). It works on its own (the round character in the corner opens the **dock**, which needs no proposal) and it works inside a proposal (**Step 05**
shows one Constanza block under each Avails line). The product owner's rule for it: *TikTok, Meta and every future avails source are part of Constanza and
part of Step 05, the same way.*

This page is for the next developer who adds a source (Google, YouTube, Pinterest, Snap, a retailer's API ...). Follow the ten steps in
[Track A](#track-a-an-api-backed-method-the-ten-steps) and you will touch **no** line of `app.js`, `constanza.js`, `admin.js`, `admin.html` or `schema.sql`.
A test file ([`tests/test_constanza_method_contract.py`](../tests/test_constanza_method_contract.py)) checks every method, the day it is registered, and
each failure tells you what to add. Worked examples: [meta_forecasting.md](meta_forecasting.md) (the larger one: it owns its geography and saves its
estimates) and [tiktok_forecasting.md](tiktok_forecasting.md) (the smaller one: it reads the proposal's geography).

## Contents

* [The registry table](#the-registry-table) - what exists today
* [Two tracks](#two-tracks) - an API-backed method, or a loaded dataset
* [Track A: the ten steps](#track-a-an-api-backed-method-the-ten-steps)
* [Track B: a loaded dataset](#track-b-a-loaded-dataset)
* Reference: [the registry](#the-registry-in-code) · [the standard estimate](#the-standard-estimate) · [status and the connection test](#status-and-the-connection-test) ·
  [the plugin contract](#the-plugin-contract-constanzacontract) · [what is saved](#what-is-saved-with-a-proposal-persistence) · [Admin](#admin-nothing-to-write) ·
  [secrets](#secrets)
* [The tests you will meet](#the-tests-you-will-meet-and-what-each-failure-means) · [REQUIRE_JS_ENGINE](#require_js_engine) · [Pitfalls](#pitfalls-we-already-paid-for)

## The registry table

One row per registered method. **Add your row when you add your method** (step 9); a test reads this table cell by cell against the code.

| Key | Label | Secrets | Files | Helper routes | Owns geography | Persists | Default products |
|---|---|---|---|---|---|---|---|
| `tiktok` | TikTok API (live) | `TIKTOK_ACCESS_TOKEN`, `TIKTOK_ADVERTISER_ID` | `constanza_tiktok.py` (+ `tiktok_client.py`, `tiktok_geo.py`, `tiktok_diagnose.py`), `constanza-tiktok.js`, `constanza-tiktok.css`; page: [tiktok_forecasting.md](tiktok_forecasting.md) | `GET /api/constanza/tiktok/status`, `GET /api/constanza/tiktok/targeting`, `GET /api/constanza/tiktok/search` | no (reads the proposal's geography and maps it to TikTok places) | no (its Step 05 card is not saved) | Tiktok Ads |
| `meta` | Meta API (live) | `META_ACCESS_TOKEN`, `META_AD_ACCOUNT_ID` (optional: `META_APP_SECRET`, `META_GRAPH_API_VERSION`) | `constanza_meta.py` (+ `meta_diagnose.py`), `constanza-meta.js`, `constanza-meta.css`; page: [meta_forecasting.md](meta_forecasting.md) | `GET /api/constanza/meta/status`, `POST /api/constanza/meta-geos`, `GET /api/constanza/meta-targeting`, `GET /api/constanza/meta-languages` | yes (the planner picks a Meta place; nothing is guessed from Nielsen markets) | yes (`forecast_snapshot`) | Facebook & Instagram Ads \| Awareness, Facebook & Instagram Ads \| Traffic / Conversion, Facebook & Instagram Ads \| Lead Gen / Calls |

The two built-in methods have no row: `platform` (avails loaded from a data series: Roku, Amazon, Netflix, live sports ...) and `generic` (the line's own
budget spread by population). They run through `app/services/constanza_engine.py` and need no registration.

## Two tracks

| | **Track A - an API-backed method** | **Track B - a loaded dataset** |
|---|---|---|
| Example | TikTok, Meta, the next ad platform | a new Roku-style avails export |
| What you write | a service module, a request block, a plugin script, a fake, a docs row | a manifest entry and an import (no code) |
| Where it shows | its own group in the dock, with its own panel; its own card on Step 05 | the dock's usual panel and card, in the data channel it belongs to |
| Guide | the ten steps below | [Track B](#track-b-a-loaded-dataset) |

If the new source is a *file the team sends*, it is Track B. If it is a *live API that answers a question about an audience*, or it needs targeting
controls the platform panel does not have, it is Track A.

## How the pieces fit

```
 planner ──► dock panel (constanza-<key>.js) ──┐                                                  Step 05 line
             builds ONE request block          ▼                                                  ┌─────────────────────────┐
                              POST /api/constanza/estimate  { product_name, months, <key>: {...} }│ app.js asks constanza.js │
                                       │  (the product's forecast method decides who answers)     │ for the buttons + card   │
                                       ▼                                                          └───────────┬─────────────┘
              constanza_methods.get(method) ──► spec.estimate(MethodRequest) ──► the STANDARD ESTIMATE        │ Insert / Recalculate
                                       │            (your service module)         (+ your block under <key>)  ▼
                                       ▼                                                          availsData[line].forecast_snapshot
              dock result  /  Step 05 card  ◄──────────── constanza.js draws both, your plugin words the middle
```

* **The server owns every number** (the scenarios, a budget cross-check, notes, caveats). A plugin formats; it never computes.
* **The browser owns the planner's choices.** A plugin turns choices into one request block and restores them from a saved estimate.
* **Behaviour that differs between methods is a flag** (`caps`) **or a string** the plugin gives the dock - never an `if (method === "x")` in `app.js` or
  `constanza.js`. A test fails if you write one.

## Track A: an API-backed method (the ten steps)

Below, **foo** is the new source (`key = "foo"`). The key is a lower-case word of 2-32 letters, digits and underscores, it is also the prefix of its
files, routes and element ids, and it must not be `platform` or `generic`. Every check of the format (the registry, the browser, a saved estimate, the database) matches the **whole**
key: `"foo\n"`, with a newline on the end, is not a key. **Choose a key that is not an ordinary JavaScript property name** (`display`,
`style`, `search`, `value` ...): the page-script hygiene tests look for the key in code.

### Step 1 - the service module

`app/services/constanza_foo.py`. It holds the client (secrets from the environment, bounded calls, errors with every secret scrubbed out), `status()`,
`estimate_response(req)` and one registration at the bottom. **Nothing may call the network, or read the registry, while the module is being imported** (the registration itself is the one allowed call).

```python
from __future__ import annotations
import os
import requests
from app.services import constanza_methods, platform_scenarios
from app.services.constanza_methods import MethodRequest, MethodSpec

PRODUCT = "Some Catalog Product Name"            # the product this method is the DEFAULT for (a real catalog name)

class FooAPIError(Exception):
    """A safe, user-facing failure (never carries a secret)."""
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code

def status() -> dict:
    """The connection, from names only: no network, no secret value."""
    missing = [name for name in ("FOO_ACCESS_TOKEN", "FOO_ACCOUNT_ID") if not (os.environ.get(name) or "").strip()]
    account = (os.environ.get("FOO_ACCOUNT_ID") or "").strip()
    return {"configured": not missing, "missing": missing, "account": ("…" + account[-4:]) if account else None,
            "setup_hint": "Add FOO_ACCESS_TOKEN and FOO_ACCOUNT_ID in the app's secrets, then press Test the connection in Admin -> Forecast."}

def estimate_response(req: MethodRequest) -> dict:
    """Ask Foo, then answer in the STANDARD ESTIMATE (plus a `foo` block). ValueError = the planner's mistake (a 422 in their words);
    FooAPIError = Foo's trouble (its own status)."""
    lower, upper = fetch_audience(req.args)                       # your client; bounded; scrubs the token out of every error
    monthly = platform_scenarios.monthly(lower)                   # lower end x 4.3 / 5.5 / 8.6 (shared with TikTok and Meta)
    notes = list(req.geo_notes) + ["Foo counts accounts, not delivery: impressions are the lower-end audience x 4.3 / 5.5 / 8.6 a month."]
    if req.hispanic_asked:
        notes.append("Hispanic targeting isn't applied to a Foo audience estimate.")
    block = {"audience": {"low": lower, "high": upper}, "monthly": monthly, "scenarios": platform_scenarios.to_public(lower)}
    return constanza_methods.standard_estimate(
        SPEC, product_name=req.product_name, months=req.months,
        imps={k: v * req.months for k, v in monthly.items()},      # FLIGHT totals (the card divides by months)
        est_uniques=lower, notes=notes, block=block, resolution=req.resolution)

def diagnose() -> list:
    """The checks behind Admin's "Test the connection": a list of constanza_methods.Check (name, ok, detail, warn, seconds)."""
    ...

SPEC = constanza_methods.register(MethodSpec(
    key="foo", label="Foo API (live)", short_label="Foo", description="A live audience-size estimate from Foo's own ad account.",
    kind="api", default_products=(PRODUCT,), owns_geography=False, request_field="foo",
    secrets=("FOO_ACCESS_TOKEN", "FOO_ACCOUNT_ID"),
    estimate=estimate_response, status=status, diagnose="app.services.constanza_foo:diagnose",
    errors=(FooAPIError,), to_http=lambda exc: (exc.status_code, str(exc)),
))
```

Every `MethodSpec` field is in the [table below](#the-registry-in-code). Three rules matter most: `request_field` **equals** the key; `secrets` are environment
variable **names** (never values); `diagnose` is a `"module:function"` **string** (it is looked up when the test runs, which avoids import cycles).

**Import rules.** At import time your module does one thing with the registry: `register(...)`. Do not call `constanza_methods.get()`, `all()`, `keys()` or `ensure_loaded()` while
your module is being imported (they import every method module, including yours, in the middle of its own import), and do not add imports to `constanza_methods.py` (it imports nothing from
the app; that is what keeps the cycle *forecast map → method module → audience → engine → forecast map* from ever closing). Test C0 imports your module first in a fresh interpreter to
prove it is safe.

Checked by: C0 (import order), C1 (registration complete), C2 (no secret in any answer).

### Step 2 - list the module

Add its dotted path to the one tuple in `app/services/constanza_methods.py`:

```python
MODULES = ("app.services.constanza_tiktok", "app.services.constanza_meta", "app.services.constanza_foo")
```

`main.py` calls `constanza_methods.ensure_loaded()` once after its imports; that imports every module in `MODULES`, which registers the methods. A module that
registers but is not listed would exist only when something happened to import it. Checked by: C3.

### Step 3 - the request block

In `app/main.py`, **above** `ConstanzaEstimateRequest`, add the model of the block your plugin sends, and one field on the request, named for the key:

```python
class FooEstimateModel(BaseModel):
    """The settings the planner chose for a Foo estimate (see constanza_foo.estimate_response, which validates them in planner words)."""
    model_config = ConfigDict(extra="forbid")                       # a typo must not quietly become a broader audience
    gender: Optional[str] = Field(None, max_length=10)
    interests: list[str] = Field(default_factory=list, max_length=50)   # cap EVERY list and EVERY string

class ConstanzaEstimateRequest(BaseModel):
    ...
    foo: Optional[FooEstimateModel] = None                          # next to tiktok / meta
```

`ConstanzaEstimateRequest` itself keeps **ignoring** unknown top-level fields (a browser tab opened before a deploy sends the older body). A method that
owns its geography needs no `geo_text`: the request's `geo_text` defaults to `""` and is not read for it. A refused block answers a 422 whose list says *where* and *why*
(`loc`, `msg`, `type`) and **never repeats the refused value**: the app's own handler leaves out every error's `input` and `ctx` (a body can say `NaN`, which the response
could not carry back, and a refused value is not echoed at all), so you write nothing for it. Checked by: C4.

### Step 4 - helper routes

`GET /api/constanza/foo/status` is mandatory: the dock's connection banner reads it and Admin's connection card reads it. Add it in `app/main.py` next
to the other helper routes; it answers `status()` and **never** carries a secret:

```python
@app.get("/api/constanza/foo/status")
def constanza_foo_status() -> dict:
    return constanza_methods.get("foo").status()
```

Any other helper your panel needs (a place search, a list of options) goes under `/api/constanza/foo/...` (or `/api/constanza/foo-...`). The rule is that **every
route a plugin calls starts with its own key**, so a route can always be traced to its method (the dock's shared routes - `estimate`, `resolve`, `interests`,
`interest-taxonomy` - are the exception). The connection test route, `POST /api/admin/constanza/{key}/test`, already exists for every API method. Checked by: C5.

### Step 5 - a strict fake and the sample

Tests never call a real API. Write a **strict, docs-shaped** fake of the API (`tests/foo_support.py`: it refuses what the real API refuses - an unknown field, a
wrong token, a number where the docs say text - and answers in the exact shape the vendor documents), then register it for the contract tests in
`tests/method_fakes.py`:

```python
from tests import foo_support                      # at the top of method_fakes.py, beside meta_support and tiktok_support

def _install_foo(monkeypatch):
    """Patches the network and the environment for ONE test (undone by the monkeypatch): the secrets, a clean cache, the forecast map with no admin rows."""
    from app.services import constanza_foo        # inside the function, as the other installers do (the app is not imported when the file is)
    monkeypatch.setenv("FOO_ACCESS_TOKEN", foo_support.TOKEN)
    monkeypatch.setenv("FOO_ACCOUNT_ID", foo_support.ACCOUNT)
    fake = foo_support.FakeFoo()
    monkeypatch.setattr(constanza_foo.requests, "get", fake.get)
    _offline_forecast_map(monkeypatch)
    return fake

FOO_BODY = {"product_name": "Some Catalog Product Name", "geo_text": "Los Angeles", "months": 2, "foo": {"gender": "female"}}

FAKES["foo"] = MethodFake(sample_body=FOO_BODY, install=_install_foo, hostile_text=HOSTILE,
                          answer=lambda hostile=False: _answer_of("foo", hostile))
```

* `sample_body` is a body of `POST /api/constanza/estimate`; its product must be forecast by *your* method under the fake (a default product of the method, or one
  `install()` maps with an admin row: `monkeypatch.setattr(fm, "load_overrides", lambda: {name: {"product_name": name, "enabled": True, "method": "foo", "platform": None}})`).
* `answer(hostile=True)` must carry `hostile_text` (markup such as `<img src=x onerror=...>`) in the server text a planner could see - a place name, a note - so the
  checks can prove it is shown as text (C9 on the dock's words, with and without `asked.method`; the browser checks on the page). `_plant` in `method_fakes.py` already plants it in the notes of any method; if your answer carries other server
  text (names from the API's own lists), plant it there too.
* **The fake must mirror the vendor's documented shapes**, not the shapes your code happens to expect. A loose fake lets a wrong request pass (see the Pitfalls).

Checked by: C6 (an entry exists and is sound), C7 (the sample is answered with the standard estimate).

### Step 6 - the plugin script

`app/static/constanza-foo.js` registers with the dock (and `constanza-foo.css` styles anything of yours; classes are prefixed `foo-` and the element ids
`cz-foo-`). **No code in `app.js`.** The smallest conforming plugin (the tests run this block):

<!-- minimal-plugin -->
```js
(function () {
  "use strict";
  if (!window.Constanza || typeof window.Constanza.registerMethod !== "function") return;
  window.Constanza.registerMethod("foo", function (ctx) {
    const esc = ctx.esc;                                              // escape EVERY string the server or the planner supplied
    const name = (d) => esc((d && d.product_name) || "");
    return {
      goLabel: "Estimate audience →",                                // the pull button
      channelLabel: "Foo · live audience estimate",                   // the picker group's heading
      sourceLabel: "Live from Foo",                                   // the product's note in the picker
      formHtml: () => '<section class="aud-sec"><p>Foo settings</p></section>',   // the panel, mounted once (no nested .aud-builder)
      bind: () => {},                                                 // wire events here; NO network call
      requestPart: () => ({ foo: {} }),                               // the request block: { <key>: {...} } and nothing else
      context: () => ({ summary: [] }),                               // what was asked, in words
      reset: () => {},                                                // the dock's Reset / a new proposal
      headlineHtml: (d) => `<div class="cz-headline">${name(d)}</div>`,
      bodyHtml: (d) => `<p>${name(d)}</p>`,                           // (d, m, asked): asked.method is null for a restored result whose form changed: draw from d
      aboutHtml: (d) => `<p>${name(d)}</p>`,
      cardHtml: (d) => `<p>${name(d)}</p>`,                           // the Step 05 card's middle; it may be handed a SAVED answer of any shape
      summaryLines: (d) => [String((d && d.product_name) || "")],     // plain text for "Copy summary"
    };
  });
})();
```

Add the tags to `app/templates/index.html`: the `<link>` for the css **after** `constanza.css`, `audience-kit.css` and `segment-browser.css`; the `<script>`
**after** `constanza.js` and `segment-browser.js`. The full list of what a plugin may give the dock, and what the dock promises it, is in
[the plugin contract](#the-plugin-contract-constanzacontract). Checked by: C8 (files and tags), C9 (conformance), C10 (nothing platform-specific in `app.js`), C14 (a corrupt saved
estimate).

### Step 7 - Admin: nothing to write

Admin → Forecast draws its "What it computes" list, its summary cards and a **connection card** (status + "Test the connection") for every registered API
method from `GET /api/admin/constanza/methods`. Open Admin → Forecast and check the select, the summary card and the card appear. Checked by: C11.

### Step 8 - the database: nothing to write

`forecast_product_map.method` is checked against a key *format* (`^[a-z][a-z0-9_]{1,31}$`, migration `0006`), not a list of names, so a new method needs no SQL.
Checked by: C12. (The default mapping of your product needs no row either: it comes from `default_products`.)

### Step 9 - the docs

1. One **row in [the registry table](#the-registry-table)** above: key, label, secrets, files, helper routes, owns geography, persists, default products.
2. `docs/foo_forecasting.md`: what the method answers and what it does not, how an admin sets it up, what is unverified. (Copy the shape of
   [tiktok_forecasting.md](tiktok_forecasting.md).) State honestly what you could not check without a real account.

Checked by: C13.

### Step 10 - run the contract tests

```bash
cd /Users/evc/code/excel_proposal_builder && source .venv/bin/activate
DATABASE_URL=postgresql://fake:fake@localhost/fake python -m pytest tests/test_constanza_method_contract.py tests/test_constanza_snapshot_compat.py -q -p no:cacheprovider
```

Every failure names what is missing. Then run your own method's tests and, in a real browser, open the dock, pick your product, pull, and press Insert.

Both files also run a **generic browser check** for every method (skipped without Playwright and Chromium): the dock offers your product in its own group with its panel and button and pulls; the Step 05 block of a
line of that product does what your `caps` say. Open the dock yourself once as well: pick your product, pull, press Insert.

**If an older test fails.** The registry's own tests (`tests/test_constanza_methods.py`: listing, ordering, `MODULES`) and the Admin summary tests of the forecast map
(`tests/test_constanza_forecast_map.py`) read the registry (`methods.keys()`, `MODULES`, `fm.DEFAULT_API_PRODUCTS`), so a new method needs **no edit** to them: a later method's default products are
counted by the summary tests on their own, and the registry tests only name TikTok and Meta for what is true of those two. What they still pin is the catalog the maps were written for: the
enabled-products sum is `22 + len(NEW_EP) + len(fm.DEFAULT_API_PRODUCTS)` (the 22 original Entravision Plus products, the ones added later, and one per default product of a live method). A method
whose default product is one of those Entravision Plus products would count it twice: that sum, and the `generic` count beside it, are the one place to adjust. The registry tests' import-order list
names today's modules only; yours is covered by C0, which reads `MODULES`. The registry's own tests also register throw-away methods named `echo`, `alpha` and `zebra`, and the contract tests'
negative twins use `dummy`: **never give a real method one of those keys.**

## Track B: a loaded dataset

A source that is a **file** needs no method and no plugin. Add the series to `app/data/forecast/manifest.json`, import it, and link its products in
`forecast_series_product` ([forecast_data_platform.md](forecast_data_platform.md), "Adding or refreshing a product's avails"; validated by the import rules and
`tests/test_forecast_manifest.py`). It is then a `platform` series: it uses the platform panel and the platform Step 05 card. If the dataset needs its **own targeting
panel or its own estimate**, register it exactly like Track A with `kind="dataset"` (no secrets or diagnose function are required; its `status()` reports the
series versions that are loaded).

## The registry in code

`app/services/constanza_methods.py` (it imports nothing from the app).

**`MethodSpec`** (frozen) - what you register:

| Field | What it is |
|---|---|
| `key` | The method's key: `ForecastConfig.method`, the database value, the request field, the file prefix. |
| `label` | Admin's "What it computes" option, e.g. `Meta API (live)`. |
| `short_label` | Admin's summary card and the picker group, e.g. `Meta`. |
| `description` | One sentence in plain words for Admin. |
| `kind` | `"api"` (needs `secrets` and `diagnose`) or `"dataset"`. |
| `default_products` | Catalog names this method is the **default** of (a renamed product is resolved through its old names). A product with no admin row uses it. |
| `owns_geography` | `True`: the dock hides its shared Where card, the request's `geo_text` is not read and nothing is resolved for it (the plugin must then have `whereHtml` and `context().geo`). |
| `request_field` | Must equal `key`: the field of `ConstanzaEstimateRequest` that carries the block. |
| `secrets` | Environment variable **names** (never values), shown in Admin and in "not connected" messages. |
| `estimate` | `estimate(req: MethodRequest) -> dict`: returns the standard estimate. Raises `ValueError` (a 422 in the planner's words) or one of `errors`. |
| `status` | `status() -> dict`: the [standard status](#status-and-the-connection-test). No network, no secret value. |
| `diagnose` | `"module:function"` returning `list[Check]`: Admin's "Test the connection". Required for `kind="api"`. Ask the platform afresh (empty your caches first). Never put a secret in a `detail`: your own function redacts what only it knows (Meta: `constanza_meta.redact`), and the registry is only the **safety net** under it (`redact_secrets`, over every `Check.detail`: see [Secrets](#secrets)). |
| `errors` | The exception types `estimate` raises for upstream trouble. |
| `to_http` | `to_http(exc) -> (status, detail)` for those errors (default: 502 with the error's text). |
| `product_info` | Optional `product_info(name) -> dict`: per-product facts merged into the catalog as `forecast.info` (Meta: the planning CPM). It runs inside the catalog every planner session loads, so it must answer an object that is plain JSON. One that raises, or answers anything else, only costs **its product** the `info` (a warning is logged): the catalog never fails for it. |
| `status_route` | Optional; default `/api/constanza/<key>/status`. |

**`MethodRequest`** (frozen) - what `estimate` receives: `product_name`, `months` (1-120, already checked), `flight_days` (or `None`), `args` (the request block as a
dict, `{}` when the planner sent none), `resolution` (the resolved shared geography; `None` when `owns_geography`), `geo_notes` (warnings about that geography, or `[]`),
`hispanic_asked` (the planner asked for Hispanic targeting: say "not applied" if you cannot do it).

**`Check`** - one line of a connection test: `Check(name, ok, detail, warn=False, seconds=0.0)`; `to_public()` is what Admin shows. One type for every method.

**Functions**: `register(spec)` (validates: key format, not a built-in, `request_field == key`, a diagnose function for an API method, unique key and default
products), `get(key)`, `all()`, `keys()`, `method_keys()` (built-ins first), `default_for(product_name, variants)`, `run_diagnose(key)` (the checks, every `detail` passed through
`redact_secrets`), `redact_secrets(spec, text)`, `connection_test(key)` (one test at a time per method), `standard_estimate(...)`, `validate_standard(spec, out)`,
`validate_status(spec, status)`, `ensure_loaded()` and `unregister(key)` (for tests only).
**`MODULES`** is the one tuple a new method adds its module path to. `BUILTIN_METHODS = ("platform", "generic")`; `KEY_RE` is the key format (always test a key with
`KEY_RE.fullmatch(key)`: Python's `$` also matches before a final newline, so `KEY_RE.match("meta\n")` would let it through).

## The standard estimate

Every method answers `POST /api/constanza/estimate` in the same shape, so the dock and the Step 05 card read all of them alike. Build it with
`constanza_methods.standard_estimate(spec, *, product_name, months, imps, est_uniques, notes, block, resolution=None, geo_method=None, geo_confidence=None,
confidence="live_api", unmatched=())`; the route then runs `validate_standard`, which is **strict** (nothing is filled in: a default impression count would be a silently
wrong number).

| Key | Meaning |
|---|---|
| `method` | your key |
| `product_name`, `platform` (= the key), `confidence` (`live_api`), `geo_method`, `geo_confidence` | provenance |
| `months` | the flight length the numbers are for |
| `imps_low`, `imps_mid`, `imps_high` | **flight totals** (monthly × months), `low <= mid <= high`; the card divides by months. The route accepts a mid of 0, but the dock and the Step 05 card offer **no Insert** unless the monthly mid (`imps_mid / months`) is a finite number above 0 (see [persistence](#what-is-saved-with-a-proposal-persistence)) |
| `est_uniques` | the lower-end audience for audience-size methods (so the Avails frequency is the mid multiplier, 5.5), or `null` |
| `reach_universe`, `series`, `weeks` | `null` for an API method |
| `notes` | list of plain-language strings (the card shows four, the dock all) |
| `unmatched_geo_tokens`, `unmatched_geo_details`, `ambiguous`, `geo_interpretations`, `geo_suggestions`, `by_area` | lists (`[]` when the method owns its geography) |
| `hispanic_targeting` | `{default, on, applies}` booleans (all false for a method that cannot apply it) |
| `<key>` | **your block**: by convention `audience {low, high}`, `monthly {low, mid, high}`, `scenarios`, an echo of what was asked, `budget_check` or `null`, `as_of` |

For an audience-size source the rule is shared: `platform_scenarios.monthly(lower)` gives the three monthly figures, **lower end × 4.3 (conservative) / 5.5 (mid, the
default and what *Insert* writes) / 8.6 (upper)**, from the lower end of the platform's range (the upper end is context, never a base).

## Status and the connection test

`status()` answers `{"configured": bool, "missing": [str], "account": str | None, "setup_hint": str, ...extras}`. Each `missing` entry **starts with the secret's name**
(an optional explanation may follow: `META_ACCESS_TOKEN (it has a space ...)`), `account` is **at most the last four digits** of an account id, and nothing in it is a secret
value. It makes no network call and works with no secret set. The same answer feeds the dock's banner (`GET /api/constanza/<key>/status`), Admin's connection card
and `POST /api/admin/constanza/<key>/test` (`{ok, checks, status}`).

`diagnose` returns a `list[Check]`: settings first (stop there if nothing is set), then one check per thing that must work against the **real** account, each failing check saying
what to fix. Run it once with the real credentials before announcing the method. Admin runs **one test at a time per method** (a second press answers 429), because ad
platforms rate-limit an account after a burst.

## The plugin contract (`Constanza.CONTRACT`)

`Constanza.registerMethod(key, factory)` in `constanza.js` calls `factory(ctx)`, **validates** the object it returns against `Constanza.CONTRACT`, and returns `true`,
or `false` with a console error saying why (a typo in a member name, a missing member, caps that need a member that is not there). A refused plugin never takes the
dock down, and a product whose method is neither built in nor registered is left out of the picker and says on Step 05 that its source didn't load.

Besides a bad key, a factory that throws, an unknown or missing member and a member of the wrong type, it **refuses**:

* a `legacy.key` that is not a lower-case field name ending in `_forecast` (like Meta's `meta_forecast`; never `forecast_snapshot` itself). The page deletes that field of the line
  every time a new estimate is filed, so a key such as `max_imps` would wipe a figure of the line and `forecast_snapshot` would wipe the estimate that was just saved;
* a cap name that is not **one of the four in `Constanza.CONTRACT.caps`**. The host asks whether the name is a property of that object itself, so a name every object happens to
  have (`toString`, `constructor`) is refused like any typo;
* `caps.ownsGeography` with the default `autoPull` (`"geo"`): "Auto-calculate from the proposal's geography" could never work for a method that does not ask about it. Such a
  method sets `autoPull` to `"saved"` or `"none"`.

**Required members:** `formHtml`, `bind`, `requestPart`, `context`, `reset`, `headlineHtml`, `bodyHtml`, `aboutHtml`, `cardHtml`, `summaryLines`.

**Optional members:** `goLabel`, `onSelect`, `onLine`, `canPull`, `goState`, `onError`, `onResult`, `onProposalReset`, `errorText`, `badgeHtml`, `geoInfo`,
`whereHtml`, `channel`, `channelLabel`, `sourceLabel`, `rangeLabels`, `caps`, `cardRequest`, `canAutoPull`, `cardEmptyHtml`, `applyLabel`, `staleReasons`,
`choicesChanged`, `restore`, `connection`, `legacy`, `snapshotLine`, `_pure` (a test hook: pure helpers exposed for the JavaScript tests).

| Member | Called when / receives -> returns |
|---|---|
| `formHtml()` | once, to draw the panel in the dock (after the shared When card). Ids `cz-<ns>-...`; escape every string with `ctx.esc`; no nested `.aud-builder` |
| `bind(ctx)` | once after mount: wire events. **No network call here** (it runs at page load for every user) - fetch on `onSelect` |
| `requestPart()` | every time the dock builds a request: `{ [key]: block }` and nothing else, deterministic (sort ids). The host reads **your own key only**: any other member you return (a `product_name`, `months`, `geo_text` of your own) never reaches the request, and a throw, or a result that is not an object holding your key, adds nothing |
| `context()` | at pull time: `{ summary: string[], geo?: string }` - what was asked, in words. **`geo` is required when `caps.ownsGeography`** (the host shows it as the place of the result). It is also read for a **restored** result, but only while the form holds the saved choices again - otherwise `asked.method` is `null` (see [What the host does](#what-the-host-does)) |
| `reset(prevLid)` | the dock's Reset and a new proposal; `prevLid` is the line the dock *was* open for (`null` for none) |
| `headlineHtml(data, m)`, `bodyHtml(data, m, asked)`, `aboutHtml(data)` | the dock's result: headline, middle, "About this estimate". `asked` is `{ geo, areas, method }`: `method` is what `context()` said when the result was pulled, **or `null`** (a restored result whose form has changed; a `context()` that threw). Describe the result from `asked.method` when it is there and from the answer's own echo when it is not |
| `cardHtml(data, m, cardOpts)` | the Step 05 card's method part; `cardOpts = { line, stale: string[], fromSnapshot }`. It may be handed a **saved** answer of any shape: tolerate missing and mistyped fields |
| `summaryLines(data, asked)` | the lines of "Copy summary" (plain text, not HTML); `asked` as above, so it must work with `asked.method === null` too |
| `goLabel`, `channelLabel`, `sourceLabel`, `channel`, `rangeLabels` | strings: the pull button, the picker group heading (the host prefixes the group key `m:`, so it can never collide with a data channel), the product note, an optional group key, and three words for Low / Mid / High (`["Conservative", "Mid · default", "Upper"]`) |
| `onSelect(product)` | the method's product became the dock's product (after the line is set, so `ctx.line()` is current) |
| `onLine(line \| null)` | the dock was opened for a line (or the same one again, or none). Without it the host calls `reset(prevLid)` |
| `canPull()`, `goState()` | before a pull: `{ok, reason}` (a local refusal); and `{enabled, reason?}` to keep the button off with the reason shown (Meta: not connected) |
| `onError(status)`, `onResult(data)`, `onProposalReset()` | after a failed pull, after a good one, and when a new proposal begins |
| `errorText(data, fallback, body)` | the planner's words for a refused request (default: `ctx.errorText`) |
| `badgeHtml(data)`, `geoInfo(data)` | the source badge on a result; the object stored as `entry.geo_resolution` on Insert (`{method, dmas, unmatched_tokens, win_rate, hispanic_share}`) |
| `whereHtml()` | required when `caps.ownsGeography`: the method's own Where card, a bare `<section class="aud-sec">` |
| `caps` | see below |
| `cardRequest(lid, line, snapshot)`, `canAutoPull(line, snapshot)` | for `autoPull: "saved"`: the block *Recalculate* sends (the line's current facts), and whether Recalculate is offered |
| `cardEmptyHtml(line, snapshot)`, `applyLabel(m)` | what the card says before an estimate exists; the Insert button's words |
| `staleReasons(snapshot, line)`, `choicesChanged(lid, block)` | reasons beyond the generic ones; whether *that line's* choices differ from the snapshot's (an order-free compare; false when no workspace exists) |
| `restore(block)`, `connection()`, `legacy`, `snapshotLine(data, line)` | put a saved request's choices back; `true/false/null` = connected / not / unknown; `{ key, adopt(entry, line) -> Snapshot }` for an older saved shape (`key` must be a lower-case field ending in `_forecast`); the budget and CPM an answer was really computed with |

**`caps`** (flags the host reads; the defaults are `{ ownsGeography: false, persist: false, autoPull: "geo", lineMonths: false }`):

| Flag | Meaning | Needs |
|---|---|---|
| `ownsGeography` | the method picks its own places: the shared Where card is hidden, `geo_text` is `""`, `context().geo` says where | `whereHtml`, `context().geo`, and `autoPull` set to `"saved"` or `"none"` (the default `"geo"` is refused) |
| `persist` | the estimate of a Step 05 line is saved with the proposal and drawn again on its card | `restore`, `choicesChanged` |
| `autoPull` | Step 05: `"geo"` pulls from the proposal's geography (data products, TikTok); `"saved"` repeats the saved request (*Recalculate*); `"none"` has no pull button on the card, the dock is where it is asked | `"saved"` needs `cardRequest`, `canAutoPull` |
| `lineMonths` | a pull for a Step 05 line sends the line's **calendar months**, not the flight box's rounded days | nothing |

**`ctx`** (what the dock hands the factory; use these, not the page's globals): `S` (the dock's state), `$`, `esc`, `num`, `compact`, `api`, `markStale`, `errorText`, `setMood`,
`announce`, `summarize`, `productByName`, `requestBody`, `targetsHispanic`, `line`, `proposalGeo`, `snapshot`, `refreshGo`. `ctx.api(path, body?)` is the only way out to the
server (a GET, or a POST of `body`): never `fetch`. Call `ctx.refreshGo()` when a connection answer has just come back (Meta: its status check): the dock judges the pull button again and the
page redraws the Step 05 cards that read the connection (exactly which, under [What the host does](#what-the-host-does)). `ctx.announce(text)` says something in the dock's polite status line.

**LineFacts** (`ctx.line()`, the facts of the Step 05 line the dock is open for; `null` for the bubble or a line that is gone - behave as standalone):
`{ id, product_name, method, label, months, flight_days, has_dates, geo_text, target_text, budget, cpm, hispanic }` (`budget` = the full-flight dollars; `months` = the
calendar months the flight touches).

**Snapshot** (one shape for every persisting method): `{ v: 1, method, saved_at, request, result, line: { product_name, method, months, geo_text, target_text, budget, cpm } }`.
The host builds it (`Constanza.makeSnapshot`) when it files a pull and **stamps `saved_at` itself** with the time of that pull, so a plugin never writes one and a newer estimate always carries a
different one. The host relies on that: it tells a Step 05 card whether the dock's form differs from the saved estimate only when something changed, and counts a new snapshot (the card's own
*Recalculate* replaced the old) as a change again, by comparing `saved_at`. The one snapshot a plugin does build is what `legacy.adopt` returns for an older shape: give it the older
shape's own time in `saved_at` (or `null` when it has none), because the page keeps the **newer** of the older and the native snapshot.

### What the host does

It runs the factory in a `try`, validates the result, and wraps **every** call it makes into the plugin while drawing: a plugin that throws on a saved
estimate leaves the method's empty state and "The saved estimate couldn't be read - estimate again", never a blank page. A plugin never needs to defend the dock, but it must
draw any saved answer without throwing and escape every string. The rules that decide what a plugin sees and what it can rely on:

* **A restored result is described by what it was pulled with.** When the dock opens for a line with a saved estimate, the host calls `restore(block)` and then `choicesChanged(lid, block)`.
  Only when `choicesChanged` answers `false` (the form holds the saved choices again) does it call `context()` for the result. A method may leave a workspace the planner already edited alone, and `context()` would then
  describe their unpulled edit, so the saved numbers would be labelled with choices they never had. Otherwise `bodyHtml` and `summaryLines` receive
  `asked = { geo: "", areas: [], method: null }` (the card header names no place either) and must fall back to the answer's own echo, as Meta does; the dock's banner says what changed.
  C9 runs both functions that way on the fake's answers.
* **The dock and the Step 05 cards tell each other what changed.** The dock calls `window.constanzaHost.onDirty(lid, dirty)` when its form starts or stops differing from that line's saved
  estimate (once per change), and the page redraws that line's block. `ctx.refreshGo()` calls `onDirty(null)`: something every card reads changed (a connection answer). That redraws only the
  blocks that read a connection or a saved estimate. It **leaves alone** a line whose estimate is on its way, a block whose method has `autoPull: "geo"` (data products, TikTok: their card reads
  neither), and a card that shows a pulled estimate with no snapshot saved behind it (a redraw would find nothing to draw it from and lose it). So one method's connection answer (Meta's) can
  never wipe a pulled Roku or TikTok card.
* **Insert needs a real monthly number.** `applyConstanzaEstimate` writes nothing and returns `false` when the line is gone or when the monthly mid is not a finite number above 0, and the card
  and the dock draw no Insert button for such a result, so a hand-edited zero cannot replace the figures the line holds. A saved result with no monthly figures at all (no readable `imps_*`) is
  not drawn on the card: it says "The saved estimate couldn't be read - estimate again" and offers *Estimate in Constanza*.
* **Screen readers are told, for every method.** The dock's status line (`#cz-status`, polite) says **once** when a result goes out of date and once when it matches the choices again, not on
  every further edit; the page has one polite live region (`#constanza-live-announce`, outside the cards, which are rebuilt with the grid) that says when a Step 05 estimate is ready, and after an
  Insert. A plugin announces nothing of this itself (`ctx.announce` is for what only it knows). In the shared audience box, a `source_url` becomes a link only when it is an `http://` or
  `https://` address, whatever else a saved result says stays text: do the same for any link of your own, and `ctx.esc` it.

### Lifecycle (`Constanza.open`)

mount → line switch (`onLine(line)` for a method that has it, else `reset(prevLid)`) → the catalog is ready → select the product (`onSelect`) →
restore the line's saved estimate (`restore`, then the result - described with `context()` only if the form holds the saved choices again, else with `asked.method` `null` - then a banner
if the line changed or the form differs). A pull reads the line's facts **before** the request and files the answer under the line only if it is still in the proposal and still holds the
product that was asked about.

## What is saved with a proposal (persistence)

* The last estimate of a Step 05 line is `availsData[lid].forecast_snapshot` (autosaved, reopened, copied with the line). **One generic field for every method.** `AvailsEntry` has
  exactly this field and the older `meta_forecast` (read-only; Meta's plugin adopts it in memory through `legacy.adopt` and never rewrites it until the planner estimates again).
  **Never add a `<key>_forecast` field**: a test fails with "store the snapshot in forecast_snapshot".
* The server keeps a snapshot only if it has an integer `v` (not `true` / `false`), a `method` matching the key format, `request` and `result` that are objects or null, and is at most 100 KB and valid JSON
  (no NaN); anything else is dropped to `null` instead of failing the save.
* A snapshot is **replaced, never changed in place** (a copied option shares the object). The database returns JSON objects with their keys re-ordered, so compare with
  `Constanza.canonicalJson`, never `JSON.stringify`.
* The Excel writer reads only the numeric and text fields of an entry (`max_imps`, `est_uniques`, spend, ...), so a snapshot cannot change an export. *Insert into Avails*
  writes `max_imps` (the monthly mid), `est_uniques` (the lower end), `frequency`, `max_spend` and `geo_resolution`, and keeps the snapshot beside them. It is offered only for a monthly
  mid that is a finite number above 0 (see [What the host does](#what-the-host-does)).
* Staleness is generic: the product, months, the proposal's geography, the line's target, the budget (more than 1 cent apart) and the CPM are compared with the snapshot's `line`;
  the method adds `staleReasons` and `choicesChanged`. A stale estimate withdraws its *Insert* button; *Recalculate* sends the line's current facts. The card's banner reads
  *Inputs changed — recalculate: the budget changed, the product changed.* (a colon, then the reasons separated by commas, and a full stop); the dock's own banner says *The line changed since
  this was pulled (…) — pull again to update.* or *The form has changed since this was pulled — pull again to update.*

## Admin: nothing to write

`GET /api/admin/forecast-map` and the light `GET /api/admin/constanza/methods` (registry only, no database) carry `methods[]` with `key`, `label`, `short_label`, `kind`, `builtin`,
`description`, `secrets`, `owns_geography`, `status_url`, `test_url`, `default_products` and `summary_text`; the "What it computes" select, the summary cards and one connection card per
API method are drawn from it by `admin.js`. An admin can map a method to a product that is not one of its `default_products`; the row then says what the estimate describes.

## Secrets

* Secrets are **environment variable names** in the spec and **values** only in the deployment's Secrets (Replit → Secrets, or `.env` locally); never in chat, code, a test or a commit.
  Tests plant obviously fake values.
* No secret value is ever in an answer, a log line or an error: scrub tokens and account ids out of every upstream message; show an account as its last four digits. Test C2 plants values
  and searches every answer. A refused request never echoes what was refused (a 422 carries no `input` or `ctx`).
* The connection test has two layers. Your `diagnose` function redacts what only it knows (Meta: `constanza_meta.redact` turns the token into `[token]`, the app secret into `[secret]`, the
  `appsecret_proof` made from them into `[proof]` and the ad account into its last four digits, wherever its digits stand), and **should still never put a secret in a `detail`**. Under it,
  `run_diagnose` passes **every `Check.detail`** through `constanza_methods.redact_secrets(spec, text)`: for each name in the spec's `secrets` it takes the value out of the text (an all-digit
  value, an ad account with or without `act_`, is left as its last four digits; any other value becomes `[NAME]`; a value of four characters or fewer is left alone, because it would be found
  inside ordinary words and numbers). It is a safety net for the one thing every method declares, because a platform's own text (an account named with its own id) goes into those lines.
* Calls are read-only, bounded (timeouts, a cap in flight) and cached where the platform allows.

## The tests you will meet, and what each failure means

`tests/test_constanza_method_contract.py` is parametrised over the registry, so a method is checked the day it registers. Each check is a function that returns the problems it found; each has a
**negative twin** (a deliberately broken dummy method) proving it can fail.

| Check | What it enforces | A failure says |
|---|---|---|
| C0 | each module imports cleanly as the first app import of a fresh interpreter; the registry still answers | a cycle: see the Import rules in step 1 |
| C1 | key format (the whole key: no trailing newline), `request_field == key`, labels, secrets as names, `diagnose` resolves, the standard status, the derived lists | what field of the registration is wrong |
| C2 | no secret value in the status, Admin payloads, the connection test, or an estimate (answered or refused) | which answer leaked which secret |
| C3 | every module that registers is in `MODULES` | add the module to `MODULES` (step 2) |
| C4 | `ConstanzaEstimateRequest.<key>` is a typed field, `extra="forbid"` all the way down, every list and string capped | the model or field to fix (step 3) |
| C5 | `GET /api/constanza/<key>/status` exists; a plugin's routes start with its key and exist; the plugin uses `ctx.api` only | the route to add or rename (step 4) |
| C6 | `tests/method_fakes.py` has a sound entry | `constanza_foo is registered but tests/method_fakes.py has no entry for "foo"` (step 5) |
| C7 | the sample request is answered with the standard estimate, as JSON | the missing or malformed key |
| C8 | `constanza-<key>.js/.css` exist, linked in the right order; no orphan files | the file or tag to add (step 6) |
| C9 | the real `constanza.js` accepts the plugin; only contract members; one request block; text is escaped; `bodyHtml` and `summaryLines` still draw a **restored** result with no `asked.method` (they must not throw, print `undefined` / `NaN` / `null`, or leave the planted hostile text unescaped) | the member to rename, the string to escape, or the `asked.method` read to guard (fall back to the answer's own echo) |
| C10 | `app.js`'s marked Constanza section and the Avails grid name no method in code (a comparison, `.key` on a result, `data-key-`, `_keyX`, the route, or the key written out as a string or an object key); `constanza.js`, `admin.js` and `admin.html` never write a key out as a string | move the method-specific code into the plugin (an exact value that is not a branch goes in `LITERAL_ALLOWED` with its reason) |
| C11 | `methods[]` in both Admin payloads; a test route per API method; no method spelled out in `admin.html` / `admin.js` | what Admin hard-codes |
| C12 | the database checks a key format; `AvailsEntry` has the one snapshot field | "store the snapshot in forecast_snapshot" |
| C13 | this guide has your row, the ten step headings, the example plugin conforms, and `docs/<key>_forecasting.md` exists | the row or page to add (step 9) |
| C14 | a corrupt or hostile saved estimate never throws out of a card or draws raw markup | the field to read defensively or escape |
| C15 | `platform` / `generic` cannot be registered; a plugin's group cannot collide with a data channel; a throwing factory, a plugin with an unknown member or a bad key is refused | what the registry or host lets through |

The rules the host applies to every plugin (the refusals under [the plugin contract](#the-plugin-contract-constanzacontract), what `requestPart` and `context` are read for, restoring a saved estimate,
Insert, the dock and the cards telling each other what changed, the announcements) are pinned with a probe plugin in a real browser by `tests/test_constanza_host_contract_dom.py` and, for the Step 05
block, `tests/test_constanza_step05_block_dom.py`. They run without your plugin, so they fail only when the host itself changes.

`tests/test_constanza_snapshot_compat.py` pins what saved proposals need: an entry round trip through the draft route with the older and the current shape, a workbook that does not change
whatever snapshot an entry holds, and the cases listed at its top.

## REQUIRE_JS_ENGINE

The plugin checks (C9, C14, C15 and the guide's example) run the real `constanza.js` in a JavaScript engine: Node when installed, else macOS's JavaScriptCore (`osascript`). Where neither
exists those tests **skip**, and a skipped check enforces nothing. `REQUIRE_JS_ENGINE=1` turns the skip into a **failure**. There is no CI configuration in this repository yet; **when CI
is added it must set `REQUIRE_JS_ENGINE=1`** on a runner that has Node (or is macOS). The tests honour it exactly as `tests/test_constanza_meta_frontend.py` does.

```bash
REQUIRE_JS_ENGINE=1 DATABASE_URL=postgresql://fake:fake@localhost/fake python -m pytest tests/test_constanza_method_contract.py -q -p no:cacheprovider
```

## Pitfalls we already paid for

* **A fake must mirror the vendor's documented shapes.** Meta wraps a reach estimate in `data` as one object (a list or a bare node also appear in the wild); TikTok refuses what its docs say it
  refuses. A fake that accepts whatever the code sends proves nothing - make it strict and docs-shaped.
* **Escape every server string.** Place names, interest names, notes and errors are text from somebody else's server: `ctx.esc` them in every `...Html` member, and keep `summaryLines` plain.
* **A restored result has no `asked.method` when the form was edited since.** `bodyHtml(data, m, asked)` and `summaryLines(data, asked)` must work with `asked = { geo: "", areas: [], method: null }`
  and then describe the result from the answer's own echo, never from the form: a saved result is always described by what it was pulled with. Reading `asked.method.summary` unguarded throws
  on a restored card, and putting `asked.method && asked.method.geo` into a template writes the word `null` into the planner's text.
* **`requestPart()` is read for your own key only.** A `product_name`, `months` or `geo_text` you return next to `{ [key]: block }` is dropped, not merged: the dock owns those.
* **Registration is refused, loudly, for the usual slips**: a cap name that is not one of the four (`toString` counts as a typo), `ownsGeography` left on `autoPull: "geo"`, and a `legacy.key` that
  is not a `..._forecast` field. The reason is in the browser console (`[constanza] plugin "foo": ...`); the dock opens without your method.
* **A zero, missing or non-numeric monthly figure has no Insert.** The estimate route accepts `imps_mid: 0`, the card and the dock do not offer to write it into Avails, and a saved result with no
  monthly figures at all reads as "couldn't be read - estimate again" on its Step 05 card. Do not work around it with a placeholder number.
* **A `Check.detail` is never the place for a secret**, even though the registry redacts the spec's `secrets` from every line: it is a net for a platform's own text, not a licence. A secret your
  method knows that is not in `secrets` (Meta's app secret and the proof made from it) is only caught by your own redaction.
* **A hook that can fail must fail small.** A raising `product_info` costs its one product the `info` (a warning is logged), never the catalog; keep it fast and free of the network, because
  every planner session runs it for every product.
* **Never block the dock on a method that failed to load.** A bad script is refused and the dock stays usable; a product whose method did not register is left out of the picker with an explicit
  note on Step 05 - it never falls through to the platform path. Keep `bind` and `formHtml` free of network calls (they run at page load for every user).
* **A method that owns its geography sends `geo_text: ""`** (the host does it for `caps.ownsGeography`), gives `context().geo` and never has the shared geography resolved for it.
* **Do not call the network at import** (the module is imported at app start, by tests and by the schema tools), and do not call the registry while importing.
* **Months.** A Step 05 line's flight is counted in *calendar months touched*; the dock's own flight box rounds days to months. A result that is a flight total (`monthly × months`) must use the
  line's months: set `caps.lineMonths`.
* **JSONB re-orders keys**, so a reopened proposal's snapshot has different key order. Compare requests with `canonicalJson`.
* **Show an account as its last four digits** and scrub the token out of every upstream message.
* **One test at a time per method**: a connection test makes many calls and platforms rate-limit an account after a burst.
* **A key such as `style` or `search` fails the page-script hygiene tests** (C10, also run by `tests/test_forecast_ui_wiring.py`, looks for the key as a string, an object key and `.key` on a result): choose a key that is not an ordinary
  property name. A key that is also a CSS word (`display`, `none`, `block`, `flex`, `grid`, `hidden`, `inline`, `table`, `auto`) is exempt from the two string rules only.
* **Do not add fields to `AvailsEntry`**; pydantic drops undeclared keys, which is why the one `forecast_snapshot` field exists.
* **A published deployment skips startup migrations** (its schema comes from Publish's diff). A new method needs no SQL, but run the app once in the workspace before Publish when a migration (like `0006`) is in the release;
  see [forecast_data_platform.md](forecast_data_platform.md), "Deploying".
