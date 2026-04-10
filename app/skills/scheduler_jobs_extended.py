"""
scheduler_jobs_extended -- Larger scheduled job functions for the CDCN Agent.

Contains heartbeat, weekly digest, and monthly governance jobs.
Session archive job has been moved to scheduler_jobs.py to balance load.
"""
from __future__ import annotations

import logging
import shutil
import time
from collections import Counter
from datetime import datetime, timedelta, timezone

from app.config import settings
from app.skills.scheduler_helpers import (
    HEARTBEAT_OK,
    _with_retry,
    _is_wake,
    _today,
    _notify,
    _audit_start,
    _audit_end,
    _is_heartbeat_ok,
    _build_doc_context,
    _load_heartbeat_instructions,
    _funding_deadlines_within_days,
    _read_session_log_today,
    _docs_indexed_since,
)

log = logging.getLogger(__name__)


# ── Job 1 -- heartbeat_job ──────────────────────────────────────────────────────


@_with_retry(max_retries=2, backoff_base=10.0)
async def _heartbeat_job() -> None:
    """
    Periodic heartbeat -- adapts OpenCLAW's HEARTBEAT.md pattern.

    Flow:
      1. Gate: skip if not in wake state.
      2. Read heartbeat.md instructions (the standing checklist).
      3. Run the indexer and deadline check to gather fresh context.
      4. Build an LLM prompt: heartbeat.md + runtime context.
      5. Call LLM. If reply is HEARTBEAT_OK -> log only, no notification.
      6. Otherwise send the report to Discord + Telegram.
      7. Log start / completion with duration to audit_log.
    """
    if not _is_wake():
        log.debug("heartbeat_job: not in wake state — skipped")
        return

    t0 = time.monotonic()
    await _audit_start("heartbeat")
    log.info("heartbeat_job: starting")

    try:
        # ── Gather context ────────────────────────────────────────────────────
        instructions = _load_heartbeat_instructions()
        doc_context = _build_doc_context()
        now_str = datetime.now(timezone.utc).strftime("%A %d %B %Y, %H:%M UTC")

        # Run indexer to pick up new files
        indexer_summary = ""
        try:
            from app.skills.indexer import IndexerSkill
            idx_result = await IndexerSkill().run(folder=settings.watched_folder)
            if idx_result.success and idx_result.metadata:
                m = idx_result.metadata
                indexer_summary = (
                    f"Indexer run: {m.get('indexed', 0)} indexed, "
                    f"{m.get('skipped', 0)} skipped, "
                    f"{m.get('failed', 0)} failed."
                )
            elif idx_result.error:
                indexer_summary = f"Indexer error: {idx_result.error}"
        except Exception as exc:
            indexer_summary = f"Indexer unavailable: {exc}"
            log.warning("heartbeat_job: indexer failed: %s", exc)

        # Check funding deadlines (14-day window)
        deadline_alerts = _funding_deadlines_within_days(14)
        deadline_text = (
            "\n".join(deadline_alerts)
            if deadline_alerts
            else "No funding deadlines within 14 days."
        )

        # Check RSS feed opportunities (last 3 days, high relevance)
        rss_text = ""
        try:
            from app.skills.funding_feed import get_recent_opportunities
            rss_opps = await get_recent_opportunities(n=5, relevance="high", days=3)
            if rss_opps:
                rss_lines = [f"**{len(rss_opps)} high-relevance RSS funding opportunity/ies (last 3 days):**"]
                for o in rss_opps:
                    line = f"- {o['title']} ({o['funder']})"
                    if o.get("deadline"):
                        line += f" — deadline {o['deadline']}"
                    rss_lines.append(line)
                rss_text = "\n".join(rss_lines)
        except Exception as exc:
            log.debug("heartbeat: RSS feed check failed: %s", exc)

        # ── Build LLM prompt ──────────────────────────────────────────────────
        system_prompt = (
            "You are CDCN Agent's heartbeat worker. "
            "Follow the heartbeat instructions strictly. "
            "Report only what genuinely needs attention. "
            f"If nothing needs attention, reply with exactly: {HEARTBEAT_OK}"
        )

        user_content_parts = [
            f"**Current time:** {now_str}",
            "",
        ]
        if instructions:
            user_content_parts += [
                "## Heartbeat Instructions (heartbeat.md)",
                instructions,
                "",
            ]
        # Disk space check
        disk_warning = ""
        try:
            usage = shutil.disk_usage("/")
            free_gb = usage.free / (1024 ** 3)
            total_gb = usage.total / (1024 ** 3)
            pct_used = (usage.used / usage.total) * 100
            if free_gb < 2.0:
                disk_warning = (
                    f"**\u26a0 DISK SPACE CRITICAL:** {free_gb:.1f} GB free "
                    f"of {total_gb:.1f} GB ({pct_used:.0f}% used). "
                    f"Immediate action required."
                )
            elif free_gb < 5.0:
                disk_warning = (
                    f"**Disk space low:** {free_gb:.1f} GB free "
                    f"of {total_gb:.1f} GB ({pct_used:.0f}% used)."
                )
        except Exception as exc:
            log.debug("heartbeat: disk check failed: %s", exc)

        user_content_parts += [
            "## Runtime Context",
            doc_context,
            "",
            f"**Document indexer result:** {indexer_summary}",
        ]
        if disk_warning:
            user_content_parts += ["", "## Disk Space", disk_warning]
        user_content_parts += [
            "",
            "## Funding Deadlines",
            deadline_text,
        ]
        if rss_text:
            user_content_parts += ["", "## RSS Funding Opportunities", rss_text]
        user_content = "\n".join(user_content_parts)

        # ── Call LLM ─────────────────────────────────────────────────────────
        from app.llm_client import llm_client
        response = await llm_client.chat(
            [{"role": "user", "content": user_content}],
            system_prompt=system_prompt,
            skill_used="heartbeat",
        )

        duration_ms = int((time.monotonic() - t0) * 1000)

        # ── Suppress or notify ────────────────────────────────────────────────
        if _is_heartbeat_ok(response):
            log.info("heartbeat_job: HEARTBEAT_OK — nothing to report (duration=%dms)", duration_ms)
            await _audit_end("heartbeat", duration_ms, "result=ok")
        else:
            log.info("heartbeat_job: sending report (duration=%dms)", duration_ms)
            report = f"**CDCN Agent — Heartbeat Report**\n\n{response.strip()}"
            await _notify(report)
            await _audit_end("heartbeat", duration_ms, "result=reported")

    except Exception as exc:
        duration_ms = int((time.monotonic() - t0) * 1000)
        log.exception("heartbeat_job: unhandled error after %dms: %s", duration_ms, exc)
        await _audit_end("heartbeat", duration_ms, f"error={exc!r}")


