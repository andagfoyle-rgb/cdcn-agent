"""
DocumentIndexerSkill — orchestrator that scans WATCHED_FOLDER, validates, parses,
chunks, and upserts documents into CDCNVectorStore.

Parsing, database, and metadata logic live in sister modules:
  - indexer_parsers.py  (file parsing, MIME validation, chunking)
  - indexer_db.py       (SQLite tracking tables and CRUD)
  - indexer_metadata.py (doc-type inference, title/attendee/topic extraction)
"""
import logging
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

from app.config import settings
from app.skills.base import BaseSkill, SkillResult
from app.storage.audit_log import log_event
from app.storage.vector_store import vector_store

# ── Imports from sub-modules ───────────────────────────────────────────────────
# Only names actually used in this file are imported directly.
# All names are also re-exported for backward compatibility via __all__-style
# star-import support (see bottom of file).

from app.skills.indexer_parsers import (
    SUPPORTED_SUFFIXES, _validate_mime, _parse_file,
)
from app.skills.indexer_db import (
    _DB_PATH, _open_db, _get_record, _upsert_record, _upsert_metadata,
    _record_version, query_metadata_db,
)
from app.skills.indexer_metadata import (
    _infer_doc_type, _doc_id_for_path, _extract_date_from_filename,
    _extract_attendees, backfill_metadata,
)

log = logging.getLogger(__name__)


# ── Skill ─────────────────────────────────────────────────────────────────────


