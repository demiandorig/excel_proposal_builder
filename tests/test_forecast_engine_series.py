"""The estimate engine over series in the forecast store (constanza_engine.estimate_from_series), offline: the store is
replaced by in-memory series, so what is pinned is the MATH and the routing, not Postgres.

The most important test here is the first: for the seven legacy series the database path must give exactly the
numbers the original bundled-workbook path gave, so moving them into Postgres changed nothing a planner can see."""
from __future__ import annotations

import math

import pytest

from app.data.constanza import load_platform_dma
from app.services import constanza_engine as engine
from app.services import constanza_forecast_map as fm
from app.services import forecast_store as store
from app.services.constanza_geo import resolve_geo
from app.services.forecast_import import geo_reference as geo

LEGACY = ["roku", "prime", "hulu", "peacock", "other_fast", "vmvpd", "cumulative"]
GEOS = ["National", "Los Angeles", "Sacramento", "California", "CA, NV, AZ", "95814", "90210; 94105; 10001", "Los Angeles County, CA",
        "Santa Monica, CA", "CA-12", "Texas", "Alaska", "Harris County, TX", "Plano, TX"]


def ref(key="t-series", sid=9001, unit="monthly", basis="platform_reported", levels=("dma",), relation="primary", note=None, **kw) -> store.SeriesRef:
    base = dict(series_id=sid, series_key=key, title=f"Series {key}", channel="ctv", avails_unit=unit, targeting_key="general", version=3,
                data_basis=basis, period_label=None, period_start=None, period_end=None, band_low=0.7, band_high=1.3,
                levels=frozenset(levels), exported_on="2026-10-13", relation=relation, link_note=note)
    base.update(kw)
    return store.SeriesRef(**base)


@pytest.fixture
def fake(monkeypatch):
    """A store whose data a test fills in."""
    class Fake:
        series: dict = {}
        dmas: dict = {}
        zips: dict = {}
        cds: dict = {}
        unknown: dict = {}
    f = Fake()
    f.series, f.dmas, f.zips, f.cds, f.unknown = {}, {}, {}, {}, {}
    monkeypatch.setattr(store, "series_by_key", lambda k: f.series.get(k))
    monkeypatch.setattr(store, "dma_table", lambda sid: f.dmas.get(sid, {}))
    monkeypatch.setattr(store, "zip_rows", lambda sid, zs: {z: f.zips[(sid, z)] for z in zs if (sid, z) in f.zips})
    monkeypatch.setattr(store, "cd_rows", lambda sid, ks: {k: f.cds[(sid, k)] for k in ks if (sid, k) in f.cds})
    monkeypatch.setattr(store, "unknown_share", lambda sid: f.unknown.get(sid, {}))
    return f


def estimate(product, text, months=1, hint=None, config=None, **kw):
    res = resolve_geo(text, hint)
    assert res.resolved, text
    return engine.estimate_avails(product, res, months=months, config=config, **kw)


def cfg(platform, hispanic=False, relation="primary"):
    return fm.ForecastConfig(enabled=True, method="platform", platform=platform, hispanic_weighted=hispanic, source="default", relation=relation)


def uniform_dmas(avails=1_000_000.0, basis="reported"):
    return {d.key: store.Row(d.key, avails, basis=basis) for d in geo.dma_table()}


# --------------------------------------------------------------------------------------------- legacy equivalence

def legacy_series(fake, platform, sid):
    data = load_platform_dma()[platform]
    fake.series[platform] = ref(platform, sid, basis="modeled")
    fake.dmas[sid] = {}
    for name, f in data.items():
        key = geo.dma_key_for_label(name)
        fake.dmas[sid][key] = store.Row(key, f.imps_mid, f.imps_low, f.imps_high, None, f.unique_viewers, f.unique_hh, f.reach_pct, "modeled")


