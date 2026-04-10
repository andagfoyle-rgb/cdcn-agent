"""
document_parser -- structured document parsing for CDCN RAG pipeline.

Uses pdfplumber (PDF) and python-docx (DOCX) to extract:
  - Full text with preserved structure
  - Sections (detected from headings, bold text, numbered items)
  - Tables (preserved as markdown)
  - Metadata (title, date, attendees, apologies, chair)

PDF and DOCX parsing routines live in document_parser_helpers.py.
"""
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from app.utils.dates import extract_date_from_text, _MONTH_MAP as _SHARED_MONTH_MAP
from app.utils.extraction import extract_attendees as _shared_extract_attendees

log = logging.getLogger(__name__)


@dataclass
class ParsedSection:
    title: str
    content: str
    page_number: int = 1


@dataclass
class ParsedTable:
    markdown: str
    page_number: int = 1


@dataclass
class ParsedDocument:
    full_text: str
    sections: list[ParsedSection] = field(default_factory=list)
    tables: list[ParsedTable] = field(default_factory=list)
    title: str = ""
    date: str | None = None
    attendees: list[str] = field(default_factory=list)
    page_count: int = 0
    extraction_method: str = ""
    has_images: bool = False
    apologies: list[str] = field(default_factory=list)
    chair: str | None = None


# ── Month map (delegated to app.utils.dates) ─────────────────────────────────

_MONTH_MAP = _SHARED_MONTH_MAP  # backward-compat alias


# ── Table to markdown ────────────────────────────────────────────────────────

def _table_to_markdown(table: list[list]) -> str:
    """Convert a pdfplumber table (list of rows) to a markdown table string."""
    if not table or not table[0]:
        return ""
    rows = []
    for row in table:
        rows.append([str(cell).strip().replace("\n", " ") if cell else "" for cell in row])

    cols = len(rows[0])
    lines = []
    lines.append("| " + " | ".join(rows[0]) + " |")
    lines.append("| " + " | ".join(["---"] * cols) + " |")
    for row in rows[1:]:
        padded = row + [""] * (cols - len(row))
        lines.append("| " + " | ".join(padded[:cols]) + " |")
    return "\n".join(lines)


# ── Attendee extraction (delegated to app.utils.extraction) ──────────────────

def _extract_attendees(text: str) -> list[str]:
    """Backward-compat wrapper -- delegates to app.utils.extraction."""
    return _shared_extract_attendees(text)


# ── Apologies extraction ─────────────────────────────────────────────────────

_APOLOGIES_RE = re.compile(
    r"(?:apologies|absent|not\s+present)\s*:\s*(.+)",
    re.I,
)


def _extract_apologies(text: str) -> list[str]:
    """Extract names listed under apologies / absent / not present."""
    names: list[str] = []
    for m in _APOLOGIES_RE.finditer(text):
        raw = m.group(1).strip().rstrip(".")
        for name in re.split(r"[,;]", raw):
            name = name.strip()
            if 2 < len(name) < 80:
                names.append(name)
    return names


# ── Chair extraction ─────────────────────────────────────────────────────────

_CHAIR_RE = re.compile(
    r"(?:chair(?:ed)?\s+by\s+|chair\s*:\s*)(.+)",
    re.I,
)


def _extract_chair(text: str) -> str | None:
    """Extract the name of the chair from text."""
    m = _CHAIR_RE.search(text)
    if m:
        name = m.group(1).strip().rstrip(".")
        name = re.split(r"[,;]", name)[0].strip()
        if 2 < len(name) < 80:
            return name
    return None


# ── Date extraction from text (delegated to app.utils.dates) ─────────────────

def _extract_date_from_text(text: str) -> str | None:
    """Backward-compat wrapper -- delegates to app.utils.dates."""
    return extract_date_from_text(text)


# ── Title extraction ─────────────────────────────────────────────────────────

