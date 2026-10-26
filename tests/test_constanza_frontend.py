"""Constanza's front end: the launcher + dock (app/static/constanza.js, constanza.css) and how the page loads them.

The markup contracts are checked statically; the pure helpers (number formatting, how a pick edits a geography list, the
numbers the cards show, the HTML the geography help renders) run in a JavaScript engine - Node when it is installed, else
macOS's built-in JavaScriptCore - with a stub `document`, so nothing here needs a browser."""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "app" / "templates" / "index.html").read_text(encoding="utf-8")
APP_JS = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
CZ_JS = (ROOT / "app" / "static" / "constanza.js").read_text(encoding="utf-8")
CZ_CSS = (ROOT / "app" / "static" / "constanza.css").read_text(encoding="utf-8")
STYLES = (ROOT / "app" / "static" / "styles.css").read_text(encoding="utf-8")


def _harness():
    path = ROOT / "tests" / "test_full_flight_frontend.py"
    spec = importlib.util.spec_from_file_location("_ff_frontend_harness_cz", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


H = _harness()


def run(snippet: str):
    """Loads constanza.js against a stub page and runs `snippet` (which returns a JSON string)."""
    fns = "\n".join(H._extract_fn(APP_JS, n) for n in ("escapeHtml", "_replaceGeoItem", "_constanzaErrorText"))
    body = (
        "var console = { warn: function () {}, log: function () {}, error: function () {} };\n"
        "var window = {};\n"
        # readyState "loading" => constanza.js only registers a DOMContentLoaded handler, so nothing is mounted
        "var document = { readyState: 'loading', addEventListener: function () {}, getElementById: function () { return null; },"
        " querySelectorAll: function () { return []; } };\n"
        "var state = { catalog: null };\n"
        + fns + "\n" + CZ_JS + "\n"
        "var P = window.Constanza._pure;\n" + snippet
    )
    try:
        out = H._run_js(body)
    except AssertionError as exc:
        if "JS engine failed" in str(exc) and "osascript" in str(exc).lower():
            pytest.skip(f"JavaScript engine unusable here: {exc}")
        raise
    return json.loads(out)


# ---------------------------------------------------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------------------------------------------------

def test_the_masthead_button_and_the_old_modal_are_gone():
    for gone in ("constanza-open-btn", "constanza-modal", "constanza-browse-interests-btn", "masthead-btn"):
        assert gone not in INDEX, gone
    for gone in ("openConstanzaModal", "closeConstanzaModal", "onConstanzaModalCalculate", "renderConstanzaModalResult",
                 "CONSTANZA_PLATFORM_OPTIONS", "onBrowseConstanzaInterests", "constanza-open-btn"):
        assert gone not in APP_JS, gone
    assert ".masthead-btn" not in STYLES


def test_the_page_loads_the_stylesheet_and_the_script_after_the_app():
    assert '<link rel="stylesheet" href="/static/constanza.css" />' in INDEX
    assert INDEX.index('/static/app.js') < INDEX.index('/static/constanza.js')
    # the launcher and the dock are built by the script, so the page carries no second copy of their markup
    assert "constanza-bubble" not in INDEX and "constanza-dock" not in INDEX


def test_every_constanza_rule_lives_in_constanza_css():
    left = re.findall(r"\.constanza-[a-z-]+|\.cz-[a-z-]+", STYLES)
    assert not left, sorted(set(left))
    assert ".constanza-bubble" in CZ_CSS and ".constanza-dock" in CZ_CSS and ".constanza-result" in CZ_CSS


def test_the_dock_respects_reduced_motion_and_fits_a_phone():
    assert "prefers-reduced-motion: reduce" in CZ_CSS
    assert "@media (max-width: 720px)" in CZ_CSS
    # the phone rules come last so they beat the 1000px ones (this order bug once left the dock 24px short of the screen)
    assert CZ_CSS.index("@media (max-width: 1000px)") < CZ_CSS.index("@media (max-width: 720px)")


def test_the_shadows_follow_the_design_system():
    """'Crisp, hairline-driven shadows, near-zero blur' (the design-system doc): no soft, wide blur in the Constanza CSS."""
    for m in re.finditer(r"box-shadow:([^;]+);", CZ_CSS):
        for blur in re.findall(r"-?\d+px\s+-?\d+px\s+(\d+)px", m.group(1)):
            assert int(blur) <= 2, m.group(0)


def test_the_masthead_is_a_grid_scoped_to_the_builder_page():
    assert 'class="masthead-inner masthead-inner--steps"' in INDEX
    assert ".masthead-inner--steps" in STYLES
    admin = (ROOT / "app" / "templates" / "admin.html").read_text(encoding="utf-8")
    assert "masthead-inner--steps" not in admin             # the admin page keeps its plain flex row


def test_the_curate_table_becomes_cards_below_1400px_and_carries_its_labels_as_css():
    block = STYLES.split("@media (max-width: 1400px) {\n  .line-items-scroll", 1)[1].split("/* Hidden utility */", 1)[0]
    assert "display: flex" in block and ".line-items thead { display: none; }" in block
    for label in ('content: "Model"', 'content: "Rate"', 'content: "Min"', 'content: "Planner note"', 'var(--lbl-budget, "Budget")', 'var(--lbl-months, "Months")'):
        assert label in block, label
    assert '"--lbl-budget"' in APP_JS and '"--lbl-months"' in APP_JS        # _applyTimeUnitLabels keeps the two billing-period labels current


def test_live_sports_period_is_listed_on_the_step_04_row_the_step_05_card_and_in_the_dock():
    assert "function _livePeriodTag" in APP_JS and "${_livePeriodTag(p)}" in APP_JS
    assert "Constanza.periodLineHtml(" in APP_JS
    assert "isLiveSports(series) && series.period_label" in CZ_JS
    assert ".period-tag" in STYLES and ".cz-period-line" in CZ_CSS


# ---------------------------------------------------------------------------------------------------------------------
# The helpers
# ---------------------------------------------------------------------------------------------------------------------

def test_compact_numbers():
    out = run("""return JSON.stringify([0, 7, 999, 1000, 1234, 15600, 999999, 1500000, 20400000, 3100000000, null, NaN].map(P.compact));""")
    assert out == ["0", "7", "999", "1K", "1.2K", "16K", "1000K", "1.5M", "20M", "3.1B", "—", "—"]


@pytest.mark.parametrize("text,items,expected", [
    ("Othello, WA; Adams County, WA; Yakima, WA", ["Othello, WA"], "Adams County, WA; Yakima, WA"),
    ("Adams County, WA; Othello, WA", ["Othello, WA"], "Adams County, WA"),
    ("Othello, WA and Surrounding areas in Adams County, WA", ["Othello, WA"], "Surrounding areas in Adams County, WA"),
    ("othello,wa ; Adams County, WA", ["Othello, WA"], "Adams County, WA"),                     # case and spacing do not matter
    ("Othello, WA; Pasco, WA; Adams County, WA", ["Othello, WA", "Pasco, WA"], "Adams County, WA"),
    ("Adams County, WA", ["Othello, WA"], None),                                                  # not in the text: do not guess an edit
])
def test_dropping_the_places_inside_a_county_edits_only_those_items(text, items, expected):
    assert run(f"return JSON.stringify(P.removeGeoItems({json.dumps(text)}, {json.dumps(items)}));") == expected


def test_a_pick_replaces_one_item_of_a_list_and_leaves_the_rest():
    out = run("""return JSON.stringify(P.replaceItem("Springfield, 95814, Placer County, CA", "Springfield", "Springfield, MO"));""")
    assert out == "Springfield, MO, 95814, Placer County, CA"


def test_monthly_numbers_divide_the_flight_total_by_the_months():
    out = run("""return JSON.stringify(P.summarize({ months: 3, imps_low: 300, imps_mid: 600, imps_high: 900, est_uniques: 50,
        series: { unit: "monthly" }, weeks: null }));""")
    assert out["month"] == {"low": 100, "mid": 200, "high": 300} and out["weekly"] is False and out["week"] is None
    assert out["total"]["mid"] == 600 and out["uniques"] == 50


def test_a_weekly_series_is_summarized_per_week_and_per_month():
    out = run("""return JSON.stringify(P.summarize({ months: 2, imps_low: 800, imps_mid: 1000, imps_high: 1200, weeks: 4,
        series: { unit: "weekly" } }));""")
    assert out["weekly"] is True and out["week"] == {"low": 200, "mid": 250, "high": 300} and out["month"]["mid"] == 500


def test_an_audience_estimate_headlines_the_narrowed_numbers_and_keeps_the_unfiltered_one():
    out = run("""return JSON.stringify(P.summarize({ months: 2, imps_low: 100, imps_mid: 200, imps_high: 300, est_uniques: 9,
        series: { unit: "monthly" }, audience: { imps_low: 10, imps_mid: 20, imps_high: 30 } }));""")
    assert out["month"]["mid"] == 10 and out["baseMonthMid"] == 100 and out["uniques"] is None      # uniques describe the unfiltered audience


def test_the_people_counts_are_the_products_audience_and_uniques_narrowed_with_the_impressions():
    out = run("""return JSON.stringify(P.summarize({ months: 2, imps_low: 100, imps_mid: 200, imps_high: 300, est_uniques: 9, reach_universe: 40,
        series: { unit: "monthly" },
        audience: { imps_low: 10, imps_mid: 20, imps_high: 30, audience_people: 2, est_uniques: 1, impressions_per_person: 5 } }));""")
    assert out["people"] == 2 and out["peopleBase"] == 40 and out["uniques"] == 1 and out["perPerson"] == 5     # 10 a month / 2 people
    plain = run("""return JSON.stringify(P.summarize({ months: 1, imps_low: 80, imps_mid: 100, imps_high: 120, est_uniques: 9, reach_universe: 20,
        series: { unit: "monthly" } }));""")
    assert plain["people"] == 20 and plain["perPerson"] == 5 and plain["uniques"] == 9
    none = run("""return JSON.stringify(P.summarize({ months: 1, imps_low: 80, imps_mid: 100, imps_high: 120, series: null }));""")
    assert none["people"] is None and none["perPerson"] is None and none["uniques"] is None


def test_the_audience_box_leads_with_the_products_audience_and_calls_the_census_figure_market_population():
    out = run("""const data = { months: 1, imps_low: 2, imps_mid: 2584100, imps_high: 3, reach_universe: 487600,
        audience: { imps_low: 242996, imps_mid: 402887, imps_high: 562778, audience_people: 76022, est_uniques: 26084, impressions_per_person: 5.3,
          underlying_population: 2049177, reliability_floor_population: 10000, below_reliability_threshold: false, market_population_adults: true,
          confidence_tier: "audience_modeled_stacked", interests: [{ supported: true, label: "Auto Intenders", rate: 0.31 }], notes: [] } };
        return JSON.stringify(P.audienceBoxHtml(data.audience, P.summarize(data)));""")
    # what the product REACHES is the unique IDs (never above the impressions beside it); the reach universe is only the pool
    assert "Unique IDs reached on this product" in out and "26,084" in out and "about 15.4 impressions each" in out
    shown = _visible(out)
    assert "Addressable pool" not in shown and "76,022" not in shown and "487,600" not in shown      # the pool is no headline (it can be larger than the impressions)
    assert 'title="Addressable pool: 76,022 IDs' in out and "a flight reaches part of it" in out          # ... it is the unique-IDs sentence's tooltip
    assert "Market population (census)" in out and "2,049,177 adults" in out and "across all media" in out
    assert "Underlying audience" not in out and "Audience on this product" not in out and "reachable person" not in out


def test_without_a_reach_universe_the_audience_box_shows_only_the_market_population_and_says_why():
    out = run("""const data = { months: 1, imps_low: 2, imps_mid: 100, imps_high: 3,
        audience: { imps_low: 1, imps_mid: 50, imps_high: 60, audience_people: null, est_uniques: null, underlying_population: 5000,
          reliability_floor_population: 10000, below_reliability_threshold: true, confidence_tier: "audience_modeled_single_cut", interests: [], notes: [] } };
        return JSON.stringify(P.audienceBoxHtml(data.audience, P.summarize(data)));""")
    assert "Market population (census)" in out and "no audience count" in out and "below the ~10,000 reliability floor" in out
    assert "Unique IDs reached" not in out and "Addressable pool" not in out


def test_badges_say_where_the_numbers_came_from_and_list_the_live_sports_period():
    out = run("""return JSON.stringify({
        live: P.badgesHtml({ geo_confidence: "high", weeks: 2.1, series: { channel: "live_sports", unit: "weekly", data_basis: "modeled", period_label: "Regular season, ends Sep 27, 2026" } }),
        netflix: P.badgesHtml({ geo_confidence: "medium", series: { channel: "netflix", unit: "monthly", data_basis: "extrapolated" } }),
        generic: P.badgesHtml({ geo_confidence: "low_confidence_international", series: null }),
    });""")
    assert "Modeled" in out["live"] and "Weekly avails" in out["live"] and "2.1 wk flight" in out["live"] and "Regular season, ends Sep 27, 2026" in out["live"]
    assert "Sample extended to all markets" in out["netflix"] and "Medium geo confidence" in out["netflix"] and "period" not in out["netflix"]
    assert "Population-share estimate" in out["generic"] and "International" in out["generic"]


def test_the_geography_help_lists_candidates_suggestions_and_what_could_not_be_matched_and_escapes_the_text():
    out = run("""return JSON.stringify(P.geoHelpHtml(P.normGeo({
        ambiguous: [{ input: "Adams <b>County</b>", candidates: [{ label: "Adams County, CO", population: 524000, resolve_hint: "Adams County, CO" }] }],
        suggestions: [{ kind: "did_you_mean", label: "Othello, WA", geo_type: "city", population: 8000, resolve_hint: "Othello, WA", replaces: "Othelo" },
                      { kind: "broaden", label: "Spokane (TV market)", geo_type: "dma", resolve_hint: "Spokane" },
                      { kind: "overlap", label: "Use Adams County, WA only", why: "Othello may be inside it", resolve_hint: "Adams County, WA", drop_inputs: ["Othello, WA"] }],
        interpretations: [{ input: "Adams County Washinton", became: "Adams County, WA", why: "read the state as Washington" }],
        unmatched_details: [{ input: "Zzz", reason: "Couldn't match 'Zzz'." }],
    })));""")
    assert 'data-act="candidate"' in out and "Adams County, CO" in out and "524K" in out
    assert out.count('data-act="suggestion"') == 3
    assert "Read “Adams County Washinton” as <strong>Adams County, WA</strong>" in out
    assert "Couldn&#039;t match" in out or "Couldn't match" in out
    assert "<b>County</b>" not in out and "&lt;b&gt;County&lt;/b&gt;" in out               # the typed text is escaped


def test_a_line_is_labelled_by_its_product_and_the_start_of_its_target():
    out = run("""return JSON.stringify([
        P.lineLabel({ product_name: "Entravision Plus - Live Sports - MLB", target_override: "" }),
        P.lineLabel({ product_name: "Entravision+ Connect - (Default)", target_override: "All 18+ | Registered to vote, voters, politically savvy, interested in community." }),
    ]);""")
    assert out[0] == "Live Sports - MLB"
    assert out[1].startswith("Connect - (Default) · All 18+") and out[1].endswith("…") and len(out[1]) < 70


def test_product_meta_names_the_basis_and_unit():
    out = run("""return JSON.stringify([P.seriesMeta(null), P.seriesMeta({ data_basis: "platform_reported", unit: "monthly" }), P.seriesMeta({ data_basis: "modeled", unit: "weekly" })]);""")
    assert out == ["Population-share estimate", "Reported avails · monthly avails", "Modeled · weekly avails"]


def test_notes_that_repeat_the_unmatched_warning_are_not_shown_twice():
    out = run("""return JSON.stringify(P.notesOf({ notes: ["NOT INCLUDED in this estimate - couldn't be matched: 'x'", "A real note"] }));""")
    assert out == ["A real note"]


# ---------------------------------------------------------------------------------------------------------------------
# Review fixes: each of these was a confirmed defect found by an independent review of the branch
# ---------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("text,drop,expected", [
    ("Orange County, CA, Orange, NJ", ["Orange"], None),                    # "Orange" is not an item here: "Orange County" is, and "Orange, NJ"
    ("902101, 90210", ["90210"], "902101"),                                 # never inside a longer ZIP
    ("90210, 902101", ["90210"], "902101"),
    ("Othello, WA; Othello WA; Adams County, WA", ["Othello, WA", "Othello WA"], "Adams County, WA"),   # every copy of the same place
    ("Othello, WA; Adams County, WA; Othello, WA", ["Othello, WA"], "Adams County, WA"),
    ("Springfield Gardens, NY; Springfield", ["Springfield"], "Springfield Gardens, NY"),
    ("Adams County, WA, Othello, WA", ["Othello, WA"], "Adams County, WA"),
    ("Spokane and Othello, WA or Pasco, WA", ["Othello, WA"], "Spokane or Pasco, WA"),
    # a state code that spells a word is a state code: OR is Oregon, in any case
    ("Portland, OR; Seattle, WA", ["Seattle, WA"], "Portland, OR"),
    ("Seattle, WA; Portland, OR", ["Seattle, WA"], "Portland, OR"),
    ("Portland, OR; Salem, OR", ["Salem, OR"], "Portland, OR"),
    ("Beaverton, OR and Hillsboro, OR", ["Hillsboro, OR"], "Beaverton, OR"),
    ("Beaverton, OR and Hillsboro, OR", ["Beaverton, OR"], "Hillsboro, OR"),
    ("portland, or; salem, or", ["salem, or"], "portland, or"),
    ("Multnomah County, OR; Washington County, OR; Linn County, OR", ["Washington County, OR"], "Multnomah County, OR; Linn County, OR"),
    # the separator goes with the item, wherever the item stands
    ("Dallas, Houston, or Austin", ["Austin"], "Dallas, Houston"),
    ("Dallas, Houston, or Austin", ["Houston"], "Dallas or Austin"),
    ("Dallas and Houston", ["Dallas"], "Houston"),
    ("Dallas or Houston or Austin", ["Houston"], "Dallas or Austin"),
    ("Dallas plus Houston", ["Houston"], "Dallas"),
    ("Dallas AND Houston", ["Houston"], "Dallas"),
    ("Dallas; Houston & Austin", ["Dallas"], "Houston & Austin"),
    # an Oxford comma after the item goes with it: nothing is left dangling
    ("Dallas, Austin, and Houston", ["Austin"], "Dallas and Houston"),
    ("Dallas, Austin, or Houston", ["Austin"], "Dallas or Houston"),
    ("Dallas, Austin, and Houston", ["Dallas"], "Austin, and Houston"),
    ("Dallas, and Austin", ["Dallas"], "Austin"),
    ("Dallas, or Austin", ["Dallas"], "Austin"),
    ("Dallas, Austin, and Houston", ["Houston"], "Dallas, Austin"),
    ("Dallas, Austin, and Houston", ["Dallas", "Austin"], "Houston"),
    ("Plano, TX, Frisco, TX, and Allen, TX", ["Frisco, TX"], "Plano, TX and Allen, TX"),
    ("Andover, MA, Austin, TX", ["Andover, MA"], "Austin, TX"),                       # a name that starts with "And" is not a word
    # a capitalised And / Plus after a comma starts a name, it is not a conjunction
    ("Dallas, Plus Ultra", ["Dallas"], "Plus Ultra"),
    ("Dallas, Plus Ultra, Waco", ["Dallas"], "Plus Ultra, Waco"),
    ("Dallas, And Co Ranch", ["Dallas"], "And Co Ranch"),
    ("Waco, Dallas, Plus Ultra", ["Dallas"], "Waco, Plus Ultra"),
])
def test_dropping_items_matches_whole_items_never_substrings(text, drop, expected):
    assert run(f"return JSON.stringify(P.removeGeoItems({json.dumps(text)}, {json.dumps(drop)}));") == expected


