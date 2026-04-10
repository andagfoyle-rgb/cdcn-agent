"""
Regression tests for known retrieval queries.

These tests verify that specific queries find the expected documents.
They require the full document corpus to be indexed, so they are
marked with @pytest.mark.skipif to skip when the DB is not available.

Add new regression tests whenever a query fails in production.
"""
from __future__ import annotations

import os
import sqlite3
import pytest

_DB_PATH = "/var/lib/cdcn-agent/indexer.db"
_HAS_DB = os.path.exists(_DB_PATH)

skip_no_db = pytest.mark.skipif(not _HAS_DB, reason="Indexer DB not available")


def _search_chunks(terms: list[str], limit: int = 20) -> list[dict]:
    """Simple keyword search directly on the chunks table."""
    results = []
    with sqlite3.connect(_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        for term in terms:
            rows = conn.execute(
                "SELECT DISTINCT doc_id, section_title, content FROM chunks "
                "WHERE content LIKE ? LIMIT ?",
                (f"%{term}%", limit),
            ).fetchall()
            results.extend([dict(r) for r in rows])
    return results


def _get_doc_filename(doc_id: str) -> str:
    """Look up filename for a doc_id."""
    with sqlite3.connect(_DB_PATH) as conn:
        row = conn.execute(
            "SELECT filename FROM document_metadata WHERE doc_id = ? LIMIT 1",
            (doc_id,),
        ).fetchone()
    return row[0] if row else ""


@skip_no_db
def test_development_officer_funding():
    """'Development Officer funding February 2026' must find relevant minutes."""
    results = _search_chunks(["Development Officer", "funding"])
    assert len(results) > 0, "No results found for 'Development Officer funding'"
    doc_ids = {r["doc_id"] for r in results}
    filenames = [_get_doc_filename(did) for did in doc_ids]
    # Should find at least one minutes document
    assert any("minute" in f.lower() or "meeting" in f.lower() for f in filenames if f), \
        f"Expected minutes document, got: {filenames}"


@skip_no_db
def test_january_2026_attendance():
    """'Who attended January 2026 meeting' must find Jan 2026 minutes."""
    results = _search_chunks(["Present", "January"])
    # Also try searching metadata
    with sqlite3.connect(_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT filename, attendees FROM document_metadata "
            "WHERE doc_date LIKE '2026-01%' AND doc_type = 'minutes'",
        ).fetchall()
    assert len(rows) > 0 or len(results) > 0, "No January 2026 meeting data found"


@skip_no_db
def test_solar_pv_project():
    """'Solar PV project status' must find relevant minutes."""
    results = _search_chunks(["solar", "PV"])
    if not results:
        results = _search_chunks(["solar panel", "solar"])
    assert len(results) > 0, "No results found for solar PV project"


@skip_no_db
def test_chunks_table_has_data():
    """Basic sanity: chunks table should have indexed data."""
    with sqlite3.connect(_DB_PATH) as conn:
        count = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    assert count > 0, "Chunks table is empty — indexing may have failed"


@skip_no_db
def test_metadata_table_has_data():
    """Basic sanity: document_metadata should have entries."""
    with sqlite3.connect(_DB_PATH) as conn:
        count = conn.execute("SELECT COUNT(*) FROM document_metadata").fetchone()[0]
    assert count > 0, "document_metadata table is empty"
