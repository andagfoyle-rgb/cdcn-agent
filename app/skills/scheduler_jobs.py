"""
scheduler_jobs — Core scheduled job functions for the CDCN Agent.

Contains wake/sleep transitions, auto-index, journal, funding feed,
and overdue check jobs.

Larger jobs (heartbeat, weekly digest, monthly governance, session archive)
live in scheduler_jobs_extended.py.
"""
from __future__ import annotations

import logging
import time

from app.config import settings
from app.skills.scheduler_helpers import (
    _with_retry,
    _is_wake,
    _today,
    _notify,
    _audit_start,
    _audit_end,
    _funding_deadlines_within_days,
    _read_session_log_today,
    _extract_month_key,
)

log = logging.getLogger(__name__)


# ── State transition jobs ──────────────────────────────────────────────────────


async def _wake_job() -> None:
    from app.state_manager import get_state_manager, AgentState
    manager = get_state_manager()
    if manager.current_state == AgentState.DREAM:
        log.info("Scheduled wake: triggering transition_to_wake")
        await manager.transition_to_wake()
    else:
        log.info("Scheduled wake: already in %s — no transition needed", manager.current_state)


async def _sleep_job() -> None:
    from app.state_manager import get_state_manager, AgentState
    manager = get_state_manager()
    if manager.current_state == AgentState.WAKE:
        log.info("Scheduled sleep: triggering transition_to_dream")
        await manager.transition_to_dream()
    else:
        log.info("Scheduled sleep: already in %s — no transition needed", manager.current_state)


# ── Job 2 — auto_index_job ─────────────────────────────────────────────────────


@_with_retry(max_retries=2, backoff_base=10.0)
async def _auto_index_job() -> None:
    """
    Auto-index: run DocumentIndexerSkill against WATCHED_FOLDER every 6 hours.
    Wake-only; logs results to audit_log.
    """
    if not _is_wake():
        log.debug("auto_index_job: not in wake state — skipped")
        return

    t0 = time.monotonic()
    await _audit_start("auto_index")
    log.info("auto_index_job: starting")

    try:
        from app.skills.indexer import IndexerSkill
        result = await IndexerSkill().run(folder=settings.watched_folder)
        duration_ms = int((time.monotonic() - t0) * 1000)

        if result.success:
            m = result.metadata or {}
            detail = (
                f"indexed={m.get('indexed', 0)} "
                f"skipped={m.get('skipped', 0)} "
                f"failed={m.get('failed', 0)}"
            )
            log.info("auto_index_job: complete in %dms — %s", duration_ms, detail)
            await _audit_end("auto_index", duration_ms, detail)
        else:
            log.warning("auto_index_job: failed — %s", result.error)
            await _audit_end("auto_index", duration_ms, f"error={result.error!r}")

    except Exception as exc:
        duration_ms = int((time.monotonic() - t0) * 1000)
        log.exception("auto_index_job: unhandled error after %dms: %s", duration_ms, exc)
        await _audit_end("auto_index", duration_ms, f"error={exc!r}")


# ── Job 3 — journal_job ────────────────────────────────────────────────────────


@_with_retry(max_retries=1, backoff_base=5.0)
async def _journal_job() -> None:
    """
    Nightly journal: summarise today's sessions, ask the LLM the three
    reflective questions, then write to data/memory/journal/YYYY-MM-DD.md.

    Runs just before transition_to_dream (scheduler ordering by JOURNAL_TIME).
    Wake-only.
    """
    if not _is_wake():
        log.debug("journal_job: not in wake state — skipped")
        return

    t0 = time.monotonic()
    await _audit_start("journal")
    log.info("journal_job: starting")

    try:
        from app.llm_client import llm_client
        from app.skills.memory import MemorySkill

        memory = MemorySkill()
        today = _today()

        # Build today's activity context from session log
        session_log = _read_session_log_today()
        recent_journal = memory.read_recent_journal(n_days=1).output or ""

        context = (
            f"**Date:** {today}\n\n"
            f"## Today's session log\n\n{session_log or '(no sessions today)'}\n\n"
            f"## Recent journal\n\n{recent_journal or '(no recent entries)'}"
        )

        system_prompt = (
            "You are CDCN Agent, an AI assistant for a Scottish community development charity. "
            "Write a first-person journal entry for today based on the session log below. "
            "Answer these three questions in your entry:\n"
            "  1. What did you work on today?\n"
            "  2. What was difficult or uncertain?\n"
            "  3. What do you want to remember for tomorrow?\n\n"
            "Be concise (5-8 sentences). Write in past tense. "
            "If there were no notable sessions, note that briefly."
        )

        entry = await llm_client.chat(
            [{"role": "user", "content": context}],
            system_prompt=system_prompt,
            skill_used="journal",
        )

        memory.write_journal(date=today, content=entry)
        duration_ms = int((time.monotonic() - t0) * 1000)
        log.info("journal_job: complete in %dms", duration_ms)
        await _audit_end("journal", duration_ms)

    except Exception as exc:
        duration_ms = int((time.monotonic() - t0) * 1000)
        log.exception("journal_job: unhandled error after %dms: %s", duration_ms, exc)
        await _audit_end("journal", duration_ms, f"error={exc!r}")


