"""
Admin -> Forecast: which catalog products show Constanza's "Auto-calculate avails" button on the Avails step, and what
the button computes (app/services/constanza_forecast_map.py).

What is pinned here:
  * with no admin rows every product behaves EXACTLY as Phase 1 hard-coded it (the Entravision Plus family gets the
    button; the ten named products are platform-modeled; the rest are a population-share estimate) — the safe default;
  * an admin row wins, is found through a product's former names (a rename doesn't orphan it), and a bad or unreadable
    row can never break an estimate;
  * the engine routes by the EFFECTIVE config, so a change in the admin page changes what the button computes;
  * the catalog payload carries each product's config (that is what the Avails card reads), the admin endpoints
    validate and answer with the refreshed list, a duplicate inherits its original's config, and a platform SKU that
    exists only in the code seed can be added to an already-seeded database.

Same offline pattern as the other tests: conftest patches the catalog and the forecast table readers; the table is
replaced by an in-memory store where a test needs rows.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import app.main as main
from app import catalog
from app.data.constanza import load_platform_dma
from app.services import constanza_engine as engine
from app.services import constanza_forecast_map as fm
from app.services.constanza_geo import resolve_geo
from app.services.schema_bootstrap import idempotent_statements

ROOT = Path(__file__).resolve().parent.parent
# The 30 products added with the forecast data platform (app/data/forecast/products.json). Offline - no database, so no
# series links - they get the built-in default: the Entravision Plus ones show the button as a population-share estimate.
NEW_FORECAST_PRODUCTS = {r["name"] for r in json.loads((ROOT / "app" / "data" / "forecast" / "products.json").read_text())["products"]}
NEW_EP = {r["name"] for r in json.loads((ROOT / "app" / "data" / "forecast" / "products.json").read_text())["products"] if r["family"] == "Entravision Plus"}
ROKU = "Entravision Plus - Roku Ads (Includes The Roku Channel and the popular Espacio Latino Hub)"
PRIME = "Entravision Plus - Amazon Prime Video"
HULU = "Entravision Plus - Hulu"
REACH = "Entravision Plus - CTV/OTT Reach"
HISPANIC = "Entravision Plus - Hispanics CTV/OTT"
VIX = "Entravision Plus - VIX 360 (Includes VIX + UnivisionNow + Univision.com / Univision Apps)"
SEARCH = "Search - SEM"
PLATFORM_SKUS = [HULU, "Entravision Plus - Peacock",
                 "Entravision Plus - Other FAST (Tubi, Pluto TV, Samsung TV Plus, and other long-tail FAST channels)",
                 "Entravision Plus - vMVPD (Fubo, Sling, DIRECTV Stream, Philo, Frndly, Vidgo + MVPD streaming apps)"]


class Store:
    """The forecast_product_map table, in memory."""

    def __init__(self):
        self.rows: dict[str, dict] = {}
        self.reads = 0

    def read(self):
        self.reads += 1
        return [dict(r) for r in self.rows.values()]

    def write(self, name, cfg, updated_by):
        self.rows[name] = {"product_name": name, **cfg, "updated_by": updated_by}

    def delete(self, names):
        for n in names:
            self.rows.pop(n, None)


@pytest.fixture
def store(monkeypatch):
    s = Store()
    monkeypatch.setattr(fm, "_read_rows", s.read)
    monkeypatch.setattr(fm, "_write", s.write)
    monkeypatch.setattr(fm, "_delete", s.delete)
    fm.invalidate()
    yield s
    fm.invalidate()


def _request(email="admin@entravision.com"):
    return SimpleNamespace(state=SimpleNamespace(user={"email": email, "is_admin": True}))


def _body(name, **kw):
    fields = dict(product_name=name, enabled=True, method="platform", platform="roku", hispanic_weighted=False)
    fields.update(kw)
    return main.ForecastMapUpdateRequest(**fields)


# ---------------------------------------------------------------------------------------------------------------------
# 1. Defaults == Phase 1
# ---------------------------------------------------------------------------------------------------------------------

def test_with_no_admin_rows_every_product_behaves_exactly_as_phase_1_hard_coded_it_plus_the_live_api_products():
    seen = 0
    for p in catalog.CATALOG:
        cfg = fm.resolve_forecast(p.name, p.family)
        expected_platform = fm.DEFAULT_PLATFORM_PRODUCTS.get(p.name)
        if p.name in fm.DEFAULT_API_PRODUCTS:                       # the one addition since: a product forecast by a live API (TikTok)
            assert (cfg.enabled, cfg.method, cfg.platform, cfg.hispanic_weighted) == (True, fm.DEFAULT_API_PRODUCTS[p.name], None, False), p.name
        else:
            assert cfg.enabled == (p.family == "Entravision Plus"), p.name           # the button: the whole family, nothing else
            assert cfg.platform == expected_platform and cfg.method == ("platform" if expected_platform else "generic"), p.name
            assert cfg.hispanic_weighted == (p.name in fm.DEFAULT_HISPANIC_PRODUCTS), p.name
        assert cfg.source == "default"
        seen += 1
    assert seen == len(catalog.CATALOG)


def test_the_built_in_mapping_is_the_one_the_planner_was_told_about():
    """The ORIGINAL 22 Entravision Plus products keep the mapping they were given in Phase 1; the products added later
    (NEW_EP) are only checked to show the button."""
    ep = [p for p in catalog.CATALOG if p.family == "Entravision Plus" and p.name not in NEW_EP]
    assert len(ep) == 22
    assert all(fm.resolve_forecast(n, "Entravision Plus").enabled for n in NEW_EP)
    modeled = {p.name: fm.resolve_forecast(p.name, p.family).platform for p in ep if fm.resolve_forecast(p.name, p.family).method == "platform"}
    assert modeled == {
        ROKU: "roku", PRIME: "prime", HULU: "hulu", "Entravision Plus - Peacock": "peacock",
        PLATFORM_SKUS[2]: "other_fast", PLATFORM_SKUS[3]: "vmvpd",
        "Entravision Plus CTV/OTT - English Content": "cumulative", "Entravision Plus CTV/OTT- Spanish Content only": "cumulative",
        HISPANIC: "cumulative", REACH: "cumulative",
    }
    generic = sorted(p.name for p in ep if fm.resolve_forecast(p.name, p.family).method == "generic")
    assert len(generic) == 12 and VIX in generic and "Entravision Plus - Online Video (OLV) Blend Reach" in generic
    assert sum(1 for n in generic if n.startswith("Netflix - ")) == 10
    assert fm.DEFAULT_HISPANIC_PRODUCTS == {HISPANIC}


def test_every_default_platform_product_is_a_real_ep_product_and_its_platform_has_data():
    data = load_platform_dma()
    for name, platform in fm.DEFAULT_PLATFORM_PRODUCTS.items():
        product = catalog.by_name(name)
        assert product is not None and product.family == "Entravision Plus", name
        assert platform in data and data[platform], (name, platform)
    assert set(fm.platform_keys()) == set(data)              # the admin page offers exactly the bundled series
    assert all(fm.platform_label(k) for k in data)


def test_the_engine_still_exports_the_old_constant_names():
    assert engine.PLATFORM_MODELED_PRODUCTS is fm.DEFAULT_PLATFORM_PRODUCTS
    assert engine.HISPANIC_TARGETED_PRODUCTS is fm.DEFAULT_HISPANIC_PRODUCTS


# ---------------------------------------------------------------------------------------------------------------------
# 2. Overrides
# ---------------------------------------------------------------------------------------------------------------------

def test_an_admin_row_wins_over_the_default(store):
    store.write(SEARCH, {"enabled": True, "method": "generic", "platform": None, "hispanic_weighted": False}, "a")
    store.write(ROKU, {"enabled": False, "method": "platform", "platform": "roku", "hispanic_weighted": False}, "a")
    store.write(HULU, {"enabled": True, "method": "platform", "platform": "peacock", "hispanic_weighted": False}, "a")
    search = fm.resolve_forecast(SEARCH)
    assert (search.enabled, search.method, search.source) == (True, "generic", "admin")      # a non-EP product can get the button
    roku = fm.resolve_forecast(ROKU)
    assert (roku.enabled, roku.platform, roku.source) == (False, "roku", "admin")             # switched off, mapping kept
    assert fm.resolve_forecast(HULU).platform == "peacock"                                    # re-pointed at another platform
    assert fm.resolve_forecast(PRIME).source == "default"                                     # untouched products stay default


def test_a_row_is_found_through_the_products_former_names(store):
    # "YouTube Ads" was split into "YouTube Ads - Skippable" in code: a row saved under the old name still reaches it
    store.write("YouTube Ads", {"enabled": True, "method": "generic", "platform": None, "hispanic_weighted": False}, "a")
    cfg = fm.resolve_forecast("YouTube Ads - Skippable")
    assert cfg.enabled is True and cfg.source == "admin"
    # ... and the built-in default follows a renamed product too (a renamed Hulu keeps its platform)
    renamed = fm.default_config("Hulu CTV", "Entravision Plus", variants={"Hulu CTV", HULU})
    assert renamed.platform == "hulu" and renamed.method == "platform" and renamed.enabled


def test_a_hispanic_default_follows_the_product_and_only_applies_to_platform_data():
    cfg = fm.default_config(HISPANIC, "Entravision Plus")
    assert cfg.hispanic_weighted is True and cfg.method == "platform"
    assert fm.default_config(VIX, "Entravision Plus").hispanic_weighted is False


def test_an_invalid_row_is_ignored_and_logged_never_fatal(store, caplog):
    store.write(HULU, {"enabled": True, "method": "platform", "platform": "no_such_platform", "hispanic_weighted": False}, "a")
    store.write(PRIME, {"enabled": True, "method": "teleport", "platform": None, "hispanic_weighted": False}, "a")
    with caplog.at_level(logging.WARNING, logger=fm.log.name):
        cfg = fm.resolve_forecast(HULU)
    assert cfg.source == "default" and cfg.platform == "hulu"
    assert fm.resolve_forecast(PRIME).source == "default"
    assert any("ignoring invalid forecast mapping" in r.getMessage() for r in caplog.records)


def test_an_unreadable_table_leaves_every_product_on_its_default_and_is_not_hammered(monkeypatch):
    calls = []
    monkeypatch.setattr(fm, "_read_rows", lambda: (calls.append(1), None)[1])
    fm.invalidate()
    for _ in range(5):
        assert fm.resolve_forecast(ROKU).source == "default"
    assert fm.load_overrides() == {}
    assert len(calls) == 1                                   # one failed read, then a quiet period (no DB call per estimate)


def test_a_failed_refresh_keeps_serving_the_last_good_rows(store, monkeypatch):
    store.write(SEARCH, {"enabled": True, "method": "generic", "platform": None, "hispanic_weighted": False}, "a")
    assert fm.resolve_forecast(SEARCH).enabled is True
    monkeypatch.setattr(fm, "_CACHE_TTL", 0.0)               # force a re-read on the next call
    monkeypatch.setattr(fm, "_read_rows", lambda: None)      # ... which fails
    assert fm.resolve_forecast(SEARCH).enabled is True       # a blip doesn't switch the button off


# ---------------------------------------------------------------------------------------------------------------------
# 3. Validation and edits
# ---------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("payload,message", [
    ({"enabled": True, "method": "teleport"}, "Choose what the button computes"),
    ({"enabled": True}, "Choose what the button computes"),
    ({"enabled": True, "method": "platform"}, "Choose which platform"),
    ({"enabled": True, "method": "platform", "platform": "   "}, "Choose which platform"),
    ({"enabled": True, "method": "platform", "platform": "myspace"}, "isn't one of the bundled platform data series"),
])
def test_clean_config_rejects_what_cannot_work(payload, message):
    with pytest.raises(ValueError, match=message):
        fm.clean_config(payload)


def test_clean_config_normalizes_a_generic_choice_and_keeps_a_platform_one():
    assert fm.clean_config({"enabled": 1, "method": "generic", "platform": "roku", "hispanic_weighted": True}) == \
        {"enabled": True, "method": "generic", "platform": None, "hispanic_weighted": False}     # generic: no platform, no weighting
    assert fm.clean_config({"enabled": False, "method": "platform", "platform": " roku ", "hispanic_weighted": True}) == \
        {"enabled": False, "method": "platform", "platform": "roku", "hispanic_weighted": True}


def test_set_override_stores_under_the_current_name_and_drops_a_former_name_row(store):
    store.write("YouTube Ads", {"enabled": False, "method": "generic", "platform": None, "hispanic_weighted": False}, "old")
    cfg = fm.set_override("YouTube Ads - Skippable", {"enabled": True, "method": "generic"}, "admin@x")
    assert cfg.enabled is True and cfg.source == "admin"
    assert set(store.rows) == {"YouTube Ads - Skippable"}                    # never two rows for one product
    assert store.rows["YouTube Ads - Skippable"]["updated_by"] == "admin@x"


def test_set_override_and_clear_override_reject_an_unknown_product(store):
    with pytest.raises(LookupError):
        fm.set_override("No Such Product", {"enabled": True, "method": "generic"}, "a")
    with pytest.raises(LookupError):
        fm.clear_override("No Such Product")
    with pytest.raises(ValueError):
        fm.set_override(ROKU, {"enabled": True, "method": "platform"}, "a")
    assert store.rows == {}


def test_clear_override_puts_a_product_back_on_its_default(store):
    fm.set_override(ROKU, {"enabled": False, "method": "platform", "platform": "prime"}, "a")
    assert fm.resolve_forecast(ROKU).platform == "prime"
    cfg = fm.clear_override(ROKU)
    assert cfg.source == "default" and cfg.platform == "roku" and cfg.enabled is True and store.rows == {}


def test_a_database_failure_while_saving_is_reported_not_swallowed(monkeypatch, store):
    def boom(*a, **k):
        raise OSError("connection lost")
    monkeypatch.setattr(fm, "_write", boom)
    with pytest.raises(RuntimeError, match="Couldn't save the forecast mapping"):
        fm.set_override(ROKU, {"enabled": True, "method": "generic"}, "a")


# ---------------------------------------------------------------------------------------------------------------------
# 4. The engine follows the effective config
# ---------------------------------------------------------------------------------------------------------------------

def test_the_default_estimates_are_what_they_were_before_the_mapping_existed():
    national = resolve_geo("National")
    roku = engine.estimate_avails(ROKU, national, months=1)
    assert roku.confidence == "platform_modeled" and roku.platform == "roku"
    vix = engine.estimate_avails(VIX, national, months=1, national_monthly_total=2_000_000)
    assert vix.confidence == "generic_fallback" and vix.platform == "generic_population"
    assert engine.estimate_avails(VIX, national, months=1) is None            # nothing to apportion without a total
    hisp = engine.estimate_avails(HISPANIC, resolve_geo("Los Angeles; Nashville"), months=1)
    assert any("Hispanic population %" in n for n in hisp.notes)


def test_repointing_a_product_at_another_platform_changes_its_numbers(store):
    national = resolve_geo("National")
    prime_numbers = engine.estimate_avails(PRIME, national, months=1)
    roku_numbers = engine.estimate_avails(ROKU, national, months=1)
    assert prime_numbers.imps_mid != roku_numbers.imps_mid
    fm.set_override(ROKU, {"enabled": True, "method": "platform", "platform": "prime"}, "a")
    repointed = engine.estimate_avails(ROKU, national, months=1)
    assert repointed.platform == "prime" and repointed.imps_mid == pytest.approx(prime_numbers.imps_mid)


def test_switching_a_platform_product_to_population_share_needs_the_lines_budget(store):
    national = resolve_geo("National")
    fm.set_override(ROKU, {"enabled": True, "method": "generic"}, "a")
    assert engine.estimate_avails(ROKU, national, months=1) is None
    est = engine.estimate_avails(ROKU, national, months=2, national_monthly_total=1_000_000)
    assert est.confidence == "generic_fallback" and est.imps_mid == pytest.approx(2_000_000)


def test_a_generic_product_can_be_given_platform_data(store):
    fm.set_override(VIX, {"enabled": True, "method": "platform", "platform": "hulu"}, "a")
    est = engine.estimate_avails(VIX, resolve_geo("National"), months=1)            # no total needed any more
    assert est.confidence == "platform_modeled" and est.platform == "hulu"


def test_hispanic_weighting_can_be_turned_on_and_off_per_product(store):
    geo = resolve_geo("Los Angeles; Nashville")            # two states with very different Hispanic shares
    plain = engine.estimate_avails(REACH, geo, months=1)
    assert not any("Hispanic population %" in n for n in plain.notes)
    fm.set_override(REACH, {"enabled": True, "method": "platform", "platform": "cumulative", "hispanic_weighted": True}, "a")
    weighted = engine.estimate_avails(REACH, geo, months=1)
    assert any("Hispanic population %" in n for n in weighted.notes)
    assert [round(a.weight, 6) for a in weighted.by_area] != [round(a.weight, 6) for a in plain.by_area]
    fm.set_override(HISPANIC, {"enabled": True, "method": "platform", "platform": "cumulative", "hispanic_weighted": False}, "a")
    assert not any("Hispanic population %" in n for n in engine.estimate_avails(HISPANIC, geo, months=1).notes)


def test_turning_the_button_off_does_not_break_the_estimate_itself(store):
    fm.set_override(ROKU, {"enabled": False, "method": "platform", "platform": "roku"}, "a")
    assert engine.estimate_avails(ROKU, resolve_geo("National"), months=1).platform == "roku"     # the button is a UI choice


def test_the_international_wording_follows_the_config_not_the_name(store):
    geo = resolve_geo("Mexico")
    assert geo.geo_confidence == "low_confidence_international"
    default = engine.estimate_avails(ROKU, geo, months=1, national_monthly_total=1_000_000)
    assert any("U.S. DMAs only" in n for n in default.notes)                    # a platform-modeled product, no US data abroad
    fm.set_override(ROKU, {"enabled": True, "method": "generic"}, "a")
    generic = engine.estimate_avails(ROKU, geo, months=1, national_monthly_total=1_000_000)
    assert not any("U.S. DMAs only" in n for n in generic.notes)


# ---------------------------------------------------------------------------------------------------------------------
# 5. The catalog payload (what the Avails card reads) and the live endpoint
# ---------------------------------------------------------------------------------------------------------------------

def _catalog_payload():
    data = asyncio.run(main.get_catalog())
    return {p["name"]: p for fam in data["products_by_family"].values() for p in fam}


def test_every_product_in_the_catalog_payload_carries_its_forecast_config():
    products = _catalog_payload()
    assert len(products) == len(catalog.effective_catalog())
    assert products[ROKU]["forecast"] == {"enabled": True, "method": "platform", "platform": "roku", "hispanic_weighted": False, "source": "default", "relation": "primary"}
    assert products[VIX]["forecast"]["method"] == "generic" and products[VIX]["forecast"]["enabled"] is True
    assert products[SEARCH]["forecast"]["enabled"] is False
    assert products["Tiktok Ads"]["forecast"]["method"] == "tiktok" and products["Tiktok Ads"]["forecast"]["platform"] is None
    assert sum(1 for p in products.values() if p["forecast"]["enabled"]) == 22 + len(NEW_EP) + len(fm.DEFAULT_API_PRODUCTS)


def test_an_admin_change_reaches_the_catalog_payload(store):
    fm.set_override(SEARCH, {"enabled": True, "method": "generic"}, "a")
    fm.set_override(ROKU, {"enabled": False, "method": "platform", "platform": "roku"}, "a")
    products = _catalog_payload()
    assert products[SEARCH]["forecast"]["enabled"] is True and products[SEARCH]["forecast"]["source"] == "admin"
    assert products[ROKU]["forecast"]["enabled"] is False


def test_the_platform_products_endpoint_is_the_live_mapping(store):
    live = asyncio.run(main.constanza_platform_products())["platform_modeled_products"]
    assert live == fm.DEFAULT_PLATFORM_PRODUCTS
    fm.set_override(VIX, {"enabled": True, "method": "platform", "platform": "hulu"}, "a")
    fm.set_override(PRIME, {"enabled": True, "method": "generic"}, "a")
    live = asyncio.run(main.constanza_platform_products())["platform_modeled_products"]
    assert live[VIX] == "hulu" and PRIME not in live


# ---------------------------------------------------------------------------------------------------------------------
# 6. Admin endpoints
# ---------------------------------------------------------------------------------------------------------------------

def test_the_admin_payload_lists_every_product_with_coverage_and_totals(store):
    data = asyncio.run(main.admin_get_forecast_map())
    assert data["summary"] == {"total": len(catalog.effective_catalog()), "enabled": 22 + len(NEW_EP) + 1, "platform_modeled": 10, "generic": 12 + len(NEW_EP), "tiktok": 1, "customized": 0}
    rows = {r["name"]: r for r in data["products"]}
    assert rows[ROKU]["enabled"] and rows[ROKU]["method"] == "platform" and rows[ROKU]["platform"] == "roku" and not rows[ROKU]["customized"]
    assert rows[SEARCH]["enabled"] is False and rows[SEARCH]["default"]["enabled"] is False
    coverage = {c["key"]: c for c in data["coverage"]}
    assert coverage["roku"]["products"] == [ROKU] and coverage["roku"]["dmas"] > 100
    assert sorted(coverage["cumulative"]["products"]) == sorted([
        "Entravision Plus CTV/OTT - English Content", "Entravision Plus CTV/OTT- Spanish Content only", HISPANIC, REACH])
    assert all(c["missing_product"] is None for c in data["coverage"])           # the seed catalog has every platform product
    assert [p["key"] for p in data["platforms"]] == fm.platform_keys()
    assert "Entravision Plus" in data["families"]


def test_saving_a_choice_answers_with_the_refreshed_list(store):
    data = asyncio.run(main.admin_set_forecast_map(_body(SEARCH, method="generic", platform=None), _request("planner@x.com")))
    assert data["saved"] is True and data["summary"]["customized"] == 1 and data["summary"]["enabled"] == 24 + len(NEW_EP)
    row = next(r for r in data["products"] if r["name"] == SEARCH)
    assert row["enabled"] and row["method"] == "generic" and row["customized"]
    assert store.rows[SEARCH]["updated_by"] == "planner@x.com"
    # switching one off moves the coverage: Roku's product is listed as "button off"
    data = asyncio.run(main.admin_set_forecast_map(_body(ROKU, enabled=False), _request()))
    roku = next(c for c in data["coverage"] if c["key"] == "roku")
    assert roku["products"] == [] and roku["disabled_products"] == [ROKU]


def test_the_admin_endpoints_map_errors_to_http_statuses(store):
    with pytest.raises(HTTPException) as e:
        asyncio.run(main.admin_set_forecast_map(_body("No Such Product"), _request()))
    assert e.value.status_code == 404
    with pytest.raises(HTTPException) as e:
        asyncio.run(main.admin_set_forecast_map(_body(ROKU, platform=None), _request()))
    assert e.value.status_code == 400 and "platform" in e.value.detail
    with pytest.raises(HTTPException) as e:
        asyncio.run(main.admin_reset_forecast_map(main.ForecastMapProductRequest(product_name="No Such Product")))
    assert e.value.status_code == 404
    with pytest.raises(HTTPException) as e:
        asyncio.run(main.admin_add_forecast_platform_product(main.ForecastMapProductRequest(product_name=SEARCH)))
    assert e.value.status_code == 400
    assert store.rows == {}


def test_reset_puts_a_product_back_and_refreshes_the_list(store):
    asyncio.run(main.admin_set_forecast_map(_body(ROKU, enabled=False, platform="prime"), _request()))
    data = asyncio.run(main.admin_reset_forecast_map(main.ForecastMapProductRequest(product_name=ROKU)))
    row = next(r for r in data["products"] if r["name"] == ROKU)
    assert data["reset"] is True and row["enabled"] and row["platform"] == "roku" and not row["customized"]
    assert data["summary"]["customized"] == 0


# ---------------------------------------------------------------------------------------------------------------------
# 7. A database seeded before the four new platform SKUs existed
# ---------------------------------------------------------------------------------------------------------------------

def _database_without_the_new_skus(monkeypatch):
    rows = [{"id": i, "is_deleted": False, "product": p} for i, p in enumerate(catalog.CATALOG) if p.name not in PLATFORM_SKUS]
    monkeypatch.setattr(catalog, "_read_product_rows", lambda: rows)
    catalog.invalidate_product_caches()
    return rows


def test_a_platform_sku_missing_from_a_seeded_database_is_flagged_and_can_be_added(store, monkeypatch):
    _database_without_the_new_skus(monkeypatch)
    data = asyncio.run(main.admin_get_forecast_map())
    coverage = {c["key"]: c for c in data["coverage"]}
    for key, sku in zip(("hulu", "peacock", "other_fast", "vmvpd"), PLATFORM_SKUS):
        assert coverage[key]["products"] == [] and coverage[key]["missing_product"]["name"] == sku, key
        assert coverage[key]["missing_product"]["base_rate"] and "PLACEHOLDER" in coverage[key]["missing_product"]["notes"].upper()
    assert coverage["roku"]["missing_product"] is None and coverage["cumulative"]["missing_product"] is None

    created = []
    monkeypatch.setattr(catalog, "create_product", lambda fields, copy_from_id=None: (created.append(fields), {"id": 99, **fields})[1])
    out = asyncio.run(main.admin_add_forecast_platform_product(main.ForecastMapProductRequest(product_name=HULU)))
    assert out["added"] is True and created[0]["name"] == HULU and created[0]["family"] == "Entravision Plus"
    assert created[0]["base_rate"] == 25.0 and "PLACEHOLDER" in created[0]["notes"].upper()    # placeholder rate, never silent


def test_only_the_built_in_platform_skus_can_be_added_and_only_once(store, monkeypatch):
    with pytest.raises(ValueError, match="isn't one of the built-in forecast platform products"):
        fm.add_missing_platform_product(SEARCH)
    with pytest.raises(ValueError, match="already in the product catalog"):
        fm.add_missing_platform_product(ROKU)


# ---------------------------------------------------------------------------------------------------------------------
# 8. Duplicating a product
# ---------------------------------------------------------------------------------------------------------------------

def _with_copy(monkeypatch, copy_name, source):
    copy = replace(source, name=copy_name)
    rows = [{"id": i, "is_deleted": False, "product": p} for i, p in enumerate(list(catalog.CATALOG) + [copy])]
    monkeypatch.setattr(catalog, "_read_product_rows", lambda: rows)
    catalog.invalidate_product_caches()


def test_a_copy_of_a_platform_product_forecasts_like_its_original(store, monkeypatch):
    source = catalog.by_name(ROKU)
    _with_copy(monkeypatch, "Entravision Plus - Roku Ads (Spanish)", source)
    # by name alone the copy would be a generic estimate ...
    assert fm.default_config("Entravision Plus - Roku Ads (Spanish)", "Entravision Plus").method == "generic"
    created = {"copied_from": ROKU, "name": "Entravision Plus - Roku Ads (Spanish)"}
    monkeypatch.setattr(main, "catalog_create_product", lambda fields, copy_from_id=None: created)
    monkeypatch.setattr(main.restrictions_svc, "inherit_product_approvals", lambda *a: 0)
    body = main.ProductRequest(copy_from_id=1)
    out = asyncio.run(main.admin_create_product(body, _request("planner@x.com")))
    assert out["added"] is True
    # ... so the copy inherits the original's config
    cfg = fm.resolve_forecast("Entravision Plus - Roku Ads (Spanish)")
    assert (cfg.platform, cfg.method, cfg.enabled, cfg.source) == ("roku", "platform", True, "admin")


def test_inheriting_writes_nothing_when_the_copy_would_already_behave_the_same(store, monkeypatch):
    source = catalog.by_name(VIX)
    _with_copy(monkeypatch, "Entravision Plus - VIX 360 (Spanish)", source)
    assert fm.inherit_config(VIX, "Entravision Plus - VIX 360 (Spanish)", "a") is False
    assert store.rows == {}
    assert fm.inherit_config(VIX, "Not In The Catalog", "a") is False


def test_a_forecast_inheritance_failure_never_blocks_the_duplicate(store, monkeypatch):
    created = {"copied_from": ROKU, "name": "Entravision Plus - Roku Ads (Copy)"}
    monkeypatch.setattr(main, "catalog_create_product", lambda fields, copy_from_id=None: created)
    monkeypatch.setattr(main.restrictions_svc, "inherit_product_approvals", lambda *a: 0)
    monkeypatch.setattr(fm, "inherit_config", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down")))
    out = asyncio.run(main.admin_create_product(main.ProductRequest(copy_from_id=1), _request()))
    assert out["added"] is True


# ---------------------------------------------------------------------------------------------------------------------
# 9. Wiring: schema, front end, admin page
# ---------------------------------------------------------------------------------------------------------------------

def test_the_table_is_created_by_the_startup_bootstrap():
    statements = idempotent_statements((ROOT / "schema.sql").read_text(encoding="utf-8"))
    mine = [s for s in statements if s["target"] == "forecast_product_map"]
    assert len(mine) == 1 and mine[0]["kind"] == "table"
    sql = re.sub(r"\s+", " ", mine[0]["sql"])
    for column in ("product_name TEXT PRIMARY KEY", "enabled BOOLEAN NOT NULL", "method TEXT NOT NULL CHECK (method IN ('platform', 'generic', 'tiktok'))",
                   "platform TEXT", "hispanic_weighted BOOLEAN NOT NULL DEFAULT FALSE"):
        assert column in sql, column


APP_JS = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
ADMIN_JS = (ROOT / "app" / "static" / "admin.js").read_text(encoding="utf-8")
ADMIN_HTML = (ROOT / "app" / "templates" / "admin.html").read_text(encoding="utf-8")


def test_the_avails_card_reads_the_products_forecast_config_not_a_hard_coded_family():
    card = APP_JS.split("function renderAvailsGrid", 1)[1].split("\nfunction ", 1)[0]
    assert "${!isFreeform && _forecastEnabled(p) ? `" in card
    assert 'p.family === "Entravision Plus"' not in card
    helper = APP_JS.split("function _forecastEnabled(p) {", 1)[1].split("\n}\n", 1)[0]
    assert "p.forecast ? !!p.forecast.enabled" in helper and 'p.family === "Entravision Plus"' in helper     # the old rule is only a fallback


def test_forecast_enabled_helper_behaviour():
    engine_path = shutil.which("node") or (shutil.which("osascript") if sys.platform == "darwin" else None)
    if not engine_path:
        pytest.skip("no JavaScript engine available")
    fn = "function _forecastEnabled(p) {" + APP_JS.split("function _forecastEnabled(p) {", 1)[1].split("\n}\n", 1)[0] + "\n}\n"
    cases = [None, {"family": "Entravision Plus"}, {"family": "Display"}, {"family": "Display", "forecast": {"enabled": True}},
             {"family": "Entravision Plus", "forecast": {"enabled": False}}]
    body = fn + "return JSON.stringify(" + json.dumps(cases) + ".map(_forecastEnabled));"
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "s.js"
        if "node" in engine_path:
            script.write_text(f"console.log((function () {{\n{body}\n}})());\n", encoding="utf-8")
            cmd = [engine_path, str(script)]
        else:
            script.write_text(f"function run() {{\n return (function () {{\n{body}\n}})();\n}}\n", encoding="utf-8")
            cmd = [engine_path, "-l", "JavaScript", str(script)]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        pytest.skip(f"JavaScript engine unusable here: {proc.stderr[:200]}")
    assert json.loads(proc.stdout.strip()) == [False, True, False, True, False]


def test_the_admin_page_has_a_forecast_tab_wired_to_the_endpoints():
    assert '<button class="admin-tab" data-tab="forecast">Forecast</button>' in ADMIN_HTML
    for element_id in ("tab-forecast", "fc-summary", "fc-coverage-body", "fc-search", "fc-family-filter", "fc-only-on", "fc-count", "fc-table", "fc-body"):
        assert f'id="{element_id}"' in ADMIN_HTML, element_id
    for token in ('document.getElementById("tab-forecast")', 'tab === "forecast" && !adminState.forecastLoaded', "wireForecast();",
                  '"/api/admin/forecast-map"', '"/api/admin/forecast-map/reset"', '"/api/admin/forecast-map/add-product"'):
        assert token in ADMIN_JS, token
    # the routes exist on the server, and are covered by the blanket admin-only rule
    for route in ('@app.get("/api/admin/forecast-map")', '@app.put("/api/admin/forecast-map")',
                  '@app.post("/api/admin/forecast-map/reset")', '@app.post("/api/admin/forecast-map/add-product")'):
        assert route in (ROOT / "app" / "main.py").read_text(encoding="utf-8"), route
