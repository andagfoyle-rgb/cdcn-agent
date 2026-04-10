"""
Base class for all interface adapters (Discord, Telegram, Web).
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from app.config import settings

if TYPE_CHECKING:
    from app.gateway.router import AgentRouter

log = logging.getLogger(__name__)


class BaseAdapter(ABC):
    name: str = "base"

    def __init__(self, router: "AgentRouter | None" = None) -> None:
        self._router: "AgentRouter | None" = router
        self._accepting: bool = True

    def set_router(self, router: "AgentRouter") -> None:
        """Late-bind the router — called from main.py after all deps are wired."""
        self._router = router

    def set_accepting(self, accepting: bool) -> None:
        """Enable or disable message handling. Called by AgentStateManager."""
        self._accepting = accepting

    @property
    def is_accepting(self) -> bool:
        return self._accepting

    @property
    def rest_message(self) -> str:
        return (
            f"CDCN Agent is currently in rest mode and will be back online "
            f"at {settings.wake_start_time}. "
            f"For urgent matters contact [admin contact]."
        )

    async def _collect_response(
        self,
        user_id: str,
        role: str,
        message: str,
        channel_id: str = "",
        display_name: str = "",
    ) -> str:
        """Collect the full agentic loop response into a single string."""
        if self._router is None:
            log.error("_collect_response called before router was set on %s", self.name)
            return "Agent router not ready — please try again in a moment."
        parts: list[str] = []
        async for token in self._router.handle_message(
            user_id=user_id,
            role=role,
            message=message,
            channel_id=channel_id,
            display_name=display_name,
        ):
            parts.append(token)
        return "".join(parts)

    @abstractmethod
    async def start(self) -> None:
        """Start the adapter (connect, poll, bind, etc.)."""
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Gracefully stop the adapter."""
        ...

    @abstractmethod
    async def send_message(self, destination: str, text: str) -> None:
        """Send a message to a channel / chat / session."""
        ...

    async def send_typing(self, channel_id: str) -> None:
        """Send a typing indicator. Override in adapters that support it."""
