"""Add the forecast seed products (app/data/forecast/products.json) to a products table that was seeded before they existed.

The products table is seeded ONCE from catalog.CATALOG, so a new SKU in code never reaches a database that already has
its rows. This inserts the missing ones by name; an existing row - including one an admin edited or soft-deleted - is never
touched, so re-running it is always safe.

One thing more, once: a seed product may say it `replaces` an older product (eDigital Display and OLV replace the earlier
display and pre-roll products). At the moment such a product is INSERTED the older row is hidden (as an admin's delete does:
a saved proposal that names it still resolves it) and its category approvals are copied to the new product. Because it only
happens on the insert, an admin who later brings the older product back is not overruled by the next start.
"""
from __future__ import annotations

import logging

from app import catalog

log = logging.getLogger(__name__)


def apply_to_db(conn) -> int:
    """Insert each seed product that has no row yet; returns how many were inserted."""
    existing = {r["name"] for r in conn.execute("SELECT name FROM products").fetchall()}
    if not existing:
        # An empty table is filled, once, from the whole CATALOG (which already contains these products). Seeding only these
        # 30 here would make the table non-empty and the base catalog would never load - so ask the catalog to do its own
        # seeding now, and the series imported right after this call find the products they link to (on a fresh database
        # nothing else would have touched the table yet).
        catalog.invalidate_product_caches()
        catalog.load_product_rows()
        existing = {r["name"] for r in conn.execute("SELECT name FROM products").fetchall()}
        if not existing:
            return 0                      # it could not be seeded (a blip); the next start tries again
    columns = ", ".join(catalog._PRODUCT_COLUMNS)
    placeholders = ", ".join(["%s"] * (len(catalog._PRODUCT_COLUMNS) + 2))
    replacements = catalog.forecast_seed_replacements()
    inserted = 0
    replaced: list[tuple[str, str]] = []
    for product, after in catalog._load_forecast_seed_products():
        if product.name in existing:
            continue
        anchor = conn.execute("SELECT sort_order FROM products WHERE name = %s", (after,)).fetchone() if after else None
        if anchor is not None:
            order = anchor["sort_order"] + 1
            conn.execute("UPDATE products SET sort_order = sort_order + 1 WHERE sort_order >= %s", (order,))
        else:
            order = conn.execute("SELECT COALESCE(MAX(sort_order), -1) + 1 AS n FROM products").fetchone()["n"]
        conn.execute(
            f"INSERT INTO products (sort_order, is_deleted, {columns}) VALUES ({placeholders}) ON CONFLICT (name) DO NOTHING",
            [order, False] + catalog._product_params(product))
        existing.add(product.name)
        inserted += 1
        older = replacements.get(product.name)
        if older and conn.execute("UPDATE products SET is_deleted = TRUE, updated_at = now() WHERE name = %s AND NOT is_deleted "
                                  "RETURNING id", (older,)).fetchone():
            replaced.append((older, product.name))
    if inserted:
        catalog.invalidate_product_caches()
    for older, newer in replaced:
        log.info("forecast seed: %r replaces %r (hidden; saved proposals still resolve it)", newer, older)
        _carry_over_approvals(older, newer)
    return inserted


def _carry_over_approvals(older: str, newer: str) -> None:
    """The new product is approved wherever the older one was (the category approvals are keyed by product NAME). Best effort: a
    database without the restrictions tables simply has nothing to carry over."""
    try:
        from app.services import restrictions
        copied = restrictions.inherit_product_approvals(older, newer, "forecast seed")
        if copied:
            log.info("forecast seed: %r inherited %d category approval(s) from %r", newer, copied, older)
    except Exception:  # noqa: BLE001 - the product swap itself is done; approvals can be mapped in Admin
        log.warning("forecast seed: could not copy the category approvals of %r to %r", older, newer, exc_info=True)
