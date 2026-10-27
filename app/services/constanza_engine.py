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

Phase 2 note: all weighting (general population, Hispanic population, and the
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

import logging
from dataclasses import dataclass, field, replace

from app.data.constanza import PlatformFigures, load_dma_reference, load_platform_dma
from app.services import constanza_hispanic, constanza_win_rate, forecast_store
from app.services.constanza_forecast_map import (
    DEFAULT_HISPANIC_PRODUCTS, DEFAULT_PLATFORM_PRODUCTS, ForecastConfig, resolve_forecast,
)
from app.services.constanza_geo import GeoResolution, ResolvedArea
from app.services.forecast_import import geo_reference

log = logging.getLogger(__name__)

# The BUILT-IN mapping of catalog products to the workbook's platform data, and the Hispanic-weighted set, now live in
# constanza_forecast_map.py as the DEFAULTS of Admin -> Forecast (an admin can override any product there). They are
# re-exported here under their old names; the estimate paths below ask resolve_forecast() for a product's EFFECTIVE
# config, so an admin's change takes effect without a code change.
PLATFORM_MODELED_PRODUCTS = DEFAULT_PLATFORM_PRODUCTS
HISPANIC_TARGETED_PRODUCTS = DEFAULT_HISPANIC_PRODUCTS

LOW_MULT = 0.7
HIGH_MULT = 1.3
# A market a series EXTENDED from a sample (Netflix) is less certain than one the source reported: a wider planning range.
EXTENDED_BAND = (0.55, 1.45)
WEEKS_PER_MONTH = 365.25 / 12 / 7


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
    # The platform's own count of the people it can reach here for this product (the export's "Reach Universe"), scaled the
    # same way as the impressions. None when the data behind the estimate has no such figure (the bundled workbook, a
    # population-share estimate). It is the ADDRESSABLE POOL — a ceiling the product could reach, not delivery (the people a flight reaches
    # are `unique_viewers`, always at or below the impressions) — and a property of the market, so the win rate leaves it alone.
    reach_universe: float | None = None
    # True for a market of a whole-state request whose avails were scaled up to the platform's own STATE total (see _state_scales): that total
    # holds households served from markets homed elsewhere, so the Hispanic layer cuts it at the state's share, not the market's.
    state_scaled: bool = False


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
    # Where the numbers came from (forecast_store.SeriesRef.to_public()): series, version, data basis, unit, period.
    series: dict | None = None
    weeks: float | None = None
    # Sum of the areas' reach universes (people the product can reach in the whole geography); None when no area has one.
    reach_universe: float | None = None
    # Set when the win rate was applied (programmatic MadHive / Trade Desk products): the rates used and the platform's raw
    # figures from before it — see constanza_win_rate.py. None for everything else.
    win_rate: dict | None = None


def _area_weights(areas: list[ResolvedArea], trait: str | None = None) -> list[float]:
    """Normalized weight per area, positionally aligned with `areas`.

    trait=None      -> weight by each area's own population (general case).
    trait="hispanic" -> weight by each area's own HISPANIC population: its population x its real Hispanic share (the Census ACS,
                        constanza_hispanic.py — the area's own row, its parent DMA's when the area is too small, the state-level
                        proxy only when neither exists).

    ALWAYS uses `area.area_population` — the resolved area's own
    population, whatever fraction of its parent DMA that actually is —
    never the parent DMA's full population. See module docstring.
    """
    raw: list[float] = []
    for area in areas:
        if trait == "hispanic":
            raw.append(constanza_hispanic.hispanic_population(area) or 0.0)
        else:
            raw.append(area.area_population)
    total = sum(raw)
    if not total and trait == "hispanic":
        return _area_weights(areas, None)       # no source knows any area: population shares, not all zeros
    if not total:
        return [0.0] * len(areas)
    return [v / total for v in raw]


def estimate_platform_modeled(
    product_name: str, platform: str, geo: GeoResolution, months: int, hispanic_weighted: bool | None = None,
    flight_days: float | None = None, relation: str = "primary", link_note: str | None = None,
) -> AvailsEstimate | None:
    """`hispanic_weighted` is the product's effective config (Admin -> Forecast); None = the built-in default for the name.

    `platform` is a series key. The series is read from Postgres (forecast_store); when the database can't be reached,
    the original model workbook's bundled figures still answer for the seven legacy series, and any other series
    returns None (the caller falls back to the population-share estimate)."""
    series = forecast_store.series_by_key(platform)
    if series is not None and relation != "primary":
        series = series.with_link(relation, link_note or series.link_note)   # the "baseline for a targeted product" caveat
    if series is not None:
        try:
            return estimate_from_series(
                product_name, series, geo, months, hispanic_weighted=bool(hispanic_weighted), flight_days=flight_days)
        except Exception as exc:  # noqa: BLE001 - a read error mid-estimate must not become a 500
            log.warning("forecast series %s could not be read (%s: %s)", platform, type(exc).__name__, exc)
    if platform not in load_platform_dma():
        return None
    return _estimate_from_bundle(product_name, platform, geo, months, hispanic_weighted)


def _estimate_from_bundle(
    product_name: str, platform: str, geo: GeoResolution, months: int, hispanic_weighted: bool | None = None,
) -> AvailsEstimate:
    """The Phase 1/2 path over app/data/constanza/platform_dma.json (the offline fallback)."""
    figures_by_dma = load_platform_dma().get(platform, {})
    notes: list[str] = []

    use_hispanic_weights = hispanic_weighted if hispanic_weighted is not None else product_name in HISPANIC_TARGETED_PRODUCTS
    if use_hispanic_weights:
        notes.append(
            "The by-area share column is each area's share of the geography's Hispanic population (Census ACS), instead of its "
            "share of the general population. The impressions here are the platform's general ones; Hispanic targeting narrows "
            "them in the audience layer."
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


def _series_notes(series: "forecast_store.SeriesRef", weeks: float | None, modeled_areas: int, total_areas: int) -> list[str]:
    notes: list[str] = []
    basis = series.data_basis
    if basis == "extrapolated" and modeled_areas == 0:
        basis = "platform_reported"          # every market in THIS answer is one the platform reported: nothing was extended
    if basis == "platform_reported":
        when = f", exported {series.exported_on}" if series.exported_on else ""
        notes.append(f"Platform-reported avails ({series.title}, v{series.version}{when}). Rows are rounded by the platform; "
                     "a ZIP or district with no row has no avails at the export's reporting floor (about 180 a month).")
    elif basis == "extrapolated":
        notes.append(f"{series.title}: the platform reported a sample of markets and the rest are EXTENDED from the Entravision+ Connect "
                     f"market distribution ({modeled_areas} of {total_areas} resolved areas are extended; planning range is wider for those).")
    elif basis == "modeled":
        notes.append(f"Estimates from the Entravision model for this series ({series.title}, v{series.version}), not platform-reported avails.")
    if series.avails_unit == "weekly":
        window = f" for {series.period_label}" if series.period_label else ""
        weeks_txt = f" This flight is about {weeks:.1f} weeks; " if weeks else " "
        notes.append(f"Live-sports figures are WEEKLY estimates{window}.{weeks_txt}the flight dates are not checked against that window "
                     "(seasons repeat each year), so confirm the games fall inside the flight.")
    if series.relation == "baseline_proxy":
        notes.append(series.link_note or "This product uses another product's avails as its baseline; its own targeting delivers fewer impressions.")
    return notes


@dataclass(frozen=True)
class StateScale:
    """How a whole state's home markets are scaled up to the platform's own state row."""
    avails: float                      # makes the home markets' avails add up to the state's avails
    universe: float | None = None      # ... their reach universes up to the state's own (None: the state row has none)
    reach: float | None = None         # ... their est. reach up to the state's own
    unique_hh: float | None = None     # ... their unique households up to the state's own

    def people(self) -> float:
        """The factor for a count of PEOPLE or households (reach, universe, unique households) when its own ratio is missing. They are all
        "who is there / who is reached" counts and are not additive across markets, so whichever of them the state row does carry is the
        right scale for the others - the avails ratio is only the last resort (using it for one of them and a state figure for another
        could put the reached people above the people there are)."""
        for ratio in (self.reach, self.universe, self.unique_hh):
            if ratio is not None:
                return ratio
        return self.avails


def _state_scales(series: "forecast_store.SeriesRef", geo: GeoResolution, dmas: dict, factor: float) -> tuple[dict[str, StateScale], list[str]]:
    """For each state requested as a whole and covered by a state row of the series: the factor that makes its home-state
    markets add up to the platform's own state total. A state is more than the markets homed in it (Wyoming's own row is
    nearly twice what its two markets hold: the rest is served from Denver, Billings and Salt Lake City), so summing the
    markets alone would understate it. Returns ({state: factor}, notes); empty when the series has no state rows."""
    if not geo.whole_states or "state" not in series.levels:
        return {}, []
    try:
        rows = forecast_store.state_rows(series.series_id, geo.whole_states)
    except Exception:  # noqa: BLE001 - the market rows still answer
        return {}, []
    usable = [st for st in geo.whole_states if rows.get(st) is not None and rows[st].avails]
    if not usable:
        return {}, []
    # A state's total already holds the households inside it that are served from markets homed elsewhere. Scaling the home
    # markets up to it is sound only when the request is those states and nothing else: any other market, ZIP or county in the
    # request could overlap that part and count it twice, so nothing is scaled then.
    homed = set(geo.whole_states)
    outside = [a.label or a.dma or "?" for a in geo.areas if not (a.source_type == "dma" and a.state in homed)]
    if outside:
        shown = ", ".join(outside[:4]) + (f" and {len(outside) - 4} more" if len(outside) > 4 else "")
        return {}, [f"The platform's own state totals ({', '.join(usable)}) were not used because the request also names areas outside "
                    f"the whole states asked for ({shown}): a state total includes households served from other markets, so mixing it "
                    "with those areas could count some twice. Each market is counted as the platform reports it."]
    scale: dict[str, StateScale] = {}
    said: list[str] = []
    for st in geo.whole_states:
        row = rows.get(st)
        if row is None or not row.avails:
            continue
        home = home_universe = home_reach = home_hh = 0.0
        for a in geo.areas:
            if a.source_type == "dma" and a.state == st and a.dma:
                key = geo_reference.dma_key_for_label(a.dma)
                dma_row = dmas.get(key) if key else None
                if dma_row is not None:
                    home += dma_row.avails * a.dma_share
                    home_universe += (dma_row.reach_universe or 0.0) * a.dma_share
                    home_reach += (dma_row.est_reach or 0.0) * a.dma_share
                    home_hh += (dma_row.unique_hh or 0.0) * a.dma_share
        if home > 0:
            # People and reach are scaled to the STATE ROW'S OWN figures when it has them: they are not additive across markets (a household
            # served from Denver is in Denver's universe, not Wyoming's), so the avails ratio is the wrong factor for them.
            scale[st] = StateScale(
                avails=row.avails / home,
                universe=row.reach_universe / home_universe if row.reach_universe and home_universe > 0 else None,
                reach=row.est_reach / home_reach if row.est_reach and home_reach > 0 else None,
                unique_hh=row.unique_hh / home_hh if row.unique_hh and home_hh > 0 else None)
            if abs(scale[st].avails - 1.0) >= 0.02:
                said.append(f"{st}: the platform's own state total is {row.avails * factor:,.0f} avails against {home * factor:,.0f} avails in the markets "
                            f"homed there (x{scale[st].avails:.2f})")
    notes = []
    if said:
        notes.append("Whole-state requests use the platform's own state totals, which include the parts of a state served from markets "
                     "homed elsewhere, so each home-state market is scaled to match - " + "; ".join(said) + ".")
    return scale, notes


def estimate_from_series(
    product_name: str, series: "forecast_store.SeriesRef", geo: GeoResolution, months: int,
    hispanic_weighted: bool = False, flight_days: float | None = None,
) -> AvailsEstimate:
    """The estimate for a product whose avails live in a published series version.

    Per resolved area the most direct row wins: a ZIP area uses the series' own ZIP row, a congressional-district area
    its district row, a whole-DMA area its DMA row. Anything finer or odd (county, city) is its parent DMA's row times
    the area's population share, as before. Only the rows needed are read from the database."""
    sid = series.series_id
    weekly = series.avails_unit == "weekly"
    weeks = (flight_days / 7.0) if (weekly and flight_days) else (months * WEEKS_PER_MONTH if weekly else None)
    factor = weeks if weekly else float(months)

    zip_ids = [a.geo_id for a in geo.areas if a.source_type == "zip" and a.geo_id] if "zip" in series.levels else []
    cd_for = {id(a): geo_reference.cd_key_from_geoid(a.geo_id) for a in geo.areas
              if a.source_type == "congressional_district" and a.geo_id} if "cd" in series.levels else {}
    zips = forecast_store.zip_rows(sid, zip_ids) if zip_ids else {}
    cds = forecast_store.cd_rows(sid, [k for k in cd_for.values() if k]) if cd_for else {}
    dmas = forecast_store.dma_table(sid)

    state_scale, state_notes = _state_scales(series, geo, dmas, factor)
    weights = _area_weights(geo.areas, trait="hispanic" if hispanic_weighted else None)
    by_area: list[AreaEstimate] = []
    notes: list[str] = []
    imps_low = imps_mid = imps_high = 0.0
    uniques_total = 0.0
    uniques_known = False
    universe_total = 0.0
    universe_known = False
    no_row: list[str] = []
    modeled_areas = 0
    for area, weight in zip(geo.areas, weights):
        if area.dma is None:
            continue
        row = None
        share = 1.0
        if area.source_type == "zip" and area.geo_id and "zip" in series.levels:
            row = zips.get(area.geo_id)
            if row is None:
                no_row.append(f"ZIP {area.geo_id}")
        elif area.source_type == "congressional_district" and cd_for.get(id(area)):
            row = cds.get(cd_for[id(area)])
            if row is None:
                no_row.append(area.label or cd_for[id(area)])
        else:
            key = geo_reference.dma_key_for_label(area.dma)
            row = dmas.get(key) if key else None
            share = area.dma_share
            if row is None:
                no_row.append(area.dma)
        area_universe = None
        sscale = None
        if row is None:
            area_low = area_mid = area_high = 0.0
            area_uniques = area_hh = None
        else:
            if row.avails_low is not None and row.avails_high is not None:
                lo_mult = row.avails_low / row.avails if row.avails else series.band_low
                hi_mult = row.avails_high / row.avails if row.avails else series.band_high
            elif series.data_basis == "extrapolated" and row.basis == "modeled":
                lo_mult, hi_mult = EXTENDED_BAND
            else:
                lo_mult, hi_mult = series.band_low, series.band_high
            if row.basis == "modeled" and series.data_basis == "extrapolated":
                modeled_areas += 1
            # A market homed in a state the planner asked for whole is scaled so the markets add up to the platform's own
            # state total (only DMA-typed areas carry whole-state requests; a ZIP / county / city is never scaled).
            sscale = state_scale.get(area.state) if area.source_type == "dma" else None
            scale = sscale.avails if sscale else 1.0
            people = sscale.people() if sscale else 1.0
            reach_scale = sscale.reach if sscale and sscale.reach is not None else people
            universe_scale = sscale.universe if sscale and sscale.universe is not None else people
            hh_scale = sscale.unique_hh if sscale and sscale.unique_hh is not None else people
            area_mid = row.avails * factor * share * scale
            area_low, area_high = area_mid * lo_mult, area_mid * hi_mult
            # reach is a per-month concept, not cumulative (the model workbook never multiplied it by months either)
            area_uniques = row.est_reach * share * reach_scale if row.est_reach is not None else None
            area_hh = row.unique_hh * share * hh_scale if row.unique_hh is not None else None
            # people, like reach, are a per-period count: never multiplied by the months of the flight
            area_universe = row.reach_universe * share * universe_scale if row.reach_universe is not None else None
        imps_low += area_low
        imps_mid += area_mid
        imps_high += area_high
        if area_uniques is not None:
            uniques_total += area_uniques
            uniques_known = True
        if area_universe is not None:
            universe_total += area_universe
            universe_known = True
        by_area.append(AreaEstimate(
            area_label=area.label or area.dma, dma=area.dma, weight=weight,
            imps_low=area_low, imps_mid=area_mid, imps_high=area_high,
            unique_viewers=area_uniques, unique_hh=area_hh,
            area_population=area.area_population, source_type=area.source_type,
            apportionment=area.apportionment, state=area.state, geo_id=area.geo_id,
            reach_universe=area_universe, state_scaled=bool(sscale),
        ))

    notes.extend(_series_notes(series, weeks, modeled_areas, len(by_area)))
    notes.extend(state_notes)
    if no_row:
        shown = ", ".join(no_row[:6]) + (f" and {len(no_row) - 6} more" if len(no_row) > 6 else "")
        notes.append(f"No avails in this series for: {shown} (counted as zero).")
    if hispanic_weighted:
        notes.append("The by-area share column is each area's share of the geography's Hispanic population (Census ACS); the impressions here "
                     "are the series' own, and Hispanic targeting narrows them in the audience layer.")
    if geo.method == "national" and "dma" in series.levels:
        try:
            unplaced = forecast_store.unknown_share(sid).get("dma")
        except Exception:  # noqa: BLE001
            unplaced = None
        if unplaced and imps_mid:
            notes.append(f"A national total excludes avails the platform could not place in a market (about {unplaced / (unplaced + imps_mid / factor):.1%}).")
    return AvailsEstimate(
        product_name=product_name, platform=series.series_key, confidence="platform_modeled",
        geo_method=geo.method, geo_confidence=geo.geo_confidence, months=months,
        imps_low=imps_low, imps_mid=imps_mid, imps_high=imps_high,
        est_uniques=uniques_total if uniques_known else None, by_area=by_area, notes=notes,
        series=series.to_public(), weeks=weeks, reach_universe=universe_total if universe_known else None,
    )


def estimate_generic_population(
    product_name: str, geo: GeoResolution, months: int, national_monthly_total: float,
    platform_modeled: bool | None = None,
) -> AvailsEstimate:
    """Fallback for products with no workbook-modeled platform (VIX 360,
    Netflix, or any product whose forecast config isn't "platform"), and the
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
    is_international = geo.geo_confidence == "low_confidence_international"
    if platform_modeled is None:
        platform_modeled = product_name in PLATFORM_MODELED_PRODUCTS
    if is_international and platform_modeled:
        # A platform-modeled product (Roku, Prime, ...) whose bundled data is US-only —
        # the generic wording below ("no platform is modeled for this product") would be wrong.
        notes = [
            "This product's platform data covers U.S. DMAs only — outside the U.S. this spreads your "
            "own entered monthly total across the selected countries by population share. "
            "Not validated against real platform delivery."
        ]
    else:
        notes = [
            "No CTV platform is separately modeled for this product (it's bought as a custom "
            "Trade-Desk site-list/PMP) — this spreads your own entered monthly total across the "
            "resolved geo by population share only. Not validated against real platform delivery."
        ]
    if is_international:
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


def _per_period(est: AvailsEstimate) -> float:
    """How many reporting periods the estimate's impressions span: the flight's weeks for a weekly series (live sports),
    else its months. Reach and unique counts are per ONE of these periods, never summed over the flight."""
    return float(est.weeks) if est.weeks else float(max(1, est.months))


_CAPPED_NOTE = ("In {n} area{s} the platform's reach is larger than its avails (it reports small areas at a floor), so unique IDs "
                "there were capped at the impressions — an ID needs at least one.")
_BASIS_WORD = {"platform_reported": "platform-reported", "extrapolated": "extended", "modeled": "modeled"}


def apply_win_rate(est: AvailsEstimate, wr: "constanza_win_rate.WinRate") -> AvailsEstimate:
    """The estimate for a product once only `wr.mid` of its avails are won (MadHive / Trade Desk bid opportunities at the
    programmatic rate; Prime Video's ad volume at its own single rate, which has no range).

    Impressions per area (low / mid / high) are multiplied by the MID win rate (the planning band keeps its meaning); the
    LOW..HIGH win-rate range is reported separately as what the delivery would be at each (`win_rate.delivered_at_low/high`).
    Unique IDs per area are re-derived from the opportunities each reached ID has (constanza_win_rate.reached_ids), so they stay
    at or below the impressions; the reach universe is untouched; the estimate's totals are re-summed from the areas. The raw
    platform figures are kept in `win_rate` so the card can show the avails the platform reported next to the expected delivery."""
    period = _per_period(est)
    raw = {"imps_low": est.imps_low, "imps_mid": est.imps_mid, "imps_high": est.imps_high, "est_uniques": est.est_uniques}
    areas: list[AreaEstimate] = []
    capped = 0
    for a in est.by_area:
        low, mid, high = constanza_win_rate.scale(a.imps_low, a.imps_mid, a.imps_high, wr)
        per_period_raw = a.imps_mid / period
        if a.unique_viewers is not None and a.unique_viewers > per_period_raw * (1 + 1e-9):
            capped += 1                                  # the platform's reach is above its own avails here (a floor row)
        reached = constanza_win_rate.reached_ids(a.unique_viewers, per_period_raw, wr.mid)
        if a.unique_hh is None:
            hh = None
        elif a.unique_viewers and reached is not None:
            hh = a.unique_hh * (reached / a.unique_viewers)         # households follow the IDs
        else:
            hh = min(a.unique_hh, per_period_raw * wr.mid)
        areas.append(replace(a, imps_low=low, imps_mid=mid, imps_high=high, unique_viewers=reached, unique_hh=hh))
    uniques = [a.unique_viewers for a in areas if a.unique_viewers is not None]
    delivered = sum(a.imps_mid for a in areas)
    unit = "week" if est.weeks else "month"
    basis = _BASIS_WORD.get((est.series or {}).get("data_basis"), "modeled")       # the bundled workbook (no series) is modeled
    pct = constanza_win_rate.pct_text
    per_mid = delivered / period
    raw_mid = est.imps_mid / period
    if wr.low == wr.high:
        # one rate, no range (Prime Video through the Amazon DSP, or an environment that sets the three equal)
        note = (f"Win rate applied: Entravision is assumed to win {pct(wr.mid)} of these {basis} avails, so the expected delivery is "
                f"{per_mid:,.0f} impressions a {unit} against the {raw_mid:,.0f} avails.")
    else:
        note = (
            f"Win rate applied: these {basis} avails are bid opportunities, and Entravision wins about {pct(wr.mid)} of the auctions it "
            f"bids on ({pct(wr.low)}–{pct(wr.high)}), so the expected delivery is {per_mid:,.0f} impressions a {unit} against the "
            f"{raw_mid:,.0f} avails (at {pct(wr.low)}: {est.imps_mid * wr.low / period:,.0f}; at {pct(wr.high)}: {est.imps_mid * wr.high / period:,.0f})."
        )
    if uniques:
        note += " Unique IDs were re-derived for that smaller delivery (never above the impressions)."
    notes = [note, *est.notes]
    if capped:
        notes.append(_CAPPED_NOTE.format(n=capped, s="s" if capped != 1 else ""))
    return replace(
        est, by_area=areas,
        imps_low=sum(a.imps_low for a in areas), imps_mid=delivered, imps_high=sum(a.imps_high for a in areas),
        est_uniques=sum(uniques) if uniques else None,
        notes=notes,
        win_rate={**wr.to_public(), "raw_imps_low": raw["imps_low"], "raw_imps_mid": raw["imps_mid"], "raw_imps_high": raw["imps_high"],
                  "raw_est_uniques": raw["est_uniques"],
                  "delivered_at_low": raw["imps_mid"] * wr.low, "delivered_at_high": raw["imps_mid"] * wr.high},
    )


def _cap_uniques_at_impressions(est: AvailsEstimate) -> AvailsEstimate:
    """No estimate may show more unique IDs than impressions — each reached ID needs at least one impression, and a ZIP the
    platform reports at its floor could carry a reach larger than its avails (none of the shipped exports does today, so this is a
    guard). Caps per area; says so when it bound."""
    period = _per_period(est)
    capped = 0
    areas: list[AreaEstimate] = []
    for a in est.by_area:
        limit = a.imps_mid / period
        if a.unique_viewers is not None and a.unique_viewers > limit * (1 + 1e-9):
            capped += 1
            ratio = (limit / a.unique_viewers) if a.unique_viewers else 0.0
            areas.append(replace(a, unique_viewers=limit, unique_hh=(a.unique_hh * ratio if a.unique_hh is not None else None)))
        else:
            areas.append(a)
    if not capped:
        return est
    uniques = [a.unique_viewers for a in areas if a.unique_viewers is not None]
    return replace(est, by_area=areas, est_uniques=sum(uniques) if uniques else None,
                   notes=[*est.notes, _CAPPED_NOTE.format(n=capped, s="s" if capped != 1 else "")])


def estimate_avails(
    product_name: str, geo: GeoResolution, months: int = 1,
    national_monthly_total: float | None = None,
    config: ForecastConfig | None = None,
    flight_days: float | None = None,
) -> AvailsEstimate | None:
    """Entry point: routes to the platform-modeled or generic-fallback
    estimator by the product's forecast config — its Admin -> Forecast row if
    it has one, else the default for its name (see
    constanza_forecast_map.resolve_forecast); `config` lets a caller that has
    already resolved it pass it in. `flight_days` (optional) lets a weekly
    series (live sports) use the line's real length instead of months x 4.35.
    Returns None only when the generic fallback would apply but no
    `national_monthly_total` was given (nothing to apportion). A country-level
    (non-US) resolution always routes to the generic fallback — no platform
    ever has DMA-style data outside the US."""
    cfg = config or resolve_forecast(product_name)
    platform = cfg.platform if cfg.method == "platform" else None
    is_international = geo.geo_confidence == "low_confidence_international"
    if platform and not is_international:
        est = estimate_platform_modeled(
            product_name, platform, geo, months, hispanic_weighted=cfg.hispanic_weighted, flight_days=flight_days,
            relation=cfg.relation, link_note=cfg.link_note)
        if est is not None:
            if cfg.win_rate:
                est = apply_win_rate(est, constanza_win_rate.for_series(platform))
            return _cap_uniques_at_impressions(est)
    if national_monthly_total is None:
        return None
    return estimate_generic_population(product_name, geo, months, national_monthly_total, platform_modeled=bool(platform))
