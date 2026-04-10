"""
Thread-based chat storage backed by SQLite.

Tables
──────
  threads              — chat thread metadata
  thread_participants  — who can see / participate in each thread
  thread_messages      — messages within a thread (user + agent turns)
"""
from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.config import settings

# ── DDL ────────────────────────────────────────────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS threads (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS thread_participants (
    thread_id TEXT NOT NULL REFERENCES threads(id),
    username  TEXT NOT NULL,
    added_at  TEXT NOT NULL,
    added_by  TEXT NOT NULL,
    PRIMARY KEY (thread_id, username)
);

CREATE TABLE IF NOT EXISTS thread_messages (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id TEXT NOT NULL REFERENCES threads(id),
    sender    TEXT NOT NULL,
    role      TEXT NOT NULL DEFAULT 'user',
    content   TEXT NOT NULL,
    ts        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tmsg_thread_ts ON thread_messages(thread_id, ts);
CREATE INDEX IF NOT EXISTS idx_tpart_user ON thread_participants(username);
"""


# ── DB helpers ────────────────────────────────────────────────────────────────���

def _db_path() -> str:
    return str(Path(settings.audit_log_path).parent / "threads.db")


def _open_db() -> sqlite3.Connection:
    path = _db_path()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_DDL)
    conn.commit()
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Thread CRUD ────────────────────────────────────────────────────────────────

def create_thread(
    name: str,
    created_by: str,
    participants: Optional[list[str]] = None,
) -> str:
    """Create a thread and add the creator + any extra participants. Returns thread id."""
    thread_id = str(uuid.uuid4())
    now = _now()
    conn = _open_db()
    try:
        conn.execute(
            "INSERT INTO threads (id, name, created_by, created_at) VALUES (?, ?, ?, ?)",
            (thread_id, name, created_by, now),
        )
        # Always add creator as participant
        all_participants = {created_by}
        if participants:
            all_participants.update(participants)
        for uname in all_participants:
            conn.execute(
                "INSERT OR IGNORE INTO thread_participants (thread_id, username, added_at, added_by) "
                "VALUES (?, ?, ?, ?)",
                (thread_id, uname, now, created_by),
            )
        conn.commit()
    finally:
        conn.close()
    return thread_id


def list_user_threads(username: str) -> list[dict]:
    """Return threads the user participates in, most-recent-message first."""
    conn = _open_db()
    try:
        rows = conn.execute(
            """
            SELECT t.id, t.name, t.created_by, t.created_at,
                   (SELECT COUNT(*) FROM thread_participants tp2 WHERE tp2.thread_id = t.id) AS participant_count,
                   (SELECT MAX(tm.ts) FROM thread_messages tm WHERE tm.thread_id = t.id) AS last_message_ts
            FROM threads t
            JOIN thread_participants tp ON tp.thread_id = t.id
            WHERE tp.username = ?
            ORDER BY COALESCE(last_message_ts, t.created_at) DESC
            """,
            (username,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_thread(thread_id: str) -> Optional[dict]:
    conn = _open_db()
    try:
        row = conn.execute("SELECT * FROM threads WHERE id = ?", (thread_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_participants(thread_id: str) -> list[str]:
    conn = _open_db()
    try:
        rows = conn.execute(
            "SELECT username FROM thread_participants WHERE thread_id = ? ORDER BY added_at",
            (thread_id,),
        ).fetchall()
        return [r["username"] for r in rows]
    finally:
        conn.close()


def is_participant(thread_id: str, username: str) -> bool:
    conn = _open_db()
    try:
        row = conn.execute(
            "SELECT 1 FROM thread_participants WHERE thread_id = ? AND username = ?",
            (thread_id, username),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def add_participant(thread_id: str, username: str, added_by: str) -> bool:
    """Add a participant. Returns True if added, False if already present."""
    conn = _open_db()
    try:
        cur = conn.execute(
            "INSERT OR IGNORE INTO thread_participants (thread_id, username, added_at, added_by) "
            "VALUES (?, ?, ?, ?)",
            (thread_id, username, _now(), added_by),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def remove_participant(thread_id: str, username: str) -> bool:
    """Remove a participant. Cannot remove the thread creator. Returns True if removed."""
    conn = _open_db()
    try:
        # Check if user is the creator
        row = conn.execute("SELECT created_by FROM threads WHERE id = ?", (thread_id,)).fetchone()
        if row and row["created_by"] == username:
            return False
        cur = conn.execute(
            "DELETE FROM thread_participants WHERE thread_id = ? AND username = ?",
            (thread_id, username),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def save_message(thread_id: str, sender: str, role: str, content: str) -> int:
    """Save a message and return its id."""
    conn = _open_db()
    try:
        cur = conn.execute(
            "INSERT INTO thread_messages (thread_id, sender, role, content, ts) VALUES (?, ?, ?, ?, ?)",
            (thread_id, sender, role, content, _now()),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_messages(thread_id: str, limit: int = 200) -> list[dict]:
    """Return messages for a thread, oldest first, up to limit."""
    conn = _open_db()
    try:
        rows = conn.execute(
            """
            SELECT id, thread_id, sender, role, content, ts
            FROM thread_messages
            WHERE thread_id = ?
            ORDER BY ts ASC, id ASC
            LIMIT ?
            """,
            (thread_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
