"""
dream_helpers — shared constants, dataclass, file-lock registry, and small
utility functions used by dream_worker and dream_tasks.

Extracted from dream_worker.py to keep each module under 300 lines.
"""
import asyncio
import re
from dataclasses import dataclass
from pathlib import Path


# ── File-level locks — prevent concurrent writes to shared files ─────────────
# Used by dream_worker tasks AND scheduler jobs that may overlap.
_file_locks: dict[str, asyncio.Lock] = {}


def _get_file_lock(path: str | Path) -> asyncio.Lock:
    """Return a per-path asyncio.Lock (created on first use)."""
    key = str(path)
    if key not in _file_locks:
        _file_locks[key] = asyncio.Lock()
    return _file_locks[key]


# ── Static paths ─────────────────────────────────────────────────────────────

_KNOWLEDGE_GRAPH_FILE = Path("skills_config/memory/knowledge_graph.md")

# ── Heuristic correction phrases (used by self_critique + learn_from_interactions) ──

_CORRECTION_PHRASES = [
    "actually,",
    "that's wrong",
    "thats wrong",
    "no, i meant",
    "no i meant",
    "not quite",
    "you misunderstood",
    "that's not right",
    "thats not right",
    "i didn't mean",
    "i meant",
    "wrong —",
    "wrong,",
    "that wasn't",
]

# ── Task outcome record ─────────────────────────────────────────────────────


@dataclass
class _TaskOutcome:
    task: str
    success: bool
    summary: str
    error: str = ""
    duration_ms: int = 0


# ── Private helpers ──────────────────────────────────────────────────────────


def _session_transcript(session) -> str:
    """Format a Session's messages list as a readable conversation string."""
    lines = []
    for msg in session.messages:
        role = msg.get("role", "user").capitalize()
        content = msg.get("content", "").strip()
        if content:
            lines.append(f"{role}: {content}")
    return "\n".join(lines)


def _is_correction(text: str) -> bool:
    lower = text.lower()
    return any(phrase in lower for phrase in _CORRECTION_PHRASES)


def _truncate(text: str, max_chars: int = 3000) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n…[truncated]"


def _read_optional(path: Path) -> str:
    try:
        return path.read_text() if path.exists() else ""
    except OSError:
        return ""


def _extract_section(text: str, header: str) -> str:
    """Extract body text between a '## HEADER' and the next '## ' or EOF."""
    pattern = re.escape(header) + r"\s*\n(.*?)(?=\n##\s|\Z)"
    match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() if match else ""
