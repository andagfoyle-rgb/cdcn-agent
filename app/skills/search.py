"""
DocumentSearchSkill — three-layer retrieval over CDCN's document archive.

Search pipeline (layers run on every query):
  Layer 1: Document Table of Contents (injected into system prompt separately)
  Layer 2: Keyword/grep search — exact text matching via SQLite LIKE + grep
  Layer 3: Semantic search — vector similarity on child chunks

Results from all layers are merged, deduplicated by document, ranked by
how many layers found each document, and returned with full text and citations.

For small document collections (policies, funding, plans, agendas, reports),
all documents of that type are loaded in full automatically.
"""
import logging
import re
import sqlite3
from pathlib import Path

from app.config import settings
from app.skills.base import BaseSkill, SkillResult

# Re-export data classes so existing imports from search.py still work
from app.skills.search_keyword import (
    SearchHit,
    SearchResult,
    _DocCandidate,
    _DB_PATH,
    _MAX_CONTEXT_CHARS,
    _MAX_MINUTES_TO_LOAD,
    _SMALL_COLLECTION_TYPES,
    _STOP_WORDS,
    _get_candidate_doc_ids,
    _get_doc_metadata,
    _get_parent_chunks,
    _get_parent_id_for_child,
    _load_file_text,
    _load_full_documents_for_type,
    _resolve_path,
    keyword_search,
    _keyword_search_parents,
    _assemble_results,
)

log = logging.getLogger(__name__)

# ── Multi-word terms for term extraction ─────────────────────────────────────

_MULTI_WORD_TERMS = [
    "development officer", "solar pv", "solar panel", "ev charge",
    "charge point", "ai ethics", "action point", "action item",
    "board meeting", "annual report", "trustees report", "trustee report",
    "funding application", "grant application", "business plan",
    "community plan", "governance policy", "data handling",
    "financial year", "recovery and resilience",
]


# ── Term extraction ──────────────────────────────────────────────────────────


