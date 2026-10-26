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
    targeting spec as ONE `flexible_spec` group, so the audience is "people who match ANY of the chosen interests / behaviors".

Setup (the app runs fine without it - every caller checks `is_configured()`):
    META_ACCESS_TOKEN     a long-lived token (a system user's is best) that can READ the ad account: the `ads_read` permission
    META_AD_ACCOUNT_ID    the ad account the estimates are asked through, with or without `act_`
  optional:
    META_GRAPH_API_VERSION   default v25.0 (the newest Marketing API version when this was written)
Read-only: a reach estimate and the targeting lists; nothing is ever created in the ad account. The token is only sent in the Authorization
header and is removed from every message this module raises.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone
from typing import Optional

import requests

from app import catalog
from app.services import platform_scenarios

log = logging.getLogger(__name__)


# Retail CPMs confirmed by the planning team. The 2x retail/media relationship implies a media CPM of half this value for the budget model.
RETAIL_CPMS = {
    "Facebook & Instagram Ads | Awareness": 12.0,
    "Facebook & Instagram Ads | Traffic / Conversion": 18.0,
    "Facebook & Instagram Ads | Lead Gen / Calls": 35.0,
}
RETAIL_TO_MEDIA = 2.0
DEFAULT_GRAPH_VERSION = "v25.0"
_GEO_FIELDS = {"country": "countries", "region": "regions", "city": "cities", "zip": "zips", "geo_market": "geo_markets"}
# The `type` of a detailed-targeting item = the key it has in a targeting spec's flexible_spec group.
TARGETING_KEYS = ("interests", "behaviors", "life_events", "industries", "income", "family_statuses")
MAX_TARGETING_ITEMS = 50
# kind -> the `class` Meta's adTargetingCategory browse takes (interests are also searchable by keyword: type=adinterest)
CATALOG_CLASSES = {"interests": "interests", "behaviors": "behaviors", "demographics": "demographics"}
_CATALOG_TTL = 12 * 3600
_ESTIMATE_TTL = 600


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


def model_auction(
    *, product_name: str, retail_budget: float, audience_lower: Optional[int] = None,
    audience_upper: Optional[int] = None, months: int = 1, audience_source: str = "manual",
    retail_cpm: Optional[float] = None,
) -> dict:
    """The planning numbers for one Meta line: three impression scenarios from the lower-end audience, and the budget cross-check.

    `audience_lower` is the LOWER end of the platform's range (what the scenarios multiply); `audience_upper` is context (a manual entry may
    give only the lower end). Without an audience there are no scenarios, only the budget figure."""
    default_cpm = product_cpm(product_name)
    if default_cpm is None:
        raise ValueError("Choose one of the three paid Facebook & Instagram Ads products.")
    cpm = default_cpm if retail_cpm is None else retail_cpm
    if not isinstance(retail_budget, (int, float)) or not (0 < retail_budget <= 1e9) or retail_budget != retail_budget:
        raise ValueError("Retail budget must be between $0 and $1 billion for the full flight.")
    if not isinstance(cpm, (int, float)) or not (0 < cpm <= 1e5) or cpm != cpm:
        raise ValueError("Retail CPM must be a positive number.")
    if not isinstance(months, int) or isinstance(months, bool) or not 1 <= months <= 120:
        raise ValueError("Months must be a whole number from 1 to 120.")
    if audience_upper is not None and audience_lower is None:
        raise ValueError("Give the lower end of the audience (the upper end alone is not a base).")
    if audience_lower is not None:
        if audience_upper is None:
            audience_upper = audience_lower
        if not (0 <= audience_lower <= audience_upper <= 1_000_000_000):
            raise ValueError("Audience bounds must be non-negative and ordered low to high.")
    if audience_source not in ("manual", "meta_api"):
        raise ValueError("Audience source must be manual or meta_api.")

    media_budget = retail_budget / RETAIL_TO_MEDIA
    media_cpm = cpm / RETAIL_TO_MEDIA
    budget_imps = round(media_budget * 1000 / media_cpm)
    scenarios = None
    if audience_lower is not None:
        public = platform_scenarios.to_public(audience_lower)
        monthly = {k: round(v) for k, v in public["monthly"].items()}
        flight = {k: round(v * months) for k, v in public["monthly"].items()}
        scenarios = {
            **public, "monthly": monthly, "months": months, "flight": flight,
            # what each scenario's flight total would cost at the planning CPM: a reality check against the line's budget, not a quote
            "retail_spend_at_flight": {k: round(v * cpm / 1000, 2) for k, v in flight.items()},
            "media_spend_at_flight": {k: round(v * media_cpm / 1000, 2) for k, v in flight.items()},
            "est_uniques": audience_lower,
        }
    return {
        "metric_type": "meta_audience_scenarios", "product_name": product_name, "as_of": datetime.now(timezone.utc).isoformat(),
        "retail_budget": round(retail_budget, 2), "media_budget": round(media_budget, 2),
        "retail_cpm": round(cpm, 2), "media_cpm_assumed": round(media_cpm, 2), "retail_to_media_multiplier": RETAIL_TO_MEDIA,
        "months": months, "budget_impressions": budget_imps,
        "potential_audience": ({"lower": audience_lower, "upper": audience_upper, "source": audience_source}
                               if audience_lower is not None else None),
        "scenarios": scenarios,
        "notes": [
            "Meta's audience size counts eligible accounts; it is not delivered reach and not a delivery forecast.",
            f"Monthly impressions are {platform_scenarios.describe()}; mid is the default and the figure the Avails step takes. A flight is the "
            "monthly figure times its months.",
            "Budget impressions (what the retail budget buys at the planning CPM, 2x retail/media) are a cross-check, not avails.",
        ],
    }


# ----------------------------------------------------------------------------------------------------------------- configuration

def missing_config() -> list[str]:
    return [name for name in ("META_ACCESS_TOKEN", "META_AD_ACCOUNT_ID") if not (os.environ.get(name) or "").strip()]


def is_configured() -> bool:
    return not missing_config()


def _account() -> str:
    account = (os.environ.get("META_AD_ACCOUNT_ID") or "").strip().removeprefix("act_")
    if not re.fullmatch(r"\d{1,30}", account):
        raise MetaAPIError("META_AD_ACCOUNT_ID must be a numeric Meta ad account ID (digits, with or without act_).", "not_configured")
    return account


def account_hint() -> Optional[str]:
    """The ad account as its last four digits - the only part of it ever shown."""
    raw = (os.environ.get("META_AD_ACCOUNT_ID") or "").strip().removeprefix("act_")
    return f"…{raw[-4:]}" if raw else None


def graph_version() -> str:
    version = (os.environ.get("META_GRAPH_API_VERSION") or DEFAULT_GRAPH_VERSION).strip()
    if not re.fullmatch(r"v\d{1,2}\.0", version):
        raise MetaAPIError("META_GRAPH_API_VERSION must look like v25.0.", "not_configured")
    return version


def status() -> dict:
    """Whether the Meta connection is set up - which settings are missing, never their values - and the planning multipliers."""
    missing = missing_config()
    return {"configured": not missing, "missing": missing, "account": account_hint(), "version": os.environ.get("META_GRAPH_API_VERSION") or DEFAULT_GRAPH_VERSION,
            "scenarios": platform_scenarios.to_public(), "setup_hint": SETUP_HINT}


SETUP_HINT = ("Add META_ACCESS_TOKEN (a token with the ads_read permission for the ad account) and META_AD_ACCOUNT_ID in the app's secrets "
              "(Replit -> Secrets), then run python -m app.services.meta_diagnose.")


# ----------------------------------------------------------------------------------------------------------------- the Graph API

def _scrub(text: str) -> str:
    """A Meta message with the token and the ad account removed (it must never reach a log, an exception or the browser)."""
    out = str(text or "")
    token = (os.environ.get("META_ACCESS_TOKEN") or "").strip()
    if token:
        out = out.replace(token, "[token]")
    account = (os.environ.get("META_AD_ACCOUNT_ID") or "").strip().removeprefix("act_")
    if account:
        out = out.replace(account, "…" + account[-4:])
    return out[:300]


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
    if code in (4, 17, 32, 341, 613, 80000, 80003, 80004) or http_status == 429:
        return MetaAPIError("Meta is rate-limiting this ad account. Wait a minute and pull again.", "rate_limit", 429)
    if code == 2641:
        return MetaAPIError("Meta cannot estimate this audience: it includes or excludes locations that are currently restricted.", "settings", 422)
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
    try:
        response = requests.get(url, params=params, headers={"Authorization": f"Bearer {os.environ['META_ACCESS_TOKEN'].strip()}"}, timeout=12)
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
        for r in result.get("data", []) if isinstance(r, dict) and r.get("key") is not None
    ]


