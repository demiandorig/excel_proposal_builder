"""The whole-series validation rules (app/services/forecast_import/validate.py). Each rule is shown firing on a series
built to break it, and NOT firing on the real exports, so the rules cannot drift into either crying wolf or sleeping."""
from __future__ import annotations

import pytest

from app.services.forecast_import import geo_reference as geo
from app.services.forecast_import.model import Figures, PeriodInfo, SeriesPayload, SeriesSpec
from app.services.forecast_import.validate import summarize, validate
from tests.forecast_support import payload as shipped_payload, shipped


def build(n_dmas=210, base=1_000_000, zip_total=None, state_total=None, devices=None, derived=None, **spec_kw):
    """A consistent series: every breakdown totals the same (a ZIP level of 1,200 rows keeps it above the thin-ZIP warning)."""
    spec = SeriesSpec(series_key="t", title="T", channel="ctv", avails_unit="monthly", reader="madhive_zip", source="x", **spec_kw)
    p = SeriesPayload(spec=spec, source_name="t.zip", source_sha256="0" * 64)
    for i, d in enumerate(geo.dma_table()[:n_dmas]):
        p.levels["dma"][d.key] = Figures(avails=base / (i + 1) ** 0.5)
    total = sum(f.avails for f in p.levels["dma"].values())
    if state_total is not False:
        p.levels["state"]["CA"] = Figures(avails=(state_total if state_total is not None else total))
    if zip_total is not False:
        zt = zip_total if zip_total is not None else total
        for i in range(1200):
            p.levels["zip"][f"{10000 + i:05d}"] = Figures(avails=zt / 1200)
    if devices is not False:
        p.devices["connected_tv"] = Figures(avails=(devices if devices is not None else total))
    if derived is not None:
        p.stats["derived"] = derived
    return p


def rules(p, severity=None):
    return sorted({f.rule for f in p.findings if severity in (None, f.severity)})


def test_a_consistent_series_passes_clean():
    p = build()
    validate(p)
    assert rules(p, "error") == [] and rules(p, "warning") == []


def test_an_empty_series_is_an_error():
    p = build(n_dmas=0, state_total=False, zip_total=False, devices=False)
    validate(p)
    assert "S005" in rules(p, "error")


@pytest.mark.parametrize("n,severity", [(210, None), (209, "warning"), (200, "warning"), (199, "error"), (40, "error")])
def test_market_coverage_thresholds(n, severity):
    p = build(n_dmas=n)
    validate(p)
    got = [f.severity for f in p.findings if f.rule == "N006"]
    assert got == ([severity] if severity else [])


def test_the_missing_markets_are_named():
    p = build(n_dmas=209)
    validate(p)
    assert geo.dma_table()[209].name in next(f.message for f in p.findings if f.rule == "N006")


def test_breakdowns_that_do_not_agree_are_an_error():
    p = build(devices=1_000_000)                                           # devices total far below the markets total
    validate(p)
    assert any(f.rule == "N004" and "device" in f.message for f in p.errors)


def test_small_rounding_differences_between_breakdowns_are_tolerated():
    base = build()
    total = sum(f.avails for f in base.levels["dma"].values())
    p = build(zip_total=total * 1.012, state_total=total * 1.004, devices=total * 0.996)
    validate(p)
    assert "N004" not in rules(p)
    p2 = build(zip_total=total * 1.03)
    validate(p2)
    assert "N004" in rules(p2, "error")                                    # beyond the ZIP tolerance (1.5%)


def test_unknown_share_warns_then_errors():
    for share, expected in ((0.02, []), (0.12, ["warning"]), (0.30, ["error"])):
        p = build()
        total = sum(f.avails for f in p.levels["dma"].values())
        p.unknown["dma"] = Figures(avails=total * share / (1 - share))
        validate(p)
        assert [f.severity for f in p.findings if f.rule == "N005"] == expected, share


def test_derived_columns_that_disagree_are_flagged():
    ok = {"dma": {"rows": 100, "total_ne_monthly": 0, "daily_sum": 1000.0, "weekly_sum": 7000.0, "monthly_sum": 30_000.0, "reach_gt_universe": 0}}
    p = build(derived=ok); validate(p); assert rules(p) == []
    bad = {"dma": {"rows": 100, "total_ne_monthly": 5, "daily_sum": 500.0, "weekly_sum": 7000.0, "monthly_sum": 30_000.0, "reach_gt_universe": 3}}
    p = build(derived=bad); validate(p)
    assert {"N001", "N003"} <= set(rules(p, "error")) and "N002" in rules(p, "warning")


def test_a_ranking_with_neither_new_york_nor_los_angeles_on_top_is_flagged():
    p = build()
    p.levels["dma"]["new-york"].avails = 1.0
    p.levels["dma"]["los-angeles"].avails = 1.0
    validate(p)
    assert "N010" in rules(p, "warning")


def test_a_large_change_from_the_published_version_needs_an_explicit_ok():
    p = build()
    total = sum(f.avails for f in p.levels["dma"].values())
    validate(p, previous_total=total * 2.0)
    assert any(f.rule == "M002" and f.severity == "error" and "--accept-large-change" in f.message for f in p.findings)
    p = build(); validate(p, previous_total=total * 2.0, accept_large_change=True)
    assert [f.severity for f in p.findings if f.rule == "M002"] == ["warning"]
    p = build(); validate(p, previous_total=total * 1.2)
    assert "M002" not in rules(p) and p.stats["change_vs_current"] == pytest.approx(-0.1667, abs=1e-3)


def test_a_changed_live_sports_period_note_is_flagged():
    p = build(period=PeriodInfo(label="Regular season", source_note="Regular Season Ends on 9/27"))
    p.stats["header_note"] = "Regular Season Ends on 10/4"
    validate(p)
    assert "P001" in rules(p, "warning")
    p = build(period=PeriodInfo(label="Regular season", source_note="Regular Season Ends on 9/27"))
    p.stats["header_note"] = "Regular Season Ends on 9/27"
    validate(p)
    assert "P001" not in rules(p)


def test_the_summary_stored_with_a_version_counts_findings_and_drops_bulky_stats():
    p = build(n_dmas=209)
    p.stats["derived"] = {"dma": {"rows": 1, "total_ne_monthly": 0, "daily_sum": 1.0, "weekly_sum": 1.0, "monthly_sum": 30.0, "reach_gt_universe": 0}}
    validate(p)
    s = summarize(p)
    assert s["counts"]["warning"] == 1 and s["counts"]["error"] == 0 and "derived" not in s["stats"]
    assert s["findings"][0]["rule"] == "N006"


@pytest.mark.parametrize("key", ["connect-default", "connect-premier", "hispanic-connect-reach", "pause-ads", "edigital-display", "edigital-olv"])
def test_the_real_exports_pass_every_rule_without_a_warning(key):
    p = shipped_payload(key)
    validate(p)
    assert rules(p, "error") == [], [f.message for f in p.errors]
    assert [f for f in p.findings if f.severity == "warning"] == []
    assert p.stats["unknown_share"]["dma"] < 0.05
    assert max(p.stats["level_totals"].values()) / min(p.stats["level_totals"].values()) < 1.02


def test_every_shipped_series_passes_validation():
    for s in shipped()[0]:
        p = shipped_payload(s.series_key)
        validate(p)
        assert rules(p, "error") == [], (s.series_key, [f.message for f in p.errors])
