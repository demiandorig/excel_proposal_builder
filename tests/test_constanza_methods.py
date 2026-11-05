"""Constanza's method registry (app/services/constanza_methods.py) and everything that is generic about it: how a method is registered,
the standard estimate and status shapes, the generic dispatch of POST /api/constanza/estimate, the default mapping of the Meta and TikTok
products, Admin's methods list and connection tests, the one saved-estimate field of an Avails entry, and migration 0006.

The Meta and TikTok estimates themselves are pinned in test_constanza_meta.py / test_constanza_tiktok.py; here a Meta product is only the
example of "a method that owns its geography" and a TikTok product of "one that does not". Offline: both run against the strict fakes of
tests/method_fakes.py, the forecast map has no database.
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
import re
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

import app.main as main
from app import catalog
from app.services import constanza_forecast_map as fm
from app.services import constanza_meta as meta
from app.services import constanza_methods as methods
from app.services import meta_diagnose, tiktok_diagnose
from app.services.constanza_geo import resolve_geo
from app.services.constanza_methods import Check, MethodSpec
from tests.meta_support import ACCOUNT, AWARENESS, TOKEN
from tests.method_fakes import FAKES, HOSTILE

ROOT = Path(__file__).resolve().parents[1]
TIKTOK = "Tiktok Ads"
ROKU = "Entravision Plus - Roku Ads (Includes The Roku Channel and the popular Espacio Latino Hub)"
VIX = "Entravision Plus - VIX 360 (Includes VIX + UnivisionNow + Univision.com / Univision Apps)"
SEARCH = "Search - SEM"
TRAFFIC = "Facebook & Instagram Ads | Traffic / Conversion"
LEAD_GEN = "Facebook & Instagram Ads | Lead Gen / Calls"
BRANDED = ("Facebook & Instagram Ads on Noticias Ya", "Facebook & Instagram Ads on EVC Radio Pages")
STANDARD = set(methods.STANDARD_KEYS)


REAL_LOAD_OVERRIDES = fm.load_overrides
METHODS_ON_LOAD = tuple(methods.keys())                       # what the application registers (a future method shows up here by itself)
BUILT_IN = ["platform", "generic"]


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr(fm, "load_overrides", lambda: {})
    fm.invalidate()
    yield
    fm.invalidate()


# --------------------------------------------------------------------------------------------------------------------------- a throw-away method

def make_echo(key="echo", *, estimate=None, **fields) -> MethodSpec:
    """A method that says back what it was asked (not registered: the `echo` fixture does that and takes it out again)."""
    box: dict = {}

    def default_estimate(req):
        return methods.standard_estimate(box["spec"], product_name=req.product_name, months=req.months, imps={"low": 10, "mid": 20, "high": 30},
                                         est_uniques=10, notes=["said back"], block={"asked": req.args}, resolution=req.resolution)

    spec_fields = dict(
        key=key, label="Echo (live)", short_label="Echo", description="Says back what it was asked.", kind="api", default_products=(),
        owns_geography=True, request_field=key, secrets=("ECHO_TOKEN",), estimate=estimate or default_estimate,
        status=lambda: {"configured": True, "missing": [], "account": None, "setup_hint": "Nothing to set up."},
        diagnose="tests.method_fakes:echo_checks",
    )
    spec_fields.update(fields)
    spec = MethodSpec(**spec_fields)
    box["spec"] = spec
    return spec


@pytest.fixture
def echo():
    added = []

    def register(key="echo", **fields):
        spec = make_echo(key, **fields)
        methods.register(spec)
        added.append(spec.key)
        return spec
    yield register
    for key in added:
        methods.unregister(key)


def _body(key, **changes):
    body = copy.deepcopy(FAKES[key].sample_body)
    body.update(changes)
    return main.ConstanzaEstimateRequest(**body)


# --------------------------------------------------------------------------------------------------------------------------- registering a method

def test_tiktok_and_meta_are_registered_in_that_order_ahead_of_any_later_method_and_the_built_ins_are_not_registered():
    assert methods.keys() == list(METHODS_ON_LOAD) and METHODS_ON_LOAD[:2] == ("tiktok", "meta")
    assert methods.method_keys() == ("platform", "generic") + METHODS_ON_LOAD
    assert methods.BUILTIN_METHODS == ("platform", "generic")
    assert methods.get("platform") is None and methods.get("generic") is None
    assert methods.get(None) is None and methods.get(["meta"]) is None and methods.get("constructor") is None


# What the two methods built first choose about geography. It is a fact of Meta and TikTok, not a rule of the registry: a later method states its own in
# its own tests, and the test below asserts only what holds for every method.
OWNS_GEOGRAPHY = {"meta": True, "tiktok": False}            # Meta picks its own places from its own search; TikTok maps the shared Nielsen geography


def test_every_registered_method_is_complete_and_answers_the_standard_status():
    for spec in methods.all():
        assert methods.KEY_RE.fullmatch(spec.key) and spec.request_field == spec.key
        assert spec.label and spec.short_label and spec.description and spec.kind in ("api", "dataset") and isinstance(spec.owns_geography, bool)
        assert spec.status_url == f"/api/constanza/{spec.key}/status" and spec.test_url == f"/api/admin/constanza/{spec.key}/test"
        methods.validate_status(spec, spec.status())
        if spec.kind == "api":                                           # (a dataset method needs neither secrets nor a connection test)
            assert spec.secrets and spec.diagnose
        if spec.diagnose:
            module, _, function = spec.diagnose.partition(":")
            assert callable(getattr(__import__(module, fromlist=[function]), function))


def test_the_completeness_test_holds_for_a_later_method_that_owns_its_geography_and_for_a_dataset_method(echo):
    """It asserts only what is true of EVERY method: adding the next source must not need an edit to it."""
    echo("alpha", owns_geography=True)
    echo("zebra", kind="dataset", secrets=(), diagnose="", owns_geography=False)
    test_every_registered_method_is_complete_and_answers_the_standard_status()


@pytest.mark.parametrize("key,owns", OWNS_GEOGRAPHY.items())
def test_meta_picks_its_own_places_and_tiktok_maps_the_shared_geography(key, owns):
    spec = methods.get(key)
    assert spec.kind == "api" and spec.owns_geography is owns


@pytest.mark.parametrize("key", ["", "a", "Echo", "1echo", "echo space", "echo-dash", "x" * 33, "écho", "echo\n"])
def test_a_key_that_is_not_a_lower_case_word_is_refused(key, echo):
    with pytest.raises(ValueError, match="lower-case word"):
        echo(key=key, request_field=key)


@pytest.mark.parametrize("key", ["platform", "generic"])
def test_a_built_in_method_cannot_be_registered(key, echo):
    with pytest.raises(ValueError, match="built-in"):
        echo(key=key, request_field=key)


@pytest.mark.parametrize("fields,message", [
    ({"request_field": "other"}, "request_field must equal the key"),
    ({"kind": "magic"}, "kind must be"),
    ({"label": "  "}, "label must say something"),
    ({"description": ""}, "description must say something"),
    ({"short_label": None}, "short_label must say something"),
    ({"diagnose": ""}, "needs a diagnose function"),
    ({"diagnose": "no_colon_here"}, "needs a diagnose function"),
    ({"secrets": ()}, "names the environment variables"),
    ({"estimate": "not callable"}, "estimate and status must be callable"),
    ({"default_products": ("Tiktok Ads",)}, "already the default product of method 'tiktok'"),
])
def test_a_spec_that_could_not_work_is_refused_with_the_reason(fields, message, echo):
    with pytest.raises(ValueError, match=message):
        echo(**fields)


def test_a_duplicate_key_and_something_that_is_not_a_spec_are_refused(echo):
    echo()
    with pytest.raises(ValueError, match="already registered"):
        methods.register(make_echo())
    with pytest.raises(ValueError, match="takes a MethodSpec"):
        methods.register({"key": "echo2"})
    assert methods.keys().count("echo") == 1


def test_a_dataset_method_needs_neither_secrets_nor_a_diagnose_function(echo):
    spec = echo("tallies", kind="dataset", secrets=(), diagnose="")
    assert methods.get("tallies") is spec and methods.summary_text(spec) == "Echo data"


def test_methods_are_listed_real_ones_first_and_then_in_the_order_they_were_registered(echo):
    echo("zebra")
    echo("alpha")
    assert methods.keys() == list(METHODS_ON_LOAD) + ["zebra", "alpha"]
    assert methods.method_keys() == ("platform", "generic") + METHODS_ON_LOAD + ("zebra", "alpha")
    assert [m["key"] for m in fm.methods_payload()] == BUILT_IN + list(METHODS_ON_LOAD) + ["zebra", "alpha"]


def test_a_removed_method_is_gone_from_every_list(echo):
    echo()
    assert "echo" in fm.METHODS
    methods.unregister("echo")
    assert "echo" not in fm.METHODS and methods.get("echo") is None and "echo" not in methods.keys()
    methods.unregister("echo")                                                    # removing it twice is harmless


def test_the_default_method_of_a_product_follows_a_renamed_product():
    assert methods.default_for(AWARENESS) == "meta" and methods.default_for(TIKTOK) == "tiktok"
    assert methods.default_for("Awareness (renamed)", {"Awareness (renamed)", AWARENESS}) == "meta"          # found through a former name
    assert methods.default_for("Tik Tok Ads", ["Tik Tok Ads", TIKTOK]) == "tiktok"
    assert methods.default_for(ROKU) is None and methods.default_for("No such product", {"Another"}) is None
    assert methods.default_for(BRANDED[0], {BRANDED[0]}) is None                    # the branded-Pages SKUs run on Entravision's own accounts
    assert {TIKTOK, AWARENESS, TRAFFIC, LEAD_GEN} <= set(methods.default_products())              # (a later method's default products join them)
    renamed = fm.default_config("Meta Awareness", "Social", variants={"Meta Awareness", AWARENESS})
    assert (renamed.enabled, renamed.method, renamed.platform) == (True, "meta", None)


def test_ensure_loaded_is_idempotent_and_leaves_the_registry_alone():
    before = methods.keys()
    methods.ensure_loaded()
    methods.ensure_loaded()
    assert methods.keys() == before


def test_the_summary_sentence_of_a_method_names_its_platform():
    assert methods.summary_text(methods.get("meta")) == "a live audience-size estimate from Meta"
    assert methods.summary_text(methods.get("tiktok")) == "a live audience-size estimate from TikTok"


# --------------------------------------------------------------------------------------------------------------------------- import order (the cycle)

@pytest.mark.parametrize("module", ["app.services.constanza_forecast_map", "app.services.constanza_methods", "app.services.constanza_meta",
                                    "app.services.constanza_tiktok", "app.services.constanza_engine", "app.main"])
def test_each_module_imports_cleanly_as_the_first_app_import_of_a_fresh_interpreter(module):
    """constanza_forecast_map -> constanza_tiktok -> constanza_audience -> constanza_engine -> constanza_forecast_map is a cycle the moment the
    forecast map loads the registry's modules at import time; it must not, whichever module the process meets first."""
    code = (
        "import importlib\n"
        f"importlib.import_module({module!r})\n"
        "from app.services import constanza_forecast_map as fm, constanza_methods as m\n"
        "assert fm.METHODS[:4] == ('platform', 'generic', 'tiktok', 'meta'), fm.METHODS\n"
        "assert fm.DEFAULT_API_PRODUCTS['Tiktok Ads'] == 'tiktok' and fm.DEFAULT_API_PRODUCTS['Facebook & Instagram Ads | Awareness'] == 'meta'\n"
        "assert m.keys()[:2] == ['tiktok', 'meta'], m.keys()\n"
        "assert fm.default_config('Tiktok Ads').method == 'tiktok'\n"
        "print('imported')\n"
    )
    env = {**os.environ, "DATABASE_URL": "postgresql://fake:fake@localhost/fake"}
    proc = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=180)
    assert proc.returncode == 0 and proc.stdout.strip().endswith("imported"), proc.stderr[-1500:]


