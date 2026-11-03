"""SegmentBrowser (app/static/segment-browser.js): the pure helpers (sort comparator, natural-sort key, search filter, highlight ranges, size
formatting, the apply diff) and the static contracts of the two stylesheets. Like test_constanza_tiktok_frontend.py, the JavaScript runs in a
JavaScript engine (Node when installed, else macOS's JavaScriptCore) with a stub `window`: no browser, no DOM. The dialog itself, in a real
browser, is tests/test_segment_browser_dom.py."""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SB_JS = (ROOT / "app" / "static" / "segment-browser.js").read_text(encoding="utf-8")
SB_CSS = (ROOT / "app" / "static" / "segment-browser.css").read_text(encoding="utf-8")
KIT_CSS = (ROOT / "app" / "static" / "audience-kit.css").read_text(encoding="utf-8")
INDEX = (ROOT / "app" / "templates" / "index.html").read_text(encoding="utf-8")


def _harness():
    path = ROOT / "tests" / "test_full_flight_frontend.py"
    spec = importlib.util.spec_from_file_location("_ff_frontend_harness_sb", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


H = _harness()


def run(snippet: str):
    """Loads segment-browser.js against a stub `window` and runs `snippet` (which returns a JSON string); P is SegmentBrowser._pure."""
    body = "var window = {};\n" + SB_JS + "\nvar P = window.SegmentBrowser._pure;\nvar SB = window.SegmentBrowser;\n" + snippet
    try:
        out = H._run_js(body)
    except AssertionError as exc:
        if "JS engine failed" in str(exc) and "osascript" in str(exc).lower():
            pytest.skip(f"JavaScript engine unusable here: {exc}")
        raise
    return json.loads(out)


# --------------------------------------------------------------------------------------------- the public surface

def test_the_module_exposes_exactly_open_close_isopen_and_the_pure_helpers():
    assert run("return JSON.stringify(Object.keys(SB).sort());") == ["_pure", "close", "isOpen", "open"]
    assert run("return JSON.stringify([SB.isOpen(), typeof SB.close(), typeof SB.open]);") == [False, "undefined", "function"]


def test_loading_the_file_does_not_touch_the_page():
    """It is a plain script tag before app.js: nothing may run at load time that needs a document."""
    assert run("return JSON.stringify(typeof document);") == "undefined"


def test_open_without_a_document_does_nothing_instead_of_throwing():
    assert run("var h = SB.open({ title: 'x' }); return JSON.stringify([typeof h.close, h.close()]);") == ["function", None]


# --------------------------------------------------------------------------------------------- the natural-sort key

def test_the_natural_key_splits_text_and_numbers():
    assert run("return JSON.stringify(P.naturalKey('Age 25-34'));") == ["age ", 25, "-", 34]
    assert run("return JSON.stringify(P.naturalKey('  Item   007 '));") == ["item ", 7]
    assert run("return JSON.stringify([P.naturalKey(''), P.naturalKey(null), P.naturalKey(undefined), P.naturalKey('   ')]);") == [[], [], [], []]
    assert run("return JSON.stringify(P.naturalKey(42));") == [42]


def test_the_natural_key_ignores_case_and_accents():
    assert run("return JSON.stringify([P.naturalKey('ESPAÑOL'), P.naturalKey('Español'), P.naturalKey('espanol')]);") == [["espanol"]] * 3
    assert run("return JSON.stringify(P.naturalKey('Cafe\\u0301'));") == ["cafe"]                   # a decomposed accent too


def test_numbers_inside_names_compare_as_numbers_and_case_does_not_matter():
    out = run("""
      var names = ['Segment 10', 'segment 2', 'Segment 1', 'SEGMENT 100', 'Segment 20', 'segment 3'];
      return JSON.stringify(names.slice().sort(P.compareNatural));""")
    assert out == ["Segment 1", "segment 2", "segment 3", "Segment 10", "Segment 20", "SEGMENT 100"]


def test_natural_order_edge_cases():
    out = run("""
      var c = P.compareNatural;
      return JSON.stringify({
        shorterFirst: c('Item', 'Item 2'), equalOnCase: c('abc', 'ABC'), numberBeforeText: c('9', 'abc'), textAfterNumber: c('abc', '9'),
        accents: c('Árbol', 'arbol'), ages: ['65+', '18-24', '25-34', '5-9'].sort(c), leadingZeros: c('a007', 'a7'), huge: c('a99999999999999999999', 'a99999999999999999998'),
        mixed: ['b1', 'a10', 'a2', 'B2', 'a1'].sort(c),
      });""")
    assert out["shorterFirst"] < 0 and out["equalOnCase"] == 0 and out["accents"] == 0 and out["leadingZeros"] == 0
    assert out["numberBeforeText"] < 0 and out["textAfterNumber"] > 0
    assert out["ages"] == ["5-9", "18-24", "25-34", "65+"]
    assert out["mixed"] == ["a1", "a2", "a10", "b1", "B2"]
    assert out["huge"] in (0, -1, 1)                                                        # too big for a double: no crash, any consistent answer


# --------------------------------------------------------------------------------------------- the column comparator

CASES_NUMERIC = """
  var col = { key: 'size', numeric: true };
  var items = [{ id: 'a', size: 10 }, { id: 'b' }, { id: 'c', size: 5 }, { id: 'd', size: 20 }, { id: 'e', size: null }, { id: 'f', size: 5 },
               { id: 'g', size: 'abc' }, { id: 'h', size: NaN }, { id: 'i', size: '12' }, { id: 'j', size: '' }, { id: 'k', size: 0 }, { id: 'l', size: -3 }];
  function order(dir) { return P.sortedIndices(items, col, dir).map(function (i) { return items[i].id; }); }
"""


def test_a_numeric_column_sorts_numerically_with_missing_values_last_in_both_directions():
    out = run(CASES_NUMERIC + "return JSON.stringify({ asc: order('asc'), desc: order('desc') });")
    assert out["asc"] == ["l", "k", "c", "f", "a", "i", "d", "b", "e", "g", "h", "j"]       # "12" counts as 12; b e g h j are blank and keep their order
    assert out["desc"] == ["d", "i", "a", "c", "f", "k", "l", "b", "e", "g", "h", "j"]      # ties (c, f) keep their order; blanks are still last


def test_the_comparator_function_matches_the_sort_and_treats_blanks_as_equal():
    out = run(CASES_NUMERIC + """
      var asc = P.comparator(col, 'asc'), desc = P.comparator(col, 'desc');
      var A = items[0], B = items[1], C = items[2], E = items[4];
      return JSON.stringify({ aLessC: asc(C, A) < 0, descFlips: desc(C, A) > 0, blankLastAsc: asc(A, B) < 0, blankLastDesc: desc(A, B) < 0, blankVsBlank: asc(B, E),
        zeroVsNothing: asc(items[10], B) < 0 });""")
    assert out == {"aLessC": True, "descFlips": True, "blankLastAsc": True, "blankLastDesc": True, "blankVsBlank": 0, "zeroVsNothing": True}


def test_a_text_column_sorts_naturally_and_blanks_go_last_both_ways():
    out = run("""
      var col = { key: 'group' };
      var items = [{ id: 1, group: 'beta 10' }, { id: 2 }, { id: 3, group: 'Alpha' }, { id: 4, group: '' }, { id: 5, group: 'beta 9' }, { id: 6, group: null }, { id: 7, group: '  ' }];
      function order(dir) { return P.sortedIndices(items, col, dir).map(function (i) { return items[i].id; }); }
      return JSON.stringify({ asc: order('asc'), desc: order('desc') });""")
    assert out["asc"] == [3, 5, 1, 2, 4, 6, 7]
    assert out["desc"] == [1, 5, 3, 2, 4, 6, 7]


def test_a_column_can_sort_by_a_value_function_and_the_default_category_falls_back_to_the_path():
    out = run("""
      var cols = P.defaultColumns();
      var cat = cols[1];
      var items = [{ id: 1, name: 'x', path: ['Z', 'a'] }, { id: 2, name: 'y', group: 'B' }, { id: 3, name: 'z', path: 'M' }, { id: 4, name: 'w' }];
      return JSON.stringify({ keys: cols.map(function (c) { return c.key; }), cats: items.map(P.categoryOf),
        order: P.sortItems(items, cat, 'asc').map(function (i) { return i.id; }) });""")
    assert out["keys"] == ["name", "category"]
    assert out["cats"] == ["Z › a", "B", "M", ""]
    assert out["order"] == [2, 3, 1, 4]                                                      # B, M, Z › a, then the blank one


def test_sorting_is_stable_and_leaves_the_input_alone():
    out = run("""
      var items = []; for (var i = 0; i < 200; i++) items.push({ id: i, size: i % 3 });
      var col = { key: 'size', numeric: true }, before = JSON.stringify(items);
      var asc = P.sortItems(items, col, 'asc').map(function (x) { return x.id; });
      var desc = P.sortItems(items, col, 'desc').map(function (x) { return x.id; });
      function stable(list) { for (var i = 1; i < list.length; i++) if (items[list[i - 1]].size === items[list[i]].size && list[i - 1] > list[i]) return false; return true; }
      return JSON.stringify({ untouched: JSON.stringify(items) === before, ascStable: stable(asc), descStable: stable(desc), n: asc.length });""")
    assert out == {"untouched": True, "ascStable": True, "descStable": True, "n": 200}


def test_ten_thousand_random_items_come_out_ordered_and_complete():
    out = run("""
      var seed = 12345; function rnd() { seed = (seed * 1103515245 + 12345) % 2147483648; return seed / 2147483648; }
      var items = []; for (var i = 0; i < 10000; i++) items.push({ id: i, name: 'Item ' + Math.floor(rnd() * 500) + ' ' + String.fromCharCode(97 + Math.floor(rnd() * 5)), size: rnd() < 0.1 ? null : Math.floor(rnd() * 1000) });
      var byName = P.sortedIndices(items, { key: 'name' }, 'asc'), bySize = P.sortedIndices(items, { key: 'size', numeric: true }, 'desc');
      var okName = true; for (var i = 1; i < byName.length; i++) if (P.compareNatural(items[byName[i - 1]].name, items[byName[i]].name) > 0) okName = false;
      var okSize = true, seenNull = false;
      for (var i = 0; i < bySize.length; i++) { var s = items[bySize[i]].size; if (s === null) seenNull = true; else if (seenNull) okSize = false; else if (i && items[bySize[i - 1]].size !== null && items[bySize[i - 1]].size < s) okSize = false; }
      var perm = byName.slice().sort(function (a, b) { return a - b; }).every(function (v, i) { return v === i; });
      return JSON.stringify({ okName: okName, okSize: okSize, perm: perm, n: byName.length });""")
    assert out == {"okName": True, "okSize": True, "perm": True, "n": 10000}


# --------------------------------------------------------------------------------------------- the search filter

def _matches(cases: list[tuple[str, dict]]) -> list[bool]:
    """One engine launch for many (query, item) pairs."""
    return run(f"return JSON.stringify({json.dumps(cases)}.map(function (c) {{ return P.filterPredicate(c[0])(c[1]); }}));")


def test_every_typed_word_must_match_in_any_order():
    item = {"id": "1", "name": "Skincare products", "group": "Beauty & Personal Care"}
    got = _matches([("skin beauty", item), ("beauty skin", item), ("care", item), ("skin fashion", item)])
    assert got == [True, True, True, False]


def test_the_filter_looks_in_name_group_path_and_description_only():
    item = {"id": "zzz9", "name": "Alpha", "group": "Bravo", "path": ["Charlie", "Delta"], "description": "Echo foxtrot", "size": 424242}
    queries = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "charlie delta", "zzz9", "424242"]
    got = _matches([(q, item) for q in queries] + [("delta", {"id": "1", "name": "n", "path": "Charlie › Delta"})])   # the last: a path given as a string
    assert got == [True] * 7 + [False, False, True]                                                                    # not the id, not the size


