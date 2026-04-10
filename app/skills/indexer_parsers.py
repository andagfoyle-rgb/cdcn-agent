"""
indexer_parsers — file parsing, MIME validation, and chunking for the document indexer.

Provides the parser chain (unstructured → native fallback) and character-based
chunking with page-number tracking.
"""
import logging
from pathlib import Path
from typing import NamedTuple

from app.config import settings

import sys as _sys

# python-magic segfaults on Windows (missing libmagic DLL).
# Declare the name at module level so tests can patch it; only import on non-Windows.
magic = None
if _sys.platform != "win32":
    try:
        import magic  # type: ignore[assignment]  # replaces None above
    except (ImportError, OSError):
        pass

log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

SUPPORTED_SUFFIXES = frozenset({".pdf", ".docx", ".txt", ".md"})

ALLOWED_MIME_TYPES = frozenset(
    {
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "text/plain",
        "text/markdown",
        "text/x-markdown",
    }
)

# ~500 tokens and ~50-token overlap expressed in characters (1 token ≈ 4 chars)
CHUNK_CHARS = 2_000
OVERLAP_CHARS = 200


# ── Chunking ──────────────────────────────────────────────────────────────────


class _PagedElement(NamedTuple):
    text: str
    page: int


def _chunk_elements(
    elements: list[_PagedElement],
    chunk_chars: int = CHUNK_CHARS,
    overlap_chars: int = OVERLAP_CHARS,
) -> list[tuple[str, int]]:
    """
    Flatten elements into a single string, then slide a window over it.

    Returns list of (chunk_text, page_number) tuples.
    Page number is that of the element at the chunk's start position.

    The overlap means consecutive chunks share `overlap_chars` characters,
    which preserves cross-sentence context at boundaries.
    """
    if not elements:
        return []

    # Build flat text and a per-element (start_offset, page) index
    flat = ""
    el_starts: list[tuple[int, int]] = []  # (char_offset, page)
    for el in elements:
        el_starts.append((len(flat), el.page))
        flat += el.text + "\n"

    def _page_at(pos: int) -> int:
        page = 1
        for start, p in el_starts:
            if start <= pos:
                page = p
            else:
                break
        return page

    chunks: list[tuple[str, int]] = []
    pos = 0
    step = max(1, chunk_chars - overlap_chars)

    while pos < len(flat):
        raw = flat[pos : pos + chunk_chars]
        chunk = raw.strip()
        if chunk:
            chunks.append((chunk, _page_at(pos)))
        pos += step

    return chunks


# ── MIME validation ───────────────────────────────────────────────────────────


def _validate_mime(path: Path) -> bool:
    """
    Check MIME type via python-magic.  Returns True for allowed types.
    Fails open (returns True) if python-magic is not available (magic is None).
    Uses the module-level ``magic`` name so tests can patch it.
    """
    if magic is None:
        # python-magic not available (e.g. absent on Windows) — rely on extension allowlist
        return True
    try:
        mime = magic.from_file(str(path), mime=True)
        allowed = mime in ALLOWED_MIME_TYPES
        if not allowed:
            log.warning("Rejected MIME type '%s' for %s", mime, path.name)
        return allowed
    except Exception as exc:
        log.debug("MIME check failed for %s: %s — proceeding by extension", path.name, exc)
        return True


# ── File parsers ──────────────────────────────────────────────────────────────


def _parse_with_unstructured(path: Path) -> list[_PagedElement]:
    from unstructured.partition.auto import partition

    elements = partition(filename=str(path))
    result: list[_PagedElement] = []
    for el in elements:
        text = str(el).strip()
        if not text:
            continue
        page_raw = getattr(getattr(el, "metadata", None), "page_number", None)
        page = int(page_raw) if page_raw is not None else 1
        result.append(_PagedElement(text=text, page=page))
    return result


def _parse_pdf_fallback(path: Path) -> list[_PagedElement]:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    elements: list[_PagedElement] = []
    for i, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        if text:
            elements.append(_PagedElement(text=text, page=i))
    return elements


def _parse_docx_fallback(path: Path) -> list[_PagedElement]:
    from docx import Document

    doc = Document(str(path))
    text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
    return [_PagedElement(text=text.strip(), page=1)] if text.strip() else []


def _parse_text_fallback(path: Path) -> list[_PagedElement]:
    text = path.read_text(errors="replace").strip()
    return [_PagedElement(text=text, page=1)] if text else []


def _parse_file(path: Path) -> list[_PagedElement]:
    """
    Parse a file into page-tagged elements.

    For .txt and .md files the text fallback is used directly.
    For .pdf/.docx, unstructured is tried first (with a 10-second thread
    timeout to guard against import hangs), then the native-parser fallback.
    All errors are swallowed; the caller handles an empty result.
    """
    suffix = path.suffix.lower()

    # Plain-text types — skip unstructured, use fast fallback directly
    if suffix in (".txt", ".md"):
        return _parse_text_fallback(path)

    # For binary formats try unstructured with a thread timeout
    import concurrent.futures
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            future = ex.submit(_parse_with_unstructured, path)
            elements = future.result(timeout=settings.parser_timeout_secs)
            if elements:
                return elements
            log.debug("unstructured returned no elements for %s — using fallback", path.name)
    except concurrent.futures.TimeoutError:
        log.warning("unstructured timed out for %s — using fallback parser", path.name)
    except Exception as exc:
        log.debug("unstructured failed for %s: %s — using fallback", path.name, exc)

    if suffix == ".pdf":
        return _parse_pdf_fallback(path)
    if suffix == ".docx":
        return _parse_docx_fallback(path)
    return _parse_text_fallback(path)
