"""The people-counts of an estimate: the platform's reach universe in the geography narrowed by the same cuts as the impressions (the
ADDRESSABLE POOL), the est. unique IDs a flight reaches (narrowed the same way, never above the impressions), and the census
population of the market that fits the cuts.

The card used to headline the census figure as the "underlying audience": Los Angeles x Entravision+ Connect - Family x women x
auto intenders read 403K impressions beside "an audience of 2.6M", which cannot be right - the product's own platform reaches
488K people in that market. The pool fixed that; the win rate (impressions x 15%) then made even the pool larger than the impressions,
so what a planner reads as the audience is the unique IDs. These tests pin the numbers to one another, offline (the store is faked)."""
from __future__ import annotations

import dataclasses

import pytest

import app.main as main
from app.services import constanza_engine as engine
from app.services import constanza_forecast_map as fm
from app.services import forecast_store as store
from app.services.constanza_audience import AudienceSpec, estimate_audience
from app.services.constanza_geo import resolve_geo
from app.services.forecast_import import geo_reference as geo
from tests.test_forecast_engine_series import ref

FAMILY = "Entravision+ Connect - Family"
LA_AVAILS, LA_UNIVERSE, LA_REACH = 2_584_100.0, 487_600.0, 167_300.0


def _rows(with_universe=True):
    out = {}
    for d in geo.dma_table():
        out[d.key] = store.Row(d.key, 100_000.0, reach_universe=20_000.0 if with_universe else None,
                               est_reach=7_000.0 if with_universe else None)
    out["los-angeles"] = store.Row("los-angeles", LA_AVAILS, reach_universe=LA_UNIVERSE if with_universe else None,
                                   est_reach=LA_REACH if with_universe else None)
    return out


def _install(monkeypatch, with_universe=True):
    series = {"connect-family": ref("connect-family", 7001, title=FAMILY)}
    links = {FAMILY: ("connect-family", "primary", None)}
    rows = {7001: _rows(with_universe)}
    monkeypatch.setattr(store, "_fetch_catalog", lambda: (series, links))
    monkeypatch.setattr(store, "dma_table", lambda sid: rows.get(sid, {}))
    monkeypatch.setattr(store, "zip_rows", lambda sid, zs: {})
    monkeypatch.setattr(store, "cd_rows", lambda sid, ks: {})
    monkeypatch.setattr(store, "unknown_share", lambda sid: {})
    store.invalidate()
    fm.invalidate()


@pytest.fixture
def with_universe(monkeypatch):
    _install(monkeypatch, True)
    yield
    store.invalidate()
    fm.invalidate()


@pytest.fixture
def without_universe(monkeypatch):
    _install(monkeypatch, False)
    yield
    store.invalidate()
    fm.invalidate()


def base_estimate(text="Los Angeles", months=1, win_rate=False):
    """The engine's estimate for the Connect-Family product. `win_rate=False` (the default here) is the platform's raw avails — these
    tests pin the people-count arithmetic itself; the same example WITH the programmatic win rate is pinned at the bottom."""
    res = resolve_geo(text)
    assert res.resolved, text
    cfg = dataclasses.replace(fm.resolve_forecast(FAMILY), win_rate=win_rate)
    est = engine.estimate_avails(FAMILY, res, months=months, config=cfg)
    assert est is not None
    return est


# --------------------------------------------------------------------------------------------- the engine carries the universe

def test_the_estimate_carries_the_platforms_reach_universe_beside_its_avails(with_universe):
    est = base_estimate()
    assert est.reach_universe == LA_UNIVERSE and est.imps_mid == LA_AVAILS and est.est_uniques == LA_REACH
    assert [a.reach_universe for a in est.by_area] == [LA_UNIVERSE]


def test_people_are_not_multiplied_by_the_months_of_the_flight(with_universe):
    one, three = base_estimate(months=1), base_estimate(months=3)
    assert three.imps_mid == 3 * one.imps_mid
    assert three.reach_universe == one.reach_universe == LA_UNIVERSE and three.est_uniques == one.est_uniques


