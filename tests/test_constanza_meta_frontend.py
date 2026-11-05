"""The Meta plugin of the Constanza dock (app/static/constanza-meta.js): what it asks Meta for, the words it uses, the choices it keeps per Step 05 line,
how a saved estimate (new shape or old) reads, and that the page loads it. The pure parts run in a JavaScript engine (Node when installed, else macOS's
JavaScriptCore) against a stub page, like test_constanza_tiktok_frontend.py: the real constanza.js (the dock that hosts the plugin and checks it against
its contract) plus the plugin, whose factory is handed a ctx of this file's making. The plugin working in a real browser (typeaheads, the browse window,
focus, the request the chips describe) is tests/test_constanza_meta_dom.py; the Step 05 card around it is tests/test_constanza_step05_block_dom.py.

Set REQUIRE_JS_ENGINE=1 (CI does) to make a missing engine a failure instead of a skip: a skipped test enforces nothing."""
from __future__ import annotations

import importlib.util
import json
import os
import re
from pathlib import Path

import pytest

from tests.fixtures import meta_snapshots as fx

ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "app" / "templates" / "index.html").read_text(encoding="utf-8")
APP_JS = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
CZ_JS = (ROOT / "app" / "static" / "constanza.js").read_text(encoding="utf-8")
MT_JS = (ROOT / "app" / "static" / "constanza-meta.js").read_text(encoding="utf-8")
MT_CSS = (ROOT / "app" / "static" / "constanza-meta.css").read_text(encoding="utf-8")
MT_CODE = re.sub(r"/\*[\s\S]*?\*/|//[^\n]*", "", MT_JS)                       # the plugin without its comments (the comments name routes and files on purpose)


