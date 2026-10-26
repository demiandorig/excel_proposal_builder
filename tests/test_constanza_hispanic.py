"""Constanza's Hispanic targeting layer (app/services/constanza_hispanic.py + the cut in constanza_audience.py), offline.

Most Entravision campaigns are Hispanic-targeted, and the platform series describe everyone a platform reaches in a market, so a
Hispanic cut multiplies each area's avails by its REAL Hispanic share (Census ACS, table B01001I), jointly with an age / gender cut from
the same table. What is pinned here: the bundled data against the age x sex bundles and known markets; which row an area gets (its own,
its parent DMA's when it is too small, the state proxy, none); the joint arithmetic (a Hispanic 18-34 audience is NOT the Hispanic share
times the 18-34 share); a series that is already Hispanic-targeted is not cut twice; "Hispanic" typed as an interest is the cut; and the
estimate API's defaults and choices."""
from __future__ import annotations

import dataclasses
from types import SimpleNamespace

import pytest

import app.main as main
from app.data.constanza import load_hispanic_pct_by_state, read_json_maybe_gz
from app.services import constanza_audience as aud
from app.services import constanza_engine as engine
from app.services import constanza_forecast_map as fm
from app.services import constanza_hispanic as hisp
from app.services import forecast_store as store
from app.services.constanza_geo import ResolvedArea, resolve_geo
from app.services.forecast_import import geo_reference as geo
from tests.test_forecast_engine_series import cfg as series_cfg, fake, ref, uniform_dmas  # noqa: F401  (the fake series store)

ROOT_DATA = hisp._DATA_DIR
ROOT = ROOT_DATA.parents[2]
REACH = "Entravision Plus - CTV/OTT Reach"
HISPANICS = "Entravision Plus - Hispanics CTV/OTT"
FAMILY = "Entravision+ Connect - Family"
HISPANIC_FAMILY = "Entravision+ Hispanic Connect - Family"


def base(text, product=REACH, months=1):
    res = resolve_geo(text)
    assert res.resolved, text
    est = engine.estimate_avails(product, res, months=months, config=dataclasses.replace(fm.resolve_forecast(product), win_rate=False))
    assert est is not None
    return est


def area(**kw):
    defaults = dict(dma=None, state=None, source_type="zip", geo_id=None, area_population=1000.0)
    defaults.update(kw)
    return SimpleNamespace(**defaults)


# ------------------------------------------------------------------------------------------------------------------ the data

@pytest.mark.parametrize("name,old", [("county", "acs_age_sex_county.json"), ("zcta", "acs_age_sex_zcta.json"), ("cd", "acs_age_sex_cd.json"),
                                      ("place", "acs_age_sex_place.json"), ("dma", "acs_age_sex_dma.json"), ("state", "acs_age_sex_state.json")])
def test_every_hispanic_bundle_covers_exactly_the_geographies_of_the_age_by_sex_bundle(name, old):
    h = read_json_maybe_gz(ROOT_DATA / f"acs_hispanic_{name}.json")
    a = read_json_maybe_gz(ROOT_DATA / old)
    assert set(h) - {"_meta"} == set(a) - {"_meta"}
    keys = [k for k in a if k != "_meta"]                                            # every geography, not a sample
    assert all(abs(h[k][0] - a[k]["total_population"]) <= 1.5 for k in keys)        # the same ACS vintage and total
    assert h["_meta"]["vintage"] == a["_meta"]["vintage"]
    assert len(h["_meta"]["bands"]) == 14 and all(len(h[k]) == 2 + 28 for k in keys)


def test_the_age_bands_add_up_to_the_hispanic_total():
    counties = read_json_maybe_gz(ROOT_DATA / "acs_hispanic_county.json")
    for fips, row in counties.items():
        if fips != "_meta":
            assert sum(row[2:]) == pytest.approx(row[1], abs=max(3, 0.01 * row[1])), fips


@pytest.mark.parametrize("dma,low,high", [
    ("Laredo", 0.90, 0.99), ("Los Angeles", 0.44, 0.50), ("New York", 0.22, 0.27), ("Miami-Ft. Lauderdale", 0.50, 0.56),
    ("Houston", 0.34, 0.40), ("Chicago", 0.20, 0.26)])
def test_known_markets_have_their_known_hispanic_shares(dma, low, high):
    frac, level = hisp.fraction(area(source_type="dma", geo_id=dma, dma=dma))
    assert level == "area" and low <= frac <= high


def test_counties_and_the_nation_look_right():
    counties = read_json_maybe_gz(ROOT_DATA / "acs_hispanic_county.json")
    la, miami, starr = counties["06037"], counties["12086"], counties["48427"]
    assert 0.47 <= la[1] / la[0] <= 0.50 and 0.67 <= miami[1] / miami[0] <= 0.71 and starr[1] / starr[0] > 0.95
    states = read_json_maybe_gz(ROOT_DATA / "acs_hispanic_state.json")
    total = sum(v[0] for k, v in states.items() if k != "_meta")
    assert 0.18 <= sum(v[1] for k, v in states.items() if k != "_meta") / total <= 0.21        # ~19-20% of the U.S.


# ------------------------------------------------------------------------------------------------------------------ which row an area gets

def test_an_area_uses_its_own_row_when_it_is_big_enough():
    entry, level = hisp.entry_for(area(source_type="county", geo_id="06037", dma="Los Angeles"))
    assert level == "area" and entry.total_population > 9_000_000


