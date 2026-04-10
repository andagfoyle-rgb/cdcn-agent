"""
Document loading, grep search, and result assembly for the CDCN search pipeline.

Extracted from search_keyword.py to keep each module under 500 lines.

Contains:
  - Grep search on document files
  - Full document loading (file I/O for PDF, DOCX, plain text)
  - Result assembly (ranking and merging hits from all layers)
"""
import hashlib
import logging
import re
import subprocess
from pathlib import Path

from app.config import settings

log = logging.getLogger(__name__)

# ── Constants (re-imported from search_keyword for convenience) ──────────────

_DOCS_DIR = Path(settings.watched_folder)
_MAX_CONTEXT_CHARS = 400_000
_MAX_MINUTES_TO_LOAD = 8


# ── Grep search on document files ───────────────────────────────────────────

def _grep_search(
    terms: list[str],
    candidates: dict,
    candidate_ids: list[str] | None,
    _DocCandidate,
) -> None:
    """Run grep on document files to find exact term matches."""
    docs_dir = _DOCS_DIR
    if not docs_dir.exists():
        return

    for term in terms[:10]:  # limit to avoid excessive greps
        if len(term) < 3:
            continue
        # Sanitise: skip terms with shell-unsafe characters
        if not re.match(r'^[\w\s\-\.]+$', term):
            continue
        try:
            result = subprocess.run(
                ['grep', '-ril', '--include=*.txt', '--include=*.md', term, str(docs_dir)],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0 and result.stdout.strip():
                for filepath in result.stdout.strip().split('\n'):
                    filepath = filepath.strip()
                    if not filepath:
                        continue
                    # Convert filepath to doc_id
                    doc_id = hashlib.sha256(filepath.encode()).hexdigest()[:20]

                    # If we have candidate_ids filter, check membership
                    if candidate_ids is not None and doc_id not in candidate_ids:
                        continue

                    if doc_id not in candidates:
                        filename = Path(filepath).name
                        candidates[doc_id] = _DocCandidate(
                            doc_id=doc_id,
                            filename=filename,
                            filepath=filepath,
                            doc_date="",
                            doc_type="",
                        )
                    candidates[doc_id].found_by.add("grep")
        except (subprocess.TimeoutExpired, Exception) as exc:
            log.debug("Grep failed for term '%s': %s", term, exc)


# ── Full document loading ────────────────────────────────────────────────────


def _load_file_text(filepath: Path) -> str:
    """Load plain text from a document file."""
    try:
        resolved = filepath.resolve()
        watched = Path(settings.watched_folder).resolve()
        if not str(resolved).startswith(str(watched)):
            return ""
    except (OSError, ValueError):
        return ""

    try:
        suffix = filepath.suffix.lower()
        if suffix == ".pdf":
            result = subprocess.run(
                ["pdftotext", str(filepath), "-"],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0:
                return result.stdout
        elif suffix == ".docx":
            try:
                result = subprocess.run(
                    ["pandoc", str(filepath), "-t", "plain"],
                    capture_output=True, text=True, timeout=30,
                )
                if result.returncode == 0 and result.stdout.strip():
                    return result.stdout
            except FileNotFoundError:
                pass
            try:
                from docx import Document
                doc = Document(str(filepath))
                return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
            except Exception:
                return ""
        else:
            return filepath.read_text(errors="replace")
    except Exception as exc:
        log.warning("Failed to load %s: %s", filepath.name, exc)
    return ""


def _resolve_path(source_file: str) -> Path | None:
    """Resolve a source_file to an absolute Path under the docs directory."""
    docs_dir = _DOCS_DIR
    candidate = docs_dir / source_file
    if candidate.exists():
        return candidate
    bare_name = Path(source_file).name
    matches = list(docs_dir.rglob(bare_name))
    return matches[0] if matches else None


def _load_full_documents_for_type(doc_type: str, _get_all_docs_by_type) -> list:
    """Load ALL documents of a given type in full. For small collections only."""
    from app.skills.search_keyword import SearchHit

    docs = _get_all_docs_by_type(doc_type)
    hits: list = []

    for d in docs:
        filepath_str = d.get("filepath", "")
        filepath = Path(filepath_str) if filepath_str else None
        if not filepath or not filepath.exists():
            # Try resolving by filename
            filepath = _resolve_path(d.get("filename", ""))
        if not filepath or not filepath.exists():
            continue

        text = _load_file_text(filepath)
        if not text.strip():
            continue

        hits.append(SearchHit(
            content=text,
            doc_name=d.get("filename", ""),
            doc_date=d.get("doc_date", "") or "",
            doc_type=doc_type,
            section_title="[Full Document]",
            page_number=1,
            relevance_score=0.9,  # High relevance (loaded by type match)
            citation=f"From {d.get('filename', '')} ({d.get('doc_date', 'undated')})",
            doc_id=d.get("doc_id", ""),
        ))

    return hits


# ── Result assembly ──────────────────────────────────────────────────────────


def _assemble_results(
    kw_candidates: dict,
    parents: dict[int, dict],
    parent_child_map: dict[int, list[dict]],
    orphan_hits: list[dict],
    small_type_hits: list,
    doc_type: list[str] | None,
    top_k: int,
    _get_doc_metadata,
) -> list:
    """Rank and assemble final search results from all layers."""
    from app.skills.search_keyword import SearchHit

    result_hits: list = []
    seen_docs: set[str] = set()
    total_chars = 0

    # For top keyword-matched documents (high score), load full document text
    kw_sorted = sorted(kw_candidates.values(), key=lambda c: -c.keyword_score)
    _full_load_limit = _MAX_MINUTES_TO_LOAD if not doc_type else 15
    for cand in kw_sorted[:_full_load_limit]:
        if cand.keyword_score < 2:
            continue  # Only load full docs for strong keyword matches
        filepath = Path(cand.filepath) if cand.filepath else None
        if not filepath or not filepath.exists():
            filepath = _resolve_path(cand.filename) if cand.filename else None
        if not filepath or not filepath.exists():
            continue
        text = _load_file_text(filepath)
        if not text.strip():
            continue
        if total_chars + len(text) > _MAX_CONTEXT_CHARS:
            continue
        meta = _get_doc_metadata(cand.doc_id)
        doc_name = meta.get("filename", cand.filename or "")
        doc_date = meta.get("doc_date", cand.doc_date or "")
        doc_type_str = meta.get("doc_type", cand.doc_type or "")
        result_hits.append(SearchHit(
            content=text,
            doc_name=doc_name,
            doc_date=doc_date or "",
            doc_type=doc_type_str,
            section_title="[Full Document]",
            page_number=1,
            relevance_score=0.85,
            citation=f"From {doc_name} ({doc_date or 'undated'}) — {doc_type_str}",
            doc_id=cand.doc_id,
        ))
        seen_docs.add(cand.doc_id)
        total_chars += len(text)
        if len(result_hits) >= top_k:
            break

    # Sort parents: keyword+semantic first, then keyword-only, then semantic-only
    sorted_parents = sorted(
        parent_child_map.items(),
        key=lambda x: (
            0 if any(c.get("distance", 1.0) <= 0.25 for c in x[1]) else 1,
            min(c.get("distance", 1.0) for c in x[1]),
        ),
    )

    # First: add keyword-matched parents (highest priority)
    seen_parents: set[int] = set()
    for pid, children in sorted_parents:
        if pid in seen_parents:
            continue
        seen_parents.add(pid)

        parent = parents.get(pid)
        if not parent:
            continue

        best_distance = min(c.get("distance", 1.0) for c in children)
        doc_id = parent.get("doc_id", "")

        doc_meta = _get_doc_metadata(doc_id)
        doc_name = doc_meta.get("filename", "unknown")
        doc_date = doc_meta.get("doc_date", "")
        doc_type_str = doc_meta.get("doc_type", "other")
        section_title = parent.get("section_title", "")

        content = parent["content"]
        if total_chars + len(content) > _MAX_CONTEXT_CHARS:
            continue

        result_hits.append(SearchHit(
            content=content,
            doc_name=doc_name,
            doc_date=doc_date or "",
            doc_type=doc_type_str,
            section_title=section_title,
            page_number=parent.get("page_number", 0),
            relevance_score=round(1.0 - best_distance, 4),
            citation=f"From {doc_name} ({doc_date or 'undated'}), section: {section_title}",
            parent_id=pid,
            doc_id=doc_id,
        ))
        seen_docs.add(doc_id)
        total_chars += len(content)

        if len(result_hits) >= top_k:
            break

    # Then: add full documents from small collections (supplementary context)
    if len(result_hits) < top_k:
        for hit in small_type_hits:
            if hit.doc_id in seen_docs:
                continue
            if total_chars + len(hit.content) > _MAX_CONTEXT_CHARS:
                continue
            if len(result_hits) >= top_k:
                break
            seen_docs.add(hit.doc_id)
            result_hits.append(hit)
            total_chars += len(hit.content)

    # Include orphan hits if we don't have enough results
    if len(result_hits) < top_k:
        for h in orphan_hits:
            if len(result_hits) >= top_k:
                break
            doc_meta = _get_doc_metadata(h.get("doc_id", ""))
            doc_name = doc_meta.get("filename", h.get("source_file", "unknown"))
            doc_date = doc_meta.get("doc_date", "")
            section_title = h.get("section_title", "")
            content = h["text"]
            if total_chars + len(content) > _MAX_CONTEXT_CHARS:
                continue

            result_hits.append(SearchHit(
                content=content,
                doc_name=doc_name,
                doc_date=doc_date or "",
                doc_type=doc_meta.get("doc_type", h.get("doc_type", "other")),
                section_title=section_title,
                page_number=h.get("page_number", 0),
                relevance_score=round(1.0 - h.get("distance", 0.5), 4),
                citation=f"From {doc_name} ({doc_date or 'undated'}), section: {section_title}",
                doc_id=h.get("doc_id", ""),
            ))
            total_chars += len(content)

    return result_hits
