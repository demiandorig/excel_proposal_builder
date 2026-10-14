"""The TikTok connection check (app/services/tiktok_diagnose.py): what it reports when everything works, when the settings are
missing, when the token is wrong, and when TikTok names the TV markets differently from what the matcher can place."""
from __future__ import annotations

import json

import pytest

from app.services import tiktok_client as tc
from app.services import tiktok_diagnose as dg
from app.services import tiktok_geo as tg
from tests.tiktok_support import GOOD_TOKEN, FakeTikTok, make_client


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for name in ("TIKTOK_ACCESS_TOKEN", "TIKTOK_ADVERTISER_ID"):
        monkeypatch.delenv(name, raising=False)
    tc.reset_client()
    tg.forget_everything()
    yield
    tc.reset_client()
    tg.forget_everything()


def test_every_check_passes_against_a_healthy_account():
    checks = dg.run_checks(make_client())
    assert [c.name for c in checks] == [
        "Access token and ad account", "Interest and behavior categories", "Keyword search", "TV markets by name", "ZIP codes", "States",
        "Audience size · United States", "Audience size · a narrow audience"]
    assert all(c.ok and not c.warn for c in checks), [(c.name, c.detail) for c in checks if not c.ok or c.warn]
    by = {c.name: c for c in checks}
    assert "×5.5" in by["Audience size · United States"].detail and "Los Angeles" in by["Audience size · a narrow audience"].detail


def test_the_whole_market_list_is_tried_by_default_and_all_210_are_placed():
    checks = dg.run_checks(make_client())
    markets = next(c for c in checks if c.name == "TV markets by name")
    assert markets.ok and not markets.warn and "210 of 210" in markets.detail


def test_without_the_settings_one_line_says_what_to_add():
    checks = dg.run_checks()
    assert len(checks) == 1 and not checks[0].ok and "TIKTOK_ACCESS_TOKEN" in checks[0].detail and "TIKTOK_ADVERTISER_ID" in checks[0].detail


def test_a_wrong_token_stops_the_run_at_the_first_check_and_says_how_to_fix_it():
    checks = dg.run_checks(make_client(FakeTikTok(token="somebody-elses")))
    assert len(checks) == 1 and not checks[0].ok and "TIKTOK_ACCESS_TOKEN" in checks[0].detail and "40105" in checks[0].detail
    assert GOOD_TOKEN not in checks[0].detail


def test_markets_tiktok_names_differently_are_listed_as_a_warning_not_a_failure():
    fake = FakeTikTok()
    original = fake._all_locations

    def renamed():
        out = original()
        for loc in out:
            if loc["type"] == "DMA":
                loc["name"] = "Metro " + str(loc["id"])          # nothing like the Nielsen names
        return out
    fake._all_locations = renamed
    checks = dg.run_checks(make_client(fake))
    markets = next(c for c in checks if c.name == "TV markets by name")
    assert markets.ok and markets.warn and "NOT placed" in markets.detail and "Los Angeles" in markets.detail
    assert markets.to_public()["warn"] is True
    assert all(c.ok for c in checks)


def test_a_failure_in_the_middle_is_reported_and_the_run_continues():
    from tests.tiktok_support import FakeResponse
    client = make_client()
    refusal = FakeResponse(200, {"code": 40002, "message": "location_ids: invalid", "request_id": "R1"})
    client.fake.queue("/ad/audience_size/estimate/", refusal, refusal)         # REACH / REACH refused, and CLICK too
    checks = dg.run_checks(client)
    bad = [c for c in checks if not c.ok]
    assert [c.name for c in bad] == ["Audience size · United States"] and "location_ids: invalid" in bad[0].detail and "40002" in bad[0].detail
    assert len(checks) == 8


def test_when_tiktok_refuses_reach_reach_but_takes_reach_click_the_check_says_which_setting_to_change():
    client = make_client()
    client.fake.reach_goals = {"CLICK"}                    # an account that takes the pair in TikTok's own documentation example
    checks = dg.run_checks(client)
    national = next(c for c in checks if c.name == "Audience size · United States")
    assert national.ok and national.warn and "TIKTOK_OPTIMIZATION_GOAL=CLICK" in national.detail and "not valid for objective_type REACH" in national.detail
    sent = [c["body"]["optimization_goal"] for c in client.fake.calls if c["path"] == "/ad/audience_size/estimate/"]
    assert sent[:2] == ["REACH", "CLICK"]


def test_with_the_setting_the_account_needs_nothing_is_refused_first(monkeypatch):
    monkeypatch.setenv("TIKTOK_OPTIMIZATION_GOAL", "CLICK")
    client = make_client()
    client.fake.reach_goals = {"CLICK"}
    checks = dg.run_checks(client)
    assert all(c.ok and not c.warn for c in checks), [(c.name, c.detail) for c in checks if not c.ok or c.warn]
    assert {c["body"]["optimization_goal"] for c in client.fake.calls if c["path"] == "/ad/audience_size/estimate/"} == {"CLICK"}


def test_the_command_prints_a_line_per_check_and_json_on_request(monkeypatch, capsys):
    monkeypatch.setattr(tc, "get_client", lambda: make_client())
    monkeypatch.setenv("TIKTOK_ACCESS_TOKEN", "x")
    monkeypatch.setenv("TIKTOK_ADVERTISER_ID", "7000000000000000001")
    assert dg.main([]) == 0
    out = capsys.readouterr().out
    assert "✓ Access token and ad account" in out and "All checks passed." in out
    assert dg.main(["--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert len(data) == 8 and all(d["ok"] for d in data) and set(data[0]) == {"name", "ok", "warn", "detail", "seconds"}


def test_the_command_exits_nonzero_when_something_failed(capsys):
    assert dg.main([]) == 1 and "Missing" in capsys.readouterr().out


def test_the_sample_of_markets_holds_the_ones_a_name_match_can_confuse():
    from app.data.constanza import load_dma_reference
    dmas = list(load_dma_reference())
    names = {d.dma for d in dg.sample_markets(dmas)}
    assert 10 <= len(names) <= dg.SAMPLE_SIZE
    assert {"New York", "Los Angeles", "Chicago"} <= names                                   # the biggest
    assert {"Portland, OR", "Portland-Auburn", "Columbus, OH", "Columbus, GA (Opelika, AL)", "Jackson, MS", "Jackson, TN"} <= names   # shared first city
    assert {"St. Louis", "Dallas-Ft. Worth"} & names                                          # the abbreviated ones


def test_the_markets_line_says_where_the_names_came_from():
    detail = next(c for c in dg.run_checks(make_client()) if c.name == "TV markets by name").detail
    assert "from TikTok's own market list" in detail
    searched = next(c for c in dg.run_checks(make_client(FakeTikTok(region_list=False))) if c.name == "TV markets by name").detail
    assert "by searching: TikTok's market list was not available" in searched


def test_a_sample_run_tries_fewer_markets_than_the_default(capsys, monkeypatch):
    monkeypatch.setattr(tc, "get_client", lambda: make_client())
    monkeypatch.setenv("TIKTOK_ACCESS_TOKEN", "x")
    monkeypatch.setenv("TIKTOK_ADVERTISER_ID", "7000000000000000001")
    assert dg.main(["--sample", "--json"]) == 0
    sample = next(d for d in __import__("json").loads(capsys.readouterr().out) if d["name"] == "TV markets by name")["detail"]
    assert " of " in sample and "210" not in sample.split(" of ")[1].split(" ")[0]