@pytest.mark.parametrize("text,item,replacement,expected", [
    ("Springfield Gardens, NY, Springfield", "Springfield", "Springfield, MO", "Springfield Gardens, NY, Springfield, MO"),
    ("Plano, TX; Travus Co., TX", "Travus Co., TX", "Travis County, TX", "Plano, TX; Travis County, TX"),
    ("Dallas, TX; Adams Cty, WA", "adams  cty, wa", "Adams County, WA", "Dallas, TX; Adams County, WA"),         # case and spacing
    ("Adams Count", "Adams Count", "Adams County, WA", "Adams County, WA"),
    ("Plano, TX; Travus County, TX", "Travus Co., TX", "Travis County, TX", "Plano, TX; Travus County, TX; Travis County, TX"),   # not found: the list is kept
    ("Plano, TX; Travis County, TX", "Travus Co., TX", "Travis County, TX", "Plano, TX; Travis County, TX"),       # ... and never duplicated
    ("Adams Count", "Something else", "Adams County, WA", "Adams County, WA"),                                    # a single typed item is the item
    ("Portland, OR", "Portlnd, OR", "Portland, OR", "Portland, OR"),                                              # OR is Oregon: one place, not a list
    ("Dallas, Houston", "Travus Co., TX", "Travis County, TX", "Dallas, Houston; Travis County, TX"),             # one comma, no state after it: a list
    ("Othello, WA", "Othelo, WA", "Othello, WA", "Othello, WA"),
    ("Adams County, Washington", "Adams Cty", "Adams County, WA", "Adams County, WA"),
])
def test_replacing_an_item_never_wipes_the_rest_of_the_list(text, item, replacement, expected):
    assert run(f"return JSON.stringify(P.replaceItem({json.dumps(text)}, {json.dumps(item)}, {json.dumps(replacement)}));") == expected


