"""
ClarifySkill — ask the user for clarification before acting.

When the LLM is uncertain about what the user wants (e.g. "which meeting?",
"which funding deadline?", "minutes from when?"), it can invoke this skill
to pause and request additional information.

The skill returns a structured clarification request that the agentic loop
yields directly to the user, halting the current turn until the user replies.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.skills.base import BaseSkill, SkillResult

log = logging.getLogger(__name__)


@dataclass
class ClarificationRequest:
    """Structured request for user clarification."""

    question: str
    options: list[str] = field(default_factory=list)
    context: str = ""


class ClarifySkill(BaseSkill):
    """
    Ask the user a clarifying question before proceeding.

    The LLM calls this when it needs more information to give an accurate
    answer.  The agentic loop detects the clarify skill result and yields
    the question directly to the user instead of making another LLM call.

    Tool definition (for the LLM):
      - question (str, required): The clarifying question to ask.
      - options (list[str], optional): Suggested answers the user can pick from.
      - context (str, optional): Brief explanation of why clarification is needed.
    """

    name = "clarify"
    description = (
        "Ask the user a clarifying question when more information is needed "
        "before you can give an accurate answer."
    )

    async def run(
        self,
        question: str = "",
        options: list[str] | None = None,
        context: str = "",
        **kwargs: Any,
    ) -> SkillResult:
        if not question:
            return SkillResult(success=False, error="A question is required.")

        options = options or []

        # Build the user-facing message
        parts: list[str] = []
        if context:
            parts.append(f"*{context}*\n")
        parts.append(question)
        if options:
            parts.append("\nHere are some options:")
            for i, opt in enumerate(options, 1):
                parts.append(f"  {i}. {opt}")

        message = "\n".join(parts)

        log.info(
            "Clarification requested: %s (options=%d)",
            question[:80],
            len(options),
        )

        return SkillResult(
            success=True,
            output=message,
            metadata={
                "type": "clarification",
                "question": question,
                "options": options,
                "context": context,
            },
        )
