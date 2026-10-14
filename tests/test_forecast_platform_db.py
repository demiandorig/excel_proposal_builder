"""The forecast tables against a real Postgres: constraints, versions (stage / publish / rollback / prune), the
idempotent import, the migration runner and the read store.

These touch a shared database (on Replit, the real one), so every row they create belongs to a `zz-test-*` series or a
`zz-test` product / table and is removed afterwards; nothing here reads or changes the real series."""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import psycopg
import pytest

from app import catalog
from app.db import get_connection
from app.services import forecast_store, migrations
from app.services.forecast_import import geo_reference as geo
from app.services.forecast_import import loader, service
from app.services.forecast_import.model import Figures, PeriodInfo, ProductLink, SeriesPayload, SeriesSpec
from tests.forecast_support import make_export, row

pytestmark = pytest.mark.real_db


@pytest.fixture(scope="module")
def db():
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("no DATABASE_URL")
    try:
        psycopg.connect(url, connect_timeout=2).close()
    except Exception:
        pytest.skip("no reachable database")
    applied = migrations.apply_pending()
    with get_connection() as conn:
        geo.seed_geo(conn)
    yield applied
    _cleanup()


def _cleanup():
    with get_connection() as conn:
        conn.execute("DELETE FROM forecast_series_def WHERE series_key LIKE 'zz-test-%'")
        conn.execute("DELETE FROM products WHERE name LIKE 'zz-test %'")
        conn.execute("DELETE FROM forecast_publisher WHERE name_norm LIKE 'zz-test %'")
        conn.execute("DELETE FROM forecast_import_batch WHERE requested_by = 'zz-test'")


@pytest.fixture
def key(db):
    k = f"zz-test-{uuid.uuid4().hex[:8]}"
    yield k
    forecast_store.invalidate()
    _cleanup()


def payload_for(key, scale=1.0, links=(), dmas=3, **kw):
    spec = SeriesSpec(series_key=key, title=f"Test {key}", channel="ctv", avails_unit="monthly", reader="madhive_zip",
                      source="x", products=list(links), **kw)
    p = SeriesPayload(spec=spec, source_name="t.zip", source_sha256=uuid.uuid4().hex * 2)
    for i, d in enumerate(geo.dma_table()[:dmas]):
        p.levels["dma"][d.key] = Figures(avails=(1000 - i) * 1000 * scale, reach_universe=100.0, est_reach=90.0)
    p.levels["state"]["CA"] = Figures(avails=500_000 * scale)
    p.levels["cd"]["CA-12"] = Figures(avails=40_000 * scale)
    p.levels["zip"]["90210"] = Figures(avails=7_000 * scale, reach_universe=300, est_reach=250)
    p.levels["zip"]["08541"] = Figures(avails=3_000 * scale)
    p.unknown["dma"] = Figures(avails=10_000)
    p.devices["connected_tv"] = Figures(avails=900_000 * scale)
    p.publishers["zz-test Publisher"] = Figures(avails=900_000 * scale)
    return p


def stage_and_publish(key, **kw):
    p = payload_for(key, **kw)
    with get_connection() as conn:
        loader.upsert_series_def(conn, p)
        res = loader.stage(conn, p, None)
        loader.publish(conn, res.series_id, "tester")
    forecast_store.invalidate()
    return res


def status_of(key):
    with get_connection() as conn:
        return {r["version"]: r["status"] for r in conn.execute("SELECT version, status FROM forecast_series_version WHERE series_key = %s", (key,)).fetchall()}


# --------------------------------------------------------------------------------------------- schema

def test_migrations_are_applied_and_there_is_nothing_pending(db):
    rows = migrations.status()
    assert rows and all(r.state == "applied" for r in rows), rows
    assert migrations.apply_pending() == []                              # idempotent


def test_reference_data_is_seeded_and_the_seed_is_repeatable(db):
    with get_connection() as conn:
        again = geo.seed_geo(conn)
        counts = {t: conn.execute(f"SELECT count(*) AS n FROM {t}").fetchone()["n"] for t in ("geo_state", "geo_dma", "geo_congressional_district")}
    assert counts == {"geo_state": 51, "geo_dma": 210, "geo_congressional_district": 436}
    assert again["aliases"] >= 258 and again["dmas"] == 210


