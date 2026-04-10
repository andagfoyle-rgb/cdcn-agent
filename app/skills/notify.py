"""
NotifySkill — unified notification routing for proactive outbound messages.

Routes messages to the right channels (Discord, Telegram, Email) based on
urgency and configuration.  Used by:
  - CronjobManager for user-created scheduled reminders
  - Heartbeat for deadline alerts
  - Any skill that needs to proactively push information

Channel selection:
  - "all": sends to all configured channels
  - "discord": Discord status channel only
  - "telegram": Telegram notification chat only
  - "email": Email to board recipients only
  - List of specific channels: ["discord", "email"]

Urgency levels:
  - "info": normal notification (all channels)
  - "warning": highlighted notification (all channels)
  - "urgent": immediate notification to all channels + email subject prefix
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.skills.base import BaseSkill, SkillResult

log = logging.getLogger(__name__)


async def _send_discord(message: str, urgent: bool = False) -> bool:
    """Send a message to the Discord status channel."""
    try:
        from app.config import settings
        channel_id = settings.discord_status_channel_id
        if not channel_id:
            log.debug("Discord status channel not configured — skipping.")
            return False

        # Import the running bot instance
        from app.interfaces.discord_adapter import DiscordAdapter
        # Access the module-level bot if available
        import sys
        adapter_mod = sys.modules.get("app.interfaces.discord_adapter")
        if not adapter_mod:
            return False

        # Try to find the running bot via the adapter
        # The adapter stores its client as self._bot
        # We need to access it through the global adapter reference
        import discord
        for obj in vars(adapter_mod).values():
            if isinstance(obj, DiscordAdapter) and hasattr(obj, "_bot") and obj._bot:
                channel = obj._bot.get_channel(int(channel_id))
                if channel:
                    prefix = "🚨 **URGENT** " if urgent else ""
                    await channel.send(f"{prefix}{message}"[:2000])
                    return True
        return False
    except Exception as exc:
        log.warning("Discord notification failed: %s", exc)
        return False


async def _send_telegram(message: str, urgent: bool = False) -> bool:
    """Send a message to the Telegram notification chat."""
    try:
        from app.config import settings
        chat_id = settings.telegram_notification_chat_id
        if not chat_id:
            log.debug("Telegram notification chat not configured — skipping.")
            return False

        import httpx
        token = settings.telegram_bot_token
        if not token:
            return False

        prefix = "🚨 URGENT: " if urgent else ""
        text = f"{prefix}{message}"

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text[:4000], "parse_mode": "Markdown"},
            )
            return resp.status_code == 200
    except Exception as exc:
        log.warning("Telegram notification failed: %s", exc)
        return False


async def _send_email(
    message: str, subject: str = "", urgent: bool = False,
) -> bool:
    """Send an email notification to board recipients."""
    try:
        from app.interfaces.email_adapter import get_email_adapter

        adapter = get_email_adapter()
        if not adapter:
            log.debug("Email adapter not initialised — skipping.")
            return False

        prefix = "[URGENT] " if urgent else ""
        subj = f"{prefix}{subject or 'CDCN Agent Notification'}"

        return await adapter.send_notification(
            subject=subj,
            body_html=f"<p>{message}</p>",
            body_text=message,
        )
    except Exception as exc:
        log.warning("Email notification failed: %s", exc)
        return False


class NotifySkill(BaseSkill):
    """
    Send proactive notifications across Discord, Telegram, and Email.

    Tool definition (for the LLM):
      - message (str, required): The notification text.
      - channels (list[str], optional): Which channels to use.
        Options: "discord", "telegram", "email". Default: all configured.
      - urgency (str, optional): "info" | "warning" | "urgent". Default: "info".
      - subject (str, optional): Email subject line (email channel only).
      - source (str, optional): What triggered this notification.
    """

    name = "notify"
    description = (
        "Send a notification to Discord, Telegram, and/or Email. "
        "Use for proactive alerts, reminders, or status updates."
    )

    async def run(
        self,
        message: str = "",
        channels: list[str] | None = None,
        urgency: str = "info",
        subject: str = "",
        source: str = "",
        **kwargs: Any,
    ) -> SkillResult:
        if not message:
            return SkillResult(success=False, error="message is required.")

        urgent = urgency == "urgent"
        target_channels = channels or ["discord", "telegram", "email"]

        results: dict[str, bool] = {}
        sent_count = 0

        for ch in target_channels:
            ch = ch.lower().strip()
            if ch == "discord":
                ok = await _send_discord(message, urgent)
                results["discord"] = ok
            elif ch == "telegram":
                ok = await _send_telegram(message, urgent)
                results["telegram"] = ok
            elif ch == "email":
                ok = await _send_email(message, subject, urgent)
                results["email"] = ok
            else:
                results[ch] = False
                log.warning("Unknown notification channel: %s", ch)

            if results.get(ch):
                sent_count += 1

        # Audit log
        try:
            from app.storage.audit_log import log_event
            await log_event(
                actor=source or "notify_skill",
                action="notification_sent",
                target=",".join(target_channels),
                detail=f"urgency={urgency} sent={sent_count}/{len(target_channels)} msg={message[:100]}",
            )
        except Exception:
            pass

        if sent_count == 0:
            log.warning(
                "Notification sent to 0 channels (channels=%s). "
                "Check adapter configuration.",
                target_channels,
            )
            return SkillResult(
                success=True,  # not a skill error — channels just aren't configured
                output=(
                    f"Notification prepared but no channels were available to deliver it. "
                    f"Channels attempted: {', '.join(target_channels)}"
                ),
                metadata={"results": results, "sent_count": 0},
            )

        channel_summary = ", ".join(
            f"{ch}:{'✓' if ok else '✗'}" for ch, ok in results.items()
        )
        log.info(
            "Notification sent: %d/%d channels (%s) source=%s",
            sent_count, len(target_channels), channel_summary, source,
        )

        return SkillResult(
            success=True,
            output=f"Notification sent to {sent_count} channel(s): {channel_summary}",
            metadata={"results": results, "sent_count": sent_count},
        )
