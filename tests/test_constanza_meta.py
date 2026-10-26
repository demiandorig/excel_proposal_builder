"""Constanza x Meta: the audience-size forecast of the three paid Facebook & Instagram Ads SKUs, offline.

Meta's reach estimate is a head count (a range); Entravision's planning rule turns its LOWER end into impressions as conservative x 4.3,
mid x 5.5 (the default) and upper x 8.6 (platform_scenarios.py). Pinned here: those scenarios and the budget cross-check, the targeting spec
sent to Meta (geography, age, gender, interests / behaviors as ONE any-of group), Meta's interest / behavior / demographic lists, how Meta's errors
are told to the planner (never with the token), the secret handling, and the API round trip - against a fake Graph API shaped like Meta's reference."""
from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

import app.main as main
from app.services import constanza_meta as meta

AWARENESS = "Facebook & Instagram Ads | Awareness"
TOKEN = "EAAB-test-private-token-0123456789"
ACCOUNT = "act_7000000000012345"


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for name in ("META_ACCESS_TOKEN", "META_AD_ACCOUNT_ID", "META_GRAPH_API_VERSION",
                 "CONSTANZA_AUDIENCE_X_CONSERVATIVE", "CONSTANZA_AUDIENCE_X_MID", "CONSTANZA_AUDIENCE_X_UPPER"):
        monkeypatch.delenv(name, raising=False)
    meta.reset_cache()
    yield
    meta.reset_cache()


class FakeGraph:
    """A Graph API shaped like Meta's reference: /search (adgeolocation, adinterest, adTargetingCategory) and act_<id>/reachestimate."""

    def __init__(self):
        self.calls: list[dict] = []
        self.reach = (900, 1100)
        self.error: dict | None = None
        self.http_status = 200
        self.estimate_ready = True

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": dict(params or {}), "headers": dict(headers or {}), "timeout": timeout})
        if self.error is not None:
            return Resp({"error": self.error}, self.http_status)
        path = url.split("graph.facebook.com/", 1)[1]
        version, rest = path.split("/", 1)
        params = params or {}
        if rest == "search":
            kind = params.get("type")
            if kind == "adgeolocation":
                return Resp({"data": [{"key": "803", "name": "Los Angeles", "type": "geo_market", "country_code": "US", "region": "California"},
                                      {"key": "804", "name": "Los Angeles-Long Beach", "type": "geo_market", "country_code": "US"}]})
            if kind == "adinterest":
                return Resp({"data": [{"id": "6003107902433", "name": "Soccer", "audience_size_lower_bound": 100_000_000, "audience_size_upper_bound": 120_000_000,
                                       "path": ["Interests", "Sports and outdoors", "Soccer"]},
                                      {"id": "6003012345678", "name": "Soccer balls", "audience_size": 4_000_000}]})
            if kind == "adTargetingCategory":
                cls = params.get("class")
                rows = {"behaviors": [{"id": "6002714895372", "name": "Frequent travelers", "type": "behaviors", "audience_size_lower_bound": 2_000_000, "audience_size_upper_bound": 2_400_000,
                                       "path": ["Behaviors", "Travel", "Frequent travelers"]},
                                      {"id": "6004386044572", "name": "Small business owners", "type": "behaviors", "path": ["Behaviors", "Business", "Small business owners"]}],
                        "interests": [{"id": "6003107902433", "name": "Soccer", "type": "interests"}, {"id": "6003020834693", "name": "Cooking", "type": "interests"}],
                        "demographics": [{"id": "6002714398172", "name": "Newlyweds (1 year)", "type": "life_events", "path": ["Demographics", "Life events", "Newlywed"]}]}[cls]
                return Resp({"data": rows})
        if rest.endswith("/reachestimate"):
            lo, hi = self.reach
            return Resp({"data": [{"users_lower_bound": lo, "users_upper_bound": hi, "estimate_ready": self.estimate_ready}]})
        if rest.startswith("act_") and "/" not in rest:
            return Resp({"name": "Entravision Test Account", "account_status": 1, "currency": "USD"})
        return Resp({"error": {"message": f"Unknown path {rest}", "code": 100}}, 400)


class Resp:
    def __init__(self, body, status=200):
        self._body, self.status_code = body, status

    @property
    def ok(self):
        return self.status_code < 400

    def json(self):
        return self._body