def test_a_tiny_area_borrows_its_parent_dmas_share():
    zcta = read_json_maybe_gz(ROOT_DATA / "acs_hispanic_zcta.json")
    tiny = next(k for k, v in zcta.items() if k != "_meta" and 0 < v[0] < hisp.MIN_AREA_POPULATION)
    entry, level = hisp.entry_for(area(source_type="zip", geo_id=tiny, dma="Los Angeles"))
    assert level == "dma" and entry is hisp._bundle("dma")["Los Angeles"]
    assert 0.44 <= hisp.fraction(area(source_type="zip", geo_id=tiny, dma="Los Angeles"))[0] <= 0.50


def test_with_no_row_of_its_own_or_its_markets_the_states_row_stands_in():
    a = area(source_type="dma", geo_id="Nowhere", dma="Nowhere", state="TX")
    entry, level = hisp.entry_for(a)
    assert level == "state" and entry is hisp._bundle("state")["TX"]
    assert hisp.fraction(a) == (entry.fraction, "state") and 0.38 <= entry.fraction <= 0.41
    nothing = area(source_type="country", geo_id="MX", dma=None, state=None)
    assert hisp.fraction(nothing) == (None, None) and hisp.hispanic_population(nothing) is None


def test_the_old_state_proxy_is_only_the_last_resort_when_the_state_bundle_lacks_the_state(monkeypatch):
    real = hisp._bundle
    monkeypatch.setattr(hisp, "_bundle", lambda name: {} if name == "state" else real(name))
    a = area(source_type="dma", geo_id="Nowhere", dma="Nowhere", state="TX")
    frac, level = hisp.fraction(a)
    assert level == "state_proxy" and frac == load_hispanic_pct_by_state()["TX"] and hisp.entry_for(a) == (None, None)


def test_a_missing_bundle_degrades_to_the_proxy_instead_of_failing(monkeypatch):
    monkeypatch.setattr(hisp, "read_json_maybe_gz", lambda path: (_ for _ in ()).throw(FileNotFoundError(path)))
    hisp._bundle.cache_clear()
    try:
        frac, level = hisp.fraction(area(source_type="county", geo_id="06037", dma="Los Angeles", state="CA"))
        assert level == "state_proxy" and frac == load_hispanic_pct_by_state()["CA"]
    finally:
        hisp._bundle.cache_clear()


def test_the_hispanic_population_of_an_area_is_its_population_times_its_share():
    a = area(source_type="dma", geo_id="Los Angeles", dma="Los Angeles", area_population=1_000_000.0)
    assert hisp.hispanic_population(a) == pytest.approx(1_000_000 * hisp.fraction(a)[0])


# ------------------------------------------------------------------------------------------------------------------ the arithmetic

def entry(hispanic=1000.0, total=4000.0):
    bands = tuple(hisp._bundle("dma")["Los Angeles"].bands)
    per = hispanic / (2 * len(bands))
    return hisp.HispanicEntry(total, hispanic, tuple([per] * len(bands)), tuple([per] * len(bands)), bands)


def test_the_share_with_no_cut_is_the_hispanic_fraction():
    e = entry()
    assert e.share(None, None, None) == pytest.approx(0.25) == e.fraction
    assert e.share(None, None, None, conditional=True) == pytest.approx(1.0)


def test_a_partly_covered_band_counts_its_overlap_and_gender_splits_it():
    e = entry()                                                       # 28 equal cells: 35.71 each
    per = 1000.0 / 28
    assert e.matching(35, 44, None) == pytest.approx(2 * per)                 # the whole 35-44 band, both sexes
    assert e.matching(35, 39, None) == pytest.approx(2 * per * 5 / 10)        # half of it
    assert e.matching(35, 44, "male") == pytest.approx(per)
    assert e.matching(18, 18, "female") == pytest.approx(per * 1 / 2)         # one of the 18-19 band's two years
    assert e.matching(85, None, None) == pytest.approx(2 * per)                # an open-ended band
    assert e.matching(0, 17, None, adults_only=True) == 0.0                    # adults only: nobody under 18
    assert e.matching(0, 200, None, adults_only=True) == pytest.approx(20 * per)                # 5 adult bands... all but the four under-18 ones


def test_a_hispanic_audience_aged_18_to_34_is_not_the_product_of_two_shares():
    """LA: the Hispanic population is younger, so Hispanic 18-34 over everyone beats Hispanic share x the 18-34 share."""
    a = area(source_type="dma", geo_id="Los Angeles", dma="Los Angeles")
    e, _ = hisp.entry_for(a)
    general_1834, applied = aud._age_gender_share(a, 18, 34, None)
    assert applied and 0.2 < general_1834 < 0.3
    assert e.share(18, 34, None) > e.fraction * general_1834 * 1.05


# ------------------------------------------------------------------------------------------------------------------ the cut in the audience layer

def test_the_cut_multiplies_each_markets_avails_by_its_real_hispanic_share():
    for text, lo, hi in (("Laredo", 0.90, 0.99), ("Los Angeles", 0.44, 0.50), ("New York", 0.22, 0.27)):
        est = base(text)
        out = aud.estimate_audience(est, aud.AudienceSpec(hispanic=True))
        assert lo <= out.imps_mid / est.imps_mid <= hi, text
        assert out.hispanic["mode"] == "cut" and out.hispanic["share"] == pytest.approx(out.imps_mid / est.imps_mid, rel=1e-6)
        assert out.cuts_applied == 1 and out.confidence_tier == "audience_modeled_single_cut"
        assert out.audience_people is None or out.audience_people == pytest.approx((est.reach_universe or 0) * out.imps_mid / est.imps_mid)