def _matches_in(columns: list[dict], cases: list[tuple[str, dict]]) -> list[bool]:
    return run(f"var cols = {json.dumps(columns)}; return JSON.stringify({json.dumps(cases)}.map(function (c) {{ return P.filterPredicate(c[0], cols)(c[1]); }}));")


def test_the_filter_also_looks_in_the_text_of_every_non_numeric_column():
    """The TikTok language pop-up shows a "TikTok code" column: typing a code you can SEE must find the row ("pt" found nothing, "es" found the wrong ones)."""
    cols = [{"key": "name", "label": "Language"}, {"key": "code", "label": "TikTok code"}, {"key": "size", "label": "Audience size", "numeric": True}]
    pt = {"id": "x1", "name": "Portuguese", "code": "pt", "size": 424242}
    es = {"id": "x2", "name": "Spanish", "code": "es"}
    got = _matches_in(cols, [("pt", pt), ("zh", pt), ("es", es), ("ja", pt), ("spanish es", es), ("424242", pt), ("x1", pt), ("portuguese pt", pt)])
    assert got == [True, False, True, False, True, False, False, True]           # a numeric column and the id are still never searched
    assert _matches([("pt", pt)]) == [False]                                       # without columns the filter is what it always was


def test_a_value_the_column_formats_is_searchable_as_shown_and_a_failing_format_does_not_break_it():
    cols = [{"key": "name"}, {"key": "label", "label": "Category"}]
    got = run(f"""var cols = {json.dumps(cols)};
      cols[1].format = function (v, it) {{ if (it.id === 'boom') throw new Error('x'); return v || (it.top ? 'Top level' : '—'); }};
      var items = [{{ id: 'a', name: 'Skincare', top: true }}, {{ id: 'b', name: 'Makeup', label: 'Beauty' }}, {{ id: 'boom', name: 'Hats', label: 'Zzz' }}];
      function ids(q) {{ return items.filter(P.filterPredicate(q, cols)).map(function (i) {{ return i.id; }}); }}
      return JSON.stringify([ids('top level'), ids('beauty'), ids('zzz'), ids('hats')]);""")
    assert got == [["a"], ["b"], [], ["boom"]]                                     # "Top level" is on screen, so it is searchable; the broken cell is just empty


