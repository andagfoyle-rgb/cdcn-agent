"""Tests for DelegateSkill — parallel task execution and aggregation."""
from __future__ import annotations

import asyncio
import pytest

from app.skills.base import BaseSkill, SkillResult
from app.skills.delegate import DelegateSkill


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class MockSearchSkill(BaseSkill):
    name = "search"
    description = "Mock search"

    async def run(self, **kwargs):
        await asyncio.sleep(0.01)
        return SkillResult(success=True, output=f"Search result for: {kwargs.get('query', '?')}")


class MockCalendarSkill(BaseSkill):
    name = "calendar_manager"
    description = "Mock calendar"

    async def run(self, **kwargs):
        await asyncio.sleep(0.01)
        return SkillResult(success=True, output="Upcoming: Board meeting 15 April")


class MockFailSkill(BaseSkill):
    name = "fail"
    description = "Always fails"

    async def run(self, **kwargs):
        raise RuntimeError("Intentional failure")


@pytest.fixture
def skill():
    skills = {
        "search": MockSearchSkill(),
        "calendar_manager": MockCalendarSkill(),
        "fail": MockFailSkill(),
    }
    return DelegateSkill(skills=skills)


def test_empty_tasks(skill):
    result = _run(skill.run(tasks=[]))
    assert not result.success
    assert "required" in result.error.lower()


def test_no_tasks(skill):
    result = _run(skill.run())
    assert not result.success


def test_single_task(skill):
    result = _run(skill.run(tasks=[
        {"skill": "search", "args": {"query": "board minutes"}, "label": "Search minutes"},
    ]))
    assert result.success
    assert "Search minutes" in result.output
    assert result.metadata["successes"] == 1
    assert result.metadata["failures"] == 0


def test_parallel_tasks(skill):
    result = _run(skill.run(tasks=[
        {"skill": "search", "args": {"query": "funding"}, "label": "Funding search"},
        {"skill": "calendar_manager", "args": {"action": "upcoming"}, "label": "Calendar check"},
    ]))
    assert result.success
    assert result.metadata["successes"] == 2
    assert "Funding search" in result.output
    assert "Calendar check" in result.output


def test_partial_failure(skill):
    result = _run(skill.run(tasks=[
        {"skill": "search", "args": {"query": "test"}, "label": "Good task"},
        {"skill": "fail", "args": {}, "label": "Bad task"},
    ]))
    assert result.success  # delegation itself succeeds
    assert result.metadata["successes"] == 1
    assert result.metadata["failures"] == 1


def test_unknown_skill(skill):
    result = _run(skill.run(tasks=[
        {"skill": "nonexistent", "args": {}},
    ]))
    assert not result.success
    assert "Unknown skill" in result.error


def test_too_many_tasks(skill):
    tasks = [{"skill": "search", "args": {"query": f"q{i}"}} for i in range(10)]
    result = _run(skill.run(tasks=tasks))
    assert not result.success
    assert "Too many" in result.error


def test_invalid_task_entries_skipped(skill):
    result = _run(skill.run(tasks=[
        {"not_a_skill": "hmm"},  # no "skill" key — skipped
    ]))
    assert not result.success
    assert "No valid" in result.error


def test_elapsed_time_tracked(skill):
    result = _run(skill.run(tasks=[
        {"skill": "search", "args": {"query": "test"}},
    ]))
    assert result.success
    assert "elapsed_secs" in result.metadata
    assert result.metadata["elapsed_secs"] >= 0
