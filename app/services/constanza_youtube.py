"""YouTube planning CPMs while live Google reach planning is being connected."""
from __future__ import annotations

from typing import Optional

from app import catalog
from app.services import paid_media_planning


RETAIL_CPMS = {"awareness": 15.0, "demand_gen": 20.0}
PRODUCT_OBJECTIVES = {
    "YouTube Ads - Skippable": ("awareness",),
    "YouTube Ads - Non-Skippable": ("awareness",),
    "YouTube Ads - Demand Gen": ("demand_gen",),
}


def objectives_for_product(product_name: str) -> tuple[str, ...]:
    if product_name in PRODUCT_OBJECTIVES:
        return PRODUCT_OBJECTIVES[product_name]
    try:
        matches = catalog.name_variants(product_name) & PRODUCT_OBJECTIVES.keys()
    except Exception:
        return ()
    return PRODUCT_OBJECTIVES[next(iter(matches))] if len(matches) == 1 else ()


def model_youtube(
    *, product_name: str, objective: str, retail_budget: float,
    audience_lower: Optional[int] = None, audience_upper: Optional[int] = None,
    reach_share: Optional[float] = None,
    frequency: float = paid_media_planning.DEFAULT_FREQUENCY,
    retail_cpm: Optional[float] = None,
) -> dict:
    """Quote the team CPM model without claiming a live Google forecast."""
    if objective not in objectives_for_product(product_name):
        raise ValueError("Choose a supported YouTube product and planning objective.")
    result = paid_media_planning.model_paid_media(
        product_name=product_name, retail_budget=retail_budget,
        retail_cpm=RETAIL_CPMS[objective] if retail_cpm is None else retail_cpm,
        metric_type="youtube_manual_planning_model",
        audience_lower=audience_lower, audience_upper=audience_upper,
        reach_share=reach_share, frequency=frequency, audience_source="manual",
        notes=[
            "The audience, when entered, is a manually sourced eligible-audience estimate, not Google forecasted reach.",
            "Budget impressions and audience × frequency use team retail CPM assumptions, not a live auction price.",
            "Demand Gen can serve outside YouTube unless the Google Ads campaign's channels are restricted appropriately.",
            "No Google ReachPlanService result or inventory availability is represented here.",
        ],
    )
    result["objective"] = objective
    return result
