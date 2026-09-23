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


def test_set_share_rebalances_others_proportionally_and_keeps_total():
    budgets = [10000, 5000, 3333.33, 1666.67]
    result = _call("setSharePct", budgets, 0, 30)
    assert round(sum(result), 2) == round(sum(budgets), 2)
    assert result[0] == 6000
    # the others keep their 3 : 2 : 1 ratio (to rounding)
    assert abs(result[1] / result[3] - 3) < 0.01 and abs(result[2] / result[3] - 2) < 0.01


def test_set_share_extremes_and_clamping():
    budgets = [5000, 3000, 2000]
    assert _call("setSharePct", budgets, 0, 100) == [10000, 0, 0]
    assert _call("setSharePct", budgets, 0, 0) == [0, 6000, 4000]
    assert _call("setSharePct", budgets, 1, 250) == [0, 10000, 0]
    assert _call("setSharePct", budgets, 1, -5) == [7143, 0, 2857]  # others keep their 5 : 2 ratio


def test_set_share_when_other_lines_are_zero_splits_the_rest_evenly():
    assert _call("setSharePct", [6000, 0, 0], 0, 50) == [3000, 1500, 1500]


def test_set_share_is_a_no_op_for_single_line_or_zero_total():
    assert _call("setSharePct", [5000], 0, 40) == [5000]
    assert _call("setSharePct", [0, 0], 0, 40) == [0, 0]


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
