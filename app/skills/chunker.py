"""
chunker -- parent-child chunking for CDCN RAG pipeline.

Splits parsed documents into parent chunks (full sections) and child chunks
(individual paragraphs/items within sections). Child chunks are embedded for
search; parent chunks are retrieved for context.

Database operations live in chunker_helpers.py.
"""
import logging
import re
from dataclasses import dataclass, field

from app.skills.document_parser import ParsedDocument, ParsedSection

log = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

_MIN_CHILD_CHARS = 30
_MIN_SECTION_CHARS_FOR_SPLIT = 100
_MAX_CHILD_CHARS = 20_000
_OVERLAP_CHARS = 200
_SHORT_DOC_CHARS = 500

# ── Data classes ─────────────────────────────────────────────────────────────


@dataclass
class Chunk:
    doc_id: str
    chunk_type: str          # 'parent' or 'child'
    parent_id: int | None    # None for parents, references chunks.id for children
    section_title: str
    content: str
    token_count: int
    page_number: int
    position_in_doc: int
    embedding_id: str = ""   # set after vector store upsert
    db_id: int | None = None # set after DB insert


@dataclass
class ChunkedDocument:
    doc_id: str
    parents: list[Chunk] = field(default_factory=list)
    children: list[Chunk] = field(default_factory=list)


# ── Token counting ───────────────────────────────────────────────────────────

