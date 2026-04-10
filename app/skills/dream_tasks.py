"""
dream_tasks — overnight consolidation tasks 1-3, extracted from
DreamWorkerSkill as standalone async functions.

Contains:
  - consolidate_memory       (task 1)
  - self_critique            (task 2)
  - learn_from_interactions  (task 3)

Tasks 4-6 live in dream_tasks_ext.py and are re-exported here so that
dream_worker.py can import everything from a single module.  Both files
receive their dependencies explicitly for isolated unit testing.
"""
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings
from app.skills.dream_helpers import (
    _TaskOutcome,
    _extract_section,
    _is_correction,
    _session_transcript,
    _truncate,
)

# Re-export tasks 4-6 so dream_worker can import everything from one place
from app.skills.dream_tasks_ext import (  # noqa: F401
    _parse_predictions,
    _sample_chunks_for_type,
    anticipate_tomorrow,
    map_document_relationships,
    refine_style_guide,
)

log = logging.getLogger(__name__)


# ── Task 1: consolidate_memory ───────────────────────────────────────────────


async def consolidate_memory(
    dream_call,
    memory_skill,
    session_manager,
) -> _TaskOutcome:
    """
    Read yesterday's sessions and distil key findings into memory.md sections.
    Tags each update with the source date and 'auto-consolidated'.

    Note: this runs after midnight (01:00-02:00 UTC) so we look at
    *yesterday's* sessions, not today's.  Falls back to today if
    yesterday has none (e.g. manual daytime run).
    """
    sessions = session_manager.get_yesterday_sessions()
    if not sessions:
        sessions = session_manager.get_today_sessions()
    if not sessions:
        return _TaskOutcome(
            task="consolidate_memory",
            success=True,
            summary="No sessions today — nothing to consolidate.",
        )

    transcripts = []
    for s in sessions:
        t = _session_transcript(s)
        if t.strip():
            transcripts.append(f"[Session {s.session_id[:8]}]\n{t}")

    if not transcripts:
        return _TaskOutcome(
            task="consolidate_memory",
            success=True,
            summary="Sessions present but no message content found.",
        )

    combined = _truncate("\n\n---\n\n".join(transcripts), max_chars=6000)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    prompt = (
        "You are analysing conversations with CDCN Agent, an AI assistant for "
        "Community Development Company Nesting (CDCN), a Scottish charitable organisation "
        "in South Nesting, Shetland.\n\n"
        "Review the following conversation transcripts from today and identify:\n\n"
        "1. REPEATED TOPICS: Topics or questions that came up more than once\n"
        "2. STRUGGLED QUESTIONS: Questions the agent appeared to answer poorly or "
        "that required follow-up clarification\n"
        "3. FREQUENTLY REFERENCED DOCUMENTS: Specific documents or files mentioned repeatedly\n"
        "4. NEW CDCN FACTS: New facts about CDCN's projects, context, or working practices "
        "that should be retained\n\n"
        "Format your response as:\n\n"
        "## REPEATED TOPICS\n- [topic]\n\n"
        "## STRUGGLED QUESTIONS\n- [question]\n\n"
        "## FREQUENTLY REFERENCED DOCUMENTS\n- [document]\n\n"
        "## NEW CDCN FACTS\n- [fact]\n\n"
        "If a category has no entries write 'None identified.'\n\n"
        f"Transcripts:\n\n{combined}"
    )

    try:
        response = await dream_call(prompt)
    except Exception as exc:  # noqa: BLE001
        return _TaskOutcome(
            task="consolidate_memory",
            success=False,
            summary="",
            error=f"LLM call failed: {exc}",
        )

    tag = f"(source: {today} sessions, auto-consolidated)"
    sections_written: list[str] = []

    for llm_header, memory_section in [
        ("## REPEATED TOPICS", "Recurring preferences and working style notes"),
        ("## STRUGGLED QUESTIONS", "Things to follow up"),
        ("## NEW CDCN FACTS", "Key decisions from recent meetings"),
    ]:
        block = _extract_section(response, llm_header)
        if block and "none identified" not in block.lower():
            result = memory_skill.update_memory(memory_section, f"{block}\n\n{tag}")
            if result.success:
                sections_written.append(memory_section)

    return _TaskOutcome(
        task="consolidate_memory",
        success=True,
        summary=(
            f"Consolidated {len(sessions)} session(s). "
            f"Updated: {', '.join(sections_written) or 'no sections needed update'}."
        ),
    )


