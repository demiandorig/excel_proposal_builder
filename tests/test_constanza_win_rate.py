"""The programmatic win rate (app/services/constanza_win_rate.py) and what it does to Constanza's estimates, offline.

Avails from MadHive / The Trade Desk are BID OPPORTUNITIES; Entravision wins 10-20% of them. So for those products the
impressions are avails x 15% (range 10-20%), the unique IDs are re-derived for that smaller delivery (never above the
impressions), Prime Video (bought through the Amazon DSP) is avails x its own single 20%, and everything bought directly (Roku Ads,
TikTok's API, a population-share estimate of the planner's OWN total) is untouched. What is pinned here: the math (with property checks over a grid of ratios), the product
classification against the REAL catalog and series manifest, the engine wiring, and that the audience layer composes with it so
the people counts a card shows stay consistent with the impressions beside them."""
from __future__ import annotations

import itertools
import json
import logging
import math
from pathlib import Path

import pytest

from app import catalog
from app.services import constanza_engine as engine
from app.services import constanza_forecast_map as fm
from app.services import constanza_win_rate as wr
from app.services import forecast_store as store
from app.services.constanza_audience import AudienceSpec, estimate_audience
from app.services.constanza_geo import resolve_geo
from app.services.forecast_import import geo_reference as geo

ROOT = Path(__file__).resolve().parents[1]
ROKU = "Entravision Plus - Roku Ads (Includes The Roku Channel and the popular Espacio Latino Hub)"
PRIME = "Entravision Plus - Amazon Prime Video"
HULU = "Entravision Plus - Hulu"
VIX360 = "Entravision Plus - VIX 360 (Includes VIX + UnivisionNow + Univision.com / Univision Apps)"
REACH = "Entravision Plus - CTV/OTT Reach"
NETFLIX = "Netflix - Run Of Network Untargeted :15s Ads"

DEFAULT = wr.WinRate(0.10, 0.15, 0.20)


# ---------------------------------------------------------------------------------------------------------- fakes

def ref(key="s", sid=1, unit="monthly", basis="platform_reported", levels=("dma",)) -> store.SeriesRef:
    return store.SeriesRef(series_id=sid, series_key=key, title=f"Series {key}", channel="ctv", avails_unit=unit, targeting_key="general",
                           version=1, data_basis=basis, period_label=None, period_start=None, period_end=None, band_low=0.7, band_high=1.3,
                           levels=frozenset(levels), exported_on="2026-10-13", relation="primary", link_note=None)


@pytest.fixture
def fake(monkeypatch):
    class Fake:
        series: dict
        dmas: dict
        zips: dict
    f = Fake()
    f.series, f.dmas, f.zips = {}, {}, {}
    monkeypatch.setattr(store, "series_by_key", lambda k: f.series.get(k))
    monkeypatch.setattr(store, "dma_table", lambda sid: f.dmas.get(sid, {}))
    monkeypatch.setattr(store, "zip_rows", lambda sid, zs: {z: f.zips[(sid, z)] for z in zs if (sid, z) in f.zips})
    monkeypatch.setattr(store, "cd_rows", lambda sid, ks: {})
    monkeypatch.setattr(store, "unknown_share", lambda sid: {})
    monkeypatch.setattr(store, "state_rows", lambda sid, ss: {})
    return f


def cfg(platform="s", win_rate=False) -> fm.ForecastConfig:
    return fm.ForecastConfig(enabled=True, method="platform", platform=platform, hispanic_weighted=False, source="default", win_rate=win_rate)


def uniform(avails, universe=None, reach=None, basis="reported"):
    return {d.key: store.Row(d.key, avails, reach_universe=universe, est_reach=reach, basis=basis) for d in geo.dma_table()}


def estimate(text, months=1, config=None, **kw):
    res = resolve_geo(text)
    assert res.resolved, text
    return engine.estimate_avails("P", res, months=months, config=config, **kw)


# ---------------------------------------------------------------------------------------------------------- the rate itself

def test_the_default_win_rate_is_10_15_20_percent(monkeypatch):
    for name in (wr.ENV_LOW, wr.ENV_MID, wr.ENV_HIGH):
        monkeypatch.delenv(name, raising=False)
    assert wr.configured() == DEFAULT == wr.WinRate(wr.DEFAULT_LOW, wr.DEFAULT_MID, wr.DEFAULT_HIGH)


@pytest.mark.parametrize("low,mid,high,expected", [
    ("12", "17", "22", (0.12, 0.17, 0.22)),            # percents
    ("0.12", "0.17", "0.22", (0.12, 0.17, 0.22)),      # fractions
    ("8%", "15%", "25%", (0.08, 0.15, 0.25)),
    ("15", "15", "15", (0.15, 0.15, 0.15)),            # a single rate, no range
    ("1%", "5%", "10%", (0.01, 0.05, 0.10)),           # an explicit % is always a percent (1% is one percent, not 100%)
    ("0.5%", "5%", "10%", (0.005, 0.05, 0.10)),        # ... and 0.5% is half a percent, not 50%
    ("1", "5", "10", (0.01, 0.05, 0.10)),              # a bare 1 is one percent: nobody means a 100% win rate
])
def test_the_environment_can_set_the_win_rate(monkeypatch, low, mid, high, expected):
    monkeypatch.setenv(wr.ENV_LOW, low); monkeypatch.setenv(wr.ENV_MID, mid); monkeypatch.setenv(wr.ENV_HIGH, high)
    assert wr.configured() == pytest.approx(wr.WinRate(*expected))


