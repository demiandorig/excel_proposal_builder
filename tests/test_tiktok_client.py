"""The TikTok Marketing API client (app/services/tiktok_client.py), against an in-memory fake of TikTok's documented API
(tests/tiktok_support.py): configuration, how a request is made, retries, caching, error classification, and that the access token
never leaks."""
from __future__ import annotations

import json
import logging

import pytest
import requests

from app.services import tiktok_client as tc
from app.services.tiktok_client import TikTokAPIError
from tests.tiktok_support import ADVERTISER, GOOD_TOKEN, FakeResponse, FakeTikTok, fail, make_client, ok, timeout_error

ESTIMATE = "/ad/audience_size/estimate/"
US_BODY = {"objective_type": "REACH", "optimization_goal": "REACH", "placement_type": "PLACEMENT_TYPE_NORMAL",
           "placements": ["PLACEMENT_TIKTOK"], "location_ids": ["6252001"]}


class Clock:
    """A clock a test moves by hand."""
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("TIKTOK_ACCESS_TOKEN", "TIKTOK_ADVERTISER_ID", "TIKTOK_API_BASE", "TIKTOK_IMPRESSIONS_PER_PERSON",
                 "TIKTOK_OBJECTIVE_TYPE", "TIKTOK_OPTIMIZATION_GOAL"):
        monkeypatch.delenv(name, raising=False)
    tc.reset_client()
    yield
    tc.reset_client()


# --------------------------------------------------------------------------------------------- configuration

def test_not_configured_until_both_the_token_and_the_ad_account_are_set(monkeypatch):
    assert not tc.is_configured() and tc.missing_config() == ["TIKTOK_ACCESS_TOKEN", "TIKTOK_ADVERTISER_ID"]
    monkeypatch.setenv("TIKTOK_ACCESS_TOKEN", "abc")
    assert tc.missing_config() == ["TIKTOK_ADVERTISER_ID"]
    monkeypatch.setenv("TIKTOK_ADVERTISER_ID", "7000000000000000001")
    assert tc.is_configured() and tc.missing_config() == []


@pytest.mark.parametrize("value", ["abc", "12", "70000 00000", "7000000000000000001x", "-7000000000"])
def test_an_ad_account_id_that_is_not_numeric_counts_as_missing(monkeypatch, value):
    monkeypatch.setenv("TIKTOK_ACCESS_TOKEN", "abc")
    monkeypatch.setenv("TIKTOK_ADVERTISER_ID", value)
    assert not tc.is_configured() and any("TIKTOK_ADVERTISER_ID" in m for m in tc.missing_config())


def test_blank_secrets_are_the_same_as_missing(monkeypatch):
    monkeypatch.setenv("TIKTOK_ACCESS_TOKEN", "   ")
    monkeypatch.setenv("TIKTOK_ADVERTISER_ID", "")
    assert not tc.is_configured()


def test_the_ad_account_is_only_ever_shown_by_its_last_four_digits(monkeypatch):
    monkeypatch.setenv("TIKTOK_ADVERTISER_ID", "7000000000000001234")
    assert tc.advertiser_hint() == "…1234" and "7000" not in tc.advertiser_hint()


@pytest.mark.parametrize("raw,expected", [("", 5.5), ("6", 6.0), ("4.25", 4.25), ("abc", 5.5), ("0", 5.5), ("-3", 5.5), ("1000", 5.5), ("nan", 5.5)])
def test_the_impressions_per_person_factor_is_5_5_unless_a_sensible_number_is_set(monkeypatch, raw, expected):
    if raw:
        monkeypatch.setenv("TIKTOK_IMPRESSIONS_PER_PERSON", raw)
    assert tc.impressions_per_person() == expected


def test_the_shared_client_needs_the_secrets_and_is_rebuilt_when_they_change(monkeypatch):
    with pytest.raises(TikTokAPIError) as err:
        tc.get_client()
    assert err.value.kind == "not_configured" and err.value.status_code == 503
    monkeypatch.setenv("TIKTOK_ACCESS_TOKEN", "t1")
    monkeypatch.setenv("TIKTOK_ADVERTISER_ID", "7000000000000000001")
    first = tc.get_client()
    assert tc.get_client() is first
    monkeypatch.setenv("TIKTOK_ACCESS_TOKEN", "t2")
    assert tc.get_client() is not first


