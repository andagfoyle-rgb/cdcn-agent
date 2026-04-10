"""
CDCNVectorStore — persistent ChromaDB wrapper with async embedding support.

Single collection "cdcn_documents" stores all indexed document chunks.
Embeddings are generated via the Ollama embed endpoint (nomic-embed-text).

Module-level singleton `vector_store` is shared by the indexer and search skills.
The legacy `get_collection()` function is preserved for backward compatibility.
"""
import asyncio
import logging
import re
from typing import Any

import chromadb
from chromadb.config import Settings as ChromaSettings

from app.config import settings

log = logging.getLogger(__name__)

COLLECTION_NAME = "cdcn_documents"

# Embedding dimension for nomic-embed-text (used for zero-vector fallback)
_EMBED_DIM = 768

# ── Metadata sanitisation ─────────────────────────────────────────────────────
# ChromaDB stores metadata as flat dicts; all values must be strings, ints,
# floats, or bools.  We enforce additional constraints before any upsert:
#   • All values coerced to str (with numeric passthrough)
#   • Strings truncated to 500 chars
#   • Angle brackets stripped to prevent XSS if values are ever rendered as HTML

_META_MAX_LEN = 500
_STRIP_ANGLES_RE = re.compile(r"[<>]")


def _sanitise_metadata(meta: dict) -> dict:
    """
    Return a copy of *meta* safe to pass to Chroma.

    Rules applied per value:
      - int / float / bool → kept as-is (Chroma native types)
      - everything else    → str(), strip angle brackets, truncate to 500 chars
    """
    out: dict = {}
    for k, v in meta.items():
        if isinstance(v, (int, float, bool)):
            out[k] = v
        else:
            s = _STRIP_ANGLES_RE.sub("", str(v))
            out[k] = s[:_META_MAX_LEN]
    return out


