"""Tests for search layers — keyword search, term extraction."""
from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock
from app.skills.search import extract_key_terms, keyword_search


# ── Term extraction ─────────────────────────────────────────────────────────

def test_extract_terms_basic():
    terms = extract_key_terms("What was discussed about solar panels at the last meeting?")
    term_lower = [t.lower() for t in terms]
    assert "solar" in term_lower or "solar panel" in " ".join(term_lower)

def test_extract_terms_funding():
    terms = extract_key_terms("Development Officer funding February 2026")
    term_lower = [t.lower() for t in terms]
    assert "development officer" in " ".join(term_lower) or "funding" in term_lower

def test_extract_terms_names():
    terms = extract_key_terms("Report from Ellis about the community plan")
    assert any("Ellis" in t for t in terms)

def test_extract_terms_acronym():
    terms = extract_key_terms("What is CDCN's policy on AI ethics?")
    assert "CDCN" in terms or "cdcn" in [t.lower() for t in terms]

def test_extract_terms_date():
    terms = extract_key_terms("What happened in January 2026?")
    assert any("January 2026" in t for t in terms)

def test_extract_terms_empty():
    terms = extract_key_terms("")
    assert terms == []

def test_extract_terms_stopwords_only():
    terms = extract_key_terms("the a an in of to")
    assert len(terms) == 0


# ── Keyword search (mocked DB) ──────────────────────────────────────────────

def test_keyword_search_no_terms():
    """Empty terms should return empty results."""
    result = keyword_search(terms=[], doc_type=None)
    assert result == {}

def test_keyword_search_with_terms():
    """Keyword search should not crash with valid terms (even if DB doesn't exist)."""
    with patch("app.skills.search.sqlite3") as mock_sqlite:
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.execute.return_value.fetchall.return_value = []
        mock_sqlite.connect.return_value = mock_conn
        mock_sqlite.Row = MagicMock()

        result = keyword_search(terms=["funding", "solar"], doc_type=None)
        assert isinstance(result, dict)
