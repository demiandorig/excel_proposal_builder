"""Admin -> Forecast: correcting which products get the programmatic win rate (constanza_win_rate.py).

The built-in rule (every platform series except Roku's and Prime Video's) is the planner's stated buying stack, not something the data
files say — Netflix's export is labelled "Direct", the Live Sports sheets are Entravision's own weekly estimates — so each product can be
set to Auto (the rule), Apply or Don't apply. Offline: the forecast_product_map table is the in-memory Store of the forecast-map tests.
"""
from __future__ import annotations

import asyncio
import dataclasses
import logging
import sys

import psycopg
import pytest
from fastapi import HTTPException

import app.main as main
from app import catalog
from app.services import constanza_forecast_map as fm
from app.services import constanza_win_rate as wr
from app.services import forecast_store as store_mod
from tests.test_constanza_forecast_map import Store, _body, _request, store  # noqa: F401  (the in-memory table and request helpers)
from tests.test_constanza_frontend import H as JS_HARNESS
from tests.test_constanza_win_rate import HULU, NETFLIX, PRIME, REACH, ROKU, VIX360, linked, manifest_links  # noqa: F401  (the manifest-linked catalog)

LIVE_SPORTS = "Entravision Plus - Live Sports - NBA"


@pytest.fixture(autouse=True)
def _every_series_key_is_valid(monkeypatch):
    """The admin accepts any series key the data has; offline there is no database, so list the manifest's too."""
    keys = sorted({key for key, _ in manifest_links().values()} | set(fm.platform_keys()))
    monkeypatch.setattr(fm, "platform_keys", lambda: keys)


def _put(name, **kw):
    return asyncio.run(main.admin_set_forecast_map(_body(name, **kw), _request()))


def _row(payload, name):
    return next(r for r in payload["products"] if r["name"] == name)


# ---------------------------------------------------------------------------------------------------------- the choice itself

@pytest.mark.parametrize("raw,expected", [(None, None), ("", None), ("auto", None), (" Auto ", None), ("default", None),
                                          (True, True), ("apply", True), ("ON", True), ("true", True),
                                          (False, False), ("skip", False), ("off", False), ("No", False)])
def test_the_win_rate_choice_reads_auto_apply_or_skip(raw, expected):
    assert fm.parse_win_rate_choice(raw) is expected


@pytest.mark.parametrize("raw", ["maybe", "15", 3, "applied-ish"])
def test_anything_else_is_refused_rather_than_read_as_auto(raw):
    with pytest.raises(ValueError, match="Auto, Apply or Don't apply"):
        fm.parse_win_rate_choice(raw)


def test_clean_config_keeps_the_choice_for_platform_data_only():
    base = {"enabled": True, "method": "platform", "platform": "hulu", "hispanic_weighted": False}
    assert fm.clean_config({**base, "win_rate": "skip"})["win_rate"] is False
    assert fm.clean_config({**base, "win_rate": "apply"})["win_rate"] is True
    assert fm.clean_config(base)["win_rate"] is None
    for method in ("generic", "tiktok"):                    # no avails to win: the choice is dropped, not stored
        assert fm.clean_config({"enabled": True, "method": method, "win_rate": "apply"})["win_rate"] is None
    with pytest.raises(ValueError):
        fm.clean_config({**base, "win_rate": "sometimes"})


# ---------------------------------------------------------------------------------------------------------- it changes the estimate

def test_an_admin_choice_beats_the_built_in_rule_in_both_directions(store, linked):
    assert fm.resolve_forecast(NETFLIX).win_rate is True and fm.resolve_forecast(ROKU).win_rate is False
    _put(NETFLIX, platform="netflix-15s", win_rate="skip")
    _put(ROKU, platform="roku", win_rate="apply")
    netflix, roku = fm.resolve_forecast(NETFLIX), fm.resolve_forecast(ROKU)
    assert (netflix.win_rate, netflix.win_rate_override) == (False, False)
    assert (roku.win_rate, roku.win_rate_override) == (True, True)
    _put(NETFLIX, platform="netflix-15s", win_rate="auto")                    # back to the rule
    assert (fm.resolve_forecast(NETFLIX).win_rate, fm.resolve_forecast(NETFLIX).win_rate_override) == (True, None)


