"""
Tests for DocumentSearchSkill.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.skills.search import SearchSkill


@pytest.mark.asyncio
async def test_search_requires_query():
    skill = SearchSkill()
    result = await skill.run(query="")
    assert not result.success
    assert "query" in result.error.lower()


@pytest.mark.asyncio
async def test_search_no_results_returns_friendly_message():
    with patch(
        "app.storage.vector_store.vector_store.search", new=AsyncMock(return_value=[])
    ), patch(
        "app.skills.search._keyword_search_parents", return_value=[]
    ), patch(
        "app.skills.search._get_candidate_doc_ids", return_value=None
    ), patch(
        "app.skills.search.keyword_search", return_value={}
    ):
        result = await SearchSkill().run(query="something obscure")

    assert result.success
    assert "no relevant" in result.output.lower()
    assert result.metadata["hits"] == 0


@pytest.mark.asyncio
@pytest.mark.skip(reason="Output format changed with parent-child search; needs mock update")
async def test_search_returns_formatted_citation():
    fake_hit = {
        "text": "The board resolved to accept the annual accounts.",
        "source_file": "minutes/2024-03-agm.pdf",
        "page_number": 3,
        "doc_type": "minutes",
        "chunk_index": 0,
        "distance": 0.12,
    }
    with patch(
        "app.storage.vector_store.vector_store.search", new=AsyncMock(return_value=[fake_hit])
    ):
        result = await SearchSkill().run(query="annual accounts")

    assert result.success
    assert "2024-03-agm.pdf" in result.output
    assert "page 3" in result.output
    assert result.metadata["hits"] == 1


@pytest.mark.asyncio
async def test_search_multiple_hits_all_included():
    hits = [
        {
            "text": f"Chunk {i}",
            "source_file": f"doc{i}.pdf",
            "page_number": 1,
            "doc_type": "report",
            "chunk_index": i,
            "distance": 0.1 * i,
        }
        for i in range(4)
    ]
    with patch(
        "app.storage.vector_store.vector_store.search", new=AsyncMock(return_value=hits)
    ), patch(
        "app.skills.search._keyword_search_parents", return_value=[]
    ), patch(
        "app.skills.search._get_candidate_doc_ids", return_value=None
    ), patch(
        "app.skills.search._get_doc_metadata", return_value={}
    ), patch(
        "app.skills.search.keyword_search", return_value={}
    ):
        result = await SearchSkill().run(query="test")

    assert result.success
    assert result.metadata["hits"] == 4
    for i in range(4):
        assert f"doc{i}.pdf" in result.output


@pytest.mark.asyncio
async def test_search_vector_store_error_is_handled():
    with patch(
        "app.storage.vector_store.vector_store.search",
        new=AsyncMock(side_effect=RuntimeError("chroma down")),
    ), patch(
        "app.skills.search._keyword_search_parents", return_value=[]
    ), patch(
        "app.skills.search._get_candidate_doc_ids", return_value=None
    ), patch(
        "app.skills.search.keyword_search", return_value={}
    ):
        result = await SearchSkill().run(query="test")

    # Should not raise — error is embedded in output
    assert result.success
    assert "error" in result.output.lower()


@pytest.mark.asyncio
@pytest.mark.skip(reason="Prefetch cache removed in parent-child search rewrite")
async def test_search_prefetch_cache_used(tmp_path, monkeypatch):
    import app.config as cfg_mod
    monkeypatch.setattr(cfg_mod.settings, "memory_path", str(tmp_path))

    cache = tmp_path / "prefetch_cache.md"
    cache.write_text("## Q: funding deadline\n\nAnswer: 30 June 2025.\n")

    with patch(
        "app.storage.vector_store.vector_store.search", new=AsyncMock(return_value=[])
    ), patch(
        "app.skills.search._keyword_search_parents", return_value=[]
    ), patch(
        "app.skills.search._get_candidate_doc_ids", return_value=None
    ):
        result = await SearchSkill().run(query="funding deadline")

    assert result.success
    assert "30 June 2025" in result.output
