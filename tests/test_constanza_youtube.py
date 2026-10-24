"""YouTube CPM planning must remain distinct from Google's live reach forecast."""
import pytest
from fastapi import HTTPException

import app.main as main
from app import catalog
from app.services import constanza_youtube as youtube
from app.services.notion_parser import PRODUCT_ALIASES


def test_youtube_awareness_uses_15_retail_cpm_and_two_to_one_spend():
    result = youtube.model_youtube(
        product_name="YouTube Ads - Skippable", objective="awareness", retail_budget=150,
        audience_lower=1000, audience_upper=1000,
    )
    assert result["retail_cpm"] == 15
    assert result["media_cpm_assumed"] == 7.5
    assert result["media_budget"] == 75
    assert result["budget_impressions"] == 10_000
    assert result["audience_scenario"]["at_average_frequency"] == 5500
    assert result["audience_scenario"]["retail_spend_at_average_frequency"] == 82.5
    assert result["audience_scenario"]["media_spend_at_average_frequency"] == 41.25
    assert result["modeled_delivery"] is None


def test_youtube_demand_gen_uses_20_retail_cpm_and_is_not_live_avails():
    assert any(p.name == "YouTube Ads - Demand Gen" for p in catalog.CATALOG)
    assert PRODUCT_ALIASES["youtube demand gen"] == "YouTube Ads - Demand Gen"
    result = youtube.model_youtube(
        product_name="YouTube Ads - Demand Gen", objective="demand_gen", retail_budget=200,
        audience_lower=1000, audience_upper=1000, reach_share=0.6,
    )
    assert result["retail_cpm"] == 20 and result["media_budget"] == 100
    assert result["budget_impressions"] == 10_000
    assert result["audience_scenario"]["retail_spend_at_average_frequency"] == 110
    assert result["modeled_delivery"]["impressions"] == 3300
    assert result["metric_type"] == "youtube_manual_planning_model"
    assert result["potential_audience"]["source"] == "manual"


def test_demand_gen_cannot_be_silently_mapped_to_non_skippable():
    with pytest.raises(ValueError, match="supported YouTube"):
        youtube.model_youtube(
            product_name="YouTube Ads - Non-Skippable", objective="demand_gen", retail_budget=200,
        )


def test_admin_cpm_override_is_reflected_in_planning_math():
    result = youtube.model_youtube(
        product_name="YouTube Ads - Demand Gen", objective="demand_gen",
        retail_budget=180, retail_cpm=18,
    )
    assert result["retail_cpm"] == 18 and result["budget_impressions"] == 10_000


def test_youtube_api_validation_and_snapshot():
    result = main.constanza_youtube_forecast(main.YouTubeForecastRequest(
        product_name="YouTube Ads - Non-Skippable", objective="awareness", retail_budget=150,
    ))
    saved = main.AvailsEntry(youtube_forecast=result).model_dump()
    reopened = main.AvailsEntry(**saved)
    assert reopened.youtube_forecast["retail_cpm"] == 15
    assert reopened.max_imps is None and reopened.max_spend is None
    with pytest.raises(HTTPException) as error:
        main.constanza_youtube_forecast(main.YouTubeForecastRequest(
            product_name="YouTube Ads - Non-Skippable", objective="demand_gen", retail_budget=150,
        ))
    assert error.value.status_code == 422