def test_an_item_that_is_a_prefix_of_one_with_a_state_is_not_that_item():
    """'Othello' is not an item of 'Othello, WA': the state after it belongs to it."""
    assert run("""return JSON.stringify([P.editGeoItem("Othello, WA", "Othello", null), P.editGeoItem("Othello, WA; Pasco", "Othello", null),
        P.editGeoItem("Othello, WA", "Othello, WA", null)]);""") == [None, None, ""]


def test_the_card_flow_uses_the_same_whole_item_replacement():
    fns = "\n".join(H._extract_fn(APP_JS, n) for n in ("escapeHtml", "_replaceGeoItem"))
    body = ("var console = { warn: function () {}, log: function () {}, error: function () {} };\nvar window = {};\n"
            "var document = { readyState: 'loading', addEventListener: function () {}, getElementById: function () { return null; }, querySelectorAll: function () { return []; } };\n"
            "var state = { catalog: null };\n" + fns + "\n" + CZ_JS + "\n"
            "var Constanza = window.Constanza;\n"
            "return JSON.stringify(_replaceGeoItem('Orange County, CA; Orange, NJ', 'Orange, NJ', 'Orange, CA'));")
    assert json.loads(H._run_js(body)) == "Orange County, CA; Orange, CA"


def test_an_insert_into_a_line_that_no_longer_exists_writes_nothing():
    fns = H._extract_fn(APP_JS, "applyConstanzaEstimate")
    body = ("var state = { lineItems: [{ id: 'a', product_name: 'P' }], availsData: {}, productIndex: {} };\n"
            "function renderAvailsGrid() { state.rendered = true; }\nfunction _effectiveProduct() { return {}; }\nfunction calcMaxSpendFromImps() { return null; }\n"
            + fns + "\n"
            "var gone = applyConstanzaEstimate('zzz', 1000, null, null);\n"
            "var ok = applyConstanzaEstimate('a', 1000.4, 50, { method: 'dma' });\n"
            "return JSON.stringify({ gone: gone, ok: ok, keys: Object.keys(state.availsData), imps: state.availsData.a.max_imps, uniques: state.availsData.a.est_uniques });")
    assert json.loads(H._run_js(body)) == {"gone": False, "ok": True, "keys": ["a"], "imps": 1000, "uniques": 50}


def _apply(entries: dict, line_id: str, imps, uniques, geo=None) -> dict:
    fns = H._extract_fn(APP_JS, "applyConstanzaEstimate")
    body = (f"var state = {{ lineItems: [{{ id: 'a', product_name: 'P' }}], availsData: {json.dumps(entries)}, productIndex: {{}} }};\n"
            "function renderAvailsGrid() {}\nfunction _effectiveProduct() { return {}; }\nfunction calcMaxSpendFromImps() { return null; }\n"
            + fns + f"\napplyConstanzaEstimate('a', {imps}, {json.dumps(uniques)}, {json.dumps(geo)});\n"
            "return JSON.stringify(state.availsData.a);")
    return json.loads(H._run_js(body))


def test_an_insert_never_leaves_more_unique_ids_on_the_line_than_impressions():
    """A line's old unique count (from an earlier Apply, or typed) used to survive an Apply that carried none, so a smaller
    impressions figure could sit beside a larger 'audience' and a frequency below one."""
    stale = _apply({"a": {"max_imps": 900000, "est_uniques": 400000, "frequency": 2.3}}, "a", 60000, None)
    assert stale["max_imps"] == 60000 and "est_uniques" not in stale and "frequency" not in stale
    # a count that still makes sense stays (and the frequency follows the new impressions)
    kept = _apply({"a": {"max_imps": 900000, "est_uniques": 20000, "frequency": 45}}, "a", 60000, None)
    assert kept["est_uniques"] == 20000 and kept["frequency"] == 3.0
    # an estimate that brings its own unique count replaces whatever was there
    fresh = _apply({"a": {"est_uniques": 400000}}, "a", 60000, 23964)
    assert fresh["est_uniques"] == 23964 and fresh["frequency"] == 2.5


