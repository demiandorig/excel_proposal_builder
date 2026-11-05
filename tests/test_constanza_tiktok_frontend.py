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
TT_CSS = (ROOT / "app" / "static" / "constanza-tiktok.css").read_text(encoding="utf-8")
KIT_CSS = (ROOT / "app" / "static" / "audience-kit.css").read_text(encoding="utf-8")


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


def run_factory(snippet: str):
    """Like run(), but the panel's factory is only CAPTURED (as `factory`) instead of being registered: the snippet calls it with a ctx of its own."""
    fns = "\n".join(H._extract_fn(APP_JS, n) for n in ("escapeHtml", "_replaceGeoItem", "_constanzaErrorText"))
    body = (
        "var console = { warn: function () {}, log: function () {}, error: function () {} };\n"
        "var window = {};\n"
        "var document = { readyState: 'loading', addEventListener: function () {}, getElementById: function () { return null; },"
        " querySelectorAll: function () { return []; } };\n"
        "var state = { catalog: null };\n"
        + fns + "\n" + CZ_JS + "\n"
        "var factory = null; window.Constanza.registerMethod = function (name, f) { if (name === 'tiktok') factory = f; };\n"
        + TT_JS + "\n" + snippet
    )
    try:
        out = H._run_js(body)
    except AssertionError as exc:
        if "JS engine failed" in str(exc) and "osascript" in str(exc).lower():
            pytest.skip(f"JavaScript engine unusable here: {exc}")
        raise
    return json.loads(out)


DATA = {
    "method": "tiktok", "product_name": "Tiktok Ads", "platform": "tiktok", "confidence": "live_api", "geo_method": "exact_dma", "geo_confidence": "high",
    "months": 2, "imps_low": 1_857_600, "imps_mid": 2_376_000, "imps_high": 3_715_200, "est_uniques": 216_000, "reach_universe": None,
    "series": None, "weeks": None, "notes": ["TikTok note."], "unmatched_geo_tokens": [], "unmatched_geo_details": [], "ambiguous": [],
    "geo_interpretations": [], "geo_suggestions": [], "by_area": [],
    "tiktok": {"audience": {"low": 216_000, "mid": 240_000, "high": 264_000}, "stage": 3, "stage_label": "Balanced",
               "stage_note": "20% to 80% of the possible audience in these locations", "impressions_per_person": 5.5,
               "scenarios": {"basis": "lower_end", "default": "mid", "multipliers": {"conservative": 4.3, "mid": 5.5, "upper": 8.6}},
               "monthly": {"low": 928_800, "mid": 1_188_000, "high": 1_857_600},
               "locations": [{"source": "Los Angeles", "name": "Los Angeles CA", "id": "800002", "geo_type": "DMA", "kind": "location_id"}],
               "unmapped": [], "objective": "Reach", "placements": ["PLACEMENT_TIKTOK"], "request_id": "REQ-1", "cached": False},
}


# --------------------------------------------------------------------------------------------- the page

def test_the_page_loads_the_tiktok_panel_after_the_dock_that_hosts_it():
    assert INDEX.index("/static/constanza.js") < INDEX.index("/static/constanza-tiktok.js")


def test_the_page_loads_the_kit_the_pop_up_and_the_panels_own_styles_in_the_order_they_override_each_other():
    css = [INDEX.index(f'href="/static/{name}.css"') for name in ("constanza", "audience-kit", "segment-browser", "constanza-tiktok")]
    assert css == sorted(css)                                                    # the panel's own rules come after the kit and the pop-up they refine
    assert INDEX.index("/static/segment-browser.js") < INDEX.index("/static/constanza-tiktok.js")      # the pop-up exists before the panel can open it


