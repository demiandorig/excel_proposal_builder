"""
Constanza's estimation engine — "Stanza": the fixed anchor that turns a
resolved geo (see constanza_geo.py) into a DMA-weighted avails number for
a given product.

This does NOT introduce forecasting where planners were previously
guessing — avails today is a manual, accurate, platform-sourced number
(pulled by hand from Roku Ads Manager, Amazon DSP, The Trade Desk, or
Madhive's UI, one geo at a time). Stanza approximates that same number
fast, from bundled DMA-level data, as a starting point the planner can
accept or refine — not a silent replacement for checking the platform
when precision matters on a specific deal.

Two confidence tiers, matching how directly each catalog product maps to
the underlying CTV avails-by-DMA workbook:

  * PLATFORM-MODELED — the product's inventory pool has its own workbook
    sheet (Roku Channel, Prime Video, Hulu, Peacock, other-FAST, vMVPD) or
    is the blended Madhive "Cumulative" total (the generic Entravision
    Plus CTV/OTT English/Spanish/Hispanic/Reach products). High confidence.
  * GENERIC FALLBACK — no platform-specific model exists (VIX 360 and
    Netflix are bought as custom Trade-Desk site-lists/PMPs, not against
    any of the workbook's named platforms) — apportion the planner's own
    entered national total by plain DMA population share. Lower
    confidence; always available as a last resort.

Phase 2 note: all weighting (general population, Hispanic proxy, and the
age/gender/interest layer in constanza_audience.py) goes through the one
`_area_weights()` function below, keyed on each ResolvedArea's OWN
population (`area.area_population`), never its parent DMA's full
population. Phase 1 had two separate weighting code paths — the general
one already did this correctly, but `_hispanic_weights()` computed off
the whole DMA regardless of how much of it was actually in scope. That
was harmless while geo resolution only ever produced whole-DMA/state
matches (dma_share was always 1.0), but would have silently overstated
any sub-DMA Hispanic-targeted estimate by up to 1/dma_share once ZIP/
county/city resolution shipped. Unifying the two paths fixes this by
construction instead of requiring every future weighting case to
remember to use area_population.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.data.constanza import PlatformFigures, load_platform_dma, load_hispanic_pct_by_state
from app.services.constanza_geo import GeoResolution, ResolvedArea

# Catalog product name -> workbook platform key. Only products whose
# inventory pool is actually one of the workbook's modeled platforms are
# listed here; everything else (VIX 360, Netflix, and any product not
# present at all) falls back to the generic population-based estimate.
# See Product Constanza's plan doc for the buying-stack rationale (Roku
# Ads = Roku's self-serve platform, Prime Video = Amazon DSP, the generic
# CTV/OTT products = Madhive's blended list — all still describable by
# this workbook's per-DMA figures regardless of who executes the buy).
PLATFORM_MODELED_PRODUCTS: dict[str, str] = {
    "Entravision Plus - Roku Ads (Includes The Roku Channel and the popular Espacio Latino Hub)": "roku",
    "Entravision Plus - Amazon Prime Video": "prime",
    "Entravision Plus - Hulu": "hulu",
    "Entravision Plus - Peacock": "peacock",
    "Entravision Plus - Other FAST (Tubi, Pluto TV, Samsung TV Plus, and other long-tail FAST channels)": "other_fast",
    "Entravision Plus - vMVPD (Fubo, Sling, DIRECTV Stream, Philo, Frndly, Vidgo + MVPD streaming apps)": "vmvpd",
    "Entravision Plus CTV/OTT - English Content": "cumulative",
    "Entravision Plus CTV/OTT- Spanish Content only": "cumulative",
    "Entravision Plus - Hispanics CTV/OTT": "cumulative",
    "Entravision Plus - CTV/OTT Reach": "cumulative",
}

# Products whose targeting is Hispanic-specific — re-weight area shares by
# state-level Hispanic population % (a proxy; the workbook has no DMA-level
# demographic breakout) instead of general population share.
HISPANIC_TARGETED_PRODUCTS = {"Entravision Plus - Hispanics CTV/OTT"}

LOW_MULT = 0.7
HIGH_MULT = 1.3


@dataclass
class AreaEstimate:
    area_label: str            # human-readable: the DMA name for whole-DMA/state matches, or the sub-DMA/country area's own label
    dma: str | None            # parent DMA, or None for a country-level area
    weight: float               # this area's share of the resolved geo's total for the applicable trait (sums to ~1 across areas)
    imps_low: float
    imps_mid: float
    imps_high: float
    unique_viewers: float | None
    unique_hh: float | None
    # Carried straight from the originating ResolvedArea so
    # constanza_audience.py can apply age/gender/interest narrowing
    # without re-resolving geo text or assuming a positional match against
    # GeoResolution.areas — engine paths can skip an area (no bundled
    # platform data for it), so by_area is not guaranteed to line up 1:1
    # with the original areas list.
    area_population: float = 0.0
    source_type: str = ""
    apportionment: str = ""
    state: str | None = None
    geo_id: str | None = None


@dataclass
class AvailsEstimate:
    product_name: str
    platform: str              # workbook platform key, or "generic_population"
    confidence: str            # "platform_modeled" | "generic_fallback"
    geo_method: str            # from GeoResolution.method
    geo_confidence: str        # from GeoResolution.geo_confidence
    months: int
    imps_low: float
    imps_mid: float
    imps_high: float
    est_uniques: float | None
    by_area: list[AreaEstimate] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _area_weights(areas: list[ResolvedArea], trait: str | None = None) -> list[float]:
    """Normalized weight per area, positionally aligned with `areas`.

    trait=None      -> weight by each area's own population (general case).
    trait="hispanic" -> weight by area.area_population x hispanic_pct[area.state].

    ALWAYS uses `area.area_population` — the resolved area's own
    population, whatever fraction of its parent DMA that actually is —
    never the parent DMA's full population. See module docstring.
    """
    pct_by_state = load_hispanic_pct_by_state() if trait == "hispanic" else None
    raw: list[float] = []
    for area in areas:
        if trait == "hispanic":
            pct = pct_by_state.get(area.state, 0.0) if area.state else 0.0
            raw.append(area.area_population * pct)
        else:
            raw.append(area.area_population)
    total = sum(raw)
    if not total:
        return [0.0] * len(areas)
    return [v / total for v in raw]


def estimate_platform_modeled(
    product_name: str, platform: str, geo: GeoResolution, months: int,
) -> AvailsEstimate:
    figures_by_dma = load_platform_dma().get(platform, {})
    notes: list[str] = []

    use_hispanic_weights = product_name in HISPANIC_TARGETED_PRODUCTS
    if use_hispanic_weights:
        notes.append(
            "Weighted by state-level Hispanic population % (proxy — the source "
            "workbook has no DMA-level demographic breakout) instead of general population share."
        )
    weights = _area_weights(geo.areas, trait="hispanic" if use_hispanic_weights else None)

    by_area: list[AreaEstimate] = []
    imps_low = imps_mid = imps_high = 0.0
    uniques_total = 0.0
    uniques_known = False
    for area, weight in zip(geo.areas, weights):
        if area.dma is None:
            continue  # country-level area with no DMA-bundled platform data — handled by the generic path instead
        figs: PlatformFigures | None = figures_by_dma.get(area.dma.strip().lower())
        if figs is None:
            continue

        # Impressions accumulate over the flight; uniques/reach are a
        # monthly reach concept, not cumulative, so they're reported as-is
        # (the workbook itself never multiplies reach by month count).
        # Each area only gets its own SHARE of its parent DMA's bundled
        # figures (area.dma_share) — a ZIP that's 8% of its DMA's
        # population contributes 8% of that DMA's avails, not the whole
        # DMA's worth just because the DMA matched.
        area_low = figs.imps_low * months * area.dma_share
        area_mid = figs.imps_mid * months * area.dma_share
        area_high = figs.imps_high * months * area.dma_share
        area_uniques = figs.unique_viewers * area.dma_share if figs.unique_viewers is not None else None
        area_unique_hh = figs.unique_hh * area.dma_share if figs.unique_hh is not None else None

        imps_low += area_low
        imps_mid += area_mid
        imps_high += area_high
        if area_uniques is not None:
            uniques_total += area_uniques
            uniques_known = True

        by_area.append(AreaEstimate(
            area_label=area.label or area.dma, dma=area.dma, weight=weight,
            imps_low=area_low, imps_mid=area_mid, imps_high=area_high,
            unique_viewers=area_uniques, unique_hh=area_unique_hh,
            area_population=area.area_population, source_type=area.source_type,
            apportionment=area.apportionment, state=area.state, geo_id=area.geo_id,
        ))

    if platform == "other_fast":
        notes.append(
            "Other-FAST (Tubi/Pluto/long-tail) unique reach isn't separately modeled in the "
            "source workbook — its reach is folded into the FAST category ceiling (~= Roku "
            "Channel's reach), not additive. Impressions are still additive and shown above."
        )

    return AvailsEstimate(
        product_name=product_name, platform=platform, confidence="platform_modeled",
        geo_method=geo.method, geo_confidence=geo.geo_confidence, months=months,
        imps_low=imps_low, imps_mid=imps_mid, imps_high=imps_high,
        est_uniques=uniques_total if uniques_known else None,
        by_area=by_area, notes=notes,
    )


def estimate_generic_population(
    product_name: str, geo: GeoResolution, months: int, national_monthly_total: float,
) -> AvailsEstimate:
    """Fallback for products with no workbook-modeled platform (VIX 360,
    Netflix, or any product not in PLATFORM_MODELED_PRODUCTS), and the
    only path available for a country-level (non-US) resolution: apportion
    the planner's own entered national/global monthly total by plain
    population share across the resolved areas. Rougher than the
    platform-modeled path — not validated against any real platform
    delivery data — and should be labeled as such in the UI."""
    weights = _area_weights(geo.areas)

    by_area: list[AreaEstimate] = []
    for area, weight in zip(geo.areas, weights):
        area_mid = national_monthly_total * weight * months
        by_area.append(AreaEstimate(
            area_label=area.label or area.dma or "?", dma=area.dma, weight=weight,
            imps_low=area_mid * LOW_MULT, imps_mid=area_mid, imps_high=area_mid * HIGH_MULT,
            unique_viewers=None, unique_hh=None,
            area_population=area.area_population, source_type=area.source_type,
            apportionment=area.apportionment, state=area.state, geo_id=area.geo_id,
        ))

    total_mid = national_monthly_total * months
    notes = [
        "No CTV platform is separately modeled for this product (it's bought as a custom "
        "Trade-Desk site-list/PMP) — this spreads your own entered monthly total across the "
        "resolved geo by population share only. Not validated against real platform delivery."
    ]
    if geo.geo_confidence == "low_confidence_international":
        notes.append(
            "International geography — population-share approximation only, no local platform "
            "avails data behind it. Materially lower confidence than a US DMA-backed estimate."
        )

    return AvailsEstimate(
        product_name=product_name, platform="generic_population", confidence="generic_fallback",
        geo_method=geo.method, geo_confidence=geo.geo_confidence, months=months,
        imps_low=total_mid * LOW_MULT, imps_mid=total_mid, imps_high=total_mid * HIGH_MULT,
        est_uniques=None, by_area=by_area, notes=notes,
    )


def estimate_avails(
    product_name: str, geo: GeoResolution, months: int = 1,
    national_monthly_total: float | None = None,
) -> AvailsEstimate | None:
    """Entry point: routes to the platform-modeled or generic-fallback
    estimator based on the product name. Returns None only when the
    generic fallback would apply but no `national_monthly_total` was
    given (nothing to apportion). A country-level (non-US) resolution
    always routes to the generic fallback — no platform ever has
    DMA-style bundled data outside the US."""
    platform = PLATFORM_MODELED_PRODUCTS.get(product_name)
    is_international = geo.geo_confidence == "low_confidence_international"
    if platform and not is_international:
        return estimate_platform_modeled(product_name, platform, geo, months)
    if national_monthly_total is None:
        return None
    return estimate_generic_population(product_name, geo, months, national_monthly_total)