@pytest.mark.parametrize("env,expected", [
    ({wr.ENV_MID: "8"}, (0.08, 0.08, 0.20)),             # a lone MID keeps the default high and a low no higher than itself
    ({wr.ENV_MID: "12"}, (0.10, 0.12, 0.20)),
    ({wr.ENV_MID: "30"}, (0.10, 0.30, 0.30)),            # ... and a MID above the default high lifts the high to it
    ({wr.ENV_LOW: "18"}, (0.18, 0.18, 0.20)),            # a lone LOW lifts the mid to it
    ({wr.ENV_HIGH: "12"}, (0.10, 0.12, 0.12)),           # a lone HIGH pulls the mid down to it
    ({wr.ENV_LOW: "5", wr.ENV_HIGH: "25"}, (0.05, 0.15, 0.25)),
])
def test_setting_only_some_of_the_three_fills_in_the_rest_in_order(monkeypatch, env, expected):
    for name in (wr.ENV_LOW, wr.ENV_MID, wr.ENV_HIGH):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    assert wr.configured() == pytest.approx(wr.WinRate(*expected))


@pytest.mark.parametrize("low,mid,high", [("abc", "15", "20"), ("20", "15", "10"), ("0", "15", "20"), ("10", "15", "250"), ("10", "", "nan"), ("-5", "15", "20")])
def test_a_bad_environment_falls_back_to_the_default_and_warns(monkeypatch, caplog, low, mid, high):
    monkeypatch.setenv(wr.ENV_LOW, low); monkeypatch.setenv(wr.ENV_MID, mid); monkeypatch.setenv(wr.ENV_HIGH, high)
    with caplog.at_level(logging.WARNING):
        assert wr.configured() == DEFAULT
    assert any("win rate" in r.getMessage().lower() for r in caplog.records)


def test_rates_are_written_the_way_a_planner_reads_them():
    assert [wr.pct_text(x) for x in (0.15, 0.1, 0.2, 0.125, 0.075, 0.005, 1.0)] == ["15%", "10%", "20%", "12.5%", "7.5%", "0.5%", "100%"]


# ---------------------------------------------------------------------------------------------------------- the impressions

def test_all_three_impressions_are_the_platforms_figures_times_the_mid_win_rate():
    low, mid, high = wr.scale(70.0, 100.0, 130.0, DEFAULT)
    assert (low, mid, high) == pytest.approx((10.5, 15.0, 19.5))          # the platform's +-30% band, on the smaller number


def test_the_win_rate_range_is_not_folded_into_the_planning_band():
    # a Low below "10% of the avails" read as a mistake next to "10%-20%": the band keeps its meaning, the range is its own figure
    low, mid, high = wr.scale(70.0, 100.0, 130.0, DEFAULT)
    assert low >= 0.10 * 70.0 and high <= 0.20 * 130.0
    assert low / mid == pytest.approx(0.7) and high / mid == pytest.approx(1.3)


def test_a_row_the_platform_reports_asymmetrically_keeps_its_asymmetry():
    low, mid, high = wr.scale(50.0, 100.0, 110.0, DEFAULT)
    assert 1 - low / mid == pytest.approx(0.5) and high / mid - 1 == pytest.approx(0.1)


def test_nothing_in_nothing_out():
    assert wr.scale(0.0, 0.0, 0.0, DEFAULT) == (0.0, 0.0, 0.0)
    assert wr.scale(0.0, -5.0, 0.0, DEFAULT) == (0.0, 0.0, 0.0)
    assert wr.scale(0.0, 100.0, 130.0, DEFAULT)[0] == 0.0


# ---------------------------------------------------------------------------------------------------------- unique IDs

GRID = list(itertools.product(
    (1.0, 50.0, 1_000.0, 80_000.0, 3_000_000.0),                 # est. reach
    (0.4, 0.9, 1.0, 1.5, 3.0, 6.7, 10.0, 15.4, 40.0, 400.0),    # bid opportunities per reached ID (avails / est. reach)
    (0.05, 0.10, 0.15, 0.20, 0.50, 1.0)))                       # win rate


@pytest.mark.parametrize("reach,n,w", GRID)
def test_unique_ids_are_never_above_the_platforms_reach_or_the_impressions_delivered(reach, n, w):
    avails = reach * n
    got = wr.reached_ids(reach, avails, w)
    delivered = avails * w
    assert 0 <= got <= reach * (1 + 1e-12) and got <= delivered * (1 + 1e-12)
    if delivered >= 1e-9:
        assert delivered / got >= 1 - 1e-9                       # frequency is never below one