# ── Job 4 -- weekly_digest_job ──────────────────────────────────────────────────


async def _weekly_digest_job() -> None:
    """
    Monday 07:15 -- post a weekly digest to Discord.

    Contents:
      - Summary of recent board minutes (via SearchSkill)
      - Funding deadlines in the next 60 days
      - Documents indexed in the past 7 days
    Wake-only.
    """
    if not _is_wake():
        log.debug("weekly_digest_job: not in wake state — skipped")
        return

    t0 = time.monotonic()
    await _audit_start("weekly_digest")
    log.info("weekly_digest_job: starting")

    try:
        from app.llm_client import llm_client
        from app.skills.search import SearchSkill

        # ── 1. Recent board minutes ───────────────────────────────────────────
        minutes_summary = ""
        try:
            search = SearchSkill()
            search_result = await search.run(query="board minutes decisions resolutions")
            if search_result.success and search_result.metadata.get("hits", 0) > 0:
                minutes_summary = search_result.output or ""
        except Exception as exc:
            log.warning("weekly_digest_job: minutes search failed: %s", exc)
            minutes_summary = "(board minutes search unavailable)"

        # ── 2. Funding deadlines -- next 60 days ─────────────────────────────
        deadline_alerts = _funding_deadlines_within_days(60)
        deadline_text = (
            "\n".join(f"- {a}" for a in deadline_alerts)
            if deadline_alerts
            else "- No funding deadlines in the next 60 days."
        )

        # ── 3. RSS funding opportunities (past 7 days) ──────────────────────
        rss_text = "- No RSS funding opportunities in the past 7 days."
        try:
            from app.skills.funding_feed import get_recent_opportunities
            rss_opps = await get_recent_opportunities(n=20, days=7)
            if rss_opps:
                high = [o for o in rss_opps if o.get("relevance") == "high"]
                med = [o for o in rss_opps if o.get("relevance") == "medium"]
                rss_lines = [f"- {len(rss_opps)} funding opportunity/ies found via RSS feeds:"]
                if high:
                    rss_lines.append(f"  - **{len(high)} high-relevance:**")
                    for o in high[:5]:
                        line = f"    - {o['title']} ({o['funder']})"
                        if o.get("deadline"):
                            line += f" — deadline {o['deadline']}"
                        rss_lines.append(line)
                if med:
                    rss_lines.append(f"  - {len(med)} medium-relevance")
                low_count = len(rss_opps) - len(high) - len(med)
                if low_count:
                    rss_lines.append(f"  - {low_count} low-relevance (filtered)")
                rss_text = "\n".join(rss_lines)
        except Exception as exc:
            log.warning("weekly_digest_job: RSS feed query failed: %s", exc)
            rss_text = "- RSS funding feed data unavailable."

        # ── 4. Documents indexed in the past 7 days ───────────────────────────
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        recent_docs = _docs_indexed_since(cutoff)
        if recent_docs:
            type_counts: Counter[str] = Counter(d.get("document_type", "other") for d in recent_docs)
            doc_lines = [f"- {len(recent_docs)} document(s) indexed in the past 7 days:"]
            for dt, n in sorted(type_counts.items()):
                doc_lines.append(f"  - {dt}: {n}")
            doc_text = "\n".join(doc_lines)
        else:
            doc_text = "- No new documents indexed in the past 7 days."

        # ── Ask LLM to write a clean digest ───────────────────────────────────
        user_content = (
            f"**Week ending:** {_today()}\n\n"
            f"## Board minutes / recent decisions\n\n{minutes_summary or '(no results)'}\n\n"
            f"## Funding deadlines (next 60 days)\n\n{deadline_text}\n\n"
            f"## RSS funding opportunities (past 7 days)\n\n{rss_text}\n\n"
            f"## Document activity (past 7 days)\n\n{doc_text}"
        )
        system_prompt = (
            "You are CDCN Agent. Produce a concise weekly digest (5-8 bullet points) "
            "for the board of a Scottish community development charity. "
            "Cover: documents indexed, any board decisions found, "
            "funding deadlines approaching, new RSS funding opportunities, "
            "and any outstanding follow-ups. "
            "Use plain English. Format as a Discord message with markdown."
        )

        digest = await llm_client.chat(
            [{"role": "user", "content": user_content}],
            system_prompt=system_prompt,
            skill_used="weekly_digest",
        )

        # ── Post to Discord ──────────────────────────────────────────────────
        discord_message = f"**CDCN Agent — Weekly Digest ({_today()})**\n\n{digest.strip()}"
        try:
            from app.state_manager import get_state_manager
            mgr = get_state_manager()
            if mgr._discord and settings.discord_status_channel_id:
                await mgr._discord.send_message(
                    settings.discord_status_channel_id, discord_message
                )
        except Exception as exc:
            log.warning("weekly_digest_job: Discord post failed: %s", exc)

        duration_ms = int((time.monotonic() - t0) * 1000)
        log.info("weekly_digest_job: complete in %dms", duration_ms)
        await _audit_end("weekly_digest", duration_ms)

    except Exception as exc:
        duration_ms = int((time.monotonic() - t0) * 1000)
        log.exception("weekly_digest_job: unhandled error after %dms: %s", duration_ms, exc)
        await _audit_end("weekly_digest", duration_ms, f"error={exc!r}")


