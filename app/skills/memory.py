"""
MemorySkill -- complete memory management for CDCN Agent.

Architecture follows OpenCLAW's flat-markdown memory pattern:
  * Identity/rules files live in skills_config/memory/ and are loaded
    verbatim into the system prompt (soul.md + agents.md + org_profile.md).
  * Long-term memory lives in skills_config/memory/memory.md as ## sections
    that are appended to (never destructively overwritten) with timestamps.
  * Runtime data (session logs, journal, cache) lives under data/memory/.

heartbeat.md contains *instructions* for the heartbeat task.
The heartbeat execution log goes to data/memory/heartbeat_log.md.
"""
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from app.config import settings
from app.skills.base import BaseSkill, SkillResult

log = logging.getLogger(__name__)

# Thread-safe lock for memory.md writes (sync methods called from async tasks)
_memory_write_lock = threading.Lock()

# ── Static identity / rules paths (in version control) ───────────────────────

MEMORY_DIR = Path("skills_config/memory")
SOUL_FILE = MEMORY_DIR / "soul.md"
AGENTS_FILE = MEMORY_DIR / "agents.md"
HEARTBEAT_FILE = MEMORY_DIR / "heartbeat.md"
STYLE_GUIDE_FILE = MEMORY_DIR / "style_guide.md"
KNOWLEDGE_GRAPH_FILE = MEMORY_DIR / "knowledge_graph.md"
LONG_TERM_MEMORY_FILE = MEMORY_DIR / "memory.md"
ORG_PROFILE_FILE = Path("skills_config/org_profile.md")
FUNDING_DEADLINES_FILE = Path("skills_config/funding_deadlines.yaml")

# ── Runtime data paths (derived from settings) ────────────────────────────────


def _data_dir() -> Path:
    return Path(settings.memory_path)


def _journal_dir() -> Path:
    return _data_dir() / "journal"


def _session_log_dir() -> Path:
    return _data_dir() / "session_log"


def _current_task_file() -> Path:
    return _data_dir() / "current_task.md"


def _prefetch_cache_file() -> Path:
    return _data_dir() / "prefetch_cache.md"


def _heartbeat_log_file() -> Path:
    return _data_dir() / "heartbeat_log.md"


