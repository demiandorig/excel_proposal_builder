"""The shared bounded connection pool (app/db.py).

Offline tests pin the contract every fail-open path in the app depends on (a missing/unreachable database raises at
once, never hangs); the real-database tests (skipped when no database answers) pin pooling itself: connections are
reused, the pool is bounded, `with` commits/rolls back, and a caller that never uses `with` can still hand its
connection back.
"""
from __future__ import annotations

import threading
import time
import uuid

import psycopg
import pytest

from app import db


@pytest.fixture(autouse=True)
def _fresh_pool():
    db.reset_for_tests()
    yield
    db.reset_for_tests()


# --------------------------------------------------------------------------- offline contract


def test_missing_database_url_is_a_runtime_error(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        db.get_connection()


def test_an_unreachable_database_fails_fast_and_then_instantly(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://nobody:x@127.0.0.1:1/none")   # port 1: connection refused
    monkeypatch.setenv("DB_CONNECT_TIMEOUT", "2")
    t0 = time.monotonic()
    with pytest.raises(psycopg.OperationalError):
        with db.get_connection():
            pass
    assert time.monotonic() - t0 < 3.0
    t1 = time.monotonic()
    for _ in range(50):                                   # the 'database is down' window: no further connect attempts
        with pytest.raises(psycopg.OperationalError, match="unavailable"):
            db.get_connection()
    assert time.monotonic() - t1 < 0.5


def test_pool_settings_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("DB_POOL_MAX", "not-a-number")
    assert db._env_int("DB_POOL_MAX", 8, 1) == 8
    monkeypatch.setenv("DB_POOL_MAX", "3")
    assert db._env_int("DB_POOL_MAX", 8, 1) == 3
    monkeypatch.setenv("DB_POOL_MAX", "0")
    assert db._env_int("DB_POOL_MAX", 8, 1) == 1          # never below the minimum


def test_stats_without_a_pool():
    assert db.pool_stats()["open"] is False


# --------------------------------------------------------------------------- real database


@pytest.fixture
def real_url(monkeypatch):
    import os
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("no DATABASE_URL")
    try:
        psycopg.connect(url, connect_timeout=2).close()
    except Exception:
        pytest.skip("no reachable database")
    return url


@pytest.mark.real_db
def test_sequential_blocks_reuse_one_connection(real_url):
    pids = []
    for _ in range(5):
        with db.get_connection() as conn:
            pids.append(conn.execute("SELECT pg_backend_pid() AS pid").fetchone()["pid"])
    assert len(set(pids)) == 1                             # one pooled connection served all five
    stats = db.pool_stats()
    assert stats["open"] and stats["max_size"] >= 1


@pytest.mark.real_db
def test_rows_are_dicts_and_with_commits_or_rolls_back(real_url):
    table = f"_pool_test_{uuid.uuid4().hex[:10]}"
    with db.get_connection() as conn:
        conn.execute(f"CREATE TABLE {table} (id INT PRIMARY KEY, note TEXT)")
    try:
        with db.get_connection() as conn:
            conn.execute(f"INSERT INTO {table} VALUES (1, 'kept')")
        with pytest.raises(ZeroDivisionError):
            with db.get_connection() as conn:
                conn.execute(f"INSERT INTO {table} VALUES (2, 'lost')")
                1 / 0
        rows = db.fetch_all(f"SELECT id, note FROM {table} ORDER BY id")
        assert rows == [{"id": 1, "note": "kept"}]       # committed, and the failed block rolled back
        assert db.fetch_one(f"SELECT count(*) AS n FROM {table}")["n"] == 1
    finally:
        db.execute(f"DROP TABLE IF EXISTS {table}")


@pytest.mark.real_db
def test_the_pool_is_bounded_and_a_busy_pool_does_not_look_like_an_outage(real_url, monkeypatch):
    monkeypatch.setenv("DB_POOL_MAX", "2")
    monkeypatch.setenv("DB_POOL_MIN", "1")
    monkeypatch.setenv("DB_POOL_TIMEOUT", "0.4")
    release = threading.Event()
    holding = []

    def hold():
        with db.get_connection() as conn:
            conn.execute("SELECT 1")
            holding.append(1)
            release.wait(5)

    threads = [threading.Thread(target=hold) for _ in range(2)]
    for t in threads:
        t.start()
    deadline = time.monotonic() + 3
    while len(holding) < 2 and time.monotonic() < deadline:
        time.sleep(0.02)
    assert len(holding) == 2
    t0 = time.monotonic()
    with pytest.raises(psycopg.OperationalError):          # a third caller waits ~0.4 s, then gets PoolTimeout
        with db.get_connection():
            pass
    assert 0.3 < time.monotonic() - t0 < 2.0
    release.set()
    for t in threads:
        t.join(5)
    with db.get_connection() as conn:                      # and the very next call works: slow is not down
        assert conn.execute("SELECT 1 AS one").fetchone()["one"] == 1


@pytest.mark.real_db
def test_a_caller_without_with_can_lend_and_return_a_connection(real_url):
    conn = db.get_connection()
    try:
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended('pool-test', 0))")   # as drive_uploader does
    finally:
        conn.close()
    with db.get_connection() as c2:                        # the lock died with the rolled-back transaction
        got = c2.execute("SELECT pg_try_advisory_xact_lock(hashtextextended('pool-test', 0)) AS ok").fetchone()["ok"]
    assert got is True
    assert db.pool_stats().get("pool_available", 1) >= 0


@pytest.mark.real_db
def test_the_pool_can_be_switched_off(real_url, monkeypatch):
    monkeypatch.setenv("DB_POOL_DISABLE", "1")
    with db.get_connection() as conn:
        assert conn.execute("SELECT 1 AS one").fetchone()["one"] == 1
    assert db.pool_stats()["open"] is False