@pytest.mark.parametrize("platform", LEGACY)
def test_the_database_path_reproduces_the_bundled_numbers_for_every_legacy_series(fake, platform):
    legacy_series(fake, platform, 100 + LEGACY.index(platform))
    series = fake.series[platform]
    checked = 0
    for text in GEOS:
        res = resolve_geo(text)
        if not res.resolved:
            continue
        for months in (1, 3):
            a = engine._estimate_from_bundle("X", platform, res, months)
            b = engine.estimate_from_series("X", series, res, months)
            for f in ("imps_low", "imps_mid", "imps_high"):
                assert math.isclose(getattr(a, f), getattr(b, f), rel_tol=1e-9, abs_tol=1e-6), (platform, text, months, f)
            assert (a.est_uniques is None) == (b.est_uniques is None)
            if a.est_uniques is not None:
                assert math.isclose(a.est_uniques, b.est_uniques, rel_tol=1e-9)
            assert [(x.area_label, round(x.weight, 12)) for x in a.by_area] == [(x.area_label, round(x.weight, 12)) for x in b.by_area]
            checked += 1
    assert checked >= 20


def test_a_hispanic_weighted_product_keeps_its_weighting(fake):
    legacy_series(fake, "cumulative", 200)
    series = fake.series["cumulative"]
    res = resolve_geo("CA, TX, NY")
    a = engine._estimate_from_bundle("X", "cumulative", res, 1, hispanic_weighted=True)
    b = engine.estimate_from_series("X", series, res, 1, hispanic_weighted=True)
    assert [round(x.weight, 12) for x in a.by_area] == [round(x.weight, 12) for x in b.by_area]
    assert any("Hispanic population" in n for n in b.notes)


# --------------------------------------------------------------------------------------------- the most direct row wins

def test_a_zip_uses_the_series_own_zip_row_not_a_share_of_its_dma(fake):
    fake.series["s"] = ref("s", 1, levels=("dma", "zip"))
    fake.dmas[1] = uniform_dmas(1_000_000)
    fake.zips[(1, "90210")] = store.Row("90210", 226_700.0, reach_universe=6000, est_reach=5500)
    est = estimate("P", "90210", config=cfg("s"))
    assert est.imps_mid == 226_700 and est.est_uniques == 5500                      # exact, with no population-share scaling
    assert est.imps_low == pytest.approx(226_700 * 0.7) and est.imps_high == pytest.approx(226_700 * 1.3)
    assert est.by_area[0].dma == "Los Angeles" and "ZIP 90210" in est.by_area[0].area_label


def test_several_zips_add_up_and_scale_with_months(fake):
    fake.series["s"] = ref("s", 1, levels=("dma", "zip"))
    fake.dmas[1] = uniform_dmas()
    fake.zips[(1, "90210")] = store.Row("90210", 100.0, est_reach=10)
    fake.zips[(1, "94105")] = store.Row("94105", 300.0, est_reach=30)
    est = estimate("P", "90210; 94105", months=3, config=cfg("s"))
    assert est.imps_mid == 1200 and est.est_uniques == 40                              # impressions x months, reach is monthly


def test_a_zip_with_no_row_counts_as_zero_and_says_so(fake):
    fake.series["s"] = ref("s", 1, levels=("dma", "zip"))
    fake.dmas[1] = uniform_dmas()
    fake.zips[(1, "90210")] = store.Row("90210", 500.0)
    est = estimate("P", "90210; 94105", config=cfg("s"))
    assert est.imps_mid == 500 and any("No avails in this series for: ZIP 94105" in n for n in est.notes)


def test_a_series_without_zip_rows_apportions_from_the_dma_as_before(fake):
    fake.series["s"] = ref("s", 1, levels=("dma",))
    fake.dmas[1] = uniform_dmas(1_000_000)
    res = resolve_geo("90210")
    est = engine.estimate_avails("P", res, config=cfg("s"))
    assert est.imps_mid == pytest.approx(1_000_000 * res.areas[0].dma_share)