# ── Task 2: self_critique ────────────────────────────────────────────────────


async def self_critique(
    dream_call,
    session_manager,
    pending_changes,
) -> _TaskOutcome:
    """
    Find exchanges where the agent underperformed (correction phrases,
    short follow-up rephrases) and propose targeted amendments to agents.md.
    All proposals go to pending_changes/ -- nothing is auto-applied.
    """
    sessions = session_manager.get_yesterday_sessions()
    if not sessions:
        sessions = session_manager.get_today_sessions()
    critique_exchanges: list[tuple[str, str, str]] = []

    for session in sessions:
        msgs = session.messages
        for i, msg in enumerate(msgs):
            if msg.get("role") != "user":
                continue
            content = msg.get("content", "")
            if not _is_correction(content):
                continue
            assistant_msg = next(
                (msgs[j]["content"] for j in range(i - 1, -1, -1)
                 if msgs[j].get("role") == "assistant"),
                "",
            )
            if assistant_msg:
                critique_exchanges.append(
                    (session.session_id[:8], assistant_msg, content)
                )

    if not critique_exchanges:
        return _TaskOutcome(
            task="self_critique",
            success=True,
            summary="No critique cases found today.",
        )

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    pending_dir = Path(settings.pending_changes_path)
    pending_dir.mkdir(parents=True, exist_ok=True)
    proposals_written = 0

    for n, (session_id, assistant_msg, user_correction) in enumerate(
        critique_exchanges, start=1
    ):
        prompt = (
            "You are reviewing an exchange between a user and CDCN Agent, an AI assistant "
            "for a Scottish charitable organisation.\n\n"
            "The user appeared to correct or express dissatisfaction with the agent's "
            "response. What could the agent have done better?\n\n"
            f"AGENT'S RESPONSE:\n{_truncate(assistant_msg, 1500)}\n\n"
            f"USER'S FOLLOW-UP:\n{_truncate(user_correction, 500)}\n\n"
            "Propose one specific amendment to the agent's operating rules that would "
            "improve future responses. Format your response EXACTLY as:\n\n"
            "PROBLEM: [what went wrong in 1-2 sentences]\n"
            "PROPOSED AMENDMENT: [exact text to add or change in the agent's rules]\n"
            "REASONING: [why this would help, in 1-2 sentences]"
        )

        try:
            response = await dream_call(prompt)
        except Exception as exc:  # noqa: BLE001
            log.warning("self_critique LLM call failed for exchange %d: %s", n, exc)
            continue

        proposal_path = pending_dir / f"agents_{today}_{n}.md"
        proposal_text = (
            f"# Proposed Amendment to agents.md\n\n"
            f"Generated: {today}\nSession: {session_id}\nStatus: pending\n\n"
            f"## Context\n\n"
            f"**Agent response that prompted review:**\n{_truncate(assistant_msg, 800)}\n\n"
            f"**User correction:**\n{_truncate(user_correction, 400)}\n\n"
            f"## Analysis\n\n{response.strip()}\n\n"
            f"---\n"
            f"*Generated by DreamWorkerSkill self_critique. "
            f"Requires human review before application.*\n"
        )
        try:
            proposal_path.write_text(proposal_text)
            pending_changes.propose(
                title=f"agents.md amendment — {today} (session {session_id})",
                diff=response.strip(),
                author="dream_worker",
            )
            proposals_written += 1
        except Exception as exc:  # noqa: BLE001
            log.warning("self_critique write failed: %s", exc)

    return _TaskOutcome(
        task="self_critique",
        success=True,
        summary=(
            f"Found {len(critique_exchanges)} critique case(s). "
            f"Wrote {proposals_written} proposal(s) to pending_changes/."
        ),
    )


# ── Task 3: learn_from_interactions ──────────────────────────────────────────


def _parse_learned_patterns(text: str) -> list[tuple[str, str, str]]:
    """Parse PATTERN N / Trigger: / Approach: / Category: blocks."""
    results: list[tuple[str, str, str]] = []
    blocks = re.split(r"PATTERN\s+\d+", text, flags=re.IGNORECASE)
    for block in blocks[1:]:
        t = re.search(r"Trigger:\s*(.+?)(?:\n|$)", block, re.IGNORECASE)
        a = re.search(r"Approach:\s*(.+?)(?:\n|$)", block, re.IGNORECASE)
        c = re.search(r"Category:\s*(.+?)(?:\n|$)", block, re.IGNORECASE)
        if t and a:
            results.append((
                t.group(1).strip(),
                a.group(1).strip(),
                c.group(1).strip() if c else "general",
            ))
    return results[:5]


