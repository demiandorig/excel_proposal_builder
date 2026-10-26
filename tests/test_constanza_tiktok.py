"""Constanza x TikTok: the validated targeting, the request TikTok's estimator takes, the estimate card's numbers (the lower-end audience x 4.3 / 5.5 / 8.6),
and the API routes - against the fake TikTok API (tests/tiktok_support.py)."""
from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

import app.main as main
from app.services import constanza_forecast_map as fm
from app.services import constanza_tiktok as ct
from app.services import forecast_store as store
from app.services import tiktok_client as tc
from app.services import tiktok_geo as tg
from app.services.constanza_geo import resolve_geo
from tests.tiktok_support import ADVERTISER, GOOD_TOKEN, FakeResponse, FakeTikTok, fail, make_client, timeout_error

TIKTOK = "Tiktok Ads"
ESTIMATE = "/ad/audience_size/estimate/"


@pytest.fixture
def tiktok(monkeypatch):
    """A fake TikTok behind the shared client, and a catalog / forecast map that need no database."""
    for name in ("CONSTANZA_AUDIENCE_X_CONSERVATIVE", "CONSTANZA_AUDIENCE_X_MID", "CONSTANZA_AUDIENCE_X_UPPER", "TIKTOK_OBJECTIVE_TYPE", "TIKTOK_OPTIMIZATION_GOAL"):
        monkeypatch.delenv(name, raising=False)
    client = make_client(FakeTikTok())
    monkeypatch.setattr(tc, "get_client", lambda: client)
    monkeypatch.setattr(store, "_fetch_catalog", lambda: ({}, {}))
    monkeypatch.setattr(fm, "load_overrides", lambda: {})
    store.invalidate()
    fm.invalidate()
    tg.forget_everything()
    yield client
    store.invalidate()
    fm.invalidate()
    tg.forget_everything()


def request(**kw):
    base = dict(product_name=TIKTOK, geo_text="Los Angeles", months=1)
    base.update(kw)
    return main.ConstanzaEstimateRequest(**base)


def targeting(**kw):
    return main.TikTokTargetingModel(**kw)


# --------------------------------------------------------------------------------------------- the targeting a planner can set

def test_an_empty_targeting_is_not_narrowed_and_asks_for_a_reach_campaign_on_tiktok():
    t = ct.Targeting.from_dict(None)
    assert t == ct.Targeting() and t.objective == "reach" and t.placements == (ct.PLACEMENT_TIKTOK,) and not t.interactions
    assert ct.Targeting.from_dict({}) == t and ct.Targeting.from_dict(targeting().model_dump()) == t


def test_a_full_targeting_is_read_as_given_and_duplicates_collapse():
    t = ct.Targeting.from_dict({
        "gender": "Women", "age_groups": ["AGE_25_34", "AGE_18_24", "AGE_25_34"], "languages": ["es", "en", "es"],
        "interest_category_ids": ["10100", "10100", "12000"], "interest_keyword_ids": ["19155001"], "video_interaction_ids": ["1700"],
        "creator_interaction_ids": ["10"], "hashtag_ids": ["21"], "video_period": 7, "spending_power": "HIGH",
        "household_income": ["TOP10", "TOP25_50"], "operating_system": "IOS", "network_types": ["5G", "WIFI"], "placements": ["PLACEMENT_TIKTOK"],
        "objective": "traffic"})
    assert t.gender == "female" and t.age_groups == ("AGE_25_34", "AGE_18_24") and t.languages == ("es", "en")
    assert t.interest_category_ids == ("10100", "12000") and t.video_period == 7 and t.spending_power == "HIGH" and t.interactions