def test_a_bigger_win_rate_or_more_opportunities_reach_more_ids_and_never_fewer():
    reach = 100_000.0
    by_w = [wr.reached_ids(reach, reach * 10, w) for w in (0.05, 0.10, 0.15, 0.20, 0.40)]
    assert by_w == sorted(by_w) and by_w[0] < by_w[-1]
    by_n = [wr.reached_ids(reach, reach * n, 0.15) for n in (1, 2, 5, 10, 40)]
    assert by_n == sorted(by_n) and by_n[-1] <= reach


def test_the_reach_model_matches_a_hand_worked_example():
    # 10 opportunities per ID, each won with probability .15: an ID is reached with probability 1 - .85^10 = 0.8031
    got = wr.reached_ids(100_000.0, 1_000_000.0, 0.15)
    assert got == pytest.approx(100_000 * (1 - 0.85 ** 10))
    assert (1_000_000 * 0.15) / got == pytest.approx(1.868, abs=1e-3)           # about 1.9 impressions per reached ID


def test_missing_or_empty_reach_never_invents_ids():
    assert wr.reached_ids(None, 1000.0, 0.15) is None
    assert wr.reached_ids(0.0, 1000.0, 0.15) == 0.0 and wr.reached_ids(100.0, 0.0, 0.15) == 0.0 and wr.reached_ids(100.0, 1000.0, 0.0) == 0.0
    assert wr.cap_uniques(None, 5.0) is None and wr.cap_uniques(10.0, 4.0) == 4.0 and wr.cap_uniques(3.0, 4.0) == 3.0 and wr.cap_uniques(3.0, -1.0) == 0.0


# ---------------------------------------------------------------------------------------------------------- the engine

def la(fake, **row):
    fake.series["s"] = ref()
    fake.dmas[1] = uniform(1_000_000.0, universe=150_000.0, reach=100_000.0)


def test_a_win_rate_product_returns_the_expected_delivery_and_remembers_the_raw_avails(fake):
    la(fake)
    raw = estimate("Los Angeles", config=cfg())
    won = estimate("Los Angeles", config=cfg(win_rate=True))
    assert raw.win_rate is None and raw.imps_mid == pytest.approx(1_000_000.0)
    assert won.imps_mid == pytest.approx(150_000.0)
    assert won.imps_low == pytest.approx(raw.imps_low * 0.15) and won.imps_high == pytest.approx(raw.imps_high * 0.15)    # the platform's band, x15%
    assert won.win_rate == {"low": 0.10, "mid": 0.15, "high": 0.20, "raw_imps_low": raw.imps_low, "raw_imps_mid": raw.imps_mid,
                            "raw_imps_high": raw.imps_high, "raw_est_uniques": raw.est_uniques,
                            "delivered_at_low": pytest.approx(100_000.0), "delivered_at_high": pytest.approx(200_000.0)}
    assert won.notes[0].startswith("Win rate applied") and "15%" in won.notes[0] and "10%–20%" in won.notes[0]
    assert "at 10%: 100,000" in won.notes[0] and "at 20%: 200,000" in won.notes[0]


def test_the_note_says_whose_figures_the_avails_are(fake):
    la(fake)
    assert "these platform-reported avails" in estimate("Los Angeles", config=cfg(win_rate=True)).notes[0]
    fake.series["s"] = ref(basis="modeled")
    assert "these modeled avails" in estimate("Los Angeles", config=cfg(win_rate=True)).notes[0]
    fake.series["s"] = ref(basis="extrapolated")
    assert "these extended avails" in estimate("Los Angeles", config=cfg(win_rate=True)).notes[0]


def test_a_fractional_rate_is_written_with_its_decimals_in_the_note(fake, monkeypatch):
    la(fake)
    monkeypatch.setenv(wr.ENV_LOW, "7.5"); monkeypatch.setenv(wr.ENV_MID, "12.5"); monkeypatch.setenv(wr.ENV_HIGH, "17.5")
    note = estimate("Los Angeles", config=cfg(win_rate=True)).notes[0]
    assert "about 12.5%" in note and "(7.5%–17.5%)" in note


def test_unique_ids_follow_the_smaller_delivery_and_stay_below_it(fake):
    la(fake)
    raw = estimate("Los Angeles", config=cfg())
    won = estimate("Los Angeles", config=cfg(win_rate=True))
    assert raw.est_uniques == pytest.approx(100_000.0)
    assert won.est_uniques == pytest.approx(100_000 * (1 - 0.85 ** 10))        # 10 opportunities each
    assert won.est_uniques < raw.est_uniques and won.est_uniques <= won.imps_mid
    assert won.reach_universe == raw.reach_universe == pytest.approx(150_000.0)   # the pool is the market's, not delivery's
    assert won.by_area[0].reach_universe == pytest.approx(150_000.0)


