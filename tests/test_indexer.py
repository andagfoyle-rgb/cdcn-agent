"""
Tests for DocumentIndexerSkill and supporting helpers.
"""
from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from app.skills.indexer import (
    IndexerSkill,
    _chunk_elements,
    _infer_doc_type,
    _PagedElement,
    _validate_mime,
    SUPPORTED_SUFFIXES,
)


# ── _infer_doc_type ────────────────────────────────────────────────────────────


def test_infer_minutes():
    assert _infer_doc_type("2024-03-agm-minutes.pdf") == "minutes"


def test_infer_policy():
    assert _infer_doc_type("safeguarding_policy_v3.docx") == "policy"


def test_infer_application():
    assert _infer_doc_type("lottery_grant_application.pdf") == "funding"


def test_infer_other():
    assert _infer_doc_type("random_file.txt") == "other"


# ── _chunk_elements ────────────────────────────────────────────────────────────


def test_chunk_empty_elements():
    assert _chunk_elements([]) == []


def test_chunk_single_short_element():
    elements = [_PagedElement(text="Short text.", page=1)]
    chunks = _chunk_elements(elements, chunk_chars=200, overlap_chars=20)
    assert len(chunks) == 1
    assert chunks[0][0] == "Short text."
    assert chunks[0][1] == 1


def test_chunk_long_text_produces_overlap():
    text = "A" * 5000
    elements = [_PagedElement(text=text, page=1)]
    chunks = _chunk_elements(elements, chunk_chars=2000, overlap_chars=200)
    # Should produce multiple chunks
    assert len(chunks) > 1
    # Consecutive chunks should share overlap_chars of text
    step = 2000 - 200
    for i in range(len(chunks) - 1):
        end_of_prev = chunks[i][0][-200:]
        start_of_next = chunks[i + 1][0][:200]
        # They should overlap (end of one appears in start of next)
        assert end_of_prev[:50] in chunks[i + 1][0]


def test_chunk_page_numbers_carried():
    elements = [
        _PagedElement(text="Page one content. " * 200, page=1),
        _PagedElement(text="Page two content. " * 200, page=2),
    ]
    chunks = _chunk_elements(elements, chunk_chars=2000, overlap_chars=100)
    assert chunks[0][1] == 1
    # Last chunk should be on page 2
    assert chunks[-1][1] == 2


# ── _validate_mime ─────────────────────────────────────────────────────────────


def test_validate_mime_accepted(tmp_path):
    f = tmp_path / "test.txt"
    f.write_text("hello")
    with patch("app.skills.indexer.magic") as mock_magic:
        mock_magic.from_file.return_value = "text/plain"
        assert _validate_mime(f) is True


def test_validate_mime_rejected(tmp_path):
    f = tmp_path / "evil.exe"
    f.write_bytes(b"\x4d\x5a" + b"\x00" * 100)
    with patch("app.skills.indexer.magic") as mock_magic:
        mock_magic.from_file.return_value = "application/x-msdownload"
        assert _validate_mime(f) is False


def test_validate_mime_fails_open_on_import_error(tmp_path):
    """If python-magic is not available, validation passes (fail-open)."""
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"%PDF-")
    with patch.dict("sys.modules", {"magic": None}):
        # ImportError path — should return True
        result = _validate_mime(f)
    assert result is True


# ── IndexerSkill integration ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_indexer_missing_folder():
    result = await IndexerSkill().run(folder="/nonexistent/xyz123")
    assert not result.success
    assert "not found" in result.error.lower()


@pytest.mark.asyncio
async def test_indexer_indexes_txt_file(tmp_path, monkeypatch):
    import app.config as cfg_mod
    monkeypatch.setattr(cfg_mod.settings, "chroma_path", str(tmp_path / "chroma"))
    (tmp_path / "sample.txt").write_text("Community benefit. " * 150)

    with patch(
        "app.skills.indexer.vector_store.add_document", new=AsyncMock()
    ) as mock_add, patch(
        "app.skills.indexer.vector_store.delete_document", new=AsyncMock()
    ):
        result = await IndexerSkill().run(folder=str(tmp_path), force=True)

    assert result.success
    assert result.metadata["indexed"] == 1
    assert mock_add.called


@pytest.mark.asyncio
async def test_indexer_skips_unchanged_file(tmp_path, monkeypatch):
    import app.config as cfg_mod
    monkeypatch.setattr(cfg_mod.settings, "chroma_path", str(tmp_path / "chroma"))
    f = tmp_path / "doc.txt"
    f.write_text("Hello world. " * 50)

    with patch("app.skills.indexer.vector_store.add_document", new=AsyncMock()), \
         patch("app.skills.indexer.vector_store.delete_document", new=AsyncMock()):
        # First run — should index
        r1 = await IndexerSkill().run(folder=str(tmp_path))
        assert r1.metadata["indexed"] == 1

        # Second run — same mtime, should skip
        r2 = await IndexerSkill().run(folder=str(tmp_path))
        assert r2.metadata["skipped"] == 1
        assert r2.metadata["indexed"] == 0


@pytest.mark.asyncio
async def test_indexer_rejects_unsupported_extension(tmp_path, monkeypatch):
    import app.config as cfg_mod
    monkeypatch.setattr(cfg_mod.settings, "chroma_path", str(tmp_path / "chroma"))
    (tmp_path / "script.py").write_text("import os")

    with patch("app.skills.indexer.vector_store.add_document", new=AsyncMock()):
        result = await IndexerSkill().run(folder=str(tmp_path))

    assert result.success
    assert result.metadata["indexed"] == 0   # .py not in SUPPORTED_SUFFIXES


@pytest.mark.asyncio
async def test_indexer_mime_rejection(tmp_path, monkeypatch):
    import app.config as cfg_mod
    monkeypatch.setattr(cfg_mod.settings, "chroma_path", str(tmp_path / "chroma"))
    (tmp_path / "fake.pdf").write_bytes(b"not a pdf but named pdf")

    with patch("app.skills.indexer._validate_mime", return_value=False), \
         patch("app.skills.indexer.vector_store.add_document", new=AsyncMock()):
        result = await IndexerSkill().run(folder=str(tmp_path), force=True)

    assert result.success
    assert result.metadata["indexed"] == 0
    assert result.metadata["failed"] == 1
