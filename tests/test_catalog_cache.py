"""
The short-TTL read-through cache in app/catalog.py. It exists because effective_catalog()/by_name()/by_family()
are called constantly (per product, per recommended tactic, per line item): reading the product table each time
would be hundreds of database round trips for one "Suggest mix" click. These tests count reads against a stubbed
reader — never a real DATABASE_URL.
"""
from __future__ import annotations

import pytest

from app import catalog


def test_ttl_cache_serves_repeated_gets_from_cache():
    calls = []
    cache = catalog._TTLCache(ttl_seconds=60)
    loader = lambda: (calls.append(1), {"x": 1})[1]

    assert cache.get(loader) == {"x": 1}
    assert cache.get(loader) == {"x": 1}
    assert cache.get(loader) == {"x": 1}
    assert len(calls) == 1  # only the FIRST get() actually called the loader


def test_ttl_cache_invalidate_forces_a_fresh_load():
    calls = []
    cache = catalog._TTLCache(ttl_seconds=60)
    loader = lambda: (calls.append(1), {"x": len(calls)})[1]

    assert cache.get(loader) == {"x": 1}
    cache.invalidate()
    assert cache.get(loader) == {"x": 2}
    assert len(calls) == 2


def test_ttl_cache_expires_after_ttl(monkeypatch):
    calls = []
    cache = catalog._TTLCache(ttl_seconds=10)
    loader = lambda: (calls.append(1), "v")[1]

    fake_now = [1000.0]
    monkeypatch.setattr(catalog.time, "monotonic", lambda: fake_now[0])
    cache.get(loader)
    fake_now[0] += 5  # within TTL
    cache.get(loader)
    assert len(calls) == 1
    fake_now[0] += 11  # now past the 10s TTL
    cache.get(loader)
    assert len(calls) == 2


def _row(name, family="Display", deleted=False, pid=1):
    p = catalog.Product(
        family=family, name=name, short_label="x", proposal_description="", sizes="", buying_model="CPM", base_rate=1.0,
        estimated_impressions=False, discloses_impressions=True, minimum_spend=0.0, minimum_flight_days=(7, 90),
        sla_data_days=None, sla_creative_days=None, sla_activate_days=None, sla_total_days=None,
        media_allocation_pct=0.0, margin_upper=0.5, margin_lower=0.3, tech_platform="", wide_orbit_code=name)
    return {"id": pid, "is_deleted": deleted, "product": p}


def test_the_product_table_is_read_once_across_repeated_lookups(monkeypatch):
    reads = []
    monkeypatch.setattr(catalog, "_read_product_rows", lambda: (reads.append(1), [_row("A")])[1])
    catalog.invalidate_product_caches()

    for _ in range(5):
        catalog.effective_catalog()
        catalog.by_name("A")
        catalog.by_family("Display")
    assert len(reads) == 1


def test_a_write_invalidates_so_the_next_read_is_fresh(monkeypatch):
    reads = []
    monkeypatch.setattr(catalog, "_read_product_rows", lambda: (reads.append(1), [_row("A")])[1])
    catalog.invalidate_product_caches()
    catalog.effective_catalog()
    catalog.invalidate_product_caches()          # what create/update/delete call when they finish
    catalog.effective_catalog()
    assert len(reads) == 2


def test_an_unreadable_table_falls_back_to_the_seed_list_and_is_not_retried_for_a_few_seconds(monkeypatch):
    reads = []
    monkeypatch.setattr(catalog, "_read_product_rows", lambda: (reads.append(1), None)[1])
    catalog.invalidate_product_caches()

    first = catalog.effective_catalog()
    for _ in range(4):
        catalog.effective_catalog()
    assert len(reads) == 1                                  # one failed read, then the seed list for a moment
    assert {p.name for p in first} >= {"Search - SEM", "YouTube Ads - Skippable"}   # the fallback is the real seed list

    fake_now = [catalog.time.monotonic() + 16]
    monkeypatch.setattr(catalog.time, "monotonic", lambda: fake_now[0])
    catalog.effective_catalog()
    assert len(reads) == 2                                  # retried after the window


def test_an_empty_table_is_seeded_once_then_read_back(monkeypatch):
    state = {"rows": [], "seeded": 0}

    def read():
        return list(state["rows"])

    def seed():
        state["seeded"] += 1
        state["rows"] = [_row("Seeded A", pid=1), _row("Seeded B", pid=2)]
    monkeypatch.setattr(catalog, "_read_product_rows", read)
    monkeypatch.setattr(catalog, "_seed_products_table", seed)
    catalog.invalidate_product_caches()

    assert [p.name for p in catalog.effective_catalog()] == ["Seeded A", "Seeded B"]
    catalog.effective_catalog()
    assert state["seeded"] == 1


def test_a_failed_seed_degrades_to_the_seed_list_instead_of_raising(monkeypatch):
    monkeypatch.setattr(catalog, "_read_product_rows", lambda: [])

    def boom():
        raise RuntimeError("insert failed")
    monkeypatch.setattr(catalog, "_seed_products_table", boom)
    catalog.invalidate_product_caches()
    assert any(p.name == "Search - SEM" for p in catalog.effective_catalog())


def test_a_missing_legacy_table_never_takes_the_seed_list_down(monkeypatch):
    def boom():
        raise RuntimeError("relation rate_overrides does not exist")
    monkeypatch.setattr(catalog, "load_rate_overrides", boom)
    monkeypatch.setattr(catalog, "load_custom_products", boom)
    monkeypatch.setattr(catalog, "load_deleted_builtin_names", boom)
    seed = catalog._seed_products()
    assert len(seed) == len(catalog.CATALOG)


def test_writes_refuse_to_run_when_the_table_cant_be_reached(monkeypatch):
    monkeypatch.setattr(catalog, "_read_product_rows", lambda: None)
    catalog.invalidate_product_caches()
    with pytest.raises(RuntimeError, match="schema.sql"):
        catalog.create_product({"name": "X", "family": "F", "buying_model": "CPM"})
    with pytest.raises(RuntimeError):
        catalog.list_products()
