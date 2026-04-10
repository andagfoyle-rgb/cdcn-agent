"""
RetrievalPipeline — composable multi-method retrieval with scoring and logging.

Wraps the existing keyword, semantic, and metadata retrieval methods.
Each method runs independently with a timeout. Results are merged,
deduplicated by document, and scored by how many methods found them.
"""
import asyncio
import json
import logging
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

import aiosqlite

from app.config import settings

log = logging.getLogger(__name__)

_DB_PATH = str(Path(settings.chroma_path).parent / "indexer.db")


@dataclass
class ScoredDocument:
    doc_id: str
    filename: str
    doc_type: str
    doc_date: str
    score: float = 0.0
    found_by: set = field(default_factory=set)
    keyword_score: int = 0
    semantic_distance: float = 1.0


@dataclass
class RetrievalResult:
    documents: list[ScoredDocument] = field(default_factory=list)
    method_status: dict = field(default_factory=dict)
    total_time_ms: int = 0
    tokens_loaded: int = 0


# ── Scoring weights ────────────────────────────────────────────────────────

_METHOD_WEIGHTS = {
    "keyword": 2.0,
    "semantic": 1.5,
    "metadata": 1.0,
    "document_index": 1.0,
}

# Multi-method bonuses
_MULTI_METHOD_BONUS = {
    3: 2.0,  # found by 3+ methods = highest confidence
    2: 1.5,  # found by 2 methods
    1: 1.0,  # single method
}


def _compute_score(doc: ScoredDocument) -> float:
    """Compute final relevance score based on methods and distances."""
    base = 0.0
    for method in doc.found_by:
        weight = _METHOD_WEIGHTS.get(method, 1.0)
        base += weight

    # Bonus for being found by multiple methods
    bonus = _MULTI_METHOD_BONUS.get(min(len(doc.found_by), 3), 1.0)
    base *= bonus

    # Semantic distance penalty (lower distance = better)
    if doc.semantic_distance < 1.0:
        base += (1.0 - doc.semantic_distance) * 2.0

    # Keyword score boost
    base += doc.keyword_score * 0.5

    return round(base, 3)