def test_a_state_is_cut_market_by_market_not_by_one_percentage():
    est = base("Texas")
    out = aud.estimate_audience(est, aud.AudienceSpec(hispanic=True))
    by_dma = {a.area_label: a.imps_mid / b.imps_mid for a, b in zip(out.by_area, est.by_area)}
    assert max(by_dma.values()) > 0.85 and by_dma["Dallas-Ft. Worth"] < 0.35 and len(set(round(v, 2) for v in by_dma.values())) > 5
    assert out.imps_mid == pytest.approx(sum(a.imps_mid for a in out.by_area))
    assert sum(a.weight for a in out.by_area) == pytest.approx(1.0)                 # the shares are now of the Hispanic audience
    top = max(out.by_area, key=lambda a: a.weight)
    assert top.area_label in ("Houston", "San Antonio", "Dallas-Ft. Worth", "McAllen-Brownsville-Harlingen", "Harlingen-Wslco-Brnsvl")


def test_a_zip_a_county_and_a_city_use_their_own_rows():
    for text, lo, hi in (("Los Angeles County, CA", 0.47, 0.50), ("90210", 0.04, 0.09), ("Sacramento, CA", 0.28, 0.32)):
        est = base(text)
        out = aud.estimate_audience(est, aud.AudienceSpec(hispanic=True))
        assert lo <= out.imps_mid / est.imps_mid <= hi, text


def test_age_gender_and_hispanic_are_one_joint_cut():
    est = base("Los Angeles")
    plain = aud.estimate_audience(est, aud.AudienceSpec(hispanic=True))
    young = aud.estimate_audience(est, aud.AudienceSpec(hispanic=True, age_min=18, age_max=34))
    men = aud.estimate_audience(est, aud.AudienceSpec(hispanic=True, age_min=18, age_max=34, gender="male"))
    assert men.imps_mid < young.imps_mid < plain.imps_mid
    assert men.imps_mid / young.imps_mid == pytest.approx(0.5, abs=0.05)
    assert young.cuts_applied == 1 and young.confidence_tier == "audience_modeled_single_cut"          # one joint demographic cut
    assert young.underlying_population < plain.underlying_population                                     # the Hispanic people who fit
    only_age = aud.estimate_audience(est, aud.AudienceSpec(age_min=18, age_max=34))
    assert young.imps_mid < only_age.imps_mid and plain.hispanic["levels"]["area"] == 1


def test_the_band_is_wider_with_the_hispanic_cut_and_wider_still_from_the_proxy():
    est = base("Los Angeles")
    plain = aud.estimate_audience(est, aud.AudienceSpec())
    cut = aud.estimate_audience(est, aud.AudienceSpec(hispanic=True))
    proxy = aud.estimate_audience(base("Anchorage"), aud.AudienceSpec(hispanic=True))
    width = lambda o: (o.imps_high - o.imps_low) / o.imps_mid           # noqa: E731
    assert width(cut) > width(plain)
    assert proxy.hispanic["levels"]["state"] == 1 and width(proxy) > width(cut)            # Alaska's markets use their state's row: a wider band
    assert any("the state's Hispanic share and age / gender mix stood in" in n for n in proxy.notes)


def test_the_engines_share_column_note_is_not_repeated_beside_the_cuts_own():
    est = engine.estimate_avails(HISPANICS, resolve_geo("Los Angeles"), months=1)
    assert any("share of the geography's Hispanic population" in n for n in est.notes)              # the base estimate explains its share column
    out = aud.estimate_audience(est, aud.AudienceSpec(hispanic=True))
    assert not any("The by-area share column" in n for n in out.notes) and any(n.startswith("Hispanic targeting:") for n in out.notes)


def test_the_note_says_what_the_cut_did_and_what_it_assumed():
    est = base("Los Angeles")
    notes = aud.estimate_audience(est, aud.AudienceSpec(hispanic=True)).notes
    text = " ".join(n for n in notes if n.startswith("Hispanic targeting"))
    assert "47% of the people here are Hispanic" in text and "Census ACS 2023 5-year" in text
    assert "in proportion to their population" in text and "segment size" in text


def test_a_tiny_area_is_cut_by_its_parent_dmas_share_and_the_note_says_so():
    zcta = read_json_maybe_gz(ROOT_DATA / "acs_hispanic_zcta.json")
    tiny = next(k for k, v in zcta.items() if k != "_meta" and 0 < v[0] < hisp.MIN_AREA_POPULATION and resolve_geo(k).resolved)
    res = resolve_geo(tiny)
    est = engine.estimate_avails(REACH, res, months=1, config=dataclasses.replace(fm.resolve_forecast(REACH), win_rate=False))
    out = aud.estimate_audience(est, aud.AudienceSpec(hispanic=True))
    assert out.hispanic["levels"]["dma"] == 1 and any("too small for a reliable Census share" in n for n in out.notes)


