"""
funding_feed_db — Database operations for the FundingFeed skill.

Handles SQLite-backed feed source management (CRUD), opportunity storage,
notification tracking, and CSV bootstrap import.  All DB access for the
funding feed subsystem lives here.
"""
from __future__ import annotations

import csv
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import aiosqlite

from app.config import settings

log = logging.getLogger(__name__)

# ── Default paths ─────────────────────────────────────────────────────────────

_DEFAULT_CSV = Path("/home/cdcn/CDCN_Funding_RSS_Database_2026.csv")

# ── Feed-sources DDL ─────────────────────────────────────────────────────────

_FEEDS_DDL = """\
CREATE TABLE IF NOT EXISTS feed_sources (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    funder     TEXT    NOT NULL,
    url        TEXT    NOT NULL UNIQUE,
    feed_type  TEXT    NOT NULL DEFAULT 'Native RSS',
    focus      TEXT    NOT NULL DEFAULT '',
    notes      TEXT    NOT NULL DEFAULT '',
    enabled    INTEGER NOT NULL DEFAULT 1,
    last_checked TEXT  NOT NULL DEFAULT '',
    last_status  TEXT  NOT NULL DEFAULT '',
    created_at TEXT    NOT NULL DEFAULT '',
    updated_at TEXT    NOT NULL DEFAULT ''
);
"""


# ── Schema helpers ───────────────────────────────────────────────────────────

async def _ensure_feeds_schema(conn: aiosqlite.Connection) -> None:
    await conn.executescript(_FEEDS_DDL)


def _sync_ensure_feeds_schema(db_path: str) -> None:
    import sqlite3 as _s3
    c = _s3.connect(db_path)
    try:
        c.executescript(_FEEDS_DDL)
        c.commit()
    finally:
        c.close()


# ── Feed count & CSV import ──────────────────────────────────────────────────

async def _db_feed_count() -> int:
    """Return the number of feed_sources rows."""
    async with aiosqlite.connect(settings.audit_log_path) as conn:
        await _ensure_feeds_schema(conn)
        row = await conn.execute_fetchall("SELECT COUNT(*) FROM feed_sources")
        return row[0][0] if row else 0


async def _import_csv_to_db(csv_path: Path | None = None) -> int:
    """One-time import from CSV into feed_sources.  Returns rows inserted."""
    path = csv_path or _DEFAULT_CSV
    if not path.exists():
        log.info("No CSV to import: %s", path)
        return 0

    rows: list[dict[str, str]] = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            funder = (row.get("Funder Name") or "").strip()
            url = (row.get("RSS Feed / Target URL") or "").strip()
            if not funder or not url:
                continue
            rows.append({
                "funder": funder,
                "url": url,
                "feed_type": (row.get("Feed Type") or "").strip(),
                "focus": (row.get("Primary Focus / Relevance") or "").strip(),
                "notes": (row.get("2026 Status Notes") or "").strip(),
            })

    now = datetime.now(timezone.utc).isoformat()
    inserted = 0
    async with aiosqlite.connect(settings.audit_log_path) as conn:
        await _ensure_feeds_schema(conn)
        for r in rows:
            try:
                await conn.execute(
                    "INSERT OR IGNORE INTO feed_sources"
                    " (funder, url, feed_type, focus, notes, enabled, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
                    (r["funder"], r["url"], r["feed_type"], r["focus"], r["notes"], now, now),
                )
                inserted += 1
            except Exception:
                pass
        await conn.commit()
    log.info("CSV import: %d feeds imported from %s", inserted, path)
    return inserted


# ── Feed config loaders ──────────────────────────────────────────────────────

async def _load_feed_config_from_db() -> list[dict[str, str]]:
    """Load enabled feeds from SQLite."""
    async with aiosqlite.connect(settings.audit_log_path) as conn:
        await _ensure_feeds_schema(conn)
        conn.row_factory = aiosqlite.Row
        rows = await conn.execute_fetchall(
            "SELECT * FROM feed_sources WHERE enabled = 1 ORDER BY funder"
        )
        return [dict(r) for r in rows]


