"""The TikTok panel of the dock (app/static/constanza-tiktok.js) and the hooks constanza.js gives it. Like test_constanza_frontend.py,
the pure parts run in a JavaScript engine (Node when installed, else macOS's JavaScriptCore) against a stub page: what the panel
sends, how its result reads, and that the page loads it."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "app" / "templates" / "index.html").read_text(encoding="utf-8")
APP_JS = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
CZ_JS = (ROOT / "app" / "static" / "constanza.js").read_text(encoding="utf-8")
TT_JS = (ROOT / "app" / "static" / "constanza-tiktok.js").read_text(encoding="utf-8")
CZ_CSS = (ROOT / "app" / "static" / "constanza.css").read_text(encoding="utf-8")


def _harness():
    path = ROOT / "tests" / "test_full_flight_frontend.py"
    spec = importlib.util.spec_from_file_location("_ff_frontend_harness_tt", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


H = _harness()


def run(snippet: str):
    """Loads constanza.js and constanza-tiktok.js against a stub page and runs `snippet` (which returns a JSON string)."""
    fns = "\n".join(H._extract_fn(APP_JS, n) for n in ("escapeHtml", "_replaceGeoItem", "_constanzaErrorText"))
    body = (
        "var console = { warn: function () {}, log: function () {}, error: function () {} };\n"
        "var window = {};\n"
        "var document = { readyState: 'loading', addEventListener: function () {}, getElementById: function () { return null; },"
        " querySelectorAll: function () { return []; } };\n"
        "var state = { catalog: null };\n"
        + fns + "\n" + CZ_JS + "\n" + TT_JS + "\n"
        "var P = window.Constanza._pure; var TT = window.Constanza._plugin('tiktok'); var S = window.Constanza._state;\n" + snippet
    )
    try:
        out = H._run_js(body)
    except AssertionError as exc:
        if "JS engine failed" in str(exc) and "osascript" in str(exc).lower():
            pytest.skip(f"JavaScript engine unusable here: {exc}")
        raise
    return json.loads(out)


DATA = {
    "product_name": "Tiktok Ads", "platform": "tiktok", "confidence": "live_api", "geo_method": "exact_dma", "geo_confidence": "high",
    "months": 2, "imps_low": 2_400_000, "imps_mid": 2_640_000, "imps_high": 2_880_000, "est_uniques": 240_000, "reach_universe": None,
    "series": None, "weeks": None, "notes": ["TikTok note."], "unmatched_geo_tokens": [], "unmatched_geo_details": [], "ambiguous": [],
    "geo_interpretations": [], "geo_suggestions": [], "by_area": [],
    "tiktok": {"audience": {"low": 216_000, "mid": 240_000, "high": 264_000}, "stage": 3, "stage_label": "Balanced",
               "stage_note": "20% to 80% of the possible audience in these locations", "impressions_per_person": 5.5,
               "monthly": {"low": 1_188_000, "mid": 1_320_000, "high": 1_452_000},
               "locations": [{"source": "Los Angeles", "name": "Los Angeles CA", "id": "800002", "geo_type": "DMA", "kind": "location_id"}],
               "unmapped": [], "objective": "Reach", "placements": ["PLACEMENT_TIKTOK"], "request_id": "REQ-1", "cached": False},
}


# --------------------------------------------------------------------------------------------- the page

def test_the_page_loads_the_tiktok_panel_after_the_dock_that_hosts_it():
    assert INDEX.index("/static/constanza.js") < INDEX.index("/static/constanza-tiktok.js")


def test_the_panel_only_talks_to_the_routes_the_server_has():
    for route in ("/api/constanza/tiktok/status", "/api/constanza/tiktok/search?q=", "/api/constanza/tiktok/targeting?kind="):
        assert route in TT_JS, route
    import app.main as main
    paths = {r.path for r in main.app.routes}
    assert {"/api/constanza/tiktok/status", "/api/constanza/tiktok/search", "/api/constanza/tiktok/targeting", "/api/constanza/estimate"} <= paths


def test_the_styles_for_the_panel_and_the_verdict_exist():
    for cls in (".cz-method-panel.hidden", ".cz-tt-status", ".cz-stagebar", ".cz-stage.on.s3"):
        assert cls in CZ_CSS, cls


def test_the_host_has_the_slot_and_the_hooks_the_panel_relies_on():
    for hook in ('id="cz-method-forms"', "registerMethod", "activePlugin()", "plug.requestPart()", "plug.canPull", "plug.headlineHtml", "plug.cardHtml", "applyMethodUI"):
        assert hook in CZ_JS, hook


# --------------------------------------------------------------------------------------------- the panel registers and answers

def test_the_panel_registers_with_the_dock_and_gives_it_everything_it_asks_for():
    out = run("""return JSON.stringify({ keys: Object.keys(TT).filter(function (k) { return k !== '_pure'; }).sort(), go: TT.goLabel });""")
    assert out["go"] == "Estimate audience →"
    for key in ("formHtml", "bind", "reset", "canPull", "onSelect", "requestPart", "context", "headlineHtml", "bodyHtml", "aboutHtml", "cardHtml", "summaryLines"):
        assert key in out["keys"], key


def test_an_untouched_panel_asks_about_the_geography_alone_for_a_reach_campaign_on_tiktok():
    out = run("return JSON.stringify(TT.requestPart());")
    assert out == {"tiktok": {
        "gender": None, "age_groups": [], "languages": [], "interest_category_ids": [], "interest_keyword_ids": [], "purchase_intention_ids": [],
        "video_interaction_ids": [], "creator_interaction_ids": [], "hashtag_ids": [], "video_period": 15, "spending_power": None,
        "household_income": [], "operating_system": None, "network_types": [], "placements": ["PLACEMENT_TIKTOK"], "objective": "reach"}}


def test_what_the_planner_picks_goes_to_the_field_the_server_reads_it_from():
    out = run("""
        var T = TT._pure.T;
        T.gender = 'female'; T.age = ['AGE_35_44', 'AGE_18_24']; T.languages = [{ code: 'es', name: 'Spanish' }];
        T.spending = 'HIGH'; T.income = ['TOP25_50', 'TOP10']; T.os = 'IOS'; T.networks = ['WIFI', '5G']; T.objective = 'traffic'; T.videoPeriod = 7;
        T.placements = ['PLACEMENT_TIKTOK', 'PLACEMENT_PANGLE'];
        TT._pure.pushPick('general_interest', { id: '12100', name: 'Skincare', path: 'Beauty & Personal Care' });
        TT._pure.pushPick('additional_interest', { id: '19156001', name: 'Beauty Tutorials' });
        TT._pure.pushPick('purchase_intention', { id: '26100', name: 'Automotive' });
        TT._pure.pushPick('video_interaction', { id: '1700', name: 'Cars & Motorcycles' });
        TT._pure.pushPick('creator_interaction', { id: '10', name: 'Fashion & Beauty' });
        TT._pure.pushPick('hashtag_interaction', { id: '21', name: 'cybermonday' });
        TT._pure.pushPick('general_interest', { id: '12100', name: 'Skincare' });
        return JSON.stringify(TT.requestPart().tiktok);""")
    assert out["gender"] == "female" and out["age_groups"] == ["AGE_18_24", "AGE_35_44"] and out["languages"] == ["es"]
    assert out["interest_category_ids"] == ["12100"] and out["interest_keyword_ids"] == ["19156001"] and out["purchase_intention_ids"] == ["26100"]
    assert out["video_interaction_ids"] == ["1700"] and out["creator_interaction_ids"] == ["10"] and out["hashtag_ids"] == ["21"]
    assert out["spending_power"] == "HIGH" and out["household_income"] == ["TOP10", "TOP25_50"] and out["operating_system"] == "IOS"
    assert out["network_types"] == ["5G", "WIFI"] and out["objective"] == "traffic" and out["video_period"] == 7
    assert out["placements"] == ["PLACEMENT_PANGLE", "PLACEMENT_TIKTOK"]


def test_the_asked_summary_names_each_setting_in_plain_words():
    out = run("""
        var T = TT._pure.T;
        T.gender = 'male'; T.age = ['AGE_25_34', 'AGE_18_24']; T.languages = [{ code: 'en', name: 'English' }]; T.spending = 'HIGH'; T.os = 'ANDROID'; T.income = ['TOP5'];
        TT._pure.pushPick('general_interest', { id: '14100', name: 'Soccer' });
        TT._pure.pushPick('general_interest', { id: '14200', name: 'Fitness' });
        TT._pure.pushPick('hashtag_interaction', { id: '21', name: 'cybermonday' });
        return JSON.stringify(TT.context().summary);""")
    assert out == ["Men", "Ages 18–24, 25–34", "Languages: English", "Interests: Soccer; Fitness", "Hashtag: cybermonday", "High spending power",
                   "Household income: Top 5% of ZIP codes", "Android"]


def test_the_counts_on_the_two_sections_follow_the_settings():
    out = run("""
        var T = TT._pure.T, before = [TT._pure.selectedCount(), TT._pure.moreCount()];
        T.gender = 'male'; T.age = ['AGE_18_24']; TT._pure.pushPick('general_interest', { id: '1', name: 'A' }); T.spending = 'HIGH'; T.objective = 'video_views';
        return JSON.stringify({ before: before, after: [TT._pure.selectedCount(), TT._pure.moreCount()] });""")
    assert out == {"before": [0, 0], "after": [3, 2]}


def test_a_pull_is_never_refused_by_the_page_the_server_says_whether_tiktok_is_connected():
    """A 'not connected' status read when the dock opened used to block Estimate until the dock was reopened, even after an admin added the
    secrets. The server's answer is always current, so the page no longer judges."""
    out = run("""
        var T = TT._pure.T, seen = [];
        seen.push(TT.canPull().ok);
        T.status = { configured: false, missing: ['TIKTOK_ACCESS_TOKEN'] }; seen.push(TT.canPull().ok);
        T.status = { configured: true }; seen.push(TT.canPull().ok);
        T.status = { error: 'x', options: null }; seen.push(TT.canPull().ok);
        return JSON.stringify(seen);""")
    assert out == [True, True, True, True]


