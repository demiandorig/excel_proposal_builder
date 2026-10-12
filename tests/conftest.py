"""Shared pytest setup for running the repository tests from any directory."""

from __future__ import annotations

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