def test_every_product_has_a_stable_key_assigned_by_the_database(db):
    with get_connection() as conn:
        assert conn.execute("SELECT count(*) AS n FROM products WHERE product_key IS NULL OR product_key = ''").fetchone()["n"] == 0
        a = conn.execute("INSERT INTO products (name, family, buying_model) VALUES ('zz-test Product (Alpha)', 'Test', 'CPM') RETURNING product_key").fetchone()["product_key"]
        b = conn.execute("INSERT INTO products (name, family, buying_model) VALUES ('zz-test  product -- alpha', 'Test', 'CPM') RETURNING product_key").fetchone()["product_key"]
        c = conn.execute("INSERT INTO products (name, family, buying_model, product_key) VALUES ('zz-test Explicit', 'Test', 'CPM', 'zz-test-explicit-key') RETURNING product_key").fetchone()["product_key"]
        conn.execute("UPDATE products SET name = 'zz-test Renamed' WHERE product_key = %s", (a,))
        assert conn.execute("SELECT product_key FROM products WHERE name = 'zz-test Renamed'").fetchone()["product_key"] == a     # a rename never changes the key
        with pytest.raises(psycopg.errors.UniqueViolation):
            with conn.transaction():
                conn.execute("INSERT INTO products (name, family, buying_model, product_key) VALUES ('zz-test Dupe', 'Test', 'CPM', %s)", (a,))
    assert a == "zz-test-product-alpha" and b == "zz-test-product-alpha-2" and c == "zz-test-explicit-key"


def test_the_database_rejects_bad_rows(key):
    sid = stage_and_publish(key).series_id
    with get_connection() as conn:
        for sql, params in [
            ("INSERT INTO forecast_avail_dma (series_id, dma_key, avails) VALUES (%s, 'no-such-dma', 1)", (sid,)),                  # unknown market
            ("INSERT INTO forecast_avail_state (series_id, state_code, avails) VALUES (%s, 'ZZ', 1)", (sid,)),                      # unknown state
            ("INSERT INTO forecast_avail_cd (series_id, cd_key, avails) VALUES (%s, 'CA-99', 1)", (sid,)),                         # unknown district
            ("INSERT INTO forecast_avail_zip (series_id, zip, avails) VALUES (%s, '9021', 1)", (sid,)),                            # not five digits
            ("INSERT INTO forecast_avail_zip (series_id, zip, avails) VALUES (%s, '12345', -1)", (sid,)),                          # negative
            ("INSERT INTO forecast_avail_device (series_id, device, avails) VALUES (%s, 'toaster', 1)", (sid,)),
            ("INSERT INTO forecast_avail_zip (series_id, zip, avails) VALUES (%s, '90210', 1)", (sid,)),                           # duplicate key
            ("INSERT INTO forecast_avail_unknown (series_id, level, avails) VALUES (%s, 'planet', 1)", (sid,)),
        ]:
            with pytest.raises(psycopg.errors.Error):
                with conn.transaction():
                    conn.execute(sql, params)
        with pytest.raises(psycopg.errors.CheckViolation):
            with conn.transaction():
                conn.execute("INSERT INTO forecast_series_def (series_key, title, channel, avails_unit) VALUES ('zz-test-bad', 't', 'radio', 'monthly')")
        with pytest.raises(psycopg.errors.CheckViolation):
            with conn.transaction():
                conn.execute("INSERT INTO forecast_series_def (series_key, title, channel, avails_unit) VALUES ('Bad Key', 't', 'ctv', 'monthly')")


# --------------------------------------------------------------------------------------------- versions

def test_publishing_retires_the_previous_version_and_only_one_is_ever_current(key):
    v1 = stage_and_publish(key)
    assert v1.version == 1 and status_of(key) == {1: "current"}
    v2 = stage_and_publish(key, scale=1.1)
    assert v2.version == 2 and status_of(key) == {1: "retired", 2: "current"}
    with get_connection() as conn:
        with pytest.raises(psycopg.errors.UniqueViolation):             # the partial unique index
            with conn.transaction():
                conn.execute("UPDATE forecast_series_version SET status = 'current' WHERE series_key = %s AND version = 1", (key,))


def test_staging_does_not_change_what_is_served(key):
    stage_and_publish(key)
    p = payload_for(key, scale=5)
    with get_connection() as conn:
        loader.stage(conn, p, None)
    forecast_store.invalidate()
    assert status_of(key) == {1: "current", 2: "staged"}
    ref = forecast_store.series_by_key(key)
    assert ref.version == 1


def test_rollback_makes_the_previous_version_current_again_without_deleting_anything(key):
    stage_and_publish(key); stage_and_publish(key, scale=2)
    with get_connection() as conn:
        assert loader.rollback(conn, key) == 1
    assert status_of(key) == {1: "current", 2: "retired"}
    with get_connection() as conn:
        assert loader.rollback(conn, key, to_version=2) == 2
        with pytest.raises(ValueError):
            loader.rollback(conn, "zz-test-nothing-here")
    assert status_of(key) == {1: "retired", 2: "current"}