@pytest.mark.parametrize("raw,message", [
    ({"gender": "robot"}, "Gender must be"),
    ({"age_groups": ["AGE_13_17"]}, "Age"),
    ({"age_groups": ["old"]}, "isn't one TikTok offers"),
    ({"languages": ["english please"]}, "isn't a TikTok language code"),
    ({"languages": [f"l{i}" for i in range(25)]}, "isn't a TikTok language code"),
    ({"languages": ["en", "es", "fr", "pt", "zh", "ja", "ko", "de", "it", "nl", "ru", "ar", "hi", "id", "th", "vi", "tr", "pl", "sv", "da", "fi"]}, "at most 20 languages"),
    ({"interest_category_ids": ["abc"]}, "isn't a TikTok ID"),
    ({"interest_category_ids": ["1; DROP TABLE"]}, "isn't a TikTok ID"),
    ({"interest_category_ids": [str(i) for i in range(60)]}, "at most 50"),
    ({"video_period": 3}, "look-back"),
    ({"spending_power": "MEDIUM"}, "Spending power"),
    ({"operating_system": "WINDOWS"}, "iOS or Android"),
    ({"household_income": ["TOP1"]}, "isn't one TikTok offers"),
    ({"network_types": ["6G"]}, "isn't one TikTok offers"),
    ({"placements": ["PLACEMENT_FACEBOOK"]}, "isn't one TikTok offers"),
    ({"objective": "brand"}, "Objective must be"),
    ({"video_interaction_ids": ["1700"], "placements": ["PLACEMENT_TIKTOK", "PLACEMENT_PANGLE"]}, "only when TikTok is the only placement"),
    ({"household_income": ["TOP5"], "placements": ["PLACEMENT_PANGLE"]}, "TikTok has to be one of the placements"),
    ({"placements": ["PLACEMENT_PANGLE"]}, "TikTok has to be one of the placements"),
    ({"purchase_intention_ids": ["26100"], "objective": "video_views"}, "isn't available for a Video views campaign"),
    ({"interest_category_ids": ["１２３４５"]}, "isn't a TikTok ID"),
    ({"interest_category_ids": "12345"}, "must be a list"),
    ({"age_groups": "AGE_18_24"}, "must be a list"),
    ({"languages": "en"}, "must be a list"),
    ({"interest_category_ids": [str(i) for i in range(40_000)]}, "at most 50"),
    ({"purchase_intention_ids": ["26100"], "interest_keyword_ids": ["19155001"], "objective": "traffic"}, "can't be used together"),
    ({"purchase_intention_ids": ["26100"]}, "isn't available for a Reach campaign"),
])
def test_a_targeting_tiktok_would_refuse_is_refused_here_in_plain_words(raw, message):
    with pytest.raises(ValueError, match=message):
        ct.Targeting.from_dict(raw)


def test_purchase_intent_is_allowed_for_a_traffic_campaign():
    assert ct.Targeting.from_dict({"purchase_intention_ids": ["26100"], "objective": "traffic"}).purchase_intention_ids == ("26100",)


# --------------------------------------------------------------------------------------------- the request TikTok takes

def loc(*ids, zips=()):
    m = tg.Mapping()
    m.locations = [tg.Location("x", "X", i, "DMA", "location_id") for i in ids] + [tg.Location("z", "Z", z, "ZIP_CODE", "zipcode_id") for z in zips]
    return m


def test_a_geography_alone_is_a_reach_request_on_the_tiktok_placement():
    body = ct.build_estimate_body(ct.Targeting(), loc("800002"))
    assert body == {"objective_type": "REACH", "optimization_goal": "REACH", "placement_type": "PLACEMENT_TYPE_NORMAL",
                    "placements": ["PLACEMENT_TIKTOK"], "location_ids": ["800002"]}


def test_every_setting_goes_to_the_field_tiktok_documents_for_it():
    t = ct.Targeting.from_dict({
        "gender": "female", "age_groups": ["AGE_35_44", "AGE_18_24"], "languages": ["es"], "interest_category_ids": ["12100"],
        "interest_keyword_ids": ["19156001"], "video_interaction_ids": ["1700"], "video_period": 7, "creator_interaction_ids": ["10"],
        "hashtag_ids": ["21"], "spending_power": "HIGH", "household_income": ["TOP10"], "operating_system": "ANDROID", "network_types": ["5G"]})
    body = ct.build_estimate_body(t, loc("800002", zips=["8840000490210"]))
    assert body["location_ids"] == ["800002"] and body["zipcode_ids"] == ["8840000490210"]
    assert body["gender"] == "GENDER_FEMALE" and body["age_groups"] == ["AGE_18_24", "AGE_35_44"] and body["languages"] == ["es"]
    assert body["interest_category_ids"] == ["12100"] and body["interest_keyword_ids"] == ["19156001"]
    assert body["spending_power"] == "HIGH" and body["household_income"] == ["TOP10"] and body["operating_systems"] == ["ANDROID"] and body["network_types"] == ["5G"]
    assert body["actions"] == [
        {"action_scene": "VIDEO_RELATED", "action_period": 7, "video_user_actions": ["WATCHED_TO_END", "LIKED", "COMMENTED", "SHARED"], "action_category_ids": ["1700"]},
        {"action_scene": "CREATOR_RELATED", "action_period": 0, "video_user_actions": ["FOLLOWING", "VIEW_HOMEPAGE"], "action_category_ids": ["10"]},
        {"action_scene": "HASHTAG_RELATED", "action_period": 0, "video_user_actions": ["VIEW_HASHTAG"], "action_category_ids": ["21"]},
    ]