def test_the_api_base_can_be_pointed_elsewhere_and_loses_a_trailing_slash(monkeypatch):
    assert tc.api_base() == "https://business-api.tiktok.com/open_api/v1.3"
    monkeypatch.setenv("TIKTOK_API_BASE", "http://127.0.0.1:9/open_api/v1.3/")
    assert tc.api_base() == "http://127.0.0.1:9/open_api/v1.3"


# --------------------------------------------------------------------------------------------- how a request is made

def test_every_request_carries_the_token_in_the_access_token_header_and_nowhere_else():
    c = make_client()
    c.estimate_audience(US_BODY)
    call = c.fake.calls[-1]
    assert call["headers"]["Access-Token"] == GOOD_TOKEN and call["headers"]["Content-Type"] == "application/json"
    assert GOOD_TOKEN not in json.dumps(call["body"]) and GOOD_TOKEN not in json.dumps(call["params"])


def test_the_estimate_is_a_post_of_the_targeting_plus_the_advertiser():
    c = make_client()
    size = c.estimate_audience(US_BODY)
    call = c.fake.calls[-1]
    assert call["method"] == "POST" and call["path"] == ESTIMATE
    assert call["body"]["advertiser_id"] == ADVERTISER and call["body"]["location_ids"] == ["6252001"]
    assert "advertiser_id" not in US_BODY                       # the caller's dict is not changed
    assert size.lower < size.mid < size.upper and size.stage == 4 and size.request_id and not size.cached


def test_list_parameters_of_a_get_are_sent_as_json_like_tiktoks_own_examples():
    c = make_client()
    c.targeting_list(["general_interest", "video_interaction"])
    call = c.fake.calls[-1]
    assert call["method"] == "GET" and call["path"] == "/targeting/search/"
    assert json.loads(call["params"]["sub_targeting_types"]) == ["GENERAL_INTEREST", "VIDEO_INTERACTION"]
    assert call["params"]["advertiser_id"] == ADVERTISER and call["params"]["targeting_type"] == "INTEREST_AND_BEHAVIOR"


def test_the_repr_and_the_logs_never_show_the_token(caplog):
    c = make_client()
    assert GOOD_TOKEN not in repr(c) and ADVERTISER not in repr(c) and ADVERTISER[-4:] in repr(c)
    caplog.set_level(logging.DEBUG)
    c.estimate_audience(US_BODY)
    c.fake.queue(ESTIMATE, FakeResponse(200, fail(40105, "bad token")))
    with pytest.raises(TikTokAPIError):
        c.estimate_audience({**US_BODY, "gender": "GENDER_MALE"})
    assert GOOD_TOKEN not in caplog.text


# --------------------------------------------------------------------------------------------- errors

@pytest.mark.parametrize("code,kind,status", [
    (40105, "auth", 502), (40102, "auth", 502), (40104, "auth", 502), (40106, "auth", 502),
    (40001, "permission", 502), (40118, "permission", 502), (40125, "permission", 502),
    (40016, "rate_limit", 429), (40100, "rate_limit", 429), (40133, "rate_limit", 429),
    (40300, "config", 503), (40301, "config", 503),
    (40000, "invalid", 422), (40002, "invalid", 422), (40065, "invalid", 422), (40700, "invalid", 422),
    (51003, "unavailable", 502), (50000, "unavailable", 502), (49999, "invalid", 422), (60001, "unavailable", 502), (41001, "unavailable", 502),
    (41000, "permission", 502), (70000, "unexpected", 502),
])
def test_every_return_code_is_classified_by_what_to_do_about_it(code, kind, status):
    assert tc._classify(code) == kind
    assert TikTokAPIError("m", kind=kind).status_code == status


