"""
Telegram interface adapter using python-telegram-bot v20+.

Allowlist: set TELEGRAM_ALLOWED_USER_IDS=123456,789012 in .env
All allowlisted users receive the "staff" role by default.
Free-text messages are routed through the full agentic loop.
"""
from __future__ import annotations

import logging

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

from app.config import settings
from app.interfaces.base_adapter import BaseAdapter

log = logging.getLogger(__name__)

_DEFAULT_ROLE = "staff"
_MAX_MSG_LEN = 4000  # Telegram hard limit is 4096; leave headroom


def _allowed(update: Update) -> bool:
    uid = update.effective_user.id if update.effective_user else None
    allowed = settings.telegram_allowed_user_id_list
    return not allowed or uid in allowed


async def _send_split(update: Update, text: str) -> None:
    """Send a response, splitting at paragraph boundaries."""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        paragraphs = [text]
    for para in paragraphs:
        while len(para) > _MAX_MSG_LEN:
            await update.message.reply_text(para[:_MAX_MSG_LEN])
            para = para[_MAX_MSG_LEN:]
        if para:
            await update.message.reply_text(para)


class TelegramAdapter(BaseAdapter):
    name = "telegram"

    def __init__(self) -> None:
        super().__init__(router=None)
        self._app: Application | None = None

    async def start(self) -> None:
        if not settings.telegram_bot_token:
            log.warning("TELEGRAM_BOT_TOKEN not set — Telegram adapter disabled.")
            return

        self._app = Application.builder().token(settings.telegram_bot_token).build()
        self._app.add_handler(CommandHandler("start", self._cmd_start))
        self._app.add_handler(CommandHandler("skills", self._cmd_skills))
        self._app.add_handler(CommandHandler("status", self._cmd_status))
        self._app.add_handler(CommandHandler("heartbeat", self._cmd_heartbeat))
        self._app.add_handler(CommandHandler("index", self._cmd_index))
        self._app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._msg_handler)
        )

        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling()
        log.info("Telegram adapter started.")

    async def stop(self) -> None:
        if self._app:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
            log.info("Telegram adapter stopped.")

    async def send_message(self, destination: str, text: str) -> None:
        if self._app:
            await self._app.bot.send_message(chat_id=destination, text=text[:4096])

    async def send_typing(self, channel_id: str) -> None:
        if self._app:
            try:
                await self._app.bot.send_chat_action(
                    chat_id=channel_id, action=ChatAction.TYPING
                )
            except Exception as exc:
                log.debug("send_typing failed for %s: %s", channel_id, exc)

    # ── Guard helper ──────────────────────────────────────────────────────────

    async def _guard(self, update: Update) -> bool:
        """Return False (and notify user) if the message should be blocked."""
        if not _allowed(update):
            return False
        if not self.is_accepting:
            await update.message.reply_text(self.rest_message)
            return False
        return True

    # ── Command handlers ──────────────────────────────────────────────────────

    async def _cmd_start(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update):
            return
        await update.message.reply_text(
            "CDCN Agent online. Send any message to chat, or use:\n"
            "/skills — list available skills\n"
            "/status — show agent status\n"
            "/index <path> — index a document\n"
            "/heartbeat — run heartbeat tasks"
        )

    async def _cmd_skills(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update):
            return
        if self._router is None:
            await update.message.reply_text("Router not ready.")
            return
        names = ", ".join(self._router.skills.keys())
        await update.message.reply_text(f"Available skills: {names}")

    async def _cmd_status(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not _allowed(update):
            return
        from app.state_manager import get_state_manager
        try:
            s = get_state_manager().status()
            await update.message.reply_text(
                f"Mode: {s['mode']}\nOnline since: {s['started_at']}"
            )
        except Exception as exc:
            await update.message.reply_text(f"Status unavailable: {exc}")

    async def _cmd_heartbeat(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update):
            return
        await update.message.reply_text("Running heartbeat tasks…")
        response = await self._collect_response(
            user_id=f"telegram:{update.effective_user.id}",
            role=_DEFAULT_ROLE,
            message="/heartbeat",
            channel_id=str(update.effective_chat.id),
        )
        await _send_split(update, response)

    async def _cmd_index(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update):
            return
        path = " ".join(ctx.args) if ctx.args else ""
        if not path:
            await update.message.reply_text("Usage: /index <file_path>")
            return
        response = await self._collect_response(
            user_id=f"telegram:{update.effective_user.id}",
            role=_DEFAULT_ROLE,
            message=f"/index {path}",
            channel_id=str(update.effective_chat.id),
        )
        await _send_split(update, response)

    # ── Free-text handler ─────────────────────────────────────────────────────

    async def _msg_handler(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._guard(update):
            return
        chat_id = str(update.effective_chat.id)
        user_id = f"telegram:{update.effective_user.id}"
        message = update.message.text or ""
        await self.send_typing(chat_id)
        response = await self._collect_response(
            user_id=user_id,
            role=_DEFAULT_ROLE,
            message=message,
            channel_id=chat_id,
        )
        await _send_split(update, response)