def test_purchase_intent_has_its_own_field_and_the_objective_presets_are_the_documented_pairs():
    t = ct.Targeting.from_dict({"purchase_intention_ids": ["26100"], "objective": "traffic"})
    body = ct.build_estimate_body(t, loc("1"))
    assert body["purchase_intention_keyword_ids"] == ["26100"] and (body["objective_type"], body["optimization_goal"]) == ("TRAFFIC", "CLICK")
    video = ct.build_estimate_body(ct.Targeting.from_dict({"objective": "video_views"}), loc("1"))
    assert (video["objective_type"], video["optimization_goal"]) == ("VIDEO_VIEWS", "ENGAGED_VIEW")


def test_nothing_that_was_not_asked_for_is_sent():
    body = ct.build_estimate_body(ct.Targeting.from_dict({"spending_power": "ALL", "gender": "all"}), loc("1"))
    assert set(body) == {"objective_type", "optimization_goal", "placement_type", "placements", "location_ids"}


# --------------------------------------------------------------------------------------------- the numbers

def test_monthly_impressions_are_three_scenarios_of_the_lower_end_audience_and_the_flight_is_that_times_the_months(tiktok):
    est = ct.estimate(resolve_geo("Los Angeles"), ct.Targeting(), 3, client=tiktok)
    size = est.size
    assert est.factor == 5.5 and est.months == 3 and est.multipliers == {"conservative": 4.3, "mid": 5.5, "upper": 8.6}
    # every scenario is the LOWER end of TikTok's range x its multiplier: conservative 4.3 (low), mid 5.5 (the default), upper 8.6 (high)
    assert est.monthly == pytest.approx({"low": size.lower * 4.3, "mid": size.lower * 5.5, "high": size.lower * 8.6})
    assert est.total["mid"] == pytest.approx(size.lower * 5.5 * 3) and est.total["low"] < est.total["mid"] < est.total["high"]
    assert est.scenarios["mid"] == {"multiplier": 5.5, "monthly": pytest.approx(size.lower * 5.5), "total": pytest.approx(size.lower * 5.5 * 3)}
    assert size.lower < size.mid < size.upper                                    # the upper end of TikTok's range is context, not a base


def test_the_planning_multipliers_can_be_changed_by_settings_and_a_bad_set_is_ignored(tiktok, monkeypatch):
    monkeypatch.setenv("CONSTANZA_AUDIENCE_X_MID", "6")
    est = ct.estimate(resolve_geo("Los Angeles"), ct.Targeting(), 1, client=tiktok)
    assert est.factor == 6.0 and est.monthly["mid"] == pytest.approx(est.size.lower * 6) and est.monthly["low"] == pytest.approx(est.size.lower * 4.3)
    monkeypatch.setenv("CONSTANZA_AUDIENCE_X_MID", "3")                          # below the conservative multiplier: out of order, the defaults are used
    assert ct.estimate(resolve_geo("Los Angeles"), ct.Targeting(), 1, client=tiktok).factor == 5.5


def test_the_estimate_sends_the_markets_zip_codes_and_targeting_tiktok_needs(tiktok):
    t = ct.Targeting.from_dict({"gender": "female", "age_groups": ["AGE_25_34"], "interest_category_ids": ["12100"]})
    est = ct.estimate(resolve_geo("Phoenix; 90210"), t, 1, client=tiktok)          # 90210 is outside Phoenix (inside Los Angeles it would be folded into the market)
    sent = tiktok.fake.calls[-1]["body"]
    assert sent["location_ids"] and sent["zipcode_ids"] == ["8840000490210"] and sent["gender"] == "GENDER_FEMALE"
    assert sent["advertiser_id"] == ADVERTISER and est.request["interest_category_ids"] == ["12100"]


def test_narrower_targeting_gives_a_smaller_audience(tiktok):
    wide = ct.estimate(resolve_geo("Los Angeles"), ct.Targeting(), 1, client=tiktok)
    narrow = ct.estimate(resolve_geo("Los Angeles"), ct.Targeting.from_dict({"gender": "male", "age_groups": ["AGE_18_24"], "interest_category_ids": ["14100"]}), 1, client=tiktok)
    assert narrow.size.mid < wide.size.mid


def test_places_tiktok_cannot_place_are_reported_and_the_rest_is_still_estimated(tiktok):
    tiktok.fake.dmas = [d for d in tiktok.fake.dmas if not d.dma.startswith("Phoenix")]
    est = ct.estimate(resolve_geo("Los Angeles; Phoenix"), ct.Targeting(), 1, client=tiktok)
    assert len(est.mapping.locations) == 1 and est.mapping.unmapped and est.mapping.unmapped[0]["input"].startswith("Phoenix")


def test_when_nothing_can_be_placed_no_estimate_is_asked_for(tiktok):
    tiktok.fake.dmas = []
    with pytest.raises(ValueError, match="None of those places could be found in TikTok"):
        ct.estimate(resolve_geo("Phoenix"), ct.Targeting(), 1, client=tiktok)
    assert tiktok.fake.count(ESTIMATE) == 0