# ── Job 6 — funding_feed_job ──────────────────────────────────────────────────


@_with_retry(max_retries=2, backoff_base=10.0)
async def _funding_feed_job() -> None:
    """
    Fetch all configured RSS funding feeds, store new entries, and notify
    Discord + Telegram if high-relevance opportunities are found.
    Wake-only.
    """
    if not _is_wake():
        log.debug("funding_feed_job: not in wake state — skipped")
        return

    t0 = time.monotonic()
    await _audit_start("funding_feed")
    log.info("funding_feed_job: starting")

    try:
        from app.skills.funding_feed import fetch_all_feeds, _mark_notified

        result = await fetch_all_feeds()
        duration_ms = int((time.monotonic() - t0) * 1000)

        new_items = result.get("new_items", [])
        if new_items:
            # Build notification for high/medium relevance new items
            notable = [i for i in new_items if i.get("relevance") in ("high", "medium")]
            if notable:
                lines = [
                    f"**Funding Feed — {len(notable)} new opportunity/ies found:**\n"
                ]
                for item in notable[:8]:
                    emoji = "\U0001f534" if item["relevance"] == "high" else "\U0001f7e1"
                    line = f"{emoji} **{item['title']}** ({item['funder']})"
                    if item.get("deadline"):
                        line += f" — deadline {item['deadline']}"
                    if item.get("link"):
                        line += f"\n   {item['link']}"
                    lines.append(line)
                if len(notable) > 8:
                    lines.append(f"...and {len(notable) - 8} more.")
                await _notify("\n".join(lines))

            # Mark all new items as notified
            ids_to_mark = [i["id"] for i in new_items if "id" in i]
            await _mark_notified(ids_to_mark)

        detail = (
            f"feeds={result['feeds_checked']} "
            f"skipped={result['feeds_skipped']} "
            f"entries={result['total_entries']} "
            f"new={result['new_opportunities']} "
            f"high={result['high_relevance']}"
        )
        log.info("funding_feed_job: complete in %dms — %s", duration_ms, detail)
        await _audit_end("funding_feed", duration_ms, detail)

    except Exception as exc:
        duration_ms = int((time.monotonic() - t0) * 1000)
        log.exception("funding_feed_job: unhandled error after %dms: %s", duration_ms, exc)
        await _audit_end("funding_feed", duration_ms, f"error={exc!r}")


# ── Job 7 — overdue_check_job ──────────────────────────────────────────────────


async def _overdue_check_job() -> None:
    """
    Run daily at 08:30. Marks overdue deadlines and posts a warning to web chat
    if any are found.
    """
    import time as _time
    t0 = _time.monotonic()
    await _audit_start("overdue_check")
    log.info("overdue_check_job: starting")

    try:
        from app.skills.deadline_tracker import check_overdue
        overdue = await check_overdue()

        duration_ms = int((_time.monotonic() - t0) * 1000)

        if not overdue:
            log.info("overdue_check_job: no overdue deadlines (duration=%dms)", duration_ms)
            await _audit_end("overdue_check", duration_ms, "result=none")
            return

        lines = [f"\u26a0\ufe0f **{len(overdue)} overdue deadline(s):**\n"]
        for d in overdue[:10]:
            lines.append(f"\U0001f534 **{d['title']}** — was due {d['due_date']} ({d['category']})")
        if len(overdue) > 10:
            lines.append(f"...and {len(overdue) - 10} more. See /deadlines for the full list.")
        lines.append("\nVisit [/deadlines](/deadlines) to update or defer these items.")
        msg = "\n".join(lines)

        await _notify(msg)
        log.info("overdue_check_job: reported %d overdue (duration=%dms)", len(overdue), duration_ms)
        await _audit_end("overdue_check", duration_ms, f"overdue={len(overdue)}")

    except Exception as exc:
        duration_ms = int((_time.monotonic() - t0) * 1000)
        log.exception("overdue_check_job: error after %dms: %s", duration_ms, exc)
        await _audit_end("overdue_check", duration_ms, f"error={exc!r}")