def test_prune_deletes_the_numbers_of_old_retired_versions_only(key):
    for scale in (1, 2, 3, 4):
        stage_and_publish(key, scale=scale)
    assert status_of(key) == {1: "retired", 2: "retired", 3: "retired", 4: "current"}
    with get_connection() as conn:
        old = conn.execute("SELECT series_id FROM forecast_series_version WHERE series_key = %s AND version = 1", (key,)).fetchone()["series_id"]
        assert loader.prune(conn, keep=1) == 2                           # versions 1 and 2; 3 is the newest retired, 4 is current
        assert conn.execute("SELECT count(*) AS n FROM forecast_avail_zip WHERE series_id = %s", (old,)).fetchone()["n"] == 0
    assert status_of(key) == {3: "retired", 4: "current"}


def test_the_same_source_and_spec_is_recognised_as_already_imported(key):
    p = payload_for(key)
    with get_connection() as conn:
        loader.upsert_series_def(conn, p)
        res = loader.stage(conn, p, None)
        loader.publish(conn, res.series_id, "t")
        found = loader.find_existing(conn, key, p.fingerprint())
        assert found["version"] == 1 and found["status"] == "current"
        changed = payload_for(key)
        changed.source_sha256 = p.source_sha256
        changed.spec.targeting_key = "hispanic"                           # same file, different spec: a new import
        assert loader.find_existing(conn, key, changed.fingerprint()) is None
        assert loader.current_total(conn, key) == pytest.approx(sum(f.avails for f in p.levels["dma"].values()))


def test_a_version_records_its_period_basis_and_validation(key):
    p = payload_for(key, period=PeriodInfo(label="Regular season", start="2026-04-01", end="2026-09-27", source_note="n"), data_basis="modeled", band_low=0.6, band_high=1.4)
    with get_connection() as conn:
        loader.upsert_series_def(conn, p)
        res = loader.stage(conn, p, None)
        loader.publish(conn, res.series_id, "t")
        v = conn.execute("SELECT * FROM forecast_series_version WHERE series_id = %s", (res.series_id,)).fetchone()
    assert v["period_label"] == "Regular season" and str(v["period_end"]) == "2026-09-27" and v["data_basis"] == "modeled"
    assert (v["band_low"], v["band_high"]) == (0.6, 1.4) and v["row_counts"]["zip"] == 2 and v["validation"]["counts"]["error"] == 0


def test_an_extension_with_no_extended_rows_is_not_labeled_extrapolated(key):
    p = payload_for(key, data_basis="extrapolated")
    with get_connection() as conn:
        loader.upsert_series_def(conn, p)
        res = loader.stage(conn, p, None)
        assert conn.execute("SELECT data_basis FROM forecast_series_version WHERE series_id = %s", (res.series_id,)).fetchone()["data_basis"] == "platform_reported"


def test_linking_products_is_by_stable_key_and_reports_names_that_are_not_in_the_catalog(key):
    with get_connection() as conn:
        conn.execute("INSERT INTO products (name, family, buying_model) VALUES ('zz-test Linked Product', 'Test', 'CPM')")
        p = payload_for(key, links=[ProductLink("zz-test Linked Product", "primary"), ProductLink("zz-test Ghost", "baseline_proxy")])
        loader.upsert_series_def(conn, p)
        assert loader.link_products(conn, p) == ["zz-test Ghost"]
        link = conn.execute("SELECT sp.* FROM forecast_series_product sp JOIN products p USING (product_key) WHERE p.name = 'zz-test Linked Product'").fetchone()
        assert link["series_key"] == key and link["relation"] == "primary"
        conn.execute("UPDATE products SET name = 'zz-test Linked Renamed' WHERE name = 'zz-test Linked Product'")
        assert conn.execute("SELECT count(*) AS n FROM forecast_series_product WHERE series_key = %s", (key,)).fetchone()["n"] == 1   # survives a rename
        conn.execute("DELETE FROM products WHERE name = 'zz-test Linked Renamed'")
        assert conn.execute("SELECT count(*) AS n FROM forecast_series_product WHERE series_key = %s", (key,)).fetchone()["n"] == 0   # and goes with the product


# --------------------------------------------------------------------------------------------- the read store

def test_the_store_serves_only_the_current_version_and_only_the_rows_asked_for(key):
    stage_and_publish(key)
    stage_and_publish(key, scale=2)
    ref = forecast_store.series_by_key(key)
    assert ref.version == 2 and ref.avails_unit == "monthly" and ref.levels == frozenset({"dma", "state", "cd", "zip"})
    sid = ref.series_id
    zips = forecast_store.zip_rows(sid, ["90210", "99999", "08541"])
    assert set(zips) == {"90210", "08541"} and zips["90210"].avails == 14_000 and zips["90210"].est_reach == 250
    assert forecast_store.cd_rows(sid, ["CA-12", "CA-13"])["CA-12"].avails == 80_000 and "CA-13" not in forecast_store.cd_rows(sid, ["CA-13"])
    assert forecast_store.state_rows(sid, ["CA"])["CA"].avails == 1_000_000
    first = geo.dma_table()[0].key
    assert forecast_store.dma_table(sid)[first].avails == 2_000_000
    assert forecast_store.unknown_share(sid) == {"dma": 10_000}
    assert forecast_store.zip_rows(sid, []) == {}


