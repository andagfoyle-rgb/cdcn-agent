"""
{{DESCRIPTION}}
"""
from app.skills.base import BaseSkill, SkillResult


class {{SKILL_NAME_PASCAL}}Skill(BaseSkill):
    name = "{{SKILL_NAME}}"
    description = "{{DESCRIPTION}}"

    async def run(self, **kwargs) -> SkillResult:
        # TODO: implement skill logic
        return SkillResult(success=True, output=None)