def test_an_estimate_with_no_census_data_at_all_says_the_cut_was_not_applied_and_not_that_it_ran():
    est = base("Los Angeles")
    blind = dataclasses.replace(est.by_area[0], source_type="country", geo_id="XX", dma=None, state=None)
    est2 = dataclasses.replace(est, by_area=[blind])
    out = aud.estimate_audience(est2, aud.AudienceSpec(hispanic=True))
    assert out.imps_mid == pytest.approx(est.imps_mid) and out.cuts_applied == 0 and out.hispanic["levels"]["none"] == 1
    assert out.hispanic["mode"] == "not_applied" and out.hispanic["share"] is None
    assert any("could not be applied" in n and "no Census data" in n for n in out.notes)
    assert not any("multiplied by its Hispanic share" in n for n in out.notes)                 # no claim of narrowing that did not happen


def test_one_area_without_data_among_covered_ones_is_left_uncut_and_the_note_says_the_total_reads_high():
    est = base("Los Angeles; New York")
    blind = dataclasses.replace(est.by_area[0], source_type="country", geo_id="XX", dma=None, state=None)
    out = aud.estimate_audience(dataclasses.replace(est, by_area=[blind, est.by_area[1]]), aud.AudienceSpec(hispanic=True))
    assert out.hispanic["mode"] == "cut" and out.cuts_applied == 1
    assert out.by_area[0].imps_mid == pytest.approx(blind.imps_mid) and out.by_area[1].imps_mid < est.by_area[1].imps_mid
    assert any("NOT applied" in n and "reads high" in n for n in out.notes)


# ------------------------------------------------------------------------------------------------------------------ already-Hispanic series

def test_a_series_that_is_already_hispanic_is_not_cut_again():
    est = dataclasses.replace(base("Los Angeles"), series={"key": "hispanic-connect-default", "targeting": "hispanic"})
    out = aud.estimate_audience(est, aud.AudienceSpec(hispanic=True))
    assert out.imps_mid == pytest.approx(est.imps_mid) and out.hispanic["mode"] == "already_targeted" and out.cuts_applied == 0
    assert any("already built from Hispanic audiences" in n for n in out.notes)
    assert not any(n.startswith("Hispanic targeting:") for n in out.notes)


def test_an_age_cut_on_an_already_hispanic_series_reads_the_hispanic_age_structure():
    est = dataclasses.replace(base("Los Angeles"), series={"key": "hispanic-connect-default", "targeting": "hispanic"})
    general = aud.estimate_audience(base("Los Angeles"), aud.AudienceSpec(age_min=18, age_max=34))
    already = aud.estimate_audience(est, aud.AudienceSpec(hispanic=True, age_min=18, age_max=34))
    assert already.imps_mid / est.imps_mid > general.imps_mid / base("Los Angeles").imps_mid     # Hispanic people skew younger than everyone
    assert 0.2 < already.imps_mid / est.imps_mid < 0.5
    assert any("read against the Hispanic population" in n for n in already.notes)
    assert already.cuts_applied == 1                                                           # the age cut counts; the Hispanic one never ran


@pytest.mark.parametrize("targeting,expected", [("hispanic", True), ("hispanic_kids_family", True), ("hispanic_premium_subscription", True),
                                                ("spanish_language", True), ("general", False), ("kids_family", False), ("a18_plus", False),
                                                (None, False), ("", False)])
def test_which_series_are_already_hispanic(targeting, expected):
    assert hisp.series_is_hispanic({"targeting": targeting}) is expected
    assert hisp.series_is_hispanic(None) is False


# ------------------------------------------------------------------------------------------------------------------ interest terms and free text

def test_hispanic_typed_as_an_interest_is_the_cut_not_an_interest():
    assert aud.split_hispanic_terms(["Hispanic", "sports", "Latino", "Hispanic / Latino", "hispanic music"]) == (["sports", "hispanic music"], True)
    assert aud.split_hispanic_terms(["sports"]) == (["sports"], False)
    est = base("Los Angeles")
    out = aud.estimate_audience(est, aud.AudienceSpec(interest_terms=["Hispanic"]))
    assert out.hispanic["mode"] == "cut" and out.interests == [] and not any("No sourced incidence rate" in n for n in out.notes)


@pytest.mark.parametrize("text", ["Hispanic men 25-44 in Houston", "latino households in Sacramento", "Latina women 18-34 Miami", "Latinx adults in Chicago"])
def test_free_text_that_names_the_audience_turns_the_cut_on(text, monkeypatch):
    monkeypatch.setattr(aud, "_openai_client", lambda: None)
    assert aud.parse_audience_text(text).hispanic is True


@pytest.mark.parametrize("text", ["non-Hispanic adults 25-54 in Houston", "General market, not Hispanic, Sacramento", "excluding the Latino audience in Miami",
                                  "Non Hispanic households", "no Hispanics", "Hispanic and non-Hispanic adults", "General market (Hispanic and non-Hispanic)",
                                  "Not targeting Hispanic", "w/o Hispanic", "excl. Hispanic", "exclude all Hispanic and Latino audiences", "avoid Hispanic audiences",
                                  "Hispanic vs non-Hispanic", "not for Hispanic audiences"])
def test_an_audience_to_leave_out_does_not_turn_the_cut_on(text, monkeypatch):
    monkeypatch.setattr(aud, "_openai_client", lambda: None)
    assert aud.parse_audience_text(text).hispanic is False and aud.mentions_hispanic_audience(text) is False