def test_the_registry_module_imports_nothing_from_the_app():
    source = (ROOT / "app" / "services" / "constanza_methods.py").read_text(encoding="utf-8")
    imports = [line for line in source.splitlines() if re.match(r"\s*(from|import)\s+app[.\s]", line)]
    assert all(line.strip().startswith("from app.services.constanza_geo import GeoResolution") for line in imports), imports      # (a type only, under TYPE_CHECKING)


def test_the_forecast_map_keeps_no_registry_answer_at_import_time():
    """METHODS / DEFAULT_API_PRODUCTS are answered when read (PEP 562), so they follow a registry change; any other name is an AttributeError."""
    assert "METHODS" not in vars(fm) and "DEFAULT_API_PRODUCTS" not in vars(fm)
    with pytest.raises(AttributeError):
        fm.NO_SUCH_NAME  # noqa: B018
    assert not hasattr(fm, "__wrapped__")


# --------------------------------------------------------------------------------------------------------------------------- the standard estimate

def _standard(spec_key="meta", **changes):
    spec = methods.get(spec_key)
    args = dict(product_name=AWARENESS, months=2, imps={"low": 10, "mid": 20, "high": 30}, est_uniques=7, notes=["n"], block={"x": 1})
    args.update(changes)
    return spec, methods.standard_estimate(spec, **args)


def test_the_standard_estimate_has_every_key_the_card_reads_and_the_methods_own_block():
    spec, out = _standard()
    assert set(out) == STANDARD | {"meta"} and out["meta"] == {"x": 1}
    assert (out["method"], out["platform"], out["confidence"], out["geo_method"], out["geo_confidence"]) == ("meta", "meta", "live_api", "meta_audience", "selected")
    assert (out["imps_low"], out["imps_mid"], out["imps_high"], out["months"], out["est_uniques"]) == (10, 20, 30, 2, 7)
    assert (out["reach_universe"], out["series"], out["weeks"], out["by_area"]) == (None, None, None, [])
    assert out["hispanic_targeting"] == {"default": False, "on": False, "applies": False}
    assert out["unmatched_geo_tokens"] == out["unmatched_geo_details"] == out["ambiguous"] == out["geo_interpretations"] == out["geo_suggestions"] == []
    assert methods.validate_standard(spec, out) is out
    json.dumps(out)


def test_the_geography_lists_come_from_the_resolution_and_a_method_can_add_the_places_it_could_not_use():
    resolution = resolve_geo("Los Angeles; Qzxwvbn")
    spec, out = _standard("tiktok", resolution=resolution, unmatched=[{"input": "Phoenix", "reason": "TikTok has no market like that"}])
    assert (out["geo_method"], out["geo_confidence"]) == (resolution.method, resolution.geo_confidence)
    assert out["unmatched_geo_tokens"] == [*resolution.unmatched_tokens, "Phoenix"]
    assert out["unmatched_geo_details"][-1] == {"input": "Phoenix", "reason": "TikTok has no market like that"}
    assert out["geo_interpretations"] == resolution.interpretations and out["geo_suggestions"] == resolution.suggestions
    assert methods.validate_standard(spec, out) is out


@pytest.mark.parametrize("change,message", [
    (lambda o: o.pop("imps_mid"), "without: imps_mid"),
    (lambda o: o.pop("meta"), "without: meta"),
    (lambda o: o.update(method="tiktok"), "answered as method 'tiktok'"),
    (lambda o: o.update(meta=[1]), "block"),
    (lambda o: o.update(months=0), "months"),
    (lambda o: o.update(months=True), "months"),
    (lambda o: o.update(months="2"), "months"),
    (lambda o: o.update(imps_mid="20"), "imps_mid"),
    (lambda o: o.update(imps_low=float("nan")), "imps_low"),
    (lambda o: o.update(imps_high=None), "imps_high"),
    (lambda o: o.update(imps_low=-1), "imps_low"),
    (lambda o: o.update(imps_low=25), "low <= mid <= high"),
    (lambda o: o.update(imps_high=15), "low <= mid <= high"),
    (lambda o: o.update(est_uniques="7"), "est_uniques"),
    (lambda o: o.update(notes="one note"), "notes must be a list"),
    (lambda o: o.update(notes=["fine", 3]), "every note"),
    (lambda o: o.update(by_area=None), "by_area must be a list"),
    (lambda o: o.update(hispanic_targeting={"default": False}), "hispanic_targeting"),
])
def test_an_answer_that_is_not_the_standard_estimate_is_refused_and_nothing_is_filled_in(change, message):
    spec, out = _standard()
    change(out)
    with pytest.raises(methods.StandardEstimateError, match=message):
        methods.validate_standard(spec, out)


def test_a_standard_estimate_may_have_no_audience_size_and_an_answer_that_is_not_an_object_is_refused():
    spec, out = _standard(est_uniques=None)
    assert methods.validate_standard(spec, out)["est_uniques"] is None
    with pytest.raises(methods.StandardEstimateError, match="not an object"):
        methods.validate_standard(spec, [out])
    assert not issubclass(methods.StandardEstimateError, ValueError)         # a method's bug, never mistaken for the planner's mistake


