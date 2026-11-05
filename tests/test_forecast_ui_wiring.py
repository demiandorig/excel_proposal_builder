"""The front-end wiring of Admin -> Forecast and of the Step 05 block (what tests/test_constanza_forecast_map.py used to pin as strings, written for
the generic block now): the Avails card asks the product's forecast config - not a family, not a method - whether it gets a Constanza block; the
Meta panel that used to sit in app.js is gone; app.js's Constanza section names no method; and the Admin page draws its method controls from the
server's method list instead of spelling a method out.

String and engine-level checks only (no browser): the behaviour is in tests/test_constanza_step05_block_dom.py and
tests/test_admin_connection_cards_dom.py."""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

from app.services import constanza_methods

ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
ADMIN_JS = (ROOT / "app" / "static" / "admin.js").read_text(encoding="utf-8")
ADMIN_HTML = (ROOT / "app" / "templates" / "admin.html").read_text(encoding="utf-8")
MAIN_PY = (ROOT / "app" / "main.py").read_text(encoding="utf-8")

BEGIN, END = "// CONSTANZA HOST: begin", "// CONSTANZA HOST: end"


def _harness():
    spec = importlib.util.spec_from_file_location("_ff_frontend_harness", ROOT / "tests" / "test_full_flight_frontend.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _registry_keys() -> list[str]:
    constanza_methods.ensure_loaded()
    return list(constanza_methods.keys())


def _host_region() -> str:
    assert APP_JS.count(BEGIN) == 1 and APP_JS.count(END) == 1, "the Constanza section of app.js must be wrapped in its two markers"
    start, end = APP_JS.index(BEGIN), APP_JS.index(END)
    assert start < end
    return APP_JS[start:end]


def _render_avails_grid() -> str:
    return APP_JS.split("function renderAvailsGrid", 1)[1].split("\nfunction ", 1)[0]


# ---------------------------------------------------------------------------------------------------------------------
# Step 05: the config decides, one generic block
# ---------------------------------------------------------------------------------------------------------------------

def test_the_avails_card_reads_the_products_forecast_config_not_a_hard_coded_family():
    card = _render_avails_grid()
    # one generic block for every method that is switched on: the config decides, not a family name and not a platform
    assert "${!isFreeform && _forecastEnabled(p) ? _constanzaBlockHtml(li, p, existing) : \"\"}" in card
    assert 'p.family === "Entravision Plus"' not in card and "meta_retail_cpm" not in card
    helper = APP_JS.split("function _forecastEnabled(p) {", 1)[1].split("\n}\n", 1)[0]
    assert "p.forecast ? !!p.forecast.enabled" in helper and 'p.family === "Entravision Plus"' in helper     # the old rule is only a fallback


def test_forecast_enabled_helper_behaviour():
    h = _harness()
    fn = h._extract_fn(APP_JS, "_forecastEnabled")
    cases = [None, {"family": "Entravision Plus"}, {"family": "Display"}, {"family": "Display", "forecast": {"enabled": True}},
             {"family": "Entravision Plus", "forecast": {"enabled": False}}]
    out = json.loads(h._run_js(fn + "\nreturn JSON.stringify(" + json.dumps(cases) + ".map(_forecastEnabled));"))
    assert out == [False, True, False, True, False]


def test_the_grid_mounts_every_block_through_the_one_function_and_binds_no_platform_panel():
    card = _render_avails_grid()
    assert "_constanzaMountBlocks(grid);" in card
    for gone in ("_metaBind", ".meta-panel", "_metaForecastPanelHtml", "data-meta-input", "meta_retail_cpm"):
        assert gone not in APP_JS, gone
    # the input-formatting handlers no longer carry an exception for a panel that is not there
    assert APP_JS.count(':not([data-key="sov_pct_freeform"])\')') == 2


def test_the_meta_panel_and_its_routes_are_gone_from_the_page_script():
    for gone in ("META PANEL", "state.metaSel", "state.metaStatus", "metaLanguages", "metaCatalog", "meta_forecast", "/api/constanza/meta-forecast",
                 "/api/constanza/meta-geos", "/api/constanza/meta-targeting", "/api/constanza/meta-languages", "/api/constanza/meta/status",
                 "onMetaForecast", "onMetaInsert", "META_GEO_TYPES"):
        assert gone not in APP_JS, gone
    assert not re.search(r"\b_meta[A-Z]\w*\(", APP_JS)


# ---------------------------------------------------------------------------------------------------------------------
# The Constanza section of app.js names no method
# ---------------------------------------------------------------------------------------------------------------------

def test_the_constanza_section_is_marked_and_big_enough_to_be_checked():
    region = _host_region()
    assert len(region.splitlines()) >= 100
    for name in ("_constanzaLineFacts", "_constanzaBlockHtml", "_constanzaMountBlocks", "_constanzaRefreshCard", "_constanzaCardOpts", "onConstanzaAutoCalc",
                 "applyConstanzaEstimate", "_constanzaShowCardError", "renderConstanzaResult", "openConstanzaForLine", "window.constanzaHost"):
        assert name in region, name


def test_the_host_object_has_exactly_the_four_members_constanza_reads():
    body = _host_region().split("window.constanzaHost = {", 1)[1].split("\n};\n", 1)[0]
    members = re.findall(r"^  (\w+):", body, re.M)
    assert members == ["lineFacts", "snapshot", "saveSnapshot", "onDirty"]


def test_no_method_is_named_in_a_code_position_in_the_constanza_section_or_the_grid():
    """The scan of contract C10 (tests/test_constanza_method_contract.py), run here on the real page script: a registry key in a comparison, `.key` on a
    result, `data-key-`, `_keyX`, `/api/constanza/key`, or written out as a string or an object key (`.includes([...])`, `.startsWith(...)`, `{ key: 1 }`) is a
    method hard-coded into the page. The rule set lives in that one file: this test used to carry a weaker copy of it (it missed `method == "key"` and every
    key that was not compared), and two copies drift."""
    from tests.test_constanza_method_contract import check_app_js
    keys = _registry_keys()
    assert keys, "the registry is empty"
    _host_region()                                                    # (the markers are in place and in order)
    problems = check_app_js(APP_JS, keys)
    assert not problems, "\n  - ".join(problems)


def test_that_scan_flags_a_key_planted_in_the_real_host_region_or_the_real_grid_however_it_is_written():
    """The negative twin, on the real file: each way of writing a method into the page that the old checks let through is named, in both places."""
    from tests.test_constanza_method_contract import check_app_js
    keys = _registry_keys()
    key = keys[0]
    grid_open = re.search(r"^(?:async )?function renderAvailsGrid\([^\n]*\{\n", APP_JS, re.M)
    assert grid_open, "renderAvailsGrid not found"
    for planted in (f'if (["{key}", "other"].includes(m)) {{}}', f'if (m.startsWith("{key}")) {{}}', f"const t = ({{ {key}: 1 }})[m];", f'if (method == "{key}") {{}}',
                    f"const names = [`{key}`];"):
        in_host = APP_JS.replace(BEGIN, BEGIN + "\n" + planted, 1)
        in_grid = APP_JS[:grid_open.end()] + planted + "\n" + APP_JS[grid_open.end():]
        for where, source in (("the host region", in_host), ("the grid", in_grid)):
            assert any(f"names the method '{key}' in code" in p for p in check_app_js(source, keys)), (where, planted)
    assert check_app_js(APP_JS, keys) == []                           # (and the real file is the control)


# ---------------------------------------------------------------------------------------------------------------------
# Admin -> Forecast
# ---------------------------------------------------------------------------------------------------------------------

def test_the_admin_page_has_a_forecast_tab_wired_to_the_endpoints():
    assert '<button class="admin-tab" data-tab="forecast">Forecast</button>' in ADMIN_HTML
    for element_id in ("tab-forecast", "fc-summary", "fc-connections", "fc-coverage-body", "fc-search", "fc-family-filter", "fc-only-on", "fc-count", "fc-table", "fc-body"):
        assert f'id="{element_id}"' in ADMIN_HTML, element_id
    for token in ('document.getElementById("tab-forecast")', 'tab === "forecast" && !adminState.forecastLoaded', "wireForecast();",
                  '"/api/admin/forecast-map"', '"/api/admin/forecast-map/reset"', '"/api/admin/forecast-map/add-product"', '"/api/admin/constanza/methods"'):
        assert token in ADMIN_JS, token
    # the routes exist on the server, and are covered by the blanket admin-only rule
    for route in ('@app.get("/api/admin/forecast-map")', '@app.put("/api/admin/forecast-map")', '@app.post("/api/admin/forecast-map/reset")',
                  '@app.post("/api/admin/forecast-map/add-product")', '@app.get("/api/admin/constanza/methods")', '@app.post("/api/admin/constanza/{key}/test")'):
        assert route in MAIN_PY, route


def test_the_admin_page_spells_out_no_method_in_an_option_an_id_or_a_card():
    """The method select, the summary cards and the connection cards are drawn from the server's method list, so a new method needs no edit here."""
    for key in _registry_keys():
        for where, text in (("admin.html", ADMIN_HTML), ("admin.js", ADMIN_JS)):
            assert f'value="{key}"' not in text, f"{where} offers {key} as a literal option"
            assert not re.search(rf"fc-{re.escape(key)}\b|fc-conn-{re.escape(key)}-", text), f"{where} spells out an element for {key}"
    assert "fc-tt" not in ADMIN_HTML and "fc-tt" not in ADMIN_JS
    assert 'id="fc-connections"' in ADMIN_HTML and "_apiMethods()" in ADMIN_JS and "_forecastMethods()" in ADMIN_JS


def test_the_admin_page_lets_the_server_decide_which_methods_exist():
    # every select option and every summary card comes from `methods`; the only method names in the script are the two built-in data methods' older names
    assert "_forecastMethods().map(m =>" in ADMIN_JS
    for line in ADMIN_JS.splitlines():
        if "TikTok API" in line or "Meta API" in line:
            assert line.strip().startswith("//"), line