def test_an_insert_remembers_the_win_rate_so_the_share_of_voice_can_say_what_it_is_of():
    geo = {"method": "exact_dma", "dmas": ["Los Angeles"], "unmatched_tokens": [], "win_rate": 0.15}
    marked = _apply({}, "a", 387615, 153707, geo)
    assert marked["win_rate"] == 0.15 and marked["max_imps"] == 387615 and marked["geo_resolution"]["win_rate"] == 0.15
    # a later insert from a product with no win rate (Roku, a population-share estimate) clears it, as does a line that never had one
    cleared = _apply({"a": {"win_rate": 0.15}}, "a", 1000, 50, {"method": "dma", "dmas": [], "unmatched_tokens": [], "win_rate": None})
    assert "win_rate" not in cleared
    assert "win_rate" not in _apply({}, "a", 1000, 50, None)


def test_typing_over_a_constanza_figure_clears_the_win_rate_marker():
    handler = _between(APP_JS, 'inp.addEventListener("blur", e => {', "updateSovBadge(lid);  // refresh against the final")
    after_edit_check = handler.split("e.target._focusParsed = undefined;\n      entry[key] = value;", 1)[1]
    assert after_edit_check.lstrip().startswith("delete entry.win_rate;")


def _sov(entries: dict, lid: str, pct: float) -> dict:
    fns = "\n".join(H._extract_fn(APP_JS, n) for n in ("sovTier", "applySovDisplay"))
    body = (f"var state = {{ availsData: {json.dumps(entries)}, lineItems: [{{ id: 'a', monthly_budget: 5000 }}] }};\n"
            "var els = {}; var document = { getElementById: function (id) { return els[id] = els[id] || { className: '', textContent: '' }; } };\n"
            "function escapeAttr(x) { return x; }\nfunction _isFullFlight() { return false; }\nfunction _moneyExact(x) { return x; }\n"
            + fns + f"\napplySovDisplay('a', {pct});\n"
            "return JSON.stringify({ badge: els['sov-badge-a'].textContent, helper: els['sov-helper-a'].textContent, cls: els['sov-badge-a'].className });")
    return json.loads(H._run_js(body))


def test_the_share_of_voice_names_what_it_is_measured_against():
    """A plan that buys 41% of the platform's avails reads 276% of the win-rate-adjusted ceiling: '276% of avails' would say it
    uses 2.76x what exists. Lines whose figures came from a win rate say 'of expected delivery'; every other line is unchanged."""
    plain = _sov({"a": {}}, "a", 85)
    assert plain == {"badge": "85% of avails", "helper": "Proposed product allocation uses: 85% of total avails", "cls": "sov-badge sov-orange"}
    won = _sov({"a": {"win_rate": 0.15}}, "a", 276)
    assert won["badge"] == "276% of expected delivery" and won["cls"] == "sov-badge sov-red"
    assert won["helper"] == "Proposed product allocation uses: 276% of expected delivery (the avails x a 15% win rate)"
    assert "12.5% win rate" in _sov({"a": {"win_rate": 0.125}}, "a", 50)["helper"]
    assert _sov({}, "a", 85)["badge"] == "85% of avails"                       # no entry at all: unchanged
    # free-form: the SOV is the planner's own declared percentage, of nothing Constanza computed
    assert _sov({"a": {"win_rate": 0.15, "freeform": True}}, "a", 40) == {"badge": "40% of avails", "helper": "Proposed product allocation uses: 40% of total avails",
                                                                         "cls": "sov-badge sov-green"}


def _between(src: str, start: str, end: str) -> str:
    return src.split(start, 1)[1].split(end, 1)[0]


def test_the_flight_box_does_not_rewrite_itself_while_the_planner_types():
    """Clearing the box and typing 6 used to give 16: the empty box was rewritten to '1' on the first keystroke."""
    handler = _between(CZ_JS, '$("cz-flight").addEventListener("input"', '$("cz-flight").addEventListener("change"')
    assert "setFlight(" not in handler and "$(\"cz-flight\").value" not in handler
    assert 'addEventListener("change", (e) => setFlight(e.target.value))' in CZ_JS


def test_an_answer_that_arrives_after_reset_or_a_newer_pull_is_dropped():
    pull = _between(CZ_JS, "async function pull()", "function clearRequestState()")
    assert "const mine = ++S.pullSeq" in pull and pull.count("if (mine !== S.pullSeq) return;") >= 2
    reset = _between(CZ_JS, "function resetForm()", "// ---- open / close")
    assert "S.pullSeq++" in reset and "S.resSeq++" in reset and "clearTimeout(resolveTimer)" in reset and "setBusy(false)" in reset


def test_opening_the_dock_for_a_line_starts_that_lines_request_clean_and_a_new_proposal_clears_the_dock():
    open_fn = _between(CZ_JS, "  function open(opts) {", "  function close() {")
    assert "if (opts.lineId || opts.geo != null) clearRequestState();" in open_fn
    clear = _between(CZ_JS, "function clearRequestState()", "function onProposalReset()")
    for field in ("geoType", "ageMin", "gender", "interests", "needTotal", "result"):
        assert field in clear, field
    assert "Constanza.onProposalReset()" in APP_JS and "state.constanzaGeoOverride = {}" in APP_JS


def test_the_summary_describes_what_the_result_was_pulled_for_not_what_the_form_says_now():
    assert "S.resultCtx = asked;" in CZ_JS and "const asked = S.resultCtx ||" in _between(CZ_JS, "function summaryText(", "function renderResult(")
    assert "markStale()" in CZ_JS and "The form has changed since this was pulled" in CZ_JS


def test_a_failed_audience_request_is_not_remembered_and_never_an_unhandled_rejection():
    load = _between(CZ_JS, "async function loadTaxonomy()", "async function renderBrowse()")
    assert "try {" in load and "if (r.ok) { S.taxonomy = r.data; return S.taxonomy; }" in load       # only a good answer is cached
    search = _between(CZ_JS, "async function searchInterests()", "function groupLabel(")
    assert "try {" in search and "isn’t available right now" in search
    assert "Products couldn’t be loaded" in CZ_JS                                                          # the catalog never arrived


def test_the_pickers_are_comboboxes_with_an_active_descendant_and_the_result_is_not_a_live_region():
    for field in ('id="cz-picker-search"', 'id="cz-interest-q"'):
        tag = CZ_JS.split(field, 1)[1].split("/>", 1)[0]
        assert 'role="combobox"' in tag and "aria-controls=" in tag and "aria-autocomplete=\"list\"" in tag, field
    assert 'id="cz-opt-${idx}"' in CZ_JS and 'id="cz-int-${i}"' in CZ_JS and "aria-activedescendant" in CZ_JS
    assert 'class="cz-group-label" role="presentation"' in CZ_JS
    assert '<div class="cz-result" id="cz-result"></div>' in CZ_JS and 'id="cz-status" role="status" aria-live="polite"' in CZ_JS


def test_picking_the_right_county_replaces_the_whole_phrase_it_was_read_from_and_nothing_else():
    text = "Dallas; Kansas City, MO and surrounding areas in Johnson County; Austin"
    out = run(f"return JSON.stringify(P.replaceItem({json.dumps(text)}, 'Kansas City, MO and surrounding areas in Johnson County', 'Johnson County, KS'));")
    assert out == "Dallas; Johnson County, KS; Austin"


@pytest.mark.parametrize("text,expected", [
    ("Dallas", False), ("Othello, WA", False), ("Portland, OR", False), ("portland, or", False), ("Adams County, Washington", False),
    ("Washington, Oregon", False), ("New York, NY", False), ("Kansas City, MO", False), ("90210", False),
    ("Dallas, TX 75201", False), ("Washington, D.C.", False), ("Dallas, USA", False), ("Plano, 75024", False), ("Dallas, DC", False),
    ("Dallas, Houston", True), ("90210, 94105", True), ("Dallas, Fort Worth", True), ("Dallas and Houston", True), ("Dallas or Houston", True), ("Dallas AND Houston", True),
    ("Dallas; Houston", True), ("Plano, TX, Frisco, TX", True), ("Dallas\nHouston", True), ("Beaverton, OR and Hillsboro, OR", True),
])
def test_text_is_a_list_when_it_names_more_than_one_place(text, expected):
    """'OR' after a comma is Oregon and 'Dallas, Houston' is two places: the old test had both backwards."""
    assert run(f"return JSON.stringify(P.looksLikeList({json.dumps(text)}));") is expected


def test_tidying_a_list_never_takes_a_state_code_for_a_word():
    out = run("""return JSON.stringify([P.tidyList("Portland, OR"), P.tidyList("Portland, OR;"), P.tidyList("; ; Portland, OR , ;"),
        P.tidyList("Beaverton, OR; ; Salem, OR"), P.tidyList("Dallas and Houston")]);""")
    assert out == ["Portland, OR", "Portland, OR", "Portland, OR", "Beaverton, OR; Salem, OR", "Dallas and Houston"]


