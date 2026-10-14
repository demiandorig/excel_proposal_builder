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
          underlying_population: 2049177, reliability_floor_population: 10000, below_reliability_threshold: false,
          confidence_tier: "audience_modeled_stacked", interests: [{ supported: true, label: "Auto Intenders", rate: 0.31 }], notes: [] } };
        return JSON.stringify(P.audienceBoxHtml(data.audience, P.summarize(data)));""")
    assert "Audience on this product" in out and "76,022" in out and "of the 487,600 it reaches here" in out and "5.3 impressions per person a month" in out
    assert "Market population (census)" in out and "2,049,177 adults" in out and "across all media" in out
    assert "Underlying audience" not in out


def test_without_a_reach_universe_the_audience_box_shows_only_the_market_population_and_says_why():
    out = run("""const data = { months: 1, imps_low: 2, imps_mid: 100, imps_high: 3,
        audience: { imps_low: 1, imps_mid: 50, imps_high: 60, audience_people: null, est_uniques: null, underlying_population: 5000,
          reliability_floor_population: 10000, below_reliability_threshold: true, confidence_tier: "audience_modeled_single_cut", interests: [], notes: [] } };
        return JSON.stringify(P.audienceBoxHtml(data.audience, P.summarize(data)));""")
    assert "Market population (census)" in out and "no audience count" in out and "below the ~10,000 reliability floor" in out
    assert "Audience on this product" not in out


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
