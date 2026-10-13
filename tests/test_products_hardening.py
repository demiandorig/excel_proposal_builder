"""
Review-round hardening of the freeform Products list: what the legacy seed keeps, how the list survives a database
blip, what a product is allowed to hold, and how the bulk CSV upload reads a spreadsheet. Same in-memory fake database
as test_products.py.
"""
from __future__ import annotations

import asyncio
import io
import os

os.environ.setdefault("DATABASE_URL", "postgresql://fake:fake@localhost/fake")

import pytest

from app import catalog
from tests.test_products import FakeDB, _product, _row_from, db  # noqa: F401  (db is a fixture)


class _Upload:
    def __init__(self, data: bytes):
        self._data = data

    async def read(self):
        return self._data


def _upload(m, text: str, encoding="utf-8"):
    return asyncio.run(m.admin_bulk_upsert_products(_Upload(text.encode(encoding))))


@pytest.fixture
def empty_db(monkeypatch, real_catalog_readers):
    fake = FakeDB([])
    monkeypatch.setattr(catalog, "get_connection", fake)
    monkeypatch.setattr(catalog, "_read_product_rows", real_catalog_readers["_read_product_rows"])
    monkeypatch.setattr(catalog, "all_product_aliases", real_catalog_readers["all_product_aliases"])
    catalog.invalidate_product_caches()
    return fake


# --- the seed keeps what the legacy tables held ------------------------------------------------------------------------

def test_a_legacy_deletion_is_seeded_as_deleted_so_old_proposals_still_resolve_it(monkeypatch, empty_db):
    monkeypatch.setattr(catalog, "load_deleted_builtin_names", lambda: {"Spotify"})
    rows = catalog.load_product_rows()
    spotify = next(r for r in rows if r["product"].name == "Spotify")
    assert spotify["is_deleted"] is True
    assert "Spotify" not in [p["name"] for p in catalog.list_products()]
    assert catalog.by_name("Spotify") is not None


def test_a_legacy_rename_is_seeded_with_its_alias(monkeypatch, empty_db):
    monkeypatch.setattr(catalog, "load_rate_overrides", lambda: {"Search - SEM": {"name": "Paid Search"}})
    catalog.load_product_rows()
    assert empty_db.aliases == {"Search - SEM": "Paid Search"}
    assert catalog.by_name("Search - SEM").name == "Paid Search"


def test_a_legacy_deletion_of_the_old_youtube_product_deletes_both_halves(monkeypatch):
    monkeypatch.setattr(catalog, "load_deleted_builtin_names", lambda: {"YouTube Ads"})
    seeded = {p.name: gone for p, gone in catalog._seed_rows()}
    assert seeded["YouTube Ads - Skippable"] is True and seeded["YouTube Ads - Non-Skippable"] is True
    assert seeded["Search - SEM"] is False


def test_a_transient_legacy_read_failure_never_seeds_a_half_informed_table(monkeypatch, empty_db):
    def blip():
        raise ConnectionError("the database blinked")
    monkeypatch.setattr(catalog, "load_rate_overrides", blip)
    rows = catalog.load_product_rows()                       # falls back to the seed list for now…
    assert all(r["id"] is None for r in rows) and empty_db.rows == []     # …and wrote nothing


def test_a_table_that_was_never_created_just_has_nothing_to_fold_in():
    class UndefinedTable(Exception):
        pass

    def missing():
        raise UndefinedTable("relation does not exist")
    assert catalog._legacy(missing, {}, strict=True) == {}
    with pytest.raises(ConnectionError):
        catalog._legacy(lambda: (_ for _ in ()).throw(ConnectionError("x")), {}, strict=True)
    assert catalog._legacy(lambda: (_ for _ in ()).throw(ConnectionError("x")), {}) == {}


# --- a database blip after the list loaded keeps serving it --------------------------------------------------------------

def test_the_last_good_list_is_served_when_a_later_read_fails(monkeypatch, db):
    created = catalog.create_product({"name": "Edited Live", "family": "Radio", "buying_model": "Fixed"})
    assert "Edited Live" in [p.name for p in catalog.effective_catalog()]
    monkeypatch.setattr(catalog, "_read_product_rows", lambda: None)       # the table becomes unreadable
    catalog._products_cache.invalidate()                                   # the TTL ran out
    names = [p.name for p in catalog.effective_catalog()]
    assert "Edited Live" in names and created["id"]                        # not the stale seed list


# --- names -------------------------------------------------------------------------------------------------------------------

def test_name_variants_follow_a_rename_chain_and_the_split_whatever_the_order(monkeypatch, db):
    monkeypatch.setattr(catalog, "_db_aliases", lambda: {"Alpha 1": "Alpha 2", "Alpha 2": "Alpha 3", "Alpha": "Alpha 1"})
    db.rows[0]["name"] = "Alpha 3"
    catalog.invalidate_product_caches()
    assert catalog.name_variants("Alpha 3") >= {"Alpha", "Alpha 1", "Alpha 2", "Alpha 3"}


def test_both_youtube_halves_answer_to_the_name_they_had_before_the_split():
    assert "YouTube Ads" in catalog.name_variants("YouTube Ads - Skippable")
    assert "YouTube Ads" in catalog.name_variants("YouTube Ads - Non-Skippable")


