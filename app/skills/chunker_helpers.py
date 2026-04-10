"""
Chunker database operations — SQLite/aiosqlite CRUD for the chunks table.

Extracted from chunker.py to keep each module under 500 lines.
"""
import logging

import aiosqlite

log = logging.getLogger(__name__)

# ── Schema DDL ──────────────────────────────────────────────────────────────

_CREATE_CHUNKS_TABLE = """
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id TEXT NOT NULL,
    chunk_type TEXT NOT NULL,
    parent_id INTEGER,
    section_title TEXT,
    content TEXT NOT NULL,
    token_count INTEGER,
    page_number INTEGER,
    position_in_doc INTEGER,
    embedding_id TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (parent_id) REFERENCES chunks(id)
);
"""

_CREATE_CHUNKS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_chunks_doc_id ON chunks(doc_id);
"""

_CREATE_CHUNKS_PARENT_INDEX = """
CREATE INDEX IF NOT EXISTS idx_chunks_parent_id ON chunks(parent_id);
"""


async def init_chunks_db(db_path: str) -> None:
    """Create the chunks table if it doesn't exist."""
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(_CREATE_CHUNKS_TABLE)
        await conn.execute(_CREATE_CHUNKS_INDEX)
        await conn.execute(_CREATE_CHUNKS_PARENT_INDEX)
        await conn.commit()


async def delete_doc_chunks(db_path: str, doc_id: str) -> int:
    """Delete all chunks for a document. Returns count deleted."""
    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute(
            "DELETE FROM chunks WHERE doc_id = ?", (doc_id,)
        )
        await conn.commit()
        return cursor.rowcount


async def store_chunks(db_path: str, chunked) -> None:
    """
    Store parent and child chunks in the database.
    Sets parent_id on children after parents are inserted.
    """
    async with aiosqlite.connect(db_path) as conn:
        # Delete existing chunks for this doc
        await conn.execute("DELETE FROM chunks WHERE doc_id = ?", (chunked.doc_id,))

        # Insert parents first, collect their IDs by section title
        parent_ids: dict[str, int] = {}
        for parent in chunked.parents:
            cursor = await conn.execute(
                """INSERT INTO chunks (doc_id, chunk_type, parent_id, section_title,
                   content, token_count, page_number, position_in_doc, embedding_id)
                   VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?)""",
                (parent.doc_id, "parent", parent.section_title, parent.content,
                 parent.token_count, parent.page_number, parent.position_in_doc,
                 parent.embedding_id),
            )
            parent_ids[parent.section_title] = cursor.lastrowid
            parent.db_id = cursor.lastrowid

        # Insert children with parent_id
        for child in chunked.children:
            pid = parent_ids.get(child.section_title)
            cursor = await conn.execute(
                """INSERT INTO chunks (doc_id, chunk_type, parent_id, section_title,
                   content, token_count, page_number, position_in_doc, embedding_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (child.doc_id, "child", pid, child.section_title, child.content,
                 child.token_count, child.page_number, child.position_in_doc,
                 child.embedding_id),
            )
            child.db_id = cursor.lastrowid

        await conn.commit()

    log.info(
        "Stored %d parents + %d children for doc_id=%s",
        len(chunked.parents), len(chunked.children), chunked.doc_id,
    )


async def get_parent_chunk(db_path: str, parent_id: int) -> dict | None:
    """Retrieve a parent chunk by its ID."""
    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            "SELECT * FROM chunks WHERE id = ? AND chunk_type = 'parent'",
            (parent_id,),
        ) as cursor:
            row = await cursor.fetchone()
        return dict(row) if row else None


async def get_parent_by_child_id(db_path: str, child_id: int) -> dict | None:
    """Retrieve the parent chunk for a given child chunk ID."""
    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            """SELECT p.* FROM chunks p
               JOIN chunks c ON c.parent_id = p.id
               WHERE c.id = ? AND p.chunk_type = 'parent'""",
            (child_id,),
        ) as cursor:
            row = await cursor.fetchone()
        return dict(row) if row else None


async def get_parents_by_doc(db_path: str, doc_id: str) -> list[dict]:
    """Get all parent chunks for a document, ordered by position."""
    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            "SELECT * FROM chunks WHERE doc_id = ? AND chunk_type = 'parent' ORDER BY position_in_doc",
            (doc_id,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def drop_chunks_table(db_path: str) -> None:
    """Drop and recreate the chunks table."""
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute("DROP TABLE IF EXISTS chunks")
        await conn.execute(_CREATE_CHUNKS_TABLE)
        await conn.execute(_CREATE_CHUNKS_INDEX)
        await conn.execute(_CREATE_CHUNKS_PARENT_INDEX)
        await conn.commit()