def test_the_estimate_totals_are_the_sum_of_the_areas(fake):
    la(fake)
    won = estimate("Los Angeles; Chicago; Boston", config=cfg(win_rate=True))
    assert len(won.by_area) == 3
    assert won.imps_mid == pytest.approx(sum(a.imps_mid for a in won.by_area))
    assert won.imps_low == pytest.approx(sum(a.imps_low for a in won.by_area)) and won.imps_high == pytest.approx(sum(a.imps_high for a in won.by_area))
    assert won.est_uniques == pytest.approx(sum(a.unique_viewers for a in won.by_area))
    assert won.reach_universe == pytest.approx(450_000.0)


def test_a_longer_flight_scales_impressions_but_not_unique_ids(fake):
    la(fake)
    one = estimate("Los Angeles", months=1, config=cfg(win_rate=True))
    three = estimate("Los Angeles", months=3, config=cfg(win_rate=True))
    assert three.imps_mid == pytest.approx(3 * one.imps_mid)
    assert three.est_uniques == pytest.approx(one.est_uniques) and three.reach_universe == pytest.approx(one.reach_universe)
    assert three.est_uniques <= three.imps_mid / 3                                # per month, uniques are still below impressions


def test_a_weekly_series_uses_weeks_not_months_for_the_opportunities(fake):
    fake.series["s"] = ref(unit="weekly", basis="modeled")
    fake.dmas[1] = uniform(1_000_000.0, universe=150_000.0, reach=100_000.0, basis="modeled")
    won = estimate("Los Angeles", months=1, config=cfg(win_rate=True), flight_days=14)       # two weeks
    raw = estimate("Los Angeles", months=1, config=cfg(), flight_days=14)
    assert won.weeks == pytest.approx(2.0) and won.imps_mid == pytest.approx(raw.imps_mid * 0.15)
    per_week = raw.imps_mid / 2
    assert won.est_uniques == pytest.approx(wr.reached_ids(100_000.0, per_week, 0.15))
    assert "a week" in won.notes[0]


def test_a_product_without_the_flag_is_untouched(fake):
    la(fake)
    raw = estimate("Los Angeles", config=cfg(win_rate=False))
    assert raw.win_rate is None and raw.imps_mid == pytest.approx(1_000_000.0) and raw.est_uniques == pytest.approx(100_000.0)
    assert not any("Win rate" in n for n in raw.notes)


def test_the_environment_win_rate_reaches_the_estimate(fake, monkeypatch):
    la(fake)
    monkeypatch.setenv(wr.ENV_LOW, "5"); monkeypatch.setenv(wr.ENV_MID, "8"); monkeypatch.setenv(wr.ENV_HIGH, "12")
    won = estimate("Los Angeles", config=cfg(win_rate=True))
    assert won.imps_mid == pytest.approx(80_000.0) and won.win_rate["mid"] == 0.08


def test_unique_ids_never_exceed_impressions_even_without_a_win_rate(fake):
    # a ZIP the platform reports at its floor can carry more reach than avails; an ID needs at least one impression
    fake.series["s"] = ref(levels=("dma", "zip"))
    fake.dmas[1] = uniform(1_000_000.0)
    fake.zips[(1, "90210")] = store.Row("90210", 100.0, reach_universe=500.0, est_reach=400.0)
    est = estimate("90210", config=cfg())
    assert est.imps_mid == 100.0 and est.est_uniques == pytest.approx(100.0) and est.by_area[0].unique_viewers == pytest.approx(100.0)
    assert any("capped at the impressions" in n for n in est.notes)
    won = estimate("90210", config=cfg(win_rate=True))
    assert won.est_uniques <= won.imps_mid <= 15.0 + 1e-9


def test_the_cap_stays_quiet_when_it_does_not_bind(fake):
    la(fake)
    assert not any("capped" in n for n in estimate("Los Angeles", config=cfg()).notes)


def test_a_population_share_estimate_is_never_multiplied(fake):
    # the planner's OWN impression total spread by population: there are no avails in it, so nothing to win
    res = resolve_geo("Los Angeles")
    est = engine.estimate_avails("P", res, months=1, national_monthly_total=500_000.0,
                                 config=fm.ForecastConfig(enabled=True, method="generic", platform=None, hispanic_weighted=False, source="default", win_rate=True))
    assert est.confidence == "generic_fallback" and est.imps_mid == pytest.approx(500_000.0) and est.win_rate is None


# ---------------------------------------------------------------------------------------------------------- which products

def manifest_links() -> dict:
    manifest = json.loads((ROOT / "app" / "data" / "forecast" / "manifest.json").read_text())
    return {p["name"]: (s["series_key"], p["relation"]) for s in manifest["series"] for p in s["products"]}


@pytest.fixture
def linked(monkeypatch):
    """Every catalog product linked to its default series exactly as the manifest says (no database)."""
    links = manifest_links()

    def series_for_product(names):
        for name in names:
            if name in links:
                key, relation = links[name]
                return ref(key).with_link(relation, None)
        return None
    monkeypatch.setattr(store, "series_for_product", series_for_product)
    return links


