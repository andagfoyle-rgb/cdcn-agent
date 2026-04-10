"""
scheduler_helpers — Shared helper functions for the CDCN Agent scheduler.

Contains time parsing, state checks, notification, audit logging,
document context builders, funding deadline utilities, and the
_with_retry decorator used by all scheduled jobs.
"""
from __future__ import annotations

import asyncio
import functools
import logging
import shutil
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.config import settings

log = logging.getLogger(__name__)

# Token used by OpenCLAW to signal "nothing to report".
# We adopt the same convention for the heartbeat LLM call.
HEARTBEAT_OK = "HEARTBEAT_OK"
# Suppress notification if the stripped reply is under this many chars.
ACK_MAX_CHARS = 300


def _with_retry(max_retries: int = 2, backoff_base: float = 5.0):
    """Decorator that retries an async scheduler job on transient failure.

    Retries up to max_retries times with exponential backoff.
    Final failure is logged but not re-raised (scheduler must never crash).
    """
    def decorator(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            for attempt in range(max_retries + 1):
                try:
                    return await fn(*args, **kwargs)
                except Exception as exc:
                    if attempt < max_retries:
                        delay = backoff_base * (2 ** attempt)
                        log.warning(
                            "%s failed (attempt %d/%d), retrying in %.0fs: %s",
                            fn.__name__, attempt + 1, max_retries + 1, delay, exc,
                        )
                        await asyncio.sleep(delay)
                    else:
                        log.error(
                            "%s failed after %d attempts: %s",
                            fn.__name__, max_retries + 1, exc, exc_info=True,
                        )
        return wrapper
    return decorator


def _parse_time(t: str) -> tuple[int, int]:
    h, m = t.split(":")
    return int(h), int(m)


def _is_wake() -> bool:
    """Return True only when the agent is fully in WAKE state."""
    try:
        from app.state_manager import get_state_manager
        return get_state_manager().is_accepting_messages()
    except Exception:
        return True  # fail-open before state manager is wired up


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


async def _notify(message: str) -> None:
    """
    Send message to Discord status channel and Telegram notification chat.

    Mirrors AgentStateManager._broadcast_sleep_notice().
    Errors are logged and swallowed — never raise from a notification.
    """
    try:
        from app.state_manager import get_state_manager
        mgr = get_state_manager()
        discord = mgr._discord
        telegram = mgr._telegram
    except Exception as exc:
        log.warning("_notify: could not resolve adapters: %s", exc)
        return

    if discord:
        try:
            status_id = settings.discord_status_channel_id
            if status_id:
                await discord.send_message(status_id, message)
        except Exception as exc:
            log.warning("_notify: Discord send failed: %s", exc)

    if telegram:
        try:
            chat_id = settings.telegram_notification_chat_id
            if chat_id:
                await telegram.send_message(chat_id, message)
        except Exception as exc:
            log.warning("_notify: Telegram send failed: %s", exc)


async def _audit_start(job_id: str) -> None:
    try:
        from app.storage.audit_log import log_event
        await log_event(actor="scheduler", action=f"{job_id}:start", detail=_now_iso())
    except Exception as exc:
        log.debug("audit start failed: %s", exc)


async def _audit_end(job_id: str, duration_ms: int, detail: str = "") -> None:
    try:
        from app.storage.audit_log import log_event
        await log_event(
            actor="scheduler",
            action=f"{job_id}:complete",
            detail=f"duration_ms={duration_ms} {detail}".strip(),
        )
    except Exception as exc:
        log.debug("audit end failed: %s", exc)


def _is_heartbeat_ok(text: str) -> bool:
    """
    Return True if the LLM reply is a HEARTBEAT_OK acknowledgment that
    should be suppressed — matching OpenCLAW's ack-suppression logic.

    A reply is treated as OK if:
      - It starts OR ends with HEARTBEAT_OK (after stripping whitespace), AND
      - The remaining content is <= ACK_MAX_CHARS characters.
    """
    stripped = text.strip()
    if stripped == HEARTBEAT_OK:
        return True
    remainder = stripped
    if stripped.startswith(HEARTBEAT_OK):
        remainder = stripped[len(HEARTBEAT_OK):].strip()
    elif stripped.endswith(HEARTBEAT_OK):
        remainder = stripped[: -len(HEARTBEAT_OK)].strip()
    else:
        return False
    return len(remainder) <= ACK_MAX_CHARS


def _build_doc_context() -> str:
    """
    Summarise the current ChromaDB collection: total chunks and counts by type.
    Returns a short markdown string safe to embed in an LLM prompt.
    """
    try:
        from app.storage.vector_store import vector_store
        docs = vector_store.list_documents()
        if not docs:
            return "Document archive: empty."
        type_counts: Counter[str] = Counter(d.get("document_type", "other") for d in docs)
        lines = [f"Document archive: {len(docs)} document(s) indexed."]
        for doc_type, count in sorted(type_counts.items()):
            lines.append(f"  - {doc_type}: {count}")
        return "\n".join(lines)
    except Exception as exc:
        log.debug("_build_doc_context failed: %s", exc)
        return "Document archive: unavailable."


def _load_funding_deadlines_text() -> str:
    """Return raw text of funding_deadlines.yaml, or a placeholder."""
    path = Path("skills_config/funding_deadlines.yaml")
    try:
        return path.read_text() if path.exists() else "(funding_deadlines.yaml not found)"
    except OSError:
        return "(could not read funding_deadlines.yaml)"


def _load_heartbeat_instructions() -> str:
    """Read skills_config/memory/heartbeat.md — the standing task checklist."""
    path = Path("skills_config/memory/heartbeat.md")
    try:
        return path.read_text() if path.exists() else ""
    except OSError:
        return ""


def _funding_deadlines_within_days(n_days: int) -> list[str]:
    """
    Return alert strings for funding deadlines within the next n_days.
    Skips submitted / awarded / declined entries.
    """
    path = Path("skills_config/funding_deadlines.yaml")
    if not path.exists():
        return []
    try:
        import yaml
        data = yaml.safe_load(path.read_text()) or {}
    except Exception as exc:
        log.warning("Could not parse funding_deadlines.yaml: %s", exc)
        return []

    today = datetime.now().date()
    skip_statuses = {"submitted", "awarded", "declined"}
    alerts: list[str] = []

    for item in data.get("deadlines", []):
        deadline_str = str(item.get("deadline", "")).strip()
        if not deadline_str or item.get("status", "") in skip_statuses:
            continue
        try:
            deadline_date = datetime.strptime(deadline_str, "%Y-%m-%d").date()
            days = (deadline_date - today).days
            if 0 <= days <= n_days:
                funder = item.get("funder", "[funder]")
                programme = item.get("programme", item.get("program", "[programme]"))
                alerts.append(
                    f"**Funding deadline approaching:** {funder} — {programme} "
                    f"— deadline {deadline_date.strftime('%d %B %Y')} ({days} days)."
                )
        except ValueError:
            pass

    return alerts


def _read_session_log_today() -> str:
    """Read today's session log file; return empty string if absent."""
    path = Path(settings.memory_path) / "session_log" / f"{_today()}.md"
    try:
        return path.read_text() if path.exists() else ""
    except OSError:
        return ""


def _docs_indexed_since(cutoff: datetime) -> list[dict]:
    """Return list_documents() entries indexed after cutoff."""
    try:
        from app.storage.vector_store import vector_store
        docs = vector_store.list_documents()
        result = []
        for d in docs:
            ts_str = d.get("indexed_at", "")
            if not ts_str:
                continue
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                if ts >= cutoff:
                    result.append(d)
            except ValueError:
                pass
        return result
    except Exception as exc:
        log.debug("_docs_indexed_since failed: %s", exc)
        return []


def _extract_month_key(path: Path) -> str:
    """
    Extract a YYYY-MM month key from a session log file.

    Tries to parse from the filename first (expecting YYYY-MM-DD prefix),
    falls back to the file's modification time.
    """
    name = path.stem  # e.g. "2026-01-15" or "2026-01-15_extra"
    try:
        # Try parsing the first 10 chars as a date
        dt = datetime.strptime(name[:10], "%Y-%m-%d")
        return dt.strftime("%Y-%m")
    except (ValueError, IndexError):
        pass
    # Fall back to mtime
    mtime = datetime.fromtimestamp(path.stat().st_mtime)
    return mtime.strftime("%Y-%m")
