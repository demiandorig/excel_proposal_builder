"""
Admin -> Products: ONE freeform product list (no built-in / custom split, no "original" list), with duplicate-to-
make-a-variant, plus the YouTube split into Skippable / Non-Skippable. The database layer is replaced by a small
in-memory fake that understands the handful of statements app/catalog.py issues.
"""
from __future__ import annotations

import asyncio
import csv
import io
import os
import re

os.environ.setdefault("DATABASE_URL", "postgresql://fake:fake@localhost/fake")

import pytest
from fastapi import HTTPException

from app import catalog
from app.catalog import Product


# --- an in-memory stand-in for the `products` / `product_aliases` tables ------------------------------------------------

class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class FakeDB:
    def __init__(self, rows=None):
        self.rows: list[dict] = rows or []
        self.aliases: dict[str, str] = {}
        self._next_id = max([r["id"] for r in self.rows], default=0) + 1
        self.statements: list[str] = []

    # context manager protocol of get_connection()
    def __call__(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def transaction(self):
        """A savepoint: an exception inside rolls the rows back to how they were on entry."""
        fake = self

        class _Savepoint:
            def __enter__(self_inner):
                import copy
                self_inner.snapshot = (copy.deepcopy(fake.rows), dict(fake.aliases))
                return self_inner

            def __exit__(self_inner, exc_type, *a):
                if exc_type is not None:
                    fake.rows, fake.aliases = self_inner.snapshot
                return False
        return _Savepoint()

    def _ordered(self):
        return sorted(self.rows, key=lambda r: (r["sort_order"], r["id"]))

    def execute(self, sql, params=()):
        q = re.sub(r"\s+", " ", sql).strip()
        self.statements.append(q)
        params = list(params)
        if q.startswith("SELECT id, is_deleted, sort_order,") and "WHERE id = %s" in q:
            return _Result([dict(r) for r in self.rows if r["id"] == params[0]])
        if q.startswith("SELECT id, is_deleted,") and "ORDER BY sort_order" in q:
            return _Result([dict(r) for r in self._ordered()])
        if q.startswith("SELECT id, is_deleted, name FROM products"):
            return _Result([{"id": r["id"], "is_deleted": r["is_deleted"], "name": r["name"]} for r in self.rows])
        if q.startswith("SELECT COALESCE(MAX(sort_order)"):
            return _Result([{"n": max([r["sort_order"] for r in self.rows], default=-1) + 1}])
        if q.startswith("UPDATE products SET sort_order = sort_order + 1 WHERE sort_order >= %s"):
            for r in self.rows:
                if r["sort_order"] >= params[0]:
                    r["sort_order"] += 1
            return _Result([])
        if q.startswith("INSERT INTO products (sort_order,"):
            seeding = q.startswith("INSERT INTO products (sort_order, is_deleted,")
            cols = ["sort_order"] + (["is_deleted"] if seeding else []) + list(catalog._PRODUCT_COLUMNS)
            row = dict(zip(cols, params))
            row.setdefault("is_deleted", False)
            if "ON CONFLICT (name) DO NOTHING" in q and any(r["name"] == row["name"] for r in self.rows):
                return _Result([])
            row["id"] = self._next_id
            self._next_id += 1
            self.rows.append(row)
            return _Result([{"id": row["id"]}])
        if q.startswith("UPDATE products SET is_deleted = TRUE"):
            for r in self.rows:
                if r["id"] == params[0]:
                    r["is_deleted"] = True
            return _Result([])
        if q.startswith("UPDATE products SET name = %s"):
            revive = "is_deleted = FALSE" in q
            product_params, rest = params[:len(catalog._PRODUCT_COLUMNS)], params[len(catalog._PRODUCT_COLUMNS):]
            target = rest[-1]
            for r in self.rows:
                if r["id"] == target:
                    r.update(zip(catalog._PRODUCT_COLUMNS, product_params))
                    if revive:
                        r["is_deleted"] = False
                        r["sort_order"] = rest[0]
            return _Result([])
        if q.startswith("DELETE FROM products WHERE id = %s"):
            self.rows = [r for r in self.rows if r["id"] != params[0]]
            return _Result([])
        if q.startswith("UPDATE product_aliases SET current_name"):
            for alias, current in list(self.aliases.items()):
                if current == params[1]:
                    self.aliases[alias] = params[0]
            return _Result([])
        if q.startswith("INSERT INTO product_aliases"):
            if not ("DO NOTHING" in q and params[0] in self.aliases):
                self.aliases[params[0]] = params[1]
            return _Result([])
        if q.startswith("SELECT alias_name, current_name FROM product_aliases"):
            return _Result([{"alias_name": a, "current_name": c} for a, c in self.aliases.items()])
        raise AssertionError(f"FakeDB doesn't understand: {q[:120]}")


def _row_from(product: Product, pid: int, order: int, deleted=False) -> dict:
    row = {"id": pid, "sort_order": order, "is_deleted": deleted}
    values = catalog._product_params(product)
    row.update(zip(catalog._PRODUCT_COLUMNS, values))
    return row


def _product(name, family="Display", **kw) -> Product:
    base = dict(
        family=family, name=name, short_label="Awareness", proposal_description="A description.", sizes="300x250",
        buying_model="CPM", base_rate=10.0, estimated_impressions=False, discloses_impressions=True,
        minimum_spend=500.0, minimum_flight_days=(14, 60), sla_data_days=1, sla_creative_days=2, sla_activate_days=3,
        sla_total_days=6, media_allocation_pct=0.0, margin_upper=0.4, margin_lower=0.3, tech_platform="TTD",
        wide_orbit_code=name)
    base.update(kw)
    return Product(**base)


@pytest.fixture
def db(monkeypatch, real_catalog_readers):
    """A fake database holding three products, wired in under the real catalog functions."""
    fake = FakeDB([
        _row_from(_product("Alpha"), 1, 0), _row_from(_product("Bravo", family="Video"), 2, 1),
        _row_from(_product("Charlie"), 3, 2),
    ])
    monkeypatch.setattr(catalog, "get_connection", fake)
    monkeypatch.setattr(catalog, "_read_product_rows", real_catalog_readers["_read_product_rows"])
    monkeypatch.setattr(catalog, "all_product_aliases", real_catalog_readers["all_product_aliases"])
    catalog.invalidate_product_caches()
    return fake


def _names(fake=None):
    return [p["name"] for p in catalog.list_products()]


# --- the YouTube split -------------------------------------------------------------------------------------------------

def test_youtube_is_two_products_and_the_old_name_still_resolves():
    names = [p.name for p in catalog.CATALOG]
    assert "YouTube Ads" not in names
    assert "YouTube Ads - Skippable" in names and "YouTube Ads - Non-Skippable" in names
    assert catalog.by_name("YouTube Ads").name == "YouTube Ads - Skippable"          # saved proposals, pasted requests
    skippable, non = catalog.by_name("YouTube Ads - Skippable"), catalog.by_name("YouTube Ads - Non-Skippable")
    assert skippable.family == non.family == "Online Video"
    assert "bumper" in non.proposal_description.lower() and "skippable in-stream" in skippable.proposal_description.lower()
    assert "NonSkippable" not in skippable.proposal_description          # no more mixed-up description


def test_the_parser_and_recommender_know_the_split_names():
    from app.services.notion_parser import PRODUCT_ALIASES
    from app.services.recommender import GOAL_PRIORITIES
    assert PRODUCT_ALIASES["youtube non-skippable"] == "YouTube Ads - Non-Skippable"
    assert PRODUCT_ALIASES["youtube bumper"] == "YouTube Ads - Non-Skippable"
    assert PRODUCT_ALIASES["youtube ads"] == "YouTube Ads - Skippable"
    names = {p.name for p in catalog.effective_catalog()}
    for goal, products in GOAL_PRIORITIES.items():
        assert all(n in names for n in products), (goal, [n for n in products if n not in names])
    assert all(canon in names for canon in PRODUCT_ALIASES.values() if "YouTube" in canon)


def test_a_legacy_rate_on_the_old_youtube_product_carries_to_both_halves(monkeypatch):
    monkeypatch.setattr(catalog, "load_rate_overrides", lambda: {"YouTube Ads": {"minimum_spend": 800.0, "name": "ignored"}})
    catalog.invalidate_product_caches()
    seed = {p.name: p for p in catalog._seed_products()}
    assert seed["YouTube Ads - Skippable"].minimum_spend == 800.0 and seed["YouTube Ads - Non-Skippable"].minimum_spend == 800.0
    assert seed["YouTube Ads - Skippable"].name == "YouTube Ads - Skippable"


# --- seeding folds the three legacy stores in -----------------------------------------------------------------------------

def test_the_seed_folds_in_legacy_overrides_admin_added_products_and_deletions(monkeypatch):
    extra = _product("Admin Added", family="Podcasts")
    monkeypatch.setattr(catalog, "load_rate_overrides", lambda: {"Search - SEM": {"name": "Paid Search", "minimum_spend": 1234.0}})
    monkeypatch.setattr(catalog, "load_custom_products", lambda: [extra])
    monkeypatch.setattr(catalog, "load_deleted_builtin_names", lambda: {"Spotify"})
    catalog.invalidate_product_caches()
    names = {p.name: p for p in catalog._seed_products()}
    assert "Paid Search" in names and "Search - SEM" not in names and names["Paid Search"].minimum_spend == 1234.0
    assert "Admin Added" in names and "Spotify" not in names


def test_seeding_an_empty_table_inserts_the_whole_list_once(monkeypatch, real_catalog_readers):
    fake = FakeDB([])
    monkeypatch.setattr(catalog, "get_connection", fake)
    monkeypatch.setattr(catalog, "_read_product_rows", real_catalog_readers["_read_product_rows"])
    catalog.invalidate_product_caches()
    rows = catalog.load_product_rows()
    assert len(rows) == len(catalog.CATALOG) and all(r["id"] for r in rows)
    assert [r["product"].name for r in rows[:2]] == [catalog.CATALOG[0].name, catalog.CATALOG[1].name]
    catalog.invalidate_product_caches()
    catalog.load_product_rows()
    assert sum(1 for q in fake.statements if q.startswith("INSERT INTO products")) == len(catalog.CATALOG)


def test_the_first_write_on_an_empty_table_seeds_it_first(monkeypatch, real_catalog_readers):
    fake = FakeDB([])
    monkeypatch.setattr(catalog, "get_connection", fake)
    monkeypatch.setattr(catalog, "_read_product_rows", real_catalog_readers["_read_product_rows"])
    catalog.invalidate_product_caches()
    catalog.create_product({"name": "Brand New", "family": "Fam", "buying_model": "Fixed"})
    assert len(fake.rows) == len(catalog.CATALOG) + 1                     # the seed list wasn't lost to the first write


# --- building a product -----------------------------------------------------------------------------------------------------

def test_a_blank_product_needs_a_name_family_and_buying_model():
    with pytest.raises(ValueError, match="name is required"):
        catalog._build_product({}, None)
    with pytest.raises(ValueError, match="Family is required"):
        catalog._build_product({"name": "X"}, None)
    with pytest.raises(ValueError, match="Buying model"):
        catalog._build_product({"name": "X", "family": "F", "buying_model": "Weekly"}, None)


def test_a_copy_keeps_everything_but_what_the_form_changed_including_the_wide_orbit_code():
    source = _product("Source Product", sla_total_days=9, tech_platform="TTD", wide_orbit_code="WO-77")
    copy = catalog._build_product({"name": "Source Product - Hispanic", "minimum_spend": 750}, source)
    assert copy.name == "Source Product - Hispanic" and copy.wide_orbit_code == "WO-77"   # a billing code is never rewritten
    assert copy.minimum_spend == 750.0 and copy.sla_total_days == 9 and copy.tech_platform == "TTD"
    assert copy.minimum_flight_days == (14, 60) and copy.margin_upper == 0.4
    renamed = catalog._build_product({"name": "Renamed"}, _product("Old", wide_orbit_code="Old"))
    assert renamed.wide_orbit_code == "Old"                                # an edit never touches it either
    brand_new = catalog._build_product({"name": "Fresh", "family": "F", "buying_model": "CPM"}, None)
    assert brand_new.wide_orbit_code == "Fresh"                            # only a blank code defaults from the name


def test_absent_keys_are_kept_and_nullable_ones_can_be_cleared():
    p = _product("P", base_rate=12.5, estimated_cpm_for_imps=9.0, sla_data_days=2)
    kept = catalog._build_product({"notes": "n"}, p)
    assert kept.base_rate == 12.5 and kept.estimated_cpm_for_imps == 9.0 and kept.notes == "n"
    cleared = catalog._build_product({"base_rate": None, "estimated_cpm_for_imps": "", "sla_data_days": None}, p)
    assert cleared.base_rate is None and cleared.estimated_cpm_for_imps is None and cleared.sla_data_days is None


def test_flags_numbers_and_flight_days_are_coerced_and_checked():
    p = catalog._build_product({"name": "N", "family": "F", "buying_model": "CPM", "base_rate": "7.5", "is_addon": 1,
                                "minimum_flight_days_min": "10", "minimum_flight_days_max": "20", "sla_total_days": "5"}, None)
    assert p.base_rate == 7.5 and p.is_addon is True and p.minimum_flight_days == (10, 20) and p.sla_total_days == 5
    with pytest.raises(ValueError, match="Minimum flight"):
        catalog._build_product({"name": "N", "family": "F", "buying_model": "CPM", "minimum_flight_days_min": 30, "minimum_flight_days_max": 10}, None)


# --- the list, against the fake tables ------------------------------------------------------------------------------------------

def test_create_appends_and_a_duplicate_sits_right_after_its_source(db):
    created = catalog.create_product({"name": "Delta", "family": "Display", "buying_model": "CPM"})
    assert _names() == ["Alpha", "Bravo", "Charlie", "Delta"] and created["id"]
    alpha_id = next(p["id"] for p in catalog.list_products() if p["name"] == "Alpha")
    copy = catalog.create_product({"name": "Alpha - Hispanic"}, copy_from_id=alpha_id)
    assert _names() == ["Alpha", "Alpha - Hispanic", "Bravo", "Charlie", "Delta"]
    assert copy["copied_from"] == "Alpha" and copy["tech_platform"] == "TTD" and copy["minimum_spend"] == 500.0


def test_the_list_is_immediately_visible_everywhere_and_a_copy_of_a_missing_product_is_refused(db):
    catalog.create_product({"name": "Echo", "family": "Radio", "buying_model": "Fixed"})
    assert "Echo" in [p.name for p in catalog.effective_catalog()] and "Radio" in catalog.families()
    assert catalog.by_name("Echo").family == "Radio"
    with pytest.raises(ValueError, match="no longer exists"):
        catalog.create_product({"name": "Ghost"}, copy_from_id=999)


def test_names_are_unique_but_a_deleted_name_can_be_reused(db):
    with pytest.raises(ValueError, match="already exists"):
        catalog.create_product({"name": "Alpha", "family": "F", "buying_model": "CPM"})
    alpha_id = next(p["id"] for p in catalog.list_products() if p["name"] == "Alpha")
    assert catalog.delete_product(alpha_id) == "Alpha"
    assert "Alpha" not in _names() and catalog.by_name("Alpha") is not None      # hidden, still resolvable for old proposals
    revived = catalog.create_product({"name": "Alpha", "family": "Display", "buying_model": "CPM", "minimum_spend": 99})
    assert revived["id"] == alpha_id and "Alpha" in _names()
    assert len([r for r in db.rows if r["name"] == "Alpha"]) == 1


def test_editing_any_field_and_renaming_keeps_the_old_name_resolving(db):
    bravo = next(p for p in catalog.list_products() if p["name"] == "Bravo")
    updated = catalog.update_product(bravo["id"], {"name": "Bravo Plus", "family": "Streaming", "base_rate": None, "notes": "n"})
    assert updated["name"] == "Bravo Plus" and updated["family"] == "Streaming" and updated["base_rate"] is None
    assert catalog.by_name("Bravo").name == "Bravo Plus"                       # via the recorded alias
    assert catalog.name_variants("Bravo Plus") >= {"Bravo", "Bravo Plus"}
    assert db.aliases["Bravo"] == "Bravo Plus"


def test_a_rename_into_a_taken_or_deleted_name_is_refused_and_nothing_is_lost(db):
    rows = {p["name"]: p["id"] for p in catalog.list_products()}
    with pytest.raises(ValueError, match="already exists"):
        catalog.update_product(rows["Bravo"], {"name": "alpha"})                 # case-insensitive
    catalog.delete_product(rows["Charlie"])
    with pytest.raises(ValueError, match="deleted product already uses"):
        catalog.update_product(rows["Bravo"], {"name": "Charlie"})               # the tombstone is what old proposals resolve to
    assert [r["name"] for r in db.rows if r["is_deleted"]] == ["Charlie"] and "Bravo" in _names()
    revived = catalog.create_product({"name": "Charlie", "family": "Display", "buying_model": "CPM"})   # the supported way back
    assert "Charlie" in _names() and revived["id"] == rows["Charlie"]


def test_a_deleted_product_cannot_be_edited_and_deleting_twice_reports_not_found(db):
    pid = catalog.list_products()[0]["id"]
    catalog.delete_product(pid)
    with pytest.raises(LookupError):
        catalog.update_product(pid, {"notes": "x"})
    assert catalog.delete_product(pid) is None


# --- the admin endpoints ---------------------------------------------------------------------------------------------------------

def test_the_product_request_distinguishes_absent_from_null():
    import app.main as m
    body = m.ProductRequest.model_validate({"base_rate": None, "notes": "n"})
    assert m._product_fields(body) == {"base_rate": None, "notes": "n"}
    assert m._product_fields(m.ProductRequest.model_validate({"copy_from_id": 4, "name": "X"})) == {"name": "X"}


class _Req:
    class state:
        user = {"email": "admin@entravision.com"}


def test_creating_a_copy_carries_the_originals_vertical_approvals_over(monkeypatch):
    import app.main as m
    seen = {}
    monkeypatch.setattr(m, "catalog_create_product",
                        lambda fields, copy_from_id=None: {"id": 9, "name": fields["name"], "copied_from": "Video - Pre-roll (OLV)"})
    monkeypatch.setattr(m.restrictions_svc, "inherit_product_approvals",
                        lambda src, new, by: seen.update(src=src, new=new, by=by) or 2)
    out = asyncio.run(m.admin_create_product(m.ProductRequest(name="Pre-roll Hispanic", copy_from_id=4), _Req()))
    assert out["restrictions_inherited"] == 2 and seen == {"src": "Video - Pre-roll (OLV)", "new": "Pre-roll Hispanic", "by": "admin@entravision.com"}


def test_a_failed_approvals_carry_over_never_fails_the_copy(monkeypatch):
    import app.main as m
    monkeypatch.setattr(m, "catalog_create_product", lambda fields, copy_from_id=None: {"id": 9, "name": "X", "copied_from": "Y"})

    def boom(*a):
        raise RuntimeError("table missing")
    monkeypatch.setattr(m.restrictions_svc, "inherit_product_approvals", boom)
    out = asyncio.run(m.admin_create_product(m.ProductRequest(name="X", copy_from_id=1), _Req()))
    assert out["added"] is True and out["restrictions_inherited"] == 0


@pytest.mark.parametrize("error,status", [(ValueError("bad"), 400), (RuntimeError("no table"), 503)])
def test_create_errors_map_to_http_statuses(monkeypatch, error, status):
    import app.main as m

    def boom(*a, **k):
        raise error
    monkeypatch.setattr(m, "catalog_create_product", boom)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(m.admin_create_product(m.ProductRequest(name="X"), _Req()))
    assert exc.value.status_code == status


@pytest.mark.parametrize("error,status", [(LookupError("nope"), 404), (ValueError("bad"), 400), (RuntimeError("no table"), 503)])
def test_update_and_delete_errors_map_to_http_statuses(monkeypatch, error, status):
    import app.main as m

    def boom(*a, **k):
        raise error
    monkeypatch.setattr(m, "catalog_update_product", boom)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(m.admin_update_product(1, m.ProductRequest(name="X")))
    assert exc.value.status_code == status
    monkeypatch.setattr(m, "catalog_delete_product", lambda pid: None)
    with pytest.raises(HTTPException) as exc2:
        asyncio.run(m.admin_delete_product(1))
    assert exc2.value.status_code == 404


def test_the_csv_export_and_bulk_upload_round_trip(db):
    import app.main as m
    exported = asyncio.run(m.admin_export_products()).body.decode("utf-8")
    rows = list(csv.DictReader(io.StringIO(exported)))
    assert [r["name"] for r in rows] == ["Alpha", "Bravo", "Charlie"]
    assert set(rows[0]) == set(m._PRODUCT_CSV_COLUMNS) and "is_custom" not in rows[0]

    upload = io.BytesIO(("name,family,buying_model,base_rate,minimum_spend,notes\n"
                         "Alpha,Display,CPM,12.5,600,updated note\n"
                         "Delta,Podcasts,Fixed,,250,\n"
                         ",Display,CPM,,,\n"
                         "Bad,Display,Weekly,,,\n").encode("utf-8"))

    class _Upload:
        async def read(self):
            return upload.getvalue()
    out = asyncio.run(m.admin_bulk_upsert_products(_Upload()))
    assert out["created"] == ["Delta"] and out["updated"] == ["Alpha"] and len(out["errors"]) == 2
    alpha = next(p for p in catalog.list_products() if p["name"] == "Alpha")
    assert alpha["base_rate"] == 12.5 and alpha["minimum_spend"] == 600.0 and alpha["notes"] == "updated note"
    assert alpha["tech_platform"] == "TTD"                                  # columns the CSV doesn't carry are untouched


# --- a copy of an approved product is approved --------------------------------------------------------------------------------------

def test_a_copy_inherits_every_sheet_entry_its_original_is_mapped_under(monkeypatch):
    from app.services import restrictions as rs
    cats = rs.builtin_categories()
    state = {"categories": cats, "using_builtin": False, "sync": None, "error": False}
    monkeypatch.setattr(rs, "load_state", lambda: state)
    executed = []

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, params=()):
            executed.append((" ".join(sql.split()), params))
    monkeypatch.setattr(rs, "get_connection", lambda: _Conn())
    added = rs.inherit_product_approvals("Video - Pre-roll (OLV)", "Pre-roll - Hispanic", "a@x")
    assert added >= 1 and executed and "UPDATE restriction_categories SET product_map" in executed[0][0]
    political = next(c for c in cats if c.name == "Political")
    mapped = [item for item, names in political.product_map.items() if "Video - Pre-roll (OLV)" in names]
    assert mapped, "the original is mapped in the built-in Political list"
    assert rs.inherit_product_approvals("Not In Any List", "Whatever", "a@x") == 0


