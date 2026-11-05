"""One strict fake per registered Constanza method, so a test (or the contract suite) can run a method end to end without a network.

    FAKES[key] = MethodFake(sample_body, install, hostile_text, answer)

  sample_body   a body of POST /api/constanza/estimate that the fake answers (a plain dict: copy.deepcopy it before changing it)
  install       install(monkeypatch) -> the fake itself. Patches the network and the environment for ONE test: the method's secrets, a clean cache,
                the forecast map with no admin rows and no database. Everything is undone by the monkeypatch.
  hostile_text  the markup planted in the server-supplied text of `answer(hostile=True)` (a place name, a note, ...), so a test can check a page
                shows it as text and never runs it
  answer        answer(hostile=False) -> what `main.constanza_estimate` returns for `sample_body` under the fake (its own patching is undone)

A new method adds its entry here (docs/constanza_methods.md): the contract test fails with a sentence when a registered key has none.
The fakes are the strict, docs-shaped ones the method's own tests use (tests/meta_support.py, tests/tiktok_support.py), not looser copies.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Callable

import pytest

from tests import meta_support, tiktok_support

HOSTILE = '<img src=x onerror="window.__pwned = 1">'


@dataclass(frozen=True)
class MethodFake:
    sample_body: dict
    install: Callable[[pytest.MonkeyPatch], Any]
    hostile_text: str
    answer: Callable[..., dict]


def _offline_forecast_map(monkeypatch: pytest.MonkeyPatch) -> None:
    """No admin rows and no database: every product keeps its built-in method."""
    from app.services import constanza_forecast_map as fm
    from app.services import forecast_store
    monkeypatch.setattr(fm, "load_overrides", lambda: {})
    monkeypatch.setattr(forecast_store, "_fetch_catalog", lambda: ({}, {}))
    fm.invalidate()
    forecast_store.invalidate()


def _no_planning_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("CONSTANZA_AUDIENCE_X_CONSERVATIVE", "CONSTANZA_AUDIENCE_X_MID", "CONSTANZA_AUDIENCE_X_UPPER"):
        monkeypatch.delenv(name, raising=False)


def _answer_of(key: str, hostile: bool) -> dict:
    import app.main as main
    fake = FAKES[key]
    body = copy.deepcopy(fake.sample_body)
    if hostile:
        body = _hostile_body(key, body)
    with pytest.MonkeyPatch.context() as monkeypatch:
        fake.install(monkeypatch)
        out = main.constanza_estimate(main.ConstanzaEstimateRequest(**body))
    if key == "tiktok":
        from app.services import tiktok_geo as tg
        tg.forget_everything()
    return _plant(key, out) if hostile else out


# --------------------------------------------------------------------------------------------------------------------------------- Meta

def _install_meta(monkeypatch: pytest.MonkeyPatch) -> meta_support.FakeGraph:
    from app.services import constanza_meta as meta
    _no_planning_overrides(monkeypatch)
    for name in ("META_GRAPH_API_VERSION", "META_APP_SECRET"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("META_ACCESS_TOKEN", meta_support.TOKEN)
    monkeypatch.setenv("META_AD_ACCOUNT_ID", meta_support.ACCOUNT)
    graph = meta_support.FakeGraph()
    monkeypatch.setattr(meta.requests, "get", graph.get)
    monkeypatch.setattr(meta, "_cache", {})                  # a cache of its own for this test, put back afterwards
    _offline_forecast_map(monkeypatch)
    return graph


META_BODY = {
    "product_name": meta_support.AWARENESS,
    "geo_text": "",                                          # Meta owns its geography: not read
    "months": 2,
    "meta": {
        "geo": {"type": "geo_market", "key": "803", "label": "Los Angeles · California · US"},
        "age_min": 25, "age_max": 44, "gender": "female",
        "languages": [{"id": "1001", "name": "Spanish"}],
        "targeting": [{"id": "6003107902433", "name": "Soccer", "type": "interests"}],
        "retail_budget": 2000.0,
    },
}


def _hostile_meta_body(body: dict) -> dict:
    """The names a planner picks from Meta's lists are echoed back in the answer: make every one of them markup."""
    block = body["meta"]
    block["geo"]["label"] = HOSTILE
    block["languages"] = [{"id": "1001", "name": HOSTILE}]
    block["targeting"] = [{"id": "6003107902433", "name": HOSTILE, "type": "interests"}]
    return body


# --------------------------------------------------------------------------------------------------------------------------------- TikTok

def _install_tiktok(monkeypatch: pytest.MonkeyPatch) -> Any:
    from app.services import tiktok_client as tc
    from app.services import tiktok_geo as tg
    _no_planning_overrides(monkeypatch)
    for name in ("TIKTOK_OBJECTIVE_TYPE", "TIKTOK_OPTIMIZATION_GOAL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TIKTOK_ACCESS_TOKEN", tiktok_support.GOOD_TOKEN)
    monkeypatch.setenv("TIKTOK_ADVERTISER_ID", tiktok_support.ADVERTISER)
    client = tiktok_support.make_client(tiktok_support.FakeTikTok())
    monkeypatch.setattr(tc, "get_client", lambda: client)
    _offline_forecast_map(monkeypatch)
    tg.forget_everything()                  # TikTok's market list is remembered per process: start from none (a test that installs this should clear it afterwards too)
    return client


TIKTOK_BODY = {"product_name": "Tiktok Ads", "geo_text": "Los Angeles", "months": 1}


def _plant(key: str, out: dict) -> dict:
    """Server text a fake cannot be asked to carry (TikTok's place names come from its market list): mark every free-text field of the answer."""
    out = copy.deepcopy(out)
    out["notes"] = [HOSTILE, *out["notes"]]
    if key == "tiktok":
        for loc in out["tiktok"]["locations"]:
            loc["name"] = f"{loc['name']} {HOSTILE}"
        out["tiktok"]["stage_label"] = HOSTILE
    return out


def _hostile_body(key: str, body: dict) -> dict:
    return _hostile_meta_body(body) if key == "meta" else body


FAKES: dict[str, MethodFake] = {
    "meta": MethodFake(sample_body=META_BODY, install=_install_meta, hostile_text=HOSTILE, answer=lambda hostile=False: _answer_of("meta", hostile)),
    "tiktok": MethodFake(sample_body=TIKTOK_BODY, install=_install_tiktok, hostile_text=HOSTILE, answer=lambda hostile=False: _answer_of("tiktok", hostile)),
}


# --------------------------------------------------------------------------------------------------------------------------------- a throw-away method
# For the registry's own tests (and a negative twin of a contract test): a method that says back what it was asked.

def echo_checks() -> list:
    """The diagnose function of the throw-away `echo` method (a "module:function" string points here)."""
    from app.services.constanza_methods import Check
    return [Check("Echo settings", True, "echo is always connected")]


def echo_leaky_checks() -> list:
    """A diagnose function that prints the environment variables of the throw-away `echo` method as they are: the registry must not let them out."""
    import os
    from app.services.constanza_methods import Check
    account = os.environ.get("ECHO_ACCOUNT")
    return [Check("Echo account", True, f"account {account} (bare {(account or '').removeprefix('act_')}) token {os.environ.get('ECHO_TOKEN')}"),
            Check("Echo refused", False, f"refused {os.environ.get('ECHO_TOKEN')}", warn=True, seconds=1.5)]
