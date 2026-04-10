"""
Schema versioning for SQLite databases.

Tracks applied migrations in a `schema_version` table and applies
pending migrations incrementally. Each migration is a (version, sql) tuple.

Usage:
    from app.utils.schema import apply_migrations

    MIGRATIONS = [
        (1, "CREATE TABLE IF NOT EXISTS foo (...);"),
        (2, "ALTER TABLE foo ADD COLUMN bar TEXT;"),
    ]

    async def _ensure_schema(conn):
        await apply_migrations(conn, MIGRATIONS)
"""
import logging

import aiosqlite

log = logging.getLogger(__name__)

_SCHEMA_VERSION_DDL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT DEFAULT (datetime('now'))
);
"""


async def apply_migrations(
    conn: aiosqlite.Connection,
    migrations: list[tuple[int, str]],
) -> int:
    """
    Apply any unapplied migrations to the database.

    Args:
        conn: Open aiosqlite connection.
        migrations: List of (version_number, sql_statement) tuples, sorted by version.

    Returns:
        Number of migrations applied.
    """
    await conn.executescript(_SCHEMA_VERSION_DDL)

    async with conn.execute(
        "SELECT COALESCE(MAX(version), 0) FROM schema_version"
    ) as cur:
        row = await cur.fetchone()
        current_version = row[0] if row else 0

    applied = 0
    for version, sql in sorted(migrations, key=lambda m: m[0]):
        if version <= current_version:
            continue
        log.info("Applying migration v%d", version)
        await conn.executescript(sql)
        await conn.execute(
            "INSERT INTO schema_version (version) VALUES (?)", (version,)
        )
        await conn.commit()
        applied += 1

    if applied:
        log.info("Applied %d migration(s), now at v%d", applied, version)

    return applied


def apply_migrations_sync(
    conn,
    migrations: list[tuple[int, str]],
) -> int:
    """Synchronous variant for non-async contexts (sqlite3.Connection)."""
    conn.executescript(_SCHEMA_VERSION_DDL)

    row = conn.execute(
        "SELECT COALESCE(MAX(version), 0) FROM schema_version"
    ).fetchone()
    current_version = row[0] if row else 0

    applied = 0
    for version, sql in sorted(migrations, key=lambda m: m[0]):
        if version <= current_version:
            continue
        log.info("Applying migration v%d", version)
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_version (version) VALUES (?)", (version,)
        )
        conn.commit()
        applied += 1

    return applied