async def learn_from_interactions(
    dream_call,
    session_manager,
) -> _TaskOutcome:
    """
    Review yesterday's conversations, extract reusable patterns and
    approaches that worked well, and store them in the learned_skills
    table for injection into future system prompts.

    Patterns include: effective response strategies for specific question
    types, document formats that users accepted without corrections,
    and successful tool-use sequences.
    """
    from app.storage.audit_log import log_learned_skill

    sessions = session_manager.get_yesterday_sessions()
    if not sessions:
        sessions = session_manager.get_today_sessions()
    if not sessions:
        return _TaskOutcome(
            task="learn_from_interactions",
            success=True,
            summary="No sessions — nothing to learn from.",
        )

    # Build a combined transcript of successful exchanges
    # (assistant turns that were NOT followed by a correction)
    good_exchanges: list[str] = []
    for session in sessions:
        msgs = session.messages
        for i, msg in enumerate(msgs):
            if msg.get("role") != "assistant":
                continue
            content = msg.get("content", "").strip()
            if not content or len(content) < 100:
                continue
            # Check if the next user message is a correction
            next_user = next(
                (msgs[j] for j in range(i + 1, len(msgs))
                 if msgs[j].get("role") == "user"),
                None,
            )
            if next_user and _is_correction(next_user.get("content", "")):
                continue  # skip — this exchange was corrected
            # Find the preceding user question
            prev_user = next(
                (msgs[j]["content"] for j in range(i - 1, -1, -1)
                 if msgs[j].get("role") == "user"),
                "",
            )
            if prev_user:
                good_exchanges.append(
                    f"Q: {_truncate(prev_user, 300)}\n"
                    f"A: {_truncate(content, 500)}"
                )

    if len(good_exchanges) < 2:
        return _TaskOutcome(
            task="learn_from_interactions",
            success=True,
            summary=f"Only {len(good_exchanges)} good exchange(s) — "
                    f"need at least 2 to extract patterns.",
        )

    combined = _truncate("\n\n---\n\n".join(good_exchanges[:20]), max_chars=6000)

    prompt = (
        "You are analysing successful conversations between a user and CDCN Agent, "
        "an AI assistant for a Scottish charitable organisation (Community Development "
        "Company Nesting, South Nesting, Shetland).\n\n"
        "Review these exchanges where the agent answered well (no user corrections "
        "followed). Extract reusable patterns — approaches that should be repeated "
        "in future similar situations.\n\n"
        "For each pattern, identify:\n"
        "- TRIGGER: What kind of question or request triggers this pattern\n"
        "- APPROACH: What the agent did well (specific, actionable)\n"
        "- CATEGORY: One of: document_drafting, information_retrieval, "
        "meeting_support, funding, community_engagement, technical, general\n\n"
        "Output 1-5 patterns. Format EXACTLY as:\n\n"
        "PATTERN 1\n"
        "Trigger: [when user asks...]\n"
        "Approach: [the agent should...]\n"
        "Category: [category]\n\n"
        "PATTERN 2\n...\n\n"
        "If no clear reusable patterns emerge, respond only with: "
        "'No patterns identified.'\n\n"
        f"Exchanges:\n\n{combined}"
    )

    try:
        response = await dream_call(prompt)
    except Exception as exc:
        return _TaskOutcome(
            task="learn_from_interactions",
            success=False,
            summary="",
            error=f"LLM call failed: {exc}",
        )

    if "no patterns identified" in response.lower():
        return _TaskOutcome(
            task="learn_from_interactions",
            success=True,
            summary="LLM found no reusable patterns in today's exchanges.",
        )

    patterns = _parse_learned_patterns(response)
    skills_stored = 0

    for trigger, approach, category in patterns:
        try:
            await log_learned_skill(
                skill_name=category,
                trigger_pattern=trigger,
                description=approach,
                source=f"dream_learn_{datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
            )
            skills_stored += 1
        except Exception as exc:
            log.warning("log_learned_skill failed: %s", exc)

    return _TaskOutcome(
        task="learn_from_interactions",
        success=True,
        summary=(
            f"Analysed {len(good_exchanges)} successful exchange(s). "
            f"Extracted {len(patterns)} pattern(s), stored {skills_stored}."
        ),
    )
