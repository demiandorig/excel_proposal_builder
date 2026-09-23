// Pure Step 04 budget-allocation helpers. Loaded before app.js; tests/test_curate_math.py runs them under Node.
(function (root) {
  "use strict";

  function decimalsFor(unit) {
    return unit >= 1 ? 0 : Math.round(-Math.log10(unit));
  }

  // Whole dollars when the total is a whole-dollar amount, otherwise cents.
  function roundingUnitFor(total) {
    return Math.abs(total - Math.round(total)) < 0.005 ? 1 : 0.01;
  }

  // Splits `total` across `weights` so the parts sum EXACTLY to `total` at `unit` precision
  // (largest-remainder method, so the result doesn't depend on row order). Non-positive weights
  // count as 0; if every weight is 0 the total is split evenly.
  function allocateLargestRemainder(weights, total, unit) {
    const n = weights.length;
    if (!n) return [];
    const units = Math.max(0, Math.round(total / unit));
    const clean = weights.map(w => (Number.isFinite(w) && w > 0 ? w : 0));
    const weightSum = clean.reduce((a, b) => a + b, 0);
    const w = weightSum > 0 ? clean : clean.map(() => 1);
    const sum = weightSum > 0 ? weightSum : n;
    const raw = w.map(x => (units * x) / sum);
    const parts = raw.map(Math.floor);
    const left = units - parts.reduce((a, b) => a + b, 0);
    const order = raw
      .map((r, i) => [r - Math.floor(r), w[i], i])
      .sort((a, b) => (b[0] - a[0]) || (b[1] - a[1]) || (a[2] - b[2]));
    for (let k = 0; k < left; k++) parts[order[k % n][2]] += 1;
    const dp = decimalsFor(unit);
    return parts.map(u => Number((u * unit).toFixed(dp)));
  }

  function positiveSum(values) {
    return values.reduce((a, b) => a + (Number.isFinite(b) && b > 0 ? b : 0), 0);
  }

  // New budgets for `target`, keeping each line's current share.
  function scaleToTotal(budgets, target) {
    return allocateLargestRemainder(budgets, target, roundingUnitFor(target));
  }

  // Sets line `k` to `pct`% of the current total; the other lines absorb the difference in
  // proportion to their current budgets (evenly if they're all 0). The total never changes.
  function setSharePct(budgets, k, pct) {
    const total = positiveSum(budgets);
    if (!(total > 0) || budgets.length < 2 || k < 0 || k >= budgets.length || !Number.isFinite(pct)) {
      return budgets.slice();
    }
    const unit = roundingUnitFor(total);
    const dp = decimalsFor(unit);
    const clampedPct = Math.min(100, Math.max(0, pct));
    const kBudget = Number((Math.round((total * clampedPct / 100) / unit) * unit).toFixed(dp));
    const others = budgets.filter((_, i) => i !== k);
    const rebalanced = allocateLargestRemainder(others, total - kBudget, unit);
    const out = [];
    let j = 0;
    for (let i = 0; i < budgets.length; i++) out.push(i === k ? kBudget : rebalanced[j++]);
    return out;
  }

  // Display shares (one decimal) that always add up to exactly 100.0; null when there's no total.
  function sharePercents(budgets) {
    if (!(positiveSum(budgets) > 0)) return budgets.map(() => null);
    return allocateLargestRemainder(budgets, 100, 0.1);
  }

  const api = { allocateLargestRemainder, roundingUnitFor, scaleToTotal, setSharePct, sharePercents };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.CurateMath = api;
})(typeof window !== "undefined" ? window : this);