def test_an_auth_failure_is_not_retried_and_says_the_token_needs_replacing():
    c = make_client(FakeTikTok(token="another-token"))
    with pytest.raises(TikTokAPIError) as err:
        c.estimate_audience(US_BODY)
    e = err.value
    assert e.kind == "auth" and e.code == 40105 and c.fake.count(ESTIMATE) == 1 and c.sleeps == []
    assert "TIKTOK_ACCESS_TOKEN" in e.public_message() and GOOD_TOKEN not in e.public_message()


def test_an_invalid_request_is_not_retried_and_shows_tiktoks_own_words():
    c = make_client()
    with pytest.raises(TikTokAPIError) as err:
        c.estimate_audience({**US_BODY, "location_ids": ["6252001", "5000003"]})
    e = err.value
    assert e.kind == "invalid" and e.status_code == 422 and c.fake.count(ESTIMATE) == 1
    assert "Overlapping locations" in e.public_message()


def test_a_wrong_ad_account_is_a_configuration_problem_not_a_planner_problem():
    c = make_client(advertiser="7999999999999999999")
    with pytest.raises(TikTokAPIError) as err:
        c.languages()
    assert err.value.kind == "config" and err.value.status_code == 503 and "ad account" in err.value.public_message()


def test_not_configured_says_what_to_add_and_never_asks_for_a_value_in_the_message(monkeypatch):
    e = tc.not_configured_error()
    msg = e.public_message()
    assert "TIKTOK_ACCESS_TOKEN" in msg and "TIKTOK_ADVERTISER_ID" in msg and "secrets" in msg and e.status_code == 503


# --------------------------------------------------------------------------------------------- retries

def test_a_timeout_is_retried_and_then_succeeds_with_a_pause_between():
    c = make_client()
    c.fake.queue(ESTIMATE, timeout_error())
    size = c.estimate_audience(US_BODY)
    assert size.mid > 0 and c.fake.count(ESTIMATE) == 2 and c.sleeps == [tc.NETWORK_BACKOFF[0]]


def test_a_server_error_is_retried_and_after_the_last_try_it_is_reported_as_unavailable():
    c = make_client()
    c.fake.queue(ESTIMATE, FakeResponse(502, "<html>Bad gateway</html>"), FakeResponse(503, "<html>x</html>"), FakeResponse(502, "<html>y</html>"))
    with pytest.raises(TikTokAPIError) as err:
        c.estimate_audience(US_BODY)
    assert err.value.kind == "unavailable" and err.value.status_code == 502 and c.fake.count(ESTIMATE) == 1 + tc.MAX_RETRIES
    assert c.sleeps == [tc.NETWORK_BACKOFF[0], tc.NETWORK_BACKOFF[1]]


def test_a_connection_that_never_comes_back_is_reported_without_the_url_or_token():
    c = make_client()
    c.fake.queue(ESTIMATE, *[timeout_error() for _ in range(3)])
    with pytest.raises(TikTokAPIError) as err:
        c.estimate_audience(US_BODY)
    assert err.value.kind == "unavailable" and "could not reach TikTok" in str(err.value) and GOOD_TOKEN not in str(err.value)


def test_tiktoks_rate_limit_is_waited_out_longer_and_retried():
    c = make_client()
    c.fake.queue(ESTIMATE, FakeResponse(200, fail(40100, "Requests made too frequently")), FakeResponse(200, fail(40133, "slow down")))
    assert c.estimate_audience(US_BODY).mid > 0
    assert c.sleeps == [tc.RATE_LIMIT_BACKOFF[0], tc.RATE_LIMIT_BACKOFF[1]]


def test_a_rate_limit_that_does_not_clear_is_reported_as_one():
    c = make_client()
    c.fake.queue(ESTIMATE, *[FakeResponse(200, fail(40100, "x")) for _ in range(3)])
    with pytest.raises(TikTokAPIError) as err:
        c.estimate_audience(US_BODY)
    assert err.value.kind == "rate_limit" and err.value.status_code == 429 and "wait a few seconds" in err.value.public_message()