def test_the_haystack_does_not_repeat_what_a_column_already_says():
    got = run("return JSON.stringify(P.haystack({ id: 'i', name: 'Alpha', group: 'Bravo' }, P.defaultColumns()));")
    assert got == "alpha\nbravo"                                                    # the Category column shows the group: no second copy


def test_the_filter_ignores_case_and_accents_and_blank_queries_match_everything():
    item = {"id": "1", "name": "Español (México)"}
    got = _matches([("ESPANOL mexico", item), ("español", item), ("MÉXICO", item), ("", item), ("   ", item), ("english", item)])
    assert got == [True, True, True, True, True, False]


def test_a_word_cannot_match_across_two_fields():
    item = {"id": "1", "name": "ab", "group": "cd"}
    assert _matches([("abcd", item), ("bc", item), ("b c", item), ("ab cd", item)]) == [False, False, True, True]   # each word on its own is fine


def test_punctuation_and_regex_characters_in_a_query_are_just_text():
    got = _matches([("25-34", {"id": "1", "name": "Ages 25-34"}), ("(", {"id": "1", "name": "Hispanic (US)"}), ("[", {"id": "1", "name": "a [b] c"}),
                    (".*", {"id": "1", "name": "x.*y"}), (".*", {"id": "1", "name": "xy"}), ("\\", {"id": "1", "name": "none"})])
    assert got == [True, True, True, True, False, False]


