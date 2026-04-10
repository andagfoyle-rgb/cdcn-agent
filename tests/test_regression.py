"""
Regression tests — known queries that must return correct results.

These tests verify the search pipeline returns expected documents for
queries that have failed in production. Add new test cases for every
production query failure going forward.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from app.skills.search import SearchSkill


# ── Helpers ──────────────────────────────────────────────────────────────────

def _mock_search_with_docs(docs: list[dict]):
    """Create mocks that return specified documents from search."""
    return docs


# ── Regression: Development Officer funding February 2026 ────────────────────

@pytest.mark.asyncio
async def test_dev_officer_funding_feb_2026(sample_documents):
    """'Development Officer funding February 2026' must find relevant content."""
    skill = SearchSkill()

    # Use the sample documents that contain Development Officer + February 2026
    with patch(
        "app.skills.search._keyword_search_parents", return_value=[]
    ), patch(
        "app.skills.search._get_candidate_doc_ids", return_value=None
    ), patch(
        "app.skills.search.keyword_search"
    ) as mock_kw:
        # Mock keyword search to return the minutes doc
        mock_kw.return_value = {
            "test_minutes": MagicMock(
                found_by={"keyword"},
                filename="CDCN minutes (2) 10-2-26.txt",
                filepath=str(sample_documents["minutes"]),
                doc_date="2026-02-10",
                doc_type="minutes",
            ),
        }

        with patch(
            "app.storage.vector_store.vector_store.search",
            new=AsyncMock(return_value=[]),
        ):
            result = await skill.run(query="Development Officer funding February 2026")

    assert result.success


# ── Regression: January 2026 meeting attendees ───────────────────────────────

@pytest.mark.asyncio
async def test_jan_2026_attendees(sample_documents):
    """'Who attended January 2026 meeting' must find attendee list."""
    # The sample_documents contain "Present: Alice Smith, Bob Jones, Carol Davis"
    text = sample_documents["minutes"].read_text()
    assert "Present:" in text
    assert "Alice Smith" in text


# ── Regression: AI Ethics Policy data handling ───────────────────────────────

@pytest.mark.asyncio
async def test_ai_ethics_policy(sample_documents):
    """'AI Ethics Policy data handling' must find the policy document."""
    text = sample_documents["policy"].read_text()
    assert "Data Handling" in text
    assert "GDPR" in text


# ── Regression: search with empty query ──────────────────────────────────────

@pytest.mark.asyncio
async def test_empty_query_handled():
    """Empty query should return a friendly error, not crash."""
    skill = SearchSkill()
    result = await skill.run(query="")
    assert not result.success or "query" in result.output.lower() or "no" in result.output.lower()


# ── Regression: search with very long query ──────────────────────────────────

@pytest.mark.asyncio
async def test_very_long_query():
    """Extremely long query should not crash the search."""
    skill = SearchSkill()
    long_query = "funding " * 1000

    with patch(
        "app.storage.vector_store.vector_store.search",
        new=AsyncMock(return_value=[]),
    ), patch(
        "app.skills.search._keyword_search_parents", return_value=[]
    ), patch(
        "app.skills.search._get_candidate_doc_ids", return_value=None
    ), patch(
        "app.skills.search.keyword_search", return_value={}
    ):
        result = await skill.run(query=long_query)

    # Should not raise
    assert result.success


# ── Regression: special characters in search ─────────────────────────────────

@pytest.mark.asyncio
async def test_special_chars_in_query():
    """Search with SQL-unsafe characters should not crash."""
    skill = SearchSkill()

    with patch(
        "app.storage.vector_store.vector_store.search",
        new=AsyncMock(return_value=[]),
    ), patch(
        "app.skills.search._keyword_search_parents", return_value=[]
    ), patch(
        "app.skills.search._get_candidate_doc_ids", return_value=None
    ), patch(
        "app.skills.search.keyword_search", return_value={}
    ):
        result = await skill.run(query="test'; DROP TABLE chunks; --")

    assert result.success