def _stale_run(steps: str):
    """Drives markStale() against a one-element fake page: the banner is a node that can be inserted and removed."""
    return run("""
        var banner = null, inserted = 0;
        var box = { querySelector: function () { return banner; }, insertAdjacentHTML: function () { banner = { remove: function () { banner = null; } }; inserted++; } };
        document.getElementById = function (id) { return id === 'cz-result' ? box : null; };
        var S = window.Constanza._state, markStale = P.markStale, requestBody = P.requestBody;
        S.product = 'P'; S.geo = 'Dallas'; S.flight = 3; S.unit = 'months'; S.result = { ok: true };
        S.resultSig = JSON.stringify(requestBody());
        function shown() { return banner !== null; }
    """ + steps)


def test_the_stale_banner_is_judged_by_comparing_the_request_not_by_every_touch_of_the_form():
    """Reopening the dock called setFlight(), which always marked the result stale: the banner appeared with nothing changed."""
    out = _stale_run("""
        var log = {};
        markStale(); log.untouched = shown();                       // the form still asks for what was pulled
        S.flight = 4; markStale(); log.changed = shown();           // a different flight length
        S.flight = 3; markStale(); log.back = shown();              // ... put back: nothing to say any more
        S.geo = 'Houston'; markStale(); markStale(); log.geo = shown(); log.once = inserted;
        S.geo = 'Dallas'; S.ageMin = '25'; markStale(); log.audience = shown();
        return JSON.stringify(log);""")
    assert out == {"untouched": False, "changed": True, "back": False, "geo": True, "once": 2, "audience": True}


def test_there_is_no_banner_without_a_result_or_for_a_result_that_has_no_request_on_record():
    out = _stale_run("""
        S.result = null; S.flight = 9; markStale(); var a = shown();
        S.result = { ok: true }; S.resultSig = null; markStale(); var b = shown();
        return JSON.stringify([a, b]);""")
    assert out == [False, False]


def test_a_repaint_of_the_result_keeps_the_banner_when_the_form_has_changed():
    """The 'show all areas' toggle repaints the result, which used to drop the 'form has changed' banner: the repaint itself now
    ends by judging the form again (so a form that changed while a pull was in flight shows it too)."""
    render = _between(CZ_JS, "  function renderResult(data) {", "  function renderError(message)")
    assert "markStale();" in render.split('$("cz-copy").addEventListener')[-1]
    assert "tog.addEventListener(\"click\", () => { S.showAllAreas = !S.showAllAreas; renderResult(data); })" in render


def _visible(html: str) -> str:
    """The text a planner sees: tags and attribute values (tooltips) removed."""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def _audience_box(extra_aud="", extra_data="", people=76022, census=2049177):
    return run(f"""const data = {{ months: 1, imps_low: 2, imps_mid: 2584100, imps_high: 3, reach_universe: 487600, {extra_data}
        audience: {{ imps_low: 242996, imps_mid: 402887, imps_high: 562778, audience_people: {people}, est_uniques: 26084, impressions_per_person: 5.3,
          underlying_population: {census}, reliability_floor_population: 10000, below_reliability_threshold: false, {extra_aud}
          confidence_tier: "audience_modeled_stacked", interests: [{{ supported: true, label: "Auto Intenders", rate: 0.31 }}], notes: [] }} }};
        return JSON.stringify(P.audienceBoxHtml(data.audience, P.summarize(data)));""")


def test_the_census_figure_says_adults_only_when_it_counts_adults():
    assert "2,049,177 adults in the market" in _audience_box("market_population_adults: true,")
    assert "2,049,177 people in the market" in _audience_box("market_population_adults: false,")


def test_the_census_line_makes_no_claim_about_what_a_product_reaches():
    """A display platform counts devices: Dallas holds 8.4M of them against 6.4M census adults, so 'more than any one product reaches'
    can be false. The census figure is only said to be the market's, across all media — and the pool is not on this box at all."""
    for people, census in ((76022, 2049177), (8_357_600, 6_380_000)):
        out = _audience_box("market_population_adults: true,", people=people, census=census)
        assert "more than any one product reaches" not in out and "Addressable pool" not in _visible(out)
        assert f"{census:,} adults in the market fit these cuts, across all media." in out


def test_a_baseline_product_is_not_called_the_audience_on_this_product():
    out = _audience_box("market_population_adults: true,", 'series: { relation: "baseline_proxy" },')
    assert "Unique IDs reached on the baseline product" in out and "on this product" not in out and "This product’s own targeting reaches fewer" in out
    assert "Addressable pool" not in _visible(out) and 'title="Addressable pool (baseline product): 76,022 IDs' in out


def test_the_summary_carries_impressions_per_unique_and_per_reachable_person_and_the_proxy_flag():
    out = run("""return JSON.stringify(P.summarize({ months: 1, imps_low: 80, imps_mid: 100, imps_high: 120, est_uniques: 20, reach_universe: 50,
        series: { unit: "monthly", relation: "baseline_proxy" } }));""")
    assert out["perPerson"] == 2 and out["perUnique"] == 5 and out["proxy"] is True
    plain = run("""return JSON.stringify(P.summarize({ months: 1, imps_low: 80, imps_mid: 100, imps_high: 120, series: { unit: "monthly" } }));""")
    assert plain["perUnique"] is None and plain["proxy"] is False


# ---------------------------------------------------------------------------------------------------------------------
# The win rate and the unique IDs: what a planner reads (MadHive / Trade Desk products)
# ---------------------------------------------------------------------------------------------------------------------

WIN = ('{ low: 0.1, mid: 0.15, high: 0.2, raw_imps_low: 1, raw_imps_mid: 2584100, raw_imps_high: 3, raw_est_uniques: 167300 }')
WIN_FULL = ('{ low: 0.1, mid: 0.15, high: 0.2, raw_imps_low: 1, raw_imps_mid: 2584100, raw_imps_high: 3, raw_est_uniques: 167300, '
            'delivered_at_low: 258410, delivered_at_high: 516820 }')


def test_the_summary_says_what_the_platform_reported_and_what_is_expected_to_deliver():
    out = run(f"""return JSON.stringify(P.summarize({{ months: 2, imps_low: 1, imps_mid: 775230, imps_high: 3, win_rate: {{ low: 0.1, mid: 0.15, high: 0.2,
        raw_imps_mid: 5168200 }}, series: {{ unit: "monthly" }} }}));""")
    assert out["winRate"] == {"low": 0.1, "mid": 0.15, "high": 0.2, "unit": "month", "forAudience": False, "raw": 2584100, "delivered": 387615,
                              "atLow": None, "atHigh": None}                       # (no sensitivity figures in this payload: none invented)
    full = run("""return JSON.stringify(P.summarize({ months: 2, imps_low: 1, imps_mid: 775230, imps_high: 3, win_rate: { low: 0.1, mid: 0.15, high: 0.2,
        raw_imps_mid: 5168200, delivered_at_low: 516820, delivered_at_high: 1033640 }, series: { unit: "monthly" } }));""")
    assert full["winRate"]["atLow"] == 258410 and full["winRate"]["atHigh"] == 516820             # per month: at 10% and at 20%
    weekly = run("""return JSON.stringify(P.summarize({ months: 1, imps_low: 1, imps_mid: 300, imps_high: 3, weeks: 3, win_rate: { low: 0.1, mid: 0.15, high: 0.2,
        raw_imps_mid: 2000 }, series: { unit: "weekly" } }));""")
    assert weekly["winRate"]["unit"] == "week" and weekly["winRate"]["raw"] == pytest.approx(2000 / 3) and weekly["winRate"]["delivered"] == 100
    plain = run("""return JSON.stringify(P.summarize({ months: 1, imps_low: 1, imps_mid: 100, imps_high: 3, win_rate: null, series: { unit: "monthly" } }));""")
    assert plain["winRate"] is None


def test_with_an_audience_cut_the_win_rate_line_describes_the_audiences_numbers_not_the_whole_geographys():
    """The table says 'Audience imps / month 60,433'; the line under it used to quote the whole geography's 387,615 as 'the expected
    delivery'. It is now scaled by the same cut, so both describe one number."""
    out = run(f"""const data = {{ months: 1, imps_low: 1, imps_mid: 387615, imps_high: 3, win_rate: {WIN_FULL}, series: {{ unit: "monthly" }},
        audience: {{ imps_low: 1, imps_mid: 60433, imps_high: 3 }} }};
        const m = P.summarize(data);
        return JSON.stringify({{ w: m.winRate, html: P.winRateHtml(m), copy: P.summaryText({{ ...data, product_name: "X", notes: [] }}) }});""")
    w = out["w"]
    cut = 60433 / 387615
    assert w["forAudience"] is True and w["delivered"] == 60433 and w["raw"] == pytest.approx(2584100 * cut)
    assert w["atLow"] == pytest.approx(258410 * cut) and w["atHigh"] == pytest.approx(516820 * cut)
    assert "for this audience" in out["html"] and "60,433" in out["html"] and "387,615" not in out["html"]
    assert "for this audience (bid opportunities); expected delivery 60,433 a month" in out["copy"] and "387,615" not in out["copy"]


