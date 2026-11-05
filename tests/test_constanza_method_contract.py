"""The contract every Constanza avails method has to meet, checked for every method the day it registers.

Constanza is the avails hub (docs/constanza_methods.md): TikTok, Meta and every source added later plug into one registry (app/services/constanza_methods.py)
and one browser contract (Constanza.CONTRACT in app/static/constanza.js). This file is the half of that rule that is ENFORCED rather than written down.
Each test below is parametrised over `constanza_methods.keys()`, so a new method is checked the day it is registered, and every failure says what to add:

    constanza_foo is registered but tests/method_fakes.py has no entry for "foo": add one (see docs/constanza_methods.md step 5)

How it is built. Each check is a plain function `check_x(key, ...) -> list[str]` (the problems it found, in words; [] = fine) that takes its inputs as
arguments. The real test calls it on the real code and asserts the list is empty; the NEGATIVE TWIN (`test_twin_*`) calls the very same function on a
deliberately broken dummy method, registered inside the test and taken out again, and asserts that the problem list names what is missing. A check that
could not fail would enforce nothing, and the twin is the proof it can. `test_every_check_has_a_twin` keeps it that way for the next check someone adds,
and an autouse fixture proves no twin leaves anything in the process-global registry or in the fakes.

The checks (C0-C15 of the design, docs/constanza_methods.md "Tests"):

  C0  import order          each module imports cleanly as the FIRST app import of a fresh interpreter, and the registry still answers
  C1  registration          key format, request_field, labels, secrets, a diagnose function that resolves, the standard status, the derived lists
  C2  no secret leaks       status, Admin payloads, the connection test and the estimate (answered and refused) carry no secret value
  C3  every module listed   a module under app/services that registers a method is in constanza_methods.MODULES
  C4  request model         `ConstanzaEstimateRequest.<key>`: a typed field, extra="forbid" all the way down, every list and string capped
  C5  routes                GET /api/constanza/<key>/status exists; a plugin's routes start with its own key (or are a shared one) and exist
  C6  the fake              tests/method_fakes.py has a strict entry for the key
  C7  the standard estimate main.constanza_estimate over the fake answers every standard key, in order, as JSON
  C8  the plugin's files    constanza-<key>.js / .css exist, are linked in index.html in the right order; no orphan files
  C9  plugin conformance    constanza.js + the plugin in a JS engine: registered, only contract members, one request block, escaped text, and a
                            restored result (asked.method is null) still drawn by bodyHtml / summaryLines with no throw, no undefined / NaN / null
  C10 app.js hygiene        the marked Constanza region and the Avails grid of app.js name no method in a code position
  C11 Admin                 methods[] in both Admin payloads, a test route per API method, no method spelled out in admin.html / admin.js
  C12 database and entry    the database checks a key FORMAT; AvailsEntry has the one forecast_snapshot field and no `<key>_forecast`
  C13 docs                  docs/constanza_methods.md has the registry row (and the headings) and docs/<key>_forecasting.md exists
  C14 hostile snapshots     a corrupt or hostile saved estimate never throws out of a card and never draws raw markup
  C15 reserved keys         `platform` / `generic` cannot be registered; a plugin's picker group cannot collide with a data channel; a bad plugin is refused

WHERE THIS DIFFERS FROM THE DESIGN (the design is the intent; the code that was built is the truth):
  * C2 plants the secrets in two ways: the values the method's own fake sets (the formats its validation needs, e.g. Meta's numeric ad account), searched
    in everything it returns, and odd values (a malformed token) that exercise the "not set up" messages. A refused estimate is produced by making the
    network layer (`requests`) answer 400 with every secret in its message, which only methods that go through `requests` can show; one that does not (TikTok's
    fake replaces the client itself) is checked on its answered estimate instead. The last four characters of an account are shown by design, so what is
    searched is the whole value.
  * C4 also caps plain strings and dicts (the existing request models do), and `extra="forbid"` is checked on every model nested in the field.
  * C5 adds a hygiene rule the Meta plugin's own test had: a plugin reaches the server only through `ctx.api` (no fetch, no URL, no eval).
  * C8 also checks the CSS <link> order against constanza.css / segment-browser.css, not only audience-kit.css, and flags a <link> to a file that is missing.
  * C9 hands the plugin the host's real `ctx` but does not mount it in a DOM (the stub page has none): formHtml / whereHtml are drawn as strings and
    `bind` is exercised by the browser suites (tests/test_constanza_*_dom.py). It also calls bodyHtml and summaryLines the way the host does for a RESTORED
    result whose form was edited since the pull (`asked = {geo: "", areas: [], method: null}`), on the sample and the hostile answer.
  * C10 reads `window.constanzaHost` with a small brace-aware parser (not a regex over indentation), strips comments before looking for names, and matches
    `.key` only on an estimate-like name (data, result, snapshot ...): a key such as `display` must not fail on `el.style.display`. It also flags the key written out as a
    string or an object key (`.includes(["key"])`, `.startsWith("key")`, `{ key: 1 }`) in app.js's section and grid, and in constanza.js, admin.js and admin.html (LITERAL_ALLOWED
    lists the exceptions, with reasons; a CSS word such as `display` is exempt from those two rules). tests/test_forecast_ui_wiring.py (package C) runs this very scan on the real
    app.js instead of keeping a copy; choose a key that is not an ordinary JavaScript property name (docs/constanza_methods.md, "Pitfalls").
  * C13 also checks the registry row cell by cell against the registration (label, secrets, files, owns geography, persists, default products), and that the
    guide's own minimal plugin conforms and lists every MethodSpec field and every Constanza.CONTRACT member (so the guide cannot drift from the code).
  * C14 runs in the JS engine through Constanza.renderCard for every persisting method; the Step 05 mount of one corrupt card among healthy ones is also run in a real
    browser for EVERY method (the last tests of this file, skipped without Playwright / Chromium), next to two generic flows that are not in the design's list: the dock offers
    the method's product in its own picker group with its own panel and pulls, and the Step 05 block of a line does what the method's `caps` say (estimate, card, Insert for a method
    that pulls from the proposal's geography; the one "Estimate in Constanza" button for one that is asked in the dock). They use the Step 05 harness of
    tests/test_constanza_step05_block_dom.py (imported lazily) with every registered method's script and css added to the page.

Set REQUIRE_JS_ENGINE=1 (CI must, once there is CI) to make a missing JavaScript engine a FAILURE instead of a skip: a skipped conformance test enforces
nothing. No JavaScript engine = neither Node nor macOS JavaScriptCore (`osascript`). The negative twin of that rule is in this file too.
"""
from __future__ import annotations

import ast
import asyncio
import contextlib
import copy
import dataclasses
import importlib
import json
import logging
import os
import re
import subprocess
import sys
import textwrap
import typing
from pathlib import Path

import annotated_types
import pytest
from fastapi import HTTPException
from pydantic import BaseModel, Field

import app.main as main
from app import catalog
from app.services import constanza_forecast_map as fm
from app.services import constanza_methods as methods
from app.services.constanza_methods import MethodRequest, MethodSpec
from tests import method_fakes
from tests.fixtures import meta_snapshots
from tests.method_fakes import FAKES, HOSTILE, MethodFake

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "static"
INDEX_HTML = ROOT / "app" / "templates" / "index.html"
GUIDE = ROOT / "docs" / "constanza_methods.md"
SERVICES = ROOT / "app" / "services"
SHARED_ROUTES = ("estimate", "resolve", "interests", "interest-taxonomy")      # the dock's own routes, which any plugin may rely on
KEYS = methods.keys()                                                         # evaluated at collection: tiktok, meta, and whatever registers next
DATA_CHANNELS = ("ctv", "platform_model", "live_sports", "netflix", "pause_ads", "olv", "display", "_generic")      # the picker's data groups (constanza.js)
ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{2,}$")


def assert_ok(check: str, key: str, problems: list[str]) -> None:
    assert not problems, f"{check} failed for method '{key}':\n  - " + "\n  - ".join(problems)


def _short(spec: MethodSpec) -> str:
    return getattr(spec.estimate, "__module__", "?").rsplit(".", 1)[-1]


# ================================================================================================================================ shared helpers

def strip_js_comments(src: str) -> str:
    """The source with its comments removed. Strings, template literals (holes included) and regex literals are left as they are, and every newline is
    kept, so a line number still means what it did. A reading aid for the checks that look for names in CODE, not a JavaScript parser."""
    out: list[str] = []
    n = len(src)
    i = 0
    word = ""                       # the identifier or number being read
    last = ("", "")                 # the last significant token: ("word" | "punct" | "value", text); decides whether a "/" starts a regex
    regex_after_words = ("return", "typeof", "case", "in", "of", "delete", "void", "throw", "else", "do", "yield", "await", "new")
    regex_after_punct = "(,=:[!&|?{};+-*%<>~^"

    def skip_string(j: int, quote: str) -> int:        # j: index of the opening quote -> index after the closing one
        j += 1
        while j < n and src[j] != quote:
            if src[j] == "\\":
                j += 1
            elif quote == "`" and src[j] == "$" and j + 1 < n and src[j + 1] == "{":
                j = skip_hole(j + 1)
                continue
            j += 1
        return j + 1

    def skip_hole(j: int) -> int:                      # j: index of the "{" of a ${ ... } -> index after its "}"
        depth = 0
        while j < n:
            ch = src[j]
            if ch in "'\"`":
                j = skip_string(j, ch)
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return j + 1
            j += 1
        return j

    while i < n:
        c = src[i]
        if c.isalnum() or c in "_$":
            word += c
            out.append(c)
            i += 1
            continue
        if word:
            last, word = ("word", word), ""
        nx = src[i + 1] if i + 1 < n else ""
        if c in "'\"`":
            j = skip_string(i, c)
            out.append(src[i:j])
            i, last = j, ("value", c)
            continue
        if c == "/" and nx == "/":
            j = src.find("\n", i)
            i = n if j < 0 else j
            continue
        if c == "/" and nx == "*":
            j = src.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append("\n" * src.count("\n", i, j))
            i = j
            continue
        starts_regex = last == ("", "") or (last[0] == "punct" and last[1] in regex_after_punct) or (last[0] == "word" and last[1] in regex_after_words)
        if c == "/" and starts_regex:
            j, in_class = i + 1, False
            while j < n:
                ch = src[j]
                if ch == "\\":
                    j += 2
                    continue
                if ch == "[":
                    in_class = True
                elif ch == "]":
                    in_class = False
                elif ch == "/" and not in_class:
                    break
                elif ch == "\n":
                    break
                j += 1
            out.append(src[i:j + 1])
            i, last = j + 1, ("value", "/")
            continue
        out.append(c)
        if not c.isspace():
            last = ("punct", c)
        i += 1
    return "".join(out)


_H = None


def _harness():
    """The JS-engine helpers of tests/test_full_flight_frontend.py (`_extract_fn`, `_run_js`, `_engine_command`), loaded the way the other frontend tests do."""
    global _H
    if _H is None:
        import importlib.util
        spec = importlib.util.spec_from_file_location("_ff_frontend_harness_contract", ROOT / "tests" / "test_full_flight_frontend.py")
        _H = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_H)
    return _H


def require_js_engine() -> bool:
    return os.environ.get("REQUIRE_JS_ENGINE") == "1"


def run_js(body: str):
    """Runs `body` (JavaScript that returns a JSON string) and parses what it printed. No usable engine: a SKIP, or - with REQUIRE_JS_ENGINE=1 - a FAILURE."""
    h = _harness()
    if h._engine_command() is None:
        if require_js_engine():
            pytest.fail("REQUIRE_JS_ENGINE=1, but there is neither Node nor macOS JavaScriptCore (osascript) here: the plugin checks cannot run, and a skipped "
                        "check enforces nothing. Install Node, or run CI on macOS.", pytrace=False)
        pytest.skip("neither node nor macOS JavaScriptCore (osascript) is available (set REQUIRE_JS_ENGINE=1 to make that a failure)")
    try:
        return json.loads(h._run_js(body))
    except AssertionError as exc:
        if "JS engine failed" in str(exc) and "osascript" in str(exc).lower():
            if require_js_engine():
                raise
            pytest.skip(f"JavaScript engine unusable here: {exc}")
        raise


def js_page(plugin_source: str, key: str, constanza_source: str | None = None) -> str:
    """The stub page the frontend tests use (no DOM), with the real constanza.js and `plugin_source` loaded; `__res[key]` says whether the host accepted the
    plugin, `__errors` collects what it logged, `Z` is window.Constanza and `P` the plugin the host holds for `key` (null when it was refused)."""
    h = _harness()
    app_js = (STATIC / "app.js").read_text(encoding="utf-8")
    outside = "\n".join(h._extract_fn(app_js, name) for name in ("escapeHtml", "_replaceGeoItem", "_constanzaErrorText"))
    host = constanza_source if constanza_source is not None else (STATIC / "constanza.js").read_text(encoding="utf-8")
    return (
        "var __errors = [];\n"
        "var console = { warn: function () {}, log: function () {}, error: function (m) { __errors.push(String(m)); } };\n"
        "var window = {};\n"
        "var document = { readyState: 'loading', addEventListener: function () {}, getElementById: function () { return null; },"
        " querySelectorAll: function () { return []; } };\n"
        "var state = { catalog: null };\n"
        + outside + "\n" + host + "\n"
        "var __reg = window.Constanza.registerMethod, __res = {};\n"
        "window.Constanza.registerMethod = function (n, f) { var ok = __reg(n, f); __res[n] = ok; return ok; };\n"
        + plugin_source + "\n"
        f"var KEY = {json.dumps(key)};\n"
        "var Z = window.Constanza, P = Z._plugin(KEY);\n"
        "function fakeEl() { return { style: {}, dataset: {}, innerHTML: '', textContent: '', hidden: false,"
        " classList: { add: function () {}, remove: function () {}, toggle: function () {}, contains: function () { return false; } },"
        " querySelector: function () { return fakeEl(); }, querySelectorAll: function () { return []; }, addEventListener: function () {},"
        " setAttribute: function () {}, getAttribute: function () { return null; }, removeAttribute: function () {}, insertAdjacentHTML: function () {},"
        " appendChild: function () {}, remove: function () {}, focus: function () {} }; }\n"
    )


def plugin_source_of(key: str) -> str:
    return (STATIC / f"constanza-{key}.js").read_text(encoding="utf-8")


# ================================================================================================================================ a throw-away method
# Registered INSIDE a test and taken out again (the registry is process-global: a leak would poison every other test). It is a method that says back what it
# was asked, with a fake of its own, and every field of it can be spoiled to make a check fail.

DUMMY_PRODUCT = "Search - SEM"
DUMMY_ANSWER_NOTE = "said back"
DUMMY_PLUGIN = """
(function () {
  "use strict";
  if (!window.Constanza || typeof window.Constanza.registerMethod !== "function") return;
  window.Constanza.registerMethod("dummy", function (ctx) {
    const esc = ctx.esc;
    const name = (d) => esc((d && d.product_name) || "");
    return {
      goLabel: "Estimate audience \\u2192", channelLabel: "Dummy \\u00b7 live estimate", sourceLabel: "Live from Dummy",
      formHtml: () => '<section class="aud-sec"><p>Dummy settings</p></section>', bind: () => {},
      requestPart: () => ({ dummy: {} }), context: () => ({ summary: [] }), reset: () => {},
      headlineHtml: (d) => `<div class="cz-headline">${name(d)}</div>`, bodyHtml: (d) => `<p>${name(d)}</p>`, aboutHtml: (d) => `<p>${name(d)}</p>`,
      cardHtml: (d) => `<p>${name(d)}</p>`, summaryLines: (d) => [String((d && d.product_name) || "")],
    };
  });
})();
"""


def make_dummy_spec(key: str = "dummy", **fields) -> MethodSpec:
    box: dict = {}

    def estimate(req: MethodRequest) -> dict:
        return methods.standard_estimate(box["spec"], product_name=req.product_name, months=req.months, imps={"low": 10, "mid": 20, "high": 30},
                                         est_uniques=10, notes=[DUMMY_ANSWER_NOTE], block={"asked": req.args}, resolution=req.resolution)

    values = dict(
        key=key, label="Dummy (live)", short_label="Dummy", description="Says back what it was asked.", kind="api", default_products=(),
        owns_geography=True, request_field=key, secrets=("DUMMY_ACCESS_TOKEN",), estimate=estimate,
        status=lambda: {"configured": bool(os.environ.get("DUMMY_ACCESS_TOKEN")), "missing": [] if os.environ.get("DUMMY_ACCESS_TOKEN") else ["DUMMY_ACCESS_TOKEN"],
                        "account": None, "setup_hint": "Add DUMMY_ACCESS_TOKEN."},
        diagnose="tests.method_fakes:echo_checks",
    )
    values.update(fields)
    spec = MethodSpec(**values)
    box["spec"] = spec
    return spec


