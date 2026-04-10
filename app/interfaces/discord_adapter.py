"""
Discord interface adapter using discord.py v2.3+.

Role mapping: set DISCORD_ROLE_MAPPING="Board->admin,Staff->staff" in .env
Role names on the left are Discord server role names; values on the right are
the CDCN internal role used for the system prompt and skill access control.
Free-text messages are routed through the full agentic loop.
"""
from __future__ import annotations

import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands

from app.config import settings
from app.interfaces.base_adapter import BaseAdapter

log = logging.getLogger(__name__)

_PARAGRAPH_DELAY = 0.5   # seconds between paragraph bursts
_MAX_MSG_LEN = 1990       # Discord hard limit is 2000; leave headroom


def _map_role(member: discord.Member) -> str:
    """Map a Discord server role to an internal CDCN role via DISCORD_ROLE_MAPPING."""
    mapping_str = settings.discord_role_mapping
    if not mapping_str:
        return "user"
    member_role_names = {r.name for r in member.roles}
    for entry in mapping_str.split(","):
        entry = entry.strip()
        if "->" not in entry:
            continue
        discord_role, cdcn_role = entry.split("->", 1)
        if discord_role.strip() in member_role_names:
            return cdcn_role.strip()
    return "user"


def _channel_allowed(channel_id: int) -> bool:
    allowed = settings.discord_allowed_channel_id_list
    return not allowed or channel_id in allowed


def _role_allowed(member: discord.Member) -> bool:
    allowed = set(settings.discord_allowed_role_name_list)
    if not allowed:
        return True
    return any(r.name in allowed for r in member.roles)


async def _send_split(channel: discord.abc.Messageable, text: str) -> None:
    """Split at paragraph boundaries and send with a brief delay between bursts."""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        paragraphs = [text]
    for i, para in enumerate(paragraphs):
        while len(para) > _MAX_MSG_LEN:
            await channel.send(para[:_MAX_MSG_LEN])
            para = para[_MAX_MSG_LEN:]
            await asyncio.sleep(_PARAGRAPH_DELAY)
        if para:
            await channel.send(para)
        if i < len(paragraphs) - 1:
            await asyncio.sleep(_PARAGRAPH_DELAY)


