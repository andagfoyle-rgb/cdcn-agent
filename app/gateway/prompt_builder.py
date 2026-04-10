"""
Prompt construction — system prompt cache, skills listing, and tool definitions.

Extracted from router.py to keep the AgentRouter class focused on orchestration.
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.skills.base import BaseSkill

# ── System-prompt cache ──────────────────────────────────────────────────────
# Keyed by role string.  Value is (prompt_text, monotonic_timestamp).

_PROMPT_CACHE: dict[str, tuple[str, float]] = {}
_PROMPT_CACHE_TTL = 60.0  # seconds

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


# ── Public helpers ───────────────────────────────────────────────────────────

async def get_system_prompt(role: str, memory_skill, skills: dict[str, BaseSkill]) -> str:
    """
    Return a system prompt for the given role.

    The prompt is assembled from:
      - memory_skill.get_system_prompt() — soul, style guide, agent context
      - build_skills_block()             — skill listing in canonical format

    Result is cached for _PROMPT_CACHE_TTL seconds per role to avoid
    repeated file reads on every turn.
    """
    now = time.monotonic()
    cached = _PROMPT_CACHE.get(role)
    if cached and (now - cached[1]) < _PROMPT_CACHE_TTL:
        return cached[0]

    soul_prompt = memory_skill.get_system_prompt()
    skills_block = build_skills_block(skills)
    prompt = f"{soul_prompt}\n\n{skills_block}"

    _PROMPT_CACHE[role] = (prompt, now)
    return prompt


def build_skills_block(skills: dict[str, BaseSkill]) -> str:
    """
    Build the skills listing injected into every system prompt.
    Simplified for function calling — no raw JSON format instructions needed.
    """
    lines = [
        "## Available Skills",
        "",
        "You have access to the following tools/skills via function calling:",
        "",
    ]
    for name, skill in skills.items():
        lines.append(f"- **{name}**: {skill.description}")
    lines += [
        "",
        "Use function calling when a skill is needed. Do not output raw JSON skill calls.",
    ]
    return "\n".join(lines)


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
