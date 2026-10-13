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