@pytest.fixture
def graph(monkeypatch):
    monkeypatch.setenv("META_ACCESS_TOKEN", TOKEN)
    monkeypatch.setenv("META_AD_ACCOUNT_ID", ACCOUNT)
    fake = FakeGraph()
    monkeypatch.setattr(meta.requests, "get", fake.get)
    return fake


# ---------------------------------------------------------------------------------------------------------------- the scenarios

def test_the_three_scenarios_are_the_lower_end_times_4_3_5_5_and_8_6():
    result = meta.model_auction(product_name=AWARENESS, retail_budget=2000, audience_lower=1_000_000, audience_upper=1_200_000)
    sc = result["scenarios"]
    assert sc["basis"] == "lower_end" and sc["default"] == "mid" and sc["multipliers"] == {"conservative": 4.3, "mid": 5.5, "upper": 8.6}
    assert sc["monthly"] == {"conservative": 4_300_000, "mid": 5_500_000, "upper": 8_600_000}
    assert sc["est_uniques"] == 1_000_000                                            # the lower end: the Avails step's frequency is then the mid multiplier
    assert result["potential_audience"] == {"lower": 1_000_000, "upper": 1_200_000, "source": "manual"}
    assert result["metric_type"] == "meta_audience_scenarios"


def test_a_flight_is_the_monthly_scenario_times_its_months_and_each_has_an_implied_spend():
    result = meta.model_auction(product_name=AWARENESS, retail_budget=2000, audience_lower=1000, audience_upper=1000, months=3)
    sc = result["scenarios"]
    assert sc["months"] == 3 and sc["flight"] == {"conservative": 12_900, "mid": 16_500, "upper": 25_800}
    assert sc["retail_spend_at_flight"] == {"conservative": 154.8, "mid": 198.0, "upper": 309.6}          # x the $12 retail CPM / 1000
    assert sc["media_spend_at_flight"] == {"conservative": 77.4, "mid": 99.0, "upper": 154.8}              # media CPM is half the retail one


def test_only_a_lower_end_is_needed_and_the_upper_end_is_never_the_base():
    only_lower = meta.model_auction(product_name=AWARENESS, retail_budget=100, audience_lower=500)
    assert only_lower["potential_audience"]["upper"] == 500 and only_lower["scenarios"]["monthly"]["mid"] == 2750
    wide = meta.model_auction(product_name=AWARENESS, retail_budget=100, audience_lower=500, audience_upper=5000)
    assert wide["scenarios"]["monthly"] == only_lower["scenarios"]["monthly"]                              # the upper end changes nothing
    with pytest.raises(ValueError, match="lower end"):
        meta.model_auction(product_name=AWARENESS, retail_budget=100, audience_upper=5000)


def test_the_multipliers_follow_the_shared_settings(monkeypatch):
    monkeypatch.setenv("CONSTANZA_AUDIENCE_X_MID", "6")
    assert meta.model_auction(product_name=AWARENESS, retail_budget=100, audience_lower=1000)["scenarios"]["monthly"]["mid"] == 6000


def test_without_an_audience_there_are_no_scenarios_only_the_budget_cross_check():
    result = meta.model_auction(product_name=AWARENESS, retail_budget=2000)
    assert result["scenarios"] is None and result["potential_audience"] is None
    assert result["budget_impressions"] == round(2000 * 1000 / 12) and result["media_budget"] == 1000 and result["media_cpm_assumed"] == 6


@pytest.mark.parametrize("name,cpm", [
    ("Facebook & Instagram Ads | Awareness", 12),
    ("Facebook & Instagram Ads | Traffic / Conversion", 18),
    ("Facebook & Instagram Ads | Lead Gen / Calls", 35),
])
def test_confirmed_retail_cpms_and_two_to_one_media_budget(name, cpm):
    result = meta.model_auction(product_name=name, retail_budget=2000)
    assert result["retail_cpm"] == cpm and result["media_budget"] == 1000 and result["media_cpm_assumed"] == cpm / 2
    assert result["budget_impressions"] == round(2000 * 1000 / cpm)


def test_branded_content_cannot_use_the_paid_meta_model():
    with pytest.raises(ValueError, match="paid Facebook"):
        meta.model_auction(product_name="Facebook & Instagram Ads on EVC Radio Pages", retail_budget=1000)


