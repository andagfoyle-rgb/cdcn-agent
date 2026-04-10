"""
Two-state wake/dream lifecycle manager for CDCN Agent.

AgentStateManager orchestrates the transition between WAKE (R710, full capability)
and DREAM (Pi 5, low-power) modes.  It owns the WoL sequence, LLM endpoint swap,
adapter enable/disable, morning orientation, and overnight summary broadcast.

The module-level ``state`` proxy provides backward-compatibility for code that
imported the old StateManager singleton.  New code should call
``get_state_manager()`` after ``init_state_manager()`` has been called in main.py.
"""
import asyncio
import logging
import time
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

import httpx

from app.config import settings

log = logging.getLogger(__name__)


# ── State enum ────────────────────────────────────────────────────────────────

class AgentState(str, Enum):
    WAKE = "wake"
    TRANSITIONING_TO_DREAM = "transitioning_to_dream"
    DREAM = "dream"
    TRANSITIONING_TO_WAKE = "transitioning_to_wake"


# Alias so old code using `AgentMode.WAKE / AgentMode.DREAM` still works.
AgentMode = AgentState


# ── AgentStateManager ─────────────────────────────────────────────────────────

class AgentStateManager:
    """
    Manages the full lifecycle of wake/dream transitions.

    Constructed in main.py once all dependencies are available and registered
    via init_state_manager().
    """

    def __init__(
        self,
        config,
        llm_client,
        memory_skill,
        dream_worker,
        discord_adapter,
        telegram_adapter,
        web_adapter,
    ) -> None:
        self._config = config
        self._llm = llm_client
        self._memory = memory_skill
        self._dream_worker = dream_worker
        # Build adapter list; None entries (disabled adapters) are skipped.
        self._adapters = [a for a in [discord_adapter, telegram_adapter, web_adapter] if a is not None]
        self._discord = discord_adapter
        self._telegram = telegram_adapter
        self._state: AgentState = AgentState.WAKE
        self._lock = asyncio.Lock()
        self.started_at: datetime = datetime.now(timezone.utc)

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def current_state(self) -> AgentState:
        return self._state

    # Alias for backward-compat with code that uses .mode
    @property
    def mode(self) -> AgentState:
        return self._state

    def is_accepting_messages(self) -> bool:
        """True only when fully in WAKE state — not during transitions."""
        return self._state == AgentState.WAKE

    def status(self) -> dict:
        return {
            "mode": self._state.value,
            "started_at": self.started_at.isoformat(),
        }

    # ── Transitions ───────────────────────────────────────────────────────────

    async def transition_to_dream(self) -> None:
        """
        Full WAKE → DREAM transition sequence.

        Thread-safe: concurrent calls are serialised by _lock.  Calls from
        already-dream or transitioning states are logged and dropped.
        """
        async with self._lock:
            if self._state != AgentState.WAKE:
                log.warning(
                    "transition_to_dream called from state %s — ignoring", self._state
                )
                return

            previous_state = self._state

            try:
                # ── 1. Mark transitioning ─────────────────────────────────────
                self._state = AgentState.TRANSITIONING_TO_DREAM
                await self._audit("transition_to_dream", "starting")
                log.info("Transitioning to DREAM mode")

                # ── 3. Broadcast goodbye to all interfaces ────────────────────
                await self._broadcast_sleep_notice()

                # ── 4. Disable message handling on all adapters ───────────────
                for adapter in self._adapters:
                    adapter.set_accepting(False)

                # ── 5. Write today's journal entry ────────────────────────────
                today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                self._memory.write_journal(
                    today,
                    f"Agent entering rest mode at {datetime.now(timezone.utc).strftime('%H:%M')} UTC.",
                )

                # ── 6. Write current task note ────────────────────────────────
                self._memory.write_current_task("Agent in rest mode — resume on next wake.")

                # ── 7. Switch LLM to dream endpoint ──────────────────────────
                import os
                _provider = os.environ.get("LLM_PROVIDER", "ollama").lower().strip()
                if _provider == "ollama":
                    self._llm.configure(
                        base_url=settings.dream_ollama_base_url,
                        model=settings.dream_model,
                    )
                else:
                    # Cloud provider — keep using it in dream mode too
                    log.info("Dream: keeping cloud LLM provider=%s (no need to switch)", _provider)

                # ── 8. Set state DREAM ────────────────────────────────────────
                self._state = AgentState.DREAM
                await self._audit("transition_to_dream", "complete")
                log.info("Now in DREAM mode")

                # ── 9. Trigger dream worker (non-blocking) ────────────────────
                asyncio.create_task(self._run_dream_cycle())

            except Exception as exc:
                log.error("transition_to_dream failed, rolling back to %s: %s", previous_state.value, exc)
                self._state = previous_state
                # Re-enable adapters if they were disabled
                for adapter in self._adapters:
                    adapter.set_accepting(True)
                await self._audit("transition_to_dream", f"rolled back: {exc}")
                raise

    async def transition_to_wake(self) -> None:
        """
        Full DREAM → WAKE transition sequence, including WoL boot wait,
        cold-start orientation, and morning summary post.
        """
        async with self._lock:
            if self._state != AgentState.DREAM:
                log.warning(
                    "transition_to_wake called from state %s — ignoring", self._state
                )
                return

            previous_state = self._state

            try:
                # ── 1. Mark transitioning ─────────────────────────────────────
                self._state = AgentState.TRANSITIONING_TO_WAKE
                await self._audit("transition_to_wake", "starting")
                log.info("Transitioning to WAKE mode")

                # ── 3. Send WoL magic packet ──────────────────────────────────
                await self._send_wol()

                # ── 4. Poll until R710 Ollama responds ────────────────────────
                await self._wait_for_ollama()

                # ── 5. Switch LLM back to wake endpoint ──────────────────────
                import os
                _provider = os.environ.get("LLM_PROVIDER", "ollama").lower().strip()
                if _provider == "ollama":
                    self._llm.configure(
                        base_url=settings.ollama_base_url,
                        model=settings.ollama_model,
                    )
                else:
                    # Cloud provider (SiliconFlow etc) — restore from env
                    self._llm.configure(
                        base_url=os.environ.get("SILICONFLOW_BASE_URL", "https://api.siliconflow.com/v1"),
                        model=os.environ.get("SILICONFLOW_MODEL", "zai-org/GLM-5"),
                    )
                    log.info("Wake: restored cloud LLM provider=%s", _provider)

                # ── 6. Cold-start orientation ─────────────────────────────────
                orientation = await self._cold_start_orientation()

                # ── 7. Apply approved pending changes ─────────────────────────
                applied = await self._apply_pending_changes()
                if applied:
                    log.info("Applied %d approved pending change(s): %s", len(applied), applied)

                # ── 8. Re-enable message handling on all adapters ─────────────
                for adapter in self._adapters:
                    adapter.set_accepting(True)

                # ── 9. Post overnight summary to Discord / Telegram ───────────
                await self._post_morning_summary(orientation, applied)

                # ── 10. Set state WAKE ────────────────────────────────────────
                self._state = AgentState.WAKE
                await self._audit("transition_to_wake", "complete")
                log.info("Now in WAKE mode")

            except Exception as exc:
                log.error("transition_to_wake failed, rolling back to %s: %s", previous_state.value, exc)
                self._state = previous_state
                await self._audit("transition_to_wake", f"rolled back: {exc}")
                raise

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _audit(self, action: str, detail: str) -> None:
        try:
            from app.storage.audit_log import log_event
            await log_event(actor="state_manager", action=action, detail=detail)
        except Exception as exc:  # noqa: BLE001
            log.warning("Audit log write failed: %s", exc)

    async def _broadcast_sleep_notice(self) -> None:
        msg = (
            f"CDCN Agent is entering rest mode. "
            f"Back online at {settings.wake_start_time}. Goodnight."
        )
        if self._discord:
            try:
                status_id = settings.discord_status_channel_id
                if status_id:
                    await self._discord.send_message(status_id, msg)
            except Exception as exc:  # noqa: BLE001
                log.warning("Discord sleep broadcast failed: %s", exc)
        if self._telegram:
            try:
                chat_id = settings.telegram_notification_chat_id
                if chat_id:
                    await self._telegram.send_message(chat_id, msg)
            except Exception as exc:  # noqa: BLE001
                log.warning("Telegram sleep broadcast failed: %s", exc)

    async def _send_wol(self) -> None:
        mac = settings.wakeonlan_mac
        if not mac:
            log.info("WAKEONLAN_MAC not configured — skipping WoL packet")
            return
        try:
            from wakeonlan import send_magic_packet  # type: ignore[import]
            send_magic_packet(mac, ip_address=settings.wakeonlan_broadcast)
            log.info("WoL magic packet sent to %s via %s", mac, settings.wakeonlan_broadcast)
        except ImportError:
            log.warning("wakeonlan library not installed — cannot send WoL packet")
        except Exception as exc:  # noqa: BLE001
            log.warning("WoL send failed: %s", exc)

    async def _wait_for_ollama(self) -> None:
        """Poll OLLAMA_BASE_URL/api/tags every 10 s, logging progress every 30 s."""
        deadline = time.monotonic() + settings.wakeonlan_boot_wait_secs
        last_log_t = time.monotonic() - 30  # force immediate first log
        attempt = 0
        log.info(
            "Waiting for Ollama at %s (timeout %ds)",
            settings.ollama_base_url,
            settings.wakeonlan_boot_wait_secs,
        )
        while time.monotonic() < deadline:
            attempt += 1
            try:
                async with httpx.AsyncClient(timeout=5) as client:
                    resp = await client.get(f"{settings.ollama_base_url}/api/tags")
                    if resp.status_code == 200:
                        log.info("Ollama responded after %d poll(s)", attempt)
                        return
            except Exception:  # noqa: BLE001
                pass
            if time.monotonic() - last_log_t >= 30:
                remaining = int(deadline - time.monotonic())
                log.info("Still waiting for Ollama — %ds remaining", remaining)
                last_log_t = time.monotonic()
            await asyncio.sleep(10)
        log.warning(
            "Ollama did not respond within %ds — proceeding anyway",
            settings.wakeonlan_boot_wait_secs,
        )

    async def _cold_start_orientation(self) -> str:
        """
        Read recent journal + current task, ask LLM for a 3-5 sentence
        orientation note, and append it to today's session log.
        """
        try:
            journal_text = self._memory.read_recent_journal(3) or ""

            task_text = self._memory.read_current_task() or ""

            context = (
                f"## Recent Journal\n{journal_text}\n\n"
                f"## Current Task\n{task_text}"
            )
            prompt = (
                "You are resuming after an overnight rest period. "
                "Based on the context below, write a brief orientation note (3–5 sentences) "
                "summarising where things stand and what should be prioritised today. "
                "Be concise and practical.\n\n"
                + context
            )
            orientation = await self._llm.chat(
                [{"role": "user", "content": prompt}],
                skill_used="orientation",
            )
            self._memory.log_session_summary(
                "cold_start",
                f"## Morning orientation\n\n{orientation}",
            )
            log.info("Cold-start orientation complete")
            return orientation
        except Exception as exc:  # noqa: BLE001
            log.warning("Cold-start orientation failed: %s", exc)
            return ""

    async def _apply_pending_changes(self) -> list[str]:
        """Apply any pending changes whose status is 'approved'."""
        try:
            from app.storage.pending_changes import apply_approved
            return apply_approved()
        except Exception as exc:  # noqa: BLE001
            log.warning("Pending changes apply failed: %s", exc)
            return []

    async def _run_dream_cycle(self) -> None:
        """Run the full five-task dream cycle (non-blocking, launched as asyncio task)."""
        try:
            await self._dream_worker.run_full_cycle()
        except Exception as exc:  # noqa: BLE001
            log.warning("Dream cycle error: %s", exc)

    async def _post_morning_summary(self, orientation: str, applied: list[str]) -> None:
        """Post overnight summary to Discord status channel and Telegram notification chat."""
        try:
            from app.storage.pending_changes import list_pending
            pending = list_pending()
            still_pending = [p for p in pending if p.get("status") == "pending"]
        except Exception:  # noqa: BLE001
            still_pending = []

        lines = ["**Good morning — CDCN Agent is online.**", ""]
        if orientation:
            lines += [orientation, ""]
        if applied:
            lines.append(f"**Changes applied overnight:** {len(applied)}")
        if still_pending:
            lines.append(f"**Changes awaiting review:** {len(still_pending)}")
        lines.append("_Ready to assist._")

        discord_msg = "\n".join(lines)
        telegram_msg = "Good morning — CDCN Agent is online."

        if self._discord:
            try:
                status_id = settings.discord_status_channel_id
                if status_id:
                    await self._discord.send_message(status_id, discord_msg)
            except Exception as exc:  # noqa: BLE001
                log.warning("Discord morning post failed: %s", exc)
        if self._telegram:
            try:
                chat_id = settings.telegram_notification_chat_id
                if chat_id:
                    await self._telegram.send_message(chat_id, telegram_msg)
            except Exception as exc:  # noqa: BLE001
                log.warning("Telegram morning notify failed: %s", exc)