def test_the_panel_only_talks_to_the_routes_the_server_has():
    for route in ("/api/constanza/tiktok/status", "/api/constanza/tiktok/search?q=", "/api/constanza/tiktok/targeting?kind="):
        assert route in TT_JS, route
    import app.main as main
    paths = {r.path for r in main.app.routes}
    assert {"/api/constanza/tiktok/status", "/api/constanza/tiktok/search", "/api/constanza/tiktok/targeting", "/api/constanza/estimate"} <= paths


def test_the_styles_for_the_panel_and_the_verdict_exist():
    for cls in (".cz-method-panel.hidden", ".cz-stagebar", ".cz-stage.on.s3"):             # the dock's: the panel's host and the result card's verdict
        assert cls in CZ_CSS, cls
    for cls in (".aud-sec", ".aud-seg--wrap", ".aud-quick", ".aud-chip--lang", ".aud-ta-list", ".aud-browse", ".aud-notice--connected", ".aud-notice--warn"):      # the kit's
        assert cls in KIT_CSS, cls
    assert ".tt-panel [hidden]" in TT_CSS and "display: none !important" in TT_CSS         # the panel's own: [hidden] must beat the kit's flex displays


def test_the_host_has_the_slot_and_the_hooks_the_panel_relies_on():
    # (the host calls a method through its guards, so what it relies on is the member's name handed to guard() / pluginHtml())
    for hook in ('id="cz-method-forms"', "registerMethod", "activePlugin()", 'guard(plug, "requestPart"', 'guard(plug, "canPull"', 'pluginHtml(plug, "headlineHtml"',
                 'pluginHtml(plug, "cardHtml"', "applyMethodUI"):
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
    assert out["month"]["mid"] == 1_188_000 and out["total"]["mid"] == 2_376_000 and out["uniques"] == 216_000 and out["people"] is None


def test_the_headline_leads_with_the_audience_and_shows_impressions_the_flight_and_tiktoks_verdict():
    html = run(f"return JSON.stringify(TT.headlineHtml({json.dumps(DATA)}, P.summarize({json.dumps(DATA)})));")
    assert "Audience size · lower end, TikTok accounts 18+" in html and "216,000" in html and "range 216K – 264K" in html
    assert "1,188,000" in html and "lower-end audience × 5.5 (4.3 – 8.6)" in html and "Flight total · 2 mo" in html and "2,376,000" in html
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


def test_the_about_section_names_the_three_scenarios_and_what_the_audience_is():
    html = run(f"return JSON.stringify(TT.aboutHtml({json.dumps(DATA)}));")
    assert "audience-size estimator" in html and "request REQ-1" in html and "under-18s" in html
    assert "the lower end of that audience × 4.3 (conservative), × 5.5 (mid, the default) or × 8.6 (upper)" in html and "planning scenarios" in html
    assert "so its frequency reads 5.5" in html
    cached = json.loads(json.dumps(DATA))
    cached["tiktok"]["cached"] = True
    assert "answered from the last ten minutes" in run(f"return JSON.stringify(TT.aboutHtml({json.dumps(cached)}));")


def test_the_copied_summary_has_the_audience_the_scenarios_and_the_places():
    out = run(f"""
        TT._pure.T.gender = 'male'; var lines = TT.summaryLines({json.dumps(DATA)}, {{ method: TT.context() }});
        return JSON.stringify(lines);""")
    assert out[0].startswith("Audience size, lower end (TikTok accounts, 18+): 216,000 (range 216,000 – 264,000); TikTok says: Balanced")
    assert "Impressions = lower-end audience × 5.5 (4.3 – 8.6) per month (mid is the default)" in out and "Asked of TikTok: Men" in out and "Places sent to TikTok: Los Angeles CA" in out


def test_the_badge_says_the_numbers_are_live_from_tiktok_and_not_a_population_share():
    out = run(f"return JSON.stringify(P.badgesHtml({json.dumps(DATA)}));")
    assert "TikTok API · live" in out and "Population-share" not in out and "High geo confidence" in out


