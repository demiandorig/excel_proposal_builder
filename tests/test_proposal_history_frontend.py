"""ProposalHistory (app/static/proposal-history.js): the module's surface, the pure helpers (flight, relative time, money, counts, error text,
the list request's query string, "show more" paging and merging, search highlighting, lineage, device label, the title line, the coercion of one
row of server JSON) and the static contracts of the file and of its stylesheet. Like test_segment_browser_frontend.py, the JavaScript runs in a
JavaScript engine (Node when installed, else macOS's JavaScriptCore) with a stub `window`: no browser, no DOM. The component itself, in a real
browser, is tests/test_proposal_history_dom.py.

The helpers are exercised in ONE engine launch (a module fixture); each test reads its own named slice of that result, so a failure names the
helper and the case."""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PH_JS = (ROOT / "app" / "static" / "proposal-history.js").read_text(encoding="utf-8")
PH_CSS = (ROOT / "app" / "static" / "proposal-history.css").read_text(encoding="utf-8")
STYLES_CSS = (ROOT / "app" / "static" / "styles.css").read_text(encoding="utf-8")


def _harness():
    path = ROOT / "tests" / "test_full_flight_frontend.py"
    spec = importlib.util.spec_from_file_location("_ff_frontend_harness_ph", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


H = _harness()


def run(snippet: str):
    """Loads proposal-history.js against a stub `window` and runs `snippet` (which returns a JSON string); P is ProposalHistory._pure, PH the module."""
    body = "var window = {};\n" + PH_JS + "\nvar PH = window.ProposalHistory;\nvar P = PH._pure;\n" + snippet
    try:
        out = H._run_js(body)
    except AssertionError as exc:
        if "JS engine failed" in str(exc) and "osascript" in str(exc).lower():
            pytest.skip(f"JavaScript engine unusable here: {exc}")
        raise
    return json.loads(out)


# --------------------------------------------------------------------------------------------- the public surface

def test_the_module_exposes_exactly_the_documented_api():
    assert run("return JSON.stringify(Object.keys(PH).sort());") == ["_pure", "close", "isOpen", "mount", "openDialog", "shell"]
    assert run("return JSON.stringify([typeof PH.openDialog, typeof PH.mount, typeof PH.shell, typeof PH.close, typeof PH.isOpen]);") == ["function"] * 5


def test_the_pure_helpers_are_exactly_the_ones_the_tests_and_the_docs_name():
    names = run("return JSON.stringify(Object.keys(P).sort());")
    assert names == sorted([
        "fold", "searchWords", "highlightRanges", "parseISODate", "formatFlight", "billingText", "parseTime", "formatWhen", "formatMoney", "fmtInt", "plural",
        "echoQuery", "countText", "describeError", "normQuery", "buildQuery", "nextPage", "mergeRows", "sortChoices", "sortPhrase", "lineageOf", "lineageLabel",
        "deviceLabel", "subline", "rowFacts"])


def test_loading_the_file_does_not_touch_the_page():
    """It is a plain script tag before app.js / admin.js: nothing may run at load time that needs a document."""
    assert run("return JSON.stringify(typeof document);") == "undefined"


def test_without_a_document_every_entry_point_is_inert_instead_of_throwing():
    out = run("""
      var h = PH.openDialog({ title: 'x' });
      var c = PH.mount(null, {});
      var c2 = PH.mount({}, {});
      var s = PH.shell({ title: 't' });
      return JSON.stringify({
        handle: [typeof h.close, h.close(), h.controller],
        isOpen: PH.isOpen(), close: typeof PH.close(),
        mount: [typeof c.reload, typeof c.setScope, typeof c.focusSearch, typeof c.destroy, c.reload(), c.setScope('x'), c.focusSearch(), c.destroy(), c2.getState().phase],
        shell: [typeof s.close, typeof s.setBusy, typeof s.focusInitial, s.dialog, s.body, s.close('r'), s.setBusy(true)],
      });""")
    assert out["handle"] == ["function", None, None]
    assert out["isOpen"] is False and out["close"] == "undefined"
    assert out["mount"][:4] == ["function"] * 4 and out["mount"][4:8] == [None] * 4 and out["mount"][8] == "loading"
    assert out["shell"] == ["function", "function", "function", None, None, None, None]


# --------------------------------------------------------------------------------------------- one engine launch for the helpers

SCENARIOS = r"""
var NOW = Date.parse('2026-11-11T15:30:00Z');
function ago(secs) { return new Date(NOW - secs * 1000).toISOString(); }
var UTC = { timeZone: 'UTC' };
function when(v, o) { var r = P.formatWhen(v, NOW, o || UTC); return [r.valid, r.main, r.minor, r.title]; }
var R = {};

R.fold = [P.fold('Peña'), P.fold('ESPAÑOL'), P.fold('Cafe\u0301'), P.fold(null), P.fold(undefined), P.fold(42), P.fold('İstanbul')];
R.searchWords = [P.searchWords('  Acme   HVAC '), P.searchWords(''), P.searchWords(null), P.searchWords('a b c d e f g h').length,
                 P.searchWords(new Array(101).join('x')).map(function (w) { return w.length; }), P.searchWords('EVC-48213 Peña')];

R.highlight = {
  accent: P.highlightRanges('Peña Nieto', ['pena']),
  decomposed: P.highlightRanges('Cafe\u0301 Bar', ['cafe']),
  overlap: P.highlightRanges('acme hvac', ['acme', 'me h']),
  twice: P.highlightRanges('abab', ['ab']),
  contained: P.highlightRanges('abcdef', ['abcdef', 'bc']),
  astralCase: P.highlightRanges('\uD801\uDC00bc xx', ['\uD801\uDC28bc']),
  adjacent: P.highlightRanges('acmehvac', ['acme', 'hvac']),
  empty: [P.highlightRanges('abc', []), P.highlightRanges('', ['a']), P.highlightRanges(null, ['a']), P.highlightRanges('abc', null), P.highlightRanges('abc', [''])],
  hostile: P.highlightRanges('<img onerror=x>', ['img', 'onerror']),
  regexChars: P.highlightRanges('a.c* corp a.c*', ['a.c*']),
  emoji: P.highlightRanges('Café \uD83D\uDE00 Café', ['\uD83D\uDE00']),
  caseless: P.highlightRanges('ACME hvac', ['acme']),
  inSentence: P.highlightRanges('Banco Popular de Puerto Rico', ['popular', 'rico']),
};

R.parseISODate = [P.parseISODate('2027-01-05'), P.parseISODate(' 2027-01-05 '), P.parseISODate('2027-02-29'), P.parseISODate('2028-02-29'), P.parseISODate('2100-02-29'),
                  P.parseISODate('2000-02-29'), P.parseISODate('2027-13-01'), P.parseISODate('2027-00-10'), P.parseISODate('2027-1-5'), P.parseISODate(20270105),
                  P.parseISODate(null), P.parseISODate('2027-04-31'), P.parseISODate('2027-04-30'), P.parseISODate('2027-01-05T00:00:00')];

R.flight = [
  P.formatFlight({ flight_start: '2027-01-05', flight_end: '2027-03-31' }),
  P.formatFlight({ flight_start: '2026-12-15', flight_end: '2027-01-04' }),
  P.formatFlight({ flight_start: '2027-01-05', flight_end: '2027-01-05' }),
  P.formatFlight({ flight_start: '2027-01-05' }),
  P.formatFlight({ flight_end: '2027-03-31' }),
  P.formatFlight({ flight_start: 'nope', flight_end: '2027-03-31' }),
  P.formatFlight({ flight_text: 'TBD   soon' }),
  P.formatFlight({ flight_text: new Array(81).join('x') }).length,
  P.formatFlight({ flight_start: '2027-02-31', flight_text: 'Q1' }),
  P.formatFlight({ flight_text: 5 }),
  P.formatFlight({}), P.formatFlight(null), P.formatFlight(undefined),
  P.formatFlight({ flight_start: '2027-01-05', flight_end: '2027-03-31', flight_text: 'ignored' }),
  P.formatFlight({ flight_start: '2026-02-01', flight_end: '2026-02-28' }),
];

R.billing = [P.billingText('week'), P.billingText('month'), P.billingText('quarter'), P.billingText('full_flight'),
             P.billingText('toString'), P.billingText('__proto__'), P.billingText('year'), P.billingText(null), P.billingText(undefined), P.billingText(3)];

R.parseTime = [P.parseTime('2026-11-11T15:23:00+00:00'), P.parseTime('2026-11-11T15:23:00Z'), P.parseTime('2026-11-11 15:23:00'), P.parseTime('2026-11-11T15:23:00.123456+00:00') % 1000,
               P.parseTime('2026-11-11T10:23:00-05:00'), P.parseTime('2026-11-11T20:53:00+0530'), P.parseTime('2026-11-11T15:23'), P.parseTime('2026-11-11T15:23:00.5Z') % 1000,
               P.parseTime('2026-02-30T00:00:00Z'), P.parseTime('2026-11-11T24:00:00Z'), P.parseTime('2026-11-11T15:60:00Z'), P.parseTime('garbage'), P.parseTime(''),
               P.parseTime(null), P.parseTime(1762874580000), P.parseTime('11/11/2026')];

R.when = {
  s59: when(ago(59)), s60: when(ago(60)), m59: when(ago(59 * 60 + 59)), h1: when(ago(3600)), h23: when(ago(23 * 3600 + 59 * 60)), d1: when(ago(86400)),
  h47: when(ago(47 * 3600)), d2: when(ago(48 * 3600)), d6: when(ago(6 * 86400 + 23 * 3600)), d7: when(ago(7 * 86400)), old: when(ago(400 * 86400)),
  skew30s: when(ago(-30)), skew2h: when(ago(-2 * 3600)), ahead25h: when(ago(-25 * 3600)),
  invalid: when('garbage'), nullv: when(null), num: when(1234),
  midnight: when('2026-11-11T00:05:00Z', UTC), noon: when('2026-11-11T12:00:00Z', UTC), pm: when('2026-11-11T23:59:00Z', UTC),
  offset: when('2026-11-11T10:23:00-05:00'), spaceSep: when('2026-11-11 15:23:00.123456+00:00'),
  iso: P.formatWhen('2026-11-11T15:23:00+00:00', NOW, UTC).iso,
  badNow: P.formatWhen(ago(120), undefined, UTC).valid,
  localZone: P.formatWhen('2026-11-11T15:23:00Z', NOW).valid,
};

R.money = [P.formatMoney(0), P.formatMoney(1250000), P.formatMoney(412500.49), P.formatMoney(412500.5), P.formatMoney(-5), P.formatMoney(-0.2), P.formatMoney(999999999999),
           P.formatMoney(NaN), P.formatMoney(Infinity), P.formatMoney('5'), P.formatMoney(null), P.formatMoney(undefined), P.formatMoney(1e15), P.formatMoney({}), P.formatMoney(12)];
R.fmtInt = [P.fmtInt(1234567), P.fmtInt(0), P.fmtInt(NaN), P.fmtInt(999.6), P.fmtInt(12), P.fmtInt(1000), P.fmtInt('1500'), P.fmtInt(undefined)];
R.plural = [P.plural(1, 'product'), P.plural(0, 'product'), P.plural(2, 'product'), P.plural(2, 'copy', 'copies')];
R.echo = [P.echoQuery('  a   b  '), P.echoQuery(new Array(61).join('x')), P.echoQuery(new Array(41).join('x')), P.echoQuery(new Array(21).join('\uD83D\uDE00')), P.echoQuery(null),
          P.echoQuery(new Array(51).join('\uD83D\uDE00')).length];

R.count = [
  P.countText({ shown: 25, total: 140 }), P.countText({ shown: 14, total: 14 }), P.countText({ shown: 1, total: 1 }), P.countText({ shown: 5, total: 5, status: 'draft' }),
  P.countText({ shown: 1, total: 1, status: 'draft' }), P.countText({ shown: 3, total: 3, query: 'acme' }), P.countText({ shown: 1, total: 1, query: 'acme' }),
  P.countText({ shown: 0, total: 0 }), P.countText({ shown: 0, total: 0, status: 'draft' }), P.countText({ shown: 0, total: 0, query: 'zzz' }),
  P.countText({ shown: 0, total: 0, query: 'zzz', status: 'generated' }), P.countText({ shown: 25, total: 140, query: 'a' }), P.countText({ shown: 10, total: 12044, status: 'generated' }),
  P.countText({ shown: 12044, total: 12044 }), P.countText({ shown: 'x', total: -3 }), P.countText(), P.countText({ shown: 2, total: 2, query: new Array(61).join('y') }),
  P.countText({ shown: 2, total: 2, status: 'generated' }), P.countText({ shown: 1, total: 1, status: 'generated' }),
];

R.error = [[0, true], [401, false], [403, false], [429, false], [500, false], [503, false], [599, false], [422, false], [404, false], [0, false], [undefined, false], [null, false], ['500', false]]
  .map(function (a) { return P.describeError(a[0], a[1]); });

R.query = {
  defaults: P.buildQuery({ pageSize: 25 }),
  nothing: P.buildQuery(),
  full: P.buildQuery({ page: 2, pageSize: 25, query: ' a&b #\u00E9 \uD83D\uDE00 ', status: 'draft', sort: 'client', dir: 'desc', extra: { mine: false } }),
  naturalDirOmitted: [P.buildQuery({ pageSize: 25, sort: 'client', dir: 'asc' }), P.buildQuery({ pageSize: 25, sort: 'value', dir: 'desc' }), P.buildQuery({ pageSize: 25, sort: 'updated', dir: 'desc' })],
  flippedDirKept: [P.buildQuery({ pageSize: 25, sort: 'client', dir: 'desc' }), P.buildQuery({ pageSize: 25, sort: 'value', dir: 'asc' }), P.buildQuery({ pageSize: 25, sort: 'updated', dir: 'asc' })],
  noDirGivesTheNaturalOne: P.buildQuery({ pageSize: 25, sort: 'client' }),
  pageOneOmitted: [P.buildQuery({ pageSize: 25, page: 1 }), P.buildQuery({ pageSize: 25, page: 0 }), P.buildQuery({ pageSize: 25, page: -3 }), P.buildQuery({ pageSize: 25, page: 'x' }), P.buildQuery({ pageSize: 25, page: '3' })],
  coerced: [P.buildQuery({ pageSize: 25, status: 'hax', sort: 'drop table', dir: 'sideways' }), P.buildQuery({ pageSize: 500 }), P.buildQuery({ pageSize: 0 }), P.buildQuery({ pageSize: 'x' }), P.buildQuery({ pageSize: 7.9 })],
  allStatusOmitted: P.buildQuery({ pageSize: 25, status: 'all' }),
  extra: [P.buildQuery({ pageSize: 25, extra: { mine: true, skip: null, gone: undefined, q: 'a b' } }), P.buildQuery({ pageSize: 25, extra: 'nope' }), P.buildQuery({ pageSize: 25, extra: ['x'] })],
  keysEscapedToo: P.buildQuery({ pageSize: 25, extra: { 'a&b': 'c=d' } }),
  longSearch: decodeURIComponent(P.buildQuery({ pageSize: 25, query: new Array(301).join('z') }).split('search=')[1]).length,
  whitespaceOnlySearch: P.buildQuery({ pageSize: 25, query: '  \t\n ' }),
  loneSurrogate: P.buildQuery({ pageSize: 25, query: 'a\uD83Db' }),
  emojiCounted: decodeURIComponent(P.buildQuery({ pageSize: 25, query: new Array(201).join('\uD83D\uDE00') }).split('search=')[1]).length,
};
R.normQuery = [P.normQuery('  evc    48213  '), P.normQuery(null), P.normQuery(new Array(251).join('q')).length, P.normQuery('a\tb\nc')];

R.nextPage = [P.nextPage(0, 25), P.nextPage(24, 25), P.nextPage(25, 25), P.nextPage(49, 25), P.nextPage(50, 25), P.nextPage(-5, 25), P.nextPage('x', 25), P.nextPage(10, 0),
              P.nextPage(10, 'x'), P.nextPage(1, 1), P.nextPage(2, 1), P.nextPage(99, 100), P.nextPage(100, 100), P.nextPage(undefined, undefined)];

function ids(list) { return list.map(function (r) { return r.proposal_id; }); }
var A = { proposal_id: 'a', v: 1 }, B = { proposal_id: 'b' }, C = { proposal_id: 'c' }, D = { proposal_id: 'd' }, E = { proposal_id: 'e' }, A2 = { proposal_id: 'a', v: 2 };
R.merge = {
  basic: ids(P.mergeRows([A, B, C], [C, D, A, E])),
  keepsTheHeldRow: P.mergeRows([A], [A2])[0].v,
  repeatInIncoming: ids(P.mergeRows([A], [B, B, B, C, B])),
  repeatInHeld: ids(P.mergeRows([A, A, B], [])),
  noId: ids(P.mergeRows([A, { client_name: 'x' }, { proposal_id: '' }, { proposal_id: 5 }, null, 'text', 7], [B, undefined, {}])),
  notArrays: [P.mergeRows(null, null), P.mergeRows('x', {}), P.mergeRows(undefined, [A]).length],
  deletionShift: ids(P.mergeRows([A, B, C, D], [B, C, D, E])),
  addedAtTheTop: ids(P.mergeRows([A, B, C], [{ proposal_id: 'new' }, A, B])),
  order: ids(P.mergeRows([C, A], [B, A, D])),
};

R.sortChoices = P.sortChoices();
R.sortPhrase = [P.sortPhrase('client', 'desc'), P.sortPhrase('client', 'asc'), P.sortPhrase('value', 'asc'), P.sortPhrase('value', 'desc'), P.sortPhrase('updated', 'desc'),
                P.sortPhrase('updated', 'asc'), P.sortPhrase('nonsense', 'asc'), P.sortPhrase('client', 'sideways'), P.sortPhrase(undefined, undefined)];

R.lineageOf = [
  P.lineageOf({ proposal_id: ' pid1 ', client_name: ' Acme Air ', notion_id: 'EVC-9', title: 'T' }), P.lineageOf('abc'), P.lineageOf('  abc '), P.lineageOf(['x']), P.lineageOf({}),
  P.lineageOf({ client_name: new Array(501).join('x') }).client_name.length, P.lineageOf({ client_name: 5, proposal_id: 7 }), P.lineageOf({ proposal_id: 5 }), P.lineageOf(''),
  P.lineageOf('   '), P.lineageOf(null), P.lineageOf(undefined), P.lineageOf(5), P.lineageOf(true), P.lineageOf({ notion_id: 'EVC-1' }), P.lineageOf({ client_name: '<b>x</b>' }),
  P.lineageOf({ proposal_id: 'p', extra: 'dropped', at: '2026-11-11' }),
];
R.lineageLabel = [
  P.lineageLabel({ proposal_id: 'p', client_name: 'Acme Air', notion_id: 'EVC-9' }), P.lineageLabel({ proposal_id: 'p', notion_id: 'EVC-9' }), P.lineageLabel('just-an-id'),
  P.lineageLabel({ client_name: 'Acme Air' }), P.lineageLabel(null), P.lineageLabel({}), P.lineageLabel({ title: 'Only a title' }), P.lineageLabel({ client_name: '<img onerror=1>' }),
];

var CHROME_MAC = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36';
R.device = [
  P.deviceLabel(CHROME_MAC, '10.0.0.3'),
  P.deviceLabel('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36 Edg/141.0.2535.51', '1.2.3.4'),
  P.deviceLabel('Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1', ''),
  P.deviceLabel('Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:128.0) Gecko/20100101 Firefox/128.0', null),
  P.deviceLabel('Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Mobile Safari/537.36', ' 9.9.9.9 '),
  P.deviceLabel('Mozilla/5.0 (X11; CrOS x86_64 14541.0.0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 OPR/106.0.0.0', ''),
  P.deviceLabel('curl/8.4.0', '127.0.0.1'), P.deviceLabel(new Array(100).join('q'), '').length, P.deviceLabel('', '10.0.0.9'), P.deviceLabel('', ''), P.deviceLabel(null, undefined),
  P.deviceLabel(CHROME_MAC, new Array(100).join('9')).split(' · ')[2].length, P.deviceLabel('Mozilla/5.0 (iPad; CPU OS 17_5 like Mac OS X) AppleWebKit/605.1.15 Version/17.5 Safari/604.1', ''),
  P.deviceLabel('Mozilla/5.0 (X11; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/115.0', ''),
];

R.subline = [
  P.subline({ proposal_title: 'EVC-48213_Fronteras_Proposal_20261105', notion_id: 'EVC-48213' }), P.subline({ proposal_title: 'Toyota 2027 Plan', notion_id: 'EVC-47990' }),
  P.subline({ proposal_title: '', notion_id: 'EVC-47510' }), P.subline({ proposal_title: 'Just a title' }), P.subline({ filename: 'file.pptx', notion_id: 'EVC-1' }),
  P.subline({ proposal_title: 'evc-48213 lower', notion_id: 'EVC-48213' }), P.subline({}), P.subline(null), P.subline({ proposal_title: 5, notion_id: 7 }),
  P.subline({ proposal_title: '  padded  ', notion_id: ' ' }), P.subline({ proposal_title: 'T', filename: 'F' }),
];

R.rowFacts = {
  good: P.rowFacts({ proposal_id: 'p1', client_name: '  Acme   HVAC ', status: 'draft', proposal_title: 'T', notion_id: 'EVC-1', total_net: 12.5, line_count: 3, wizard_step: 4, time_unit: 'month',
                     tabs_built: ['A', ' B ', '', 5], duplicated_from: { client_name: 'X' }, can_reopen: true, updated_at: '2026-11-11T15:23:00+00:00', created_by_email: 'a@b.c',
                     requested_by: 'AE', requester_user_agent: 'UA', requester_ip: '1.1.1.1' }),
  empty: P.rowFacts(null),
  hostile: P.rowFacts({ proposal_id: 5, client_name: 42, status: ['draft'], total_net: '12', line_count: 'x', wizard_step: 99, time_unit: { a: 1 }, tabs_built: 'A', duplicated_from: ['l'], can_reopen: 'no',
                        updated_at: 5, created_by_email: null, requested_by: {}, seller_email: 'fallback@x.y', requester_user_agent: 7, requester_ip: 8 }),
  steps: [P.rowFacts({ wizard_step: 0 }).step, P.rowFacts({ wizard_step: 1 }).step, P.rowFacts({ wizard_step: 8 }).step, P.rowFacts({ wizard_step: 9 }).step, P.rowFacts({ wizard_step: '3' }).step, P.rowFacts({ wizard_step: 3.7 }).step],
  lines: [P.rowFacts({ line_count: 0 }).lines, P.rowFacts({ line_count: -1 }).lines, P.rowFacts({ line_count: '5' }).lines, P.rowFacts({ line_count: 5.9 }).lines],
  reopen: [P.rowFacts({ can_reopen: false }).canReopen, P.rowFacts({ can_reopen: null }).canReopen, P.rowFacts({}).canReopen, P.rowFacts({ can_reopen: 0 }).canReopen],
  netNotFinite: [String(P.rowFacts({ total_net: NaN }).net), String(P.rowFacts({ total_net: Infinity }).net), String(P.rowFacts({ total_net: 0 }).net)],
  titleFallsBackToTheFilename: P.rowFacts({ proposal_title: '', filename: 'f.pptx' }).title,
  statusOther: [P.rowFacts({ status: 'generated' }).draft, P.rowFacts({ status: 'DRAFT' }).draft, P.rowFacts({ status: undefined }).draft],
};

return JSON.stringify(R);
"""


@pytest.fixture(scope="module")
def R():
    try:
        probe = run("return JSON.stringify({ok: typeof P.fold});")
    except AssertionError as exc:
        pytest.skip(f"JavaScript engine unusable here: {exc}")
    assert probe == {"ok": "function"}
    return run(SCENARIOS)


# --------------------------------------------------------------------------------------------- text and search

def test_fold_lowercases_and_drops_accents_and_never_throws_on_odd_input(R):
    assert R["fold"] == ["pena", "espanol", "cafe", "", "", "42", "istanbul"]


def test_search_words_are_what_the_server_will_use(R):
    words, empty, null, count, lengths, mixed = R["searchWords"]
    assert words == ["acme", "hvac"] and empty == [] and null == [] and count == 6 and lengths == [80] and mixed == ["evc-48213", "pena"]


def test_highlight_ranges_cover_the_original_characters_for_accents_overlaps_and_odd_text(R):
    h = R["highlight"]
    assert h["accent"] == [[0, 4]]                                                                  # "Peña" is matched by "pena": 4 characters of the original
    assert h["decomposed"] == [[0, 5]]                                                              # "Cafe" + a combining accent is one 5-unit span, accent included
    assert h["overlap"] == [[0, 6]]                                                                 # "acme" and "me h" overlap: one mark over "acme h"
    assert h["contained"] == [[0, 6]]                                                               # a word inside another word's span does not shorten it
    assert h["adjacent"] == [[0, 8]] and h["twice"] == [[0, 4]]                                     # touching spans are merged into one mark, not two side by side
    assert h["empty"] == [[], [], [], [], []]
    assert h["hostile"] == [[1, 4], [5, 12]]                                                        # only offsets ever leave the helper: the text is drawn from text nodes
    assert h["regexChars"] == [[0, 4], [10, 14]]                                                    # "a.c*" is literal: "abc" would not match, and `*` breaks nothing
    assert h["emoji"] == [[5, 7]]                                                                   # a surrogate pair is one 2-unit span
    assert h["astralCase"] == [[0, 4]]                                                              # a capital letter outside the BMP (Deseret) folds as one character, not as two halves
    assert h["caseless"] == [[0, 4]] and h["inSentence"] == [[6, 13], [24, 28]]


# --------------------------------------------------------------------------------------------- dates, times, money

def test_parse_iso_date_accepts_only_real_calendar_dates(R):
    ok = {"y": 2027, "m": 1, "d": 5}
    assert R["parseISODate"] == [ok, ok, None, {"y": 2028, "m": 2, "d": 29}, None, {"y": 2000, "m": 2, "d": 29}, None, None, None, None, None, None, {"y": 2027, "m": 4, "d": 30}, None]


def test_the_flight_line_follows_the_design_for_every_shape_of_dates(R):
    f = R["flight"]
    assert f[0] == "Jan 5 – Mar 31, 2027"                                                           # the year once when both dates share it
    assert f[1] == "Dec 15, 2026 – Jan 4, 2027"                                                     # both years across a year end
    assert f[2] == "Jan 5, 2027"                                                                    # one day
    assert f[3] == "Starts Jan 5, 2027" and f[4] == "Ends Mar 31, 2027"
    assert f[5] == "Ends Mar 31, 2027"                                                              # an unreadable start is no start
    assert f[6] == "TBD soon"                                                                       # the raw text, whitespace collapsed
    assert f[7] == 60                                                                               # ... and at most 60 characters
    assert f[8] == "Q1"                                                                             # the text stands in when the dates do not parse (Feb 31)
    assert f[9] == "" and f[10] == "" and f[11] == "" and f[12] == ""
    assert f[13] == "Jan 5 – Mar 31, 2027"                                                          # parsed dates win over the text
    assert f[14] == "Feb 1 – Feb 28, 2026"


def test_the_billing_line_knows_four_units_and_nothing_inherited(R):
    assert R["billing"] == ["Billed weekly", "Billed monthly", "Billed quarterly", "One full-flight bill", "", "", "", "", "", ""]


def test_timestamps_parse_by_hand_the_same_way_in_every_engine(R):
    base = 1762874580000                                                                            # 2025-11-11T15:23:00Z? (see below)
    ms = [R["parseTime"][i] for i in (0, 1, 2, 4, 5, 6)]
    assert len(set(ms)) == 1                                                                        # +00:00, Z, no zone (the server's UTC), -05:00 and +0530 of the same instant, no seconds
    assert R["parseTime"][3] == 123 and R["parseTime"][7] == 500                                    # a 6-digit fraction and a 1-digit one
    assert R["parseTime"][8:] == [None] * 8 or all(v is None for v in R["parseTime"][8:])           # Feb 30, 24:00, 15:60, junk, "", null, a number, a US date: not a time (NaN -> null)
    assert base > 0


def test_relative_time_changes_word_at_each_boundary(R):
    w = R["when"]
    assert w["s59"][1:3] == ["Just now", "Nov 11, 3:29 PM"] and w["s60"][1] == "1 min ago" and w["m59"][1] == "59 min ago"
    assert w["h1"][1] == "1 hr ago" and w["h23"][1] == "23 hr ago"
    assert w["d1"][1] == "Yesterday" and w["h47"][1] == "Yesterday" and w["d2"][1] == "2 days ago" and w["d6"][1] == "6 days ago"
    assert w["d7"][1:3] == ["Nov 4, 2026", ""]                                                      # from 7 days on: the date, the year always, no second line
    assert w["old"][1:3] == ["Oct 7, 2025", ""]


def test_a_timestamp_in_the_future_reads_just_now_unless_it_is_a_day_ahead(R):
    w = R["when"]
    assert w["skew30s"][1] == "Just now" and w["skew2h"][1] == "Just now"                           # clock skew
    assert w["ahead25h"][1:3] == ["Nov 12, 2026", ""]                                               # a day or more ahead is shown as a date, not "just now"


def test_an_unreadable_timestamp_is_a_dash_and_not_valid(R):
    for key in ("invalid", "nullv", "num"):
        assert R["when"][key] == [False, "—", "", ""], key


def test_the_exact_time_line_and_the_tooltip_read_like_a_clock(R):
    w = R["when"]
    assert w["s60"][3] == "Wednesday, November 11, 2026 at 3:29 PM"
    assert w["midnight"][2] == "Nov 11, 12:05 AM" and w["noon"][2] == "Nov 11, 12:00 PM" and w["pm"][2] == "Nov 11, 11:59 PM"
    assert w["offset"][2] == "Nov 11, 3:23 PM" and w["spaceSep"][2] == "Nov 11, 3:23 PM"             # a UTC offset and a space instead of the T
    assert w["iso"] == "2026-11-11T15:23:00+00:00"                                                  # the original string goes to <time datetime>
    assert w["badNow"] is True and w["localZone"] is True                                           # no `now` falls back to the clock; no timeZone option uses the viewer's zone


def test_money_is_whole_dollars_and_anything_that_is_not_a_number_is_none(R):
    assert R["money"] == ["$0", "$1,250,000", "$412,500", "$412,501", "-$5", "$0", "$999,999,999,999", None, None, None, None, None, None, None, "$12"]


def test_fmt_int_and_plural(R):
    assert R["fmtInt"] == ["1,234,567", "0", "0", "1,000", "12", "1,000", "1,500", "0"]
    assert R["plural"] == ["product", "products", "products", "copies"]


# --------------------------------------------------------------------------------------------- the count line and the errors

def test_echo_query_collapses_whitespace_and_shortens_long_text_by_characters(R):
    e = R["echo"]
    assert e[0] == "a b" and e[1] == "x" * 40 + "…" and e[2] == "x" * 40 and e[3] == "😀" * 20 and e[4] == ""
    assert e[5] == 81                                                                               # 40 emoji = 80 UTF-16 units, plus the ellipsis (cut by characters: never a split surrogate)


def test_the_count_line_says_how_many_and_of_what(R):
    c = R["count"]
    assert c[0] == "Showing 25 of 140 proposals" and c[1] == "14 proposals" and c[2] == "1 proposal"
    assert c[3] == "5 drafts" and c[4] == "1 draft"
    assert c[5] == "3 proposals match “acme”" and c[6] == "1 proposal matches “acme”"
    assert c[7] == "No proposals" and c[8] == "No drafts"
    assert c[9] == "No proposals match “zzz”" and c[10] == "No generated proposals match “zzz”"
    assert c[11] == "Showing 25 of 140 proposals that match “a”"
    assert c[12] == "Showing 10 of 12,044 generated proposals" and c[13] == "12,044 proposals"
    assert c[14] == "No proposals" and c[15] == "No proposals"                                        # nonsense counts are zero, not NaN
    assert c[16] == "2 proposals match “" + "y" * 40 + "…”"
    assert c[17] == "2 generated proposals" and c[18] == "1 generated proposal"


def test_error_text_is_by_cause_and_never_the_servers_own_words(R):
    e = R["error"]
    assert e[0] == "Check your connection and try again."
    assert e[1] == "Your session ended. Signing you in again…"
    assert e[2] == "You don’t have access to this list."
    assert e[3] == "Too many requests. Wait a moment, then try again."
    assert e[4] == e[5] == e[6] == "The server had a problem. Try again in a moment. Nothing has been lost."
    assert e[7] == e[8] == e[9] == e[10] == e[11] == e[12] == "Something went wrong. Try again in a moment."
    assert not any(re.search(r"\d{3}", text) for text in e)                                         # no status code in what a person reads


# --------------------------------------------------------------------------------------------- the request and the paging

def test_the_query_string_leaves_defaults_out_and_encodes_everything_else(R):
    q = R["query"]
    assert q["defaults"] == "page_size=25" and q["nothing"] == "page_size=25"
    assert q["full"] == "page=2&page_size=25&search=a%26b%20%23%C3%A9%20%F0%9F%98%80&status=draft&sort=client&dir=desc&mine=false"
    assert q["naturalDirOmitted"] == ["page_size=25&sort=client", "page_size=25&sort=value", "page_size=25"]
    assert q["flippedDirKept"] == ["page_size=25&sort=client&dir=desc", "page_size=25&sort=value&dir=asc", "page_size=25&dir=asc"]
    assert q["noDirGivesTheNaturalOne"] == "page_size=25&sort=client"
    assert q["pageOneOmitted"] == ["page_size=25"] * 4 + ["page=3&page_size=25"]
    assert q["allStatusOmitted"] == "page_size=25"


def test_nothing_the_caller_passes_can_put_an_unknown_word_in_the_request(R):
    q = R["query"]
    assert q["coerced"] == ["page_size=25", "page_size=100", "page_size=25", "page_size=25", "page_size=7"]    # unknown status / sort / dir are dropped; the size is clamped to 1..100
    assert q["extra"] == ["page_size=25&mine=true&q=a%20b", "page_size=25", "page_size=25&0=x"]
    assert q["keysEscapedToo"] == "page_size=25&a%26b=c%3Dd"                                        # a key is escaped like a value


def test_the_typed_search_is_trimmed_collapsed_capped_and_survives_a_lone_surrogate(R):
    q = R["query"]
    assert q["longSearch"] == 200 and q["emojiCounted"] == 400                                      # 200 characters, counted by code point (200 emoji = 400 units)
    assert q["whitespaceOnlySearch"] == "page_size=25"
    assert q["loneSurrogate"] == "page_size=25&search=a%EF%BF%BDb"                                  # encodeURIComponent would throw: the half emoji becomes U+FFFD
    assert R["normQuery"] == ["evc 48213", "", 200, "a b c"]


def test_show_more_asks_for_the_page_after_the_rows_already_held(R):
    assert R["nextPage"] == [1, 1, 2, 2, 3, 1, 1, 1, 1, 2, 3, 1, 2, 1]


def test_merging_pages_keeps_order_and_never_repeats_or_loses_a_row(R):
    m = R["merge"]
    assert m["basic"] == ["a", "b", "c", "d", "e"]
    assert m["keepsTheHeldRow"] == 1                                                                # the row on screen keeps its place (and its node)
    assert m["repeatInIncoming"] == ["a", "b", "c"] and m["repeatInHeld"] == ["a", "b"]
    assert m["noId"] == ["a", "b"]                                                                  # a row without a usable id is dropped
    assert m["notArrays"] == [[], [], 1]
    assert m["deletionShift"] == ["a", "b", "c", "d", "e"]                                          # a row deleted elsewhere shifted the pages: the new one is added once
    assert m["addedAtTheTop"] == ["a", "b", "c", "new"]                                             # a row autosaved elsewhere shifted them the other way
    assert m["order"] == ["c", "a", "b", "d"]


def test_the_sort_choices_are_the_six_the_narrow_select_offers_in_order(R):
    assert [(c["value"], c["label"]) for c in R["sortChoices"]] == [
        ("updated:desc", "Last edited · newest first"), ("updated:asc", "Last edited · oldest first"), ("client:asc", "Client · A to Z"),
        ("client:desc", "Client · Z to A"), ("value:desc", "Net value · high to low"), ("value:asc", "Net value · low to high")]
    assert all(c["value"] == c["sort"] + ":" + c["dir"] for c in R["sortChoices"])


def test_the_sort_phrase_is_what_a_screen_reader_hears(R):
    assert R["sortPhrase"] == ["client, Z to A", "client, A to Z", "net value, low to high", "net value, high to low", "last edited, newest first", "last edited, oldest first",             # an unknown sort reads as the default one, an unknown direction as descending
                               "last edited, oldest first", "client, Z to A", "last edited, newest first"]


# --------------------------------------------------------------------------------------------- lineage, device, subline, row

def test_lineage_accepts_an_object_a_bare_id_or_nothing_and_cleans_each_value(R):
    L = R["lineageOf"]
    assert L[0] == {"proposal_id": "pid1", "client_name": "Acme Air", "notion_id": "EVC-9", "title": "T"}
    assert L[1] == L[2] == {"proposal_id": "abc", "client_name": "", "notion_id": "", "title": ""}
    assert L[3] is None and L[4] is None                                                            # a list and {} are no lineage
    assert L[5] == 200                                                                              # each value is cut at 200 characters
    assert L[6] is None and L[7] is None                                                            # non-strings count as nothing
    assert L[8] is None and L[9] is None and L[10] is None and L[11] is None and L[12] is None and L[13] is None
    assert L[14]["notion_id"] == "EVC-1" and L[15]["client_name"] == "<b>x</b>"                     # markup stays plain text here: it is only ever drawn as a text node
    assert set(L[16]) == {"proposal_id", "client_name", "notion_id", "title"}                       # unknown keys (the server's `at`) are dropped


def test_the_copied_from_label_names_the_client_then_the_notion_id_then_says_another_proposal(R):
    L = R["lineageLabel"]
    assert L[0] == {"text": "Copied from Acme Air", "title": "Copied from Acme Air (EVC-9)"}
    assert L[1] == {"text": "Copied from EVC-9", "title": "Copied from another proposal (EVC-9)"}
    assert L[2] == {"text": "Copied from another proposal", "title": "Copied from another proposal"}
    assert L[3] == {"text": "Copied from Acme Air", "title": "Copied from Acme Air"}
    assert L[4] is None and L[5] is None
    assert L[6] == {"text": "Copied from another proposal", "title": "Copied from another proposal"}      # a title alone names nobody
    assert L[7]["text"] == "Copied from <img onerror=1>"


def test_the_device_label_is_os_browser_version_and_address(R):
    d = R["device"]
    assert d[0] == "Mac · Chrome 141 · 10.0.0.3"
    assert d[1] == "Windows · Edge 141 · 1.2.3.4"                                                   # Edge also says Chrome in its user agent: Edge wins
    assert d[2] == "iPhone · Safari 17"
    assert d[3] == "Mac · Firefox 128"
    assert d[4] == "Android · Chrome 140 · 9.9.9.9"                                                 # the address is trimmed
    assert d[5] == "ChromeOS · Opera 106"
    assert d[6] == "curl/8.4.0 · 127.0.0.1"                                                         # an unknown agent is shown as it is
    assert d[7] == 40 and d[8] == "10.0.0.9" and d[9] == "" and d[10] == ""
    assert d[11] == 64                                                                              # a long "address" is cut at 64
    assert d[12] == "iPad · Safari 17" and d[13] == "Linux · Firefox 115"


def test_the_title_line_adds_the_notion_id_only_when_the_title_lacks_it(R):
    s = R["subline"]
    assert s[0] == {"text": "EVC-48213_Fronteras_Proposal_20261105", "title": "EVC-48213_Fronteras_Proposal_20261105"}
    assert s[1]["text"] == "EVC-47990 Toyota 2027 Plan"
    assert s[2]["text"] == "EVC-47510"
    assert s[3]["text"] == "Just a title"
    assert s[4]["text"] == "EVC-1 file.pptx"                                                        # the file name stands in for a missing title
    assert s[5]["text"] == "evc-48213 lower"                                                        # the comparison ignores case
    assert s[6]["text"] == "" and s[7]["text"] == "" and s[8]["text"] == ""
    assert s[9]["text"] == "padded" and s[10]["text"] == "T"


def test_a_row_of_server_json_is_coerced_so_every_field_has_the_type_the_drawing_expects(R):
    good = R["rowFacts"]["good"]
    assert good["id"] == "p1" and good["name"] == "Acme HVAC" and good["draft"] is True and good["net"] == 12.5 and good["lines"] == 3 and good["step"] == 4 and good["unit"] == "month"
    assert good["tabs"] == ["A", "B"] and good["lineage"]["client_name"] == "X" and good["canReopen"] is True and good["createdBy"] == "a@b.c" and good["requestedBy"] == "AE"
    empty = R["rowFacts"]["empty"]
    assert empty["id"] == "" and empty["name"] == "" and empty["draft"] is False and empty["net"] is None and empty["lines"] is None and empty["step"] is None and empty["tabs"] == []
    bad = R["rowFacts"]["hostile"]
    assert bad["id"] == "" and bad["name"] == "" and bad["draft"] is False and bad["net"] is None and bad["lines"] is None and bad["step"] is None and bad["unit"] == ""
    assert bad["tabs"] == [] and bad["lineage"] is None and bad["canReopen"] is True and bad["updatedAt"] == "" and bad["createdBy"] == ""
    assert bad["requestedBy"] == "fallback@x.y" and bad["userAgent"] == "" and bad["ip"] == ""      # requested_by missing: the seller's address stands in
    assert R["rowFacts"]["steps"] == [None, 1, 8, None, 3, 3]
    assert R["rowFacts"]["lines"] == [0, None, 5, 5]
    assert R["rowFacts"]["reopen"] == [False, True, True, True]                                     # only an explicit false disables
    assert R["rowFacts"]["netNotFinite"] == ["null", "null", "0"]                                   # (stringified: JSON would turn a NaN into null by itself)
    assert R["rowFacts"]["titleFallsBackToTheFilename"] == "f.pptx"
    assert R["rowFacts"]["statusOther"] == [False, False, False]                                    # only the exact word "draft" is a draft


# --------------------------------------------------------------------------------------------- static contracts of the files

def _code(js: str) -> str:
    """The script without its comments (block and line comments; the quotes in the source never hold `//` followed by a space in this file)."""
    out = re.sub(r"/\*.*?\*/", "", js, flags=re.S)
    return "\n".join(re.sub(r"(?<![:\"'\\])//\s.*$", "", line) for line in out.splitlines())


def test_the_component_never_parses_markup_and_never_evaluates_text():
    code = _code(PH_JS)
    for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "DOMParser", "createContextualFragment", "eval(", "new Function", "setTimeout(\"", "setInterval(\""):
        assert banned not in code, banned
    assert "textContent" in code and "createElement" in code                                        # everything is built from nodes and text