def test_a_saved_row_without_a_choice_keeps_following_the_rule(store, linked):
    _put(HULU, platform="hulu")                                               # e.g. only the Hispanic weighting was touched
    assert fm.resolve_forecast(HULU).win_rate is True and fm.resolve_forecast(HULU).win_rate_override is None
    _put(HULU, platform="roku")                                               # remapped to Roku's series: the rule follows the series
    assert fm.resolve_forecast(HULU).win_rate is False


def test_switching_a_product_off_platform_data_forgets_the_choice(store, linked):
    _put(HULU, platform="hulu", win_rate="skip")
    _put(HULU, method="generic", platform=None, win_rate="skip")
    cfg = fm.resolve_forecast(HULU)
    assert cfg.method == "generic" and cfg.win_rate is False and cfg.win_rate_override is None
    assert store.rows[HULU]["win_rate"] is None


def test_the_engine_follows_the_admins_choice(store, linked, monkeypatch):
    from app.services import constanza_engine as engine
    from app.services.constanza_geo import resolve_geo
    res = resolve_geo("Los Angeles")
    raw = engine.estimate_avails(HULU, res, months=1)                         # offline: the bundled workbook answers
    assert raw.win_rate is not None and raw.win_rate["mid"] == 0.15
    _put(HULU, platform="hulu", win_rate="skip")
    skipped = engine.estimate_avails(HULU, res, months=1)
    assert skipped.win_rate is None and skipped.imps_mid == pytest.approx(raw.win_rate["raw_imps_mid"]) and skipped.imps_mid > raw.imps_mid * 6
    _put(ROKU, platform="roku", win_rate="apply")
    forced = engine.estimate_avails(ROKU, res, months=1)
    assert forced.win_rate is not None and forced.imps_mid == pytest.approx(forced.win_rate["raw_imps_mid"] * 0.15)


# ---------------------------------------------------------------------------------------------------------- the admin payload

def test_the_admin_list_shows_each_products_win_rate_the_default_and_the_choice(store, linked):
    _put(NETFLIX, platform="netflix-15s", win_rate="skip")
    payload = asyncio.run(main.admin_get_forecast_map())
    netflix, roku, hulu = _row(payload, NETFLIX), _row(payload, ROKU), _row(payload, HULU)
    assert (netflix["win_rate"], netflix["win_rate_override"], netflix["win_rate_default"], netflix["customized"]) == (False, False, True, True)
    assert (roku["win_rate"], roku["win_rate_override"], roku["win_rate_default"]) == (False, None, False)
    assert (hulu["win_rate"], hulu["win_rate_override"], hulu["win_rate_default"]) == (True, None, True)
    assert payload["win_rate"] == {"low": 0.1, "mid": 0.15, "high": 0.2}       # the rates in force, said on the tab


def test_the_rates_in_force_follow_the_environment(store, monkeypatch):
    monkeypatch.setenv(wr.ENV_MID, "12")
    assert asyncio.run(main.admin_get_forecast_map())["win_rate"] == pytest.approx({"low": 0.10, "mid": 0.12, "high": 0.20})


def test_the_endpoint_refuses_a_nonsense_choice_with_a_400(store, linked):
    with pytest.raises(HTTPException) as exc:
        _put(HULU, platform="hulu", win_rate="whenever")
    assert exc.value.status_code == 400 and "Auto, Apply or Don't apply" in exc.value.detail


def test_a_reset_returns_the_product_to_the_rule(store, linked):
    _put(NETFLIX, platform="netflix-15s", win_rate="skip")
    asyncio.run(main.admin_reset_forecast_map(main.ForecastMapProductRequest(product_name=NETFLIX)))
    assert fm.resolve_forecast(NETFLIX).win_rate is True and NETFLIX not in store.rows


# ---------------------------------------------------------------------------------------------------------- a duplicate keeps the original's behavior

def test_a_copy_of_a_product_set_to_skip_is_skipped_too(store, linked, monkeypatch):
    _put(NETFLIX, platform="netflix-15s", win_rate="skip")
    copy = "Netflix - Run Of Network Untargeted :15s Ads (copy)"
    original = catalog.by_name(NETFLIX)
    clone = dataclasses.replace(original, name=copy)
    real_by_name = catalog.by_name
    monkeypatch.setattr(catalog, "by_name", lambda n: clone if n == copy else real_by_name(n))
    assert fm.inherit_config(NETFLIX, copy, "admin@entravision.com") is True
    assert store.rows[copy]["win_rate"] is False and store.rows[copy]["platform"] == "netflix-15s"


