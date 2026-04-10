"""
Tests for the WriterSkill.
"""
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_writer_missing_template():
    from app.skills.writer import WriterSkill
    result = await WriterSkill().run(template="nonexistent_template_xyz")
    assert not result.success
    assert "not found" in result.error.lower()


@pytest.mark.asyncio
async def test_writer_produces_draft(tmp_path, monkeypatch):
    # Point template dir at tmp_path
    import app.skills.writer as writer_mod
    monkeypatch.setattr(writer_mod, "TEMPLATE_DIR", tmp_path)
    (tmp_path / "test_tmpl.md").write_text("# {{TITLE}}\n\n{{BODY}}")

    with (
        patch("app.skills.writer.chat", new=AsyncMock(return_value="Generated draft content.")),
        patch("pathlib.Path.mkdir"),
        patch("pathlib.Path.write_text"),
    ):
        from app.skills.writer import WriterSkill
        result = await WriterSkill().run(template="test_tmpl", save_draft=False)

    assert result.success
    assert result.output == "Generated draft content."
