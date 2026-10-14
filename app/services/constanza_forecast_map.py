"""
Which catalog products show Constanza's "Auto-calculate avails" button on the Avails step (Step 05), and what that
button computes — the mapping an admin sees and edits in Admin -> Forecast.

Two layers, so nothing changes until an admin chooses to change it:

  DEFAULT (a product with no row in `forecast_product_map`) — exactly what Phase 1 hard-coded:
      * enabled   : the product is in the "Entravision Plus" family
      * method    : "platform" for the ten products in DEFAULT_PLATFORM_PRODUCTS (the Roku / Prime / Hulu / Peacock /
                    Other-FAST / vMVPD pools and the four blended Madhive CTV/OTT products), else "generic" (the planner's
                    own budget apportioned by population share — VIX 360, Netflix, OLV Blend, ...)
      * hispanic  : DEFAULT_HISPANIC_PRODUCTS (re-weight by state Hispanic population %)
  OVERRIDE (a row) — the admin's explicit choice for that one product. Rows are keyed by the product's NAME and looked up
      through catalog.name_variants, so renaming a product (or splitting it) never orphans its row — the same convention as
      the Restricted Verticals mapping.

Reading is fail-open: if the table can't be read (no database, not created yet) every product simply keeps its default,
which is how the app behaved before this existed. The table is created at startup by schema_bootstrap from schema.sql.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Iterable, Optional

from app import catalog
from app.data.constanza import load_platform_dma
from app.db import get_connection
from app.services import forecast_store

log = logging.getLogger(__name__)

EP_FAMILY = "Entravision Plus"

# The platform data series bundled in app/data/constanza/platform_dma.json, as an admin should read them. Order is
# display order; a series present in the data but missing here is still offered (labelled by its key).
PLATFORM_LABELS: dict[str, str] = {
    "roku": "Roku Channel",
    "prime": "Amazon Prime Video",
    "hulu": "Hulu",
    "peacock": "Peacock",
    "other_fast": "Other FAST (Tubi, Pluto TV, Samsung TV Plus, …)",
    "vmvpd": "vMVPD (Fubo, Sling, DIRECTV Stream, …)",
    "cumulative": "Blended CTV/OTT (Madhive cumulative)",
}

# Catalog product name -> workbook platform key. Only products whose inventory pool is actually one of the workbook's
# modeled platforms are listed here; everything else (VIX 360, Netflix, and any product not present at all) falls back
# to the generic population-based estimate. See Product Constanza's plan doc for the buying-stack rationale (Roku Ads =
# Roku's self-serve platform, Prime Video = Amazon DSP, the generic CTV/OTT products = Madhive's blended list — all still
# describable by this workbook's per-DMA figures regardless of who executes the buy).
DEFAULT_PLATFORM_PRODUCTS: dict[str, str] = {
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

# Products whose targeting is Hispanic-specific — re-weight area shares by state-level Hispanic population % (a proxy;
# the workbook has no DMA-level demographic breakout) instead of general population share.
DEFAULT_HISPANIC_PRODUCTS = frozenset({"Entravision Plus - Hispanics CTV/OTT"})

METHODS = ("platform", "generic")


@dataclass(frozen=True)
class ForecastConfig:
    enabled: bool                  # does the avails card show the button
    method: str                    # "platform" | "generic"
    platform: Optional[str]        # a platform key when method == "platform"
    hispanic_weighted: bool        # only meaningful for method == "platform"
    source: str                    # "default" | "admin"
    relation: str = "primary"      # "baseline_proxy": the series is another product's avails used as this one's baseline
    link_note: Optional[str] = None  # the sentence stored with that link ("this targeted variant will deliver fewer impressions")

    def to_public(self) -> dict:
        return {"enabled": self.enabled, "method": self.method, "platform": self.platform,
                "hispanic_weighted": self.hispanic_weighted, "source": self.source, "relation": self.relation}

    def same_behavior(self, other: "ForecastConfig") -> bool:
        return (self.enabled, self.method, self.platform, self.hispanic_weighted) == \
               (other.enabled, other.method, other.platform, other.hispanic_weighted)


# ---------------------------------------------------------------------------
# Platform data
# ---------------------------------------------------------------------------

def platform_keys() -> list[str]:
    """Every data series a product can be forecast from: the bundled model workbook's platforms (display order first),
    then every current series in the database (ordered by channel, then key)."""
    try:
        present = list(load_platform_dma().keys())
    except Exception:  # noqa: BLE001 — a missing bundle must not take the admin page down
        present = []
    ordered = [k for k in PLATFORM_LABELS if k in present]
    ordered += [k for k in present if k not in PLATFORM_LABELS]
    try:
        db = sorted(forecast_store.current_series().values(), key=lambda r: (r.channel, r.series_key))
    except Exception:  # noqa: BLE001
        db = []
    return ordered + [r.series_key for r in db if r.series_key not in ordered]


def platform_label(key: str) -> str:
    if key in PLATFORM_LABELS:
        return PLATFORM_LABELS[key]
    ref = forecast_store.series_by_key(key)
    return ref.title if ref else key


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

def _family_of(product_name: str) -> Optional[str]:
    try:
        product = catalog.by_name(product_name)
    except Exception:  # noqa: BLE001
        product = None
    return product.family if product else None


def _name_variants(product_name: str) -> set[str]:
    try:
        return catalog.name_variants(product_name)
    except Exception:  # noqa: BLE001
        return {product_name}


def default_config(product_name: str, family: Optional[str] = None, variants: Optional[set[str]] = None) -> ForecastConfig:
    """What a product does with no admin row. A product linked to a series in the database (forecast_series_product) is
    enabled and forecast from that series; otherwise Phase 1's behavior, found through every name the product has
    answered to (so a renamed 'Entravision Plus - Hulu' keeps its platform)."""
    variants = variants if variants is not None else _name_variants(product_name)
    platform = DEFAULT_PLATFORM_PRODUCTS.get(product_name)
    if platform is None:
        platform = next((DEFAULT_PLATFORM_PRODUCTS[v] for v in sorted(variants) if v in DEFAULT_PLATFORM_PRODUCTS), None)
    hispanic = bool(product_name in DEFAULT_HISPANIC_PRODUCTS or variants & DEFAULT_HISPANIC_PRODUCTS)
    linked = forecast_store.series_for_product([product_name] + sorted(variants - {product_name}))
    if linked is not None:
        return ForecastConfig(enabled=True, method="platform", platform=linked.series_key,
                              hispanic_weighted=hispanic and linked.series_key == platform, source="default", relation=linked.relation,
                              link_note=linked.link_note)
    if family is None:
        family = _family_of(product_name)
    return ForecastConfig(
        enabled=(family == EP_FAMILY),
        method="platform" if platform else "generic",
        platform=platform,
        hispanic_weighted=hispanic and bool(platform),
        source="default",
    )


# ---------------------------------------------------------------------------
# Overrides (the table) — cached, fail-open
# ---------------------------------------------------------------------------

_CACHE_TTL = 30.0              # seconds a read is trusted; every write below invalidates at once, so this is only a backstop
_RETRY_AFTER_FAILURE = 15.0    # after a failed read, don't hit the database again for a moment (a missing table, no DB)
_lock = threading.Lock()
_cache: dict[str, Any] = {"rows": None, "at": 0.0, "retry_at": 0.0}


def invalidate() -> None:
    with _lock:
        _cache.update(rows=None, at=0.0, retry_at=0.0)


def _read_rows() -> Optional[list[dict]]:
    """Every override row, or None when the table can't be read."""
    try:
        with get_connection() as conn:
            return list(conn.execute(
                "SELECT product_name, enabled, method, platform, hispanic_weighted, updated_at, updated_by "
                "FROM forecast_product_map ORDER BY product_name"
            ).fetchall())
    except Exception as exc:  # noqa: BLE001 — a missing table / unreachable DB must never break a proposal
        log.warning("forecast_product_map unavailable, every product keeps its default: %s", exc)
        return None