def test_a_part_of_a_market_gets_its_share_of_the_universe_like_its_impressions(with_universe):
    est = base_estimate("Los Angeles County, CA")
    share = est.imps_mid / LA_AVAILS
    assert 0 < share < 1
    assert est.reach_universe == pytest.approx(LA_UNIVERSE * share)


def test_a_series_without_a_universe_has_none(without_universe):
    est = base_estimate()
    assert est.reach_universe is None and est.by_area[0].reach_universe is None


# --------------------------------------------------------------------------------------------- the audience layer narrows it with the impressions

WOMEN_AUTO = AudienceSpec(gender="female", interest_terms=["Auto Intenders"])


def test_the_audience_is_the_reach_universe_narrowed_by_the_same_cuts_as_the_impressions(with_universe):
    base = base_estimate()
    aud = estimate_audience(base, WOMEN_AUTO)
    cut = aud.imps_mid / base.imps_mid
    assert 0 < cut < 0.5
    assert aud.audience_people == pytest.approx(LA_UNIVERSE * cut)
    assert aud.est_uniques == pytest.approx(LA_REACH * cut)
    # the card's three people-counts always agree: impressions >= people reached >= uniques
    assert aud.imps_mid >= aud.audience_people >= aud.est_uniques > 0


def test_the_audience_never_exceeds_the_impressions_it_sits_beside_for_the_reported_example(with_universe):
    """The reported case: 403K impressions beside a 2.6M 'audience'. The audience is now tens of thousands of people."""
    aud = estimate_audience(base_estimate(), WOMEN_AUTO)
    assert aud.imps_mid == pytest.approx(402_887, rel=0.01)
    assert aud.audience_people == pytest.approx(76_000, rel=0.02)
    assert aud.audience_people < aud.imps_mid and aud.underlying_population > aud.audience_people   # the census figure is context


def test_impressions_per_person_do_not_change_with_the_cuts_or_the_months(with_universe):
    a_geo = estimate_audience(base_estimate(), AudienceSpec())
    a_cut = estimate_audience(base_estimate(), WOMEN_AUTO)
    a_3mo = estimate_audience(base_estimate(months=3), WOMEN_AUTO)
    expected = LA_AVAILS / LA_UNIVERSE
    assert a_geo.impressions_per_person == pytest.approx(expected) and a_cut.impressions_per_person == pytest.approx(expected)
    assert a_3mo.impressions_per_person == pytest.approx(expected)      # monthly impressions per person


def test_with_no_cuts_the_audience_is_the_whole_universe(with_universe):
    aud = estimate_audience(base_estimate(), AudienceSpec())
    assert aud.cuts_applied == 0 and aud.audience_people == LA_UNIVERSE and aud.est_uniques == LA_REACH


def test_each_area_of_a_list_is_narrowed_and_the_audience_is_their_sum(with_universe):
    base = base_estimate("Los Angeles; Chicago")
    aud = estimate_audience(base, AudienceSpec(gender="male", age_min=25, age_max=44))
    assert len(aud.by_area) == 2 and all(a.audience_people for a in aud.by_area)
    assert sum(a.audience_people for a in aud.by_area) == pytest.approx(aud.audience_people)
    assert sum(a.unique_viewers for a in aud.by_area) == pytest.approx(aud.est_uniques)


def test_without_a_universe_there_is_no_audience_count_and_the_market_population_stands_alone(without_universe):
    aud = estimate_audience(base_estimate(), WOMEN_AUTO)
    assert aud.audience_people is None and aud.est_uniques is None and aud.impressions_per_person is None
    assert aud.underlying_population > 0 and aud.market_population == aud.underlying_population


# --------------------------------------------------------------------------------------------- the census figure counts adults when an interest is applied

