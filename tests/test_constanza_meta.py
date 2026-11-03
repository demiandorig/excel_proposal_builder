"""Constanza x Meta: the audience-size forecast of the three paid Facebook & Instagram Ads SKUs, offline.

Meta's reach estimate is a head count (a range); Entravision's planning rule turns its LOWER end into impressions as conservative x 4.3,
mid x 5.5 (the default) and upper x 8.6 (platform_scenarios.py). Pinned here: those scenarios and the budget cross-check, the targeting spec
sent to Meta (geography, age, gender, interests / behaviors as ONE any-of group), Meta's interest / behavior / demographic lists, how Meta's errors
are told to the planner (never with the token), the secret handling, and the API round trip - against a fake Graph API shaped like Meta's reference.
Languages (Meta's `locales`, most campaigns target Spanish speakers) are pinned too: the spec, the search and its row shapes, the cache, the route,
the snapshot and the diagnostics. The locale ids below are INVENTED for the fake (the app never carries a locale id of its own; they come from Meta's search)."""
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
    """A Graph API shaped like Meta's reference: /search (adgeolocation, adinterest, adTargetingCategory, adlocale) and act_<id>/reachestimate."""

    def __init__(self):
        self.calls: list[dict] = []
        self.reach = (900, 1100)
        self.error: dict | None = None
        self.http_status = 200
        self.estimate_ready = True
        self.empty = False             # the lists answer with no rows (a transient empty answer must not stick)
        self.narrow_by_language = True   # a language narrows the reach estimate (False: the fake "ignores" locales, like a Meta that did not apply them)
        self.refuse_locales = False      # reachestimate refuses a spec that carries locales (Meta's "Invalid parameter")
        self.language_jitter = 0         # a spec with locales answers this much BELOW the same spec without them (0 = no change; use with narrow_by_language=False)
        self.plain_us_failures = 0       # the next N asks for the plain U.S. estimate (a country alone) fail with a transient error
        self.extra_demographics: list[dict] = []     # more rows in the demographics list (e.g. a long type or name)
        self.shape = "object"          # how reachestimate wraps its node: Meta's reference says "a single node" = one object ("list", "bare" also read)
        # adlocale rows: Meta's reference shows `key` (a number) and `name`; invented ids
        self.locales = [{"key": 6, "name": "English (US)"}, {"key": 24, "name": "English (UK)"}, {"key": 1001, "name": "Spanish"},
                        {"key": 1002, "name": "Spanish (Mexico)"}, {"key": 1003, "name": "Spanish (Spain)"}, {"key": 1004, "name": "Portuguese (Brazil)"}]

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
            if kind == "adlocale":
                q = (params.get("q") or "").lower()
                rows = [r for r in self.locales if not isinstance(r, dict) or str(r.get("name") or "").lower().startswith(q)]       # autocomplete: a prefix
                return Resp({"data": [] if self.empty else rows[:int(params.get("limit", 8))]})                                      # Meta's default limit is 8
            if kind == "adTargetingCategory" and self.empty:
                return Resp({"data": []})
            if kind == "adTargetingCategory":
                cls = params.get("class")
                rows = {"behaviors": [{"id": "6002714895372", "name": "Frequent travelers", "type": "behaviors", "audience_size_lower_bound": 2_000_000, "audience_size_upper_bound": 2_400_000,
                                       "path": ["Behaviors", "Travel", "Frequent travelers"]},
                                      {"id": "6004386044572", "name": "Small business owners", "type": "behaviors", "path": ["Behaviors", "Business", "Small business owners"]}],
                        "interests": [{"id": "6003107902433", "name": "Soccer", "type": "interests"}, {"id": "6003020834693", "name": "Cooking", "type": "interests"}],
                        "demographics": [{"id": "6002714398172", "name": "Newlyweds (1 year)", "type": "life_events", "path": ["Demographics", "Life events", "Newlywed"]},
                                         {"id": "6015559470583", "name": "Likely to engage with political content (US)", "type": "politics", "path": ["Demographics", "Politics"]},
                                         {"id": "6004854404172", "name": "iPhone 15", "type": "user_device", "path": ["Demographics", "Mobile Device User", "iPhone"]},
                                         {"id": "6006", "name": "No type at all"}]}[cls]
                return Resp({"data": rows + (self.extra_demographics if cls == "demographics" else [])})
        if rest.endswith("/reachestimate"):
            lo, hi = self.reach
            spec = json.loads(params["targeting_spec"])
            if spec == {"geo_locations": {"countries": ["US"]}} and self.plain_us_failures > 0:
                self.plain_us_failures -= 1
                return Resp({"error": {"message": "transient", "code": 1}}, 500)
            if spec.get("locales"):
                if self.refuse_locales:
                    return Resp({"error": {"message": "Invalid parameter", "code": 100}}, 400)
                if self.narrow_by_language:                                        # a language only narrows the audience
                    lo, hi = lo // 3, hi // 3
                lo, hi = lo - self.language_jitter, hi - self.language_jitter
            node = {"users_lower_bound": lo, "users_upper_bound": hi, "estimate_ready": self.estimate_ready}
            return Resp({"data": node if self.shape == "object" else ([node] if self.shape == "list" else None)} if self.shape != "bare" else node)
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
    assert call["url"] == "https://graph.facebook.com/v26.0/act_7000000000012345/reachestimate"        # the current Marketing API version, the account without act_ doubling
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
    with pytest.raises(meta.MetaAPIError, match="v26.0"):
        meta.fetch_reach_estimate(geo_type="country", geo_key="US", age_min=40)


@pytest.mark.parametrize("env", [{}, {"META_ACCESS_TOKEN": TOKEN}, {"META_AD_ACCOUNT_ID": ACCOUNT},
                                 {"META_ACCESS_TOKEN": "zzz spaced zzz", "META_AD_ACCOUNT_ID": ACCOUNT}, {"META_ACCESS_TOKEN": "tøken", "META_AD_ACCOUNT_ID": ACCOUNT},
                                 {"META_ACCESS_TOKEN": TOKEN, "META_AD_ACCOUNT_ID": "٧٠٠٠٠"}, {"META_ACCESS_TOKEN": TOKEN, "META_AD_ACCOUNT_ID": "my-account"}])