def test_renamed_paid_sku_keeps_its_confirmed_cpm(monkeypatch):
    monkeypatch.setattr(meta.catalog, "name_variants", lambda name: {name, AWARENESS})
    assert meta.product_cpm("Renamed Awareness SKU") == 12


@pytest.mark.parametrize("overrides", [
    {"retail_budget": -1}, {"retail_budget": float("nan")}, {"retail_budget": 0}, {"retail_cpm": 0}, {"retail_cpm": float("inf")},
    {"audience_lower": 300, "audience_upper": 200}, {"audience_lower": -1}, {"audience_lower": 2_000_000_000},
    {"months": 0}, {"months": 121}, {"months": 2.5}, {"months": True}, {"audience_source": "guess"},
])
def test_invalid_inputs_fail_before_creating_a_forecast(overrides):
    args = dict(product_name=AWARENESS, retail_budget=100, audience_lower=100)
    args.update(overrides)
    with pytest.raises(ValueError):
        meta.model_auction(**args)


# ---------------------------------------------------------------------------------------------------------------- the targeting spec

def test_geography_age_and_gender_make_the_targeting_spec():
    spec = meta.build_targeting_spec(geo_type="geo_market", geo_key="803", age_min=25, age_max=44, gender="female")
    assert spec == {"geo_locations": {"geo_markets": [{"key": "803"}]}, "age_min": 25, "age_max": 44, "genders": [2]}
    assert meta.build_targeting_spec(geo_type="country", geo_key="US") == {"geo_locations": {"countries": ["US"]}}
    assert meta.build_targeting_spec(geo_type="zip", geo_key="US:90210", gender="all") == {"geo_locations": {"zips": [{"key": "US:90210"}]}}


def test_chosen_interests_and_behaviors_are_one_any_of_group():
    spec = meta.build_targeting_spec(geo_type="country", geo_key="US", targeting=[
        {"id": "6003107902433", "name": "Soccer", "type": "interests"}, {"id": "6003020834693", "name": "Cooking", "type": "interests"},
        {"id": "6002714895372", "name": "Frequent travelers", "type": "behaviors"}, {"id": "6003107902433", "name": "Soccer", "type": "interests"}])
    assert spec["flexible_spec"] == [{"interests": [{"id": "6003107902433", "name": "Soccer"}, {"id": "6003020834693", "name": "Cooking"}],
                                      "behaviors": [{"id": "6002714895372", "name": "Frequent travelers"}]}]       # one group (any of), duplicates folded


@pytest.mark.parametrize("kwargs", [
    {"geo_type": "planet", "geo_key": "1"}, {"geo_type": "city", "geo_key": ""}, {"geo_type": "city", "geo_key": "a b"},
    {"geo_type": "city", "geo_key": "1", "age_min": 17}, {"geo_type": "city", "geo_key": "1", "age_max": 66},
    {"geo_type": "city", "geo_key": "1", "age_min": 40, "age_max": 30}, {"geo_type": "city", "geo_key": "1", "gender": "x"},
    {"geo_type": "city", "geo_key": "1", "targeting": [{"id": "abc", "name": "x", "type": "interests"}]},
    {"geo_type": "city", "geo_key": "1", "targeting": [{"id": "1", "name": "x", "type": "ethnic_affinity"}]},
    {"geo_type": "city", "geo_key": "1", "targeting": [{"id": "1", "name": "", "type": "interests"}]},
    {"geo_type": "city", "geo_key": "1", "targeting": "soccer"},
    {"geo_type": "city", "geo_key": "1", "targeting": [{"id": str(i), "name": "n", "type": "interests"} for i in range(1, 60)]},
])
def test_a_choice_meta_would_refuse_is_refused_before_a_call(kwargs):
    with pytest.raises(ValueError):
        meta.build_targeting_spec(**kwargs)


# ---------------------------------------------------------------------------------------------------------------- the live calls