def test_items_with_missing_fields_do_not_break_the_filter():
    assert _matches([("a", {"id": "1", "name": "a"}), ("a", {"id": "1"}), ("", {"id": "1"})]) == [True, False, True]
    assert run("return JSON.stringify(P.haystack({ id: 1 }));") == ""


def test_search_words_split_on_any_whitespace_and_fold():
    assert run("return JSON.stringify(P.searchWords('  Español\\tMÉXICO \\n x '));") == ["espanol", "mexico", "x"]


# --------------------------------------------------------------------------------------------- highlight ranges

def test_highlight_ranges_cover_each_word_and_merge_overlaps():
    assert run("return JSON.stringify(P.highlightRanges('Skincare products', ['skin']));") == [[0, 4]]
    assert run("return JSON.stringify(P.highlightRanges('Skincare skin', ['skin']));") == [[0, 4], [9, 13]]
    assert run("return JSON.stringify(P.highlightRanges('abcdef', ['abc', 'cde']));") == [[0, 5]]
    assert run("return JSON.stringify(P.highlightRanges('abcdef', ['bc', 'xyz']));") == [[1, 3]]
    assert run("return JSON.stringify([P.highlightRanges('abc', []), P.highlightRanges('', ['a']), P.highlightRanges(null, ['a']), P.highlightRanges('abc', ['z'])]);") == [[], [], [], []]