def test_unconfigured_calls_say_which_secret_is_missing(monkeypatch, env):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    missing = meta.missing_config()
    assert missing and not meta.is_configured()
    for call in (lambda: meta.fetch_reach_estimate(geo_type="country", geo_key="US"), lambda: meta.search_geography("Los Angeles", "geo_market"),
                 lambda: meta.targeting_catalog("interests"), lambda: meta.search_targeting("soccer"),
                 lambda: meta.search_locales(), lambda: meta.search_locales("Spanish"),
                 lambda: meta.fetch_reach_estimate(geo_type="country", geo_key="US", locales=[{"id": "1001", "name": "Spanish"}])):
        with pytest.raises(meta.MetaAPIError) as err:
            call()
        assert err.value.kind == "not_configured" and err.value.status_code == 503 and missing[0].split(" ")[0] in str(err.value) and "Replit" in str(err.value)
    blob = json.dumps(meta.status())
    assert TOKEN not in blob and "tøken" not in blob and "zzz spaced zzz" not in blob


def test_a_non_numeric_ad_account_is_a_setting_to_fix(monkeypatch, graph):
    monkeypatch.setenv("META_AD_ACCOUNT_ID", "my-account")
    with pytest.raises(meta.MetaAPIError, match="numeric"):
        meta.fetch_reach_estimate(geo_type="country", geo_key="US")


def test_the_status_never_carries_a_secret(monkeypatch, graph):
    out = main.constanza_meta_status()
    assert out["configured"] is True and out["missing"] == [] and out["account"] == "…2345" and out["version"] == "v26.0"
    assert out["scenarios"]["multipliers"] == {"conservative": 4.3, "mid": 5.5, "upper": 8.6}
    assert out["language_note"] == meta.LANGUAGE_NOTE
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
    ({"message": "Rate limit", "code": 613}, 400, "rate_limit", 429, "Wait a few minutes"),
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
    assert graph.calls[-1]["params"]["class"] == "demographics"
    # a row keeps its OWN type; one whose type cannot go in a targeting spec (user_device) or has none is left out, never relabelled life_events
    assert {d["name"]: d["type"] for d in demo} == {"Likely to engage with political content (US)": "politics", "Newlyweds (1 year)": "life_events"}
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
    assert [d["name"] for d in meta.search_targeting("iphone")["demographics"]] == []                    # not selectable: left out of the search too
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
    assert names.index("Language search") < names.index("Reach estimate · with a language") < names.index("Targeting list · interests") and names[-1] == "Language list"
    by_name = {c.name: c for c in checks}
    assert "Spanish" in by_name["Language search"].detail and "smaller than the whole U.S." in by_name["Reach estimate · with a language"].detail
    assert not any(c.warn for c in checks if c.name.startswith(("Language", "Reach estimate")))          # (the fake's short targeting lists are flagged, as before)
    search = next(c for c in graph.calls if c["params"].get("type") == "adlocale" and c["params"].get("q") == "Spanish")
    assert search["params"]["limit"] == 50
    asked = [json.loads(c["params"]["targeting_spec"]) for c in graph.calls if "targeting_spec" in c["params"]]
    assert {"geo_locations": {"countries": ["US"]}, "locales": [1001]} in asked                 # the estimate WITH the language the search found
    blob = json.dumps([c.to_public() for c in checks])
    assert TOKEN not in blob and "7000000000012345" not in blob


def test_the_language_checks_say_what_is_wrong_and_the_rest_still_run(graph):
    from app.services import meta_diagnose
    graph.locales = []                                                                    # Meta finds no language
    by_name = {c.name: c for c in meta_diagnose.run_checks()}
    assert not by_name["Language search"].ok and "found nothing" in by_name["Language search"].detail and "adlocale" in by_name["Language search"].detail
    assert not by_name["Reach estimate · with a language"].ok and "no language to ask about" in by_name["Reach estimate · with a language"].detail
    assert not by_name["Language list"].ok and "empty language list" in by_name["Language list"].detail
    assert by_name["Reach estimate · United States"].ok and by_name["Targeting list · behaviors"].ok          # nothing else is held back
    graph.locales = FakeGraph().locales
    meta.reset_cache()
    graph.refuse_locales = True                                                           # Meta answers a spec with locales: "Invalid parameter"
    by_name = {c.name: c for c in meta_diagnose.run_checks()}
    assert by_name["Language search"].ok and not by_name["Reach estimate · with a language"].ok
    assert "Meta refused these settings" in by_name["Reach estimate · with a language"].detail and by_name["Language list"].ok


def test_a_language_that_does_not_narrow_the_estimate_is_flagged_not_failed(graph):
    from app.services import meta_diagnose
    graph.narrow_by_language = False
    check = {c.name: c for c in meta_diagnose.run_checks()}["Reach estimate · with a language"]
    assert check.ok and check.warn and "may not be applying" in check.detail and check.to_public()["warn"] is True


def test_the_language_check_uses_the_exact_spanish_when_meta_lists_it_among_others(monkeypatch, graph):
    from app.services import meta_diagnose
    monkeypatch.setattr(meta, "search_locales", lambda q="": [{"id": "7", "name": "Latin American Spanish"}, {"id": "1001", "name": "Spanish"}])
    check = {c.name: c for c in meta_diagnose.run_checks()}["Language search"]
    assert "using: Spanish (locale 1001)" in check.detail


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


# ---------------------------------------------------------------------------------------------------------------- the round-3 audit fixes

@pytest.mark.parametrize("shape", ["object", "list", "bare"])
def test_the_reach_node_is_read_whether_data_is_one_object_a_one_item_list_or_the_bare_object(graph, shape):
    graph.shape = shape
    meta.reset_cache()
    assert meta.fetch_reach_estimate(geo_type="country", geo_key="US") == (900, 1100)


def test_a_reach_answer_whose_data_is_empty_is_a_clean_failure_not_a_crash(graph):
    graph.shape = "none"
    with pytest.raises(meta.MetaAPIError, match="did not return audience bounds"):
        meta.fetch_reach_estimate(geo_type="country", geo_key="US")