class CDCNVectorStore:
    """
    Manages a single ChromaDB collection for all CDCN document chunks.

    All embedding calls are async (via llm_client); Chroma itself is
    synchronous in-process — operations are fast and do not require
    run_in_executor wrapping at this scale.

    Chunk IDs are deterministic: "{doc_id}::chunk::{index:06d}"
    Metadata schema per chunk:
        doc_id         str  — stable hash of source file path
        source_file    str  — path relative to WATCHED_FOLDER
        page_number    int  — 1-based; 0 if unknown
        chunk_index    int  — 0-based position within document
        document_type  str  — "minutes"|"policy"|"application"|"constitution"|"report"|"other"
        indexed_at     str  — ISO timestamp
    """

    def __init__(self) -> None:
        self._client: chromadb.ClientAPI | None = None
        self._collection: chromadb.Collection | None = None

    # ── Internal ──────────────────────────────────────────────────────────────

    def _get_collection(self) -> chromadb.Collection:
        if self._client is None:
            self._client = chromadb.PersistentClient(
                path=settings.chroma_path,
                settings=ChromaSettings(anonymized_telemetry=False),
            )
        if self._collection is None:
            self._collection = self._client.get_or_create_collection(
                name=COLLECTION_NAME,
                metadata={"hnsw:space": "cosine"},
            )
        return self._collection

    # ── Write ─────────────────────────────────────────────────────────────────

    async def add_document(
        self,
        doc_id: str,
        chunks: list[str],
        metadata: list[dict],
    ) -> None:
        """
        Embed each chunk sequentially then upsert the batch into Chroma.

        Sequential embedding keeps Ollama load steady and avoids overwhelming
        the local inference server.  On embed failure, a zero vector is stored
        (the chunk is retained for keyword display but won't match semantic queries).

        Args:
            doc_id:   Stable identifier for this document (from _doc_id_for_path).
            chunks:   Raw text for each chunk.
            metadata: One dict per chunk; doc_id is injected automatically.
        """
        if not chunks:
            return

        from app.llm_client import embed

        # Embed with limited concurrency (3 parallel) to speed up batch
        # indexing without overwhelming Ollama on the Pi/R710.
        _EMBED_CONCURRENCY = 3
        sem = asyncio.Semaphore(_EMBED_CONCURRENCY)

        async def _embed_one(i: int, text: str) -> tuple[int, list[float]]:
            async with sem:
                try:
                    return (i, await embed(text))
                except Exception as exc:
                    log.warning(
                        "Embed failed for chunk %d of doc %s: %s — storing zero vector",
                        i, doc_id, exc,
                    )
                    return (i, [1e-10] * _EMBED_DIM)  # near-zero; avoids cosine NaN

        results = await asyncio.gather(
            *(_embed_one(i, chunk) for i, chunk in enumerate(chunks))
        )
        results.sort(key=lambda r: r[0])
        vectors: list[list[float]] = [v for _, v in results]

        # Flag chunks that failed embedding so search can deprioritise them
        embed_failed = {i for i, v in results if all(abs(x) < 1e-9 for x in v[:3])}

        col = self._get_collection()
        ids = [f"{doc_id}::chunk::{i:06d}" for i in range(len(chunks))]
        full_meta = [
            _sanitise_metadata({**m, "doc_id": doc_id, **({"embed_failed": True} if i in embed_failed else {})})
            for i, m in enumerate(metadata)
        ]

        col.upsert(
            ids=ids,
            embeddings=vectors,
            documents=chunks,
            metadatas=full_meta,
        )
        log.debug("Upserted %d chunks for doc_id=%s", len(chunks), doc_id)

    async def delete_document(self, doc_id: str) -> int:
        """
        Delete all chunks belonging to doc_id.

        Returns the number of chunks removed (0 if the document was not found).
        Errors are logged and swallowed so the caller can proceed with indexing.
        """
        col = self._get_collection()
        try:
            existing = col.get(where={"doc_id": doc_id}, include=[])
            chunk_ids: list[str] = existing["ids"]
            if chunk_ids:
                col.delete(ids=chunk_ids)
                log.info("Deleted %d chunks for doc_id=%s", len(chunk_ids), doc_id)
            return len(chunk_ids)
        except Exception as exc:
            log.warning("delete_document(%s) failed: %s", doc_id, exc)
            return 0

    # ── Read ──────────────────────────────────────────────────────────────────

    async def search(
        self,
        query: str,
        n_results: int = 8,
        doc_type: str | None = None,
        where: dict | None = None,
    ) -> list[dict]:
        """
        Semantic search over all indexed chunks.

        Embeds the query then performs a cosine-similarity lookup in Chroma.
        Supports metadata filtering via doc_type shorthand or raw where dict.

        Returns:
            List of dicts (sorted by ascending distance / most similar first):
              text, source_file, page_number, doc_type, chunk_index, distance,
              plus all metadata fields from ChromaDB
            Empty list if the collection is empty or embedding fails.
        """
        from app.llm_client import embed

        col = self._get_collection()
        count = col.count()
        if count == 0:
            return []

        try:
            vector = await embed(query)
        except Exception as exc:
            log.error("Embed failed for search query: %s", exc)
            return []

        actual_n = min(n_results, count)
        query_kwargs: dict[str, Any] = {
            "query_embeddings": [vector],
            "n_results": actual_n,
            "include": ["documents", "metadatas", "distances"],
        }
        if where:
            query_kwargs["where"] = where
        elif doc_type:
            query_kwargs["where"] = {"document_type": doc_type}

        try:
            results = col.query(**query_kwargs)
        except Exception as exc:
            log.error("Chroma query failed: %s", exc)
            return []

        hits: list[dict] = []
        for doc, meta, dist in zip(
            results["documents"][0],
            results["metadatas"][0],
            results["distances"][0],
        ):
            # Skip chunks whose embedding failed (zero-vector fallbacks)
            if meta.get("embed_failed"):
                continue
            hit = {
                "text": doc,
                "source_file": meta.get("source_file", ""),
                "page_number": int(meta.get("page_number", 0)),
                "doc_type": meta.get("document_type", "other"),
                "chunk_index": int(meta.get("chunk_index", 0)),
                "distance": round(float(dist), 4),
            }
            # Include all metadata for parent-child retrieval
            hit.update(meta)
            hits.append(hit)
        return hits

    def list_documents(self) -> list[dict]:
        """
        Return one summary dict per unique doc_id.

        Keys: doc_id, source_file, document_type, indexed_at, chunk_count.
        Performs a full metadata scan — acceptable for a community org's library.
        """
        col = self._get_collection()
        try:
            results = col.get(include=["metadatas"])
        except Exception as exc:
            log.warning("list_documents failed: %s", exc)
            return []

        docs: dict[str, dict] = {}
        for meta in results.get("metadatas") or []:
            doc_id = meta.get("doc_id", "")
            if not doc_id:
                continue
            if doc_id not in docs:
                docs[doc_id] = {
                    "doc_id": doc_id,
                    "source_file": meta.get("source_file", ""),
                    "document_type": meta.get("document_type", "other"),
                    "indexed_at": meta.get("indexed_at", ""),
                    "chunk_count": 0,
                }
            docs[doc_id]["chunk_count"] += 1

        return list(docs.values())

    def get_all_document_metadata(self) -> list[dict]:
        """
        Return raw metadata for every chunk — used by DreamWorkerSkill for
        prefetch cache generation.
        """
        col = self._get_collection()
        try:
            results = col.get(include=["metadatas"])
            return results.get("metadatas") or []
        except Exception as exc:
            log.warning("get_all_document_metadata failed: %s", exc)
            return []

    def health_check(self) -> dict:
        """Check ChromaDB integrity. Returns {"ok": bool, "detail": str, "count": int}."""
        try:
            col = self._get_collection()
            count = col.count()
            # Quick sanity: try to peek at one record
            if count > 0:
                peek = col.peek(limit=1)
                if not peek.get("ids"):
                    return {"ok": False, "detail": "Collection non-empty but peek returned no ids", "count": count}
            return {"ok": True, "detail": "healthy", "count": count}
        except Exception as exc:
            return {"ok": False, "detail": str(exc), "count": -1}

    async def rebuild(self) -> dict:
        """Drop and re-create the collection, then re-index all documents.

        Returns {"rebuilt": bool, "docs_indexed": int, "errors": list[str]}.
        """
        errors: list[str] = []
        try:
            if self._client is None:
                self._get_collection()
            self._client.delete_collection(COLLECTION_NAME)
            self._collection = None
            log.warning("ChromaDB collection '%s' dropped for rebuild", COLLECTION_NAME)
        except Exception as exc:
            errors.append(f"Drop failed: {exc}")
            log.error("Failed to drop collection for rebuild: %s", exc)

        # Re-create collection
        self._collection = None
        self._get_collection()

        # Trigger a full re-index via the indexer skill
        try:
            from app.skills.indexer import IndexerSkill
            indexer = IndexerSkill()
            result = await indexer.run(force=True)
            doc_count = result.metadata.get("indexed", 0) if result.metadata else 0
            if not result.success:
                errors.append(f"Indexer: {result.error}")
            return {"rebuilt": True, "docs_indexed": doc_count, "errors": errors}
        except Exception as exc:
            errors.append(f"Re-index failed: {exc}")
            log.error("Rebuild re-index failed: %s", exc)
            return {"rebuilt": True, "docs_indexed": 0, "errors": errors}


# ── Module-level singleton ────────────────────────────────────────────────────

vector_store = CDCNVectorStore()


# ── Backward-compat shim ──────────────────────────────────────────────────────

def get_collection() -> chromadb.Collection:
    """Kept for code written before CDCNVectorStore was introduced."""
    return vector_store._get_collection()
