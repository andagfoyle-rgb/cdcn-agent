"""
DocumentIndex — generates and caches a table-of-contents index of all
documents in the CDCN archive.  Injected into every system prompt so the
LLM always knows what documents exist and what they contain.

The index is built from the SQLite document_metadata and chunks tables —
no LLM calls are made.  It is rebuilt automatically after indexing runs
and cached in memory for fast access.
"""
import json
import logging
import sqlite3
import time
from pathlib import Path

from app.config import settings

log = logging.getLogger(__name__)

_DB_PATH = str(Path(settings.chroma_path).parent / "indexer.db")
_INDEX_CACHE_PATH = Path(settings.memory_path) / "document_index_cache.txt"

# In-memory cache
_cached_index: str = ""
_cached_at: float = 0.0
_CACHE_TTL = 300.0  # 5 minutes


def build_index() -> str:
    """
    Generate the document archive table of contents from the database.
    Groups documents by type and includes topic summaries for each.
    Returns the formatted index string.
    """
    global _cached_index, _cached_at

    try:
        with sqlite3.connect(_DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            docs = conn.execute(
                "SELECT filename, filepath, doc_type, doc_date, title, topics, doc_id "
                "FROM document_metadata ORDER BY doc_type, doc_date"
            ).fetchall()
    except Exception as exc:
        log.warning("build_index: failed to query document_metadata: %s", exc)
        return ""

    if not docs:
        return ""

    # Group by doc_type
    groups: dict[str, list[dict]] = {}
    for row in docs:
        d = dict(row)
        doc_type = d.get("doc_type", "other")
        groups.setdefault(doc_type, []).append(d)

    # For each document, build a one-line summary using topics and section titles
    lines = [f"DOCUMENT ARCHIVE INDEX ({len(docs)} documents):\n"]

    # Type display names and sort order
    type_labels = {
        "minutes": "MINUTES",
        "agenda": "AGENDAS",
        "policy": "POLICIES",
        "funding": "FUNDING",
        "report": "REPORTS",
        "plan": "PLANS",
        "other": "OTHER",
    }
    type_order = ["minutes", "policy", "funding", "report", "plan", "agenda", "other"]

    for doc_type in type_order:
        if doc_type not in groups:
            continue
        doc_list = groups[doc_type]
        label = type_labels.get(doc_type, doc_type.upper())
        lines.append(f"{label} ({len(doc_list)} documents):")

        for d in doc_list:
            filename = d.get("filename", "unknown")
            doc_date = d.get("doc_date", "")
            date_str = f" ({doc_date})" if doc_date else ""

            # Build topic summary from stored topics
            topic_summary = _get_topic_summary(d, conn if 'conn' in dir() else None)
            topic_part = f": {topic_summary}" if topic_summary else ""

            lines.append(f"- {filename}{date_str}{topic_part}")

        lines.append("")

    # Handle any types not in our predefined order
    for doc_type in sorted(groups.keys()):
        if doc_type in type_order:
            continue
        doc_list = groups[doc_type]
        lines.append(f"{doc_type.upper()} ({len(doc_list)} documents):")
        for d in doc_list:
            filename = d.get("filename", "unknown")
            doc_date = d.get("doc_date", "")
            date_str = f" ({doc_date})" if doc_date else ""
            topic_summary = _get_topic_summary(d, None)
            topic_part = f": {topic_summary}" if topic_summary else ""
            lines.append(f"- {filename}{date_str}{topic_part}")
        lines.append("")

    index_text = "\n".join(lines)

    # Cache in memory
    _cached_index = index_text
    _cached_at = time.monotonic()

    # Persist to file
    try:
        _INDEX_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _INDEX_CACHE_PATH.write_text(index_text)
    except Exception as exc:
        log.warning("build_index: failed to persist cache file: %s", exc)

    log.info("Document index built: %d documents, %d chars", len(docs), len(index_text))
    return index_text


def _get_topic_summary(doc: dict, conn: sqlite3.Connection | None = None) -> str:
    """
    Build a short topic summary for a document.
    Uses topics from document_metadata first, then falls back to
    section titles from the chunks table.
    Truncated to ~120 chars max to keep the index compact.
    """
    summary = ""

    # Try topics from metadata
    topics_raw = doc.get("topics")
    if topics_raw:
        try:
            topics = json.loads(topics_raw) if isinstance(topics_raw, str) else topics_raw
            if topics and isinstance(topics, list):
                # Filter out very short/generic topics and long ones
                good = [t[:50] for t in topics if isinstance(t, str) and 3 < len(t) < 80]
                if good:
                    summary = ", ".join(good[:6])
        except (json.JSONDecodeError, TypeError):
            pass

    # Fall back to section titles from chunks table
    if not summary:
        doc_id = doc.get("doc_id", "")
        if doc_id and conn is not None:
            try:
                rows = conn.execute(
                    "SELECT DISTINCT section_title FROM chunks "
                    "WHERE doc_id = ? AND chunk_type = 'parent' "
                    "AND section_title IS NOT NULL AND length(section_title) > 3 "
                    "AND section_title != 'Preamble' "
                    "LIMIT 6",
                    (doc_id,),
                ).fetchall()
                titles = [r[0][:50] for r in rows if r[0]]
                if titles:
                    summary = ", ".join(titles)
            except Exception:
                pass

    # Truncate to keep index compact
    if len(summary) > 120:
        summary = summary[:117] + "..."
    return summary


def get_index() -> str:
    """
    Return the cached document index.  Rebuilds if stale or empty.
    """
    global _cached_index, _cached_at

    now = time.monotonic()
    if _cached_index and (now - _cached_at) < _CACHE_TTL:
        return _cached_index

    # Try loading from file cache first
    if not _cached_index and _INDEX_CACHE_PATH.exists():
        try:
            _cached_index = _INDEX_CACHE_PATH.read_text()
            _cached_at = now
            if _cached_index:
                return _cached_index
        except Exception:
            pass

    # Rebuild from database
    return build_index()


def inject_index(system_prompt: str) -> str:
    """
    Append the document archive index to the system prompt.
    """
    index = get_index()
    if not index:
        return system_prompt

    return (
        system_prompt
        + "\n\n## CDCN Document Archive Index\n\n"
        "When answering questions, consult this index to identify which documents "
        "are likely to contain the answer. You can request specific documents by "
        "name using the search tool.\n\n"
        + index
    )


def invalidate_cache():
    """Clear the in-memory cache so the next get_index() rebuilds."""
    global _cached_index, _cached_at
    _cached_index = ""
    _cached_at = 0.0