def test_a_negation_does_not_hide_a_real_mention_elsewhere_in_the_text(monkeypatch):
    monkeypatch.setattr(aud, "_openai_client", lambda: None)
    assert aud.parse_audience_text("non-Hispanic white women and Latino men in Dallas").hispanic is True


def test_free_text_without_it_leaves_the_cut_off(monkeypatch):
    monkeypatch.setattr(aud, "_openai_client", lambda: None)
    assert aud.parse_audience_text("men 25-44 in Houston who like sports").hispanic is False
    assert main.constanza_parse_audience(main.ConstanzaParseAudienceRequest(text="Hispanic men in Houston"))["hispanic"] is True
    # an unspecified field is null, NOT an explicit "off" that would override a Hispanic-targeted product's default when fed back to /estimate
    assert main.constanza_parse_audience(main.ConstanzaParseAudienceRequest(text="men 25-44 in Houston"))["hispanic"] is None


# ------------------------------------------------------------------------------------------------------------------ the engine's own shares

def test_a_hispanic_weighted_product_shows_each_areas_share_of_the_hispanic_population():
    res = resolve_geo("Texas")
    est = engine.estimate_avails(HISPANICS, res, months=1)
    pops = {a.dma: hisp.hispanic_population(a) for a in res.areas}
    total = sum(pops.values())
    assert {d.dma: d.weight for d in est.by_area} == pytest.approx({k: v / total for k, v in pops.items()})
    assert any("share of the geography's Hispanic population" in n for n in est.notes)
    general = {d.dma: d.weight for d in engine.estimate_avails(REACH, res, months=1).by_area}
    assert general != {d.dma: d.weight for d in est.by_area}


# ------------------------------------------------------------------------------------------------------------------ the API

def body(product=REACH, **kw):
    return main.ConstanzaEstimateRequest(**{"product_name": product, "geo_text": "Los Angeles", "months": 1, **kw})


def test_a_general_product_is_not_cut_unless_the_planner_asks():
    out = main.constanza_estimate(body())
    assert "audience" not in out and out["hispanic_targeting"] == {"default": False, "on": False, "applies": True}


def test_the_planner_can_turn_the_cut_on_for_any_product():
    plain = main.constanza_estimate(body())
    out = main.constanza_estimate(body(hispanic=True))
    assert out["hispanic_targeting"] == {"default": False, "on": True, "applies": True}
    assert out["audience"]["hispanic"]["mode"] == "cut" and out["audience"]["cuts_applied"] == 1
    assert 0.44 <= out["audience"]["imps_mid"] / plain["imps_mid"] <= 0.50
    assert out["imps_mid"] == plain["imps_mid"]                                  # the base estimate stays the platform's own figure


def test_a_hispanic_targeted_product_has_the_cut_on_by_default_and_can_turn_it_off():
    plain = main.constanza_estimate(body(REACH))
    default_on = main.constanza_estimate(body(HISPANICS))
    assert default_on["hispanic_targeting"] == {"default": True, "on": True, "applies": True} and default_on["audience"]["hispanic"]["mode"] == "cut"
    assert 0.44 <= default_on["audience"]["imps_mid"] / default_on["imps_mid"] <= 0.50
    assert plain["imps_mid"] == pytest.approx(default_on["imps_mid"])             # the same blended pool underneath
    off = main.constanza_estimate(body(HISPANICS, hispanic=False))
    assert off["hispanic_targeting"] == {"default": True, "on": False, "applies": True} and "audience" not in off


def test_the_audience_blocks_own_choice_beats_the_requests():
    out = main.constanza_estimate(body(hispanic=False, audience=main.ConstanzaAudienceSpecModel(hispanic=True, age_min=18, age_max=34)))
    assert out["hispanic_targeting"]["on"] is True and out["audience"]["hispanic"] is not None
    out = main.constanza_estimate(body(hispanic=True, audience=main.ConstanzaAudienceSpecModel(hispanic=False, age_min=18, age_max=34)))
    assert out["hispanic_targeting"]["on"] is False and out["audience"]["hispanic"] is None and out["audience"]["cuts_applied"] == 1


def test_an_audience_without_hispanic_targeting_is_what_it_was():
    out = main.constanza_estimate(body(audience=main.ConstanzaAudienceSpecModel(age_min=18, age_max=34)))
    assert out["audience"]["hispanic"] is None and out["hispanic_targeting"]["on"] is False


def test_a_series_that_is_already_hispanic_defaults_on_and_is_not_cut(monkeypatch):
    series = {"hispanic-connect-family": ref("hispanic-connect-family", 7002, title=HISPANIC_FAMILY, targeting_key="hispanic_kids_family")}
    links = {HISPANIC_FAMILY: ("hispanic-connect-family", "primary", None)}
    rows = {7002: {d.key: store.Row(d.key, 100_000.0) for d in geo.dma_table()}}
    monkeypatch.setattr(store, "_fetch_catalog", lambda: (series, links))
    monkeypatch.setattr(store, "dma_table", lambda sid: rows.get(sid, {}))
    monkeypatch.setattr(store, "zip_rows", lambda sid, zs: {})
    monkeypatch.setattr(store, "cd_rows", lambda sid, ks: {})
    monkeypatch.setattr(store, "unknown_share", lambda sid: {})
    store.invalidate(); fm.invalidate()
    try:
        out = main.constanza_estimate(body(HISPANIC_FAMILY))
        assert out["hispanic_targeting"] == {"default": True, "on": True, "applies": True}
        assert out["audience"]["hispanic"]["mode"] == "already_targeted"
        assert out["audience"]["imps_mid"] == pytest.approx(out["imps_mid"])
    finally:
        store.invalidate(); fm.invalidate()