def test_the_notes_say_what_the_audience_is_and_that_the_impressions_are_a_planning_factor(tiktok):
    est = ct.estimate(resolve_geo("Los Angeles"), ct.Targeting(), 1, client=tiktok)
    text = " ".join(est.notes())
    assert "TikTok accounts (18 and over)" in text and "lower-end audience" in text and "x 4.3 / 5.5 / 8.6" in text.replace("× ", "x ").replace("x 4.3", "x 4.3")
    assert "conservative / mid / upper; mid is the default" in text and "not a delivery forecast" in text


def test_a_narrow_audience_gets_tiktoks_own_warning(tiktok):
    tiktok.fake.estimate_overrides = lambda data: {**data, "user_count_stage": 1}
    est = ct.estimate(resolve_geo("Los Angeles"), ct.Targeting(), 1, client=tiktok)
    assert any("Too narrow" in n for n in est.notes())


# --------------------------------------------------------------------------------------------- the estimate endpoint

def test_a_hispanic_ask_is_not_applied_to_a_tiktok_estimate_and_says_so(tiktok):
    plain = main.constanza_estimate(request())
    assert plain["hispanic_targeting"] == {"default": False, "on": False, "applies": False}
    assert not any("Hispanic targeting isn't applied" in n for n in plain["notes"])
    for kw in ({"hispanic": True}, {"audience": main.ConstanzaAudienceSpecModel(hispanic=True)}):
        out = main.constanza_estimate(request(**kw))
        assert out["hispanic_targeting"]["on"] is False and out["imps_mid"] == plain["imps_mid"] and "audience" not in out
        assert any("Hispanic targeting isn't applied to a TikTok audience estimate" in n for n in out["notes"])


def test_the_estimate_is_shaped_like_every_other_estimate_plus_a_tiktok_block(tiktok):
    out = main.constanza_estimate(request(months=2))
    t = out["tiktok"]
    assert out["platform"] == "tiktok" and out["confidence"] == "live_api" and out["months"] == 2 and out["series"] is None and out["by_area"] == []
    lower = t["audience"]["low"]
    assert out["imps_low"] == pytest.approx(lower * 4.3 * 2) and out["imps_mid"] == pytest.approx(lower * 5.5 * 2) and out["imps_high"] == pytest.approx(lower * 8.6 * 2)
    assert out["est_uniques"] == lower and out["imps_mid"] / 2 / out["est_uniques"] == pytest.approx(5.5)    # the Avails step's frequency is the MID multiplier
    assert t["monthly"] == pytest.approx({"low": lower * 4.3, "mid": lower * 5.5, "high": lower * 8.6}) and t["impressions_per_person"] == 5.5
    sc = t["scenarios"]
    assert sc["basis"] == "lower_end" and sc["default"] == "mid" and sc["multipliers"] == {"conservative": 4.3, "mid": 5.5, "upper": 8.6}
    assert sc["by_scenario"]["upper"]["total"] == pytest.approx(lower * 8.6 * 2) and sc["monthly"]["conservative"] == pytest.approx(lower * 4.3)
    assert t["locations"][0]["source"].startswith("Los Angeles") and t["locations"][0]["geo_type"] == "DMA" and t["unmapped"] == []
    assert t["objective"] == "Reach" and t["placements"] == ["PLACEMENT_TIKTOK"] and t["request_id"] and t["cached"] is False
    assert t["stage"] in (1, 2, 3, 4) and t["stage_label"]


def test_an_identical_pull_is_answered_from_memory_and_says_so(tiktok):
    first = main.constanza_estimate(request())
    again = main.constanza_estimate(request())
    assert first["tiktok"]["cached"] is False and again["tiktok"]["cached"] is True and tiktok.fake.count(ESTIMATE) == 1
    assert again["imps_mid"] == first["imps_mid"]


def test_the_targeting_of_the_request_reaches_tiktok(tiktok):
    out = main.constanza_estimate(request(tiktok=targeting(gender="female", age_groups=["AGE_25_34", "AGE_35_44"], languages=["es"], interest_category_ids=["12100"])))
    sent = tiktok.fake.calls[-1]["body"]
    assert sent["gender"] == "GENDER_FEMALE" and sent["age_groups"] == ["AGE_25_34", "AGE_35_44"] and sent["languages"] == ["es"]
    assert out["imps_mid"] < main.constanza_estimate(request())["imps_mid"]


def test_a_national_pull_uses_the_united_states_and_a_state_uses_the_state(tiktok):
    nat = main.constanza_estimate(request(geo_text="National"))
    assert nat["tiktok"]["locations"][0]["id"] == "6252001" and nat["tiktok"]["locations"][0]["geo_type"] == "COUNTRY"
    tx = main.constanza_estimate(request(geo_text="Texas"))
    assert tx["tiktok"]["locations"][0]["geo_type"] == "PROVINCE" and tx["tiktok"]["locations"][0]["source"] == "Texas"