def test_the_win_rate_line_names_the_rate_both_figures_and_the_delivery_at_each_end_of_the_range():
    out = run(f"""const data = {{ months: 1, imps_low: 1, imps_mid: 387615, imps_high: 3, win_rate: {WIN_FULL}, series: {{ unit: "monthly" }} }};
        return JSON.stringify(P.winRateHtml(P.summarize(data)));""")
    assert "Win rate 15%" in out and "2,584,100 avails a month" in out and "bid opportunities" in out and "387,615" in out
    assert "258,410 at 10%" in out and "516,820 at 20%" in out and "for this audience" not in out
    # whose avails they are depends on the series (platform-reported, Entravision's model, an audience slice): the line must not claim
    assert "platform reports" not in out and "platform's avails" not in out
    assert run("""return JSON.stringify(P.winRateHtml(P.summarize({ months: 1, imps_low: 1, imps_mid: 5, imps_high: 3, series: null })));""") == ""
    fractional = run("""return JSON.stringify(P.winRateHtml(P.summarize({ months: 1, imps_low: 1, imps_mid: 10, imps_high: 3, win_rate: { low: 0.08, mid: 0.125, high: 0.2,
        raw_imps_mid: 100, delivered_at_low: 8, delivered_at_high: 20 }, series: { unit: "monthly" } })));""")
    assert "12.5%" in fractional and "8 at 8%" in fractional and "20 at 20%" in fractional
    single = run("""return JSON.stringify(P.winRateHtml(P.summarize({ months: 1, imps_low: 1, imps_mid: 15, imps_high: 3, win_rate: { low: 0.15, mid: 0.15, high: 0.15,
        raw_imps_mid: 100, delivered_at_low: 15, delivered_at_high: 15 }, series: { unit: "monthly" } })));""")
    assert "Win rate 15%" in single and " at 15%" not in single                                    # one fixed rate: no sensitivity to show


def test_the_headline_shows_unique_ids_and_keeps_the_addressable_pool_out_of_sight():
    """The platform's reach universe is a ceiling a flight only partly delivers to, and after the win rate it is often LARGER than the
    impressions: shown as a tile it read as 'an audience bigger than the impressions' again. It is a tooltip here (and detail in the dock)."""
    out = run("""return JSON.stringify(P.reachLinesHtml(P.summarize({ months: 1, imps_low: 1, imps_mid: 387615, imps_high: 3, est_uniques: 153707, reach_universe: 487600,
        series: { unit: "monthly" } }), null));""")
    assert "Unique IDs reached / month" in out and "153,707" in out and "about 2.5 impressions each" in out
    assert out.count("487,600") == 1 and 'title="Addressable pool: 487,600 IDs' in out and "a flight reaches part of it" in out      # only in the tooltip
    assert "Audience (people)" not in out and "reachable person" not in out and ">Addressable pool" not in out
    no_pool = run("""return JSON.stringify(P.reachLinesHtml(P.summarize({ months: 1, imps_low: 1, imps_mid: 100, imps_high: 3, est_uniques: 20, series: null }), null));""")
    assert "Unique IDs reached" in no_pool and "title=" not in no_pool
    assert run("""return JSON.stringify(P.reachLinesHtml(P.summarize({ months: 1, imps_low: 1, imps_mid: 100, imps_high: 3, reach_universe: 9, series: { unit: "monthly" } }), null));""") == ""
    assert run("""return JSON.stringify(P.reachLinesHtml(P.summarize({ months: 1, imps_low: 1, imps_mid: 100, imps_high: 3, series: null }), null));""") == ""


def test_a_forecast_method_with_its_own_panel_keeps_its_own_audience_line():
    """TikTok's card draws its own audience size: the shared lines must not add unique IDs beside it."""
    out = run("""return JSON.stringify(P.reachLinesHtml(P.summarize({ months: 1, imps_low: 1, imps_mid: 100, imps_high: 3, est_uniques: 20, series: null }), { cardHtml: () => "" }));""")
    assert "Unique IDs" not in out


def test_the_badge_row_shows_the_win_rate_only_when_one_was_applied():
    with_wr = run(f"""return JSON.stringify(P.badgesHtml({{ geo_confidence: "high", series: {{ unit: "monthly", data_basis: "platform_reported" }}, win_rate: {WIN} }}));""")
    assert "Win rate 15%" in with_wr
    without = run("""return JSON.stringify(P.badgesHtml({ geo_confidence: "high", series: { unit: "monthly", data_basis: "platform_reported" } }));""")
    assert "Win rate" not in without


def test_the_win_rate_note_is_for_api_readers_and_is_not_repeated_beside_the_win_rate_line():
    notes = run("""return JSON.stringify(P.notesOf({ notes: ["Win rate applied: x", "Platform-reported avails (v2)", "NOT INCLUDED in this estimate — y"] }));""")
    assert notes == ["Platform-reported avails (v2)"]
    box = run("""const aud = { confidence_tier: "audience_modeled_single_cut", audience_people: 5, est_uniques: 4, underlying_population: 100,
        reliability_floor_population: 10000, below_reliability_threshold: false, interests: [], notes: ["Win rate applied: x", "A real audience note"] };
        return JSON.stringify(P.audienceBoxHtml(aud, null));""")
    assert "A real audience note" in box and "Win rate applied" not in box


def test_the_copied_summary_carries_the_win_rate_and_unique_ids_but_not_the_pool():
    out = run(f"""const data = {{ product_name: "Connect Family", months: 1, imps_low: 1, imps_mid: 387615, imps_high: 3, est_uniques: 153707, reach_universe: 487600,
        win_rate: {WIN_FULL}, series: {{ unit: "monthly", title: "Connect Family", version: 2 }}, notes: [] }};
        return JSON.stringify(P.summaryText(data));""")
    assert "Win rate 15% applied: 2,584,100 avails a month (bid opportunities); expected delivery 387,615 a month (258,410 at 10%, 516,820 at 20%)" in out
    assert "platform reports" not in out
    assert "Unique IDs reached per month: 153,707 (about 2.5 impressions each)" in out
    assert "Addressable pool" not in out and "487,600" not in out
    assert "Audience on this product" not in out and "Est. uniques per month" not in out


def test_the_step_05_card_prints_the_unique_ids_once_with_or_without_an_audience_cut():
    """With cuts the audience box already carries the unique-IDs sentence; the card used to print it a second time just above."""
    def render(extra: str) -> str:
        return run("""const stub = () => ({ addEventListener() {}, innerHTML: "", onclick: null });
            const el = { style: {}, innerHTML: "", querySelector: stub };
            window.Constanza.renderCard(el, { product_name: "X", months: 1, imps_low: 70, imps_mid: 100, imps_high: 130, est_uniques: 20, reach_universe: 150,
                geo_confidence: "high", notes: [], series: { unit: "monthly", data_basis: "platform_reported" }, ambiguous: [], """ + extra + """ },
                { getText: () => "", onGeoText() {}, onApply() {}, onRefine() {} });
            return JSON.stringify(el.innerHTML);""")
    plain = render("")
    assert plain.count("Unique IDs reached") == 1 and 'title="Addressable pool: 150 IDs' in plain
    with_cut = render("""audience: { imps_low: 7, imps_mid: 10, imps_high: 13, audience_people: 15, est_uniques: 2, underlying_population: 1000,
        reliability_floor_population: 10000, below_reliability_threshold: false, confidence_tier: "audience_modeled_single_cut", interests: [], notes: [],
        market_population_adults: false }""")
    assert with_cut.count("Unique IDs reached") == 1 and 'title="Addressable pool: 15 IDs' in with_cut
    assert "Addressable pool" not in _visible(plain) and "Addressable pool" not in _visible(with_cut)


# ---------------------------------------------------------------------------------------------------------------------
# Hispanic targeting
# ---------------------------------------------------------------------------------------------------------------------

HISP_CUT = '{ mode: "cut", share: 0.467, levels: { area: 1, dma: 0, state_proxy: 0, none: 0 }, vintage: "ACS 2023 5-year" }'
HISP_ALREADY = '{ mode: "already_targeted", share: 0.467, levels: { area: 1 }, vintage: "ACS 2023 5-year" }'


def _aud(hispanic):
    return (f'{{ imps_low: 1, imps_mid: 125408, imps_high: 3, underlying_population: 7800000, confidence_tier: "audience_modeled_single_cut", '
            f'cuts_applied: 1, interests: [], notes: [], hispanic: {hispanic}, by_area: [] }}')


