"""
CDCN Agent — FastAPI entry point.

Startup sequence:
  1. Construct all adapters, skills, and the AgentRouter.
  2. Register the router singleton and bind it to all adapters.
  3. Build AgentStateManager and register it.
  4. Mount web_router (chat UI, login, WebSocket) at root.
  5. Apply security-headers middleware.
  6. Start adapters concurrently.
  7. Start APScheduler.
  8. Boot-time state reconciliation (wake/dream based on clock).

Shutdown sequence:
  1. Stop scheduler.
  2. Stop adapters.
  3. Write current-task note for resume context.
"""
import asyncio
import logging
import signal
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.staticfiles import StaticFiles

from app.config import settings
from app.gateway.router import AgentRouter, gateway_router, set_router_instance
from app.gateway.session import SessionManager
from app.interfaces.discord_adapter import DiscordAdapter
from app.interfaces.telegram_bot import TelegramAdapter
from app.interfaces.web import WebAdapter, web_router
from app.llm_client import llm_client
from app.skills.dream_worker import DreamWorkerSkill
from app.skills.indexer import IndexerSkill
from app.skills.memory import MemorySkill
from app.skills.scheduler import get_scheduler, start_scheduler
from app.skills.search import SearchSkill
from app.skills.skill_builder import SkillBuilderSkill
from app.skills.funding_feed import FundingFeedSkill
from app.skills.calendar_manager import CalendarManagerSkill
from app.skills.document_editor import DocumentEditorSkill
from app.skills.writer import WriterSkill
from app.state_manager import AgentState, AgentStateManager, init_state_manager, state
from app.storage import audit_log, pending_changes
from app.storage.vector_store import vector_store

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger(__name__)


# ── Security-headers middleware ────────────────────────────────────────────────


class _SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        # connect-src 'self' allows same-origin WebSocket (ws:/wss:)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; connect-src 'self' ws: wss:; "
            "img-src 'self' data:; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "script-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com https://cdn.jsdelivr.net;"
        )
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        return response


# ── Wake-hours helper ─────────────────────────────────────────────────────────


def _in_wake_hours() -> bool:
    """Return True if the current UTC time falls within the configured wake window."""

    def _parse(t: str) -> int:
        h, m = t.split(":")
        return int(h) * 60 + int(m)

    now = datetime.now(timezone.utc)
    now_min = now.hour * 60 + now.minute
    wake_min = _parse(settings.wake_start_time)
    end_min = _parse(settings.wake_end_time)

    if wake_min <= end_min:
        return wake_min <= now_min < end_min
    else:
        # Midnight-crossing window e.g. 22:00–07:00
        return now_min >= wake_min or now_min < end_min


# ── Lifespan ──────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Build adapters ─────────────────────────────────────────────────────
    discord_adapter = DiscordAdapter()
    telegram_adapter = TelegramAdapter()
    web_adapter = WebAdapter()

    # ── Build skills ───────────────────────────────────────────────────────
    memory_skill = MemorySkill()
    session_manager = SessionManager()
    dream_worker = DreamWorkerSkill(
        llm_client=llm_client,
        memory_skill=memory_skill,
        vector_store=vector_store,
        session_manager=session_manager,
        pending_changes=pending_changes,
        audit_log=audit_log,
    )
    skills = {
        "search": SearchSkill(),
        "indexer": IndexerSkill(),
        "writer": WriterSkill(),
        "dream_worker": dream_worker,
        "skill_builder": SkillBuilderSkill(),
        "memory": memory_skill,
        "funding_feed": FundingFeedSkill(),
        "document_editor": DocumentEditorSkill(),
        "calendar_manager": CalendarManagerSkill(),
    }

    # ── Build and register AgentRouter ────────────────────────────────────
    router = AgentRouter(
        skills=skills,
        llm=llm_client,
        memory_skill=memory_skill,
    )
    set_router_instance(router)

    # Bind router to all adapters
    for adapter in (discord_adapter, telegram_adapter, web_adapter):
        adapter.set_router(router)

    # ── Build and register AgentStateManager ──────────────────────────────
    manager = AgentStateManager(
        config=settings,
        llm_client=llm_client,
        memory_skill=memory_skill,
        dream_worker=dream_worker,
        discord_adapter=discord_adapter,
        telegram_adapter=telegram_adapter,
        web_adapter=web_adapter,
    )
    init_state_manager(manager)

    # ── Start adapters concurrently ────────────────────────────────────────
    await asyncio.gather(
        discord_adapter.start(),
        telegram_adapter.start(),
        web_adapter.start(),
        return_exceptions=True,
    )

    # ── Start scheduler ────────────────────────────────────────────────────
    start_scheduler()

    # ── Boot-time state reconciliation ────────────────────────────────────
    in_wake = _in_wake_hours()
    if in_wake and manager.current_state == AgentState.DREAM:
        log.info("Boot: within wake hours but state is DREAM — triggering immediate wake")
        asyncio.create_task(manager.transition_to_wake())
    elif not in_wake and manager.current_state == AgentState.WAKE:
        log.info("Boot: outside wake hours — triggering immediate dream transition")
        asyncio.create_task(manager.transition_to_dream())
    else:
        log.info(
            "Boot: state=%s in_wake=%s — no immediate transition needed",
            manager.current_state.value,
            in_wake,
        )

    log.info(
        "CDCN Agent ready — mode=%s host=%s port=%d",
        manager.current_state.value,
        settings.gateway_bind_host,
        settings.gateway_port,
    )

    yield

    # ── Shutdown ───────────────────────────────────────────────────────────
    log.info("CDCN Agent shutting down.")

    # Write a resume note so morning orientation has context
    try:
        memory_skill.write_current_task(
            f"Agent shut down at {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC."
        )
    except Exception as exc:
        log.warning("Could not write shutdown note: %s", exc)

    scheduler = get_scheduler()
    if scheduler.running:
        scheduler.shutdown(wait=False)

    await asyncio.gather(
        discord_adapter.stop(),
        telegram_adapter.stop(),
        web_adapter.stop(),
        return_exceptions=True,
    )
    log.info("CDCN Agent stopped.")


# ── FastAPI app ────────────────────────────────────────────────────────────────

app = FastAPI(title="CDCN Agent", version="0.1.0", lifespan=lifespan)

app.add_middleware(_SecurityHeadersMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://cdcnagent.com", "http://localhost:8400", "http://127.0.0.1:8400"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
)

# Static files (JS libs, fonts)
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Web UI routes (login, chat, WebSocket, pending-changes) — root mount
app.include_router(web_router)

# Agent API (chat, status, sessions) — /api prefix
app.include_router(gateway_router, prefix="/api")


@app.get("/health")
async def health():
    return {"status": "ok", **state.status()}
