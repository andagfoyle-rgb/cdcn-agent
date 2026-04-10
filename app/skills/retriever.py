"""
MetadataRetriever — hybrid document retrieval for CDCN Agent.

Retrieval pipeline (in order of specificity):
  1. "Last N" queries  → metadata DB sorted by date, load top N in full
  2. Date-range queries → metadata DB filtered by date/type, load in full
  3. All other queries  → vector search to find the specific relevant documents,
                          then load those in full (not all docs of a type)
     Fallback: if vector search finds nothing relevant and a doc_type was
               detected, load the 3 most recent docs of that type.

The key improvement over pure metadata retrieval: vector search is used as the
primary finder so a query like "what was decided about the solar panels?" picks
the 2-3 specific meeting minutes that mention solar — not all 29 meetings.
"""
import logging
import re
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────────

_DB_PATH = "/var/lib/cdcn-agent/indexer.db"
_DOCS_DIR = Path("/var/lib/cdcn-agent/documents")
_MAX_CHARS = 160_000          # ~40K tokens — accuracy-first; GLM-5 has 200K context
from app.config import settings as _cfg
_VECTOR_DISTANCE_THRESHOLD = _cfg.vector_distance_threshold
_MAX_FILES_FROM_VECTOR = 8    # load more candidates; accuracy over speed

# ── Type detection ────────────────────────────────────────────────────────────

_TYPE_KEYWORDS: list[tuple[list[str], str]] = [
    (["minute", "minutes", "meeting notes", "meeting minutes", "board meeting",
      "what happened at", "what was discussed", "who attended", "who was at",
      "action point", "action items", "decisions", "apologies"], "minutes"),
    (["agenda"], "agenda"),
    (["policy", "policies", "procedure", "regulation", "rules", "ethics",
      "ai policy", "artificial intelligence"], "policy"),
    (["funding", "grant", "application", "bid", "hie", "highlands and islands",
      "budget", "cost estimate", "financial ask"], "funding"),
    (["trustee", "trustees", "annual report", "trustee report",
      "charity report", "oscr"], "report"),
    (["action plan", "strategy", "programme", "community plan",
      "business plan", "development plan"], "plan"),
    (["draft"], "draft"),
]


def _detect_doc_type(query: str) -> str | None:
    """Return the most likely doc_type for this query, or None."""
    q = query.lower()
    for keywords, doc_type in _TYPE_KEYWORDS:
        if any(kw in q for kw in keywords):
            return doc_type
    return None


# ── Date range detection ──────────────────────────────────────────────────────

_MONTH_MAP = {
    "january": "01", "february": "02", "march": "03", "april": "04",
    "may": "05", "june": "06", "july": "07", "august": "08",
    "september": "09", "october": "10", "november": "11", "december": "12",
    "jan": "01", "feb": "02", "mar": "03", "apr": "04",
    "jun": "06", "jul": "07", "aug": "08",
    "sep": "09", "oct": "10", "nov": "11", "dec": "12",
}

# Matches DD/MM/YYYY or DD-MM-YYYY (UK format)
_DMY_RE = re.compile(r"\b(\d{1,2})[/\-](\d{1,2})[/\-](20\d{2})\b")

# Matches MM/DD/YYYY ambiguously — we treat all slash dates as DD/MM/YYYY (UK)
def _dmy_to_iso(day: str, month: str, year: str) -> str:
    return f"{year}-{int(month):02d}-{int(day):02d}"