def default_win_rate(name):
    return fm.default_config(name).win_rate


def test_roku_ads_is_the_only_forecast_product_exempt_from_the_win_rate(linked):
    platform_products = [p for p in catalog.CATALOG if fm.default_config(p.name).method == "platform"]
    assert len(platform_products) >= 50                                           # every series-backed product, in any family (51 today)
    exempt = {p.name for p in platform_products if not fm.default_config(p.name).win_rate}
    assert exempt == {ROKU}
    assert fm.default_config(PRIME).win_rate is True                              # Prime Video (Amazon DSP) has a rate of its own


def test_the_madhive_and_trade_desk_products_all_get_the_win_rate(linked):
    for name in (HULU, REACH, NETFLIX, VIX360, "Entravision Plus - Peacock", "Entravision+ Connect - (Default)",
                 "Entravision+ Hispanic Connect - Family", "Entravision+ Pause Ads", "Entravision Plus - VIX Select",
                 "Entravision Plus - Live Sports - NBA", "Netflix - Age Targeted :30s Ads", "Entravision Plus - Hispanics CTV/OTT",
                 "eDigital Display", "eDigital OLV"):                              # eDigital's series are MadHive exports too
        cfgd = fm.default_config(name)
        assert cfgd.method == "platform" and cfgd.win_rate is True, name


def test_a_product_with_no_forecast_from_data_never_gets_it(linked):
    for p in catalog.CATALOG:
        cfgd = fm.default_config(p.name)
        if cfgd.method != "platform":
            assert cfgd.win_rate is False, p.name


def test_tiktok_and_population_share_products_never_get_it(linked):
    assert fm.default_config("Tiktok Ads").win_rate is False
    generic = fm.default_config("Entravision Plus - Online Video (OLV) Blend Reach")
    assert generic.method == "generic" and generic.win_rate is False


def test_the_series_a_product_is_forecast_from_decides_the_default(linked):
    row = {"enabled": True, "method": "platform", "platform": "cumulative", "hispanic_weighted": False}
    assert fm.resolve_forecast(HULU, overrides={HULU: {**row, "platform": "roku"}}).win_rate is False     # now forecast from Roku's own series
    assert fm.resolve_forecast(HULU, overrides={HULU: {**row, "platform": "prime"}}).win_rate is True       # Prime's series is bought through the Amazon DSP: it carries Prime's own rate
    assert fm.resolve_forecast(HULU, overrides={HULU: row}).win_rate is True
    # ... and the Roku product remapped to a MadHive series is MadHive bid opportunities, whatever its name
    assert fm.resolve_forecast(ROKU, overrides={ROKU: row}).win_rate is True
    assert fm.resolve_forecast(PRIME, overrides={PRIME: {**row, "platform": "connect-default"}}).win_rate is True
    # a population-share estimate or TikTok has no avails to win
    assert fm.resolve_forecast(HULU, overrides={HULU: {**row, "method": "generic", "platform": None}}).win_rate is False
    assert fm.resolve_forecast(HULU, overrides={HULU: {**row, "method": "tiktok", "platform": None}}).win_rate is False


def test_a_renamed_roku_product_stays_exempt(monkeypatch, linked):
    monkeypatch.setattr(fm, "_name_variants", lambda name: {name, ROKU})
    assert fm.default_config("Roku Ads (renamed)", family=fm.EP_FAMILY).win_rate is False


def test_the_catalog_payload_tells_the_ui_which_products_carry_a_win_rate(linked):
    products = [p for p in catalog.CATALOG if p.name in (ROKU, HULU)]
    out = fm.forecast_for_products(products)
    assert out[ROKU]["win_rate"] is False and out[HULU]["win_rate"] is True


# ---------------------------------------------------------------------------------------------------------- with the audience layer

CUTS = [AudienceSpec(), AudienceSpec(age_min=18, age_max=34), AudienceSpec(gender="female"), AudienceSpec(age_min=25, age_max=54, gender="male"),
        AudienceSpec(interest_terms=["sports"]), AudienceSpec(age_min=18, age_max=24, gender="male", interest_terms=["sports"])]


@pytest.mark.parametrize("n", [1.2, 3.0, 6.7, 15.4, 40.0])
@pytest.mark.parametrize("spec", CUTS, ids=lambda s: f"{s.age_min}-{s.age_max}/{s.gender}/{len(s.interest_terms)}i")
def test_the_audience_never_shows_more_unique_ids_than_impressions(fake, n, spec):
    reach = 100_000.0
    fake.series["s"] = ref()
    fake.dmas[1] = uniform(reach * n, universe=reach * 1.4, reach=reach)
    for geo_text in ("Los Angeles", "Sacramento, Fresno", "CA"):
        for months in (1, 3):
            base = estimate(geo_text, months=months, config=cfg(win_rate=True))
            aud = estimate_audience(base, spec)
            assert aud.est_uniques is not None and aud.est_uniques <= aud.imps_mid / months * (1 + 1e-9)
            # the cut multiplies impressions, unique IDs and the pool alike
            cut = aud.imps_mid / base.imps_mid
            assert aud.est_uniques == pytest.approx(base.est_uniques * cut) and aud.audience_people == pytest.approx(base.reach_universe * cut)
            for area in aud.by_area:
                assert area.unique_viewers <= area.imps_mid / months * (1 + 1e-9)