def test_same_behavior_notices_a_different_win_rate():
    a = fm.ForecastConfig(True, "platform", "hulu", False, "default", win_rate=True)
    b = dataclasses.replace(a, win_rate=False)
    assert a.same_behavior(a) and not a.same_behavior(b)


# ---------------------------------------------------------------------------------------------------------- the table and the migration

def test_the_win_rate_column_is_added_by_schema_and_by_migration_0004():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    schema = (root / "schema.sql").read_text()
    assert "ALTER TABLE forecast_product_map ADD COLUMN IF NOT EXISTS win_rate BOOLEAN;" in schema
    migration = next((root / "migrations").glob("0004_*.sql")).read_text()
    assert "ADD COLUMN IF NOT EXISTS win_rate BOOLEAN" in migration and "DROP" not in migration.upper().replace("-- ", "")
    from app.services import schema_bootstrap
    assert any("forecast_product_map" in st and "win_rate" in st for st in schema_bootstrap.split_statements(schema)), \
        "schema_bootstrap would not pick the column up at startup"


class _FakeConn:
    """A connection whose SELECT with the win_rate column fails (the column does not exist yet) but the old SELECT works."""
    def __init__(self, calls, rows):
        self.calls, self.rows = calls, rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.calls.append(sql)
        if "win_rate" in sql:
            raise psycopg.errors.UndefinedColumn('column "win_rate" does not exist')
        outer = self

        class R:
            def fetchall(self_inner):
                return [dict(r) for r in outer.rows]
        return R()


def test_a_database_without_the_column_still_serves_every_other_override(monkeypatch, caplog):
    calls: list[str] = []
    rows = [{"product_name": HULU, "enabled": True, "method": "platform", "platform": "peacock", "hispanic_weighted": False,
             "updated_at": None, "updated_by": "a"}]
    monkeypatch.setattr(fm, "get_connection", lambda: _FakeConn(calls, rows))
    conftest = sys.modules.get("conftest") or sys.modules["tests.conftest"]
    real_read_rows = conftest._REAL_FORECAST_READERS["map_read_rows"]       # (conftest swaps _read_rows for an offline stub in every test)
    with caplog.at_level(logging.WARNING):
        got = real_read_rows()
    assert got is not None and got[0]["platform"] == "peacock" and got[0]["win_rate"] is None
    assert len(calls) == 2 and "win_rate" in calls[0] and "win_rate" not in calls[1]
    assert any("no win_rate column yet" in r.getMessage() for r in caplog.records)


def test_saving_without_a_win_rate_choice_still_works_on_a_database_without_the_column(monkeypatch):
    calls: list[str] = []

    class Conn(_FakeConn):
        def execute(self, sql, params=None):
            self.calls.append(sql)
            if "win_rate" in sql:
                raise psycopg.errors.UndefinedColumn('column "win_rate" does not exist')
            return None

    monkeypatch.setattr(fm, "get_connection", lambda: Conn(calls, []))
    fm._write(HULU, {"enabled": True, "method": "platform", "platform": "hulu", "hispanic_weighted": False, "win_rate": None}, "a")
    assert len(calls) == 2 and "win_rate" not in calls[1]
    with pytest.raises(RuntimeError, match="win-rate column"):
        fm._write(HULU, {"enabled": True, "method": "platform", "platform": "hulu", "hispanic_weighted": False, "win_rate": False}, "a")


# ---------------------------------------------------------------------------------------------------------- the admin page