def test_the_panel_tells_the_dock_what_the_dock_used_to_know_about_tiktok_itself():
    """The dock names no method any more: the group in the picker, the note under the product, the badge and what an Insert files as the geography are the
    panel's own words, and `caps.autoPull: "geo"` says Step 05 pulls it from the proposal's geography as it always has. The markup is the dock's old one, byte for byte."""
    out = run(f"""
        var d = {json.dumps(DATA)};
        return JSON.stringify({{ label: TT.channelLabel, source: TT.sourceLabel, caps: TT.caps, badge: TT.badgeHtml(d), info: TT.geoInfo(d), viaHost: window.Constanza.geoInfo(d),
          state: window.Constanza.methodState('tiktok'), badges: P.badgesHtml(d) }});""")
    assert out["label"] == "TikTok · live audience estimate" and out["source"] == "Live from TikTok" and out["caps"] == {"autoPull": "geo"}
    assert out["badge"] == '<span class="cz-badge cz-reported" title="Asked of TikTok\'s own audience-size estimator just now">TikTok API · live</span>'
    assert out["badges"].startswith(out["badge"]) and "High geo confidence" in out["badges"]
    assert out["info"] == {"method": "exact_dma", "dmas": ["Los Angeles"], "unmatched_tokens": [], "win_rate": None, "hispanic_share": None}
    assert out["viaHost"] == out["info"]                                                         # the dock hands an Insert the panel's answer, and it is what the data path would say
    assert out["state"]["kind"] == "plugin" and out["state"]["caps"] == {"ownsGeography": False, "persist": False, "autoPull": "geo", "lineMonths": False}
    assert out["state"]["legacyKey"] is None and out["state"]["label"] == "Live from TikTok"


def test_the_step_05_card_shows_the_audience_and_says_it_is_the_geography_alone():
    out = run(f"""
        var el = {{ style: {{}}, innerHTML: '', querySelector: function () {{ return {{ addEventListener: function () {{}}, innerHTML: '' }}; }} }};
        window.Constanza.renderCard(el, {json.dumps(DATA)}, {{ getText: function () {{ return 'x'; }}, onGeoText: function () {{}}, onApply: function () {{}}, onRefine: function () {{}} }});
        return JSON.stringify(el.innerHTML);""")
    assert "TikTok API · live" in out and "Audience size, lower end (TikTok accounts, 18+)" in out and "216,000" in out and "Balanced" in out
    assert "geography alone" in out and "Refine in Constanza" in out and "1,188,000" in out
    assert "Est. monthly uniques" not in out and "Audience (people):" not in out


def test_a_tiktok_product_has_its_own_group_in_the_picker_and_no_budget_to_spread():
    # the group, its heading and the note under the product come from the panel (the dock names no method), the group key is "m:<channel>"
    assert 'channelLabel: "TikTok · live audience estimate"' in TT_JS and 'sourceLabel: "Live from TikTok"' in TT_JS
    assert 'const groupOf = (plug) => "m:" + (plug.channel || keyOf(plug));' in CZ_JS and "CHANNEL_ORDER.concat(groupKeys" in CZ_JS
    assert "!pluginOf(p) && !!((p && p.method === \"generic\")" in CZ_JS
    out = run("""
        state.catalog = { families: ['Social', 'Entravision Plus'], products_by_family: {
          'Social': [{ name: 'Tiktok Ads', family: 'Social', forecast: { enabled: true, method: 'tiktok' } }],
          'Entravision Plus': [{ name: 'Roku', family: 'Entravision Plus', forecast: { enabled: true, method: 'platform', series: { key: 'roku', channel: 'platform_model' } } }] } };
        var list = P.loadProducts().map(function (p) { return [p.name, p.channel]; });
        return JSON.stringify({ list: list, note: P.seriesMeta(null, 'tiktok') });""")
    assert out == {"list": [["Tiktok Ads", "m:tiktok"], ["Roku", "platform_model"]], "note": "Live from TikTok"}