def test_highlight_ranges_map_back_through_accents_to_the_original_text():
    assert run("return JSON.stringify(P.highlightRanges('Español', ['espanol']));") == [[0, 7]]
    assert run("return JSON.stringify(P.highlightRanges('Espan\\u0303ol', ['espanol']));") == [[0, 8]]   # a decomposed ñ is two code units
    assert run("return JSON.stringify(P.highlightRanges('El Niño ñ', ['nino']));") == [[3, 7]]
    assert run("return JSON.stringify(P.highlightRanges('ÁRBOL árbol', ['arbol']));") == [[0, 5], [6, 11]]


# --------------------------------------------------------------------------------------------- sizes

def test_compact_numbers():
    out = run("""return JSON.stringify([0, 7, 999, 1000, 1049, 1050, 1500, 9999, 10000, 15000, 123456, 999499, 999999, 1000000, 1234567, 15000000, 999999999, 2000000000, 1.5e12, -2500]
      .map(P.compact));""")
    assert out == ["0", "7", "999", "1K", "1K", "1.1K", "1.5K", "10K", "10K", "15K", "123K", "999K", "1M", "1M", "1.2M", "15M", "1B", "2B", "1500B", "-2.5K"]
    assert run("return JSON.stringify([P.compact(NaN), P.compact(Infinity), P.compact('x')]);") == ["—", "—", "—"]


def test_the_size_column_text():
    out = run("""return JSON.stringify([
      { size: 1234567 }, { size: 1200000, sizeUpper: 1500000 }, { size: 1500000, sizeUpper: 1500000 }, { size: 1500000, sizeUpper: 900000 },
      {}, { size: null }, { size: undefined, sizeUpper: 5 }, { size: 0 }, { size: '2500' }, { size: 950 }, { size: 'n/a' }].map(P.formatSize));""")
    assert out == ["1.2M", "1.2M – 1.5M", "1.5M", "1.5M", "—", "—", "—", "0", "2.5K", "950", "—"]


def test_the_size_tooltip_gives_the_full_numbers():
    out = run("return JSON.stringify([{ size: 1200000, sizeUpper: 1500000 }, { size: 950 }, {}].map(P.sizeTitle));")
    assert out == ["1,200,000 to 1,500,000 people", "950 people", ""]


def test_cell_text_for_columns():
    out = run("""
      var item = { id: 1, name: 'N', size: 1200000, rate: 3.5, group: 'G', empty: null };
      function t(col) { return P.cellText(col, item); }
      return JSON.stringify([
        t({ key: 'name' }), t({ key: 'size', numeric: true }), t({ key: 'rate', numeric: true }), t({ key: 'empty', numeric: true }), t({ key: 'empty' }),
        t({ key: 'rate', numeric: true, format: function (v) { return v + '%'; } }), t({ key: 'group', format: function () { throw new Error('boom'); } }),
        t({ key: 'x', value: function (it) { return it.name + '!'; } }), t({ key: 'size', numeric: true, format: function (v, it) { return it.name + v; } }),
        t({ key: 'group', format: function () { return null; } }) ]);""")
    assert out == ["N", "1.2M", "3.5", "—", "", "3.5%", "", "N!", "N1200000", ""]


# --------------------------------------------------------------------------------------------- what Apply sends