def test_the_admin_page_has_a_win_rate_column_and_says_which_rates_are_in_force():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    html, js = (root / "app/templates/admin.html").read_text(), (root / "app/static/admin.js").read_text()
    assert ">Win rate</th>" in html and 'id="fc-winrate-note"' in html
    assert html.count("<th") >= 9 and 'colspan="9"' in html and 'colspan="8"' not in html.split('id="fc-body"', 1)[1].split("</table>", 1)[0]
    load = js.split("async function loadForecast() {", 1)[1].split("\n}\n", 1)[0]
    assert 'colspan="9"' in load and 'colspan="8"' not in load and 'getElementById("fc-winrate-note")' in load      # a failed load spans the table and clears the note
    row = js.split("function _forecastRowHtml(p) {", 1)[1].split("\nfunction renderForecastRows", 1)[0]
    assert "_forecastWinRateHtml(p, isPlatform)" in row
    cell = js.split("function _forecastWinRateHtml(p, isPlatform) {", 1)[1].split("\n}\n", 1)[0]
    assert 'data-fc="win_rate"' in cell and 'value="auto"' in cell and 'value="apply"' in cell and 'value="skip"' in cell
    assert "Auto — ${autoWord}" in cell and "fc-na" in cell                                   # a non-platform row has nothing to choose
    save = js.split("async function saveForecastRow(tr, changed) {", 1)[1].split("\nasync function resetForecastRow", 1)[0]
    assert "win_rate: winRate" in save and 'winRate = "auto"' in save                         # switching off platform data resets the choice
    assert "renderForecastWinRateNote();" in js.split("function applyForecastPayload(data) {", 1)[1].split("\n}\n", 1)[0]


def test_the_admin_win_rate_cell_and_note_render_in_a_javascript_engine():
    import json
    from pathlib import Path
    admin_js = (Path(__file__).resolve().parents[1] / "app/static/admin.js").read_text(encoding="utf-8")
    fns = "\n".join(JS_HARNESS._extract_fn(admin_js, n) for n in ("_forecastWinRateHtml", "renderForecastWinRateNote"))
    body = (
        "function escapeAttr(x) { return String(x); }\n"
        "var els = { 'fc-winrate-note': { innerHTML: '', textContent: '' } };\n"
        "var document = { getElementById: function (id) { return els[id] || null; } };\n"
        "var adminState = { forecast: { win_rate: { low: 0.1, mid: 0.15, high: 0.2 }, products: [\n"
        "  { name: 'A', enabled: true, method: 'platform', win_rate: true, win_rate_override: null, win_rate_default: true, win_rate_auto: true },\n"
        "  { name: 'B', enabled: true, method: 'platform', win_rate: false, win_rate_override: false, win_rate_default: true, win_rate_auto: true },\n"
        "  { name: 'C', enabled: true, method: 'platform', win_rate: false, win_rate_override: null, win_rate_default: false, win_rate_auto: false },\n"
        "  { name: 'E', enabled: true, method: 'platform', win_rate: false, win_rate_override: null, win_rate_default: true, win_rate_auto: false },\n"
        "  { name: 'F', enabled: false, method: 'platform', win_rate: true, win_rate_override: null, win_rate_default: true, win_rate_auto: true },\n"
        "  { name: 'D', enabled: true, method: 'generic', win_rate: false, win_rate_override: null, win_rate_default: false, win_rate_auto: false } ] } };\n"
        + fns + "\nrenderForecastWinRateNote();\n"
        "var P = adminState.forecast.products;\n"
        "return JSON.stringify({ a: _forecastWinRateHtml(P[0], true), b: _forecastWinRateHtml(P[1], true), c: _forecastWinRateHtml(P[2], true),\n"
        "  remapped: _forecastWinRateHtml(P[3], true), d: _forecastWinRateHtml(P[5], false), note: els['fc-winrate-note'].innerHTML });"
    )
    out = json.loads(JS_HARNESS._run_js(body))
    assert '<option value="auto" selected>Auto — applied</option>' in out["a"] and 'value="apply" selected' not in out["a"]
    assert '<option value="skip" selected>' in out["b"] and "Auto — applied" in out["b"]     # the choice differs from Auto: both are visible
    assert '<option value="auto" selected>Auto — not applied</option>' in out["c"]
    assert "Auto — not applied" in out["remapped"] and "Auto — applied" not in out["remapped"]   # Auto follows the series used NOW, not the built-in mapping
    assert "fc-na" in out["d"] and "<select" not in out["d"]
    assert "Programmatic win rate: 15%" in out["note"] and "(10%–20%)" in out["note"]
    # counted over the products with the button ON that use platform data (A, B, C, E: not F, which is off, and not the population-share D)
    assert "applied to the avails of 1 of the 4 products with the button on that use platform data" in out["note"] and "1 set by hand" in out["note"]


# ---------------------------------------------------------------------------------------------------------- only a MISSING column is excused