def test_the_panel_updates_its_buttons_in_place_and_only_rebuilds_them_when_the_menus_change():
    for hook in ("function buildChoices()", "function syncChoices()", "function renderLangQuick()", "if (sig === builtFor) return;", "if (box.dataset.sig !== sig)"):
        assert hook in TT_JS, hook
    changed = TT_JS[TT_JS.index("    function changed() {"):TT_JS.index("    async function loadStatus(")]
    assert "buildChoices()" not in changed and "syncChoices()" in changed and "renderSections()" in changed and "renderLanguages()" in changed
    assert 'T.placements = ["PLACEMENT_TIKTOK"]' in changed                    # the interaction lock lives where every pick passes through


def test_browsing_every_category_is_the_shared_pop_up_and_not_a_dump_of_chips():
    assert "window.SegmentBrowser.open(" in TT_JS and "cz-tt-browse-body" not in TT_JS and "cz-browse" not in TT_JS
    open_call = TT_JS[TT_JS.index("function openBrowse()"):TT_JS.index("// ---------------------------------------------------------------- languages")]
    for part in ("tabs: BROWSE_TABS", "load: (tab) => loadCatalog(tab)", "isChosen:", "onApply:", "returnFocus: opener", "pushPick(tab, item)"):
        assert part in open_call, part
    assert "max:" not in open_call                                              # the pop-up's one global max cannot say "50 of EACH kind": the limit is applied on the way back
    assert "text-transform" not in TT_CSS and "animation" not in TT_CSS         # nothing here animates; the kit carries the reduced-motion rules


def test_the_page_tells_the_panel_when_a_pull_failed_so_a_stale_connection_banner_refreshes():
    assert 'guard(plug, "onError", [r.status])' in CZ_JS and "onError" in TT_JS and "loadStatus(true)" in TT_JS


def test_the_chip_button_style_does_not_reset_the_chip_font_size():
    assert "button.cz-chip { cursor: pointer; font-family: inherit; }" in CZ_CSS and "font: inherit" not in CZ_CSS.split("button.cz-chip", 1)[1].split("}", 1)[0]


# --------------------------------------------------------------------------------------------- the four sections

def test_the_panel_markup_is_a_banner_then_four_sections_in_order_and_never_asks_for_the_geography():
    html = run("return JSON.stringify(TT._pure.formHtml());")
    assert html.index('id="cz-tt-status"') < html.index('class="aud-builder"')
    order = [html.index(f'id="cz-tt-{key}"') for key in ("sec-who", "sec-lang", "sec-aud", "more")]
    assert order == sorted(order)
    for title in (">Who<", ">Language<", ">Audience<", ">More targeting<"):
        assert title in html, title
    assert html.count('class="aud-sec"') == 4 and '<details class="aud-sec" id="cz-tt-more">' in html      # "More targeting" folds, the others do not
    assert "cz-geo" not in html and ">Where<" not in html                                                   # the geography is the dock's own box
    assert "18+ · TikTok leaves out under-18s" in html and "language people have set in their TikTok app" in html


def test_the_connection_banner_is_drawn_once_as_a_status_region_with_nothing_in_it_yet():
    import re
    html = run("return JSON.stringify(TT._pure.formHtml());")
    banner = re.search(r'<div id="cz-tt-status"[^>]*>(.*?)</div>', html, re.S)
    assert banner and 'role="status"' in banner.group(0).split(">", 1)[0]                   # announced when its words change (checking, connected, not connected, couldn't check)
    assert banner.group(1) == "" and html.count('id="cz-tt-status"') == 1                    # the words come from renderStatus, which replaces only the contents of this one element


