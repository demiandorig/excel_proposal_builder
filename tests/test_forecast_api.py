"""The forecast API surface (offline, the store faked): what the estimate / catalog / series / admin-data endpoints return
once avails live in Postgres series - provenance, units, validation of the new input, and graceful failure."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import app.main as main
from app.services import constanza_forecast_map as fm
from app.services import forecast_store as store
from app.services.forecast_import import geo_reference as geo
from tests.test_forecast_engine_series import cfg, ref, uniform_dmas  # noqa: F401  (helpers)

ROKU = "Entravision Plus - Roku Ads (Includes The Roku Channel and the popular Espacio Latino Hub)"
MLB = "Entravision Plus - Live Sports - MLB"
CONNECT = "Entravision+ Connect - (Default)"
NETFLIX_AGE = "Netflix - Age Targeted :15s Ads"


@pytest.fixture
def series_store(monkeypatch):
    """Three current series (a legacy one, a platform export, a weekly live-sports one) and the product links."""
    series = {
        "roku": ref("roku", 1, title="Roku Channel", basis="modeled"),
        "connect-default": ref("connect-default", 2, title="Entravision+ Connect - (Default)", levels=("dma", "state", "cd", "zip"),
                               device_mix={"connected_tv": 0.76, "mobile": 0.24}, top_publishers=[["Samsung TV Plus", 0.18]],
                               row_counts={"dma": 210, "zip": 24848}),
        "live-sports-mlb": ref("live-sports-mlb", 3, title="MLB", unit="weekly", basis="modeled", channel="live_sports",
                               period_label="Regular season, ends Sep 27, 2026", period_end="2026-09-27"),
        "netflix-15s": ref("netflix-15s", 4, title="Netflix :15s", basis="extrapolated", channel="netflix"),
    }
    links = {ROKU: ("roku", "primary", None), CONNECT: ("connect-default", "primary", None), MLB: ("live-sports-mlb", "primary", None),
             NETFLIX_AGE: ("netflix-15s", "baseline_proxy", "Baseline; targeted variants deliver fewer impressions.")}
    dmas = {1: uniform_dmas(1000.0, "modeled"), 2: uniform_dmas(2000.0), 3: uniform_dmas(500.0, "modeled"), 4: uniform_dmas(800.0)}
    monkeypatch.setattr(store, "_fetch_catalog", lambda: (series, links))
    monkeypatch.setattr(store, "dma_table", lambda sid: dmas.get(sid, {}))
    monkeypatch.setattr(store, "zip_rows", lambda sid, zs: {})
    monkeypatch.setattr(store, "cd_rows", lambda sid, ks: {})
    monkeypatch.setattr(store, "unknown_share", lambda sid: {})
    store.invalidate()
    fm.invalidate()
    yield
    store.invalidate()
    fm.invalidate()


def estimate_body(**kw):
    base = dict(product_name=CONNECT, geo_text="Los Angeles", months=1)
    base.update(kw)
    return main.ConstanzaEstimateRequest(**base)


def test_the_estimate_says_which_series_version_and_basis_it_used(series_store):
    out = main.constanza_estimate(estimate_body())
    s = out["series"]
    assert out["platform"] == "connect-default" and out["confidence"] == "platform_modeled"
    assert s["key"] == "connect-default" and s["version"] == 3 and s["data_basis"] == "platform_reported" and s["unit"] == "monthly"
    assert s["device_mix"] == {"connected_tv": 0.76, "mobile": 0.24} and s["top_publishers"] == [["Samsung TV Plus", 0.18]]
    assert out["imps_mid"] == 2000.0 and out["weeks"] is None


def test_a_live_sports_estimate_reports_its_period_and_uses_flight_days(series_store):
    out = main.constanza_estimate(estimate_body(product_name=MLB, flight_days=14))
    assert out["series"]["period_label"] == "Regular season, ends Sep 27, 2026" and out["series"]["unit"] == "weekly"
    assert out["weeks"] == 2.0 and out["imps_mid"] == 1000.0
    assert any("WEEKLY" in n for n in out["notes"])


def test_the_generic_fallback_has_no_series(series_store):
    out = main.constanza_estimate(estimate_body(product_name="Search - SEM", national_monthly_total=1_000_000))
    assert out["series"] is None and out["confidence"] == "generic_fallback"


@pytest.mark.parametrize("days", [0, -3, float("inf"), float("nan"), 4000])
def test_absurd_flight_lengths_are_a_plain_422(series_store, days):
    with pytest.raises(HTTPException) as e:
        main.constanza_estimate(estimate_body(flight_days=days))
    assert e.value.status_code == 422 and "Flight days" in e.value.detail


def test_a_product_with_no_data_and_no_total_gets_an_actionable_422(series_store):
    with pytest.raises(HTTPException) as e:
        main.constanza_estimate(estimate_body(product_name="Search - SEM"))
    assert e.value.status_code == 422 and "no forecast avails loaded" in e.value.detail


def _catalog():
    data = asyncio.run(main.get_catalog())
    return {p["name"]: p for fam in data["products_by_family"].values() for p in fam}


def test_the_catalog_carries_each_products_series_for_the_avails_card(series_store):
    products = _catalog()
    f = products[MLB]["forecast"]
    assert f["enabled"] and f["method"] == "platform" and f["platform"] == "live-sports-mlb" and f["source"] == "default"
    assert f["series"]["period_label"] == "Regular season, ends Sep 27, 2026" and f["series"]["unit"] == "weekly" and f["series"]["relation"] == "primary"
    assert products[ROKU]["forecast"]["series"]["data_basis"] == "modeled"
    assert products[NETFLIX_AGE]["forecast"]["series"]["relation"] == "baseline_proxy"
    assert "series" not in products["Search - SEM"]["forecast"]


def test_a_product_linked_to_a_series_is_enabled_whatever_its_family(series_store):
    cfg_ = fm.resolve_forecast(NETFLIX_AGE, "Some Other Family")
    assert cfg_.enabled and cfg_.platform == "netflix-15s" and cfg_.relation == "baseline_proxy"


def test_an_admin_can_point_a_product_at_any_series_and_not_at_one_that_does_not_exist(series_store):
    assert "connect-default" in fm.platform_keys() and "roku" in fm.platform_keys()
    clean = fm.clean_config({"enabled": True, "method": "platform", "platform": "connect-default"})
    assert clean["platform"] == "connect-default"
    with pytest.raises(ValueError, match="bundled platform data series"):
        fm.clean_config({"enabled": True, "method": "platform", "platform": "nope"})


def test_the_admin_payload_describes_every_series_and_who_uses_it(series_store):
    data = fm.admin_payload()
    cov = {c["key"]: c for c in data["coverage"]}
    assert cov["connect-default"]["series"]["version"] == 3 and CONNECT in cov["connect-default"]["products"]
    assert cov["connect-default"]["dmas"] == 210 and cov["live-sports-mlb"]["series"]["period_label"].startswith("Regular season")
    plat = {p["key"]: p for p in data["platforms"]}
    assert plat["live-sports-mlb"]["unit"] == "weekly" and plat["live-sports-mlb"]["channel"] == "live_sports" and plat["roku"]["data_basis"] == "modeled"


def test_the_series_endpoint_lists_what_is_published_with_its_products(series_store):
    out = main.forecast_series_list()
    keys = [s["key"] for s in out["series"]]
    assert keys == sorted(keys, key=lambda k: (out["series"][keys.index(k)]["channel"], k))       # ordered by channel then key
    mlb = next(s for s in out["series"] if s["key"] == "live-sports-mlb")
    assert mlb["products"] == [{"name": MLB, "relation": "primary"}] and mlb["period_label"] and mlb["row_counts"] == {}
    netflix = next(s for s in out["series"] if s["key"] == "netflix-15s")
    assert netflix["products"][0]["relation"] == "baseline_proxy"


def test_the_series_endpoint_is_empty_not_an_error_without_a_database():
    store.invalidate()
    assert main.forecast_series_list() == {"series": []}


def test_the_admin_data_endpoint_maps_a_database_failure_to_503(monkeypatch):
    def boom():
        raise RuntimeError("no database")
    monkeypatch.setattr(store, "data_overview", boom)
    with pytest.raises(HTTPException) as e:
        main.admin_forecast_data()
    assert e.value.status_code == 503
    monkeypatch.setattr(store, "data_overview", lambda: {"series": [], "batches": []})
    assert main.admin_forecast_data() == {"series": [], "batches": []}


def test_rollback_maps_errors_and_refreshes_the_caches(monkeypatch, series_store):
    from app.services.forecast_import import service
    request = SimpleNamespace(state=SimpleNamespace(user={"email": "admin@x.com"}))
    seen = {}
    monkeypatch.setattr(service, "rollback_series", lambda key, to, by: seen.update(args=(key, to, by)) or 1)
    monkeypatch.setattr(store, "data_overview", lambda: {"series": [], "batches": []})
    out = main.admin_forecast_rollback(main.ForecastRollbackRequest(series_key="roku", to_version=None), request)
    assert out["version"] == 1 and seen["args"] == ("roku", None, "admin@x.com")
    monkeypatch.setattr(service, "rollback_series", lambda key, to, by: (_ for _ in ()).throw(ValueError("no earlier version")))
    with pytest.raises(HTTPException) as e:
        main.admin_forecast_rollback(main.ForecastRollbackRequest(series_key="roku"), request)
    assert e.value.status_code == 400 and "no earlier version" in e.value.detail
    monkeypatch.setattr(service, "rollback_series", lambda key, to, by: (_ for _ in ()).throw(RuntimeError("db")))
    with pytest.raises(HTTPException) as e:
        main.admin_forecast_rollback(main.ForecastRollbackRequest(series_key="roku"), request)
    assert e.value.status_code == 503


def test_the_startup_hook_loads_in_the_background_and_never_blocks_or_raises(monkeypatch):
    from app.services.forecast_import import service
    called = {}
    monkeypatch.setattr(service, "bootstrap", lambda: called.setdefault("ran", True) and {"series": [("a", "published")], "migrations": [], "products": 0})
    main._bootstrap_forecast_data()
    assert called["ran"]
    monkeypatch.setattr(service, "bootstrap", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    main._bootstrap_forecast_data()                                                 # swallowed, logged