# ── Module-level singleton ─────────────────────────────────────────────────────
# Wired up by main.py after all dependencies are constructed.

_manager: AgentStateManager | None = None


def init_state_manager(manager: AgentStateManager) -> None:
    """Called once from main.py to register the constructed manager."""
    global _manager
    _manager = manager


def get_state_manager() -> AgentStateManager:
    if _manager is None:
        raise RuntimeError(
            "AgentStateManager not yet initialised — call init_state_manager() first."
        )
    return _manager


# ── Backward-compat proxy ─────────────────────────────────────────────────────
# Old code imports ``from app.state_manager import state, AgentMode``.
# The proxy delegates to the real manager once it is available.

class _StateProxy:
    """Shim so pre-refactor code using `state.mode` / `await state.set_mode()` still works."""

    @property
    def mode(self) -> AgentState:
        return _manager.current_state if _manager else AgentState.WAKE

    def is_accepting_messages(self) -> bool:
        return _manager.is_accepting_messages() if _manager else True

    async def set_mode(self, mode: AgentState) -> None:
        if _manager is None:
            log.warning("state.set_mode called before init_state_manager — ignoring")
            return
        if mode in (AgentState.DREAM, "dream"):
            await _manager.transition_to_dream()
        else:
            await _manager.transition_to_wake()

    def status(self) -> dict:
        return _manager.status() if _manager else {"mode": "wake"}

    # Kept for the /health endpoint in main.py
    @property
    def started_at(self) -> datetime:
        return _manager.started_at if _manager else datetime.now(timezone.utc)


state = _StateProxy()
