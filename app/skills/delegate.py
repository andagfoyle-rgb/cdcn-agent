"""
DelegateSkill — break complex tasks into parallel sub-tasks.

Runs multiple existing skills concurrently and aggregates their results.
This enables the agent to parallelise work like:
  - Board pack: action points + deadlines + funding updates simultaneously
  - Meeting prep: search minutes + check deadlines + gather funding in parallel
  - Comprehensive search: multiple search queries at once

The LLM specifies a list of sub-tasks, each with a skill name and arguments.
The delegate skill runs them all via asyncio.gather and returns the combined
results.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from app.skills.base import BaseSkill, SkillResult

log = logging.getLogger(__name__)

# Maximum concurrent sub-tasks to prevent resource exhaustion on Pi 5
_MAX_CONCURRENT = 5

# Maximum time for all sub-tasks combined (seconds)
_TIMEOUT_SECS = 120


class SubTask:
    """A single sub-task to delegate to an existing skill."""

    def __init__(
        self,
        skill: str,
        args: dict[str, Any] | None = None,
        label: str = "",
    ):
        self.skill = skill
        self.args = args or {}
        self.label = label or skill


class DelegateSkill(BaseSkill):
    """
    Run multiple skills in parallel and aggregate results.

    Tool definition (for the LLM):
      - tasks (list[dict], required): List of sub-tasks.
        Each dict has:
          - skill (str): Name of the skill to call.
          - args (dict, optional): Arguments for the skill.
          - label (str, optional): Human-readable label for this sub-task.
      - summary_prompt (str, optional): Instructions for how to combine results.
    """

    name = "delegate"
    description = (
        "Run multiple skills in parallel to handle complex, multi-step tasks "
        "efficiently. Specify a list of sub-tasks with skill names and arguments."
    )

    def __init__(self, skills: dict[str, BaseSkill] | None = None) -> None:
        self._skills = skills or {}

    def set_skills(self, skills: dict[str, BaseSkill]) -> None:
        """Called by main.py after all skills are constructed."""
        self._skills = skills

    async def run(
        self,
        tasks: list[dict[str, Any]] | None = None,
        summary_prompt: str = "",
        **kwargs: Any,
    ) -> SkillResult:
        if not tasks:
            return SkillResult(success=False, error="tasks list is required.")

        if len(tasks) > _MAX_CONCURRENT:
            return SkillResult(
                success=False,
                error=f"Too many sub-tasks ({len(tasks)}). Maximum is {_MAX_CONCURRENT}.",
            )

        # Parse sub-tasks
        sub_tasks: list[SubTask] = []
        for t in tasks:
            if isinstance(t, dict):
                skill = t.get("skill", "")
                if not skill:
                    continue
                if skill not in self._skills:
                    return SkillResult(
                        success=False,
                        error=f"Unknown skill: '{skill}'. Available: {', '.join(sorted(self._skills.keys()))}",
                    )
                sub_tasks.append(SubTask(
                    skill=skill,
                    args=t.get("args", {}),
                    label=t.get("label", skill),
                ))

        if not sub_tasks:
            return SkillResult(success=False, error="No valid sub-tasks found.")

        log.info(
            "Delegating %d sub-tasks: %s",
            len(sub_tasks),
            [st.label for st in sub_tasks],
        )

        t0 = time.monotonic()

        # Run all sub-tasks concurrently with timeout
        async def _run_one(st: SubTask) -> tuple[str, SkillResult]:
            try:
                skill = self._skills[st.skill]
                result = await skill.run(**st.args)
                return st.label, result
            except Exception as exc:
                log.warning("Sub-task '%s' failed: %s", st.label, exc)
                return st.label, SkillResult(success=False, error=str(exc))

        try:
            results = await asyncio.wait_for(
                asyncio.gather(*[_run_one(st) for st in sub_tasks]),
                timeout=_TIMEOUT_SECS,
            )
        except asyncio.TimeoutError:
            elapsed = time.monotonic() - t0
            return SkillResult(
                success=False,
                error=f"Sub-tasks timed out after {elapsed:.0f}s (limit: {_TIMEOUT_SECS}s).",
            )

        elapsed = time.monotonic() - t0

        # Aggregate results
        combined: dict[str, Any] = {}
        successes = 0
        failures = 0

        for label, result in results:
            combined[label] = {
                "success": result.success,
                "output": result.output if result.success else None,
                "error": result.error if not result.success else None,
            }
            if result.success:
                successes += 1
            else:
                failures += 1

        # Build human-readable output
        output_parts: list[str] = []
        output_parts.append(
            f"**Delegation complete** — {successes} succeeded, "
            f"{failures} failed ({elapsed:.1f}s)\n"
        )
        for label, result in results:
            status = "✓" if result.success else "✗"
            output_text = (
                str(result.output)[:500] if result.success
                else f"Error: {result.error}"
            )
            output_parts.append(f"### {status} {label}\n{output_text}\n")

        output = "\n".join(output_parts)

        log.info(
            "Delegation complete: %d/%d succeeded in %.1fs",
            successes, len(sub_tasks), elapsed,
        )

        # Audit
        try:
            from app.storage.audit_log import log_event
            await log_event(
                actor="delegate_skill",
                action="delegation_complete",
                target=",".join(st.label for st in sub_tasks),
                detail=f"success={successes} fail={failures} elapsed={elapsed:.1f}s",
            )
        except Exception:
            pass

        return SkillResult(
            success=True,
            output=output,
            metadata={
                "results": combined,
                "successes": successes,
                "failures": failures,
                "elapsed_secs": round(elapsed, 1),
                "summary_prompt": summary_prompt,
            },
        )
