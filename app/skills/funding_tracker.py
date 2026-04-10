"""
FundingTrackerSkill — track funding applications through their lifecycle stages.

Database: /var/lib/cdcn-agent/tracker.db  (shared with deadline_tracker, action_tracker)

Stages: draft -> internal_review -> submitted -> acknowledged ->
        decision_pending -> awarded/declined/withdrawn
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path

import aiosqlite

from app.config import settings
from app.skills.base import BaseSkill, SkillResult

log = logging.getLogger(__name__)

_DB_PATH = str(Path(settings.audit_log_path).parent / "tracker.db")

_DDL = """
CREATE TABLE IF NOT EXISTS applications (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    app_id            TEXT UNIQUE NOT NULL,
    funder            TEXT NOT NULL,
    programme         TEXT NOT NULL DEFAULT '',
    amount_requested  REAL DEFAULT 0,
    stage             TEXT DEFAULT 'draft',
    assigned_to       TEXT DEFAULT '',
    deadline          TEXT DEFAULT '',
    submitted_date    TEXT,
    decision_date     TEXT,
    amount_awarded    REAL,
    notes             TEXT DEFAULT '',
    created_at        TEXT DEFAULT (datetime('now')),
    updated_at        TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_app_stage ON applications(stage);
CREATE INDEX IF NOT EXISTS idx_app_funder ON applications(funder);
"""

VALID_STAGES = frozenset({
    "draft", "internal_review", "submitted", "acknowledged",
    "decision_pending", "awarded", "declined", "withdrawn",
})

# Allowed forward transitions.  Terminal stages (awarded/declined/withdrawn)
# have no onward transitions.  "withdrawn" can happen from any active stage.
_TRANSITIONS: dict[str, set[str]] = {
    "draft":            {"internal_review", "submitted", "withdrawn"},
    "internal_review":  {"submitted", "withdrawn"},
    "submitted":        {"acknowledged", "decision_pending", "awarded", "declined", "withdrawn"},
    "acknowledged":     {"decision_pending", "awarded", "declined", "withdrawn"},
    "decision_pending": {"awarded", "declined", "withdrawn"},
    "awarded":          set(),
    "declined":         set(),
    "withdrawn":        set(),
}


# ── Schema ────────────────────────────────────────────────────────────────────


async def _ensure_schema() -> None:
    async with aiosqlite.connect(_DB_PATH) as conn:
        await conn.executescript(_DDL)
        await conn.commit()


# ── ID generation ─────────────────────────────────────────────────────────────


async def _next_app_id() -> str:
    """Generate FA-YYYYMMDD-NNN where NNN increments within the day."""
    today = date.today().strftime("%Y%m%d")
    prefix = f"FA-{today}-"
    async with aiosqlite.connect(_DB_PATH) as conn:
        row = await (
            await conn.execute(
                "SELECT COUNT(*) FROM applications WHERE app_id LIKE ?",
                (f"{prefix}%",),
            )
        ).fetchone()
        seq = (row[0] if row else 0) + 1
    return f"{prefix}{seq:03d}"


# ── CRUD ──────────────────────────────────────────────────────────────────────


async def add_application(
    funder: str,
    programme: str = "",
    amount: float = 0,
    deadline: str = "",
    assigned_to: str = "",
    notes: str = "",
) -> dict:
    await _ensure_schema()
    app_id = await _next_app_id()
    async with aiosqlite.connect(_DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute(
            """INSERT INTO applications
               (app_id, funder, programme, amount_requested, stage,
                assigned_to, deadline, notes)
               VALUES (?,?,?,?,'draft',?,?,?)""",
            (app_id, funder, programme, amount, assigned_to, deadline, notes),
        )
        await conn.commit()
        row = await (
            await conn.execute("SELECT * FROM applications WHERE app_id=?", (app_id,))
        ).fetchone()
        return dict(row) if row else {}


async def update_stage(
    app_id: str,
    new_stage: str,
    notes: str = "",
) -> dict | None:
    """Advance an application to a new stage, validating the transition."""
    await _ensure_schema()
    new_stage = new_stage.lower().strip()
    if new_stage not in VALID_STAGES:
        raise ValueError(f"Invalid stage '{new_stage}'. Valid: {', '.join(sorted(VALID_STAGES))}")

    async with aiosqlite.connect(_DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        row = await (
            await conn.execute("SELECT * FROM applications WHERE app_id=?", (app_id,))
        ).fetchone()
        if not row:
            return None
        current = row["stage"]

    allowed = _TRANSITIONS.get(current, set())
    if new_stage not in allowed:
        raise ValueError(
            f"Cannot move from '{current}' to '{new_stage}'. "
            f"Allowed transitions: {', '.join(sorted(allowed)) or 'none (terminal stage)'}"
        )

    now = datetime.utcnow().isoformat()
    updates: dict[str, str | None] = {"stage": new_stage, "updated_at": now}

    if new_stage == "submitted":
        updates["submitted_date"] = now
    if new_stage in ("awarded", "declined"):
        updates["decision_date"] = now

    if notes:
        existing_notes = row["notes"] or ""
        sep = " | " if existing_notes else ""
        updates["notes"] = f"{existing_notes}{sep}[{new_stage}] {notes}"

    set_clause = ", ".join(f"{k}=?" for k in updates)
    params = list(updates.values()) + [app_id]
    async with aiosqlite.connect(_DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute(
            f"UPDATE applications SET {set_clause} WHERE app_id=?", params
        )
        await conn.commit()
        row = await (
            await conn.execute("SELECT * FROM applications WHERE app_id=?", (app_id,))
        ).fetchone()
        return dict(row) if row else None


async def list_applications(
    stage: str | None = None,
    funder: str | None = None,
) -> list[dict]:
    await _ensure_schema()
    clauses: list[str] = []
    params: list = []
    if stage:
        clauses.append("stage = ?")
        params.append(stage)
    if funder:
        clauses.append("funder LIKE ?")
        params.append(f"%{funder}%")
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    async with aiosqlite.connect(_DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        rows = await (
            await conn.execute(
                f"SELECT * FROM applications {where} ORDER BY created_at DESC", params
            )
        ).fetchall()
        return [dict(r) for r in rows]


async def get_application(app_id: str) -> dict | None:
    await _ensure_schema()
    async with aiosqlite.connect(_DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        row = await (
            await conn.execute("SELECT * FROM applications WHERE app_id=?", (app_id,))
        ).fetchone()
        return dict(row) if row else None


async def get_pipeline_summary() -> dict:
    """Return counts and total amounts by stage."""
    await _ensure_schema()
    async with aiosqlite.connect(_DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        rows = await (
            await conn.execute(
                "SELECT stage, COUNT(*) as count, "
                "COALESCE(SUM(amount_requested), 0) as total_requested, "
                "COALESCE(SUM(amount_awarded), 0) as total_awarded "
                "FROM applications GROUP BY stage ORDER BY stage"
            )
        ).fetchall()
        stages = {r["stage"]: dict(r) for r in rows}

        # Grand totals
        totals_row = await (
            await conn.execute(
                "SELECT COUNT(*) as total_apps, "
                "COALESCE(SUM(amount_requested), 0) as total_requested, "
                "COALESCE(SUM(amount_awarded), 0) as total_awarded "
                "FROM applications"
            )
        ).fetchone()
        totals = dict(totals_row) if totals_row else {}

    return {"by_stage": stages, "totals": totals}


async def check_stale_applications(days: int = 30) -> list[dict]:
    """Find applications stuck in a non-terminal stage for more than `days` days."""
    await _ensure_schema()
    terminal = ("awarded", "declined", "withdrawn")
    cutoff = datetime.utcnow()
    from datetime import timedelta
    cutoff_iso = (cutoff - timedelta(days=days)).isoformat()
    placeholders = ", ".join("?" * len(terminal))
    async with aiosqlite.connect(_DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        rows = await (
            await conn.execute(
                f"SELECT * FROM applications "
                f"WHERE stage NOT IN ({placeholders}) "
                f"AND updated_at < ? "
                f"ORDER BY updated_at ASC",
                list(terminal) + [cutoff_iso],
            )
        ).fetchall()
        return [dict(r) for r in rows]


# ── Skill ─────────────────────────────────────────────────────────────────────


class FundingTrackerSkill(BaseSkill):
    """Track funding applications through their lifecycle stages."""
    name = "funding_tracker"
    description = (
        "Track funding applications from draft to outcome. "
        "Actions: add (new application), advance (change stage), list, get, "
        "pipeline (summary by stage), stale (stuck applications). "
        "Use for 'add a funding application for HIE' or 'what is our funding pipeline?'."
    )

    async def run(self, action_or_dict=None, **kwargs) -> SkillResult:
        if isinstance(action_or_dict, dict):
            kwargs.update(action_or_dict)
            action = kwargs.pop("action", "list")
        else:
            action = action_or_dict or kwargs.pop("action", "list")

        try:
            if action == "add":
                funder = kwargs.get("funder", "")
                if not funder:
                    return SkillResult(success=False, error="'funder' is required.")
                r = await add_application(
                    funder=funder,
                    programme=kwargs.get("programme", ""),
                    amount=float(kwargs.get("amount", 0)),
                    deadline=kwargs.get("deadline", ""),
                    assigned_to=kwargs.get("assigned_to", ""),
                    notes=kwargs.get("notes", ""),
                )
                return SkillResult(
                    success=True,
                    output=(
                        f"Created application **{r['app_id']}**: "
                        f"{r['funder']} — {r['programme'] or 'General'} "
                        f"(£{r['amount_requested']:,.0f})"
                    ),
                    metadata=r,
                )

            elif action == "advance":
                app_id = kwargs.get("app_id", "")
                new_stage = kwargs.get("stage", "")
                if not app_id or not new_stage:
                    return SkillResult(
                        success=False,
                        error="'app_id' and 'stage' are required for advance.",
                    )
                r = await update_stage(
                    app_id=app_id,
                    new_stage=new_stage,
                    notes=kwargs.get("notes", ""),
                )
                if r is None:
                    return SkillResult(success=False, error=f"Application {app_id} not found.")
                return SkillResult(
                    success=True,
                    output=f"**{r['app_id']}** advanced to **{r['stage']}**.",
                    metadata=r,
                )

            elif action == "list":
                items = await list_applications(
                    stage=kwargs.get("stage"),
                    funder=kwargs.get("funder"),
                )
                if not items:
                    return SkillResult(
                        success=True,
                        output="No funding applications found.",
                        metadata={"count": 0},
                    )
                _icons = {
                    "draft": "📝", "internal_review": "🔍",
                    "submitted": "📤", "acknowledged": "📬",
                    "decision_pending": "⏳", "awarded": "✅",
                    "declined": "❌", "withdrawn": "↩️",
                }
                lines = [f"**Funding Applications ({len(items)}):**\n"]
                for a in items:
                    icon = _icons.get(a["stage"], "•")
                    amt = f"£{a['amount_requested']:,.0f}" if a["amount_requested"] else "TBC"
                    who = f" [{a['assigned_to']}]" if a.get("assigned_to") else ""
                    lines.append(
                        f"{icon} **{a['app_id']}** — {a['funder']}: "
                        f"{a['programme'] or 'General'} ({amt}){who} — *{a['stage']}*"
                    )
                return SkillResult(
                    success=True,
                    output="\n".join(lines),
                    metadata={"count": len(items)},
                )

            elif action == "get":
                app_id = kwargs.get("app_id", "")
                if not app_id:
                    return SkillResult(success=False, error="'app_id' is required.")
                r = await get_application(app_id)
                if not r:
                    return SkillResult(success=False, error=f"Application {app_id} not found.")
                awarded = f"£{r['amount_awarded']:,.0f}" if r.get("amount_awarded") else "—"
                lines = [
                    f"**{r['app_id']}** — {r['funder']}: {r['programme'] or 'General'}",
                    f"  Stage: **{r['stage']}**",
                    f"  Amount requested: £{r['amount_requested']:,.0f}",
                    f"  Amount awarded: {awarded}",
                    f"  Assigned to: {r['assigned_to'] or '—'}",
                    f"  Deadline: {r['deadline'] or '—'}",
                    f"  Submitted: {r['submitted_date'] or '—'}",
                    f"  Decision: {r['decision_date'] or '—'}",
                    f"  Notes: {r['notes'] or '—'}",
                    f"  Created: {r['created_at']}",
                    f"  Updated: {r['updated_at']}",
                ]
                return SkillResult(
                    success=True,
                    output="\n".join(lines),
                    metadata=r,
                )

            elif action == "pipeline":
                summary = await get_pipeline_summary()
                by_stage = summary["by_stage"]
                totals = summary["totals"]
                if not by_stage:
                    return SkillResult(
                        success=True,
                        output="No funding applications in the pipeline.",
                        metadata=summary,
                    )
                _icons = {
                    "draft": "📝", "internal_review": "🔍",
                    "submitted": "📤", "acknowledged": "📬",
                    "decision_pending": "⏳", "awarded": "✅",
                    "declined": "❌", "withdrawn": "↩️",
                }
                lines = ["**Funding Pipeline Summary:**\n"]
                for stage_name, data in by_stage.items():
                    icon = _icons.get(stage_name, "•")
                    lines.append(
                        f"{icon} **{stage_name}**: {data['count']} "
                        f"(£{data['total_requested']:,.0f} requested)"
                    )
                lines.append(
                    f"\n**Totals**: {totals['total_apps']} applications, "
                    f"£{totals['total_requested']:,.0f} requested, "
                    f"£{totals['total_awarded']:,.0f} awarded"
                )
                return SkillResult(
                    success=True,
                    output="\n".join(lines),
                    metadata=summary,
                )

            elif action == "stale":
                days = int(kwargs.get("days", 30))
                items = await check_stale_applications(days=days)
                if not items:
                    return SkillResult(
                        success=True,
                        output=f"No stale applications (threshold: {days} days).",
                        metadata={"count": 0},
                    )
                lines = [f"**Stale Applications ({len(items)}, >{days} days without update):**\n"]
                for a in items:
                    lines.append(
                        f"⚠️ **{a['app_id']}** — {a['funder']}: {a['programme'] or 'General'} "
                        f"— stuck in *{a['stage']}* since {a['updated_at'][:10]}"
                    )
                return SkillResult(
                    success=True,
                    output="\n".join(lines),
                    metadata={"count": len(items)},
                )

            else:
                return SkillResult(
                    success=False,
                    error=f"Unknown action: {action}. Use: add, advance, list, get, pipeline, stale",
                )

        except ValueError as exc:
            return SkillResult(success=False, error=str(exc))
        except Exception as exc:
            log.exception("FundingTrackerSkill.run failed")
            return SkillResult(success=False, error=str(exc))