def test_a_published_version_never_changes_so_its_dma_table_can_be_cached(key):
    first = stage_and_publish(key)
    t1 = forecast_store.dma_table(first.series_id)
    stage_and_publish(key, scale=3)
    assert forecast_store.dma_table(first.series_id) == t1                # old version still readable, unchanged


def test_products_resolve_to_their_series_through_any_of_their_names(key):
    with get_connection() as conn:
        conn.execute("INSERT INTO products (name, family, buying_model) VALUES ('zz-test Resolve Me', 'Test', 'CPM')")
        p = payload_for(key, links=[ProductLink("zz-test Resolve Me", "baseline_proxy", "use as a floor")])
        loader.upsert_series_def(conn, p)
        res = loader.stage(conn, p, None)
        loader.publish(conn, res.series_id, "t")
        loader.link_products(conn, p)
    forecast_store.invalidate()
    ref = forecast_store.series_for_product(["zz-test Old Name", "zz-test Resolve Me"])
    assert ref.series_key == key and ref.relation == "baseline_proxy" and ref.link_note == "use as a floor"
    assert forecast_store.series_for_product(["zz-test nobody"]) is None
    assert ("zz-test Resolve Me", "baseline_proxy") in forecast_store.products_for_series()[key]


def test_the_admin_overview_lists_versions_and_batches(key):
    stage_and_publish(key); stage_and_publish(key, scale=2)
    data = forecast_store.data_overview()
    entry = next(s for s in data["series"] if s["series_key"] == key)
    assert [v["version"] for v in entry["versions"]] == [2, 1] and entry["versions"][0]["status"] == "current"
    assert isinstance(data["batches"], list)


# --------------------------------------------------------------------------------------------- the whole import, end to end

def full_export(path: Path, scale=1.0):
    """A small export that satisfies every rule: 210 canonical DMAs, breakdowns that reconcile."""
    dmas = [row(d.name, round((2000 - d.rank * 5) * 1000 * scale, -2)) for d in geo.dma_table()]
    total = sum(r[4] for r in dmas)
    unknown = round(total * 0.02, -2)
    dmas.append(row("Unknown", unknown))
    grand = total + unknown
    states = [row("California", round(grand * 0.5, -2)), row("Texas", round(grand * 0.3, -2)), row("Unknown", grand - round(grand * 0.5, -2) - round(grand * 0.3, -2))]
    cds = [row("CA Congressional District 12", round(grand * 0.6, -2)), row("TX Congressional District 01", grand - round(grand * 0.6, -2))]
    zips = [row("90210", round(grand * 0.4, -2)), row("77001", grand - round(grand * 0.4, -2))]
    devices = [row("Connected TV", round(grand * 0.8, -2)), row("Mobile", grand - round(grand * 0.8, -2))]
    pubs = [row("zz-test Publisher A", round(grand * 0.7, -2)), row("zz-test Publisher B", grand - round(grand * 0.7, -2))]
    return make_export(path, prefix="zz test export", dmas=dmas, states=states, cds=cds, zips=zips, devices=devices, publishers=pubs)


def write_manifest(tmp_path, key, extra_series=()):
    manifest = {"manifest_version": 1, "source_dir": ".", "series": [{
        "series_key": key, "title": f"zz-test {key}", "channel": "ctv", "avails_unit": "monthly", "reader": "madhive_zip",
        "source": "export.zip", "data_basis": "platform_reported", "exported_on": "2026-10-13",
        "products": [{"name": "zz-test Imported Product", "relation": "primary"}]}, *extra_series]}
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    return path


def test_a_full_import_stages_publishes_links_and_is_idempotent(key, tmp_path):
    with get_connection() as conn:
        conn.execute("INSERT INTO products (name, family, buying_model) VALUES ('zz-test Imported Product', 'Test', 'CPM')")
    full_export(tmp_path / "export.zip")
    manifest = write_manifest(tmp_path, key)
    out = service.run_import(manifest_path=manifest, requested_by="zz-test")
    assert [(o.series_key, o.status, o.version) for o in out] == [(key, "published", 1)] and out[0].errors == 0
    assert out[0].row_counts == {"dma": 210, "state": 2, "cd": 2, "zip": 2, "device": 2, "publisher": 2}
    forecast_store.invalidate()
    ref = forecast_store.series_for_product(["zz-test Imported Product"])
    assert ref.series_key == key and ref.version == 1 and ref.device_mix["connected_tv"] == pytest.approx(0.8, abs=0.01)
    again = service.run_import(manifest_path=manifest, requested_by="zz-test")
    assert [(o.status, o.version) for o in again] == [("unchanged", 1)] and status_of(key) == {1: "current"}


