"""
Web interface adapter — thin module that assembles and re-exports all web components.

Imports from:
  - web_routes.py   — all HTTP route handlers
  - web_socket.py   — WebSocket endpoint
  - web_auth.py     — auth helpers, presence, upload validation
  - web_templates.py — HTML generators (sidebar, page head, login, etc.)
  - web_pages.py    — calendar, action points, funding, memory, pending changes
  - web_admin.py    — admin page + admin API
  - web_dashboard.py — dashboard page + stats API

Static assets live in app/static/{css,js}/.
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter

from app.interfaces.base_adapter import BaseAdapter
from app.interfaces.web_auth import active_ws
from app.interfaces.web_routes import mount_all_routes

log = logging.getLogger(__name__)


# ── Combined router ──────────────────────────────────────────────────────────

web_router = APIRouter()
mount_all_routes(web_router)


# ── WebAdapter ───────────────────────────────────────────────────────────────

class WebAdapter(BaseAdapter):
    """Adapter that exposes the web interface via FastAPI routes + WebSocket."""

    name = "web"

    def __init__(self) -> None:
        super().__init__(router=None)

    async def start(self) -> None:
        log.info("WebAdapter: routes registered via web_router.")

    async def stop(self) -> None:
        for session_key, ws in list(active_ws.items()):
            try:
                await ws.close(code=1001)
            except Exception:
                pass
        active_ws.clear()
        log.info("WebAdapter: stopped.")

    async def send_message(self, destination: str, text: str) -> None:
        ws = active_ws.get(destination)
        if ws:
            try:
                await ws.send_text(json.dumps({"type": "token", "content": text}))
                await ws.send_text(json.dumps({"type": "done"}))
            except Exception as exc:
                log.debug("WebAdapter.send_message failed for %s: %s", destination, exc)

    async def send_typing(self, channel_id: str) -> None:
        pass


def create_web_app() -> WebAdapter:
    """Factory function that returns a ready-to-use WebAdapter instance."""
    return WebAdapter()