def test_http_429_is_a_rate_limit_too():
    c = make_client()
    c.fake.queue(ESTIMATE, FakeResponse(429, {"code": 40100, "message": "slow", "request_id": "r"}))
    assert c.estimate_audience(US_BODY).mid > 0 and c.sleeps == [tc.RATE_LIMIT_BACKOFF[0]]


def test_tiktok_service_error_codes_5xxxx_are_retried_once_more_and_then_reported():
    c = make_client()
    c.fake.queue(ESTIMATE, FakeResponse(200, fail(51003, "internal")), FakeResponse(200, fail(51003, "internal")), FakeResponse(200, fail(51003, "internal")))
    with pytest.raises(TikTokAPIError) as err:
        c.estimate_audience(US_BODY)
    assert err.value.kind == "unavailable" and err.value.code == 51003


def test_an_answer_that_is_not_json_or_has_no_code_is_unexpected_and_never_a_zero():
    c = make_client()
    c.fake.queue(ESTIMATE, FakeResponse(200, "<html>captive portal</html>"))
    with pytest.raises(TikTokAPIError) as err:
        c.estimate_audience(US_BODY)
    assert err.value.kind == "unexpected"
    c.fake.queue(ESTIMATE, FakeResponse(200, {"hello": "world"}))
    with pytest.raises(TikTokAPIError) as err2:
        c.estimate_audience({**US_BODY, "gender": "GENDER_FEMALE"})
    assert err2.value.kind == "unexpected"


# --------------------------------------------------------------------------------------------- caching and spacing

def test_an_identical_estimate_is_answered_from_memory_for_ten_minutes():
    clock = Clock()
    c = make_client(clock=clock)
    first = c.estimate_audience(US_BODY)
    second = c.estimate_audience(dict(US_BODY))
    assert c.fake.count(ESTIMATE) == 1 and not first.cached and second.cached and second.mid == first.mid
    clock.now += tc.TTL_ESTIMATE - 1
    assert c.estimate_audience(US_BODY).cached
    clock.now += 2
    assert not c.estimate_audience(US_BODY).cached and c.fake.count(ESTIMATE) == 2


def test_a_different_targeting_is_a_different_estimate():
    c = make_client()
    a = c.estimate_audience(US_BODY)
    b = c.estimate_audience({**US_BODY, "gender": "GENDER_FEMALE"})
    assert c.fake.count(ESTIMATE) == 2 and b.mid < a.mid


def test_a_failed_call_is_never_cached():
    c = make_client()
    c.fake.queue(ESTIMATE, FakeResponse(200, fail(40002, "bad")))
    with pytest.raises(TikTokAPIError):
        c.estimate_audience(US_BODY)
    assert c.estimate_audience(US_BODY).mid > 0 and c.fake.count(ESTIMATE) == 2


def test_catalogs_are_cached_for_hours_and_refetched_after():
    clock = Clock()
    c = make_client(clock=clock)
    assert c.languages() == c.languages() and c.fake.count("/tool/language/") == 1
    clock.now += tc.TTL_CATALOG + 1
    c.languages()
    assert c.fake.count("/tool/language/") == 2


def test_calls_are_spaced_by_the_minimum_interval():
    clock = Clock()
    waited = []

    def sleep(seconds):
        waited.append(round(seconds, 3))
        clock.now += seconds

    c = make_client(clock=clock, min_interval=0.5)
    c._sleep = sleep
    c._throttle._sleep = sleep
    c.languages()
    c.targeting_list(["general_interest"])
    c.targeting_search("auto")
    assert waited == [0.5, 0.5]


# --------------------------------------------------------------------------------------------- reading the answers

def test_the_language_list_is_code_and_name():
    langs = make_client().languages()
    assert {"code": "es", "name": "Spanish"} in langs and all(set(l) == {"code", "name"} for l in langs)


def test_the_category_lists_carry_ids_names_levels_and_children():
    lists = make_client().targeting_list()
    gen = {i["id"]: i for i in lists["general_interest"]}
    assert gen["10000"]["name"] == "Apparel & Accessories" and gen["10000"]["children_ids"] == ["10100", "10200"] and gen["10100"]["level"] == 2
    assert [i["name"] for i in lists["hashtag_interaction"]] == ["cybermonday", "christmas"]
    assert set(lists) == set(tc.LIST_KINDS)