def _extract_title(text: str, filename: str) -> str:
    """Extract document title from first meaningful lines or filename."""
    for line in text.split("\n")[:15]:
        line = line.strip()
        if 5 < len(line) < 200 and not line.startswith("http"):
            return line
    stem = re.sub(r"[_\-]+", " ", Path(filename).stem).strip()
    return stem[:200]


# ── Section detection ────────────────────────────────────────────────────────

_SECTION_PATTERNS = [
    re.compile(r"^(?:progress\s+(?:on|from)\s+(?:recent\s+)?action\s+points?)", re.I),
    re.compile(r"^(?:report\s+from\s+\w+)", re.I),
    re.compile(r"^(?:d\.?o\.?\s+(?:update|report))", re.I),
    re.compile(r"^(?:aocb|any\s+other\s+(?:competent\s+)?business)", re.I),
    re.compile(r"^(?:date\s+(?:of\s+next\s+meeting|and\s+venue))", re.I),
    re.compile(r"^(?:approval\s+of\s+minutes)", re.I),
    re.compile(r"^(?:matters?\s+arising)", re.I),
    re.compile(r"^(?:treasurer'?s?\s+report)", re.I),
    re.compile(r"^(?:financial\s+(?:report|update|summary))", re.I),
    re.compile(r"^(?:chair'?s?\s+(?:report|remarks?|opening))", re.I),
    re.compile(r"^(?:correspondence)", re.I),
    re.compile(r"^(?:planning|projects?|updates?)\s", re.I),
    re.compile(r"^\d{1,2}\.\s+[A-Z]"),
    re.compile(r"^[A-Z][A-Z\s,&/]{10,80}$"),
    re.compile(r"^[A-Z][A-Za-z\s/&]+:\s"),
    re.compile(r"^(?:agenda\s+)?item\s+\d+", re.I),
    re.compile(r"^\*\*[^*]+\*\*\s*$"),
    re.compile(r"^(?:chairman'?s?\s+report)", re.I),
    re.compile(r"^(?:secretary'?s?\s+report)", re.I),
]


def _is_section_heading(line: str) -> bool:
    """Check if a line looks like a section heading."""
    stripped = line.strip()
    if len(stripped) < 3 or len(stripped) > 200:
        return False
    return any(p.match(stripped) for p in _SECTION_PATTERNS)


def _detect_sections(text: str) -> list[ParsedSection]:
    """Split text into sections based on heading patterns."""
    lines = text.split("\n")
    sections: list[ParsedSection] = []
    current_title = "Preamble"
    current_lines: list[str] = []

    for line in lines:
        if _is_section_heading(line):
            content = "\n".join(current_lines).strip()
            if content:
                sections.append(ParsedSection(title=current_title, content=content))
            current_title = line.strip()
            current_lines = []
        else:
            current_lines.append(line)

    content = "\n".join(current_lines).strip()
    if content:
        sections.append(ParsedSection(title=current_title, content=content))

    return sections


# ── Plain text parsing ───────────────────────────────────────────────────────

def _parse_text(path: Path) -> ParsedDocument:
    """Parse a plain text or markdown file."""
    text = path.read_text(errors="replace").strip()
    return ParsedDocument(
        full_text=text,
        sections=_detect_sections(text),
        title=_extract_title(text, path.name),
        date=_extract_date_from_text(text),
        page_count=0,
    )


# ── Public API ───────────────────────────────────────────────────────────────

def parse_document(filepath: str | Path) -> ParsedDocument:
    """
    Parse a document file and return structured content.

    Supports: PDF, DOCX, TXT, MD
    Returns ParsedDocument with full_text, sections, tables, metadata.
    """
    path = Path(filepath)
    suffix = path.suffix.lower()

    if suffix == ".pdf":
        from app.skills.document_parser_helpers import _parse_pdf
        return _parse_pdf(path)
    elif suffix == ".docx":
        from app.skills.document_parser_helpers import _parse_docx
        return _parse_docx(path)
    elif suffix in (".txt", ".md"):
        return _parse_text(path)
    else:
        log.warning("Unsupported file type: %s", suffix)
        return ParsedDocument(full_text="", title=path.stem)