def test_the_audience_narrows_the_win_rate_delivery_not_the_raw_avails(fake):
    la(fake)
    base = estimate("Los Angeles", config=cfg(win_rate=True))
    raw = estimate("Los Angeles", config=cfg())
    aud = estimate_audience(base, AudienceSpec(age_min=18, age_max=34))
    cut = aud.imps_mid / base.imps_mid
    assert 0 < cut < 1 and aud.imps_mid == pytest.approx(raw.imps_mid * 0.15 * cut)
    assert aud.imps_low < aud.imps_mid < aud.imps_high and aud.imps_low >= 0


def test_the_audience_band_is_never_narrower_than_the_estimates_own_band(fake):
    la(fake)
    base = estimate("Los Angeles", config=cfg(win_rate=True))
    aud = estimate_audience(base, AudienceSpec(age_min=18, age_max=34))
    assert aud.imps_low / aud.imps_mid <= base.imps_low / base.imps_mid + 1e-9
    assert aud.imps_high / aud.imps_mid >= base.imps_high / base.imps_mid - 1e-9


# ---------------------------------------------------------------------------------------------------------- gaps the mutation review found

def test_unique_households_follow_the_ids_and_never_exceed_the_delivery(fake):
    fake.series["s"] = ref()
    fake.dmas[1] = {d.key: store.Row(d.key, 1_000_000.0, reach_universe=150_000.0, est_reach=100_000.0, unique_hh=60_000.0) for d in geo.dma_table()}
    raw = estimate("Los Angeles", config=cfg())
    won = estimate("Los Angeles", config=cfg(win_rate=True))
    area_raw, area_won = raw.by_area[0], won.by_area[0]
    assert area_raw.unique_hh == pytest.approx(60_000.0)
    ratio = area_won.unique_viewers / area_raw.unique_viewers                 # about 0.80 at 10 opportunities each
    assert area_won.unique_hh == pytest.approx(60_000.0 * ratio) and area_won.unique_hh < area_raw.unique_hh
    assert area_won.unique_hh <= area_won.imps_mid


def test_households_with_no_reach_figure_are_capped_by_the_delivery(fake):
    fake.series["s"] = ref()
    fake.dmas[1] = {d.key: store.Row(d.key, 100.0, unique_hh=5_000.0) for d in geo.dma_table()}    # no est. reach, hh far above the avails
    won = estimate("Los Angeles", config=cfg(win_rate=True))
    assert won.by_area[0].unique_viewers is None and won.by_area[0].unique_hh == pytest.approx(100.0 * 0.15)


def test_the_cap_holds_per_month_for_a_long_flight_and_per_week_for_a_weekly_series(fake):
    fake.series["s"] = ref(levels=("dma", "zip"))
    fake.dmas[1] = uniform(1_000_000.0)
    fake.zips[(1, "90210")] = store.Row("90210", 100.0, est_reach=400.0)               # a floor row: reach above avails
    for months in (1, 3):
        est = estimate("90210", months=months, config=cfg())
        assert est.est_uniques == pytest.approx(100.0) and est.est_uniques <= est.imps_mid / months
    fake.series["s"] = ref(unit="weekly", basis="modeled", levels=("dma", "zip"))
    weekly = estimate("90210", months=1, config=cfg(), flight_days=14)
    assert weekly.weeks == pytest.approx(2.0) and weekly.est_uniques == pytest.approx(weekly.imps_mid / 2.0)


def test_the_cap_says_so_with_or_without_a_win_rate_and_stays_quiet_at_exactly_one_per_id(fake):
    fake.series["s"] = ref(levels=("dma", "zip"))
    fake.dmas[1] = uniform(1_000_000.0)
    fake.zips[(1, "90210")] = store.Row("90210", 100.0, est_reach=400.0)
    for win in (False, True):
        notes = estimate("90210", config=cfg(win_rate=win)).notes
        assert sum("capped at the impressions" in n for n in notes) == 1, (win, notes)
    fake.zips[(1, "90210")] = store.Row("90210", 400.0, est_reach=400.0)               # exactly one opportunity per ID: nothing to cap
    for months in (1, 3):
        for win in (False, True):
            assert not any("capped" in n for n in estimate("90210", months=months, config=cfg(win_rate=win)).notes), (months, win)