def make_dummy_fake(key: str = "dummy", *, answer=None, hostile_text: str = HOSTILE, sample_body: dict | None = None) -> MethodFake:
    def install(monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("DUMMY_ACCESS_TOKEN", "dummy-test-token-0123456789")
        monkeypatch.setattr(fm, "load_overrides", lambda: {DUMMY_PRODUCT: {"product_name": DUMMY_PRODUCT, "enabled": True, "method": key, "platform": None}})
        fm.invalidate()
        return None

    return MethodFake(sample_body=sample_body or {"product_name": DUMMY_PRODUCT, "months": 2}, install=install, hostile_text=hostile_text,
                      answer=answer or (lambda hostile=False: method_fakes._answer_of(key, hostile)))


@contextlib.contextmanager
def dummy_method(key: str = "dummy", *, fake: bool | MethodFake = True, **fields):
    """Registers a throw-away method (and, unless `fake` is False, a fake for it) and takes both out again, whatever the body does."""
    spec = make_dummy_spec(key, **fields)
    methods.register(spec)
    fm.invalidate()
    if fake is not False:
        FAKES[key] = fake if isinstance(fake, MethodFake) else make_dummy_fake(key)
    try:
        yield spec
    finally:
        methods.unregister(key)
        FAKES.pop(key, None)
        fm.invalidate()


@pytest.fixture(autouse=True)
def _nothing_leaks_out_of_a_test():
    """The registry and the fakes are process-global: a test of this file (a twin above all) that leaves a registration or a fake behind would change what
    every later test in the run sees. Checked after EVERY test, so the leak is named at the test that made it."""
    before = (list(methods.keys()), sorted(FAKES), dict(methods.default_products()), methods.method_keys())
    yield
    fm.invalidate()
    after = (list(methods.keys()), sorted(FAKES), dict(methods.default_products()), methods.method_keys())
    assert after == before, f"this test changed the process-global registry or the fakes: before {before}, after {after}"


# ================================================================================================================================ C0 import order

def check_import_order(module: str, *, expected_keys: list[str], extra_path: str | None = None) -> list[str]:
    """`module` is the FIRST app import of a fresh interpreter: it imports cleanly, and then the forecast map and the registry still answer (the same
    methods, the same default products). The cycle that makes this fail: constanza_forecast_map -> a method module -> constanza_audience -> constanza_engine
    -> constanza_forecast_map."""
    code = textwrap.dedent(f"""
        import sys
        if {extra_path!r}: sys.path.insert(0, {extra_path!r})
        import importlib
        importlib.import_module({module!r})
        from app.services import constanza_forecast_map as fm, constanza_methods as m
        assert tuple(fm.METHODS) == m.method_keys(), ('METHODS', fm.METHODS, m.method_keys())
        assert dict(fm.DEFAULT_API_PRODUCTS) == m.default_products(), 'DEFAULT_API_PRODUCTS'
        assert m.keys() == {expected_keys!r}, ('registered methods', m.keys(), 'expected', {expected_keys!r})
        for name, key in m.default_products().items():
            assert fm.default_config(name).method == key, (name, key)
        print('imported')
    """)
    env = {**os.environ, "DATABASE_URL": os.environ.get("DATABASE_URL") or "postgresql://fake:fake@localhost/fake"}
    proc = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=240)
    if proc.returncode == 0 and proc.stdout.strip().endswith("imported"):
        return []
    tail = (proc.stderr or proc.stdout).strip().splitlines()[-4:]
    return [f"{module} cannot be the first app import of a fresh interpreter, or the registry does not answer afterwards: {' | '.join(tail)}. A method module must "
            "never import constanza_forecast_map or constanza_engine at the top of constanza_methods.py, and nothing may call constanza_methods.ensure_loaded() "
            "while a module is being imported (docs/constanza_methods.md, \"Import rules\")."]


C0_MODULES = ["app.services.constanza_forecast_map", "app.services.constanza_methods", *methods.MODULES, "app.services.constanza_engine", "app.main"]


@pytest.mark.parametrize("module", C0_MODULES)
def test_c0_each_module_is_a_clean_first_import_and_the_registry_still_answers(module):
    assert_ok("C0", module, check_import_order(module, expected_keys=list(KEYS)))


def test_twin_c0_a_module_that_cannot_be_imported_first_is_named(tmp_path):
    (tmp_path / "broken_first_import.py").write_text("from app.services import constanza_forecast_map\nraise ImportError(\"cannot import name 'x' from partially initialized module (circular import)\")\n")
    problems = check_import_order("broken_first_import", expected_keys=list(KEYS), extra_path=str(tmp_path))
    assert len(problems) == 1 and "broken_first_import cannot be the first app import" in problems[0] and "circular import" in problems[0]
    assert "ensure_loaded()" in problems[0] and "docs/constanza_methods.md" in problems[0]


def test_twin_c0_a_registry_that_does_not_answer_with_the_methods_is_named():
    problems = check_import_order("app.services.constanza_methods", expected_keys=[*KEYS, "ghost"])
    assert len(problems) == 1 and "registered methods" in problems[0] and "ghost" in problems[0]


# ================================================================================================================================ C1 registration

def check_registration(key: str) -> list[str]:
    """The registration is complete and the status answers the standard shape. (`register()` refuses most of this at registration; the checks that need the
    method to be CALLED - its status, its diagnose function, its default products - are only made here.)"""
    spec = methods.get(key)
    if spec is None:
        return [f'"{key}" is not registered: add constanza_methods.register(MethodSpec(key="{key}", ...)) at the bottom of its module and list the module in '
                "constanza_methods.MODULES (docs/constanza_methods.md steps 1 and 2)"]
    problems: list[str] = []
    mod = _short(spec)
    if not methods.KEY_RE.fullmatch(spec.key) or spec.key in methods.BUILTIN_METHODS:
        problems.append(f"{mod}: the key {spec.key!r} must be a lower-case word of 2-32 letters, digits or underscores and not platform / generic")
    if spec.request_field != spec.key:
        problems.append(f"{mod}: request_field is {spec.request_field!r} but must equal the key {spec.key!r}: the estimate request carries its block under the key")
    for name in ("label", "short_label", "description"):
        if not (isinstance(getattr(spec, name), str) and getattr(spec, name).strip()):
            problems.append(f"{mod}: MethodSpec.{name} is empty: Admin shows it, so say something in plain words")
    if spec.kind not in methods.KINDS:
        problems.append(f"{mod}: kind must be one of {', '.join(methods.KINDS)}")
    if spec.kind == "api" and not spec.secrets:
        problems.append(f"{mod}: an API method names the environment variables it needs in MethodSpec.secrets (names only, never values)")
    for secret in spec.secrets:
        if not (isinstance(secret, str) and ENV_NAME.match(secret)):
            problems.append(f"{mod}: secret {secret!r} is not an environment variable NAME (upper-case letters, digits and underscores): list names, never values")
    if spec.kind == "api":
        module_name, _, function_name = (spec.diagnose or "").partition(":")
        try:
            resolved = getattr(importlib.import_module(module_name), function_name)
            if not callable(resolved):
                raise TypeError("not callable")
        except Exception as exc:                                              # noqa: BLE001 - whatever stops it resolving is the answer
            problems.append(f'{mod}: diagnose "{spec.diagnose}" does not resolve to a function ({type(exc).__name__}: {exc}): Admin\'s "Test the connection" calls it; '
                            "it returns a list of constanza_methods.Check")
    if not (callable(spec.estimate) and callable(spec.status)):
        problems.append(f"{mod}: estimate and status must be callable")
    # the status, with nothing set up: names what is missing, secret's NAME first, and says it is not configured
    with pytest.MonkeyPatch.context() as mp:
        for secret in spec.secrets:
            mp.delenv(secret, raising=False)
        try:
            bare = spec.status()
            methods.validate_status(spec, bare)
            if bare["configured"] is not False:
                problems.append(f"{mod}: status() says configured: true with {', '.join(spec.secrets)} unset: configured must follow the secrets")
            first_words = {entry.split()[0] for entry in bare["missing"]}
            for secret in spec.secrets:
                if secret not in first_words:
                    problems.append(f"{mod}: status().missing does not name {secret} when it is unset: each entry starts with the secret's NAME, then (optionally) what to do")
        except ValueError as exc:
            problems.append(f"{mod}: status() is not the standard status ({exc}) - {{configured: bool, missing: [str], account: str | None, setup_hint: str}}")
        except Exception as exc:                                              # noqa: BLE001
            problems.append(f"{mod}: status() raised {type(exc).__name__}: {exc}: it must answer without a network call and without any secret set")
    # the derived lists follow the registry
    if tuple(fm.METHODS) != methods.method_keys() or key not in fm.METHODS:
        problems.append(f"the forecast map's METHODS {tuple(fm.METHODS)} is not the registry's {methods.method_keys()}: it must be derived (constanza_forecast_map.__getattr__)")
    if dict(fm.DEFAULT_API_PRODUCTS) != methods.default_products():
        problems.append("the forecast map's DEFAULT_API_PRODUCTS is not derived from the registry's default products")
    names = {p.name for p in catalog.effective_catalog()}
    for product in spec.default_products:
        if product not in names:
            problems.append(f"{mod}: default product {product!r} is not a catalog product name (a typo?): the default method of a product is looked up by its catalog name")
        elif fm.default_config(product).method != key:
            problems.append(f"{mod}: {product!r} is listed as a default product but the forecast map's default config for it is method {fm.default_config(product).method!r}")
    if spec.status_url != f"/api/constanza/{key}/status" and not spec.status_route:
        problems.append(f"{mod}: status route {spec.status_url!r} must be /api/constanza/{key}/status")
    return problems


@pytest.mark.parametrize("key", KEYS)
def test_c1_the_registration_is_complete_and_the_status_is_the_standard_one(key):
    assert_ok("C1", key, check_registration(key))


def test_twin_c1_a_registration_with_nothing_behind_it_is_named():
    with dummy_method(diagnose="tests.method_fakes:no_such_function", secrets=("dummy_token_in_lower_case",),
                      status=lambda: {"configured": True, "missing": ["SOME_OTHER_NAME"], "setup_hint": "x"}) as spec:
        problems = check_registration(spec.key)
    text = "\n".join(problems)
    assert 'diagnose "tests.method_fakes:no_such_function" does not resolve' in text and "returns a list of constanza_methods.Check" in text
    assert "is not an environment variable NAME" in text
    assert "status() is not the standard status" in text and "account" in text
    assert check_registration("never_registered") == [
        '"never_registered" is not registered: add constanza_methods.register(MethodSpec(key="never_registered", ...)) at the bottom of its module and list the '
        "module in constanza_methods.MODULES (docs/constanza_methods.md steps 1 and 2)"]


def test_twin_c1_a_status_that_ignores_the_secrets_and_a_default_product_that_is_not_a_product_are_named():
    with dummy_method(status=lambda: {"configured": True, "missing": [], "account": None, "setup_hint": "ok"}, default_products=("No Such Product Anywhere",)) as spec:
        problems = check_registration(spec.key)
    text = "\n".join(problems)
    assert "says configured: true with DUMMY_ACCESS_TOKEN unset" in text
    assert "default product 'No Such Product Anywhere' is not a catalog product name" in text


# ================================================================================================================================ C2 no secret leaks

class _Refusal:
    """What `requests` answers when the upstream refuses: HTTP 400 with every secret in its message, in Meta's and TikTok's error shapes."""

    def __init__(self, secrets: list[str]):
        text = "bad credentials " + " ".join(secrets)
        self.status_code, self.ok, self.text, self.headers, self.content = 400, False, text, {}, text.encode()
        self._body = {"error": {"message": text, "code": 100}, "code": 40105, "message": text, "request_id": "REQ-REFUSED"}

    def json(self):
        return self._body


def _blobs_of(key: str, spec: MethodSpec, *, with_estimate: bool) -> list[tuple[str, str]]:
    blobs = [("status()", json.dumps(spec.status(), default=str)),
             ("GET /api/admin/constanza/methods", json.dumps(asyncio.run(main.admin_constanza_methods()), default=str)),
             ("GET /api/admin/forecast-map", json.dumps(asyncio.run(main.admin_get_forecast_map()), default=str))]
    try:
        blobs.append(("POST /api/admin/constanza/%s/test" % key, json.dumps(main.admin_constanza_test(key), default=str)))
    except HTTPException as exc:
        blobs.append(("the connection test's refusal", json.dumps(exc.detail, default=str)))
    if with_estimate:
        try:
            blobs.append(("the estimate", json.dumps(main.constanza_estimate(main.ConstanzaEstimateRequest(**copy.deepcopy(FAKES[key].sample_body))), default=str)))
        except HTTPException as exc:
            blobs.append((f"the refused estimate (HTTP {exc.status_code})", json.dumps(exc.detail, default=str)))
    return blobs


def check_no_secret_leak(key: str) -> list[str]:
    """No secret VALUE appears in anything the method's answers carry. Three passes: (1) with the values the method's own fake sets (the formats its validation
    needs), over the status, both Admin payloads, the connection test and the answered estimate; (2) with odd values planted (so the 'not set up' messages
    are exercised); (3) with the upstream refusing with every secret in its text (only for a method that goes through `requests`)."""
    spec, fake = methods.get(key), FAKES.get(key)
    if spec is None or fake is None:
        return [f'cannot look for secrets: "{key}" has no registration or no fake (C1 / C6 say what to add)']
    problems: list[str] = []

    def scan(where: str, secrets: dict[str, str], blobs: list[tuple[str, str]]) -> None:
        for name, value in secrets.items():
            if len(value) <= 4:
                problems.append(f"the fake sets {name} to a value of {len(value)} characters: use a longer one so a leak can be seen (the last four characters of an account are shown by design)")
                continue
            for label, blob in blobs:
                if value in blob:
                    problems.append(f"{where}: the value of {name} appears in {label}: never put a secret in an answer, a log or an error - scrub it, and show an account as its last four digits")

    with pytest.MonkeyPatch.context() as mp:                                  # pass 1: the fake's own values
        fake.install(mp)
        values = {name: os.environ.get(name, "") for name in spec.secrets}
        missing = [name for name, value in values.items() if not value]
        if missing:
            problems.append(f"the fake's install() does not set {', '.join(missing)}: it must set every secret the method names, to a fake value")
        scan("with the fake's values", {n: v for n, v in values.items() if v}, _blobs_of(key, spec, with_estimate=True))
    with pytest.MonkeyPatch.context() as mp:                                  # pass 2: odd, malformed values
        fake.install(mp)
        planted = {name: f"PLANTED-{name}-9f3a7c1e5d" for name in spec.secrets}
        for name, value in planted.items():
            mp.setenv(name, value)
        scan("with odd values planted", planted, _blobs_of(key, spec, with_estimate=False))
    with pytest.MonkeyPatch.context() as mp:                                  # pass 3: the upstream refuses and repeats the secrets
        import requests
        fake.install(mp)
        values = {name: os.environ.get(name, "") for name in spec.secrets}
        echoed = [v for v in values.values() if v]
        refuse = lambda *a, **k: _Refusal(echoed)                             # noqa: E731
        for attr in ("get", "post", "request"):
            mp.setattr(requests, attr, refuse)
        mp.setattr(requests.Session, "request", lambda self, *a, **k: _Refusal(echoed))
        scan("when the upstream refuses", {n: v for n, v in values.items() if v}, _blobs_of(key, spec, with_estimate=True))
    return problems


@pytest.mark.parametrize("key", KEYS)
def test_c2_no_answer_the_method_gives_carries_a_secret(key):
    assert_ok("C2", key, check_no_secret_leak(key))


def test_twin_c2_a_status_that_shows_the_whole_account_is_named():
    leak = lambda: {"configured": True, "missing": [], "account": os.environ.get("DUMMY_ACCESS_TOKEN"), "setup_hint": "ok"}      # noqa: E731
    with dummy_method(status=leak) as spec:
        problems = check_no_secret_leak(spec.key)
    assert any("the value of DUMMY_ACCESS_TOKEN appears in status()" in p and "scrub it" in p for p in problems), problems
    assert any("appears in POST /api/admin/constanza/dummy/test" in p for p in problems)          # the connection test echoes the status