# --------------------------------------------------------------------------------------------------------------------------- the standard status

def test_the_standard_status_names_what_is_missing_the_secret_first_and_never_a_value(monkeypatch):
    for name in ("META_ACCESS_TOKEN", "META_AD_ACCOUNT_ID", "TIKTOK_ACCESS_TOKEN", "TIKTOK_ADVERTISER_ID"):
        monkeypatch.delenv(name, raising=False)
    for key, secrets in (("meta", ("META_ACCESS_TOKEN", "META_AD_ACCOUNT_ID")), ("tiktok", ("TIKTOK_ACCESS_TOKEN", "TIKTOK_ADVERTISER_ID"))):
        spec = methods.get(key)
        status = methods.validate_status(spec, spec.status())
        assert status["configured"] is False and status["account"] is None
        assert [m.split()[0] for m in status["missing"]] == list(secrets) == list(spec.secrets)
    monkeypatch.setenv("META_ACCESS_TOKEN", TOKEN)
    monkeypatch.setenv("META_AD_ACCOUNT_ID", ACCOUNT)
    monkeypatch.setenv("TIKTOK_ACCESS_TOKEN", "SECRET-TOKEN-VALUE")
    monkeypatch.setenv("TIKTOK_ADVERTISER_ID", "7000000000000001234")
    for key, last_four in (("meta", "…2345"), ("tiktok", "…1234")):
        status = methods.validate_status(methods.get(key), methods.get(key).status())
        assert status["configured"] is True and status["missing"] == [] and status["account"] == last_four
        blob = json.dumps(status)
        assert TOKEN not in blob and "SECRET-TOKEN-VALUE" not in blob and "7000000000012345" not in blob and "7000000000000001234" not in blob
    assert methods.get("tiktok").status()["advertiser"] == "…1234"                   # the panel's own key stays beside `account`


@pytest.mark.parametrize("status,message", [
    ("nope", "must answer an object"),
    ({"missing": [], "account": None, "setup_hint": "x"}, "configured"),
    ({"configured": 1, "missing": [], "account": None, "setup_hint": "x"}, "configured"),
    ({"configured": False, "missing": "TOKEN", "account": None, "setup_hint": "x"}, "missing"),
    ({"configured": False, "missing": [""], "account": None, "setup_hint": "x"}, "missing"),
    ({"configured": True, "missing": ["TOKEN"], "account": None, "setup_hint": "x"}, "still lists"),
    ({"configured": True, "missing": [], "setup_hint": "x"}, "account"),
    ({"configured": True, "missing": [], "account": 7, "setup_hint": "x"}, "account"),
    ({"configured": True, "missing": [], "account": None, "setup_hint": " "}, "setup_hint"),
])
def test_a_status_that_is_not_the_standard_shape_is_refused(status, message):
    with pytest.raises(ValueError, match=message):
        methods.validate_status(methods.get("meta"), status)


# --------------------------------------------------------------------------------------------------------------------------- Check

def test_one_check_type_serves_every_methods_connection_test():
    assert tiktok_diagnose.Check is Check and meta_diagnose.Check is Check
    assert Check("Token", True, "fine", warn=True, seconds=1.2345).to_public() == {"name": "Token", "ok": True, "warn": True, "detail": "fine", "seconds": 1.23}
    assert Check("Token", False, "bad", warn=True).to_public()["warn"] is False                    # a failure is never "a warning"


def test_the_connection_checks_of_a_method_are_looked_up_when_they_run(echo, monkeypatch):
    echo()
    assert [c.name for c in methods.run_diagnose("echo")] == ["Echo settings"]
    monkeypatch.setattr(meta_diagnose, "run_checks", lambda: [Check("Replaced", True, "yes")])
    assert [c.name for c in methods.run_diagnose("meta")] == ["Replaced"]
    with pytest.raises(KeyError):
        methods.run_diagnose("platform")


def test_one_connection_test_at_a_time_per_method_and_a_failed_test_frees_the_lock():
    with methods.connection_test("meta"):
        with pytest.raises(methods.ConnectionTestBusy):
            with methods.connection_test("meta"):
                pass
        with methods.connection_test("tiktok"):                                           # another method is not held up
            pass
    with pytest.raises(RuntimeError):
        with methods.connection_test("meta"):
            raise RuntimeError("the test crashed")
    with methods.connection_test("meta"):                                                 # ... and the lock was let go
        pass


# --------------------------------------------------------------------------------------------------------------------------- the generic dispatch

def test_a_meta_product_answers_the_standard_estimate_with_a_meta_block_and_no_geography_text(monkeypatch):
    FAKES["meta"].install(monkeypatch)
    out = main.constanza_estimate(_body("meta"))
    assert STANDARD | {"meta"} <= set(out) and out["method"] == "meta" and out["platform"] == "meta"
    block = out["meta"]
    assert out["est_uniques"] == block["audience"]["low"] == block["scenarios"]["est_uniques"]            # the lower end: the Avails step's est. uniques
    assert out["imps_mid"] / out["months"] == block["monthly"]["mid"] == block["scenarios"]["monthly"]["mid"]
    assert (out["imps_low"], out["imps_mid"], out["imps_high"]) == tuple(block["scenarios"]["flight"][k] for k in ("conservative", "mid", "upper"))
    assert out["imps_mid"] / out["months"] / out["est_uniques"] == pytest.approx(5.5)                       # frequency = the mid multiplier
    assert (out["unmatched_geo_tokens"], out["by_area"], out["ambiguous"]) == ([], [], [])


def test_a_method_that_owns_its_geography_never_resolves_the_shared_one(monkeypatch):
    FAKES["meta"].install(monkeypatch)

    def refuse(*args, **kwargs):
        raise AssertionError("the shared geography must not be resolved for a method that owns its own")
    monkeypatch.setattr(main.constanza_geo, "resolve_geo", refuse)
    assert main.constanza_estimate(_body("meta", geo_text=""))["method"] == "meta"
    assert main.constanza_estimate(_body("meta", geo_text="Nowhere, Inc."))["method"] == "meta"           # sent anyway by an older page: still not read


def test_a_method_that_does_not_own_its_geography_still_resolves_the_shared_one_blank_text_included(monkeypatch):
    """A blank geo_text is a blank box today: it is resolved like any other text (to the whole country), and a key that is missing altogether is the same
    as a blank one. An unresolvable text keeps its 422. None of that changed when the method became generic."""
    FAKES["tiktok"].install(monkeypatch)
    asked = []
    real = main.constanza_geo.resolve_geo
    monkeypatch.setattr(main.constanza_geo, "resolve_geo", lambda text, **kw: (asked.append(text), real(text, **kw))[1])
    for product in (ROKU, TIKTOK):
        asked.clear()
        blank = main.constanza_estimate(main.ConstanzaEstimateRequest(product_name=product, geo_text="", months=1))
        omitted = main.constanza_estimate(main.ConstanzaEstimateRequest(product_name=product, months=1))
        without_block = lambda out: {k: v for k, v in out.items() if k != "tiktok"}          # noqa: E731  (TikTok's own block says whether it came from memory)
        assert asked == ["", ""] and without_block(blank) == without_block(omitted) and blank["method"] in ("platform", "tiktok"), product
    for product in (ROKU, TIKTOK):
        with pytest.raises(HTTPException) as err:
            main.constanza_estimate(main.ConstanzaEstimateRequest(product_name=product, geo_text="Qzxwvbn", months=1))
        assert err.value.status_code == 422 and err.value.detail == main._geo_failure_detail("Qzxwvbn", real("Qzxwvbn")), product
    assert main.ConstanzaEstimateRequest.model_json_schema()["required"] == ["product_name"]            # the one OpenAPI change: geo_text may be omitted


def test_the_meta_block_is_required_for_a_meta_product_and_says_so_in_planner_words(monkeypatch):
    graph = FAKES["meta"].install(monkeypatch)
    for body in (main.ConstanzaEstimateRequest(product_name=AWARENESS, months=1),
                 main.ConstanzaEstimateRequest(product_name=AWARENESS, months=1, meta=main.MetaEstimateModel()),
                 _body("meta", meta=main.MetaEstimateModel(age_min=25))):
        with pytest.raises(HTTPException) as err:
            main.constanza_estimate(body)
        assert err.value.status_code == 422 and err.value.detail == "Choose where first: pick a place from Meta's list."
    assert graph.calls == []                                                                               # nothing was asked of Meta