def _valid_row(row: dict) -> bool:
    if row.get("method") not in METHODS:
        return False
    if row["method"] == "platform":
        return bool(row.get("platform")) and row["platform"] in platform_keys()
    return True


def load_overrides() -> dict[str, dict]:
    """{product name: row} of the explicit admin choices. {} when there are none or the table can't be read. A row that
    names a platform the data no longer has is ignored (with a warning) rather than breaking the estimate."""
    now = time.monotonic()
    with _lock:
        rows, at, retry_at = _cache["rows"], _cache["at"], _cache["retry_at"]
    if rows is not None and now - at < _CACHE_TTL:
        return rows
    if now < retry_at:                    # a refresh failed a moment ago: keep serving what we have, don't hit the DB again
        return rows or {}
    fresh = _read_rows()
    with _lock:
        if fresh is None:
            _cache.update(retry_at=now + _RETRY_AFTER_FAILURE)
            return _cache["rows"] or {}
        out: dict[str, dict] = {}
        for row in fresh:
            if _valid_row(row):
                out[row["product_name"]] = row
            else:
                log.warning("ignoring invalid forecast mapping for %r: %r", row.get("product_name"), row)
        _cache.update(rows=out, at=now, retry_at=0.0)
        return out


def resolve_forecast(product_name: str, family: Optional[str] = None, *, overrides: Optional[dict] = None) -> ForecastConfig:
    """The effective config of one product: its admin row when it has one, else its default."""
    overrides = load_overrides() if overrides is None else overrides
    variants = _name_variants(product_name)
    row = overrides.get(product_name)
    if row is None:
        row = next((overrides[v] for v in sorted(variants) if v in overrides), None)
    if row is not None:
        platform = row.get("platform") if row["method"] == "platform" else None
        base = default_config(product_name, family, variants)
        return ForecastConfig(
            enabled=bool(row["enabled"]), method=row["method"], platform=platform,
            hispanic_weighted=bool(row.get("hispanic_weighted")) and row["method"] == "platform", source="admin",
            relation=base.relation if platform and platform == base.platform else "primary",
            link_note=base.link_note if platform and platform == base.platform else None,
        )
    return default_config(product_name, family, variants)


