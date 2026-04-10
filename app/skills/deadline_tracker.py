"""
DeadlineTrackerSkill -- track CDCN statutory, funding, and policy obligations.

Database: /var/lib/cdcn-agent/tracker.db  (shared with action_tracker, funding)

Categories: funding, statutory, policy_review, contractual, event, meeting, other
  'event'   -- fixed historical calendar events (e.g. board meetings scanned from minutes)
  'meeting' -- user-created calendar events

CRUD operations, schema, and seed data live in deadline_tracker_db.py.
"""
from __future__ import annotations

import logging

from app.skills.base import BaseSkill, SkillResult

# Re-export all public symbols from the DB module so existing imports work
from app.skills.deadline_tracker_db import (  # noqa: F401
    _DB_PATH,
    _INDEXER_DB,
    VALID_CATEGORIES,
    _ensure_schema,
    add_deadline,
    list_deadlines,
    get_deadline,
    update_deadline,
    delete_deadline,
    mark_complete,
    check_overdue,
    get_calendar_data,
    scan_and_add_board_meetings,
    prepopulate_deadlines,
)

log = logging.getLogger(__name__)


# ── Skill ─────────────────────────────────────────────────────────────────────


class DeadlineTrackerSkill(BaseSkill):
    name = "deadline_tracker"
    description = (
        "Track CDCN obligations and deadlines. Actions: list (upcoming deadlines), "
        "add (new deadline), complete (mark done), overdue (check overdue). "
        "Use for 'what deadlines do we have this month?' or 'add a deadline for the AGM'."
    )

    async def run(self, action_or_dict=None, **kwargs) -> SkillResult:
        if isinstance(action_or_dict, dict):
            kwargs.update(action_or_dict)
            action = kwargs.pop("action", "list")
        else:
            action = action_or_dict or kwargs.pop("action", "list")

        try:
            if action == "add":
                r = await add_deadline(
                    title=kwargs.get("title", ""),
                    category=kwargs.get("category", "other"),
                    due_date=kwargs.get("due_date", ""),
                    deadline_type=kwargs.get("deadline_type", "hard_deadline"),
                    assigned_to=kwargs.get("assigned_to", ""),
                    notes=kwargs.get("notes", ""),
                    created_by=kwargs.get("created_by", "agent"),
                )
                return SkillResult(
                    success=True,
                    output=f"Added deadline: **{r['title']}** due {r['due_date']} ({r['category']})",
                    metadata=r,
                )

            elif action == "list":
                items = await list_deadlines(
                    status=kwargs.get("status"),
                    category=kwargs.get("category"),
                    days_ahead=int(kwargs.get("days_ahead", 90)),
                )
                if not items:
                    return SkillResult(success=True, output="No deadlines found.", metadata={"count": 0})
                _icons = {"pending": "\u23f3", "in_progress": "\U0001f504", "completed": "\u2705",
                          "overdue": "\U0001f534", "deferred": "\u23f8\ufe0f"}
                lines = [f"**Upcoming Deadlines ({len(items)}):**\n"]
                for d in items:
                    icon = _icons.get(d["status"], "\u2022")
                    assigned = f" \u2014 {d['assigned_to']}" if d.get("assigned_to") else ""
                    lines.append(
                        f"{icon} **{d['title']}** \u2014 {d['due_date']} ({d['category']}){assigned}"
                    )
                return SkillResult(success=True, output="\n".join(lines), metadata={"count": len(items)})

            elif action == "complete":
                ok = await mark_complete(int(kwargs.get("id", 0)), kwargs.get("notes", ""))
                if ok:
                    return SkillResult(success=True, output=f"Deadline marked as complete.")
                return SkillResult(success=False, error="Deadline not found or already complete.")

            elif action == "overdue":
                items = await check_overdue()
                if not items:
                    return SkillResult(success=True, output="No overdue deadlines \u2014 all good.", metadata={"count": 0})
                lines = [f"\u26a0\ufe0f **{len(items)} overdue deadline(s):**\n"]
                for d in items:
                    lines.append(f"\U0001f534 **{d['title']}** \u2014 was due {d['due_date']} ({d['category']})")
                return SkillResult(success=True, output="\n".join(lines), metadata={"count": len(items)})

            else:
                return SkillResult(success=False, error=f"Unknown action: {action}. Use: list, add, complete, overdue")

        except Exception as exc:
            log.exception("DeadlineTrackerSkill.run failed")
            return SkillResult(success=False, error=str(exc))
