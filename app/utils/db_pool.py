"""
Lightweight async SQLite connection pool with resilience utilities.

aiosqlite opens/closes a connection per context manager. For batch operations
(like indexing 50 documents), the overhead adds up. This module provides a
simple pool that reuses connections, plus retry logic for locked/corrupt databases.

Usage:
    from app.utils.db_pool import get_pool, resilient_execute

    pool = get_pool("/path/to/db.sqlite")

    async with pool.acquire() as conn:
        await resilient_execute(conn, "SELECT ...")
"""
import asyncio
import logging
import sqlite3
from contextlib import asynccontextmanager

import aiosqlite

log = logging.getLogger(__name__)

_pools: dict[str, "ConnectionPool"] = {}


class ConnectionPool:
    """Simple bounded async connection pool for aiosqlite."""

    def __init__(self, db_path: str, max_size: int = 5) -> None:
        self._db_path = db_path
        self._max_size = max_size
        self._pool: asyncio.Queue[aiosqlite.Connection] = asyncio.Queue(maxsize=max_size)
        self._created = 0
        self._lock = asyncio.Lock()

    async def _create_connection(self) -> aiosqlite.Connection:
        conn = await aiosqlite.connect(self._db_path)
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA busy_timeout=5000")
        return conn

    @asynccontextmanager
    async def acquire(self):
        """Acquire a connection from the pool."""
        conn = None
        # Try to get from pool first
        try:
            conn = self._pool.get_nowait()
        except asyncio.QueueEmpty:
            async with self._lock:
                if self._created < self._max_size:
                    conn = await self._create_connection()
                    self._created += 1

        if conn is None:
            # Pool full — wait for one to be returned
            conn = await self._pool.get()

        try:
            yield conn
        finally:
            # Return to pool (don't close)
            try:
                self._pool.put_nowait(conn)
            except asyncio.QueueFull:
                await conn.close()
                async with self._lock:
                    self._created -= 1

    async def close_all(self) -> None:
        """Close all pooled connections."""
        while not self._pool.empty():
            try:
                conn = self._pool.get_nowait()
                await conn.close()
            except asyncio.QueueEmpty:
                break
        async with self._lock:
            self._created = 0


def get_pool(db_path: str, max_size: int = 5) -> ConnectionPool:
    """Get or create a connection pool for the given database path."""
    if db_path not in _pools:
        _pools[db_path] = ConnectionPool(db_path, max_size)
    return _pools[db_path]


# ---------------------------------------------------------------------------
# Database resilience helpers
# ---------------------------------------------------------------------------

_LOCKED_RETRY_DELAY = 1.0
_LOCKED_MAX_RETRIES = 3


async def resilient_execute(conn: aiosqlite.Connection, sql: str, params=None, retries: int = _LOCKED_MAX_RETRIES):
    """Execute SQL with retry on database-locked errors."""
    for attempt in range(retries):
        try:
            if params:
                return await conn.execute(sql, params)
            return await conn.execute(sql)
        except (sqlite3.OperationalError, Exception) as exc:
            if "locked" in str(exc).lower() and attempt < retries - 1:
                log.warning("Database locked, retrying in %.1fs (attempt %d/%d)", _LOCKED_RETRY_DELAY, attempt + 1, retries)
                await asyncio.sleep(_LOCKED_RETRY_DELAY)
            elif "corrupt" in str(exc).lower() or "malformed" in str(exc).lower():
                log.critical("DATABASE CORRUPTION DETECTED: %s", exc)
                raise
            else:
                raise
