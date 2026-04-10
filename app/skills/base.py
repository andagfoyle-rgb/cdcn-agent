"""
Base class for all agent skills.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SkillResult:
    success: bool
    output: Any = None
    error: str = ""
    metadata: dict = field(default_factory=dict)


class BaseSkill(ABC):
    """Every skill must implement `run` and expose a `name`."""

    name: str = "base"
    description: str = ""

    @abstractmethod
    async def run(self, **kwargs) -> SkillResult:
        ...

    async def __call__(self, **kwargs) -> SkillResult:
        return await self.run(**kwargs)