def _detect_date_range(query: str) -> tuple[str | None, str | None]:
    """
    Return (date_from, date_to) ISO strings inferred from the query.
    Returns (None, None) if no date signals are present.

    Handles:
      - DD/MM/YYYY or DD-MM-YYYY pairs (e.g. "between 01/03/2025 and 03/03/2026")
      - "this year" / "last year"
      - Bare 4-digit year
      - "Month YYYY" (e.g. "March 2025")
    """
    q = query.lower()
    today = datetime.now()

    # ── DD/MM/YYYY pairs (highest priority — most specific) ───────────────────
    dmy_hits = _DMY_RE.findall(q)
    if len(dmy_hits) >= 2:
        iso_dates = sorted(_dmy_to_iso(*h) for h in dmy_hits)
        return iso_dates[0], iso_dates[-1]
    if len(dmy_hits) == 1:
        iso = _dmy_to_iso(*dmy_hits[0])
        return iso, iso

    if "this year" in q:
        return f"{today.year}-01-01", f"{today.year}-12-31"

    if "last year" in q:
        y = today.year - 1
        return f"{y}-01-01", f"{y}-12-31"

    # "last N months" — relative date range
    _last_months = re.search(r"last\s+(\d+)\s+months?", q)
    if _last_months:
        from dateutil.relativedelta import relativedelta
        n = int(_last_months.group(1))
        date_from = (today - relativedelta(months=n)).strftime("%Y-%m-%d")
        return date_from, today.strftime("%Y-%m-%d")

    # "current financial year" — UK financial year April to March
    if "financial year" in q or "fiscal year" in q:
        if today.month >= 4:
            fy_start = f"{today.year}-04-01"
            fy_end = f"{today.year + 1}-03-31"
        else:
            fy_start = f"{today.year - 1}-04-01"
            fy_end = f"{today.year}-03-31"
        return fy_start, fy_end

    # "Month YYYY … Month YYYY" — two named months
    named_hits = []
    for month_name, month_num in _MONTH_MAP.items():
        for m in re.finditer(rf"\b{month_name}\b[,\s]+(20\d{{2}})\b", q):
            y = m.group(1)
            from calendar import monthrange
            last_day = monthrange(int(y), int(month_num))[1]
            named_hits.append((f"{y}-{month_num}-01", f"{y}-{month_num}-{last_day:02d}"))
    if len(named_hits) >= 2:
        named_hits.sort()
        return named_hits[0][0], named_hits[-1][1]
    if len(named_hits) == 1:
        return named_hits[0]

    # Multiple bare years (e.g. "2025 to 2026")
    year_hits = re.findall(r"\b(20\d{2})\b", q)
    if len(year_hits) >= 2:
        year_hits_sorted = sorted(set(year_hits))
        return f"{year_hits_sorted[0]}-01-01", f"{year_hits_sorted[-1]}-12-31"
    if len(year_hits) == 1:
        y = year_hits[0]
        return f"{y}-01-01", f"{y}-12-31"

    return None, None


def _detect_last_n(query: str) -> int:
    """Detect 'last N' pattern. Returns N or 0."""
    q = query.lower()
    _num_words = {
        "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
        "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
        "1": 1, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6,
    }
    m = re.search(r"last\s+(\w+)\s+(?:set\s+of\s+)?(?:minute|meeting|board|document|report)", q)
    if m:
        return _num_words.get(m.group(1), 0)
    return 0


# ── Metadata DB query ─────────────────────────────────────────────────────────


