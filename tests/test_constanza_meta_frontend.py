"""The Meta panel of Step 05 (app/static/app.js): the three scenarios, the interest / behavior picker, the stale mark, and what "Insert" writes into
Avails. The functions run in a JavaScript engine (Node or macOS JavaScriptCore) against stubs, like the other front-end tests."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")


def _harness():
    spec = importlib.util.spec_from_file_location("_ff_frontend_harness_meta", ROOT / "tests" / "test_full_flight_frontend.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


H = _harness()
FNS = ("escapeHtml", "escapeAttr", "_metaFullFlightRetailBudget", "_metaSelected", "_metaChipsHtml", "_metaPulledFor", "_metaForecastSummaryHtml",
       "_metaFlightHint", "_metaForecastPanelHtml", "_metaTargetResultsHtml", "_metaMarkStale", "onMetaPanelClick", "onMetaInsert")

# what model_auction returns for a lower end of 1,000,000 over 3 months at the $12 Awareness CPM and a $2,000 budget
SNAPSHOT = {
    "metric_type": "meta_audience_scenarios", "product_name": "P", "as_of": "2026-10-26T12:00:00+00:00", "retail_budget": 2000, "media_budget": 1000,
    "retail_cpm": 12, "media_cpm_assumed": 6, "months": 3, "budget_impressions": 166667,
    "potential_audience": {"lower": 1_000_000, "upper": 1_200_000, "source": "meta_api"},
    "scenarios": {"basis": "lower_end", "default": "mid", "multipliers": {"conservative": 4.3, "mid": 5.5, "upper": 8.6}, "months": 3, "est_uniques": 1_000_000,
                  "monthly": {"conservative": 4_300_000, "mid": 5_500_000, "upper": 8_600_000}, "flight": {"conservative": 12_900_000, "mid": 16_500_000, "upper": 25_800_000},
                  "retail_spend_at_flight": {"conservative": 154800, "mid": 198000, "upper": 309600}},
    "meta_targeting": {"geo_type": "geo_market", "geo_key": "803", "geo_name": "Los Angeles · California · US", "age_min": 25, "age_max": 44, "gender": "female",
                       "detailed": [{"id": "6003107902433", "name": "Soccer", "type": "interests"}]},
    "proposal_geo": "Los Angeles", "target_text": "Adults 25-54",
}


def run(snippet: str, snapshot=None, line=None, months=3, geo="Los Angeles", target="Adults 25-54", cpm=12, dated=True, multipliers=None):
    line = line or {"id": "L1", "product_name": "P", "monthly_budget": 2000, "months": 1}
    body = (
        "var console = { warn: function () {}, log: function () {}, error: function () {} };\n"
        f"var state = {{ availsData: {{ L1: {{ meta_forecast: {json.dumps(snapshot)} }} }}, lineItems: [{json.dumps(line)}], productIndex: {{ P: {{ meta_retail_cpm: {cpm} }} }},"
        f" activeTierLabel: 'A', catalog: {{ audience_multipliers: {json.dumps(multipliers or {'conservative': 4.3, 'mid': 5.5, 'upper': 8.6})} }} }};\n"
        f"function _mbParseDate(x) {{ return x ? new Date(x) : null; }}\n"
        f"function _effectiveStartDate() {{ return {json.dumps('2026-11-01' if dated else '')}; }}\nfunction _effectiveEndDate() {{ return {json.dumps('2026-12-31' if dated else '')}; }}\n"
        f"function _effectiveProduct(li, p) {{ return {{ estimated_cpm_for_imps: {cpm} }}; }}\n"
        f"function _constanzaFlightMonths(li) {{ return {months}; }}\n"
        f"function _constanzaGeoFor(lid) {{ return {json.dumps(geo)}; }}\n"
        f"function _effectiveTargetText(li) {{ return {json.dumps(target)}; }}\n"
        "var applied = null;\nfunction applyConstanzaEstimate(lid, imps, uniques, info) { applied = { lid: lid, imps: imps, uniques: uniques, info: info }; return true; }\n"
        + "\n".join(H._extract_fn(APP_JS, n) for n in FNS) + "\n" + snippet
    )
    try:
        return json.loads(H._run_js(body))
    except AssertionError as exc:
        if "JS engine failed" in str(exc) and "osascript" in str(exc).lower():
            pytest.skip(f"JavaScript engine unusable here: {exc}")
        raise


LI = "{ id: 'L1', product_name: 'P', monthly_budget: 2000, months: 1 }"


def test_the_summary_shows_three_scenarios_from_the_lower_end_with_mid_as_the_default():
    html = run(f"return JSON.stringify(_metaForecastSummaryHtml({json.dumps(SNAPSHOT)}, {LI}, {{ meta_retail_cpm: 12 }}));")
    assert "Conservative <small>× 4.3</small>" in html and "Mid · default <small>× 5.5</small>" in html and "Upper <small>× 8.6</small>" in html
    assert "4,300,000" in html and 'class="meta-mid">5,500,000' in html and "8,600,000" in html          # imps per month, mid highlighted
    assert "Flight · 3 mo" in html and "16,500,000" in html and "$198,000" in html
    assert "<strong>1,000,000</strong> – 1,200,000 accounts (from Meta)" in html and "lower end" in html and "frequency reads 5.5" in html
    assert "Pulled for Los Angeles · California · US (TV market), women ages 25–44, any of: Soccer." in html
    assert "166,667 impressions over the 3-month flight" in html and "not avails" in html
    assert "Insert the mid-case (5,500,000 / month) into Avails →" in html and 'data-meta-act="insert"' in html
    assert "Inputs changed" not in html and "Planning scenarios, not a delivery forecast" in html


def test_a_pulled_audience_without_a_stored_name_falls_back_to_the_key_and_a_name_is_escaped():
    bare = {**SNAPSHOT, "meta_targeting": {"geo_type": "zip", "geo_key": "US:90210", "geo_name": None, "age_min": None, "age_max": None, "gender": "all", "detailed": []}}
    html = run(f"return JSON.stringify(_metaForecastSummaryHtml({json.dumps(bare)}, {LI}, {{ meta_retail_cpm: 12 }}));")
    assert "Pulled for Meta geography US:90210 (ZIP)." in html
    evil = {**SNAPSHOT, "meta_targeting": {**SNAPSHOT["meta_targeting"], "geo_name": "<img src=x onerror=alert(1)>", "detailed": [{"id": "1", "name": "\"><script>", "type": "interests"}]}}
    out = run(f"return JSON.stringify(_metaForecastSummaryHtml({json.dumps(evil)}, {LI}, {{ meta_retail_cpm: 12 }}));")
    assert "<img" not in out and "<script>" not in out and "&lt;img" in out


def test_a_control_changed_after_a_calculation_marks_it_stale_and_withdraws_the_insert_button():
    html = run(f"return JSON.stringify(_metaForecastSummaryHtml({json.dumps(SNAPSHOT)}, {LI}, {{ meta_retail_cpm: 12 }}, true));")
    assert "Inputs changed — recalculate" in html and "meta-insert-btn" not in html
    snippet = """
        var feedback = { innerHTML: '' };
        var panel = { dataset: { metaLid: 'L1' }, querySelector: function () { return feedback; } };
        _metaMarkStale(panel);
        return JSON.stringify(feedback.innerHTML);"""
    marked = run(snippet, snapshot=SNAPSHOT)
    assert "Inputs changed — recalculate" in marked and "Insert the mid-case" not in marked
    assert run(snippet, snapshot=None) == ""                                    # nothing calculated yet: nothing to mark


def test_a_manual_entry_says_so():
    manual = {**SNAPSHOT, "potential_audience": {"lower": 500, "upper": 500, "source": "manual"}}
    assert "(entered manually)" in run(f"return JSON.stringify(_metaForecastSummaryHtml({json.dumps(manual)}, {LI}, {{ meta_retail_cpm: 12 }}));")


@pytest.mark.parametrize("change", [
    {"months": 5}, {"geo": "Phoenix"}, {"target": "Adults 18+"}, {"cpm": 18}, {"line": {"id": "L1", "product_name": "P", "monthly_budget": 3000, "months": 1}},
    {"line": {"id": "L1", "product_name": "Q", "monthly_budget": 2000, "months": 1}}])
def test_a_changed_line_marks_the_snapshot_stale_and_withdraws_the_insert_button(change):
    html = run(f"return JSON.stringify(_metaForecastSummaryHtml({json.dumps(SNAPSHOT)}, {LI if 'line' not in change else json.dumps(change['line'])}, {{ meta_retail_cpm: 12 }}));", **{k: v for k, v in change.items() if k != "line"},
               line=change.get("line"))
    assert "Inputs changed — recalculate" in html and "meta-insert-btn" not in html and "Insert the mid-case" not in html


def test_without_an_audience_there_is_only_the_budget_cross_check_and_no_insert():
    bare = {**SNAPSHOT, "potential_audience": None, "scenarios": None}
    html = run(f"return JSON.stringify(_metaForecastSummaryHtml({json.dumps(bare)}, {LI}, {{ meta_retail_cpm: 12 }}));")
    assert "Enter or pull an audience to see the scenarios" in html and "meta-scenarios" not in html and "meta-insert-btn" not in html and "Budget cross-check" in html


def test_insert_writes_the_mid_scenario_and_the_lower_end_audience_into_avails():
    out = run("var r = onMetaInsert('L1'); return JSON.stringify({ r: r, applied: applied });", snapshot=SNAPSHOT)
    assert out["r"] is True and out["applied"]["lid"] == "L1"
    assert out["applied"]["imps"] == 5_500_000 and out["applied"]["uniques"] == 1_000_000            # frequency = 5.5 on the Avails step
    assert out["applied"]["info"] == {"method": "meta_audience", "dmas": ["Los Angeles · California · US"], "unmatched_tokens": [], "win_rate": None, "hispanic_share": None}
    nothing = run("var r = onMetaInsert('L1'); return JSON.stringify({ r: r, applied: applied });", snapshot=None)
    assert nothing == {"r": False, "applied": None}
    bare = run("var r = onMetaInsert('L1'); return JSON.stringify({ r: r, applied: applied });", snapshot={**SNAPSHOT, "scenarios": None})
    assert bare == {"r": False, "applied": None}


def test_the_panel_explains_the_rule_has_no_frequency_or_reach_share_and_asks_for_the_lower_end():
    html = run(f"return JSON.stringify(_metaForecastPanelHtml({LI}, {{ meta_retail_cpm: 12 }}, {{}}));")
    assert "lower end</strong> of Meta's audience × 4.3 (conservative), 5.5 (mid, the default) or 8.6 (upper)" in html
    assert "default placements (Facebook, Instagram, Messenger, Audience Network)" in html and "Comscore markets" in html and "(no flight dates" not in html
    assert "Audience size, lower end" in html and "Upper end (optional)" in html and "over 3 month(s)" in html
    assert "Expected reach share" not in html and "Average frequency" not in html and "meta-frequency" not in html
    assert "Interests &amp; behaviors" in html and 'data-meta-act="search"' in html and 'data-meta-act="browse"' in html
    assert "Meta's own lists" in html and "any of the chosen interests" in html


def test_the_panel_quotes_the_multipliers_the_server_uses_not_a_hard_coded_set():
    html = run(f"return JSON.stringify(_metaForecastPanelHtml({LI}, {{ meta_retail_cpm: 12 }}, {{}}));", multipliers={"conservative": 3, "mid": 6, "upper": 10})
    assert "audience × 3 (conservative), 6 (mid, the default) or 10 (upper)" in html and "4.3" not in html


def test_a_line_without_flight_dates_says_the_one_month_is_a_stand_in():
    html = run(f"return JSON.stringify(_metaForecastPanelHtml({LI}, {{ meta_retail_cpm: 12 }}, {{}}));", dated=False)
    assert "no flight dates: the flight length is not known, so 1 month is assumed" in html


def test_the_age_and_gender_of_the_last_pull_are_restored_when_the_panel_is_drawn_again():
    html = run(f"return JSON.stringify(_metaForecastPanelHtml({LI}, {{ meta_retail_cpm: 12 }}, {{ meta_forecast: {json.dumps(SNAPSHOT)} }}));")
    assert 'class="meta-age-min" type="number" min="18" max="65" placeholder="All" value="25"' in html and 'value="44"' in html and '<option value="female" selected>' in html


def test_chosen_options_are_restored_from_the_snapshot_and_shown_as_chips():
    out = run("return JSON.stringify({ chosen: _metaSelected('L1'), chips: _metaChipsHtml('L1') });", snapshot=SNAPSHOT)
    assert out["chosen"] == [{"id": "6003107902433", "name": "Soccer", "type": "interests"}]
    assert "Soccer" in out["chips"] and 'data-meta-act="remove"' in out["chips"] and "interests" in out["chips"]
    empty = run("return JSON.stringify(_metaChipsHtml('L1'));", snapshot=None)
    assert "None chosen" in empty and "default placements" in empty


def test_results_group_by_list_mark_what_is_chosen_and_keep_names_with_spaces_and_quotes_intact():
    groups = ("[['Interests', [{ id: '1', name: 'Frequent \"travelers\" & <b>x</b>', type: 'interests', path: ['A', 'B'], audience_lower: 2000000 }]],"
              " ['Behaviors', [{ id: '2', name: 'Soccer moms', type: 'behaviors', path: [] }]], ['Demographics', []]]")
    html = run(f"return JSON.stringify(_metaTargetResultsHtml({groups}, [{{ id: '2', type: 'behaviors', name: 'Soccer moms' }}]));")
    assert 'data-name="Frequent &quot;travelers&quot; &amp; &lt;b&gt;x&lt;/b&gt;"' in html and "<b>x</b>" not in html          # escaped, spaces kept (escapeAttr would have made them underscores)
    assert 'data-name="Soccer moms"' in html and 'title="A › B"' in html and "2,000,000+" in html
    assert html.count("cz-ok") == 1 and "Demographics" not in html                                                          # only the chosen one is marked; an empty group is not shown
    assert "Nothing found" in run("return JSON.stringify(_metaTargetResultsHtml([['Interests', []]], []));")


def test_more_than_sixty_hits_say_how_many_were_left_out():
    many = "[['Interests', Array.from({ length: 75 }, (_, i) => ({ id: String(i + 1), name: 'n' + i, type: 'interests', path: [] }))]]"
    html = run(f"return JSON.stringify(_metaTargetResultsHtml({many}, []));")
    assert html.count('data-meta-act="add"') == 60 and "15 more: search to narrow" in html


def test_clicking_a_result_chooses_it_once_and_the_chip_removes_it():
    snippet = """
        var chips = { innerHTML: '' };
        var panel = { dataset: { metaLid: 'L1' }, contains: function () { return true; }, querySelector: function () { return chips; } };
        state.availsData.L1 = state.availsData.L1 || {};
        function click(attrs) { var t = { dataset: attrs, classList: { add: function () {} } }; onMetaPanelClick(panel, { target: { closest: function () { return t; } } }); }
        click({ metaAct: 'add', id: '6003107902433', name: 'Soccer', type: 'interests' });
        click({ metaAct: 'add', id: '6003107902433', name: 'Soccer', type: 'interests' });
        click({ metaAct: 'add', id: '6002714895372', name: 'Frequent travelers', type: 'behaviors' });
        var after = JSON.parse(JSON.stringify(_metaSelected('L1')));
        click({ metaAct: 'remove', i: '0' });
        return JSON.stringify({ after: after, left: _metaSelected('L1'), chips: chips.innerHTML });"""
    out = run(snippet, snapshot=None)
    assert [x["name"] for x in out["after"]] == ["Soccer", "Frequent travelers"]                          # chosen once, however often it is clicked
    assert [x["name"] for x in out["left"]] == ["Frequent travelers"] and "Frequent travelers" in out["chips"] and "Soccer" not in out["chips"]


def test_the_panel_is_wired_in_the_grid_and_loads_no_third_party_script():
    assert 'panel.addEventListener("click", e => onMetaPanelClick(panel, e))' in APP_JS
    assert "/api/constanza/meta-targeting?kind=" in APP_JS and "/api/constanza/meta-targeting?q=" in APP_JS and "/api/constanza/meta-forecast" in APP_JS
    assert ":not([data-meta-input])" in APP_JS                                                           # the panel's own inputs are not number-formatted like Avails cells