def _load_feed_config(csv_path: Path | None = None) -> list[dict[str, str]]:
    """Load feed configuration from CSV.  Legacy fallback only."""
    path = csv_path or _DEFAULT_CSV
    if not path.exists():
        log.warning("Feed config CSV not found: %s", path)
        return []
    feeds = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            funder = (row.get("Funder Name") or "").strip()
            url = (row.get("RSS Feed / Target URL") or "").strip()
            feed_type = (row.get("Feed Type") or "").strip()
            if not funder or not url:
                continue
            feeds.append({
                "funder": funder,
                "url": url,
                "feed_type": feed_type,
                "focus": (row.get("Primary Focus / Relevance") or "").strip(),
                "notes": (row.get("2026 Status Notes") or "").strip(),
            })
    return feeds


# ── Feed CRUD ────────────────────────────────────────────────────────────────

async def list_feeds(include_disabled: bool = False) -> list[dict]:
    """Return all feed sources, optionally including disabled ones."""
    async with aiosqlite.connect(settings.audit_log_path) as conn:
        await _ensure_feeds_schema(conn)
        conn.row_factory = aiosqlite.Row
        if include_disabled:
            rows = await conn.execute_fetchall(
                "SELECT * FROM feed_sources ORDER BY enabled DESC, funder"
            )
        else:
            rows = await conn.execute_fetchall(
                "SELECT * FROM feed_sources WHERE enabled = 1 ORDER BY funder"
            )
        return [dict(r) for r in rows]


async def add_feed(
    funder: str, url: str, feed_type: str = "Native RSS",
    focus: str = "", notes: str = "",
) -> dict:
    """Add a new feed source.  Returns the new row as a dict."""
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(settings.audit_log_path) as conn:
        await _ensure_feeds_schema(conn)
        cur = await conn.execute(
            "INSERT INTO feed_sources"
            " (funder, url, feed_type, focus, notes, enabled, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
            (funder.strip(), url.strip(), feed_type.strip(), focus.strip(), notes.strip(), now, now),
        )
        await conn.commit()
        feed_id = cur.lastrowid
        conn.row_factory = aiosqlite.Row
        rows = await conn.execute_fetchall(
            "SELECT * FROM feed_sources WHERE id = ?", (feed_id,)
        )
        return dict(rows[0]) if rows else {"id": feed_id}