def _harness():
    spec = importlib.util.spec_from_file_location("_ff_frontend_harness_meta", ROOT / "tests" / "test_full_flight_frontend.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


H = _harness()
OUTSIDE = ("escapeHtml", "_replaceGeoItem", "_constanzaErrorText")             # the few helpers constanza.js and the plugin take from app.js

# Meta's own lists give these; the numbers they stand for are the fixtures'.
LINE = fx.LINE_FACTS


def run(snippet: str, *, line=LINE, snaps=None, geo="Los Angeles", hispanic=False):
    """Loads constanza.js and constanza-meta.js against a stub page (the plugin REGISTERS with the real dock) and runs `snippet`, which returns a JSON string.
    In the snippet: `M` is a second instance of the plugin built by its factory from a ctx of ours (`ctx`, a fake dock: nothing is on screen, `api` records
    its calls in `calls` and never answers), `X` its `_pure` members, `Z` the real `window.Constanza`; `LINE` is the Step 05 line the dock is open for
    (null: the dock on its own), `SNAPS` the saved estimates by line id."""
    fns = "\n".join(H._extract_fn(APP_JS, n) for n in OUTSIDE)
    body = (
        "var __errors = [];\n"
        "var console = { warn: function () {}, log: function () {}, error: function (m) { __errors.push(String(m)); } };\n"
        "var window = {};\n"
        "var document = { readyState: 'loading', addEventListener: function () {}, getElementById: function () { return null; },"
        " querySelectorAll: function () { return []; } };\n"
        "var state = { catalog: null };\n"
        + fns + "\n" + CZ_JS + "\n"
        "var factory = null, __register = window.Constanza.registerMethod;\n"
        "window.Constanza.registerMethod = function (name, f) { if (name === 'meta') factory = f; return __register(name, f); };\n"
        + MT_JS + "\n"
        f"var LINE = {json.dumps(line)}, SNAPS = {json.dumps(snaps or {})}, GEO = {json.dumps(geo)}, HISPANIC = {json.dumps(hispanic)};\n"
        "var calls = [], stales = 0, announced = [], refreshed = 0, Z = window.Constanza, els = {};\n"
        "function fakeEl() { return { className: '', dataset: {}, innerHTML: '', textContent: '', hidden: false, querySelectorAll: function () { return []; } }; }\n"
        "var ctx = { S: { open: true, lineId: LINE ? LINE.id : null }, $: function (id) { return els[id] || null; }, esc: escapeHtml,\n"
        "  num: function (n) { return String(n); }, compact: function (n) { return String(n); }, markStale: function () { stales++; },\n"
        "  api: function (path, body) { calls.push([path, body === undefined ? null : body]); return new Promise(function () {}); },\n"
        "  errorText: _constanzaErrorText, setMood: function () {}, announce: function (t) { announced.push(t); }, summarize: function () { return {}; },\n"
        "  productByName: function () { return null; }, requestBody: function () { return {}; }, targetsHispanic: function () { return HISPANIC; },\n"
        "  line: function () { return LINE; }, proposalGeo: function () { return GEO; }, snapshot: function (lid) { return SNAPS[lid] || null; }, refreshGo: function () { refreshed++; } };\n"
        "var M = factory(ctx), X = M._pure;\n"
        + snippet
    )
    try:
        return json.loads(H._run_js(body))
    except AssertionError as exc:
        if "JS engine failed" in str(exc) and "osascript" in str(exc).lower():
            if os.environ.get("REQUIRE_JS_ENGINE") == "1":
                raise
            pytest.skip(f"JavaScript engine unusable here: {exc}")
        raise


def J(value) -> str:
    return json.dumps(value)


ESTIMATE = fx.ESTIMATE


def card(data=None, **kw):
    """The Step 05 card's method part for an answer."""
    return run(f"return JSON.stringify(M.cardHtml({J(ESTIMATE if data is None else data)}, null, {{}}));", **kw)


# ----------------------------------------------------------------------------------------------------------------- the page

def test_the_page_loads_the_meta_plugin_after_the_dock_the_tiktok_plugin_and_the_pop_up_it_uses():
    assert INDEX.index("/static/constanza.js") < INDEX.index("/static/constanza-tiktok.js") < INDEX.index("/static/constanza-meta.js")
    assert INDEX.index("/static/segment-browser.js") < INDEX.index("/static/constanza-meta.js")             # the pop-up exists before the plugin can open it
    assert INDEX.count("/static/constanza-meta.js") == 1


def test_the_page_loads_the_plugins_own_styles_after_the_kit_and_the_pop_up_they_refine_and_the_old_file_is_gone():
    css = [INDEX.index(f'href="/static/{name}.css"') for name in ("constanza", "audience-kit", "segment-browser", "constanza-meta")]
    assert css == sorted(css)
    assert "meta-audience.css" not in INDEX and not (ROOT / "app" / "static" / "meta-audience.css").exists()
    for cls in (".mt-panel", ".mt-scen", ".mt-sc.mt-mid", ".mt-headline", ".mt-check", ".mt-more"):
        assert cls in MT_CSS, cls
    assert ".mt-panel [hidden]" in MT_CSS and "display: none !important" in MT_CSS                         # [hidden] must beat the kit's flex displays
    assert "prefers-reduced-motion" in MT_CSS and "forced-colors" in MT_CSS


def test_the_plugin_only_talks_to_routes_under_its_own_key_and_the_shared_estimate_and_the_server_has_them():
    import app.main as main

    code = MT_CODE
    routes = set(re.findall(r"/api/constanza/[A-Za-z0-9_/-]+", code))
    assert routes == {"/api/constanza/meta/status", "/api/constanza/meta-geos", "/api/constanza/meta-targeting", "/api/constanza/meta-languages"}, routes
    assert all(re.match(r"/api/constanza/meta[/-]", r) for r in routes)                                 # the rule for any method's file: its own key, then / or -
    assert "/api/constanza/estimate" not in code                                                       # the dock sends the estimate; the plugin only writes its block
    assert "meta-forecast" not in code                                                                # the old route (kept one release for tabs opened before the deploy)
    paths = {r.path for r in main.app.routes}
    assert routes <= paths and "/api/constanza/estimate" in paths
    for fragment in ("meta-targeting?kind=", "meta-targeting?q=", "meta-languages?q="):
        assert fragment in code, fragment


def test_the_plugin_reaches_the_server_only_through_the_docks_api_and_loads_no_script_and_holds_no_secret():
    code = MT_CODE
    assert not re.search(r"\bfetch\s*\(", code) and "XMLHttpRequest" not in code and "sendBeacon" not in code           # ctx.api is the one way out
    assert not re.search(r"https?://", code)                                                                        # no URL at all, so no script or request to elsewhere
    assert not re.search(r"EAA[A-Za-z0-9]{10,}", code) and "META_ACCESS_TOKEN" not in code and "META_APP_SECRET" not in code      # no token, no secret's name
    assert "document.write" not in code and "eval(" not in code and "new Function" not in code
    assert "localStorage" not in code and "sessionStorage" not in code                                              # what the planner chose lives in the page's memory only


def test_the_plugin_uses_no_page_global_but_the_dock_the_pop_up_and_the_document():
    code = MT_CODE
    assert set(re.findall(r"window\.([A-Za-z_]+)", code)) == {"Constanza", "SegmentBrowser"}
    for page_global in ("applyConstanzaEstimate", "_effective", "_constanza", "_meta", "renderAvailsGrid", "constanzaHost"):
        assert page_global not in code, page_global                                                               # the page is reached through ctx only
    assert not re.search(r"(?<![\w.])state\.", code)                                                              # nor the page's own `state`


def test_every_class_the_plugin_draws_is_the_kits_the_docks_or_its_own_mt_namespace():
    drawn = run(f"""return JSON.stringify(M.whereHtml() + M.formHtml() + M.headlineHtml({J(ESTIMATE)}, {{}}) + M.bodyHtml({J(ESTIMATE)}, {{}}, null) + M.aboutHtml({J(ESTIMATE)})
        + M.cardHtml({J(ESTIMATE)}) + M.cardEmptyHtml({J(LINE)}, null) + M.badgeHtml({J(ESTIMATE)}));""", line=None)
    tokens = {t for value in re.findall(r'class="([^"]*)"', drawn) for t in value.split()}
    foreign = {t for t in tokens if not t.startswith(("aud-", "cz-", "mt-"))}
    assert foreign <= {"main"}, foreign          # (the dock's own "main" metric)                                           # no class of the old panel (meta-*) or of another plugin (tt-*)
    assert any(t.startswith("mt-") for t in tokens)


def test_the_plugin_registers_with_the_real_dock_which_checks_it_against_its_contract():
    out = run("""
        var P = Z._plugin('meta'), C = Z.CONTRACT, known = C.required.concat(C.optional);
        var again = __register('meta', factory);
        return JSON.stringify({ registered: !!P, errors: __errors, again: again, unknown: Object.keys(P).filter(function (k) { return known.indexOf(k) < 0; }),
          missing: C.required.filter(function (k) { return !(k in P); }), caps: P.caps, rangeLabels: P.rangeLabels, go: P.goLabel, group: P.channelLabel, source: P.sourceLabel,
          legacy: P.legacy.key });""")
    assert out["registered"] and out["errors"] == [] and out["again"] is True
    assert out["unknown"] == [] and out["missing"] == []
    assert out["caps"] == {"ownsGeography": True, "persist": True, "autoPull": "saved", "lineMonths": True}
    assert out["rangeLabels"] == ["Conservative", "Mid · default", "Upper"] and out["go"] == "Estimate audience →"
    assert out["group"] == "Meta · live audience estimate" and out["source"] == "Live from Meta" and out["legacy"] == "meta_forecast"


def test_the_plugin_gives_the_dock_every_member_each_of_its_capabilities_needs():
    out = run("""return JSON.stringify(['whereHtml', 'restore', 'choicesChanged', 'cardRequest', 'canAutoPull', 'onLine', 'goState', 'connection', 'cardEmptyHtml', 'applyLabel',
        'staleReasons', 'snapshotLine', 'badgeHtml', 'geoInfo', 'errorText', 'canPull', 'onSelect', 'onResult', 'onError', 'onProposalReset'].filter(function (k) { return typeof M[k] !== 'function'; }));""")
    assert out == []


def test_nothing_is_asked_of_meta_when_the_panel_is_drawn_or_bound_only_when_the_product_is_chosen_or_a_line_asks():
    out = run("""
        M.formHtml(); M.whereHtml(); M.bind(ctx); M.onLine(null); M.onLine({ id: 'L1' }); M.reset(null); M.restore({}); M.cardEmptyHtml(LINE, null);
        var drawn = calls.length;
        M.onSelect({ name: 'P' });
        var afterSelect = calls.slice();
        M.connection(); M.connection();                               // the answer is on its way: asked once, not twice
        return JSON.stringify({ drawn: drawn, afterSelect: afterSelect, total: calls.length });""")
    assert out["drawn"] == 0
    assert out["afterSelect"] == [["/api/constanza/meta/status", None]] and out["total"] == 1


def test_a_connection_asked_by_a_step_05_line_before_any_select_is_asked_once_and_answers_unknown():
    out = run("var a = M.connection(); var b = M.connection(); return JSON.stringify({ a: a, b: b, calls: calls });")
    assert out == {"a": None, "b": None, "calls": [["/api/constanza/meta/status", None]]}


# ----------------------------------------------------------------------------------------------------------------- the request

EMPTY_BLOCK = {"geo": None, "age_min": None, "age_max": None, "gender": "all", "languages": [], "targeting": []}


def test_an_untouched_dock_on_its_own_sends_no_place_and_nothing_narrowed_and_no_budget():
    assert run("return JSON.stringify(M.requestPart());", line=None) == {"meta": EMPTY_BLOCK}


def test_the_request_is_the_saved_choices_with_the_line_budget_and_cpm_and_only_the_metas_own_key():
    out = run(f"M.onLine({{ id: 'L1' }}); return JSON.stringify(M.requestPart());", snaps={"L1": fx.native()})
    assert list(out) == ["meta"]
    assert out["meta"] == {
        "geo": {"type": "geo_market", "key": "803", "label": "Los Angeles · California · US"}, "age_min": 25, "age_max": 44, "gender": "female",
        "languages": [{"id": "1001", "name": "Spanish"}, {"id": "1002", "name": "Spanish (Mexico)"}],
        "targeting": [{"id": "6003107902433", "name": "Soccer", "type": "interests"}], "retail_budget": 2000, "retail_cpm": 12}
    assert "audience_lower" not in out["meta"] and "meta_geo_type" not in out["meta"]


def test_age_is_sent_only_when_it_narrows_and_the_label_fits_the_servers_limit():
    out = run("""M.onLine({ id: 'L1' }); var s = X.sel(); s.geo = { type: 'geo_market', key: '803', name: 'LA', region: '', country: '', label: 'x'.repeat(300) };
                 s.ageMin = 18; s.ageMax = 65; s.gender = 'all'; var all = M.requestPart().meta;
                 s.ageMin = 65; s.ageMax = 65; var old = M.requestPart().meta;
                 s.ageMin = 18; s.ageMax = 30; s.geo.label = ''; var young = M.requestPart().meta;
                 return JSON.stringify({ all: all, old: old, young: young });""", snaps={"L1": fx.native()})
    assert out["all"]["age_min"] is None and out["all"]["age_max"] is None and len(out["all"]["geo"]["label"]) == 160
    assert (out["old"]["age_min"], out["old"]["age_max"]) == (65, None)
    assert (out["young"]["age_min"], out["young"]["age_max"], out["young"]["geo"]["label"]) == (None, 30, "LA")


def test_the_ids_in_the_request_do_not_depend_on_the_order_they_were_clicked_in():
    out = run("""var s = X.sel(); s.languages = [{ id: '1002', name: 'Spanish (Mexico)' }, { id: '1001', name: 'Spanish' }];
                 s.detailed = [{ id: '9', name: 'Z', type: 'interests' }, { id: '3', name: 'A', type: 'behaviors' }, { id: '2', name: 'B', type: 'interests' }];
                 var one = M.requestPart().meta;
                 s.languages.reverse(); s.detailed.reverse();
                 return JSON.stringify({ one: one, two: M.requestPart().meta });""", line=None)
    assert out["one"] == out["two"]
    assert [x["id"] for x in out["one"]["languages"]] == ["1001", "1002"]
    assert [f'{x["type"]}:{x["id"]}' for x in out["one"]["targeting"]] == ["behaviors:3", "interests:2", "interests:9"]


def test_a_line_sends_the_whole_flights_budget_and_its_cpm_and_a_line_without_either_sends_neither():
    out = run("""M.onLine({ id: 'L1' }); LINE = { id: 'L1', months: 4, budget: 6000, cpm: 12.5 }; var a = M.requestPart().meta;
                 LINE = { id: 'L1', months: 4, budget: 0, cpm: null }; var b = M.requestPart().meta;
                 LINE = { id: 'L1', months: 4, budget: 1500, cpm: 0 }; var c = M.requestPart().meta;
                 return JSON.stringify({ a: a, b: b, c: c });""")
    assert (out["a"]["retail_budget"], out["a"]["retail_cpm"]) == (6000, 12.5)
    assert "retail_budget" not in out["b"] and "retail_cpm" not in out["b"]
    assert out["c"]["retail_budget"] == 1500 and "retail_cpm" not in out["c"]                      # the server then uses the product's own planning CPM


def test_on_its_own_a_typed_budget_is_sent_and_the_cpm_is_never_guessed():
    out = run("""var w = X.ws(); w.budget = '$33,862.50'; var a = M.requestPart().meta; w.budget = 'abc'; var b = M.requestPart().meta; w.budget = '0'; var c = M.requestPart().meta;
                 return JSON.stringify({ a: a, b: b, c: c });""", line=None)
    assert out["a"]["retail_budget"] == 33862.5 and "retail_cpm" not in out["a"]
    assert "retail_budget" not in out["b"] and "retail_budget" not in out["c"]


def test_recalculate_sends_the_workspace_when_the_line_has_one_else_the_saved_choices_with_the_lines_current_facts():
    out = run("""var snap = SNAPS.L1, line = { id: 'L1', months: 3, budget: 3000, cpm: 18 };
                 var fromSaved = M.cardRequest('L1', line, snap);                    // no workspace yet: the saved estimate's choices
                 M.onLine({ id: 'L1' }); X.sel().gender = 'male'; X.sel().ageMin = 30;
                 var fromWorkspace = M.cardRequest('L1', line, snap);                // unpulled edits are sent
                 return JSON.stringify({ fromSaved: fromSaved, fromWorkspace: fromWorkspace, other: M.cardRequest('L2', line, snap) });""", snaps={"L1": fx.native()})
    saved = out["fromSaved"]
    assert (saved["gender"], saved["age_min"], saved["age_max"]) == ("female", 25, 44) and (saved["retail_budget"], saved["retail_cpm"]) == (3000, 18)
    edited = out["fromWorkspace"]
    assert (edited["gender"], edited["age_min"], edited["age_max"]) == ("male", 30, 44) and (edited["retail_budget"], edited["retail_cpm"]) == (3000, 18)
    assert out["other"]["gender"] == "female"                                                       # a line with no workspace of its own: the snapshot's choices


def test_recalculate_is_offered_only_when_there_are_choices_to_repeat_and_a_place_to_repeat_them_for():
    out = run("""var line = { id: 'L1' }, snap = SNAPS.L1;
                 var withSnap = M.canAutoPull(line, snap), manual = M.canAutoPull(line, { request: null }), nothing = M.canAutoPull(line, null);
                 M.onLine({ id: 'L1' }); X.sel().geo = null; var cleared = M.canAutoPull(line, snap);
                 return JSON.stringify({ withSnap: withSnap, manual: manual, nothing: nothing, cleared: cleared });""", snaps={"L1": fx.native()})
    assert out["withSnap"] == {"ok": True}
    assert out["manual"]["ok"] is False and "choose the audience in Constanza first" in out["manual"]["reason"]
    assert out["nothing"]["ok"] is False and out["cleared"]["ok"] is False and "place was cleared" in out["cleared"]["reason"]


def test_a_pull_without_a_place_is_refused_in_words_and_with_one_it_goes():
    out = run("""var a = M.canPull(); X.sel().geo = { type: 'city', key: '1', name: 'Phoenix', region: '', country: '', label: 'Phoenix' }; return JSON.stringify({ a: a, b: M.canPull() });""", line=None)
    assert out["a"] == {"ok": False, "reason": "Choose where first: search for a place and pick one from the list."} and out["b"] == {"ok": True}


def test_the_context_kept_with_a_result_says_what_was_asked_and_names_the_place():
    out = run("""M.onLine({ id: 'L1' }); var c = M.context(); X.sel().geo = null; return JSON.stringify({ c: c, none: M.context() });""", snaps={"L1": fx.native()})
    assert out["c"]["geo"] == "Los Angeles · California · US"
    assert out["c"]["summary"] == ["Los Angeles (TV market)", "Women", "Ages 25–44", "Language set to Spanish", "Any of: Soccer"]
    assert out["none"]["geo"] == ""


# ----------------------------------------------------------------------------------------------------------------- what is chosen, per line

def test_a_saved_request_restores_geography_age_gender_languages_and_interests():
    sel = run("M.onLine({ id: 'L1' }); return JSON.stringify(X.sel());", snaps={"L1": fx.native()})
    assert sel["geoType"] == "geo_market" and sel["geo"] == {"type": "geo_market", "key": "803", "name": "Los Angeles", "region": "California", "country": "US",
                                                             "label": "Los Angeles · California · US"}
    assert (sel["ageMin"], sel["ageMax"], sel["gender"]) == (25, 44, "female")
    assert sel["languages"] == [{"id": "1001", "name": "Spanish"}, {"id": "1002", "name": "Spanish (Mexico)"}]
    assert sel["detailed"] == [{"id": "6003107902433", "name": "Soccer", "type": "interests"}]


def manual_snapshot():
    """A manual old estimate as the page would hand it to the dock (adopted in memory: no request to restore)."""
    return run(f"return JSON.stringify(M.legacy.adopt({J(fx.MANUAL)}, {J(LINE)}));")


def test_with_no_saved_estimate_or_a_manual_one_the_choices_are_everyone_and_no_place_yet():
    nothing = {"geoType": "geo_market", "geo": None, "ageMin": 18, "ageMax": 65, "gender": "all", "languages": [], "detailed": []}
    assert run("M.onLine({ id: 'L1' }); return JSON.stringify(X.sel());") == nothing
    manual = run("M.onLine({ id: 'L1' }); return JSON.stringify(X.sel());", snaps={"L1": manual_snapshot()})
    assert manual == nothing and run("return JSON.stringify(X.sel());", line=None) == nothing


def test_a_saved_request_with_no_age_means_all_ages_and_with_odd_values_stays_inside_metas_limits():
    def sel_of(**changes):
        block = {**fx.REQUEST["meta"], **changes}
        return run(f"return JSON.stringify(X.selFromBlock({J(block)}));")

    s = sel_of(age_min=None, age_max=None, gender=None, geo={"type": "geo_market", "key": "803", "label": None})
    assert (s["ageMin"], s["ageMax"], s["gender"]) == (18, 65, "all") and s["geo"]["name"] == "Meta geography 803"
    odd = sel_of(age_min=12, age_max=90)
    assert (odd["ageMin"], odd["ageMax"]) == (18, 65)
    backwards = sel_of(age_min=50, age_max=30)
    assert (backwards["ageMin"], backwards["ageMax"]) == (50, 50)


def test_a_block_that_is_not_an_object_or_holds_junk_is_no_choices_at_all():
    out = run("""var junk = [null, undefined, 'x', 7, [], { geo: 'LA', languages: 'es', targeting: 5, age_min: 'old' }, { languages: [null, 3, { id: 1 }, { name: 'x' }, { id: 5, name: 'Ok' }], targeting: [{}, { id: 1, name: 'Soccer' }] }];
                return JSON.stringify(junk.map(function (b) { var s = X.selFromBlock(b); return [s.geo, s.ageMin, s.ageMax, s.gender, s.languages, s.detailed]; }));""")
    for row in out[:5]:
        assert row == [None, 18, 65, "all", [], []]
    assert out[5] == [None, 18, 65, "all", [], []]
    assert out[6] == [None, 18, 65, "all", [{"id": "5", "name": "Ok"}], [{"id": "1", "name": "Soccer", "type": "interests"}]]


def test_two_lines_keep_their_own_choices_and_the_same_line_again_keeps_them():
    out = run("""M.onLine({ id: 'L1' }); X.sel().gender = 'male';
                 M.onLine({ id: 'L2' }); var second = X.sel().gender; X.sel().gender = 'female';
                 M.onLine({ id: 'L1' });
                 return JSON.stringify({ first: X.sel().gender, second: second, keys: Object.keys(X.T.byKey).sort(), active: X.T.active });""", snaps={"L2": None})
    assert out == {"first": "male", "second": "all", "keys": ["L1", "L2"], "active": "L1"}


def test_the_dock_on_its_own_has_its_own_workspace_and_the_bubble_does_not_touch_a_lines():
    out = run("""M.onLine({ id: 'L1' }); X.sel().gender = 'male'; M.onLine(null); X.sel().gender = 'female';
                 var dock = X.sel().gender, key = X.T.active; M.onLine({ id: 'L1' });
                 return JSON.stringify({ dock: dock, key: key, line: X.sel().gender });""")
    assert out == {"dock": "female", "key": "dock", "line": "male"}


def test_reset_forgets_the_line_the_dock_was_open_for_and_the_dock_on_its_own_and_a_new_proposal_forgets_all():
    out = run("""M.onLine({ id: 'L1' }); X.sel().gender = 'male'; M.onLine({ id: 'L2' }); X.sel().gender = 'male'; M.onLine(null); X.sel().gender = 'female';
                 M.reset('L1'); var afterReset = Object.keys(X.T.byKey).sort();
                 M.reset(null); var afterBubble = Object.keys(X.T.byKey).sort();
                 M.onProposalReset(); return JSON.stringify({ afterReset: afterReset, afterBubble: afterBubble, afterProposal: Object.keys(X.T.byKey), active: X.T.active });""")
    assert out == {"afterReset": ["L2"], "afterBubble": ["L2"], "afterProposal": [], "active": "dock"}


def test_a_choice_that_differs_from_the_saved_estimate_is_noticed_per_line_in_any_order_and_never_creates_a_workspace():
    out = run("""var block = SNAPS.L1.request.meta, none = M.choicesChanged('L1', block);               // no workspace: unchanged, and none is made
                 var made = Object.keys(X.T.byKey).length;
                 M.onLine({ id: 'L1' }); var seeded = M.choicesChanged('L1', block);
                 X.sel().languages.reverse(); X.sel().detailed.reverse(); var reordered = M.choicesChanged('L1', block);
                 X.sel().gender = 'male'; var changed = M.choicesChanged('L1', block), otherLine = M.choicesChanged('L2', block);
                 X.sel().gender = 'female'; var back = M.choicesChanged('L1', block);
                 return JSON.stringify({ none: none, made: made, seeded: seeded, reordered: reordered, changed: changed, otherLine: otherLine, back: back,
                                         notBlock: [M.choicesChanged('L1', null), M.choicesChanged('L1', 'x'), M.choicesChanged(undefined, block)] });""", snaps={"L1": fx.native()})
    assert out == {"none": False, "made": 0, "seeded": False, "reordered": False, "changed": True, "otherLine": False, "back": False, "notBlock": [False, False, False]}


@pytest.mark.parametrize("edit", ["s.gender = 'male'", "s.ageMin = 30", "s.ageMax = 50", "s.geo = null", "s.geo.key = '804'", "s.languages = []",
                                  "s.languages.push({ id: '6', name: 'English (US)' })", "s.detailed = []", "s.detailed.push({ id: '2', name: 'x', type: 'behaviors' })"])
def test_changing_any_choice_after_the_estimate_is_noticed_and_putting_it_back_is_not(edit):
    out = run(f"""M.onLine({{ id: 'L1' }}); var s = X.sel(), block = SNAPS.L1.request.meta, before = M.choicesChanged('L1', block);
                  {edit}; var after = M.choicesChanged('L1', block);
                  M.reset('L1'); M.onLine({{ id: 'L1' }}); var restored = M.choicesChanged('L1', block);
                  return JSON.stringify({{ before: before, after: after, restored: restored }});""", snaps={"L1": fx.native()})
    assert out == {"before": False, "after": True, "restored": False}


def test_a_workspace_the_planner_changed_is_not_overwritten_by_the_restore_of_a_saved_estimate():
    out = run("""M.onLine({ id: 'L1' }); X.sel().gender = 'male'; X.changed();
                 M.restore(SNAPS.L1.request.meta); var kept = X.sel().gender;                       // touched: the planner's edit stays
                 M.reset('L1'); M.onLine({ id: 'L1' }); X.ws().touched = false; X.sel().gender = 'male'; M.restore(SNAPS.L1.request.meta);
                 return JSON.stringify({ kept: kept, untouched: X.sel().gender, stales: stales });""", snaps={"L1": fx.native()})
    assert out["kept"] == "male" and out["untouched"] == "female" and out["stales"] == 1


def test_a_change_tells_the_dock_the_request_changed():
    assert run("X.changed(); X.changed(); return JSON.stringify(stales);", line=None) == 2


# ----------------------------------------------------------------------------------------------------------------- words for what is chosen

def test_each_section_has_a_live_summary_and_a_count():
    out = run("""M.onLine({ id: 'L1' }); var s = X.sel(), sums = X.summaries(s), empty = X.summaries(X.selFromBlock(null));
                 s.detailed = [1, 2, 3].map(function (i) { return { id: String(i), name: 'Pick ' + i, type: 'interests' }; });
                 return JSON.stringify({ s: sums, empty: empty, many: X.summaries(s).aud });""", snaps={"L1": fx.native()})
    assert out["s"]["where"] == {"text": "Los Angeles · TV market", "set": True, "count": 0}
    assert out["s"]["who"] == {"text": "Women · 25–44", "set": True, "count": 2}
    assert out["s"]["lang"] == {"text": "Spanish", "set": True, "count": 1}
    assert out["s"]["aud"] == {"text": "Soccer", "set": True, "count": 1}
    assert out["empty"]["where"]["text"] == "Choose a place" and not out["empty"]["where"]["set"]
    assert out["empty"]["who"] == {"text": "All genders · all ages", "set": False, "count": 0}
    assert out["empty"]["lang"] == {"text": "Any language", "set": False, "count": 0} and out["empty"]["aud"]["text"] == "No interests or behaviors yet"
    assert out["many"] == {"text": "Pick 1, Pick 2 +1 more", "set": True, "count": 3}


@pytest.mark.parametrize("lo,hi,text", [(18, 65, ""), (25, 44, "25–44"), (55, 65, "55+"), (18, 24, "18–24"), (65, 65, "65+"), (18, 64, "18–64")])
def test_age_in_words(lo, hi, text):
    assert run(f"return JSON.stringify(X.ageText({lo}, {hi}));") == text


def test_languages_group_by_family_so_a_quick_pick_is_one_chip():
    out = run("""return JSON.stringify(X.langFamilies([{ id: '1', name: 'Spanish' }, { id: '2', name: 'English (US)' }, { id: '3', name: 'Spanish (Spain)' },
                 { id: '4', name: 'Portuguese (Brazil)' }]).map(function (g) { return [g.label, g.items.length, g.key]; }));""")
    assert out == [["Spanish", 2, "spanish"], ["English (US)", 1, "english"], ["Portuguese (Brazil)", 1, "portuguese"]]
    assert run("return JSON.stringify([X.langStarts('Español', 'espanol'), X.langStarts('Spanish (Mexico)', 'Spanish'), X.langStarts('Portuguese', 'Spanish')]);") == [True, True, False]


def test_the_quick_picks_name_languages_to_look_up_and_carry_no_locale_id():
    quick = run("return JSON.stringify(X.LANG_QUICK);")
    assert [q["key"] for q in quick] == ["spanish", "english", "both"] and quick[0]["label"] == "Spanish speakers" and quick[0]["words"] == ["Spanish"]
    assert quick[2]["words"] == ["Spanish", "English"]
    # no locale number anywhere in the plugin code: Meta's own list is the only source. The only 3+ digit numbers are these known ones (maxlengths, the server's
    # label limit, the search delay, the status cache, a ZIP in a placeholder)
    code = MT_CODE
    numbers = set(re.findall(r"(?<![\w.,])\d{3,}(?![\w.,])", code))
    assert numbers <= {"100", "160", "300", "503", "1000", "30000", "90210"}, numbers
    assert "locales" not in code                                              # the server builds the spec; the plugin only sends [{id, name}]


def test_adding_languages_skips_what_is_chosen_and_stops_at_the_limit():
    out = run("""var s = { languages: [{ id: '1', name: 'A' }] };
                 var first = X.addLanguages(s, [{ id: '1', name: 'A' }, { id: '2', name: 'B' }]);
                 var many = Array.from({ length: 40 }, function (_, i) { return { id: String(100 + i), name: 'L' + i }; });
                 var capped = X.addLanguages(s, many);
                 return JSON.stringify({ first: first, capped: capped, n: s.languages.length, max: X.MAX_LANGUAGES, limit: X.MAX_DETAILED });""")
    assert out == {"first": False, "capped": True, "n": 25, "max": 25, "limit": 50}


def test_the_place_pills_come_from_the_proposals_geography_and_are_never_searched_by_the_markup():
    out = run("""return JSON.stringify(['Phoenix, AZ and Los Angeles DMA', 'Los Angeles', 'TBD', 'National', '', 'AZ, CA, TX', 'Austin (Texas); Austin, TX; Dallas - Fort Worth DMA; Houston',
        'x'.repeat(80)].map(X.geoSuggestions));""")
    assert out == [["Phoenix", "Los Angeles"], ["Los Angeles"], [], [], [], [], ["Austin", "Dallas - Fort Worth", "Houston"], []]
    assert run("M.whereHtml(); M.formHtml(); return JSON.stringify(calls.length);") == 0                       # drawing the panel never asks Meta


def test_highlight_marks_the_typed_words_and_escapes_everything():
    out = run("""return JSON.stringify([X.highlight('Los Angeles-Long Beach', 'los beach'), X.highlight('<b>x</b> & y', 'x'), X.highlight('Plain', ''), X.highlight('aaa', 'aa a'),
        X.highlight('<img src=x onerror=1>', ''), X.highlight('<i>\u0130stanbul</i>', 'stan')]);""")        # (no words to mark; and a letter whose lower case is longer than itself)
    assert out[0] == "<mark>Los</mark> Angeles-Long <mark>Beach</mark>" and out[1] == "&lt;b&gt;<mark>x</mark>&lt;/b&gt; &amp; y"
    assert out[2] == "Plain" and out[3] == "<mark>aaa</mark>"
    assert out[4] == "&lt;img src=x onerror=1&gt;" and out[5] == "&lt;i&gt;\u0130stanbul&lt;/i&gt;"


def test_audience_sizes_read_short_with_the_range_when_there_is_one():
    out = run("return JSON.stringify([X.sizeText(120000000, 140000000), X.sizeText(900, 900), X.sizeText(2000000, null), X.sizeText(null, 5), X.sizeText(999999, 1500000), X.sizeText(15000, 20000)]);")
    assert out == ["120M – 140M", "900", "2M", "", "1M – 1.5M", "15K – 20K"]


def test_a_demographic_kind_reads_in_words():
    out = run("return JSON.stringify([X.kindLabel('interests'), X.kindLabel('life_events'), X.kindLabel('home_value'), X.kindLabel('something_new'), X.tabOf('behaviors'), X.tabOf('life_events'), X.tabOf('interests')]);")
    assert out == ["Interest", "Life event", "Home value", "something new", "behaviors", "demographics", "interests"]


def test_every_amount_is_whole_dollars_without_cents_and_two_decimals_with_them():
    out = run("""return JSON.stringify([33862.5, 2000, 12.5, 12, 99.89999999999999, 2000.0000000001, 0.05, 1234567.891, 0, -5.5, null, '', 'abc', undefined, '35'].map(X.money));""")
    assert out == ["$33,862.50", "$2,000", "$12.50", "$12", "$99.90", "$2,000", "$0.05", "$1,234,567.89", "$0", "-$5.50", "—", "—", "—", "—", "$35"]


def test_each_search_box_stops_at_the_length_its_server_search_takes(monkeypatch):
    from app.services import constanza_meta as meta

    box = run("return JSON.stringify(X.SEARCH_MAX);")
    html = run("return JSON.stringify(M.whereHtml() + M.formHtml());")
    for which in ("geo", "aud", "lang"):
        field = re.search(rf'<input[^>]*data-mt-ta-input="{which}"[^>]*>', html).group(0)
        assert f'maxlength="{box[which]}"' in field, which
    monkeypatch.setattr(meta, "_graph_get", lambda path, params: {"data": []})
    monkeypatch.setattr(meta, "is_configured", lambda: False)
    meta.search_geography("x" * box["geo"], "city")
    meta.search_targeting("x" * box["aud"])
    meta.search_locales("x" * box["lang"])
    for ask, longest in ((lambda q: meta.search_geography(q, "city"), box["geo"]), (meta.search_targeting, box["aud"]), (meta.search_locales, box["lang"])):
        with pytest.raises(ValueError):
            ask("x" * (longest + 1))                                                                      # one more than the box allows would be refused: so the box stops there


# ----------------------------------------------------------------------------------------------------------------- the panel's markup

def test_the_panel_has_who_language_audience_and_a_folded_budget_and_wheres_card_is_a_bare_section():
    html = run("return JSON.stringify(M.formHtml());", line=None)
    for key, title in (("who", "Who"), ("lang", "Language"), ("aud", "Audience")):
        assert f'id="cz-mt-sec-{key}"' in html and f'id="cz-mt-{key}-title">{title}</h3>' in html
    assert html.index(">Who</h3>") < html.index(">Language</h3>") < html.index(">Audience</h3>") < html.index(">Budget cross-check<")
    assert html.count("<details") == 2 and 'id="cz-mt-sec-budget"' in html                 # the budget card and the language caveat fold; nothing else does
    for gone in ("Calculate scenarios", "Upper end (optional)", 'type="number"', "Pull audience and calculate", "Audience size, lower end"):
        assert gone not in html, gone
    where = run("return JSON.stringify(M.whereHtml());", line=None)
    assert where.lstrip().startswith("<section") and 'id="cz-mt-sec-where"' in where and 'class="aud-sec mt-where"' in where
    assert "aud-builder" not in where                                                       # a nested builder would restart the card numbering
    assert where.count('class="aud-step"') == 1 and ">Where</h3>" in where and 'id="cz-mt-geo-q"' in where and 'id="cz-mt-geotype"' in where


def test_every_text_input_opts_out_of_the_avails_number_formatting_and_every_id_is_unique():
    html = run("return JSON.stringify(M.whereHtml() + M.formHtml());", line=None)
    inputs = re.findall(r"<input[^>]*>", html)
    assert len(inputs) == 4 and all('type="text"' in i for i in inputs)
    assert sum('role="combobox"' in i for i in inputs) == 3 and 'data-mt-budget' in "".join(inputs)
    ids = re.findall(r' id="([^"]+)"', html)
    assert len(ids) == len(set(ids)) and len(ids) > 20
    assert all(i.startswith("cz-mt") for i in ids)


def test_the_hispanic_offer_is_drawn_hidden_and_shown_only_for_a_line_that_targets_hispanic_audiences():
    html = run("return JSON.stringify(M.formHtml());", hispanic=True)
    assert "This line targets Hispanic audiences — add Spanish?" in html and "data-mt-suggest hidden" in html          # drawn hidden: the screen decides, live
    assert 'data-mt-act="lang-quick" data-mt-needs-api data-key="spanish"' in html                                   # one click looks Spanish up in Meta's list


def test_the_connection_banner_is_drawn_once_as_a_status_region_that_can_take_the_keyboard():
    html = run("return JSON.stringify(M.formHtml());", line=None)
    banner = re.search(r'<div id="cz-mt-status"[^>]*>', html).group(0)
    assert 'role="status"' in banner and 'tabindex="-1"' in banner                          # announced when its words change; a place for the keyboard when "Check again" is replaced
    assert html.count('id="cz-mt-status"') == 1 and 'id="cz-mt-dates"' in html


def test_the_plugin_reads_no_planning_cpm_off_the_docks_product_object():
    """loadProducts() hands the plugin name, family, method, series, channel and the Hispanic flags: no forecast.info. A reader for a CPM that is never
    there only made the budget note look as if it could name a figure (the result's cross-check names it)."""
    assert "productCpm" not in MT_CODE and "forecast.info" not in MT_CODE


def test_the_language_caveat_is_in_the_markup_with_the_servers_own_wording_filled_in_later():
    html = run("return JSON.stringify(M.formHtml());", line=None)
    assert "data-mt-langshort" in html and "data-mt-langfull" in html and "Why this can be fewer than all Spanish speakers" in html


def test_a_name_in_a_chip_is_escaped():
    out = run("""var evil = { geo: { type: 'geo_market', key: '1', label: '<img src=x> · <b>CA</b>' }, languages: [{ id: '1', name: '<u>Spanish</u>' }, { id: '2', name: '<i>Spanish (Mexico)</i>' }],
                   targeting: [{ id: '1', name: '"><script>x</script>', type: 'interests' }] };
                 var s = X.selFromBlock(evil); return JSON.stringify(X.geoChips(s) + X.langChips(s) + X.detailedChips(s));""", line=None)
    assert "<img" not in out and "<b>" not in out and "<u>" not in out and "<i>" not in out and "<script>" not in out
    assert "&lt;img src=x&gt;" in out and "&lt;u&gt;Spanish&lt;/u&gt;" in out and "&lt;script&gt;" in out


# ----------------------------------------------------------------------------------------------------------------- the connection to Meta

def test_the_banner_asks_connects_names_what_is_missing_or_fails():
    states = {
        "loading": None,
        "connected": {"data": {"configured": True, "account": "…4821", "version": "v26.0", "missing": [], "scenarios": {"multipliers": {"conservative": 4.3, "mid": 5.5, "upper": 8.6}}}},
        "off": {"data": {"configured": False, "missing": ["META_ACCESS_TOKEN", "META_AD_ACCOUNT_ID (it must be the numeric ad account ID)"], "setup_hint": "Add META_ACCESS_TOKEN <b>now</b>."}},
        "error": {"data": None, "error": "The server <i>stopped</i>."},
    }
    out = run(f"""var s = {J(states)}, o = {{}}, el = fakeEl(); els['cz-mt-status'] = el;
        Object.keys(s).forEach(function (k) {{ X.T.status = s[k]; X.renderStatus(); o[k] = {{ html: el.innerHTML, state: el.dataset.state, cls: el.className }}; }});
        return JSON.stringify(o);""", line=None)
    assert out["loading"]["state"] == "checking" and "Checking the connection to Meta" in out["loading"]["html"]
    conn = out["connected"]
    assert conn["state"] == "on" and "aud-notice--connected" in conn["cls"] and "Connected to Meta" in conn["html"] and "<code>…4821</code>" in conn["html"] and "API v26.0" in conn["html"]
    assert "impressions = lower-end audience × 5.5 (4.3 – 8.6) a month" in conn["html"]
    off = out["off"]
    assert off["state"] == "off" and "aud-notice--warn" in off["cls"] and "Meta isn’t connected yet" in off["html"] and "<code>META_ACCESS_TOKEN</code>" in off["html"]
    assert "<code>META_AD_ACCOUNT_ID</code> (it must be the numeric ad account ID)" in off["html"] and "How an admin sets this up" in off["html"]
    assert "<b>" not in off["html"] and "&lt;b&gt;now" in off["html"]                       # the hint is text
    err = out["error"]
    assert err["state"] == "error" and "Couldn’t check the Meta connection" in err["html"] and 'data-mt-act="recheck"' in err["html"] and "<i>" not in err["html"] and "&lt;i&gt;stopped" in err["html"]


def test_only_a_known_not_configured_answer_switches_the_estimate_off_and_the_reason_names_the_settings():
    out = run("""function st(x) { X.T.status = x; return [X.connected(), M.goState(), M.connection()]; }
        var unknown = st(undefined), failed = st({ data: null, error: 'x' }), on = st({ data: { configured: true } }),
            off = st({ data: { configured: false, missing: ['META_ACCESS_TOKEN', 'META_AD_ACCOUNT_ID (it must be numeric)'] } }), bare = st({ data: { configured: false, missing: [] } });
        return JSON.stringify({ unknown: unknown, failed: failed, on: on, off: off, bare: bare });""", line=None)
    assert out["unknown"][0] is None and out["unknown"][1] == {"enabled": True}
    assert out["failed"][0] is None and out["failed"][1] == {"enabled": True}                          # a check that failed does not stop the estimate: it tells its own error
    assert out["on"][0] is True and out["on"][1] == {"enabled": True}
    assert out["off"][0] is False and out["off"][2] is False
    assert out["off"][1] == {"enabled": False, "reason": "Meta isn't connected yet: an admin needs to add META_ACCESS_TOKEN and META_AD_ACCOUNT_ID in the app's secrets."}
    assert out["bare"][1]["reason"] == "Meta isn't connected yet: an admin needs to add the Meta settings in the app's secrets."


def test_the_language_caveat_prefers_the_servers_text_and_shows_its_first_sentence():
    out = run("""var a = X.languageNote();                                                           // nothing asked yet: the plugin's own words
        X.T.languageNote = 'From the list.'; var b = X.languageNote();                           // the languages answer's note
        X.T.status = { data: { configured: true, language_note: 'Meta says A. Then B. Then C.' } }; var c = X.languageNote();     // the status' note wins
        return JSON.stringify({ a: a, b: b, c: c, short: X.shortNote(c), plain: X.shortNote('No full stop'), same: a === X.LANGUAGE_NOTE });""", line=None)
    assert out["same"] and "not a count of who speaks it" in out["a"] and "language people have set Facebook or Instagram to" in out["a"]
    assert out["b"] == "From the list." and out["c"] == "Meta says A. Then B. Then C."
    assert out["short"] == "Meta says A." and out["plain"] == "No full stop"


# ----------------------------------------------------------------------------------------------------------------- a refusal that names a field

def test_a_refusal_that_names_a_field_says_which_pick_it_was_about():
    out = run("""
      var body = { meta: { targeting: [{ id: '1', name: 'Parents (all)', type: 'household_composition' }, { id: '2', name: 'Soccer', type: 'interests' }], languages: [{ id: '1001', name: 'Spanish' }] } };
      var refuse = function (loc, msg) { return { detail: [{ loc: loc, msg: msg }] }; };
      return JSON.stringify([
        M.errorText(refuse(['body', 'meta', 'targeting', 0, 'type'], 'String should have at most 20 characters'), 'x', body),
        M.errorText(refuse(['body', 'meta', 'targeting', 1, 'name'], 'Field required.'), 'x', body),
        M.errorText(refuse(['body', 'meta', 'languages', 0, 'name'], 'Field required'), 'x', body),
        M.errorText({ detail: [{ loc: ['body', 'meta', 'age_min'], msg: 'Input should be a valid integer' }, { loc: ['body', 'meta', 'age_min'], msg: 'Input should be a valid integer' }] }, 'x', body),
        M.errorText({ detail: [{ loc: ['body', 'meta', 'geo', 'label'], msg: 'too long' }, { loc: ['body', 'meta', 'retail_budget'], msg: 'not a number' }] }, 'x', body),
        M.errorText(refuse(['body', 'meta', 'geo', 'type'], 'bad'), 'x', body),
        M.errorText(refuse(['body', 'meta', 'targeting', 9, 'type'], 'bad'), 'x', body),
        M.errorText(refuse(['body'], 'Field required'), 'x', body),
        M.errorText(refuse(['body', 'months'], 'Input should be greater than 0'), 'x', body),
      ]);""", line=None)
    tail = "Change that choice and estimate again."
    assert out == [f"“Parents (all)” (Household): String should have at most 20 characters. {tail}", f"“Soccer” (Interest): Field required. {tail}",
                   f"the language “Spanish”: Field required. {tail}", f"the youngest age: Input should be a valid integer. {tail}",       # the same refusal twice is said once
                   f"the place’s name: too long; the retail budget: not a number. {tail}", f"the kind of place: bad. {tail}", f"the interests and behaviors: bad. {tail}",
                   f"Field required. {tail}", f"the flight length: Input should be greater than 0. {tail}"]


def test_a_plain_refusal_or_no_detail_is_passed_through_unchanged():
    out = run("""return JSON.stringify([M.errorText({ detail: 'Meta refused these settings: x.' }, 'Unprocessable', {}), M.errorText({}, 'Service Unavailable', {}),
                                         M.errorText({ detail: [] }, 'Bad Request'), M.errorText(null, '', undefined)]);""", line=None)
    assert out == ["Meta refused these settings: x.", "Service Unavailable", "Bad Request", "Request failed."]


def test_a_refused_estimate_is_shown_again_after_the_connection_changed_under_the_planner():
    out = run("M.onError(503); M.onError(422); return JSON.stringify(calls);", line=None)
    assert out == [["/api/constanza/meta/status", None]]                                              # only an unavailable service makes the plugin ask again


# ----------------------------------------------------------------------------------------------------------------- the result: the dock, the card, the summary

def test_the_card_shows_the_lower_end_headline_the_range_what_was_asked_the_spend_and_the_cross_check():
    html = card()
    assert '<strong>1,000,000</strong><span>accounts at the lower end of Meta’s audience</span>' in html
    assert "Meta’s range (lower – upper end): <strong>1,000,000 – 1,200,000</strong> accounts (from Meta)." in html
    assert "<strong>Pulled for</strong> Los Angeles · California · US (TV market), women ages 25–44, language set to Spanish, any of: Soccer." in html
    assert "its frequency reads 5.5" in html and "lower end</strong>: it is the est. uniques" in html
    assert "Conservative <strong>$154,800</strong> / Mid · default <strong>$198,000</strong> / Upper <strong>$309,600.50</strong>" in html         # whole dollars plain, cents with two digits
    assert "$2,000 retail → $1,000 media at a $12 retail CPM buys about 166,667 impressions over the 3-month flight" in html and "not avails" in html
    assert "Planning scenarios, not a delivery forecast · 2026-10-26" in html
    assert "Insert" not in html and "Inputs changed" not in html                                             # the card's buttons and banner are the dock's, not the plugin's


def test_the_dock_headline_has_the_lower_end_and_three_scenario_columns_with_the_mid_one_marked_as_the_default():
    html = run(f"return JSON.stringify(M.headlineHtml({J(ESTIMATE)}, {{}}));", line=None)
    assert '<span class="cz-mvalue">1,000,000</span>' in html and "Meta’s range 1,000,000 – 1,200,000 (from Meta)" in html
    assert html.count('class="cz-metric mt-sc') == 3 and html.count("mt-mid") == 1                           # one highlighted: the default
    assert 'Conservative · <span class="mt-mult">× 4.3</span>' in html and 'Mid · default · <span class="mt-mult">× 5.5</span>' in html and 'Upper · <span class="mt-mult">× 8.6</span>' in html
    assert html.index("4,300,000") < html.index("5,500,000") < html.index("8,600,000") and "imps / month" in html
    assert "Flight · 3 mo</dt><dd>16,500,000" in html and "$198,000" in html and "$309,600.50" in html
    assert 'aria-label="Impressions a month: lower-end audience × 5.5 (4.3 – 8.6)"' in html


def test_a_one_month_flight_has_no_flight_row_and_a_result_without_a_cpm_has_no_spend_row():
    one = fx.estimate()
    one["meta"] = {**one["meta"], "scenarios": {**one["meta"]["scenarios"], "months": 1}}
    html = run(f"return JSON.stringify(M.headlineHtml({J(one)}, {{}}));", line=None)
    assert "Flight ·" not in html and "$198,000" in html
    no_cpm = fx.estimate()
    no_cpm["meta"]["scenarios"] = {k: v for k, v in no_cpm["meta"]["scenarios"].items() if k not in ("retail_spend_at_flight", "media_spend_at_flight")}
    html = run(f"return JSON.stringify(M.headlineHtml({J(no_cpm)}, {{}}));", line=None)
    assert "Retail spend at the planning CPM" not in html and "Flight · 3 mo" in html
    assert "Retail spend the flight implies" not in card(no_cpm)


def test_the_middle_says_what_was_asked_the_place_the_cross_check_and_the_language_caveat_and_the_kept_words_win():
    html = run(f"return JSON.stringify(M.bodyHtml({J(ESTIMATE)}, {{}}, null));", line=None)
    assert "What was asked of Meta" in html and ">Women<" in html and ">Ages 25–44<" in html and ">Language set to Spanish<" in html and ">Any of: Soccer<" in html
    assert "Place sent to Meta" in html and "Los Angeles · California · US <small>TV market</small>" in html
    assert "Budget cross-check" in html and "166,667 impressions" in html and "not who speaks it" in html
    kept = run(f"return JSON.stringify(M.bodyHtml({J(ESTIMATE)}, {{}}, {{ method: {{ summary: ['Kept words'] }} }}));", line=None)
    assert "Kept words" in kept and "Ages 25–44" not in kept                                                  # what THIS result was pulled for, not the form now
    nothing = fx.estimate()
    nothing["meta"]["targeting"] = {"geo": {"type": "country", "key": "US", "label": "United States"}, "age_min": None, "age_max": None, "gender": "all", "languages": [], "detailed": []}
    nothing["meta"]["budget_check"] = None
    bare = run(f"return JSON.stringify(M.bodyHtml({J(nothing)}, {{}}, null));", line=None)
    assert "Everyone in the place" in bare and "Budget cross-check" not in bare and "not who speaks it" not in bare


def test_about_this_estimate_states_what_meta_counts_the_rule_the_placements_and_the_comscore_and_language_caveats_only_when_they_apply():
    html = run(f"return JSON.stringify(M.aboutHtml({J(ESTIMATE)}));", line=None)
    assert "About this estimate" in html and "not delivered reach and not a delivery forecast" in html and "as of 2026-10-26" in html
    assert "× 4.3 (conservative), × 5.5 (mid, the default) or × 8.6 (upper)" in html and "so its frequency reads 5.5" in html
    assert "default placements (Facebook, Instagram, Messenger, Audience Network)" in html
    assert "Comscore markets" in html and "language people have set Facebook or Instagram to" in html
    city = fx.estimate()
    city["meta"]["targeting"] = {**city["meta"]["targeting"], "geo": {"type": "city", "key": "1", "label": "Phoenix · Arizona · US"}, "languages": []}
    plain = run(f"return JSON.stringify(M.aboutHtml({J(city)}));", line=None)
    assert "Comscore" not in plain and "language people have set" not in plain and "Audience Network" in plain


def test_a_server_multiplier_change_is_what_the_result_says_not_a_hard_coded_set():
    odd = fx.estimate()
    odd["meta"]["scenarios"]["multipliers"] = {"conservative": 3, "mid": 6, "upper": 10}
    html = run(f"return JSON.stringify(M.headlineHtml({J(odd)}, {{}}) + M.aboutHtml({J(odd)}) + M.cardHtml({J(odd)}));", line=None)
    assert "× 3" in html and "× 6" in html and "× 10" in html and "frequency reads 6" in html and "4.3" not in html and "5.5" not in html


def test_the_copied_summary_names_the_place_the_choices_the_scenarios_and_the_cross_check_as_plain_text():
    lines = run(f"return JSON.stringify(M.summaryLines({J(ESTIMATE)}, null));", line=None)
    text = "\n".join(lines)
    assert lines[0] == "Audience size, lower end (Meta accounts): 1,000,000 (Meta’s range 1,000,000 – 1,200,000)"
    assert lines[1] == "Impressions = lower-end audience × 5.5 (4.3 – 8.6) per month (mid is the default)"
    assert "Retail spend the flight implies at the planning CPM: Conservative $154,800 / Mid · default $198,000 / Upper $309,600.50" in text
    assert "Asked of Meta: Women · Ages 25–44 · Language set to Spanish · Any of: Soccer" in text and "Place sent to Meta: Los Angeles · California · US (TV market)" in text
    assert "Budget cross-check: $2,000 retail → $1,000 media" in text and "not who speaks it" in text
    assert "<" not in text and "&amp;" not in text                                                          # plain text: nothing is HTML-escaped
    kept = run(f"return JSON.stringify(M.summaryLines({J(ESTIMATE)}, {{ method: {{ summary: ['Kept words'] }} }}));", line=None)
    assert "Asked of Meta: Kept words" in "\n".join(kept)


def test_a_language_is_never_said_to_be_spoken_it_is_what_people_have_set():
    html = card()
    assert "language set to Spanish" in html and "not who speaks it" in html and "fewer than all speakers" in html
    none = fx.estimate()
    none["meta"]["targeting"] = {**none["meta"]["targeting"], "languages": []}
    plain = card(none)
    assert "language" not in plain.replace("The language is", "") and "fewer than all speakers" not in plain
    old = fx.estimate()
    del old["meta"]["targeting"]["languages"]                                                                # saved before languages existed
    assert "Pulled for" in card(old) and "language" not in card(old).replace("The language is", "")


def test_a_pulled_audience_without_a_stored_name_falls_back_to_the_key():
    bare = fx.estimate()
    bare["meta"]["targeting"] = {"geo": {"type": "zip", "key": "US:90210", "label": None}, "age_min": None, "age_max": None, "gender": "all", "detailed": [], "languages": []}
    assert "Pulled for</strong> Meta geography US:90210 (ZIP)." in card(bare)


def test_the_languages_of_a_pull_are_named_by_family_unless_only_one_version_was_chosen():
    out = run("""function t(languages) { return X.pulledFor({ geo: { type: 'city', key: '1', label: 'Phoenix' }, gender: 'all', languages: languages }); }
                 return JSON.stringify([t([{ id: '1', name: 'Spanish' }, { id: '2', name: 'Spanish (Mexico)' }, { id: '3', name: 'English (US)' }]), t([{ id: '2', name: 'Spanish (Mexico)' }])]);""", line=None)
    assert out[0] == "Phoenix (city), language set to Spanish or English (US)"
    assert out[1] == "Phoenix (city), language set to Spanish (Mexico)"


def test_a_manual_estimate_says_entered_manually_and_has_no_pulled_for_line():
    manual = fx.estimate()
    manual["meta"]["audience"] = {"low": 500, "high": 500, "source": "manual"}
    manual["meta"]["targeting"] = None
    html = card(manual)
    assert "<strong>500</strong> accounts (entered manually)." in html and "(from Meta)" not in html and "Pulled for" not in html
    badge = run(f"return JSON.stringify([M.badgeHtml({J(manual)}), M.badgeHtml({J(ESTIMATE)})]);", line=None)
    assert "Entered manually" in badge[0] and "Meta API · live" in badge[1] and "cz-reported" in badge[1]


def test_the_insert_button_says_the_mid_case_per_month_and_a_saved_estimate_that_cannot_say_it_still_draws():
    out = run("return JSON.stringify([M.applyLabel({ month: { mid: 5500000 } }), M.applyLabel({}), M.applyLabel(null), M.applyLabel({ month: {} })]);", line=None)
    assert out[0] == "Insert the mid-case (5,500,000 / month) into Avails →" and out[1] == out[2] == out[3] == "Insert the mid-case (— / month) into Avails →"


def test_inserting_stores_the_place_meta_was_asked_about_and_a_manual_figure_says_so():
    out = run(f"""var manual = {J({**fx.estimate(), "meta": {**fx.ESTIMATE["meta"], "targeting": None}})};
                 return JSON.stringify([M.geoInfo({J(ESTIMATE)}), M.geoInfo(manual), M.geoInfo({{}}), M.geoInfo(null), Z.geoInfo({J(ESTIMATE)}) ]);""", line=None)
    live = {"method": "meta_audience", "dmas": ["Los Angeles · California · US"], "unmatched_tokens": [], "win_rate": None, "hispanic_share": None}
    assert out[0] == live and out[4] == live
    assert out[1]["dmas"] == out[2]["dmas"] == out[3]["dmas"] == ["entered audience"] and out[1]["method"] == "meta_audience"


def test_the_card_records_the_cpm_the_server_really_used_when_the_line_sent_none():
    out = run(f"return JSON.stringify([M.snapshotLine({J(ESTIMATE)}, null), M.snapshotLine({{}}, null), M.snapshotLine(null, null)]);", line=None)
    assert out[0] == {"budget": 2000, "cpm": 12} and out[1] == {"budget": None, "cpm": None} and out[2] == {"budget": None, "cpm": None}


def test_the_card_before_any_estimate_says_what_to_do_and_when_there_is_no_budget_how_to_get_the_cross_check():
    with_budget = run(f"return JSON.stringify(M.cardEmptyHtml({J(LINE)}, null));", line=None)
    assert "Meta needs an audience: choose the place, age, language and interests in Constanza, then insert the result here." in with_budget and "Add a budget" not in with_budget
    no_budget = run(f"return JSON.stringify(M.cardEmptyHtml({J({**LINE, 'budget': 0})}, null));", line=None)
    assert "Add a budget in Step 04 to see the budget cross-check." in no_budget
    dock_only = run("return JSON.stringify(M.cardEmptyHtml(null, null));", line=None)
    assert "Meta needs an audience" in dock_only and "Add a budget" not in dock_only


@pytest.mark.parametrize("name", list(fx.corrupt_variants()))
def test_a_saved_estimate_that_is_broken_never_throws_out_of_any_piece_the_dock_draws_and_runs_nothing(name):
    # every piece is called directly, without the dock's own guard: a card must survive a hand-edited proposal by itself
    data = fx.corrupt_variants()[name].get("result")
    out = run(f"""var data = {J(data)}, got = {{}}, threw = [];
        ['headlineHtml', 'bodyHtml', 'aboutHtml', 'cardHtml', 'summaryLines', 'badgeHtml', 'geoInfo', 'snapshotLine', 'applyLabel'].forEach(function (name) {{
          try {{ got[name] = M[name](data, {{}}, null); }} catch (e) {{ threw.push(name + ': ' + e.message); }}
        }});
        try {{ got.empty = M.cardEmptyHtml({J(LINE)}, null); }} catch (e) {{ threw.push('cardEmptyHtml: ' + e.message); }}
        return JSON.stringify({{ threw: threw, got: got }});""", line=None)
    assert out["threw"] == [], out["threw"]
    assert isinstance(out["got"]["cardHtml"], str) and out["got"]["cardHtml"] and isinstance(out["got"]["summaryLines"], list)
    markup = "".join(v for k, v in out["got"].items() if isinstance(v, str))
    assert "<img" not in markup.replace("&lt;img", "")                                                    # a saved name with markup is shown as text, never as a tag


def test_every_server_text_with_markup_is_shown_as_text_in_every_piece_that_draws_it():
    hostile = fx.hostile(fx.native())["result"]
    out = run(f"""var data = {J(hostile)}; var asked = {{ method: {{ summary: [{J(fx.HOSTILE)}] }} }};
        return JSON.stringify([M.headlineHtml(data, {{}}), M.bodyHtml(data, {{}}, asked), M.aboutHtml(data), M.cardHtml(data), M.badgeHtml(data)]);""", line=None)
    html = "".join(out)
    assert "<img" not in html and "&lt;img src=x onerror=&quot;window.__pwned=1&quot;&gt;" in html


# ----------------------------------------------------------------------------------------------------------------- a proposal saved with the old shape (meta_forecast)

@pytest.mark.parametrize("name", list(fx.LEGACY))
def test_every_old_saved_shape_is_adopted_in_memory_as_a_snapshot_of_the_dock_with_no_network(name):
    old = fx.LEGACY[name]
    out = run(f"return JSON.stringify([M.legacy.adopt({J(old)}, {J(LINE)}), calls.length]);", line=None)
    snap, asked = out
    assert asked == 0 and snap["v"] == 1 and snap["method"] == "meta" and snap["legacy"] is True and snap["saved_at"] == old["as_of"]
    assert snap["line"] == {"product_name": "P", "method": "meta", "months": 3, "geo_text": "Los Angeles", "target_text": "Adults 25-54", "budget": 2000, "cpm": 12}


def test_a_live_old_estimate_becomes_the_request_it_was_pulled_with_and_the_answer_the_dock_draws():
    snap, _ = run(f"return JSON.stringify([M.legacy.adopt({J(fx.LIVE_WITH_LANGUAGES)}, {J(LINE)}), 0]);", line=None)
    assert snap["request"] == {
        "product_name": "P", "geo_text": "", "months": 3,
        "meta": {"geo": {"type": "geo_market", "key": "803", "label": "Los Angeles · California · US"}, "age_min": 25, "age_max": 44, "gender": "female",
                 "languages": [{"id": "1001", "name": "Spanish"}, {"id": "1002", "name": "Spanish (Mexico)"}],
                 "targeting": [{"id": "6003107902433", "name": "Soccer", "type": "interests"}], "retail_budget": 2000, "retail_cpm": 12}}
    result = snap["result"]
    assert (result["imps_low"], result["imps_mid"], result["imps_high"], result["est_uniques"], result["months"]) == (12_900_000, 16_500_000, 25_800_000, 1_000_000, 3)
    meta = result["meta"]
    assert meta["audience"] == {"low": 1_000_000, "high": 1_200_000, "source": "meta_api"} and meta["monthly"] == {"low": 4_300_000, "mid": 5_500_000, "high": 8_600_000}
    assert meta["scenarios"] == fx.LIVE_WITH_LANGUAGES["scenarios"] and meta["budget_check"]["budget_impressions"] == 166667 and meta["language_note"]
    assert meta["targeting"]["geo"] == {"type": "geo_market", "key": "803", "label": "Los Angeles · California · US"} and meta["as_of"] == "2026-10-26T12:00:00+00:00"
    assert result["notes"] == fx.LIVE_WITH_LANGUAGES["notes"] and result["method"] == "meta"


def test_an_old_estimate_without_languages_has_no_language_caveat_a_manual_one_has_no_request_and_a_budget_only_one_has_no_result():
    live = run(f"return JSON.stringify(M.legacy.adopt({J(fx.LIVE_WITHOUT_LANGUAGES)}, {J(LINE)}));", line=None)
    assert live["result"]["meta"]["language_note"] is None and live["result"]["meta"]["targeting"]["languages"] == []
    manual = run(f"return JSON.stringify(M.legacy.adopt({J(fx.MANUAL)}, {J(LINE)}));", line=None)
    assert manual["request"] is None and manual["result"]["meta"]["audience"]["source"] == "manual" and manual["result"]["meta"]["targeting"] is None
    only = run(f"return JSON.stringify(M.legacy.adopt({J(fx.BUDGET_ONLY)}, {J(LINE)}));", line=None)
    assert only["request"] is None and only["result"] is None and only["legacy_budget_check"]["retail_budget"] == 2000


def test_an_old_estimate_renders_with_the_old_words():
    card_html = run(f"var s = M.legacy.adopt({J(fx.LIVE_WITH_LANGUAGES)}, {J(LINE)}); return JSON.stringify(M.cardHtml(s.result, null, {{}}));", line=None)
    assert "<strong>1,000,000</strong><span>accounts at the lower end of Meta’s audience</span>" in card_html
    assert "Meta’s range (lower – upper end): <strong>1,000,000 – 1,200,000</strong> accounts (from Meta)." in card_html
    assert "<strong>Pulled for</strong> Los Angeles · California · US (TV market), women ages 25–44, language set to Spanish, any of: Soccer." in card_html
    assert "$2,000 retail → $1,000 media at a $12 retail CPM buys about 166,667 impressions over the 3-month flight" in card_html
    assert "Conservative <strong>$154,800</strong> / Mid · default <strong>$198,000</strong> / Upper <strong>$309,600.50</strong>" in card_html
    assert run(f"var s = M.legacy.adopt({J(fx.LIVE_WITH_LANGUAGES)}, {J(LINE)}); return JSON.stringify(M.applyLabel({{ month: {{ mid: s.result.imps_mid / s.result.months }} }}));", line=None) \
        == "Insert the mid-case (5,500,000 / month) into Avails →"
    manual = run(f"var s = M.legacy.adopt({J(fx.MANUAL)}, {J(LINE)}); return JSON.stringify(M.cardHtml(s.result, null, {{}}));", line=None)
    assert "<strong>500</strong> accounts (entered manually)." in manual and "Pulled for" not in manual
    budget_only = run(f"var s = M.legacy.adopt({J(fx.BUDGET_ONLY)}, {J(LINE)}); return JSON.stringify(M.cardEmptyHtml({J(LINE)}, s));", line=None)
    assert "Estimate an audience to see the scenarios." in budget_only and "$2,000 retail → $1,000 media at a $12 retail CPM buys about 166,667 impressions" in budget_only


@pytest.mark.parametrize("name", list(fx.LEGACY))
def test_an_adopted_old_estimate_through_the_dock_is_fresh_for_its_line_and_stale_for_each_of_the_six_things_the_old_card_compared(name):
    entry = {"meta_forecast": fx.LEGACY[name]}
    out = run(f"""var snap = Z.adoptLegacy('meta', {J(entry)}, {J(LINE)}), line = {J(LINE)};
        function stale(change) {{ return Z.snapshotStale(snap, Object.assign({{}}, line, change)); }}
        return JSON.stringify({{ fresh: Z.snapshotStale(snap, line), months: stale({{ months: 5 }}), geo: stale({{ geo_text: 'Phoenix' }}), target: stale({{ target_text: 'Adults 18+' }}),
          cpm: stale({{ cpm: 18 }}), budget: stale({{ budget: 3000 }}), product: stale({{ product_name: 'Q' }}), back: Z.snapshotStale(snap, line) }});""", line=None)
    assert out["fresh"] == [] and out["back"] == []                                                     # putting the line back un-marks it
    assert out["months"] == ["the flight length changed"] and out["geo"] == ["the proposal’s geography changed"] and out["target"] == ["the line’s target changed"]
    assert out["cpm"] == ["the CPM changed"] and out["budget"] == ["the budget changed"] and out["product"] == ["the product changed"]


def test_an_old_estimates_choices_in_the_dock_decide_whether_its_audience_changed():
    out = run(f"""var snap = Z.adoptLegacy('meta', {{ meta_forecast: {J(fx.LIVE_WITH_LANGUAGES)} }}, {J(LINE)}), P = Z._plugin('meta'), line = {J(LINE)};
        P.onLine({{ id: 'L1' }}); P.restore(snap.request.meta);
        var seeded = Z.snapshotStale(snap, line);
        P._pure.sel().gender = 'male'; var changed = Z.snapshotStale(snap, line);
        return JSON.stringify({{ seeded: seeded, changed: changed }});""", line=None)
    assert out["seeded"] == [] and out["changed"] == ["the audience in Constanza changed"]


def test_a_manual_or_budget_only_old_estimate_has_no_choices_to_mark_and_nothing_to_repeat():
    out = run(f"""var line = {J(LINE)}, P = Z._plugin('meta'), manual = Z.adoptLegacy('meta', {{ meta_forecast: {J(fx.MANUAL)} }}, line),
                      only = Z.adoptLegacy('meta', {{ meta_forecast: {J(fx.BUDGET_ONLY)} }}, line);
        var before = [P.canAutoPull(line, manual), P.canAutoPull(line, only)];
        P.onLine({{ id: 'L1' }}); P._pure.sel().gender = 'male';                                 // the planner picks something in the dock: still nothing to mark
        return JSON.stringify({{ stale: [Z.snapshotStale(manual, line), Z.snapshotStale(only, line)], before: before, after: P.canAutoPull(line, manual) }});""", line=None)
    assert out["stale"] == [[], []]
    assert all(x["ok"] is False and "nothing to repeat" in x["reason"] for x in out["before"])
    assert out["after"]["ok"] is False                                                                   # a workspace with no place yet has none to repeat either


def test_an_old_estimate_that_is_not_an_object_or_has_odd_values_is_adopted_as_nothing_or_without_throwing():
    out = run("""var junk = [null, undefined, 'x', 7, [], {}, { metric_type: 'x', scenarios: 5, potential_audience: 'a', meta_targeting: 'b', months: 'many' }];
                 return JSON.stringify(junk.map(function (j) { try { var s = M.legacy.adopt(j, null); return s === null ? null : [s.v, s.method, s.legacy]; } catch (e) { return 'threw ' + e.message; } }));""", line=None)
    assert out[:5] == [None, None, None, None, None]
    assert all(not str(x).startswith("threw") for x in out)


def test_the_dock_asked_for_an_old_entry_gets_the_snapshot_and_a_newer_native_one_in_the_entry_is_the_one_the_page_prefers():
    out = run(f"""var both = {{ forecast_snapshot: {J(fx.native())}, meta_forecast: {J(fx.LIVE_WITH_LANGUAGES)} }};
        var adopted = Z.adoptLegacy('meta', both, {J(LINE)}), none = Z.adoptLegacy('meta', {{ forecast_snapshot: {J(fx.native())} }}, {J(LINE)});
        return JSON.stringify({{ adopted: !!adopted && adopted.legacy === true && adopted.saved_at, none: none }});""", line=None)
    assert out == {"adopted": "2026-10-26T12:00:00+00:00", "none": None}                                    # adoptLegacy only ever reads the old key; which is newer is the page's call


def test_a_typeahead_shows_its_wait_notes_as_waits_so_a_dropped_search_never_leaves_one_to_be_shown_again():
    """taClose forgets the rows of a wait note (nothing is answering them any more) and the arrow key asks again. A wait note shown with a plain taShow would be
    remembered, and ArrowDown would show 'Searching Meta…' for good (the cancelled-search tests of tests/test_constanza_meta_dom.py run it in a browser)."""
    plain = re.findall(r'taShow\([^()]*\[\{ note: "(?:Searching|Loading)[^"]*" \}\][^()]*\)', MT_CODE)
    assert plain == []
    assert len(re.findall(r"\btaWait\(", MT_CODE)) >= 4                                                  # the place, interest and language searches, and the wait before the first two
    assert "t.wait" in MT_CODE and "askAgain(" in MT_CODE