def test_the_diff_is_only_the_changes_in_order():
    out = run("""
      var a = { tab: 't', item: { id: 'a' } }, b = { tab: 't', item: { id: 'b' } }, c = { tab: 'u', item: { id: 'c' } }, d = { tab: 'u', item: { id: 'd' } };
      var initial = new Map([['k1', a], ['k2', b]]);
      var picked = new Map([['k2', b], ['k4', d], ['k3', c]]);
      var r = P.diffSelection(initial, picked);
      return JSON.stringify({ added: r.added.map(function (x) { return x.item.id; }), removed: r.removed.map(function (x) { return x.item.id; }), tabs: r.added.map(function (x) { return x.tab; }),
        same: P.diffSelection(initial, new Map(initial)) });""")
    assert out == {"added": ["d", "c"], "removed": ["a"], "tabs": ["u", "u"], "same": {"added": [], "removed": []}}


def test_the_apply_button_text():
    out = run("""return JSON.stringify([P.applyText(undefined, 0, 0), P.applyText(undefined, 3, 0), P.applyText(undefined, 0, 2), P.applyText(undefined, 3, 2),
      P.applyText('Add to audience', 0, 0), P.applyText('Add to audience', 3, 0)]);""")
    assert out == ["Add selected", "Add 3 selected", "Remove 2", "Add 3, remove 2", "Add to audience", "Add to audience (3)"]


def test_a_custom_apply_label_never_says_add_when_it_also_removes():
    """"Add to audience (3)" for one add and two removals, and "Add languages (1)" for a single removal, were the old texts."""
    out = run("""return JSON.stringify([
      P.applyText('Add to audience', 0, 2), P.applyText('Add to audience', 0, 1), P.applyText('Add to audience', 1, 2), P.applyText('Add languages', 3, 1),
      P.applyText('Save', 0, 4), P.applyText('Save', 2, 0), P.applyText('Save', 0, 0)]);""")
    assert out == ["Remove 2", "Remove 1", "Apply changes (3)", "Apply changes (4)", "Remove 4", "Save (2)", "Save"]


def test_the_typed_search_is_shortened_when_it_is_echoed_back():
    out = run("""return JSON.stringify([P.echoQuery('  xyz '), P.echoQuery(''), P.echoQuery(null), P.echoQuery('a'.repeat(40)), P.echoQuery('b'.repeat(41)),
      P.echoQuery('90210,'.repeat(30)).length, P.echoQuery('\\ud83d\\ude00'.repeat(45)).length]);""")
    assert out[:4] == ["xyz", "", "", "a" * 40] and out[4] == "b" * 40 + "…"
    assert out[5] == 41 and out[6] == 40 * 2 + 1                                    # 40 characters and the ellipsis (an emoji is two code units, never cut in half)


def test_singular_nouns():
    assert run("return JSON.stringify(['segments', 'interests', 'categories', 'hashtags'].map(P.singular));") == ["segment", "interest", "category", "hashtag"]


# --------------------------------------------------------------------------------------------- static contracts

