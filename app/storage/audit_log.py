"""
Append-only audit log backed by SQLite (aiosqlite for async callers,
sqlite3 for sync callers such as pending_changes).

Design constraint: NO UPDATE or DELETE operations are ever issued against
log tables.  Every write is a plain INSERT.

Tables
──────
  audit_log              — legacy catch-all (backward compat)
  llm_calls              — one row per LLM API call
  document_ops           — indexing, deletion events
  auth_events            — logins, failures, token issuance
  state_transitions      — wake ↔ dream transitions
  dream_cycles           — one row per completed dream-worker run
  pending_change_actions — propose / approve / reject events
  funding_opportunities  — scraped/RSS funding entries (UPDATE allowed for notified flag)
  page_hashes            — content hashes for page-change detection (UPDATE allowed)
"""
from __future__ import annotations

import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

import aiosqlite

from app.config import settings

# ── DDL ────────────────────────────────────────────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS audit_log (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      TEXT    NOT NULL,
    actor   TEXT    NOT NULL,
    action  TEXT    NOT NULL,
    target  TEXT,
    detail  TEXT
);

CREATE TABLE IF NOT EXISTS llm_calls (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                TEXT    NOT NULL,
    user_id           TEXT,
    role              TEXT,
    skill             TEXT,
    prompt_tokens     INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    latency_ms        INTEGER NOT NULL DEFAULT 0,
    state             TEXT
);

CREATE TABLE IF NOT EXISTS document_ops (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL,
    user_id   TEXT,
    file_path TEXT NOT NULL,
    operation TEXT NOT NULL,
    status    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS auth_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT NOT NULL,
    username   TEXT NOT NULL,
    event_type TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS state_transitions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT NOT NULL,
    from_state TEXT NOT NULL,
    to_state   TEXT NOT NULL,
    trigger    TEXT
);

CREATE TABLE IF NOT EXISTS dream_cycles (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT NOT NULL,
    start_ts        TEXT NOT NULL,
    end_ts          TEXT NOT NULL,
    tasks_completed INTEGER NOT NULL DEFAULT 0,
    tasks_failed    INTEGER NOT NULL DEFAULT 0,
    summary         TEXT
);

CREATE TABLE IF NOT EXISTS pending_change_actions (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       TEXT NOT NULL,
    filename TEXT NOT NULL,
    action   TEXT NOT NULL,
    user_id  TEXT,
    reason   TEXT
);