# ------------------------------------------------------------------------------------------------------------------ review fixes

GENERIC = "Entravision Plus - Online Video (OLV) Blend Reach"


def test_a_population_share_estimate_is_never_cut_it_is_the_planners_own_total():
    plain = main.constanza_estimate(body(GENERIC, national_monthly_total=1_000_000))
    assert plain["platform"] == "generic_population" and plain["imps_mid"] == pytest.approx(1_000_000)
    for kw in ({"hispanic": True}, {"audience": main.ConstanzaAudienceSpecModel(hispanic=True)},
               {"audience": main.ConstanzaAudienceSpecModel(interest_terms=["Hispanic"])}):
        out = main.constanza_estimate(body(GENERIC, national_monthly_total=1_000_000, **kw))
        assert out["hispanic_targeting"] == {"default": False, "on": False, "applies": False}
        assert out.get("audience") is None or out["audience"]["imps_mid"] == pytest.approx(1_000_000)
        assert out["imps_mid"] == pytest.approx(1_000_000)
        assert any("isn't applied to a population-share estimate" in n for n in out["notes"])
    quiet = main.constanza_estimate(body(GENERIC, national_monthly_total=1_000_000))
    assert not any("population-share estimate: it spreads" in n for n in quiet["notes"])          # said only when it was asked for


def test_a_country_is_a_population_share_estimate_so_it_is_not_cut_and_says_why():
    out = main.constanza_estimate(body(REACH, geo_text="Mexico", national_monthly_total=1_000_000, hispanic=True))
    assert out["hispanic_targeting"]["applies"] is False and out["hispanic_targeting"]["on"] is False
    assert out["imps_mid"] == pytest.approx(1_000_000) and any("population-share estimate" in n for n in out["notes"])


def test_turning_the_cut_off_on_a_hispanic_weighted_product_gives_population_weights_and_no_hispanic_note():
    on = main.constanza_estimate(body(HISPANICS, geo_text="Texas"))
    off = main.constanza_estimate(body(HISPANICS, geo_text="Texas", hispanic=False))
    assert off["hispanic_targeting"]["on"] is False and "audience" not in off
    assert not any("Hispanic population" in n for n in off["notes"])
    general = main.constanza_estimate(body(REACH, geo_text="Texas"))
    assert {a["area_label"]: a["weight"] for a in off["by_area"]} == pytest.approx({a["area_label"]: a["weight"] for a in general["by_area"]})
    assert any(n.startswith("Hispanic targeting:") for n in on["audience"]["notes"])


def test_an_already_hispanic_series_cannot_be_turned_off_and_its_age_cut_is_always_read_against_the_hispanic_population(monkeypatch):
    series = {"hispanic-connect-family": ref("hispanic-connect-family", 7002, title=HISPANIC_FAMILY, targeting_key="hispanic_kids_family")}
    links = {HISPANIC_FAMILY: ("hispanic-connect-family", "primary", None)}
    rows = {7002: {d.key: store.Row(d.key, 100_000.0) for d in geo.dma_table()}}
    monkeypatch.setattr(store, "_fetch_catalog", lambda: (series, links))
    monkeypatch.setattr(store, "dma_table", lambda sid: rows.get(sid, {}))
    monkeypatch.setattr(store, "zip_rows", lambda sid, zs: {})
    monkeypatch.setattr(store, "cd_rows", lambda sid, ks: {})
    monkeypatch.setattr(store, "unknown_share", lambda sid: {})
    store.invalidate(); fm.invalidate()
    try:
        age = main.ConstanzaAudienceSpecModel(age_min=18, age_max=34)
        default = main.constanza_estimate(body(HISPANIC_FAMILY, audience=age))
        off = main.constanza_estimate(body(HISPANIC_FAMILY, hispanic=False, audience=age))
        off2 = main.constanza_estimate(body(HISPANIC_FAMILY, audience=main.ConstanzaAudienceSpecModel(age_min=18, age_max=34, hispanic=False)))
        for out in (off, off2):
            assert out["hispanic_targeting"] == {"default": True, "on": True, "applies": True}
            assert out["audience"]["imps_mid"] == pytest.approx(default["audience"]["imps_mid"])
            assert out["audience"]["hispanic"]["mode"] == "already_targeted"
        # catalog and endpoint agree, in a US market and in an international one (which has no series on the estimate)
        cfg = fm.resolve_forecast(HISPANIC_FAMILY)
        assert fm.hispanic_default(cfg) is True and fm.series_is_hispanic_for(cfg) is True
        mexico = main.constanza_estimate(body(HISPANIC_FAMILY, geo_text="Mexico", national_monthly_total=1_000_000))
        assert mexico["hispanic_targeting"]["applies"] is False
    finally:
        store.invalidate(); fm.invalidate()


