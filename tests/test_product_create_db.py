"""Adding a product against a real PostgreSQL (skipped without a reachable DATABASE_URL, like test_forecast_platform_db.py).

products.product_key is NOT NULL and was filled only by a BEFORE INSERT trigger (migration 0001). A published deployment never runs
migrations, so the trigger can be absent there while the column is present: Add Product then failed with a NotNullViolation, which
escaped as a bare 500 and read "Request failed." in the form. The application now sets the key itself. These tests run the real insert
in a transaction that is rolled back, with the trigger switched off (the production situation) and on, so nothing is left behind.
"""
from __future__ import annotations

import os
import uuid

import psycopg
import pytest
from psycopg.rows import dict_row

from app import catalog
from app.services import migrations

pytestmark = pytest.mark.real_db


@pytest.fixture(scope="module")
def url():
    value = os.environ.get("DATABASE_URL")
    if not value:
        pytest.skip("no DATABASE_URL")
    try:
        psycopg.connect(value, connect_timeout=2).close()
    except Exception:
        pytest.skip("no reachable database")
    migrations.apply_pending()
    return value


class _Borrowed:
    """get_connection() stand-in: hands out ONE open transaction and never commits it, so the test can roll everything back."""

    def __init__(self, conn):
        self.conn = conn

    def __call__(self):
        return self

    def __enter__(self):
        return self.conn

    def __exit__(self, *exc):
        return False


@pytest.fixture
def transaction(url, monkeypatch):
    conn = psycopg.connect(url, row_factory=dict_row)
    monkeypatch.setattr(catalog, "get_connection", _Borrowed(conn))
    catalog.invalidate_product_caches()
    yield conn
    conn.rollback()
    conn.close()
    catalog.invalidate_product_caches()


def _name() -> str:
    return f"zz-test Key {uuid.uuid4().hex[:8]}"


def _key_of(conn, product_id) -> str:
    return conn.execute("SELECT product_key FROM products WHERE id = %s", (product_id,)).fetchone()["product_key"]


def _form(name: str) -> dict:
    return {"name": name, "family": "Search", "buying_model": "Fixed"}


def test_the_slug_is_the_one_the_database_function_makes(transaction):
    for name in ("Google Search", "  Meta | Awareness!! ", "---", "", "Ünïcode Ads", "A" * 120, "x-" * 60, "Tiktok Ads", "a_b c-d"):
        sql = transaction.execute("SELECT product_key_slug(%s) AS s", (name,)).fetchone()["s"]
        assert catalog.product_key_slug(name) == sql, name


def test_a_product_is_added_when_the_trigger_that_used_to_fill_its_key_is_missing(transaction):
    transaction.execute("ALTER TABLE products DISABLE TRIGGER products_assign_key_trg")            # rolled back with the rest
    name = _name()
    out = catalog.create_product(_form(name))
    assert _key_of(transaction, out["id"]) == catalog.product_key_slug(name)


def test_the_old_code_path_really_fails_there_and_would_have_been_a_bare_500(transaction):
    """The reproduction of the reported error, kept so nobody removes the explicit key as 'redundant with the trigger'."""
    transaction.execute("ALTER TABLE products DISABLE TRIGGER products_assign_key_trg")
    columns = ", ".join(catalog._PRODUCT_COLUMNS)
    placeholders = ", ".join(["%s"] * (len(catalog._PRODUCT_COLUMNS) + 1))
    product = catalog._build_product(_form(_name()), None)
    with pytest.raises(psycopg.errors.NotNullViolation) as exc:
        transaction.execute(f"INSERT INTO products (sort_order, {columns}) VALUES ({placeholders})", [9999] + catalog._product_params(product))
    assert exc.value.diag.column_name == "product_key"


def test_look_alike_names_get_numbered_keys_with_or_without_the_trigger(transaction):
    base = _name()
    first = catalog.create_product(_form(base))
    second = catalog.create_product(_form(base.upper().replace("ZZ-TEST", "zz-test") + "!"))        # same slug, different name
    assert _key_of(transaction, second["id"]) == _key_of(transaction, first["id"]) + "-2"


def test_a_copy_and_a_bulk_upload_get_keys_too(transaction):
    transaction.execute("ALTER TABLE products DISABLE TRIGGER products_assign_key_trg")
    source = catalog.create_product(_form(_name()))
    copy = catalog.create_product({"name": _name()}, copy_from_id=source["id"])
    assert _key_of(transaction, copy["id"]) == catalog.product_key_slug(copy["name"])
    names = [_name(), _name()]
    out = catalog.bulk_upsert_products([{"line": i + 2, "fields": _form(n)} for i, n in enumerate(names)])
    assert out["errors"] == [] and sorted(out["created"]) == sorted(names)
    rows = transaction.execute("SELECT name, product_key FROM products WHERE name = ANY(%s)", (names,)).fetchall()
    assert {r["name"]: r["product_key"] for r in rows} == {n: catalog.product_key_slug(n) for n in names}


def test_a_deleted_product_brought_back_keeps_its_key(transaction):
    name = _name()
    first = catalog.create_product(_form(name))
    key = _key_of(transaction, first["id"])
    catalog.delete_product(first["id"])
    again = catalog.create_product(_form(name))
    assert again["id"] == first["id"] and _key_of(transaction, again["id"]) == key


def test_a_database_that_refuses_for_another_reason_says_which_rule_and_nothing_else(transaction):
    """A rule this form cannot foresee (here a CHECK added by someone) is a RuntimeError sentence, never a bare 500."""
    transaction.execute("ALTER TABLE products ADD CONSTRAINT zz_test_never_ok CHECK (name NOT LIKE 'zz-test Refused%')")
    with pytest.raises(RuntimeError) as exc:
        catalog.create_product(_form("zz-test Refused product"))
    assert "'zz_test_never_ok'" in str(exc.value) and "server log" in str(exc.value)
    assert "Refused product" not in str(exc.value)
