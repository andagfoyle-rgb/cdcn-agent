"""
Tool/skill handling — injection detection, skill-call parsing, execution, response
cleaning, and citation verification.

Extracted from router.py to keep the AgentRouter class focused on orchestration.
"""
from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING

from app.gateway.prompt_builder import VERIFICATION_PROMPT

if TYPE_CHECKING:
    from app.llm_client import CDCNLLMClient
    from app.skills.base import BaseSkill

log = logging.getLogger(__name__)

# ── Prompt injection detection ────────────────────────────────────────────────
# Patterns are compiled once at import time.  Checked before any LLM call.

_INJECTION_PATTERNS: list[re.Pattern] = [
    re.compile(r"ignore\s+previous\s+instructions", re.IGNORECASE),
    re.compile(r"(?<!\w)system\s*:", re.IGNORECASE),
    re.compile(r"^###", re.MULTILINE),
]

# ── Skill-call extraction ─────────────────────────────────────────────────────
# Matches a JSON object containing "skill" inside a fenced code block.
# The LLM is instructed to use exactly this format.

_SKILL_CALL_RE = re.compile(
    r"```(?:json)?\s*(\{[^`]*\"skill\"[^`]*\})\s*```",
    re.DOTALL,
)

# ── GLM-5 XML tool-call fallback ──────────────────────────────────────────────
# GLM-5 (SiliconFlow) sometimes emits its own <tool_call> XML format in the
# content field rather than using OpenAI function calling.

_XML_TOOL_CALL_RE = re.compile(
    r"<tool_call>(\w+)(.*?)</tool_call>",
    re.DOTALL,
)
_XML_ARG_RE = re.compile(
    r"<arg_key>([^<]*)</arg_key>\s*<arg_value>(.*?)</arg_value>",
    re.DOTALL,
)

# Matches fenced code blocks that contain leaked skill-call JSON (has "skill" key).
_FENCED_BLOCK_RE_SKILL = re.compile(
    r'```(?:json)?\s*\n\s*\{[^}]*"skill"\s*:.*?\}\s*\n```',
    re.DOTALL,
)

# Maximum additional tool-call rounds allowed when the LLM re-emits tool calls
# in the final response step instead of answering the user.
MAX_TOOL_ROUNDS = 3


# ── Public functions ─────────────────────────────────────────────────────────

def is_injection(message: str) -> bool:
    """Return True if the message matches a known prompt-injection pattern."""
    return any(p.search(message) for p in _INJECTION_PATTERNS)


def clean_llm_response(text: str) -> str:
    """
    Strip artefacts that should never reach the user:
      - Fenced code blocks that contain leaked skill-call JSON
      - XML <tool_call> blocks (GLM-5 format that bypassed the XML handler)
    Preserves legitimate code blocks (tables, examples, plain text wrapping).
    Collapses any resulting runs of blank lines to a single blank line.
    """
    cleaned = _FENCED_BLOCK_RE_SKILL.sub("", text)
    cleaned = _XML_TOOL_CALL_RE.sub("", cleaned)
    # If the LLM wrapped the entire response in a single fenced block,
    # unwrap it rather than stripping it.
    _unwrap = re.match(r"^\s*```[a-zA-Z]*\n(.*?)```\s*$", cleaned, re.DOTALL)
    if _unwrap:
        cleaned = _unwrap.group(1)
    # Collapse 3+ consecutive newlines to 2
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def parse_skill_call(text: str) -> dict | None:
    """
    Extract the first skill-call JSON object from a fenced code block.
    Returns None if no valid skill call is found.
    """
    match = _SKILL_CALL_RE.search(text)
    if not match:
        return None
    try:
        data = json.loads(match.group(1))
        if isinstance(data, dict) and "skill" in data:
            return data
    except json.JSONDecodeError as exc:
        log.debug("Skill JSON parse failed: %s — raw: %s", exc, match.group(1)[:200])
    return None


def parse_xml_tool_call(text: str) -> dict | None:
    """
    Parse a GLM-5-style XML tool call from the content field.
    Returns {"name": str, "args": dict} or None.
    """
    match = _XML_TOOL_CALL_RE.search(text)
    if not match:
        return None
    skill_name = match.group(1).strip()
    if not skill_name:
        return None
    args: dict[str, str] = {}
    for arg_match in _XML_ARG_RE.finditer(match.group(2)):
        args[arg_match.group(1).strip()] = arg_match.group(2).strip()
    log.debug("Parsed XML tool call: skill=%s args=%s", skill_name, args)
    return {"name": skill_name, "args": args}


def strip_xml_tool_calls(text: str) -> str:
    """Remove XML <tool_call> blocks from text, returning the remainder."""
    return _XML_TOOL_CALL_RE.sub("", text).strip()


async def execute_skill(skill_name: str, args: dict, skills: dict[str, BaseSkill]) -> dict:
    """
    Dispatch to the named skill.  Always returns a dict — never raises.
    Unknown / failed skills return an error dict the LLM can explain.
    Maps search_archive → search skill.
    """
    actual_name = "search" if skill_name == "search_archive" else skill_name
    skill = skills.get(actual_name)
    if not skill:
        log.warning("LLM requested unknown skill: %s", skill_name)
        return {
            "success": False,
            "error": f"Skill '{skill_name}' is not available.",
            "output": None,
        }
    try:
        result = await skill.run(**args)

        output = result.output
        metadata = result.metadata if hasattr(result, "metadata") else {}
        if actual_name == "search" and result.success and output:
            output = (
                output + "\n\n---\n"
                "CITATION REQUIREMENT: Every factual claim in your response MUST "
                "include a citation in the format [Document Name, Date, Section]. "
                "If a claim cannot be supported by the search results above, "
                "explicitly mark it as [UNVERIFIED]."
            )

        return {
            "success": result.success,
            "output": output,
            "error": result.error,
            "metadata": metadata,
        }
    except Exception as exc:  # noqa: BLE001
        log.exception("Skill '%s' raised an exception", skill_name)
        return {"success": False, "error": str(exc), "output": None, "metadata": {}}


async def verify_response(response: str, context_messages: list[dict], llm: CDCNLLMClient) -> str:
    """
    Verify that every factual claim in the response has a citation.
    If verification fails, returns corrected response with [UNVERIFIED] tags.
    This is mandatory for CDCN — a registered charity where incorrect
    governance/financial information has real consequences.
    """
    try:
        verify_messages = [
            {
                "role": "user",
                "content": (
                    f"{VERIFICATION_PROMPT}\n\n"
                    f"RESPONSE TO VERIFY:\n{response}"
                ),
            }
        ]
        verified = await llm.chat(
            verify_messages,
            system_prompt=(
                "You are a citation verification assistant for CDCN, a Scottish charity. "
                "Your ONLY job is to check that factual claims have proper citations. "
                "Return the response with [UNVERIFIED] tags where needed, or unchanged if OK."
            ),
            skill_used="verification",
        )
        if verified and verified.strip():
            log.info("Verification complete: %d chars input → %d chars output",
                     len(response), len(verified))
            return verified.strip()
    except Exception as exc:
        log.warning("Verification step failed: %s — returning unverified response", exc)
    return response