def test_each_clear_button_has_a_name_that_says_what_it_clears_and_none_sits_inside_the_folding_summary():
    html = run("return JSON.stringify(TT._pure.formHtml());")
    import re
    labels = dict(re.findall(r'id="cz-tt-clear-(\w+)"[^>]*aria-label="([^"]+)"', html))
    assert labels == {"who": "Clear age and gender", "lang": "Clear languages", "aud": "Clear interests and behaviors", "more": "Clear more targeting"}
    summary = html[html.index("<summary"):html.index("</summary>")]
    assert "<button" not in summary and "cz-fold-twin" in summary                                    # only the invisible twin that keeps the room
    assert html.index('id="cz-tt-clear-more"') < html.index('<details class="aud-sec" id="cz-tt-more">')      # the real link: beside the <details>, before it
    assert html.count('class="cz-fold"') == 1


def test_the_file_header_numbers_the_sections_as_the_dock_shows_them_after_where_and_when():
    head = TT_JS.split("(function () {", 1)[0]
    for line in ("3 Who", "4 Language", "5 Audience", "6 More targeting"):
        assert line in head, line
    assert "1 Who" not in head and "starts at Who" not in head


def test_the_age_summary_reads_neighbouring_brackets_as_one_range_and_the_who_line_adds_the_gender():
    out = run("""
        var T = TT._pure.T, rows = [];
        [[], ['AGE_25_34'], ['AGE_25_34', 'AGE_35_44'], ['AGE_35_44', 'AGE_45_54', 'AGE_55_100'], ['AGE_18_24', 'AGE_35_44', 'AGE_45_54'],
         ['AGE_18_24', 'AGE_25_34', 'AGE_35_44', 'AGE_45_54', 'AGE_55_100'], ['AGE_55_100', 'AGE_18_24']].forEach(function (a) { T.age = a; rows.push(TT._pure.ageSummary()); });
        T.age = ['AGE_25_34', 'AGE_35_44']; T.gender = 'female'; var both = TT._pure.whoSummary();
        T.age = []; var gender = TT._pure.whoSummary(); T.gender = 'male'; var men = TT._pure.whoSummary(); T.gender = ''; var none = TT._pure.whoSummary();
        return JSON.stringify({ rows: rows, both: both, gender: gender, men: men, none: none });""")
    assert out["rows"] == ["", "25–34", "25–44", "35+", "18–24, 35–54", "18+", "18–24, 55+"]
    assert out == {**out, "both": "Women · 25–44", "gender": "Women", "men": "Men", "none": ""}


def test_the_more_targeting_summary_lists_only_what_differs_from_the_defaults():
    out = run("""
        var T = TT._pure.T, rows = [TT._pure.moreSummary()];
        T.spending = 'HIGH'; rows.push(TT._pure.moreSummary());
        T.os = 'IOS'; T.objective = 'traffic'; rows.push(TT._pure.moreSummary());
        T.income = ['TOP5']; T.networks = ['WIFI', '5G']; T.placements = ['PLACEMENT_TIKTOK', 'PLACEMENT_PANGLE']; rows.push(TT._pure.moreSummary());
        T.income = ['TOP5', 'TOP10']; rows.push(TT._pure.moreSummary());
        return JSON.stringify(rows);""")
    assert out == ["", "High spenders", "High spenders · iOS · Traffic",
                   "High spenders · Top 5% of ZIP codes · iOS · 5G, Wi-Fi · TikTok + Pangle · Traffic",
                   "High spenders · 2 income ranges · iOS · 5G, Wi-Fi · TikTok + Pangle · Traffic"]


def test_a_long_list_of_picks_shows_two_names_and_how_many_more():
    out = run("""return JSON.stringify([[], ['A'], ['A', 'B'], ['A', 'B', 'C'], ['A', 'B', 'C', 'D', 'E']].map(TT._pure.namesSummary));""")
    assert out == ["", "A", "A, B", "A, B +1", "A, B +3"]


