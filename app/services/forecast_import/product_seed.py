"""Add the forecast seed products (app/data/forecast/products.json) to a products table that was seeded before they existed.

The products table is seeded ONCE from catalog.CATALOG, so a new SKU in code never reaches a database that already has
its rows. This inserts the missing ones by name and nothing else: an existing row - including one an admin edited or
soft-deleted - is never touched, so re-running it is always safe.
"""
from __future__ import annotations

from app import catalog


def apply_to_db(conn) -> int:
    """Insert each seed product that has no row yet; returns how many were inserted."""
    existing = {r["name"] for r in conn.execute("SELECT name FROM products").fetchall()}
    if not existing:
        # An empty table is filled, once, from the whole CATALOG (which already contains these products) on first
        # use. Seeding only these 30 here would make the table non-empty and the base catalog would never load.
        return 0
    columns = ", ".join(catalog._PRODUCT_COLUMNS)
    placeholders = ", ".join(["%s"] * (len(catalog._PRODUCT_COLUMNS) + 2))
    inserted = 0
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
    if inserted:
        catalog.invalidate_product_caches()
    return inserted
