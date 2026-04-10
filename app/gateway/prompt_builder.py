"""
Prompt construction — system prompt cache, skills listing, and tool definitions.

Cost-reduction measure 1: The system prompt is built ONCE at startup and cached
in memory.  It is only rebuilt when the underlying content changes (detected via
SHA-256 hash of the concatenated source files).  This produces an IDENTICAL
prefix string across all requests, allowing SiliconFlow's automatic prompt
caching to kick in (cached input tokens cost 40x less than fresh input).

The system prompt includes:
  - Soul/identity (soul.md)
  - Agent rules (agents.md)
  - Organisation profile (org_profile.md)
  - Learned skills from the database
  - Skills listing (registered skill descriptions)

All of these are deterministic for a given state — no timestamps, no random
ordering.  The prompt is always sent as the FIRST message so it forms the
cached prefix.
"""
from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

from app.config import settings

if TYPE_CHECKING:
    from app.skills.base import BaseSkill

log = logging.getLogger(__name__)

# ── Cached prompt state ──────────────────────────────────────────────────────

_cached_prompt: str = ""
_cached_prompt_hash: str = ""
_cached_prompt_ts: float = 0.0
_HASH_CHECK_INTERVAL = 300.0  # Check for changes every 5 minutes (not every call)


# ── Constants injected into prompts ──────────────────────────────────────────

NO_MORE_TOOLS_SUFFIX = (
    "\n\n## IMPORTANT — FINAL RESPONSE RULES\n"
    "You have just received the result of a tool/skill call. "
    "Write your reply to the user now using plain, natural language. "
    "Do NOT output any tool call, JSON block, XML tag, or skill invocation syntax. "
    "Just answer the question or confirm the action in conversational prose.\n\n"
    "CITATION RULES: For every factual claim, include a citation in the format "
    "[Document Name, Date, Section]. If a claim is not supported by the provided "
    "context, mark it explicitly as [UNVERIFIED]. CDCN is a registered charity — "
    "incorrect information about governance decisions or financial figures could "
    "have real consequences."
)

VERIFICATION_PROMPT = (
    "Review the following response for citation accuracy. For each factual claim:\n"
    "1. Does every claim have a citation in [Document Name, Date, Section] format?\n"
    "2. Is any claim not supported by the provided context?\n"
    "If any claims lack citations or are unsupported, return ONLY the corrected "
    "response with [UNVERIFIED] tags added. If all claims are properly cited, "
    "return the response unchanged. Do not add commentary."
)

# Legacy alias — some modules import this directly
_PROMPT_CACHE: dict[str, tuple[str, float]] = {}


# ── Deterministic content builders ───────────────────────────────────────────

def _read_file(path: Path) -> str:
    """Read a file; return empty string if missing."""
    try:
        return path.read_text() if path.exists() else ""
    except OSError:
        return ""


def _build_learned_skills_block() -> str:
    """Load learned skills from the DB deterministically (sorted by name)."""
    try:
        import sqlite3
        db_path = settings.audit_log_path
        if not Path(db_path).exists():
            return ""
        conn = sqlite3.connect(db_path)
        try:
            rows = conn.execute(
                "SELECT skill_name, description FROM learned_skills "
                "ORDER BY skill_name, id"
            ).fetchall()
        except sqlite3.OperationalError:
            return ""
        finally:
            conn.close()

        if not rows:
            return ""

        lines = ["## Learned Behaviours", ""]
        for name, desc in rows:
            # Truncate long descriptions for prompt efficiency
            short_desc = desc.strip()[:200]
            lines.append(f"- **{name}**: {short_desc}")
        return "\n".join(lines)
    except Exception as exc:
        log.debug("Could not load learned skills: %s", exc)
        return ""


def build_skills_block(skills: dict[str, BaseSkill]) -> str:
    """
    Build the skills listing injected into every system prompt.
    Deterministic: skills sorted by name.
    """
    lines = [
        "## Available Skills",
        "",
        "You have access to the following tools/skills via function calling:",
        "",
    ]
    for name in sorted(skills.keys()):
        skill = skills[name]
        lines.append(f"- **{name}**: {skill.description}")
    lines += [
        "",
        "Use function calling when a skill is needed. Do not output raw JSON skill calls.",
    ]
    return "\n".join(lines)


