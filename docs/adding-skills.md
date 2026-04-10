# Adding Skills to CDCN Agent

Skills are the agent's capabilities. Each skill is a focused Python class
(optionally backed by a SKILL.md spec) that handles one kind of task:
searching documents, drafting text, checking deadlines, and so on.

---

## Two-Part Skill Structure

Every skill has two components:

| File | Required | Purpose |
|---|---|---|
| `SKILL.md` | Yes | Human-readable specification: what the skill does, when to use it, call format, limitations |
| `skill_name.py` | Optional | Python implementation; required if the skill needs to call code |

Pure-markdown skills (no Python) are useful for "prompt skills" — behaviours
that the LLM can perform just by reading a spec with no extra code.

---

## Pure-Markdown Skill Example

`skills_config/drafts/funding_update/SKILL.md`:

```markdown
# Skill: funding_update

## Description
Generate a one-paragraph funding update suitable for including in a board report.

## USE WHEN
- A trustee asks for a funding update
- The writer skill is drafting a board report and needs a funding summary
- The heartbeat identifies an upcoming deadline

## CALL FORMAT
{"skill": "funding_update", "args": {"funder": "...", "status": "..."}}

## LIMITATIONS
- Does not access external databases or the funder's website
- Requires the caller to provide the funder name and current status
- Output should be reviewed before sending to the funder

## EXAMPLE OUTPUT
> Following our application to the National Lottery Community Fund submitted in
> March, we are awaiting a decision expected by June. Our project officer has
> confirmed the application is progressing through the assessment stage.
```

Pure-markdown skills are not registered in the Python code — they are provided
as context to the LLM via the system prompt loader.

---

## Python-Backed Skill

Python skills inherit from `BaseSkill` and live in `app/skills/`.

```python
# app/skills/my_skill.py
from app.skills.base import BaseSkill, SkillResult


class MySkill(BaseSkill):
    name = "my_skill"            # unique lowercase identifier, no spaces
    description = (
        "One sentence describing what this skill does — shown in cdcn-agent skills"
    )

    async def run(self, query: str = "", **kwargs) -> SkillResult:
        # All skills are async. Return a SkillResult.
        if not query:
            return SkillResult(success=False, error="query is required")

        result = do_something(query)
        return SkillResult(
            success=True,
            output=result,
            metadata={"source": "my_skill"},
        )
```

### SkillResult Fields

| Field | Type | Description |
|---|---|---|
| `success` | `bool` | Whether the skill completed without error |
| `output` | `Any` | The result (string, dict, list — must be JSON-serialisable) |
| `error` | `str` | Error message if `success=False` |
| `metadata` | `dict` | Optional extra information (counts, file paths, etc.) |

### Register the Skill

Add it to the `skills` dict in `app/main.py`:

```python
from app.skills.my_skill import MySkill

skills = {
    ...
    "my_skill": MySkill(),
}
```

Restart the service:
```bash
sudo systemctl restart cdcn-agent
```

---

## Using the Skill Builder (Recommended Starting Point)

The skill builder uses the LLM to draft a new skill from a plain-English description.
It produces a `SKILL.md` and optional Python file in `skills_config/drafts/` for review.

### Example

```bash
cdcn-agent new-skill "Search for all board minutes from the past year and produce a timeline of key decisions"
```

Or from the chat interface:
```
User:  Draft a skill that monitors Companies House for our charity registration number and alerts us if any filings are overdue.

Agent: I'll draft that skill now...
       Skill draft saved to: skills_config/drafts/companies_house_monitor/
       Files:
         SKILL.md              — spec (review before activating)
         companies_house_monitor.py — implementation

       New dependencies required: httpx (already in requirements.txt)

       To activate:
         1. Review skills_config/drafts/companies_house_monitor/SKILL.md
         2. Copy companies_house_monitor.py to app/skills/
         3. Register it in app/main.py skills dict
         4. Restart the agent
```

---

## Review Checklist Before Installing a Skill

Before copying a draft skill into production, check:

- [ ] The SKILL.md clearly states what the skill does and its limitations
- [ ] The Python code only writes to `data/` or `skills_config/` — not system paths
- [ ] No `exec()`, `eval()`, or `subprocess` calls (unless explicitly required)
- [ ] No hardcoded credentials or secrets
- [ ] External HTTP calls are present only if the description required them
- [ ] If the skill modifies `skills_config/`, it uses `pending_changes.propose()` rather than writing directly
- [ ] There is a test in `tests/test_<skill_name>.py`

---

## Testing a Skill

Test a skill directly from the CLI without sending a real chat message:

```bash
# Test with no arguments (skill should handle empty gracefully)
cdcn-agent test-skill search

# Test with specific arguments
cdcn-agent test-skill search --args '{"query": "board minutes 2024"}'

# Test the writer skill
cdcn-agent test-skill writer --args '{"template": "board_minute", "context": "test session"}'
```

Or via Makefile in dev mode:
```bash
# Shortcut — edit the Makefile target as needed
make index   # tests the indexer skill against data/documents/
```

---

## Pending Changes Workflow

If a skill needs to modify the agent's long-term configuration (agents.md,
style_guide.md, templates), it must route the change through the pending-changes
queue rather than writing directly. This ensures a human reviews and approves
the change before it affects the agent's behaviour.

```python
from app.storage.pending_changes import propose

change_id = propose(
    title="Update style guide — add plain-English requirement",
    diff="## Plain English\n\nAll external communications must use plain English...",
    author="my_skill",
    change_type="style",
    target_file="skills_config/memory/style_guide.md",
)
```

The proposed change appears in `cdcn-agent pending` and the `/pending-changes` web UI.
An admin reviews and approves or rejects it.