def test_a_changed_source_becomes_a_new_version_and_a_wild_one_needs_an_explicit_ok(key, tmp_path):
    full_export(tmp_path / "export.zip")
    manifest = write_manifest(tmp_path, key)
    service.run_import(manifest_path=manifest, requested_by="zz-test")
    full_export(tmp_path / "export.zip", scale=1.1)                      # +10%: fine
    out = service.run_import(manifest_path=manifest, requested_by="zz-test")
    assert (out[0].status, out[0].version) == ("published", 2) and status_of(key) == {1: "retired", 2: "current"}
    full_export(tmp_path / "export.zip", scale=3.0)                      # x3: blocked
    out = service.run_import(manifest_path=manifest, requested_by="zz-test")
    assert out[0].status == "invalid" and any(f.rule == "M002" for f in out[0].findings) and status_of(key) == {1: "retired", 2: "current"}
    out = service.run_import(manifest_path=manifest, requested_by="zz-test", accept_large_change=True)
    assert out[0].status == "published" and status_of(key)[3] == "current"


def test_an_invalid_series_is_reported_and_skipped_while_the_others_still_load(key, tmp_path):
    full_export(tmp_path / "export.zip")
    make_export(tmp_path / "broken.zip", dmas=[row("Gotham", 1000)])
    bad_key = key + "-bad"
    manifest = write_manifest(tmp_path, key, extra_series=[{
        "series_key": bad_key, "title": "zz-test bad", "channel": "ctv", "avails_unit": "monthly", "reader": "madhive_zip",
        "source": "broken.zip"}])
    out = {o.series_key: o for o in service.run_import(manifest_path=manifest, requested_by="zz-test")}
    assert out[key].status == "published" and out[bad_key].status == "invalid"
    assert any(f.rule == "G001" for f in out[bad_key].findings)
    assert status_of(bad_key) == {}                                      # nothing of the bad one was written


def test_a_missing_source_file_is_reported_without_stopping_the_run(key, tmp_path):
    full_export(tmp_path / "export.zip")
    manifest = write_manifest(tmp_path, key, extra_series=[{
        "series_key": key + "-gone", "title": "zz-test gone", "channel": "ctv", "avails_unit": "monthly", "reader": "madhive_zip", "source": "absent.zip"}])
    out = {o.series_key: o for o in service.run_import(manifest_path=manifest, requested_by="zz-test")}
    assert out[key].status == "published" and out[key + "-gone"].status == "error" and "not found" in out[key + "-gone"].note


def test_the_run_is_recorded_in_the_import_ledger(key, tmp_path):
    full_export(tmp_path / "export.zip")
    service.run_import(manifest_path=write_manifest(tmp_path, key), requested_by="zz-test")
    with get_connection() as conn:
        batch = conn.execute("SELECT * FROM forecast_import_batch WHERE requested_by = 'zz-test' ORDER BY batch_id DESC LIMIT 1").fetchone()
    assert batch["status"] == "succeeded" and batch["tool_version"].startswith("forecast-import") and batch["finished_at"] is not None
    assert batch["report"]["series"][0]["series_key"] == key and batch["sources"][0]["sha256"]


def test_rollback_through_the_service_and_the_cli_status(key, tmp_path):
    full_export(tmp_path / "export.zip")
    manifest = write_manifest(tmp_path, key)
    service.run_import(manifest_path=manifest, requested_by="zz-test")
    full_export(tmp_path / "export.zip", scale=1.2)
    service.run_import(manifest_path=manifest, requested_by="zz-test")
    assert service.rollback_series(key) == 1 and status_of(key) == {1: "current", 2: "retired"}
    assert any(r["series_key"] == key and r["version"] == 1 for r in service.status())


# --------------------------------------------------------------------------------------------- startup bootstrap

def test_the_bootstrap_never_raises_and_loads_only_series_never_imported(monkeypatch, db):
    calls = {}
    monkeypatch.setattr(service, "run_import", lambda **kw: calls.setdefault("kw", kw) and [])
    result = service.bootstrap()
    assert calls["kw"]["skip_existing_series"] is True and calls["kw"]["requested_by"] == "startup"
    assert "error" not in result
    monkeypatch.setattr(service, "run_import", lambda **kw: (_ for _ in ()).throw(RuntimeError("boom")))
    assert "boom" in service.bootstrap()["error"]                        # logged, reported, never raised


def test_only_one_instance_bootstraps_at_a_time(monkeypatch, db):
    called = []
    monkeypatch.setattr(service, "run_import", lambda **kw: called.append(1) or [])
    with get_connection() as holder:
        holder.execute("SELECT pg_advisory_lock(%s)", (service.BOOTSTRAP_LOCK_KEY,))
        try:
            result = service.bootstrap()
        finally:
            holder.execute("SELECT pg_advisory_unlock(%s)", (service.BOOTSTRAP_LOCK_KEY,))
            holder.commit()
    assert result.get("skipped") and called == []