def _compute_prompt_hash(parts: list[str]) -> str:
    """SHA-256 hash of concatenated prompt parts."""
    combined = "\n".join(parts)
    return hashlib.sha256(combined.encode()).hexdigest()[:16]


# ── Public helpers ───────────────────────────────────────────────────────────

async def get_system_prompt(role: str, memory_skill, skills: dict[str, BaseSkill]) -> str:
    """
    Return the cached system prompt, rebuilding only if source content has changed.

    The prompt is identical across calls with the same underlying content,
    enabling SiliconFlow's automatic prompt prefix caching.
    """
    global _cached_prompt, _cached_prompt_hash, _cached_prompt_ts

    now = time.monotonic()

    # Only check for changes periodically, not every call
    if _cached_prompt and (now - _cached_prompt_ts) < _HASH_CHECK_INTERVAL:
        return _cached_prompt

    # Read all source parts
    from app.skills.memory import SOUL_FILE, AGENTS_FILE, ORG_PROFILE_FILE
    soul = _read_file(SOUL_FILE).strip()
    agents = _read_file(AGENTS_FILE).strip()
    org = _read_file(ORG_PROFILE_FILE).strip()
    learned = _build_learned_skills_block()
    skills_block = build_skills_block(skills)

    parts = [soul, agents, org, learned, skills_block]
    new_hash = _compute_prompt_hash(parts)

    # If hash unchanged, just update timestamp and return cached
    if new_hash == _cached_prompt_hash and _cached_prompt:
        _cached_prompt_ts = now
        return _cached_prompt

    # Rebuild prompt
    identity_parts = [p for p in [soul, agents, org] if p]
    identity = "\n\n---\n\n".join(identity_parts) if identity_parts else "(no identity context loaded)"

    prompt_sections = [identity]
    if learned:
        prompt_sections.append(learned)
    prompt_sections.append(skills_block)

    _cached_prompt = "\n\n".join(prompt_sections)
    _cached_prompt_hash = new_hash
    _cached_prompt_ts = now

    log.info("System prompt rebuilt (hash=%s, len=%d chars)", new_hash, len(_cached_prompt))
    return _cached_prompt


def invalidate_prompt_cache():
    """
    Force a rebuild of the system prompt on next call.
    Call this after indexing, dream mode, or learned_skills changes.
    """
    global _cached_prompt_hash, _cached_prompt_ts
    _cached_prompt_hash = ""
    _cached_prompt_ts = 0.0
    log.info("System prompt cache invalidated")


def get_prompt_cache_info() -> dict:
    """Return info about the current prompt cache state (for diagnostics)."""
    return {
        "hash": _cached_prompt_hash,
        "length": len(_cached_prompt),
        "age_seconds": round(time.monotonic() - _cached_prompt_ts, 1) if _cached_prompt_ts else None,
    }


