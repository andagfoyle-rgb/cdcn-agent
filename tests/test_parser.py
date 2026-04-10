"""Tests for document_parser — PDF fallback chain, DOCX edge cases, metadata extraction."""
from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from app.skills.document_parser import (
    parse_document, ParsedDocument, _detect_sections, _is_section_heading,
    _extract_apologies, _extract_chair, _table_to_markdown,
)


# ── Section detection ───────────────────────────────────────────────────────

def test_section_heading_numbered():
    assert _is_section_heading("1. Welcome and Apologies")

def test_section_heading_allcaps():
    assert _is_section_heading("ACTIONS REQUIRED FROM LAST MEETING")

def test_section_heading_report():
    assert _is_section_heading("Report from Ellis")

def test_section_heading_treasurer():
    assert _is_section_heading("Treasurer's Report")

def test_section_heading_chairman():
    assert _is_section_heading("Chairman's Report")

def test_section_heading_aocb():
    assert _is_section_heading("AOCB")

def test_section_heading_do_update():
    assert _is_section_heading("DO Update")

def test_section_heading_date_of_next():
    assert _is_section_heading("Date of Next Meeting")

def test_section_heading_agenda_item():
    assert _is_section_heading("Agenda Item 2: Finance")

def test_not_section_heading_short():
    assert not _is_section_heading("Hi")

def test_not_section_heading_normal_sentence():
    assert not _is_section_heading("The meeting was held in the community hall.")


# ── Section splitting ────────────────────────────────────────────────────────

def test_detect_sections_basic():
    text = "Preamble text here.\n\n1. Welcome and Apologies\nSome content here.\n\nTreasurer's Report\nFinancial stuff."
    sections = _detect_sections(text)
    assert len(sections) >= 2
    titles = [s.title for s in sections]
    assert "1. Welcome and Apologies" in titles


# ── Metadata extraction ──────────────────────────────────────────────────────

def test_extract_apologies():
    text = "Apologies: John Smith, Jane Doe\nPresent: Alice, Bob"
    result = _extract_apologies(text)
    assert len(result) >= 2
    assert "John Smith" in result

def test_extract_apologies_absent():
    text = "Absent: Mark Wilson, Sarah Brown"
    result = _extract_apologies(text)
    assert len(result) >= 2

def test_extract_apologies_none():
    text = "No apologies section in this document."
    result = _extract_apologies(text)
    assert result == []

def test_extract_chair():
    text = "Chair: Professor Ellis\nMinutes of meeting held..."
    result = _extract_chair(text)
    assert result is not None
    assert "Ellis" in result

def test_extract_chair_chaired_by():
    text = "Chaired by Mark Robertson\nPresent: Alice, Bob"
    result = _extract_chair(text)
    assert result is not None
    assert "Mark Robertson" in result

def test_extract_chair_none():
    text = "No chair mentioned in this text."
    result = _extract_chair(text)
    assert result is None


# ── Table to markdown ────────────────────────────────────────────────────────

def test_table_to_markdown():
    table = [["Name", "Amount"], ["CDCN", "£500"], ["Other", "£200"]]
    md = _table_to_markdown(table)
    assert "| Name | Amount |" in md
    assert "| CDCN | £500 |" in md
    assert "---" in md

def test_table_to_markdown_empty():
    assert _table_to_markdown([]) == ""
    assert _table_to_markdown([[]]) == ""


# ── Parse document (text files) ──────────────────────────────────────────────

def test_parse_text_file(tmp_path):
    f = tmp_path / "test.txt"
    f.write_text("Meeting Minutes\n\nPresent: Alice, Bob\n\n1. Welcome\nHello everyone.")
    result = parse_document(f)
    assert isinstance(result, ParsedDocument)
    assert len(result.full_text) > 0
    assert result.title

def test_parse_empty_text_file(tmp_path):
    f = tmp_path / "empty.txt"
    f.write_text("")
    result = parse_document(f)
    assert result.full_text == ""

def test_parse_unsupported_type(tmp_path):
    f = tmp_path / "test.xyz"
    f.write_text("some content")
    result = parse_document(f)
    assert result.full_text == ""


# ── PDF fallback chain (mocked) ─────────────────────────────────────────────

def test_pdf_fallback_pdfplumber_success(tmp_path):
    """Test that pdfplumber is tried first and succeeds."""
    import sys
    f = tmp_path / "test.pdf"
    f.write_bytes(b"%PDF-1.4 fake content")  # Minimal fake PDF

    mock_page = MagicMock()
    mock_page.extract_text.return_value = "This is a test document with enough text to pass the threshold of one hundred characters per page easily."
    mock_page.extract_tables.return_value = []

    mock_pdf = MagicMock()
    mock_pdf.pages = [mock_page]
    mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
    mock_pdf.__exit__ = MagicMock(return_value=False)

    mock_pdfplumber = MagicMock()
    mock_pdfplumber.open.return_value = mock_pdf

    with patch.dict(sys.modules, {"pdfplumber": mock_pdfplumber}):
        result = parse_document(f)

    assert len(result.full_text) > 0
    assert result.extraction_method == "pdfplumber"


# ── DOCX parsing (mocked) ───────────────────────────────────────────────────

def test_docx_corrupted_file(tmp_path):
    """Test that corrupted DOCX files are handled gracefully."""
    f = tmp_path / "corrupted.docx"
    f.write_bytes(b"this is not a valid docx file")
    result = parse_document(f)
    assert isinstance(result, ParsedDocument)
    # Should not raise, should return empty or minimal result