def test_the_asked_summary_names_the_video_look_back_only_when_a_video_interaction_is_picked():
    out = run("""
        var T = TT._pure.T, before = TT.context().summary.slice();
        TT._pure.pushPick('video_interaction', { id: '1700', name: 'Cars & Motorcycles' });
        var d15 = TT.context().summary.slice(); T.videoPeriod = 7; var d7 = TT.context().summary.slice(); T.videoPeriod = 0; var any = TT.context().summary.slice();
        return JSON.stringify({ before: before, d15: d15, d7: d7, any: any });""")
    assert out["before"] == [] and out["d15"] == ["Video interaction: Cars & Motorcycles", "Video look-back: 15 days"]
    assert out["d7"][-1] == "Video look-back: 7 days" and out["any"][-1] == "Video look-back: any time"


def test_a_pick_is_added_once_and_a_kind_holds_at_most_fifty():
    out = run("""
        var T = TT._pure.T, add = (i) => TT._pure.pushPick('general_interest', { id: String(i), name: 'N' + i });
        var first = add(1), again = add(1), rest = []; for (var i = 2; i <= 60; i++) rest.push(add(i));
        return JSON.stringify({ first: first, again: again, kept: T.picks.length, refused: rest.filter(function (x) { return !x; }).length });""")
    assert out == {"first": True, "again": False, "kept": 50, "refused": 10}


