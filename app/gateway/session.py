"""
Session management — file-backed persistence under data/memory/session_log/.

Directory layout:
    data/memory/session_log/
        YYYY-MM-DD/
            <session-id>.json
            <session-id>.tmp   (transient — atomic rename target)

SessionManager is the single source of truth.  The FastAPI HTTP endpoints
for the control plane are in router.py; this module is pure business logic.
"""
import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock

from app.config import settings
from app.gateway import identity_map

log = logging.getLogger(__name__)


# ── Message visibility filter ─────────────────────────────────────────────────

_HIDDEN_PREFIXES = (
    "IMPORTANT: Read ALL",   # RAG document injection
    "[Relevant pre-loaded",  # prefetch context
    "[Pre-loaded context]",
    "=== DOCUMENT:",         # full document dumps
    "=== JOURNAL:",          # journal dumps
    "[The writer skill",     # internal writer result
    "[Search result",        # internal search result
    "Keyword match",         # keyword fallback results
    "From ",                 # vector search excerpts ("From filename (page N):")
)

# Marker that separates the user's actual question from injected RAG context
_RAG_SEPARATOR = "--- RETRIEVED DOCUMENTS"


def _strip_rag_context(content: str) -> str:
    """Remove injected RAG context from a user message, keeping only the question."""
    idx = content.find(_RAG_SEPARATOR)
    if idx > 0:
        return content[:idx].rstrip()
    return content


def _is_visible_message(msg: dict) -> bool:
    """Return True if this message should be shown in the chat history UI."""
    role = msg.get("role", "")
    content = msg.get("content") or ""
    if role == "tool":
        return False
    if role == "assistant":
        stripped = content.strip()
        # Skip empty turns and tool-call-only turns with no prose
        if not stripped or stripped.startswith("<tool_call>"):
            return False
        return True
    if role == "user":
        return not any(content.startswith(p) for p in _HIDDEN_PREFIXES)
    return False


# ── Data model ────────────────────────────────────────────────────────────────


@dataclass
class Session:
    session_id: str
    user_id: str
    role: str
    messages: list[dict] = field(default_factory=list)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    last_active: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    exchange_count: int = 0


# ── Manager ───────────────────────────────────────────────────────────────────