def test_the_live_reach_request_uses_the_secret_only_in_the_header_and_the_chosen_targeting(graph):
    out = meta.fetch_reach_estimate(geo_type="geo_market", geo_key="803", age_min=25, gender="female",
                                    targeting=[{"id": "6003107902433", "name": "Soccer", "type": "interests"}])
    assert out == (900, 1100)
    call = graph.calls[-1]
    assert call["url"] == "https://graph.facebook.com/v25.0/act_7000000000012345/reachestimate"        # the newest Marketing API version, the account without act_ doubling
    assert TOKEN not in call["url"] and TOKEN not in json.dumps(call["params"]) and call["headers"] == {"Authorization": f"Bearer {TOKEN}"}
    spec = json.loads(call["params"]["targeting_spec"])
    assert spec == {"geo_locations": {"geo_markets": [{"key": "803"}]}, "age_min": 25, "genders": [2],
                    "flexible_spec": [{"interests": [{"id": "6003107902433", "name": "Soccer"}]}]}
    assert call["timeout"] and call["timeout"] <= 15


def test_an_identical_estimate_is_answered_from_memory(graph):
    meta.fetch_reach_estimate(geo_type="country", geo_key="US")
    meta.fetch_reach_estimate(geo_type="country", geo_key="US")
    assert len(graph.calls) == 1
    meta.fetch_reach_estimate(geo_type="country", geo_key="US", age_min=30)
    assert len(graph.calls) == 2


def test_the_ad_account_may_be_given_with_or_without_act(monkeypatch, graph):
    monkeypatch.setenv("META_AD_ACCOUNT_ID", "7000000000012345")
    meta.reset_cache()
    meta.fetch_reach_estimate(geo_type="country", geo_key="US")
    assert "/act_7000000000012345/reachestimate" in graph.calls[-1]["url"] and "act_act_" not in graph.calls[-1]["url"]


def test_a_graph_version_can_be_set_and_a_bad_one_is_refused(monkeypatch, graph):
    monkeypatch.setenv("META_GRAPH_API_VERSION", "v24.0")
    meta.fetch_reach_estimate(geo_type="country", geo_key="US")
    assert "/v24.0/" in graph.calls[-1]["url"]
    monkeypatch.setenv("META_GRAPH_API_VERSION", "latest")
    with pytest.raises(meta.MetaAPIError, match="v25.0"):
        meta.fetch_reach_estimate(geo_type="country", geo_key="US", age_min=40)


@pytest.mark.parametrize("env", [{}, {"META_ACCESS_TOKEN": TOKEN}, {"META_AD_ACCOUNT_ID": ACCOUNT}])
def test_unconfigured_calls_say_which_secret_is_missing(monkeypatch, env):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    missing = meta.missing_config()
    assert missing and not meta.is_configured()
    for call in (lambda: meta.fetch_reach_estimate(geo_type="country", geo_key="US"), lambda: meta.search_geography("Los Angeles", "geo_market"),
                 lambda: meta.targeting_catalog("interests"), lambda: meta.search_targeting("soccer")):
        with pytest.raises(meta.MetaAPIError) as err:
            call()
        assert err.value.kind == "not_configured" and err.value.status_code == 503 and missing[0] in str(err.value) and "Replit" in str(err.value)


def test_a_non_numeric_ad_account_is_a_setting_to_fix(monkeypatch, graph):
    monkeypatch.setenv("META_AD_ACCOUNT_ID", "my-account")
    with pytest.raises(meta.MetaAPIError, match="numeric"):
        meta.fetch_reach_estimate(geo_type="country", geo_key="US")


def test_the_status_never_carries_a_secret(monkeypatch, graph):
    out = main.constanza_meta_status()
    assert out["configured"] is True and out["missing"] == [] and out["account"] == "…2345" and out["version"] == "v25.0"
    assert out["scenarios"]["multipliers"] == {"conservative": 4.3, "mid": 5.5, "upper": 8.6}
    blob = json.dumps(out)
    assert TOKEN not in blob and "7000000000012345" not in blob


# ---------------------------------------------------------------------------------------------------------------- Meta's answers and errors

def test_geography_search_returns_choices_and_never_guesses(graph):
    found = meta.search_geography("Los Angeles", "geo_market")
    assert [f["key"] for f in found] == ["803", "804"] and found[0] == {"key": "803", "name": "Los Angeles", "type": "geo_market", "region": "California", "country_code": "US"}
    p = graph.calls[-1]["params"]
    assert p["type"] == "adgeolocation" and json.loads(p["location_types"]) == ["geo_market"] and p["q"] == "Los Angeles"
    with pytest.raises(ValueError):
        meta.search_geography("Los Angeles", "planet")
    with pytest.raises(ValueError):
        meta.search_geography("L", "city")


