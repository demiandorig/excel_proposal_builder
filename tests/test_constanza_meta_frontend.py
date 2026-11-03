"""The Meta panel of Step 05 (app/static/app.js): what it asks Meta for, the words it uses, the saved snapshot and the stale mark, the result, and
what "Insert" writes into Avails. The code between the "META PANEL: begin / end" markers of app.js runs in a JavaScript engine (Node or macOS
JavaScriptCore) against stubs, like the other front-end tests; the panel working on a real page (typeaheads, the browse window, focus) is
tests/test_constanza_meta_dom.py."""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
REGION = APP_JS[APP_JS.index("// META PANEL: begin"):APP_JS.index("// META PANEL: end")]


def _harness():
    spec = importlib.util.spec_from_file_location("_ff_frontend_harness_meta", ROOT / "tests" / "test_full_flight_frontend.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


H = _harness()
OUTSIDE = ("escapeHtml", "escapeAttr", "_targetsHispanic", "_constanzaErrorText")          # the region's few neighbours in app.js (the real ones: text, a line's target, a server message)

# what model_auction returns for a lower end of 1,000,000 over 3 months at the $12 Awareness CPM and a $2,000 budget
SNAPSHOT = {
    "metric_type": "meta_audience_scenarios", "product_name": "P", "as_of": "2026-10-26T12:00:00+00:00", "retail_budget": 2000, "media_budget": 1000,
    "retail_cpm": 12, "media_cpm_assumed": 6, "months": 3, "budget_impressions": 166667,
    "potential_audience": {"lower": 1_000_000, "upper": 1_200_000, "source": "meta_api"},
    "scenarios": {"basis": "lower_end", "default": "mid", "multipliers": {"conservative": 4.3, "mid": 5.5, "upper": 8.6}, "months": 3, "est_uniques": 1_000_000,
                  "monthly": {"conservative": 4_300_000, "mid": 5_500_000, "upper": 8_600_000}, "flight": {"conservative": 12_900_000, "mid": 16_500_000, "upper": 25_800_000},
                  "retail_spend_at_flight": {"conservative": 154800, "mid": 198000, "upper": 309600.5}},
    "meta_targeting": {"geo_type": "geo_market", "geo_key": "803", "geo_name": "Los Angeles · California · US", "age_min": 25, "age_max": 44, "gender": "female",
                       "detailed": [{"id": "6003107902433", "name": "Soccer", "type": "interests"}],
                       "languages": [{"id": "1001", "name": "Spanish"}, {"id": "1002", "name": "Spanish (Mexico)"}]},
    "proposal_geo": "Los Angeles", "target_text": "Adults 25-54",
}
LI = "{ id: 'L1', product_name: 'P', monthly_budget: 2000, months: 1 }"
P = "{ meta_retail_cpm: 12 }"


def run(snippet: str, snapshot=None, line=None, months=3, geo="Los Angeles", target="Adults 25-54", cpm=12, dated=True, multipliers=None, hispanic=False,
        status="undefined", catalog_note=None):
    line = line or {"id": "L1", "product_name": "P", "monthly_budget": 2000, "months": 1}
    body = (
        "var console = { warn: function () {}, log: function () {}, error: function () {} };\n"
        f"var state = {{ availsData: {{ L1: {{ meta_forecast: {json.dumps(snapshot)} }} }}, lineItems: [{json.dumps(line)}], productIndex: {{ P: {{ meta_retail_cpm: {cpm} }} }},"
        f" activeTierLabel: 'A', metaStatus: {status}, catalog: {{ audience_multipliers: {json.dumps(multipliers or {'conservative': 4.3, 'mid': 5.5, 'upper': 8.6})} }} }};\n"
        f"function _mbParseDate(x) {{ return x ? new Date(x) : null; }}\n"
        f"function _effectiveStartDate() {{ return {json.dumps('2026-11-01' if dated else '')}; }}\nfunction _effectiveEndDate() {{ return {json.dumps('2026-12-31' if dated else '')}; }}\n"
        f"function _effectiveProduct(li, p) {{ return {{ estimated_cpm_for_imps: {cpm} }}; }}\n"
        f"function _constanzaFlightMonths(li) {{ return {months}; }}\n"
        f"function _constanzaGeoFor(lid) {{ return {json.dumps(geo)}; }}\n"
        f"function _effectiveTargetText(li) {{ return {json.dumps(target)}; }}\n"
        f"function _targetsHispanic(li) {{ return {json.dumps(hispanic)}; }}\n"
        "var applied = null;\nfunction applyConstanzaEstimate(lid, imps, uniques, info) { applied = { lid: lid, imps: imps, uniques: uniques, info: info }; return true; }\n"
        + "\n".join(H._extract_fn(APP_JS, n) for n in OUTSIDE if n != "_targetsHispanic") + "\n" + REGION + "\n" + snippet
    )
    try:
        return json.loads(H._run_js(body))
    except AssertionError as exc:
        if "JS engine failed" in str(exc) and "osascript" in str(exc).lower():
            pytest.skip(f"JavaScript engine unusable here: {exc}")
        raise


def summary_html(snapshot, line=LI, forced="undefined"):
    return run(f"return JSON.stringify(_metaForecastSummaryHtml({json.dumps(snapshot)}, {line}, {P}, {forced}));", snapshot=snapshot)


# ----------------------------------------------------------------------------------------------------------------- the result

def test_the_summary_shows_the_lower_end_headline_three_scenarios_and_mid_as_the_default():
    html = summary_html(SNAPSHOT)
    assert '<strong>1,000,000</strong><span>accounts at the lower end of Meta’s audience</span>' in html
    assert "Meta’s range (lower – upper end): <strong>1,000,000 – 1,200,000</strong> accounts (from Meta)." in html
    assert "Conservative <small>× 4.3</small>" in html and "Mid · default <small>× 5.5</small>" in html and "Upper <small>× 8.6</small>" in html
    assert "<strong>4,300,000</strong>" in html and '<div class="meta-card meta-mid">' in html and "<strong>8,600,000</strong>" in html
    assert html.count('class="meta-card meta-mid"') == 1 and html.count("meta-card") >= 3                        # one highlighted: the default
    assert "Flight · 3 mo</dt><dd>16,500,000" in html and "$198,000" in html and "$309,600.50" in html           # whole dollars plain, cents with two digits
    assert "its frequency reads 5.5" in html and "lower end</strong>: it is the est. uniques" in html
    assert "<strong>Pulled for</strong> Los Angeles · California · US (TV market), women ages 25–44, language set to Spanish, any of: Soccer." in html
    assert "166,667 impressions over the 3-month flight" in html and "not avails" in html
    assert "Insert the mid-case (5,500,000 / month) into Avails →" in html and 'data-meta-act="insert"' in html and "meta-insert-btn" in html
    assert "Inputs changed" not in html and "Planning scenarios, not a delivery forecast · 2026-10-26" in html


def test_a_language_is_never_said_to_be_spoken_it_is_what_people_have_set():
    html = summary_html(SNAPSHOT)
    assert "language set to Spanish" in html and "not who speaks it" in html and "fewer than all speakers" in html
    none = {**SNAPSHOT, "meta_targeting": {**SNAPSHOT["meta_targeting"], "languages": []}}
    assert "language" not in summary_html(none).replace("The language is", "")                                      # no language asked: no language sentence at all
    assert "fewer than all speakers" not in summary_html(none)
    old = {**SNAPSHOT, "meta_targeting": {k: v for k, v in SNAPSHOT["meta_targeting"].items() if k != "languages"}}        # saved before languages existed
    assert "Pulled for" in summary_html(old) and "language" not in summary_html(old).replace("The language is", "")


def test_a_pulled_audience_without_a_stored_name_falls_back_to_the_key_and_every_name_is_escaped():
    bare = {**SNAPSHOT, "meta_targeting": {"geo_type": "zip", "geo_key": "US:90210", "geo_name": None, "age_min": None, "age_max": None, "gender": "all", "detailed": [], "languages": []}}
    assert "Pulled for</strong> Meta geography US:90210 (ZIP)." in summary_html(bare)
    evil = {**SNAPSHOT, "meta_targeting": {**SNAPSHOT["meta_targeting"], "geo_name": "<img src=x onerror=alert(1)>", "detailed": [{"id": "1", "name": "\"><script>", "type": "interests"}],
                                         "languages": [{"id": "1001", "name": "<i>Spanish</i>"}]}}
    out = summary_html(evil)
    assert "<img" not in out and "<script>" not in out and "<i>" not in out and "&lt;img" in out and "&lt;i&gt;Spanish" in out


def test_the_languages_of_a_pull_are_named_by_family_unless_only_one_version_was_chosen():
    out = run("""return JSON.stringify([
        _metaPulledFor({ meta_targeting: { geo_type: 'city', geo_key: '1', geo_name: 'Phoenix', gender: 'all', languages: [{ id: '1', name: 'Spanish' }, { id: '2', name: 'Spanish (Mexico)' }, { id: '3', name: 'English (US)' }] } }),
        _metaPulledFor({ meta_targeting: { geo_type: 'city', geo_key: '1', geo_name: 'Phoenix', gender: 'all', languages: [{ id: '2', name: 'Spanish (Mexico)' }] } })]);""")
    assert out[0] == "Phoenix (city), language set to Spanish or English (US)"
    assert out[1] == "Phoenix (city), language set to Spanish (Mexico)"


def test_a_manual_snapshot_says_entered_manually_and_is_not_marked_by_choices():
    manual = {**SNAPSHOT, "potential_audience": {"lower": 500, "upper": 500, "source": "manual"}}
    del manual["meta_targeting"]
    html = summary_html(manual)
    assert "<strong>500</strong> accounts (entered manually)." in html and "(from Meta)" not in html and "Pulled for" not in html and "Inputs changed" not in html
    assert "Insert the mid-case" in html
    changed = run(f"var sel = _metaSel('L1'); sel.gender = 'male'; return JSON.stringify(_metaForecastSummaryHtml({json.dumps(manual)}, {LI}, {P}));", snapshot=manual)
    assert "Inputs changed" not in changed                              # a typed figure answers none of the choices


def test_a_forced_stale_mark_withdraws_the_insert_button():
    html = summary_html(SNAPSHOT, forced="true")
    assert "Inputs changed — recalculate" in html and "meta-insert-btn" not in html and "Insert the mid-case" not in html and "is-stale" in html


@pytest.mark.parametrize("change", [
    {"months": 5}, {"geo": "Phoenix"}, {"target": "Adults 18+"}, {"cpm": 18}, {"line": {"id": "L1", "product_name": "P", "monthly_budget": 3000, "months": 1}},
    {"line": {"id": "L1", "product_name": "Q", "monthly_budget": 2000, "months": 1}}])
def test_a_changed_line_marks_the_snapshot_stale_and_withdraws_the_insert_button(change):
    line = json.dumps(change["line"]) if "line" in change else LI
    html = run(f"return JSON.stringify(_metaForecastSummaryHtml({json.dumps(SNAPSHOT)}, {line}, {P}));", snapshot=SNAPSHOT,
               **{k: v for k, v in change.items() if k != "line"}, line=change.get("line"))
    assert "Inputs changed — recalculate" in html and "meta-insert-btn" not in html and "Insert the mid-case" not in html


@pytest.mark.parametrize("edit", ["sel.gender = 'male'", "sel.ageMin = 30", "sel.ageMax = 50", "sel.geo = null", "sel.geo.key = '804'", "sel.languages = []",
                                  "sel.languages.push({ id: '6', name: 'English (US)' })", "sel.detailed = []", "sel.detailed.push({ id: '2', name: 'x', type: 'behaviors' })"])
def test_changing_a_choice_after_the_estimate_marks_it_stale_and_putting_it_back_does_not(edit):
    snippet = f"""
        var sel = _metaSel('L1');
        var before = _metaForecastSummaryHtml(state.availsData.L1.meta_forecast, state.lineItems[0], state.productIndex.P);
        {edit};
        var after = _metaForecastSummaryHtml(state.availsData.L1.meta_forecast, state.lineItems[0], state.productIndex.P);
        state.metaSel = {{}}; delete state.metaSel.L1;
        var restored = _metaForecastSummaryHtml(state.availsData.L1.meta_forecast, state.lineItems[0], state.productIndex.P);
        return JSON.stringify({{ before: before, after: after, restored: restored }});"""
    out = run(snippet, snapshot=SNAPSHOT)
    assert "Inputs changed" not in out["before"] and "meta-insert-btn" in out["before"]
    assert "Inputs changed — recalculate" in out["after"] and "meta-insert-btn" not in out["after"]
    assert "Inputs changed" not in out["restored"]


def test_the_mark_is_redrawn_into_the_panel_and_nothing_calculated_yet_has_nothing_to_mark():
    snippet = """
        var feedback = { innerHTML: '' };
        var panel = { dataset: { metaLid: 'L1' }, querySelector: function () { return feedback; } };
        _metaSel('L1').gender = 'male';
        _metaMarkStale(panel);
        return JSON.stringify(feedback.innerHTML);"""
    assert "Inputs changed — recalculate" in run(snippet, snapshot=SNAPSHOT) and "Insert the mid-case" not in run(snippet, snapshot=SNAPSHOT)
    assert run(snippet, snapshot=None) == ""


def test_without_an_audience_there_is_only_the_budget_cross_check_and_no_insert():
    bare = {**SNAPSHOT, "potential_audience": None, "scenarios": None}
    html = summary_html(bare)
    assert "Estimate an audience to see the scenarios" in html and "meta-scen" not in html and "meta-insert-btn" not in html and "Budget cross-check" in html


def test_insert_writes_the_mid_scenario_and_the_lower_end_audience_into_avails():
    out = run("var r = onMetaInsert('L1'); return JSON.stringify({ r: r, applied: applied });", snapshot=SNAPSHOT)
    assert out["r"] is True and out["applied"]["lid"] == "L1"
    assert out["applied"]["imps"] == 5_500_000 and out["applied"]["uniques"] == 1_000_000            # frequency = 5.5 on the Avails step
    assert out["applied"]["info"] == {"method": "meta_audience", "dmas": ["Los Angeles · California · US"], "unmatched_tokens": [], "win_rate": None, "hispanic_share": None}
    assert run("var r = onMetaInsert('L1'); return JSON.stringify({ r: r, applied: applied });", snapshot=None) == {"r": False, "applied": None}
    assert run("var r = onMetaInsert('L1'); return JSON.stringify({ r: r, applied: applied });", snapshot={**SNAPSHOT, "scenarios": None}) == {"r": False, "applied": None}


# ----------------------------------------------------------------------------------------------------------------- what is chosen

def test_a_saved_snapshot_restores_geography_age_gender_languages_and_interests():
    sel = run("return JSON.stringify(_metaSel('L1'));", snapshot=SNAPSHOT)
    assert sel["geoType"] == "geo_market" and sel["geo"] == {"type": "geo_market", "key": "803", "name": "Los Angeles", "region": "California", "country": "US",
                                                             "label": "Los Angeles · California · US"}
    assert (sel["ageMin"], sel["ageMax"], sel["gender"]) == (25, 44, "female")
    assert sel["languages"] == [{"id": "1001", "name": "Spanish"}, {"id": "1002", "name": "Spanish (Mexico)"}]
    assert sel["detailed"] == [{"id": "6003107902433", "name": "Soccer", "type": "interests"}]


def test_with_no_snapshot_or_a_manual_one_the_choices_are_everyone_and_no_place_yet():
    nothing = run("return JSON.stringify(_metaSel('L1'));", snapshot=None)
    assert nothing == {"geoType": "geo_market", "geo": None, "ageMin": 18, "ageMax": 65, "gender": "all", "languages": [], "detailed": []}
    manual = {**SNAPSHOT, "potential_audience": {"lower": 5, "upper": 5, "source": "manual"}}
    del manual["meta_targeting"]
    assert run("return JSON.stringify(_metaSel('L1'));", snapshot=manual) == nothing


def test_a_snapshot_with_no_age_means_all_ages_and_with_odd_values_stays_inside_meta_limits():
    t = {**SNAPSHOT["meta_targeting"], "age_min": None, "age_max": None, "gender": None, "geo_name": None}
    sel = run("return JSON.stringify(_metaSel('L1'));", snapshot={**SNAPSHOT, "meta_targeting": t})
    assert (sel["ageMin"], sel["ageMax"], sel["gender"]) == (18, 65, "all") and sel["geo"]["name"] == "Meta geography 803"
    t = {**SNAPSHOT["meta_targeting"], "age_min": 12, "age_max": 90}
    odd = run("return JSON.stringify(_metaSel('L1'));", snapshot={**SNAPSHOT, "meta_targeting": t})
    assert (odd["ageMin"], odd["ageMax"]) == (18, 65)


def test_the_choices_are_kept_per_line_for_the_session():
    out = run("""var a = _metaSel('L1'); a.gender = 'male'; var b = _metaSel('L2');
                 return JSON.stringify({ same: _metaSel('L1') === a, a: _metaSel('L1').gender, b: b.gender });""", snapshot=SNAPSHOT)
    assert out == {"same": True, "a": "male", "b": "all"}


def test_each_section_has_a_live_summary_and_a_count():
    out = run("""var sel = _metaSel('L1'); var s = _metaSummaries(sel);
                 var empty = _metaSummaries(_metaSelFromSnapshot(null));
                 sel.detailed = [1, 2, 3].map(function (i) { return { id: String(i), name: 'Pick ' + i, type: 'interests' }; });
                 return JSON.stringify({ s: s, empty: empty, many: _metaSummaries(sel).aud });""", snapshot=SNAPSHOT)
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
    assert run(f"return JSON.stringify(_metaAgeText({lo}, {hi}));") == text


def test_languages_group_by_family_so_a_quick_pick_is_one_chip():
    out = run("""return JSON.stringify(_metaLangFamilies([{ id: '1', name: 'Spanish' }, { id: '2', name: 'English (US)' }, { id: '3', name: 'Spanish (Spain)' },
                 { id: '4', name: 'Portuguese (Brazil)' }]).map(function (g) { return [g.label, g.items.length, g.key]; }));""")
    assert out == [["Spanish", 2, "spanish"], ["English (US)", 1, "english"], ["Portuguese (Brazil)", 1, "portuguese"]]
    assert run("return JSON.stringify([_metaLangStarts('Español', 'espanol'), _metaLangStarts('Spanish (Mexico)', 'Spanish'), _metaLangStarts('Portuguese', 'Spanish')]);") == [True, True, False]


def test_the_quick_picks_name_languages_to_look_up_and_carry_no_locale_id():
    quick = run("return JSON.stringify(META_LANG_QUICK);")
    assert [q["key"] for q in quick] == ["spanish", "english", "both"] and quick[0]["label"] == "Spanish speakers" and quick[0]["words"] == ["Spanish"]
    assert quick[2]["words"] == ["Spanish", "English"]
    # no locale number anywhere in the panel code: Meta's own list is the only source. The only 3+ digit numbers are these known ones (a maxlength,
    # the server's label limit, the search delay, Meta's list size, the status cache and a ZIP in a placeholder)
    code = re.sub(r"//[^\n]*", "", REGION)
    numbers = set(re.findall(r"(?<![\w.])\d{3,}(?![\w.])", code))
    assert numbers <= {"100", "160", "300", "1000", "30000", "90210"}, numbers
    assert "locales" not in code                                              # the server builds the spec; the panel only sends [{id, name}]


def test_adding_languages_skips_what_is_chosen_and_stops_at_the_limit():
    out = run("""var sel = { languages: [{ id: '1', name: 'A' }] };
                 var first = _metaAddLanguages(sel, [{ id: '1', name: 'A' }, { id: '2', name: 'B' }]);
                 var many = Array.from({ length: 40 }, function (_, i) { return { id: String(100 + i), name: 'L' + i }; });
                 var capped = _metaAddLanguages(sel, many);
                 return JSON.stringify({ first: first, capped: capped, n: sel.languages.length, max: META_MAX_LANGUAGES, limit: META_MAX_DETAILED });""")
    assert out == {"first": False, "capped": True, "n": 25, "max": 25, "limit": 50}


# ----------------------------------------------------------------------------------------------------------------- the request

def test_the_request_carries_the_geography_age_gender_interests_and_languages_and_no_manual_audience():
    body = run("""var li = state.lineItems[0]; return JSON.stringify(_metaRequestBody(li, state.productIndex.P, _metaSel('L1')));""", snapshot=SNAPSHOT)
    assert body == {
        "product_name": "P", "retail_budget": 2000, "retail_cpm": 12, "months": 3,
        "meta_geo_type": "geo_market", "meta_geo_key": "803", "meta_geo_label": "Los Angeles · California · US",
        "age_min": 25, "age_max": 44, "gender": "female",
        "meta_targeting": [{"id": "6003107902433", "name": "Soccer", "type": "interests"}],
        "meta_languages": [{"id": "1001", "name": "Spanish"}, {"id": "1002", "name": "Spanish (Mexico)"}],
    }
    assert "audience_lower" not in body and "audience_upper" not in body


def test_age_is_sent_only_when_it_narrows_and_the_label_fits_the_servers_limit():
    out = run("""var sel = _metaSel('L1'); var li = state.lineItems[0];
                 sel.ageMin = 18; sel.ageMax = 65; sel.gender = 'all'; sel.languages = []; sel.detailed = [];
                 var all = _metaRequestBody(li, state.productIndex.P, sel);
                 sel.ageMin = 65; sel.ageMax = 65; sel.geo.label = 'x'.repeat(300);
                 var old = _metaRequestBody(li, state.productIndex.P, sel);
                 sel.ageMin = 18; sel.ageMax = 30; sel.geo.label = '';
                 var young = _metaRequestBody(li, state.productIndex.P, sel);
                 return JSON.stringify({ all: all, old: old, young: young });""", snapshot=SNAPSHOT)
    assert (out["all"]["age_min"], out["all"]["age_max"], out["all"]["gender"], out["all"]["meta_languages"], out["all"]["meta_targeting"]) == (None, None, "all", [], [])
    assert (out["old"]["age_min"], out["old"]["age_max"]) == (65, None) and len(out["old"]["meta_geo_label"]) == 160
    assert (out["young"]["age_min"], out["young"]["age_max"], out["young"]["meta_geo_label"]) == (None, 30, "Los Angeles")


def test_a_line_that_is_a_period_budget_sends_the_whole_flights_retail_budget():
    body = run("""return JSON.stringify(_metaRequestBody(state.lineItems[0], state.productIndex.P, _metaSel('L1')));""", snapshot=SNAPSHOT,
               line={"id": "L1", "product_name": "P", "monthly_budget": 1500, "months": 4})
    assert body["retail_budget"] == 6000


# ----------------------------------------------------------------------------------------------------------------- the connection banner

def test_the_banner_asks_connects_names_what_is_missing_or_fails():
    states = json.dumps({
        "loading": None,
        "connected": {"data": {"configured": True, "account": "…4821", "version": "v26.0", "missing": []}},
        "off": {"data": {"configured": False, "missing": ["META_ACCESS_TOKEN", "META_AD_ACCOUNT_ID (it must be the numeric ad account ID)"], "setup_hint": "Add META_ACCESS_TOKEN <b>now</b>."}},
        "error": {"data": None, "error": "The server <i>stopped</i>."},
    })
    out = run(f"var s = {states}; var o = {{}}; Object.keys(s).forEach(function (k) {{ o[k] = _metaStatusHtml(s[k]); }}); return JSON.stringify(o);")
    assert "Checking the connection to Meta" in out["loading"]
    assert "aud-notice--connected" in out["connected"] and "Connected to Meta" in out["connected"] and "<code>…4821</code>" in out["connected"] and "API v26.0" in out["connected"]
    assert "aud-notice--warn" in out["off"] and "Meta isn’t connected yet" in out["off"] and "<code>META_ACCESS_TOKEN</code>" in out["off"]
    assert "<code>META_AD_ACCOUNT_ID</code> (it must be the numeric ad account ID)" in out["off"] and "How an admin sets this up" in out["off"]
    assert "<b>" not in out["off"] and "&lt;b&gt;now" in out["off"]                       # the hint is text
    assert "Couldn’t check the Meta connection" in out["error"] and 'data-meta-act="recheck"' in out["error"] and "<i>" not in out["error"] and "&lt;i&gt;stopped" in out["error"]


def test_only_a_known_not_configured_answer_switches_the_estimate_off_and_the_reason_names_the_settings():
    out = run("""return JSON.stringify([_metaConnected(undefined), _metaConnected({ data: null, error: 'x' }), _metaConnected({ data: { configured: true } }), _metaConnected({ data: { configured: false } }),
        _metaOffReason({ data: { missing: ['META_ACCESS_TOKEN', 'META_AD_ACCOUNT_ID (it must be numeric)'] } }), _metaOffReason({ data: { missing: [] } })]);""")
    assert out[:4] == [None, None, True, False]
    assert out[4] == "Meta isn't connected yet: an admin needs to add META_ACCESS_TOKEN and META_AD_ACCOUNT_ID in the app's secrets."
    assert out[5] == "Meta isn't connected yet: an admin needs to add the Meta settings in the app's secrets."


def test_the_language_caveat_prefers_the_servers_text_and_shows_its_first_sentence():
    out = run("""var note = 'Meta says A. Then B. Then C.';
        var a = _metaLanguageNote(); state.metaLanguageNote = 'From the list.'; var b = _metaLanguageNote();
        state.metaStatus = { data: { language_note: note } }; var c = _metaLanguageNote();
        return JSON.stringify({ a: a, b: b, c: c, short: _metaShortNote(note), plain: _metaShortNote('No full stop') });""")
    assert out["a"] == run("return JSON.stringify(META_LANGUAGE_NOTE);") and out["b"] == "From the list." and out["c"] == "Meta says A. Then B. Then C."
    assert out["short"] == "Meta says A." and out["plain"] == "No full stop"
    assert "not a count of who speaks it" in out["a"]


# ----------------------------------------------------------------------------------------------------------------- the panel markup

def panel_html(**kw):
    return run(f"return JSON.stringify(_metaForecastPanelHtml({LI}, {P}, state.availsData.L1 || {{}}));", **kw)


def test_the_panel_has_four_sections_a_plan_strip_and_no_manual_audience_form():
    html = panel_html(snapshot=None)
    for key, title in (("where", "Where"), ("who", "Who"), ("lang", "Language"), ("aud", "Audience")):
        assert f'data-meta-sec="{key}"' in html and f'>{title}</h4>' in html
    assert html.index(">Where</h4>") < html.index(">Who</h4>") < html.index(">Language</h4>") < html.index(">Audience</h4>")
    assert html.count('class="aud-step"') == 4 and 'class="aud-builder meta-builder"' in html
    assert "lower end × 4.3 / 5.5 / 8.6" in html and "over 3 months" in html and "Meta’s defaults" in html and "Retail CPM" in html
    assert "default placements (Facebook, Instagram, Messenger, Audience Network)" in html
    assert ">Estimate audience</button>" in html and "Pull audience and calculate" not in html
    for gone in ("Calculate scenarios", "Audience size, lower end", "Upper end (optional)", "meta-audience", "meta-model-btn", "meta-live-details", "<details class=\"meta-live", 'type="number"'):
        assert gone not in html, gone
    assert html.count("<details") == 1                                                 # only the language caveat folds


def test_the_panel_quotes_the_multipliers_the_server_uses_not_a_hard_coded_set():
    html = panel_html(snapshot=None, multipliers={"conservative": 3, "mid": 6, "upper": 10})
    assert "lower end × 3 / 6 / 10" in html and "audience × 3 (conservative), 6 (mid, the default) or 10 (upper)" in html and "4.3" not in html


def test_a_line_without_flight_dates_says_the_one_month_is_a_stand_in():
    html = panel_html(snapshot=None, dated=False, months=1)
    assert "No flight dates on this option: the flight length is not known, so 1 month is assumed." in html and "over 1 month<" in html
    assert "No flight dates" not in panel_html(snapshot=None)


def test_every_text_input_opts_out_of_the_avails_number_formatting_and_ids_are_unique_per_line():
    html = panel_html(snapshot=SNAPSHOT)
    inputs = re.findall(r"<input[^>]*>", html)
    assert len(inputs) == 3 and all("data-meta-input" in i and 'type="text"' in i and 'role="combobox"' in i for i in inputs)
    assert ":not([data-meta-input])" in APP_JS                                         # the grid's number-formatting handlers skip them
    other = run("return JSON.stringify(_metaForecastPanelHtml({ id: 'L2', product_name: 'P', monthly_budget: 1, months: 1 }, state.productIndex.P, {}));", snapshot=SNAPSHOT)
    ids = re.findall(r' id="([^"]+)"', html) + re.findall(r' id="([^"]+)"', other)
    assert len(ids) == len(set(ids)) and len(ids) > 10


def test_the_panel_shows_what_was_asked_when_a_proposal_is_reopened():
    html = panel_html(snapshot=SNAPSHOT)
    assert "Los Angeles" in html and "TV market · California" in html and 'aria-pressed="true">Women<' in html and '<option value="25" selected>25</option>' in html
    assert '<option value="44" selected>44</option>' in html and ">Spanish</span><span class=\"aud-chip-meta\">2 versions</span>" in html and "Soccer" in html
    assert 'aria-pressed="true">Spanish speakers<' in html and 'aria-pressed="false">English<' in html and 'aria-pressed="true" >' not in html
    assert "Inputs changed" not in html and "meta-insert-btn" in html                  # the saved result is still current


def test_the_hispanic_offer_is_in_the_markup_only_for_a_line_that_targets_hispanic_audiences():
    assert "This line targets Hispanic audiences — add Spanish?" in panel_html(snapshot=None, hispanic=True)
    assert "This line targets Hispanic audiences" not in panel_html(snapshot=None, hispanic=False)
    spanish = panel_html(snapshot=SNAPSHOT, hispanic=True)                              # Spanish already chosen: drawn, but hidden
    assert re.search(r'data-meta-suggest hidden>', spanish)
    assert re.search(r'data-meta-suggest>', panel_html(snapshot=None, hispanic=True))


def test_the_place_pills_come_from_the_proposals_geography_and_are_never_searched_by_the_markup():
    out = run("""return JSON.stringify(['Phoenix, AZ and Los Angeles DMA', 'Los Angeles', 'TBD', 'National', '', 'AZ, CA, TX', 'Austin (Texas); Austin, TX; Dallas - Fort Worth DMA; Houston',
        'x'.repeat(80)].map(_metaGeoSuggestions));""")
    assert out == [["Phoenix", "Los Angeles"], ["Los Angeles"], [], [], [], [], ["Austin", "Dallas - Fort Worth", "Houston"], []]
    html = panel_html(snapshot=None)
    assert 'data-q="Los Angeles"' in html and "Search “Los Angeles”" in html
    assert "fetch(" not in _fn_source("_metaForecastPanelHtml") and "fetch(" not in _fn_source("_metaWhereBodyHtml")          # drawing the panel never asks Meta


def test_a_place_pill_is_hidden_once_a_place_is_chosen_and_the_language_note_is_in_the_markup():
    html = panel_html(snapshot=SNAPSHOT)
    assert re.search(r"data-meta-geosuggest hidden", html)
    assert "Meta&#039;s languages are the language people have set Facebook or Instagram to" in html and "data-meta-langfull" in html


def test_a_name_in_the_chips_is_escaped():
    evil = {**SNAPSHOT, "meta_targeting": {**SNAPSHOT["meta_targeting"], "geo_name": "<img src=x> · <b>CA</b>", "detailed": [{"id": "1", "name": "\"><script>x</script>", "type": "interests"}],
                                         "languages": [{"id": "1001", "name": "<u>Spanish</u>"}]}}
    html = panel_html(snapshot=evil)
    assert "<img" not in html and "<script>" not in html and "<u>" not in html and "<b>CA" not in html


# ----------------------------------------------------------------------------------------------------------------- small helpers

def test_highlight_marks_the_typed_words_and_escapes_everything():
    out = run("""return JSON.stringify([_metaHighlight('Los Angeles-Long Beach', 'los beach'), _metaHighlight('<b>x</b> & y', 'x'), _metaHighlight('Plain', ''), _metaHighlight('aaa', 'aa a')]);""")
    assert out[0] == "<mark>Los</mark> Angeles-Long <mark>Beach</mark>" and out[1] == "&lt;b&gt;<mark>x</mark>&lt;/b&gt; &amp; y"
    assert out[2] == "Plain" and out[3] == "<mark>aaa</mark>"


def test_audience_sizes_read_short_with_the_range_when_there_is_one():
    out = run("return JSON.stringify([_metaSizeText(120000000, 140000000), _metaSizeText(900, 900), _metaSizeText(2000000, null), _metaSizeText(null, 5), _metaSizeText(999999, 1500000), _metaSizeText(15000, 20000)]);")
    assert out == ["120M – 140M", "900", "2M", "", "1M – 1.5M", "15K – 20K"]


def test_a_demographic_kind_reads_in_words():
    out = run("return JSON.stringify([_metaKindLabel('interests'), _metaKindLabel('life_events'), _metaKindLabel('home_value'), _metaKindLabel('something_new'), _metaTabOf('behaviors'), _metaTabOf('life_events'), _metaTabOf('interests')]);")
    assert out == ["Interest", "Life event", "Home value", "something new", "behaviors", "demographics", "interests"]


# ----------------------------------------------------------------------------------------------------------------- the wiring in app.js

def _fn_source(name: str) -> str:
    return H._extract_fn(APP_JS, name)


def _grid_source() -> str:
    start = APP_JS.index("function renderAvailsGrid() {")
    return APP_JS[start:APP_JS.index("\n}\n", start)]


def test_the_grid_builds_the_panel_and_binds_each_one_and_the_old_manual_code_is_gone():
    grid = _grid_source()
    assert "_metaForecastPanelHtml(li, p, existing)" in grid and 'grid.querySelectorAll(".meta-panel").forEach(_metaBind)' in grid
    for gone in ("meta-plan-block", "meta-model-btn", "meta-pull-btn", "meta-search-btn", "meta-audience", "meta-geo-choice", "onMetaGeoSearch", "onMetaTargetLookup",
                 "_metaTargetResultsHtml", "_metaChipsHtml", "_metaSelected", "state.metaTargeting", "liveAudience"):
        assert gone not in APP_JS, gone
    assert "/api/constanza/meta-geos" in REGION and "/api/constanza/meta-forecast" in REGION and "/api/constanza/meta/status" in REGION
    assert "/api/constanza/meta-targeting?kind=" in REGION and "/api/constanza/meta-targeting?q=" in REGION and "/api/constanza/meta-languages" in REGION
    assert "meta_languages" in REGION and "SegmentBrowser" in REGION


def test_the_panel_loads_no_third_party_script_and_puts_no_secret_in_the_page():
    assert "http" not in re.sub(r"//[^\n]*", "", REGION)                                                          # no URL at all, so no script from elsewhere
    assert not re.search(r"EAA[A-Za-z0-9]{10,}", REGION) and "META_ACCESS_TOKEN" not in REGION and "META_APP_SECRET" not in REGION


def test_the_panels_css_and_scripts_are_linked_in_the_page():
    html = (ROOT / "app" / "templates" / "index.html").read_text(encoding="utf-8")
    for tag in ("/static/audience-kit.css", "/static/segment-browser.css", "/static/meta-audience.css", "/static/segment-browser.js"):
        assert tag in html
    assert html.index("segment-browser.js") < html.index("/static/app.js")                         # the dialog is there before the panel can open it
    css = (ROOT / "app" / "static" / "meta-audience.css").read_text(encoding="utf-8")
    assert ".meta-panel" in css and "prefers-reduced-motion" in css and "meta-card.meta-mid" in css


# ----------------------------------------------------------------------------------------------------------------- money, one rule

def test_every_amount_is_whole_dollars_without_cents_and_two_decimals_with_them():
    out = run("""return JSON.stringify([33862.5, 2000, 12.5, 12, 99.89999999999999, 2000.0000000001, 0.05, 1234567.891, 0, -5.5, null, '', 'abc', undefined, '35'].map(_metaMoney));""")
    assert out == ["$33,862.50", "$2,000", "$12.50", "$12", "$99.90", "$2,000", "$0.05", "$1,234,567.89", "$0", "-$5.50", "—", "—", "—", "—", "$35"]


def test_the_plan_strip_and_the_result_show_the_same_money_rule_for_a_budget_and_a_cpm_with_cents():
    snapshot = {**SNAPSHOT, "retail_budget": 33862.5, "media_budget": 16931.25, "retail_cpm": 12.5,
                "scenarios": {**SNAPSHOT["scenarios"], "retail_spend_at_flight": {"conservative": 161250, "mid": 33862.5, "upper": 309600.55}}}
    line = "{ id: 'L1', product_name: 'P', monthly_budget: 11287.5, months: 3 }"
    html = run(f"return JSON.stringify(_metaForecastPanelHtml({line}, {{ meta_retail_cpm: 12.5 }}, state.availsData.L1 || {{}}));", snapshot=snapshot, cpm=12.5, line={"id": "L1", "product_name": "P", "monthly_budget": 11287.5, "months": 1})
    strip = html[html.index('class="meta-plan"'):html.index("</ul>")]
    assert "$33,862.50 <small>over 3 months" in strip and ">$12.50</span>" in strip                  # the budget and the CPM, both with their cents
    result = html[html.index('class="meta-result'):]
    assert "$33,862.50 retail → $16,931.25 media at a $12.50 retail CPM" in result
    assert "<dd>$161,250</dd>" in result and "<dd>$33,862.50</dd>" in result and "<dd>$309,600.55</dd>" in result          # whole dollars plain, cents with two digits
    assert not re.search(r"\$\d[\d,]*\.\d(?!\d)", html), re.findall(r"\$\d[\d,]*\.\d(?!\d)", html)                       # never "$33,862.5"
    whole = run(f"return JSON.stringify(_metaForecastPanelHtml({LI}, {P}, {{}}));", snapshot=None)
    assert ">$12</span>" in whole and "$2,000 <small>" in whole


# ----------------------------------------------------------------------------------------------------------------- a refusal that names a field

def test_a_refusal_that_names_a_field_says_which_pick_it_was_about():
    out = run("""
      var body = { meta_targeting: [{ id: '1', name: 'Parents (all)', type: 'household_composition' }, { id: '2', name: 'Soccer', type: 'interests' }], meta_languages: [{ id: '1001', name: 'Spanish' }] };
      var refuse = function (loc, msg) { return { detail: [{ loc: loc, msg: msg }] }; };
      return JSON.stringify([
        _metaErrorText(refuse(['body', 'meta_targeting', 0, 'type'], 'String should have at most 20 characters'), 'x', body),
        _metaErrorText(refuse(['body', 'meta_targeting', 1, 'name'], 'Field required.'), 'x', body),
        _metaErrorText(refuse(['body', 'meta_languages', 0, 'name'], 'Field required'), 'x', body),
        _metaErrorText({ detail: [{ loc: ['body', 'age_min'], msg: 'Input should be a valid integer' }, { loc: ['body', 'age_min'], msg: 'Input should be a valid integer' }] }, 'x', body),
        _metaErrorText({ detail: [{ loc: ['body', 'meta_geo_label'], msg: 'too long' }, { loc: ['body', 'retail_budget'], msg: 'not a number' }] }, 'x', body),
        _metaErrorText(refuse(['body', 'meta_targeting', 9, 'type'], 'bad'), 'x', body),
        _metaErrorText(refuse(['body'], 'Field required'), 'x', body),
      ]);""")
    tail = "Change that choice and estimate again."
    assert out == [f"“Parents (all)” (Household): String should have at most 20 characters. {tail}", f"“Soccer” (Interest): Field required. {tail}",
                   f"the language “Spanish”: Field required. {tail}", f"the youngest age: Input should be a valid integer. {tail}",     # the same refusal twice is said once
                   f"the place’s name: too long; the retail budget: not a number. {tail}", f"meta targeting: bad. {tail}", f"Field required. {tail}"]


def test_a_plain_refusal_or_no_detail_is_passed_through_unchanged():
    out = run("""return JSON.stringify([_metaErrorText({ detail: 'Meta refused these settings: x.' }, 'Unprocessable', {}), _metaErrorText({}, 'Service Unavailable', {}),
                                         _metaErrorText({ detail: [] }, 'Bad Request'), _metaErrorText(null, '', undefined)]);""")
    assert out == ["Meta refused these settings: x.", "Service Unavailable", "Bad Request", "Request failed."]
    assert "_metaErrorText(data, response.statusText, body)" in _fn_source("onMetaForecast")                  # the estimate is the one request the model can refuse by field


# ----------------------------------------------------------------------------------------------------------------- the search boxes

def test_each_search_box_stops_at_the_length_its_server_search_takes(monkeypatch):
    from app.services import constanza_meta as meta
    box = run("return JSON.stringify(META_SEARCH_MAX);")
    html = panel_html(snapshot=None)
    for which in ("geo", "aud", "lang"):
        field = re.search(rf'<input[^>]*data-meta-ta-input="{which}"[^>]*>', html).group(0)
        assert f'maxlength="{box[which]}"' in field, which
    monkeypatch.setattr(meta, "_graph_get", lambda path, params: {"data": []})
    monkeypatch.setattr(meta, "is_configured", lambda: False)
    meta.search_geography("x" * box["geo"], "city")
    meta.search_targeting("x" * box["aud"])
    meta.search_locales("x" * box["lang"])
    for ask, longest in ((lambda q: meta.search_geography(q, "city"), box["geo"]), (meta.search_targeting, box["aud"]), (meta.search_locales, box["lang"])):
        with pytest.raises(ValueError):
            ask("x" * (longest + 1))                                                                      # one more than the box allows would be refused: so the box stops there


def test_the_browse_controls_have_a_reason_line_that_stays_hidden_until_the_pop_up_is_missing():
    html = panel_html(snapshot=None)
    assert html.count("data-meta-browse-reason hidden") == 2
    assert "window.SegmentBrowser" in _fn_source("_metaBrowseOff") and "Reload the page to get it back" in REGION