def test_the_meta_request_models_refuse_what_they_do_not_know_and_cap_every_list():
    base = copy.deepcopy(FAKES["meta"].sample_body["meta"])
    main.MetaEstimateModel(**base)
    for bad in ({"surprise": 1}, {"geo": {**base["geo"], "extra": 1}}, {"languages": [{"id": "1", "name": "x", "extra": 1}]},
                {"targeting": [{"id": "1", "name": "x", "type": "interests", "extra": 1}]},
                {"languages": [{"id": "1", "name": "x"}] * 51}, {"targeting": [{"id": "1", "name": "x", "type": "interests"}] * 51},
                {"gender": "x" * 11}, {"geo": {"type": "x" * 21, "key": "1"}}, {"geo": {"type": "city", "key": "1" * 41}},
                {"geo": {"type": "city", "key": "1", "label": "x" * 161}}):
        with pytest.raises(ValidationError):
            main.MetaEstimateModel(**{**base, **bad})
    main.ConstanzaEstimateRequest(product_name=AWARENESS, surprise=1)                  # a tab from before a deploy may send what it likes at the top level


def test_a_registered_method_with_another_block_in_the_body_ignores_it(monkeypatch):
    """The dispatch recognises the method from the product's configuration, never from the body: a TikTok product that is sent a Meta block."""
    graph = FAKES["tiktok"].install(monkeypatch)
    out = main.constanza_estimate(_body("tiktok", meta=main.MetaEstimateModel(geo={"type": "city", "key": "9"})))
    assert out["method"] == "tiktok" and "meta" not in out
    assert graph.fake.count("/ad/audience_size/estimate/") == 1


def test_the_platform_and_population_share_answers_say_which_method_made_them():
    plain = main.constanza_estimate(main.ConstanzaEstimateRequest(product_name=ROKU, geo_text="Los Angeles", months=1))
    assert plain["method"] == "platform" and "audience" not in plain
    narrowed = main.constanza_estimate(main.ConstanzaEstimateRequest(
        product_name=ROKU, geo_text="Los Angeles", months=1, audience=main.ConstanzaAudienceSpecModel(age_min=25, age_max=44, gender="female")))
    assert narrowed["method"] == "platform" and "audience" in narrowed
    share = main.constanza_estimate(main.ConstanzaEstimateRequest(product_name=VIX, geo_text="Los Angeles", months=1, national_monthly_total=5_000_000))
    assert share["method"] == "generic"
    share_aud = main.constanza_estimate(main.ConstanzaEstimateRequest(
        product_name=VIX, geo_text="Los Angeles", months=1, national_monthly_total=5_000_000, audience=main.ConstanzaAudienceSpecModel(age_min=25)))
    assert share_aud["method"] == "generic"


def test_a_tiktok_answer_is_what_it_was_before_the_registry_plus_the_method_key(monkeypatch):
    FAKES["tiktok"].install(monkeypatch)
    out = main.constanza_estimate(_body("tiktok"))
    assert set(out) == {"method", "product_name", "platform", "confidence", "geo_method", "geo_confidence", "months", "imps_low", "imps_mid", "imps_high",
                        "est_uniques", "reach_universe", "series", "weeks", "notes", "unmatched_geo_tokens", "unmatched_geo_details", "ambiguous",
                        "geo_interpretations", "geo_suggestions", "by_area", "hispanic_targeting", "tiktok"}
    assert set(out["tiktok"]) == {"audience", "stage", "stage_label", "stage_note", "impressions_per_person", "scenarios", "monthly", "locations", "unmapped",
                                  "objective", "placements", "request_id", "cached"}
    assert out["method"] == "tiktok" and out["platform"] == "tiktok" and out["confidence"] == "live_api" and out["est_uniques"] == out["tiktok"]["audience"]["low"]


def test_a_method_that_raises_a_value_error_is_a_422_with_its_words_and_one_that_raises_its_own_error_gets_its_status(echo, monkeypatch):
    class Refused(Exception):
        status_code = 418

    def fail(kind):
        def estimate(req):
            raise kind("Pick a place first.") if kind is ValueError else kind("the platform said no")
        return estimate
    for kind, status, detail in ((ValueError, 422, "Pick a place first."), (Refused, 418, "the platform said no")):
        echo("echo", estimate=fail(kind), errors=(Refused,), to_http=lambda e: (e.status_code, str(e)))
        monkeypatch.setattr(fm, "load_overrides", lambda: {SEARCH: {"product_name": SEARCH, "enabled": True, "method": "echo", "platform": None}})
        with pytest.raises(HTTPException) as err:
            main.constanza_estimate(main.ConstanzaEstimateRequest(product_name=SEARCH, months=1))
        assert (err.value.status_code, err.value.detail) == (status, detail)
        methods.unregister("echo")
    echo("echo", estimate=fail(Refused), errors=(Refused,))                                           # no to_http: 502 with the error's text
    with pytest.raises(HTTPException) as err:
        main.constanza_estimate(main.ConstanzaEstimateRequest(product_name=SEARCH, months=1))
    assert (err.value.status_code, err.value.detail) == (502, "the platform said no")


def test_a_method_that_answers_in_the_wrong_shape_is_a_500_with_the_reason_logged_and_none_of_it_shown(echo, monkeypatch, caplog):
    echo("echo", estimate=lambda req: {"method": "echo", "imps_low": 1})
    monkeypatch.setattr(fm, "load_overrides", lambda: {SEARCH: {"product_name": SEARCH, "enabled": True, "method": "echo", "platform": None}})
    with caplog.at_level(logging.ERROR, logger=main.__name__):
        with pytest.raises(HTTPException) as err:
            main.constanza_estimate(main.ConstanzaEstimateRequest(product_name=SEARCH, months=1))
    assert err.value.status_code == 500 and "can't read" in err.value.detail and "product_name" not in err.value.detail
    assert any("echo" in r.getMessage() and "without:" in r.getMessage() for r in caplog.records)


def test_a_throw_away_method_runs_through_the_same_route_and_a_product_can_be_mapped_to_it(echo, monkeypatch):
    echo()
    monkeypatch.setattr(fm, "load_overrides", lambda: {SEARCH: {"product_name": SEARCH, "enabled": True, "method": "echo", "platform": None}})
    out = main.constanza_estimate(main.ConstanzaEstimateRequest(product_name=SEARCH, months=3))
    assert out["method"] == "echo" and out["echo"] == {"asked": {}} and out["months"] == 3 and out["notes"] == ["said back"]
    assert fm.resolve_forecast(SEARCH).method == "echo"


def test_a_hispanic_ask_is_said_not_to_apply_to_a_meta_estimate_and_changes_no_number(monkeypatch):
    FAKES["meta"].install(monkeypatch)
    plain = main.constanza_estimate(_body("meta"))
    for extra in ({"hispanic": True}, {"audience": main.ConstanzaAudienceSpecModel(hispanic=True)},
                  {"audience": main.ConstanzaAudienceSpecModel(interest_terms=["Hispanic"])}):
        asked = main.constanza_estimate(_body("meta", **extra))
        assert any("Hispanic targeting isn't applied to a Meta audience estimate" in n for n in asked["notes"]), extra
        assert asked["hispanic_targeting"] == {"default": False, "on": False, "applies": False}
        assert asked["imps_mid"] == plain["imps_mid"]
    assert not any("Hispanic" in n for n in plain["notes"])


# --------------------------------------------------------------------------------------------------------------------------- a refused estimate leaks nothing

def test_no_estimate_status_or_connection_test_answer_carries_a_secret(monkeypatch):
    graph = FAKES["meta"].install(monkeypatch)
    monkeypatch.setenv("META_APP_SECRET", "APP-SECRET-PLANTED-0042")
    secrets = [TOKEN, "APP-SECRET-PLANTED-0042", "7000000000012345"]
    seen = [json.dumps(main.constanza_estimate(_body("meta"))), json.dumps(main.constanza_meta_status()), json.dumps(main.admin_constanza_test("meta")),
            json.dumps(asyncio.run(main.admin_constanza_methods())), json.dumps(asyncio.run(main.admin_get_forecast_map()))]
    meta.reset_cache()                                                 # (an identical estimate is answered from memory: ask Meta again to get its refusal)
    graph.error, graph.http_status = {"message": f"bad token {TOKEN} for act_7000000000012345 {'APP-SECRET-PLANTED-0042'}", "code": 100}, 400
    with pytest.raises(HTTPException) as err:
        main.constanza_estimate(_body("meta"))
    seen.append(err.value.detail)
    for blob in seen:
        for secret in secrets:
            assert secret not in blob, secret


