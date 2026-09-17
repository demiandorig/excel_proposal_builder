"""
Covers the new short-TTL read-through cache in app/catalog.py — the actual
confirmed root cause of "Suggest Mix takes forever": _apply_override()
called load_rate_overrides() (a full-table SELECT) once PER PRODUCT, and
effective_catalog() is called fresh by by_family() once PER RECOMMENDED
TACTIC, so a single Suggest Mix click was triggering on the order of
hundreds of DB round trips. This only tests the caching layer itself (call
counting against a stubbed connection, never a real DATABASE_URL) — not
the SQL/row-shape logic those functions already had, which is unchanged.
"""
from __future__ import annotations

import pytest

from app import catalog


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return list(self._rows)


class _CountingConn:
    """Returns empty results for anything — this test only cares HOW MANY
    TIMES the connection is opened, not what comes back."""
    def __init__(self, counter: list):
        self._counter = counter

    def __enter__(self):
        self._counter.append(1)
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=()):
        return _FakeResult([])


@pytest.fixture(autouse=True)
def _reset_catalog_caches():
    """These caches are module-level singletons — reset before AND after
    every test in this file so no test's cache state leaks into another
    (in here or in a different test file run in the same pytest session)."""
    catalog._rate_overrides_cache.invalidate()
    catalog._custom_products_cache.invalidate()
    catalog._deleted_builtin_names_cache.invalidate()
    yield
    catalog._rate_overrides_cache.invalidate()
    catalog._custom_products_cache.invalidate()
    catalog._deleted_builtin_names_cache.invalidate()


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


def test_load_rate_overrides_only_hits_the_db_once_across_repeated_calls(monkeypatch):
    open_count = []
    monkeypatch.setattr(catalog, "get_connection", lambda: _CountingConn(open_count))

    catalog.load_rate_overrides()
    catalog.load_rate_overrides()
    catalog.load_rate_overrides()

    assert len(open_count) == 1


def test_load_custom_products_only_hits_the_db_once_across_repeated_calls(monkeypatch):
    open_count = []
    monkeypatch.setattr(catalog, "get_connection", lambda: _CountingConn(open_count))

    catalog.load_custom_products()
    catalog.load_custom_products()

    assert len(open_count) == 1


def test_load_deleted_builtin_names_only_hits_the_db_once_across_repeated_calls(monkeypatch):
    open_count = []
    monkeypatch.setattr(catalog, "get_connection", lambda: _CountingConn(open_count))

    catalog.load_deleted_builtin_names()
    catalog.load_deleted_builtin_names()

    assert len(open_count) == 1


def test_save_rate_overrides_invalidates_so_the_next_read_is_fresh(monkeypatch):
    open_count = []
    monkeypatch.setattr(catalog, "get_connection", lambda: _CountingConn(open_count))
    # save_rate_overrides() itself reads load_custom_products() (for name
    # validation) and writes rate_overrides — both go through the same
    # stubbed connection, so count everything together.

    catalog.load_rate_overrides()
    assert len(open_count) == 1
    catalog.save_rate_overrides({})  # writes + must invalidate on its own
    catalog.load_rate_overrides()
    # The write itself opened a connection, AND invalidated the cache, so
    # this next read opens ANOTHER connection rather than serving stale
    # data from before the write.
    assert len(open_count) > 1


def test_clear_rate_override_invalidates(monkeypatch):
    open_count = []
    monkeypatch.setattr(catalog, "get_connection", lambda: _CountingConn(open_count))

    catalog.load_rate_overrides()
    before = len(open_count)
    catalog.clear_rate_override("Some Product")
    catalog.load_rate_overrides()
    # The clear (a write) + the fresh reload after it (no longer served
    # from a stale cache) both open a connection = 2 more than before.
    assert len(open_count) == before + 2


def test_set_builtin_deleted_invalidates_both_caches(monkeypatch):
    open_count = []
    monkeypatch.setattr(catalog, "get_connection", lambda: _CountingConn(open_count))

    catalog.load_deleted_builtin_names()
    catalog.load_rate_overrides()
    before = len(open_count)
    catalog.set_builtin_deleted("Some Product", True)
    catalog.load_deleted_builtin_names()
    catalog.load_rate_overrides()
    # 1 write + 2 fresh reads = 3 more connections, not 0 (stale cache) or
    # just 1 (only one of the two caches actually invalidated).
    assert len(open_count) == before + 3
