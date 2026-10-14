"""Meta auction forecasts preserve the audience/delivery distinction and pricing rule."""
from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

import app.main as main
from app.services import constanza_meta as meta


@pytest.mark.parametrize("name,cpm", [
    ("Facebook & Instagram Ads | Awareness", 12),
    ("Facebook & Instagram Ads | Traffic / Conversion", 18),
    ("Facebook & Instagram Ads | Lead Gen / Calls", 35),
])
def test_confirmed_retail_cpms_and_two_to_one_media_budget(name, cpm):
    result = meta.model_auction(product_name=name, retail_budget=2000)
    assert result["retail_cpm"] == cpm
    assert result["media_budget"] == 1000
    assert result["media_cpm_assumed"] == cpm / 2
    assert result["budget_impressions"] == round(2000 * 1000 / cpm)
    assert result["modeled_delivery"] is None  # no assumed 100% reach


def test_audience_size_times_frequency_is_only_a_scenario():
    result = meta.model_auction(
        product_name="Facebook & Instagram Ads | Awareness", retail_budget=200,
        audience_lower=1000, audience_upper=1000,
    )
    assert result["potential_audience"]["mid"] == 1000
    assert result["audience_scenario"] == {
        "at_average_frequency": 5500, "at_frequency_9": 9000, "at_frequency_10": 10000,
        "retail_spend_at_average_frequency": 66, "media_spend_at_average_frequency": 33,
        "retail_spend_at_frequency_9": 108, "retail_spend_at_frequency_10": 120,
    }
    assert result["budget_impressions"] == round(200 * 1000 / 12)
    assert result["modeled_delivery"] is None
    assert result["unit"] == "full_flight" and result["metric_type"] != "inventory_avails"


def test_calibrated_reach_share_and_budget_both_constrain_modeled_delivery():
    name = "Facebook & Instagram Ads | Awareness"
    audience_limited = meta.model_auction(
        product_name=name, retail_budget=2000, audience_lower=1000, audience_upper=1000, reach_share=0.6,
    )
    assert audience_limited["modeled_delivery"] == {
        "impressions_low": 3300, "impressions": 3300, "impressions_high": 3300, "reach": 600, "reach_share": 0.6,
    }
    budget_limited = meta.model_auction(
        product_name=name, retail_budget=12, audience_lower=1000, audience_upper=1000, reach_share=0.6,
    )
    assert budget_limited["modeled_delivery"]["impressions"] == 1000
    assert budget_limited["modeled_delivery"]["reach"] == 182


def test_branded_content_cannot_use_paid_meta_auction_model():
    with pytest.raises(ValueError, match="paid Facebook"):
        meta.model_auction(product_name="Facebook & Instagram Ads on EVC Radio Pages", retail_budget=1000)


def test_renamed_paid_sku_keeps_its_confirmed_cpm(monkeypatch):
    monkeypatch.setattr(meta.catalog, "name_variants", lambda name: {name, "Facebook & Instagram Ads | Awareness"})
    assert meta.product_cpm("Renamed Awareness SKU") == 12


def test_unconfigured_live_pull_has_actionable_error(monkeypatch):
    monkeypatch.delenv("META_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("META_AD_ACCOUNT_ID", raising=False)
    with pytest.raises(meta.MetaAPIError, match="Replit Secrets"):
        meta.fetch_reach_estimate(geo_type="country", geo_key="US")


@pytest.mark.parametrize("overrides", [
    {"retail_budget": -1}, {"retail_budget": float("nan")},
    {"audience_lower": 300, "audience_upper": 200},
    {"audience_lower": 100, "audience_upper": 100, "reach_share": 1.1},
    {"reach_share": 0.5}, {"frequency": 0},
])
def test_invalid_inputs_fail_before_creating_forecast(overrides):
    args = dict(product_name="Facebook & Instagram Ads | Awareness", retail_budget=100)
    args.update(overrides)
    with pytest.raises(ValueError):
        meta.model_auction(**args)


def test_live_reach_request_uses_server_secret_and_selected_targeting(monkeypatch):
    monkeypatch.setenv("META_ACCESS_TOKEN", "test-private-token")
    monkeypatch.setenv("META_AD_ACCOUNT_ID", "act_12345")
    seen = {}

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"users_lower_bound": 900, "users_upper_bound": 1100, "estimate_ready": True}

    def fake_get(url, **kwargs):
        seen.update(url=url, **kwargs)
        return Response()

    monkeypatch.setattr(meta.requests, "get", fake_get)
    assert meta.fetch_reach_estimate(geo_type="geo_market", geo_key="123", age_min=25, gender="female") == (900, 1100)
    assert "test-private-token" not in seen["url"]
    assert seen["headers"] == {"Authorization": "Bearer test-private-token"}
    target = json.loads(seen["params"]["targeting_spec"])
    assert target == {"geo_locations": {"geo_markets": [{"key": "123"}]}, "age_min": 25, "genders": [2]}


def test_api_manual_and_live_paths_keep_provenance_separate(monkeypatch):
    name = "Facebook & Instagram Ads | Lead Gen / Calls"
    manual = main.constanza_meta_forecast(main.MetaAuctionForecastRequest(
        product_name=name, retail_budget=700, audience_lower=1000, audience_upper=1200,
    ))
    assert manual["potential_audience"]["source"] == "manual"
    assert "meta_targeting" not in manual
    monkeypatch.setattr(meta, "fetch_reach_estimate", lambda **kwargs: (800, 1200))
    live = main.constanza_meta_forecast(main.MetaAuctionForecastRequest(
        product_name=name, retail_budget=700, meta_geo_type="city", meta_geo_key="42",
    ))
    assert live["potential_audience"]["source"] == "meta_api"
    assert live["meta_targeting"]["geo_key"] == "42"
    with pytest.raises(HTTPException) as error:
        main.constanza_meta_forecast(main.MetaAuctionForecastRequest(
            product_name=name, retail_budget=700, meta_geo_type="city", meta_geo_key="42", audience_lower=1, audience_upper=1,
        ))
    assert error.value.status_code == 422


def test_meta_snapshot_round_trips_without_becoming_inventory_avails():
    result = meta.model_auction(
        product_name="Facebook & Instagram Ads | Awareness", retail_budget=200,
        audience_lower=1000, audience_upper=1000, reach_share=0.6,
    )
    saved = main.AvailsEntry(meta_forecast=result).model_dump()
    reopened = main.AvailsEntry(**saved)
    assert reopened.meta_forecast["modeled_delivery"]["impressions"] == 3300
    assert reopened.max_imps is None and reopened.max_spend is None
