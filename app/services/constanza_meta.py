"""
Constanza x Meta (Facebook & Instagram): audience-size forecasting for the paid Meta SKUs, built from Meta's own Marketing API.

Meta's `reachestimate` answers with a head count, not delivery: the range of accounts (`users_lower_bound` .. `users_upper_bound`) that
match a targeting. It does not forecast impressions, so Entravision's planning rule turns the LOWER end of that range into impressions in
three scenarios (platform_scenarios.py, shared with TikTok): **monthly impressions = lower-end audience x 4.3 (conservative) / 5.5 (mid, the
default) / 8.6 (upper)**. A flight of N months is N times that. The est. uniques of the Avails step is the lower-end audience, so its
frequency is the mid multiplier. A separate, budget-based figure (what the retail budget buys at the planning CPM) is shown beside it as a
cross-check; it is not an avails number.

What the planner can set, all from Meta itself (nothing is typed from memory):
  * geography: Meta's own geography search (DMA "geo markets", cities, regions, ZIPs, countries) - the planner picks the exact key, nothing is guessed;
  * age (18-65) and gender;
  * interests and behaviors (and the other "detailed targeting" lists): searched or browsed from Meta's `search` endpoint, then sent in the
    targeting spec as ONE `flexible_spec` group, so the audience is "people who match ANY of the chosen interests / behaviors";
  * languages (most Entravision campaigns target Spanish speakers): Meta's own list of locales (`search?type=adlocale`), sent as the spec's
    `locales`. See `build_targeting_spec` for how they combine with the rest and LANGUAGE_NOTE for what a "language" is to Meta.

Setup (the app runs fine without it - every caller checks `is_configured()`):
    META_ACCESS_TOKEN     a long-lived token (a system user's is best) that can READ the ad account: the `ads_read` permission
    META_AD_ACCOUNT_ID    the ad account the estimates are asked through, with or without `act_`
  optional:
    META_GRAPH_API_VERSION   default v26.0 (Marketing API v26.0 was released 2026-07-29; a pinned older version stops working when Meta retires it)
    META_APP_SECRET          only if the Meta app has "Require App Secret" switched on: every call then carries an `appsecret_proof`
Read-only: a reach estimate and the targeting lists; nothing is ever created in the ad account. The token is only sent in the Authorization
header and is removed from every message this module raises.

Meta is also a registered Constanza METHOD (constanza_methods.py): `estimate_response` below is what POST /api/constanza/estimate answers for a
Meta product - the standard estimate plus a `meta` block - and the geography is Meta's own (the planner picks the place from Meta's search, so
`owns_geography` is true and Constanza's Nielsen geography is not resolved for it). `model_auction` is the one place the scenarios and the budget
cross-check are computed; the older POST /api/constanza/meta-forecast uses it too, so both give the same numbers.

Two things the figure is NOT: it counts people active on Meta's DEFAULT placements (Facebook, Instagram, Messenger, Audience Network, ...), the
same as Ads Manager's "Estimated audience size", not only Facebook and Instagram; and `geo_market` is Meta's Comscore Markets (Meta moved off
Nielsen DMAs on 2026-06-22), so its footprint can differ from the Nielsen markets the rest of Constanza uses.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import requests

from app import catalog
from app.services import constanza_methods, platform_scenarios
from app.services.constanza_methods import MethodRequest, MethodSpec

log = logging.getLogger(__name__)


# Retail CPMs confirmed by the planning team. The 2x retail/media relationship implies a media CPM of half this value for the budget model.
RETAIL_CPMS = {
    "Facebook & Instagram Ads | Awareness": 12.0,
    "Facebook & Instagram Ads | Traffic / Conversion": 18.0,
    "Facebook & Instagram Ads | Lead Gen / Calls": 35.0,
}
RETAIL_TO_MEDIA = 2.0
DEFAULT_GRAPH_VERSION = "v26.0"
_GEO_FIELDS = {"country": "countries", "region": "regions", "city": "cities", "zip": "zips", "geo_market": "geo_markets"}
# The `type` of a detailed-targeting item = the key it has in a targeting spec's flexible_spec group. Only the fields that take a list of
# {id, name} objects are here: a demographics row of another type (user_device, user_os, markets, education_statuses, ...) needs a different spec
# shape and is left out of the lists instead of being sent under the wrong key.
TARGETING_KEYS = ("interests", "behaviors", "life_events", "industries", "income", "family_statuses", "politics", "generation",
                  "home_ownership", "home_type", "home_value", "household_composition", "net_worth", "office_type", "moms")
MAX_TARGETING_ITEMS = 50
# What a chosen option may carry. One place for the lists (_item), the check of a pick (_clean_targeting) and the request model in main.py, so a row
# the lists offer is always one a request can carry: the model's limit for `type` is the longest key above, never a number typed in separately.
MAX_TARGETING_ID_LENGTH = 30
MAX_NAME_LENGTH = 120
MAX_TARGETING_TYPE_LENGTH = max(len(key) for key in TARGETING_KEYS)
_TARGETING_ID_RE = re.compile(rf"[0-9]{{1,{MAX_TARGETING_ID_LENGTH}}}")
# Meta documents a limit of 50 locales in a targeting spec; a planner never needs close to that many, so the app stops at half.
MAX_LOCALES = 25
_LOCALE_ID_RE = re.compile(r"[1-9][0-9]{0,9}")           # a locale id is a positive whole number (Meta's reference: `key`, e.g. 6 = English (US))
_LOCALE_LIST_LIMIT = 1000                                 # Meta's own advice for "all of them": a blank `q` with limit=1000
_LOCALE_SEARCH_LIMIT = 50                                 # a keyword search (Meta's default of 8 would hide the regional variants)
_LANGUAGE_TTL = 12 * 3600
# kind -> the `class` Meta's adTargetingCategory browse takes (interests are also searchable by keyword: type=adinterest)
CATALOG_CLASSES = {"interests": "interests", "behaviors": "behaviors", "demographics": "demographics"}
_CATALOG_TTL = 12 * 3600
_ESTIMATE_TTL = 600

# What Meta's "language" is, in the words the panel shows next to the language picker (and returns from the status and languages routes).
LANGUAGE_NOTE = (
    "Meta's languages are the language people have set Facebook or Instagram to, not a count of who speaks it. A Spanish speaker whose app is "
    "set to English is not included, so a Spanish audience can come out smaller than the Spanish-speaking population. Leave languages empty to "
    "count everyone in the geography, whatever language they use."
)


class MetaAPIError(Exception):
    """A safe, user-facing Meta configuration or API failure (never carries the token)."""

    def __init__(self, message: str, kind: str = "error", status_code: int = 503):
        super().__init__(message)
        self.kind = kind
        self.status_code = status_code


# ----------------------------------------------------------------------------------------------------------------- the products

def product_cpm(product_name: str) -> Optional[float]:
    """Resolve current/renamed catalog names; exclude branded-content SKUs."""
    if product_name in RETAIL_CPMS:
        return RETAIL_CPMS[product_name]
    try:
        matches = catalog.name_variants(product_name) & RETAIL_CPMS.keys()
    except Exception:  # a failed catalog lookup must not turn another SKU into a Meta buy
        return None
    return RETAIL_CPMS[next(iter(matches))] if len(matches) == 1 else None


BUDGET_MESSAGE = "Retail budget must be between $0 and $1 billion for the full flight."
CPM_MESSAGE = "Retail CPM must be a positive number."


def _check_retail_budget(value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not (0 < value <= 1e9) or value != value:
        raise ValueError(BUDGET_MESSAGE)
    return value


def _check_retail_cpm(value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not (0 < value <= 1e5) or value != value:
        raise ValueError(CPM_MESSAGE)
    return value


def model_auction(
    *, product_name: str, retail_budget: Optional[float], audience_lower: Optional[int] = None,
    audience_upper: Optional[int] = None, months: int = 1, audience_source: str = "manual",
    retail_cpm: Optional[float] = None, strict_product: bool = True,
) -> dict:
    """The planning numbers for one Meta line: three impression scenarios from the lower-end audience, and the budget cross-check.

    `audience_lower` is the LOWER end of the platform's range (what the scenarios multiply); `audience_upper` is context (a caller may give only the
    lower end). `audience_source` says where the range came from: "meta_api" for a live pull, "manual" for bounds a caller supplied itself (the panel
    never does; snapshots saved before it pulled everything from Meta carry it). Without an audience there are no scenarios, only the budget figure.

    `retail_budget` None means "no cross-check": the budget fields come back null (an explicit 0 or a negative number is still refused). With
    `strict_product` (the default, what POST /meta-forecast uses) the product must be one of the three paid SKUs; the estimate route passes
    False because an admin may map the Meta method to another product, which then has no planning CPM of its own: the CPM is the one the caller
    gives, or none, and without a CPM there is no spend figure and no budget cross-check."""
    default_cpm = product_cpm(product_name)
    if default_cpm is None and strict_product:
        raise ValueError("Choose one of the three paid Facebook & Instagram Ads products.")
    cpm = default_cpm if retail_cpm is None else retail_cpm
    if retail_budget is not None:
        _check_retail_budget(retail_budget)
    if cpm is not None:
        _check_retail_cpm(cpm)
    if not isinstance(months, int) or isinstance(months, bool) or not 1 <= months <= 120:
        raise ValueError("Months must be a whole number from 1 to 120.")
    if audience_upper is not None and audience_lower is None:
        raise ValueError("Give the lower end of the audience (the upper end alone is not a base).")
    if audience_lower is not None:
        if audience_upper is None:
            audience_upper = audience_lower
        if not (0 <= audience_lower <= audience_upper <= 1_000_000_000):
            raise ValueError("Audience bounds must be non-negative, ordered low to high, and no larger than 1,000,000,000.")
    if audience_source not in ("manual", "meta_api"):
        raise ValueError("Audience source must be manual or meta_api.")

    media_budget = None if retail_budget is None else retail_budget / RETAIL_TO_MEDIA
    media_cpm = None if cpm is None else cpm / RETAIL_TO_MEDIA
    budget_imps = None if media_budget is None or media_cpm is None else round(media_budget * 1000 / media_cpm)
    scenarios = None
    if audience_lower is not None:
        public = platform_scenarios.to_public(audience_lower)
        monthly = {k: round(v) for k, v in public["monthly"].items()}
        flight = {k: v * months for k, v in monthly.items()}          # exactly the monthly figure shown, times the months
        scenarios = {
            **public, "monthly": monthly, "months": months, "flight": flight,
            # what each scenario's flight total would cost at the planning CPM: a reality check against the line's budget, not a quote
            "retail_spend_at_flight": None if cpm is None else {k: round(v * cpm / 1000, 2) for k, v in flight.items()},
            "media_spend_at_flight": None if media_cpm is None else {k: round(v * media_cpm / 1000, 2) for k, v in flight.items()},
            "est_uniques": audience_lower,
        }
    notes = [
        "Meta's audience size counts eligible accounts; it is not delivered reach and not a delivery forecast.",
        f"Monthly impressions are {platform_scenarios.describe()}; mid is the default and the figure the Avails step takes. A flight is the "
        "monthly figure times its months.",
    ]
    if budget_imps is not None:
        notes.append("Budget impressions (what the retail budget buys at the planning CPM, 2x retail/media) are a cross-check, not avails.")
    return {
        "metric_type": "meta_audience_scenarios", "product_name": product_name, "as_of": datetime.now(timezone.utc).isoformat(),
        "retail_budget": None if retail_budget is None else round(retail_budget, 2),
        "media_budget": None if media_budget is None else round(media_budget, 2),
        "retail_cpm": None if cpm is None else round(cpm, 2),
        "media_cpm_assumed": None if media_cpm is None else round(media_cpm, 2),
        "retail_to_media_multiplier": RETAIL_TO_MEDIA,
        "months": months, "budget_impressions": budget_imps,
        "potential_audience": ({"lower": audience_lower, "upper": audience_upper, "source": audience_source}
                               if audience_lower is not None else None),
        "scenarios": scenarios,
        "notes": notes,
    }


# ----------------------------------------------------------------------------------------------------------------- configuration

_TOKEN_RE = re.compile(r"[\x21-\x7e]+")                 # a token is printable ASCII with no space: anything else was mangled in the paste
_ACCOUNT_RE = re.compile(r"[0-9]{1,30}")


def missing_config() -> list[str]:
    """What is not set up, by name (never a value): a missing secret, or one that cannot be right ("copy it again")."""
    out = []
    token = (os.environ.get("META_ACCESS_TOKEN") or "").strip()
    if not token:
        out.append("META_ACCESS_TOKEN")
    elif not _TOKEN_RE.fullmatch(token):
        out.append("META_ACCESS_TOKEN (it has a space or a character a token cannot hold: copy it again)")
    account = (os.environ.get("META_AD_ACCOUNT_ID") or "").strip()
    if not account:
        out.append("META_AD_ACCOUNT_ID")
    elif not _ACCOUNT_RE.fullmatch(account.removeprefix("act_")):
        out.append("META_AD_ACCOUNT_ID (it must be the numeric ad account ID, with or without act_)")
    return out


def is_configured() -> bool:
    return not missing_config()


def _account() -> str:
    account = (os.environ.get("META_AD_ACCOUNT_ID") or "").strip().removeprefix("act_")
    if not _ACCOUNT_RE.fullmatch(account):
        raise MetaAPIError("META_AD_ACCOUNT_ID must be a numeric Meta ad account ID (digits, with or without act_).", "not_configured")
    return account


def account_hint() -> Optional[str]:
    """The ad account as its last four digits - the only part of it ever shown."""
    raw = (os.environ.get("META_AD_ACCOUNT_ID") or "").strip().removeprefix("act_")
    return f"…{raw[-4:]}" if raw else None


def graph_version() -> str:
    version = (os.environ.get("META_GRAPH_API_VERSION") or DEFAULT_GRAPH_VERSION).strip()
    if not re.fullmatch(r"v[0-9]{1,2}\.0", version):
        raise MetaAPIError("META_GRAPH_API_VERSION must look like v26.0.", "not_configured")
    return version


def status() -> dict:
    """Whether the Meta connection is set up - which settings are missing (the setting's name first, then what to do), never their values - the
    ad account as its last four digits, and the planning multipliers. The standard status shape of constanza_methods.validate_status plus
    Meta's own extras."""
    missing = missing_config()
    return {"configured": not missing, "missing": missing, "account": account_hint(), "version": (os.environ.get("META_GRAPH_API_VERSION") or DEFAULT_GRAPH_VERSION).strip(),
            "scenarios": platform_scenarios.to_public(), "setup_hint": SETUP_HINT, "language_note": LANGUAGE_NOTE}


SETUP_HINT = ("Add META_ACCESS_TOKEN (a token with the ads_read permission for the ad account) and META_AD_ACCOUNT_ID in the app's secrets "
              "(Replit -> Secrets), then run python -m app.services.meta_diagnose. If the Meta app has Require App Secret switched on, also add "
              "META_APP_SECRET.")


# ----------------------------------------------------------------------------------------------------------------- the Graph API

def redact(text) -> str:
    """`text` with the access token, the app secret, the `appsecret_proof` made from them and the ad account taken out (the account is left as its
    last four digits, wherever its digits stand: a message, or the ad account's own name). Not shortened: the lines of a connection test use it too."""
    out = str(text or "")
    token = (os.environ.get("META_ACCESS_TOKEN") or "").strip()
    secret = (os.environ.get("META_APP_SECRET") or "").strip()
    if token and secret:
        out = out.replace(hmac.new(secret.encode(), token.encode(), hashlib.sha256).hexdigest(), "[proof]")
    if token:
        out = out.replace(token, "[token]")
    if secret:
        out = out.replace(secret, "[secret]")
    account = (os.environ.get("META_AD_ACCOUNT_ID") or "").strip().removeprefix("act_")
    if len(account) > 4:                                        # (up to four digits there is nothing to hide, and replacing them would garble numbers)
        out = out.replace(account, "…" + account[-4:])
    return out


def _scrub(text: str) -> str:
    """A Meta message with the token and the ad account removed (it must never reach a log, an exception or the browser), cut to a sentence or two."""
    return redact(text)[:300]


def _classify(error: dict, http_status: int) -> MetaAPIError:
    """Meta's error object -> a plain message and the HTTP status this app answers with."""
    code = error.get("code")
    sub = error.get("error_subcode")
    message = _scrub(error.get("message") or "")
    if code in (190, 102) or http_status == 401:
        return MetaAPIError("Meta rejected the access token (expired, revoked or wrong). Make a new long-lived token and update META_ACCESS_TOKEN.", "token", 503)
    if code in (10, 200, 294) or (isinstance(code, int) and 200 <= code <= 299):
        return MetaAPIError("This token cannot read the ad account: it needs the ads_read permission on META_AD_ACCOUNT_ID (and the account must be "
                            "shared with the app's business).", "permission", 503)
    if code in (4, 17, 32, 341, 613, 80000, 80003, 80004, 80014) or http_status == 429:
        return MetaAPIError("Meta is rate-limiting this ad account. Wait a few minutes and pull again (a newly created app is blocked for up to "
                            "five minutes after a burst).", "rate_limit", 429)
    if code == 2641:
        return MetaAPIError("Meta cannot estimate this audience: it includes or excludes locations that are currently restricted.", "settings", 422)
    if "appsecret_proof" in message.lower() or "app secret" in message.lower():
        return MetaAPIError("Meta requires an appsecret_proof for this app (App Settings > Advanced > Require App Secret is on). Switch that off, or set "
                            "META_APP_SECRET in the app's secrets.", "not_configured", 503)
    if code == 2635 or "deprecated version" in message.lower():
        return MetaAPIError("Meta has retired the Marketing API version this app asks for. Set META_GRAPH_API_VERSION to a current version (v26.0).",
                            "not_configured", 503)
    # Meta's own signal for an ad account it cannot show is subcode 33 (and its wording "missing permissions"). "does not exist" alone is read as the
    # account only when Meta is not saying "invalid parameter" (code 100): there it can be a place or an interest that was withdrawn, which is a 422 about the settings.
    if sub == 33 or "missing permissions" in message.lower() or (code != 100 and "does not exist" in message.lower()):
        return MetaAPIError("Meta says this ad account does not exist or the token cannot see it: check META_AD_ACCOUNT_ID, and in Business Settings assign "
                            "the ad account to the token's (system) user with ads_read.", "permission", 503)
    if code == 100 or http_status == 400:
        return MetaAPIError(f"Meta refused these settings{': ' + message if message else ''}. Check the geography and targeting in Ads Manager.", "settings", 422)
    return MetaAPIError(f"Meta could not answer (error {code if code is not None else http_status}"
                        f"{', ' + str(sub) if sub else ''}){': ' + message if message else ''}.", "meta", 502)


def _graph_get(path: str, params: dict) -> dict:
    missing = missing_config()
    if missing:
        raise MetaAPIError(f"Meta is not configured: add {' and '.join(missing)} in the app's secrets (Replit -> Secrets).", "not_configured", 503)
    version = graph_version()
    url = f"https://graph.facebook.com/{version}/{path}"
    token = os.environ["META_ACCESS_TOKEN"].strip()
    params = dict(params)
    secret = (os.environ.get("META_APP_SECRET") or "").strip()
    if secret:                                                  # an app with "Require App Secret" on refuses calls without the proof
        params["appsecret_proof"] = hmac.new(secret.encode(), token.encode(), hashlib.sha256).hexdigest()
    try:
        response = requests.get(url, params=params, headers={"Authorization": f"Bearer {token}"}, timeout=12)
    except requests.RequestException as exc:
        raise MetaAPIError("Meta did not answer in time. Try again in a moment.", "unreachable", 502) from None
    try:
        result = response.json()
    except ValueError:
        result = None
    if isinstance(result, dict) and isinstance(result.get("error"), dict):
        raise _classify(result["error"], response.status_code)
    if not response.ok:
        raise _classify({}, response.status_code)
    if not isinstance(result, dict):
        raise MetaAPIError("Meta answered with something this app cannot read.", "meta", 502)
    return result


# ----------------------------------------------------------------------------------------------------------------- lookups

_lock = threading.Lock()
_cache: dict[str, tuple[float, object]] = {}


def _cached(key: str, ttl: float, fetch):
    now = time.monotonic()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    value = fetch()
    with _lock:
        _cache[key] = (now, value)
        if len(_cache) > 400:          # a bounded memory: drop the oldest entries
            for stale in sorted(_cache, key=lambda k: _cache[k][0])[:100]:
                _cache.pop(stale, None)
    return value


def reset_cache() -> None:
    with _lock:
        _cache.clear()


def _remembered_list(key: str, ttl: float, fetch) -> list:
    """A list Meta is asked for once per `ttl` seconds. Only a non-empty list is remembered: an empty answer is asked again."""
    with _lock:
        hit = _cache.get(key)
        if hit and time.monotonic() - hit[0] < ttl:
            return list(hit[1])
    items = fetch()
    if items:
        with _lock:
            _cache[key] = (time.monotonic(), items)
    return list(items)


def search_geography(query: str, location_type: str) -> list[dict]:
    """Candidate geographies for explicit planner selection; never guess a DMA."""
    if location_type not in _GEO_FIELDS:
        raise ValueError("Choose country, region, city, ZIP, or geo market.")
    query = query.strip()
    if not 2 <= len(query) <= 100:
        raise ValueError("Search text must be 2–100 characters.")
    result = _graph_get("search", {"type": "adgeolocation", "location_types": json.dumps([location_type]), "q": query, "limit": 20})
    return [
        {"key": str(r["key"]), "name": str(r.get("name") or ""), "type": location_type,
         "region": str(r.get("region") or ""), "country_code": str(r.get("country_code") or "")}
        for r in (result.get("data") or []) if isinstance(r, dict) and r.get("key") is not None
    ]


def _size(row: dict, side: str) -> Optional[int]:
    raw = row.get(f"audience_size_{side}_bound")
    if raw is None and side == "lower":
        raw = row.get("audience_size")
    try:
        value = int(raw)
    except (TypeError, ValueError, OverflowError):
        return None
    return value if value >= 0 else None


def _item(row: dict, kind: Optional[str]) -> Optional[dict]:
    """One detailed-targeting option as the panel shows it; None for a row without a numeric id or a name, or whose type this app cannot put in a
    targeting spec (a row keeps its OWN type: it is never relabelled to the list it was browsed from). A name longer than a pick can carry is
    shortened, as `_locale` does, so every row offered can be chosen."""
    if not isinstance(row, dict) or row.get("id") is None or not str(row.get("name") or "").strip():
        return None
    item_id = str(row["id"]).strip()
    if not _TARGETING_ID_RE.fullmatch(item_id):
        return None
    item_type = str(row.get("type") or kind or "")
    if item_type not in TARGETING_KEYS:
        return None
    path = row.get("path")
    return {"id": item_id, "name": str(row["name"]).strip()[:MAX_NAME_LENGTH], "type": item_type,
            "path": [str(p) for p in path] if isinstance(path, list) else [],
            "audience_lower": _size(row, "lower"), "audience_upper": _size(row, "upper"),
            "description": str(row.get("description") or "")[:240]}


def targeting_catalog(kind: str) -> list[dict]:
    """Every option of one detailed-targeting list straight from Meta (cached for hours): `interests`, `behaviors` or `demographics`
    (life events, industries, income, family status...). Not every option Meta has is returned to every account, and Meta retires options;
    what is listed is what this ad account can use today."""
    if kind not in CATALOG_CLASSES:
        raise ValueError("Choose interests, behaviors or demographics.")

    def fetch():
        result = _graph_get("search", {"type": "adTargetingCategory", "class": CATALOG_CLASSES[kind], "limit": 1000})
        items = [i for i in (_item(r, None if kind == "demographics" else kind) for r in (result.get("data") or [])) if i]
        return sorted(items, key=lambda i: i["name"].lower())
    if not is_configured():
        return fetch()                                        # raises the "not configured" message
    return _remembered_list(f"catalog:{graph_version()}:{_account()}:{kind}", _CATALOG_TTL, fetch)


def search_targeting(q: str) -> dict[str, list[dict]]:
    """What a keyword finds: interests (Meta's own keyword search, `type=adinterest`) plus the behaviors and demographics whose name or path
    contains the words (from the cached lists). {"interests": [...], "behaviors": [...], "demographics": [...]}."""
    q = (q or "").strip()
    if not 2 <= len(q) <= 80:
        raise ValueError("Search text must be 2–80 characters.")
    result = _graph_get("search", {"type": "adinterest", "q": q, "limit": 25})
    interests = [i for i in (_item(r, "interests") for r in (result.get("data") or [])) if i]
    words = [w for w in re.split(r"\s+", q.lower()) if w]

    def match(item):
        hay = (item["name"] + " " + " ".join(item["path"])).lower()
        return all(w in hay for w in words)
    out = {"interests": interests, "behaviors": [], "demographics": []}
    for kind in ("behaviors", "demographics"):
        try:
            out[kind] = [i for i in targeting_catalog(kind) if match(i)][:25]
        except MetaAPIError as exc:
            if exc.kind in ("token", "permission", "not_configured"):
                raise
            out[kind] = []                 # one list unavailable must not hide the interests that were found
    return out


def _clean_targeting(items) -> list[dict]:
    """The planner's chosen detailed-targeting options, validated to what a targeting spec takes."""
    if items is None:
        return []
    if not isinstance(items, (list, tuple)) or len(items) > MAX_TARGETING_ITEMS:
        raise ValueError(f"Choose at most {MAX_TARGETING_ITEMS} interests and behaviors.")
    out, seen = [], set()
    for raw in items:
        if not isinstance(raw, dict):
            raise ValueError("Each interest or behavior needs an id, a name and a type.")
        item_id, name, kind = str(raw.get("id") or ""), str(raw.get("name") or "").strip(), str(raw.get("type") or "")
        if not _TARGETING_ID_RE.fullmatch(item_id) or not 1 <= len(name) <= MAX_NAME_LENGTH or kind not in TARGETING_KEYS:
            raise ValueError("Each interest or behavior needs a numeric Meta id, a name and a known type; pick it from Meta's list.")
        if (kind, item_id) not in seen:
            seen.add((kind, item_id))
            out.append({"id": item_id, "name": name, "type": kind})
    return out


def _locale(row) -> Optional[dict]:
    """One language as the picker shows it. Meta's reference puts the number under `key` (`id` is read too, in case the shape moves); None for
    a row without a whole-number id or a name."""
    if not isinstance(row, dict):
        return None
    raw = row.get("key") if row.get("key") is not None else row.get("id")
    if isinstance(raw, bool) or not isinstance(raw, (int, str)):
        return None
    locale_id, name = str(raw).strip(), str(row.get("name") or "").strip()
    if not _LOCALE_ID_RE.fullmatch(locale_id) or not name:
        return None
    return {"id": locale_id, "name": name[:MAX_NAME_LENGTH]}


def search_locales(q: str = "") -> list[dict]:
    """The languages Meta can target, as [{id, name}] sorted by name (the id is a string; `build_targeting_spec` sends it as a number).

    Without `q`: the whole list. Meta's targeting search documents this (`type=adlocale`, a blank `q`, `limit=1000`), so no curated list is
    needed; it is cached for hours, and an empty answer is asked again. With `q` (2-60 characters): the locales Meta's search finds for those
    letters, e.g. "Spanish" (regional variants such as "Spanish (Spain)" are separate locales). A language is whatever Meta lists: this app
    never carries a locale id of its own."""
    q = (q or "").strip()
    if q and not 2 <= len(q) <= 60:
        raise ValueError("Search text must be 2–60 characters.")

    def fetch():
        result = _graph_get("search", {"type": "adlocale", "q": q, "limit": _LOCALE_SEARCH_LIMIT if q else _LOCALE_LIST_LIMIT})
        rows = result.get("data")
        found: dict[str, dict] = {}
        for row in rows if isinstance(rows, list) else []:
            item = _locale(row)
            if item and item["id"] not in found:
                found[item["id"]] = item
        return sorted(found.values(), key=lambda i: (i["name"].lower(), int(i["id"])))
    if q or not is_configured():
        return fetch()                                        # a keyword search is asked fresh; unconfigured raises the "not configured" message
    return _remembered_list(f"locales:{graph_version()}", _LANGUAGE_TTL, fetch)


def clean_locales(items) -> list[dict]:
    """The planner's chosen languages ([{id, name}] picked from search_locales), validated to what a targeting spec takes: at most MAX_LOCALES,
    each with a whole-number Meta locale id and a name. A language chosen twice counts once."""
    if items is None:
        return []
    if not isinstance(items, (list, tuple)) or len(items) > MAX_LOCALES:
        raise ValueError(f"Choose at most {MAX_LOCALES} languages.")
    out, seen = [], set()
    for raw in items:
        if not isinstance(raw, dict):
            raise ValueError("Each language needs a Meta locale id and a name; pick it from Meta's language list.")
        raw_id = raw.get("id")
        item_id = "" if isinstance(raw_id, bool) or not isinstance(raw_id, (int, str)) else str(raw_id).strip()
        name = str(raw.get("name") or "").strip()
        if not _LOCALE_ID_RE.fullmatch(item_id) or not 1 <= len(name) <= MAX_NAME_LENGTH:
            raise ValueError("Each language needs a numeric Meta locale id and a name; pick it from Meta's language list.")
        if item_id not in seen:
            seen.add(item_id)
            out.append({"id": item_id, "name": name})
    return out


def build_targeting_spec(*, geo_type: str, geo_key: str, age_min: Optional[int] = None, age_max: Optional[int] = None,
                         gender: Optional[str] = None, targeting=None, locales=None) -> dict:
    """The targeting_spec sent to Meta for explicitly selected geography, age, gender, interests / behaviors and languages. ValueError for a
    choice Meta would refuse.

    How the choices combine (Meta's Ads Manager rule): different kinds of choice are ANDed - geography AND age AND gender AND the interests /
    behaviors AND the languages. Inside a kind they are ORed: the chosen interests / behaviors form ONE flexible_spec group (people who match
    ANY of them), and several languages are ANY of them too (`locales` is a plain list of locale ids; Meta's reference documents up to 50).
    So "Spanish + English" in Los Angeles is people there whose app is set to either: the first language narrows the audience compared with none,
    and each further one widens it again. The names that came with the chosen languages stay with the snapshot for display; the spec carries the
    ids only."""
    if geo_type not in _GEO_FIELDS or not re.fullmatch(r"[A-Za-z0-9_:-]{1,40}", geo_key or ""):
        raise ValueError("Select a valid Meta geography from the search results.")
    if age_min is not None and not 18 <= age_min <= 65:
        raise ValueError("Meta minimum age must be 18–65.")
    if age_max is not None and not 18 <= age_max <= 65:
        raise ValueError("Meta maximum age must be 18–65.")
    if age_min is not None and age_max is not None and age_min > age_max:
        raise ValueError("Minimum age cannot exceed maximum age.")
    if gender not in (None, "all", "male", "female"):
        raise ValueError("Gender must be all, male, or female.")
    value = geo_key if geo_type == "country" else {"key": geo_key}
    spec: dict = {"geo_locations": {_GEO_FIELDS[geo_type]: [value]}}
    if age_min is not None:
        spec["age_min"] = age_min
    if age_max is not None:
        spec["age_max"] = age_max
    if gender in ("male", "female"):
        spec["genders"] = [1 if gender == "male" else 2]
    chosen = _clean_targeting(targeting)
    if chosen:
        group: dict[str, list] = {}
        for it in chosen:
            group.setdefault(it["type"], []).append({"id": it["id"], "name": it["name"]})
        spec["flexible_spec"] = [group]
    languages = clean_locales(locales)
    if languages:
        spec["locales"] = sorted(int(language["id"]) for language in languages)      # a stable order: the same languages share one cached estimate
    return spec


def fetch_reach_estimate(*, geo_type: str, geo_key: str, age_min: Optional[int] = None, age_max: Optional[int] = None,
                         gender: Optional[str] = None, targeting=None, locales=None) -> tuple[int, int]:
    """Meta's potential audience (lower, upper) for explicitly selected targeting - the account-scoped `reachestimate`. The estimate is
    remembered for ten minutes under the whole targeting spec, languages included, so a Spanish estimate never answers an English one."""
    spec = build_targeting_spec(geo_type=geo_type, geo_key=geo_key, age_min=age_min, age_max=age_max, gender=gender, targeting=targeting,
                                locales=locales)
    missing = missing_config()
    if missing:
        raise MetaAPIError(f"Meta is not configured: add {' and '.join(missing)} in the app's secrets (Replit -> Secrets).", "not_configured", 503)
    account = _account()
    body = json.dumps(spec, separators=(",", ":"), sort_keys=True)

    def fetch():
        result = _graph_get(f"act_{account}/reachestimate", {"targeting_spec": body, "fields": "users_lower_bound,users_upper_bound,estimate_ready"})
        # Meta's reference describes the answer as "a single AdAccountReachEstimate node": `data` is that one object. A one-item list and a bare
        # top-level object are read the same way, so a change in shape does not break every pull.
        data = result.get("data")
        if isinstance(data, dict):
            row = data
        elif isinstance(data, list) and data and isinstance(data[0], dict):
            row = data[0]
        else:
            row = result
        if row.get("estimate_ready") is False or result.get("estimate_ready") is False:
            raise MetaAPIError("Meta is still preparing this audience estimate. Try again shortly.", "not_ready", 503)
        try:
            lower, upper = int(row["users_lower_bound"]), int(row["users_upper_bound"])
        except (KeyError, TypeError, ValueError, OverflowError):
            raise MetaAPIError("Meta did not return audience bounds for that targeting.", "meta", 502) from None
        if lower < 0 or upper < 0:
            raise MetaAPIError("Meta cannot estimate this audience (it returned no figure, which does not mean the audience is empty). "
                               "Check the targeting in Ads Manager.", "unavailable", 422)
        if upper < lower:
            raise MetaAPIError("Meta returned invalid audience bounds for that targeting.", "meta", 502)
        return lower, upper
    return _cached(f"reach:{graph_version()}:{account}:{body}", _ESTIMATE_TTL, fetch)


# ----------------------------------------------------------------------------------------------------------------- the method

@dataclass(frozen=True)
class MetaArgs:
    """What the planner set up for a Meta estimate, validated: the `meta` block of POST /api/constanza/estimate."""
    geo_type: str
    geo_key: str
    geo_label: str
    age_min: Optional[int]
    age_max: Optional[int]
    gender: str                                   # "all" | "male" | "female"
    languages: tuple                              # ({id, name}, ...) picked from search_locales
    targeting: tuple                              # ({id, name, type}, ...) picked from the interest / behavior lists
    retail_budget: Optional[float]                # the whole flight at retail, dollars; None = no budget cross-check
    retail_cpm: Optional[float]                   # None = the product's own planning CPM

    @classmethod
    def from_dict(cls, raw: Optional[dict]) -> "MetaArgs":
        """ValueError in planner words for anything Meta would refuse or that could not be asked of it - before a single call is spent."""
        raw = raw or {}
        if not isinstance(raw, dict):
            raise ValueError("The Meta settings must be an object.")
        geo = raw.get("geo")
        if not isinstance(geo, dict) or not geo:
            raise ValueError("Choose where first: pick a place from Meta's list.")
        geo_type, geo_key = str(geo.get("type") or ""), str(geo.get("key") or "")
        if geo_type not in _GEO_FIELDS or not re.fullmatch(r"[A-Za-z0-9_:-]{1,40}", geo_key):
            raise ValueError("Select a valid Meta geography from the search results.")
        budget, cpm = raw.get("retail_budget"), raw.get("retail_cpm")
        if budget is not None:
            _check_retail_budget(budget)
        if cpm is not None:
            _check_retail_cpm(cpm)
        gender = raw.get("gender") or "all"
        languages, targeting = clean_locales(raw.get("languages")), _clean_targeting(raw.get("targeting"))
        # the same checks as the request that is sent (ages, gender, the shape of the place): a refused choice is said now, before any call
        build_targeting_spec(geo_type=geo_type, geo_key=geo_key, age_min=raw.get("age_min"), age_max=raw.get("age_max"), gender=gender,
                             targeting=targeting, locales=languages)
        return cls(
            geo_type=geo_type, geo_key=geo_key, geo_label=str(geo.get("label") or "").strip()[:160],
            age_min=raw.get("age_min"), age_max=raw.get("age_max"), gender=gender,
            languages=tuple(languages), targeting=tuple(targeting),
            retail_budget=None if budget is None else float(budget), retail_cpm=None if cpm is None else float(cpm),
        )


_PLACEMENTS_NOTE = ("Meta counts people on its default placements (Facebook, Instagram, Messenger, Audience Network), as Ads Manager's Estimated "
                    "audience size does.")
_COMSCORE_NOTE = ("Meta's TV markets are Comscore markets, so a market's footprint can differ from the Nielsen markets used elsewhere in this app.")
_HISPANIC_NOTE = "Hispanic targeting isn't applied to a Meta audience estimate: use a language (Spanish) and Meta's own interests."
# A lower end of 0 is not "no audience": it is Meta saying the audience is under its reporting floor. The numbers stay (they are what the rule gives), the note says what they mean.
_TOO_NARROW_NOTE = ("Meta reports fewer than 1,000 accounts for these settings, so the audience is too narrow to plan on and the impressions come out as 0. "
                    "Widen the place, the age range or the interests.")


def estimate_response(req: MethodRequest) -> dict:
    """The estimate of a product forecast by Meta's reach estimate: the standard estimate plus a `meta` block (the audience range, the three
    monthly scenarios, what was asked, the budget cross-check when there is a budget, the language caveat when languages were chosen).

    The scenarios and the cross-check come from `model_auction`, the same rule POST /meta-forecast applies, so both routes give the same numbers.
    The flight is the monthly figure times `req.months`; `est_uniques` is the lower end, so the Avails step's frequency is the mid multiplier.
    ValueError (planner words) for a request Meta could not be asked; MetaAPIError for anything Meta-side."""
    args = MetaArgs.from_dict(req.args)
    lower, upper = fetch_reach_estimate(geo_type=args.geo_type, geo_key=args.geo_key, age_min=args.age_min, age_max=args.age_max,
                                        gender=args.gender, targeting=list(args.targeting), locales=list(args.languages))
    result = model_auction(product_name=req.product_name, retail_budget=args.retail_budget, audience_lower=lower, audience_upper=upper,
                           months=req.months, audience_source="meta_api", retail_cpm=args.retail_cpm, strict_product=False)
    scenarios = result["scenarios"]
    flight = scenarios["flight"]
    budget_check = None
    if result["retail_budget"] is not None and result["retail_cpm"] is not None:
        budget_check = {k: result[k] for k in ("retail_budget", "media_budget", "retail_cpm", "media_cpm_assumed", "retail_to_media_multiplier",
                                               "months", "budget_impressions")}
    block = {
        "audience": {"low": lower, "high": upper, "source": "meta_api"},
        "monthly": {platform_scenarios.SLOT[name]: scenarios["monthly"][name] for name in platform_scenarios.SCENARIOS},
        "scenarios": scenarios,
        "targeting": {
            "geo": {"type": args.geo_type, "key": args.geo_key, "label": args.geo_label or None},
            "age_min": args.age_min, "age_max": args.age_max, "gender": args.gender,
            "languages": [dict(item) for item in args.languages], "detailed": [dict(item) for item in args.targeting],
        },
        "budget_check": budget_check,
        "language_note": LANGUAGE_NOTE if args.languages else None,
        "as_of": result["as_of"],
    }
    notes = list(result["notes"]) + [_PLACEMENTS_NOTE]
    if lower == 0:
        notes.insert(0, _TOO_NARROW_NOTE)                      # first: the dock shows only the first few notes
    if args.geo_type == "geo_market":
        notes.append(_COMSCORE_NOTE)
    if req.hispanic_asked:
        notes.append(_HISPANIC_NOTE)
    return constanza_methods.standard_estimate(
        SPEC, product_name=req.product_name, months=req.months,
        imps={"low": flight["conservative"], "mid": flight["mid"], "high": flight["upper"]},
        est_uniques=lower, notes=notes, block=block,
    )


def _http_error(exc: Exception) -> tuple:
    return exc.status_code, str(exc)


def _product_info(product_name: str) -> dict:
    return {"retail_cpm": product_cpm(product_name)}


# The three paid SKUs are Meta's by default; the two branded-Pages SKUs ("... on Noticias Ya", "... on EVC Radio Pages") run on Entravision's own
# pages, which an ad-account audience estimate does not describe, so they are not listed (and an admin who maps one to Meta is warned).
SPEC = constanza_methods.register(MethodSpec(
    key="meta", label="Meta API (live)", short_label="Meta",
    description="A live audience-size estimate from Meta's own ad account (Facebook and Instagram), with the place, age, language and interests picked from Meta's lists.",
    kind="api", default_products=tuple(RETAIL_CPMS), owns_geography=True, request_field="meta",
    secrets=("META_ACCESS_TOKEN", "META_AD_ACCOUNT_ID"),
    estimate=estimate_response, status=status, diagnose="app.services.meta_diagnose:run_checks",
    errors=(MetaAPIError,), to_http=_http_error, product_info=_product_info,
))