@pytest.mark.parametrize("error,status,kind,http,words", [
    ({"message": "Invalid OAuth access token.", "code": 190}, 400, "token", 503, "new long-lived token"),
    ({"message": "The access token is expired", "code": 190, "error_subcode": 463}, 400, "token", 503, "META_ACCESS_TOKEN"),
    ({"message": "(#200) Requires ads_read", "code": 200}, 403, "permission", 503, "ads_read"),
    ({"message": "(#10) Application does not have permission", "code": 10}, 403, "permission", 503, "ads_read"),
    ({"message": "Too many calls to this ad account", "code": 80004}, 400, "rate_limit", 429, "rate-limiting"),
    ({"message": "Rate limit", "code": 613}, 400, "rate_limit", 429, "Wait a minute"),
    ({"message": "Invalid parameter", "code": 100}, 400, "settings", 422, "Meta refused these settings"),
    ({"message": "restricted locations", "code": 2641}, 400, "settings", 422, "restricted"),
    ({"message": "Something broke", "code": 2, "error_subcode": 1}, 500, "meta", 502, "error 2"),
])
def test_meta_errors_become_plain_messages_with_the_right_http_status(graph, error, status, kind, http, words):
    graph.error, graph.http_status = error, status
    with pytest.raises(meta.MetaAPIError) as err:
        meta.fetch_reach_estimate(geo_type="country", geo_key="US")
    assert err.value.kind == kind and err.value.status_code == http and words in str(err.value)


def test_no_error_message_carries_the_token_or_the_ad_account(graph):
    graph.error, graph.http_status = {"message": f"Invalid parameter for act_7000000000012345 with token {TOKEN} in {TOKEN}", "code": 100}, 400
    with pytest.raises(meta.MetaAPIError) as err:
        meta.fetch_reach_estimate(geo_type="country", geo_key="US")
    text = str(err.value)
    assert TOKEN not in text and "7000000000012345" not in text and "[token]" in text and "…2345" in text


def test_a_timeout_or_a_non_json_answer_is_a_clean_failure(monkeypatch, graph):
    def boom(*a, **k):
        raise meta.requests.Timeout("slow")
    monkeypatch.setattr(meta.requests, "get", boom)
    with pytest.raises(meta.MetaAPIError) as err:
        meta.fetch_reach_estimate(geo_type="country", geo_key="US")
    assert err.value.kind == "unreachable" and err.value.status_code == 502 and TOKEN not in str(err.value)

    class Html:
        status_code, ok = 200, True
        def json(self):
            raise ValueError("not json")
    monkeypatch.setattr(meta.requests, "get", lambda *a, **k: Html())
    with pytest.raises(meta.MetaAPIError, match="cannot read"):
        meta.fetch_reach_estimate(geo_type="country", geo_key="US", age_min=22)


@pytest.mark.parametrize("reach,message", [((-1, -1), "cannot estimate this audience"), ((500, 100), "invalid audience bounds")])
def test_an_unavailable_or_impossible_estimate_is_said_not_shown_as_a_number(graph, reach, message):
    graph.reach = reach
    with pytest.raises(meta.MetaAPIError, match=message):
        meta.fetch_reach_estimate(geo_type="country", geo_key="US")


def test_an_estimate_that_is_not_ready_says_to_try_again(graph):
    graph.estimate_ready = False
    with pytest.raises(meta.MetaAPIError, match="still preparing"):
        meta.fetch_reach_estimate(geo_type="country", geo_key="US")


# ---------------------------------------------------------------------------------------------------------------- the lists Meta offers

def test_the_interest_list_and_the_other_lists_come_from_metas_own_browse(graph):
    interests = meta.targeting_catalog("interests")
    assert [i["name"] for i in interests] == ["Cooking", "Soccer"] and interests[0]["type"] == "interests"
    p = graph.calls[-1]["params"]
    assert p["type"] == "adTargetingCategory" and p["class"] == "interests"
    behaviors = meta.targeting_catalog("behaviors")
    assert behaviors[0] == {"id": "6002714895372", "name": "Frequent travelers", "type": "behaviors", "path": ["Behaviors", "Travel", "Frequent travelers"],
                            "audience_lower": 2_000_000, "audience_upper": 2_400_000, "description": ""}
    demo = meta.targeting_catalog("demographics")
    assert demo[0]["type"] == "life_events" and graph.calls[-1]["params"]["class"] == "demographics"
    with pytest.raises(ValueError):
        meta.targeting_catalog("ethnic_affinity")