def test_the_age_presets_cover_the_brackets_of_the_menu_and_are_left_out_when_it_is_not_one_they_know():
    out = run("""
        var T = TT._pure.T, normal = TT._pure.agePresets();
        T.status = { configured: true, options: { age_groups: [{ id: 'AGE_18_24', label: '18–24' }, { id: 'AGE_25_100', label: '25+' }] } };
        var odd = TT._pure.agePresets();
        T.status = { configured: true, options: { age_groups: [{ id: 'young', label: 'Young' }] } };
        return JSON.stringify({ normal: normal, odd: odd, unknown: TT._pure.agePresets() });""")
    assert out["normal"] == [["18–34", ["AGE_18_24", "AGE_25_34"]], ["25–54", ["AGE_25_34", "AGE_35_44", "AGE_45_54"]], ["35+", ["AGE_35_44", "AGE_45_54", "AGE_55_100"]]]
    assert out["odd"] == [["18–34", ["AGE_18_24"]]] and out["unknown"] == []


# --------------------------------------------------------------------------------------------- language

def test_the_languages_go_to_the_request_as_sorted_codes_whatever_order_they_were_added_in():
    out = run("""
        var T = TT._pure.T; T.languages = [{ code: 'pt', name: 'Portuguese' }, { code: 'es', name: 'Spanish' }, { code: 'en', name: 'English' }];
        return JSON.stringify({ codes: TT.requestPart().tiktok.languages, said: TT.context().summary });""")
    assert out["codes"] == ["en", "es", "pt"] and out["said"] == ["Languages: Portuguese, Spanish, English"]


def test_a_language_quick_pick_looks_the_language_up_in_the_list_by_code_then_name_and_only_offers_what_the_list_has():
    out = run("""
        var P = TT._pure, T = P.T, codes = function () { return P.LANG_PICKS.map(function (p) { return P.pickLanguages(p).map(function (l) { return l.code; }); }); };
        var none = codes();                                                                  // no list yet: nothing is offered
        T.languagesAll = [{ code: 'pt', name: 'Portuguese' }, { code: 'es', name: 'Spanish' }, { code: 'en', name: 'English' }]; var plain = codes();
        T.languagesAll = [{ code: 'spa', name: 'Spanish (Latin America)' }, { code: 'eng', name: 'English' }]; var byName = codes();
        T.languagesAll = [{ code: 'pt', name: 'Portuguese' }, { code: 'en', name: 'English' }]; var noSpanish = codes();
        T.languagesAll = [{ code: 'xx', name: 'Spanglish' }]; var lookalike = codes();       // a name that only starts like Spanish is not Spanish
        return JSON.stringify({ none: none, plain: plain, byName: byName, noSpanish: noSpanish, lookalike: lookalike });""")
    assert out["none"] == [[], [], []]
    assert out["plain"] == [["es"], ["en"], ["es", "en"]]
    assert out["byName"] == [["spa"], ["eng"], ["spa", "eng"]]
    assert out["noSpanish"] == [[], ["en"], []]
    assert out["lookalike"] == [[], [], []]


def test_the_hispanic_suggestion_reads_the_docks_switch_or_the_pages_reading_of_the_line_and_nothing_else():
    out = run("""
        var P = TT._pure, rows = [];
        rows.push(P.targetsHispanic());                                          // a fresh dock, no line
        S.hispanic = true; rows.push(P.targetsHispanic()); S.hispanic = null;    // the dock was told
        S.lineId = 'L1'; rows.push(P.targetsHispanic());                         // a line, but the page has no line items to read
        state.lineItems = [{ id: 'L1', target_override: 'Hispanic adults' }, { id: 'L2', target_override: 'Adults' }];
        function _targetsHispanic(li) { return /hispanic/i.test(li.target_override); }
        rows.push(P.targetsHispanic()); S.lineId = 'L2'; rows.push(P.targetsHispanic()); S.lineId = 'L9'; rows.push(P.targetsHispanic());
        return JSON.stringify(rows);""")
    assert out == [False, True, False, True, False, False]