@pytest.mark.parametrize("error,kind,http,words", [
    ({"message": "Unsupported get request. Object with ID 'act_7000000000012345' does not exist, cannot be loaded due to missing permissions, or does not support this operation",
      "code": 100, "error_subcode": 33}, "permission", 503, "assign"),
    ({"message": "Calls to this api have exceeded the rate limit", "code": 80014}, "rate_limit", 429, "few minutes"),
    ({"message": "(#2635) You are calling a deprecated version of the Ads API. Please update to the latest version", "code": 2635}, "not_configured", 503, "META_GRAPH_API_VERSION"),
    ({"message": "Invalid appsecret_proof provided in the API argument", "code": 100}, "not_configured", 503, "META_APP_SECRET"),
])
def test_meta_config_problems_are_told_as_config_problems_not_as_a_targeting_mistake(graph, error, kind, http, words):
    graph.error, graph.http_status = error, 400
    with pytest.raises(meta.MetaAPIError) as err:
        meta.fetch_reach_estimate(geo_type="country", geo_key="US")
    assert err.value.kind == kind and err.value.status_code == http and words in str(err.value) and "geography" not in str(err.value)


def test_an_app_with_require_app_secret_gets_the_proof_and_the_secret_is_never_shown(monkeypatch, graph):
    import hashlib, hmac
    monkeypatch.setenv("META_APP_SECRET", "s3cr3t-app-secret")
    meta.fetch_reach_estimate(geo_type="country", geo_key="US")
    proof = graph.calls[-1]["params"]["appsecret_proof"]
    assert proof == hmac.new(b"s3cr3t-app-secret", TOKEN.encode(), hashlib.sha256).hexdigest()
    assert "s3cr3t-app-secret" not in json.dumps(graph.calls[-1])
    graph.error, graph.http_status = {"message": "echo s3cr3t-app-secret", "code": 100}, 400
    with pytest.raises(meta.MetaAPIError) as err:
        meta.fetch_reach_estimate(geo_type="country", geo_key="US", age_min=33)
    assert "s3cr3t-app-secret" not in str(err.value)
    monkeypatch.delenv("META_APP_SECRET")
    graph.error = None
    meta.reset_cache()
    meta.fetch_reach_estimate(geo_type="country", geo_key="US")
    assert "appsecret_proof" not in graph.calls[-1]["params"]


def test_an_empty_list_is_asked_again_but_a_real_one_is_remembered(graph):
    graph.empty = True
    assert meta.targeting_catalog("behaviors") == []
    graph.empty = False
    assert [i["name"] for i in meta.targeting_catalog("behaviors")] == ["Frequent travelers", "Small business owners"]
    n = len(graph.calls)
    meta.targeting_catalog("behaviors")
    assert len(graph.calls) == n


def test_a_null_data_list_is_an_empty_list_not_a_500(graph, monkeypatch):
    monkeypatch.setattr(graph, "get", lambda url, params=None, headers=None, timeout=None: Resp({"data": None}))
    monkeypatch.setattr(meta.requests, "get", graph.get)
    assert meta.search_geography("Los Angeles", "geo_market") == [] and meta.targeting_catalog("interests") == []
    assert meta.search_targeting("soccer")["interests"] == []


def test_the_flight_is_exactly_the_monthly_figure_shown_times_the_months():
    sc = meta.model_auction(product_name=AWARENESS, retail_budget=100, audience_lower=1233, months=7)["scenarios"]
    for k in ("conservative", "mid", "upper"):
        assert sc["flight"][k] == sc["monthly"][k] * 7


def test_a_bound_that_is_too_large_says_so_and_does_not_blame_the_order():
    with pytest.raises(ValueError, match="no larger than 1,000,000,000"):
        meta.model_auction(product_name=AWARENESS, retail_budget=100, audience_lower=1, audience_upper=2_000_000_000)


def test_the_geography_label_is_kept_with_the_snapshot(graph):
    live = main.constanza_meta_forecast(main.MetaAuctionForecastRequest(
        product_name=AWARENESS, retail_budget=700, meta_geo_type="geo_market", meta_geo_key="803", meta_geo_label="Los Angeles · California · US"))
    assert live["meta_targeting"]["geo_name"] == "Los Angeles · California · US"
    unlabeled = main.constanza_meta_forecast(main.MetaAuctionForecastRequest(product_name=AWARENESS, retail_budget=700, meta_geo_type="geo_market", meta_geo_key="803"))
    assert unlabeled["meta_targeting"]["geo_name"] is None


def test_the_diagnostics_name_every_documented_account_status(graph, monkeypatch):
    from app.services import meta_diagnose
    for raw, words, warns in ((1, "active", False), (8, "pending settlement", True), (100, "pending closure", True), (202, "any closed", True), (55, "status 55", True), (None, "status not given", False)):
        real = graph.get
        def get(url, params=None, headers=None, timeout=None, raw=raw):
            if url.endswith("/act_7000000000012345"):
                return Resp({"name": "A", "account_status": raw, "currency": "USD"})
            return real(url, params=params, headers=headers, timeout=timeout)
        monkeypatch.setattr(meta.requests, "get", get)
        check = meta_diagnose.run_checks()[1]
        assert check.ok and words in check.detail and check.warn is warns, (raw, check)


# ---------------------------------------------------------------------------------------------------------------- languages (Meta's `locales`)

SPANISH = {"id": "1001", "name": "Spanish"}
SPANISH_MX = {"id": "1002", "name": "Spanish (Mexico)"}
ENGLISH_US = {"id": "6", "name": "English (US)"}


def _spec_sent(graph) -> dict:
    return json.loads(graph.calls[-1]["params"]["targeting_spec"])


