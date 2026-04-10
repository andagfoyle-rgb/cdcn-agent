"""
Memory helpers — heartbeat, auto-journal, and deadline checking for MemorySkill.

Extracted from memory.py to keep each module under 500 lines.
"""
import logging
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings

log = logging.getLogger(__name__)

# ── Paths (mirrored from memory.py to avoid circular imports) ──────────────
FUNDING_DEADLINES_FILE = Path("skills_config/funding_deadlines.yaml")


def _now_display() -> str:
    """Human-readable UTC timestamp for memory entries."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def check_deadlines() -> list[str]:
    """
    Parse funding_deadlines.yaml and return alert strings for deadlines
    within the next 14 days.  Submitted/awarded/declined items are skipped.
    """
    if not FUNDING_DEADLINES_FILE.exists():
        return []
    try:
        import yaml

        data = yaml.safe_load(FUNDING_DEADLINES_FILE.read_text()) or {}
    except Exception as exc:
        log.warning("Could not parse funding_deadlines.yaml: %s", exc)
        return []

    today = datetime.now().date()
    alerts: list[str] = []
    skip_statuses = {"submitted", "awarded", "declined"}

    for item in data.get("deadlines", []):
        deadline_str = str(item.get("deadline", "")).strip()
        if not deadline_str or item.get("status", "") in skip_statuses:
            continue
        try:
            deadline_date = datetime.strptime(deadline_str, "%Y-%m-%d").date()
            days = (deadline_date - today).days
            if 0 <= days <= settings.deadline_reminder_days:
                funder = item.get("funder", "[funder]")
                programme = item.get("programme", "[programme]")
                alerts.append(
                    f"\u26a0\ufe0f DEADLINE IN {days} DAYS: {funder} \u2014 {programme} \u2014 due {deadline_str}"
                )
        except ValueError:
            log.debug("Could not parse deadline date: %s", deadline_str)

    return alerts


async def run_heartbeat(memory_skill) -> "SkillResult":
    """
    Execute the heartbeat task checklist as defined in heartbeat.md.

    Steps:
      1. Run the document indexer (pick up any new files).
      2. Check funding_deadlines.yaml for deadlines within 14 days.
      3. Log the run to data/memory/heartbeat_log.md.

    Returns a summary string including any deadline alerts.
    """
    from app.skills.base import SkillResult
    from app.skills.indexer import DocumentIndexerSkill

    ts = _now_display()
    results: list[str] = []
    alerts: list[str] = []

    # 1. Index new documents
    try:
        idx = await DocumentIndexerSkill().run()
        results.append(f"Indexer: {idx.output}")
    except Exception as exc:
        log.warning("Heartbeat indexer failed: %s", exc)
        results.append(f"Indexer error: {exc}")

    # 2. Funding deadline check
    alerts = check_deadlines()
    results.extend(alerts)

    # 3. Log execution
    data_dir = Path(settings.memory_path)
    log_path = data_dir / "heartbeat_log.md"
    _ensure_dir(log_path.parent)
    with log_path.open("a") as f:
        f.write(f"\n## Heartbeat \u2014 {ts}\n\n")
        for line in results:
            f.write(f"- {line}\n")
        if not results:
            f.write("- All clear\n")

    summary = "\n".join(results) if results else "All clear"
    log.info(
        "Heartbeat complete: %d items, %d deadline alert(s)", len(results), len(alerts)
    )
    return SkillResult(
        success=True,
        output=summary,
        metadata={"alerts": len(alerts), "alert_texts": alerts},
    )


async def auto_journal(memory_skill) -> "SkillResult":
    """
    LLM-generated nightly journal entry (uses the dream model).

    Reads soul context and any recent journal for continuity, then
    asks the model to write a brief reflective entry.
    """
    from app.skills.base import SkillResult
    from app.llm_client import dream_chat

    soul = memory_skill.load_soul()
    recent = memory_skill.read_recent_journal(n_days=1).output or ""

    messages = [
        {
            "role": "system",
            "content": (
                "You are CDCN Agent, an AI assistant for a Scottish community organisation. "
                "Write a brief, factual journal entry (4\u20136 sentences) summarising today's "
                "activity and any notes worth remembering. "
                "Write in first person, past tense. Be concise."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Soul context:\n{soul}\n\n"
                f"Most recent journal:\n{recent}\n\n"
                "Write today's journal entry."
            ),
        },
    ]
    try:
        entry = await dream_chat(messages)
    except Exception as exc:
        return SkillResult(success=False, error=str(exc))

    memory_skill.write_journal(content=entry)
    log.info("Nightly journal written for %s", _today())
    return SkillResult(success=True, output=entry)