def _selectors(css: str) -> list[str]:
    """Every selector of every style rule (flattening @media/@supports; skipping @keyframes bodies), comments removed."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    out: list[str] = []
    stack: list[str] = []                                           # the at-rule each open block belongs to ("" for a style rule)
    start = 0
    for i, ch in enumerate(css):
        if ch == "{":
            prelude = css[start:i].strip()
            if prelude.startswith("@"):
                stack.append(prelude.split()[0])
            else:
                if not (stack and stack[-1] == "@keyframes"):
                    depth, buf = 0, ""
                    for c in prelude + ",":
                        depth += (c in "([") - (c in ")]")
                        if c == "," and depth == 0:
                            out.append(buf.strip())
                            buf = ""
                        else:
                            buf += c
                stack.append("")
            start = i + 1
        elif ch == "}":
            stack.pop()
            start = i + 1
    assert stack == [], "unbalanced braces"
    return [x for x in out if x]


def test_the_dialog_stylesheet_is_scoped_under_segb():
    sels = _selectors(SB_CSS)
    assert len(sels) > 60
    stray = [s for s in sels if ".segb-" not in s]
    assert stray == [], stray
    assert all(re.fullmatch(r"segb-[a-z-]+", name) for name in re.findall(r"@keyframes\s+([\w-]+)", SB_CSS))


# The one selector in the kit that matches on the class PREFIX instead of naming a class: "[hidden] always wins on anything the kit owns".
KIT_HIDDEN_RULE = ':is([class^="aud-"], [class*=" aud-"])[hidden]'


def test_the_kit_stylesheet_is_scoped_under_aud():
    sels = _selectors(KIT_CSS)
    assert len(sels) > 100
    stray = [s for s in sels if ".aud-" not in s and s != ":root" and s != KIT_HIDDEN_RULE]
    assert stray == [], stray
    assert KIT_HIDDEN_RULE in sels                                                          # ...and that exception really is there, exactly once
    assert sels.count(KIT_HIDDEN_RULE) == 1
    assert all(re.fullmatch(r"aud-[a-z-]+", name) for name in re.findall(r"@keyframes\s+([\w-]+)", KIT_CSS))
    custom = set(re.findall(r"(?:^|[\s;{])--([\w-]+)\s*:", re.sub(r"/\*.*?\*/", "", KIT_CSS, flags=re.S)))
    assert custom and all(c.startswith("aud-") for c in custom), custom                          # the only custom properties it defines are --aud-*


# `[class~="aud-builder"]`: an attribute selector naming a kit class without redefining it.
ATTR_REFINEMENT = re.compile(r'\[class[~*^|$]?="(aud-[a-z0-9-]+|segb-[a-z0-9-]+)"\]')


def _own_selectors(css: str) -> list[str]:
    """The selectors of a stylesheet that is not the kit's, with every attribute selector that merely NAMES a kit class taken out of them."""
    return [ATTR_REFINEMENT.sub("[kit-class]", sel) for sel in _selectors(css)]


def test_no_other_stylesheet_already_uses_these_prefixes():
    """The kit's classes are the kit's. The dock refines one of them from outside with an attribute selector (`.cz-method-panel [class~="aud-builder"]`:
    the TikTok panel's markup is not the dock's to add a class to), which is fine; a `.aud-*` / `.segb-*` CLASS selector, an animation or a custom property
    with those prefixes in another sheet would redefine the kit, which is not."""
    for name in ("styles.css", "constanza.css", "admin.css"):
        css = (ROOT / "app" / "static" / name).read_text(encoding="utf-8")
        bare = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
        classes = [sel for sel in _own_selectors(css) if re.search(r"\.(aud|segb)-", sel)]
        assert classes == [], (name, classes)
        assert re.findall(r"@keyframes\s+(?:aud|segb)-", bare) == [], name
        assert re.findall(r"(?:^|[\s;{])--(?:aud|segb)-[\w-]*\s*:", bare) == [], name          # reading --aud-muted is fine; defining it is not


def test_the_prefix_check_tells_a_refinement_from_a_redefinition():
    """The check above must let `.cz-method-panel [class~="aud-builder"]` through and stop `.aud-builder` / `.cz-x .aud-chip` / `[class~=aud-x].segb-row`."""
    ok = '.cz-method-panel [class~="aud-builder"] { counter-reset: none; }'
    assert [x for x in _own_selectors(ok) if re.search(r"\.(aud|segb)-", x)] == []
    for bad in ('.aud-builder { gap: 4px; }', '.cz-x .aud-chip { color: red; }', '.cz-x [class~="aud-chip"].aud-chip--geo { color: red; }',
                '.segb-row:hover { color: red; }', '[class~="aud-chip"] .aud-chip-x { color: red; }'):
        assert [x for x in _own_selectors(bad) if re.search(r"\.(aud|segb)-", x)] != [], bad
    assert _own_selectors(ok) == ['.cz-method-panel [kit-class]']


