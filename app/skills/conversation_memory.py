"""
ConversationMemorySkill — search and browse past conversations.

Uses the existing SessionManager for session data and FTS5 on
shared_messages for full-text search across all interfaces.
"""
import logging
from datetime import datetime, timedelta

import aiosqlite

from app.config import settings
from app.gateway.session import SessionManager
from app.skills.base import BaseSkill, SkillResult

log = logging.getLogger(__name__)

# FTS5 virtual table DDL — created lazily on first search
_FTS_DDL = """
CREATE VIRTUAL TABLE IF NOT EXISTS shared_messages_fts
USING fts5(content, content='shared_messages', content_rowid='id');
"""

_FTS_REBUILD = """
INSERT INTO shared_messages_fts(shared_messages_fts) VALUES('rebuild');
"""

# Trigger to keep FTS in sync with new inserts
_FTS_TRIGGER = """
CREATE TRIGGER IF NOT EXISTS shared_messages_ai AFTER INSERT ON shared_messages BEGIN
    INSERT INTO shared_messages_fts(rowid, content) VALUES (new.id, new.content);
END;
"""


class ConversationMemorySkill(BaseSkill):
    name = "conversation_memory"
    description = (
        "Search and browse past conversation history across all interfaces. "
        "Actions: search (full-text), list (recent sessions), read (session detail)."
    )

    def __init__(self) -> None:
        self._sessions = SessionManager()
        self._fts_ready = False

    async def _ensure_fts(self) -> None:
        """Create the FTS5 index if it doesn't exist yet, then rebuild."""
        if self._fts_ready:
            return
        try:
            async with aiosqlite.connect(settings.audit_log_path) as conn:
                await conn.executescript(_FTS_DDL)
                await conn.executescript(_FTS_TRIGGER)
                await conn.executescript(_FTS_REBUILD)
            self._fts_ready = True
            log.info("FTS5 index on shared_messages created/rebuilt")
        except Exception as exc:
            log.warning("FTS5 setup failed: %s", exc)

    async def run(self, action: str = "list", **kwargs) -> SkillResult:
        if action == "search":
            return await self._search(
                query=kwargs.get("query", ""),
                limit=int(kwargs.get("limit", 20)),
            )
        if action == "list":
            return await self._list_sessions(
                days=int(kwargs.get("days", 7)),
            )
        if action == "read":
            return await self._read_session(
                session_id=kwargs.get("session_id", ""),
                date=kwargs.get("date", ""),
            )
        return SkillResult(success=False, error=f"Unknown action: {action}")

    async def _search(self, query: str, limit: int = 20) -> SkillResult:
        """Full-text search across shared_messages using FTS5."""
        if not query.strip():
            return SkillResult(success=False, error="Search query cannot be empty")

        await self._ensure_fts()

        try:
            async with aiosqlite.connect(settings.audit_log_path) as conn:
                conn.row_factory = aiosqlite.Row
                # FTS5 match query
                sql = """
                    SELECT sm.id, sm.ts, sm.username, sm.display_name,
                           sm.role, sm.msg_type,
                           snippet(shared_messages_fts, 0, '>>>', '<<<', '...', 40) AS excerpt
                    FROM shared_messages_fts
                    JOIN shared_messages sm ON sm.id = shared_messages_fts.rowid
                    WHERE shared_messages_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?
                """
                async with conn.execute(sql, (query, limit)) as cur:
                    rows = [dict(r) for r in await cur.fetchall()]

            return SkillResult(
                success=True,
                output=f"Found {len(rows)} match(es) for '{query}'.",
                metadata={"matches": rows, "query": query},
            )
        except Exception as exc:
            log.warning("FTS search failed: %s", exc)
            return SkillResult(success=False, error=str(exc))

    async def _list_sessions(self, days: int = 7) -> SkillResult:
        """List recent sessions from the last N days."""
        summaries = []
        for offset in range(days):
            date_str = (datetime.now() - timedelta(days=offset)).strftime("%Y-%m-%d")
            sessions = self._sessions._get_sessions_for_date(date_str)
            for s in sessions:
                summaries.append({
                    "session_id": s.session_id[:8],
                    "date": date_str,
                    "user": s.user_id,
                    "messages": len(s.messages),
                    "exchanges": s.exchange_count,
                })

        return SkillResult(
            success=True,
            output=f"{len(summaries)} session(s) in the last {days} day(s).",
            metadata={"sessions": summaries},
        )

    async def _read_session(self, session_id: str, date: str = "") -> SkillResult:
        """Read a specific session by ID prefix."""
        if not session_id:
            return SkillResult(success=False, error="session_id is required")

        # Search across recent dates if no date given
        dates_to_check = []
        if date:
            dates_to_check = [date]
        else:
            for offset in range(14):
                dates_to_check.append(
                    (datetime.now() - timedelta(days=offset)).strftime("%Y-%m-%d")
                )

        for d in dates_to_check:
            sessions = self._sessions._get_sessions_for_date(d)
            for s in sessions:
                if s.session_id.startswith(session_id):
                    visible = [
                        {"role": m["role"], "content": m.get("content", "")[:500]}
                        for m in s.messages
                        if m.get("role") in ("user", "assistant")
                        and m.get("content", "").strip()
                    ]
                    return SkillResult(
                        success=True,
                        output=f"Session {s.session_id[:8]} — {len(visible)} messages.",
                        metadata={
                            "session_id": s.session_id,
                            "user": s.user_id,
                            "date": d,
                            "messages": visible,
                        },
                    )

        return SkillResult(success=False, error=f"Session not found: {session_id}")
