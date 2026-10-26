"""Shared pytest setup for running the repository tests from any directory."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# The product list comes from the `products` table; with no table reachable it is the seed in app/catalog.py,
# which is what almost every test wants — deterministic and database-free. It is installed at IMPORT time too,
# because several test modules build restriction categories (which read the product list) while they are being
# collected, before any fixture runs; without it each of those reads would wait on a database that isn't there.
import app.catalog as _catalog  # noqa: E402

_REAL_CATALOG_READERS = {
    name: getattr(_catalog, name)
    for name in ("_read_product_rows", "load_rate_overrides", "load_custom_products", "load_deleted_builtin_names",
                 "all_product_aliases", "resolve_product_alias")
}


def _offline_readers():
    return {
        "_read_product_rows": lambda: None,
        "load_rate_overrides": lambda: {},
        "load_custom_products": lambda: [],
        "load_deleted_builtin_names": lambda: set(),
        "all_product_aliases": lambda: dict(_catalog._STATIC_ALIASES),
        "resolve_product_alias": lambda name: _catalog._STATIC_ALIASES.get(name),
    }


for _name, _fn in _offline_readers().items():
    setattr(_catalog, _name, _fn)


# The forecast data (series in Postgres) and the Admin -> Forecast override rows are read through these two functions. Like
# the product list above, they are pinned OFFLINE at import time: several test modules assert at import (the script-style
# Constanza tests), i.e. during collection, and their numbers must not depend on what the machine's database happens to
# hold (on Replit it holds the real series). real_db tests get the real readers back.
import app.services.constanza_forecast_map as _forecast_map  # noqa: E402
import app.services.forecast_store as _forecast_store  # noqa: E402

_REAL_FORECAST_READERS = {"store_fetch": _forecast_store._fetch_catalog, "map_read_rows": _forecast_map._read_rows}
_REAL_DMA_TABLE = _forecast_store.dma_table          # the cached function; a test may replace the module attribute
_forecast_store._fetch_catalog = lambda: None
_forecast_map._read_rows = lambda: []


def pytest_configure(config):
    config.addinivalue_line("markers", "real_db: the test talks to the real DATABASE_URL (no offline product list)")


@pytest.fixture(autouse=True)
def _product_list_without_a_database(request, monkeypatch):
    """A test that wants overrides / custom products / aliases patches those loaders itself, exactly as before;
    a test that exercises the table patches app.catalog._read_product_rows. Tests marked real_db get the real
    readers back."""
    readers = _REAL_CATALOG_READERS if request.node.get_closest_marker("real_db") else _offline_readers()
    for name, fn in readers.items():
        monkeypatch.setattr(_catalog, name, fn)
    _catalog.invalidate_product_caches()
    yield
    _catalog.invalidate_product_caches()


@pytest.fixture
def real_catalog_readers():
    """The real (database-reading) catalog functions, for a test that stubs the database layer itself."""
    return _REAL_CATALOG_READERS


@pytest.fixture(autouse=True)
def _faq_rows_without_a_database(request, monkeypatch):
    """The Process FAQs tab reads its rows from the `faq_entries` table; offline that is the built-in rows. A test of
    the table itself patches `app.services.faq_entries.get_connection`; real_db tests keep the real loader."""
    if request.node.get_closest_marker("real_db"):
        return
    from app.services import faq_entries
    real_load = faq_entries.load_entries
    monkeypatch.setattr(faq_entries, "load_entries", faq_entries.default_entries)
    monkeypatch.setattr(faq_entries, "_real_load_entries", real_load, raising=False)


@pytest.fixture(autouse=True)
def _forecast_map_without_a_database(request, monkeypatch):
    """Admin -> Forecast reads its overrides from the `forecast_product_map` table and the forecast series from the
    forecast_* tables; offline there are none, so every product keeps its built-in default (what the app did before the
    mapping existed). A test of the table or of overrides patches `app.services.constanza_forecast_map._read_rows` (and
    _write/_delete) itself, a test of the series patches `app.services.forecast_store._fetch_catalog`; real_db tests keep
    the real readers."""
    real = bool(request.node.get_closest_marker("real_db"))
    monkeypatch.setattr(_forecast_map, "_read_rows", _REAL_FORECAST_READERS["map_read_rows"] if real else (lambda: []))
    monkeypatch.setattr(_forecast_store, "_fetch_catalog", _REAL_FORECAST_READERS["store_fetch"] if real else (lambda: None))
    # A generator fixture must always reach its yield: returning before it makes pytest raise "did not yield a value"
    # for real_db tests (which is how this failed the post-merge run on a machine that has a database).
    _forecast_map.invalidate()
    _forecast_store.invalidate()
    _REAL_DMA_TABLE.cache_clear()
    yield
    _forecast_map.invalidate()
    _forecast_store.invalidate()
    _REAL_DMA_TABLE.cache_clear()


# The programmatic win rate is configurable from the environment (CONSTANZA_WIN_RATE_LOW / _MID / _HIGH / _PRIME, also via a .env that app.main
# loads); the tests pin the DEFAULT 10 / 15 / 20% unless one sets its own, so a developer's local override must never leak in. Scrubbed at
# import (the script-style test modules assert while being collected) and before every test (app.main's load_dotenv() may add them later).
_WIN_RATE_ENV = ("CONSTANZA_WIN_RATE_LOW", "CONSTANZA_WIN_RATE_MID", "CONSTANZA_WIN_RATE_HIGH", "CONSTANZA_WIN_RATE_PRIME")
for _var in _WIN_RATE_ENV:
    os.environ.pop(_var, None)


# The audience-size platforms' planning multipliers (CONSTANZA_AUDIENCE_X_*) are configurable the same way; tests pin the defaults 4.3 / 5.5 / 8.6.
_AUDIENCE_X_ENV = ("CONSTANZA_AUDIENCE_X_CONSERVATIVE", "CONSTANZA_AUDIENCE_X_MID", "CONSTANZA_AUDIENCE_X_UPPER")
for _var in _AUDIENCE_X_ENV:
    os.environ.pop(_var, None)


@pytest.fixture(autouse=True)
def _default_win_rate(monkeypatch):
    for var in (*_WIN_RATE_ENV, *_AUDIENCE_X_ENV):
        monkeypatch.delenv(var, raising=False)
