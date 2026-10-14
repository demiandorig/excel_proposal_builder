"""Pure full-flight auction planning math shared by Meta and YouTube.

This models a proposal at team CPM assumptions. It does not claim platform
delivery, reserve inventory, or provide a monthly avails/SOV denominator.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Optional


RETAIL_TO_MEDIA = 2.0
DEFAULT_FREQUENCY = 5.5
HIGH_FREQUENCY_SCENARIOS = (9.0, 10.0)


def model_paid_media(
    *, product_name: str, retail_budget: float, retail_cpm: float, metric_type: str,
    audience_lower: Optional[int] = None, audience_upper: Optional[int] = None,
    reach_share: Optional[float] = None, frequency: float = DEFAULT_FREQUENCY,
    audience_source: str = "manual", notes: Optional[list[str]] = None,
) -> dict:
    """Calculate full-flight spend and audience scenarios, with provenance.

    An audience estimate by itself is never treated as delivered reach.
    A combined delivery model is returned only with an explicit reach share.
    """
    if not math.isfinite(retail_budget) or not 0 < retail_budget <= 1e9:
        raise ValueError("Retail budget must be between $0 and $1 billion for the full flight.")
    if not math.isfinite(retail_cpm) or not 0 < retail_cpm <= 1e5:
        raise ValueError("Retail CPM must be a positive number.")
    if not math.isfinite(frequency) or not 0 < frequency <= 100:
        raise ValueError("Average frequency must be between 0 and 100 for the full flight.")
    if (audience_lower is None) != (audience_upper is None):
        raise ValueError("Give both audience bounds, or neither.")
    if audience_lower is not None and not (0 <= audience_lower <= audience_upper <= 1_000_000_000):
        raise ValueError("Audience bounds must be non-negative and ordered low to high.")
    if reach_share is not None and (not math.isfinite(reach_share) or not 0 < reach_share <= 1):
        raise ValueError("Reach share must be greater than 0% and at most 100%.")
    if reach_share is not None and audience_lower is None:
        raise ValueError("An audience estimate is required to apply a reach share.")
    if audience_source not in ("manual", "meta_api"):
        raise ValueError("Audience source must be manual or meta_api.")

    media_budget = retail_budget / RETAIL_TO_MEDIA
    media_cpm = retail_cpm / RETAIL_TO_MEDIA
    budget_imps = round(media_budget * 1000 / media_cpm)
    audience_mid = round((audience_lower + audience_upper) / 2) if audience_lower is not None else None
    audience_scenario = None
    delivery = None
    if audience_mid is not None:
        average_imps = round(audience_mid * frequency)
        high_9_imps = round(audience_mid * HIGH_FREQUENCY_SCENARIOS[0])
        high_10_imps = round(audience_mid * HIGH_FREQUENCY_SCENARIOS[1])
        audience_scenario = {
            "at_average_frequency": average_imps,
            "at_frequency_9": high_9_imps,
            "at_frequency_10": high_10_imps,
            "retail_spend_at_average_frequency": round(average_imps * retail_cpm / 1000, 2),
            "media_spend_at_average_frequency": round(average_imps * media_cpm / 1000, 2),
            "retail_spend_at_frequency_9": round(high_9_imps * retail_cpm / 1000, 2),
            "retail_spend_at_frequency_10": round(high_10_imps * retail_cpm / 1000, 2),
        }
        if reach_share is not None:
            imps = min(budget_imps, round(audience_mid * reach_share * frequency))
            delivery = {
                "impressions_low": min(budget_imps, round(audience_lower * reach_share * frequency)),
                "impressions": imps,
                "impressions_high": min(budget_imps, round(audience_upper * reach_share * frequency)),
                "reach": round(imps / frequency), "reach_share": reach_share,
            }

    return {
        "metric_type": metric_type, "unit": "full_flight",
        "product_name": product_name, "as_of": datetime.now(timezone.utc).isoformat(),
        "retail_budget": round(retail_budget, 2), "media_budget": round(media_budget, 2),
        "retail_cpm": round(retail_cpm, 2), "media_cpm_assumed": round(media_cpm, 2),
        "retail_to_media_multiplier": RETAIL_TO_MEDIA, "frequency": frequency,
        "budget_impressions": budget_imps,
        "potential_audience": {"lower": audience_lower, "mid": audience_mid, "upper": audience_upper, "source": audience_source}
        if audience_mid is not None else None,
        "audience_scenario": audience_scenario, "modeled_delivery": delivery,
        "notes": notes or [],
    }