def test_places_that_could_not_be_placed_are_listed_as_not_included(tiktok):
    tiktok.fake.dmas = [d for d in tiktok.fake.dmas if not d.dma.startswith("Phoenix")]
    out = main.constanza_estimate(request(geo_text="Los Angeles; Phoenix"))
    assert out["notes"][0].startswith("NOT INCLUDED in this estimate") and "Phoenix" in out["notes"][0]
    assert any(t.startswith("Phoenix") for t in out["unmatched_geo_tokens"]) and out["tiktok"]["unmapped"]
    assert any(d["input"].startswith("Phoenix") and d["reason"] for d in out["unmatched_geo_details"])


def test_a_geography_that_does_not_resolve_is_the_usual_422_before_tiktok_is_asked(tiktok):
    with pytest.raises(HTTPException) as err:
        main.constanza_estimate(request(geo_text="Qzxwvbn"))
    assert err.value.status_code == 422 and tiktok.fake.calls == []


@pytest.mark.parametrize("bad", [dict(age_groups=["AGE_13_17"]), dict(gender="robot"), dict(interest_category_ids=["x"]), dict(purchase_intention_ids=["26100"])])
def test_a_targeting_that_cannot_be_asked_is_a_422_in_plain_words_and_no_call_is_spent(tiktok, bad):
    with pytest.raises(HTTPException) as err:
        main.constanza_estimate(request(tiktok=targeting(**bad)))
    assert err.value.status_code == 422 and isinstance(err.value.detail, str) and tiktok.fake.calls == []


def test_tiktoks_own_refusal_comes_back_as_a_422_with_its_words(tiktok):
    tiktok.fake.queue(ESTIMATE, FakeResponse(200, fail(40002, "age_groups: AGE_18_24 is not valid for these locations")))
    with pytest.raises(HTTPException) as err:
        main.constanza_estimate(request())
    assert err.value.status_code == 422 and "AGE_18_24 is not valid" in err.value.detail


def test_the_errors_an_admin_can_fix_say_which_setting(monkeypatch, tiktok):
    bad = make_client(FakeTikTok(token="somebody-elses"))
    monkeypatch.setattr(tc, "get_client", lambda: bad)
    with pytest.raises(HTTPException) as err:
        main.constanza_estimate(request())
    assert err.value.status_code == 502 and "TIKTOK_ACCESS_TOKEN" in err.value.detail and GOOD_TOKEN not in err.value.detail

    def not_set():
        raise tc.not_configured_error()
    monkeypatch.setattr(tc, "get_client", not_set)
    with pytest.raises(HTTPException) as err2:
        main.constanza_estimate(request())
    assert err2.value.status_code == 503 and "TikTok isn't connected yet" in err2.value.detail and "TIKTOK_ADVERTISER_ID" in err2.value.detail


def test_a_rate_limit_and_an_outage_are_told_apart(tiktok):
    tiktok.fake.queue(ESTIMATE, *[FakeResponse(200, fail(40100, "slow")) for _ in range(3)])
    with pytest.raises(HTTPException) as limited:
        main.constanza_estimate(request())
    assert limited.value.status_code == 429 and "wait a few seconds" in limited.value.detail
    tiktok.fake.queue(ESTIMATE, *[timeout_error() for _ in range(3)])
    with pytest.raises(HTTPException) as down:
        main.constanza_estimate(request(geo_text="Chicago"))
    assert down.value.status_code == 502 and "try again in a moment" in down.value.detail


def test_a_product_that_is_not_the_tiktok_product_is_not_sent_to_tiktok(tiktok):
    assert fm.resolve_forecast(TIKTOK).method == "tiktok"
    news = fm.resolve_forecast("Tik Tok Ads on Entravision News and Radio Pages")
    assert news.method == "generic" and not news.enabled
    assert fm.resolve_forecast("Entravision+ Connect - Family").method != "tiktok"


# --------------------------------------------------------------------------------------------- the product and the admin's mapping

def test_the_tiktok_product_is_on_by_default_with_the_tiktok_method_and_no_platform(tiktok):
    cfg = fm.resolve_forecast(TIKTOK)
    assert (cfg.enabled, cfg.method, cfg.platform, cfg.hispanic_weighted, cfg.source) == (True, "tiktok", None, False, "default")
    assert cfg.to_public()["method"] == "tiktok"
    assert "tiktok" in fm.METHODS and fm.DEFAULT_API_PRODUCTS == {"Tiktok Ads": "tiktok"}


