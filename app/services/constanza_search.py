"""
Constanza's search-interest grounding — the Google Ads API integration
that refines an audience estimate's geographic skew using real search
volume, when configured. Follows the same env-var + is_configured() +
<Platform>APIError degrade pattern as app/services/notion_client.py.

Why Google Ads, not Google Trends: Trends' only free access path
(pytrends) scrapes a site whose ToS prohibits automated access and was
archived by its own maintainers in April 2025; the real Trends API is
still alpha-gated with no self-serve access. Trends' 0-100 index is also
a RATIO normalized against its own shifting peak — it carries no
information about absolute volume, so two geographies can show an
identical score with a 10x difference in real audience size. Google Ads'
`GenerateKeywordHistoricalMetrics` returns real (Google-bucketed but
absolute) monthly search volume, geo-targetable down to city/postal-code/
congressional-district/DMA — the right shape of data for this. See the
Phase 2 plan doc for the full citation trail.

IMPORTANT GOTCHA FOR WHOEVER WIRES REAL CREDENTIALS: verified against
Google's own published geo-targets file (developers.google.com/google-ads/
api/data/geotargets) that there is NO "DMA Region" (or "TV Region")
target type covering Nielsen's 210 US media markets — only 18 "TV Region"
entries exist worldwide and they are not Nielsen DMAs. This module's
"parent DMA baseline" language should be read as "parent baseline
geography" — in practice that will need to be the area's parent STATE
(a real Google Ads target type) instead of its parent DMA, since no DMA-
level Google Ads geo-target exists to query. `scripts/refresh_search_interest.py`
(not yet written — build it when real Google Ads credentials are
available) will need its own ZIP/county/city -> Google Ads geo-target-
criteria-ID crosswalk (built by name-matching against Google's public
geo-targets CSV) as part of that work.

THIS MODULE IS NOT LIVE-TESTED. It requires a Google Ads manager (MCC)
account + developer token this environment doesn't have — built correct
and gated behind config, exactly like notion_client.py/drive_uploader.py
degrade when their own credentials are absent, so it activates the
moment real credentials are added without any code change. The one-time
policy question this doesn't resolve on its own: confirm with Google Ads
API support that Basic-tier access covers this non-ad-buying downstream
use case before relying on it at real volume (an access/policy question,
not an engineering one).

CORRECTED RENORMALIZATION (the bug an adversarial design review caught
in the first draft): the multiplier for one area is ALWAYS computed
relative to that area's own PARENT DMA's search volume — a fixed external
baseline — never relative to "the other areas resolved in this same
query." The first-draft version normalized against the query's own
resolved-area set, which collapses to a no-op 1.0 for the single most
common query shape (a lone city/ZIP) — exactly the scenario ("Sacramento
the city") that motivated this feature in the first place.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

_DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "constanza"
_CACHE_PATH = _DATA_DIR / "search_interest_by_geo.json"

# Clamp so one sparse/low-volume keyword sample can't swing an estimate
# wildly in either direction — see the plan doc's §5 correction.
_MULTIPLIER_MIN = 0.3
_MULTIPLIER_MAX = 3.0


class GoogleAdsAPIError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def is_configured() -> bool:
    return bool(
        os.environ.get("GOOGLE_ADS_DEVELOPER_TOKEN")
        and os.environ.get("GOOGLE_ADS_CLIENT_ID")
        and os.environ.get("GOOGLE_ADS_CLIENT_SECRET")
        and os.environ.get("GOOGLE_ADS_REFRESH_TOKEN")
        and os.environ.get("GOOGLE_ADS_LOGIN_CUSTOMER_ID")
    )


@dataclass
class SearchVolume:
    keyword_set: str
    geo_target_id: str
    monthly_volume: float
    vintage: str


@lru_cache(maxsize=1)
def _load_cache() -> dict:
    """The offline-refreshed snapshot scripts/refresh_search_interest.py
    produces — never a live call in the request path (matching Constanza's
    other data sources). Missing/empty until that script has been run
    against real Google Ads credentials."""
    if not _CACHE_PATH.exists():
        return {}
    return json.loads(_CACHE_PATH.read_text())


def geo_interest_multiplier(keyword_set: str, area_geo_target_id: str | None, dma_geo_target_id: str | None) -> tuple[float, bool]:
    """Returns (multiplier, applied). `applied=False` (multiplier=1.0, a
    true no-op) whenever the cache is empty, either geo target id is
    missing, or no volume was found for one side of the ratio — fails
    open rather than blocking the estimate, per the plan's fail-open
    design. `applied=True` only when a real area-vs-parent-DMA ratio was
    actually computed."""
    cache = _load_cache()
    if not cache or area_geo_target_id is None or dma_geo_target_id is None:
        return 1.0, False

    area_entry = cache.get(keyword_set, {}).get(area_geo_target_id)
    dma_entry = cache.get(keyword_set, {}).get(dma_geo_target_id)
    if not area_entry or not dma_entry or not dma_entry.get("monthly_volume"):
        return 1.0, False

    raw_multiplier = area_entry["monthly_volume"] / dma_entry["monthly_volume"]
    return max(_MULTIPLIER_MIN, min(_MULTIPLIER_MAX, raw_multiplier)), True


def fetch_historical_metrics(keywords: list[str], geo_target_ids: list[str]) -> list[SearchVolume]:
    """Calls Google Ads API's KeywordPlanIdeaService.GenerateKeywordHistoricalMetrics.
    Only used by scripts/refresh_search_interest.py (an offline batch job,
    never per-request) — raises GoogleAdsAPIError on any failure rather
    than degrading silently, since a failed offline refresh should be
    loud, unlike a failed live call in a request path."""
    if not is_configured():
        raise GoogleAdsAPIError(
            "Google Ads API not configured — set GOOGLE_ADS_DEVELOPER_TOKEN, "
            "GOOGLE_ADS_CLIENT_ID, GOOGLE_ADS_CLIENT_SECRET, GOOGLE_ADS_REFRESH_TOKEN, "
            "and GOOGLE_ADS_LOGIN_CUSTOMER_ID."
        )
    try:
        from google.ads.googleads.client import GoogleAdsClient
    except ImportError as e:
        raise GoogleAdsAPIError(
            "google-ads package not installed — run: pip install google-ads"
        ) from e

    client = GoogleAdsClient.load_from_dict({
        "developer_token": os.environ["GOOGLE_ADS_DEVELOPER_TOKEN"],
        "client_id": os.environ["GOOGLE_ADS_CLIENT_ID"],
        "client_secret": os.environ["GOOGLE_ADS_CLIENT_SECRET"],
        "refresh_token": os.environ["GOOGLE_ADS_REFRESH_TOKEN"],
        "login_customer_id": os.environ["GOOGLE_ADS_LOGIN_CUSTOMER_ID"],
        "use_proto_plus": True,
    })
    keyword_plan_idea_service = client.get_service("KeywordPlanIdeaService")
    request = client.get_type("GenerateKeywordHistoricalMetricsRequest")
    request.customer_id = os.environ["GOOGLE_ADS_LOGIN_CUSTOMER_ID"]
    request.keywords.extend(keywords)
    request.geo_target_constants.extend(
        f"geoTargetConstants/{gid}" for gid in geo_target_ids
    )
    try:
        response = keyword_plan_idea_service.generate_keyword_historical_metrics(request=request)
    except Exception as e:
        raise GoogleAdsAPIError(f"Google Ads API call failed: {e}") from e

    keyword_set = ",".join(sorted(keywords))
    results = []
    for result in response.results:
        metrics = result.keyword_metrics
        results.append(SearchVolume(
            keyword_set=keyword_set,
            geo_target_id=",".join(geo_target_ids),  # the API returns volume aggregated across the whole target set, not per-target — see module docstring's cost note
            monthly_volume=metrics.avg_monthly_searches or 0,
            vintage=time.strftime("%Y-%m"),
        ))
    return results