def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token for English."""
    return max(1, len(text) // 4)


# ── Child splitting ──────────────────────────────────────────────────────────

def _split_into_children(section: ParsedSection, doc_id: str, position_base: int) -> list[Chunk]:
    """
    Split a section's content into child chunks.

    Strategy:
      1. Split on double-newlines (paragraph breaks)
      2. Split on numbered items (1., 2., etc.)
      3. Split on bullet points (-, *, bullet)
      4. If a paragraph is very long (>800 chars), split on sentences

    Each child preserves the section title for metadata.
    """
    content = section.content.strip()
    if not content:
        return []

    # If section is very short, the parent is its own child
    if len(content) < _MIN_SECTION_CHARS_FOR_SPLIT:
        return [Chunk(
            doc_id=doc_id,
            chunk_type="child",
            parent_id=None,
            section_title=section.title,
            content=content,
            token_count=_estimate_tokens(content),
            page_number=section.page_number,
            position_in_doc=position_base,
        )]

    # Split on paragraph breaks first
    raw_parts = re.split(r"\n\s*\n", content)

    # Further split long paragraphs on numbered items or bullets
    parts: list[str] = []
    for part in raw_parts:
        part = part.strip()
        if not part:
            continue

        numbered = re.split(r"\n\s*(?=\d{1,2}\.\s+[A-Z])", part)
        if len(numbered) > 1:
            parts.extend(n.strip() for n in numbered if n.strip())
            continue

        bulleted = re.split(r"\n\s*(?=[•\-\*]\s+)", part)
        if len(bulleted) > 1:
            parts.extend(b.strip() for b in bulleted if b.strip())
            continue

        if len(part) > 800:
            sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z])", part)
            current = ""
            for sent in sentences:
                if len(current) + len(sent) > 600 and current:
                    parts.append(current.strip())
                    current = sent
                else:
                    current = current + " " + sent if current else sent
            if current.strip():
                parts.append(current.strip())
            continue

        parts.append(part)

    # Create child chunks
    children: list[Chunk] = []
    for i, part in enumerate(parts):
        if len(part) < _MIN_CHILD_CHARS:
            if children:
                children[-1].content += "\n" + part
                children[-1].token_count = _estimate_tokens(children[-1].content)
            continue

        children.append(Chunk(
            doc_id=doc_id,
            chunk_type="child",
            parent_id=None,
            section_title=section.title,
            content=part,
            token_count=_estimate_tokens(part),
            page_number=section.page_number,
            position_in_doc=position_base + i,
        ))

    # Add overlap
    if _OVERLAP_CHARS > 0 and len(children) > 1:
        for i in range(1, len(children)):
            prev_tail = children[i - 1].content[-_OVERLAP_CHARS:]
            clean_start = max(prev_tail.rfind(". "), prev_tail.rfind("\n"))
            if clean_start > 0:
                prev_tail = prev_tail[clean_start + 1:].strip()
            if prev_tail and len(prev_tail) > 20:
                children[i].content = prev_tail + "\n" + children[i].content
                children[i].token_count = _estimate_tokens(children[i].content)

    # Split any child chunk that exceeds the token limit
    final_children: list[Chunk] = []
    for child in children:
        if len(child.content) > _MAX_CHILD_CHARS:
            sentences = re.split(r"(?<=[.!?])\s+", child.content)
            current = ""
            for sent in sentences:
                if len(current) + len(sent) > _MAX_CHILD_CHARS and current:
                    final_children.append(Chunk(
                        doc_id=child.doc_id, chunk_type="child", parent_id=None,
                        section_title=child.section_title, content=current.strip(),
                        token_count=_estimate_tokens(current.strip()),
                        page_number=child.page_number, position_in_doc=child.position_in_doc,
                    ))
                    current = sent
                else:
                    current = current + " " + sent if current else sent
            if current.strip():
                final_children.append(Chunk(
                    doc_id=child.doc_id, chunk_type="child", parent_id=None,
                    section_title=child.section_title, content=current.strip(),
                    token_count=_estimate_tokens(current.strip()),
                    page_number=child.page_number, position_in_doc=child.position_in_doc,
                ))
            log.debug(
                "Split oversized child chunk (%d chars) into %d parts in section '%s'",
                len(child.content), len(final_children) - len(final_children) + 1,
                child.section_title,
            )
        else:
            final_children.append(child)

    return final_children


# ── Main chunking function ───────────────────────────────────────────────────

def chunk_document(parsed: ParsedDocument, doc_id: str) -> ChunkedDocument:
    """
    Create parent and child chunks from a parsed document.

    Parent chunks = full sections (no size limit).
    Child chunks = individual paragraphs/items within sections.
    Every child has a parent. Every parent has at least one child.
    """
    result = ChunkedDocument(doc_id=doc_id)
    position = 0

    full_text = parsed.full_text.strip() if parsed.full_text else ""
    if len(full_text) < _SHORT_DOC_CHARS and full_text:
        title = parsed.title or "Document"
        parent = Chunk(
            doc_id=doc_id, chunk_type="parent", parent_id=None,
            section_title=title, content=f"## {title}\n\n{full_text}",
            token_count=_estimate_tokens(full_text), page_number=1,
            position_in_doc=0,
        )
        child = Chunk(
            doc_id=doc_id, chunk_type="child", parent_id=None,
            section_title=title, content=full_text,
            token_count=_estimate_tokens(full_text), page_number=1,
            position_in_doc=1,
        )
        result.parents.append(parent)
        result.children.append(child)
        log.debug("Short document (%d chars) — single parent/child", len(full_text))
        warnings = validate_chunks(result, full_text)
        if warnings:
            log.warning("Chunk validation warnings for doc_id=%s: %s", doc_id, warnings)
        return result

    sections = parsed.sections
    if not sections:
        sections = [ParsedSection(
            title=parsed.title or "Document",
            content=parsed.full_text,
            page_number=1,
        )]

    for section in sections:
        content = section.content.strip()
        if not content:
            continue

        parent = Chunk(
            doc_id=doc_id, chunk_type="parent", parent_id=None,
            section_title=section.title,
            content=f"## {section.title}\n\n{content}",
            token_count=_estimate_tokens(content),
            page_number=section.page_number,
            position_in_doc=position,
        )
        result.parents.append(parent)

        children = _split_into_children(section, doc_id, position + 1)
        if not children:
            children = [Chunk(
                doc_id=doc_id, chunk_type="child", parent_id=None,
                section_title=section.title, content=content,
                token_count=_estimate_tokens(content),
                page_number=section.page_number, position_in_doc=position + 1,
            )]

        result.children.extend(children)
        position += len(children) + 1

    # Include table content as additional child chunks under a "Tables" parent
    if parsed.tables:
        table_content = "\n\n".join(t.markdown for t in parsed.tables)
        table_parent = Chunk(
            doc_id=doc_id, chunk_type="parent", parent_id=None,
            section_title="Tables",
            content=f"## Tables\n\n{table_content}",
            token_count=_estimate_tokens(table_content),
            page_number=parsed.tables[0].page_number,
            position_in_doc=position,
        )
        result.parents.append(table_parent)

        for i, table in enumerate(parsed.tables):
            if len(table.markdown) > _MIN_CHILD_CHARS:
                result.children.append(Chunk(
                    doc_id=doc_id, chunk_type="child", parent_id=None,
                    section_title="Tables", content=table.markdown,
                    token_count=_estimate_tokens(table.markdown),
                    page_number=table.page_number,
                    position_in_doc=position + i + 1,
                ))

        if not any(c.section_title == "Tables" for c in result.children):
            result.children.append(Chunk(
                doc_id=doc_id, chunk_type="child", parent_id=None,
                section_title="Tables", content=table_content,
                token_count=_estimate_tokens(table_content),
                page_number=parsed.tables[0].page_number,
                position_in_doc=position + 1,
            ))

    warnings = validate_chunks(result, parsed.full_text or "")
    if warnings:
        log.warning("Chunk validation warnings for doc_id=%s: %s", doc_id, warnings)

    return result


# ── Chunk validation ────────────────────────────────────────────────────────

def validate_chunks(chunked: ChunkedDocument, original_text: str) -> list[str]:
    """Validate chunked output for structural integrity."""
    warnings: list[str] = []

    parent_titles = {p.section_title for p in chunked.parents}
    child_titles = {c.section_title for c in chunked.children}

    for title in parent_titles:
        if title not in child_titles:
            msg = f"Parent '{title}' has no child chunks"
            warnings.append(msg)
            log.warning("validate_chunks: %s", msg)

    for chunk in chunked.parents + chunked.children:
        if not chunk.content or not chunk.content.strip():
            msg = f"Empty {chunk.chunk_type} chunk in section '{chunk.section_title}'"
            warnings.append(msg)
            log.warning("validate_chunks: %s", msg)

    for chunk in chunked.parents + chunked.children:
        if chunk.chunk_type == "child" and len(chunk.content) > _MAX_CHILD_CHARS:
            msg = (
                f"Child chunk in '{chunk.section_title}' exceeds max size "
                f"({len(chunk.content)} chars > {_MAX_CHILD_CHARS})"
            )
            warnings.append(msg)
            log.warning("validate_chunks: %s", msg)

    for chunk in chunked.children:
        if len(chunk.content.strip()) < 20:
            msg = (
                f"Very short child chunk ({len(chunk.content.strip())} chars) "
                f"in section '{chunk.section_title}'"
            )
            warnings.append(msg)
            log.warning("validate_chunks: %s", msg)

    original_stripped = re.sub(r"\s+", "", original_text)
    if original_stripped:
        children_text = "".join(re.sub(r"\s+", "", c.content) for c in chunked.children)
        coverage = len(children_text) / len(original_stripped)
        if coverage < 0.95:
            msg = (
                f"Child chunks cover only {coverage:.1%} of original text "
                f"(expected >=95%)"
            )
            warnings.append(msg)
            log.warning("validate_chunks: %s", msg)

    return warnings


# ── Re-export database operations from chunker_helpers ──────────────────────

from app.skills.chunker_helpers import (  # noqa: E402, F401
    init_chunks_db,
    delete_doc_chunks,
    store_chunks,
    get_parent_chunk,
    get_parent_by_child_id,
    get_parents_by_doc,
    drop_chunks_table,
)
