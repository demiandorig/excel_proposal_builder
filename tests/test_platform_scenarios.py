"""The audience-size platforms' impression scenarios (app/services/platform_scenarios.py): TikTok's audience-size estimate and Meta's reach
estimate give a head count as a range; Entravision's planning rule turns the LOWER end of it into impressions as conservative x 4.3,
mid x 5.5 (the default) and upper x 8.6."""
from __future__ import annotations

import logging

import pytest

from app.services import platform_scenarios as ps

ENVS = ("CONSTANZA_AUDIENCE_X_CONSERVATIVE", "CONSTANZA_AUDIENCE_X_MID", "CONSTANZA_AUDIENCE_X_UPPER")


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for name in ENVS:
        monkeypatch.delenv(name, raising=False)


def test_the_defaults_are_4_3_5_5_and_8_6_and_mid_is_the_default_scenario():
    assert ps.multipliers() == {"conservative": 4.3, "mid": 5.5, "upper": 8.6} and ps.DEFAULT_SCENARIO == "mid"
    assert ps.SCENARIOS == ("conservative", "mid", "upper") and ps.SLOT == {"conservative": "low", "mid": "mid", "upper": "high"}


def test_every_scenario_is_the_lower_end_of_the_platforms_range_times_its_multiplier():
    assert ps.monthly(1_000_000) == pytest.approx({"low": 4_300_000, "mid": 5_500_000, "high": 8_600_000})
    assert ps.monthly(0) == {"low": 0.0, "mid": 0.0, "high": 0.0} and ps.monthly(None) == {"low": 0.0, "mid": 0.0, "high": 0.0}
    assert ps.monthly(-5)["mid"] == 0.0                                           # never a negative audience


def test_the_public_form_says_the_basis_the_default_and_what_each_scenario_comes_to():
    assert ps.to_public() == {"basis": "lower_end", "default": "mid", "multipliers": {"conservative": 4.3, "mid": 5.5, "upper": 8.6}}
    out = ps.to_public(200_000)
    assert out["monthly"] == pytest.approx({"conservative": 860_000, "mid": 1_100_000, "upper": 1_720_000})
    assert "4.3 (conservative) / 5.5 (mid) / 8.6 (upper)" in ps.describe() and "lower-end audience" in ps.describe()


@pytest.mark.parametrize("env,expected", [
    ({"CONSTANZA_AUDIENCE_X_MID": "6"}, (4.3, 6.0, 8.6)),                             # one setting moves one multiplier
    ({"CONSTANZA_AUDIENCE_X_CONSERVATIVE": "3", "CONSTANZA_AUDIENCE_X_UPPER": "10"}, (3.0, 5.5, 10.0)),
    ({"CONSTANZA_AUDIENCE_X_CONSERVATIVE": "5", "CONSTANZA_AUDIENCE_X_MID": "5", "CONSTANZA_AUDIENCE_X_UPPER": "5"}, (5.0, 5.0, 5.0)),   # one fixed figure
    ({"CONSTANZA_AUDIENCE_X_MID": " 7.25 "}, (4.3, 7.25, 8.6)),
])
def test_the_environment_can_change_the_multipliers(monkeypatch, env, expected):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    assert tuple(ps.multipliers()[n] for n in ps.SCENARIOS) == pytest.approx(expected)


@pytest.mark.parametrize("env", [
    {"CONSTANZA_AUDIENCE_X_MID": "abc"}, {"CONSTANZA_AUDIENCE_X_MID": "3"}, {"CONSTANZA_AUDIENCE_X_UPPER": "5"}, {"CONSTANZA_AUDIENCE_X_CONSERVATIVE": "6"},
    {"CONSTANZA_AUDIENCE_X_MID": "0"}, {"CONSTANZA_AUDIENCE_X_MID": "-2"}, {"CONSTANZA_AUDIENCE_X_UPPER": "1000"}, {"CONSTANZA_AUDIENCE_X_MID": "nan"},
    {"CONSTANZA_AUDIENCE_X_UPPER": "inf"}])
def test_a_bad_or_out_of_order_set_is_ignored_with_a_warning_never_half_applied(monkeypatch, caplog, env):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    with caplog.at_level(logging.WARNING):
        assert ps.multipliers() == {"conservative": 4.3, "mid": 5.5, "upper": 8.6}
    assert any("multipliers" in r.getMessage() or "CONSTANZA_AUDIENCE_X" in r.getMessage() for r in caplog.records)


def test_blank_settings_are_unset():
    import os
    os.environ["CONSTANZA_AUDIENCE_X_MID"] = "  "
    try:
        assert ps.multipliers()["mid"] == 5.5
    finally:
        del os.environ["CONSTANZA_AUDIENCE_X_MID"]