def test_languages_go_into_the_spec_as_plain_locale_ids_and_the_rest_of_the_spec_is_unchanged():
    base = meta.build_targeting_spec(geo_type="geo_market", geo_key="803", age_min=25, gender="female",
                                     targeting=[{"id": "6003107902433", "name": "Soccer", "type": "interests"}])
    spec = meta.build_targeting_spec(geo_type="geo_market", geo_key="803", age_min=25, gender="female",
                                     targeting=[{"id": "6003107902433", "name": "Soccer", "type": "interests"}], locales=[SPANISH_MX, SPANISH])
    assert spec["locales"] == [1001, 1002] and all(type(i) is int for i in spec["locales"])        # integers, in a stable order; ids only (the names are for display)
    assert {k: v for k, v in spec.items() if k != "locales"} == base                                 # geography, age, gender, interests exactly as before
    assert "Spanish" not in json.dumps(spec)
    for none in (None, [], ()):
        assert "locales" not in meta.build_targeting_spec(geo_type="country", geo_key="US", locales=none)


def test_chosen_languages_are_cleaned_for_display_and_a_duplicate_counts_once():
    assert meta.clean_locales(None) == [] and meta.clean_locales([]) == []
    cleaned = meta.clean_locales([{"id": 1001, "name": "  Spanish "}, {"id": "1001", "name": "Spanish again"}, {"id": "6", "name": "English (US)"}])
    assert cleaned == [{"id": "1001", "name": "Spanish"}, {"id": "6", "name": "English (US)"}]        # an int id is read, names are kept, the repeat is dropped
    assert meta.build_targeting_spec(geo_type="country", geo_key="US", locales=[SPANISH, SPANISH, ENGLISH_US])["locales"] == [6, 1001]


@pytest.mark.parametrize("locales", [
    "Spanish", {"id": "1001", "name": "Spanish"}, [["1001", "Spanish"]], ["1001"], [None],
    [{"id": "abc", "name": "x"}], [{"id": "", "name": "x"}], [{"id": None, "name": "x"}], [{"id": True, "name": "x"}], [{"id": 6.5, "name": "x"}],
    [{"id": "0", "name": "x"}], [{"id": "-5", "name": "x"}], [{"id": "01", "name": "x"}], [{"id": "12345678901", "name": "x"}], [{"id": "٦", "name": "x"}],
    [{"id": "6", "name": ""}], [{"id": "6", "name": "   "}], [{"id": "6"}], [{"id": "6", "name": "n" * 121}],
    [{"id": str(i), "name": "n"} for i in range(1, 27)],
])
def test_a_language_meta_would_refuse_is_refused_before_a_call(graph, locales):
    with pytest.raises(ValueError):
        meta.build_targeting_spec(geo_type="country", geo_key="US", locales=locales)
    with pytest.raises(ValueError):
        meta.fetch_reach_estimate(geo_type="country", geo_key="US", locales=locales)
    assert graph.calls == []


def test_up_to_the_maximum_number_of_languages_is_accepted_with_a_plain_message_beyond_it():
    full = [{"id": str(i), "name": f"Language {i}"} for i in range(1, meta.MAX_LOCALES + 1)]
    assert meta.build_targeting_spec(geo_type="country", geo_key="US", locales=full)["locales"] == list(range(1, meta.MAX_LOCALES + 1))
    with pytest.raises(ValueError, match=f"at most {meta.MAX_LOCALES} languages"):
        meta.clean_locales(full + [{"id": "999", "name": "One too many"}])


def test_the_live_reach_request_carries_the_locales_and_a_language_never_answers_for_another(graph):
    plain = meta.fetch_reach_estimate(geo_type="country", geo_key="US")
    spanish = meta.fetch_reach_estimate(geo_type="country", geo_key="US", locales=[SPANISH])
    assert plain == (900, 1100) and spanish == (300, 366)                                        # the fake narrows by a language: they are different answers
    assert _spec_sent(graph) == {"geo_locations": {"countries": ["US"]}, "locales": [1001]}
    assert len(graph.calls) == 2
    meta.fetch_reach_estimate(geo_type="country", geo_key="US", locales=[SPANISH])               # the same Spanish estimate: from memory
    assert len(graph.calls) == 2
    english = meta.fetch_reach_estimate(geo_type="country", geo_key="US", locales=[ENGLISH_US])  # another language: asked of Meta, not answered with Spanish's number
    assert len(graph.calls) == 3 and _spec_sent(graph)["locales"] == [6] and english == (300, 366)
    meta.fetch_reach_estimate(geo_type="country", geo_key="US", locales=[SPANISH, SPANISH_MX])
    meta.fetch_reach_estimate(geo_type="country", geo_key="US", locales=[SPANISH_MX, SPANISH])   # the same set in another order is the same question
    assert len(graph.calls) == 4 and _spec_sent(graph)["locales"] == [1001, 1002]
    assert TOKEN not in json.dumps(graph.calls[-1]["params"])


def test_a_spec_with_languages_and_interests_keeps_both(graph):
    meta.fetch_reach_estimate(geo_type="geo_market", geo_key="803", age_min=25, locales=[SPANISH],
                              targeting=[{"id": "6003107902433", "name": "Soccer", "type": "interests"}])
    assert _spec_sent(graph) == {"geo_locations": {"geo_markets": [{"key": "803"}]}, "age_min": 25, "locales": [1001],
                                 "flexible_spec": [{"interests": [{"id": "6003107902433", "name": "Soccer"}]}]}


# ---- the language search

def test_the_whole_language_list_is_one_blank_search_with_a_big_limit_cached_for_hours(graph):
    items = meta.search_locales()
    assert [i["name"] for i in items] == ["English (UK)", "English (US)", "Portuguese (Brazil)", "Spanish", "Spanish (Mexico)", "Spanish (Spain)"]
    assert items[1] == {"id": "6", "name": "English (US)"} and all(isinstance(i["id"], str) for i in items)    # the id is a string, the number Meta sends is read
    p = graph.calls[-1]["params"]
    assert p == {"type": "adlocale", "q": "", "limit": 1000}                                                   # Meta's documented way to list every locale
    assert graph.calls[-1]["url"] == "https://graph.facebook.com/v26.0/search" and graph.calls[-1]["headers"] == {"Authorization": f"Bearer {TOKEN}"}
    meta.search_locales()
    meta.search_locales("   ")                                                                                  # blank after trimming = the whole list
    assert len(graph.calls) == 1