def test_twin_c2_an_estimate_that_repeats_the_token_and_a_fake_that_sets_nothing_are_named():
    spec_fields = {}
    with dummy_method(**spec_fields) as spec:
        def leaky(req):
            out = methods.standard_estimate(spec, product_name=req.product_name, months=req.months, imps={"low": 1, "mid": 2, "high": 3}, est_uniques=1,
                                            notes=["the token is " + os.environ.get("DUMMY_ACCESS_TOKEN", "")], block={}, resolution=req.resolution)
            return out
        object.__setattr__(spec, "estimate", leaky)                           # (a frozen spec: replace its estimate for this one test)
        problems = check_no_secret_leak(spec.key)
    assert any("appears in the estimate" in p for p in problems), problems
    silent = MethodFake(sample_body={"product_name": DUMMY_PRODUCT, "months": 1}, install=lambda mp: None, hostile_text=HOSTILE, answer=lambda hostile=False: {})
    with dummy_method(fake=silent) as spec:
        problems = check_no_secret_leak(spec.key)
    assert any("the fake's install() does not set DUMMY_ACCESS_TOKEN" in p for p in problems), problems


# ================================================================================================================================ C3 every registering module is listed

def registers_a_method(source: str) -> bool:
    """Does this module call constanza_methods.register(...)? Read from the syntax tree, so a docstring or a comment that mentions it does not count."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return "constanza_methods.register(" in source
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "register" and "method" in getattr(func.value, "id", ""):
            return True
        if isinstance(func, ast.Name) and func.id == "register":
            return True
    return False


def check_modules_listed(services_dir: Path, modules: tuple, keys: list[str]) -> list[str]:
    problems: list[str] = []
    found = sorted(p for p in services_dir.glob("*.py") if p.name != "constanza_methods.py" and registers_a_method(p.read_text(encoding="utf-8")))
    listed = {m.rsplit(".", 1)[-1] for m in modules}
    for path in found:
        if path.stem not in listed:
            problems.append(f'app/services/{path.name} registers a method but "app.services.{path.stem}" is not in constanza_methods.MODULES: add it '
                            "(docs/constanza_methods.md step 2) - without it the method exists only when something happens to import the module")
    for module in modules:
        if not (services_dir / (module.rsplit(".", 1)[-1] + ".py")).exists():
            problems.append(f'constanza_methods.MODULES lists "{module}" but there is no such file in app/services')
        elif module.rsplit(".", 1)[-1] not in {p.stem for p in found}:
            problems.append(f'constanza_methods.MODULES lists "{module}" but that module does not call constanza_methods.register(')
    if not keys:
        problems.append("no method is registered at all")
    return problems


def test_c3_every_module_that_registers_a_method_is_listed_and_every_listed_module_registers():
    assert_ok("C3", "*", check_modules_listed(SERVICES, methods.MODULES, KEYS))


def test_twin_c3_a_module_that_registers_but_is_not_listed_is_named(tmp_path):
    for name, text in (("constanza_listed.py", "constanza_methods.register(MethodSpec(key='listed'))\n"), ("constanza_foo.py", "from x import y\nconstanza_methods.register(MethodSpec(key='foo'))\n"),
                       ("constanza_quiet.py", '"""Mentions constanza_methods.register( in a docstring only."""\nX = 1\n')):
        (tmp_path / name).write_text(text)
    problems = check_modules_listed(tmp_path, ("app.services.constanza_listed", "app.services.constanza_ghost", "app.services.constanza_quiet"), ["listed"])
    text = "\n".join(problems)
    assert 'app/services/constanza_foo.py registers a method but "app.services.constanza_foo" is not in constanza_methods.MODULES: add it' in text
    assert 'lists "app.services.constanza_ghost" but there is no such file' in text
    assert 'lists "app.services.constanza_quiet" but that module does not call constanza_methods.register(' in text


# ================================================================================================================================ C4 the request model

def _unwrap(annotation):
    """The pydantic model (or None) behind Optional[Model] / list[Model] / Model."""
    args = typing.get_args(annotation)
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    for arg in args:
        found = _unwrap(arg)
        if found is not None:
            return found
    return None


def _needs_cap(annotation) -> bool:
    origin = typing.get_origin(annotation)
    if annotation in (str, dict, list, tuple, set):
        return True
    if origin in (list, tuple, set, dict):
        return True
    return any(_needs_cap(arg) for arg in typing.get_args(annotation) if arg is not type(None)) if origin is typing.Union or str(origin) == "types.UnionType" else False


def _walk_model(model: type[BaseModel], path: str, seen: set, problems: list[str]) -> None:
    if model in seen:
        return
    seen.add(model)
    if model.model_config.get("extra") != "forbid":
        problems.append(f'{model.__name__} ({path}) must set model_config = ConfigDict(extra="forbid"): a field it does not know is refused, because a typo '
                        "must not quietly become a broader audience")
    for name, info in model.model_fields.items():
        annotation = info.annotation
        capped = any(isinstance(m, annotated_types.MaxLen) for m in info.metadata) or any(getattr(m, "max_length", None) for m in info.metadata)
        if _needs_cap(annotation) and not capped and not typing.get_origin(annotation) is typing.Literal:
            kind = "list" if "list" in str(annotation) else ("dict" if "dict" in str(annotation) else "text")
            problems.append(f"{model.__name__}.{name} is a {kind} with no max_length: cap it, e.g. Field(default_factory=list, max_length=50) "
                            "(docs/constanza_methods.md step 3) - a request must be bounded before anything looks inside it")
        inner = _unwrap(annotation)
        if inner is not None:
            _walk_model(inner, f"{path}.{name}", seen, problems)


def check_request_model(key: str, model: type[BaseModel] | None = None) -> list[str]:
    model = model or main.ConstanzaEstimateRequest
    problems: list[str] = []
    if key not in model.model_fields:
        return [f'{model.__name__} has no field "{key}": add `{key}: Optional[{key.capitalize()}EstimateModel] = None` next to the other methods in app/main.py '
                "(docs/constanza_methods.md step 3); without it the dispatch hands the method an empty block and every planner choice is silently dropped"]
    inner = _unwrap(model.model_fields[key].annotation)
    if inner is None:
        problems.append(f'{model.__name__}.{key} must be typed Optional[<a pydantic model>] so its shape is validated and shows in the OpenAPI schema')
    else:
        _walk_model(inner, f"{model.__name__}.{key}", set(), problems)
    if model.model_config.get("extra") == "forbid":
        problems.append(f"{model.__name__} must keep IGNORING unknown top-level fields: a tab opened before a deploy sends the older request")
    return problems


@pytest.mark.parametrize("key", KEYS)
def test_c4_the_request_carries_the_method_in_a_typed_strict_capped_field(key):
    assert_ok("C4", key, check_request_model(key))


def test_c4_the_request_still_ignores_a_field_it_does_not_know_at_the_top():
    assert main.ConstanzaEstimateRequest(product_name="x", a_field_a_later_release_adds=1).product_name == "x"


def test_twin_c4_a_method_with_no_field_or_a_loose_field_is_named():
    class LooseItem(BaseModel):
        name: str                                           # no cap, and the model accepts unknown fields

    class Loose(BaseModel):
        items: list[LooseItem] = []
        label: str = "x"

    class Request(BaseModel):
        product_name: str
        loose: typing.Optional[Loose] = None
        untyped: typing.Optional[dict] = None

    assert "has no field \"absent\"" in check_request_model("absent", Request)[0]
    text = "\n".join(check_request_model("loose", Request))
    assert "Loose (Request.loose) must set model_config = ConfigDict(extra=\"forbid\")" in text
    assert "LooseItem (Request.loose.items) must set model_config" in text
    assert "Loose.items is a list with no max_length" in text and "Loose.label is a text with no max_length" in text and "LooseItem.name is a text with no max_length" in text
    assert "must be typed Optional[<a pydantic model>]" in "\n".join(check_request_model("untyped", Request))

    class Strict(BaseModel):
        model_config = {"extra": "forbid"}
        items: list[str] = Field(default_factory=list, max_length=5)
        note: typing.Optional[str] = Field(None, max_length=20)
        count: int = 0

    class Good(BaseModel):
        product_name: str
        good: typing.Optional[Strict] = None

    assert check_request_model("good", Good) == []


# ================================================================================================================================ C5 routes

def _route_table():
    return [(getattr(r, "path", ""), set(getattr(r, "methods", None) or ())) for r in main.app.routes]


def check_routes(key: str, *, routes: list | None = None, plugin_js: str | None = None) -> list[str]:
    routes = _route_table() if routes is None else routes
    problems: list[str] = []
    status_path = f"/api/constanza/{key}/status"
    if not any(path == status_path and "GET" in verbs for path, verbs in routes):
        problems.append(f"there is no GET {status_path}: add it in app/main.py next to the other helper routes (docs/constanza_methods.md step 4). It answers "
                        "the method's status() - the dock's banner reads it - and never carries a secret")
    if plugin_js is None:
        path = STATIC / f"constanza-{key}.js"
        plugin_js = path.read_text(encoding="utf-8") if path.exists() else ""
    code = strip_js_comments(plugin_js)
    known = {path for path, _ in routes}
    for found in sorted(set(re.findall(r"/api/constanza/[A-Za-z0-9_/-]*", code))):
        name = found[len("/api/constanza/"):]
        if name in SHARED_ROUTES:
            continue
        if not re.match(rf"{re.escape(key)}[/-]", name):                           # exactly "<key>/..." or "<key>-...": a route that merely starts with the letters does not count
            problems.append(f"constanza-{key}.js calls {found}: a plugin's own routes start with /api/constanza/{key}/ or /api/constanza/{key}- "
                            f"(or are one of the dock's shared routes: {', '.join(SHARED_ROUTES)}), so a route can always be traced to its method")
        elif found.endswith(("/", "-")):                                          # a prefix the script finishes at run time ("/api/constanza/tiktok/" + path)
            if not any(path.startswith(found) for path in known):
                problems.append(f"constanza-{key}.js builds a route from {found!r} but the server has no route that starts with it")
        elif found not in known:
            problems.append(f"constanza-{key}.js calls {found} but the server has no such route: add it in app/main.py (or fix the path)")
    if re.search(r"\bfetch\s*\(|XMLHttpRequest|sendBeacon", code):
        problems.append(f"constanza-{key}.js reaches the network itself: a plugin talks to the server only through ctx.api (it adds the JSON headers, the error handling and the "
                        "dock's own guards), never fetch / XMLHttpRequest / sendBeacon")
    if re.search(r"https?://", code):
        problems.append(f"constanza-{key}.js contains a URL: a plugin calls its own /api/constanza/{key}/... routes only, so no script or request ever goes elsewhere")
    if re.search(r"\beval\s*\(|new\s+Function\b|document\.write\b", code):
        problems.append(f"constanza-{key}.js uses eval / new Function / document.write: never run text as code")
    return problems


@pytest.mark.parametrize("key", KEYS)
def test_c5_the_status_route_exists_and_the_plugin_only_calls_its_own_routes(key):
    assert_ok("C5", key, check_routes(key))


def test_twin_c5_a_missing_status_route_and_a_stray_route_are_named():
    plugin = ('const a = ctx.api("/api/constanza/estimate"); const b = ctx.api("/api/constanza/other/status"); const c = ctx.api("/api/constanza/dummy/geos");\n'
              'const m = ctx.api("/api/constanza/dummyish/x"); const p = ctx.api("/api/constanza/dummy/" + path);\n'
              '// ctx.api("/api/constanza/zzz/ignored in a comment")\n const d = fetch("/api/constanza/dummy/status"); const e = "https://example.com/x.js";')
    problems = check_routes("dummy", routes=[("/api/constanza/estimate", {"POST"})], plugin_js=plugin)
    text = "\n".join(problems)
    assert "there is no GET /api/constanza/dummy/status: add it in app/main.py" in text
    assert "calls /api/constanza/other/status: a plugin's own routes start with /api/constanza/dummy/" in text
    assert "calls /api/constanza/dummy/geos but the server has no such route" in text
    assert "zzz" not in text                                                                 # a comment is not a call
    assert "calls /api/constanza/dummyish/x: a plugin's own routes start with" in text       # a route that merely starts with the key's letters is not the key's
    assert "builds a route from '/api/constanza/dummy/' but the server has no route that starts with it" in text
    assert "reaches the network itself" in text and "contains a URL" in text


# ================================================================================================================================ C6 the fake

def check_fake(key: str) -> list[str]:
    spec = methods.get(key)
    if key not in FAKES:
        name = _short(spec) if spec else f"constanza_{key}"
        return [f'{name} is registered but tests/method_fakes.py has no entry for "{key}": add one (see docs/constanza_methods.md step 5): '
                f'FAKES["{key}"] = MethodFake(sample_body=..., install=..., hostile_text=HOSTILE, answer=...) over a strict fake of the method\'s API']
    fake = FAKES[key]
    problems: list[str] = []
    if not isinstance(fake, MethodFake):
        return [f'tests/method_fakes.py FAKES["{key}"] must be a MethodFake(sample_body, install, hostile_text, answer)']
    if not (isinstance(fake.sample_body, dict) and isinstance(fake.sample_body.get("product_name"), str) and fake.sample_body["product_name"]):
        problems.append(f'FAKES["{key}"].sample_body must be the body of POST /api/constanza/estimate, with a product_name')
    if not callable(fake.install) or not callable(fake.answer):
        problems.append(f'FAKES["{key}"].install and .answer must be callable (install(monkeypatch) patches the network and the secrets; answer(hostile=False) returns the estimate)')
    if not (isinstance(fake.hostile_text, str) and "<" in fake.hostile_text and "onerror" in fake.hostile_text):
        problems.append(f'FAKES["{key}"].hostile_text must be markup that would run if it were drawn unescaped (use method_fakes.HOSTILE)')
    if problems:
        return problems
    with pytest.MonkeyPatch.context() as mp:
        fake.install(mp)
        product = fake.sample_body["product_name"]
        if fm.resolve_forecast(product).method != key:
            problems.append(f'FAKES["{key}"]: the product {product!r} of its sample_body is forecast by method {fm.resolve_forecast(product).method!r} under the fake, '
                            f"not {key!r}: pick one of the method's default products, or have install() map it with an admin row (see _install_dummy in tests/test_constanza_method_contract.py)")
    return problems


@pytest.mark.parametrize("key", KEYS)
def test_c6_the_method_has_a_strict_fake_with_a_sample_the_method_answers(key):
    assert_ok("C6", key, check_fake(key))


def test_twin_c6_a_method_with_no_fake_or_a_careless_one_is_named():
    with dummy_method(fake=False) as spec:
        problems = check_fake(spec.key)
    assert len(problems) == 1
    assert problems[0].startswith("test_constanza_method_contract is registered but tests/method_fakes.py has no entry for \"dummy\": add one (see docs/constanza_methods.md step 5)")
    careless = MethodFake(sample_body={"product_name": "Display - Not A Dummy Product"}, install=lambda mp: None, hostile_text="plain text", answer=lambda hostile=False: {})
    with dummy_method(fake=careless) as spec:
        text = "\n".join(check_fake(spec.key))
    assert "hostile_text must be markup that would run" in text
    careless2 = MethodFake(sample_body={"product_name": "Search - SEM"}, install=lambda mp: None, hostile_text=HOSTILE, answer=lambda hostile=False: {})
    with dummy_method(fake=careless2) as spec:
        text = "\n".join(check_fake(spec.key))
    assert "is forecast by method" in text and "not 'dummy'" in text and "pick one of the method's default products" in text


# ================================================================================================================================ C7 the standard estimate

class _LogTap(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.ERROR)
        self.lines: list[str] = []

    def emit(self, record):
        self.lines.append(record.getMessage())


def check_standard_estimate(key: str) -> list[str]:
    spec, fake = methods.get(key), FAKES.get(key)
    if spec is None or fake is None:
        return [f'cannot answer the sample request: "{key}" has no registration or no fake (C1 / C6 say what to add)']
    problems: list[str] = []
    tap = _LogTap()
    logger = logging.getLogger("app.main")
    logger.addHandler(tap)
    try:
        for hostile in (False, True):
            label = "the hostile answer" if hostile else "the sample answer"
            try:
                out = fake.answer(hostile=hostile)
            except HTTPException as exc:
                why = f" (server log: {' | '.join(tap.lines)})" if tap.lines else ""
                problems.append(f"{label}: main.constanza_estimate answered HTTP {exc.status_code} for the fake's sample_body: {exc.detail}{why}")
                continue
            except Exception as exc:                                          # noqa: BLE001
                problems.append(f"{label}: the fake could not answer its own sample_body: {type(exc).__name__}: {exc}")
                continue
            try:
                methods.validate_standard(spec, out)
            except methods.StandardEstimateError as exc:
                problems.append(f"{label} is not the standard estimate: {exc} Build it with constanza_methods.standard_estimate(...) (docs/constanza_methods.md, \"The standard estimate\")")
                continue
            if out.get("method") != key or not isinstance(out.get(key), dict):
                problems.append(f"{label}: `method` must be {key!r} and the block must sit under the key {key!r}")
            for k in methods.STANDARD_KEYS:
                if k not in out:
                    problems.append(f"{label}: the key {k!r} is missing")
            months = fake.sample_body.get("months", 1)
            if out.get("months") != months:
                problems.append(f"{label}: months is {out.get('months')!r} but the sample asked for {months}: the flight totals are monthly x months")
            uniques, mid = out.get("est_uniques"), out.get("imps_mid")
            if uniques is not None and isinstance(mid, (int, float)) and uniques > mid:
                problems.append(f"{label}: est_uniques ({uniques}) is more than imps_mid ({mid}): an audience cannot be larger than its impressions")
            try:
                json.dumps(out, allow_nan=False)
            except (TypeError, ValueError) as exc:
                problems.append(f"{label} cannot be written as JSON ({exc}): no NaN, no objects, only numbers, strings, lists and dicts")
            if hostile and json.dumps(fake.hostile_text)[1:-1] not in json.dumps(out):                  # (as the text reads inside a JSON string)
                problems.append(f"{label} does not carry the fake's hostile_text anywhere: answer(hostile=True) must plant it in the server text (a note, a place name) so "
                                "the browser checks can prove it is escaped")
    finally:
        logger.removeHandler(tap)
    return problems


@pytest.mark.parametrize("key", KEYS)
def test_c7_the_sample_request_is_answered_with_the_standard_estimate(key):
    assert_ok("C7", key, check_standard_estimate(key))


def test_twin_c7_an_answer_that_is_not_the_standard_estimate_is_named():
    def broken(req):
        out = methods.standard_estimate(methods.get("dummy"), product_name=req.product_name, months=req.months, imps={"low": 10, "mid": 20, "high": 30},
                                        est_uniques=10, notes=[], block={}, resolution=req.resolution)
        del out["notes"]
        return out
    with dummy_method(estimate=broken) as spec:
        problems = check_standard_estimate(spec.key)
    text = "\n".join(problems)
    assert "answered HTTP 500" in text and "answered without: notes" in text            # the server log carries the reason
    unordered = lambda req: methods.standard_estimate(methods.get("dummy"), product_name=req.product_name, months=req.months,           # noqa: E731
                                                       imps={"low": 30, "mid": 20, "high": 10}, est_uniques=10, notes=[], block={}, resolution=req.resolution)
    with dummy_method(estimate=unordered) as spec:
        text = "\n".join(check_standard_estimate(spec.key))
    assert "answered HTTP 500" in text and "low <= mid <= high" in text
    no_plant = make_dummy_fake("dummy", answer=lambda hostile=False: methods.standard_estimate(
        methods.get("dummy"), product_name="p", months=2, imps={"low": 1, "mid": 2, "high": 3}, est_uniques=1, notes=["fine"], block={}))
    with dummy_method(fake=no_plant) as spec:
        text = "\n".join(check_standard_estimate(spec.key))
    assert "does not carry the fake's hostile_text anywhere" in text


# ================================================================================================================================ C8 the plugin's files

def _tags(index_html: str):
    scripts = [(m.start(), m.group(1)) for m in re.finditer(r"<script\b[^>]*\bsrc=\"/static/([^\"?]+)(?:\?[^\"]*)?\"", index_html)]
    links = [(m.start(), m.group(1)) for m in re.finditer(r"<link\b[^>]*\bhref=\"/static/([^\"?]+)(?:\?[^\"]*)?\"", index_html)]
    return scripts, links


def check_plugin_files(key: str, *, static_dir: Path = STATIC, index_html: str | None = None) -> list[str]:
    index_html = INDEX_HTML.read_text(encoding="utf-8") if index_html is None else index_html
    scripts, links = _tags(index_html)
    problems: list[str] = []
    js_name, css_name = f"constanza-{key}.js", f"constanza-{key}.css"
    js = static_dir / js_name
    if not js.exists():
        problems.append(f"app/static/{js_name} does not exist: the method's panel is a script that registers with the dock (docs/constanza_methods.md step 6). Without it the "
                        "product is never offered in the dock and its Step 05 line says the estimate source didn't load")
    else:
        if not re.search(rf"registerMethod\(\s*[\"']{re.escape(key)}[\"']", strip_js_comments(js.read_text(encoding='utf-8'))):
            problems.append(f'{js_name} never calls registerMethod("{key}", ...): the first argument must be the method\'s key')
        at = [pos for pos, name in scripts if name == js_name]
        if len(at) != 1:
            problems.append(f'index.html must load {js_name} exactly once (<script src="/static/{js_name}"></script>); it has {len(at)}')
        else:
            for before in ("constanza.js", "segment-browser.js"):
                where = [pos for pos, name in scripts if name == before]
                if not where or where[0] > at[0]:
                    problems.append(f"index.html must load {before} BEFORE {js_name}: the plugin registers with the dock and opens the shared pop-up as soon as it loads")
    if (static_dir / css_name).exists():
        at = [pos for pos, name in links if name == css_name]
        if len(at) != 1:
            problems.append(f'index.html must link {css_name} exactly once (<link rel="stylesheet" href="/static/{css_name}" />); it has {len(at)}')
        else:
            for before in ("constanza.css", "audience-kit.css", "segment-browser.css"):
                where = [pos for pos, name in links if name == before]
                if not where or where[0] > at[0]:
                    problems.append(f"index.html must link {before} BEFORE {css_name}: the plugin's rules refine the dock's, the kit's and the pop-up's")
    return problems


def check_no_orphan_files(keys: list[str], *, static_dir: Path = STATIC, index_html: str | None = None) -> list[str]:
    index_html = INDEX_HTML.read_text(encoding="utf-8") if index_html is None else index_html
    scripts, links = _tags(index_html)
    problems: list[str] = []
    for path in sorted(static_dir.glob("constanza-*")):
        match = re.fullmatch(r"constanza-([a-z][a-z0-9_]*)\.(js|css)", path.name)
        if not match:
            problems.append(f"app/static/{path.name} is named like a method's file but is not constanza-<key>.js / .css")
        elif match.group(1) not in keys:
            problems.append(f"app/static/{path.name} belongs to no registered method ('{match.group(1)}' is not in the registry): register the method, or delete the orphan")
    for _, name in scripts + links:
        if re.fullmatch(r"constanza-[a-z0-9_]+\.(js|css)", name) and not (static_dir / name).exists():
            problems.append(f"index.html references /static/{name} but the file does not exist")
    return problems


@pytest.mark.parametrize("key", KEYS)
def test_c8_the_plugins_files_exist_and_are_linked_in_order(key):
    assert_ok("C8", key, check_plugin_files(key))


def test_c8_no_file_named_for_a_method_is_an_orphan_and_no_tag_points_at_nothing():
    assert_ok("C8", "*", check_no_orphan_files(list(KEYS)))


def test_twin_c8_a_missing_plugin_a_misplaced_tag_and_an_orphan_are_named(tmp_path):
    (tmp_path / "constanza-dummy.js").write_text('window.Constanza.registerMethod("not_dummy", function () {});')
    (tmp_path / "constanza-dummy.css").write_text(".x{}")
    (tmp_path / "constanza-orphan.js").write_text("")
    html = ('<link href="/static/constanza-dummy.css"><link href="/static/constanza.css"><link href="/static/audience-kit.css"><link href="/static/segment-browser.css">'
            '<script src="/static/constanza-dummy.js"></script><script src="/static/segment-browser.js"></script><script src="/static/constanza.js"></script>'
            '<script src="/static/constanza-ghost.js?v=1"></script>')
    text = "\n".join(check_plugin_files("dummy", static_dir=tmp_path, index_html=html))
    assert 'constanza-dummy.js never calls registerMethod("dummy", ...)' in text
    assert "must load constanza.js BEFORE constanza-dummy.js" in text and "must link constanza.css BEFORE constanza-dummy.css" in text
    assert "app/static/constanza-nothing.js does not exist" in "\n".join(check_plugin_files("nothing", static_dir=tmp_path, index_html=html))
    text = "\n".join(check_no_orphan_files(["dummy"], static_dir=tmp_path, index_html=html))
    assert "constanza-orphan.js belongs to no registered method ('orphan' is not in the registry)" in text
    assert "index.html references /static/constanza-ghost.js but the file does not exist" in text


# ================================================================================================================================ C9 plugin conformance

def check_plugin_conformance(key: str, *, plugin_source: str | None = None, answer: dict | None = None, hostile_answer: dict | None = None,
                             hostile_text: str = HOSTILE) -> list[str]:
    """The real constanza.js and the plugin in a JavaScript engine: the host accepts it, it has only members the contract knows and gives one request block under
    its own key, and what it draws from the fake's answers - the sample one and the one with hostile server text - is a string with no raw markup.

    One host rule is checked on top (docs/constanza_methods.md, "What the host does"): a RESTORED result is shown with no `context()` when the form was edited since the
    pull, so the host hands `bodyHtml` and `summaryLines` `asked = {geo: "", areas: [], method: null}`. Both are run that way on both answers: they must return what
    they always return (a string, a list of strings), must not throw, must not print "undefined", "NaN" or "null" into the text (sample answer), and must still escape the
    planted hostile text - a saved result is described by the answer's own echo, never by an `asked.method` that is not there."""
    source = plugin_source if plugin_source is not None else plugin_source_of(key)
    fake = FAKES.get(key)
    if answer is None:
        if fake is None:
            return [f'cannot run the plugin: "{key}" has no fake to answer with (C6 says what to add)']
        answer, hostile_answer = fake.answer(), fake.answer(hostile=True)
        hostile_text = fake.hostile_text
    hostile_answer = hostile_answer or answer
    body = (
        js_page(source, key)
        + f"var DATA = {json.dumps(answer)}, HOSTILE_DATA = {json.dumps(hostile_answer)};\n"
        "function safe(fn) { try { return fn(); } catch (e) { return { __threw: String((e && e.message) || e) }; } }\n"
        "var out = { registered: __res[KEY] === true, errors: __errors, present: !!P };\n"
        "if (P) {\n"
        "  var caps = Object.assign({}, Z.CONTRACT.caps, P.caps || {});\n"
        "  out.members = Object.keys(P); out.required = Z.CONTRACT.required; out.optional = Z.CONTRACT.optional; out.caps = caps;\n"
        "  out.request = safe(function () { return P.requestPart(); });\n"
        "  out.context = safe(function () { return P.context(); });\n"
        "  out.outputs = {}; out.restored = {};\n"
        "  [['sample', DATA], ['hostile', HOSTILE_DATA]].forEach(function (pair) {\n"
        "    var data = pair[1], m = Z._pure.summarize(data), asked = { geo: '', areas: [], method: P.context() };\n"
        "    var gone = { geo: '', areas: [], method: null };      // what constanza.js restoreLine() hands over when the form was edited since the pull\n"
        "    out.restored[pair[0] + ':bodyHtml'] = safe(function () { return P.bodyHtml(data, m, gone); });\n"
        "    out.restored[pair[0] + ':summaryLines'] = safe(function () { return P.summaryLines(data, gone); });\n"
        "    var calls = { formHtml: function () { return P.formHtml(); }, headlineHtml: function () { return P.headlineHtml(data, m); },\n"
        "      bodyHtml: function () { return P.bodyHtml(data, m, asked); }, aboutHtml: function () { return P.aboutHtml(data); },\n"
        "      cardHtml: function () { return P.cardHtml(data, m, { line: null, stale: [], fromSnapshot: false }); }, summaryLines: function () { return P.summaryLines(data, asked); } };\n"
        "    if (caps.ownsGeography) calls.whereHtml = function () { return P.whereHtml(); };\n"
        "    if (P.badgeHtml) calls.badgeHtml = function () { return P.badgeHtml(data); };\n"
        "    if (P.cardEmptyHtml) calls.cardEmptyHtml = function () { return P.cardEmptyHtml(null, null); };\n"
        "    if (P.geoInfo) calls.geoInfo = function () { return P.geoInfo(data); };\n"
        "    Object.keys(calls).forEach(function (name) { out.outputs[pair[0] + ':' + name] = safe(calls[name]); });\n"
        "    var el = fakeEl();\n"
        "    out.outputs[pair[0] + ':renderCard'] = safe(function () { Z.renderCard(el, data, { line: null, stale: [], fromSnapshot: false, onApply: function () {}, onRefine: function () {},\n"
        "      onRecalc: function () {}, getText: function () { return ''; }, onGeoText: function () {} }); return el.innerHTML; });\n"
        "  });\n"
        "}\n"
        "return JSON.stringify(out);"
    )
    out = run_js(body)
    problems: list[str] = []
    if not out["registered"]:
        why = [e for e in out["errors"] if f'plugin "{key}"' in e or key in e] or out["errors"] or ["it never called registerMethod"]
        return [f'constanza-{key}.js: the dock refused the plugin: {" | ".join(why)} - fix the members named (Constanza.CONTRACT in constanza.js lists them; '
                'docs/constanza_methods.md, "The plugin contract")']
    known = set(out["required"]) | set(out["optional"])
    for member in out["members"]:
        if member not in known:
            problems.append(f'constanza-{key}.js returns a member "{member}" that the contract does not know (a typo?): the contract lists {", ".join(sorted(known))}')
    for member in out["required"]:
        if member not in out["members"]:
            problems.append(f'constanza-{key}.js is missing the required member "{member}"')
    request = out["request"]
    if not (isinstance(request, dict) and list(request) == [key] and isinstance(request[key], dict)):
        problems.append(f"requestPart() must return exactly {{{key}: <object>}} - the method's own block and nothing else (got {json.dumps(request)[:120]})")
    context = out["context"]
    if not (isinstance(context, dict) and isinstance(context.get("summary"), list) and all(isinstance(s, str) for s in context["summary"])):
        problems.append("context() must return {summary: [strings], geo?: string}: what was asked, in words")
    elif out["caps"].get("ownsGeography") and not isinstance(context.get("geo"), str):
        problems.append("context().geo is required (a string) when caps.ownsGeography is true: the host shows it as the place of a result")

    def judge(name: str, value, restored: bool = False) -> None:
        pair, _, member = name.partition(":")
        on = f"the {pair} answer" + (' of a restored result (asked = {geo: "", areas: [], method: null})' if restored else "")
        if isinstance(value, dict) and "__threw" in value:
            hint = ("the host gives a restored result no asked.method when the form was edited since the pull (context() would describe the unpulled edit): guard the read "
                    "and describe the result from the answer's own echo, as Meta does" if restored
                    else "a plugin draws any saved answer without throwing (it may be a corrupt one); read fields defensively")
            problems.append(f"{member}() threw on {on}: {value['__threw']} - {hint}")
            return
        texts = value if isinstance(value, list) else [value]
        if member == "summaryLines":                                      # plain text for the clipboard ("Copy summary"), never inserted as HTML
            if not (isinstance(value, list) and all(isinstance(t, str) for t in value)):
                problems.append(f"summaryLines() must return a list of strings (the lines of the copied summary); it returned {json.dumps(value)[:80]} on {on}")
                return
        elif member == "geoInfo":
            if not isinstance(value, dict):
                problems.append("geoInfo(data) must return the object stored as entry.geo_resolution ({method, dmas, unmatched_tokens, win_rate, hispanic_share})")
            return
        elif not all(isinstance(t, str) for t in texts):
            problems.append(f"{member}() must return a string of HTML; it returned {json.dumps(value)[:80]} on {on}")
            return
        if restored and pair == "sample":                                 # the words a planner would read: not a template that printed what was not there
            shown = sorted(set(re.findall(r"\b(undefined|NaN|null)\b", re.sub(r"<[^>]*>", " ", " ".join(texts)))))
            if shown:
                problems.append(f"{member}() printed {', '.join(shown)} in its text on {on}: a field it read from asked.method (or from the answer) was not there - fall back to the "
                                "answer's own echo, or leave the words out")
        if member == "summaryLines":
            return                                                        # (the clipboard text is never HTML: the markup rule below is for the Html members)
        for text in texts:
            if hostile_text in text or re.search(r"<img\b[^>]*onerror", text, re.I) or re.search(r"<script\b", text, re.I):
                problems.append(f"{member}() drew server text as markup on {on}: escape every server or planner string with ctx.esc(...) before it goes into HTML")
                break

    for name, value in sorted(out["outputs"].items()):
        judge(name, value)
    for name, value in sorted(out["restored"].items()):
        judge(name, value, restored=True)
    return problems


@pytest.mark.parametrize("key", KEYS)
def test_c9_the_plugin_conforms_to_the_contract_and_escapes_what_the_server_says(key):
    assert_ok("C9", key, check_plugin_conformance(key))


def _answer_for_dummy(note: str = DUMMY_ANSWER_NOTE) -> dict:
    spec = make_dummy_spec("dummy")
    return methods.standard_estimate(spec, product_name=DUMMY_PRODUCT, months=2, imps={"low": 10, "mid": 20, "high": 30}, est_uniques=10, notes=[note], block={})


def test_twin_c9_a_plugin_with_a_typo_a_missing_member_or_raw_markup_is_named():
    answer = _answer_for_dummy()
    typo = DUMMY_PLUGIN.replace("headlineHtml:", "headlineHtm:")
    text = "\n".join(check_plugin_conformance("dummy", plugin_source=typo, answer=answer))
    assert "the dock refused the plugin" in text and 'unknown member "headlineHtm"' in text and "missing headlineHtml" in text and "docs/constanza_methods.md" in text
    hostile = _answer_for_dummy(HOSTILE)
    hostile["product_name"] = HOSTILE
    raw = DUMMY_PLUGIN.replace("const name = (d) => esc((d && d.product_name) || \"\");", "const name = (d) => String((d && d.product_name) || \"\");")
    text = "\n".join(check_plugin_conformance("dummy", plugin_source=raw, answer=answer, hostile_answer=hostile))
    assert "headlineHtml() drew server text as markup on the hostile answer: escape every server or planner string with ctx.esc(...)" in text
    wrong_block = DUMMY_PLUGIN.replace("requestPart: () => ({ dummy: {} })", "requestPart: () => ({ dummy: {}, extra: 1 })")
    assert "requestPart() must return exactly {dummy: <object>}" in "\n".join(check_plugin_conformance("dummy", plugin_source=wrong_block, answer=answer))
    throws = DUMMY_PLUGIN.replace("cardHtml: (d) => `<p>${name(d)}</p>`", "cardHtml: (d) => { throw new Error('boom'); }")
    assert "cardHtml() threw on the sample answer: boom" in "\n".join(check_plugin_conformance("dummy", plugin_source=throws, answer=answer))
    assert check_plugin_conformance("dummy", plugin_source=DUMMY_PLUGIN, answer=answer, hostile_answer=_answer_for_dummy(HOSTILE)) == []        # (the good one passes)


def test_twin_c9_a_plugin_that_cannot_describe_a_restored_result_without_its_context_is_named():
    """The host rule of the restored result: no asked.method when the form was edited since the pull. A plugin that reads it unguarded throws, one that prints it
    unguarded writes undefined / NaN / null into the planner's words, one that escapes only while the context is there leaves markup - and one that falls back to the
    answer's own echo passes."""
    answer = _answer_for_dummy()
    hostile = _answer_for_dummy(HOSTILE)
    hostile["product_name"] = HOSTILE

    def named(source: str) -> str:
        return "\n".join(check_plugin_conformance("dummy", plugin_source=source, answer=answer, hostile_answer=hostile))

    body = "bodyHtml: (d) => `<p>${name(d)}</p>`"
    lines = "summaryLines: (d) => [String((d && d.product_name) || \"\")]"
    assert body in DUMMY_PLUGIN and lines in DUMMY_PLUGIN, "DUMMY_PLUGIN no longer has the members this twin edits: update the twin"
    # 1. reads asked.method unguarded: fine while the context is there (the usual call), a throw when it is not
    unguarded = DUMMY_PLUGIN.replace(body, "bodyHtml: (d, m, asked) => `<p>${name(d)} ${esc(asked.method.summary.join(', '))}</p>`")
    text = named(unguarded)
    assert "bodyHtml() threw on the sample answer of a restored result" in text and "bodyHtml() threw on the hostile answer of a restored result" in text
    assert "guard the read and describe the result from the answer's own echo" in text and 'asked = {geo: "", areas: [], method: null}' in text
    assert "threw on the sample answer:" not in text, text                                          # (the usual call, with its context, is fine)
    # 2. prints what is not there: null (a guarded read that falls through), undefined (a missing field), NaN (arithmetic on one)
    printing = DUMMY_PLUGIN.replace(lines, "summaryLines: (d, asked) => [String((d && d.product_name) || \"\"), 'Asked: ' + (asked.method && asked.method.geo), "
                                           "'Place: ' + asked.where, 'Months: ' + Number(asked.months)]")
    text = named(printing)
    assert "summaryLines() printed NaN, null, undefined in its text on the sample answer of a restored result" in text, text
    assert "hostile answer of a restored result" not in text                                       # (the words are judged on the sample answer only)
    in_html = DUMMY_PLUGIN.replace(body, "bodyHtml: (d, m, asked) => `<p>${name(d)} <b>${asked.method && asked.method.geo}</b></p>`")
    assert "bodyHtml() printed null in its text on the sample answer of a restored result" in named(in_html)
    # 3. escapes while the context is there, raw markup when it is not
    raw_without_context = DUMMY_PLUGIN.replace(body, "bodyHtml: (d, m, asked) => (asked.method ? `<p>${name(d)}</p>` : `<p>${String((d && d.product_name) || '')}</p>`)")
    text = named(raw_without_context)
    assert "bodyHtml() drew server text as markup on the hostile answer of a restored result" in text, text
    assert "drew server text as markup on the hostile answer:" not in text                         # (the usual call is escaped)
    # 4. a summary that is not a list of strings, and a body that is not a string, on the restored path
    wrong = DUMMY_PLUGIN.replace(lines, "summaryLines: (d, asked) => (asked.method ? [String((d && d.product_name) || '')] : 'one string')").replace(body, "bodyHtml: (d, m, asked) => (asked.method ? `<p>${name(d)}</p>` : 7)")
    text = named(wrong)
    assert "summaryLines() must return a list of strings" in text and "bodyHtml() must return a string of HTML; it returned 7 on the sample answer of a restored result" in text
    # the plugins that read it the way the host means it pass
    guarded = DUMMY_PLUGIN.replace(body, "bodyHtml: (d, m, asked) => `<p>${name(d)} ${esc(((asked && asked.method && asked.method.summary) || []).join(', '))}</p>`")
    assert named(guarded) == "" and named(DUMMY_PLUGIN) == ""


def test_twin_c9_a_missing_javascript_engine_is_a_failure_when_ci_demands_one_and_a_skip_otherwise(monkeypatch):
    h = _harness()
    monkeypatch.setattr(h, "_engine_command", lambda: None)
    monkeypatch.delenv("REQUIRE_JS_ENGINE", raising=False)
    with pytest.raises(pytest.skip.Exception):
        run_js("return JSON.stringify({});")
    monkeypatch.setenv("REQUIRE_JS_ENGINE", "1")
    with pytest.raises(pytest.fail.Exception) as err:
        run_js("return JSON.stringify({});")
    assert "REQUIRE_JS_ENGINE=1" in str(err.value) and "enforces nothing" in str(err.value)


# ================================================================================================================================ C10 app.js hygiene

HOST_BEGIN, HOST_END = "// CONSTANZA HOST: begin", "// CONSTANZA HOST: end"


def host_region(source: str) -> tuple[str, list[str]]:
    """The text between the two markers of app.js's Constanza section, and what is wrong with the markers."""
    problems: list[str] = []
    if source.count(HOST_BEGIN) != 1 or source.count(HOST_END) != 1:
        return "", [f"app.js must contain `{HOST_BEGIN}` and `{HOST_END}` exactly once each around the Constanza section (it has {source.count(HOST_BEGIN)} and "
                    f"{source.count(HOST_END)}): the hygiene check is anchored on them, so a missing marker would make it pass for nothing"]
    start, end = source.index(HOST_BEGIN), source.index(HOST_END)
    if end < start:
        return "", [f"`{HOST_END}` comes before `{HOST_BEGIN}` in app.js"]
    region = source[start:end]
    if len(region.splitlines()) < 100:
        problems.append(f"the region between the CONSTANZA HOST markers is only {len(region.splitlines())} lines: the Constanza section is hundreds of lines, so the markers have "
                        "moved off it and the hygiene check would be looking at nothing")
    return region, problems


def grid_body(source: str) -> str:
    match = re.search(r"^(?:async )?function renderAvailsGrid\(", source, re.M)
    if not match:
        return ""
    return source[match.start(): source.index("\n}\n", match.start()) + 2]


# A method key that is also a CSS word (`display`, `none` ...) may stand as a plain string in the page scripts, so the two literal rules below skip it; every
# other rule still looks for it. Keep this list to CSS values and property names: a real method key is never added to it.
CSS_ISH_KEYS = ("display", "block", "flex", "grid", "none", "hidden", "inline", "table", "auto")


def literal_patterns(key: str) -> dict[str, str]:
    """A key written out as a VALUE: any string exactly equal to it (an `.includes`, a `.startsWith`, an array, a map lookup, a comparison written any way)
    and an object key (`{ key: 1 }`). A string that merely contains the word (a sentence in a message) is not matched."""
    k = re.escape(key)
    return {
        f'the string "{key}" written out as a value (["a", "{key}"].includes(m), m.startsWith("{key}") ...)': rf"""["'`]{k}["'`]""",
        f"an object key {{ {key}: ... }}": rf"[{{,]\s*{k}\s*:(?!:)",
    }


def code_patterns(key: str) -> dict[str, str]:
    k = re.escape(key)
    patterns = {
        f'a comparison with "{key}"': rf"""[=!]==?\s*["'`]{k}["'`]|["'`]{k}["'`]\s*[=!]==?""",
        f"`.{key}` on an estimate (data.{key}, result.{key} ...)": rf"\b(?:data|result|res|est|estimate|answer|response|out|snap|snapshot|saved|entry|body|json|plug|plugin)\.{k}\b",
        f"a data-{key}- attribute": rf"data-{k}-",
        f"a name starting _{key}": rf"(?<![\w$.])_{k}[A-Za-z0-9_]*",
        f"state.{key}...": rf"\bstate\.{k}(?![a-z0-9_])\w*",
        f"a lookup [\"{key}\"] or case \"{key}\"": rf"""\[\s*["'`]{k}["'`]\s*\]|\bcase\s+["'`]{k}["'`]""",
        f"the route /api/constanza/{key}": rf"/api/constanza/{k}(?![A-Za-z0-9_])",
    }
    if key not in CSS_ISH_KEYS:
        patterns.update(literal_patterns(key))
    return patterns


def host_members(source: str) -> list[str] | None:
    """The member names of the object literal assigned to window.constanzaHost (a brace-aware read, not a regex over indentation); None when it is not there."""
    code = strip_js_comments(source)
    match = re.search(r"window\.constanzaHost\s*=\s*\{", code)
    if not match:
        return None
    i, depth, names, token = match.end(), 1, [], ""
    quote = None
    while i < len(code) and depth > 0:
        c = code[i]
        if quote:
            if c == "\\":
                i += 1
            elif c == quote:
                quote = None
        elif c in "'\"`":
            quote = c
        elif c in "{([":
            depth += 1
        elif c in "})]":
            depth -= 1
        elif depth == 1 and c == ",":
            token = ""
        elif depth == 1 and (c.isalnum() or c in "_$"):
            token += c
        elif depth == 1 and c == ":" and token:
            names.append(token)
            token = ""
        elif depth == 1 and c.isspace():
            pass
        elif depth == 1:
            token = ""
        i += 1
    return names


def check_app_js(source: str, keys: list[str]) -> list[str]:
    region, problems = host_region(source)
    if not region:
        return problems
    grid = grid_body(source)
    if not grid:
        problems.append("app.js has no function renderAvailsGrid: the Avails grid is where every Step 05 block is drawn, and it is checked too")
    text = strip_js_comments(region) + "\n" + strip_js_comments(grid)
    for key in keys:
        for what, pattern in code_patterns(key).items():
            hit = re.search(pattern, text)
            if hit:
                line = text[: hit.start()].count("\n") + 1
                problems.append(f"app.js names the method '{key}' in code ({what}, near line {line} of the checked text: {hit.group(0)!r}): the page never names a method - what differs "
                                "between methods is a flag or a string the method's own script gives constanza.js (caps, channelLabel, badgeHtml, geoInfo ...); move this into constanza-"
                                f"{key}.js (docs/constanza_methods.md, \"The plugin contract\")")
    routes = set(re.findall(r"/api/constanza/([A-Za-z0-9_-]+)", text))
    stray = sorted(routes - {"estimate", "resolve"})
    if stray:
        problems.append(f"app.js's Constanza section calls /api/constanza/{', /api/constanza/'.join(stray)}: only estimate and resolve are the page's own; a method's routes belong to its script")
    members = host_members(source)
    if members != ["lineFacts", "snapshot", "saveSnapshot", "onDirty"]:
        problems.append(f"window.constanzaHost must have exactly lineFacts, snapshot, saveSnapshot, onDirty (found {members}): it is the whole of what constanza.js reads from the page")
    return problems


# The page scripts that must stay method-free besides app.js's Constanza section: the host (constanza.js) knows methods only through plugins, and Admin draws its
# method controls from the server's list. A registry key written out as a string or an object key in any of them is a method hard-coded into the page.
PAGE_FILES = {"constanza.js": STATIC / "constanza.js", "admin.js": STATIC / "admin.js", "admin.html": ROOT / "app" / "templates" / "admin.html"}
# Where a registry key may stand as a string in those files anyway: {file: {key: the reason}}. EMPTY today, and meant to stay short: each entry needs a reason that
# says why it is not a method branch (a label in a message is not matched at all; this is for an exact value, e.g. a key that must be echoed back to the server).
LITERAL_ALLOWED: dict[str, dict[str, str]] = {}


def strip_html_comments(text: str) -> str:
    return re.sub(r"<!--.*?-->", lambda m: "\n" * m.group(0).count("\n"), text, flags=re.S)


def check_page_literals(sources: dict[str, str], keys: list[str], allowed: dict[str, dict[str, str]] | None = None) -> list[str]:
    """A registry key written out as a string value or an object key (comments stripped) in constanza.js, admin.js or admin.html, except where LITERAL_ALLOWED says
    why. `sources` is {file name: text}."""
    allowed = LITERAL_ALLOWED if allowed is None else allowed
    problems: list[str] = []
    for name, source in sources.items():
        text = strip_html_comments(source) if name.endswith(".html") else strip_js_comments(source)
        for key in keys:
            if key in CSS_ISH_KEYS or key in allowed.get(name, {}):
                continue
            for what, pattern in literal_patterns(key).items():
                hit = re.search(pattern, text)
                if hit:
                    line = text[: hit.start()].count("\n") + 1
                    problems.append(f"{name} names the method '{key}' as {what} (line {line}: {hit.group(0)!r}): this file never names a method - it learns what methods exist from "
                                    f"the registry (Admin) or from the plugin's own script (constanza.js). If this really is not a method branch, add it to LITERAL_ALLOWED in "
                                    "tests/test_constanza_method_contract.py with the reason")
    for name, entries in allowed.items():
        for key, reason in entries.items():
            if not (isinstance(reason, str) and reason.strip()):
                problems.append(f"LITERAL_ALLOWED[{name!r}][{key!r}] has no reason: say why this occurrence is not a method hard-coded into the page")
    return problems


def test_c10_the_constanza_section_of_app_js_names_no_method_in_code():
    assert_ok("C10", "*", check_app_js((STATIC / "app.js").read_text(encoding="utf-8"), list(KEYS)))


def test_c10_constanza_js_admin_js_and_admin_html_write_no_registry_key_out_as_a_value():
    assert_ok("C10", "*", check_page_literals({name: path.read_text(encoding="utf-8") for name, path in PAGE_FILES.items()}, list(KEYS)))


def test_c10_the_comment_stripper_leaves_code_and_strings_alone_on_the_real_files():
    for name in ("app.js", "constanza.js", "constanza-meta.js", "constanza-tiktok.js"):
        source = (STATIC / name).read_text(encoding="utf-8")
        stripped = strip_js_comments(source)
        assert 0.4 < len(stripped) / len(source) < 1.0 and stripped.count("\n") == source.count("\n"), name
        # what is left still parses as JavaScript (a stripper that ate code or kept half a comment would break it)
        assert run_js(f"var f = new Function({json.dumps(stripped)}); return JSON.stringify({{ ok: typeof f }});") == {"ok": "function"}, name


FAKE_APP_JS = textwrap.dedent("""
    function renderAvailsGrid() {
      return `${_constanzaBlockHtml(li, p, e)}`;
    }
    // CONSTANZA HOST: begin
    %(body)s
    window.constanzaHost = {
      lineFacts: lid => 1,
      snapshot: lid => 2,
      saveSnapshot: (lid, s) => { return 3; },
      onDirty: lid => 4,
    };
    // CONSTANZA HOST: end
""")


def _fake_app(body: str = "", filler: int = 120, host: bool = True) -> str:
    text = FAKE_APP_JS % {"body": body + "\n".join(f"const filler{i} = {i};" for i in range(filler))}
    return text if host else text.replace("window.constanzaHost", "window.somethingElse")


def test_twin_c10_a_method_named_in_code_a_missing_marker_a_short_region_and_a_wrong_host_are_named():
    assert check_app_js(_fake_app("// the dummy method does this: if (x === 'dummy') in a comment\nconst note = 'dummy words in a string';\n"), ["dummy"]) == []
    for body, expect in (("if (method === 'dummy') { }", 'a comparison with "dummy"'), ("const t = data.dummy.audience;", "`.dummy` on an estimate"),
                         ("el.setAttribute('data-dummy-x', 1);", "a data-dummy- attribute"), ("function _dummyBind() {}", "a name starting _dummy"),
                         ("state.dummySel = {};", "state.dummy..."), ("fetch('/api/constanza/dummy/status');", "the route /api/constanza/dummy"),
                         ("const x = table['dummy'];", "a lookup")):
        text = "\n".join(check_app_js(_fake_app(body), ["dummy"]))
        assert f"app.js names the method 'dummy' in code ({expect}" in text and "move this into constanza-dummy.js" in text, (body, text)
    assert "calls /api/constanza/other" in "\n".join(check_app_js(_fake_app("fetch('/api/constanza/other/x');"), []))
    no_marker = _fake_app().replace(HOST_BEGIN, "// begin")
    assert "must contain `// CONSTANZA HOST: begin`" in "\n".join(check_app_js(no_marker, ["dummy"]))
    short = "\n".join(check_app_js(_fake_app(filler=3), ["dummy"]))
    assert "is only " in short and "markers have moved off it" in short
    assert "window.constanzaHost must have exactly" in "\n".join(check_app_js(_fake_app(host=False), []))
    a_key_in_the_grid = _fake_app().replace("return `${_constanzaBlockHtml(li, p, e)}`;", "return data.dummy ? 1 : 2;")
    assert "app.js names the method 'dummy'" in "\n".join(check_app_js(a_key_in_the_grid, ["dummy"]))             # the Avails grid is checked as well


def test_twin_c10_a_key_written_out_as_a_string_or_an_object_key_is_named_wherever_it_hides():
    """What the comparison rules cannot see: `["a", "dummy"].includes(m)`, `m.startsWith("dummy")`, `({ dummy: 1 })[m]`, a template or single-quoted string."""
    for body in ('if (["other", "dummy"].includes(m)) { }', 'if (m.startsWith("dummy")) { }', "const t = ({ dummy: 1 })[m];", "const t = ({ a: 1, dummy: 2 });",
                 "const names = [`dummy`];", "const k = 'dummy';"):
        text = "\n".join(check_app_js(_fake_app(body), ["dummy"]))
        assert "app.js names the method 'dummy' in code (" in text, (body, text)
    for body in ("const text = 'the dummy method does this';", "const url = 'dummy-things';", "const t = cond ? dummy : other;", "x.dummy = 1;", "const t = { dummy_a: 1 };"):
        assert check_app_js(_fake_app(body), ["dummy"]) == [], body                       # a longer string, another word, a property: not the key written out
    in_the_grid = _fake_app().replace("return `${_constanzaBlockHtml(li, p, e)}`;", 'return ["dummy"].includes(x) ? 1 : 2;')
    assert "app.js names the method 'dummy'" in "\n".join(check_app_js(in_the_grid, ["dummy"]))
    for sources in ({"constanza.js": 'if (["tiktok", "dummy"].includes(key)) {}'}, {"admin.js": "const t = ({ dummy: 1 })[m];"},
                    {"admin.html": '<option value="dummy">Dummy</option>'}, {"admin.html": '<div data-method="dummy"></div>'}):
        name = next(iter(sources))
        text = "\n".join(check_page_literals(sources, ["dummy"], {}))
        assert f"{name} names the method 'dummy' as " in text and "LITERAL_ALLOWED" in text, (sources, text)
    quiet = {"constanza.js": '// dummy: ["dummy"].includes(x) in a comment\nconst a = "the dummy panel";', "admin.js": "/* { dummy: 1 } */ const b = 'dummy_x';",
             "admin.html": '<!-- <option value="dummy"> -->\n<p>dummy words</p>'}
    assert check_page_literals(quiet, ["dummy"], {}) == []                                 # comments and longer strings do not count
    allowed = {"admin.js": {"dummy": "echoed back to the server as the one value it asks for"}}
    assert check_page_literals({"admin.js": 'send("dummy");'}, ["dummy"], allowed) == []   # an allow-listed occurrence passes ...
    assert check_page_literals({"constanza.js": 'send("dummy");'}, ["dummy"], allowed) != []       # ... but only in its own file
    assert any("has no reason" in p for p in check_page_literals({}, ["dummy"], {"admin.js": {"dummy": "  "}}))
    assert check_page_literals({"admin.js": 'x = "display"; y = { display: 1 };'}, ["display"], {}) == []     # a CSS word as a key is exempt (CSS_ISH_KEYS)
    assert not LITERAL_ALLOWED or all(reason.strip() for entries in LITERAL_ALLOWED.values() for reason in entries.values())


def test_c10_a_key_that_is_an_ordinary_property_name_is_not_flagged_for_a_css_word():
    """`display` as a method key must not fail on `el.style.display` (only an estimate-like name followed by .display counts), nor on the CSS value
    string 'display' or an object such as { display: 'none' } (CSS_ISH_KEYS): those two literal rules do not apply to a key that is also a CSS word."""
    assert check_app_js(_fake_app("el.style.display = 'block'; const label = 'display'; const s = { display: 'none' };\n"), ["display"]) == []
    assert check_app_js(_fake_app("if (method === 'display') {}"), ["display"]) != []                    # ... but a comparison with it is still a method branch


# ================================================================================================================================ C11 Admin

def check_admin(key: str, *, routes: list | None = None, admin_html: str | None = None, admin_js: str | None = None) -> list[str]:
    spec = methods.get(key)
    routes = _route_table() if routes is None else routes
    admin_html = (ROOT / "app" / "templates" / "admin.html").read_text(encoding="utf-8") if admin_html is None else admin_html
    admin_js = (STATIC / "admin.js").read_text(encoding="utf-8") if admin_js is None else admin_js
    problems: list[str] = []
    with pytest.MonkeyPatch.context() as mp:
        if key in FAKES:
            FAKES[key].install(mp)
        light = asyncio.run(main.admin_constanza_methods())["methods"]
        heavy = asyncio.run(main.admin_get_forecast_map())
    for where, listed in (("GET /api/admin/constanza/methods", light), ("GET /api/admin/forecast-map", heavy.get("methods") or [])):
        entry = next((m for m in listed if m.get("key") == key), None)
        if entry is None:
            problems.append(f"{where} has no methods[] entry for '{key}': Admin draws its select, summary card and connection card from it (constanza_forecast_map.methods_payload "
                            "reads the registry, so a registered method appears by itself - is the module in constanza_methods.MODULES?)")
            continue
        if spec is not None:
            for field, expected in (("label", spec.label), ("short_label", spec.short_label), ("kind", spec.kind), ("secrets", list(spec.secrets)),
                                    ("owns_geography", spec.owns_geography), ("default_products", list(spec.default_products))):
                if entry.get(field) != expected:
                    problems.append(f"{where}: methods[].{field} for '{key}' is {entry.get(field)!r}, not what the registration says ({expected!r})")
            if spec.kind == "api" and (not entry.get("status_url") or not entry.get("test_url")):
                problems.append(f"{where}: an API method needs status_url and test_url for its connection card (got {entry.get('status_url')!r}, {entry.get('test_url')!r})")
    if key not in (heavy.get("summary") or {}):
        problems.append(f"GET /api/admin/forecast-map: summary has no count for '{key}' (admin_payload counts one per registry key)")
    if spec is not None and spec.kind == "api":
        test_path = "/api/admin/constanza/{key}/test"
        if not any(path == test_path and "POST" in verbs for path, verbs in routes):
            problems.append("there is no POST /api/admin/constanza/{key}/test route: it is the connection card's \"Test the connection\" for every API method")
        try:
            main.admin_constanza_test("no_such_method_here")
            problems.append("POST /api/admin/constanza/<unknown>/test must answer 404, not run something")
        except HTTPException as exc:
            if exc.status_code != 404:
                problems.append(f"POST /api/admin/constanza/<unknown>/test answered {exc.status_code}, not 404")
    for where, text in (("admin.html", admin_html), ("admin.js", admin_js)):
        if re.search(rf"""value=["']{re.escape(key)}["']""", text):
            problems.append(f"{where} offers '{key}' as a literal <option>: the \"What it computes\" select is drawn from methods[] - remove the literal, Admin needs no edit for a new method")
        if re.search(rf"fc-conn-{re.escape(key)}-|fc-{re.escape(key)}\b", text):
            problems.append(f"{where} spells out an element id for '{key}' (fc-conn-{key}-...): the connection card is generated for every API method; remove it")
    for stale in ("fc-tt-", "fc-tt\""):
        if stale in admin_html or stale in admin_js:
            problems.append(f"admin.html / admin.js still contain {stale!r}, the old hand-written TikTok card: it is generated now")
    return problems


@pytest.mark.parametrize("key", KEYS)
def test_c11_admin_is_generated_from_the_registry(key):
    assert_ok("C11", key, check_admin(key))


def test_twin_c11_a_hand_written_card_and_a_missing_test_route_are_named():
    html = '<select><option value="dummy">Dummy</option></select>'
    js = "const id = 'fc-conn-dummy-state';"
    with dummy_method() as spec:
        text = "\n".join(check_admin(spec.key, routes=[("/api/constanza/estimate", {"POST"})], admin_html=html, admin_js=js))
    assert "there is no POST /api/admin/constanza/{key}/test route" in text
    assert "admin.html offers 'dummy' as a literal <option>" in text and "admin.js spells out an element id for 'dummy'" in text
    assert "has no methods[] entry for 'never_registered'" in "\n".join(check_admin("never_registered"))


# ================================================================================================================================ C12 database and the saved entry

def check_database_and_entry(*, schema_sql: str | None = None, migrations_dir: Path | None = None, entry_model: type[BaseModel] | None = None,
                             keys: list[str] | None = None) -> list[str]:
    schema_sql = (ROOT / "schema.sql").read_text(encoding="utf-8") if schema_sql is None else schema_sql
    migrations_dir = migrations_dir or ROOT / "migrations"
    entry_model = entry_model or main.AvailsEntry
    keys = list(KEYS) if keys is None else keys
    problems: list[str] = []
    table = re.search(r"CREATE TABLE IF NOT EXISTS forecast_product_map \((.*?)\n\);", schema_sql, re.S)
    check = re.search(r"method\s+TEXT NOT NULL CHECK \(method ~ '([^']+)'\)", table.group(1)) if table else None
    if not check or check.group(1) != methods.KEY_RE.pattern:
        problems.append(f"schema.sql's forecast_product_map.method CHECK must be the key FORMAT (method ~ '{methods.KEY_RE.pattern}'), not a list of names: a new method must "
                        "never need SQL (docs/constanza_methods.md step 8)")
    if table and re.search(r"method\s+IN\s*\(", table.group(1)):
        problems.append("schema.sql enumerates method names in the forecast_product_map CHECK (IN (...)): replace it with the key-format check - adding a method must need no SQL")
    latest = None
    for path in sorted(migrations_dir.glob("*.sql")):
        text = path.read_text(encoding="utf-8")
        if "forecast_product_map_method_check" in text:
            latest = (path, text)
    if latest is None:
        problems.append("no migration sets forecast_product_map_method_check: migration 0006 widens it to the key format")
    else:
        found = re.search(r"CHECK \(method ~ '([^']+)'\)", latest[1])
        if not found or found.group(1) != methods.KEY_RE.pattern:
            problems.append(f"the latest migration that touches forecast_product_map_method_check ({latest[0].name}) must set the key-format check '{methods.KEY_RE.pattern}', not a list of names")
    for key in keys:
        if not methods.KEY_RE.fullmatch(key):
            problems.append(f"the method key {key!r} does not match the database's key format {methods.KEY_RE.pattern}")
    per_method = sorted(name for name in entry_model.model_fields if name.endswith("_forecast") or name.endswith("_snapshot"))
    extra = [n for n in per_method if n not in ("forecast_snapshot", "meta_forecast")]
    if "forecast_snapshot" not in entry_model.model_fields:
        problems.append("AvailsEntry has no forecast_snapshot field: it is the ONE field every method's last estimate is saved in (docs/constanza_methods.md, \"Persistence\")")
    for name in extra:
        problems.append(f"AvailsEntry has a new per-method field '{name}': store the snapshot in forecast_snapshot, never add a field per method (pydantic drops undeclared keys, "
                        "but a field per method makes the saved shape grow with every source)")
    if "meta_forecast" not in entry_model.model_fields:
        problems.append("AvailsEntry must keep declaring meta_forecast: saved proposals still hold it, and a field that is not declared is silently dropped on the next save")
    return problems


def test_c12_the_database_checks_a_key_format_and_a_saved_line_has_one_snapshot_field():
    assert_ok("C12", "*", check_database_and_entry())


def test_twin_c12_a_list_of_names_and_a_per_method_field_are_named(tmp_path):
    schema = "CREATE TABLE IF NOT EXISTS forecast_product_map (\n  product_name TEXT PRIMARY KEY,\n  method TEXT NOT NULL CHECK (method IN ('platform','generic','dummy'))\n);"
    (tmp_path / "0001_x.sql").write_text("ALTER TABLE forecast_product_map ADD CONSTRAINT forecast_product_map_method_check CHECK (method IN ('platform','dummy'));")

    class Entry(BaseModel):
        meta_forecast: typing.Optional[dict] = None
        dummy_forecast: typing.Optional[dict] = None

    text = "\n".join(check_database_and_entry(schema_sql=schema, migrations_dir=tmp_path, entry_model=Entry, keys=["Dummy-Key"]))
    assert "must be the key FORMAT" in text and "enumerates method names" in text and "must set the key-format check" in text
    assert "does not match the database's key format" in text
    assert "AvailsEntry has no forecast_snapshot field" in text
    assert "new per-method field 'dummy_forecast': store the snapshot in forecast_snapshot" in text
    assert any("does not match the database's key format" in p for p in check_database_and_entry(keys=["meta\n"]))      # Python's `$` forgives a final newline, Postgres's does not


# ================================================================================================================================ C13 docs

def _cells(line: str) -> list[str]:
    parts = re.split(r"(?<!\\)\|", line.strip())
    return [p.strip().replace("\\|", "|") for p in parts[1:-1]]


def registry_table(text: str) -> dict[str, dict[str, str]]:
    """{key: {column: cell}} of the guide's registry table (the first table whose header has Key and Owns geography)."""
    rows: dict[str, dict[str, str]] = {}
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("|") and "Key" in line and "Owns geography" in line and i + 1 < len(lines) and set(lines[i + 1].replace("|", "").strip()) <= set("-: "):
            header = _cells(line)
            for row in lines[i + 2:]:
                if not row.startswith("|"):
                    break
                cells = _cells(row)
                if cells and len(cells) == len(header):
                    rows[cells[0].strip("`")] = dict(zip(header, cells))
            break
    return rows


GUIDE_STEPS = tuple(range(1, 11))


def check_docs(key: str, *, guide_text: str | None = None, docs_dir: Path | None = None, plugin_js: str | None = None) -> list[str]:
    spec = methods.get(key)
    guide_text = GUIDE.read_text(encoding="utf-8") if guide_text is None and GUIDE.exists() else (guide_text or "")
    docs_dir = docs_dir or ROOT / "docs"
    problems: list[str] = []
    if not guide_text:
        return ["docs/constanza_methods.md does not exist: it is the guide to adding a source and holds the registry table (one row per method)"]
    for number in GUIDE_STEPS:
        if not re.search(rf"^#{{2,4}}\s+Step {number}\b", guide_text, re.M):
            problems.append(f"docs/constanza_methods.md has no heading \"Step {number}\": the checklist is ten numbered steps, each a heading (### Step {number} - ...)")
            break
    if not re.search(r"^##\s+The registry table", guide_text, re.M):
        problems.append('docs/constanza_methods.md has no "## The registry table" section')
    row = registry_table(guide_text).get(key)
    if row is None:
        problems.append(f"docs/constanza_methods.md has no row for '{key}' in the registry table: add one (step 9 of the checklist) - | `{key}` | label | secrets | files | helper "
                        "routes | owns geography | persists | default products |")
    elif spec is not None:
        label = row.get("Label", "")
        if spec.label not in label:
            problems.append(f"the registry row for '{key}' gives the label {label!r}; the registration says {spec.label!r}")
        for secret in spec.secrets:
            if secret not in row.get("Secrets", ""):
                problems.append(f"the registry row for '{key}' does not mention the secret {secret} (Secrets column): an admin reads this to know what to set")
        module_file = getattr(spec.estimate, "__module__", "").rsplit(".", 1)[-1] + ".py"
        if module_file not in row.get("Files", "") or f"constanza-{key}.js" not in row.get("Files", ""):
            problems.append(f"the registry row for '{key}' must list its files in the Files column: {module_file} and constanza-{key}.js")
        owns = "yes" if spec.owns_geography else "no"
        if row.get("Owns geography", "").lower().split(" ")[0].strip("*") != owns:
            problems.append(f"the registry row for '{key}' says Owns geography = {row.get('Owns geography')!r}; the registration says {owns}")
        if plugin_js is None:
            path = STATIC / f"constanza-{key}.js"
            plugin_js = path.read_text(encoding="utf-8") if path.exists() else ""
        persists = "yes" if re.search(r"caps\s*:\s*\{[^}]*\bpersist\s*:\s*true", strip_js_comments(plugin_js)) else "no"
        if row.get("Persists", "").lower().split(" ")[0].strip("*") != persists:
            problems.append(f"the registry row for '{key}' says Persists = {row.get('Persists')!r}; the plugin's caps say {persists}")
        for product in spec.default_products:
            if product not in row.get("Default products", ""):
                problems.append(f"the registry row for '{key}' does not list its default product {product!r} (Default products column; escape a | as \\|)")
        if f"/api/constanza/{key}/status" not in row.get("Helper routes", ""):
            problems.append(f"the registry row for '{key}' must list /api/constanza/{key}/status in the Helper routes column")
    page = docs_dir / f"{key}_forecasting.md"
    if not page.exists() or not page.read_text(encoding="utf-8").strip():
        problems.append(f"docs/{key}_forecasting.md does not exist: every method has its own page (what it answers, what it does not, how to set it up, what is unverified); "
                        "link it from the registry row")
    return problems


@pytest.mark.parametrize("key", KEYS)
def test_c13_the_guide_has_the_methods_row_and_the_method_has_its_own_page(key):
    assert_ok("C13", key, check_docs(key))


GOOD_ROW = ("| `dummy` | Dummy (live) | `DUMMY_ACCESS_TOKEN` | `test_constanza_method_contract.py`, `constanza-dummy.js` | `/api/constanza/dummy/status` | yes | no | Search - SEM |")
GOOD_GUIDE = ("## The registry table\n\n| Key | Label | Secrets | Files | Helper routes | Owns geography | Persists | Default products |\n|---|---|---|---|---|---|---|---|\n"
              + GOOD_ROW + "\n" + "\n".join(f"### Step {n} - x" for n in GUIDE_STEPS) + "\n")


def test_twin_c13_a_doc_without_its_row_or_with_a_wrong_one_is_named(tmp_path):
    (tmp_path / "dummy_forecasting.md").write_text("# Dummy\n")
    with dummy_method(default_products=("Search - SEM",)) as spec:
        assert check_docs(spec.key, guide_text=GOOD_GUIDE, docs_dir=tmp_path, plugin_js="window.Constanza.registerMethod('dummy', f => ({ caps: { persist: false } }));") == []
        text = "\n".join(check_docs(spec.key, guide_text=GOOD_GUIDE.replace(GOOD_ROW, "").replace("### Step 7 - x\n", ""), docs_dir=tmp_path))
        assert "has no heading \"Step 7\"" in text and "has no row for 'dummy' in the registry table: add one (step 9 of the checklist)" in text
        wrong = GOOD_ROW.replace("`DUMMY_ACCESS_TOKEN`", "-").replace("| yes | no |", "| no | yes |").replace("Search - SEM", "x")
        text = "\n".join(check_docs(spec.key, guide_text=GOOD_GUIDE.replace(GOOD_ROW, wrong), docs_dir=tmp_path, plugin_js=""))
        assert "does not mention the secret DUMMY_ACCESS_TOKEN" in text and "Owns geography = 'no'" in text and "Persists = 'yes'" in text
        assert "does not list its default product 'Search - SEM'" in text
        empty = tmp_path / "empty"
        empty.mkdir()
        assert "docs/dummy_forecasting.md does not exist" in "\n".join(check_docs(spec.key, guide_text=GOOD_GUIDE, docs_dir=empty))
    assert "docs/constanza_methods.md does not exist" in check_docs("dummy", guide_text="", docs_dir=tmp_path)[0]


def _guide_block(marker: str) -> str:
    """The first fenced block of the guide that follows a line containing `marker`."""
    text = GUIDE.read_text(encoding="utf-8")
    start = text.index(marker)
    fence = text.index("```", start)
    body_start = text.index("\n", fence) + 1
    return text[body_start: text.index("```", body_start)]


def test_c13_the_guides_minimal_plugin_conforms_to_the_contract():
    """The plugin the guide shows (step 6) is run through C9 under the key `dummy`: a guide whose example would be refused by the dock is a broken guide."""
    source = _guide_block("<!-- minimal-plugin -->").replace("foo", "dummy")
    assert "registerMethod(" in source
    assert check_plugin_conformance("dummy", plugin_source=source, answer=_answer_for_dummy(), hostile_answer=_answer_for_dummy(HOSTILE)) == []


def test_c13_the_guide_lists_every_methodspec_field_and_every_contract_member():
    text = GUIDE.read_text(encoding="utf-8")
    fields = [f.name for f in dataclasses.fields(MethodSpec)]
    missing = [name for name in fields if f"`{name}`" not in text]
    assert not missing, f"docs/constanza_methods.md does not describe the MethodSpec fields {missing} (list each as `name` in the MethodSpec table)"
    contract = run_js(js_page("", "none") + "return JSON.stringify({ required: Z.CONTRACT.required, optional: Z.CONTRACT.optional, ctx: Z.CONTRACT.ctx, caps: Object.keys(Z.CONTRACT.caps) });")
    for group in ("required", "optional", "ctx", "caps"):
        gone = [name for name in contract[group] if f"`{name}`" not in text]
        assert not gone, f"docs/constanza_methods.md does not describe the Constanza.CONTRACT {group} members {gone} (list each as `name`)"
    for helper in ("standard_estimate", "validate_standard", "MethodRequest", "Check", "register", "MODULES", "REQUIRE_JS_ENGINE"):
        assert helper in text, f"docs/constanza_methods.md never mentions {helper}"


# ================================================================================================================================ C14 hostile and corrupt saved estimates

def corrupt_variants_for(key: str, snapshot: dict) -> dict[str, dict]:
    """The saved snapshots a card must survive (the Meta fixtures' list, written for any method's block): nothing thrown out of the draw, nothing run."""
    def clone():
        return copy.deepcopy(snapshot)

    def mapped(value, fn):
        if isinstance(value, dict):
            return {k: mapped(v, fn) for k, v in value.items()}
        if isinstance(value, list):
            return [mapped(v, fn) for v in value]
        return fn(value)

    def hostile_strings(value, top=True):
        """Markup in every string except the fields the host reads to pick the plugin (method, v), so the plugin's own card is what draws it."""
        if isinstance(value, dict):
            return {k: (v if k in ("method", "v") else hostile_strings(v, False)) for k, v in value.items()}
        if isinstance(value, list):
            return [hostile_strings(v, False) for v in value]
        return HOSTILE + value if isinstance(value, str) else value

    no_block, bad_notes, no_request, empty_result = clone(), clone(), clone(), clone()
    no_block["result"][key] = None
    bad_notes["result"]["notes"] = "x"
    no_request["request"] = None
    empty_result["result"] = {}
    strings = mapped(clone(), lambda leaf: str(leaf) if isinstance(leaf, (int, float)) and not isinstance(leaf, bool) else leaf)
    strings["v"] = 1
    return {"empty": {}, "only v and method": {"v": 1, "method": key}, "empty result": empty_result, f"{key} block is null": no_block, "notes is a string": bad_notes,
            "request is null": no_request, "numbers are strings": strings, "markup in every string": meta_snapshots.hostile(clone()),
            "markup in every server string": hostile_strings(clone())}


def _snapshot_of(key: str, answer: dict) -> dict:
    fake = FAKES[key]
    return {"v": 1, "method": key, "saved_at": "2026-11-03T12:00:00+00:00", "request": copy.deepcopy(fake.sample_body), "result": answer,
            "line": {"product_name": fake.sample_body["product_name"], "method": key, "months": answer.get("months", 1), "geo_text": "", "target_text": "", "budget": 0, "cpm": None}}


def check_hostile_snapshots(key: str, *, plugin_source: str | None = None, snapshot: dict | None = None, hostile_text: str | None = None) -> list[str]:
    """For a method that saves its estimates (caps.persist): Constanza.renderCard over every corrupt or hostile snapshot never throws, never leaves raw markup
    and never draws a card that does not say it could not be read. A method that does not persist has nothing to check (its answer is never saved)."""
    source = plugin_source if plugin_source is not None else plugin_source_of(key)
    if snapshot is None:
        snapshot = _snapshot_of(key, FAKES[key].answer())
    hostile_text = hostile_text or (FAKES[key].hostile_text if key in FAKES else HOSTILE)
    variants = corrupt_variants_for(key, snapshot)
    body = (
        js_page(source, key)
        + f"var VARIANTS = {json.dumps(variants)};\n"
        "if (!P) return JSON.stringify({ refused: true, errors: __errors });\n"
        "var caps = Object.assign({}, Z.CONTRACT.caps, P.caps || {});\n"
        "if (!caps.persist) return JSON.stringify({ persists: false });\n"
        "var out = { persists: true, drawn: {} };\n"
        "Object.keys(VARIANTS).forEach(function (name) {\n"
        "  var snap = VARIANTS[name], el = fakeEl(), threw = null;\n"
        "  try { Z.renderCard(el, snap.result, { line: { id: 'L1', product_name: 'p', months: 2, budget: 100, cpm: 10, geo_text: '', target_text: '' }, stale: [], fromSnapshot: true, snapshot: snap,\n"
        "    onApply: function () {}, onRefine: function () {}, onRecalc: function () {}, getText: function () { return ''; }, onGeoText: function () {} }); } catch (e) { threw = String((e && e.message) || e); }\n"
        "  out.drawn[name] = { threw: threw, html: el.innerHTML };\n"
        "});\n"
        "return JSON.stringify(out);"
    )
    out = run_js(body)
    if out.get("refused"):
        return [f"constanza-{key}.js was refused by the dock: {' | '.join(out['errors'])}"]
    if not out["persists"]:
        return []
    problems: list[str] = []
    for name, drawn in out["drawn"].items():
        if drawn["threw"]:
            problems.append(f"a saved estimate with {name!r} threw out of the card: {drawn['threw']} - read saved fields defensively (a snapshot is data anyone may have edited); "
                            "the host catches a plugin's throw, but a throw in the HOST's own drawing code is a bug too")
        elif hostile_text in drawn["html"] or re.search(r"<img\b[^>]*onerror", drawn["html"], re.I):
            problems.append(f"a saved estimate with {name!r} drew server text as markup: escape every string with ctx.esc(...)")
        elif not drawn["html"].strip():
            problems.append(f"a saved estimate with {name!r} drew nothing at all: a card that cannot read a saved estimate says so (\"The saved estimate couldn't be read - estimate again.\")")
    return problems


@pytest.mark.parametrize("key", KEYS)
def test_c14_a_corrupt_or_hostile_saved_estimate_never_throws_or_runs(key):
    assert_ok("C14", key, check_hostile_snapshots(key))


PERSISTING_DUMMY = DUMMY_PLUGIN.replace(
    "requestPart:", "caps: { persist: true }, restore: () => {}, choicesChanged: () => false,\n      requestPart:")


def test_twin_c14_a_card_that_writes_a_saved_string_as_markup_is_named():
    answer = _answer_for_dummy()
    snapshot = {"v": 1, "method": "dummy", "saved_at": "2026-11-03T12:00:00+00:00", "request": {"dummy": {}}, "result": answer, "line": {}}
    raw = PERSISTING_DUMMY.replace("const name = (d) => esc((d && d.product_name) || \"\");", "const name = (d) => String((d && d.product_name) || \"\");")
    problems = check_hostile_snapshots("dummy", plugin_source=raw, snapshot=snapshot)
    assert any("'markup in every server string' drew server text as markup" in p for p in problems), problems
    assert check_hostile_snapshots("dummy", plugin_source=PERSISTING_DUMMY, snapshot=snapshot) == []
    assert check_hostile_snapshots("dummy", plugin_source=DUMMY_PLUGIN, snapshot=snapshot) == []                       # not persisting: nothing to check


# ================================================================================================================================ C15 reserved keys, picker groups, refused plugins

def check_reserved_keys() -> list[str]:
    """The registry refuses the two built-in keys (a method that took `platform` would hijack the data path). Whatever it does answer is undone."""
    problems: list[str] = []
    for key in ("platform", "generic"):
        try:
            methods.register(make_dummy_spec(key))
            problems.append(f"constanza_methods.register accepted the built-in key '{key}': refuse it (BUILTIN_METHODS), or a method would take over the data path")
        except ValueError:
            pass
        finally:
            methods.unregister(key)
    return problems


def check_picker_group_and_refusals(key: str, *, plugin_source: str | None = None, constanza_source: str | None = None) -> list[str]:
    """In the JS engine: the plugin's picker group never collides with a data channel, its product is offered, and a plugin whose factory throws or has a typo
    is refused while a good one still registers afterwards (so one bad script never takes the dock down)."""
    source = plugin_source if plugin_source is not None else plugin_source_of(key)
    spec = methods.get(key)
    product = (spec.default_products[0] if spec and spec.default_products else DUMMY_PRODUCT)
    body = (
        js_page(source, key, constanza_source)
        + f"var PRODUCT = {json.dumps(product)};\n"
        "function attempt(k, f) { try { return Z.registerMethod(k, f); } catch (e) { return 'threw: ' + ((e && e.message) || e); } }\n"
        "var out = { registered: __res[KEY] === true, errors: __errors };\n"
        "if (P) {\n"
        "  state.catalog = { families: ['F'], products_by_family: { F: [ { name: PRODUCT, forecast: { enabled: true, method: KEY, series: null } },\n"
        "    { name: 'Ghost product', forecast: { enabled: true, method: 'not_registered_anywhere', series: null } } ] } };\n"
        "  out.offered = Z._pure.loadProducts().map(function (p) { return { name: p.name, channel: p.channel }; });\n"
        "}\n"
        "out.throwing = attempt('boom', function () { throw new Error('the factory threw'); });\n"
        "out.typo = attempt('typo', function () { return { formHtml: function () { return ''; }, bind: function () {}, nonsense: 1 }; });\n"
        "out.bad_key = [attempt('platform', function () { return {}; }), attempt('generic', function () { return {}; }), attempt('Bad Key', function () { return {}; })];\n"
        "out.leftovers = [Z._plugin('boom'), Z._plugin('typo'), Z._plugin('platform')].map(function (p) { return p === null; });\n"
        "out.still_registered = !!Z._plugin(KEY);\n"
        "return JSON.stringify(out);"
    )
    out = run_js(body)
    problems: list[str] = []
    if not out["registered"]:
        return [f"constanza-{key}.js was refused by the dock: {' | '.join(out['errors'])}"]
    names = {p["name"]: p["channel"] for p in out["offered"]}
    if product not in names:
        problems.append(f"the dock does not offer the method's product {product!r} once its catalog entry says forecast.method = {key!r}: loadProducts() lists a product when its method is registered")
    else:
        channel = names[product]
        if channel in DATA_CHANNELS or not channel.startswith("m:"):
            problems.append(f"the picker group of {key!r} is {channel!r}: a plugin's group must be 'm:<channel>' so it can never collide with a data channel ({', '.join(DATA_CHANNELS[:-1])})")
    if "Ghost product" in names:
        problems.append("a product whose method is not registered was offered in the dock: it must be left out (and say so on Step 05), never fall through to the data path")
    if out["throwing"] is not False or out["typo"] is not False or any(r is not False for r in out["bad_key"]):
        problems.append("registerMethod must return false (and never throw) for a throwing factory, a plugin with an unknown member, and the keys platform / generic / one that is not a "
                        f"lower-case word (got {out['throwing']}, {out['typo']}, {out['bad_key']})")
    if not all(out["leftovers"]):
        problems.append("a refused plugin left something registered: registerMethod must add nothing when it returns false")
    if not out["still_registered"]:
        problems.append("registering refused plugins unregistered a good one")
    return problems


def test_c15_the_registry_refuses_the_built_in_keys():
    assert_ok("C15", "*", check_reserved_keys())


@pytest.mark.parametrize("key", KEYS)
def test_c15_a_plugins_picker_group_never_collides_and_a_bad_plugin_is_refused_without_harm(key):
    assert_ok("C15", key, check_picker_group_and_refusals(key))


def test_twin_c15_a_registry_that_takes_a_built_in_key_is_named_and_undone():
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(methods, "BUILTIN_METHODS", ())
        problems = check_reserved_keys()
    assert len(problems) == 2 and "accepted the built-in key 'platform'" in problems[0] and "BUILTIN_METHODS" in problems[0]
    assert methods.get("platform") is None and methods.get("generic") is None                            # (the check took its registrations out again)


def test_twin_c15_a_host_that_does_not_prefix_a_plugins_group_a_throwing_host_and_a_missing_product_are_named():
    host = (STATIC / "constanza.js").read_text(encoding="utf-8")
    unprefixed = host.replace('const groupOf = (plug) => "m:" + (plug.channel || keyOf(plug));', "const groupOf = (plug) => (plug.channel || keyOf(plug));")
    assert unprefixed != host, "constanza.js no longer defines groupOf the way this twin edits it: update the twin"
    plugin = DUMMY_PLUGIN.replace("channelLabel:", 'channel: "display", channelLabel:')
    text = "\n".join(check_picker_group_and_refusals("dummy", plugin_source=plugin, constanza_source=unprefixed))
    assert "the picker group of 'dummy' is 'display': a plugin's group must be 'm:<channel>'" in text
    assert check_picker_group_and_refusals("dummy", plugin_source=plugin) == []                         # the real host prefixes it: 'm:display' cannot collide
    unguarded = host.replace("try { plug = factory(pluginCtx()); } catch (e) { return fail(`the factory threw: ${(e && e.message) || e}`); }", "plug = factory(pluginCtx());")
    assert unguarded != host, "constanza.js no longer guards the factory the way this twin edits it: update the twin"
    text = "\n".join(check_picker_group_and_refusals("dummy", plugin_source=DUMMY_PLUGIN, constanza_source=unguarded))
    assert "registerMethod must return false (and never throw)" in text and "threw: the factory threw" in text
    blind = host.replace("if (!isDataMethod(p.forecast.method) && !plugins[p.forecast.method]) continue;", "")
    assert blind != host, "constanza.js no longer skips unregistered methods the way this twin edits it: update the twin"
    assert "a product whose method is not registered was offered" in "\n".join(check_picker_group_and_refusals("dummy", plugin_source=DUMMY_PLUGIN, constanza_source=blind))


# ================================================================================================================================ the same contract in a real browser

# Two generic flows for EVERY registered method, in headless Chromium (skipped when Playwright or Chromium is missing): the dock offers the method's product in its own picker
# group with its own panel and button; and the Step 05 block of a line of that product does what its capabilities say (a method that pulls from the proposal's geography
# estimates, shows the card and Inserts; one that is asked in the dock has the one "Estimate in Constanza" button). The page is the Step 05 harness of
# tests/test_constanza_step05_block_dom.py (the real app.js section, constanza.js, every registered plugin's script and css), /api answered by the real route functions over the
# method's own strict fake. What needs a method's own settings (Meta's place) is in that method's DOM tests.

def _step05():
    pytest.importorskip("playwright.sync_api")
    return importlib.import_module("tests.test_constanza_step05_block_dom")


@pytest.fixture(scope="module")
def browser_server():
    step05 = _step05()
    import threading
    httpd = step05._Server(("127.0.0.1", 0), step05._Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


@pytest.fixture(scope="module")
def chromium():
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as pw:
        try:
            b = pw.chromium.launch()
        except Exception as exc:                                                       # noqa: BLE001 - no browser installed here
            pytest.skip(f"Chromium is not available: {exc}")
        yield b
        b.close()


def _page_for(step05, monkeypatch, key: str):
    """The Step 05 harness with every registered method's script and css in it, and the key's sample product as a product of that method."""
    extra_links = "".join(f'<link rel="stylesheet" href="/static/constanza-{k}.css">' for k in methods.keys()
                          if (STATIC / f"constanza-{k}.css").exists() and f"constanza-{k}.css" not in step05.HARNESS)
    extra_scripts = "".join(f'<script src="/static/constanza-{k}.js"></script>' for k in methods.keys() if f"constanza-{k}.js" not in step05.HARNESS)
    html = step05.HARNESS.replace("</head>", extra_links + "</head>", 1).replace("</body>", extra_scripts + "</body>", 1)
    monkeypatch.setattr(step05, "HARNESS", html)
    product = FAKES[key].sample_body["product_name"]
    monkeypatch.setitem(step05.PRODUCTS, product, step05._product("Social", key, cpm=10, forecast={"info": {}}))
    return product


def _open(chromium, url, step05, line_product=None):
    world = step05.World()
    context, page = step05.open_page(chromium, url, world)
    step05.setup(page, lines=[{"id": "L1", "product_name": line_product}] if line_product is not None else [], avails={})       # (it also gives the page its product catalog)
    return world, context, page


@pytest.mark.parametrize("key", KEYS)
def test_browser_the_dock_offers_the_methods_product_in_its_own_group_with_its_own_panel(chromium, browser_server, key):
    step05 = _step05()
    with pytest.MonkeyPatch.context() as mp:
        FAKES[key].install(mp)
        product = _page_for(step05, mp, key)
        world, context, page = _open(chromium, browser_server, step05)
        try:
            plug_caps = page.evaluate(f"Object.assign({{}}, Constanza.CONTRACT.caps, (Constanza._plugin({json.dumps(key)}) || {{}}).caps || {{}})")
            group = page.evaluate(f"(Constanza._plugin({json.dumps(key)}) || {{}}).channelLabel || {json.dumps(key)}")
            go = page.evaluate(f"(Constanza._plugin({json.dumps(key)}) || {{}}).goLabel || 'Pull avails →'")
            page.evaluate("product => Constanza.open({ product, geo: 'Los Angeles', months: 2 })", product)
            page.wait_for_selector("#constanza-dock:not(.hidden)")
            page.wait_for_function(f"() => document.getElementById('cz-pname').textContent.includes({json.dumps(product)})")
            assert page.evaluate("Constanza._state.product") == product, f"the dock did not select {product!r}: is the method's script loaded and registered?"
            assert page.locator(f'.cz-method-panel[data-method="{key}"]:not(.hidden)').count() == 1, f"the dock did not show the panel of '{key}'"
            assert page.evaluate("() => document.getElementById('cz-go').textContent.trim()") == go.strip()
            assert page.evaluate("() => document.getElementById('cz-sec-where').classList.contains('hidden')") is bool(plug_caps["ownsGeography"])
            page.click("#cz-product-btn")
            assert group.lower() in page.inner_text("#cz-picker-list").lower(), f"the picker has no group headed {group!r}"      # (headings are drawn in capitals)
            assert page.locator("#cz-picker-list").inner_text().count(product) >= 1
            page.keyboard.press("Escape")
            # the pull: a method that can be asked with the form as it stands (no place to pick, no setting to choose) answers into the result card; one that needs the planner's
            # settings first says so in words (its own DOM tests drive it with settings)
            ready = page.evaluate(f"(() => {{ const p = Constanza._plugin({json.dumps(key)}); const c = p && p.canPull ? p.canPull() : {{ ok: true }}; const g = p && p.goState ? p.goState() : {{ enabled: true }}; return !!(c && c.ok) && g.enabled !== false; }})()")
            page.click("#cz-go")
            page.wait_for_selector("#cz-result .cz-headline, #cz-result .cz-error", timeout=15000)
            if ready:
                assert page.locator("#cz-result .cz-error").count() == 0, page.inner_text("#cz-result")
                assert product in page.inner_text("#cz-result .cz-card-head") and page.locator("#cz-result .cz-headline").count() == 1
                assert page.evaluate("typeof window.__pwned") == "undefined" and page.evaluate("Constanza._state.result.method") == key
            else:
                assert page.inner_text("#cz-result .cz-error").strip(), "a pull the method cannot make yet must say why in the result card"
        finally:
            context.close()
            if key == "tiktok":
                from app.services import tiktok_geo
                tiktok_geo.forget_everything()
    assert [e for e in page.errors if "[constanza]" not in e] == [], page.errors


@pytest.mark.parametrize("key", KEYS)
def test_browser_the_step_05_block_of_a_line_does_what_the_methods_capabilities_say(chromium, browser_server, key):
    step05 = _step05()
    with pytest.MonkeyPatch.context() as mp:
        FAKES[key].install(mp)
        product = _page_for(step05, mp, key)
        world, context, page = _open(chromium, browser_server, step05, line_product=product)
        try:
            caps = page.evaluate(f"Object.assign({{}}, Constanza.CONTRACT.caps, (Constanza._plugin({json.dumps(key)}) || {{}}).caps || {{}})")
            assert page.locator('.constanza-block[data-lid="L1"]').get_attribute("data-method") == key
            buttons = step05.buttons(page)
            if caps["autoPull"] == "geo":
                assert buttons[:2] == ["Auto-calculate avails", "Open in Constanza"], buttons
                page.click(".constanza-autocalc-btn")
                page.wait_for_selector("#constanza-result-L1 .constanza-range-table, #constanza-result-L1 .constanza-error", timeout=15000)
                assert page.locator("#constanza-result-L1 .constanza-error").count() == 0, page.inner_text("#constanza-result-L1")
                sent = world.estimates[-1]
                assert sent["product_name"] == product and sent["geo_text"] == "Los Angeles" and sent["months"] == 2
                page.click("#constanza-result-L1 .cz-apply")
                entry = page.evaluate("state.availsData.L1")
                assert entry["max_imps"] > 0 and entry["max_imps_estimated"] is True and entry["basis"] == "imps" and isinstance(entry["geo_resolution"], dict)
                assert page.evaluate("typeof window.__pwned") == "undefined"
            else:
                assert buttons == ["Estimate in Constanza →"], buttons
        finally:
            context.close()
            if key == "tiktok":
                from app.services import tiktok_geo
                tiktok_geo.forget_everything()
    assert [e for e in page.errors if "[constanza]" not in e] == [], page.errors


@pytest.mark.parametrize("key", KEYS)
def test_browser_a_corrupt_or_hostile_saved_estimate_of_any_method_takes_no_other_line_down(chromium, browser_server, key):
    """C14 on a real Step 05: a line whose saved estimate is corrupt (every variant of corrupt_variants_for, markup in every string included) never stops the other lines being
    drawn, shows the card or the plain "couldn't be read" sentence, and runs nothing - whichever method the snapshot claims to be (a hand-edited file may claim any)."""
    step05 = _step05()
    with pytest.MonkeyPatch.context() as mp:
        FAKES[key].install(mp)
        product = _page_for(step05, mp, key)
        snapshot = _snapshot_of(key, FAKES[key].answer())
        world, context, page = _open(chromium, browser_server, step05)
        page.allowed = ["[constanza]"]                                                    # the page says once, in the console, that it could not read one
        try:
            for name, broken in corrupt_variants_for(key, snapshot).items():
                step05.setup(page, lines=[{"id": "L1", "product_name": step05.ROKU}, {"id": "L2", "product_name": product}, {"id": "L3", "product_name": product}],
                             avails={"L2": {"forecast_snapshot": broken}})
                for lid in ("L1", "L2", "L3"):
                    assert page.locator(f'.constanza-block[data-lid="{lid}"]').count() == 1, f"{name}: the block of line {lid} was not drawn"
                assert page.locator('.constanza-block[data-lid="L1"] button').count() >= 1 and page.locator('.constanza-block[data-lid="L3"] button').count() >= 1, name
                page.wait_for_timeout(60)                                                     # (a broken image would report its error a moment later)
                assert page.evaluate("document.querySelectorAll('#avails-grid img[onerror]').length") == 0, f"{name}: saved text became markup"
                assert page.evaluate("typeof window.__pwned") == "undefined", f"{name}: saved text ran as script"
                if page.is_visible("#constanza-result-L2"):
                    assert page.inner_text("#constanza-result-L2").strip(), f"{name}: the line's card is on screen and empty"
        finally:
            context.close()
            if key == "tiktok":
                from app.services import tiktok_geo
                tiktok_geo.forget_everything()
    unexpected = [e for e in page.errors if not any(allowed in e for allowed in page.allowed)]
    assert unexpected == [], unexpected


# ================================================================================================================================ the contract tests keep their own promises

def test_every_check_has_a_twin():
    """A check that could not fail enforces nothing: each `check_*` function above has a `test_twin_<check>` that calls it on something broken."""
    here = Path(__file__).read_text(encoding="utf-8")
    twins = " ".join(re.findall(r"^def (test_twin_\w+)", here, re.M))
    prefixes = {"check_import_order": "test_twin_c0", "check_registration": "test_twin_c1", "check_no_secret_leak": "test_twin_c2", "check_modules_listed": "test_twin_c3",
                "check_request_model": "test_twin_c4", "check_routes": "test_twin_c5", "check_fake": "test_twin_c6", "check_standard_estimate": "test_twin_c7",
                "check_plugin_files": "test_twin_c8", "check_no_orphan_files": "test_twin_c8", "check_plugin_conformance": "test_twin_c9", "check_app_js": "test_twin_c10", "check_page_literals": "test_twin_c10",
                "check_admin": "test_twin_c11", "check_database_and_entry": "test_twin_c12", "check_docs": "test_twin_c13", "check_hostile_snapshots": "test_twin_c14",
                "check_reserved_keys": "test_twin_c15", "check_picker_group_and_refusals": "test_twin_c15"}
    defined = set(re.findall(r"^def (check_\w+)\(", here, re.M))
    assert defined == set(prefixes), f"a check was added or removed without a twin: {sorted(defined ^ set(prefixes))} (add test_twin_<id>_... and list it here)"
    for check, twin in prefixes.items():
        assert twin in twins, f"{check} has no negative twin ({twin}_...)"
        body_calls = re.findall(rf"{check}\(", here)
        assert len(body_calls) >= 3, f"{check} must be called by its real test and by its twin"


def test_the_twins_left_nothing_behind_in_the_registry_or_the_fakes():
    """(The autouse fixture asserts it after every test; this one names the property for the reader and checks the forecast map too.)"""
    assert methods.keys() == list(KEYS) and sorted(FAKES) == sorted(KEYS)
    assert tuple(fm.METHODS) == ("platform", "generic", *KEYS)
    assert fm.DEFAULT_API_PRODUCTS == methods.default_products()
    assert "dummy" not in methods.default_products().values() and methods.get("dummy") is None