# ── Job 8 — session_archive_job ───────────────────────────────────────────────


async def _session_archive_job() -> None:
    """
    Daily at 03:00 (dream mode) — compress old session_log directories.

    NOT wake-gated: this is a housekeeping task that runs during dream mode.

    Flow:
      1. Scan data/memory/session_log/ for JSON files older than
         settings.session_archive_days (default 90).
      2. Group files by month (YYYY-MM).
      3. For each month, create a .tar.gz in data/memory/session_archive/.
      4. Delete original JSON files after successful archive.
      5. Log results to audit_log.
    """
    import shutil
    import tarfile
    from collections import defaultdict
    from datetime import datetime, timedelta

    t0 = time.monotonic()
    await _audit_start("session_archive")
    log.info("session_archive_job: starting")

    try:
        from pathlib import Path

        session_log_dir = Path(settings.memory_path) / "session_log"
        archive_dir = Path(settings.memory_path) / "session_archive"

        if not session_log_dir.exists():
            duration_ms = int((time.monotonic() - t0) * 1000)
            log.info("session_archive_job: session_log dir does not exist — nothing to do")
            await _audit_end("session_archive", duration_ms, "result=no_session_log_dir")
            return

        archive_dir.mkdir(parents=True, exist_ok=True)

        cutoff = datetime.now() - timedelta(days=settings.session_archive_days)
        cutoff_ts = cutoff.timestamp()

        # ── Collect eligible files grouped by month ──────────────────────────
        month_files: dict[str, list[Path]] = defaultdict(list)
        for f in session_log_dir.iterdir():
            if not f.is_file():
                continue
            if f.stat().st_mtime >= cutoff_ts:
                continue
            month_key = _extract_month_key(f)
            month_files[month_key].append(f)

        if not month_files:
            duration_ms = int((time.monotonic() - t0) * 1000)
            log.info("session_archive_job: no files older than %d days", settings.session_archive_days)
            await _audit_end("session_archive", duration_ms, "result=none_eligible")
            return

        # ── Archive each month bucket ────────────────────────────────────────
        total_archived = 0
        total_deleted = 0
        months_processed = 0

        for month_key in sorted(month_files.keys()):
            files = month_files[month_key]
            archive_name = f"sessions_{month_key}.tar.gz"
            archive_path = archive_dir / archive_name
            tmp_path = archive_dir / f".tmp_{archive_name}"

            try:
                existing_members: set[str] = set()
                if archive_path.exists():
                    with tarfile.open(archive_path, "r:gz") as existing_tar:
                        existing_members = {m.name for m in existing_tar.getmembers()}

                with tarfile.open(tmp_path, "w:gz") as tar:
                    if archive_path.exists():
                        with tarfile.open(archive_path, "r:gz") as old_tar:
                            for member in old_tar.getmembers():
                                tar.addfile(member, old_tar.extractfile(member))

                    added = 0
                    for f in files:
                        arcname = f.name
                        if arcname in existing_members:
                            log.debug("session_archive_job: %s already in archive — skipping", arcname)
                            continue
                        tar.add(str(f), arcname=arcname)
                        added += 1

                shutil.move(str(tmp_path), str(archive_path))

                deleted = 0
                for f in files:
                    try:
                        f.unlink()
                        deleted += 1
                    except OSError as exc:
                        log.warning("session_archive_job: failed to delete %s: %s", f, exc)

                total_archived += added
                total_deleted += deleted
                months_processed += 1
                log.info(
                    "session_archive_job: %s — archived %d files, deleted %d originals",
                    archive_name, added, deleted,
                )

            except Exception as exc:
                log.exception("session_archive_job: failed to create archive %s: %s", archive_name, exc)
                if tmp_path.exists():
                    try:
                        tmp_path.unlink()
                    except OSError:
                        pass

        duration_ms = int((time.monotonic() - t0) * 1000)
        detail = (
            f"months={months_processed} "
            f"archived={total_archived} "
            f"deleted={total_deleted}"
        )
        log.info("session_archive_job: complete in %dms — %s", duration_ms, detail)
        await _audit_end("session_archive", duration_ms, detail)

    except Exception as exc:
        duration_ms = int((time.monotonic() - t0) * 1000)
        log.exception("session_archive_job: unhandled error after %dms: %s", duration_ms, exc)
        await _audit_end("session_archive", duration_ms, f"error={exc!r}")