def test_the_audience_box_says_what_share_the_hispanic_cut_used_and_where_it_came_from():
    out = run(f"""const data = {{ months: 1, imps_low: 1, imps_mid: 268459, imps_high: 3, audience: {_aud(HISP_CUT)} }};
        return JSON.stringify(P.audienceBoxHtml(data.audience, P.summarize(data)));""")
    assert "Hispanic targeting" in out and "46.7% of the people here are Hispanic" in out and "Census ACS 2023 5-year" in out
    assert "avails narrowed to each area" in out and "already built" not in out


def test_an_already_hispanic_product_says_no_further_cut_was_applied():
    out = run(f"""const data = {{ months: 1, imps_low: 1, imps_mid: 268459, imps_high: 3, audience: {_aud(HISP_ALREADY)} }};
        return JSON.stringify(P.audienceBoxHtml(data.audience, P.summarize(data)));""")
    assert "already built from Hispanic audiences" in out and "no further cut was applied" in out and "narrowed to each area" not in out


def test_a_geography_with_no_census_share_says_so():
    out = run(f"""const data = {{ months: 1, imps_low: 1, imps_mid: 1, imps_high: 3, audience: {_aud('{ mode: "cut", share: null, levels: {}, vintage: null }')} }};
        return JSON.stringify(P.audienceBoxHtml(data.audience, P.summarize(data)));""")
    assert "no Census share was available for this geography" in out and "NaN" not in out and "null" not in out


def test_the_result_carries_a_hispanic_badge_and_the_copied_summary_a_line():
    out = run(f"""const data = {{ product_name: "X", months: 1, imps_low: 1, imps_mid: 268459, imps_high: 3, notes: [], audience: {_aud(HISP_CUT)}, series: {{ unit: "monthly" }} }};
        return JSON.stringify({{ badges: P.badgesHtml(data), copy: P.summaryText(data) }});""")
    assert "Hispanic targeting · 46.7%" in out["badges"] and "Census ACS" in out["badges"]
    assert "Hispanic targeting: avails narrowed to each area’s Hispanic share (46.7% of the people here are Hispanic, Census ACS 2023 5-year)" in out["copy"]
    already = run(f"""const data = {{ product_name: "X", months: 1, imps_low: 1, imps_mid: 268459, imps_high: 3, notes: [], audience: {_aud(HISP_ALREADY)}, series: {{ unit: "monthly" }} }};
        return JSON.stringify({{ badges: P.badgesHtml(data), copy: P.summaryText(data) }});""")
    assert "Hispanic targeting" in already["badges"] and "·" not in already["badges"].split("Hispanic targeting")[1].split("</span>")[0]
    assert "already built from Hispanic audiences (no further cut applied)" in already["copy"]
    plain = run("""const data = { product_name: "X", months: 1, imps_low: 1, imps_mid: 1, imps_high: 3, notes: [], series: { unit: "monthly" } };
        return JSON.stringify({ badges: P.badgesHtml(data), copy: P.summaryText(data) });""")
    assert "Hispanic" not in plain["badges"] and "Hispanic" not in plain["copy"]


def test_the_step_05_card_has_its_own_hispanic_switch_and_says_why_it_is_on():
    def toggle(targeting, opts="{ onHispanic: function () {} }"):
        return run(f"return JSON.stringify(P.hispanicToggleHtml({{ hispanic_targeting: {targeting} }}, {opts}));")
    on_default = toggle('{ default: true, on: true }')
    assert 'class="cz-hispanic-toggle"' in on_default and "checked" in on_default and "on by default for this product" in on_default
    from_proposal = toggle('{ default: false, on: true }', "{ onHispanic: function () {}, hispanicFromProposal: true }")
    assert "checked" in from_proposal and "from the proposal’s targeting" in from_proposal
    off = toggle('{ default: false, on: false }')
    assert "checked" not in off and "default for this product" not in off and "proposal" not in off
    chosen_on = toggle('{ default: false, on: true }')
    assert "checked" in chosen_on and "<small>" not in chosen_on                       # the planner's own choice needs no explanation
    assert run("return JSON.stringify(P.hispanicToggleHtml({}, { onHispanic: function () {} }));") == ""           # TikTok: no switch
    assert run("return JSON.stringify(P.hispanicToggleHtml({ hispanic_targeting: { default: false, on: true } }, {}));") == ""


def test_the_dock_reads_a_products_hispanic_default_from_the_catalog():
    out = run("""state.catalog = { families: ["F"], products_by_family: { F: [
        { name: "A", forecast: { enabled: true, method: "platform", hispanic_default: true, series: { key: "cumulative", targeting: "general", channel: "platform_model" } } },
        { name: "B", forecast: { enabled: true, method: "platform", hispanic_weighted: true, series: { key: "x", targeting: "general" } } },
        { name: "C", forecast: { enabled: true, method: "platform", hispanic_default: true, series: { key: "hispanic-connect-default", targeting: "hispanic" } } },
        { name: "D", forecast: { enabled: true, method: "platform", series: { key: "vix-select", targeting: "spanish_language" } } },
        { name: "E", forecast: { enabled: true, method: "generic" } } ] } };
        return JSON.stringify(P.loadProducts().map(p => [p.name, p.hispanicDefault, p.hispanicAlready]));""")
    assert out == [["A", True, False], ["B", True, False], ["C", True, True], ["D", False, True], ["E", False, False]]


def test_the_dock_has_the_hispanic_checkbox_and_only_sends_a_choice_the_planner_made():
    assert 'id="cz-hispanic"' in CZ_JS and "cz-hispanic-note" in CZ_JS
    assert CZ_JS.index('id="cz-hispanic"') > CZ_JS.index('id="cz-aud"')                      # inside the Audience section
    assert "...(!plug && S.hispanic !== null && !(productByName(S.product) || {}).hispanicAlready ? { hispanic: S.hispanic } : {})" in CZ_JS   # null = the product's default: the server decides
    assert CZ_JS.count("hispanic: null") >= 3                                                   # initial state and both resets
    assert "e.target.checked === !!p.hispanicDefault" in CZ_JS and 'S.hispanic = (p && e.target.checked' in CZ_JS
    assert "opts.hispanic != null" in CZ_JS


@pytest.mark.parametrize("fields,expected", [
    ({"demo": "Hispanic adults 25-54"}, True), ({"target_override": "Latino households"}, True), ({"behavioral": "Latinas who shop online"}, True),
    ({"demo": "Adults 25-54", "language": "Spanish"}, False), ({"demo": "Adults 25-54"}, False), ({}, False),
    ({"demo": "Hispanic-owned small businesses"}, True), ({"demo": "anti-hispanicize"}, False),
    ({"demo": "General market, non-Hispanic adults 25-54"}, False), ({"demo": "Adults, not Latino"}, False), ({"demo": "excluding the Hispanic segment"}, False),
    ({"demo": "non-Hispanic whites and Latina moms"}, True)])
def test_a_proposal_that_names_a_hispanic_audience_turns_the_cards_switch_on(fields, expected):
    li = {k: v for k, v in fields.items() if k.startswith("target")}
    parsed = {k: v for k, v in fields.items() if not k.startswith("target")}
    fns = "\n".join(H._extract_fn(APP_JS, n) for n in ("_targetsHispanic", "_constanzaHispanicFor"))
    body = (f"var state = {{ parsed: {json.dumps(parsed)}, constanzaHispanic: {{}} }};\n" + fns + "\n"
            f"return JSON.stringify(_targetsHispanic({json.dumps(li)}));")
    assert json.loads(H._run_js(body)) is expected


def _hispanic_for(parsed, lines, products, mine, calls):
    fns = "\n".join(H._extract_fn(APP_JS, n) for n in ("_targetsHispanic", "_constanzaHispanicFor"))
    body = (f"var state = {{ parsed: {json.dumps(parsed)}, constanzaHispanic: {json.dumps(mine)}, productIndex: {json.dumps(products)} }};\n" + fns + "\n"
            f"var lines = {json.dumps(lines)};\n"
            f"return JSON.stringify({calls});")
    return json.loads(H._run_js(body))


PLATFORM = {"forecast": {"method": "platform"}}
GENERIC = {"forecast": {"method": "generic"}}
TIKTOK = {"forecast": {"method": "tiktok"}}


def test_the_planners_own_switch_beats_the_proposals_targeting():
    out = _hispanic_for({"demo": "Hispanic adults"}, {}, {"P": PLATFORM}, {"a": False, "b": True},
                        "[_constanzaHispanicFor('a', { product_name: 'P' }), _constanzaHispanicFor('b', { product_name: 'P' }), _constanzaHispanicFor('c', { product_name: 'P' }),"
                        " (state.parsed = { demo: 'Adults' }, _constanzaHispanicFor('c', { product_name: 'P' }))]")
    assert out == [False, True, True, None]