# ── Helpers ───────────────────────────────────────────────────────────────────


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _now_display() -> str:
    """Human-readable UTC timestamp for memory entries."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _read_optional(path: Path) -> str:
    """Read a file; return empty string if missing or unreadable."""
    try:
        return path.read_text() if path.exists() else ""
    except OSError:
        return ""


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


# ── MemorySkill ───────────────────────────────────────────────────────────────


class MemorySkill(BaseSkill):
    """
    Manages CDCN Agent's layered memory system.

    Long-term identity/rules (skills_config/memory/):
        soul.md, agents.md, heartbeat.md, style_guide.md,
        knowledge_graph.md, memory.md

    Runtime data (data/memory/):
        session_log/YYYY-MM-DD.md, journal/YYYY-MM-DD.md,
        journal/dream_YYYY-MM-DD.md, current_task.md,
        prefetch_cache.md, heartbeat_log.md
    """

    name = "memory"
    description = (
        "Manage CDCN's organisational memory: read context, update long-term memory, "
        "write journal and session logs, check upcoming funding deadlines."
    )

    def __init__(self, settings=None) -> None:
        self._prompt_cache: str = ""
        self._prompt_cache_ts: float = 0.0

    # ── Loaders ──────────────────────────────────────────────────────────────

    def load_soul(self) -> str:
        return _read_optional(SOUL_FILE)

    def load_memory(self) -> str:
        return _read_optional(LONG_TERM_MEMORY_FILE)

    def load_agents_rules(self) -> str:
        return _read_optional(AGENTS_FILE)

    def load_heartbeat_instructions(self) -> str:
        return _read_optional(HEARTBEAT_FILE)

    def load_style_guide(self) -> str:
        return _read_optional(STYLE_GUIDE_FILE)

    def load_knowledge_graph(self) -> str:
        return _read_optional(KNOWLEDGE_GRAPH_FILE)

    # ── System prompt (60 s TTL) ─────────────────────────────────────────────

    def get_system_prompt(self) -> str:
        now = time.monotonic()
        if self._prompt_cache and (now - self._prompt_cache_ts) < 60.0:
            return self._prompt_cache

        parts: list[str] = []
        for path in (SOUL_FILE, AGENTS_FILE, ORG_PROFILE_FILE):
            text = _read_optional(path)
            if text.strip():
                parts.append(text.strip())

        prompt = "\n\n---\n\n".join(parts) if parts else "(no identity context loaded)"
        self._prompt_cache = prompt
        self._prompt_cache_ts = now
        return prompt

    # ── Long-term memory — section-based append ─────────────────────────────

    def update_memory(self, section: str, content: str) -> SkillResult:
        if not section or not content:
            return SkillResult(success=False, error="section and content are required")

        ts = _now_display()
        new_entry = f"\n*Updated {ts}*\n\n{content.strip()}\n"
        header_line = f"## {section}"

        with _memory_write_lock:
            current_text = _read_optional(LONG_TERM_MEMORY_FILE)
            lines = current_text.splitlines(keepends=True)

            target_idx: int | None = None
            for i, line in enumerate(lines):
                if line.rstrip() == header_line:
                    target_idx = i
                    break

            if target_idx is not None:
                end_idx = len(lines)
                for i in range(target_idx + 1, len(lines)):
                    if lines[i].startswith("## "):
                        end_idx = i
                        break
                insert_at = end_idx
                while insert_at > target_idx + 1 and lines[insert_at - 1].strip() == "":
                    insert_at -= 1
                new_lines = lines[:insert_at] + [new_entry] + lines[insert_at:]
                new_text = "".join(new_lines)
            else:
                new_text = current_text.rstrip() + f"\n\n{header_line}\n{new_entry}"

            LONG_TERM_MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
            LONG_TERM_MEMORY_FILE.write_text(new_text)
        log.info("Updated memory section '## %s'", section)
        return SkillResult(success=True, output=f"Updated '## {section}' in memory.md")

    # ── Session log ──────────────────────────────────────────────────────────

    def log_session_summary(self, session_id: str, summary: str) -> SkillResult:
        if not summary:
            return SkillResult(success=False, error="summary is required")

        log_dir = _session_log_dir()
        _ensure_dir(log_dir)
        log_file = log_dir / f"{_today()}.md"

        sid_short = (session_id or "unknown")[:8]
        ts = _now_display()
        entry = f"\n## Session summary — {sid_short} at {ts}\n\n{summary.strip()}\n"

        with log_file.open("a") as f:
            f.write(entry)

        log.info("Session summary logged: session=%s", session_id)
        return SkillResult(success=True, output=str(log_file))

    # ── Journal ──────────────────────────────────────────────────────────────

    def write_journal(self, date: str = "", content: str = "") -> SkillResult:
        if not content:
            return SkillResult(success=False, error="content is required")
        target_date = date or _today()
        _ensure_dir(_journal_dir())
        path = _journal_dir() / f"{target_date}.md"
        path.write_text(f"# Journal — {target_date}\n\n{content.strip()}\n")
        log.info("Journal written for %s", target_date)
        return SkillResult(success=True, output=str(path))

    def write_dream_journal(self, date: str = "", content: str = "") -> SkillResult:
        if not content:
            return SkillResult(success=False, error="content is required")
        target_date = date or _today()
        _ensure_dir(_journal_dir())
        path = _journal_dir() / f"dream_{target_date}.md"
        path.write_text(f"# Dream journal — {target_date}\n\n{content.strip()}\n")
        log.info("Dream journal written for %s", target_date)
        return SkillResult(success=True, output=str(path))

    def read_recent_journal(self, n_days: int = 3) -> str:
        j_dir = _journal_dir()
        entries: list[str] = []

        for offset in range(n_days - 1, -1, -1):
            date_str = (datetime.now(timezone.utc) - timedelta(days=offset)).strftime("%Y-%m-%d")
            for prefix in ("", "dream_"):
                path = j_dir / f"{prefix}{date_str}.md"
                if path.exists():
                    text = _read_optional(path).strip()
                    if text:
                        entries.append(text)

        return "\n\n---\n\n".join(entries) if entries else "(no recent journal entries)"

    # ── Current task ─────────────────────────────────────────────────────────

    def write_current_task(self, description: str = "") -> SkillResult:
        path = _current_task_file()
        _ensure_dir(path.parent)
        ts = _now_display()
        path.write_text(
            f"# Current Task\n\nUpdated: {ts}\n\n{description.strip() or '(idle)'}\n"
        )
        return SkillResult(success=True, output=description or "(idle)")

    def read_current_task(self) -> Optional[str]:
        path = _current_task_file()
        if not path.exists():
            return None
        return _read_optional(path)

    # ── Prefetch cache ───────────────────────────────────────────────────────

    def read_prefetch_cache(self) -> SkillResult:
        return SkillResult(success=True, output=_read_optional(_prefetch_cache_file()))

    def write_prefetch_cache(self, content: str = "") -> SkillResult:
        path = _prefetch_cache_file()
        _ensure_dir(path.parent)
        path.write_text(content)
        return SkillResult(success=True, output=str(path))

    # ── get_context ──────────────────────────────────────────────────────────

    def get_context(self) -> SkillResult:
        parts: list[str] = []

        soul = self.load_soul()
        if soul.strip():
            parts.append(f"# Identity\n\n{soul.strip()}")

        memory = self.load_memory()
        if memory.strip():
            parts.append(f"# Long-term Memory\n\n{memory.strip()}")

        journal = self.read_recent_journal(n_days=3)
        if journal and journal != "(no recent journal entries)":
            parts.append(f"# Recent Journal\n\n{journal}")

        task = self.read_current_task()
        if task:
            parts.append(f"# Current Task\n\n{task.strip()}")

        output = "\n\n---\n\n".join(parts) if parts else "(no context available)"
        return SkillResult(success=True, output=output)

    # ── run() dispatcher ─────────────────────────────────────────────────────

    async def run(self, action: str = "recall", **kwargs) -> SkillResult:  # noqa: C901
        if action == "get_context":
            return self.get_context()
        if action == "recall":
            soul = self.load_soul()
            return SkillResult(success=True, output=soul or "(no soul file found)")
        if action == "update_memory":
            return self.update_memory(
                section=kwargs.get("section", ""),
                content=kwargs.get("content", ""),
            )
        if action in ("store_summary", "log_session_summary"):
            return self.log_session_summary(
                session_id=kwargs.get("session_id", ""),
                summary=kwargs.get("summary", ""),
            )
        if action == "journal":
            from app.skills.memory_helpers import auto_journal
            return await auto_journal(self)
        if action == "write_journal":
            return self.write_journal(
                date=kwargs.get("date", ""),
                content=kwargs.get("content", ""),
            )
        if action == "write_dream_journal":
            return self.write_dream_journal(
                date=kwargs.get("date", ""),
                content=kwargs.get("content", ""),
            )
        if action == "read_journal":
            return self.read_recent_journal(n_days=int(kwargs.get("n_days", 3)))
        if action == "write_current_task":
            return self.write_current_task(description=kwargs.get("description", ""))
        if action == "read_current_task":
            return self.read_current_task()
        if action == "read_prefetch_cache":
            return self.read_prefetch_cache()
        if action == "write_prefetch_cache":
            return self.write_prefetch_cache(content=kwargs.get("content", ""))
        if action == "heartbeat":
            from app.skills.memory_helpers import run_heartbeat
            return await run_heartbeat(self)

        return SkillResult(success=False, error=f"Unknown memory action: '{action}'")