class SessionManager:
    """
    Manages persistent sessions stored as JSON files.

    Key design choices mirrored from OpenCLAW's session store:
    - Session key derivation: user_id (normalised) + date determines lookup key
    - Atomic writes via temp-file rename to prevent partial reads
    - One session per user per day (load_or_create returns the first match)
    """

    def __init__(self) -> None:
        self._write_locks: dict[str, Lock] = {}
        self._locks_lock = Lock()

    def _get_write_lock(self, session_id: str) -> Lock:
        """Return a per-session lock, creating it if needed."""
        with self._locks_lock:
            if session_id not in self._write_locks:
                self._write_locks[session_id] = Lock()
            return self._write_locks[session_id]

    # ── Paths ──────────────────────────────────────────────────────────────────

    def _session_root(self) -> Path:
        return Path(settings.memory_path) / "session_log"

    def _day_dir(self, date_str: str) -> Path:
        d = self._session_root() / date_str
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _today(self) -> str:
        return datetime.now().strftime("%Y-%m-%d")

    def _session_date(self, session: "Session") -> str:
        """Extract date from created_at ISO string."""
        return session.created_at[:10]

    def _session_path(self, session_id: str, date_str: str) -> Path:
        return self._day_dir(date_str) / f"{session_id}.json"

    # ── Core operations ────────────────────────────────────────────────────────

    def load_or_create(self, user_id: str, role: str) -> Session:
        """
        Return today's active session for user_id, or create a new one.

        Resolves cross-interface identity links (e.g. discord:123 -> web:alice)
        before lookup, so linked users share a single session.

        Normalises user_id to lowercase + strip before comparison, consistent
        with the session-key normalisation pattern in OpenCLAW.
        """
        canonical = identity_map.resolve(user_id)
        normalised = canonical.strip().lower()
        today = self._today()
        day_dir = self._day_dir(today)

        for session_file in sorted(day_dir.glob("*.json")):
            if session_file.suffix == ".tmp":
                continue
            try:
                data = json.loads(session_file.read_text())
                if data.get("user_id", "").strip().lower() == normalised:
                    return Session(**data)
            except (json.JSONDecodeError, TypeError, KeyError):
                log.warning("Skipping corrupt session file: %s", session_file)

        session = Session(session_id=str(uuid.uuid4()), user_id=user_id, role=role)
        self._write(session)
        log.info(
            "New session created: id=%s user=%s role=%s",
            session.session_id,
            user_id,
            role,
        )
        return session

    # Maximum messages kept per session.  Beyond this we trim the oldest
    # non-system messages, keeping the first system message (if any) and the
    # most recent _MAX_SESSION_MESSAGES entries.  This prevents unbounded
    # growth that would bloat disk and slow JSON deserialisation.
    _MAX_SESSION_MESSAGES = 200

    def save(self, session: Session) -> None:
        """Persist session state.  Updates last_active timestamp before writing."""
        session.last_active = datetime.now(timezone.utc).isoformat()
        self._trim_messages(session)
        self._write(session)

    def _trim_messages(self, session: Session) -> None:
        """Cap session messages to _MAX_SESSION_MESSAGES, preserving system messages."""
        msgs = session.messages
        if len(msgs) <= self._MAX_SESSION_MESSAGES:
            return
        # Keep the system message(s) at the start and the most recent messages
        system_msgs = [m for m in msgs if m.get("role") == "system"]
        non_system = [m for m in msgs if m.get("role") != "system"]
        keep = self._MAX_SESSION_MESSAGES - len(system_msgs)
        session.messages = system_msgs + non_system[-keep:] if keep > 0 else system_msgs
        log.info(
            "Trimmed session %s from %d to %d messages",
            session.session_id, len(msgs), len(session.messages),
        )

    def load_recent_messages(self, user_id: str, hours: int = 48) -> list[dict]:
        """
        Return all visible messages for user_id from the last `hours` hours,
        oldest-first, filtered to only user↔agent exchanges (no RAG injections,
        tool payloads, or internal context blocks).
        """
        canonical = identity_map.resolve(user_id)
        normalised = canonical.strip().lower()
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

        # Collect day directories that could contain sessions within the window
        days_to_check: list[str] = []
        for offset in range(hours // 24 + 2):  # +2 for safety at day boundaries
            day = (datetime.now() - timedelta(days=offset)).strftime("%Y-%m-%d")
            days_to_check.append(day)

        all_messages: list[tuple[str, dict]] = []  # (iso_timestamp, message)

        for day in days_to_check:
            day_dir = self._session_root() / day
            if not day_dir.exists():
                continue
            for session_file in sorted(day_dir.glob("*.json")):
                if session_file.suffix == ".tmp":
                    continue
                try:
                    data = json.loads(session_file.read_text())
                    if data.get("user_id", "").strip().lower() != normalised:
                        continue
                    session_created = data.get("created_at", "")
                    session_date = session_created[:10]  # YYYY-MM-DD
                    for msg in data.get("messages", []):
                        if not _is_visible_message(msg):
                            continue
                        tagged = dict(msg)
                        if tagged["role"] == "user":
                            tagged["content"] = _strip_rag_context(tagged["content"])
                        tagged["_session_date"] = session_date
                        all_messages.append((session_created, tagged))
                except (json.JSONDecodeError, TypeError, KeyError):
                    log.warning("Skipping corrupt session file: %s", session_file)

        # Sort by session creation time (messages within a session are already ordered)
        # and deduplicate exact content
        seen: set[str] = set()
        result: list[dict] = []
        for _, msg in sorted(all_messages, key=lambda x: x[0]):
            key = f"{msg['role']}:{msg['content'][:120]}"
            if key not in seen:
                seen.add(key)
                result.append(msg)

        return result

    def load_all_recent_messages(self, hours: int = 48) -> list[dict]:
        """
        Return all visible messages from ALL users in the last `hours` hours,
        oldest-first, with user attribution on each message.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

        days_to_check: list[str] = []
        for offset in range(hours // 24 + 2):
            day = (datetime.now() - timedelta(days=offset)).strftime("%Y-%m-%d")
            days_to_check.append(day)

        all_messages: list[tuple[str, dict]] = []

        for day in days_to_check:
            day_dir = self._session_root() / day
            if not day_dir.exists():
                continue
            for session_file in sorted(day_dir.glob("*.json")):
                if session_file.suffix == ".tmp":
                    continue
                try:
                    data = json.loads(session_file.read_text())
                    raw_user_id = data.get("user_id", "")
                    # Extract display name from "web:alice" → "alice"
                    display_name = raw_user_id.split(":", 1)[-1] if ":" in raw_user_id else raw_user_id
                    session_created = data.get("created_at", "")
                    session_date = session_created[:10]
                    for msg in data.get("messages", []):
                        if not _is_visible_message(msg):
                            continue
                        tagged = dict(msg)
                        if tagged["role"] == "user":
                            tagged["content"] = _strip_rag_context(tagged["content"])
                        tagged["_session_date"] = session_date
                        tagged["_user"] = display_name
                        all_messages.append((session_created, tagged))
                except (json.JSONDecodeError, TypeError, KeyError):
                    log.warning("Skipping corrupt session file: %s", session_file)

        seen: set[str] = set()
        result: list[dict] = []
        for _, msg in sorted(all_messages, key=lambda x: x[0]):
            key = f"{msg.get('_user','')}:{msg['role']}:{msg['content'][:120]}"
            if key not in seen:
                seen.add(key)
                result.append(msg)

        return result

    def get_today_sessions(self) -> list[Session]:
        """Return all sessions created today, newest-last."""
        return self._get_sessions_for_date(self._today())

    def get_yesterday_sessions(self) -> list[Session]:
        """Return all sessions created yesterday, newest-last.

        Used by the dream worker's consolidate_memory task which runs
        after midnight — by then 'today' has rolled over but the
        conversations to consolidate were from yesterday.
        """
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        return self._get_sessions_for_date(yesterday)

    def _get_sessions_for_date(self, date_str: str) -> list[Session]:
        """Return all sessions for a given date string (YYYY-MM-DD)."""
        day_dir = self._session_root() / date_str
        if not day_dir.exists():
            return []
        sessions: list[Session] = []

        for session_file in sorted(day_dir.glob("*.json")):
            if session_file.suffix == ".tmp":
                continue
            try:
                data = json.loads(session_file.read_text())
                sessions.append(Session(**data))
            except (json.JSONDecodeError, TypeError, KeyError):
                log.warning("Skipping corrupt session file: %s", session_file)

        return sessions

    # ── I/O ───────────────────────────────────────────────────────────────────

    def _write(self, session: Session) -> None:
        """Atomic write: serialise to a .tmp file then rename into place.

        Uses a per-session lock to prevent concurrent writes from corrupting
        the temp file (e.g. two interfaces saving the same session simultaneously).
        """
        lock = self._get_write_lock(session.session_id)
        with lock:
            date_str = self._session_date(session)
            final = self._session_path(session.session_id, date_str)
            tmp = final.with_suffix(".tmp")
            try:
                tmp.write_text(json.dumps(asdict(session), indent=2))
                tmp.rename(final)
            except OSError as exc:
                log.error("Failed to write session %s: %s", session.session_id, exc)
                tmp.unlink(missing_ok=True)
                raise