def test_a_congressional_district_uses_its_own_row(fake):
    fake.series["s"] = ref("s", 1, levels=("dma", "cd"))
    fake.dmas[1] = uniform_dmas(1_000_000)
    fake.cds[(1, "CA-12")] = store.Row("CA-12", 6_453_400.0, est_reach=168_700)
    est = estimate("P", "CA-12", config=cfg("s"))
    assert est.imps_mid == 6_453_400 and est.est_uniques == 168_700
    fake.series["s"] = ref("s", 1, levels=("dma",))                                    # no district level: DMA shares instead
    assert estimate("P", "CA-12", config=cfg("s")).imps_mid != 6_453_400


def test_a_whole_dma_uses_its_dma_row_and_a_county_a_share_of_it(fake):
    fake.series["s"] = ref("s", 1, levels=("dma",))
    fake.dmas[1] = uniform_dmas(1_000_000)
    assert estimate("P", "Los Angeles", config=cfg("s")).imps_mid == 1_000_000
    county = resolve_geo("Los Angeles County, CA")
    est = engine.estimate_avails("P", county, config=cfg("s"))
    assert est.imps_mid == pytest.approx(1_000_000 * county.areas[0].dma_share) and 0 < est.imps_mid < 1_000_000


def test_a_market_the_series_lacks_is_zero_with_a_note(fake):
    fake.series["s"] = ref("s", 1)
    fake.dmas[1] = {k: v for k, v in uniform_dmas().items() if k != "glendive"}
    est = estimate("P", "Glendive", config=cfg("s"))
    assert est.imps_mid == 0 and any("No avails in this series for: Glendive" in n for n in est.notes)


# --------------------------------------------------------------------------------------------- units, bands, provenance

def test_weekly_series_multiply_by_the_weeks_of_the_flight(fake):
    fake.series["w"] = ref("w", 2, unit="weekly", basis="modeled", period_label="Regular season, ends Sep 27, 2026")
    fake.dmas[2] = uniform_dmas(1000.0, "modeled")
    by_months = estimate("P", "Los Angeles", months=2, config=cfg("w"))
    assert by_months.weeks == pytest.approx(2 * 365.25 / 12 / 7) and by_months.imps_mid == pytest.approx(1000 * by_months.weeks)
    by_days = estimate("P", "Los Angeles", months=1, config=cfg("w"), flight_days=21)
    assert by_days.weeks == 3.0 and by_days.imps_mid == 3000                           # the line's real length beats months x 4.35
    note = next(n for n in by_days.notes if "WEEKLY" in n)
    assert "Regular season, ends Sep 27, 2026" in note and "3.0 weeks" in note and "not checked" in note


def test_monthly_series_ignore_flight_days(fake):
    fake.series["m"] = ref("m", 3)
    fake.dmas[3] = uniform_dmas(1000.0)
    est = estimate("P", "Los Angeles", months=2, config=cfg("m"), flight_days=500)
    assert est.imps_mid == 2000 and est.weeks is None and not any("WEEKLY" in n for n in est.notes)


def test_a_row_with_its_own_planning_range_uses_it_and_extended_rows_get_a_wider_one(fake):
    fake.series["m"] = ref("m", 4, basis="modeled")
    fake.dmas[4] = {**uniform_dmas(), "los-angeles": store.Row("los-angeles", 1000.0, 800.0, 1500.0, basis="modeled")}
    est = estimate("P", "Los Angeles", config=cfg("m"))
    assert (est.imps_low, est.imps_mid, est.imps_high) == (800.0, 1000.0, 1500.0)
    fake.series["x"] = ref("x", 5, basis="extrapolated")
    fake.dmas[5] = {**uniform_dmas(1000.0), "los-angeles": store.Row("los-angeles", 1000.0, basis="reported"), "chicago": store.Row("chicago", 1000.0, basis="modeled")}
    reported, extended = estimate("P", "Los Angeles", config=cfg("x")), estimate("P", "Chicago", config=cfg("x"))
    assert reported.imps_low == pytest.approx(700) and extended.imps_low == pytest.approx(550) and extended.imps_high == pytest.approx(1450)
    assert any("EXTENDED" in n for n in extended.notes) and not any("EXTENDED" in n for n in reported.notes)


