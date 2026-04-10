"""
CronjobManagerSkill — manage scheduled jobs via chat.

Authorised users (admin/staff) can:
  - list     — show all active scheduled jobs with next run times
  - add      — create a new recurring notification/reminder job
  - remove   — delete a user-created job (system jobs are protected)
  - pause    — temporarily pause a job
  - resume   — resume a paused job

User-created jobs are stored in a SQLite table so they survive restarts.
System jobs (heartbeat, auto_index, journal, etc.) cannot be deleted
but can be paused/resumed.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.skills.base import BaseSkill, SkillResult

log = logging.getLogger(__name__)


def _db_path() -> Path:
    from app.config import settings
    return Path(settings.audit_log_path).parent / "cronjobs.db"

# System job IDs that cannot be deleted (only paused/resumed)
_SYSTEM_JOBS = frozenset({
    "wake", "sleep", "heartbeat", "auto_index", "journal",
    "weekly_digest", "monthly_governance", "overdue_check",
    "funding_feed_am", "funding_feed_noon", "session_archive",
    "nightly_backup",
})

_DDL = """\
CREATE TABLE IF NOT EXISTS user_jobs (
    job_id       TEXT PRIMARY KEY,
    description  TEXT NOT NULL,
    schedule     TEXT NOT NULL,
    message      TEXT NOT NULL,
    channels     TEXT NOT NULL DEFAULT 'all',
    created_by   TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    active       INTEGER NOT NULL DEFAULT 1
);
"""


async def _open_db():
    import aiosqlite
    db = await aiosqlite.connect(str(_db_path()))
    await db.execute(_DDL)
    await db.commit()
    return db


def _parse_schedule(schedule: str):
    """Parse a human-friendly schedule string into an APScheduler trigger.

    Returns a CronTrigger or IntervalTrigger.

    Supported formats:
      - "every 6 hours" / "every 30 minutes"
      - "daily at 09:00"
      - "weekly monday 08:00"
      - "monthly 1st 09:00"
      - Raw cron: "cron 0 9 * * mon-fri"
    """
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger

    s = schedule.lower().strip()

    # Interval: "every N hours/minutes"
    if s.startswith("every "):
        parts = s.split()
        if len(parts) >= 3:
            try:
                n = int(parts[1])
            except ValueError:
                raise ValueError(f"Cannot parse interval number: {parts[1]}")
            unit = parts[2].rstrip("s")  # hours -> hour
            if unit == "hour":
                return IntervalTrigger(hours=n)
            elif unit == "minute":
                return IntervalTrigger(minutes=n)
            elif unit == "day":
                return IntervalTrigger(days=n)
        raise ValueError(f"Cannot parse interval: {schedule}")

    # Daily: "daily at HH:MM"
    if s.startswith("daily"):
        time_part = s.replace("daily", "").replace("at", "").strip()
        if ":" in time_part:
            h, m = time_part.split(":")
            return CronTrigger(hour=int(h), minute=int(m))
        raise ValueError(f"Cannot parse daily schedule: {schedule}")

    # Weekly: "weekly monday 08:00"
    if s.startswith("weekly"):
        parts = s.split()
        if len(parts) >= 3:
            day = parts[1][:3]  # monday -> mon
            time_part = parts[2]
            h, m = time_part.split(":")
            return CronTrigger(day_of_week=day, hour=int(h), minute=int(m))
        raise ValueError(f"Cannot parse weekly schedule: {schedule}")

    # Monthly: "monthly 1st 09:00" or "monthly 15 09:00"
    if s.startswith("monthly"):
        parts = s.split()
        if len(parts) >= 3:
            day_str = parts[1].rstrip("stndrh")  # 1st -> 1
            time_part = parts[2]
            h, m = time_part.split(":")
            return CronTrigger(day=int(day_str), hour=int(h), minute=int(m))
        raise ValueError(f"Cannot parse monthly schedule: {schedule}")

    # Raw cron: "cron 0 9 * * mon-fri"
    if s.startswith("cron "):
        fields = s[5:].split()
        if len(fields) == 5:
            return CronTrigger.from_crontab(" ".join(fields))
        raise ValueError(f"Cron expression must have 5 fields: {schedule}")

    raise ValueError(
        f"Cannot parse schedule: '{schedule}'. "
        f"Try: 'every 6 hours', 'daily at 09:00', 'weekly monday 08:00', "
        f"'monthly 1st 09:00', or 'cron 0 9 * * mon-fri'"
    )


async def _user_job_callback(job_id: str, message: str, channels: str) -> None:
    """Called by APScheduler when a user-created job fires."""
    log.info("User job fired: %s", job_id)
    try:
        from app.skills.notify import NotifySkill
        notify = NotifySkill()
        await notify.run(
            message=message,
            channels=channels.split(",") if channels != "all" else None,
            source=f"scheduled:{job_id}",
        )
    except Exception as exc:
        log.error("User job %s notification failed: %s", job_id, exc)


def _register_user_job(
    job_id: str, trigger: CronTrigger | IntervalTrigger,
    message: str, channels: str,
) -> None:
    """Register a user job with the running scheduler."""
    from app.skills.scheduler import get_scheduler

    scheduler = get_scheduler()
    scheduler.add_job(
        _user_job_callback,
        trigger,
        id=job_id,
        replace_existing=True,
        args=[job_id, message, channels],
    )


async def restore_user_jobs() -> int:
    """Called at startup to re-register all active user jobs.

    Returns the number of jobs restored.
    """
    try:
        db = await _open_db()
        try:
            cursor = await db.execute(
                "SELECT job_id, schedule, message, channels FROM user_jobs WHERE active = 1"
            )
            rows = await cursor.fetchall()
            count = 0
            for job_id, schedule, message, channels in rows:
                try:
                    trigger = _parse_schedule(schedule)
                    _register_user_job(job_id, trigger, message, channels)
                    count += 1
                except Exception as exc:
                    log.warning("Failed to restore job %s: %s", job_id, exc)
            log.info("Restored %d user-created scheduled jobs", count)
            return count
        finally:
            await db.close()
    except Exception as exc:
        log.warning("Failed to restore user jobs: %s", exc)
        return 0


class CronjobManagerSkill(BaseSkill):
    """
    Manage scheduled jobs via chat.

    Tool definition (for the LLM):
      - action (str, required): "list" | "add" | "remove" | "pause" | "resume"
      - job_id (str): Required for remove/pause/resume.
      - schedule (str): Required for add. E.g. "daily at 09:00".
      - message (str): Required for add. The notification message.
      - description (str): Optional description for add.
      - channels (str): Optional for add. "discord", "telegram", "email", or "all".
    """

    name = "cronjob_manager"
    description = (
        "List, add, remove, pause, or resume scheduled jobs. "
        "Lets authorised users create recurring reminders and notifications."
    )

    async def run(
        self,
        action: str = "list",
        job_id: str = "",
        schedule: str = "",
        message: str = "",
        description: str = "",
        channels: str = "all",
        user_id: str = "",
        **kwargs: Any,
    ) -> SkillResult:
        action = action.lower().strip()

        if action == "list":
            return await self._list_jobs()
        elif action == "add":
            return await self._add_job(
                schedule=schedule,
                message=message,
                description=description,
                channels=channels,
                created_by=user_id or "unknown",
            )
        elif action == "remove":
            return await self._remove_job(job_id)
        elif action == "pause":
            return await self._pause_job(job_id)
        elif action == "resume":
            return await self._resume_job(job_id)
        else:
            return SkillResult(
                success=False,
                error=f"Unknown action: '{action}'. Use list/add/remove/pause/resume.",
            )

    async def _list_jobs(self) -> SkillResult:
        from app.skills.scheduler import get_scheduler

        scheduler = get_scheduler()
        jobs = scheduler.get_jobs()

        lines: list[str] = []
        lines.append(f"**Scheduled Jobs ({len(jobs)} total)**\n")

        for job in sorted(jobs, key=lambda j: j.id):
            next_run = job.next_run_time
            next_str = next_run.strftime("%Y-%m-%d %H:%M UTC") if next_run else "paused"
            is_system = job.id in _SYSTEM_JOBS
            tag = "[system]" if is_system else "[user]"
            lines.append(f"- **{job.id}** {tag} — next: {next_str}")

        return SkillResult(success=True, output="\n".join(lines))

    async def _add_job(
        self,
        schedule: str,
        message: str,
        description: str,
        channels: str,
        created_by: str,
    ) -> SkillResult:
        if not schedule:
            return SkillResult(success=False, error="schedule is required.")
        if not message:
            return SkillResult(success=False, error="message is required.")

        try:
            trigger = _parse_schedule(schedule)
        except ValueError as exc:
            return SkillResult(success=False, error=str(exc))

        # Generate a unique job ID
        ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        job_id = f"user_{ts}_{hash(message) % 10000:04d}"

        # Persist to database
        db = await _open_db()
        try:
            await db.execute(
                "INSERT INTO user_jobs (job_id, description, schedule, message, channels, created_by, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (job_id, description or message[:80], schedule, message, channels,
                 created_by, datetime.now(timezone.utc).isoformat()),
            )
            await db.commit()
        finally:
            await db.close()

        # Register with the live scheduler
        _register_user_job(job_id, trigger, message, channels)

        log.info("User job created: %s schedule=%s by=%s", job_id, schedule, created_by)

        return SkillResult(
            success=True,
            output=f"Scheduled job **{job_id}** created: \"{message[:60]}\" — {schedule}",
            metadata={"job_id": job_id, "schedule": schedule},
        )

    async def _remove_job(self, job_id: str) -> SkillResult:
        if not job_id:
            return SkillResult(success=False, error="job_id is required.")
        if job_id in _SYSTEM_JOBS:
            return SkillResult(
                success=False,
                error=f"Cannot delete system job '{job_id}'. Use pause instead.",
            )

        from app.skills.scheduler import get_scheduler

        scheduler = get_scheduler()

        # Remove from scheduler
        try:
            scheduler.remove_job(job_id)
        except Exception:
            pass  # may not be registered if paused

        # Remove from database
        db = await _open_db()
        try:
            cursor = await db.execute(
                "DELETE FROM user_jobs WHERE job_id = ?", (job_id,)
            )
            await db.commit()
            if cursor.rowcount == 0:
                return SkillResult(success=False, error=f"Job '{job_id}' not found.")
        finally:
            await db.close()

        log.info("User job removed: %s", job_id)
        return SkillResult(success=True, output=f"Job **{job_id}** removed.")

    async def _pause_job(self, job_id: str) -> SkillResult:
        if not job_id:
            return SkillResult(success=False, error="job_id is required.")

        from app.skills.scheduler import get_scheduler

        scheduler = get_scheduler()
        try:
            scheduler.pause_job(job_id)
        except Exception as exc:
            return SkillResult(success=False, error=f"Cannot pause '{job_id}': {exc}")

        log.info("Job paused: %s", job_id)
        return SkillResult(success=True, output=f"Job **{job_id}** paused.")

    async def _resume_job(self, job_id: str) -> SkillResult:
        if not job_id:
            return SkillResult(success=False, error="job_id is required.")

        from app.skills.scheduler import get_scheduler

        scheduler = get_scheduler()
        try:
            scheduler.resume_job(job_id)
        except Exception as exc:
            return SkillResult(success=False, error=f"Cannot resume '{job_id}': {exc}")

        log.info("Job resumed: %s", job_id)
        return SkillResult(success=True, output=f"Job **{job_id}** resumed.")
