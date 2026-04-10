"""
Keyword, grep, and document-loading helpers for the CDCN search pipeline.

Contains:
  - Data classes: SearchHit, SearchResult, _DocCandidate
  - Constants: database paths, stop words, collection config
  - Metadata pre-filter queries (doc_ids by type/date)
  - Parent chunk retrieval from SQLite
  - Layer 2 keyword search (SQLite LIKE + grep)
  - Keyword-scored parent chunk search

Document loading, grep search, and result assembly live in search_loader.py.

All public names are consumed by the main search orchestrator in search.py.
"""
import hashlib
import logging
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from app.config import settings

log = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

# SQLite paths
_DB_PATH = str(Path(settings.chroma_path).parent / "indexer.db")
_DOCS_DIR = Path(settings.watched_folder)

# Token budget for context assembly (~100K tokens = ~400K chars)
_MAX_CONTEXT_CHARS = 400_000

# Small collection types: load ALL documents of these types when queried
_SMALL_COLLECTION_TYPES = {"policy", "funding", "plan", "agenda", "report"}
# For minutes (large collection), load top N most relevant
_MAX_MINUTES_TO_LOAD = 8

_STOP_WORDS = frozenset({
    "the", "a", "an", "in", "of", "to", "for", "by", "was", "what",
    "is", "are", "were", "been", "be", "have", "has", "had", "do",
    "does", "did", "will", "would", "could", "should", "may", "can",
    "our", "we", "they", "it", "this", "that", "with", "from", "at",
    "on", "as", "or", "and", "but", "not", "no", "so", "if", "about",
    "which", "who", "whom", "how", "when", "where", "why", "there",
    "their", "its", "my", "your", "his", "her", "suggested", "please",
    "ok", "tell", "me", "you", "document", "across", "last", "all",
    "any", "some", "current", "what", "summarise", "summarize",
})


# ── Data classes ─────────────────────────────────────────────────────────────


@dataclass
class SearchHit:
    content: str           # parent chunk text or full document text
    doc_name: str          # source filename
    doc_date: str          # document date
    doc_type: str          # minutes, policy, etc.
    section_title: str     # section heading
    page_number: int
    relevance_score: float
    citation: str          # pre-formatted citation string
    parent_id: int | None = None
    doc_id: str = ""


@dataclass
class SearchResult:
    hits: list[SearchHit]
    total_children_matched: int = 0


@dataclass
class _DocCandidate:
    """A document found by one or more retrieval layers."""
    doc_id: str
    filename: str
    filepath: str
    doc_date: str
    doc_type: str
    found_by: set = field(default_factory=set)  # {"keyword", "semantic", "grep"}
    best_semantic_distance: float = 1.0
    keyword_score: int = 0
    matched_sections: list = field(default_factory=list)


# ── Metadata pre-filter ─────────────────────────────────────────────────────


