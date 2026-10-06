"""Step 04 budget math (app/static/curate-math.js), exercised under Node. Skips if Node isn't installed."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parents[1] / "app" / "static" / "curate-math.js"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node not installed")


def _call(fn: str, *args):
    script = (
        f"const m = require({json.dumps(str(MODULE))});"
        f"process.stdout.write(JSON.stringify(m.{fn}(...{json.dumps(list(args))})));"
    )
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=30, check=True)
    return json.loads(out.stdout)


def test_scale_keeps_mix_and_hits_target_exactly():
    result = _call("scaleToTotal", [10000, 5000, 3333.33, 1666.67], 25000)
    assert sum(result) == 25000
    assert result == [12500, 6250, 4167, 2083]


def test_scale_with_cents_target_is_exact_to_the_cent():
    result = _call("scaleToTotal", [1000, 2000, 3000], 10000.5)
    assert round(sum(result), 2) == 10000.5
    assert all(round(v, 2) == v for v in result)


def test_scale_from_all_zero_splits_evenly_and_zero_lines_stay_zero():
    assert _call("scaleToTotal", [0, 0, 0], 900) == [300, 300, 300]
    assert _call("scaleToTotal", [0, 400, 600], 2000) == [0, 800, 1200]


def test_percents_to_budgets_hits_the_total_exactly_when_tenths_sum_to_100():
    result = _call("percentsToBudgets", [333, 333, 334], 10000)
    assert result == [3330, 3330, 3340]
    assert _call("percentsToBudgets", [333, 333, 334], 7) == [2, 2, 3]
    assert round(sum(_call("percentsToBudgets", [500, 250, 250], 10000.5)), 2) == 10000.5


def test_percents_to_budgets_refuses_anything_that_is_not_exactly_100():
    # the allocator alone would renormalize 87.5% up to 100% — the helper must not
    assert _call("percentsToBudgets", [500, 375], 10000) is None
    assert _call("percentsToBudgets", [600, 500], 10000) is None
    assert _call("percentsToBudgets", [1000, 0], 0) is None
    assert _call("percentsToBudgets", [], 100) is None


def test_percents_to_budgets_allows_a_zero_line():
    assert _call("percentsToBudgets", [700, 300, 0], 1000) == [700, 300, 0]


def test_tenths_helpers_snap_clamp_and_convert():
    assert _call("toTenths", 33.34) == 333 and _call("toTenths", 33.35) == 334
    assert _call("toTenths", 250) == 1000 and _call("toTenths", -5) == 0 and _call("toTenths", "x") == 0
    assert _call("sumTenths", [333, 333, 334]) == 1000
    assert _call("tenthsFromAmount", 4000, 10000) == 400
    assert _call("tenthsFromAmount", 4003, 10000) == 400          # snaps to the 0.1% grid
    assert _call("tenthsFromAmount", 100, 0) == 0
    assert _call("amountFromTenths", 400, 10000) == 4000


def test_even_tenths_always_sum_to_exactly_100_percent():
    assert sorted(_call("evenTenths", 3)) == [333, 333, 334]
    assert _call("evenTenths", 4) == [250, 250, 250, 250]
    for n in (1, 2, 3, 6, 7, 9):
        assert sum(_call("evenTenths", n)) == 1000
    assert _call("evenTenths", 0) == []


def test_share_round_trip_through_the_display_shares_is_lossless():
    budgets = [5000, 3000, 2000]
    shares = _call("sharePercents", budgets)                     # what the read-only column shows
    tenths = [round(x * 10) for x in shares]
    assert sum(tenths) == 1000
    assert _call("percentsToBudgets", tenths, 10000) == budgets  # entering edit mode and saving untouched changes nothing


def test_share_percents_always_display_as_exactly_100():
    shares = _call("sharePercents", [1, 1, 1])
    assert shares == [33.4, 33.3, 33.3] or sorted(shares) == [33.3, 33.3, 33.4]
    assert round(sum(shares), 1) == 100.0
    assert _call("sharePercents", [0, 0]) == [None, None]


def test_round_trip_scale_up_then_down_preserves_mix():
    original = [7000, 2000, 1000]
    up = _call("scaleToTotal", original, 30000)
    back = _call("scaleToTotal", up, 10000)
    assert back == original
