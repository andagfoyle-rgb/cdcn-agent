"""Tests for CronjobManagerSkill — schedule parsing and CRUD operations."""
from __future__ import annotations

import asyncio
import pytest
from unittest.mock import patch, MagicMock, AsyncMock


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ── System job constants (importable without APScheduler) ────────────────────

def test_system_jobs_include_heartbeat():
    from app.skills.cronjob_manager import _SYSTEM_JOBS
    assert "heartbeat" in _SYSTEM_JOBS


def test_system_jobs_include_journal():
    from app.skills.cronjob_manager import _SYSTEM_JOBS
    assert "journal" in _SYSTEM_JOBS


def test_system_jobs_include_auto_index():
    from app.skills.cronjob_manager import _SYSTEM_JOBS
    assert "auto_index" in _SYSTEM_JOBS


# ── Schedule parsing (needs APScheduler) ─────────────────────────────────────

apscheduler = pytest.importorskip("apscheduler")


def test_parse_every_hours():
    from app.skills.cronjob_manager import _parse_schedule
    from apscheduler.triggers.interval import IntervalTrigger
    trigger = _parse_schedule("every 6 hours")
    assert isinstance(trigger, IntervalTrigger)


def test_parse_every_minutes():
    from app.skills.cronjob_manager import _parse_schedule
    from apscheduler.triggers.interval import IntervalTrigger
    trigger = _parse_schedule("every 30 minutes")
    assert isinstance(trigger, IntervalTrigger)


def test_parse_daily_at():
    from app.skills.cronjob_manager import _parse_schedule
    from apscheduler.triggers.cron import CronTrigger
    trigger = _parse_schedule("daily at 09:00")
    assert isinstance(trigger, CronTrigger)


def test_parse_weekly():
    from app.skills.cronjob_manager import _parse_schedule
    from apscheduler.triggers.cron import CronTrigger
    trigger = _parse_schedule("weekly monday 08:00")
    assert isinstance(trigger, CronTrigger)


def test_parse_monthly():
    from app.skills.cronjob_manager import _parse_schedule
    from apscheduler.triggers.cron import CronTrigger
    trigger = _parse_schedule("monthly 1st 09:00")
    assert isinstance(trigger, CronTrigger)


def test_parse_cron():
    from app.skills.cronjob_manager import _parse_schedule
    from apscheduler.triggers.cron import CronTrigger
    trigger = _parse_schedule("cron 0 9 * * mon-fri")
    assert isinstance(trigger, CronTrigger)


def test_parse_invalid():
    from app.skills.cronjob_manager import _parse_schedule
    with pytest.raises(ValueError, match="Cannot parse"):
        _parse_schedule("whenever you feel like it")


def test_parse_every_invalid_unit():
    from app.skills.cronjob_manager import _parse_schedule
    with pytest.raises(ValueError, match="Cannot parse"):
        _parse_schedule("every 5 fortnights")


def test_parse_every_days():
    from app.skills.cronjob_manager import _parse_schedule
    from apscheduler.triggers.interval import IntervalTrigger
    trigger = _parse_schedule("every 2 days")
    assert isinstance(trigger, IntervalTrigger)


# ── Skill action tests (with mocked scheduler/db) ───────────────────────────

def test_list_jobs():
    from app.skills.cronjob_manager import CronjobManagerSkill
    skill = CronjobManagerSkill()

    mock_scheduler = MagicMock()
    mock_job = MagicMock()
    mock_job.id = "heartbeat"
    mock_job.next_run_time = None
    mock_scheduler.get_jobs.return_value = [mock_job]

    with patch("app.skills.scheduler.get_scheduler", return_value=mock_scheduler):
        result = _run(skill.run(action="list"))

    assert result.success
    assert "heartbeat" in result.output
    assert "[system]" in result.output


def test_add_job_missing_schedule():
    from app.skills.cronjob_manager import CronjobManagerSkill
    skill = CronjobManagerSkill()
    result = _run(skill.run(action="add", message="test"))
    assert not result.success
    assert "schedule" in result.error.lower()


def test_add_job_missing_message():
    from app.skills.cronjob_manager import CronjobManagerSkill
    skill = CronjobManagerSkill()
    result = _run(skill.run(action="add", schedule="daily at 09:00"))
    assert not result.success
    assert "message" in result.error.lower()


def test_remove_system_job():
    from app.skills.cronjob_manager import CronjobManagerSkill
    skill = CronjobManagerSkill()
    result = _run(skill.run(action="remove", job_id="heartbeat"))
    assert not result.success
    assert "system job" in result.error.lower() or "Cannot delete" in result.error


def test_unknown_action():
    from app.skills.cronjob_manager import CronjobManagerSkill
    skill = CronjobManagerSkill()
    result = _run(skill.run(action="explode"))
    assert not result.success
    assert "Unknown action" in result.error


def test_pause_job():
    from app.skills.cronjob_manager import CronjobManagerSkill
    skill = CronjobManagerSkill()
    mock_scheduler = MagicMock()
    with patch("app.skills.scheduler.get_scheduler", return_value=mock_scheduler):
        result = _run(skill.run(action="pause", job_id="heartbeat"))
    assert result.success
    mock_scheduler.pause_job.assert_called_once_with("heartbeat")


def test_resume_job():
    from app.skills.cronjob_manager import CronjobManagerSkill
    skill = CronjobManagerSkill()
    mock_scheduler = MagicMock()
    with patch("app.skills.scheduler.get_scheduler", return_value=mock_scheduler):
        result = _run(skill.run(action="resume", job_id="heartbeat"))
    assert result.success
    mock_scheduler.resume_job.assert_called_once_with("heartbeat")
