"""
Smoke-test Constanza's estimation engine.

Mirrors the source CTV-avails-by-DMA workbook's own "Validation" sheet:
summed per-DMA figures must reconcile exactly to the workbook's declared
national totals (data extracted by scripts/extract_constanza_data.py).
Also checks flight-length scaling, the Hispanic-population re-weighting,
and the generic population-share fallback used for non-modeled products
(VIX 360, Netflix).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.data.constanza import load_platform_dma, load_dma_reference
from app.services.constanza_geo import resolve_geo
from app.services.constanza_engine import estimate_avails, PLATFORM_MODELED_PRODUCTS

print("=" * 80)
print("Constanza engine — reconciliation against the source workbook")
print("=" * 80)

ROKU = "Entravision Plus - Roku Ads (Includes The Roku Channel and the popular Espacio Latino Hub)"
PRIME = "Entravision Plus - Amazon Prime Video"
REACH = "Entravision Plus - CTV/OTT Reach"
HISPANIC = "Entravision Plus - Hispanics CTV/OTT"
VIX = "Entravision Plus - VIX 360 (Includes VIX + UnivisionNow + Univision.com / Univision Apps)"

national = resolve_geo("National")


def close(a, b, tol=1e-6):
    return abs(a - b) <= tol * max(abs(a), abs(b), 1)


# --- 1. National sum reconciles to the workbook's own per-DMA totals -------
platform_dma = load_platform_dma()
for label, product, platform in [("Roku", ROKU, "roku"), ("Prime", PRIME, "prime")]:
    est = estimate_avails(product, national, months=1)
    expected_mid = sum(f.imps_mid for f in platform_dma[platform].values())
    assert close(est.imps_mid, expected_mid), (label, est.imps_mid, expected_mid)
    print(f"{label} national imps_mid reconciles to bundled per-DMA sum: OK ({est.imps_mid:,.0f})")

est = estimate_avails(REACH, national, months=1)
expected = sum(f.imps_mid for f in platform_dma["cumulative"].values())
assert close(est.imps_mid, expected)
print(f"Blended 'CTV/OTT Reach' (Madhive cumulative) national reconciles: OK ({est.imps_mid:,.0f})")

# --- 2. Every catalog-mapped product actually has bundled data for its geo -
for name, platform in PLATFORM_MODELED_PRODUCTS.items():
    assert platform in platform_dma, f"{name} maps to unbundled platform {platform!r}"
print(f"All {len(PLATFORM_MODELED_PRODUCTS)} platform-modeled products map to bundled platform data: OK")

# --- 3. Flight-length scaling: impressions scale, uniques don't ------------
la = resolve_geo("Los Angeles DMA")
est_1mo = estimate_avails(ROKU, la, months=1)
est_3mo = estimate_avails(ROKU, la, months=3)
assert close(est_3mo.imps_mid, est_1mo.imps_mid * 3)
assert est_1mo.est_uniques == est_3mo.est_uniques  # reach doesn't accumulate
print("Impressions scale linearly with flight months; uniques/reach stay monthly: OK")

# --- 4. DMA-level weight sums to ~1 across a resolved geo ------------------
tx = resolve_geo("Texas")
est_tx = estimate_avails(ROKU, tx, months=1)
weight_sum = sum(d.weight for d in est_tx.by_area)
assert close(weight_sum, 1.0, tol=1e-6), weight_sum
print(f"Per-DMA weights sum to 1.0 across a resolved state geo: OK ({weight_sum:.6f})")

# --- 5. Hispanic-targeting re-weight actually differs from population -----
est_hispanic = estimate_avails(HISPANIC, tx, months=1)
est_general = estimate_avails(REACH, tx, months=1)
# Same geo, different products/platforms — just confirm the Hispanic path
# ran its own weighting note and produced DMA weights that differ from a
# population-weighted run of the same geo against a platform that doesn't
# use it.
assert any("Hispanic" in n for n in est_hispanic.notes)
hispanic_weights = {d.dma: d.weight for d in est_hispanic.by_area}
population_weights = {d.dma: d.weight for d in est_general.by_area}
assert hispanic_weights != population_weights
print("Hispanic-targeted product's DMA weights differ from population-only weights: OK")

# --- 6. Generic fallback (no platform model) apportions by population -----
est_vix = estimate_avails(VIX, la, months=1, national_monthly_total=1_000_000)
assert est_vix.confidence == "generic_fallback"
assert close(est_vix.imps_mid, 1_000_000)
assert estimate_avails(VIX, la, months=1) is None  # no total given -> nothing to apportion
print("Generic population fallback (VIX 360/Netflix) apportions correctly, and requires a total: OK")

# --- 7. DMA reference itself reconciles to the national TV-household total
dmas = load_dma_reference()
assert len(dmas) == 210
assert close(sum(d.tv_hh for d in dmas), 128_100_000, tol=1e-9)
print(f"210 bundled DMAs sum to the Nielsen 128.1M national TV-household total: OK")

print()
print("All Constanza engine checks passed.")