def build_tool_definitions(skills: dict[str, BaseSkill]) -> list[dict]:
    """Convert registered skills to OpenAI tool definitions."""
    tools = []
    for name, skill in skills.items():
        tool = {
            "type": "function",
            "function": {
                "name": name,
                "description": skill.description,
                "parameters": {"type": "object", "properties": {}, "required": []},
            }
        }
        if name == "search" or name == "search_archive":
            tool["function"]["name"] = "search_archive"
            tool["function"]["description"] = (
                "Search the CDCN document archive. Use this whenever you need to find "
                "information from meeting minutes, policies, funding documents, or any "
                "other CDCN records."
            )
            tool["function"]["parameters"] = {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The search query — what information are you looking for?"},
                    "doc_type": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["minutes", "agm_minutes", "policy", "funding", "report", "plan", "agenda", "other"]},
                        "description": "Filter by document type. Omit to search all types.",
                    },
                    "date_from": {"type": "string", "description": "Filter documents from this date (ISO format YYYY-MM-DD)"},
                    "date_to": {"type": "string", "description": "Filter documents up to this date (ISO format YYYY-MM-DD)"},
                    "section": {"type": "string", "description": "Filter by section title within documents (e.g. 'DO Update', 'Action Points')"},
                },
                "required": ["query"],
            }
        elif name == "indexer":
            tool["function"]["parameters"] = {
                "type": "object",
                "properties": {
                    "folder": {"type": "string", "description": "Folder to index (optional, uses default)"},
                    "force": {"type": "boolean", "description": "Force re-index all files"},
                },
                "required": [],
            }
        elif name == "writer":
            tool["function"]["parameters"] = {
                "type": "object",
                "properties": {
                    "template": {"type": "string", "enum": ["funding_application", "board_minute", "governance_policy", "trustees_report"]},
                    "brief": {"type": "string", "description": "Instructions for the document"},
                },
                "required": ["template", "brief"],
            }
        elif name == "memory":
            tool["function"]["parameters"] = {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["get_context", "store_summary"]},
                    "summary": {"type": "string"},
                },
                "required": ["action"],
            }
        elif name == "deadline_tracker":
            tool["function"]["parameters"] = {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list", "add", "complete", "overdue"],
                               "description": "list=show upcoming, add=create new, complete=mark done, overdue=check overdue"},
                    "title": {"type": "string", "description": "Deadline title (for add)"},
                    "category": {"type": "string", "enum": ["funding", "statutory", "policy_review", "contractual", "event", "other"]},
                    "due_date": {"type": "string", "description": "ISO date YYYY-MM-DD (for add)"},
                    "assigned_to": {"type": "string"},
                    "notes": {"type": "string"},
                    "days_ahead": {"type": "integer", "description": "How many days ahead to look (default 90)"},
                    "id": {"type": "integer", "description": "Deadline ID (for complete)"},
                },
                "required": ["action"],
            }
        elif name == "action_tracker":
            tool["function"]["parameters"] = {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["list", "add", "complete"],
                               "description": "list=show open actions, add=create new, complete=mark done"},
                    "description": {"type": "string", "description": "Action description (for add)"},
                    "assigned_to": {"type": "string"},
                    "due_date": {"type": "string"},
                    "meeting_date": {"type": "string"},
                    "priority": {"type": "string", "enum": ["high", "medium", "low"]},
                    "action_id": {"type": "string", "description": "Action ID like AP-20260323-001 (for complete)"},
                    "status": {"type": "string", "enum": ["open", "in_progress", "completed", "deferred", "closed"]},
                },
                "required": ["action"],
            }
        elif name == "meeting_prep":
            tool["function"]["parameters"] = {
                "type": "object",
                "properties": {
                    "meeting_date": {"type": "string", "description": "ISO date YYYY-MM-DD of the meeting"},
                },
                "required": [],
            }
        elif name == "calendar_manager":
            tool["function"]["parameters"] = {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["month", "upcoming", "add", "update", "delete", "complete"],
                        "description": "month=get events for a month, upcoming=next N days, add=create event, update=modify, delete=remove, complete=mark done",
                    },
                    "year": {"type": "integer", "description": "Year (for month action)"},
                    "month": {"type": "integer", "description": "Month 1-12 (for month action)"},
                    "days": {"type": "integer", "description": "Days ahead to look (for upcoming, default 30)"},
                    "title": {"type": "string", "description": "Event title (for add)"},
                    "due_date": {"type": "string", "description": "Date YYYY-MM-DD (for add/update)"},
                    "category": {
                        "type": "string",
                        "enum": ["event", "meeting", "funding", "statutory", "policy_review", "contractual", "other"],
                        "description": "Event category",
                    },
                    "event_time": {"type": "string", "description": "Time HH:MM (for add/update)"},
                    "notes": {"type": "string"},
                    "assigned_to": {"type": "string"},
                    "id": {"type": "integer", "description": "Event ID (for update/delete/complete)"},
                },
                "required": ["action"],
            }
        elif name == "document_editor":
            tool["function"]["parameters"] = {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["read", "save", "list"],
                        "description": "read=get full document content, save=write content to file, list=browse directory",
                    },
                    "path": {
                        "type": "string",
                        "description": "Relative path within the document archive (e.g. 'Minutes/report.docx')",
                    },
                    "content": {
                        "type": "string",
                        "description": "Document content to save (required for action=save)",
                    },
                    "title": {
                        "type": "string",
                        "description": "Document title — used as heading when saving .docx files",
                    },
                    "extension": {
                        "type": "string",
                        "description": "Filter by file extension when listing (e.g. '.docx')",
                    },
                    "create_backup": {
                        "type": "boolean",
                        "description": "Create a .bak backup before overwriting (default true)",
                    },
                },
                "required": ["action"],
            }
        tools.append(tool)
    return tools