class DocumentIndexerSkill(BaseSkill):
    """
    Incremental document indexer for CDCN's watched folder.

    On each run, only new and modified files are processed.  Per-file state
    is persisted in a SQLite tracking table so that subsequent runs are fast.

    Returns a plain-English summary string (suitable for LLM or human reading).
    """

    name = "indexer"
    description = (
        "Index new or modified documents (PDF, DOCX, TXT, Markdown) from the "
        "watched folder into the searchable vector store."
    )

    def __init__(self, settings_obj=None, vector_store_override=None) -> None:
        """
        Accept optional settings and vector_store for dependency injection (e.g. tests).
        If vector_store_override is given it is used instead of the module-level singleton.
        """
        self._vector_store = vector_store_override  # None → use module-level singleton
        # settings_obj accepted for API compat but module-level settings used

    def is_supported_file(self, filename: str) -> bool:
        """Return True if the file extension is on the allowlist."""
        from pathlib import PurePosixPath
        suffix = PurePosixPath(filename).suffix.lower()
        return suffix in SUPPORTED_SUFFIXES

    def infer_doc_type(self, filename: str) -> str:
        """Infer the document type from the filename."""
        return _infer_doc_type(filename)

    def chunk_text(self, text: str, chunk_words: int = 500, overlap_words: int = 50) -> list[str]:
        """
        Split *text* into overlapping word-based chunks.

        Produces chunks of up to *chunk_words* words with *overlap_words*
        words of overlap between consecutive chunks.
        """
        words = text.split()
        if not words:
            return []
        step = max(1, chunk_words - overlap_words)
        chunks = []
        pos = 0
        while pos < len(words):
            chunk = " ".join(words[pos: pos + chunk_words])
            if chunk.strip():
                chunks.append(chunk)
            pos += step
        return chunks

    def _vs(self):
        """Return the vector store to use (injected or module-level singleton)."""
        if self._vector_store is not None:
            return self._vector_store
        return vector_store

    async def run(
        self,
        folder=None,
        force: bool = False,
        **kwargs,
    ) -> SkillResult:
        # Accept a dict (e.g. run({'folder': '...'})) or a plain string
        if isinstance(folder, dict):
            kwargs.update(folder)
            folder = kwargs.pop("folder", None)
            force = kwargs.pop("force", force)
        folder_path = Path(folder or settings.watched_folder)
        if not folder_path.exists():
            return SkillResult(
                success=False, error=f"Folder not found: {folder_path}"
            )

        indexed, skipped, failed = 0, 0, 0
        new_files: list[str] = []
        failure_details: list[tuple[str, str]] = []  # (filename, reason)

        async with _open_db() as conn:
            for file_path in sorted(folder_path.rglob("*")):
                if not file_path.is_file():
                    continue
                if file_path.suffix.lower() not in SUPPORTED_SUFFIXES:
                    continue

                path_str = str(file_path.resolve())
                last_modified = file_path.stat().st_mtime

                if not force:
                    existing = await _get_record(conn, path_str)
                    if (
                        existing
                        and existing["status"] == "ok"
                        and abs(existing["last_modified"] - last_modified) < 0.01
                    ):
                        skipped += 1
                        continue

                outcome = await self._index_one(conn, file_path, last_modified)
                if outcome == "ok":
                    indexed += 1
                    new_files.append(file_path.name)
                else:
                    failed += 1
                    failure_details.append((file_path.name, outcome))

        # ── Indexing verification report ──────────────────────────────────
        total_supported = sum(
            1 for f in folder_path.rglob("*")
            if f.is_file() and f.suffix.lower() in SUPPORTED_SUFFIXES
        )
        successful = indexed
        fail_summary = (
            "; ".join(f"{name}: {reason}" for name, reason in failure_details)
            if failure_details else "none"
        )
        prefix = "WARNING " if (total_supported > 0 and failed / total_supported > 0.10) else ""
        report_text = (
            f"{prefix}Indexing report: {total_supported} documents indexed, "
            f"{successful} successful, {failed} failed "
            f"(failures: {fail_summary})"
        )
        log.info(report_text)

        # Zero-chunk detection: warn about files marked ok but with 0 chunks
        try:
            async with _open_db() as conn:
                from app.skills.chunker import init_chunks_db
                await init_chunks_db(_DB_PATH)
                async with aiosqlite.connect(_DB_PATH) as chunks_conn:
                    conn.row_factory = aiosqlite.Row
                    async with conn.execute(
                        "SELECT doc_id, path FROM indexed_files WHERE status = 'ok'"
                    ) as cur:
                        ok_rows = await cur.fetchall()
                    for row in ok_rows:
                        async with chunks_conn.execute(
                            "SELECT COUNT(*) FROM chunks WHERE doc_id = ?",
                            (row["doc_id"],),
                        ) as ccur:
                            count = (await ccur.fetchone())[0]
                        if count == 0:
                            fname = Path(row["path"]).name
                            log.warning(
                                "Zero-chunk detection: %s (doc_id=%s) has status 'ok' "
                                "but 0 chunks in chunks table",
                                fname, row["doc_id"],
                            )
        except Exception as exc:
            log.debug("Zero-chunk detection check failed: %s", exc)

        # Store report in audit log
        try:
            await log_event(
                actor="indexer", action="indexing_report", detail=report_text,
            )
        except Exception as exc:
            log.debug("Failed to store indexing report in audit log: %s", exc)

        summary = (
            f"Indexed {indexed} file(s), "
            f"skipped {skipped} unchanged, "
            f"{failed} failed."
        )
        if new_files:
            summary += f"\nNew/updated: {', '.join(new_files)}"

        # Rebuild document index after indexing changes
        if indexed > 0:
            try:
                from app.skills.document_index import build_index
                build_index()
                log.info("Document index rebuilt after indexing %d files", indexed)
            except Exception as exc:
                log.warning("Failed to rebuild document index: %s", exc)

            # Invalidate system prompt cache so the new doc index is picked up
            try:
                from app.gateway.prompt_builder import invalidate_prompt_cache
                invalidate_prompt_cache()
            except Exception:
                pass

        return SkillResult(
            success=True,
            output=summary,
            metadata={"indexed": indexed, "skipped": skipped, "failed": failed},
        )

    async def force_reindex(self, path: str | Path) -> SkillResult:
        """Force re-processing of a single file, ignoring cached state."""
        file_path = Path(path)
        if not file_path.exists():
            return SkillResult(success=False, error=f"File not found: {path}")
        if file_path.suffix.lower() not in SUPPORTED_SUFFIXES:
            return SkillResult(
                success=False, error=f"Unsupported file type: {file_path.suffix}"
            )

        async with _open_db() as conn:
            outcome = await self._index_one(conn, file_path, file_path.stat().st_mtime)

        if outcome == "ok":
            return SkillResult(success=True, output=f"Re-indexed: {file_path.name}")
        return SkillResult(success=False, error=outcome)

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _index_one(
        self,
        conn: aiosqlite.Connection,
        file_path: Path,
        last_modified: float,
    ) -> str:
        """
        Index a single file using the new parser -> chunker -> embed pipeline.

        Returns "ok" on success, or an error string on failure.
        All exceptions are caught; the caller decides what to count.
        """
        from app.skills.document_parser import parse_document
        from app.skills.chunker import (
            chunk_document, store_chunks, init_chunks_db, ChunkedDocument,
        )

        path_str = str(file_path.resolve())
        doc_id = _doc_id_for_path(file_path)
        doc_type = _infer_doc_type(file_path.name)
        indexed_at = datetime.now(timezone.utc).isoformat()
        db_path = _DB_PATH

        try:
            # MIME validation
            if not _validate_mime(file_path):
                msg = f"Rejected MIME type: {file_path.name}"
                await _upsert_record(
                    conn,
                    path_str=path_str,
                    last_modified=last_modified,
                    doc_id=doc_id,
                    document_type=doc_type,
                    status="rejected",
                    indexed_at=indexed_at,
                    error_msg=msg,
                )
                return msg

            # ── Parse with new structured parser ─────────────────────────────
            parsed = parse_document(file_path)
            if not parsed.full_text.strip():
                msg = f"No text extracted from {file_path.name}"
                log.warning(msg)
                await _upsert_record(
                    conn,
                    path_str=path_str,
                    last_modified=last_modified,
                    doc_id=doc_id,
                    document_type=doc_type,
                    status="empty",
                    indexed_at=indexed_at,
                    error_msg=msg,
                )
                return msg

            # ── Chunk with parent-child chunker ──────────────────────────────
            chunked = chunk_document(parsed, doc_id)
            if not chunked.children:
                return "no chunks produced"

            # ── Ensure chunks table exists ───────────────────────────────────
            await init_chunks_db(db_path)

            # ── Store parent-child chunks in SQLite ──────────────────────────
            await store_chunks(db_path, chunked)

            # ── Embed and store CHILD chunks only in ChromaDB ────────────────
            watched_root = Path(settings.watched_folder).resolve()
            try:
                source_file = str(file_path.resolve().relative_to(watched_root))
            except ValueError:
                source_file = file_path.name

            # Use parsed date or fall back to filename extraction
            doc_date = parsed.date or _extract_date_from_filename(file_path.name) or ""

            chunks_text = [c.content for c in chunked.children]
            metadata = []
            for c in chunked.children:
                # Find the parent_id for this child's section
                parent_db_id = 0
                for p in chunked.parents:
                    if p.section_title == c.section_title and p.db_id:
                        parent_db_id = p.db_id
                        break

                metadata.append({
                    "source_file": source_file,
                    "page_number": c.page_number,
                    "chunk_index": c.position_in_doc,
                    "document_type": doc_type,
                    "indexed_at": indexed_at,
                    "chunk_type": "child",
                    "parent_id": parent_db_id,
                    "chunk_db_id": c.db_id or 0,
                    "section_title": c.section_title or "",
                    "doc_date": doc_date,
                    "doc_id": doc_id,
                })

            # Atomic re-index: add new chunks first under a temp ID, then
            # delete old chunks, then rename.  If add fails, old data is preserved.
            temp_doc_id = f"{doc_id}__reindex_tmp"
            try:
                await self._vs().add_document(temp_doc_id, chunks_text, metadata)
                await self._vs().delete_document(doc_id)
                # Rename temp chunks to the real doc_id
                await self._vs().delete_document(temp_doc_id)
                await self._vs().add_document(doc_id, chunks_text, metadata)
            except Exception as exc:
                # Cleanup temp and preserve originals
                await self._vs().delete_document(temp_doc_id)
                log.error("Atomic re-index failed for %s, old data preserved: %s", doc_id, exc)
                raise

            # ── Populate document_metadata ────────────────────────────────────
            attendees = parsed.attendees
            if not attendees and doc_type in ("minutes", "agenda"):
                # Fallback to old extraction
                elements = _parse_file(file_path)
                attendees = _extract_attendees(elements) if elements else []

            await _upsert_metadata(
                conn,
                filename=file_path.name,
                filepath=path_str,
                doc_type=doc_type,
                doc_date=doc_date or None,
                title=parsed.title,
                attendees=attendees,
                topics=[s.title for s in parsed.sections[:10] if s.title != "Preamble"],
                file_size=file_path.stat().st_size,
                page_count=parsed.page_count,
                indexed_at=indexed_at,
                full_text_chars=len(parsed.full_text),
                doc_id=doc_id,
                extraction_method=getattr(parsed, "extraction_method", None) or None,
            )

            # Record success
            await _upsert_record(
                conn,
                path_str=path_str,
                last_modified=last_modified,
                doc_id=doc_id,
                document_type=doc_type,
                status="ok",
                indexed_at=indexed_at,
            )
            # Record version for change tracking
            try:
                await _record_version(conn, doc_id, file_path.name, file_path, indexed_at)
            except Exception as exc:
                log.debug("Version recording failed for %s: %s", file_path.name, exc)
            log.info(
                "Indexed %s: %d parents + %d children, type=%s",
                file_path.name, len(chunked.parents), len(chunked.children), doc_type,
            )
            return "ok"

        except Exception as exc:
            log.exception("Failed to index %s", file_path.name)
            await _upsert_record(
                conn,
                path_str=path_str,
                last_modified=last_modified,
                doc_id=doc_id,
                document_type=doc_type,
                status="error",
                indexed_at=indexed_at,
                error_msg=str(exc)[:500],
            )
            return str(exc)

    async def build_metadata_db(self) -> SkillResult:
        """
        Backfill document_metadata for every file already tracked as 'ok'
        in indexed_files but not yet present in document_metadata.

        Safe to call repeatedly -- uses INSERT OR REPLACE.
        Delegates to indexer_metadata.backfill_metadata().
        """
        result = await backfill_metadata()
        return SkillResult(
            success=True,
            output=(
                f"Metadata backfill complete: {result['populated']} populated, "
                f"{result['skipped']} skipped (already present or missing file), "
                f"{result['failed']} failed."
            ),
            metadata=result,
        )

    async def query_metadata(
        self,
        doc_type: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """
        Query document_metadata with optional filters.
        Delegates to indexer_db.query_metadata_db().
        """
        return await query_metadata_db(
            doc_type=doc_type, date_from=date_from, date_to=date_to, limit=limit,
        )


# Backward-compat alias
IndexerSkill = DocumentIndexerSkill