def test_an_empty_language_list_is_asked_again_but_a_real_one_is_remembered(graph):
    graph.empty = True
    assert meta.search_locales() == []
    graph.empty = False
    assert len(meta.search_locales()) == 6
    n = len(graph.calls)
    meta.search_locales()
    assert len(graph.calls) == n


def test_a_keyword_search_asks_meta_for_that_text_sorted_by_name(graph):
    found = meta.search_locales(" Spanish ")
    assert [f["name"] for f in found] == ["Spanish", "Spanish (Mexico)", "Spanish (Spain)"]
    p = graph.calls[-1]["params"]
    assert p["type"] == "adlocale" and p["q"] == "Spanish" and p["limit"] == 50
    meta.search_locales("Spanish")
    assert len(graph.calls) == 2                                                                                # a keyword search is not remembered
    assert meta.search_locales("zzz") == []


@pytest.mark.parametrize("q", ["a", "x" * 61])
def test_a_language_search_text_must_be_two_to_sixty_characters(graph, q):
    with pytest.raises(ValueError, match="2–60"):
        meta.search_locales(q)
    assert graph.calls == []


def test_every_row_shape_meta_might_send_is_read_and_junk_is_dropped(graph):
    graph.locales = [
        {"key": 6, "name": "English (US)"},                          # Meta's reference shape: a numeric `key`
        {"id": "24", "name": "English (UK)"},                         # the number under `id`
        {"key": "1001", "name": "  Spanish "},                        # a numeric string, a name with padding
        {"key": None, "id": 5, "name": "German"},                     # `key` null: `id` is used
        {"key": 6, "name": "English (US) again"},                     # the same id twice: the first one is kept
        {"key": True, "name": "bool"}, {"key": 1.5, "name": "float"}, {"key": "abc", "name": "letters"}, {"key": 0, "name": "zero"},
        {"key": "007", "name": "leading zeros"}, {"key": -3, "name": "negative"}, {"key": [6], "name": "list"},
        {"key": 7}, {"key": 8, "name": ""}, {"key": 9, "name": None}, {"name": "no id"},
        "junk", None, 42, ["list"],
    ]
    assert meta.search_locales() == [{"id": "24", "name": "English (UK)"}, {"id": "6", "name": "English (US)"}, {"id": "5", "name": "German"},
                                     {"id": "1001", "name": "Spanish"}]


def test_a_language_list_that_is_empty_null_or_not_a_list_is_an_empty_list_not_a_500(graph, monkeypatch):
    for body in ({"data": []}, {"data": None}, {"data": {"key": 6, "name": "English (US)"}}, {"data": "English"}, {}):
        monkeypatch.setattr(meta.requests, "get", lambda url, params=None, headers=None, timeout=None, body=body: Resp(body))
        meta.reset_cache()
        assert meta.search_locales() == [] and meta.search_locales("Spanish") == []


def test_a_name_longer_than_a_selection_can_carry_is_shortened_so_it_can_still_be_chosen(graph):
    graph.locales = [{"key": 6, "name": "L" * 300}]
    [row] = meta.search_locales()
    assert len(row["name"]) == 120
    assert meta.clean_locales([row]) == [row]


def test_meta_errors_on_the_language_search_are_planner_readable(graph):
    graph.error, graph.http_status = {"message": "bad", "code": 190}, 401
    with pytest.raises(meta.MetaAPIError) as err:
        meta.search_locales()
    assert err.value.kind == "token" and err.value.status_code == 503 and TOKEN not in str(err.value)
    graph.error, graph.http_status = {"message": "x", "code": 613}, 400
    with pytest.raises(meta.MetaAPIError) as err:
        meta.search_locales("Spanish")
    assert err.value.kind == "rate_limit" and err.value.status_code == 429


def test_the_language_note_says_in_plain_words_what_a_meta_language_is():
    note = meta.LANGUAGE_NOTE
    assert "set Facebook or Instagram to" in note and "not a count of who speaks it" in note and "set to English is not included" in note
    assert "Leave languages empty" in note
    assert TOKEN not in note


# ---- the routes

def test_the_languages_route_serves_the_list_the_search_and_the_note(graph):
    out = main.constanza_meta_languages()
    assert out == {"items": meta.search_locales(), "note": meta.LANGUAGE_NOTE} and len(out["items"]) == 6
    assert out["items"][0] == {"id": "24", "name": "English (UK)"}
    found = main.constanza_meta_languages(q="Spanish")
    assert [i["id"] for i in found["items"]] == ["1001", "1002", "1003"] and found["note"] == meta.LANGUAGE_NOTE
    assert "meta-languages" in {getattr(r, "path", "").rsplit("/", 1)[-1] for r in main.app.router.routes if "GET" in (getattr(r, "methods", None) or ())}


@pytest.mark.parametrize("q", ["a", "x" * 61])
def test_the_languages_route_rejects_a_bad_search_text_with_422(graph, q):
    with pytest.raises(HTTPException) as bad:
        main.constanza_meta_languages(q=q)
    assert bad.value.status_code == 422 and "2–60" in bad.value.detail and graph.calls == []


def test_the_languages_route_maps_meta_errors_like_the_other_routes(graph):
    graph.error, graph.http_status = {"message": "x", "code": 613}, 400
    with pytest.raises(HTTPException) as limited:
        main.constanza_meta_languages()
    assert limited.value.status_code == 429 and "rate-limiting" in limited.value.detail
    graph.error, graph.http_status = {"message": f"bad {TOKEN}", "code": 190}, 401
    with pytest.raises(HTTPException) as token:
        main.constanza_meta_languages(q="Spanish")
    assert token.value.status_code == 503 and TOKEN not in token.value.detail


def test_the_languages_route_says_which_secret_is_missing_when_meta_is_not_connected():
    with pytest.raises(HTTPException) as missing:
        main.constanza_meta_languages()
    assert missing.value.status_code == 503 and "META_ACCESS_TOKEN" in missing.value.detail
    assert main.constanza_meta_status()["language_note"] == meta.LANGUAGE_NOTE and main.constanza_meta_status()["configured"] is False


