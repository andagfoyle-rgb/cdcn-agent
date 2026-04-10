"""Tests for NotifySkill — channel routing and message formatting."""
from __future__ import annotations

import asyncio
import pytest
from unittest.mock import patch, AsyncMock


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_empty_message():
    from app.skills.notify import NotifySkill
    skill = NotifySkill()
    result = _run(skill.run(message=""))
    assert not result.success
    assert "required" in result.error.lower()


def test_send_all_channels_unconfigured():
    from app.skills.notify import NotifySkill
    skill = NotifySkill()
    with patch("app.skills.notify._send_discord", new_callable=AsyncMock, return_value=False), \
         patch("app.skills.notify._send_telegram", new_callable=AsyncMock, return_value=False), \
         patch("app.skills.notify._send_email", new_callable=AsyncMock, return_value=False):
        result = _run(skill.run(message="Test notification"))
    assert result.success
    assert result.metadata["sent_count"] == 0


def test_send_discord_only():
    from app.skills.notify import NotifySkill
    skill = NotifySkill()
    with patch("app.skills.notify._send_discord", new_callable=AsyncMock, return_value=True):
        result = _run(skill.run(
            message="Discord only message",
            channels=["discord"],
        ))
    assert result.success
    assert result.metadata["sent_count"] == 1
    assert result.metadata["results"]["discord"] is True


def test_send_telegram_only():
    from app.skills.notify import NotifySkill
    skill = NotifySkill()
    with patch("app.skills.notify._send_telegram", new_callable=AsyncMock, return_value=True):
        result = _run(skill.run(
            message="Telegram message",
            channels=["telegram"],
        ))
    assert result.success
    assert result.metadata["sent_count"] == 1


def test_send_email_only():
    from app.skills.notify import NotifySkill
    skill = NotifySkill()
    with patch("app.skills.notify._send_email", new_callable=AsyncMock, return_value=True):
        result = _run(skill.run(
            message="Email notification",
            channels=["email"],
            subject="Test Subject",
        ))
    assert result.success
    assert result.metadata["sent_count"] == 1


def test_urgent_flag():
    from app.skills.notify import NotifySkill
    skill = NotifySkill()
    discord_mock = AsyncMock(return_value=True)
    with patch("app.skills.notify._send_discord", discord_mock):
        result = _run(skill.run(
            message="Urgent alert!",
            channels=["discord"],
            urgency="urgent",
        ))
    assert result.success
    discord_mock.assert_called_once_with("Urgent alert!", True)


def test_mixed_channels():
    from app.skills.notify import NotifySkill
    skill = NotifySkill()
    with patch("app.skills.notify._send_discord", new_callable=AsyncMock, return_value=True), \
         patch("app.skills.notify._send_telegram", new_callable=AsyncMock, return_value=False), \
         patch("app.skills.notify._send_email", new_callable=AsyncMock, return_value=True):
        result = _run(skill.run(
            message="Mixed channels test",
            channels=["discord", "telegram", "email"],
        ))
    assert result.success
    assert result.metadata["sent_count"] == 2
    assert result.metadata["results"]["discord"] is True
    assert result.metadata["results"]["telegram"] is False
    assert result.metadata["results"]["email"] is True


def test_unknown_channel_returns_false():
    from app.skills.notify import NotifySkill
    skill = NotifySkill()
    result = _run(skill.run(
        message="Test",
        channels=["carrier_pigeon"],
    ))
    assert result.success
    assert result.metadata["sent_count"] == 0
    assert result.metadata["results"]["carrier_pigeon"] is False
