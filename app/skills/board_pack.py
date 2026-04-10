"""
BoardPackSkill -- generate a complete board pack for CDCN board meetings.

Combines multiple data sources into a single comprehensive document:
  1. Cover page (CDCN branding, meeting date, type)
  2. Draft agenda (customised to meeting type)
  3. Previous minutes (most recent board minutes via vector search)
  4. Action points tracker (open/in-progress from action_tracker)
  5. Upcoming deadlines (next 60 days from deadline_tracker)
  6. Funding pipeline summary (if funding data available)
  7. Papers for decision (additional papers retrieved by name)

Outputs both markdown and DOCX (via docx_converter).
Saves to skills_config/drafts/board_pack_YYYYMMDD.{md,docx}
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from app.skills.base import BaseSkill, SkillResult

log = logging.getLogger(__name__)

DRAFTS_DIR = Path("skills_config/drafts")

# Meeting type constants
MEETING_REGULAR = "regular"
MEETING_AGM = "AGM"
MEETING_EXTRAORDINARY = "extraordinary"

# ── Agenda templates by meeting type ────────────────────────────────────────

_AGENDA_REGULAR = [
    "1. Apologies for Absence",
    "2. Minutes of Previous Meeting -- approval",
    "3. Matters Arising from Minutes",
    "4. Outstanding Action Points (see Section 4)",
    "5. Financial Report",
    "6. Funding Update",
    "7. Development Officer Report",
    "8. Upcoming Deadlines (see Section 5)",
    "9. Any Other Business",
    "10. Date of Next Meeting",
]

_AGENDA_AGM = [
    "1. Welcome and Apologies",
    "2. Minutes of Previous AGM -- approval",
    "3. Chairperson's Report",
    "4. Treasurer's Report and Annual Accounts",
    "5. Appointment of Independent Examiner",
    "6. Election of Directors / Office Bearers",
    "7. Report on Activities and Achievements",
    "8. Development Officer Report",
    "9. Funding Update and Financial Outlook",
    "10. Matters Arising from Minutes",
    "11. Outstanding Action Points (see Section 4)",
    "12. Upcoming Deadlines (see Section 5)",
    "13. Any Other Competent Business",
    "14. Date of Next Meeting",
]

_AGENDA_EXTRAORDINARY = [
    "1. Apologies for Absence",
    "2. Statement of Purpose for Extraordinary Meeting",
    "3. Papers for Decision (see Section 7)",
    "4. Discussion",
    "5. Resolutions and Voting",
    "6. Outstanding Action Points (see Section 4)",
    "7. Any Other Business",
    "8. Date of Next Meeting",
]

_AGENDA_MAP = {
    MEETING_REGULAR: _AGENDA_REGULAR,
    MEETING_AGM: _AGENDA_AGM,
    MEETING_EXTRAORDINARY: _AGENDA_EXTRAORDINARY,
}

_MEETING_LABELS = {
    MEETING_REGULAR: "Board Meeting",
    MEETING_AGM: "Annual General Meeting",
    MEETING_EXTRAORDINARY: "Extraordinary Board Meeting",
}


# ── Data-gathering helpers ──────────────────────────────────────────────────


async def _get_open_actions() -> list[dict]:
    """Fetch open and in-progress action points."""
    try:
        from app.skills.action_tracker import list_actions
        open_actions = await list_actions(status="open")
        in_progress = await list_actions(status="in_progress")
        return open_actions + in_progress
    except Exception as exc:
        log.warning("Could not fetch action points: %s", exc)
        return []


async def _get_upcoming_deadlines(days: int = 60) -> list[dict]:
    """Fetch deadlines due within the given number of days."""
    try:
        from app.skills.deadline_tracker import list_deadlines
        return await list_deadlines(days_ahead=days)
    except Exception as exc:
        log.warning("Could not fetch deadlines: %s", exc)
        return []


async def _get_previous_minutes(vector_store) -> str:
    """Retrieve the most recent board minutes via vector search."""
    if not vector_store:
        return "No document archive available."
    try:
        hits = await vector_store.search(
            query="board meeting minutes decisions agreed",
            n_results=8,
            doc_type="minutes",
        )
        if not hits:
            return "No recent minutes found in the document archive."

        lines: list[str] = []
        seen_sources: set[str] = set()
        for hit in hits:
            src = Path(hit["source_file"]).name if hit.get("source_file") else ""
            if src in seen_sources:
                continue
            seen_sources.add(src)
            excerpt = hit.get("text", "")[:500].replace("\n", " ")
            lines.append(f"### {src}\n\n{excerpt}...")
            if len(seen_sources) >= 2:
                break
        return "\n\n".join(lines) if lines else "No recent minutes found."
    except Exception as exc:
        log.warning("Could not retrieve previous minutes: %s", exc)
        return "Previous minutes unavailable."


async def _get_funding_pipeline() -> Optional[str]:
    """Retrieve funding pipeline summary if tracker exists."""
    try:
        from app.skills.funding_tracker import list_applications
        apps = await list_applications(status="active")
        if not apps:
            return None
        lines: list[str] = []
        total_value = 0
        for app in apps:
            amount = app.get("amount_requested", 0) or 0
            total_value += amount
            status_label = app.get("status", "unknown").replace("_", " ").title()
            amount_str = f" -- {amount:,.0f}" if amount else ""
            lines.append(
                f"- **{app.get('funder', 'Unknown')}**{amount_str} [{status_label}]"
            )
        summary = f"*{len(apps)} active application(s), total pipeline value: "
        summary += f"{total_value:,.0f}*\n\n" if total_value else "TBC*\n\n"
        summary += "\n".join(lines)
        return summary
    except ImportError:
        log.debug("funding_tracker module not available -- skipping pipeline section")
        return None
    except Exception as exc:
        log.warning("Could not fetch funding pipeline: %s", exc)
        return None


async def _search_papers(
    paper_names: list[str],
    search_skill=None,
    vector_store=None,
) -> list[tuple[str, str]]:
    """
    Search for additional papers by name.
    Returns list of (title, excerpt) tuples.
    """
    if not paper_names:
        return []
    results: list[tuple[str, str]] = []
    for name in paper_names:
        excerpt = ""
        # Try vector search first
        if vector_store:
            try:
                hits = await vector_store.search(query=name, n_results=3)
                if hits:
                    src = Path(hits[0]["source_file"]).name if hits[0].get("source_file") else name
                    excerpt = hits[0].get("text", "")[:600].replace("\n", " ")
                    results.append((src, excerpt))
                    continue
            except Exception as exc:
                log.debug("Vector search for paper '%s' failed: %s", name, exc)

        # Fall back to search skill
        if search_skill:
            try:
                result = await search_skill.run(query=name)
                if result.success and result.output:
                    text = result.output if isinstance(result.output, str) else str(result.output)
                    results.append((name, text[:600]))
                    continue
            except Exception as exc:
                log.debug("Search skill for paper '%s' failed: %s", name, exc)

        results.append((name, "*Paper not found in document archive -- please attach manually.*"))
    return results


# ── Markdown builder ────────────────────────────────────────────────────────


def _build_board_pack_markdown(
    meeting_date: str,
    meeting_type: str,
    agenda: list[str],
    previous_minutes: str,
    actions: list[dict],
    deadlines: list[dict],
    funding_summary: Optional[str],
    papers: list[tuple[str, str]],
) -> str:
    today = date.today().isoformat()
    meeting_label = _MEETING_LABELS.get(meeting_type, "Board Meeting")

    lines: list[str] = []

    # ── Section 1: Cover page ────────────────────────────────────────────
    lines += [
        f"# CDCN {meeting_label}",
        "",
        f"**Date:** {meeting_date}",
        f"**Type:** {meeting_label}",
        f"**Organisation:** Community Development Company of Nesting (SC048164)",
        f"**Venue:** Aald Skul, South Nesting, Shetland",
        f"**Prepared:** {today}",
        "",
        "---",
        "",
    ]

    # ── Section 2: Draft agenda ──────────────────────────────────────────
    lines += [
        "## 2. Draft Agenda",
        "",
    ]
    for item in agenda:
        lines.append(item)
    lines += ["", "---", ""]

    # ── Section 3: Previous minutes ──────────────────────────────────────
    lines += [
        "## 3. Previous Minutes",
        "",
        previous_minutes,
        "",
        "---",
        "",
    ]

    # ── Section 4: Action points tracker ─────────────────────────────────
    lines += [
        "## 4. Action Points Tracker",
        "",
    ]
    if actions:
        lines.append(f"*{len(actions)} open/in-progress action(s) as at {today}:*")
        lines.append("")
        lines.append("| Action ID | Assigned To | Description | Due Date | Status |")
        lines.append("|-----------|-------------|-------------|----------|--------|")
        for a in actions[:30]:
            aid = a.get("action_id", "-")
            who = a.get("assigned_to", "-") or "-"
            desc = (a.get("description", "") or "")[:60]
            due = a.get("due_date", "-") or "-"
            st = (a.get("status", "open") or "open").replace("_", " ").title()
            lines.append(f"| {aid} | {who} | {desc} | {due} | {st} |")
    else:
        lines.append("*No outstanding action points.*")
    lines += ["", "---", ""]

    # ── Section 5: Upcoming deadlines ────────────────────────────────────
    lines += [
        "## 5. Upcoming Deadlines (next 60 days)",
        "",
    ]
    if deadlines:
        lines.append("| Due Date | Title | Category | Status |")
        lines.append("|----------|-------|----------|--------|")
        for d in deadlines:
            cat = (d.get("category", "") or "").replace("_", " ").title()
            st = (d.get("status", "pending") or "pending").replace("_", " ").title()
            lines.append(
                f"| {d.get('due_date', '-')} | {d.get('title', '-')} | {cat} | {st} |"
            )
    else:
        lines.append("*No deadlines in the next 60 days.*")
    lines += ["", "---", ""]

    # ── Section 6: Funding pipeline summary ──────────────────────────────
    lines += [
        "## 6. Funding Pipeline Summary",
        "",
    ]
    if funding_summary:
        lines.append(funding_summary)
    else:
        lines.append("*No active funding applications to report, or funding tracker unavailable.*")
    lines += ["", "---", ""]

    # ── Section 7: Papers for decision ───────────────────────────────────
    lines += [
        "## 7. Papers for Decision",
        "",
    ]
    if papers:
        for title, excerpt in papers:
            lines += [
                f"### {title}",
                "",
                excerpt,
                "",
            ]
    else:
        lines.append("*No additional papers submitted for this meeting.*")
    lines += ["", "---", ""]

    # ── Footer ───────────────────────────────────────────────────────────
    lines += [
        "",
        "*This board pack was auto-generated by CDCN Agent. "
        "Please verify all information before the meeting.*",
    ]

    return "\n".join(lines)


# ── Skill class ─────────────────────────────────────────────────────────────


class BoardPackSkill(BaseSkill):
    """Generate a complete board pack for CDCN board meetings."""

    name = "board_pack"
    description = (
        "Generate a complete board pack combining agenda, minutes, actions, "
        "deadlines, and papers. Use when asked to prepare a board pack, "
        "compile meeting papers, or generate documents for a board meeting."
    )

    def __init__(
        self,
        search_skill=None,
        action_tracker=None,
        deadline_tracker=None,
        vector_store=None,
        memory_skill=None,
    ):
        self._search_skill = search_skill
        self._action_tracker = action_tracker
        self._deadline_tracker = deadline_tracker
        self._vector_store = vector_store
        self._memory_skill = memory_skill

    async def run(self, action_or_dict=None, **kwargs) -> SkillResult:
        if isinstance(action_or_dict, dict):
            kwargs.update(action_or_dict)

        meeting_date = kwargs.get("meeting_date", "") or kwargs.get("date", "")
        if not meeting_date:
            # Default to next Tuesday (typical CDCN board meeting day)
            today = date.today()
            days_ahead = (1 - today.weekday()) % 7  # 1 = Tuesday
            if days_ahead == 0:
                days_ahead = 7
            meeting_date = (today + timedelta(days=days_ahead)).isoformat()

        meeting_type = kwargs.get("meeting_type", MEETING_REGULAR)
        if meeting_type not in _AGENDA_MAP:
            return SkillResult(
                success=False,
                error=(
                    f"Unknown meeting_type: '{meeting_type}'. "
                    f"Valid types: {MEETING_REGULAR}, {MEETING_AGM}, {MEETING_EXTRAORDINARY}"
                ),
            )

        additional_papers: list[str] = kwargs.get("additional_papers", []) or []
        if isinstance(additional_papers, str):
            additional_papers = [p.strip() for p in additional_papers.split(",") if p.strip()]

        try:
            # ── Gather data concurrently ─────────────────────────────────
            import asyncio

            actions_task = asyncio.create_task(_get_open_actions())
            deadlines_task = asyncio.create_task(_get_upcoming_deadlines(60))
            minutes_task = asyncio.create_task(
                _get_previous_minutes(self._vector_store)
            )
            funding_task = asyncio.create_task(_get_funding_pipeline())
            papers_task = asyncio.create_task(
                _search_papers(
                    additional_papers,
                    search_skill=self._search_skill,
                    vector_store=self._vector_store,
                )
            )

            actions = await actions_task
            deadlines = await deadlines_task
            previous_minutes = await minutes_task
            funding_summary = await funding_task
            papers = await papers_task

            # ── Select agenda ────────────────────────────────────────────
            agenda = _AGENDA_MAP[meeting_type]

            # ── Build markdown ───────────────────────────────────────────
            markdown = _build_board_pack_markdown(
                meeting_date=meeting_date,
                meeting_type=meeting_type,
                agenda=agenda,
                previous_minutes=previous_minutes,
                actions=actions,
                deadlines=deadlines,
                funding_summary=funding_summary,
                papers=papers,
            )

            # ── Save markdown ────────────────────────────────────────────
            DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
            safe_date = meeting_date.replace("-", "")
            md_filename = f"board_pack_{safe_date}.md"
            md_path = DRAFTS_DIR / md_filename
            md_path.write_text(markdown)
            log.info("Board pack markdown saved: %s", md_path)

            # ── Convert to DOCX ──────────────────────────────────────────
            docx_path = None
            download_url = ""
            try:
                from app.skills.docx_converter import markdown_to_docx, get_download_url
                meeting_label = _MEETING_LABELS.get(meeting_type, "Board Meeting")
                docx_filename = f"board_pack_{safe_date}.docx"
                docx_path = markdown_to_docx(
                    markdown,
                    filename=docx_filename,
                )
                download_url = get_download_url(docx_path)
                log.info("Board pack DOCX saved: %s", docx_path)
            except Exception as exc:
                log.warning("DOCX conversion failed: %s -- returning markdown only", exc)

            # ── Build summary ────────────────────────────────────────────
            meeting_label = _MEETING_LABELS.get(meeting_type, "Board Meeting")
            summary_parts = [
                f"Board pack generated for **{meeting_label}** on **{meeting_date}**.",
                f"- {len(actions)} open/in-progress action point(s)",
                f"- {len(deadlines)} upcoming deadline(s)",
                f"- {len(papers)} additional paper(s)",
            ]
            if funding_summary:
                summary_parts.append("- Funding pipeline summary included")
            else:
                summary_parts.append("- Funding pipeline: not available")

            summary = "\n".join(summary_parts)

            metadata = {
                "meeting_date": meeting_date,
                "meeting_type": meeting_type,
                "md_path": str(md_path),
                "md_filename": md_filename,
                "action_count": len(actions),
                "deadline_count": len(deadlines),
                "paper_count": len(papers),
            }
            if docx_path:
                metadata["docx_path"] = str(docx_path)
                metadata["docx_filename"] = docx_path.name
                metadata["download_url"] = download_url

            return SkillResult(
                success=True,
                output=summary,
                metadata=metadata,
            )

        except Exception as exc:
            log.exception("BoardPackSkill.run failed")
            return SkillResult(success=False, error=str(exc))