def test_the_forecast_request_takes_languages_as_id_and_name_only():
    assert main.MetaAuctionForecastRequest(product_name=AWARENESS, retail_budget=1).meta_languages == []                  # omitted: nothing changes
    request = main.MetaAuctionForecastRequest(product_name=AWARENESS, retail_budget=1, meta_languages=[{"id": "1001", "name": "Spanish"}])
    assert request.meta_languages[0].model_dump() == {"id": "1001", "name": "Spanish"}
    with pytest.raises(Exception):
        main.MetaLanguageItem(id="1001", name="Spanish", extra="x")


def test_a_live_forecast_with_languages_sends_them_and_saves_them_with_the_snapshot(graph):
    live = main.constanza_meta_forecast(main.MetaAuctionForecastRequest(
        product_name=AWARENESS, retail_budget=700, meta_geo_type="geo_market", meta_geo_key="803", age_min=25,
        meta_languages=[main.MetaLanguageItem(id="1002", name="Spanish (Mexico)"), main.MetaLanguageItem(id="1001", name="Spanish")]))
    assert _spec_sent(graph)["locales"] == [1001, 1002] and _spec_sent(graph)["geo_locations"] == {"geo_markets": [{"key": "803"}]}
    assert live["potential_audience"] == {"lower": 300, "upper": 366, "source": "meta_api"}
    assert live["scenarios"]["monthly"] == {"conservative": 1290, "mid": 1650, "upper": 2580}                  # the lower end of the Spanish estimate (300)
    assert live["meta_targeting"]["languages"] == [{"id": "1002", "name": "Spanish (Mexico)"}, {"id": "1001", "name": "Spanish"}]        # as chosen, names kept
    saved = main.AvailsEntry(meta_forecast=live, max_imps=live["scenarios"]["monthly"]["mid"], est_uniques=300).model_dump()
    assert main.AvailsEntry(**saved).meta_forecast["meta_targeting"]["languages"][1] == {"id": "1001", "name": "Spanish"}


def test_a_live_forecast_without_languages_is_unchanged_except_for_an_empty_languages_list(graph):
    live = main.constanza_meta_forecast(main.MetaAuctionForecastRequest(
        product_name=AWARENESS, retail_budget=700, meta_geo_type="geo_market", meta_geo_key="803", age_min=25,
        meta_targeting=[main.MetaTargetingItem(id="6003107902433", name="Soccer", type="interests")]))
    assert "locales" not in _spec_sent(graph) and live["potential_audience"] == {"lower": 900, "upper": 1100, "source": "meta_api"}
    assert live["meta_targeting"] == {"geo_type": "geo_market", "geo_key": "803", "geo_name": None, "age_min": 25, "age_max": None, "gender": None,
                                      "detailed": [{"id": "6003107902433", "name": "Soccer", "type": "interests"}], "languages": []}
    # a proposal saved before languages existed has no `languages` key and still reopens
    old = {k: v for k, v in live["meta_targeting"].items() if k != "languages"}
    assert main.AvailsEntry(meta_forecast={**live, "meta_targeting": old}).meta_forecast["meta_targeting"] == old


def test_the_manual_audience_path_still_works_and_carries_no_targeting_snapshot(graph):
    manual = main.constanza_meta_forecast(main.MetaAuctionForecastRequest(product_name=AWARENESS, retail_budget=700, audience_lower=1000, audience_upper=1200))
    assert manual["potential_audience"]["source"] == "manual" and "meta_targeting" not in manual and graph.calls == []


def test_forecast_language_mistakes_are_422_before_any_call_to_meta(graph):
    def ask(**kwargs):
        with pytest.raises(HTTPException) as error:
            main.constanza_meta_forecast(main.MetaAuctionForecastRequest(product_name=AWARENESS, retail_budget=700, **kwargs))
        assert error.value.status_code == 422
        return error.value.detail
    assert "Languages need a live Meta pull" in ask(meta_languages=[{"id": "1001", "name": "Spanish"}])                                   # no geography
    assert "Use either the live Meta audience" in ask(meta_geo_type="country", meta_geo_key="US", audience_lower=5, meta_languages=[{"id": "1001", "name": "Spanish"}])
    assert "numeric Meta locale id" in ask(meta_geo_type="country", meta_geo_key="US", meta_languages=[{"id": "abc", "name": "Spanish"}])
    assert "numeric Meta locale id" in ask(meta_geo_type="country", meta_geo_key="US", meta_languages=[{"id": "1001", "name": ""}])
    assert f"at most {meta.MAX_LOCALES} languages" in ask(meta_geo_type="country", meta_geo_key="US",
                                                          meta_languages=[{"id": str(i), "name": "n"} for i in range(1, meta.MAX_LOCALES + 2)])
    assert graph.calls == []


def test_meta_refusing_a_language_estimate_is_a_planner_readable_422(graph):
    graph.refuse_locales = True
    with pytest.raises(HTTPException) as error:
        main.constanza_meta_forecast(main.MetaAuctionForecastRequest(
            product_name=AWARENESS, retail_budget=700, meta_geo_type="country", meta_geo_key="US", meta_languages=[{"id": "1001", "name": "Spanish"}]))
    assert error.value.status_code == 422 and "Meta refused these settings" in error.value.detail and TOKEN not in error.value.detail
    graph.refuse_locales, graph.error, graph.http_status = False, {"message": "x", "code": 613}, 400
    with pytest.raises(HTTPException) as limited:
        main.constanza_meta_forecast(main.MetaAuctionForecastRequest(
            product_name=AWARENESS, retail_budget=700, meta_geo_type="country", meta_geo_key="US", meta_languages=[{"id": "1001", "name": "Spanish"}]))
    assert limited.value.status_code == 429


# ---------------------------------------------------------------------------------------------------------------- every pick the lists offer can be sent back