def test_an_explicit_off_is_not_overridden_by_a_hispanic_interest_term():
    for kw in ({"hispanic": False, "audience": main.ConstanzaAudienceSpecModel(interest_terms=["Hispanic"])},
               {"audience": main.ConstanzaAudienceSpecModel(interest_terms=["Hispanic", "Latino"], hispanic=False)}):
        out = main.constanza_estimate(body(**kw))
        assert out["hispanic_targeting"]["on"] is False and out["audience"]["hispanic"] is None
        assert out["audience"]["imps_mid"] == pytest.approx(out["imps_mid"]) and out["audience"]["interests"] == []
    on = main.constanza_estimate(body(audience=main.ConstanzaAudienceSpecModel(interest_terms=["Hispanic"])))
    assert on["hispanic_targeting"]["on"] is True and on["audience"]["hispanic"]["mode"] == "cut"        # no explicit choice: the term turns it on


def test_the_admin_payload_shows_which_products_are_hispanic_by_default_and_why(monkeypatch):
    payload = fm.admin_payload()
    rows = {r["name"]: r for r in payload["products"]}
    assert rows[HISPANICS]["hispanic_default"] is True and rows[HISPANICS]["hispanic_from_series"] is False and rows[HISPANICS]["hispanic_weighted"] is True
    assert rows[REACH]["hispanic_default"] is False and rows[REACH]["hispanic_from_series"] is False
    assert rows["Tiktok Ads"]["hispanic_default"] is False


# ------------------------------------------------------------------------------------------------------------------ round-2 review fixes

def test_the_alaska_markets_get_a_real_joint_age_cut_from_their_states_row_not_a_dropped_one():
    est = base("Anchorage")
    ak = hisp._bundle("state")["AK"]
    out = aud.estimate_audience(est, aud.AudienceSpec(hispanic=True, age_min=18, age_max=34))
    ratio = out.imps_mid / est.imps_mid
    assert ratio == pytest.approx(ak.share(18, 34, None)) and ratio < 0.04                    # ~2%, not the 6.8% of "Hispanic" alone
    assert out.hispanic["levels"]["state"] == 1 and any("state's Hispanic share and age / gender mix stood in" in n for n in out.notes)
    assert not any("NOT applied" in n for n in out.notes)


def test_the_state_proxy_never_pretends_an_age_cut_it_could_not_read_was_applied(monkeypatch):
    real = hisp._bundle
    monkeypatch.setattr(hisp, "_bundle", lambda name: {} if name == "state" else real(name))
    est = base("Anchorage")
    only = aud.estimate_audience(est, aud.AudienceSpec(hispanic=True))
    assert only.hispanic["levels"]["state_proxy"] == 1 and only.imps_mid / est.imps_mid == pytest.approx(load_hispanic_pct_by_state()["AK"])
    aged = aud.estimate_audience(est, aud.AudienceSpec(hispanic=True, age_min=18, age_max=34))
    assert aged.imps_mid == pytest.approx(est.imps_mid) and aged.hispanic["mode"] == "not_applied"        # nothing narrowed, and it says so
    assert any("could not be applied" in n for n in aged.notes) and not any("jointly with the age" in n for n in aged.notes)


def test_an_already_hispanic_series_is_never_zeroed_by_an_area_with_no_hispanic_residents():
    zcta = read_json_maybe_gz(ROOT_DATA / "acs_hispanic_zcta.json")
    zero = next(k for k, v in zcta.items() if k != "_meta" and v[0] >= 400 and v[1] == 0 and resolve_geo(k).resolved)
    est = dataclasses.replace(base(zero), series={"key": "hispanic-connect-default", "targeting": "hispanic"})
    assert est.imps_mid > 0
    plain = aud.estimate_audience(est, aud.AudienceSpec(hispanic=True))
    assert plain.imps_mid == pytest.approx(est.imps_mid) and plain.est_uniques == est.est_uniques                       # unchanged: nothing to cut
    aged = aud.estimate_audience(est, aud.AudienceSpec(hispanic=True, age_min=18, age_max=34))
    assert 0.2 < aged.imps_mid / est.imps_mid < 0.5                                                                    # the parent DMA's Hispanic age mix, not 0
    assert aged.hispanic["levels"]["dma"] == 1


def test_the_conditional_age_mix_needs_enough_hispanic_people_to_mean_anything():
    big = hisp._bundle("county")["06037"]
    assert hisp._usable(big, conditional=True)
    thin = hisp.HispanicEntry(5000.0, 12.0, (1.0,) * 14, (0.0,) * 14, big.bands)
    assert hisp._usable(thin, conditional=False) and not hisp._usable(thin, conditional=True)