def test_a_list_is_asked_of_meta_once_for_hours(graph):
    meta.targeting_catalog("behaviors")
    meta.targeting_catalog("behaviors")
    assert len([c for c in graph.calls if c["params"].get("class") == "behaviors"]) == 1


def test_a_keyword_finds_interests_from_meta_and_matching_behaviors_and_demographics(graph):
    found = meta.search_targeting("soccer")
    assert [i["name"] for i in found["interests"]] == ["Soccer", "Soccer balls"] and found["interests"][0]["audience_lower"] == 100_000_000
    assert found["interests"][1]["audience_lower"] == 4_000_000                                     # the older single audience_size field is read too
    assert found["behaviors"] == [] and found["demographics"] == []
    again = meta.search_targeting("travel")
    assert [b["name"] for b in again["behaviors"]] == ["Frequent travelers"]                         # a name OR path word
    assert [d["name"] for d in meta.search_targeting("life events")["demographics"]] == ["Newlyweds (1 year)"]
    with pytest.raises(ValueError):
        meta.search_targeting("a")


def test_one_list_being_unavailable_does_not_hide_the_interests_that_were_found(monkeypatch, graph):
    real = meta.targeting_catalog

    def flaky(kind):
        if kind == "behaviors":
            raise meta.MetaAPIError("Meta returned something odd", "meta", 502)
        return real(kind)
    monkeypatch.setattr(meta, "targeting_catalog", flaky)
    found = meta.search_targeting("soccer")
    assert found["interests"] and found["behaviors"] == []
    monkeypatch.setattr(meta, "targeting_catalog", lambda kind: (_ for _ in ()).throw(meta.MetaAPIError("bad token", "token", 503)))
    with pytest.raises(meta.MetaAPIError):                                                          # but a token problem is never swallowed
        meta.search_targeting("soccer")


# ---------------------------------------------------------------------------------------------------------------- the API

def test_api_manual_and_live_paths_keep_provenance_separate(graph):
    name = "Facebook & Instagram Ads | Lead Gen / Calls"
    manual = main.constanza_meta_forecast(main.MetaAuctionForecastRequest(product_name=name, retail_budget=700, audience_lower=1000, audience_upper=1200, months=2))
    assert manual["potential_audience"]["source"] == "manual" and "meta_targeting" not in manual
    assert manual["scenarios"]["flight"]["mid"] == 11_000 and manual["scenarios"]["monthly"]["conservative"] == 4300
    live = main.constanza_meta_forecast(main.MetaAuctionForecastRequest(
        product_name=name, retail_budget=700, meta_geo_type="geo_market", meta_geo_key="803", age_min=25,
        meta_targeting=[main.MetaTargetingItem(id="6003107902433", name="Soccer", type="interests")]))
    assert live["potential_audience"] == {"lower": 900, "upper": 1100, "source": "meta_api"}
    assert live["scenarios"]["monthly"] == {"conservative": 3870, "mid": 4950, "upper": 7740}          # the LOWER end (900), not the middle
    assert live["meta_targeting"]["geo_key"] == "803" and live["meta_targeting"]["detailed"] == [{"id": "6003107902433", "name": "Soccer", "type": "interests"}]
    for kwargs in ({"meta_geo_type": "city", "meta_geo_key": "42", "audience_lower": 1, "audience_upper": 1},
                   {"meta_geo_type": "city"}, {"meta_targeting": [main.MetaTargetingItem(id="1", name="x", type="interests")]}):
        with pytest.raises(HTTPException) as error:
            main.constanza_meta_forecast(main.MetaAuctionForecastRequest(product_name=name, retail_budget=700, **kwargs))
        assert error.value.status_code == 422