def test_a_keyword_search_returns_each_kind_it_found_something_in():
    found = make_client().targeting_search("auto")
    assert {i["name"] for i in found["additional_interest"]} == {"Auto Racing", "Auto Repair", "Auto Insurance"}
    assert any(i["name"] == "Cars & Trucks" for i in found["general_interest"]) is False        # "auto" is not in that name
    assert found["hashtag_interaction"] and found["hashtag_interaction"][0]["name"].startswith("#auto")
    assert make_client().targeting_search("   ") == {}


def test_a_search_can_be_limited_to_some_kinds():
    c = make_client()
    found = c.targeting_search("sports", ["general_interest", "creator_interaction"])
    assert set(found) == {"general_interest", "creator_interaction"}
    assert json.loads(c.fake.calls[-1]["params"]["sub_targeting_types"]) == ["GENERAL_INTEREST", "CREATOR_INTERACTION"]


def test_location_tags_are_normalized_and_disabled_ones_are_marked():
    c = make_client()
    c.fake.queue("/tool/targeting/search/", FakeResponse(200, ok({"targeting_tag_list": [
        {"keyword": "x", "name": "Off", "geo": {"geo_id": "1", "geo_type": "DMA", "parent_id": "0", "region_code": "US"}, "status_info": {"status": "DISABLED", "reason": "OFFLINE"}},
        {"keyword": "x", "name": "On", "geo": {"geo_id": "2", "geo_type": "DMA"}, "status_info": {"status": "ENABLED"}},
        {"keyword": "x", "name": "No id", "geo": {}},
    ]})))
    tags = c.search_locations(["x"], geo_types=["DMA"])
    assert [(t["name"], t["geo_id"], t["enabled"]) for t in tags] == [("Off", "1", False), ("On", "2", True)]


def test_a_batch_search_sends_the_documented_fields():
    c = make_client()
    c.search_locations(["Los Angeles", "Phoenix"], geo_types=["DMA"], region_codes=["US"])
    body = c.fake.calls[-1]["body"]
    assert body["search_type"] == "BATCH_REGION_SEARCH" and body["keywords"] == ["Los Angeles", "Phoenix"]
    assert body["geo_types"] == ["DMA"] and body["region_codes"] == ["US"] and body["placements"] == ["PLACEMENT_TIKTOK"]
    assert body["advertiser_id"] == ADVERTISER and body["objective_type"] == "REACH"
    assert c.search_locations([], geo_types=["DMA"]) == []                  # nothing to look up is no call
    assert c.fake.count("/tool/targeting/search/") == 1


@pytest.mark.parametrize("data,lower,upper,stage", [
    ({"user_count": {"lower_end": 107229000, "upper_end": 131059000}}, 107229000, 131059000, None),
    ({"user_count": {"lower_end": "1,000", "upper_end": "2,000"}, "user_count_stage": 2}, 1000, 2000, 2),
    ({"user_count": {"1": 500, "2": 900}, "user_count_stage": 3}, 500, 900, 3),                 # the v1.2 keys
    ({"user_count": {"lower_end": 900, "upper_end": 500}}, 500, 900, None),                     # reversed: put right
    ({"user_count": 12345}, 12345, 12345, None),
    ({"user_count": {"lower_end": 0, "upper_end": 0}, "user_count_stage": 1}, 0, 0, 1),         # a real zero is an answer
    ({"user_count": {"lower_end": 5}}, 5, 5, None),
    ({"user_count": {"lower_end": 5, "upper_end": 6}, "user_count_stage": 9}, 5, 6, None),     # an unknown stage is not invented
])
def test_the_audience_size_is_read_leniently(data, lower, upper, stage):
    size = tc.parse_audience_size(data)
    assert (size.lower, size.upper, size.stage) == (lower, upper, stage) and size.mid == (lower + upper) / 2