def extract_key_terms(query: str) -> list[str]:
    """
    Extract key search terms from a query using regex.
    Returns multi-word phrases and significant single words.
    No LLM call — pure regex extraction.
    """
    q_lower = query.lower()
    terms: list[str] = []

    # Multi-word terms first (higher value)
    for mw in _MULTI_WORD_TERMS:
        if mw in q_lower:
            terms.append(mw)

    # Named entities: capitalised words in original query
    for match in re.finditer(r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\b', query):
        name = match.group(1)
        if name.lower() not in _STOP_WORDS and len(name) > 2:
            terms.append(name)

    # Acronyms (2+ uppercase letters)
    for match in re.finditer(r'\b([A-Z]{2,})\b', query):
        terms.append(match.group(1))

    # Dates: "Month YYYY" patterns
    for match in re.finditer(r'\b((?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4})\b', query, re.IGNORECASE):
        terms.append(match.group(1))

    # Remaining significant single words
    words = re.findall(r'[a-zA-Z]+', query)
    for w in words:
        if w.lower() not in _STOP_WORDS and len(w) > 2 and w.lower() not in [t.lower() for t in terms]:
            terms.append(w)

    # Deduplicate preserving order
    seen = set()
    unique = []
    for t in terms:
        key = t.lower()
        if key not in seen:
            seen.add(key)
            unique.append(t)

    return unique


# ── Three-layer search function ──────────────────────────────────────────────


async def search(
    query: str,
    doc_type: list[str] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    section: str | None = None,
    top_k: int = 10,
    return_parents: bool = True,
    exact_terms: list[str] | None = None,
) -> SearchResult:
    """
    Three-layer retrieval:
      1. Extract key terms from query
      2. Run keyword/grep search (Layer 2)
      3. Run semantic search (Layer 3)
      4. Merge and deduplicate results
      5. For small document types, load all docs of that type
      6. Load full document text for top candidates
      7. Return assembled context with citations
    """
    from app.storage.vector_store import vector_store

    # ── Step 1: Extract terms ────────────────────────────────────────────────
    key_terms = extract_key_terms(query)
    log.info("Three-layer search: query='%s', terms=%s, doc_type=%s",
             query[:80], key_terms[:8], doc_type)

    # ── Check if this is a small collection type query ───────────────────────
    # If querying a small collection type, load ALL documents of that type
    small_type_hits: list[SearchHit] = []
    if doc_type:
        for dt in doc_type:
            if dt in _SMALL_COLLECTION_TYPES:
                type_hits = _load_full_documents_for_type(dt)
                small_type_hits.extend(type_hits)
                log.info("Small collection load: %d full documents for type '%s'",
                         len(type_hits), dt)

    # ── Step 2: Keyword search (Layer 2) ─────────────────────────────────────
    # Run keyword search with date filter but WITHOUT doc_type filter.
    # This ensures cross-type results are found (e.g. query about "funding"
    # that is discussed in minutes, not just funding documents).
    kw_candidates = keyword_search(
        terms=key_terms,
        doc_type=None,  # Don't restrict by type — keyword matches are strong signals
        date_from=date_from,
        date_to=date_to,
        exact_terms=exact_terms,
    )

    # Also get parent chunk IDs from keyword search (with filters for semantic)
    candidate_ids = _get_candidate_doc_ids(doc_type, date_from, date_to)
    # For keyword parent search, use date-only filter (not doc_type)
    date_only_ids = _get_candidate_doc_ids(None, date_from, date_to)
    kw_parent_ids = _keyword_search_parents(query, date_only_ids, limit=10)

    # For documents found by keyword search, also get their parent chunk IDs
    # so they're included even if the parent-level keyword threshold wasn't met
    for doc_id, cand in kw_candidates.items():
        try:
            with sqlite3.connect(_DB_PATH) as conn:
                rows = conn.execute(
                    "SELECT id FROM chunks WHERE doc_id = ? AND chunk_type = 'parent' "
                    "ORDER BY position_in_doc LIMIT 10",
                    (doc_id,),
                ).fetchall()
                for row in rows:
                    if row[0] not in kw_parent_ids:
                        kw_parent_ids.append(row[0])
        except Exception:
            pass

    # ── Step 3: Semantic search (Layer 3) ────────────────────────────────────
    where_conditions: list[dict] = []

    if candidate_ids is not None:
        if not candidate_ids and not kw_candidates and not small_type_hits:
            return SearchResult(hits=[], total_children_matched=0)
        if candidate_ids:
            if len(candidate_ids) == 1:
                where_conditions.append({"doc_id": candidate_ids[0]})
            else:
                where_conditions.append({"doc_id": {"$in": candidate_ids}})

    if section:
        where_conditions.append({"section_title": {"$eq": section}})

    where_conditions.append({"chunk_type": {"$eq": "child"}})

    where: dict | None = None
    if len(where_conditions) == 1:
        where = where_conditions[0]
    elif len(where_conditions) > 1:
        where = {"$and": where_conditions}

    hits = await vector_store.search(
        query=query,
        n_results=min(top_k * settings.search_overfetch_ratio, 50),
        where=where,
    )

    if not hits:
        where_fallback = None
        if candidate_ids and len(candidate_ids) == 1:
            where_fallback = {"doc_id": candidate_ids[0]}
        elif candidate_ids and len(candidate_ids) > 1:
            where_fallback = {"doc_id": {"$in": candidate_ids}}
        hits = await vector_store.search(
            query=query,
            n_results=top_k,
            where=where_fallback,
        )

    log.info("Semantic search: %d child hits", len(hits))

    # Track which docs were found by semantic search
    for h in hits:
        did = h.get("doc_id", "")
        if did and did in kw_candidates:
            kw_candidates[did].found_by.add("semantic")
            dist = h.get("distance", 1.0)
            if dist < kw_candidates[did].best_semantic_distance:
                kw_candidates[did].best_semantic_distance = dist
        elif did:
            meta = _get_doc_metadata(did)
            kw_candidates[did] = _DocCandidate(
                doc_id=did,
                filename=meta.get("filename", ""),
                filepath=meta.get("filepath", ""),
                doc_date=meta.get("doc_date", ""),
                doc_type=meta.get("doc_type", ""),
                found_by={"semantic"},
                best_semantic_distance=h.get("distance", 1.0),
            )

    if not return_parents:
        # Simple mode: return child texts directly
        result_hits = []
        for h in hits[:top_k]:
            doc_meta = _get_doc_metadata(h.get("doc_id", ""))
            doc_name = doc_meta.get("filename", h.get("source_file", "unknown"))
            doc_date = doc_meta.get("doc_date", "")
            doc_type_str = doc_meta.get("doc_type", h.get("doc_type", "other"))
            section_title = h.get("section_title", "")

            result_hits.append(SearchHit(
                content=h["text"],
                doc_name=doc_name,
                doc_date=doc_date or "",
                doc_type=doc_type_str,
                section_title=section_title,
                page_number=h.get("page_number", 0),
                relevance_score=1.0 - h.get("distance", 0.5),
                citation=f"From {doc_name} ({doc_date or 'undated'}), section: {section_title}",
                doc_id=h.get("doc_id", ""),
            ))
        return SearchResult(hits=result_hits, total_children_matched=len(hits))

    # ── Step 4: Build parent-child map from semantic hits ────────────────────
    parent_child_map: dict[int, list[dict]] = {}
    orphan_hits: list[dict] = []

    for h in hits:
        pid = h.get("parent_id")
        if pid and isinstance(pid, int) and pid > 0:
            parent_child_map.setdefault(pid, []).append(h)
        else:
            chunk_db_id = h.get("chunk_db_id")
            if chunk_db_id:
                pid = _get_parent_id_for_child(int(chunk_db_id))
                if pid:
                    parent_child_map.setdefault(pid, []).append(h)
                    continue
            orphan_hits.append(h)

    # Add keyword-matched parents
    for kpid in kw_parent_ids:
        if kpid not in parent_child_map:
            parent_child_map[kpid] = [{"distance": 0.25, "text": "", "doc_id": ""}]
            log.info("Keyword search added parent %d", kpid)
        else:
            parent_child_map[kpid].append({"distance": 0.25, "text": "", "doc_id": ""})
            log.info("Keyword boost applied to parent %d", kpid)

    # Retrieve parent chunks
    parent_ids = list(parent_child_map.keys())
    parents = _get_parent_chunks(parent_ids)

    # ── Step 5: Rank and assemble results ────────────────────────────────────
    result_hits = _assemble_results(
        kw_candidates, parents, parent_child_map, orphan_hits,
        small_type_hits, doc_type, top_k,
    )

    log.info("Three-layer search complete: %d hits, %d keyword candidates, %d semantic hits",
             len(result_hits), len(kw_candidates), len(hits))

    return SearchResult(hits=result_hits, total_children_matched=len(hits))


# ── Skill wrapper ────────────────────────────────────────────────────────────


class DocumentSearchSkill(BaseSkill):
    """
    Search CDCN's indexed document library with three-layer retrieval.

    Layer 1: Document index (injected into system prompt)
    Layer 2: Keyword/grep exact matching
    Layer 3: Semantic vector search

    For small collections (policies, funding, plans), loads all documents automatically.
    Returns cited, full-section results from indexed PDFs, DOCX, and Markdown files.
    """

    name = "search"
    description = (
        "Search the CDCN document archive using keyword and semantic search. "
        "Returns full document content with citations. For small document collections "
        "(policies, funding, plans), loads all documents automatically."
    )

    def __init__(self, settings_obj=None, vector_store_override=None) -> None:
        self._vector_store = vector_store_override

    async def run(
        self,
        query_or_dict=None,
        n_results: int = 10,
        doc_type: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        section: str | None = None,
        exact_terms: list[str] | None = None,
        **kwargs,
    ) -> SkillResult:
        # Accept a dict (e.g. run({'query': '...', 'n_results': 5}))
        if isinstance(query_or_dict, dict):
            kwargs.update(query_or_dict)
            query = kwargs.pop("query", "")
            n_results = int(kwargs.pop("n_results", n_results))
            doc_type = kwargs.pop("doc_type", doc_type)
            date_from = kwargs.pop("date_from", date_from)
            date_to = kwargs.pop("date_to", date_to)
            section = kwargs.pop("section", section)
            exact_terms = kwargs.pop("exact_terms", exact_terms)
        else:
            query = query_or_dict or kwargs.pop("query", "") or ""

        if not query:
            return SkillResult(success=False, error="query is required")

        # Normalise doc_type to list
        doc_type_list = None
        if doc_type:
            doc_type_list = [doc_type] if isinstance(doc_type, str) else list(doc_type)

        # Normalise exact_terms
        if exact_terms and isinstance(exact_terms, str):
            exact_terms = [exact_terms]

        try:
            result = await search(
                query=query,
                doc_type=doc_type_list,
                date_from=date_from,
                date_to=date_to,
                section=section,
                top_k=n_results,
                return_parents=True,
                exact_terms=exact_terms,
            )
        except Exception as exc:
            log.error("Search failed: %s", exc)
            return SkillResult(
                success=True,
                output=f"Search error: {exc}",
                metadata={"hits": 0, "error": str(exc)},
            )

        if not result.hits:
            return SkillResult(
                success=True,
                output="No relevant documents found for your query.",
                metadata={"hits": 0},
            )

        # Format output with citations
        output_parts: list[str] = []
        for hit in result.hits:
            header = f"=== FROM: {hit.doc_name} ({hit.doc_date or 'undated'}) — {hit.doc_type} ==="
            content = hit.content
            output_parts.append(f"{header}\n{content}")

        return SkillResult(
            success=True,
            output="\n\n---\n\n".join(output_parts),
            metadata={
                "hits": len(result.hits),
                "children_matched": result.total_children_matched,
                "citations": [h.citation for h in result.hits],
            },
        )


# Backward-compat alias
SearchSkill = DocumentSearchSkill
