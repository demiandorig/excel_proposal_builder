"""PostgreSQL access for the app's persistent data.

Every module opens connections the same way, `with get_connection() as conn:` (commit on success, roll back on an
error). Behind that call is ONE small, bounded connection pool per process instead of a brand-new connection for every
operation. A connection costs a TCP + TLS + auth round trip and a server process; the forecast endpoints alone can make
several lookups per request, and Autoscale runs several instances, so the pool keeps the number of server connections
small and predictable (`DB_POOL_MAX` per process) and removes the connect latency from the request path.

Behaviour that callers rely on and that is preserved:

  * `get_connection()` raises `RuntimeError` when DATABASE_URL is not set and `psycopg.OperationalError` when the
    database cannot be reached. It fails FAST: the first failed attempt records a short "database is down" window
    (`DB_RETRY_AFTER`, 5 s) during which further calls raise at once instead of each waiting out a connect timeout.
    The many fail-open paths (offline product list, forecast defaults, ...) depend on that, as do the tests. That also holds
    once the pool exists: a pool that has lost its connections and cannot make new ones is told apart from a merely busy
    one with a single direct connect, and then every caller fails at once for the same window.
  * `with get_connection() as conn:` yields a normal psycopg connection (rows come back as dicts).
  * A caller that does not use `with` (the Drive folder lock) may call `conn.execute(...)` and `conn.close()`; the
    wrapper lends a pooled connection on first use and `close()` rolls back and returns it.

Environment (all optional):
  DB_POOL_MAX          maximum connections per process            (default 8)
  DB_POOL_MIN          connections kept open when idle            (default 1)
  DB_POOL_TIMEOUT      seconds to wait for a free connection      (default 10)
  DB_CONNECT_TIMEOUT   seconds to wait for a new connection       (default 8)
  DB_POOL_MAX_IDLE     close an idle extra connection after (s)   (default 300)
  DB_POOL_MAX_LIFETIME recycle a connection after (s)             (default 1800)
  DB_POOL_DISABLE      "1" -> no pool, one connection per call (the previous behaviour)
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Iterable, Optional

import psycopg
from psycopg.rows import dict_row

try:  # listed in requirements.txt; an environment that has not installed it yet keeps the old per-call behaviour
    from psycopg_pool import ConnectionPool, PoolTimeout
    _HAS_POOL = True
except ImportError:  # pragma: no cover - only on a stale environment
    ConnectionPool = None  # type: ignore[assignment]
    PoolTimeout = ()  # type: ignore[assignment,misc]   # `except ()` matches nothing
    _HAS_POOL = False

log = logging.getLogger(__name__)

APPLICATION_NAME = "proposal-builder"
DB_RETRY_AFTER = 5.0   # seconds a failed connect keeps later calls from trying again

_lock = threading.Lock()
_probe_lock = threading.Lock()      # one direct "is the database reachable?" connect at a time
_pool: Optional["ConnectionPool"] = None
_pool_pid: Optional[int] = None
_down_until = 0.0
_down_error: Optional[str] = None
_probe_ok_at = 0.0                  # when the database last answered a direct probe
_FIRST_WAIT = 2.0                   # seconds to wait for a pooled connection before asking "is the database even there?"
_PROBE_FRESH = 2.0                  # a probe that answered this recently speaks for every caller that is waiting


class _RecentlyDown(psycopg.OperationalError):
    """Raised by the fail-fast window itself. The outage is already recorded: whoever catches this must not record it again
    (doing so would push the end of the window out every time a caller sees it, and under steady traffic it would never end)."""


class _ProbeFailed(psycopg.OperationalError):
    """The direct reachability probe failed, and it opened the fail-fast window itself."""


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    try:
        return max(minimum, int(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is required for persistent application data.")
    return url


def _connect_kwargs() -> dict:
    return {
        "row_factory": dict_row,
        "connect_timeout": _env_int("DB_CONNECT_TIMEOUT", 8, 1),
        "application_name": APPLICATION_NAME,
    }


def _pool_disabled() -> bool:
    return (not _HAS_POOL) or os.environ.get("DB_POOL_DISABLE", "").strip() in ("1", "true", "yes")


def _mark_down(exc: BaseException) -> None:
    global _down_until, _down_error
    _down_until = time.monotonic() + DB_RETRY_AFTER
    _down_error = f"{type(exc).__name__}: {exc}".strip()


def _raise_if_recently_down() -> None:
    if _down_until and time.monotonic() < _down_until:
        raise _RecentlyDown(f"database unavailable (retrying shortly): {_down_error}")


def _probe_reachable() -> None:
    """One direct connect with a short timeout. Raises OperationalError (and opens the 'database is down' window) when it
    fails. Serialised, and re-checked under the lock, so one failed probe fails every waiting caller instead of each
    making its own; a probe that answered within the last couple of seconds answers for the callers queued behind it, so a
    busy pool does not become a queue of direct connects. A server that answers "too many clients" is alive, only full:
    that is a busy pool, never an outage."""
    global _probe_ok_at
    with _probe_lock:
        _raise_if_recently_down()
        if time.monotonic() - _probe_ok_at < _PROBE_FRESH:
            return
        try:
            psycopg.connect(database_url(), **{**_connect_kwargs(), "connect_timeout": 2}).close()
        except psycopg.errors.TooManyConnections:
            _probe_ok_at = time.monotonic()
            return
        except Exception as exc:  # noqa: BLE001
            _mark_down(exc)
            raise _ProbeFailed(f"database unavailable: {type(exc).__name__}: {exc}".strip()) from exc
        _probe_ok_at = time.monotonic()


def _get_pool() -> "ConnectionPool":
    """The process-wide pool, created on first use. Raises (fast) when the database cannot be reached."""
    global _pool, _pool_pid
    url = database_url()
    if _pool is not None and _pool_pid == os.getpid() and not _pool.closed:
        _raise_if_recently_down()
        return _pool
    with _lock:
        if _pool is not None and _pool_pid == os.getpid() and not _pool.closed:
            return _pool
        _raise_if_recently_down()
        # One direct connect first: a refused / unreachable database errors immediately here, whereas a pool would
        # keep retrying in the background until its open() timeout. It also proves the credentials before we commit
        # to holding connections.
        try:
            probe = psycopg.connect(url, **_connect_kwargs())
        except Exception as exc:  # noqa: BLE001
            _mark_down(exc)
            raise
        probe.close()
        max_size = _env_int("DB_POOL_MAX", 8, 1)
        min_size = min(_env_int("DB_POOL_MIN", 1, 0), max_size)
        pool = ConnectionPool(
            url,
            min_size=min_size,
            max_size=max_size,
            kwargs=_connect_kwargs(),
            open=False,
            name=APPLICATION_NAME,
            timeout=_env_float("DB_POOL_TIMEOUT", 10.0),
            max_idle=_env_float("DB_POOL_MAX_IDLE", 300.0),
            max_lifetime=_env_float("DB_POOL_MAX_LIFETIME", 1800.0),
            reconnect_timeout=30.0,
            check=ConnectionPool.check_connection,   # a connection the server dropped while idle is replaced, not lent
        )
        try:
            pool.open(wait=True, timeout=_env_float("DB_CONNECT_TIMEOUT", 8.0))
        except Exception as exc:  # noqa: BLE001
            _mark_down(exc)
            pool.close()
            raise psycopg.OperationalError(f"could not open the connection pool: {exc}") from exc
        _pool, _pool_pid = pool, os.getpid()
        return pool


class _PooledConnection:
    """What get_connection() returns. As a context manager it is exactly `with pool.connection() as conn:` (commit on
    success, roll back on an error, connection returned to the pool). Used without `with`, the first method call lends
    a connection and `close()` hands it back."""

    __slots__ = ("_pool", "_cm", "_conn")

    def __init__(self, pool: "ConnectionPool") -> None:
        self._pool = pool
        self._cm = None
        self._conn = None

    def __enter__(self) -> psycopg.Connection:
        full = _env_float("DB_POOL_TIMEOUT", 10.0)
        deadline = time.monotonic() + full
        wait = min(full, _FIRST_WAIT)
        try:
            while True:
                try:
                    self._cm = self._pool.connection(timeout=wait)
                    return self._cm.__enter__()
                except PoolTimeout:
                    # No connection yet. Busy, or is the database gone? One direct connect tells them apart (and when it IS gone,
                    # opens the fail-fast window for every caller at once): always asked at least once, however short the budget.
                    # A busy pool keeps waiting for the rest of its budget, as before - in slices, so a window another caller
                    # opens while this one waits ends this wait too.
                    _probe_reachable()
                    left = deadline - time.monotonic()
                    if left <= 0.05:
                        raise
                    wait = max(0.1, min(_FIRST_WAIT, left))
        except PoolTimeout:
            raise          # every connection is busy: slow, not down - do not make the next callers fail instantly
        except (_RecentlyDown, _ProbeFailed):
            raise          # the outage is already on record; recording it again would extend the window for everyone
        except psycopg.OperationalError as exc:
            _mark_down(exc)
            raise

    def __exit__(self, exc_type, exc, tb):
        cm, self._cm = self._cm, None
        return cm.__exit__(exc_type, exc, tb) if cm is not None else False

    def _lend(self) -> psycopg.Connection:
        if self._conn is None:
            self._conn = self._pool.getconn()
        return self._conn

    def close(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            try:
                conn.rollback()
            except Exception:  # noqa: BLE001
                pass
            self._pool.putconn(conn)

    def __getattr__(self, name: str) -> Any:          # execute / cursor / commit / rollback / transaction / ...
        return getattr(self._lend(), name)


def get_connection():
    """A connection to the provisioned PostgreSQL database; use it as `with get_connection() as conn:`."""
    if _pool_disabled():
        _raise_if_recently_down()
        try:
            return psycopg.connect(database_url(), **_connect_kwargs())
        except psycopg.OperationalError as exc:
            _mark_down(exc)
            raise
    return _PooledConnection(_get_pool())


def release_advisory_lock(conn, key: int) -> bool:
    """Release a session-level advisory lock this connection holds, even when the work that took it failed and left the
    transaction aborted (an aborted transaction cannot run the unlock, and a pooled connection would then go back to the
    pool still holding it, blocking every later holder). When it cannot be released cleanly the connection is closed:
    ending the session drops every lock it held, and the pool replaces a closed connection instead of lending it again."""
    try:
        conn.rollback()
        row = conn.execute("SELECT pg_advisory_unlock(%s) AS ok", (key,)).fetchone()
        conn.commit()
        if row is not None and (row["ok"] if isinstance(row, dict) else row[0]):
            return True
    except Exception:  # noqa: BLE001
        log.warning("could not release advisory lock %s cleanly; closing the connection", key, exc_info=True)
    try:
        conn.close()
    except Exception:  # noqa: BLE001
        pass
    return False


def close_pool() -> None:
    """Close the pool (application shutdown, tests). The next get_connection() builds a fresh one."""
    global _pool, _pool_pid
    with _lock:
        pool, _pool, _pool_pid = _pool, None, None
    if pool is not None:
        try:
            pool.close(timeout=5.0)
        except Exception:  # noqa: BLE001
            log.debug("closing the connection pool failed", exc_info=True)


def reset_for_tests() -> None:
    """Forget the pool and the 'database is down' memory (tests that change DATABASE_URL)."""
    global _down_until, _down_error, _probe_ok_at
    close_pool()
    _down_until, _down_error, _probe_ok_at = 0.0, None, 0.0


def pool_stats() -> dict:
    """Pool size / usage for diagnostics (an empty dict when no pool exists)."""
    pool = _pool
    if pool is None or pool.closed:
        return {"enabled": not _pool_disabled(), "open": False}
    stats = dict(pool.get_stats())
    stats.update({"enabled": True, "open": True, "min_size": pool.min_size, "max_size": pool.max_size})
    return stats


def fetch_all(sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
    with get_connection() as conn:
        return list(conn.execute(sql, tuple(params)).fetchall())


def fetch_one(sql: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
    with get_connection() as conn:
        return conn.execute(sql, tuple(params)).fetchone()


def execute(sql: str, params: Iterable[Any] = ()) -> None:
    with get_connection() as conn:
        conn.execute(sql, tuple(params))