def test_the_built_in_snapshot_is_persisted_first_when_a_copy_inherits(monkeypatch):
    from app.services import restrictions as rs
    state = {"categories": rs.builtin_categories(), "using_builtin": True, "sync": None, "error": False}
    monkeypatch.setattr(rs, "load_state", lambda: state)
    seeded = []
    monkeypatch.setattr(rs, "seed_builtin_into_db", lambda by: seeded.append(by))

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, *a, **k):
            return None
    monkeypatch.setattr(rs, "get_connection", lambda: _Conn())
    rs.inherit_product_approvals("Video - Pre-roll (OLV)", "Pre-roll - Hispanic", "a@x")
    assert seeded == ["a@x"]


def test_a_product_that_is_not_on_the_sheets_list_reads_not_accepted_for_every_product_alike():
    from app.services import restrictions as rs
    verdict = rs.evaluate_product("Anything New", "Display", [c for c in rs.builtin_categories() if c.name == "Political"])
    assert verdict["verdict"] == "not_allowed" and "map it in Admin" in verdict["checks"][0]["note"]


# --- saved proposals follow a product's current name ---------------------------------------------------------------------------------

def test_reopening_a_saved_proposal_shows_a_split_or_renamed_product_under_its_current_name():
    import app.main as m
    state = {"line_items": [{"product_name": "YouTube Ads", "monthly_budget": 500}, {"product_name": "Search - SEM", "monthly_budget": 1}],
             "tiers": [{"label": "A", "line_items": [{"product_name": "YouTube Ads"}, {"product_name": "Totally Unknown"}]}]}
    fixed = m._with_current_product_names(state)
    assert fixed["line_items"][0]["product_name"] == "YouTube Ads - Skippable" and fixed["line_items"][1]["product_name"] == "Search - SEM"
    assert fixed["tiers"][0]["line_items"][0]["product_name"] == "YouTube Ads - Skippable"
    assert fixed["tiers"][0]["line_items"][1]["product_name"] == "Totally Unknown"       # untouched when it can't be resolved
