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


# --------------------------------------------------------------------------- busy is not down (offline, stubbed connects)


class _NoPool:
    """A pool that never has a connection free: every caller times out and has to ask whether the database is there."""

    def connection(self, timeout=None):
        raise db.PoolTimeout("every connection is busy")


class _Closable:
    def close(self):
        pass


def test_a_server_that_answers_too_many_clients_is_a_busy_pool_not_an_outage(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://x:y@127.0.0.1:1/none")
    def full(*a, **k):
        raise psycopg.errors.TooManyConnections("sorry, too many clients already")
    monkeypatch.setattr(db.psycopg, "connect", full)
    db._probe_reachable()                                         # returns: the server is alive, only full
    assert db._down_until == 0.0
    monkeypatch.setattr(db, "_PROBE_FRESH", 0.0)
    with pytest.raises(db.PoolTimeout):                           # the caller keeps waiting its budget and is told it was slow
        monkeypatch.setenv("DB_POOL_TIMEOUT", "0.3")
        db._PooledConnection(_NoPool()).__enter__()
    assert db._down_until == 0.0                                  # and the next callers are not failed instantly


def test_a_probe_that_just_answered_speaks_for_the_callers_queued_behind_it(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://x:y@127.0.0.1:1/none")
    calls = []
    monkeypatch.setattr(db.psycopg, "connect", lambda *a, **k: (calls.append(1), _Closable())[1])
    for _ in range(6):
        db._probe_reachable()
    assert len(calls) == 1                                        # not one direct connect per waiting caller
    monkeypatch.setattr(db, "_PROBE_FRESH", 0.0)
    db._probe_reachable()
    assert len(calls) == 2                                        # but an old answer is not trusted


def test_a_failed_probe_opens_the_window_once_and_later_callers_do_not_extend_it(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://x:y@127.0.0.1:1/none")
    attempts = []
    def refuse(*a, **k):
        attempts.append(1)
        raise psycopg.OperationalError("connection refused")
    monkeypatch.setattr(db.psycopg, "connect", refuse)
    with pytest.raises(psycopg.OperationalError, match="unavailable"):
        db._PooledConnection(_NoPool()).__enter__()
    until = db._down_until
    assert until > 0 and len(attempts) == 1
    for _ in range(4):
        time.sleep(0.01)
        with pytest.raises(psycopg.OperationalError, match="unavailable"):
            db._PooledConnection(_NoPool()).__enter__()
    assert db._down_until == until, "every caller that met the window pushed its end out: under steady traffic it never closes"
    assert len(attempts) == 1                                     # and none of them tried to connect


class _SleepyPool:
    """A pool with no connection free: the caller waits out the timeout it asked for inside the context manager, as the real one does."""

    def connection(self, timeout=None):
        class _Wait:
            def __enter__(self):
                time.sleep(timeout or 0)
                raise db.PoolTimeout("every connection is busy")

            def __exit__(self, *exc):
                return False
        return _Wait()


def test_a_waiting_caller_stops_waiting_when_another_caller_opens_the_outage_window(monkeypatch):
    """A caller whose own probe was skipped (one answered a moment ago) used to sit out the whole pool timeout, look nowhere, and
    fail as 'busy' (PoolTimeout) long after the database was known to be gone."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://x:y@127.0.0.1:1/none")
    monkeypatch.setenv("DB_POOL_TIMEOUT", "6")
    monkeypatch.setattr(db, "_FIRST_WAIT", 0.2)
    monkeypatch.setattr(db, "_probe_ok_at", time.monotonic())               # fresh: this caller's own probes return at once
    timer = threading.Timer(0.5, lambda: db._mark_down(psycopg.OperationalError("server gone")))
    timer.start()
    t0 = time.monotonic()
    try:
        with pytest.raises(psycopg.OperationalError, match="unavailable"):
            db._PooledConnection(_SleepyPool()).__enter__()
    finally:
        timer.cancel()
    assert time.monotonic() - t0 < 2.0, "it waited out its whole budget without looking again"


def test_a_busy_pool_is_still_waited_on_for_the_whole_budget_and_is_not_an_outage(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://x:y@127.0.0.1:1/none")
    monkeypatch.setenv("DB_POOL_TIMEOUT", "0.7")
    monkeypatch.setattr(db, "_FIRST_WAIT", 0.2)
    monkeypatch.setattr(db, "_probe_ok_at", time.monotonic())
    t0 = time.monotonic()
    with pytest.raises(db.PoolTimeout):
        db._PooledConnection(_SleepyPool()).__enter__()
    assert 0.6 < time.monotonic() - t0 < 1.6
    assert db._down_until == 0.0                                            # slow is not down


@pytest.mark.parametrize("budget", ["0.4", "1.0"])
def test_even_a_short_pool_budget_asks_whether_the_database_is_there(monkeypatch, budget):
    """With DB_POOL_TIMEOUT at or below the first slice the probe was skipped: a dead database was reported as 'busy' (PoolTimeout)
    every time and no window opened, so every caller waited the whole budget again."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://x:y@127.0.0.1:1/none")
    monkeypatch.setenv("DB_POOL_TIMEOUT", budget)
    def refuse(*a, **k):
        raise psycopg.OperationalError("connection refused")
    monkeypatch.setattr(db.psycopg, "connect", refuse)
    with pytest.raises(psycopg.OperationalError, match="unavailable"):
        db._PooledConnection(_SleepyPool()).__enter__()
    assert db._down_until > 0                                               # the window is open for everyone


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


# --------------------------------------------------------------------------- an outage AFTER the pool exists


class _Proxy:
    """A TCP forwarder to the real database that can be cut, to simulate the server vanishing under a live pool."""

    def __init__(self, host, port):
        import socket
        self.upstream = (host, port)
        self.listener = socket.socket()
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(50)
        self.port = self.listener.getsockname()[1]
        self.sockets = []
        self.alive = True
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        import socket
        while self.alive:
            try:
                client, _ = self.listener.accept()
                server = socket.create_connection(self.upstream)
            except Exception:  # noqa: BLE001
                return
            self.sockets += [client, server]
            threading.Thread(target=self._pipe, args=(client, server), daemon=True).start()
            threading.Thread(target=self._pipe, args=(server, client), daemon=True).start()

    @staticmethod
    def _pipe(a, b):
        try:
            while True:
                data = a.recv(65536)
                if not data:
                    break
                b.sendall(data)
        except Exception:  # noqa: BLE001
            pass
        for s in (a, b):
            try:
                s.close()
            except Exception:  # noqa: BLE001
                pass

    def cut(self):
        self.alive = False
        try:
            self.listener.close()
        except Exception:  # noqa: BLE001
            pass
        for s in self.sockets:
            try:
                s.shutdown(2)
                s.close()
            except Exception:  # noqa: BLE001
                pass


@pytest.mark.real_db
def test_a_database_outage_after_the_pool_exists_fails_fast_instead_of_stalling_every_call(real_url, monkeypatch):
    """The 'database is down' window used to guard only the first connect: once a pool existed, every call waited out the
    whole pool timeout (10 s) - threads piled up behind a database that was not coming back."""
    from psycopg.conninfo import conninfo_to_dict
    info = conninfo_to_dict(real_url)
    proxy = _Proxy(info.get("host") or "127.0.0.1", int(info.get("port") or 5432))
    try:
        monkeypatch.setenv("DATABASE_URL", real_url.replace(f":{info.get('port', 5432)}/", f":{proxy.port}/", 1)
                           if f":{info.get('port', 5432)}/" in real_url else real_url)
        monkeypatch.setenv("DB_POOL_TIMEOUT", "8")
        with db.get_connection() as conn:                       # the pool now exists, through the proxy
            assert conn.execute("SELECT 1 AS one").fetchone()["one"] == 1
        proxy.cut()
        t0 = time.monotonic()
        with pytest.raises(psycopg.OperationalError):
            with db.get_connection() as conn:
                conn.execute("SELECT 1")
        assert time.monotonic() - t0 < 5.0, "the first call after the outage must not wait out the whole pool timeout"
        t1 = time.monotonic()
        for _ in range(40):                                      # then every caller fails at once, for the whole window
            with pytest.raises(psycopg.OperationalError, match="unavailable"):
                with db.get_connection():
                    pass
        assert time.monotonic() - t1 < 0.5
    finally:
        proxy.cut()


# --------------------------------------------------------------------------- releasing a session advisory lock safely


class _Row(dict):
    pass


class _FakeConn:
    def __init__(self, unlock_works=True, rollback_works=True):
        self.unlock_works, self.rollback_works = unlock_works, rollback_works
        self.calls, self.closed = [], False

    def rollback(self):
        self.calls.append("rollback")
        if not self.rollback_works:
            raise psycopg.OperationalError("connection is gone")

    def execute(self, sql, params=()):
        self.calls.append(sql.split("(")[0].strip())
        if not self.unlock_works:
            raise psycopg.errors.InFailedSqlTransaction("current transaction is aborted")
        outer = self

        class Cur:
            def fetchone(self):
                return _Row(ok=True)
        return Cur()

    def commit(self):
        self.calls.append("commit")

    def close(self):
        self.closed = True


def test_a_lock_is_released_after_a_rollback_and_the_connection_is_kept():
    conn = _FakeConn()
    assert db.release_advisory_lock(conn, 42) is True
    assert conn.calls == ["rollback", "SELECT pg_advisory_unlock", "commit"] and conn.closed is False


def test_a_lock_that_cannot_be_released_closes_the_connection_so_the_server_drops_it():
    """Ending the session releases every lock it holds; a closed connection is replaced by the pool, never lent again."""
    for conn in (_FakeConn(unlock_works=False), _FakeConn(rollback_works=False)):
        assert db.release_advisory_lock(conn, 42) is False and conn.closed is True


@pytest.mark.real_db
def test_an_aborted_transaction_does_not_stop_the_lock_being_released(real_url):
    other = psycopg.connect(real_url, autocommit=True)
    try:
        with db.get_connection() as conn:
            conn.execute("SELECT pg_advisory_lock(%s)", (987654321,))
            with pytest.raises(psycopg.errors.DivisionByZero):
                conn.execute("SELECT 1/0")                        # the transaction is now aborted
            assert db.release_advisory_lock(conn, 987654321) is True
        assert other.execute("SELECT pg_try_advisory_lock(%s)", (987654321,)).fetchone()[0] is True
    finally:
        other.execute("SELECT pg_advisory_unlock_all()")
        other.close()