def test_names_are_unique_ignoring_case_and_the_family_keeps_its_existing_spelling(db):
    with pytest.raises(ValueError, match="already exists"):
        catalog.create_product({"name": "ALPHA", "family": "Display", "buying_model": "CPM"})
    made = catalog.create_product({"name": "Echo", "family": "video", "buying_model": "CPM"})
    assert made["family"] == "Video"                                       # "Video" already exists; no look-alike family
    assert [p["name"] for p in catalog.list_products()].count("Echo") == 1


def test_whitespace_in_a_name_is_collapsed(db):
    made = catalog.create_product({"name": "  Echo   Plus ", "family": "Display", "buying_model": "CPM"})
    assert made["name"] == "Echo Plus"


# --- what a product may hold --------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("field,value,message", [
    ("base_rate", "nan", "real number"), ("base_rate", "inf", "real number"), ("base_rate", -1, "between"),
    ("minimum_spend", "abc", "must be a number"), ("minimum_spend", 1e12, "between"),
    ("margin_upper", 1.5, "between"), ("sla_total_days", 2.5, "whole number"), ("sla_total_days", -3, "between 0 and 100,000"),
    ("minimum_flight_days_min", -1, "between 0 and 100,000"), ("minimum_flight_days_max", 2.5, "whole number"),
])
def test_numbers_must_be_real_and_sane(field, value, message):
    base = {"name": "N", "family": "F", "buying_model": "CPM"}
    with pytest.raises(ValueError, match=message):
        catalog._build_product({**base, field: value}, None)


def test_control_characters_are_stripped_from_text_but_ordinary_whitespace_survives():
    p = catalog._build_product({"name": "Pasted\x00 Name\x0b", "family": "F", "buying_model": "CPM",
                                "proposal_description": "Line one\nLine two\x07\tTabbed"}, None)
    assert p.name == "Pasted Name" and p.proposal_description == "Line one\nLine two\tTabbed"


def test_an_overlong_text_is_refused():
    with pytest.raises(ValueError, match="too long"):
        catalog._build_product({"name": "N" * 400, "family": "F", "buying_model": "CPM"}, None)


# --- the bulk upload ---------------------------------------------------------------------------------------------------------------

def test_bulk_upload_reads_currency_formatted_cells_and_refuses_unparseable_ones(db):
    import app.main as m
    out = _upload(m, "name,family,buying_model,base_rate,minimum_spend\n"
                     "Alpha,Display,CPM,\"$1,000.00\",\"1,500\"\n"
                     "Bravo,Video,CPM,abc,100\n")
    assert out["updated"] == ["Alpha"] and out["created"] == []
    alpha = next(p for p in catalog.list_products() if p["name"] == "Alpha")
    assert alpha["base_rate"] == 1000.0 and alpha["minimum_spend"] == 1500.0
    bravo = next(p for p in catalog.list_products() if p["name"] == "Bravo")
    assert bravo["base_rate"] == 10.0                                      # an unreadable cell must NOT wipe the rate
    assert len(out["errors"]) == 1 and "isn't a number" in out["errors"][0] and "Row 3" in out["errors"][0]


def test_bulk_upload_skips_unchanged_rows_matches_names_ignoring_case_and_accepts_excel_cp1252(db):
    import app.main as m
    out = _upload(m, "name,family,buying_model,notes\n"
                     "alpha,Display,CPM,\n"
                     "Bravo,Video,CPM,caf\xe9 note\n", encoding="cp1252")
    assert out["unchanged"] == ["Alpha"] and out["updated"] == ["Bravo"] and out["created"] == []
    assert next(p for p in catalog.list_products() if p["name"] == "Bravo")["notes"] == "café note"
    assert [p["name"] for p in catalog.list_products()] == ["Alpha", "Bravo", "Charlie"]


def test_bulk_upload_one_bad_row_rolls_back_alone(db, monkeypatch):
    import app.main as m
    import psycopg

    real_params = catalog._product_params

    def refusing(product):
        if product.name == "Poison":
            raise psycopg.errors.DataError("value too long")
        return real_params(product)
    monkeypatch.setattr(catalog, "_product_params", refusing)
    out = _upload(m, "name,family,buying_model\nGood One,Display,CPM\nPoison,Display,CPM\nGood Two,Display,CPM\n")
    assert out["created"] == ["Good One", "Good Two"]
    assert len(out["errors"]) == 1 and "Row 3 ('Poison')" in out["errors"][0] and "database refused" in out["errors"][0]


def test_bulk_upload_brings_a_deleted_name_back_instead_of_colliding_with_it(db):
    import app.main as m
    charlie = next(p["id"] for p in catalog.list_products() if p["name"] == "Charlie")
    catalog.delete_product(charlie)
    out = _upload(m, "name,family,buying_model\nCharlie,Display,CPM\n")
    assert out["created"] == ["Charlie"] and "Charlie" in [p["name"] for p in catalog.list_products()]
    assert len([r for r in db.rows if r["name"] == "Charlie"]) == 1
    assert next(r for r in db.rows if r["name"] == "Charlie")["id"] == charlie


def test_bulk_upload_rejects_an_unrecognised_yes_no(db):
    import app.main as m
    out = _upload(m, "name,family,buying_model,is_addon\nAlpha,Display,CPM,maybe\n")
    assert out["updated"] == [] and "isn't yes/no" in out["errors"][0]
