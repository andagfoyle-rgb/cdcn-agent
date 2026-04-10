"""Tests for VisionSkill — image encoding and path validation."""
from __future__ import annotations

import asyncio
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ── Image encoding (pure logic, no external deps) ───────────────────────────

def test_encode_image_png(tmp_path):
    from app.skills.vision import _encode_image
    img = tmp_path / "test.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
    b64, mime = _encode_image(img)
    assert mime == "image/png"
    assert len(b64) > 0


def test_encode_image_jpg(tmp_path):
    from app.skills.vision import _encode_image
    img = tmp_path / "test.jpg"
    img.write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)
    b64, mime = _encode_image(img)
    assert mime == "image/jpeg"


def test_encode_image_unsupported(tmp_path):
    from app.skills.vision import _encode_image
    img = tmp_path / "test.xyz"
    img.write_bytes(b"content")
    with pytest.raises(ValueError, match="Unsupported"):
        _encode_image(img)


def test_encode_image_missing():
    from app.skills.vision import _encode_image
    with pytest.raises(FileNotFoundError):
        _encode_image(Path("/nonexistent/image.png"))


def test_encode_image_too_large(tmp_path):
    from app.skills.vision import _encode_image
    img = tmp_path / "huge.png"
    img.write_bytes(b"\x89PNG" + b"\x00" * (21 * 1024 * 1024))
    with pytest.raises(ValueError, match="too large"):
        _encode_image(img)


def test_mime_map_coverage():
    from app.skills.vision import _MIME_MAP
    assert ".png" in _MIME_MAP
    assert ".jpg" in _MIME_MAP
    assert ".jpeg" in _MIME_MAP
    assert ".gif" in _MIME_MAP
    assert ".webp" in _MIME_MAP


# ── Skill-level tests (require pydantic_settings for config) ────────────────

pydantic_settings = pytest.importorskip("pydantic_settings")


def test_rejects_empty_path():
    from app.skills.vision import VisionSkill
    skill = VisionSkill()
    result = _run(skill.run(image_path=""))
    assert not result.success
    assert "required" in result.error.lower()


def test_rejects_path_outside_allowed():
    from app.skills.vision import VisionSkill
    skill = VisionSkill()
    result = _run(skill.run(image_path="/etc/passwd"))
    assert not result.success
    assert "Access denied" in result.error


def test_describe_mode_with_mock(tmp_path):
    from app.skills.vision import VisionSkill
    img = tmp_path / "test.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

    mock_client = MagicMock()
    mock_client.chat = AsyncMock(return_value="A photograph showing a building.")

    mock_settings = MagicMock()
    mock_settings.watched_folder = str(tmp_path)
    mock_settings.memory_path = "/nonexistent1"
    mock_settings.backup_path = "/nonexistent2"

    with patch("app.llm_client.CDCNLLMClient", return_value=mock_client), \
         patch("app.config.settings", mock_settings):
        skill = VisionSkill()
        result = _run(skill.run(image_path=str(img), mode="describe"))

    assert result.success
    assert "building" in result.output
    assert result.metadata["mode"] == "describe"


def test_ocr_mode_with_mock(tmp_path):
    from app.skills.vision import VisionSkill
    img = tmp_path / "scan.jpg"
    img.write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)

    mock_client = MagicMock()
    mock_client.chat = AsyncMock(return_value="Minutes of CDCN Board Meeting\n4 March 2026")

    mock_settings = MagicMock()
    mock_settings.watched_folder = str(tmp_path)
    mock_settings.memory_path = "/nonexistent1"
    mock_settings.backup_path = "/nonexistent2"

    with patch("app.llm_client.CDCNLLMClient", return_value=mock_client), \
         patch("app.config.settings", mock_settings):
        skill = VisionSkill()
        result = _run(skill.run(image_path=str(img), mode="ocr"))

    assert result.success
    assert "CDCN Board Meeting" in result.output
