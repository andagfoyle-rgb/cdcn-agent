"""
Deadline tracker database operations — schema, CRUD, and seed data.

Extracted from deadline_tracker.py to keep each module under 500 lines.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

import aiosqlite

from app.config import settings

log = logging.getLogger(__name__)

_DB_PATH = str(Path(settings.audit_log_path).parent / "tracker.db")
_INDEXER_DB = str(Path(settings.audit_log_path).parent / "indexer.db")

# Schema without CHECK constraints so migrations don't break inserts.
_DDL = """
CREATE TABLE IF NOT EXISTS deadlines (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    title           TEXT NOT NULL,
    category        TEXT NOT NULL,
    deadline_type   TEXT DEFAULT 'hard_deadline',
    due_date        TEXT NOT NULL,
    reminder_days   TEXT DEFAULT '[30,14,7,1]',
    status          TEXT DEFAULT 'pending',
    assigned_to     TEXT,
    source_document TEXT,
    notes           TEXT,
    recurrence      TEXT,
    created_at      TEXT DEFAULT (datetime('now')),
    completed_at    TEXT,
    created_by      TEXT,
    event_time      TEXT
);
"""

VALID_CATEGORIES = frozenset({
    "funding", "statutory", "policy_review", "contractual",
    "event", "meeting", "other",
})

# ── Schema + migration ────────────────────────────────────────────────────────


def _migrate_schema_sync() -> None:
    """
    Idempotent synchronous migration.
    Recreates the deadlines table if it is missing the event_time column.
    """
    with sqlite3.connect(_DB_PATH) as conn:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='deadlines'"
        ).fetchone()
        if not row:
            return
        if "event_time" in row[0]:
            return

        log.info("deadline_tracker: migrating schema — adding event_time column")
        col_rows = conn.execute("PRAGMA table_info(deadlines)").fetchall()
        old_cols = [c[1] for c in col_rows]
        rows = conn.execute("SELECT * FROM deadlines").fetchall()

        conn.execute("DROP TABLE deadlines")
        conn.execute("""
            CREATE TABLE deadlines (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                title           TEXT NOT NULL,
                category        TEXT NOT NULL,
                deadline_type   TEXT DEFAULT 'hard_deadline',
                due_date        TEXT NOT NULL,
                reminder_days   TEXT DEFAULT '[30,14,7,1]',
                status          TEXT DEFAULT 'pending',
                assigned_to     TEXT,
                source_document TEXT,
                notes           TEXT,
                recurrence      TEXT,
                created_at      TEXT DEFAULT (datetime('now')),
                completed_at    TEXT,
                created_by      TEXT,
                event_time      TEXT
            )
        """)
        keep = [c for c in old_cols if c != "id"]
        placeholders = ", ".join("?" * len(keep))
        col_list = ", ".join(keep)
        for row in rows:
            d = dict(zip(old_cols, row))
            conn.execute(
                f"INSERT INTO deadlines (id, {col_list}) VALUES (?, {placeholders})",
                [d["id"]] + [d[c] for c in keep],
            )
        conn.commit()
        log.info("deadline_tracker: migration complete, %d rows preserved", len(rows))


_MIGRATIONS = [
    (1, _DDL),
    (2, "ALTER TABLE deadlines ADD COLUMN event_time TEXT;"),
]


async def _ensure_schema() -> None:
    _migrate_schema_sync()
    async with aiosqlite.connect(_DB_PATH) as conn:
        await conn.executescript(_DDL)
        from app.utils.schema import apply_migrations
        await apply_migrations(conn, _MIGRATIONS)
        await conn.commit()


# ── CRUD ──────────────────────────────────────────────────────────────────────


async def add_deadline(
    title: str,
    category: str,
    due_date: str,
    deadline_type: str = "hard_deadline",
    assigned_to: str = "",
    notes: str = "",
    source_document: str = "",
    recurrence: str = "",
    reminder_days: str = "[30,14,7,1]",
    created_by: str = "system",
    event_time: str = "",
) -> dict:
    await _ensure_schema()
    async with aiosqlite.connect(_DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute(
            """INSERT INTO deadlines
               (title, category, deadline_type, due_date, reminder_days, status,
                assigned_to, source_document, notes, recurrence, created_by, event_time)
               VALUES (?,?,?,?,?,'pending',?,?,?,?,?,?)""",
            (title, category, deadline_type, due_date, reminder_days,
             assigned_to, source_document, notes, recurrence, created_by,
             event_time or None),
        )
        await conn.commit()
        row = await (
            await conn.execute("SELECT * FROM deadlines WHERE id=?", (cur.lastrowid,))
        ).fetchone()
        return dict(row) if row else {}


async def list_deadlines(
    status: str | None = None,
    category: str | None = None,
    days_ahead: int | None = None,
    include_completed: bool = False,
) -> list[dict]:
    await _ensure_schema()
    clauses: list[str] = []
    params: list = []
    if status:
        clauses.append("status = ?")
        params.append(status)
    elif not include_completed:
        clauses.append("status != 'completed'")
    if category:
        clauses.append("category = ?")
        params.append(category)
    if days_ahead is not None:
        today = date.today().isoformat()
        future = (date.today() + timedelta(days=days_ahead)).isoformat()
        clauses.append("due_date >= ?")
        params.append(today)
        clauses.append("due_date <= ?")
        params.append(future)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    async with aiosqlite.connect(_DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        rows = await (
            await conn.execute(
                f"SELECT * FROM deadlines {where} ORDER BY due_date ASC", params
            )
        ).fetchall()
        return [dict(r) for r in rows]


async def get_deadline(deadline_id: int) -> dict | None:
    await _ensure_schema()
    async with aiosqlite.connect(_DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        row = await (
            await conn.execute("SELECT * FROM deadlines WHERE id=?", (deadline_id,))
        ).fetchone()
        return dict(row) if row else None


async def update_deadline(deadline_id: int, **fields) -> dict | None:
    await _ensure_schema()
    allowed = {
        "title", "category", "deadline_type", "due_date", "status",
        "assigned_to", "notes", "recurrence", "reminder_days", "source_document",
        "event_time",
    }
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not updates:
        return await get_deadline(deadline_id)
    set_clause = ", ".join(f"{k}=?" for k in updates)
    params = list(updates.values()) + [deadline_id]
    async with aiosqlite.connect(_DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute(f"UPDATE deadlines SET {set_clause} WHERE id=?", params)
        await conn.commit()
        row = await (
            await conn.execute("SELECT * FROM deadlines WHERE id=?", (deadline_id,))
        ).fetchone()
        return dict(row) if row else None


async def delete_deadline(deadline_id: int) -> bool:
    await _ensure_schema()
    async with aiosqlite.connect(_DB_PATH) as conn:
        await conn.execute("DELETE FROM deadlines WHERE id=?", (deadline_id,))
        await conn.commit()
        return conn.total_changes > 0


async def mark_complete(deadline_id: int, notes: str = "") -> bool:
    await _ensure_schema()
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(_DB_PATH) as conn:
        await conn.execute(
            "UPDATE deadlines SET status='completed', completed_at=? WHERE id=?",
            (now, deadline_id),
        )
        if notes:
            await conn.execute(
                "UPDATE deadlines SET notes=COALESCE(CASE WHEN notes!='' THEN notes||' | ' ELSE '' END,'') || ? WHERE id=?",
                (notes, deadline_id),
            )
        await conn.commit()
        row = await (
            await conn.execute(
                "SELECT id FROM deadlines WHERE id=? AND status='completed'", (deadline_id,)
            )
        ).fetchone()
        return row is not None


async def check_overdue() -> list[dict]:
    """Mark pending/in_progress deadlines past their due_date as overdue."""
    await _ensure_schema()
    today = date.today().isoformat()
    async with aiosqlite.connect(_DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute(
            "UPDATE deadlines SET status='overdue' "
            "WHERE due_date < ? AND status IN ('pending','in_progress')",
            (today,),
        )
        await conn.commit()
        rows = await (
            await conn.execute(
                "SELECT * FROM deadlines WHERE status='overdue' ORDER BY due_date ASC"
            )
        ).fetchall()
        return [dict(r) for r in rows]


async def get_calendar_data(year: int, month: int) -> list[dict]:
    """Return all deadlines whose due_date falls in the given month."""
    await _ensure_schema()
    month_prefix = f"{year}-{month:02d}"
    async with aiosqlite.connect(_DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        rows = await (
            await conn.execute(
                "SELECT * FROM deadlines WHERE due_date LIKE ? ORDER BY due_date ASC",
                (f"{month_prefix}%",),
            )
        ).fetchall()
        return [dict(r) for r in rows]


# ── Board meeting calendar ────────────────────────────────────────────────────


async def scan_and_add_board_meetings() -> int:
    """
    1. Read all minutes dates from indexer.db -> add as 'event' category entries.
    2. From the most recent confirmed meeting date, generate fortnightly Tuesday
       recurrences for 6 months ahead.
    Returns total new rows inserted.
    """
    await _ensure_schema()

    async with aiosqlite.connect(_DB_PATH) as conn:
        existing = {
            r[0]
            for r in await (
                await conn.execute(
                    "SELECT due_date FROM deadlines WHERE category IN ('event','meeting') "
                    "AND title LIKE 'Board Meeting%'"
                )
            ).fetchall()
        }

    minute_dates: list[str] = []
    try:
        with sqlite3.connect(_INDEXER_DB) as idx:
            rows = idx.execute(
                "SELECT DISTINCT doc_date FROM document_metadata "
                "WHERE doc_type='minutes' AND doc_date IS NOT NULL "
                "ORDER BY doc_date ASC"
            ).fetchall()
            minute_dates = [r[0] for r in rows]
    except Exception as exc:
        log.warning("scan_and_add_board_meetings: could not read indexer.db: %s", exc)

    added = 0

    for d in minute_dates:
        if d in existing:
            continue
        try:
            dt = date.fromisoformat(d)
        except ValueError:
            continue
        await add_deadline(
            title="Board Meeting",
            category="event",
            due_date=d,
            deadline_type="reminder",
            notes=f"Board meeting \u2014 {dt.strftime('%A %-d %B %Y')}",
            recurrence="fortnightly",
            reminder_days="[7,1]",
            created_by="system",
            event_time="19:00",
        )
        existing.add(d)
        added += 1

    anchor: date | None = None
    for d in reversed(minute_dates):
        try:
            dt = date.fromisoformat(d)
            if dt.weekday() == 1:
                anchor = dt
                break
        except ValueError:
            continue

    if anchor is None:
        anchor = date(2026, 2, 10)

    today = date.today()
    end = today + timedelta(days=182)
    cursor = anchor
    while cursor <= end:
        if cursor > today:
            iso = cursor.isoformat()
            if iso not in existing:
                await add_deadline(
                    title="Board Meeting",
                    category="event",
                    due_date=iso,
                    deadline_type="reminder",
                    notes=f"Board meeting \u2014 {cursor.strftime('%A %-d %B %Y')} at the Aald Sk\u00fcl",
                    recurrence="fortnightly",
                    reminder_days="[7,1]",
                    created_by="system",
                    event_time="19:00",
                )
                existing.add(iso)
                added += 1
        cursor += timedelta(days=14)

    log.info("scan_and_add_board_meetings: added %d new entries", added)
    return added


# ── Seed data ─────────────────────────────────────────────────────────────────


async def prepopulate_deadlines() -> None:
    """Insert known CDCN obligations on first run (idempotent)."""
    await _ensure_schema()
    async with aiosqlite.connect(_DB_PATH) as conn:
        count = (await (await conn.execute("SELECT COUNT(*) FROM deadlines")).fetchone())[0]
    if count > 0:
        return

    seeds = [
        ("AGM \u2014 Annual General Meeting", "statutory", "2027-03-31",
         "hard_deadline", "", "Annual requirement under company law", "annual"),
        ("OSCR Annual Return", "statutory", "2026-12-31",
         "hard_deadline", "", "Due within 9 months of financial year end", "annual"),
        ("Companies House Confirmation Statement", "statutory", "2026-12-31",
         "hard_deadline", "", "Annual confirmation statement", "annual"),
        ("Public Liability Insurance Renewal", "contractual", "2026-09-30",
         "hard_deadline", "", "Annual insurance renewal", "annual"),
        ("HIE Development Officer Grant Report", "funding", "2026-06-30",
         "hard_deadline", "", "Per HIE grant agreement", ""),
        ("AI & Ethics Policy Review", "policy_review", "2027-01-01",
         "soft_deadline", "", "Annual review of AI & Ethics Policy", "annual"),
        ("Safeguarding Policy Review", "policy_review", "2027-01-01",
         "soft_deadline", "", "Annual review of safeguarding policy", "annual"),
        ("CARES Scotland Grant Report", "funding", "2026-09-30",
         "hard_deadline", "", "CARES Scotland \u00a32,000 grant reporting", ""),
        ("Shetland Community Benefit Fund Report", "funding", "2026-12-31",
         "hard_deadline", "", "Shetland CBF \u00a315,492 grant reporting", ""),
    ]
    for title, category, due_date, deadline_type, assigned_to, notes, recurrence in seeds:
        await add_deadline(
            title=title, category=category, due_date=due_date,
            deadline_type=deadline_type, assigned_to=assigned_to,
            notes=notes, recurrence=recurrence, created_by="system",
        )
    log.info("DeadlineTracker: seeded %d initial deadlines", len(seeds))
