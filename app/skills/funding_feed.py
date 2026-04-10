"""
FundingFeedSkill — RSS/Atom feed aggregator for funding opportunities.

Monitors feeds listed in CDCN_Funding_RSS_Database_2026.csv, stores new
entries in the funding_opportunities SQLite table, and surfaces relevant
opportunities to CDCN staff via the agent.

Architecture notes
──────────────────
  * Feed list is loaded from a CSV file (configurable path).
  * "Native RSS" feeds are parsed with feedparser.
  * "Target URL (Needs Generator)" pages are scraped with BeautifulSoup;
    a content-hash table (page_hashes) avoids reprocessing unchanged pages.
  * Deduplication is by GUID (or link hash if no GUID).  The SQLite table
    has a UNIQUE index on guid — INSERT OR IGNORE handles duplicates.
  * Relevance scoring uses keyword matching against CDCN's profile.
  * The skill exposes two main entry points:
      fetch_all()  — called by the scheduler; returns new opportunities
      run()        — called by the agentic loop; returns a formatted summary

Sub-modules
───────────
  funding_feed_db.py      — all database operations (CRUD, storage, queries)
  funding_feed_parser.py  — content extraction, parsing, scraping, scoring
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from app.skills.base import BaseSkill, SkillResult

from app.skills.funding_feed_db import (
    _DEFAULT_CSV,
    _db_feed_count,
    _format_opportunity,
    _get_unnotified,
    _import_csv_to_db,
    _load_feed_config_from_db,
    _store_opportunities,
    get_recent_opportunities,
    update_feed_status,
    # Re-export public API so callers can still do
    # ``from app.skills.funding_feed import list_feeds`` etc.
    list_feeds,
    add_feed,
    update_feed,
    delete_feed,
)

from app.skills.funding_feed_parser import (
    _fetch_single_feed,
    _scrape_single_page,
)

log = logging.getLogger(__name__)

# Re-export names that other modules might reference at the top level.
__all__ = [
    "FundingFeedSkill",
    "fetch_all_feeds",
    "list_feeds",
    "add_feed",
    "update_feed",
    "delete_feed",
    "get_recent_opportunities",
]


# ── Skill class ──────────────────────────────────────────────────────────────


class FundingFeedSkill(BaseSkill):
    """
    RSS/Atom funding feed aggregator for CDCN Agent.

    Agentic loop usage:
      {"skill": "funding_feed", "action": "search", "query": "heritage"}
      {"skill": "funding_feed", "action": "latest"}
      {"skill": "funding_feed", "action": "fetch"}  — force a fresh pull
    """

    name = "funding_feed"
    description = (
        "Monitor RSS feeds from UK/Scottish funders and surface relevant "
        "funding opportunities for CDCN."
    )

    def __init__(self, csv_path: Path | None = None) -> None:
        self._csv_path = csv_path or _DEFAULT_CSV

    async def run(self, **kwargs) -> SkillResult:
        action = kwargs.get("action", "latest")
        try:
            if action == "fetch":
                return await self._do_fetch()
            elif action == "search":
                return await self._do_search(kwargs.get("query", ""))
            else:
                return await self._do_latest(kwargs)
        except Exception as exc:
            log.exception("FundingFeedSkill error: %s", exc)
            return SkillResult(success=False, error=str(exc))

    async def _do_fetch(self) -> SkillResult:
        """Force-fetch all feeds and store new opportunities."""
        result = await fetch_all_feeds(self._csv_path)
        return SkillResult(
            success=True,
            output=result["summary"],
            metadata=result,
        )

    async def _do_search(self, query: str) -> SkillResult:
        """Search stored opportunities by keyword."""
        if not query:
            return SkillResult(success=False, error="No search query provided.")
        opps = await get_recent_opportunities(n=50, days=90)
        query_lower = query.lower()
        matches = [
            o for o in opps
            if query_lower in (o.get("title", "") + " " + o.get("summary", "") + " " + o.get("funder", "")).lower()
        ]
        if not matches:
            return SkillResult(
                success=True,
                output=f"No funding opportunities matching '{query}' found in the last 90 days.",
                metadata={"hits": 0},
            )
        blocks = [_format_opportunity(o) for o in matches[:15]]
        output = f"**{len(matches)} funding opportunity/ies matching '{query}':**\n\n" + "\n\n---\n\n".join(blocks)
        return SkillResult(
            success=True,
            output=output,
            metadata={"hits": len(matches)},
        )

    async def _do_latest(self, kwargs: dict) -> SkillResult:
        """Return latest opportunities, optionally filtered."""
        relevance = kwargs.get("relevance")
        days = int(kwargs.get("days", 7))
        n = int(kwargs.get("n", 15))
        opps = await get_recent_opportunities(n=n, relevance=relevance, days=days)
        if not opps:
            return SkillResult(
                success=True,
                output=f"No funding opportunities found in the last {days} days.",
                metadata={"hits": 0},
            )
        blocks = [_format_opportunity(o) for o in opps]
        output = f"**{len(opps)} recent funding opportunity/ies (last {days} days):**\n\n" + "\n\n---\n\n".join(blocks)
        return SkillResult(
            success=True,
            output=output,
            metadata={"hits": len(opps)},
        )


# ── Top-level fetch function (used by scheduler) ─────────────────────────────


async def fetch_all_feeds(
    csv_path: Path | None = None,
) -> dict[str, Any]:
    """
    Fetch all configured feeds — RSS and scraped pages — and store new
    opportunities.  Returns a summary dict.

    On first run, imports feeds from CSV into the database.  Subsequent
    runs read from the DB so that add/remove/enable changes take effect.
    """
    t0 = time.monotonic()

    # Bootstrap: import CSV into DB if the table is empty
    count = await _db_feed_count()
    if count == 0:
        await _import_csv_to_db(csv_path)

    feeds = await _load_feed_config_from_db()
    if not feeds:
        return {
            "rss_checked": 0,
            "pages_scraped": 0,
            "new_opportunities": 0,
            "total_entries": 0,
            "high_relevance": 0,
            "summary": "No feed configuration found.",
            "new_items": [],
        }

    all_opps: list[dict[str, Any]] = []
    rss_checked = 0
    pages_scraped = 0

    for feed_cfg in feeds:
        feed_id = feed_cfg.get("id")
        try:
            if "Needs Generator" in feed_cfg.get("feed_type", ""):
                pages_scraped += 1
                opps = await _scrape_single_page(feed_cfg)
            else:
                rss_checked += 1
                opps = await _fetch_single_feed(feed_cfg)
            all_opps.extend(opps)
            status = f"ok:{len(opps)} entries"
        except Exception as exc:
            log.warning("Feed fetch error for %s: %s", feed_cfg.get("funder"), exc)
            status = f"error:{exc}"
        if feed_id:
            try:
                await update_feed_status(feed_id, status)
            except Exception:
                pass

    new_count = await _store_opportunities(all_opps)
    high_count = sum(1 for o in all_opps if o["relevance"] == "high")
    duration_ms = int((time.monotonic() - t0) * 1000)

    # Get the actual new items for notification purposes
    new_items = await _get_unnotified() if new_count > 0 else []

    summary = (
        f"Funding feed scan complete in {duration_ms}ms: "
        f"{rss_checked} RSS feeds checked, {pages_scraped} pages scraped, "
        f"{len(all_opps)} entries parsed, {new_count} new, "
        f"{high_count} high-relevance."
    )
    log.info(summary)

    return {
        "rss_checked": rss_checked,
        "pages_scraped": pages_scraped,
        "new_opportunities": new_count,
        "total_entries": len(all_opps),
        "high_relevance": high_count,
        "duration_ms": duration_ms,
        "summary": summary,
        "new_items": new_items,
    }