# --------------------------------------------------------------------------------------------- the host sends it for the TikTok product only

def test_only_the_tiktok_product_sends_the_tiktok_targeting_and_never_the_entravision_audience():
    out = run("""
        S.products = [{ name: 'Tiktok Ads', method: 'tiktok' }, { name: 'Roku', method: 'platform' }];
        S.geo = 'Los Angeles'; S.ageMin = '25'; S.gender = 'male'; S.total = '1000000'; S.interests = [{ label: 'Sports' }];
        S.product = 'Tiktok Ads'; var tt = P.requestBody();
        S.product = 'Roku'; var other = P.requestBody();
        return JSON.stringify({ tt: tt, other: other });""")
    assert out["tt"]["audience"] is None and out["tt"]["national_monthly_total"] is None and out["tt"]["tiktok"]["objective"] == "reach"
    assert out["other"]["audience"]["gender"] == "male" and "tiktok" not in out["other"] and out["other"]["national_monthly_total"] == 1_000_000


def test_changing_the_tiktok_setup_makes_the_shown_result_stale():
    out = run("""
        S.products = [{ name: 'Tiktok Ads', method: 'tiktok' }]; S.product = 'Tiktok Ads'; S.geo = 'Los Angeles';
        var a = JSON.stringify(P.requestBody()); TT._pure.T.gender = 'female'; var b = JSON.stringify(P.requestBody());
        return JSON.stringify({ differs: a !== b });""")
    assert out["differs"] is True


