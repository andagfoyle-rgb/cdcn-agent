"""
funding_feed_parser — Content extraction and parsing for the FundingFeed skill.

Handles RSS/Atom feed parsing, HTML page scraping, article extraction,
deadline/amount extraction, funding-signal detection, and relevance scoring.
"""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin

import feedparser
import httpx
from bs4 import BeautifulSoup, Tag

log = logging.getLogger(__name__)

# ── CDCN relevance keywords ──────────────────────────────────────────────────
# Grouped by priority.

_HIGH_RELEVANCE = [
    "community development", "rural regeneration", "heritage",
    "community empowerment", "shetland", "highlands and islands",
    "fragile area", "island", "asset transfer", "community ownership",
    "development officer", "core funding", "third sector",
    "community benefit", "community energy", "recreation",
    "village hall", "community hub", "capacity building",
]

_MEDIUM_RELEVANCE = [
    "scotland", "scottish", "charity", "voluntary",
    "cost of living", "poverty", "wellbeing",
    "capital project", "renovation", "building",
    "small grant", "community group", "social enterprise",
]

_HIGH_RE = re.compile("|".join(re.escape(k) for k in _HIGH_RELEVANCE), re.IGNORECASE)
_MED_RE = re.compile("|".join(re.escape(k) for k in _MEDIUM_RELEVANCE), re.IGNORECASE)

# ── Deadline extraction ──────────────────────────────────────────────────────

_DEADLINE_PATTERNS = [
    # "deadline: 13 May 2026", "closes 1 June 2026", "closing date: ..."
    re.compile(
        r"(?:deadline|clos(?:es|ing)\s*(?:date)?)\s*[:–-]?\s*"
        r"(\d{1,2}\s+\w+\s+\d{4})",
        re.IGNORECASE,
    ),
    # ISO-ish: "2026-05-13"
    re.compile(r"(\d{4}-\d{2}-\d{2})"),
]

_DATE_FORMATS = ["%d %B %Y", "%d %b %Y", "%Y-%m-%d"]


def _extract_deadline(text: str) -> str | None:
    """Try to extract a deadline date from text.  Returns YYYY-MM-DD or None."""
    for pat in _DEADLINE_PATTERNS:
        m = pat.search(text)
        if m:
            raw = m.group(1).strip()
            for fmt in _DATE_FORMATS:
                try:
                    dt = datetime.strptime(raw, fmt)
                    return dt.strftime("%Y-%m-%d")
                except ValueError:
                    continue
    return None


# ── Amount extraction ─────────────────────────────────────────────────────────

_AMOUNT_RE = re.compile(r"£[\d,]+(?:\s*[-–]\s*£[\d,]+)?")


def _extract_amount(text: str) -> str | None:
    m = _AMOUNT_RE.search(text)
    return m.group(0) if m else None


# ── Funding-signal gate ──────────────────────────────────────────────────────

_STRONG_FUNDING_SIGNALS = [
    r"\bopen\s+for\s+applications?\b",
    r"\bcall\s+for\s+(?:proposals?|applications?|expressions)\b",
    r"\bexpressions?\s+of\s+interest\b",
    r"\bapply\s+(?:now|by|before|today)\b",
    r"\bgrant(?:s)?\s+(?:available|open|scheme|programme|program|fund)\b",
    r"\bfunding\s+(?:available|open|opportunity|opportunities|round|call|programme|program|scheme)\b",
    r"\bnow\s+(?:open|accepting|inviting)\b",
    r"\beligib(?:le|ility)\s+(?:criteria|to\s+apply|organisations?|groups?)\b",
    r"\bdeadline\s*[:–-]",
    r"\bclos(?:es|ing)\s+(?:date|soon)\b",
    r"\bawards?\s+(?:of\s+up\s+to|scheme|available)\b",
    r"£[\d,]+\s*[-–]\s*£[\d,]+",
]

_WEAK_FUNDING_SIGNALS = [
    r"\bgrant(?:s)?\b", r"\bfunding\b", r"\bscheme\b", r"\bprogramme\b",
    r"\bapplication(?:s)?\b", r"\bapply\b", r"\bbursary\b", r"\bbursaries\b",
]

_STRONG_RE = re.compile("|".join(_STRONG_FUNDING_SIGNALS), re.IGNORECASE)
_WEAK_RE = re.compile("|".join(_WEAK_FUNDING_SIGNALS), re.IGNORECASE)


def _has_funding_signal(title: str, summary: str) -> bool:
    """Return True if the entry looks like an actual funding opportunity.

    Requires either one strong signal anywhere, or at least one weak signal
    in the title itself (a blog post about a person rarely has "grant" or
    "funding" in the title).
    """
    combined = f"{title} {summary}"
    if _STRONG_RE.search(combined):
        return True
    if _WEAK_RE.search(title):
        return True
    return False


# ── Relevance scoring ────────────────────────────────────────────────────────

def _score_relevance(title: str, summary: str) -> str:
    """Return 'high', 'medium', or 'low' based on keyword matches."""
    combined = f"{title} {summary}"
    high_hits = len(_HIGH_RE.findall(combined))
    med_hits = len(_MED_RE.findall(combined))
    score = high_hits * 3 + med_hits * 1
    if score >= 3:
        return "high"
    elif score >= 1:
        return "medium"
    return "low"