def test_every_targeting_key_fits_the_request_model():
    for key in meta.TARGETING_KEYS:
        item = main.MetaTargetingItem(id="6023005404983", name="n" * meta.MAX_NAME_LENGTH, type=key)        # no key is longer than the model allows
        assert item.type == key
    longest = max(meta.TARGETING_KEYS, key=len)
    assert meta.MAX_TARGETING_TYPE_LENGTH == len(longest) >= len("household_composition")               # the limit IS the longest key, not a number typed in beside it
    assert main.MetaTargetingItem.model_fields["type"].metadata[0].max_length == meta.MAX_TARGETING_TYPE_LENGTH
    with pytest.raises(Exception):
        main.MetaTargetingItem(id="1", name="n", type="x" * (meta.MAX_TARGETING_TYPE_LENGTH + 1))      # a made-up long type is still refused
    # and the language items and request lists carry what the service takes
    assert main.MetaLanguageItem(id="1001", name="n" * meta.MAX_NAME_LENGTH).name == "n" * meta.MAX_NAME_LENGTH


def test_a_long_name_is_shortened_in_the_lists_so_the_row_can_still_be_chosen(graph):
    graph.extra_demographics = [{"id": "6023005404983", "name": "D" * 300, "type": "household_composition", "path": ["Demographics", "Household composition"]}]
    row = next(i for i in meta.targeting_catalog("demographics") if i["type"] == "household_composition")
    assert len(row["name"]) == meta.MAX_NAME_LENGTH == 120
    picked = {"id": row["id"], "name": row["name"], "type": row["type"]}
    assert meta.build_targeting_spec(geo_type="country", geo_key="US", targeting=[picked])["flexible_spec"] == [{"household_composition": [{"id": row["id"], "name": row["name"]}]}]
    assert main.MetaTargetingItem(**picked).name == row["name"]                                       # the request model takes it too
    graph.reach = (900, 1100)


def test_a_row_without_a_numeric_id_is_left_out_of_the_lists_because_a_pick_could_not_carry_it(graph):
    graph.extra_demographics = [{"id": "not-a-number", "name": "Bad id", "type": "life_events"}, {"id": "1" * 31, "name": "Too long id", "type": "life_events"},
                                {"id": " 6012345 ", "name": "Padded id", "type": "life_events"}]
    names = {i["name"]: i["id"] for i in meta.targeting_catalog("demographics")}
    assert "Bad id" not in names and "Too long id" not in names and names["Padded id"] == "6012345"


@pytest.fixture
def client(monkeypatch):
    """The real app over HTTP (the request model, the routes, the 422 answers) with a signed-in session and no database."""
    monkeypatch.setattr(main.auth_svc, "get_user_by_session", lambda token: {"id": 1, "email": "planner@example.com", "is_admin": False})
    monkeypatch.setattr(main.auth_svc, "refresh_session", lambda token: None)
    try:
        from fastapi.testclient import TestClient
        return TestClient(main.app, cookies={main.SESSION_COOKIE_NAME: "test-session"})
    except (ImportError, RuntimeError) as exc:                                                    # Starlette's test client needs httpx2 (or httpx) installed
        pytest.skip(f"no HTTP test client here: {exc}")


def test_picking_a_household_demographic_is_estimated_over_http_not_refused_with_a_422(graph, client):
    graph.extra_demographics = [{"id": "6023005404983", "name": "Parents (all)", "type": "household_composition", "path": ["Demographics", "Household composition"],
                                 "audience_size_lower_bound": 1000, "audience_size_upper_bound": 2000}]
    listed = client.get("/api/constanza/meta-targeting", params={"kind": "demographics"})
    assert listed.status_code == 200
    pick = next(i for i in listed.json()["items"] if i["type"] == "household_composition")             # the browse list offers it...
    body = {"product_name": AWARENESS, "retail_budget": 2000, "retail_cpm": 12, "months": 1, "meta_geo_type": "geo_market", "meta_geo_key": "803",
            "meta_geo_label": "Los Angeles", "gender": "all", "meta_languages": [],
            "meta_targeting": [{"id": pick["id"], "name": pick["name"], "type": pick["type"]}]}
    answer = client.post("/api/constanza/meta-forecast", json=body)                                    # ...and the estimate accepts it
    assert answer.status_code == 200, answer.json()
    assert _spec_sent(graph)["flexible_spec"] == [{"household_composition": [{"id": "6023005404983", "name": "Parents (all)"}]}]
    assert answer.json()["meta_targeting"]["detailed"] == [{"id": "6023005404983", "name": "Parents (all)", "type": "household_composition"}]


def test_every_kind_of_pick_is_accepted_by_the_route_over_http(graph, client):
    for key in meta.TARGETING_KEYS:
        body = {"product_name": AWARENESS, "retail_budget": 2000, "meta_geo_type": "country", "meta_geo_key": "US",
                "meta_targeting": [{"id": "6023005404983", "name": "A pick", "type": key}]}
        answer = client.post("/api/constanza/meta-forecast", json=body)
        assert answer.status_code == 200, (key, answer.json())


def test_a_refused_field_is_a_422_that_names_the_field_so_the_panel_can_say_which_pick(graph, client):
    body = {"product_name": AWARENESS, "retail_budget": 2000, "meta_geo_type": "country", "meta_geo_key": "US",
            "meta_targeting": [{"id": "1", "name": "ok", "type": "interests"}, {"id": "2", "name": "n" * 121, "type": "interests"}]}
    answer = client.post("/api/constanza/meta-forecast", json=body)
    assert answer.status_code == 422
    assert answer.json()["detail"][0]["loc"] == ["body", "meta_targeting", 1, "name"] and graph.calls == []


# ---------------------------------------------------------------------------------------------------------------- the language diagnostics

def _checks(graph=None):
    from app.services import meta_diagnose
    return {c.name: c for c in meta_diagnose.run_checks()}


LANGUAGE = "Reach estimate · with a language"
ENGLISH = "Reach estimate · English, a common language (information)"


def test_the_language_check_prints_what_it_compared_the_language_figure_next_to_the_plain_us_one(graph):
    check = _checks()[LANGUAGE]
    assert check.ok and not check.warn
    assert "U.S. accounts set to Spanish: 300 – 366" in check.detail and "against 900 – 1,100 for the whole U.S." in check.detail
    assert "smaller than the whole U.S." in check.detail and "33.3% of it" in check.detail