class CDCNBot(commands.Bot):
    def __init__(self, *args, adapter: "DiscordAdapter", **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._adapter = adapter

    async def setup_hook(self) -> None:
        """Register slash commands after login."""
        await self.tree.sync()
        log.info("Discord slash commands synced")

    async def on_ready(self) -> None:
        log.info("Discord bot ready as %s", self.user)
        status_id = settings.discord_status_channel_id
        if status_id:
            ch = self.get_channel(int(status_id))
            if ch:
                try:
                    await ch.send("CDCN Agent online.")
                except Exception as exc:
                    log.warning("Status channel post failed: %s", exc)

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        if not _channel_allowed(message.channel.id):
            return
        if isinstance(message.author, discord.Member) and not _role_allowed(message.author):
            return
        if not self._adapter.is_accepting:
            await message.channel.send(self._adapter.rest_message)
            return

        # Let prefix commands (!) be handled by commands framework
        await self.process_commands(message)

        # Route free text through the agentic loop
        if message.content and not message.content.startswith(self.command_prefix):
            role = (
                _map_role(message.author)
                if isinstance(message.author, discord.Member)
                else "user"
            )
            channel_id = str(message.channel.id)
            user_id = f"discord:{message.author.id}"
            display_name = message.author.display_name
            async with message.channel.typing():
                response = await self._adapter._collect_response(
                    user_id=user_id,
                    role=role,
                    message=message.content,
                    channel_id=channel_id,
                    display_name=display_name,
                )
            await _send_split(message.channel, response)


class DiscordAdapter(BaseAdapter):
    name = "discord"

    def __init__(self) -> None:
        super().__init__(router=None)
        intents = discord.Intents.default()
        intents.message_content = True
        self._bot = CDCNBot(command_prefix="!", intents=intents, adapter=self)
        self._setup_slash_commands()

    def _setup_slash_commands(self) -> None:
        tree = self._bot.tree
        adapter = self

        @tree.command(name="chat", description="Chat with CDCN Agent")
        @app_commands.describe(message="Your message to the agent")
        async def slash_chat(interaction: discord.Interaction, message: str) -> None:
            if not adapter.is_accepting:
                await interaction.response.send_message(adapter.rest_message, ephemeral=True)
                return
            if not isinstance(interaction.user, discord.Member):
                await interaction.response.send_message("DMs not supported.", ephemeral=True)
                return
            if not _role_allowed(interaction.user):
                await interaction.response.send_message(
                    "You don't have access to this bot.", ephemeral=True
                )
                return
            await interaction.response.defer()
            role = _map_role(interaction.user)
            channel_id = str(interaction.channel_id or interaction.channel.id)
            user_id = f"discord:{interaction.user.id}"
            display_name = interaction.user.display_name
            async with interaction.channel.typing():
                response = await adapter._collect_response(
                    user_id=user_id,
                    role=role,
                    message=message,
                    channel_id=channel_id,
                    display_name=display_name,
                )
            if len(response) <= 2000:
                await interaction.followup.send(response)
            else:
                await interaction.followup.send(response[:2000])
                await _send_split(interaction.channel, response[2000:])

        @tree.command(name="status", description="Show agent status")
        async def slash_status(interaction: discord.Interaction) -> None:
            from app.state_manager import get_state_manager
            try:
                s = get_state_manager().status()
                await interaction.response.send_message(
                    f"Mode: **{s['mode']}** — Online since: {s['started_at']}",
                    ephemeral=True,
                )
            except Exception as exc:
                await interaction.response.send_message(
                    f"Status unavailable: {exc}", ephemeral=True
                )

        @tree.command(name="sleep", description="[Admin] Transition agent to dream mode")
        async def slash_sleep(interaction: discord.Interaction) -> None:
            if not isinstance(interaction.user, discord.Member):
                await interaction.response.send_message("Not available in DMs.", ephemeral=True)
                return
            if _map_role(interaction.user) != "admin":
                await interaction.response.send_message("Admin only.", ephemeral=True)
                return
            await interaction.response.defer(ephemeral=True)
            from app.state_manager import get_state_manager
            await get_state_manager().transition_to_dream()
            await interaction.followup.send("Transition to dream mode initiated.")

        @tree.command(name="wake", description="[Admin] Transition agent to wake mode")
        async def slash_wake(interaction: discord.Interaction) -> None:
            if not isinstance(interaction.user, discord.Member):
                await interaction.response.send_message("Not available in DMs.", ephemeral=True)
                return
            if _map_role(interaction.user) != "admin":
                await interaction.response.send_message("Admin only.", ephemeral=True)
                return
            await interaction.response.defer(ephemeral=True)
            from app.state_manager import get_state_manager
            await get_state_manager().transition_to_wake()
            await interaction.followup.send("Transition to wake mode initiated.")

    async def start(self) -> None:
        if not settings.discord_bot_token:
            log.warning("DISCORD_BOT_TOKEN not set — Discord adapter disabled.")
            return
        asyncio.create_task(self._bot.start(settings.discord_bot_token))
        log.info("Discord adapter starting.")

    async def stop(self) -> None:
        if not self._bot.is_closed():
            await self._bot.close()
            log.info("Discord adapter stopped.")

    async def send_message(self, destination: str, text: str) -> None:
        ch = self._bot.get_channel(int(destination))
        if ch:
            await _send_split(ch, text)

    async def send_typing(self, channel_id: str) -> None:
        ch = self._bot.get_channel(int(channel_id))
        if ch:
            try:
                async with ch.typing():
                    pass
            except Exception as exc:
                log.debug("send_typing failed for channel %s: %s", channel_id, exc)