def test_an_admin_can_choose_the_tiktok_method_for_a_product_and_it_carries_no_platform():
    assert fm.clean_config({"enabled": True, "method": "tiktok", "platform": "roku", "hispanic_weighted": True}) == \
        {"enabled": True, "method": "tiktok", "platform": None, "hispanic_weighted": False, "win_rate": None}     # (TikTok has no avails to win)
    with pytest.raises(ValueError):
        fm.clean_config({"enabled": True, "method": "magic"})
    assert fm._valid_row({"method": "tiktok", "platform": None, "enabled": True})


def test_an_admins_row_wins_over_the_default(tiktok):
    row = {"product_name": TIKTOK, "enabled": False, "method": "generic", "platform": None, "hispanic_weighted": False}
    cfg = fm.resolve_forecast(TIKTOK, overrides={TIKTOK: row})
    assert cfg.method == "generic" and not cfg.enabled and cfg.source == "admin"
    other = fm.resolve_forecast("Entravision+ Connect - Family", overrides={"Entravision+ Connect - Family": {**row, "product_name": "Entravision+ Connect - Family", "method": "tiktok", "enabled": True}})
    assert other.method == "tiktok"


# --------------------------------------------------------------------------------------------- the status and the catalogs

def test_the_status_never_carries_a_secret(monkeypatch):
    monkeypatch.setenv("TIKTOK_ACCESS_TOKEN", "SECRET-TOKEN-VALUE")
    monkeypatch.setenv("TIKTOK_ADVERTISER_ID", "7000000000000001234")
    out = main.constanza_tiktok_status()
    blob = json.dumps(out)
    assert out["configured"] is True and out["missing"] == [] and out["advertiser"] == "…1234" and out["impressions_per_person"] == 5.5
    assert out["scenarios"]["multipliers"] == {"conservative": 4.3, "mid": 5.5, "upper": 8.6}
    assert "SECRET-TOKEN-VALUE" not in blob and "7000000000000001234" not in blob