def test_the_component_uses_no_page_global_and_no_storage():
    code = _code(PH_JS)
    for banned in ("localStorage", "sessionStorage", "document.cookie", "alert(", "confirm(", "prompt(", "escapeHtml", "_debounce", "adminState", "state.parsed"):
        assert banned not in code, banned
    assert "window.fetch" in code                                                                   # the guarded fetch the session guard installs, never a captured copy


def test_the_header_documents_the_frozen_api():
    header = PH_JS[: PH_JS.index("(function () {")]
    for needle in ("ProposalHistory.openDialog", "ProposalHistory.mount", "ProposalHistory.shell", "onDuplicate", "reload({ highlightId", "keepRows", "setScope", "focusSearch",
                   "getState", "destroy", "linkTarget", "canDelete", "canDuplicate", "emptyAction", "fetchJson", "searchDelay", "initialFocus", "returnFocus", "setBusy"):
        assert needle in header, needle


def _selectors(css: str) -> list[str]:
    """Every selector of every style rule (inside @media / @container too), keyframe steps excluded, comments dropped."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    out: list[str] = []
    stack: list[str] = []
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


def test_the_stylesheet_is_scoped_under_ph_and_needs_only_styles_css():
    sels = _selectors(PH_CSS)
    assert len(sels) > 150
    stray = [s for s in sels if not re.search(r"\.ph(-|\b)", s)]
    assert stray == [], stray
    assert all(re.fullmatch(r"ph-[a-z-]+", name) for name in re.findall(r"@keyframes\s+([\w-]+)", PH_CSS))
    assert all(name == "ph" for name in re.findall(r"@container\s+([\w-]+)", PH_CSS))                  # one named container, the component root


def test_every_custom_property_the_stylesheet_reads_exists():
    defined = set(re.findall(r"(--[\w-]+)\s*:", STYLES_CSS)) | set(re.findall(r"(--[\w-]+)\s*:", PH_CSS))
    used = set(re.findall(r"var\((--[\w-]+)", PH_CSS))
    assert used and used <= defined, sorted(used - defined)
    assert "--ink-faint" not in used                                                                # the header promises it is never used for text (3.3:1 on white)


def test_the_stylesheet_has_the_container_tiers_and_the_motion_and_contrast_blocks():
    code = re.sub(r"/\*.*?\*/", "", PH_CSS, flags=re.S)
    assert "container: ph / inline-size" in code
    for tier in ("@container ph (min-width: 1080px)", "@container ph (min-width: 950px)", "@container ph (min-width: 1180px)", "@container ph (max-width: 519px)", "@container ph (max-width: 399px)"):
        assert tier in code, tier
    assert "@media (prefers-reduced-motion: reduce)" in code and "@media (forced-colors: active)" in code and "@media (max-width: 640px)" in code
    for form_class in (".ph-form", ".ph-form-grid", ".ph-field", ".ph-flabel", ".ph-req", ".ph-input", ".ph-hint", ".ph-ferror", ".ph-form-foot", ".ph-overlay--top", ".ph-dialog--form"):
        assert form_class in code, form_class                                                       # the primitives the duplicate dialog is built from


def test_the_menu_carries_its_own_tokens_because_it_lives_outside_the_component():
    code = re.sub(r"/\*.*?\*/", "", PH_CSS, flags=re.S)
    assert re.search(r"\.ph,\s*\.ph-dialog,\s*\.ph-menu\s*\{[^}]*--ph-danger", code)
    assert re.search(r"\.ph-menu\s*\{[^}]*position:\s*fixed[^}]*z-index:\s*800", code)