def test_every_estimate_says_where_its_numbers_came_from(fake):
    fake.series["s"] = ref("s", 1, device_mix={"connected_tv": 0.8, "mobile": 0.2}, top_publishers=[["Tubi", 0.3]])
    fake.dmas[1] = uniform_dmas()
    est = estimate("P", "Los Angeles", config=cfg("s"))
    assert est.series["key"] == "s" and est.series["version"] == 3 and est.series["data_basis"] == "platform_reported"
    assert est.series["device_mix"] == {"connected_tv": 0.8, "mobile": 0.2} and est.series["top_publishers"] == [["Tubi", 0.3]]
    assert est.platform == "s" and est.confidence == "platform_modeled"
    assert any("Platform-reported avails" in n and "v3" in n and "2026-10-13" in n for n in est.notes)


def test_a_baseline_proxy_product_is_told_it_is_an_upper_bound(fake):
    fake.series["n"] = ref("n", 1, relation="baseline_proxy", note="Run-of-network baseline; this targeted variant will deliver fewer impressions.")
    fake.dmas[1] = uniform_dmas()
    est = estimate("Netflix - Age Targeted :15s Ads", "Los Angeles", config=cfg("n", relation="baseline_proxy"))
    assert any("will deliver fewer impressions" in n for n in est.notes) and est.series["relation"] == "baseline_proxy"


def test_a_national_total_mentions_what_the_platform_could_not_place(fake):
    fake.series["s"] = ref("s", 1)
    fake.dmas[1] = uniform_dmas(1_000_000)
    fake.unknown[1] = {"dma": 5_000_000.0}
    est = estimate("P", "National", config=cfg("s"))
    note = next(n for n in est.notes if "could not place" in n)
    assert "2.3%" in note                                                              # 5M of (5M + 210M)


# --------------------------------------------------------------------------------------------- routing and fail-open

def test_routing_through_estimate_avails_uses_the_effective_config(fake):
    fake.series["roku"] = ref("roku", 1)
    fake.dmas[1] = uniform_dmas(123.0)
    est = estimate("Entravision Plus - Roku Ads (Includes The Roku Channel and the popular Espacio Latino Hub)", "Los Angeles")
    assert est.platform == "roku" and est.imps_mid == 123.0


def test_a_series_the_store_cannot_serve_falls_back_cleanly(fake):
    res = resolve_geo("Los Angeles")
    assert engine.estimate_avails("P", res, config=cfg("not-loaded")) is None            # nothing to fall back to, no total given
    generic = engine.estimate_avails("P", res, config=cfg("not-loaded"), national_monthly_total=1_000_000)
    assert generic.confidence == "generic_fallback" and generic.platform == "generic_population"


def test_a_legacy_series_still_answers_from_the_bundled_data_when_the_database_fails(fake, monkeypatch):
    fake.series["roku"] = ref("roku", 1)
    def boom(sid):
        raise RuntimeError("database went away")
    monkeypatch.setattr(store, "dma_table", boom)
    res = resolve_geo("Los Angeles")
    est = engine.estimate_avails("P", res, config=cfg("roku"))
    bundled = engine._estimate_from_bundle("P", "roku", res, 1)
    assert est.imps_mid == bundled.imps_mid


def test_a_new_series_whose_data_cannot_be_read_does_not_become_a_500(fake, monkeypatch):
    fake.series["s"] = ref("s", 1)
    monkeypatch.setattr(store, "dma_table", lambda sid: (_ for _ in ()).throw(RuntimeError("database went away")))
    res = resolve_geo("Los Angeles")
    assert engine.estimate_avails("P", res, config=cfg("s")) is None
    assert engine.estimate_avails("P", res, config=cfg("s"), national_monthly_total=5e6).confidence == "generic_fallback"


def test_international_geography_never_uses_dma_series(fake):
    fake.series["s"] = ref("s", 1)
    fake.dmas[1] = uniform_dmas()
    res = resolve_geo("Mexico", "country")
    assert engine.estimate_avails("P", res, config=cfg("s")) is None
    assert engine.estimate_avails("P", res, config=cfg("s"), national_monthly_total=1e6).confidence == "generic_fallback"
