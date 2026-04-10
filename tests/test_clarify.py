"""Tests for ClarifySkill."""
from __future__ import annotations

import asyncio
import pytest
from app.skills.clarify import ClarifySkill


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.fixture
def skill():
    return ClarifySkill()


def test_basic_question(skill):
    result = _run(skill.run(question="Which meeting are you referring to?"))
    assert result.success
    assert "Which meeting" in result.output
    assert result.metadata["type"] == "clarification"


def test_question_with_options(skill):
    result = _run(skill.run(
        question="Which funding deadline?",
        options=["Highland Council — due 15 April", "OSCR Annual Return — due 30 June"],
    ))
    assert result.success
    assert "1." in result.output
    assert "Highland Council" in result.output
    assert result.metadata["options"] == [
        "Highland Council — due 15 April",
        "OSCR Annual Return — due 30 June",
    ]


def test_question_with_context(skill):
    result = _run(skill.run(
        question="Can you specify the date range?",
        context="Multiple meetings match your query.",
    ))
    assert result.success
    assert "Multiple meetings" in result.output
    assert "date range" in result.output


def test_empty_question(skill):
    result = _run(skill.run(question=""))
    assert not result.success
    assert "required" in result.error.lower()


def test_no_question(skill):
    result = _run(skill.run())
    assert not result.success