# --------------------------------------------------------------------------------------------------------------------------- products and the map

def test_the_three_paid_meta_skus_use_the_meta_method_by_default_with_no_row_and_the_branded_ones_stay_off():
    forecast = fm.forecast_for_products([p for p in catalog.effective_catalog() if p.name in (AWARENESS, TRAFFIC, LEAD_GEN, *BRANDED)])
    for name, cpm in ((AWARENESS, 12.0), (TRAFFIC, 18.0), (LEAD_GEN, 35.0)):
        assert forecast[name] == {"enabled": True, "method": "meta", "platform": None, "hispanic_weighted": False, "source": "default", "relation": "primary",
                                  "win_rate": False, "win_rate_override": None, "hispanic_default": False, "info": {"retail_cpm": cpm}}
    for name in BRANDED:
        assert forecast[name]["enabled"] is False and forecast[name]["method"] == "generic" and "info" not in forecast[name]
    assert "info" not in fm.forecast_for_products([p for p in catalog.effective_catalog() if p.name == TIKTOK])[TIKTOK]       # TikTok has no per-product facts


@pytest.mark.parametrize("hook,why", [
    (lambda name: (_ for _ in ()).throw(RuntimeError("db lookup failed")), "db lookup failed"),
    (lambda name: ["retail_cpm", 12.0], "list, not an object"),
    (lambda name: {"retail_cpm": float("nan")}, "nan"),
    (lambda name: {"retail_cpm": {1, 2}}, "set"),
])
def test_a_product_info_hook_that_fails_costs_its_product_the_info_and_is_logged_never_a_500_on_the_catalog(hook, why, echo, caplog):
    """The hook runs inside GET /api/catalog, which every planner session loads: one method's bug must not take the product list down."""
    echo(default_products=(SEARCH,), product_info=hook)
    with caplog.at_level(logging.WARNING, logger=fm.log.name):
        forecast = fm.forecast_for_products([p for p in catalog.effective_catalog() if p.name in (SEARCH, AWARENESS, TIKTOK)])
        data = asyncio.run(main.get_catalog())
    assert forecast[SEARCH]["method"] == "echo" and "info" not in forecast[SEARCH]
    assert forecast[AWARENESS]["info"] == {"retail_cpm": 12.0} and "info" not in forecast[TIKTOK]          # the other methods' products are untouched
    assert any("'echo'" in r.getMessage() and SEARCH in r.getMessage() for r in caplog.records)
    products = {p["name"]: p for family in data["products_by_family"].values() for p in family}
    assert "info" not in products[SEARCH]["forecast"] and products[AWARENESS]["forecast"]["info"] == {"retail_cpm": 12.0}


def test_a_product_info_hook_that_answers_an_object_is_still_carried(echo):
    echo(default_products=(SEARCH,), product_info=lambda name: {"note": name, "n": 3})
    assert fm.forecast_for_products([p for p in catalog.effective_catalog() if p.name == SEARCH])[SEARCH]["info"] == {"note": SEARCH, "n": 3}


def test_the_catalog_payload_carries_the_planning_cpm_both_ways_and_the_multipliers():
    data = asyncio.run(main.get_catalog())
    products = {p["name"]: p for family in data["products_by_family"].values() for p in family}
    for name, cpm in ((AWARENESS, 12.0), (TRAFFIC, 18.0), (LEAD_GEN, 35.0)):
        assert products[name]["meta_retail_cpm"] == cpm == products[name]["forecast"]["info"]["retail_cpm"]      # the old field stays for one release
    assert all(products[name]["meta_retail_cpm"] is None for name in BRANDED)
    assert data["audience_multipliers"] == {"conservative": 4.3, "mid": 5.5, "upper": 8.6}


def test_an_admin_row_naming_meta_is_valid_and_one_naming_a_method_nobody_owns_is_ignored_with_the_warning(monkeypatch, caplog):
    assert fm._valid_row({"method": "meta", "platform": None, "enabled": True})
    assert fm._valid_row({"method": "tiktok", "platform": None, "enabled": True})
    assert not fm._valid_row({"method": "teleport", "platform": None, "enabled": True})
    rows = [{"product_name": SEARCH, "enabled": True, "method": "meta", "platform": None, "hispanic_weighted": False, "win_rate": None},
            {"product_name": ROKU, "enabled": True, "method": "teleport", "platform": None, "hispanic_weighted": False, "win_rate": None}]
    monkeypatch.setattr(fm, "load_overrides", REAL_LOAD_OVERRIDES)      # the autouse fixture replaced it: read the real table again
    monkeypatch.setattr(fm, "_read_rows", lambda: rows)
    fm.invalidate()
    with caplog.at_level(logging.WARNING, logger=fm.log.name):
        loaded = fm.load_overrides()
    assert list(loaded) == [SEARCH] and fm.resolve_forecast(SEARCH).method == "meta"
    assert fm.resolve_forecast(ROKU).source == "default" and fm.resolve_forecast(ROKU).method == "platform"
    assert any("ignoring invalid forecast mapping" in r.getMessage() and "teleport" in r.getMessage() for r in caplog.records)


def test_a_save_that_names_a_method_is_checked_against_the_registry_and_the_message_lists_the_choices(echo):
    assert fm.clean_config({"enabled": True, "method": "meta", "platform": "roku", "hispanic_weighted": True}) == \
        {"enabled": True, "method": "meta", "platform": None, "hispanic_weighted": False, "win_rate": None}
    with pytest.raises(ValueError) as err:
        fm.clean_config({"enabled": True, "method": "echo"})
    labels = [spec.label for spec in methods.all()]
    assert str(err.value).startswith("Choose what the button computes: " + ", ".join(["platform data", "a population-share estimate"] + labels[:-1]) + " or " + labels[-1] + ".")
    echo()
    assert fm.clean_config({"enabled": True, "method": "echo"})["method"] == "echo"
    with pytest.raises(ValueError) as err:
        fm.clean_config({"enabled": True, "method": "nope"})
    labels = [spec.label for spec in methods.all()]
    assert labels[-1] == "Echo (live)" and ", ".join(labels[:-1]) + " or Echo (live)" in str(err.value)


def test_a_meta_method_mapped_to_a_product_that_is_not_a_meta_sku_has_no_planning_cpm_until_the_request_gives_one(monkeypatch):
    graph = FAKES["meta"].install(monkeypatch)
    monkeypatch.setattr(fm, "load_overrides", lambda: {SEARCH: {"product_name": SEARCH, "enabled": True, "method": "meta", "platform": None}})
    assert fm.forecast_for_products([p for p in catalog.effective_catalog() if p.name == SEARCH])[SEARCH]["info"] == {"retail_cpm": None}
    block = copy.deepcopy(FAKES["meta"].sample_body["meta"])
    out = main.constanza_estimate(main.ConstanzaEstimateRequest(product_name=SEARCH, months=2, meta=block))
    assert out["method"] == "meta" and out["meta"]["budget_check"] is None and out["meta"]["scenarios"]["retail_spend_at_flight"] is None
    assert not any("Budget impressions" in n for n in out["notes"])
    block["retail_cpm"] = 20.0
    out = main.constanza_estimate(main.ConstanzaEstimateRequest(product_name=SEARCH, months=2, meta=block))
    assert out["meta"]["budget_check"]["retail_cpm"] == 20.0 and out["meta"]["budget_check"]["budget_impressions"] == 100_000
    assert graph.calls                                                                                      # (and it asked Meta both times)


# --------------------------------------------------------------------------------------------------------------------------- Admin