def test_a_language_figure_only_a_little_below_the_us_figure_is_not_taken_as_proof(graph):
    graph.narrow_by_language, graph.language_jitter, graph.reach = False, 1000, (240_000_000, 241_000_000)      # Meta ignored `locales`; the figure moved by rounding
    check = _checks()[LANGUAGE]
    assert check.ok and check.warn and "not clearly smaller" in check.detail and "may not be applying" in check.detail and "100.0% of it" in check.detail
    graph.language_jitter = 0
    meta.reset_cache()
    assert _checks()[LANGUAGE].warn                                                               # the very same figure as the U.S.: flagged too
    graph.narrow_by_language = True
    meta.reset_cache()
    assert not _checks()[LANGUAGE].warn                                                           # a real drop is the result the check is looking for


def test_a_language_that_takes_nine_tenths_or_more_of_the_us_is_flagged_and_one_clearly_below_is_not(graph):
    from app.services import meta_diagnose
    assert meta_diagnose.LANGUAGE_NARROWS_BELOW == 0.9
    graph.narrow_by_language, graph.reach = False, (1000, 1200)
    graph.language_jitter = 99                                                                    # 901 of 1000: 90.1%
    assert _checks()[LANGUAGE].warn
    graph.language_jitter = 101                                                                   # 899 of 1000: 89.9%
    meta.reset_cache()
    assert not _checks()[LANGUAGE].warn


def test_without_a_plain_us_figure_the_language_check_says_it_could_not_compare_and_claims_nothing(graph):
    graph.plain_us_failures = 99                                                                  # the plain U.S. estimate fails every time
    checks = _checks()
    assert not checks["Reach estimate · United States"].ok
    for narrows in (True, False):                                                                 # whether or not Meta applied the language
        graph.narrow_by_language = narrows
        meta.reset_cache()
        graph.plain_us_failures = 99
        check = _checks()[LANGUAGE]
        assert check.ok and check.warn and "not verified" in check.detail and "no plain U.S. figure to compare with" in check.detail
        assert "smaller than the whole U.S." not in check.detail and "narrowed the estimate" not in check.detail


def test_a_plain_us_estimate_that_failed_once_is_asked_again_for_the_language_check(graph):
    graph.plain_us_failures = 1                                                                   # the check above fails; asking once more works
    checks = _checks()
    assert not checks["Reach estimate · United States"].ok
    assert checks[LANGUAGE].ok and not checks[LANGUAGE].warn and "against 900 – 1,100 for the whole U.S." in checks[LANGUAGE].detail


def test_the_english_line_reports_what_meta_did_next_to_the_plain_us_figure(graph):
    checks = _checks()
    names = list(checks)
    assert names.index(LANGUAGE) < names.index(ENGLISH) < names.index("Targeting list · interests")
    english = checks[ENGLISH]
    assert english.ok and not english.warn
    assert "U.S. accounts set to English (US): 300 – 366" in english.detail and "against 900 – 1,100 for the whole U.S." in english.detail
    assert "narrowed the estimate for English too" in english.detail and "For information only" in english.detail
    asked = [json.loads(c["params"]["targeting_spec"]) for c in graph.calls if "targeting_spec" in c["params"]]
    assert {"geo_locations": {"countries": ["US"]}, "locales": [6]} in asked                      # English (US), exactly as Meta's list names it


def test_the_english_line_says_so_when_meta_did_not_narrow_and_names_the_documented_qualifier_but_never_warns(graph):
    graph.narrow_by_language = False
    english = _checks()[ENGLISH]
    assert english.ok and not english.warn and english.to_public()["warn"] is False
    assert "no clear narrowing" in english.detail and "100.0% of it" in english.detail
    assert "a language other than the common one for a location" in english.detail


def test_the_english_line_never_fails_the_run_even_when_meta_will_not_answer_it(monkeypatch, graph, capsys):
    from app.services import meta_diagnose
    real = meta.search_locales

    def search(q=""):
        if q == "English":
            raise meta.MetaAPIError("Meta is rate-limiting this ad account.", "rate_limit", 429)
        return real(q)
    monkeypatch.setattr(meta, "search_locales", search)
    english = _checks()[ENGLISH]
    assert english.ok and not english.warn and "Could not ask" in english.detail and "does not change the result of the run" in english.detail
    assert meta_diagnose.main([]) == 0                                                            # every other line passed: the exit status is 0
    assert "✓ Reach estimate · English" in capsys.readouterr().out
    graph.locales = [r for r in graph.locales if not r["name"].startswith("English")]             # Meta lists no English at all
    monkeypatch.setattr(meta, "search_locales", real)
    meta.reset_cache()
    english = _checks()[ENGLISH]
    assert english.ok and not english.warn and "no language called English" in english.detail


# ---------------------------------------------------------------------------------------------------------------- the words around the manual path and the docs

def test_the_route_and_the_env_example_no_longer_invite_a_typed_audience():
    doc = main.constanza_meta_forecast.__doc__
    assert "may enter the audience" not in doc and "predates the panel" in doc
    from pathlib import Path
    env = (Path(__file__).resolve().parents[1] / ".env.example").read_text(encoding="utf-8")
    assert "manually entered audience" not in env and "Meta isn't connected yet" in env and "Avails fields" in env
    with pytest.raises(HTTPException) as both:                                                    # the backend path itself stays: old callers and old snapshots
        main.constanza_meta_forecast(main.MetaAuctionForecastRequest(product_name=AWARENESS, retail_budget=700, meta_geo_type="country", meta_geo_key="US", audience_lower=5))
    assert "Use either the live Meta audience" in both.value.detail and "manually" not in both.value.detail


def test_the_docs_say_languages_are_ored_without_also_saying_a_language_can_only_shrink_the_audience():
    from pathlib import Path
    docs = (Path(__file__).resolve().parents[1] / "docs" / "forecast_data_platform.md").read_text(encoding="utf-8")
    assert "adding a language can only make the audience smaller" not in docs
    assert "the first language narrows the audience compared with none, and each further language widens it again" in docs.replace("\n", " ")
    assert "can only narrow the audience\n    compared with none" not in meta.build_targeting_spec.__doc__ and "each further one widens it again" in meta.build_targeting_spec.__doc__.replace("\n", " ")
    # the unverified qualifier from Meta's own reference is written down next to the check that answers it
    assert "differs from the common" in docs and "Reach estimate · English, a common language" in docs