def test_a_whole_state_scaled_to_the_platforms_state_total_is_cut_at_the_states_share_not_the_home_markets(fake):
    """Wyoming's state row is nearly twice what its home markets hold (the rest is served from markets homed elsewhere): that extra volume is not
    the home markets' households, so the Hispanic share of the TOTAL is the state's."""
    fake.series["s"] = ref("s", 1, levels=("dma", "state"))
    fake.dmas[1] = uniform_dmas(1000.0)
    fake.states[(1, "WY")] = store.Row("WY", 3800.0)
    est = engine.estimate_avails("P", resolve_geo("WY"), months=1, config=series_cfg("s"))
    assert est.imps_mid == pytest.approx(3800.0) and all(a.state_scaled for a in est.by_area)
    out = aud.estimate_audience(est, aud.AudienceSpec(hispanic=True))
    wy = hisp._bundle("state")["WY"]
    assert out.imps_mid == pytest.approx(3800.0 * wy.fraction) and out.hispanic["levels"]["state_total"] == 2
    assert out.hispanic["share"] == pytest.approx(wy.fraction)
    assert any("cut at the STATE's Hispanic share, not market by market" in n for n in out.notes)
    # an age cut reads the state's own mix too
    aged = aud.estimate_audience(est, aud.AudienceSpec(hispanic=True, age_min=18, age_max=34))
    assert aged.imps_mid / est.imps_mid == pytest.approx(wy.share(18, 34, None))
    # the same state WITHOUT a state row is the market sum, cut market by market
    del fake.states[(1, "WY")]
    plain = engine.estimate_avails("P", resolve_geo("WY"), months=1, config=series_cfg("s"))
    assert not any(a.state_scaled for a in plain.by_area)
    out2 = aud.estimate_audience(plain, aud.AudienceSpec(hispanic=True))
    assert out2.hispanic["levels"]["area"] == 2 and out2.hispanic["levels"]["state_total"] == 0


def test_a_market_or_a_county_inside_a_state_is_never_state_scaled_even_with_state_rows(fake):
    fake.series["s"] = ref("s", 1, levels=("dma", "state"))
    fake.dmas[1] = uniform_dmas(1000.0)
    fake.states[(1, "WY")] = store.Row("WY", 3800.0)
    for text in ("Casper-Riverton", "Albany County, WY", "New York"):
        est = engine.estimate_avails("P", resolve_geo(text), months=1, config=series_cfg("s"))
        assert not any(a.state_scaled for a in est.by_area), text


@pytest.mark.parametrize("text", ["Hispanic vs. non-Hispanic", "Latino vs. not Latino", "Hispanic versus non-Hispanic"])
def test_a_dotted_vs_still_means_everyone_in_python(text):
    assert aud.mentions_hispanic_audience(text) is False


@pytest.mark.parametrize("term", ["Hispanic & Latino", "Hispanic or Latino", "Latino Hispanic", "Hispanic, Latino", "Hispanic-Latino", "Hispanic Latino",
                                  "hispanic and latino", "Hispanic households", "Latino consumers"])
def test_every_way_of_typing_hispanic_as_an_interest_is_the_cut(term):
    assert aud.split_hispanic_terms([term]) == ([], True)
    out = aud.estimate_audience(base("Los Angeles"), aud.AudienceSpec(interest_terms=[term]))
    assert out.hispanic["mode"] == "cut" and out.interests == [] and not any("No sourced incidence rate" in n for n in out.notes)


@pytest.mark.parametrize("term", ["hispanic music", "Latin Music", "Latin music concerts", "hispanic heritage"])
def test_a_hispanic_flavoured_interest_stays_an_interest(term):
    assert aud.split_hispanic_terms([term]) == ([term], False)


# --- the extractor (scripts/extract_constanza_hispanic.py), loaded by path

def _extractor():
    import importlib.util
    spec = importlib.util.spec_from_file_location("extract_constanza_hispanic", ROOT_DATA.parents[2] / "scripts" / "extract_constanza_hispanic.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_extractors_dma_and_state_aggregation_reproduces_the_shipped_bundles_exactly():
    ex = _extractor()
    county = read_json_maybe_gz(ROOT_DATA / "acs_hispanic_county.json")
    by_dma, by_state = ex._aggregate(county)
    shipped_dma, shipped_state = read_json_maybe_gz(ROOT_DATA / "acs_hispanic_dma.json"), read_json_maybe_gz(ROOT_DATA / "acs_hispanic_state.json")
    assert set(by_dma) == set(shipped_dma) - {"_meta"} and set(by_state) == set(shipped_state) - {"_meta"}
    assert all(by_dma[k] == shipped_dma[k] for k in by_dma) and all(by_state[k] == shipped_state[k] for k in by_state)


def test_the_extractor_reads_the_census_sentinels_as_zero_never_a_negative_population():
    ex = _extractor()
    assert ex._num("-666666666") == 0.0 and ex._num(None) == 0.0 and ex._num("") == 0.0 and ex._num("abc") == 0.0 and ex._num("12") == 12.0
    header = ["state", "county", *ex.VARIABLES]
    row = ["06", "037", "1000", "480", *(["10"] * 28)]
    row[2 + ex.VARIABLES.index("B01001I_005E")] = "-666666666"
    out = ex._rows_to_lists([row], header, ["state", "county"])
    assert out["06037"][:2] == [1000, 480] and min(out["06037"]) == 0 and len(out["06037"]) == 2 + 28


def test_the_extractor_refuses_to_publish_a_run_that_got_almost_nothing():
    ex = _extractor()
    assert ex.SANITY_MINIMUMS == {"counties": 3000, "ZCTAs": 30000, "congressional districts": 430, "places": 30000}
    with pytest.raises(SystemExit):
        ex._check_sizes({"counties": 3222, "ZCTAs": 0, "congressional districts": 440, "places": 32325}, [])
    with pytest.raises(SystemExit):
        ex._check_sizes({"counties": 3222, "ZCTAs": 33772, "congressional districts": 440, "places": 32325}, ["place: state 17 (HTTP 500)"])
    ex._check_sizes({"counties": 3222, "ZCTAs": 33772, "congressional districts": 440, "places": 32325}, [])