def test_a_failed_bootstrap_does_not_leave_its_advisory_lock_on_a_pooled_connection(monkeypatch, db):
    """Any SQL error aborts the transaction; the unlock then could not run, and the connection went back to the pool still
    holding the session lock - every later bootstrap (any instance) then skipped for as long as the connection lived."""
    from app.services.forecast_import import product_seed
    monkeypatch.setattr(product_seed, "apply_to_db", lambda conn: conn.execute("SELECT 1/0"))
    result = service.bootstrap()
    assert "error" in result and "division" in result["error"].lower()          # the REAL error, not InFailedSqlTransaction
    with psycopg.connect(os.environ["DATABASE_URL"]) as other:                    # a different session
        got = other.execute("SELECT pg_try_advisory_lock(%s)", (service.BOOTSTRAP_LOCK_KEY,)).fetchone()[0]
        assert got is True, "the bootstrap lock is still held by a pooled connection"
        other.execute("SELECT pg_advisory_unlock(%s)", (service.BOOTSTRAP_LOCK_KEY,))


def test_the_next_bootstrap_after_a_failed_one_is_not_skipped(monkeypatch, db):
    from app.services.forecast_import import product_seed
    monkeypatch.setattr(product_seed, "apply_to_db", lambda conn: conn.execute("SELECT 1/0"))
    service.bootstrap()
    monkeypatch.undo()
    monkeypatch.setattr(service, "run_import", lambda **kw: [])
    assert "skipped" not in service.bootstrap()


def test_a_series_the_startup_import_skips_still_gets_its_products_linked(key, monkeypatch, db):
    """On a fresh database the products table is filled after the first import, so the first import linked nothing - and
    every later start skipped the series (it already has a version), so nothing ever would."""
    p = payload_for(key, links=[ProductLink("zz-test Late Product", "primary", "Baseline note")])
    with get_connection() as conn:
        loader.upsert_series_def(conn, p)
        res = loader.stage(conn, p, None)
        loader.publish(conn, res.series_id, "t")
        conn.commit()
        conn.execute("INSERT INTO products (name, family, buying_model) VALUES ('zz-test Late Product', 'Test', 'CPM')")   # exists only NOW
        conn.commit()
        assert conn.execute("SELECT count(*) AS n FROM forecast_series_product WHERE series_key = %s", (key,)).fetchone()["n"] == 0
    monkeypatch.setattr(service, "load_manifest", lambda path=None: ([p.spec], Path(".")))
    monkeypatch.setattr(service, "read_all", lambda specs, d: {p.spec.series_key: p})
    outcomes = service.run_import(only=[key], skip_existing_series=True)
    assert outcomes == []                                                          # nothing was imported ...
    with get_connection() as conn:
        link = conn.execute("SELECT sp.* FROM forecast_series_product sp JOIN products pr USING (product_key) "
                            "WHERE pr.name = 'zz-test Late Product'").fetchone()
    assert link is not None and link["series_key"] == key and link["note"] == "Baseline note"   # ... but the link is there


# --------------------------------------------------------------------------------------------- the product seed

def test_an_empty_products_table_is_seeded_by_the_catalog_before_any_link_is_made(monkeypatch):
    """apply_to_db used to return at once on an empty table, leaving the first import to find no products to link."""
    from types import SimpleNamespace
    from app.services.forecast_import import product_seed
    calls = []

    class Conn:
        rows: list = []

        def execute(self, sql, params=()):
            assert sql.startswith("SELECT name FROM products"), sql
            return SimpleNamespace(fetchall=lambda: [{"name": n} for n in self.rows])

    conn = Conn()
    monkeypatch.setattr(catalog, "invalidate_product_caches", lambda: calls.append("invalidate"))
    monkeypatch.setattr(catalog, "load_product_rows", lambda: (calls.append("load"), conn.rows.extend(["A", "B"]))[1])
    monkeypatch.setattr(catalog, "_load_forecast_seed_products", lambda: [])
    assert product_seed.apply_to_db(conn) == 0 and calls == ["invalidate", "load"]
    conn.rows = []
    monkeypatch.setattr(catalog, "load_product_rows", lambda: [])                      # a database blip: nothing could be seeded
    assert product_seed.apply_to_db(conn) == 0