async def update_feed(feed_id: int, **kwargs) -> bool:
    """Update a feed source.  Accepts: funder, url, feed_type, focus, notes, enabled."""
    allowed = {"funder", "url", "feed_type", "focus", "notes", "enabled"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return False
    updates["updated_at"] = datetime.now(timezone.utc).isoformat()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [feed_id]
    async with aiosqlite.connect(settings.audit_log_path) as conn:
        await _ensure_feeds_schema(conn)
        cur = await conn.execute(
            f"UPDATE feed_sources SET {set_clause} WHERE id = ?", values
        )
        await conn.commit()
        return cur.rowcount > 0


async def delete_feed(feed_id: int) -> bool:
    """Delete a feed source permanently."""
    async with aiosqlite.connect(settings.audit_log_path) as conn:
        await _ensure_feeds_schema(conn)
        cur = await conn.execute(
            "DELETE FROM feed_sources WHERE id = ?", (feed_id,)
        )
        await conn.commit()
        return cur.rowcount > 0


async def update_feed_status(feed_id: int, status: str) -> None:
    """Update the last_checked timestamp and status for a feed."""
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(settings.audit_log_path) as conn:
        await _ensure_feeds_schema(conn)
        await conn.execute(
            "UPDATE feed_sources SET last_checked = ?, last_status = ? WHERE id = ?",
            (now, status, feed_id),
        )
        await conn.commit()


# ── Opportunity storage & queries ────────────────────────────────────────────

async def _store_opportunities(
    opportunities: list[dict[str, Any]],
) -> int:
    """
    Store opportunities in SQLite.  Returns count of newly inserted rows.
    Uses INSERT OR IGNORE for deduplication on guid.
    """
    if not opportunities:
        return 0

    from pathlib import Path as _Path
    _Path(settings.audit_log_path).parent.mkdir(parents=True, exist_ok=True)

    new_count = 0
    async with aiosqlite.connect(settings.audit_log_path) as conn:
        # Ensure schema exists
        from app.storage.audit_log import _ensure_schema
        await _ensure_schema(conn)

        for opp in opportunities:
            ts = datetime.now(timezone.utc).isoformat()
            try:
                cursor = await conn.execute(
                    "INSERT OR IGNORE INTO funding_opportunities"
                    " (ts, guid, funder, title, link, summary, published,"
                    "  deadline, amount, eligibility, relevance, feed_url, notified)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
                    (
                        ts,
                        opp["guid"],
                        opp["funder"],
                        opp["title"],
                        opp["link"],
                        opp["summary"],
                        opp["published"],
                        opp.get("deadline") or "",
                        opp.get("amount") or "",
                        opp.get("eligibility") or "",
                        opp["relevance"],
                        opp["feed_url"],
                    ),
                )
                if cursor.rowcount > 0:
                    new_count += 1
            except Exception as exc:
                log.debug("Insert failed for guid=%s: %s", opp["guid"], exc)

        await conn.commit()

    return new_count


async def _get_unnotified() -> list[dict]:
    """Return opportunities that haven't been notified yet, ordered by relevance."""
    async with aiosqlite.connect(settings.audit_log_path) as conn:
        from app.storage.audit_log import _ensure_schema
        await _ensure_schema(conn)
        conn.row_factory = aiosqlite.Row
        rows = []
        async with conn.execute(
            "SELECT * FROM funding_opportunities"
            " WHERE notified = 0"
            " ORDER BY"
            "   CASE relevance WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,"
            "   ts DESC"
            " LIMIT 50"
        ) as cur:
            for row in await cur.fetchall():
                rows.append(dict(row))
        return rows


async def _mark_notified(ids: list[int]) -> None:
    """Mark opportunities as notified.  This is a controlled UPDATE, not a
    general-purpose mutation — we do NOT add append-only triggers to this
    table because the notified flag is an operational state, not an audit record."""
    if not ids:
        return
    async with aiosqlite.connect(settings.audit_log_path) as conn:
        from app.storage.audit_log import _ensure_schema
        await _ensure_schema(conn)
        placeholders = ",".join("?" for _ in ids)
        await conn.execute(
            f"UPDATE funding_opportunities SET notified = 1 WHERE id IN ({placeholders})",
            ids,
        )
        await conn.commit()


async def get_recent_opportunities(
    n: int = 20,
    relevance: str | None = None,
    days: int | None = None,
) -> list[dict]:
    """
    Query recent funding opportunities.  Used by the agentic loop and
    heartbeat/digest jobs.

    Args:
        n: max results
        relevance: filter by 'high', 'medium', or 'low'
        days: only return entries from the last N days
    """
    async with aiosqlite.connect(settings.audit_log_path) as conn:
        from app.storage.audit_log import _ensure_schema
        await _ensure_schema(conn)
        conn.row_factory = aiosqlite.Row

        clauses = ["1=1"]
        params: list[Any] = []

        if relevance:
            clauses.append("relevance = ?")
            params.append(relevance)
        if days:
            cutoff = datetime.now(timezone.utc)
            cutoff = (cutoff - timedelta(days=days)).isoformat()
            clauses.append("ts >= ?")
            params.append(cutoff)

        params.append(n)
        query = (
            "SELECT * FROM funding_opportunities"
            f" WHERE {' AND '.join(clauses)}"
            " ORDER BY"
            "   CASE relevance WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,"
            "   ts DESC"
            " LIMIT ?"
        )

        rows = []
        async with conn.execute(query, params) as cur:
            for row in await cur.fetchall():
                rows.append(dict(row))
        return rows


def _format_opportunity(opp: dict) -> str:
    """Format a single opportunity as a markdown block."""
    parts = [f"**{opp['title']}**"]
    parts.append(f"Funder: {opp['funder']}")
    if opp.get("amount"):
        parts.append(f"Amount: {opp['amount']}")
    if opp.get("deadline"):
        parts.append(f"Deadline: {opp['deadline']}")
    if opp.get("link"):
        parts.append(f"Link: {opp['link']}")
    if opp.get("summary"):
        # Truncate for display
        s = opp["summary"]
        if len(s) > 200:
            s = s[:197] + "..."
        parts.append(f"Summary: {s}")
    parts.append(f"Relevance: {opp.get('relevance', 'unknown')}")
    return "\n".join(parts)
