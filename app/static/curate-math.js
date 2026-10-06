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

  // --- Staged "Edit mix" helpers -------------------------------------------------------
  // Percentages are held as whole TENTHS of a percent (1000 = 100.0%) so "33.3 + 33.3 + 33.4"
  // can be checked for exactly 100 — floats can't — and so every row snaps to the same 0.1%
  // grid the read-only share column displays.

  function toTenths(pct) {
    return Number.isFinite(pct) ? Math.min(1000, Math.max(0, Math.round(pct * 10))) : 0;
  }

  function sumTenths(tenths) {
    return tenths.reduce((a, b) => a + (Number.isFinite(b) ? b : 0), 0);
  }

  // 100.0% split as evenly as one-decimal shares allow (e.g. 3 lines -> 33.4 / 33.3 / 33.3).
  function evenTenths(n) {
    return n > 0 ? allocateLargestRemainder(new Array(n).fill(1), 1000, 1) : [];
  }

  // A dollar amount as tenths of `total`; 0 when there's no usable total.
  function tenthsFromAmount(amount, total) {
    return total > 0 && Number.isFinite(amount) ? toTenths((amount / total) * 100) : 0;
  }

  function amountFromTenths(tenths, total) {
    return (total * tenths) / 1000;
  }

  // Budgets that sum EXACTLY to `total` for the staged tenths — or null unless the tenths add up
  // to exactly 100.0%. (The allocator below silently renormalizes any weights it's given, so the
  // sum has to be checked here: otherwise a plan at 87.5% would quietly be stretched to 100%.)
  function percentsToBudgets(tenths, total) {
    if (!(total > 0) || !tenths.length || sumTenths(tenths) !== 1000) return null;
    return allocateLargestRemainder(tenths.map(t => Math.max(0, t)), total, roundingUnitFor(total));
  }

  // Display shares (one decimal) that always add up to exactly 100.0; null when there's no total.
  function sharePercents(budgets) {
    if (!(positiveSum(budgets) > 0)) return budgets.map(() => null);
    return allocateLargestRemainder(budgets, 100, 0.1);
  }

  const api = {
    allocateLargestRemainder, roundingUnitFor, scaleToTotal, sharePercents,
    toTenths, sumTenths, evenTenths, tenthsFromAmount, amountFromTenths, percentsToBudgets,
  };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.CurateMath = api;
})(typeof window !== "undefined" ? window : this);