def test_every_class_the_kit_defines_is_in_its_documentation():
    """The panel authors read only the comment at the top of audience-kit.css: a class that is styled but not listed there would go unused."""
    header = KIT_CSS[: KIT_CSS.index("*/")]
    body = re.sub(r"/\*.*?\*/", "", KIT_CSS, flags=re.S)
    classes = sorted(set(re.findall(r"\.(aud-[a-z0-9-]+)", body)))
    assert len(classes) > 50
    missing = [c for c in classes if c not in header]
    assert missing == [], missing


def test_the_kit_documents_a_minimal_example_for_each_main_component():
    header = KIT_CSS[: KIT_CSS.index("*/")]
    for needle in ('class="aud-builder"', 'class="aud-sec', 'class="aud-sec-head"', 'class="aud-seg"', 'class="aud-quick"', 'class="aud-range"',
                   'class="aud-chips"', 'class="aud-ta"', 'class="aud-browse"', 'class="aud-notice', 'class="aud-actions"', 'class="aud-field"'):
        assert needle in header, needle


def test_the_page_loads_the_dialog_before_the_code_that_uses_it_and_both_stylesheets():
    assert INDEX.index("/static/segment-browser.js") < INDEX.index("/static/app.js")
    assert INDEX.index("/static/segment-browser.js") < INDEX.index("/static/constanza.js")
    for sheet in ("/static/audience-kit.css", "/static/segment-browser.css"):
        assert f'href="{sheet}"' in INDEX
    assert INDEX.index("/static/constanza.css") < INDEX.index("/static/audience-kit.css")             # the kit may refine what the dock sheet sets


def test_the_dialog_script_documents_its_api_at_the_top():
    header = SB_JS[: SB_JS.index("*/")]
    for needle in ("SegmentBrowser.open", "load(tabKey)", "isChosen", "max", "onApply", "returnFocus", "columns", "Item = {", "applyLabel", "chosenCount", "noun",
                   "maxPerTab", "maxNote(tab)", "Apply changes", "Esc"):
        assert needle in header, needle


def test_the_dialog_never_builds_markup_from_item_text():
    """Everything an item contributes goes in through textContent / text nodes. No innerHTML/outerHTML/insertAdjacentHTML/document.write anywhere."""
    code = re.sub(r"/\*.*?\*/", "", SB_JS, flags=re.S)
    for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
        assert bad not in code, bad


def test_the_kit_documents_the_hidden_rule_the_plain_quick_picks_and_the_hover_rule():
    header = KIT_CSS[: KIT_CSS.index("*/")]
    for needle in ("hidden", "aud-quick--plain", "HOVER, PRESSED, DISABLED"):
        assert needle in header, needle
    body = re.sub(r"/\*.*?\*/", "", KIT_CSS, flags=re.S)
    assert re.search(r"\.aud-builder \[hidden\], " + re.escape(KIT_HIDDEN_RULE) + r"\s*\{\s*display:\s*none\s*!important", body)


def test_no_kit_hover_rule_applies_to_a_pressed_or_a_disabled_control():
    """A pressed pill under the mouse went white-on-white because `:hover` out-ranked `[aria-pressed]`; a disabled control still changed colour on hover."""
    hovers = [sel for sel in _selectors(KIT_CSS) if ":hover" in sel]
    assert len(hovers) > 10
    for sel in hovers:
        assert ":not(:disabled" in sel or 'aria-disabled="true"' in sel, sel                       # a disabled control (or option) does not react to the mouse
        if re.search(r"\.aud-(seg|quick)", sel) and not re.search(r'\[aria-pressed="true"\]:hover', sel):
            assert 'aria-pressed="true"' in sel.split(":not(", 1)[1], sel                          # nor does a pressed pill: its own rule styles it under the mouse


def test_the_dialog_stylesheet_documents_what_it_adds():
    header = SB_CSS[: SB_CSS.index("*/")]
    for needle in (".segb-limit", "maxPerTab", ".segb-discard", ".is-tight"):
        assert needle in header, needle
    code = re.sub(r"/\*.*?\*/", "", SB_CSS, flags=re.S)
    assert "overflow-wrap: break-word" in code and ".segb-table.is-tight .segb-td { overflow-wrap: anywhere; }" in code
    assert "forced-colors: active" in code and ".segb-row:focus-visible { outline: 2px solid Highlight" in code