class RetrievalPipeline:
    """
    Composable retrieval pipeline. Each method returns scored results.
    Results are merged, deduplicated, and ranked by combined score.
    """

    def __init__(self):
        self._method_timeout = 30  # seconds per method

    async def retrieve(
        self,
        query: str,
        doc_type: list[str] | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        user_id: str = "",
    ) -> RetrievalResult:
        """Run all methods, merge results, return ranked documents."""
        t0 = time.monotonic()
        all_docs: dict[str, ScoredDocument] = {}
        method_status: dict[str, str] = {}

        # Run methods with individual timeouts
        methods = [
            ("keyword", self._keyword_search, query, doc_type, date_from, date_to),
            ("semantic", self._semantic_search, query, doc_type, date_from, date_to),
            ("metadata", self._metadata_filter, query, doc_type, date_from, date_to),
        ]

        for name, method, *args in methods:
            mt0 = time.monotonic()
            try:
                results = await asyncio.wait_for(
                    method(*args), timeout=self._method_timeout
                )
                for doc in results:
                    if doc.doc_id in all_docs:
                        existing = all_docs[doc.doc_id]
                        existing.found_by.update(doc.found_by)
                        existing.keyword_score = max(existing.keyword_score, doc.keyword_score)
                        existing.semantic_distance = min(existing.semantic_distance, doc.semantic_distance)
                    else:
                        all_docs[doc.doc_id] = doc
                elapsed = int((time.monotonic() - mt0) * 1000)
                method_status[name] = f"ok:{len(results)}results:{elapsed}ms"
            except asyncio.TimeoutError:
                method_status[name] = "timeout"
                log.warning("Retrieval method %s timed out after %ds", name, self._method_timeout)
            except Exception as exc:
                method_status[name] = f"error:{str(exc)[:100]}"
                log.warning("Retrieval method %s failed: %s", name, exc)

        # Score and sort
        for doc in all_docs.values():
            doc.score = _compute_score(doc)

        ranked = sorted(all_docs.values(), key=lambda d: -d.score)
        total_ms = int((time.monotonic() - t0) * 1000)

        log.info(
            "Retrieval pipeline: %s — %d unique documents in %dms",
            method_status, len(ranked), total_ms,
        )

        result = RetrievalResult(
            documents=ranked,
            method_status=method_status,
            total_time_ms=total_ms,
        )

        # Log to retrieval_log table (fire and forget)
        asyncio.create_task(self._log_retrieval(query, result, user_id))

        return result

    # ── Individual retrieval methods ─────────────────────────────────────────

    async def _keyword_search(
        self, query, doc_type, date_from, date_to,
    ) -> list[ScoredDocument]:
        from app.skills.search import keyword_search, extract_key_terms

        terms = extract_key_terms(query)
        candidates = keyword_search(
            terms=terms, doc_type=doc_type,
            date_from=date_from, date_to=date_to,
        )
        results = []
        for doc_id, cand in candidates.items():
            results.append(ScoredDocument(
                doc_id=doc_id,
                filename=cand.filename,
                doc_type=cand.doc_type,
                doc_date=cand.doc_date,
                found_by={"keyword"},
                keyword_score=cand.keyword_score,
            ))
        return results

    async def _semantic_search(
        self, query, doc_type, date_from, date_to,
    ) -> list[ScoredDocument]:
        from app.storage.vector_store import vector_store

        hits = await vector_store.search(query=query, n_results=24, doc_type=None)
        docs: dict[str, ScoredDocument] = {}
        for hit in hits:
            did = hit.get("doc_id", "")
            if not did:
                continue
            dist = hit.get("distance", 1.0)
            if did in docs:
                docs[did].semantic_distance = min(docs[did].semantic_distance, dist)
            else:
                docs[did] = ScoredDocument(
                    doc_id=did,
                    filename=hit.get("source_file", ""),
                    doc_type=hit.get("document_type", ""),
                    doc_date=hit.get("doc_date", ""),
                    found_by={"semantic"},
                    semantic_distance=dist,
                )
        return list(docs.values())

    async def _metadata_filter(
        self, query, doc_type, date_from, date_to,
    ) -> list[ScoredDocument]:
        if not doc_type and not date_from and not date_to:
            return []
        try:
            with sqlite3.connect(_DB_PATH) as conn:
                conn.row_factory = sqlite3.Row
                clauses = []
                params = []
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
                rows = conn.execute(
                    f"SELECT * FROM document_metadata {where} ORDER BY doc_date DESC LIMIT 20",
                    params,
                ).fetchall()
            return [
                ScoredDocument(
                    doc_id=r["doc_id"] or "",
                    filename=r["filename"],
                    doc_type=r["doc_type"],
                    doc_date=r["doc_date"] or "",
                    found_by={"metadata"},
                )
                for r in rows
            ]
        except Exception as exc:
            log.warning("Metadata filter failed: %s", exc)
            return []

    # ── Retrieval logging ────────────────────────────────────────────────────

    async def _log_retrieval(self, query: str, result: RetrievalResult, user_id: str):
        """Log retrieval results to the retrieval_log table."""
        try:
            db_path = _DB_PATH
            async with aiosqlite.connect(db_path) as conn:
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS retrieval_log (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        query TEXT,
                        timestamp TEXT DEFAULT (datetime('now')),
                        methods_used TEXT,
                        documents_found INTEGER,
                        tokens_loaded INTEGER,
                        time_ms INTEGER,
                        user_id TEXT
                    )
                """)
                await conn.execute(
                    """INSERT INTO retrieval_log
                       (query, methods_used, documents_found, tokens_loaded, time_ms, user_id)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        query[:500],
                        json.dumps(result.method_status),
                        len(result.documents),
                        result.tokens_loaded,
                        result.total_time_ms,
                        user_id,
                    ),
                )
                await conn.commit()
        except Exception as exc:
            log.debug("Failed to log retrieval: %s", exc)


# Module-level singleton
retrieval_pipeline = RetrievalPipeline()
