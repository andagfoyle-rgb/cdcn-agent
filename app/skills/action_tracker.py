"""
ActionTrackerSkill — track action points extracted from CDCN meeting minutes.

Database: /var/lib/cdcn-agent/tracker.db  (shared with deadline_tracker, funding)
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path

import aiosqlite

from app.config import settings
from app.skills.base import BaseSkill, SkillResult

log = logging.getLogger(__name__)

_DB_PATH = str(Path(settings.audit_log_path).parent / "tracker.db")

_DDL = """
CREATE TABLE IF NOT EXISTS action_points (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    action_id         TEXT UNIQUE NOT NULL,
    meeting_date      TEXT,
    meeting_document  TEXT,
    description       TEXT NOT NULL,
    assigned_to       TEXT,
    due_date          TEXT,
    status            TEXT DEFAULT 'open' CHECK(status IN
                          ('open','in_progress','completed','deferred','closed')),
    priority          TEXT DEFAULT 'medium' CHECK(priority IN ('high','medium','low')),
    resolution_notes  TEXT,
    resolved_at       TEXT,
    created_at        TEXT DEFAULT (datetime('now')),
    updated_at        TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_ap_status ON action_points(status);
CREATE INDEX IF NOT EXISTS idx_ap_meeting_date ON action_points(meeting_date);
"""

# Valid priority values from the "Actions Required" table in CDCN minutes.
# The table uses: Urgent, Soon, Ongoing, When convenient / When appropriate.
# We map these to the DB schema values (high, medium, low).
_PRIORITY_MAP = {
    "urgent": "high",
    "soon": "medium",
    "ongoing": "medium",
    "when convenient": "low",
    "when appropriate": "low",
}


def _make_action_id(meeting_date: str, index: int) -> str:
    safe = (meeting_date or "unknown").replace("-", "")
    return f"AP-{safe}-{index:03d}"


# ── Schema ────────────────────────────────────────────────────────────────────


async def _ensure_schema() -> None:
    async with aiosqlite.connect(_DB_PATH) as conn:
        await conn.executescript(_DDL)
        await conn.commit()


# ── CRUD ──────────────────────────────────────────────────────────────────────


async def add_action(
    description: str,
    assigned_to: str = "",
    due_date: str = "",
    meeting_date: str = "",
    meeting_document: str = "",
    priority: str = "medium",
    action_id: str = "",
) -> dict:
    await _ensure_schema()
    if not action_id:
        async with aiosqlite.connect(_DB_PATH) as conn:
            row = await (
                await conn.execute(
                    "SELECT COUNT(*) FROM action_points WHERE meeting_date=?", (meeting_date,)
                )
            ).fetchone()
            action_id = _make_action_id(meeting_date, (row[0] if row else 0) + 1)
    async with aiosqlite.connect(_DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute(
            """INSERT OR IGNORE INTO action_points
               (action_id, meeting_date, meeting_document, description,
                assigned_to, due_date, status, priority)
               VALUES (?,?,?,?,?,?,'open',?)""",
            (action_id, meeting_date, meeting_document, description,
             assigned_to, due_date, priority),
        )
        await conn.commit()
        row = await (
            await conn.execute("SELECT * FROM action_points WHERE action_id=?", (action_id,))
        ).fetchone()
        return dict(row) if row else {}


async def list_actions(
    status: str | None = None,
    assigned_to: str | None = None,
    meeting_date: str | None = None,
) -> list[dict]:
    await _ensure_schema()
    clauses: list[str] = []
    params: list = []
    if status:
        clauses.append("status = ?")
        params.append(status)
    if assigned_to:
        clauses.append("assigned_to LIKE ?")
        params.append(f"%{assigned_to}%")
    if meeting_date:
        clauses.append("meeting_date = ?")
        params.append(meeting_date)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    async with aiosqlite.connect(_DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        rows = await (
            await conn.execute(
                f"SELECT * FROM action_points {where} ORDER BY meeting_date DESC, action_id ASC",
                params,
            )
        ).fetchall()
        return [dict(r) for r in rows]


async def get_action(action_id: str) -> dict | None:
    await _ensure_schema()
    async with aiosqlite.connect(_DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        row = await (
            await conn.execute("SELECT * FROM action_points WHERE action_id=?", (action_id,))
        ).fetchone()
        return dict(row) if row else None


async def update_action(action_id: str, **fields) -> dict | None:
    await _ensure_schema()
    allowed = {"status", "assigned_to", "due_date", "priority",
               "resolution_notes", "description", "meeting_date"}
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not updates:
        return await get_action(action_id)
    now = datetime.utcnow().isoformat()
    updates["updated_at"] = now
    if updates.get("status") in ("completed", "closed"):
        updates.setdefault("resolved_at", now)
    set_clause = ", ".join(f"{k}=?" for k in updates)
    params = list(updates.values()) + [action_id]
    async with aiosqlite.connect(_DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute(
            f"UPDATE action_points SET {set_clause} WHERE action_id=?", params
        )
        await conn.commit()
        row = await (
            await conn.execute("SELECT * FROM action_points WHERE action_id=?", (action_id,))
        ).fetchone()
        return dict(row) if row else None


async def delete_action(action_id: str) -> bool:
    await _ensure_schema()
    async with aiosqlite.connect(_DB_PATH) as conn:
        await conn.execute("DELETE FROM action_points WHERE action_id=?", (action_id,))
        await conn.commit()
        return conn.total_changes > 0


def _is_priority(text: str) -> str | None:
    """If *text* is a known priority keyword, return the normalised form; else None."""
    candidate = text.lower().strip().rstrip(".")
    return candidate if candidate in _PRIORITY_MAP else None


def _is_noise(text: str) -> bool:
    """Return True for page numbers, column headers, and signature blocks."""
    low = text.lower().strip()
    if low in ("who", "action", "priority"):
        return True
    if low.startswith("page ") and low[5:].strip().isdigit():
        return True
    if low.startswith("approval") or low.startswith("minutes approved") or low.startswith("date:"):
        return True
    if "signature:" in low or "____" in text:
        return True
    return False


def extract_actions_from_pdf(
    pdf_path: str, meeting_date: str = "",
) -> list[dict]:
    """Extract action points from the 'Actions Required' table in a CDCN
    meeting minutes PDF.

    Handles two layouts:
      - Inline:   Who, Action, Priority, Who, Action, Priority, ...
      - Trailing: Who, Action, Who, Action, ... Priority, Priority, ...
    """
    from unstructured.partition.pdf import partition_pdf

    elements = partition_pdf(pdf_path, languages=["eng"])
    meeting_document = Path(pdf_path).name

    # Collect all text elements after "Actions Required"
    texts: list[str] = []
    found_section = False
    for e in elements:
        t = str(e).strip()
        if not t:
            continue
        if "Actions Required" in t:
            found_section = True
            continue
        if found_section:
            texts.append(t)

    if not texts:
        log.warning("No 'Actions Required' section found in %s", pdf_path)
        return []

    # Strip noise entries
    texts = [t for t in texts if not _is_noise(t)]

    # Detect layout: check if every 3rd element (starting from index 2) is a
    # priority keyword — if so it's the inline layout.
    inline = False
    if len(texts) >= 3:
        sample = [_is_priority(texts[i]) for i in range(2, min(len(texts), 12), 3)]
        if sample and all(s is not None for s in sample):
            inline = True

    actions: list[dict] = []

    if inline:
        # Triplets: Who, Action, Priority
        i = 0
        while i + 1 < len(texts):
            who = texts[i]
            action_text = texts[i + 1]
            # Priority may be the 3rd element, or embedded at the end of action text
            pri_raw = None
            if i + 2 < len(texts):
                pri_raw = _is_priority(texts[i + 2])
            if pri_raw:
                i += 3
            else:
                # Check if priority is appended to the action text
                for kw in _PRIORITY_MAP:
                    if action_text.lower().rstrip(".").endswith(kw):
                        action_text = action_text[:-(len(kw))].rstrip(". ")
                        pri_raw = kw
                        break
                i += 2
            priority = _PRIORITY_MAP.get(pri_raw or "", "medium")
            actions.append({
                "description": action_text,
                "assigned_to": who,
                "meeting_date": meeting_date,
                "meeting_document": meeting_document,
                "priority": priority,
            })
    else:
        # Trailing layout: Who/Action pairs, then priorities at the end.
        # Peel off trailing priorities.
        priorities: list[str] = []
        while texts and _is_priority(texts[-1]) is not None:
            priorities.insert(0, _is_priority(texts[-1]))  # type: ignore
            texts.pop()
        # Also strip stray "Priority" header that may appear between pages
        texts = [t for t in texts if t.lower().strip() != "priority"]

        # Parse Who/Action pairs.  Handle continuation lines where the PDF
        # splits one action across multiple text elements.
        pairs: list[tuple[str, str]] = []
        i = 0
        while i < len(texts):
            who = texts[i].strip()
            if i + 1 < len(texts):
                next_t = texts[i + 1].strip()
                if len(who) < 60 and len(next_t) > 15:
                    # Absorb continuation lines (start with lowercase = continuation)
                    action_text = next_t
                    j = i + 2
                    while j < len(texts) and texts[j][0].islower():
                        action_text += " " + texts[j].strip()
                        j += 1
                    pairs.append((who, action_text))
                    i = j
                    continue
            # Merged line — try to split "Name Action text" into (name, action).
            # Absorb any lowercase continuation lines first.
            merged = who
            j = i + 1
            while j < len(texts) and texts[j][0].islower():
                merged += " " + texts[j].strip()
                j += 1
            # Try to split at a known name boundary
            name_pat = re.match(
                r"^((?:[\w]+(?:\s*/\s*[\w]+)?(?:\s+[\w]+)?))\s+((?:Ensure|Continue|Speak|Contact|Reply|Arrange|Begin|Transfer|Apply|Finalise|Consider|Resubmit|Complete|Communicate|Conduct|Collate|Locate|Draft|Explore|Coordinate|Prepare|Manage|Upload|Pursue|Plant|Adopt|Submit|Review|Follow|Send|Meet|Ask|Seek|Confirm)[a-z]*\s.+)",
                merged, re.IGNORECASE,
            )
            if name_pat:
                pairs.append((name_pat.group(1).strip(), name_pat.group(2).strip()))
            else:
                pairs.append(("", merged))
            i = j

        for idx, (who, action_text) in enumerate(pairs):
            pri_raw = priorities[idx] if idx < len(priorities) else None
            priority = _PRIORITY_MAP.get(pri_raw or "", "medium")
            actions.append({
                "description": action_text,
                "assigned_to": who,
                "meeting_date": meeting_date,
                "meeting_document": meeting_document,
                "priority": priority,
            })

    return actions


# ── Skill ─────────────────────────────────────────────────────────────────────


class ActionTrackerSkill(BaseSkill):
    name = "action_tracker"
    description = (
        "Track action points from CDCN meetings. Actions: list (open actions), "
        "add (new action), complete (mark done). "
        "Use for 'what actions are outstanding?' or 'add an action for Ellis to contact the electrician'."
    )

    async def run(self, action_or_dict=None, **kwargs) -> SkillResult:
        if isinstance(action_or_dict, dict):
            kwargs.update(action_or_dict)
            action = kwargs.pop("action", "list")
        else:
            action = action_or_dict or kwargs.pop("action", "list")

        try:
            if action == "add":
                r = await add_action(
                    description=kwargs.get("description", ""),
                    assigned_to=kwargs.get("assigned_to", ""),
                    due_date=kwargs.get("due_date", ""),
                    meeting_date=kwargs.get("meeting_date", ""),
                    priority=kwargs.get("priority", "medium"),
                )
                return SkillResult(
                    success=True,
                    output=f"Added action **{r.get('action_id')}**: {r.get('description', '')[:80]}",
                    metadata=r,
                )

            elif action == "list":
                items = await list_actions(
                    status=kwargs.get("status", "open") if not kwargs.get("all_statuses") else None,
                    assigned_to=kwargs.get("assigned_to"),
                    meeting_date=kwargs.get("meeting_date"),
                )
                if not items:
                    return SkillResult(success=True, output="No open action points.", metadata={"count": 0})
                _icons = {"open": "📋", "in_progress": "🔄", "completed": "✅",
                          "deferred": "⏸️", "closed": "🔒"}
                lines = [f"**Action Points ({len(items)}):**\n"]
                for a in items:
                    icon = _icons.get(a["status"], "•")
                    due = f" — due {a['due_date']}" if a.get("due_date") else ""
                    who = f" [{a['assigned_to']}]" if a.get("assigned_to") else ""
                    lines.append(
                        f"{icon} **{a['action_id']}**{who}: {a['description'][:80]}{due}"
                    )
                return SkillResult(success=True, output="\n".join(lines), metadata={"count": len(items)})

            elif action == "complete":
                r = await update_action(
                    kwargs.get("action_id", ""),
                    status="completed",
                    resolution_notes=kwargs.get("notes", ""),
                )
                if r:
                    return SkillResult(success=True, output=f"Action {r['action_id']} marked complete.")
                return SkillResult(success=False, error="Action not found.")

            elif action == "extract":
                pdf_path = kwargs.get("pdf_path", "")
                meeting_date = kwargs.get("meeting_date", "")
                if not pdf_path:
                    return SkillResult(success=False, error="pdf_path is required for extract.")
                candidates = extract_actions_from_pdf(pdf_path, meeting_date=meeting_date)
                added = []
                for c in candidates:
                    r = await add_action(
                        description=c["description"],
                        assigned_to=c["assigned_to"],
                        meeting_date=c["meeting_date"],
                        meeting_document=c.get("meeting_document", ""),
                        priority=c.get("priority", "medium"),
                    )
                    if r:
                        added.append(r)
                return SkillResult(
                    success=True,
                    output=f"Extracted and added {len(added)} action points from {Path(pdf_path).name}.",
                    metadata={"count": len(added), "actions": added},
                )

            else:
                return SkillResult(success=False, error=f"Unknown action: {action}. Use: list, add, complete, extract")

        except Exception as exc:
            log.exception("ActionTrackerSkill.run failed")
            return SkillResult(success=False, error=str(exc))