def forecast_for_products(products: Iterable) -> dict[str, dict]:
    """{product name: public config} for the catalog payload — one table read for the whole list. A platform-modeled
    product also carries its series' public metadata (data basis, unit, version and, for live sports, the period)."""
    overrides = load_overrides()
    out: dict[str, dict] = {}
    for p in products:
        cfg = resolve_forecast(p.name, p.family, overrides=overrides)
        public = cfg.to_public()
        ref = forecast_store.series_by_key(cfg.platform) if cfg.method == "platform" and cfg.platform else None
        if ref is not None:
            # the product's own link to the series: whether the series is its baseline proxy, and the stored caveat for that
            public["series"] = ref.with_link(cfg.relation, cfg.link_note).to_public()
        out[p.name] = public
    return out


def live_platform_products() -> dict[str, str]:
    """{product name: platform key} for every catalog product whose effective config is platform-modeled — the live
    counterpart of DEFAULT_PLATFORM_PRODUCTS."""
    out: dict[str, str] = {}
    overrides = load_overrides()
    for p in catalog.effective_catalog():
        cfg = resolve_forecast(p.name, p.family, overrides=overrides)
        if cfg.method == "platform" and cfg.platform:
            out[p.name] = cfg.platform
    return out


# ---------------------------------------------------------------------------
# Admin edits
# ---------------------------------------------------------------------------

def clean_config(payload: dict) -> dict:
    """An admin submission -> {enabled, method, platform, hispanic_weighted}. ValueError carries a planner-readable
    message. A generic estimate has no platform and no Hispanic weighting, so those are cleared rather than kept."""
    method = payload.get("method")
    if method not in METHODS:
        raise ValueError("Choose what the button computes: platform data, or a population-share estimate.")
    platform = (payload.get("platform") or "").strip() or None
    hispanic = bool(payload.get("hispanic_weighted"))
    if method == "platform":
        if not platform:
            raise ValueError("Choose which platform's data this product uses.")
        if platform not in platform_keys():
            raise ValueError(f"'{platform}' isn't one of the bundled platform data series.")
    else:
        platform, hispanic = None, False
    return {"enabled": bool(payload.get("enabled")), "method": method, "platform": platform, "hispanic_weighted": hispanic}


def _write(name: str, cfg: dict, updated_by: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO forecast_product_map (product_name, enabled, method, platform, hispanic_weighted, updated_at, updated_by) "
            "VALUES (%s, %s, %s, %s, %s, now(), %s) "
            "ON CONFLICT (product_name) DO UPDATE SET enabled = EXCLUDED.enabled, method = EXCLUDED.method, "
            "platform = EXCLUDED.platform, hispanic_weighted = EXCLUDED.hispanic_weighted, updated_at = now(), "
            "updated_by = EXCLUDED.updated_by",
            (name, cfg["enabled"], cfg["method"], cfg["platform"], cfg["hispanic_weighted"], updated_by),
        )


def _delete(names: Iterable[str]) -> None:
    names = list(names)
    if not names:
        return
    with get_connection() as conn:
        conn.execute("DELETE FROM forecast_product_map WHERE product_name = ANY(%s)", (names,))


def set_override(product_name: str, payload: dict, updated_by: str) -> ForecastConfig:
    """Save an admin's choice for one product (stored under its CURRENT name; a row left over from a former name is
    removed so the product never has two). LookupError for a product that isn't in the catalog."""
    product = catalog.by_name(product_name)
    if product is None:
        raise LookupError(f"'{product_name}' isn't in the product catalog.")
    cfg = clean_config(payload)
    stale = [v for v in _name_variants(product.name) if v != product.name]
    try:
        _write(product.name, cfg, updated_by)
        _delete(stale)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Couldn't save the forecast mapping: {exc}") from exc
    finally:
        invalidate()
    return resolve_forecast(product.name, product.family)


def clear_override(product_name: str) -> ForecastConfig:
    """Put a product back on its default."""
    product = catalog.by_name(product_name)
    if product is None:
        raise LookupError(f"'{product_name}' isn't in the product catalog.")
    try:
        _delete(_name_variants(product.name) | {product_name})
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Couldn't reset the forecast mapping: {exc}") from exc
    finally:
        invalidate()
    return resolve_forecast(product.name, product.family)