# --------------------------------------------------------------------------------------------- the result

def test_the_numbers_of_a_tiktok_result_are_monthly_impressions_and_the_audience_as_uniques():
    out = run(f"return JSON.stringify(P.summarize({json.dumps(DATA)}));")
    assert out["month"]["mid"] == 1_320_000 and out["total"]["mid"] == 2_640_000 and out["uniques"] == 240_000 and out["people"] is None


def test_the_headline_leads_with_the_audience_and_shows_impressions_the_flight_and_tiktoks_verdict():
    html = run(f"return JSON.stringify(TT.headlineHtml({json.dumps(DATA)}, P.summarize({json.dumps(DATA)})));")
    assert "Audience size · TikTok accounts, 18+" in html and "240,000" in html and "216K – 264K range" in html
    assert "1,320,000" in html and "audience × 5.5" in html and "Flight total · 2 mo" in html and "2,640,000" in html
    assert 'class="cz-stage on s3"' in html and "Balanced" in html and "20% to 80% of the possible audience" in html


def test_a_one_month_flight_has_no_flight_total_and_a_missing_verdict_is_not_invented():
    data = json.loads(json.dumps(DATA))
    data["months"] = 1
    data["tiktok"]["stage"] = None
    html = run(f"return JSON.stringify(TT.headlineHtml({json.dumps(data)}, P.summarize({json.dumps(data)})));")
    assert "Flight total" not in html and "cz-stagebar" not in html


def test_the_body_says_what_was_asked_and_where_it_was_sent():
    html = run(f"""
        TT._pure.T.gender = 'female'; TT._pure.pushPick('general_interest', {{ id: '12100', name: 'Skincare' }});
        var asked = {{ method: TT.context() }};
        return JSON.stringify(TT.bodyHtml({json.dumps(DATA)}, P.summarize({json.dumps(DATA)}), asked));""")
    assert "What was asked of TikTok" in html and ">Women<" in html and "Interest: Skincare" in html and "Objective: Reach" in html and "Placement: TikTok" in html
    assert "Places sent to TikTok" in html and "Los Angeles CA" in html and "TV market" in html


def test_the_result_is_drawn_from_what_was_asked_then_not_from_the_form_as_it_is_now():
    html = run(f"""
        var T = TT._pure.T; T.gender = 'female'; var asked = {{ method: TT.context() }}; T.gender = 'male';
        return JSON.stringify(TT.bodyHtml({json.dumps(DATA)}, P.summarize({json.dumps(DATA)}), asked));""")
    assert ">Women<" in html and ">Men<" not in html


def test_text_from_tiktok_is_escaped_everywhere_it_is_shown():
    data = json.loads(json.dumps(DATA))
    data["tiktok"]["locations"][0]["name"] = "<img src=x onerror=alert(1)>"
    data["tiktok"]["stage_label"] = "<b>Balanced</b>"
    data["tiktok"]["request_id"] = "<script>1</script>"
    html = run(f"""
        var m = P.summarize({json.dumps(data)});
        return JSON.stringify([TT.headlineHtml({json.dumps(data)}, m), TT.bodyHtml({json.dumps(data)}, m, {{}}), TT.aboutHtml({json.dumps(data)}), TT.cardHtml({json.dumps(data)}, m)].join("\\n"));""")
    assert "<img" not in html and "<script" not in html and "<b>Balanced" not in html and "&lt;img" in html