def _get_candidate_doc_ids(
    doc_type: list[str] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[str] | None:
    """
    Query document_metadata to get doc_ids matching the filters.
    Returns None if no filters are active (search all docs).
    Returns empty list if filters are active but no docs match.
    """
    if not doc_type and not date_from and not date_to:
        return None

    clauses: list[str] = []
    params: list = []

    if doc_type:
        placeholders = ",".join("?" * len(doc_type))
        clauses.append(f"doc_type IN ({placeholders})")
        params.extend(doc_type)
    if date_from:
        clauses.append("doc_date >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("doc_date <= ?")
        params.append(date_to)

    where = "WHERE " + " AND ".join(clauses)

    try:
        with sqlite3.connect(_DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"SELECT filepath FROM document_metadata {where}",
                params,
            ).fetchall()
        doc_ids = []
        for row in rows:
            filepath = row["filepath"]
            if filepath:
                doc_id = hashlib.sha256(filepath.encode()).hexdigest()[:20]
                doc_ids.append(doc_id)
        return doc_ids
    except Exception as exc:
        log.warning("Metadata pre-filter failed: %s", exc)
        return None


def _get_doc_metadata(doc_id: str) -> dict:
    """Get document metadata by looking up via indexed_files -> document_metadata."""
    try:
        with sqlite3.connect(_DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM indexed_files WHERE doc_id = ? LIMIT 1",
                (doc_id,),
            ).fetchone()
            if row:
                filepath = row["path"]
                filename = Path(filepath).name
                meta_row = conn.execute(
                    "SELECT * FROM document_metadata WHERE filename = ? LIMIT 1",
                    (filename,),
                ).fetchone()
                if meta_row:
                    return dict(meta_row)
                return {"filename": filename, "doc_type": row["document_type"], "doc_date": None}
    except Exception as exc:
        log.debug("get_doc_metadata failed for %s: %s", doc_id, exc)
    return {}


def _get_all_docs_by_type(doc_type: str) -> list[dict]:
    """Get all document metadata rows for a given type."""
    try:
        with sqlite3.connect(_DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM document_metadata WHERE doc_type = ? ORDER BY doc_date DESC",
                (doc_type,),
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception as exc:
        log.warning("_get_all_docs_by_type failed: %s", exc)
        return []


# ── Parent chunk retrieval ───────────────────────────────────────────────────


def _get_parent_chunks(parent_ids: list[int]) -> dict[int, dict]:
    """Retrieve parent chunks from the chunks table by their IDs."""
    if not parent_ids:
        return {}
    try:
        with sqlite3.connect(_DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            placeholders = ",".join("?" * len(parent_ids))
            rows = conn.execute(
                f"SELECT * FROM chunks WHERE id IN ({placeholders}) AND chunk_type = 'parent'",
                parent_ids,
            ).fetchall()
        return {row["id"]: dict(row) for row in rows}
    except Exception as exc:
        log.warning("Parent chunk retrieval failed: %s", exc)
        return {}


def _get_parent_id_for_child(child_chunk_db_id: int) -> int | None:
    """Look up parent_id for a child chunk in the chunks table."""
    try:
        with sqlite3.connect(_DB_PATH) as conn:
            row = conn.execute(
                "SELECT parent_id FROM chunks WHERE id = ?",
                (child_chunk_db_id,),
            ).fetchone()
            return row[0] if row else None
    except Exception:
        return None


# ── Layer 2: Keyword search ─────────────────────────────────────────────────


def keyword_search(
    terms: list[str],
    doc_type: list[str] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    exact_terms: list[str] | None = None,
) -> dict[str, _DocCandidate]:
    """
    Layer 2: Search using SQLite LIKE queries on chunks + grep on disk.
    Returns a dict of doc_id -> _DocCandidate for documents matching keywords.
    """
    from app.skills.search_loader import _grep_search

    candidates: dict[str, _DocCandidate] = {}

    # Combine regular extracted terms with explicit exact_terms
    all_terms = list(terms)
    if exact_terms:
        all_terms.extend(exact_terms)

    if not all_terms:
        return candidates

    # Get candidate doc_ids from filters
    candidate_ids = _get_candidate_doc_ids(doc_type, date_from, date_to)

    # ── SQLite LIKE search on chunks table ──────────────────────────────────
    try:
        with sqlite3.connect(_DB_PATH) as conn:
            conn.row_factory = sqlite3.Row

            for term in all_terms[:15]:  # limit terms to avoid huge queries
                doc_filter = ""
                params: list = [f"%{term}%"]

                if candidate_ids is not None:
                    if not candidate_ids:
                        continue
                    placeholders = ",".join("?" * len(candidate_ids))
                    doc_filter = f" AND doc_id IN ({placeholders})"
                    params.extend(candidate_ids)

                rows = conn.execute(
                    f"SELECT DISTINCT doc_id, section_title FROM chunks "
                    f"WHERE content LIKE ?{doc_filter} LIMIT 50",
                    params,
                ).fetchall()

                for row in rows:
                    did = row["doc_id"]
                    if did not in candidates:
                        meta = _get_doc_metadata(did)
                        candidates[did] = _DocCandidate(
                            doc_id=did,
                            filename=meta.get("filename", ""),
                            filepath=meta.get("filepath", ""),
                            doc_date=meta.get("doc_date", ""),
                            doc_type=meta.get("doc_type", ""),
                        )
                    candidates[did].found_by.add("keyword")
                    candidates[did].keyword_score += 1
                    if row["section_title"]:
                        candidates[did].matched_sections.append(row["section_title"])

    except Exception as exc:
        log.warning("SQLite keyword search failed: %s", exc)

    # ── Grep search on document files ───────────────────────────────────────
    _grep_search(all_terms, candidates, candidate_ids, _DocCandidate)

    log.info("Keyword search: %d terms -> %d candidate documents", len(all_terms), len(candidates))
    return candidates


# ── Layer 2 (enhanced): Keyword search on parent chunks with scoring ────────


def _keyword_search_parents(
    query: str,
    candidate_ids: list[str] | None = None,
    limit: int = 10,
) -> list[int]:
    """
    SQLite keyword search: find parent chunk IDs whose content contains
    key phrases from the query. Returns parent IDs sorted by match score.
    """
    words = re.findall(r"[a-z]+", query.lower())
    keywords = [w for w in words if w not in _STOP_WORDS and len(w) > 2]
    if not keywords:
        return []

    try:
        with sqlite3.connect(_DB_PATH) as conn:
            score_expr = " + ".join(
                "CASE WHEN content LIKE ? THEN 1 ELSE 0 END"
                for _ in keywords
            )
            params: list = [f"%{kw}%" for kw in keywords]

            doc_filter = ""
            if candidate_ids:
                placeholders = ",".join("?" * len(candidate_ids))
                doc_filter = f" AND doc_id IN ({placeholders})"
                params.extend(candidate_ids)

            min_matches = 1
            rows = conn.execute(
                f"SELECT * FROM ("
                f"  SELECT id, ({score_expr}) as score FROM chunks "
                f"  WHERE chunk_type = 'parent'{doc_filter}"
                f") WHERE score >= ? ORDER BY score DESC LIMIT ?",
                params + [min_matches, limit],
            ).fetchall()
            return [r[0] for r in rows]
    except Exception as exc:
        log.debug("Keyword parent search failed: %s", exc)
        return []


# ── Re-export from search_loader for backward compatibility ─────────────────

from app.skills.search_loader import (  # noqa: E402, F401
    _load_file_text,
    _resolve_path,
    _load_full_documents_for_type as _load_full_documents_for_type_inner,
    _assemble_results as _assemble_results_inner,
)


def _load_full_documents_for_type(doc_type: str) -> list[SearchHit]:
    """Load ALL documents of a given type in full. For small collections only."""
    return _load_full_documents_for_type_inner(doc_type, _get_all_docs_by_type)


def _assemble_results(
    kw_candidates: dict[str, _DocCandidate],
    parents: dict[int, dict],
    parent_child_map: dict[int, list[dict]],
    orphan_hits: list[dict],
    small_type_hits: list[SearchHit],
    doc_type: list[str] | None,
    top_k: int,
) -> list[SearchHit]:
    """Rank and assemble final search results from all layers."""
    return _assemble_results_inner(
        kw_candidates, parents, parent_child_map, orphan_hits,
        small_type_hits, doc_type, top_k, _get_doc_metadata,
    )