def test_the_panel_asks_the_dock_whether_the_line_targets_hispanic_audiences_and_believes_the_answer():
    out = run_factory("""
        var rows = [];
        [true, false].forEach(function (answer) {
          var asked = 0, ctx = { S: { hispanic: !answer, lineId: 'L1' }, $: function () { return null; }, esc: String, targetsHispanic: function () { asked += 1; return answer; } };
          var T = factory(ctx);
          rows.push([T._pure.targetsHispanic(), T._pure.targetsHispanic(), asked]);     // asked every time, never cached; the dock's S and the page are not consulted
        });
        return JSON.stringify(rows);""")
    assert out == [[True, True, 2], [False, False, 2]]


def test_the_old_guarded_reach_for_page_globals_is_only_the_fallback_for_a_dock_without_the_function():
    out = run_factory("""
        function _targetsHispanic(li) { return /hispanic/i.test(li.target_override); }
        state.lineItems = [{ id: 'L1', target_override: 'Hispanic adults' }, { id: 'L2', target_override: 'Adults' }];
        var rows = [], S = { hispanic: null, lineId: null }, ctx = { S: S, $: function () { return null; }, esc: String };           // no ctx.targetsHispanic
        var T = factory(ctx);
        rows.push(T._pure.targetsHispanic());                                      // no line, nothing told
        S.hispanic = true; rows.push(T._pure.targetsHispanic()); S.hispanic = null;          // told
        S.lineId = 'L1'; rows.push(T._pure.targetsHispanic());                     // the page's reading of the line
        S.lineId = 'L2'; rows.push(T._pure.targetsHispanic());
        ctx.targetsHispanic = function () { return false; };                       // a dock that does offer it: the fallback is not used any more
        S.lineId = 'L1'; rows.push(T._pure.targetsHispanic());
        return JSON.stringify(rows);""")
    assert out == [False, True, True, False, False]


def test_the_dock_gives_a_panel_targetsHispanic_that_reads_its_state_fresh_each_time():
    out = run("""
        var seen = null;
        window.Constanza.registerMethod('probe', function (ctx) { seen = ctx; return { formHtml: function () { return ''; }, bind: function () {} }; });
        var rows = [typeof seen.targetsHispanic, seen.targetsHispanic()];                        // a fresh dock
        S.hispanic = true; rows.push(seen.targetsHispanic()); S.hispanic = null;                 // told
        S.lineId = 'L1'; state.lineItems = [{ id: 'L1', target_override: 'Hispanic' }, { id: 'L2', target_override: 'Adults' }];
        function _targetsHispanic(li) { return /hispanic/i.test(li.target_override); }
        rows.push(seen.targetsHispanic()); S.lineId = 'L2'; rows.push(seen.targetsHispanic());    // the line the dock is open for NOW
        return JSON.stringify(rows);""")
    assert out == ["function", False, True, True, False]


def test_a_failing_page_helper_never_breaks_the_panel():
    out = run("""
        var P = TT._pure; S.lineId = 'L1'; state.lineItems = [{ id: 'L1' }];
        function _targetsHispanic() { throw new Error('boom'); }
        return JSON.stringify(P.targetsHispanic());""")
    assert out is False


# --------------------------------------------------------------------------------------------- the pop-up

def test_the_pop_up_gets_each_category_as_text_with_its_path_and_whether_it_is_a_top_level_one():
    out = run("""return JSON.stringify(TT._pure.browseItems([{ id: 12100, name: 'Skincare', path: 'Beauty & Personal Care', has_children: false },
        { id: '12000', name: 'Beauty', path: '', has_children: true }, { id: 7, name: '#tag', path: '' }]));""")
    assert out == [{"id": "12100", "name": "Skincare", "path": "Beauty & Personal Care", "top": False},
                   {"id": "12000", "name": "Beauty", "path": "", "top": True}, {"id": "7", "name": "#tag", "path": "", "top": False}]