# ── Feed entry helpers ───────────────────────────────────────────────────────

def _make_guid(entry: Any, feed_url: str) -> str:
    """Generate a stable GUID for a feed entry."""
    guid = getattr(entry, "id", "") or ""
    if guid:
        return guid
    link = getattr(entry, "link", "") or ""
    title = getattr(entry, "title", "") or ""
    raw = f"{feed_url}|{link}|{title}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _entry_published(entry: Any) -> str:
    """Extract published date as ISO string, or empty."""
    pub = getattr(entry, "published_parsed", None)
    if pub:
        try:
            return datetime(*pub[:6], tzinfo=timezone.utc).isoformat()
        except Exception:
            pass
    return getattr(entry, "published", "") or ""


# ── Page-change detection ───────────────────────────────────────────────────

async def _get_page_hash(url: str) -> str | None:
    """Return the stored content hash for *url*, or None if unseen."""
    import aiosqlite
    from app.config import settings
    async with aiosqlite.connect(settings.audit_log_path) as conn:
        from app.storage.audit_log import _ensure_schema
        await _ensure_schema(conn)
        async with conn.execute(
            "SELECT content_hash FROM page_hashes WHERE url = ?", (url,)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else None


async def _set_page_hash(url: str, content_hash: str) -> None:
    """Insert or update the content hash for *url*."""
    import aiosqlite
    from app.config import settings
    ts = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(settings.audit_log_path) as conn:
        from app.storage.audit_log import _ensure_schema
        await _ensure_schema(conn)
        await conn.execute(
            "INSERT INTO page_hashes (url, content_hash, last_checked)"
            " VALUES (?, ?, ?)"
            " ON CONFLICT(url) DO UPDATE SET content_hash=excluded.content_hash,"
            " last_checked=excluded.last_checked",
            (url, content_hash, ts),
        )
        await conn.commit()


# ── HTML article extraction ─────────────────────────────────────────────────

_ARTICLE_SELECTORS = [
    "article",
    "[class*='news-item']",
    "[class*='news_item']",
    "[class*='post-item']",
    "[class*='post_item']",
    "[class*='card']",
    "[class*='listing']",
    ".post",
    ".entry",
    ".item",
]


def _extract_articles(
    soup: BeautifulSoup, base_url: str,
) -> list[dict[str, str]]:
    """Extract article blocks from a news/funding page.

    Returns a list of dicts with keys: title, link, summary.
    Tries structured selectors first, then falls back to link extraction.
    """
    articles: list[dict[str, str]] = []

    # -- Tier 1: structured article blocks --
    blocks: list[Tag] = []
    for sel in _ARTICLE_SELECTORS:
        blocks = soup.select(sel)
        if len(blocks) >= 2:
            break
    else:
        blocks = []

    if blocks:
        seen_titles: set[str] = set()
        for block in blocks:
            for svg in block.find_all("svg"):
                svg.decompose()

            title = ""
            link = ""

            heading = block.find(["h1", "h2", "h3", "h4"])
            if heading:
                title = heading.get_text(strip=True)
                inner_a = heading.find("a", href=True)
                if inner_a:
                    link = urljoin(base_url, inner_a["href"])

            if not title:
                for cls in ("title", "card-title", "card_title", "heading"):
                    title_el = block.find(class_=lambda c: c and cls in c)
                    if title_el:
                        title = title_el.get_text(strip=True)
                        break

            link_tag = block.find("a", href=True)
            if not link and link_tag:
                link = urljoin(base_url, link_tag["href"])
            if not title and link_tag:
                title = (
                    link_tag.get("aria-label", "")
                    or link_tag.get_text(strip=True)
                )
            if not title:
                continue

            title_key = title.lower().strip()
            if title_key in seen_titles:
                continue
            seen_titles.add(title_key)

            paras = block.find_all("p")
            summary = " ".join(p.get_text(strip=True) for p in paras)
            if not summary:
                summary = block.get_text(" ", strip=True)
            if summary.startswith(title):
                summary = summary[len(title):].strip()

            articles.append({"title": title, "link": link, "summary": summary})
        return articles

    # -- Tier 2: fall back to link-based extraction --
    main = soup.find("main") or soup.find("div", role="main") or soup.body
    if not main:
        return []

    seen_links: set[str] = set()
    for a_tag in main.find_all("a", href=True):
        href = urljoin(base_url, a_tag["href"])
        if href in seen_links or "#" == a_tag["href"] or href == base_url:
            continue
        text = a_tag.get_text(strip=True)
        if len(text) < 10:
            continue
        seen_links.add(href)

        parent = a_tag.parent
        summary = ""
        if parent:
            sibling_p = parent.find_next_sibling("p")
            if sibling_p:
                summary = sibling_p.get_text(strip=True)
            elif parent.name != "a":
                summary = parent.get_text(" ", strip=True)

        articles.append({"title": text, "link": href, "summary": summary})

    return articles


# ── Page scraper ────────────────────────────────────────────────────────────

_SCRAPE_HEADERS = {
    "User-Agent": "CDCN-Agent/1.0 (Community Development Company of Nesting; +https://nesting.community)",
    "Accept": "text/html, application/xhtml+xml, */*;q=0.1",
}


async def _scrape_single_page(
    feed_cfg: dict[str, str],
) -> list[dict[str, Any]]:
    """
    Scrape a funder's news/funding page and return opportunity dicts.
    Same return format as _fetch_single_feed() for pipeline compatibility.
    """
    url = feed_cfg["url"]
    funder = feed_cfg["funder"]

    try:
        async with httpx.AsyncClient(
            timeout=20.0, follow_redirects=True, headers=_SCRAPE_HEADERS
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            html = resp.text
    except Exception as exc:
        log.warning("Page fetch failed for %s (%s): %s", funder, url, exc)
        return []

    # -- Change detection --
    content_hash = hashlib.sha256(html.encode()).hexdigest()
    prev_hash = await _get_page_hash(url)
    if prev_hash == content_hash:
        log.debug("Page unchanged for %s (%s), skipping parse", funder, url)
        return []

    # -- Parse & extract --
    soup = BeautifulSoup(html, "lxml")
    raw_articles = _extract_articles(soup, url)

    if not raw_articles:
        log.info("No article blocks found on %s (%s)", funder, url)
        await _set_page_hash(url, content_hash)
        return []

    opportunities: list[dict[str, Any]] = []
    for art in raw_articles:
        title = art["title"][:200]
        link = art["link"]
        summary = art["summary"]
        if len(summary) > 1000:
            summary = summary[:997] + "..."

        if not _has_funding_signal(title, summary):
            log.debug("Skipping non-funding scraped entry: %s", title[:80])
            continue

        guid = hashlib.sha256(f"{url}|{link}|{title}".encode()).hexdigest()[:32]
        combined_text = f"{title} {summary} {feed_cfg.get('focus', '')} {feed_cfg.get('notes', '')}"
        deadline = _extract_deadline(combined_text)
        amount = _extract_amount(combined_text)
        relevance = _score_relevance(title, summary)

        opportunities.append({
            "guid": guid,
            "funder": funder,
            "title": title,
            "link": link,
            "summary": summary,
            "published": "",
            "deadline": deadline,
            "amount": amount,
            "eligibility": "",
            "relevance": relevance,
            "feed_url": url,
        })

    await _set_page_hash(url, content_hash)
    log.info(
        "Scraped %s (%s): %d articles found, %d passed funding filter",
        funder, url, len(raw_articles), len(opportunities),
    )
    return opportunities


# ── RSS/Atom feed fetcher ───────────────────────────────────────────────────

_FEED_HEADERS = {
    "User-Agent": "CDCN-Agent/1.0 (Community Development Company of Nesting; +https://nesting.community)",
    "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*;q=0.1",
}


async def _fetch_single_feed(
    feed_cfg: dict[str, str],
) -> list[dict[str, Any]]:
    """
    Parse a single RSS/Atom feed and return a list of opportunity dicts.
    Uses httpx to fetch with proper Accept headers, then feedparser to parse.
    """
    url = feed_cfg["url"]
    funder = feed_cfg["funder"]

    try:
        async with httpx.AsyncClient(
            timeout=15.0, follow_redirects=True, headers=_FEED_HEADERS
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            content = resp.text
    except Exception as exc:
        log.warning("HTTP fetch failed for %s (%s): %s", funder, url, exc)
        return []

    try:
        parsed = feedparser.parse(content)
    except Exception as exc:
        log.warning("feedparser error for %s (%s): %s", funder, url, exc)
        return []

    if not parsed.entries:
        if parsed.bozo:
            log.info(
                "No entries from %s (%s): %s",
                funder, url, parsed.bozo_exception,
            )
        else:
            log.debug("Empty feed from %s (%s)", funder, url)
        return []

    opportunities: list[dict[str, Any]] = []
    for entry in parsed.entries:
        title = (getattr(entry, "title", "") or "").strip()
        link = (getattr(entry, "link", "") or "").strip()
        summary_raw = (
            getattr(entry, "summary", "")
            or getattr(entry, "description", "")
            or ""
        ).strip()
        summary = re.sub(r"<[^>]+>", " ", summary_raw)
        summary = re.sub(r"\s+", " ", summary).strip()
        if len(summary) > 1000:
            summary = summary[:997] + "..."

        if not _has_funding_signal(title, summary):
            log.debug("Skipping non-funding entry: %s", title[:80])
            continue

        combined_text = f"{title} {summary} {feed_cfg.get('focus', '')} {feed_cfg.get('notes', '')}"
        deadline = _extract_deadline(combined_text)
        amount = _extract_amount(combined_text)
        relevance = _score_relevance(title, summary)

        opportunities.append({
            "guid": _make_guid(entry, url),
            "funder": funder,
            "title": title,
            "link": link,
            "summary": summary,
            "published": _entry_published(entry),
            "deadline": deadline,
            "amount": amount,
            "eligibility": "",
            "relevance": relevance,
            "feed_url": url,
        })

    return opportunities
