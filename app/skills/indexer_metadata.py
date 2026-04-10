"""
indexer_metadata — metadata extraction helpers for the document indexer.

Provides document-type inference, stable ID generation, date extraction,
title/attendee/topic extraction, and page counting.
"""
import hashlib
import re
from pathlib import Path

from app.utils.dates import extract_date_from_filename, _MONTH_MAP as _SHARED_MONTH_MAP
from app.utils.extraction import extract_attendees as _shared_extract_attendees

# ── Document type inference ───────────────────────────────────────────────────

_DOC_TYPE_PATTERNS: list[tuple[str, str]] = [
    # More-specific patterns first
    (r"agenda", "agenda"),
    (r"minute|agm|meeting.note|meeting.corrected|meeting.update", "minutes"),
    (r"policy|procedure|bylaw|standing.?order", "policy"),
    (r"funding|grant|application|proposal|bid", "funding"),
    (r"constitution|article|memorandum", "constitution"),
    (r"report|review|survey|evaluation|audit|trustee", "report"),
    (r"plan|strategy|programme", "plan"),
    (r"journal|diary", "journal"),
    (r"draft", "draft"),
]


def _infer_doc_type(filename: str) -> str:
    stem = filename.lower()
    for pattern, doc_type in _DOC_TYPE_PATTERNS:
        if re.search(pattern, stem):
            return doc_type
    return "other"


def _doc_id_for_path(path: Path) -> str:
    """Stable 20-char hex ID derived from the file's resolved absolute path."""
    return hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:20]


# ── Date extraction (delegated to app.utils.dates) ────────────────────────────

_MONTH_MAP = _SHARED_MONTH_MAP  # backward-compat alias


def _extract_date_from_filename(filename: str) -> str | None:
    """Backward-compat wrapper — delegates to app.utils.dates."""
    return extract_date_from_filename(filename)


# ── Content-based metadata extraction ─────────────────────────────────────────


def _extract_title(elements: list, filename: str) -> str:
    """
    Extract a document title from the first meaningful line of content,
    or fall back to a cleaned version of the filename stem.
    """
    for el in elements[:10]:
        for line in el.text.split("\n"):
            line = line.strip()
            if 5 < len(line) < 200 and not line.startswith("http"):
                return line
    stem = re.sub(r"[_\-]+", " ", Path(filename).stem).strip()
    return stem[:200]


def _extract_attendees(elements: list) -> list[str]:
    """Backward-compat wrapper — delegates to app.utils.extraction."""
    return _shared_extract_attendees(elements)


def _extract_topics(elements: list) -> list[str]:
    """
    Extract key topic phrases by looking for short, capitalised lines that
    resemble headings or agenda items (not sentences, not plain numbers).
    """
    topics: list[str] = []
    seen: set[str] = set()
    for el in elements[:60]:
        for line in el.text.split("\n"):
            line = line.strip()
            if (
                5 < len(line) < 80
                and not line.endswith(".")
                and re.match(r"^[A-Z]", line)
                and not re.match(r"^\d+[\.\)]\s", line)  # skip "1. item" lines
                and line.lower() not in seen
            ):
                seen.add(line.lower())
                topics.append(line)
                if len(topics) >= 10:
                    return topics
    return topics


def _count_pages(elements: list) -> int:
    """Return the highest page number seen across all elements (>=1)."""
    if not elements:
        return 0
    return max((el.page for el in elements), default=1)


# ── Metadata backfill ─────────────────────────────────────────────────────────


async def backfill_metadata() -> dict:
    """
    Backfill document_metadata for every file already tracked as 'ok'
    in indexed_files but not yet present in document_metadata.

    Safe to call repeatedly -- uses INSERT OR REPLACE.
    Returns a dict with keys: populated, skipped, failed.
    """
    import logging
    from datetime import datetime, timezone

    import aiosqlite

    from app.skills.indexer_db import _open_db, _upsert_metadata
    from app.skills.indexer_parsers import _parse_file

    log = logging.getLogger(__name__)

    populated = 0
    skipped = 0
    failed = 0

    async with _open_db() as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            "SELECT path, doc_id, document_type, indexed_at "
            "FROM indexed_files WHERE status = 'ok'"
        ) as cur:
            rows = await cur.fetchall()

        for row in rows:
            file_path = Path(row["path"])
            if not file_path.exists():
                skipped += 1
                continue

            # Skip if metadata already present (unless we want to force)
            async with conn.execute(
                "SELECT id FROM document_metadata WHERE filename = ?",
                (file_path.name,),
            ) as chk:
                exists = await chk.fetchone()
            if exists:
                skipped += 1
                continue

            try:
                elements = _parse_file(file_path)
                full_text = "\n".join(el.text for el in elements)
                doc_type = _infer_doc_type(file_path.name)
                await _upsert_metadata(
                    conn,
                    filename=file_path.name,
                    filepath=row["path"],
                    doc_type=doc_type,
                    doc_date=_extract_date_from_filename(file_path.name),
                    title=_extract_title(elements, file_path.name),
                    attendees=_extract_attendees(elements) if doc_type in ("minutes", "agenda") else [],
                    topics=_extract_topics(elements),
                    file_size=file_path.stat().st_size,
                    page_count=_count_pages(elements),
                    indexed_at=row["indexed_at"] or datetime.now(timezone.utc).isoformat(),
                    full_text_chars=len(full_text),
                    doc_id=row["doc_id"],
                )
                populated += 1
                log.info("Backfilled metadata for %s", file_path.name)
            except Exception as exc:
                log.warning("Metadata backfill failed for %s: %s", file_path.name, exc)
                failed += 1

    return {"populated": populated, "skipped": skipped, "failed": failed}