def test_an_interest_rate_is_applied_to_adults_not_to_everyone_including_children(with_universe):
    """'31% of adults are auto intenders' x the market's ADULT women, not x all women: children are not auto intenders."""
    base = base_estimate()
    area = base.by_area[0]
    aud = estimate_audience(base, WOMEN_AUTO)
    all_women = area.area_population * 0.503 * 0.31          # the old figure: every woman and girl
    assert aud.underlying_population < all_women * 0.9
    assert aud.underlying_population > all_women * 0.6       # adults are roughly three quarters of the population


def test_without_an_interest_the_census_population_is_unchanged(with_universe):
    base = base_estimate()
    aud = estimate_audience(base, AudienceSpec(gender="female"))
    assert aud.underlying_population == pytest.approx(base.by_area[0].area_population * 0.503, rel=0.02)


def test_the_reliability_floor_still_uses_the_census_population(with_universe):
    aud = estimate_audience(base_estimate("90210"), AudienceSpec(age_min=85, age_max=85, gender="female", interest_terms=["Auto Intenders"]))
    assert aud.underlying_population < aud.reliability_floor_population and aud.below_reliability_threshold
    assert any("market population" in n and "reliability floor" in n for n in aud.notes)


# --------------------------------------------------------------------------------------------- the API says it

def estimate_body(**kw):
    base = dict(product_name=FAMILY, geo_text="Los Angeles", months=1)
    base.update(kw)
    return main.ConstanzaEstimateRequest(**base)


def test_the_estimate_payload_has_the_universe_and_the_audience_counts(with_universe):
    plain = main.constanza_estimate(estimate_body())
    assert plain["reach_universe"] == LA_UNIVERSE and plain["by_area"][0]["reach_universe"] == LA_UNIVERSE
    # Connect - Family is bought through MadHive: the endpoint's impressions are the avails x the 15% win rate (the raw figure is kept)
    assert plain["win_rate"]["mid"] == 0.15 and plain["win_rate"]["raw_imps_mid"] == LA_AVAILS and plain["imps_mid"] == pytest.approx(LA_AVAILS * 0.15)
    out = main.constanza_estimate(estimate_body(audience=main.ConstanzaAudienceSpecModel(gender="female", interest_terms=["Auto Intenders"])))
    a = out["audience"]
    reached = 1 - 0.85 ** (LA_AVAILS / LA_REACH)                                   # share of the platform's reach that wins an impression
    assert a["audience_people"] == pytest.approx(76_000, rel=0.02) and a["est_uniques"] == pytest.approx(26_000 * reached, rel=0.02)
    assert a["impressions_per_person"] == pytest.approx(5.3 * 0.15, abs=0.05)
    assert a["market_population"] == a["underlying_population"] > a["audience_people"]
    assert a["by_area"][0]["audience_people"] == pytest.approx(a["audience_people"])
    assert a["est_uniques"] < a["imps_mid"]                                         # unique IDs never above the impressions beside them


def test_the_payload_says_null_when_the_data_has_no_universe(without_universe):
    out = main.constanza_estimate(estimate_body(audience=main.ConstanzaAudienceSpecModel(gender="female")))
    assert out["reach_universe"] is None and out["audience"]["audience_people"] is None and out["audience"]["est_uniques"] is None


# --------------------------------------------------------------------------------------------- the review's findings

def test_the_market_population_says_whether_it_counts_adults(with_universe):
    base = base_estimate()
    with_interest = estimate_audience(base, WOMEN_AUTO)
    without_interest = estimate_audience(base, AudienceSpec(gender="female"))
    assert with_interest.market_population_adults is True and without_interest.market_population_adults is False


def test_a_country_with_no_age_data_is_not_called_adults():
    """Mexico has no ACS age bands, so the interest rate is applied to everyone: the card must not say 'adults'."""
    res = resolve_geo("Mexico")
    est = engine.estimate_avails(FAMILY, res, months=1, national_monthly_total=1_000_000)
    aud = estimate_audience(est, AudienceSpec(interest_terms=["Auto Intenders"]))
    assert aud.underlying_population > 0 and aud.market_population_adults is False


