"""Meta auction planning: live eligible audience and a separate, budget-based model.

Meta's reachestimate is an eligible-audience range, not delivered reach or
reservable impressions. Keep these figures separate from Constanza's inventory
ceiling and from the proposal's max_imps / max_spend fields.
"""
from __future__ import annotations

import os
import re
from typing import Optional

import requests

from app import catalog
from app.services import paid_media_planning


# Retail CPMs confirmed by the planning team. The 2x retail/media relationship
# implies a media CPM of half this value for the budget model.
RETAIL_CPMS = {
    "Facebook & Instagram Ads | Awareness": 12.0,
    "Facebook & Instagram Ads | Traffic / Conversion": 18.0,
    "Facebook & Instagram Ads | Lead Gen / Calls": 35.0,
}
DEFAULT_FREQUENCY = paid_media_planning.DEFAULT_FREQUENCY  # entire campaign flight
_GEO_FIELDS = {"country": "countries", "region": "regions", "city": "cities", "zip": "zips", "geo_market": "geo_markets"}


class MetaAPIError(Exception):
    """A safe, user-facing Meta configuration or API failure."""


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
    audience_upper: Optional[int] = None, reach_share: Optional[float] = None,
    frequency: float = DEFAULT_FREQUENCY, audience_source: str = "manual",
    retail_cpm: Optional[float] = None,
) -> dict:
    """Return full-flight planning metrics, never an inventory guarantee.

    A combined modeled delivery figure requires an explicitly supplied reach
    share. Without historical calibration, show the budget and audience
    scenarios independently instead of silently assuming 100% reach.
    """
    default_cpm = product_cpm(product_name)
    if default_cpm is None:
        raise ValueError("Choose one of the three paid Facebook & Instagram Ads products.")
    return paid_media_planning.model_paid_media(
        product_name=product_name, retail_budget=retail_budget,
        retail_cpm=default_cpm if retail_cpm is None else retail_cpm,
        metric_type="meta_auction_planning_model",
        audience_lower=audience_lower, audience_upper=audience_upper,
        reach_share=reach_share, frequency=frequency, audience_source=audience_source,
        notes=[
            "Meta's audience size counts eligible accounts; it is not delivered reach.",
            "Audience × frequency and its implied spend assume every eligible account is reached; the 9–10 values are scenarios, not guaranteed avails.",
            "Budget impressions use the team's retail CPM and 2× retail/media rule, not a live auction price.",
        ],
    )


def is_configured() -> bool:
    return bool(os.environ.get("META_ACCESS_TOKEN") and os.environ.get("META_AD_ACCOUNT_ID"))


def _graph_get(path: str, params: dict) -> dict:
    if not is_configured():
        raise MetaAPIError("Meta is not configured. Add META_ACCESS_TOKEN and META_AD_ACCOUNT_ID in Replit Secrets.")
    version = os.environ.get("META_GRAPH_API_VERSION", "v26.0")
    if not re.fullmatch(r"v\d{1,2}\.0", version):
        raise MetaAPIError("META_GRAPH_API_VERSION must look like v26.0.")
    url = f"https://graph.facebook.com/{version}/{path}"
    try:
        response = requests.get(url, params=params, headers={"Authorization": f"Bearer {os.environ['META_ACCESS_TOKEN']}"}, timeout=12)
        response.raise_for_status()
        result = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise MetaAPIError("Meta could not return an audience estimate. Check account access and targeting in Ads Manager.") from exc
    if not isinstance(result, dict) or "error" in result:
        raise MetaAPIError("Meta rejected the targeting or account access. Check the selected geography in Ads Manager.")
    return result


def search_geography(query: str, location_type: str) -> list[dict]:
    """Candidate geographies for explicit planner selection; never guess a DMA."""
    if location_type not in _GEO_FIELDS:
        raise ValueError("Choose country, region, city, ZIP, or geo market.")
    query = query.strip()
    if not 2 <= len(query) <= 100:
        raise ValueError("Search text must be 2–100 characters.")
    result = _graph_get("search", {"type": "adgeolocation", "location_types": f'["{location_type}"]', "q": query, "limit": 20})
    return [
        {"key": str(r["key"]), "name": str(r.get("name") or ""), "type": location_type,
         "region": str(r.get("region") or ""), "country_code": str(r.get("country_code") or "")}
        for r in result.get("data", []) if isinstance(r, dict) and r.get("key") is not None
    ]


def fetch_reach_estimate(*, geo_type: str, geo_key: str, age_min: Optional[int] = None,
                         age_max: Optional[int] = None, gender: Optional[str] = None) -> tuple[int, int]:
    """Fetch Meta's potential audience for explicitly selected targeting."""
    if geo_type not in _GEO_FIELDS or not re.fullmatch(r"[A-Za-z0-9_:-]{1,40}", geo_key):
        raise ValueError("Select a valid Meta geography from the search results.")
    if age_min is not None and not 18 <= age_min <= 65:
        raise ValueError("Meta minimum age must be 18–65.")
    if age_max is not None and not 18 <= age_max <= 65:
        raise ValueError("Meta maximum age must be 18–65.")
    if age_min is not None and age_max is not None and age_min > age_max:
        raise ValueError("Minimum age cannot exceed maximum age.")
    if gender not in (None, "all", "male", "female"):
        raise ValueError("Gender must be all, male, or female.")
    import json
    value = geo_key if geo_type == "country" else {"key": geo_key}
    targeting = {"geo_locations": {_GEO_FIELDS[geo_type]: [value]}}
    if age_min is not None:
        targeting["age_min"] = age_min
    if age_max is not None:
        targeting["age_max"] = age_max
    if gender in ("male", "female"):
        targeting["genders"] = [1 if gender == "male" else 2]
    if not is_configured():
        raise MetaAPIError("Meta is not configured. Add META_ACCESS_TOKEN and META_AD_ACCOUNT_ID in Replit Secrets.")
    account = os.environ.get("META_AD_ACCOUNT_ID", "").removeprefix("act_")
    if not re.fullmatch(r"\d{1,30}", account):
        raise MetaAPIError("META_AD_ACCOUNT_ID must be a numeric Meta ad account ID.")
    result = _graph_get(f"act_{account}/reachestimate", {"targeting_spec": json.dumps(targeting, separators=(",", ":")),
                                                        "fields": "users_lower_bound,users_upper_bound,estimate_ready"})
    if result.get("estimate_ready") is False:
        raise MetaAPIError("Meta is still preparing this audience estimate. Try again shortly.")
    try:
        lower = int(result["users_lower_bound"])
        upper = int(result["users_upper_bound"])
    except (KeyError, TypeError, ValueError) as exc:
        raise MetaAPIError("Meta did not return audience bounds for that targeting.") from exc
    if lower < 0 or upper < lower:
        raise MetaAPIError("Meta returned invalid audience bounds for that targeting.")
    return lower, upper