@pytest.mark.parametrize("data", [{}, {"user_count": None}, {"user_count": {}}, {"user_count": {"lower_end": "many"}}, {"x": 1}])
def test_an_answer_with_no_audience_size_is_an_error_naming_what_it_held(data):
    with pytest.raises(TikTokAPIError) as err:
        tc.parse_audience_size(data, request_id="REQ-9")
    assert err.value.kind == "unexpected" and err.value.request_id == "REQ-9" and "no audience size" in str(err.value)


def test_a_negative_audience_is_an_error():
    with pytest.raises(TikTokAPIError):
        tc.parse_audience_size({"user_count": {"lower_end": -1, "upper_end": 5}})


def test_only_what_tiktok_itself_said_is_quoted_as_tiktoks():
    said = TikTokAPIError("Access token is wrong", kind="auth", code=40105).public_message()
    detail = TikTokAPIError("HTTP 502 with an unreadable body", kind="unavailable").public_message()
    assert "TikTok said: Access token is wrong" in said
    assert "TikTok said" not in detail and "(HTTP 502 with an unreadable body)" in detail and "try again in a moment" in detail


def test_asking_for_every_kind_leaves_the_kinds_out_which_is_what_tiktok_defaults_to():
    c = make_client()
    c.targeting_list()
    assert "sub_targeting_types" not in c.fake.calls[-1]["params"]
    c.targeting_search("auto")
    assert "sub_targeting_types" not in c.fake.calls[-1]["params"] and json.loads(c.fake.calls[-1]["params"]["search_keywords"]) == ["auto"]
    c.targeting_list(["hashtag_interaction"])
    assert json.loads(c.fake.calls[-1]["params"]["sub_targeting_types"]) == ["HASHTAG_INTERACTION"]


# --------------------------------------------------------------------------------------------- the review's findings

@pytest.mark.parametrize("token", ["abc\u200bdef", "abc def", "abc\ndef", "tok…", "tökén", "abc\tdef"])
def test_a_token_with_a_pasted_invisible_or_non_ascii_character_is_reported_as_a_setting_to_fix(monkeypatch, token):
    monkeypatch.setenv("TIKTOK_ACCESS_TOKEN", token)
    monkeypatch.setenv("TIKTOK_ADVERTISER_ID", "7000000000000000001")
    assert not tc.is_configured()
    assert any(m.startswith("TIKTOK_ACCESS_TOKEN (") and "copy it again" in m for m in tc.missing_config())
    assert token not in " ".join(tc.missing_config())


@pytest.mark.parametrize("base,usable", [
    ("https://business-api.tiktok.com/open_api/v1.3", True), ("HTTPS://example.com/x", True), ("http://127.0.0.1:8790/open_api/v1.3", True),
    ("http://localhost/x", True), ("http://business-api.tiktok.com/open_api/v1.3", False), ("ftp://x", False), ("http://127.0.0.1.evil.com/", False),
])
def test_the_api_base_must_be_https_unless_it_is_this_machine(monkeypatch, base, usable):
    monkeypatch.setenv("TIKTOK_ACCESS_TOKEN", "abc")
    monkeypatch.setenv("TIKTOK_ADVERTISER_ID", "7000000000000000001")
    monkeypatch.setenv("TIKTOK_API_BASE", base)
    assert tc.is_configured() is usable
    if not usable:
        assert any("TIKTOK_API_BASE" in m for m in tc.missing_config())


def test_a_character_a_header_cannot_carry_is_a_configuration_error_not_a_422_with_the_codec_message():
    class Latin1Session:
        def request(self, *a, **k):
            "tök".encode("latin-1", "strict")
            raise UnicodeEncodeError("latin-1", "\u200b", 0, 1, "ordinal not in range")
    c = make_client()
    c._session = Latin1Session()
    with pytest.raises(TikTokAPIError) as err:
        c.languages()
    assert err.value.kind == "config" and err.value.status_code == 503 and "latin-1" not in err.value.public_message()