def test_api_errors_carry_the_right_status_and_a_planner_readable_message(graph):
    graph.error, graph.http_status = {"message": "x", "code": 613}, 400
    with pytest.raises(HTTPException) as error:
        main.constanza_meta_forecast(main.MetaAuctionForecastRequest(product_name=AWARENESS, retail_budget=100, meta_geo_type="country", meta_geo_key="US"))
    assert error.value.status_code == 429 and "rate-limiting" in error.value.detail
    graph.error, graph.http_status = {"message": "bad", "code": 190}, 401
    with pytest.raises(HTTPException) as error:
        main.constanza_meta_geos(main.MetaGeoSearchRequest(query="Los Angeles", location_type="geo_market"))
    assert error.value.status_code == 503 and TOKEN not in error.value.detail


def test_the_targeting_routes_serve_lists_and_keyword_search(graph):
    assert [i["name"] for i in main.constanza_meta_targeting(kind="behaviors")["items"]][:1] == ["Frequent travelers"]
    assert main.constanza_meta_targeting(q="soccer")["results"]["interests"][0]["name"] == "Soccer"
    with pytest.raises(HTTPException) as bad:
        main.constanza_meta_targeting(kind="ethnic_affinity")
    assert bad.value.status_code == 422
    with pytest.raises(HTTPException) as short:
        main.constanza_meta_targeting(q="a")
    assert short.value.status_code == 422


def test_the_snapshot_round_trips_and_the_scenario_inserted_is_the_mid_one():
    result = meta.model_auction(product_name=AWARENESS, retail_budget=200, audience_lower=1000, audience_upper=1000)
    saved = main.AvailsEntry(meta_forecast=result, max_imps=result["scenarios"]["monthly"]["mid"], est_uniques=result["scenarios"]["est_uniques"]).model_dump()
    reopened = main.AvailsEntry(**saved)
    assert reopened.meta_forecast["scenarios"]["monthly"]["mid"] == 5500 and reopened.max_imps == 5500 and reopened.est_uniques == 1000
    assert round(reopened.max_imps / reopened.est_uniques, 1) == 5.5                                  # the Avails step's frequency is the mid multiplier


def test_the_catalog_still_marks_only_the_paid_skus_as_meta_lines():
    import asyncio
    products = {p["name"]: p for fam in asyncio.run(main.get_catalog())["products_by_family"].values() for p in fam}
    assert products[AWARENESS]["meta_retail_cpm"] == 12 and products["Facebook & Instagram Ads | Lead Gen / Calls"]["meta_retail_cpm"] == 35
    assert products["Facebook & Instagram Ads on EVC Radio Pages"]["meta_retail_cpm"] is None


# ---------------------------------------------------------------------------------------------------------------- the diagnostics

def test_the_diagnostics_walk_the_whole_connection(graph):
    from app.services import meta_diagnose
    checks = meta_diagnose.run_checks()
    names = [c.name for c in checks]
    assert all(c.ok for c in checks), [(c.name, c.detail) for c in checks if not c.ok]
    assert names[0] == "Settings" and any("ad account" in n for n in names) and any("TV market" in n for n in names) and any("interest" in n.lower() for n in names)
    assert any("Targeting list · behaviors" == n for n in names) and any("lower end x5.5" in c.detail for c in checks)
    blob = json.dumps([c.to_public() for c in checks])
    assert TOKEN not in blob and "7000000000012345" not in blob


def test_the_diagnostics_stop_with_a_clear_line_when_unconfigured_or_the_token_is_bad(monkeypatch, graph):
    from app.services import meta_diagnose
    graph.error, graph.http_status = {"message": "expired", "code": 190}, 401
    checks = meta_diagnose.run_checks()
    assert [c.ok for c in checks] == [True, False] and "new long-lived token" in checks[1].detail
    monkeypatch.delenv("META_ACCESS_TOKEN")
    checks = meta_diagnose.run_checks()
    assert len(checks) == 1 and not checks[0].ok and "META_ACCESS_TOKEN" in checks[0].detail


def test_a_bare_reach_object_is_read_like_the_data_list(monkeypatch, graph):
    class Bare:
        status_code, ok = 200, True
        def json(self):
            return {"users_lower_bound": 70, "users_upper_bound": 90, "estimate_ready": True}
    monkeypatch.setattr(meta.requests, "get", lambda *a, **k: Bare())
    assert meta.fetch_reach_estimate(geo_type="country", geo_key="CA") == (70, 90)
