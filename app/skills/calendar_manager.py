"""
CalendarManagerSkill — calendar and event management for CDCN.

Wraps the deadline_tracker database with calendar-specific operations:
  • List events for a month
  • Add / update / delete events and deadlines
  • Upcoming events summary

Uses the same tracker.db as deadline_tracker — they share data.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from app.skills.base import BaseSkill, SkillResult
from app.skills.deadline_tracker import (
    VALID_CATEGORIES,
    add_deadline,
    delete_deadline,
    get_calendar_data,
    list_deadlines,
    mark_complete,
    update_deadline,
)

log = logging.getLogger(__name__)


class CalendarManagerSkill(BaseSkill):
    name = "calendar_manager"
    description = (
        "Manage the CDCN calendar — add events and meetings, view upcoming "
        "deadlines, and browse the monthly calendar. Categories: "
        "event, meeting, funding, statutory, policy_review, contractual, other."
    )

    async def run(self, **kwargs) -> SkillResult:
        action = kwargs.get("action", "month")

        dispatch = {
            "month": self._month,
            "upcoming": self._upcoming,
            "add": self._add,
            "update": self._update,
            "delete": self._delete,
            "complete": self._complete,
        }
        handler = dispatch.get(action)
        if not handler:
            return SkillResult(
                success=False,
                error=f"Unknown action '{action}'. Use: {', '.join(dispatch)}",
            )
        try:
            return await handler(kwargs)
        except Exception as exc:
            log.exception("CalendarManager error: %s", exc)
            return SkillResult(success=False, error=str(exc))

    async def _month(self, kw: dict) -> SkillResult:
        """Get all events/deadlines for a given month."""
        today = date.today()
        year = int(kw.get("year", today.year))
        month = int(kw.get("month", today.month))
        events = await get_calendar_data(year, month)
        return SkillResult(
            success=True,
            output={
                "year": year,
                "month": month,
                "events": events,
                "count": len(events),
            },
        )

    async def _upcoming(self, kw: dict) -> SkillResult:
        """Get upcoming events/deadlines within N days."""
        days = int(kw.get("days", 30))
        category = kw.get("category")
        events = await list_deadlines(days_ahead=days, category=category)
        return SkillResult(
            success=True,
            output={"days": days, "events": events, "count": len(events)},
        )

    async def _add(self, kw: dict) -> SkillResult:
        """Add a new event or deadline."""
        title = kw.get("title", "").strip()
        if not title:
            return SkillResult(success=False, error="'title' is required.")
        due_date = kw.get("due_date") or kw.get("date", "")
        if not due_date:
            return SkillResult(success=False, error="'due_date' is required (YYYY-MM-DD).")
        category = kw.get("category", "event")
        if category not in VALID_CATEGORIES:
            return SkillResult(
                success=False,
                error=f"Invalid category '{category}'. Use: {', '.join(sorted(VALID_CATEGORIES))}",
            )

        event = await add_deadline(
            title=title,
            category=category,
            due_date=due_date,
            deadline_type=kw.get("deadline_type", "reminder"),
            assigned_to=kw.get("assigned_to", ""),
            notes=kw.get("notes", ""),
            created_by=kw.get("created_by", "web"),
            event_time=kw.get("event_time", ""),
        )
        return SkillResult(success=True, output=event)

    async def _update(self, kw: dict) -> SkillResult:
        """Update an existing event."""
        event_id = kw.get("id")
        if not event_id:
            return SkillResult(success=False, error="'id' is required.")
        fields = {}
        for key in ("title", "due_date", "category", "notes", "assigned_to",
                     "event_time", "status", "deadline_type"):
            if key in kw and kw[key] is not None:
                fields[key] = kw[key]
        if not fields:
            return SkillResult(success=False, error="No fields to update.")
        result = await update_deadline(int(event_id), **fields)
        if result is None:
            return SkillResult(success=False, error=f"Event {event_id} not found.")
        return SkillResult(success=True, output=result)

    async def _delete(self, kw: dict) -> SkillResult:
        """Delete an event."""
        event_id = kw.get("id")
        if not event_id:
            return SkillResult(success=False, error="'id' is required.")
        ok = await delete_deadline(int(event_id))
        if not ok:
            return SkillResult(success=False, error=f"Event {event_id} not found.")
        return SkillResult(success=True, output={"deleted": int(event_id)})

    async def _complete(self, kw: dict) -> SkillResult:
        """Mark an event/deadline as completed."""
        event_id = kw.get("id")
        if not event_id:
            return SkillResult(success=False, error="'id' is required.")
        ok = await mark_complete(int(event_id), notes=kw.get("notes", ""))
        if not ok:
            return SkillResult(success=False, error=f"Event {event_id} not found.")
        return SkillResult(success=True, output={"completed": int(event_id)})