def test_the_status_says_what_is_missing_and_still_has_the_menus(monkeypatch):
    monkeypatch.delenv("TIKTOK_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("TIKTOK_ADVERTISER_ID", raising=False)
    out = main.constanza_tiktok_status()
    assert out["configured"] is False and out["missing"] == ["TIKTOK_ACCESS_TOKEN", "TIKTOK_ADVERTISER_ID"] and "secrets" in out["setup_hint"]
    opts = out["options"]
    assert [a["id"] for a in opts["age_groups"]] == ["AGE_18_24", "AGE_25_34", "AGE_35_44", "AGE_45_54", "AGE_55_100"]
    assert [o["id"] for o in opts["objectives"]] == ["reach", "video_views", "traffic"] and opts["default_objective"] == "reach"
    assert {p["id"] for p in opts["placements"]} == {"PLACEMENT_TIKTOK", "PLACEMENT_PANGLE"} and opts["video_periods"] == [0, 7, 15]


def test_a_category_list_comes_with_the_path_of_each_category(tiktok):
    out = main.constanza_tiktok_targeting("general_interest")
    items = {i["id"]: i for i in out["items"]}
    assert items["10100"]["path"] == "Apparel & Accessories" and items["10000"]["path"] == "" and items["10000"]["has_children"] is True
    assert main.constanza_tiktok_targeting("languages")["items"][0] == {"code": "en", "name": "English"}
    for kind in ("purchase_intention", "video_interaction", "creator_interaction", "hashtag_interaction"):
        assert main.constanza_tiktok_targeting(kind)["items"]


def test_an_unknown_kind_of_targeting_is_a_422(tiktok):
    with pytest.raises(HTTPException) as err:
        main.constanza_tiktok_targeting("secrets")
    assert err.value.status_code == 422


def test_a_search_finds_each_kind_and_a_one_letter_search_asks_nothing(tiktok):
    out = main.constanza_tiktok_search("auto")
    assert {"additional_interest", "hashtag_interaction"} <= set(out["results"]) and out["query"] == "auto"
    assert main.constanza_tiktok_search("a")["results"] == {} and main.constanza_tiktok_search("   ")["results"] == {}
    assert tiktok.fake.count("/targeting/search/") == 1


def test_the_catalog_routes_report_tiktoks_failures_cleanly(monkeypatch):
    def not_set():
        raise tc.not_configured_error()
    monkeypatch.setattr(tc, "get_client", not_set)
    with pytest.raises(HTTPException) as err:
        main.constanza_tiktok_targeting("general_interest")
    assert err.value.status_code == 503
    with pytest.raises(HTTPException) as err2:
        main.constanza_tiktok_search("auto")
    assert err2.value.status_code == 503


def test_paths_follow_the_tree_and_survive_a_cycle():
    items = [{"id": "1", "name": "Top", "children_ids": ["2"], "level": 1}, {"id": "2", "name": "Mid", "children_ids": ["3"], "level": 2},
             {"id": "3", "name": "Leaf", "children_ids": [], "level": 3}]
    assert {i["id"]: i["path"] for i in ct.with_paths(items)} == {"1": "", "2": "Top", "3": "Top › Mid"}
    loop = [{"id": "1", "name": "A", "children_ids": ["2"]}, {"id": "2", "name": "B", "children_ids": ["1"]}]
    assert {i["id"]: i["path"] for i in ct.with_paths(loop)} == {"1": "B", "2": "A"}


# --------------------------------------------------------------------------------------------- Admin -> Forecast

def test_the_admin_connection_test_runs_the_checks_and_never_returns_a_secret(monkeypatch, tiktok):
    monkeypatch.setenv("TIKTOK_ACCESS_TOKEN", "SECRET-TOKEN-VALUE")
    monkeypatch.setenv("TIKTOK_ADVERTISER_ID", "7000000000000000001")
    out = main.admin_tiktok_test()
    assert out["ok"] is True and len(out["checks"]) == 10 and out["status"]["configured"] is True
    blob = json.dumps(out)
    assert "SECRET-TOKEN-VALUE" not in blob and "7000000000000000001" not in blob and GOOD_TOKEN not in blob


def test_the_admin_connection_test_says_what_is_missing_when_nothing_is_set(monkeypatch):
    monkeypatch.delenv("TIKTOK_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("TIKTOK_ADVERTISER_ID", raising=False)
    out = main.admin_tiktok_test()
    assert out["ok"] is False and len(out["checks"]) == 1 and "TIKTOK_ACCESS_TOKEN" in out["checks"][0]["detail"]


def test_the_admin_page_offers_the_tiktok_method_and_the_connection_test():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    html = (root / "app" / "templates" / "admin.html").read_text(encoding="utf-8")
    js = (root / "app" / "static" / "admin.js").read_text(encoding="utf-8")
    assert 'id="fc-tt-test"' in html and 'id="fc-tt-checks"' in html and "TIKTOK_ACCESS_TOKEN" in html and "TIKTOK_ADVERTISER_ID" in html
    assert 'value="tiktok"' in js and "TikTok API (live)" in js and "/api/admin/tiktok/test" in js and "/api/constanza/tiktok/status" in js


def test_the_optimization_goal_of_a_reach_estimate_can_be_replaced_by_a_setting_and_a_traffic_one_cannot(monkeypatch):
    reach = ct.build_estimate_body(ct.Targeting(), loc("1"))
    assert (reach["objective_type"], reach["optimization_goal"]) == ("REACH", "REACH")
    monkeypatch.setenv("TIKTOK_OPTIMIZATION_GOAL", "click")
    assert ct.build_estimate_body(ct.Targeting(), loc("1"))["optimization_goal"] == "CLICK"
    traffic = ct.build_estimate_body(ct.Targeting.from_dict({"objective": "traffic"}), loc("1"))
    assert (traffic["objective_type"], traffic["optimization_goal"]) == ("TRAFFIC", "CLICK")        # its own pair, untouched
    assert ct.build_estimate_body(ct.Targeting(), loc("1"), goal_override="CLICK")["optimization_goal"] == "CLICK"


def test_every_browse_tab_reads_the_one_list_tiktok_sends_by_default(tiktok):
    kinds = ("general_interest", "purchase_intention", "video_interaction", "creator_interaction", "hashtag_interaction")
    for kind in kinds:
        assert main.constanza_tiktok_targeting(kind)["items"]
    assert tiktok.fake.count("/targeting/search/") == 1
    assert "sub_targeting_types" not in tiktok.fake.calls[0]["params"]


# --------------------------------------------------------------------------------------------- the review's findings

def test_targeting_that_is_not_an_object_is_refused_in_plain_words():
    for bad in ("12345", ["x"], 5):
        with pytest.raises(ValueError, match="must be an object"):
            ct.Targeting.from_dict(bad)


def test_a_flood_of_ids_is_refused_without_working_through_it():
    import time
    started = time.monotonic()
    with pytest.raises(ValueError, match="at most 50"):
        ct.Targeting.from_dict({"interest_category_ids": [str(i) for i in range(200_000)]})
    assert time.monotonic() - started < 1.0


def test_a_traffic_estimate_carries_the_promotion_type_tiktok_asks_for():
    body = ct.build_estimate_body(ct.Targeting.from_dict({"objective": "traffic"}), loc("1"))
    assert body["promotion_type"] == "WEBSITE" and "promotion_type" not in ct.build_estimate_body(ct.Targeting(), loc("1"))


def test_a_traffic_campaign_with_purchase_intent_is_taken_by_tiktok(tiktok):
    t = ct.Targeting.from_dict({"objective": "traffic", "purchase_intention_ids": ["26100"], "age_groups": ["AGE_25_34"]})
    est = ct.estimate(resolve_geo("Los Angeles"), t, 1, client=tiktok)
    assert est.size.mid > 0 and tiktok.fake.calls[-1]["body"]["promotion_type"] == "WEBSITE"


def test_geography_alone_is_asked_without_age_groups_like_tiktoks_own_example(tiktok):
    est = ct.estimate(resolve_geo("Los Angeles"), ct.Targeting(), 1, client=tiktok)
    assert "age_groups" not in tiktok.fake.calls[-1]["body"] and not est.adult_ages_sent


def test_when_tiktok_asks_for_the_age_groups_to_be_spelled_out_they_are_sent_and_remembered(tiktok):
    """Return code 40065: U.S. interest targeting needs `age_groups` that do not contain AGE_13_17. The five adult groups are the same audience
    (the estimate leaves out under-18s), so the request is repeated once with them, the planner is told, and the next one does not fail first."""
    t = ct.Targeting.from_dict({"interest_category_ids": ["12100"]})
    first = ct.estimate(resolve_geo("Los Angeles"), t, 1, client=tiktok)
    estimates = [c for c in tiktok.fake.calls if c["path"] == ESTIMATE]
    assert len(estimates) == 2 and "age_groups" not in estimates[0]["body"]
    assert estimates[1]["body"]["age_groups"] == ["AGE_18_24", "AGE_25_34", "AGE_35_44", "AGE_45_54", "AGE_55_100"]
    assert first.adult_ages_sent and tiktok.adult_ages_needed and any("age groups spelled out" in n for n in first.notes())
    ct.estimate(resolve_geo("Chicago"), t, 1, client=tiktok)
    estimates = [c for c in tiktok.fake.calls if c["path"] == ESTIMATE]
    assert len(estimates) == 3 and estimates[2]["body"]["age_groups"]            # remembered: no failing call first


def test_what_was_learned_from_one_age_refusal_only_reaches_requests_that_use_the_restricted_options(tiktok):
    """The "spell the adult ages out" memory is not a process-wide switch: a request with no interests, behaviors or income is asked exactly
    as the planner set it up (no age groups, no note about interests), whatever was asked before."""
    ct.estimate(resolve_geo("Los Angeles"), ct.Targeting.from_dict({"interest_category_ids": ["12100"]}), 1, client=tiktok)
    assert tiktok.adult_ages_needed
    plain = ct.estimate(resolve_geo("National"), ct.Targeting.from_dict({"gender": "female"}), 1, client=tiktok)
    assert "age_groups" not in tiktok.fake.calls[-1]["body"] and not plain.adult_ages_sent
    assert not any("age groups spelled out" in n for n in plain.notes())
    income = ct.estimate(resolve_geo("Chicago"), ct.Targeting.from_dict({"household_income": ["TOP25_50"]}), 1, client=tiktok)
    assert tiktok.fake.calls[-1]["body"]["age_groups"] and income.adult_ages_sent          # income is restricted too: said up front


def test_a_planners_own_age_groups_are_never_replaced(tiktok):
    t = ct.Targeting.from_dict({"interest_category_ids": ["12100"], "age_groups": ["AGE_25_34"]})
    est = ct.estimate(resolve_geo("Los Angeles"), t, 1, client=tiktok)
    assert tiktok.fake.calls[-1]["body"]["age_groups"] == ["AGE_25_34"] and not est.adult_ages_sent and not tiktok.adult_ages_needed


def test_an_age_refusal_that_the_adult_groups_do_not_cure_is_reported(tiktok):
    tiktok.fake.queue(ESTIMATE, FakeResponse(200, fail(40065, "age restrictions")), FakeResponse(200, fail(40065, "age restrictions again")))
    with pytest.raises(tc.TikTokAPIError) as err:
        ct.estimate(resolve_geo("Los Angeles"), ct.Targeting.from_dict({"interest_category_ids": ["12100"]}), 1, client=tiktok)
    assert err.value.code == 40065 and err.value.kind == "invalid"


def test_the_endpoint_tells_the_planner_when_the_age_groups_were_spelled_out(tiktok):
    out = main.constanza_estimate(request(tiktok=targeting(interest_category_ids=["12100"])))
    assert any("age groups spelled out" in n for n in out["notes"]) and out["imps_mid"] > 0


def test_the_wire_model_refuses_a_field_it_does_not_know_instead_of_dropping_it():
    with pytest.raises(Exception):
        main.TikTokTargetingModel(**{"gender": "male", "agegroups": ["AGE_18_24"]})
