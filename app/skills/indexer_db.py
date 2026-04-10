"""
indexer_db — SQLite tracking database for the document indexer.

Manages the indexed_files, document_metadata, and document_versions tables.
Provides the async context manager for opening the DB with schema migrations,
plus CRUD helpers for each table.
"""
import hashlib
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite

from app.config import settings

log = logging.getLogger(__name__)

# SQLite tracking DB lives alongside ChromaDB
_DB_PATH = str(Path(settings.chroma_path).parent / "indexer.db")

# ── Schema DDL ────────────────────────────────────────────────────────────────

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS indexed_files (
    path           TEXT PRIMARY KEY,
    last_modified  REAL NOT NULL,
    doc_id         TEXT NOT NULL,
    document_type  TEXT NOT NULL DEFAULT 'other',
    status         TEXT NOT NULL DEFAULT 'ok',
    indexed_at     TEXT,
    error_msg      TEXT
);
"""

_CREATE_METADATA_TABLE = """
CREATE TABLE IF NOT EXISTS document_metadata (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    filename        TEXT NOT NULL UNIQUE,
    filepath        TEXT NOT NULL,
    doc_type        TEXT NOT NULL,
    doc_date        TEXT,
    title           TEXT,
    attendees       TEXT,
    topics          TEXT,
    file_size       INTEGER,
    page_count      INTEGER,
    indexed_at      TEXT,
    full_text_chars INTEGER,
    doc_id          TEXT,
    extraction_method TEXT
);
"""

_CREATE_METADATA_INDEX = """
CREATE INDEX IF NOT EXISTS idx_dm_doc_id ON document_metadata(doc_id);
"""

_CREATE_METADATA_TYPE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_dm_doc_type ON document_metadata(doc_type);
"""

_CREATE_METADATA_DATE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_dm_doc_date ON document_metadata(doc_date);
"""

_CREATE_VERSIONS_TABLE = """
CREATE TABLE IF NOT EXISTS document_versions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id       TEXT NOT NULL,
    filename     TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    file_size    INTEGER,
    indexed_at   TEXT NOT NULL,
    change_summary TEXT DEFAULT ''
);
"""

_CREATE_VERSIONS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_dv_doc_id ON document_versions(doc_id);
"""


# ── DB context manager ────────────────────────────────────────────────────────


@asynccontextmanager
async def _open_db():
    """Async context manager that opens the indexer tracking DB and ensures schema."""
    Path(_DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(_DB_PATH) as conn:
        await conn.execute(_CREATE_TABLE)
        await conn.execute(_CREATE_METADATA_TABLE)
        await conn.execute(_CREATE_METADATA_INDEX)
        await conn.execute(_CREATE_METADATA_TYPE_INDEX)
        await conn.execute(_CREATE_METADATA_DATE_INDEX)
        await conn.execute(_CREATE_VERSIONS_TABLE)
        await conn.execute(_CREATE_VERSIONS_INDEX)
        # Migration: add extraction_method column if missing
        try:
            await conn.execute(
                "ALTER TABLE document_metadata ADD COLUMN extraction_method TEXT"
            )
        except Exception:
            pass  # column already exists
        await conn.commit()
        yield conn


# ── CRUD helpers ──────────────────────────────────────────────────────────────


async def _get_record(conn: aiosqlite.Connection, path_str: str) -> dict | None:
    conn.row_factory = aiosqlite.Row
    async with conn.execute(
        "SELECT * FROM indexed_files WHERE path = ?", (path_str,)
    ) as cur:
        row = await cur.fetchone()
    return dict(row) if row else None


async def _upsert_record(
    conn: aiosqlite.Connection,
    *,
    path_str: str,
    last_modified: float,
    doc_id: str,
    document_type: str,
    status: str,
    indexed_at: str,
    error_msg: str = "",
) -> None:
    await conn.execute(
        """
        INSERT OR REPLACE INTO indexed_files
          (path, last_modified, doc_id, document_type, status, indexed_at, error_msg)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (path_str, last_modified, doc_id, document_type, status, indexed_at, error_msg),
    )
    await conn.commit()


async def _upsert_metadata(
    conn: aiosqlite.Connection,
    *,
    filename: str,
    filepath: str,
    doc_type: str,
    doc_date: str | None,
    title: str | None,
    attendees: list[str],
    topics: list[str],
    file_size: int,
    page_count: int,
    indexed_at: str,
    full_text_chars: int,
    doc_id: str = "",
    extraction_method: str | None = None,
) -> None:
    await conn.execute(
        """
        INSERT OR REPLACE INTO document_metadata
          (filename, filepath, doc_type, doc_date, title, attendees, topics,
           file_size, page_count, indexed_at, full_text_chars, doc_id,
           extraction_method)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            filename,
            filepath,
            doc_type,
            doc_date,
            title,
            json.dumps(attendees) if attendees else None,
            json.dumps(topics) if topics else None,
            file_size,
            page_count,
            indexed_at,
            full_text_chars,
            doc_id,
            extraction_method,
        ),
    )
    await conn.commit()


async def _record_version(
    conn: aiosqlite.Connection,
    doc_id: str,
    filename: str,
    file_path: Path,
    indexed_at: str,
) -> None:
    """Record a version entry for the document, computing a content hash."""
    content_hash = hashlib.sha256(file_path.read_bytes()).hexdigest()

    # Check if this exact hash already exists (no change)
    async with conn.execute(
        "SELECT content_hash FROM document_versions "
        "WHERE doc_id = ? ORDER BY id DESC LIMIT 1",
        (doc_id,),
    ) as cur:
        prev = await cur.fetchone()

    if prev and prev[0] == content_hash:
        return  # no change — don't record duplicate version

    await conn.execute(
        """
        INSERT INTO document_versions
          (doc_id, filename, content_hash, file_size, indexed_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (doc_id, filename, content_hash, file_path.stat().st_size, indexed_at),
    )
    await conn.commit()


async def get_document_versions(doc_id: str) -> list[dict]:
    """Return version history for a document, newest first."""
    async with _open_db() as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            "SELECT * FROM document_versions WHERE doc_id = ? ORDER BY id DESC",
            (doc_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def query_metadata_db(
    *,
    doc_type: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """
    Query document_metadata with optional filters.

    Args:
        doc_type:  Filter by doc_type (e.g. "minutes", "policy").
        date_from: ISO date string — only docs on/after this date.
        date_to:   ISO date string — only docs on/before this date.
        limit:     Maximum rows to return.

    Returns list of dicts with all metadata fields.
    """
    clauses: list[str] = []
    params: list = []

    if doc_type:
        clauses.append("doc_type = ?")
        params.append(doc_type)
    if date_from:
        clauses.append("doc_date >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("doc_date <= ?")
        params.append(date_to)

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)

    results: list[dict] = []
    async with _open_db() as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            f"SELECT * FROM document_metadata {where} "
            f"ORDER BY doc_date DESC NULLS LAST, filename LIMIT ?",
            params,
        ) as cur:
            rows = await cur.fetchall()
        for row in rows:
            d = dict(row)
            d["attendees"] = json.loads(d["attendees"]) if d.get("attendees") else []
            d["topics"] = json.loads(d["topics"]) if d.get("topics") else []
            results.append(d)

    return results