def test_the_state_and_unplaced_notes_still_speak_in_raw_avails(fake):
    """Whole-state scaling happens BEFORE the win rate, so its note quotes the platform's own totals: it must say 'avails', not
    'impressions', beside a headline that is already 15% of them."""
    fake.series["s"] = ref(levels=("dma", "state"))
    fake.dmas[1] = {d.key: store.Row(d.key, 10_000.0, reach_universe=5000.0, est_reach=2000.0) for d in geo.dma_table()}
    from app.services import forecast_store as fs
    fs_states = {(1, "WY"): store.Row("WY", 38_000.0)}
    fake_state_rows = lambda sid, ss: {s: fs_states[(sid, s)] for s in ss if (sid, s) in fs_states}
    from unittest import mock
    with mock.patch.object(fs, "state_rows", fake_state_rows):
        won = estimate("WY", config=cfg(win_rate=True))
    state_note = next(n for n in won.notes if "state total" in n)
    assert "avails against" in state_note and "avails in the markets homed there" in state_note
    assert won.imps_mid == pytest.approx(38_000.0 * 0.15)


# ---------------------------------------------------------------------------------------------------------- Prime Video (Amazon DSP)

def test_prime_video_has_one_win_rate_of_its_own_twenty_percent(monkeypatch):
    monkeypatch.delenv("CONSTANZA_WIN_RATE_PRIME", raising=False)
    assert wr.series_rate("prime") == wr.WinRate(0.20, 0.20, 0.20)                # one figure: no 10-20% range to show
    assert wr.for_series("prime") == wr.WinRate(0.20, 0.20, 0.20)
    assert wr.for_series("hulu") == wr.for_series(None) == wr.configured() == DEFAULT      # every other series keeps the programmatic set
    assert wr.series_rate("roku") is None and wr.series_rate(None) is None


@pytest.mark.parametrize("raw,expected", [("25", 0.25), ("25%", 0.25), ("0.3", 0.3), ("1%", 0.01), ("0.5%", 0.005), ("1", 0.01), ("", 0.20), ("  ", 0.20)])
def test_prime_videos_rate_can_be_set_from_the_environment(monkeypatch, raw, expected):
    monkeypatch.setenv("CONSTANZA_WIN_RATE_PRIME", raw)
    assert wr.for_series("prime") == pytest.approx(wr.WinRate(expected, expected, expected))
    assert wr.for_series("hulu") == DEFAULT                                      # ... without touching the programmatic rates


@pytest.mark.parametrize("raw", ["abc", "0", "-5", "250%", "nan", "inf"])
def test_a_bad_prime_rate_falls_back_to_twenty_percent_and_warns(monkeypatch, caplog, raw):
    monkeypatch.setenv("CONSTANZA_WIN_RATE_PRIME", raw)
    with caplog.at_level(logging.WARNING):
        assert wr.for_series("prime") == wr.WinRate(0.20, 0.20, 0.20)
    assert any("CONSTANZA_WIN_RATE_PRIME" in r.getMessage() for r in caplog.records)


def test_the_programmatic_environment_never_moves_prime_videos_rate(monkeypatch):
    monkeypatch.setenv(wr.ENV_LOW, "5"); monkeypatch.setenv(wr.ENV_MID, "8"); monkeypatch.setenv(wr.ENV_HIGH, "12")
    assert wr.for_series("prime") == wr.WinRate(0.20, 0.20, 0.20) and wr.for_series("hulu") == pytest.approx(wr.WinRate(0.05, 0.08, 0.12))


def test_prime_video_estimates_are_the_workbooks_avails_times_twenty_percent():
    from app.data.constanza import load_platform_dma
    est = engine.estimate_avails(PRIME, resolve_geo("New York"), months=1)
    raw = load_platform_dma()["prime"]["new york"]
    assert est.win_rate is not None and (est.win_rate["low"], est.win_rate["mid"], est.win_rate["high"]) == (0.20, 0.20, 0.20)
    assert est.imps_mid == pytest.approx(raw.imps_mid * 0.20)
    assert (est.imps_low, est.imps_high) == pytest.approx((raw.imps_low * 0.20, raw.imps_high * 0.20))     # the planning band keeps its meaning
    assert est.win_rate["raw_imps_mid"] == pytest.approx(raw.imps_mid)
    assert est.win_rate["delivered_at_low"] == est.win_rate["delivered_at_high"] == pytest.approx(raw.imps_mid * 0.20)   # no sensitivity range
    assert est.est_uniques is not None and est.est_uniques <= est.imps_mid                                # unique IDs never exceed the impressions
    note = est.notes[0]
    assert note.startswith("Win rate applied: Entravision is assumed to win 20% of these modeled avails")
    assert "bid opportunities" not in note and "auctions" not in note and "10%" not in note               # a single rate, and not a pool of bids


