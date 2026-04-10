"""Tests for chunker — parent-child chunking and validation."""
from __future__ import annotations

import pytest
from app.skills.chunker import (
    chunk_document, validate_chunks, ChunkedDocument, Chunk, _estimate_tokens,
)
from app.skills.document_parser import ParsedDocument, ParsedSection


def _make_parsed(text: str, sections: list[ParsedSection] | None = None) -> ParsedDocument:
    return ParsedDocument(
        full_text=text,
        sections=sections or [],
        title="Test Document",
    )


# ── Token estimation ────────────────────────────────────────────────────────

def test_estimate_tokens():
    assert _estimate_tokens("hello world") >= 1
    assert _estimate_tokens("") == 1  # min 1
    assert _estimate_tokens("a" * 400) == 100  # ~4 chars per token


# ── Basic chunking ──────────────────────────────────────────────────────────

def test_chunk_with_sections():
    # Content must exceed _SHORT_DOC_CHARS (500) to trigger section-based chunking
    section_a = ("Welcome to the meeting held on 4th March 2026 at the Community Hall. "
                 "The chair opened by welcoming all attendees and noted apologies from "
                 "several members who could not attend. There are many important items on "
                 "the agenda for this quarterly board session and we need to work through "
                 "them carefully and efficiently to ensure all business is covered.")
    section_b = ("The treasurer reported a healthy balance of £5,000 in the account. "
                 "This is a significant improvement over last quarter when we had some "
                 "concerns about the reserves. A detailed breakdown of income and "
                 "expenditure was circulated to all board members for review.")
    sections = [
        ParsedSection(title="Welcome", content=section_a),
        ParsedSection(title="Finance", content=section_b),
    ]
    full = "\n\n".join(s.content for s in sections)
    parsed = _make_parsed(full, sections)
    result = chunk_document(parsed, "doc123")
    assert len(result.parents) >= 2
    assert len(result.children) >= 2
    assert all(c.chunk_type == "child" for c in result.children)
    assert all(p.chunk_type == "parent" for p in result.parents)


def test_chunk_no_sections():
    """Document with no sections should still produce parent + child."""
    parsed = _make_parsed("This is a document with no clear section structure but enough text to chunk.")
    result = chunk_document(parsed, "doc456")
    assert len(result.parents) >= 1
    assert len(result.children) >= 1


def test_chunk_empty_document():
    parsed = _make_parsed("")
    result = chunk_document(parsed, "empty")
    # Empty doc should still not crash
    assert isinstance(result, ChunkedDocument)


def test_chunk_short_document():
    """Very short documents should get single parent + single child."""
    parsed = _make_parsed("Short text here.")
    result = chunk_document(parsed, "short")
    assert len(result.parents) >= 1
    assert len(result.children) >= 1


def test_every_parent_has_child():
    sections = [
        ParsedSection(title="Section A", content="Content for section A with enough text to create a chunk."),
        ParsedSection(title="Section B", content="Content for section B also with enough text."),
        ParsedSection(title="Section C", content="Section C content here."),
    ]
    parsed = _make_parsed("Full doc text", sections)
    result = chunk_document(parsed, "doc789")
    parent_titles = {p.section_title for p in result.parents}
    child_parent_titles = {c.section_title for c in result.children}
    # Every parent's section title should appear in children
    for pt in parent_titles:
        assert pt in child_parent_titles, f"Parent '{pt}' has no children"


# ── Validation ──────────────────────────────────────────────────────────────

def test_validate_chunks_clean():
    sections = [
        ParsedSection(title="Intro", content="This is the introduction with enough content."),
    ]
    parsed = _make_parsed("This is the introduction with enough content.", sections)
    result = chunk_document(parsed, "clean")
    warnings = validate_chunks(result, parsed.full_text)
    # Should have no critical warnings (minor coverage warnings OK)
    critical = [w for w in warnings if "empty" in w.lower() or "no children" in w.lower()]
    assert len(critical) == 0


def test_validate_no_empty_chunks():
    sections = [
        ParsedSection(title="Test", content="Valid content here with enough text."),
    ]
    parsed = _make_parsed("Valid content here with enough text.", sections)
    result = chunk_document(parsed, "noempty")
    for chunk in result.children + result.parents:
        assert chunk.content.strip(), f"Found empty chunk: {chunk}"