def test_the_about_section_names_the_factor_and_what_the_audience_is():
    html = run(f"return JSON.stringify(TT.aboutHtml({json.dumps(DATA)}));")
    assert "audience-size estimator" in html and "× 5.5 a month" in html and "request REQ-1" in html and "under-18s" in html
    cached = json.loads(json.dumps(DATA))
    cached["tiktok"]["cached"] = True
    assert "answered from the last ten minutes" in run(f"return JSON.stringify(TT.aboutHtml({json.dumps(cached)}));")


def test_the_copied_summary_has_the_audience_the_factor_and_the_places():
    out = run(f"""
        TT._pure.T.gender = 'male'; var lines = TT.summaryLines({json.dumps(DATA)}, {{ method: TT.context() }});
        return JSON.stringify(lines);""")
    assert out[0].startswith("Audience size (TikTok accounts, 18+): 240,000 (216,000 – 264,000); TikTok says: Balanced")
    assert "Impressions = audience × 5.5 per month" in out and "Asked of TikTok: Men" in out and "Places sent to TikTok: Los Angeles CA" in out


def test_the_badge_says_the_numbers_are_live_from_tiktok_and_not_a_population_share():
    out = run(f"return JSON.stringify(P.badgesHtml({json.dumps(DATA)}));")
    assert "TikTok API · live" in out and "Population-share" not in out and "High geo confidence" in out


def test_the_step_05_card_shows_the_audience_and_says_it_is_the_geography_alone():
    out = run(f"""
        var el = {{ style: {{}}, innerHTML: '', querySelector: function () {{ return {{ addEventListener: function () {{}}, innerHTML: '' }}; }} }};
        window.Constanza.renderCard(el, {json.dumps(DATA)}, {{ getText: function () {{ return 'x'; }}, onGeoText: function () {{}}, onApply: function () {{}}, onRefine: function () {{}} }});
        return JSON.stringify(el.innerHTML);""")
    assert "TikTok API · live" in out and "Audience size (TikTok accounts, 18+)" in out and "240,000" in out and "Balanced" in out
    assert "geography alone" in out and "Refine in Constanza" in out and "1,320,000" in out
    assert "Est. monthly uniques" not in out and "Audience (people):" not in out


def test_a_tiktok_product_has_its_own_group_in_the_picker_and_no_budget_to_spread():
    assert 'tiktok: "TikTok · live audience estimate"' in CZ_JS and '"tiktok", "_generic"' in CZ_JS
    assert "!pluginOf(p) && !!((p && p.method === \"generic\")" in CZ_JS


def test_the_panel_updates_its_buttons_in_place_and_only_rebuilds_them_when_the_menus_change():
    for hook in ("function buildChoices()", "function syncChoices()", "function syncBrowse()", "if (sig === builtFor) return;"):
        assert hook in TT_JS, hook
    changed = TT_JS[TT_JS.index("    function changed() {"):TT_JS.index("// ---------------------------------------------------------------- the connection")]
    assert "buildChoices()" not in changed and "syncChoices()" in changed and "syncBrowse()" in changed
    assert 'T.placements = ["PLACEMENT_TIKTOK"]' in changed                    # the interaction lock lives where every pick passes through


def test_the_page_tells_the_panel_when_a_pull_failed_so_a_stale_connection_banner_refreshes():
    assert "plug.onError(r.status)" in CZ_JS and "onError" in TT_JS and "loadStatus(true)" in TT_JS


def test_the_chip_button_style_does_not_reset_the_chip_font_size():
    assert "button.cz-chip { cursor: pointer; font-family: inherit; }" in CZ_CSS and "font: inherit" not in CZ_CSS.split("button.cz-chip", 1)[1].split("}", 1)[0]