class _Boom:
    """A connection whose statements fail with an error that is NOT a missing column (a lock timeout, a dropped connection)."""
    def __init__(self, error, calls):
        self.error, self.calls = error, calls

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.calls.append(sql)
        raise self.error


@pytest.mark.parametrize("error", [psycopg.errors.QueryCanceled("canceling statement due to statement timeout"),
                                   psycopg.errors.LockNotAvailable("could not obtain lock"), psycopg.OperationalError("server closed the connection")])
def test_another_failure_is_not_mistaken_for_a_missing_column_when_reading(monkeypatch, caplog, error):
    calls: list[str] = []
    monkeypatch.setattr(fm, "get_connection", lambda: _Boom(error, calls))
    conftest = sys.modules.get("conftest") or sys.modules["tests.conftest"]
    with caplog.at_level(logging.WARNING):
        got = conftest._REAL_FORECAST_READERS["map_read_rows"]()
    assert got is None and len(calls) == 1                                            # unreadable: no second, column-less query that hides it
    assert not any("no win_rate column" in r.getMessage() for r in caplog.records)
    assert any("unavailable" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("win_rate", [None, False, True])
def test_another_failure_is_not_mistaken_for_a_missing_column_when_saving(monkeypatch, win_rate):
    calls: list[str] = []
    error = psycopg.errors.QueryCanceled("canceling statement due to statement timeout")
    monkeypatch.setattr(fm, "get_connection", lambda: _Boom(error, calls))
    with pytest.raises(psycopg.errors.QueryCanceled):                                 # surfaces as it is (set_override reports it as a failed save)
        fm._write(HULU, {"enabled": True, "method": "platform", "platform": "hulu", "hispanic_weighted": False, "win_rate": win_rate}, "a")
    assert len(calls) == 1                                                            # and no retry without the column that would "succeed" silently


# ---------------------------------------------------------------------------------------------------------- the label says what Auto gives NOW

def test_the_auto_label_follows_the_series_the_product_uses_now_not_its_built_in_mapping(store, linked):
    payload = asyncio.run(main.admin_get_forecast_map())
    hulu = _row(payload, HULU)
    assert (hulu["win_rate_default"], hulu["win_rate_auto"]) == (True, True)
    _put(HULU, platform="roku")                                                       # now read from Roku's own series: Auto means "not applied"
    hulu = _row(asyncio.run(main.admin_get_forecast_map()), HULU)
    assert (hulu["win_rate"], hulu["win_rate_default"], hulu["win_rate_auto"]) == (False, True, False)
    _put(ROKU, platform="connect-default")                                            # ... and Roku's product on a MadHive series: Auto means "applied"
    roku = _row(asyncio.run(main.admin_get_forecast_map()), ROKU)
    assert (roku["win_rate"], roku["win_rate_default"], roku["win_rate_auto"]) == (True, False, True)
    _put(HULU, platform="roku", win_rate="apply")                                     # an explicit choice leaves the Auto label alone
    hulu = _row(asyncio.run(main.admin_get_forecast_map()), HULU)
    assert (hulu["win_rate"], hulu["win_rate_override"], hulu["win_rate_auto"]) == (True, True, False)


def test_a_copy_inherits_the_choice_not_the_effective_value(store, linked, monkeypatch):
    copy = "Entravision Plus - Hulu (copy)"
    clone = dataclasses.replace(catalog.by_name(HULU), name=copy)
    real_by_name = catalog.by_name
    monkeypatch.setattr(catalog, "by_name", lambda n: clone if n == copy else real_by_name(n))
    _put(HULU, platform="peacock")                                                    # a platform change, Auto win rate: the row must carry NO choice
    assert fm.inherit_config(HULU, copy, "a") is True
    assert store.rows[copy]["win_rate"] is None and store.rows[copy]["platform"] == "peacock"
    assert fm.resolve_forecast(copy).win_rate is True                                 # ... so the copy follows the series rule like its original
    _put(HULU, platform="peacock", win_rate="skip")
    store.rows.pop(copy)
    assert fm.inherit_config(HULU, copy, "a") is True and store.rows[copy]["win_rate"] is False


def test_none_is_not_a_synonym_for_dont_apply():
    with pytest.raises(ValueError):
        fm.parse_win_rate_choice("none")