def test_seed_products_are_added_once_without_touching_rows_an_admin_changed(db, monkeypatch):
    """Uses THROWAWAY seed products only: deleting a real catalog product would cascade away its forecast link."""
    from dataclasses import replace
    from app.services.forecast_import import product_seed
    with get_connection() as conn:
        anchor = conn.execute("SELECT name FROM products ORDER BY sort_order, id LIMIT 1").fetchone()
    if anchor is None:
        pytest.skip("empty products table: the first-use seed fills it")
    template = catalog.CATALOG[0]
    a, b, c = (replace(template, name=f"zz-test Seed {x}", base_rate=40.0) for x in "ABC")
    monkeypatch.setattr(catalog, "_load_forecast_seed_products", lambda: [(a, anchor["name"]), (b, a.name), (c, b.name)])
    with get_connection() as conn:
        conn.execute("INSERT INTO products (name, family, buying_model, base_rate, is_deleted) VALUES (%s, 'Test', 'CPM', 55, TRUE)", (c.name,))   # an admin edited AND removed C
        before = conn.execute("SELECT base_rate, is_deleted FROM products WHERE name = %s", (c.name,)).fetchone()
        assert product_seed.apply_to_db(conn) == 2 and product_seed.apply_to_db(conn) == 0                  # A and B added, once
        after = conn.execute("SELECT base_rate, is_deleted FROM products WHERE name = %s", (c.name,)).fetchone()
        assert after == before and float(after["base_rate"]) == 55 and after["is_deleted"] is True            # C untouched
        order = {r["name"]: r["sort_order"] for r in conn.execute("SELECT name, sort_order FROM products").fetchall()}
        assert order[a.name] == order[anchor["name"]] + 1 and order[b.name] == order[a.name] + 1             # placed after their anchors


def test_a_seed_product_that_replaces_an_older_one_hides_it_once_and_an_admin_who_brings_it_back_is_left_alone(db, monkeypatch):
    """eDigital Display / OLV replace the earlier display and pre-roll products: at the moment the new row is inserted the older
    one is hidden (a saved proposal still resolves it) and its category approvals are copied; the next start does not hide it again."""
    from dataclasses import replace
    from app.services import restrictions
    from app.services.forecast_import import product_seed
    template = catalog.CATALOG[0]
    old, new = (replace(template, name=f"zz-test {x}", base_rate=rate) for x, rate in (("Old Display", 9.0), ("New Display", 40.0)))
    monkeypatch.setattr(catalog, "_load_forecast_seed_products", lambda: [(new, None)])
    monkeypatch.setattr(catalog, "forecast_seed_replacements", lambda: {new.name: old.name})
    carried = []
    monkeypatch.setattr(restrictions, "inherit_product_approvals", lambda o, n, by: carried.append((o, n, by)) or 3)

    def hidden(conn, name):
        return conn.execute("SELECT is_deleted FROM products WHERE name = %s", (name,)).fetchone()["is_deleted"]

    with get_connection() as conn:
        conn.execute("INSERT INTO products (name, family, buying_model, base_rate, is_deleted) VALUES (%s, 'Test', 'CPM', 9, FALSE)", (old.name,))
        assert product_seed.apply_to_db(conn) == 1                           # the new product is inserted ...
        assert hidden(conn, old.name) is True and hidden(conn, new.name) is False    # ... and the older one is hidden, not deleted
    assert carried == [(old.name, new.name, "forecast seed")]                # its approvals went to the new one
    with get_connection() as conn:                                           # an admin brings the older product back
        conn.execute("UPDATE products SET is_deleted = FALSE WHERE name = %s", (old.name,))
        assert product_seed.apply_to_db(conn) == 0                           # the new one already exists: nothing to do ...
        assert hidden(conn, old.name) is False                               # ... and the admin's decision stands
    assert len(carried) == 1


def test_a_replaced_product_still_resolves_by_name_for_a_saved_proposal(db, monkeypatch):
    """A hidden product keeps its row, so a proposal that already names it keeps its line (and its old rate)."""
    from dataclasses import replace
    name = f"zz-test Hidden {uuid.uuid4().hex[:6]}"
    with get_connection() as conn:
        conn.execute("INSERT INTO products (name, family, buying_model, base_rate, is_deleted) VALUES (%s, 'Test', 'CPM', 9, TRUE)", (name,))
    catalog.invalidate_product_caches()
    found = catalog.by_name(name)
    assert found is not None and found.base_rate == 9
    assert name not in [p.name for p in catalog.effective_catalog()]         # ... while nothing new can pick it


# --------------------------------------------------------------------------------------------- the migration runner, isolated