def inherit_config(source_name: str, new_name: str, updated_by: str) -> bool:
    """A duplicated product does what its original does: if the copy's own default would differ (a copy of the Roku
    product has a new name, so by default it would be a generic estimate), the original's effective config is saved on
    the copy. True when a row was written."""
    source = resolve_forecast(source_name)
    copy = catalog.by_name(new_name)
    if copy is None:
        return False
    if source.same_behavior(resolve_forecast(new_name, copy.family)):
        return False
    _write(copy.name, {"enabled": source.enabled, "method": source.method, "platform": source.platform,
                       "hispanic_weighted": source.hispanic_weighted}, updated_by)
    invalidate()
    return True


# ---------------------------------------------------------------------------
# Platform products that exist in the code seed but may not be in a database already seeded
# ---------------------------------------------------------------------------

def _seed_product(name: str):
    return next((p for p in catalog.CATALOG if p.name == name), None)


def missing_platform_products() -> dict[str, dict]:
    """{platform key: seed product info} for each platform whose default catalog product (a SKU added to the code seed
    for it) isn't in the live catalog. The product list is a table seeded ONCE, so a SKU added to the seed later never
    reaches an existing database by itself — Admin -> Forecast offers to add it."""
    out: dict[str, dict] = {}
    for name, platform in DEFAULT_PLATFORM_PRODUCTS.items():
        seed = _seed_product(name)
        if seed is None or catalog.by_name(name) is not None:
            continue
        out.setdefault(platform, {"name": seed.name, "buying_model": seed.buying_model, "base_rate": seed.base_rate,
                                  "minimum_spend": seed.minimum_spend, "notes": seed.notes})
    return out


def add_missing_platform_product(product_name: str) -> dict:
    """Add one of the missing platform SKUs (see above) to the product table. ValueError when the name isn't one."""
    if product_name not in DEFAULT_PLATFORM_PRODUCTS or _seed_product(product_name) is None:
        raise ValueError("That isn't one of the built-in forecast platform products.")
    if catalog.by_name(product_name) is not None:
        raise ValueError(f"'{product_name}' is already in the product catalog.")
    return catalog.create_product(catalog.product_to_form(_seed_product(product_name)))


# ---------------------------------------------------------------------------
# The admin page's payload
# ---------------------------------------------------------------------------

def admin_payload() -> dict:
    """Every live catalog product with its effective forecast config, the platform coverage, and the totals."""
    overrides = load_overrides()
    products = catalog.effective_catalog()
    rows = []
    for p in products:
        variants = _name_variants(p.name)
        cfg = resolve_forecast(p.name, p.family, overrides=overrides)
        default = default_config(p.name, p.family, variants)
        rows.append({
            "name": p.name, "family": p.family, "buying_model": p.buying_model, "base_rate": p.base_rate,
            "enabled": cfg.enabled, "method": cfg.method, "platform": cfg.platform, "hispanic_weighted": cfg.hispanic_weighted,
            "customized": cfg.source == "admin",
            "default": default.to_public(),
        })
    missing = missing_platform_products()
    coverage = []
    try:
        data = load_platform_dma()
    except Exception:  # noqa: BLE001
        data = {}
    platforms = []
    for key in platform_keys():
        using = [r["name"] for r in rows if r["enabled"] and r["method"] == "platform" and r["platform"] == key]
        parked = [r["name"] for r in rows if not r["enabled"] and r["method"] == "platform" and r["platform"] == key]
        ref = forecast_store.series_by_key(key)
        entry = {
            "key": key, "label": platform_label(key), "dmas": (ref.row_counts.get("dma") if ref else None) or len(data.get(key, {})),
            "products": using, "disabled_products": parked, "missing_product": missing.get(key),
        }
        pub = {"key": key, "label": platform_label(key)}
        if ref is not None:
            info = ref.to_public()
            entry["series"] = info
            entry["row_counts"] = ref.row_counts
            pub.update(channel=ref.channel, unit=ref.avails_unit, data_basis=ref.data_basis, period_label=ref.period_label)
        coverage.append(entry)
        platforms.append(pub)
    enabled = [r for r in rows if r["enabled"]]
    return {
        "products": rows,
        "families": sorted({r["family"] for r in rows}),
        "platforms": platforms,
        "coverage": coverage,
        "summary": {
            "total": len(rows), "enabled": len(enabled),
            "platform_modeled": sum(1 for r in enabled if r["method"] == "platform"),
            "generic": sum(1 for r in enabled if r["method"] == "generic"),
            "customized": sum(1 for r in rows if r["customized"]),
        },
    }