def _size(row: dict, side: str) -> Optional[int]:
    raw = row.get(f"audience_size_{side}_bound")
    if raw is None and side == "lower":
        raw = row.get("audience_size")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def _item(row: dict, kind: str) -> Optional[dict]:
    """One detailed-targeting option as the panel shows it; None for a row without an id or a name."""
    if not isinstance(row, dict) or row.get("id") is None or not str(row.get("name") or "").strip():
        return None
    item_type = str(row.get("type") or kind)
    if item_type not in TARGETING_KEYS:
        item_type = kind if kind in TARGETING_KEYS else item_type
    path = row.get("path")
    return {"id": str(row["id"]), "name": str(row["name"]).strip(), "type": item_type,
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
        items = [i for i in (_item(r, kind if kind != "demographics" else "life_events") for r in result.get("data", [])) if i]
        return sorted(items, key=lambda i: i["name"].lower())
    if not is_configured():
        return fetch()                                        # raises the "not configured" message
    return list(_cached(f"catalog:{graph_version()}:{_account()}:{kind}", _CATALOG_TTL, fetch))


def search_targeting(q: str) -> dict[str, list[dict]]:
    """What a keyword finds: interests (Meta's own keyword search, `type=adinterest`) plus the behaviors and demographics whose name or path
    contains the words (from the cached lists). {"interests": [...], "behaviors": [...], "demographics": [...]}."""
    q = (q or "").strip()
    if not 2 <= len(q) <= 80:
        raise ValueError("Search text must be 2–80 characters.")
    result = _graph_get("search", {"type": "adinterest", "q": q, "limit": 25})
    interests = [i for i in (_item(r, "interests") for r in result.get("data", [])) if i]
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
        if not re.fullmatch(r"\d{1,30}", item_id) or not 1 <= len(name) <= 120 or kind not in TARGETING_KEYS:
            raise ValueError("Each interest or behavior needs a numeric Meta id, a name and a known type; pick it from Meta's list.")
        if (kind, item_id) not in seen:
            seen.add((kind, item_id))
            out.append({"id": item_id, "name": name, "type": kind})
    return out


def build_targeting_spec(*, geo_type: str, geo_key: str, age_min: Optional[int] = None, age_max: Optional[int] = None,
                         gender: Optional[str] = None, targeting=None) -> dict:
    """The targeting_spec sent to Meta for explicitly selected geography, age, gender and interests / behaviors. ValueError for a choice Meta
    would refuse. Chosen options form ONE flexible_spec group: the audience is people who match ANY of them."""
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
    return spec


def fetch_reach_estimate(*, geo_type: str, geo_key: str, age_min: Optional[int] = None, age_max: Optional[int] = None,
                         gender: Optional[str] = None, targeting=None) -> tuple[int, int]:
    """Meta's potential audience (lower, upper) for explicitly selected targeting - the account-scoped `reachestimate`."""
    spec = build_targeting_spec(geo_type=geo_type, geo_key=geo_key, age_min=age_min, age_max=age_max, gender=gender, targeting=targeting)
    missing = missing_config()
    if missing:
        raise MetaAPIError(f"Meta is not configured: add {' and '.join(missing)} in the app's secrets (Replit -> Secrets).", "not_configured", 503)
    account = _account()
    body = json.dumps(spec, separators=(",", ":"), sort_keys=True)

    def fetch():
        result = _graph_get(f"act_{account}/reachestimate", {"targeting_spec": body, "fields": "users_lower_bound,users_upper_bound,estimate_ready"})
        # the reachestimate edge answers with a one-item `data` list (Meta's reference: "an array containing a single AdAccountReachEstimate node");
        # a bare object is read the same way
        row = result["data"][0] if isinstance(result.get("data"), list) and result["data"] and isinstance(result["data"][0], dict) else result
        if row.get("estimate_ready") is False or result.get("estimate_ready") is False:
            raise MetaAPIError("Meta is still preparing this audience estimate. Try again shortly.", "not_ready", 503)
        try:
            lower, upper = int(row["users_lower_bound"]), int(row["users_upper_bound"])
        except (KeyError, TypeError, ValueError):
            raise MetaAPIError("Meta did not return audience bounds for that targeting.", "meta", 502) from None
        if lower < 0 or upper < 0:
            raise MetaAPIError("Meta cannot estimate this audience (it returned no figure, which does not mean the audience is empty). "
                               "Check the targeting in Ads Manager.", "unavailable", 422)
        if upper < lower:
            raise MetaAPIError("Meta returned invalid audience bounds for that targeting.", "meta", 502)
        return lower, upper
    return _cached(f"reach:{graph_version()}:{account}:{body}", _ESTIMATE_TTL, fetch)