def test_a_redirect_is_never_followed_so_the_token_cannot_be_sent_to_another_host():
    seen = {}

    class Redirecting:
        def request(self, method, url, **kwargs):
            seen.update(kwargs)
            return FakeResponse(302, "")
    c = make_client()
    c._session = Redirecting()
    with pytest.raises(TikTokAPIError) as err:
        c.languages()
    assert seen["allow_redirects"] is False and err.value.kind == "unexpected" and "redirect" in str(err.value)


def test_http_429_with_a_body_that_is_not_json_is_still_a_rate_limit():
    c = make_client()
    c.fake.queue("/tool/language/", *[FakeResponse(429, "Too Many Requests") for _ in range(3)])
    with pytest.raises(TikTokAPIError) as err:
        c.languages()
    assert err.value.kind == "rate_limit" and err.value.status_code == 429


def test_maintenance_is_unavailable_and_is_not_retried():
    c = make_client()
    c.fake.queue(ESTIMATE, FakeResponse(200, fail(60001, "The system is in maintenance.")))
    with pytest.raises(TikTokAPIError) as err:
        c.estimate_audience(US_BODY)
    assert err.value.kind == "unavailable" and c.fake.count(ESTIMATE) == 1 and c.sleeps == []


def test_what_tiktok_says_loses_the_token_and_any_ad_account_id_before_it_is_shown_to_anyone():
    c = make_client()
    msg = f"No permission for advertiser 7000000000000001234 with token {GOOD_TOKEN}; location 8840000440601 is fine"
    c.fake.queue(ESTIMATE, FakeResponse(200, fail(40001, msg)))
    with pytest.raises(TikTokAPIError) as err:
        c.estimate_audience(US_BODY)
    shown = err.value.public_message() + " " + str(err.value)
    assert GOOD_TOKEN not in shown and "7000000000000001234" not in shown and "…1234" in shown and "8840000440601" in shown     # a place ID is kept


def test_a_call_that_cannot_finish_inside_its_deadline_is_not_retried():
    clock = Clock()
    c = make_client(clock=clock)
    c.fake.queue(ESTIMATE, FakeResponse(200, fail(40100, "slow")), FakeResponse(200, fail(40100, "slow")))

    def slow_sleep(seconds):
        clock.now += 40                                  # each pause eats most of the deadline

    c._sleep = slow_sleep
    with pytest.raises(TikTokAPIError) as err:
        c.estimate_audience(US_BODY)
    assert err.value.kind == "rate_limit" and c.fake.count(ESTIMATE) <= 2


def test_a_retry_that_needs_its_whole_connect_and_read_time_is_not_started_when_it_cannot_finish_by_the_deadline():
    """Another attempt can take CONNECT_TIMEOUT + READ_TIMEOUT (30 s): after 15 s spent, a retry that could end at 45.6 s is not begun."""
    clock = Clock()
    c = make_client(clock=clock)
    c.fake.queue(ESTIMATE, timeout_error(), timeout_error(), timeout_error())
    inner = c._session

    class Slow:
        def request(self, *a, **k):
            clock.now += 15                              # the attempt ran 15 s before it timed out
            return inner.request(*a, **k)

    c._session = Slow()
    with pytest.raises(TikTokAPIError) as err:
        c.estimate_audience(US_BODY)
    assert err.value.kind == "unavailable" and c.fake.count(ESTIMATE) == 1
    assert clock.now - 1000.0 <= tc.CALL_DEADLINE


def test_an_ad_account_id_is_scrubbed_even_when_letters_or_an_underscore_touch_it():
    c = make_client()
    c.fake.queue(ESTIMATE, FakeResponse(200, fail(40001, "advertiser_7000000000000001234 and id7000000000000001234x are not allowed")))
    with pytest.raises(TikTokAPIError) as err:
        c.estimate_audience(US_BODY)
    shown = err.value.public_message() + " " + str(err.value)
    assert "7000000000000001234" not in shown and "advertiser_…1234" in shown and "id…1234x" in shown