def test_admin_gets_the_methods_with_everything_a_card_and_a_select_need_and_the_summary_counts_each_one():
    payload = asyncio.run(main.admin_get_forecast_map())
    listed = payload["methods"]
    assert [m["key"] for m in listed] == BUILT_IN + list(METHODS_ON_LOAD)
    assert [m["builtin"] for m in listed] == [True, True] + [False] * len(METHODS_ON_LOAD)
    labels = {m["key"]: m["label"] for m in listed}
    assert labels["platform"] == "Platform data" and labels["generic"] == "Population share"
    assert labels["tiktok"] == "TikTok API (live)" and labels["meta"] == "Meta API (live)"
    for m in listed:
        assert set(m) >= {"key", "label", "short_label", "kind", "builtin", "description", "secrets", "status_url", "test_url", "default_products", "summary_text"}, m["key"]
    tiktok, meta_row = (next(m for m in listed if m["key"] == key) for key in ("tiktok", "meta"))
    assert (tiktok["status_url"], tiktok["test_url"], tiktok["secrets"], tiktok["kind"]) == (
        "/api/constanza/tiktok/status", "/api/admin/constanza/tiktok/test", ["TIKTOK_ACCESS_TOKEN", "TIKTOK_ADVERTISER_ID"], "api")
    assert (meta_row["status_url"], meta_row["test_url"], meta_row["secrets"], meta_row["owns_geography"]) == (
        "/api/constanza/meta/status", "/api/admin/constanza/meta/test", ["META_ACCESS_TOKEN", "META_AD_ACCOUNT_ID"], True)
    assert meta_row["default_products"] == [AWARENESS, TRAFFIC, LEAD_GEN] and tiktok["default_products"] == [TIKTOK]
    assert meta_row["summary_text"] == "a live audience-size estimate from Meta"
    assert all(m["status_url"] is None and m["test_url"] is None and m["secrets"] == [] for m in listed[:2])             # a built-in has nothing to connect
    assert all(isinstance(m["owns_geography"], bool) for m in listed)                                                    # every entry has the flag, so a reader never branches on its absence
    assert {m["key"]: m["owns_geography"] for m in listed if m["key"] in ("platform", "generic", "tiktok", "meta")} == {"platform": False, "generic": False, "tiktok": False, "meta": True}
    assert payload["summary"]["meta"] == 3 and payload["summary"]["tiktok"] == 1
    rows = {r["name"]: r for r in payload["products"]}
    assert all(rows[n]["method"] == "meta" and rows[n]["enabled"] and not rows[n]["customized"] for n in (AWARENESS, TRAFFIC, LEAD_GEN))
    assert all(rows[n]["default"]["method"] == "meta" for n in (AWARENESS, TRAFFIC, LEAD_GEN))


def test_the_light_methods_route_answers_without_the_catalog_or_the_database(monkeypatch):
    def down(*args, **kwargs):
        raise RuntimeError("the database is down")
    monkeypatch.setattr(fm, "_read_rows", down)
    monkeypatch.setattr(fm, "get_connection", down)
    monkeypatch.setattr(catalog, "effective_catalog", down)
    monkeypatch.setattr(main, "effective_catalog", down)
    out = asyncio.run(main.admin_constanza_methods())
    assert out == {"methods": fm.methods_payload()} and [m["key"] for m in out["methods"]] == BUILT_IN + list(METHODS_ON_LOAD)
    with pytest.raises(RuntimeError):                                                 # (the heavy payload does need them: this is why the light route exists)
        asyncio.run(main.admin_get_forecast_map())


def test_the_admin_routes_exist_and_the_old_tiktok_test_path_stays():
    routes = {(path, method) for route in main.app.routes for path in [getattr(route, "path", "")] for method in getattr(route, "methods", ()) or ()}
    assert ("/api/admin/constanza/methods", "GET") in routes
    assert ("/api/admin/constanza/{key}/test", "POST") in routes
    assert ("/api/admin/tiktok/test", "POST") in routes and ("/api/admin/forecast-map", "GET") in routes
    assert ("/api/constanza/meta/status", "GET") in routes and ("/api/constanza/tiktok/status", "GET") in routes
    assert ("/api/constanza/meta-forecast", "POST") in routes                               # kept one release


@pytest.mark.parametrize("key", ["meta", "tiktok"])
def test_the_connection_test_of_a_method_runs_its_checks_and_answers_ok_checks_status_without_a_secret(key, monkeypatch):
    FAKES[key].install(monkeypatch)
    out = main.admin_constanza_test(key)
    assert set(out) == {"ok", "checks", "status"} and out["ok"] is True and len(out["checks"]) >= 5
    assert all(set(c) == {"name", "ok", "warn", "detail", "seconds"} for c in out["checks"])
    assert out["status"]["configured"] is True and out["status"]["account"]
    blob = json.dumps(out)
    for secret in (TOKEN, "7000000000012345", "test-token-123", "7000000000000000001"):
        assert secret not in blob


@pytest.mark.parametrize("key,secret", [("meta", "META_ACCESS_TOKEN"), ("tiktok", "TIKTOK_ACCESS_TOKEN")])
def test_the_connection_test_says_which_secret_is_missing_when_the_method_is_not_set_up(key, secret, monkeypatch):
    for name in ("META_ACCESS_TOKEN", "META_AD_ACCOUNT_ID", "TIKTOK_ACCESS_TOKEN", "TIKTOK_ADVERTISER_ID"):
        monkeypatch.delenv(name, raising=False)
    out = main.admin_constanza_test(key)
    assert out["ok"] is False and len(out["checks"]) == 1 and secret in out["checks"][0]["detail"]
    assert out["status"]["configured"] is False and out["status"]["missing"][0].split()[0] == secret


@pytest.mark.parametrize("key", ["nothing", "platform", "generic", "constructor", "META", ""])
def test_an_unknown_method_has_no_connection_test(key):
    with pytest.raises(HTTPException) as err:
        main.admin_constanza_test(key)
    assert err.value.status_code == 404


def test_the_old_tiktok_test_function_is_the_same_test_under_the_old_name(monkeypatch):
    FAKES["tiktok"].install(monkeypatch)
    old, new = main.admin_tiktok_test(), main.admin_constanza_test("tiktok")
    assert [c["name"] for c in old["checks"]] == [c["name"] for c in new["checks"]] and old["ok"] is new["ok"] is True and old["status"] == new["status"]


def test_a_second_connection_test_while_one_runs_is_a_429_and_the_next_one_after_it_runs_fine(monkeypatch):
    started, release = threading.Event(), threading.Event()
    calls = []

    def slow(key):
        calls.append(key)
        started.set()
        release.wait(10)
        return [Check("Slow", True, "done")]
    monkeypatch.setattr(methods, "run_diagnose", slow)
    for name in ("META_ACCESS_TOKEN", "META_AD_ACCOUNT_ID"):
        monkeypatch.delenv(name, raising=False)
    result: dict = {}
    first = threading.Thread(target=lambda: result.update(main.admin_constanza_test("meta")))
    first.start()
    try:
        assert started.wait(10)
        with pytest.raises(HTTPException) as err:
            main.admin_constanza_test("meta")
        assert err.value.status_code == 429 and err.value.detail == "A connection test is already running. Wait for it to finish, then try again."
        assert main.admin_constanza_test("tiktok")["checks"][0]["name"] == "Slow"            # another method is not held up
    finally:
        release.set()
        first.join(10)
    assert result["checks"][0]["name"] == "Slow" and result["ok"] is True
    assert main.admin_constanza_test("meta")["ok"] is True and calls.count("meta") == 2


def test_the_registry_redacts_the_secrets_a_method_names_from_every_line_of_its_connection_test(echo, monkeypatch):
    """The net under a method's own diagnose: whatever it prints, the value of each variable in MethodSpec.secrets does not leave. An account of
    digits is left as its last four; any other secret becomes its name; a value too short to find safely is left alone."""
    echo(secrets=("ECHO_TOKEN", "ECHO_ACCOUNT"), diagnose="tests.method_fakes:echo_leaky_checks")
    monkeypatch.setenv("ECHO_TOKEN", "PLANTED-ECHO-TOKEN-8842")
    monkeypatch.setenv("ECHO_ACCOUNT", "act_9100000000054321")
    first, second = methods.run_diagnose("echo")
    assert first.detail == "account act_…4321 (bare …4321) token [ECHO_TOKEN]" and first.ok and first.name == "Echo account"
    assert second.detail == "refused [ECHO_TOKEN]" and not second.ok and second.warn and second.seconds == 1.5          # nothing but the detail changes
    assert "PLANTED-ECHO-TOKEN-8842" not in json.dumps([c.to_public() for c in methods.run_diagnose("echo")])
    monkeypatch.setenv("ECHO_TOKEN", "tok")                                                                           # too short to look for
    monkeypatch.delenv("ECHO_ACCOUNT")
    assert methods.run_diagnose("echo")[0].detail == "account None (bare ) token tok"
    answer = main.admin_constanza_test("echo")
    assert answer["checks"][0]["detail"] == "account None (bare ) token tok" and answer["ok"] is False


# --------------------------------------------------------------------------------------------------------------------------- over HTTP

