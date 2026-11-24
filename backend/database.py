"""PostgreSQL connection configuration and lightweight connection pooling.

Supports both `psycopg_pool.ConnectionPool` (when installed) and an internal
thread-safe LIFO connection pool fallback so concurrent tool executions via
`asyncio.gather` reuse warm PostgreSQL connections without TCP/TLS handshake
bottlenecks, while preserving exact `with get_connection() as conn:` commit /
rollback semantics across the codebase.
"""

from __future__ import annotations

import atexit
import os
import queue
import threading
import time
from pathlib import Path
from typing import Any

import psycopg
from dotenv import load_dotenv
from psycopg.pq import TransactionStatus

try:
    from psycopg_pool import ConnectionPool as _PsycopgConnectionPool
except ImportError:  # pragma: no cover - optional dependency
    _PsycopgConnectionPool = None

# Load the project's root .env file
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

_POOL_MAX_SIZE = max(0, int(os.getenv("DB_POOL_MAX_SIZE", "12")))
_IDLE_HEALTHCHECK_SECONDS = float(os.getenv("DB_POOL_IDLE_CHECK_SECONDS", "45"))


def get_connection_params() -> dict[str, Any]:
    """Build normalized psycopg connection kwargs from environment variables."""
    params: dict[str, Any] = {
        "dbname": os.getenv("PGDATABASE", "erp_ai"),
    }

    host = os.getenv("PGHOST")
    port = os.getenv("PGPORT")
    user = os.getenv("PGUSER")
    password = os.getenv("PGPASSWORD")

    if host:
        params["host"] = host
    if port:
        params["port"] = port
    if user:
        params["user"] = user
    if password:
        params["password"] = password

    return params


def _is_connection_usable(conn: psycopg.Connection[Any]) -> bool:
    return not conn.closed and not conn.broken


def _reset_for_pool(conn: psycopg.Connection[Any]) -> bool:
    """Ensure a connection is clean and IDLE before returning it to the pool."""
    if not _is_connection_usable(conn):
        return False
    try:
        tx_status = conn.info.transaction_status
        if tx_status in (TransactionStatus.INTRANS, TransactionStatus.INERROR):
            conn.rollback()
        elif tx_status != TransactionStatus.IDLE:
            return False
        if conn.autocommit:
            conn.autocommit = False
        return _is_connection_usable(conn)
    except Exception:
        return False


class _FallbackConnectionPool:
    """Lightweight thread-safe LIFO pool for psycopg v3 connections."""

    def __init__(self, max_size: int) -> None:
        self._max_size = max_size
        self._idle: queue.LifoQueue[tuple[float, psycopg.Connection[Any]]] = (
            queue.LifoQueue(maxsize=max_size)
        )
        self._closed = False
        self._lock = threading.Lock()

    def getconn(self) -> psycopg.Connection[Any]:
        now = time.monotonic()
        while not self._closed:
            try:
                returned_at, conn = self._idle.get_nowait()
            except queue.Empty:
                break

            if not _is_connection_usable(conn):
                self._safe_close(conn)
                continue

            if (now - returned_at) >= _IDLE_HEALTHCHECK_SECONDS:
                try:
                    conn.execute("SELECT 1")
                    if conn.info.transaction_status == TransactionStatus.INTRANS:
                        conn.commit()
                except Exception:
                    self._safe_close(conn)
                    continue

            return conn

        return psycopg.connect(**get_connection_params())

    def putconn(self, conn: psycopg.Connection[Any]) -> None:
        if self._closed or not _reset_for_pool(conn):
            self._safe_close(conn)
            return

        try:
            self._idle.put_nowait((time.monotonic(), conn))
        except queue.Full:
            self._safe_close(conn)

    def close(self) -> None:
        with self._lock:
            self._closed = True
        while True:
            try:
                _ts, conn = self._idle.get_nowait()
            except queue.Empty:
                break
            self._safe_close(conn)

    @staticmethod
    def _safe_close(conn: psycopg.Connection[Any]) -> None:
        try:
            conn.close()
        except Exception:
            pass


class _PooledConnection:
    """Proxy preserving `psycopg.Connection` context-manager and `.close()` contracts."""

    __slots__ = ("_conn", "_release", "_released")

    def __init__(self, conn: psycopg.Connection[Any], release_fn) -> None:
        self._conn = conn
        self._release = release_fn
        self._released = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)

    def __enter__(self) -> "_PooledConnection":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        if not self._released:
            try:
                if _is_connection_usable(self._conn):
                    tx = self._conn.info.transaction_status
                    if exc_type is not None:
                        if tx != TransactionStatus.IDLE:
                            self._conn.rollback()
                    elif not self._conn.autocommit:
                        if tx == TransactionStatus.INTRANS:
                            self._conn.commit()
                        elif tx == TransactionStatus.INERROR:
                            self._conn.rollback()
            finally:
                self.close()
        return False

    def close(self) -> None:
        if self._released:
            return
        self._released = True
        self._release(self._conn)


_pool_lock = threading.Lock()
_psycopg_pool: Any = None
_fallback_pool: _FallbackConnectionPool | None = None


def _get_pool():
    """Lazily initialize the active connection pool."""
    global _psycopg_pool, _fallback_pool

    if _POOL_MAX_SIZE <= 0:
        return None

    if _psycopg_pool is not None:
        return ("psycopg_pool", _psycopg_pool)
    if _fallback_pool is not None:
        return ("fallback", _fallback_pool)

    with _pool_lock:
        if _psycopg_pool is not None:
            return ("psycopg_pool", _psycopg_pool)
        if _fallback_pool is not None:
            return ("fallback", _fallback_pool)

        if _PsycopgConnectionPool is not None:
            params = get_connection_params()
            conninfo = psycopg.conninfo.make_conninfo(**params)
            _psycopg_pool = _PsycopgConnectionPool(
                conninfo=conninfo,
                min_size=1,
                max_size=_POOL_MAX_SIZE,
                open=True,
            )
            return ("psycopg_pool", _psycopg_pool)

        _fallback_pool = _FallbackConnectionPool(max_size=_POOL_MAX_SIZE)
        return ("fallback", _fallback_pool)


def close_pool() -> None:
    """Close all pooled connections cleanly on process shutdown."""
    global _psycopg_pool, _fallback_pool
    with _pool_lock:
        p_pool, _psycopg_pool = _psycopg_pool, None
        f_pool, _fallback_pool = _fallback_pool, None

    if p_pool is not None:
        try:
            p_pool.close()
        except Exception:
            pass
    if f_pool is not None:
        f_pool.close()


atexit.register(close_pool)


def get_connection() -> _PooledConnection | psycopg.Connection[Any]:
    """Acquire a PostgreSQL connection from the pool (or direct if disabled)."""
    pool_entry = _get_pool()
    if pool_entry is None:
        return psycopg.connect(**get_connection_params())

    kind, pool = pool_entry
    if kind == "psycopg_pool":
        raw_conn = pool.getconn()

        def _release_pg_pool(conn: psycopg.Connection[Any]) -> None:
            if _reset_for_pool(conn):
                try:
                    pool.putconn(conn)
                    return
                except Exception:
                    pass
            try:
                conn.close()
            except Exception:
                pass

        return _PooledConnection(raw_conn, _release_pg_pool)

    raw_conn = pool.getconn()
    return _PooledConnection(raw_conn, pool.putconn)


def get_db():
    """Provide a database connection for FastAPI dependency injection."""
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()
