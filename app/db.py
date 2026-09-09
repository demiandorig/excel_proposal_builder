"""Small PostgreSQL connection helpers for the app's persistent data."""
from __future__ import annotations

import os
from typing import Any, Iterable

import psycopg
from psycopg.rows import dict_row


def get_connection() -> psycopg.Connection:
    """Open a connection to the provisioned PostgreSQL database."""
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required for persistent application data.")
    return psycopg.connect(database_url, row_factory=dict_row)


def fetch_all(sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
    with get_connection() as conn:
        return list(conn.execute(sql, tuple(params)).fetchall())


def fetch_one(sql: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
    with get_connection() as conn:
        return conn.execute(sql, tuple(params)).fetchone()


def execute(sql: str, params: Iterable[Any] = ()) -> None:
    with get_connection() as conn:
        conn.execute(sql, tuple(params))