def test_calls_in_flight_are_capped_and_a_queue_that_would_wait_too_long_is_refused():
    clock = Clock()
    c = make_client(clock=clock, min_interval=1.0)
    with pytest.raises(TikTokAPIError) as err:
        for _ in range(20):                               # the 20th slot would be 19 s away: more than the 5 s a turn may wait
            c._throttle.wait(tc.MAX_QUEUE_WAIT)
    assert err.value.kind == "rate_limit" and "waiting for their turn" in str(err.value)
    assert c._slots._value == tc.MAX_CONCURRENT


def test_the_in_flight_cap_gives_every_slot_back_even_when_a_call_fails():
    c = make_client()
    for _ in range(tc.MAX_CONCURRENT * 2):
        with pytest.raises(TikTokAPIError):
            c.estimate_audience({**US_BODY, "location_ids": []})
    assert c._slots._value == tc.MAX_CONCURRENT


def test_the_market_list_is_the_countries_states_and_markets_with_their_kind():
    regions = make_client().list_regions()
    by_name = {r["name"]: r for r in regions}
    assert by_name["United States"]["level"] == "COUNTRY" and by_name["United States"]["id"] == "6252001"
    assert by_name["Texas"]["area_type"] == "ADMIN" and by_name["Texas"]["level"] == "PROVINCE" and by_name["Texas"]["parent_id"] == "6252001"
    markets = [r for r in regions if r["area_type"] == "METROPOLITAN_OR_DMA"]
    assert len(markets) == 210 and all(r["level"] == "PROVINCE" and r["region_code"] == "US" for r in markets)


def test_the_market_list_is_one_get_with_the_documented_parameters_and_is_cached():
    c = make_client()
    c.list_regions()
    c.list_regions()
    call = c.fake.calls[0]
    assert c.fake.count("/tool/region/") == 1 and call["method"] == "GET"
    assert json.loads(call["params"]["placements"]) == ["PLACEMENT_TIKTOK"] and call["params"]["level_range"] == "TO_PROVINCE"
    assert call["params"]["objective_type"] == "REACH" and call["params"]["advertiser_id"] == ADVERTISER


def test_a_place_found_by_search_carries_the_chain_of_its_parents_when_tiktok_sends_it():
    c = make_client()
    city = next(t for t in c.search_locations(["Frankfort"], geo_types=["CITY", "DISTRICT"], search_type="FUZZY_SEARCH") if t["name"] == "Frankfort")
    assert [(a["name"], a["geo_type"]) for a in city["ancestors"]] == [("Franklin County", "CITY"), ("Kentucky", "PROVINCE"), ("United States", "COUNTRY")]
    bare = make_client(FakeTikTok(parent_tags=False)).search_locations(["Frankfort"], geo_types=["CITY", "DISTRICT"], search_type="FUZZY_SEARCH")
    assert bare and all(t["ancestors"] == [] for t in bare)


def test_a_catalog_that_came_back_empty_is_not_remembered_so_the_next_call_asks_again():
    c = make_client()
    c.fake.queue("/tool/language/", FakeResponse(200, ok({"languages": []})))
    assert c.languages() == []
    assert [l["code"] for l in c.languages()][:1] == ["en"] and c.fake.count("/tool/language/") == 2     # the empty answer was not kept
    assert c.languages() and c.fake.count("/tool/language/") == 2                                        # the real one is
    c2 = make_client()
    c2.fake.queue("/targeting/search/", FakeResponse(200, ok({"general_interest": {"list_result": []}})))
    assert all(items == [] for items in c2.targeting_list().values())
    assert c2.targeting_list()["general_interest"] and c2.fake.count("/targeting/search/") == 2
    c3 = make_client()
    c3.fake.queue("/tool/region/", FakeResponse(200, ok({"region_info": []})))
    assert c3.list_regions() == [] and c3.list_regions() and c3.fake.count("/tool/region/") == 2
    c4 = make_client()
    c4.fake.queue("/tool/targeting/search/", FakeResponse(200, ok({"targeting_tag_list": []})))
    assert c4.search_locations(["Phoenix"], geo_types=["DMA"]) == []
    assert c4.search_locations(["Phoenix"], geo_types=["DMA"]) and c4.fake.count("/tool/targeting/search/") == 2