def test_only_a_product_forecast_from_platform_data_is_ever_sent_the_hispanic_flag():
    """A population-share estimate spreads the planner's OWN impression total: cutting it to the Hispanic share would shrink the plan itself."""
    out = _hispanic_for({"demo": "Hispanic adults 25-54"}, {}, {"P": PLATFORM, "G": GENERIC, "T": TIKTOK}, {"G": True},
                        "[_constanzaHispanicFor('x', { product_name: 'P' }), _constanzaHispanicFor('x', { product_name: 'G' }), _constanzaHispanicFor('x', { product_name: 'T' }),"
                        " _constanzaHispanicFor('G', { product_name: 'G' }), _constanzaHispanicFor('x', { product_name: 'unknown' }), _constanzaHispanicFor('x', null)]")
    assert out == [True, None, None, None, None, None]


def test_a_lines_own_target_text_replaces_the_campaigns_not_adds_to_it():
    """Step 04's per-line target replaces the campaign targeting everywhere else (_effectiveTargetText); a Hispanic campaign can have a general-market line."""
    cases = [({"product_name": "P"}, True), ({"product_name": "P", "target_override": "Adults 18+ general market"}, False),
             ({"product_name": "P", "target_override": "Non-Hispanic white adults 35+"}, False), ({"product_name": "P", "target_override": "Hispanic adults 18-34"}, True),
             ({"product_name": "P", "target_override": "  "}, True), ({"product_name": "P", "target_override": "Adults", "target_secondary": "Latino households"}, True)]
    out = _hispanic_for({"demo": "Hispanic adults 25-54"}, {}, {"P": PLATFORM}, {}, "lines" if False else "[" + ",".join(f"_constanzaHispanicFor('x', {json.dumps(li)})" for li, _ in cases) + "]")
    assert out == [True if want else None for _, want in cases]


@pytest.mark.parametrize("text,expected", [
    ("Hispanic and non-Hispanic adults", False), ("General market (Hispanic and non-Hispanic)", False), ("Not targeting Hispanic", False), ("w/o Hispanic", False),
    ("excl. Hispanic", False), ("exclude all Hispanic and Latino audiences", False), ("avoid Hispanic audiences", False), ("Hispanic vs non-Hispanic", False),
    ("not for Hispanic audiences", False), ("Hispanic vs. non-Hispanic", False), ("Latino vs. not Latino", False), ("Hispanic adults", True), ("non-Hispanic whites and Latina moms", True), ("Latinx and Hispanic women", True)])
def test_the_text_matchers_in_js_and_python_agree_on_what_leaves_the_audience_out(text, expected):
    from app.services.constanza_audience import mentions_hispanic_audience
    out = _hispanic_for({"demo": text}, {}, {}, {}, "_targetsHispanic({})")
    assert out is expected and mentions_hispanic_audience(text) is expected


def test_a_cut_that_could_not_run_says_so_and_never_claims_to_have_narrowed():
    na = '{ mode: "not_applied", share: null, levels: { none: 1 }, vintage: "ACS 2023 5-year" }'
    out = run(f"""const data = {{ product_name: "X", months: 1, imps_low: 1, imps_mid: 100, imps_high: 3, notes: [], series: {{ unit: "monthly" }},
        audience: {{ imps_low: 1, imps_mid: 100, imps_high: 3, underlying_population: 5, confidence_tier: "audience_modeled_geo_only", cuts_applied: 0, interests: [],
        notes: ["Hispanic targeting was asked for but could not be applied: there is no Census data for this geography, so the estimate is not narrowed to Hispanic audiences."],
        hispanic: {na}, by_area: [] }} }};
        return JSON.stringify({{ box: P.audienceBoxHtml(data.audience, P.summarize(data)), badges: P.badgesHtml(data), copy: P.summaryText(data) }});""")
    assert "Hispanic targeting not applied" in out["box"] and "narrowed to each area" not in out["box"] and out["box"].count("could not be applied") == 0
    assert "Hispanic targeting · not applied" in out["badges"] and "· 0%" not in out["badges"]
    assert "Hispanic targeting: not applied (no Census data for this geography)" in out["copy"]


def test_the_audience_box_says_the_hispanic_story_once_with_its_assumption_and_keeps_the_warnings():
    out = run(f"""const data = {{ months: 1, imps_low: 1, imps_mid: 268459, imps_high: 3, audience: {{ imps_low: 1, imps_mid: 125408, imps_high: 3, underlying_population: 7800000,
        confidence_tier: "audience_modeled_single_cut", cuts_applied: 1, interests: [], hispanic: {HISP_CUT}, by_area: [], notes: [
        "Hispanic targeting: the platform's avails describe everyone it reaches, so each area's were multiplied by its Hispanic share of the population (47% of the people here are Hispanic; Census ACS 2023 5-year). This assumes x.",
        "2 areas too small for a reliable Census share used their parent DMA's Hispanic share.",
        "Hispanic targeting was NOT applied to Foo (no Census data for it) — those areas are counted at full size, so the total reads high."] }} }};
        return JSON.stringify(P.audienceBoxHtml(data.audience, P.summarize(data)));""")
    assert out.count("Hispanic targeting") == 2                                           # the line (once) and the "was NOT applied" warning: nothing else repeats it
    assert "46.7% of the people here are Hispanic, Census ACS 2023 5-year" in out and "in proportion to their population" in out and "(Census ACS 2023 5-year))" not in out
    assert "multiplied by its Hispanic share" not in out                                 # the server's duplicate of the line above is not repeated
    assert "too small for a reliable Census share" in out and "reads high" in out        # its warnings stay


def test_no_card_switch_where_hispanic_targeting_does_not_apply_or_is_the_products_own_data():
    def toggle(data):
        return run(f"return JSON.stringify(P.hispanicToggleHtml({data}, {{ onHispanic: function () {{}} }}));")
    assert toggle('{ hispanic_targeting: { default: false, on: false, applies: false } }') == ""                          # a population-share or TikTok estimate
    assert toggle(f'{{ hispanic_targeting: {{ default: true, on: true, applies: true }}, audience: {{ hispanic: {HISP_ALREADY} }} }}') == ""   # the data IS the audience
    assert 'class="cz-hispanic-toggle"' in toggle(f'{{ hispanic_targeting: {{ default: true, on: true, applies: true }}, audience: {{ hispanic: {HISP_CUT} }} }}')


def test_the_dock_resets_its_hispanic_choice_per_product_and_never_sends_off_for_an_already_hispanic_one():
    assert "if (name !== S.product) S.hispanic = null;" in CZ_JS                                   # a choice belongs to the product it was made for
    assert "box.disabled = !!(p && p.hispanicAlready);" in CZ_JS and "if (p && p.hispanicAlready) return true;" in CZ_JS
    assert "(p && e.target.checked === !!p.hispanicDefault) ? null : e.target.checked" in CZ_JS      # back to the default = no preference (no false "form changed" banner)
    assert 'if ($("cz-aud-count")) refreshAudienceUI(); else refreshHispanicUI();' in CZ_JS            # the Audience badge follows the product


def test_the_share_of_voice_helper_says_when_the_avails_were_also_narrowed_to_the_hispanic_audience():
    def helper(entry):
        fns = "\n".join(H._extract_fn(APP_JS, n) for n in ("sovTier", "applySovDisplay"))
        body = ("var state = { availsData: { a: " + json.dumps(entry) + " }, lineItems: [], timeUnit: 'month' };\n"
                "var out = { badge: { className: '', textContent: '' }, helper: { className: '', textContent: '' } };\n"
                "var document = { getElementById: function (id) { return id.indexOf('badge') >= 0 ? out.badge : out.helper; } };\n"
                "function escapeAttr(x) { return x; }\nfunction _isFullFlight() { return false; }\nfunction _moneyExact(x) { return x; }\n" + fns +
                "\napplySovDisplay('a', 85);\nreturn JSON.stringify({ badge: out.badge.textContent, helper: out.helper.textContent });")
        return json.loads(H._run_js(body))
    both = helper({"win_rate": 0.15, "hispanic_share": 0.467})
    assert both["badge"] == "85% of expected delivery" and "the avails x a 15% win rate x the 46.7% Hispanic share" in both["helper"]
    only = helper({"hispanic_share": 0.467})
    assert only["badge"] == "85% of Hispanic avails" and "(the avails x the 46.7% Hispanic share)" in only["helper"]
    plain = helper({"win_rate": 0.15})
    assert plain["badge"] == "85% of expected delivery" and "Hispanic" not in plain["helper"] and "(the avails x a 15% win rate)" in plain["helper"]
    assert helper({})["badge"] == "85% of avails"
    assert "delete entry.hispanic_share;" in APP_JS and "entry.hispanic_share = geoInfo.hispanic_share" in APP_JS