@pytest.fixture
def http(monkeypatch):
    """The real app over HTTP (routing, the request models, the login and admin rules) with a stubbed session and no database: http(admin=True)."""
    try:
        from fastapi.testclient import TestClient
    except (ImportError, RuntimeError) as exc:                                                 # Starlette's test client needs httpx installed
        pytest.skip(f"no HTTP test client here: {exc}")

    def make(admin: bool):
        monkeypatch.setattr(main.auth_svc, "get_user_by_session", lambda token: {"id": 1, "email": "planner@example.com", "is_admin": admin})
        monkeypatch.setattr(main.auth_svc, "refresh_session", lambda token: None)
        return TestClient(main.app, cookies={main.SESSION_COOKIE_NAME: "test-session"})
    return make


def test_a_meta_estimate_over_http_is_the_standard_estimate_with_no_secret_in_it(http, monkeypatch):
    FAKES["meta"].install(monkeypatch)
    answer = http(admin=False).post("/api/constanza/estimate", json=FAKES["meta"].sample_body)
    assert answer.status_code == 200, answer.text
    out = answer.json()
    assert STANDARD | {"meta"} <= set(out) and out["method"] == "meta" and out["meta"]["scenarios"]["flight"]["mid"] == out["imps_mid"]
    assert TOKEN not in answer.text and "7000000000012345" not in answer.text                   # (the ad account shows as its last four digits at most)


def test_a_refused_pick_in_the_meta_block_is_a_422_that_names_where_in_the_block_it_is(http, monkeypatch):
    graph = FAKES["meta"].install(monkeypatch)
    client = http(admin=False)
    body = copy.deepcopy(FAKES["meta"].sample_body)
    body["meta"]["targeting"].append({"id": "2", "name": "n" * 121, "type": "interests"})
    answer = client.post("/api/constanza/estimate", json=body)
    assert answer.status_code == 422 and answer.json()["detail"][0]["loc"] == ["body", "meta", "targeting", 1, "name"]
    body = copy.deepcopy(FAKES["meta"].sample_body)
    body["meta"]["geo"]["surprise"] = 1
    answer = client.post("/api/constanza/estimate", json=body)
    assert answer.status_code == 422 and answer.json()["detail"][0]["loc"] == ["body", "meta", "geo", "surprise"]
    body = copy.deepcopy(FAKES["meta"].sample_body)
    body["meta"]["languages"] = [{"id": str(i), "name": "x"} for i in range(51)]
    assert client.post("/api/constanza/estimate", json=body).status_code == 422                 # more than the model's 50
    assert graph.calls == []


def test_a_field_this_page_does_not_know_at_the_top_of_the_request_is_still_ignored_for_a_tab_from_before_a_deploy(http, monkeypatch):
    FAKES["meta"].install(monkeypatch)
    body = {**copy.deepcopy(FAKES["meta"].sample_body), "a_field_a_later_release_adds": {"x": 1}}
    assert http(admin=False).post("/api/constanza/estimate", json=body).status_code == 200


@pytest.mark.parametrize("field,loc", [
    ('"months": NaN', ["body", "months"]), ('"months": Infinity', ["body", "months"]), ('"months": 1e999', ["body", "months"]),
    ('"audience": {"age_min": Infinity}', ["body", "audience", "age_min"]), ('"tiktok": {"video_period": NaN}', ["body", "tiktok", "video_period"]),
    ('"meta": {"age_min": 1e999}', ["body", "meta", "age_min"]), ('"meta": {"age_max": -Infinity}', ["body", "meta", "age_max"]),
])
def test_a_number_json_cannot_hold_in_a_whole_number_field_is_a_422_that_does_not_echo_it_not_a_500(field, loc, http, monkeypatch):
    """NaN / Infinity / 1e999 are not JSON (a browser never writes them) but Python's parser reads them: the 422 used to carry the value back in
    `input` and the response encoder could not write it, so the client got an 'Internal Server Error' and the server a stack trace."""
    FAKES["meta"].install(monkeypatch)
    http(admin=False)                                                                            # (the session stub)
    from fastapi.testclient import TestClient
    client = TestClient(main.app, cookies={main.SESSION_COOKIE_NAME: "test-session"}, raise_server_exceptions=False)
    answer = client.post("/api/constanza/estimate", content='{"product_name": "Tiktok Ads", %s}' % field, headers={"content-type": "application/json"})
    assert answer.status_code == 422, answer.text
    errors = answer.json()["detail"]
    assert errors[0]["loc"] == loc and errors[0]["msg"] and errors[0]["type"]                    # where and why are still said
    assert all("input" not in e and "ctx" not in e for e in errors) and "inf" not in answer.text.lower() and "nan" not in answer.text.lower()


def test_a_meta_product_asked_with_no_place_and_no_geo_text_key_at_all_is_answered_in_words_over_http(http, monkeypatch):
    graph = FAKES["meta"].install(monkeypatch)
    answer = http(admin=False).post("/api/constanza/estimate", json={"product_name": AWARENESS, "months": 1})
    assert answer.status_code == 422 and answer.json()["detail"] == "Choose where first: pick a place from Meta's list." and graph.calls == []


def test_the_admin_method_routes_are_for_admins_only_and_the_list_needs_nothing_else(http, monkeypatch):
    FAKES["meta"].install(monkeypatch)
    for method, path in (("get", "/api/admin/constanza/methods"), ("post", "/api/admin/constanza/meta/test"), ("post", "/api/admin/tiktok/test")):
        refused = getattr(http(admin=False), method)(path)
        assert refused.status_code == 403, path
    admin = http(admin=True)
    listed = admin.get("/api/admin/constanza/methods")
    assert listed.status_code == 200 and [m["key"] for m in listed.json()["methods"]] == BUILT_IN + list(METHODS_ON_LOAD)
    tested = admin.post("/api/admin/constanza/meta/test")
    assert tested.status_code == 200 and set(tested.json()) == {"ok", "checks", "status"} and tested.json()["ok"] is True
    assert TOKEN not in tested.text and "7000000000012345" not in tested.text
    assert admin.post("/api/admin/constanza/nothing/test").status_code == 404
    assert admin.post("/api/admin/constanza/platform/test").status_code == 404


def test_the_connection_tests_are_plain_functions_so_their_dozen_blocking_calls_run_in_a_worker_thread_not_on_the_event_loop():
    """(An `async def` here would freeze every other request while Meta or TikTok answers; the TestClient cannot show that, its clients each have a loop.)"""
    import inspect
    assert not inspect.iscoroutinefunction(main.admin_constanza_test) and not inspect.iscoroutinefunction(main.admin_tiktok_test)


def test_the_old_tiktok_test_path_still_answers_over_http(http, monkeypatch):
    FAKES["tiktok"].install(monkeypatch)
    old = http(admin=True).post("/api/admin/tiktok/test")
    assert old.status_code == 200 and old.json()["ok"] is True and "test-token-123" not in old.text


def test_two_admins_pressing_test_at_once_get_one_test_and_one_429_over_http(http, monkeypatch):
    """Two clients, two requests in flight at once: the second really arrives while the first is still inside the method's calls."""
    started, release = threading.Event(), threading.Event()

    def slow(key):
        started.set()
        release.wait(10)
        return [Check("Slow", True, "done")]
    monkeypatch.setattr(methods, "run_diagnose", slow)
    first_client, second_client = http(admin=True), http(admin=True)
    first: dict = {}
    thread = threading.Thread(target=lambda: first.update(response=first_client.post("/api/admin/constanza/meta/test")))
    thread.start()
    try:
        assert started.wait(10)
        refused = second_client.post("/api/admin/constanza/meta/test")
        assert refused.status_code == 429 and "already running" in refused.json()["detail"]
    finally:
        release.set()
        thread.join(10)
    assert first["response"].status_code == 200 and first["response"].json()["checks"][0]["name"] == "Slow"
    assert second_client.post("/api/admin/constanza/meta/test").status_code == 200                # the lock was let go


# --------------------------------------------------------------------------------------------------------------------------- AvailsEntry

def _snapshot(**changes):
    snap = {"v": 1, "method": "meta", "saved_at": "2026-11-03T12:00:00+00:00", "request": {"product_name": AWARENESS, "months": 2},
            "result": {"method": "meta", "imps_mid": 3300}, "line": {"product_name": AWARENESS, "months": 2, "budget": 2000}}
    snap.update(changes)
    return snap


