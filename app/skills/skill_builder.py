"""
SkillBuilderSkill — draft new skills using the LLM.

Accepts a plain-English description.  Reads existing SKILL.md files and
skill_template.md as examples, then asks the configured LLM to produce:
  • SKILL.md  — human-readable spec with USE WHEN / CALL FORMAT sections
  • Python implementation (optional, may be empty if trivial)
  • Suggested filename
  • New package dependencies (if any)
  • Plain-English explanation and limitations

Output is saved to skills_config/drafts/{skill_name}/.
Nothing is auto-installed or auto-registered.  The operator reviews the
draft and follows the returned instructions to add it manually.

Security constraints given to the LLM
──────────────────────────────────────
  • No external HTTP calls unless the description explicitly requests it
  • Filesystem access limited to data/ and skills_config/
  • Simple, auditable code only — no exec(), no subprocess unless critical
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from app.skills.base import BaseSkill, SkillResult

log = logging.getLogger(__name__)

_DRAFTS_DIR = Path("skills_config/drafts")
_TEMPLATE_FILE = Path("skills_config/skill_template.md")
_EXISTING_SKILLS_DIR = Path("skills_config")

_SYSTEM_PROMPT = """\
You are a senior Python developer drafting a new skill for CDCN Agent, \
a document-management assistant for a Scottish community development charity.

Rules:
- Prioritise simple, readable, auditable code.
- No external HTTP calls unless the description explicitly requests it.
- Filesystem access is limited to data/ and skills_config/ directories.
- Never use exec(), eval(), or subprocess unless the description requires it.
- Follow the existing skill pattern: inherit BaseSkill, implement async run(**kwargs), \
return SkillResult(success=…, output=…, error=…).

Respond using EXACTLY this format (all sections required, even if empty):

## SKILL.md
<full SKILL.md content — name, description, USE WHEN, CALL FORMAT, LIMITATIONS>

## Python Implementation
<full Python file content, or "# No separate implementation needed.">

## Suggested Filename
<snake_case_name.py>

## New Dependencies
<comma-separated package names, or "none">

## Explanation
<2–4 sentences plain-English summary of what the skill does and its limitations>
"""


def _load_examples() -> str:
    """
    Collect up to 3 existing SKILL.md files as few-shot examples.
    Also includes skill_template.md if present.
    """
    parts: list[str] = []

    if _TEMPLATE_FILE.exists():
        parts.append(f"### skill_template.md\n{_TEMPLATE_FILE.read_text()[:800]}")

    count = 0
    for skill_md in sorted(_EXISTING_SKILLS_DIR.rglob("SKILL.md")):
        if count >= 3:
            break
        try:
            parts.append(f"### {skill_md.parent.name}/SKILL.md\n{skill_md.read_text()[:600]}")
            count += 1
        except OSError:
            pass

    if not parts:
        return ""
    return "## Existing skill examples (for style reference)\n\n" + "\n\n".join(parts)


def _parse_sections(text: str) -> dict[str, str]:
    """
    Split the LLM response into named sections using ## headings.
    Returns a dict keyed by the heading text (stripped, lowercased for matching).
    """
    sections: dict[str, str] = {}
    current_key = ""
    current_lines: list[str] = []

    for line in text.splitlines():
        if line.startswith("## "):
            if current_key:
                sections[current_key] = "\n".join(current_lines).strip()
            current_key = line[3:].strip()
            current_lines = []
        else:
            current_lines.append(line)

    if current_key:
        sections[current_key] = "\n".join(current_lines).strip()

    return sections


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9_]", "_", name.lower()).strip("_")


class SkillBuilderSkill(BaseSkill):
    """
    Draft a new skill from a plain-English description.

    Call format: {"skill": "skill_builder", "args": {"description": "…"}}

    Returns instructions for the operator to review and register the draft.
    """

    name = "skill_builder"
    description = (
        "Draft a new CDCN Agent skill from a plain-English description. "
        "Produces a SKILL.md spec and optional Python implementation saved to "
        "skills_config/drafts/. Operator must review before activating."
    )

    async def run(self, description: str = "", **kwargs) -> SkillResult:
        if not description or len(description.strip()) < 10:
            return SkillResult(
                success=False,
                error="description is required (minimum 10 characters).",
            )

        # ── Build prompt ──────────────────────────────────────────────────────
        examples = _load_examples()
        user_message = (
            f"{examples}\n\n" if examples else ""
        ) + f"## New skill description\n\n{description.strip()}"

        # ── Call LLM ──────────────────────────────────────────────────────────
        try:
            from app.llm_client import llm_client
            raw = await llm_client.chat(
                [{"role": "user", "content": user_message}],
                system_prompt=_SYSTEM_PROMPT,
                skill_used="skill_builder",
            )
        except Exception as exc:
            return SkillResult(success=False, error=f"LLM call failed: {exc}")

        # ── Parse response ────────────────────────────────────────────────────
        sections = _parse_sections(raw)

        skill_md = sections.get("SKILL.md", "").strip()
        py_impl = sections.get("Python Implementation", "").strip()
        suggested_filename = sections.get("Suggested Filename", "").strip()
        dependencies = sections.get("New Dependencies", "none").strip()
        explanation = sections.get("Explanation", "").strip()

        if not skill_md:
            return SkillResult(
                success=False,
                error="LLM did not produce a SKILL.md section. Raw response:\n" + raw[:500],
            )

        # ── Derive skill name ─────────────────────────────────────────────────
        if suggested_filename:
            skill_name = _slug(Path(suggested_filename).stem)
        else:
            # Infer from description (first 3 words)
            skill_name = _slug("_".join(description.split()[:3]))
        if not skill_name:
            skill_name = "new_skill"

        # ── Save to drafts/ ───────────────────────────────────────────────────
        draft_dir = _DRAFTS_DIR / skill_name
        draft_dir.mkdir(parents=True, exist_ok=True)

        (draft_dir / "SKILL.md").write_text(skill_md)

        py_path: Path | None = None
        if py_impl and py_impl.strip() != "# No separate implementation needed.":
            py_path = draft_dir / f"{skill_name}.py"
            py_path.write_text(py_impl)

        (draft_dir / "description.txt").write_text(description.strip())

        # ── Build operator instructions ───────────────────────────────────────
        dep_note = (
            f"Install dependencies first:\n  pip install {dependencies}"
            if dependencies.lower() != "none"
            else "No new dependencies required."
        )
        py_note = (
            f"Python file saved to: {py_path}"
            if py_path
            else "No separate Python file (logic may be inline in SKILL.md)."
        )
        instructions = (
            f"Skill draft saved to: {draft_dir}\n\n"
            f"Files:\n"
            f"  SKILL.md       — spec (review before activating)\n"
            + (f"  {skill_name}.py — implementation\n" if py_path else "")
            + f"\n{dep_note}\n\n"
            f"To activate:\n"
            f"  1. Review {draft_dir}/SKILL.md and {py_path or 'implementation'}.\n"
            f"  2. Copy {skill_name}.py to app/skills/{skill_name}.py.\n"
            f"  3. Register it in app/main.py skills dict.\n"
            f"  4. Restart the agent.\n\n"
            f"Explanation: {explanation}"
        )

        return SkillResult(
            success=True,
            output=instructions,
            metadata={
                "skill_name": skill_name,
                "draft_dir": str(draft_dir),
                "has_python": py_path is not None,
                "dependencies": dependencies,
            },
        )