CREATE TABLE IF NOT EXISTS funding_opportunities (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT    NOT NULL,
    guid        TEXT    NOT NULL UNIQUE,
    funder      TEXT    NOT NULL,
    title       TEXT    NOT NULL,
    link        TEXT    NOT NULL,
    summary     TEXT    NOT NULL,
    published   TEXT,
    deadline    TEXT,
    amount      TEXT,
    eligibility TEXT,
    relevance   TEXT    NOT NULL,
    feed_url    TEXT    NOT NULL,
    notified    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS page_hashes (
    url           TEXT PRIMARY KEY,
    content_hash  TEXT NOT NULL,
    last_checked  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS learned_skills (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT    NOT NULL,
    skill_name      TEXT    NOT NULL,
    trigger_pattern TEXT    NOT NULL,
    description     TEXT    NOT NULL,
    source          TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS shared_messages (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT    NOT NULL,
    username     TEXT    NOT NULL,
    display_name TEXT    NOT NULL,
    role         TEXT    NOT NULL,
    msg_type     TEXT    NOT NULL,
    content      TEXT    NOT NULL
);

CREATE TRIGGER IF NOT EXISTS no_update_audit_log
    BEFORE UPDATE ON audit_log BEGIN
    SELECT RAISE(FAIL, 'audit_log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS no_delete_audit_log
    BEFORE DELETE ON audit_log BEGIN
    SELECT RAISE(FAIL, 'audit_log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS no_update_auth_events
    BEFORE UPDATE ON auth_events BEGIN
    SELECT RAISE(FAIL, 'auth_events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS no_delete_auth_events
    BEFORE DELETE ON auth_events BEGIN
    SELECT RAISE(FAIL, 'auth_events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS no_update_document_ops
    BEFORE UPDATE ON document_ops BEGIN
    SELECT RAISE(FAIL, 'document_ops is append-only'); END;
CREATE TRIGGER IF NOT EXISTS no_delete_document_ops
    BEFORE DELETE ON document_ops BEGIN
    SELECT RAISE(FAIL, 'document_ops is append-only'); END;
CREATE TRIGGER IF NOT EXISTS no_update_state_transitions
    BEFORE UPDATE ON state_transitions BEGIN
    SELECT RAISE(FAIL, 'state_transitions is append-only'); END;
CREATE TRIGGER IF NOT EXISTS no_delete_state_transitions
    BEFORE DELETE ON state_transitions BEGIN
    SELECT RAISE(FAIL, 'state_transitions is append-only'); END;
CREATE TRIGGER IF NOT EXISTS no_update_dream_cycles
    BEFORE UPDATE ON dream_cycles BEGIN
    SELECT RAISE(FAIL, 'dream_cycles is append-only'); END;
CREATE TRIGGER IF NOT EXISTS no_delete_dream_cycles
    BEFORE DELETE ON dream_cycles BEGIN
    SELECT RAISE(FAIL, 'dream_cycles is append-only'); END;
CREATE TRIGGER IF NOT EXISTS no_update_llm_calls
    BEFORE UPDATE ON llm_calls BEGIN
    SELECT RAISE(FAIL, 'llm_calls is append-only'); END;
CREATE TRIGGER IF NOT EXISTS no_delete_llm_calls
    BEFORE DELETE ON llm_calls BEGIN
    SELECT RAISE(FAIL, 'llm_calls is append-only'); END;
CREATE TRIGGER IF NOT EXISTS no_update_pending_change_actions
    BEFORE UPDATE ON pending_change_actions BEGIN
    SELECT RAISE(FAIL, 'pending_change_actions is append-only'); END;
CREATE TRIGGER IF NOT EXISTS no_delete_pending_change_actions
    BEFORE DELETE ON pending_change_actions BEGIN
    SELECT RAISE(FAIL, 'pending_change_actions is append-only'); END;
CREATE TRIGGER IF NOT EXISTS no_update_learned_skills
    BEFORE UPDATE ON learned_skills BEGIN
    SELECT RAISE(FAIL, 'learned_skills is append-only'); END;
CREATE TRIGGER IF NOT EXISTS no_delete_learned_skills
    BEFORE DELETE ON learned_skills BEGIN
    SELECT RAISE(FAIL, 'learned_skills is append-only'); END;
"""

# Mapping of event_type names to table names for get_recent_events queries
_TABLE_MAP: dict[str, str] = {
    "llm_calls":              "llm_calls",
    "document_ops":           "document_ops",
    "auth_events":            "auth_events",
    "state_transitions":      "state_transitions",
    "dream_cycles":           "dream_cycles",
    "pending_change_actions": "pending_change_actions",
    "audit_log":              "audit_log",
    "funding_opportunities":  "funding_opportunities",
    "learned_skills":         "learned_skills",
}

_ALL_TABLES = list(_TABLE_MAP.values())


# ── Async helpers ──────────────────────────────────────────────────────────────


async def _ensure_schema(conn: aiosqlite.Connection) -> None:
    """Apply the DDL to *conn* using executescript (safe for triggers)."""
    await conn.executescript(_DDL)


@asynccontextmanager
async def _adb():
    """Async context manager that opens the audit DB, ensures schema, and closes on exit."""
    from pathlib import Path as _Path
    _Path(settings.audit_log_path).parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(settings.audit_log_path) as conn:
        await _ensure_schema(conn)
        yield conn


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Async log functions ────────────────────────────────────────────────────────


async def log_event(
    actor: str, action: str, target: str = "", detail: str = ""
) -> None:
    """Backward-compatible catch-all logger.  Writes to the legacy audit_log table."""
    async with _adb() as conn:
        await conn.execute(
            "INSERT INTO audit_log (ts, actor, action, target, detail)"
            " VALUES (?, ?, ?, ?, ?)",
            (_ts(), actor, action, target, detail),
        )
        await conn.commit()


async def log_llm_call(
    user_id: str = "",
    role: str = "",
    skill: str = "",
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    latency_ms: int = 0,
    state: str = "",
) -> None:
    async with _adb() as conn:
        await conn.execute(
            "INSERT INTO llm_calls"
            " (ts, user_id, role, skill, prompt_tokens, completion_tokens, latency_ms, state)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (_ts(), user_id, role, skill, prompt_tokens, completion_tokens, latency_ms, state),
        )
        await conn.commit()


async def log_document_op(
    user_id: str = "",
    file_path: str = "",
    operation: str = "",
    status: str = "",
) -> None:
    async with _adb() as conn:
        await conn.execute(
            "INSERT INTO document_ops (ts, user_id, file_path, operation, status)"
            " VALUES (?, ?, ?, ?, ?)",
            (_ts(), user_id, file_path, operation, status),
        )
        await conn.commit()


async def log_auth_event(username: str = "", event_type: str = "") -> None:
    async with _adb() as conn:
        await conn.execute(
            "INSERT INTO auth_events (ts, username, event_type) VALUES (?, ?, ?)",
            (_ts(), username, event_type),
        )
        await conn.commit()


async def log_state_transition(
    from_state: str = "", to_state: str = "", trigger: str = ""
) -> None:
    async with _adb() as conn:
        await conn.execute(
            "INSERT INTO state_transitions (ts, from_state, to_state, trigger)"
            " VALUES (?, ?, ?, ?)",
            (_ts(), from_state, to_state, trigger),
        )
        await conn.commit()


async def log_dream_cycle(
    start: str = "",
    end: str = "",
    tasks_completed: int = 0,
    tasks_failed: int = 0,
    summary: str = "",
) -> None:
    async with _adb() as conn:
        await conn.execute(
            "INSERT INTO dream_cycles"
            " (ts, start_ts, end_ts, tasks_completed, tasks_failed, summary)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (_ts(), start, end, tasks_completed, tasks_failed, summary),
        )
        await conn.commit()


async def log_pending_action(
    filename: str = "",
    action: str = "",
    user_id: str = "",
    reason: Optional[str] = None,
) -> None:
    async with _adb() as conn:
        await conn.execute(
            "INSERT INTO pending_change_actions (ts, filename, action, user_id, reason)"
            " VALUES (?, ?, ?, ?, ?)",
            (_ts(), filename, action, user_id, reason),
        )
        await conn.commit()


async def log_learned_skill(
    skill_name: str = "",
    trigger_pattern: str = "",
    description: str = "",
    source: str = "",
) -> None:
    async with _adb() as conn:
        await conn.execute(
            "INSERT INTO learned_skills (ts, skill_name, trigger_pattern, description, source)"
            " VALUES (?, ?, ?, ?, ?)",
            (_ts(), skill_name, trigger_pattern, description, source),
        )
        await conn.commit()


async def get_learned_skills(limit: int = 15) -> list[dict]:
    """Return the most recent learned skills, newest first."""
    async with _adb() as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            "SELECT skill_name, trigger_pattern, description"
            " FROM learned_skills ORDER BY id DESC LIMIT ?",
            (limit,),
        ) as cur:
            return [dict(row) for row in await cur.fetchall()]


# ── Sync log function (used by pending_changes from non-async contexts) ────────


def log_pending_action_sync(
    filename: str = "",
    action: str = "",
    user_id: str = "",
    reason: Optional[str] = None,
) -> None:
    """Synchronous version for use in non-async code paths."""
    import os
    from pathlib import Path as _Path
    _Path(settings.audit_log_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.audit_log_path)
    for stmt in _DDL.strip().split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)
    conn.execute(
        "INSERT INTO pending_change_actions (ts, filename, action, user_id, reason)"
        " VALUES (?, ?, ?, ?, ?)",
        (_ts(), filename, action, user_id, reason),
    )
    conn.commit()
    conn.close()


# ── Shared messages (chat history for web UI) ─────────────────────────────────


async def save_shared_message(
    username: str = "",
    display_name: str = "",
    role: str = "",
    msg_type: str = "",
    content: str = "",
) -> None:
    """Append a message to the shared_messages table."""
    async with _adb() as conn:
        await conn.execute(
            "INSERT INTO shared_messages (ts, username, display_name, role, msg_type, content)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (_ts(), username, display_name, role, msg_type, content),
        )
        await conn.commit()


# ── Query ──────────────────────────────────────────────────────────────────────


async def get_recent_events(
    n: int = 100,
    event_type: Optional[str] = None,
    since: Optional[str] = None,
) -> list[dict]:
    """
    Return the most recent n events, optionally filtered by table / event_type
    and by a minimum timestamp (ISO string, inclusive).

    If event_type is None, results from all tables are merged and sorted by ts.
    """
    since_clause = "AND ts >= ?" if since else ""

    async with _adb() as conn:
        conn.row_factory = aiosqlite.Row

        if event_type and event_type in _TABLE_MAP:
            tables = [_TABLE_MAP[event_type]]
        else:
            tables = _ALL_TABLES

        rows: list[dict] = []
        for table in tables:
            params: list = [n]
            query = (
                f"SELECT *, '{table}' AS event_type FROM {table}"
                f" WHERE 1=1 {since_clause}"
                f" ORDER BY id DESC LIMIT ?"
            )
            if since:
                params = [since, n]
            async with conn.execute(query, params) as cur:
                for row in await cur.fetchall():
                    rows.append(dict(row))

    # Merge and sort across tables when querying all
    if len(tables) > 1:
        rows.sort(key=lambda r: r.get("ts", ""), reverse=True)
        rows = rows[:n]

    return rows


# ── Dashboard query helpers ────────────────────────────────────────────────────


async def get_llm_usage_summary(days: int = 30) -> list[dict]:
    """
    Aggregate LLM token usage by day for the last *days* days.
    Returns list of {date, total_prompt_tokens, total_completion_tokens, call_count}.
    """
    async with _adb() as conn:
        conn.row_factory = aiosqlite.Row
        query = """
            SELECT date(ts) AS date,
                   SUM(prompt_tokens)     AS total_prompt_tokens,
                   SUM(completion_tokens)  AS total_completion_tokens,
                   COUNT(*)                AS call_count
              FROM llm_calls
             WHERE ts >= date('now', ?)
             GROUP BY date(ts)
             ORDER BY date(ts)
        """
        async with conn.execute(query, (f"-{days} days",)) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_job_outcomes(days: int = 7) -> list[dict]:
    """
    Aggregate job success/failure counts from the audit_log table for
    scheduler-related events over the last *days* days.
    Returns list of {job_id, success_count, failure_count, avg_duration_ms}.
    """
    async with _adb() as conn:
        conn.row_factory = aiosqlite.Row
        query = """
            SELECT target AS job_id,
                   SUM(CASE WHEN action LIKE '%success%' OR action LIKE '%completed%' THEN 1 ELSE 0 END) AS success_count,
                   SUM(CASE WHEN action LIKE '%fail%' OR action LIKE '%error%' THEN 1 ELSE 0 END)       AS failure_count,
                   COUNT(*) AS total_count
              FROM audit_log
             WHERE ts >= date('now', ?)
               AND (actor LIKE '%scheduler%' OR actor LIKE '%job%' OR action LIKE '%job%' OR action LIKE '%scheduler%')
             GROUP BY target
             ORDER BY total_count DESC
        """
        async with conn.execute(query, (f"-{days} days",)) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_indexing_activity(days: int = 7) -> list[dict]:
    """
    Aggregate document indexing activity by day for the last *days* days.
    Returns list of {date, indexed_count, error_count}.
    """
    async with _adb() as conn:
        conn.row_factory = aiosqlite.Row
        query = """
            SELECT date(ts) AS date,
                   SUM(CASE WHEN status = 'ok' OR status = 'success' OR status = 'indexed' THEN 1 ELSE 0 END) AS indexed_count,
                   SUM(CASE WHEN status = 'error' OR status = 'failed' THEN 1 ELSE 0 END) AS error_count
              FROM document_ops
             WHERE ts >= date('now', ?)
             GROUP BY date(ts)
             ORDER BY date(ts)
        """
        async with conn.execute(query, (f"-{days} days",)) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_dream_cycle_summary(days: int = 7) -> list[dict]:
    """
    Return dream cycle summaries for the last *days* days.
    Returns list of {ts, start_ts, end_ts, tasks_completed, tasks_failed, summary}.
    """
    async with _adb() as conn:
        conn.row_factory = aiosqlite.Row
        query = """
            SELECT ts, start_ts, end_ts, tasks_completed, tasks_failed, summary
              FROM dream_cycles
             WHERE ts >= date('now', ?)
             ORDER BY ts DESC
        """
        async with conn.execute(query, (f"-{days} days",)) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_state_transition_summary(days: int = 7) -> list[dict]:
    """
    Return state transitions for the last *days* days.
    Returns list of {ts, from_state, to_state, trigger}.
    """
    async with _adb() as conn:
        conn.row_factory = aiosqlite.Row
        query = """
            SELECT ts, from_state, to_state, trigger
              FROM state_transitions
             WHERE ts >= date('now', ?)
             ORDER BY ts DESC
        """
        async with conn.execute(query, (f"-{days} days",)) as cur:
            return [dict(r) for r in await cur.fetchall()]


# ── Backward compat ────────────────────────────────────────────────────────────


async def recent_events(limit: int = 50) -> list[dict]:
    """Kept for backward compatibility — queries only the legacy audit_log table."""
    return await get_recent_events(n=limit, event_type="audit_log")


class AuditLog:
    """
    Class-based façade over the module-level async log functions.

    Provided so callers that do ``from app.storage.audit_log import AuditLog``
    and instantiate it can call ``await instance.log_event(...)`` instead of
    the bare module-level function.

    An optional ``db_path`` argument overrides ``settings.audit_log_path``
    for the lifetime of this instance (useful in tests).
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        self._db_path = db_path

    # ------------------------------------------------------------------
    # Internal helper: context manager that honours the instance db_path
    # ------------------------------------------------------------------
    def _adb_ctx(self):
        """Return an async context manager pointing at the correct DB path."""
        if self._db_path is None:
            return _adb()
        # Build a local context manager using the overridden path
        from contextlib import asynccontextmanager
        from pathlib import Path as _Path

        @asynccontextmanager
        async def _local_adb():
            _Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
            async with aiosqlite.connect(self._db_path) as conn:
                await _ensure_schema(conn)
                yield conn

        return _local_adb()

    # ------------------------------------------------------------------
    # Log writers
    # ------------------------------------------------------------------

    async def log_event(
        self,
        actor: str,
        action: str,
        target: str = "",
        detail: str = "",
    ) -> None:
        async with self._adb_ctx() as conn:
            await conn.execute(
                "INSERT INTO audit_log (ts, actor, action, target, detail)"
                " VALUES (?, ?, ?, ?, ?)",
                (_ts(), actor, action, target, detail),
            )
            await conn.commit()

    async def log_auth_event(self, username: str = "", event_type: str = "") -> None:
        async with self._adb_ctx() as conn:
            await conn.execute(
                "INSERT INTO auth_events (ts, username, event_type) VALUES (?, ?, ?)",
                (_ts(), username, event_type),
            )
            await conn.commit()

    async def log_document_op(
        self,
        user_id: str = "",
        file_path: str = "",
        operation: str = "",
        status: str = "",
    ) -> None:
        async with self._adb_ctx() as conn:
            await conn.execute(
                "INSERT INTO document_ops (ts, user_id, file_path, operation, status)"
                " VALUES (?, ?, ?, ?, ?)",
                (_ts(), user_id, file_path, operation, status),
            )
            await conn.commit()

    async def log_state_transition(
        self, from_state: str = "", to_state: str = "", trigger: str = ""
    ) -> None:
        async with self._adb_ctx() as conn:
            await conn.execute(
                "INSERT INTO state_transitions (ts, from_state, to_state, trigger)"
                " VALUES (?, ?, ?, ?)",
                (_ts(), from_state, to_state, trigger),
            )
            await conn.commit()

    async def log_dream_cycle(
        self,
        start: str = "",
        end: str = "",
        tasks_completed: int = 0,
        tasks_failed: int = 0,
        summary: str = "",
    ) -> None:
        async with self._adb_ctx() as conn:
            await conn.execute(
                "INSERT INTO dream_cycles"
                " (ts, start_ts, end_ts, tasks_completed, tasks_failed, summary)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (_ts(), start, end, tasks_completed, tasks_failed, summary),
            )
            await conn.commit()

    async def log_llm_call(
        self,
        user_id: str = "",
        role: str = "",
        skill: str = "",
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        latency_ms: int = 0,
        state: str = "",
    ) -> None:
        async with self._adb_ctx() as conn:
            await conn.execute(
                "INSERT INTO llm_calls"
                " (ts, user_id, role, skill, prompt_tokens, completion_tokens, latency_ms, state)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (_ts(), user_id, role, skill, prompt_tokens, completion_tokens, latency_ms, state),
            )
            await conn.commit()

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    async def get_recent_events(
        self,
        n: int = 100,
        event_type: Optional[str] = None,
        since: Optional[str] = None,
    ) -> list[dict]:
        since_clause = "AND ts >= ?" if since else ""
        async with self._adb_ctx() as conn:
            conn.row_factory = aiosqlite.Row
            if event_type and event_type in _TABLE_MAP:
                tables = [_TABLE_MAP[event_type]]
            else:
                tables = _ALL_TABLES
            rows: list[dict] = []
            for table in tables:
                params: list = [n]
                query = (
                    f"SELECT *, '{table}' AS event_type FROM {table}"
                    f" WHERE 1=1 {since_clause}"
                    f" ORDER BY id DESC LIMIT ?"
                )
                if since:
                    params = [since, n]
                async with conn.execute(query, params) as cur:
                    for row in await cur.fetchall():
                        rows.append(dict(row))
        if len(tables) > 1:
            rows.sort(key=lambda r: r.get("ts", ""), reverse=True)
            rows = rows[:n]
        return rows