def _snapshot_of_bytes(size):
    snap = _snapshot(result={"pad": ""})
    snap["result"]["pad"] = "x" * (size - len(json.dumps(snap, separators=(",", ":"), ensure_ascii=False).encode("utf-8")))
    assert len(json.dumps(snap, separators=(",", ":"), ensure_ascii=False).encode("utf-8")) == size
    return snap


def test_an_avails_entry_keeps_the_legacy_meta_field_and_gains_the_one_snapshot_field():
    assert {"meta_forecast", "forecast_snapshot"} <= set(main.AvailsEntry.model_fields)
    legacy = {"metric_type": "meta_audience_scenarios", "months": 1, "scenarios": None}
    entry = main.AvailsEntry(max_imps=1, meta_forecast=legacy, forecast_snapshot=_snapshot())
    dumped = entry.model_dump()
    assert dumped["meta_forecast"] == legacy and dumped["forecast_snapshot"] == _snapshot()
    assert main.AvailsEntry(**dumped).model_dump() == dumped                           # a save -> reopen round trip is stable
    assert main.AvailsEntry().model_dump()["forecast_snapshot"] is None and main.AvailsEntry(forecast_snapshot=None).forecast_snapshot is None
    assert "surprise" not in main.AvailsEntry(surprise=3).model_dump()                 # an undeclared key is still dropped (which is why this one is declared)


def test_no_other_per_method_field_has_been_added_to_an_avails_entry():
    """A new source stores its estimate in forecast_snapshot; it never adds a field here."""
    per_method = [name for name in main.AvailsEntry.model_fields if name.endswith("_forecast") or name.endswith("_snapshot")]
    assert sorted(per_method) == ["forecast_snapshot", "meta_forecast"], "store the snapshot in forecast_snapshot"


@pytest.mark.parametrize("snapshot", [
    "text", 3, True, [1, 2], [], {}, None,
    _snapshot(v=None), _snapshot(v="1"), _snapshot(v=True), _snapshot(v=1.5), {k: v for k, v in _snapshot().items() if k != "v"},
    _snapshot(method=None), _snapshot(method=7), _snapshot(method="Meta"), _snapshot(method="m"), _snapshot(method="has space"), _snapshot(method="x" * 33),
    _snapshot(method="meta\n"),                                                           # (Python's `$` forgives a final newline; the database's pattern does not)
    {k: v for k, v in _snapshot().items() if k != "method"},
    _snapshot(request=[1]), _snapshot(request="x"), _snapshot(request=7), _snapshot(result=["x"]), _snapshot(result="a string"), _snapshot(result=True),
])
def test_a_saved_estimate_that_is_not_one_is_dropped_never_a_failed_save(snapshot):
    assert main.AvailsEntry(forecast_snapshot=snapshot).forecast_snapshot is None
    assert main.clean_forecast_snapshot(snapshot) is None


@pytest.mark.parametrize("snapshot", [
    _snapshot(), _snapshot(request=None, result=None), _snapshot(v=2), _snapshot(method="a_future_method2"),
    {"v": 1, "method": "meta"}, {"v": 1, "method": "meta", "request": {}, "result": {}, "unknown": [1, 2]},
])
def test_a_saved_estimate_of_the_right_shape_is_kept_as_it_came(snapshot):
    assert main.AvailsEntry(forecast_snapshot=snapshot).forecast_snapshot == snapshot


def test_a_saved_estimate_over_100_kb_is_dropped_and_one_at_the_limit_is_kept():
    assert main.MAX_FORECAST_SNAPSHOT_BYTES == 100 * 1024
    assert main.AvailsEntry(forecast_snapshot=_snapshot_of_bytes(main.MAX_FORECAST_SNAPSHOT_BYTES)).forecast_snapshot is not None
    assert main.AvailsEntry(forecast_snapshot=_snapshot_of_bytes(main.MAX_FORECAST_SNAPSHOT_BYTES + 1)).forecast_snapshot is None
    assert main.AvailsEntry(forecast_snapshot=_snapshot(result={"pad": "é" * 60_000})).forecast_snapshot is None       # 120 KB of bytes, only 60,000 characters
    assert main.AvailsEntry(max_imps=5, forecast_snapshot=_snapshot_of_bytes(200_000)).max_imps == 5                  # the rest of the entry is untouched


def test_a_snapshot_that_cannot_be_written_as_json_is_dropped():
    loop: dict = {}
    loop["self"] = loop
    assert main.clean_forecast_snapshot(_snapshot(result=loop)) is None
    assert main.clean_forecast_snapshot(_snapshot(result={"x": object()})) is None


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_a_snapshot_holding_nan_or_infinity_is_dropped_because_the_database_would_refuse_the_whole_save(bad):
    """Python writes NaN as a bare word, which is not JSON: Postgres rejects it, and the whole draft with it."""
    assert main.clean_forecast_snapshot(_snapshot(result={"audience": {"low": bad}})) is None
    assert main.AvailsEntry(max_imps=5, forecast_snapshot=_snapshot(request={"x": [bad]})).forecast_snapshot is None


# --------------------------------------------------------------------------------------------------------------------------- the migration

def test_the_database_checks_a_key_format_not_a_list_of_names_and_the_migration_says_the_same():
    schema = (ROOT / "schema.sql").read_text(encoding="utf-8")
    migration = (ROOT / "migrations" / "0006_forecast_map_method_open.sql").read_text(encoding="utf-8")
    table = re.search(r"CREATE TABLE IF NOT EXISTS forecast_product_map \((.*?)\n\);", schema, re.S).group(1)
    schema_check = re.search(r"method\s+TEXT NOT NULL CHECK \(method ~ '([^']+)'\)", table).group(1)
    migration_check = re.search(r"CHECK \(method ~ '([^']+)'\)", migration).group(1)
    assert schema_check == migration_check == methods.KEY_RE.pattern                       # one pattern in three places
    assert "IN (" not in table and "'tiktok'" not in table and "'platform'" not in table      # no method name is enumerated any more
    assert "DROP CONSTRAINT IF EXISTS forecast_product_map_method_check" in migration and "to_regclass('public.forecast_product_map')" in migration
    assert all(methods.KEY_RE.fullmatch(key) for key in methods.method_keys())
    assert not methods.KEY_RE.fullmatch("meta\n") and not re.fullmatch(schema_check, "meta\n")      # Postgres's `~` takes no final newline either
    for name in BUILT_IN + list(METHODS_ON_LOAD):                                           # every value any database may already hold passes the new check
        assert re.fullmatch(schema_check, name)


def test_migration_0006_is_the_next_migration_and_parses_as_one_statement():
    from app.services import migrations
    from app.services.schema_bootstrap import split_statements
    found = migrations.discover()
    assert [m.version for m in found] == [f"{n:04d}" for n in range(1, len(found) + 1)]
    latest = next(m for m in found if m.version == "0006")
    assert latest.name == "forecast_map_method_open" and len(split_statements(latest.sql)) == 1
    assert split_statements(latest.sql)[0].startswith("DO $$") and latest.sql.count("$$") == 2


def test_the_baseline_table_statement_still_parses_for_the_startup_bootstrap():
    from app.services.schema_bootstrap import idempotent_statements
    mine = [s for s in idempotent_statements((ROOT / "schema.sql").read_text(encoding="utf-8")) if s["target"] == "forecast_product_map"]
    assert [s["kind"] for s in mine] == ["table"]
    assert "'^[a-z][a-z0-9_]{1,31}$'" in re.sub(r"\s+", " ", mine[0]["sql"])


def test_the_modules_that_register_a_method_are_the_ones_listed_and_include_meta_and_tiktok():
    found = sorted(path.stem for path in (ROOT / "app" / "services").glob("*.py") if "constanza_methods.register(" in path.read_text(encoding="utf-8"))
    assert {"constanza_meta", "constanza_tiktok"} <= set(found)
    assert sorted(m.rsplit(".", 1)[1] for m in methods.MODULES) == found


def test_hostile_text_is_only_ever_planted_in_free_text_fields_of_the_fakes_answers():
    for key, fake in FAKES.items():
        plain, hostile = fake.answer(), fake.answer(hostile=True)
        planted = json.dumps(HOSTILE)[1:-1]                                  # (as it reads inside a JSON string)
        assert planted not in json.dumps(plain) and planted in json.dumps(hostile) and fake.hostile_text == HOSTILE
        methods.validate_standard(methods.get(key), hostile)
        assert hostile["method"] == key and hostile["months"] == plain["months"] and hostile["imps_mid"] == plain["imps_mid"]