# ── Job 5 -- monthly_governance_job ────────────────────────────────────────────


async def _monthly_governance_job() -> None:
    """
    1st of month, 08:00 -- governance document review reminder.

    Queries ChromaDB for policy documents, flags any whose indexed_at is
    older than 12 months, and posts a reminder to Discord and Telegram.
    """
    if not _is_wake():
        log.warning(
            "monthly_governance_job: running outside wake state — "
            "reminder will be sent anyway"
        )

    t0 = time.monotonic()
    await _audit_start("monthly_governance")
    log.info("monthly_governance_job: starting")

    try:
        from app.storage.vector_store import vector_store

        all_docs = vector_store.list_documents()
        policy_docs = [d for d in all_docs if d.get("document_type") == "policy"]

        month_label = datetime.now().strftime("%B %Y")
        cutoff = datetime.now(timezone.utc) - timedelta(days=365)

        stale: list[dict] = []
        for doc in policy_docs:
            ts_str = doc.get("indexed_at", "")
            if not ts_str:
                stale.append(doc)
                continue
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                if ts < cutoff:
                    stale.append(doc)
            except ValueError:
                stale.append(doc)

        if not policy_docs:
            body = (
                f"**Governance Review — {month_label}**\n\n"
                "No governance policy documents are currently indexed. "
                "Consider uploading safeguarding, finance, and other policies "
                "to the document archive."
            )
        elif not stale:
            body = (
                f"**Governance Review — {month_label}**\n\n"
                f"All {len(policy_docs)} indexed governance policy document(s) "
                "were indexed within the past 12 months. No immediate review required."
            )
        else:
            lines = [
                f"**Governance Review Reminder — {month_label}**\n",
                f"{len(stale)} of {len(policy_docs)} policy document(s) "
                "have not been refreshed in the past 12 months and may need review:\n",
            ]
            for doc in stale[:20]:
                source = doc.get("source_file", doc.get("doc_id", "unknown"))
                lines.append(f"- `{source}`")
            if len(stale) > 20:
                lines.append(f"- ... and {len(stale) - 20} more.")
            lines.append(
                "\nPlease review these documents and re-upload updated versions "
                "to the document archive."
            )
            body = "\n".join(lines)

        try:
            from app.skills.memory import MemorySkill
            memory = MemorySkill()
            journal_content = (
                f"## Governance Review Reminder — {month_label}\n\n{body}"
            )
            memory.write_journal(date=_today(), content=journal_content)
        except Exception as exc:
            log.warning("monthly_governance_job: journal write failed: %s", exc)

        await _notify(body)

        duration_ms = int((time.monotonic() - t0) * 1000)
        log.info("monthly_governance_job: complete in %dms (%d stale)", duration_ms, len(stale))
        await _audit_end(
            "monthly_governance",
            duration_ms,
            f"policy_docs={len(policy_docs)} stale={len(stale)}",
        )

    except Exception as exc:
        duration_ms = int((time.monotonic() - t0) * 1000)
        log.exception("monthly_governance_job: unhandled error after %dms: %s", duration_ms, exc)
        await _audit_end("monthly_governance", duration_ms, f"error={exc!r}")