def test_the_payload_carries_the_adults_flag(with_universe):
    out = main.constanza_estimate(estimate_body(audience=main.ConstanzaAudienceSpecModel(gender="female", interest_terms=["Auto Intenders"])))
    assert out["audience"]["market_population_adults"] is True
    plain = main.constanza_estimate(estimate_body(audience=main.ConstanzaAudienceSpecModel(gender="female")))
    assert plain["audience"]["market_population_adults"] is False


def test_a_reach_universe_larger_than_the_census_population_is_explained_not_called_smaller(monkeypatch):
    """A display platform counts devices or IDs: eDigital Display's universe in Dallas is 8.4M against 6.4M census adults. The note says why."""
    _install(monkeypatch, True)
    rows = store.dma_table(7001)
    rows["los-angeles"] = store.Row("los-angeles", LA_AVAILS, reach_universe=60_000_000.0, est_reach=LA_REACH)
    monkeypatch.setattr(store, "dma_table", lambda sid: rows)
    try:
        aud = estimate_audience(base_estimate(), WOMEN_AUTO)
        assert aud.audience_people > aud.underlying_population
        assert any("larger than the census population" in n and "devices or IDs" in n for n in aud.notes)
    finally:
        store.invalidate()
        fm.invalidate()


def test_the_census_note_is_absent_when_the_universe_is_smaller(with_universe):
    aud = estimate_audience(base_estimate(), WOMEN_AUTO)
    assert not any("larger than the census population" in n for n in aud.notes)


def test_an_age_range_with_no_adults_and_an_interest_rate_of_adults_says_so(with_universe):
    aud = estimate_audience(base_estimate(), AudienceSpec(age_min=16, age_max=17, interest_terms=["Auto Intenders"]))
    assert aud.underlying_population == 0 and any("this age range has none" in n for n in aud.notes)
    assert not any("larger than the census population" in n for n in aud.notes)


# --------------------------------------------------------------------------------------------- the same example once the win rate applies

def test_with_the_win_rate_the_reported_example_keeps_unique_ids_below_the_impressions(with_universe):
    """The win rate cuts the impressions to 15% of the avails (403K -> 60K) while the platform's reach universe (76K people in the
    market that fit the cuts) is a property of the market. That pool is now LARGER than the impressions, so it must not be offered as
    'the audience': what the impressions reach is the unique IDs, re-derived for the smaller delivery, and it stays below them."""
    base = base_estimate(win_rate=True)
    aud = estimate_audience(base, WOMEN_AUTO)
    assert aud.imps_mid == pytest.approx(402_887 * 0.15, rel=0.01)
    assert aud.audience_people == pytest.approx(76_000, rel=0.02) and aud.audience_people > aud.imps_mid       # the pool exceeds delivery
    reached = LA_REACH * (1 - 0.85 ** (LA_AVAILS / LA_REACH))
    cut = aud.imps_mid / base.imps_mid
    assert aud.est_uniques == pytest.approx(reached * cut)
    assert 0 < aud.est_uniques < aud.imps_mid                                    # ... but the people REACHED are below the impressions
    assert aud.imps_mid / aud.est_uniques == pytest.approx(0.15 * (LA_AVAILS / LA_REACH) / (1 - 0.85 ** (LA_AVAILS / LA_REACH)))   # about 2.5 each


def test_the_win_rate_leaves_the_pool_and_its_ratio_to_the_unfiltered_avails_alone(with_universe):
    raw, won = base_estimate(), base_estimate(win_rate=True)
    assert won.reach_universe == raw.reach_universe == LA_UNIVERSE
    a_raw, a_won = estimate_audience(raw, WOMEN_AUTO), estimate_audience(won, WOMEN_AUTO)
    assert a_won.audience_people == pytest.approx(a_raw.audience_people)
    assert a_won.imps_mid == pytest.approx(a_raw.imps_mid * 0.15)
