"""
SchedulerSkill — APScheduler-based recurring task runner for CDCN Agent.

Thin orchestration module that registers all scheduled jobs and exposes
the public API (get_scheduler, start_scheduler, SchedulerSkill).

Job implementations live in:
  - scheduler_jobs.py        — primary scheduled jobs (heartbeat, journal, etc.)
  - scheduler_maintenance.py — infrastructure jobs (backup, DB maintenance, etc.)

Helper functions and the _with_retry decorator live in:
  - scheduler_helpers.py
"""
from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import settings
from app.skills.scheduler_helpers import _parse_time
from app.skills.scheduler_jobs import (
    _wake_job,
    _sleep_job,
    _auto_index_job,
    _journal_job,
    _funding_feed_job,
    _overdue_check_job,
)
from app.skills.scheduler_jobs_extended import (
    _heartbeat_job,
    _weekly_digest_job,
    _monthly_governance_job,
)
from app.skills.scheduler_jobs import _session_archive_job
from app.skills.scheduler_maintenance import (
    _nightly_backup_job,
    _db_maintenance_job,
    _disk_monitor_job,
    _backup_verify_job,
)

log = logging.getLogger(__name__)

_scheduler = AsyncIOScheduler()


# ── Public API ─────────────────────────────────────────────────────────────────


def get_scheduler() -> AsyncIOScheduler:
    return _scheduler


def start_scheduler() -> None:
    """Register all jobs and start the scheduler.  Safe to call only once."""
    if _scheduler.running:
        return

    wake_h, wake_m = _parse_time(settings.wake_start_time)
    sleep_h, sleep_m = _parse_time(settings.wake_end_time)
    journal_h, journal_m = _parse_time(settings.journal_time)

    # ── State transitions ────────────────────────────────────────────────────
    _scheduler.add_job(
        _wake_job,
        CronTrigger(hour=wake_h, minute=wake_m),
        id="wake",
        replace_existing=True,
    )
    _scheduler.add_job(
        _sleep_job,
        CronTrigger(hour=sleep_h, minute=sleep_m),
        id="sleep",
        replace_existing=True,
    )

    # ── Heartbeat — interval, wake only ──────────────────────────────────────
    _scheduler.add_job(
        _heartbeat_job,
        "interval",
        hours=settings.heartbeat_interval_hours,
        id="heartbeat",
        replace_existing=True,
    )

    # ── Auto-index — every 6 h, wake only ────────────────────────────────────
    _scheduler.add_job(
        _auto_index_job,
        "interval",
        hours=6,
        id="auto_index",
        replace_existing=True,
    )

    # ── Journal — cron at JOURNAL_TIME, wake only ─────────────────────────────
    _scheduler.add_job(
        _journal_job,
        CronTrigger(hour=journal_h, minute=journal_m),
        id="journal",
        replace_existing=True,
    )

    # ── Weekly digest — Monday 07:15, wake only ───────────────────────────────
    _scheduler.add_job(
        _weekly_digest_job,
        CronTrigger(day_of_week="mon", hour=7, minute=15),
        id="weekly_digest",
        replace_existing=True,
    )

    # ── Monthly governance check — 1st of month 08:00 ─────────────────────────
    _scheduler.add_job(
        _monthly_governance_job,
        CronTrigger(day=1, hour=8, minute=0),
        id="monthly_governance",
        replace_existing=True,
    )

    # ── Daily overdue check — 08:30 every day ─────────────────────────────────
    _scheduler.add_job(
        _overdue_check_job,
        CronTrigger(hour=8, minute=30),
        id="overdue_check",
        replace_existing=True,
    )

    # ── Funding feed scan — 07:30 and 13:00 daily, wake only ────────────────
    _scheduler.add_job(
        _funding_feed_job,
        CronTrigger(hour=7, minute=30),
        id="funding_feed_am",
        replace_existing=True,
    )
    _scheduler.add_job(
        _funding_feed_job,
        CronTrigger(hour=12, minute=0),
        id="funding_feed_noon",
        replace_existing=True,
    )

    # ── Session archive — 03:00 daily, NOT wake-gated (dream mode) ───────────
    _scheduler.add_job(
        _session_archive_job,
        CronTrigger(hour=3, minute=0),
        id="session_archive",
        replace_existing=True,
    )

    # ── Nightly backup — 04:00 daily, NOT wake-gated ─────────────────────────
    _scheduler.add_job(
        _nightly_backup_job,
        CronTrigger(hour=4, minute=0),
        id="nightly_backup",
        replace_existing=True,
    )

    # ── Backup verification — 05:00 daily, NOT wake-gated ─────────────────
    _scheduler.add_job(
        _backup_verify_job,
        CronTrigger(hour=5, minute=0),
        id="backup_verify",
        replace_existing=True,
    )

    # ── Disk space monitor — 08:35 daily ──────────────────────────────────
    _scheduler.add_job(
        _disk_monitor_job,
        CronTrigger(hour=8, minute=35),
        id="disk_monitor",
        replace_existing=True,
    )

    # ── Weekly DB maintenance — Sunday 02:00, NOT wake-gated ──────────────
    _scheduler.add_job(
        _db_maintenance_job,
        CronTrigger(day_of_week="sun", hour=2, minute=0),
        id="db_maintenance",
        replace_existing=True,
    )

    _scheduler.start()
    log.info("Scheduler started with %d jobs.", len(_scheduler.get_jobs()))


class SchedulerSkill:
    """Thin wrapper exposing the scheduler start/stop API as a skill-like object."""
    name = "scheduler"
    description = "APScheduler-based recurring task runner."

    def start(self) -> None:
        start_scheduler()

    def get_scheduler(self) -> AsyncIOScheduler:
        return _scheduler