def _query_metadata(
    doc_type: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """Query document_metadata with optional filters. Returns list of row dicts."""
    clauses: list[str] = []
    params: list = []
    if doc_type:
        clauses.append("doc_type = ?")
        params.append(doc_type)
    if date_from:
        # Exclude NULL-dated docs when a specific range is requested
        clauses.append("doc_date >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("doc_date <= ?")
        params.append(date_to)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)
    try:
        with sqlite3.connect(_DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"SELECT * FROM document_metadata {where} "
                f"ORDER BY doc_date DESC NULLS LAST, filename LIMIT ?",
                params,
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception as exc:
        log.warning("Metadata query failed: %s", exc)
        return []


# ── File loading ──────────────────────────────────────────────────────────────


def _validate_path(filepath: Path) -> bool:
    """Ensure filepath resolves to within the watched folder or data dir."""
    try:
        resolved = filepath.resolve()
        watched = Path(_cfg.watched_folder).resolve()
        data_dir = Path("data").resolve()
        return str(resolved).startswith(str(watched)) or str(resolved).startswith(str(data_dir))
    except (OSError, ValueError):
        return False


def _load_file_text(filepath: Path) -> str:
    """Load plain text from a document file. Returns empty string on error."""
    if not _validate_path(filepath):
        log.warning("Path validation failed for %s — refusing to process", filepath)
        return ""
    try:
        suffix = filepath.suffix.lower()
        if suffix == ".docx":
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
        elif suffix == ".pdf":
            try:
                result = subprocess.run(
                    ["pdftotext", str(filepath), "-"],
                    capture_output=True, text=True, timeout=30,
                )
                if result.returncode == 0:
                    return result.stdout
            except Exception:
                return ""
        else:
            return filepath.read_text(errors="replace")
    except Exception as exc:
        log.warning("Failed to load %s: %s", filepath.name, exc)
    return ""


# ── Path resolution ───────────────────────────────────────────────────────────


def _resolve_path(source_file: str, docs_dir: Path) -> Path | None:
    """
    Resolve a source_file string (which may be a relative path like
    'minutes/foo.pdf' or a bare filename) to an absolute Path.
    Returns None if not found.
    """
    # Try direct join (handles both relative subdirectory paths and bare names)
    candidate = docs_dir / source_file
    if candidate.exists():
        return candidate
    # Try searching by bare filename only
    bare_name = Path(source_file).name
    matches = list(docs_dir.rglob(bare_name))
    return matches[0] if matches else None


def _rows_to_paths(rows: list[dict], docs_dir: Path) -> list[Path]:
    """Convert metadata rows to resolved file Paths (skipping missing files)."""
    paths: list[Path] = []
    for row in rows:
        filepath_str = row.get("filepath", "")
        filename = row.get("filename", "")
        p = Path(filepath_str) if filepath_str else None
        if p and p.exists():
            paths.append(p)
        else:
            found = _resolve_path(filename, docs_dir)
            if found:
                paths.append(found)
            else:
                log.warning("File not found in docs_dir: %s", filename)
    return paths


# ── Main retriever ────────────────────────────────────────────────────────────


class MetadataRetriever:
    """
    Hybrid document retriever.

    retrieve(query) returns (docs_context, file_list, used_metadata):
      docs_context — full document text to inject into LLM context
      file_list    — comma-separated relative paths for download link prompts
      used_metadata — True if any retrieval occurred (False → no docs found)
    """

    def __init__(self, docs_dir: Path = _DOCS_DIR, max_chars: int = _MAX_CHARS):
        self.docs_dir = docs_dir
        self.max_chars = max_chars

    async def retrieve(self, query: str) -> tuple[str, str, bool]:
        """
        Main entry point. Returns (docs_context, file_list, used_metadata).

        Strategy:
          1. "last N" queries  → most recent N docs of the detected type
          2. Date-range queries → metadata-filtered docs for the period
          3. All others        → vector search to find specific relevant docs,
                                 load those in full; type-based fallback if
                                 vector search returns nothing useful
        """
        doc_type = _detect_doc_type(query)
        date_from, date_to = _detect_date_range(query)
        last_n = _detect_last_n(query)

        log.info(
            "Retriever: doc_type=%s date_from=%s date_to=%s last_n=%d",
            doc_type, date_from, date_to, last_n,
        )

        # Build file list for download-link prompts
        file_list = ", ".join(
            str(f.relative_to(self.docs_dir))
            for f in sorted(self.docs_dir.rglob("*"))
            if f.is_file()
        )

        selected: list[Path] = []

        # ── Case 1: "last N" → date-sorted metadata ───────────────────────────
        if last_n > 0:
            rows = _query_metadata(doc_type=doc_type, limit=last_n * 4)
            rows.sort(key=lambda r: r.get("doc_date") or "", reverse=True)
            rows = rows[:last_n]
            selected = _rows_to_paths(rows, self.docs_dir)
            log.info("Last-%d retrieval: %d files selected", last_n, len(selected))

        # ── Case 2: Date-range specified → metadata-filtered ──────────────────
        elif date_from or date_to:
            rows = _query_metadata(
                doc_type=doc_type, date_from=date_from, date_to=date_to, limit=50,
            )
            selected = _rows_to_paths(rows, self.docs_dir)
            log.info(
                "Date-range retrieval: %d files for %s–%s",
                len(selected), date_from, date_to,
            )

        # ── Case 3: Vector search → find specific relevant docs ───────────────
        else:
            selected = await self._vector_retrieve(query, doc_type)

        if not selected:
            return "", file_list, False

        docs_context = self._load_files(selected)
        if not docs_context:
            return "", file_list, False

        return docs_context, file_list, True

    # ── Vector-search-based retrieval ─────────────────────────────────────────

    async def _vector_retrieve(
        self,
        query: str,
        doc_type: str | None,
    ) -> list[Path]:
        """
        Run a vector search, deduplicate by document, filter by relevance
        threshold, and return up to _MAX_FILES_FROM_VECTOR Paths.

        If no results pass the threshold but a doc_type was detected, returns
        the 3 most recent documents of that type as a fallback.
        """
        from app.storage.vector_store import vector_store

        # Search across ALL document types — do not filter by doc_type here.
        # If we filter and the type detection was wrong, we silently exclude
        # the correct answer.  doc_type is used only for the fallback below.
        hits = await vector_store.search(
            query=query,
            n_results=24,
            doc_type=None,
        )

        # Deduplicate by source_file, keep best (lowest) distance per file
        best: dict[str, float] = {}
        for hit in hits:
            src = hit.get("source_file", "")
            if not src:
                continue
            if src not in best or hit["distance"] < best[src]:
                best[src] = hit["distance"]

        # Filter by relevance threshold and sort by distance
        relevant = [
            (src, dist) for src, dist in best.items()
            if dist < _VECTOR_DISTANCE_THRESHOLD
        ]
        relevant.sort(key=lambda x: x[1])

        log.info(
            "Vector search: %d hits, %d files pass threshold (%.2f): %s",
            len(hits),
            len(relevant),
            _VECTOR_DISTANCE_THRESHOLD,
            [s for s, _ in relevant[:_MAX_FILES_FROM_VECTOR]],
        )

        # Deduplicate by resolved path so old flat-path chunks and new
        # subdir-path chunks for the same physical file don't double-load.
        seen_paths: set[Path] = set()
        paths: list[Path] = []
        for src, _ in relevant[:_MAX_FILES_FROM_VECTOR]:
            p = _resolve_path(src, self.docs_dir)
            if p and p not in seen_paths:
                seen_paths.add(p)
                paths.append(p)

        # Fallback: if vector search found nothing useful but a type was detected,
        # load the most recent 3 docs of that type
        if not paths and doc_type:
            rows = _query_metadata(doc_type=doc_type, limit=3)
            paths = _rows_to_paths(rows, self.docs_dir)
            log.info(
                "Vector fallback to type '%s': %d files", doc_type, len(paths)
            )

        return paths

    # ── Full-text loading ─────────────────────────────────────────────────────

    def _load_files(self, paths: list[Path]) -> str:
        """Load full text for each path. Returns combined context string."""
        full_docs: list[str] = []
        total_chars = 0

        for filepath in paths:
            if total_chars >= self.max_chars:
                break

            text = _load_file_text(filepath)
            if not text.strip():
                log.warning("No text extracted from %s", filepath.name)
                continue

            remaining = self.max_chars - total_chars
            if len(text) > remaining:
                text = text[:remaining]

            try:
                label = str(filepath.relative_to(self.docs_dir))
            except ValueError:
                label = filepath.name

            full_docs.append(f"=== DOCUMENT: {label} ===\n{text}")
            total_chars += len(text)
            log.info("Loaded: %s (%d chars)", filepath.name, len(text))

        return "\n\n".join(full_docs)