def test_prime_video_gets_its_own_rate_whatever_the_product_name_when_forecast_from_its_series():
    geo_ = resolve_geo("Los Angeles")
    config = fm.ForecastConfig(enabled=True, method="platform", platform="prime", hispanic_weighted=False, source="admin", win_rate=True)
    est = engine.estimate_avails(HULU, geo_, months=2, config=config)
    assert est.win_rate["mid"] == 0.20 and est.win_rate["raw_imps_mid"] == pytest.approx(est.imps_mid / 0.20)
    # a programmatic product keeps the programmatic rate (and its range note)
    hulu = engine.estimate_avails(HULU, geo_, months=1, config=fm.ForecastConfig(
        enabled=True, method="platform", platform="hulu", hispanic_weighted=False, source="admin", win_rate=True))
    assert hulu.win_rate["mid"] == 0.15 and "bid opportunities" in hulu.notes[0]
    # ... and a Roku-series product stays untouched
    roku = engine.estimate_avails(ROKU, geo_, months=1)
    assert roku.win_rate is None


def test_the_admin_payload_says_what_rate_each_product_gets(linked):
    payload = fm.admin_payload()
    rows = {r["name"]: r for r in payload["products"]}
    assert rows[PRIME]["win_rate"] is True and rows[PRIME]["win_rate_mid"] == 0.20
    assert rows[HULU]["win_rate_mid"] == 0.15
    assert payload["win_rate_series"] == {"prime": 0.20}
    assert rows[ROKU]["win_rate"] is False and rows[ROKU]["win_rate_mid"] == 0.15      # what it WOULD get if Apply is chosen
    generic = rows["Entravision Plus - Online Video (OLV) Blend Reach"]
    assert generic["method"] == "generic" and generic["win_rate_mid"] is None


def test_the_bundled_prime_figures_use_the_measured_ad_load_of_2_6_minutes_an_hour():
    """The workbook carries 3.2 ad minutes per hour for Prime Video; Constanza plans on Ampere's measured 2.6
    (scripts/extract_constanza_data.PRIME_AD_LOAD). Prime's national impressions are device-hours x ad load x ads per minute x fill from
    the workbook's Assumptions sheet, so with 2.6 they are 4.58B a month (5.64B at 3.2); uniques and households do not depend on it."""
    from app.data.constanza import load_platform_dma
    figs = load_platform_dma()["prime"].values()
    device_hours = 2.15e12 * 0.045 * 0.8 / 1.45 / 60
    assert sum(f.imps_mid for f in figs) == pytest.approx(device_hours * 2.6 * 2.2 * 0.9, rel=1e-9)
    assert sum(f.imps_low for f in figs) == pytest.approx(0.7 * device_hours * 2.6 * 2.2 * 0.9, rel=1e-9)
    assert sum(f.imps_high for f in figs) == pytest.approx(1.3 * device_hours * 2.6 * 2.2 * 0.9, rel=1e-9)
    assert sum(f.unique_viewers for f in figs) == pytest.approx(130_000_000)           # Amazon's U.S. ad-supported reach: untouched
    assert sum(f.unique_hh for f in figs) == pytest.approx(130_000_000 / 2.45)


def test_the_extractor_scales_prime_by_its_ad_load_and_nothing_else():
    import importlib.util
    spec = importlib.util.spec_from_file_location("extract_constanza_data", ROOT / "scripts" / "extract_constanza_data.py")
    ex = importlib.util.module_from_spec(spec); spec.loader.exec_module(ex)

    class Sheet:
        def __init__(self, label, value): self.cells = {(ex.PRIME_AD_LOAD_ROW, 1): label, (ex.PRIME_AD_LOAD_ROW, 2): value}
        def cell(self, row, column):
            return type("C", (), {"value": self.cells.get((row, column))})
    wb = {"Assumptions": Sheet("Ad load — Prime Video (ad minutes per hour)", 3.2)}
    assert ex.prime_ad_load_factor(wb) == pytest.approx(2.6 / 3.2)
    assert ex.prime_ad_load_factor({"Assumptions": Sheet("Ad load — Prime Video (ad minutes per hour)", 2.6)}) == 1.0     # a refreshed workbook that carries 2.6 itself
    with pytest.raises(SystemExit):
        ex.prime_ad_load_factor({"Assumptions": Sheet("Ad load — Hulu (min/hr)", 8.23)})                                  # a reshuffled sheet fails loudly
    dma = {"prime": {"a": {"imps_low": 70.0, "imps_mid": 100.0, "imps_high": 130.0, "unique_viewers": 5, "unique_hh": 2, "reach_pct": 0.4}}, "roku": {"a": {"imps_mid": 9.0}}}
    national = {"Reference: Prime Video (not on list)": {"imps_mid": 100.0}, "Cumulative incl. Prime Video (reference)": {"imps_mid": 1000.0}}
    ex.apply_prime_ad_load(dma, national, 0.5)
    assert dma["prime"]["a"] == {"imps_low": 35.0, "imps_mid": 50.0, "imps_high": 65.0, "unique_viewers": 5, "unique_hh": 2, "reach_pct": 0.4}
    assert dma["roku"]["a"] == {"imps_mid": 9.0}
    assert national["Reference: Prime Video (not on list)"]["imps_mid"] == 50.0 and national["Cumulative incl. Prime Video (reference)"]["imps_mid"] == 950.0