def test_the_forecast_map_accepts_the_tiktok_method_and_still_rejects_any_other(db):
    """Migration 0003 widens the method check to 'tiktok'; the schema.sql baseline says the same, and the migration is re-runnable."""
    mine = "zz-test Tiktok map"
    with get_connection() as conn:
        conn.execute("DELETE FROM forecast_product_map WHERE product_name = %s", (mine,))
        try:
            for method, platform in (("tiktok", None), ("generic", None), ("platform", "roku")):
                conn.execute("INSERT INTO forecast_product_map (product_name, enabled, method, platform) VALUES (%s, TRUE, %s, %s) "
                             "ON CONFLICT (product_name) DO UPDATE SET method = EXCLUDED.method, platform = EXCLUDED.platform", (mine, method, platform))
            conn.execute("UPDATE forecast_product_map SET method = 'tiktok', platform = NULL WHERE product_name = %s", (mine,))
            assert conn.execute("SELECT method FROM forecast_product_map WHERE product_name = %s", (mine,)).fetchone()["method"] == "tiktok"
            with pytest.raises(psycopg.errors.CheckViolation):
                with conn.transaction():
                    conn.execute("UPDATE forecast_product_map SET method = 'magic' WHERE product_name = %s", (mine,))
            sql = next(m for m in migrations.discover() if m.version == "0003").sql
            conn.execute(sql)                                                  # run again over the already-widened check: nothing breaks
            conn.execute(sql)
            assert conn.execute("SELECT method FROM forecast_product_map WHERE product_name = %s", (mine,)).fetchone()["method"] == "tiktok"
        finally:
            conn.execute("DELETE FROM forecast_product_map WHERE product_name = %s", (mine,))


def test_an_admin_choice_of_the_tiktok_method_is_stored_and_read_back(db):
    from app.services import constanza_forecast_map as fm
    name = next(p.name for p in catalog.CATALOG if p.family == "Search")
    fm.invalidate()
    try:
        cfg = fm.set_override(name, {"enabled": True, "method": "tiktok"}, "zz-test")
        assert (cfg.enabled, cfg.method, cfg.platform, cfg.source) == (True, "tiktok", None, "admin")
        assert fm.resolve_forecast(name).method == "tiktok"
    finally:
        fm.clear_override(name)
        fm.invalidate()
    assert fm.resolve_forecast(name).method == "generic"


def test_the_migration_runner_applies_in_order_rolls_back_a_failure_and_detects_drift(db, tmp_path):
    suffix = uuid.uuid4().hex[:8]
    ledger = f"zz_test_ledger_{suffix}"
    t1, t2 = f"zz_test_a_{suffix}", f"zz_test_b_{suffix}"
    (tmp_path / "0001_make_a.sql").write_text(f"CREATE TABLE {t1} (id INT PRIMARY KEY);\nINSERT INTO {t1} VALUES (1);\n")
    (tmp_path / "0002_make_b_then_fail.sql").write_text(f"CREATE TABLE {t2} (id INT);\nSELECT 1/0;\n")
    (tmp_path / "0003_never.sql").write_text("SELECT 1;\n")
    try:
        assert migrations.apply_pending(tmp_path, dry_run=True, ledger_table=ledger) == ["0001", "0002", "0003"]
        with pytest.raises(psycopg.errors.DivisionByZero):
            migrations.apply_pending(tmp_path, ledger_table=ledger)
        states = {s.version: s.state for s in migrations.status(tmp_path, ledger_table=ledger)}
        assert states == {"0001": "applied", "0002": "pending", "0003": "pending"}          # 0001 is durable, 0002 rolled back whole
        with get_connection() as conn:
            assert conn.execute("SELECT to_regclass(%s) AS t", (t2,)).fetchone()["t"] is None    # its CREATE TABLE was undone too
        (tmp_path / "0002_make_b_then_fail.sql").write_text(f"CREATE TABLE {t2} (id INT);\n")
        assert migrations.apply_pending(tmp_path, ledger_table=ledger) == ["0002", "0003"]
        assert migrations.apply_pending(tmp_path, ledger_table=ledger) == []
        (tmp_path / "0001_make_a.sql").write_text(f"-- edited later\nCREATE TABLE {t1} (id INT PRIMARY KEY);\n")
        assert {s.version: s.state for s in migrations.status(tmp_path, ledger_table=ledger)}["0001"] == "drifted"
        assert migrations.apply_pending(tmp_path, ledger_table=ledger) == []                  # drift is reported, never re-run
    finally:
        with get_connection() as conn:
            conn.execute(f"DROP TABLE IF EXISTS {t1}, {t2}, {ledger}")


def test_migration_files_must_be_named_and_numbered_properly(tmp_path):
    (tmp_path / "0001_ok.sql").write_text("SELECT 1;")
    (tmp_path / "1_bad_name.sql").write_text("SELECT 1;")
    with pytest.raises(ValueError, match="NNNN_snake_case"):
        migrations.discover(tmp_path)
    (tmp_path / "1_bad_name.sql").unlink()
    (tmp_path / "0001_dupe.sql").write_text("SELECT 1;")
    with pytest.raises(ValueError, match="used by both"):
        migrations.discover(tmp_path)
    (tmp_path / "0001_dupe.sql").unlink()
    assert migrations.new_migration_path("add_thing", tmp_path).name == "0002_add_thing.sql"
    with pytest.raises(ValueError):
        migrations.new_migration_path("Bad Name", tmp_path)
    with pytest.raises(ValueError):
        migrations._check_ledger("x; DROP TABLE y")
