# Discord Bot Setup

CDCN Agent connects to Discord as a bot. It answers questions in permitted
channels and posts scheduled reports (heartbeat, weekly digest, governance
reminders) to a dedicated status channel.

The bot connects **outbound only** — no Tailscale or inbound firewall rules
are required on the Pi.

---

## 1. Create a Bot Application

1. Go to [discord.com/developers/applications](https://discord.com/developers/applications)
2. Click **New Application** — name it "CDCN Agent"
3. Go to **Bot** → click **Reset Token** → copy the token
4. Under **Privileged Gateway Intents**, enable:
   - ✅ **Message Content Intent** (required — without this the bot cannot read messages)
5. Save changes

Set in `.env`:
```env
DISCORD_BOT_TOKEN=your-bot-token-here
```

---

## 2. Set Required Permissions

Generate an invite URL with the minimum required permissions:

| Permission | Why needed |
|---|---|
| Read Messages / View Channels | Read questions from users |
| Send Messages | Post answers and reports |
| Create Public Threads | Thread replies to keep channels tidy |
| Use Slash Commands | Register slash commands (future use) |
| Read Message History | Context for multi-turn conversations |

**Invite URL:**
```
https://discord.com/api/oauth2/authorize?client_id=YOUR_CLIENT_ID&permissions=292057785344&scope=bot+applications.commands
```

Replace `YOUR_CLIENT_ID` with the Application ID from the **General Information** tab.

---

## 3. Recommended Channel Structure

Create three channels in your server:

| Channel | Purpose | Who has access |
|---|---|---|
| `#cdcn-agent` | General queries — ask the agent anything | All trustees |
| `#cdcn-admin` | Staff and admin interactions | Staff + Admin roles |
| `#cdcn-status` | Automated reports (heartbeat, digest, governance) — read-only for trustees | All trustees (read), bot (write) |

Make `#cdcn-status` read-only for regular members: Server Settings → Channels → Permissions → deny "Send Messages" for the @everyone role.

---

## 4. Get Channel and Role IDs

Enable **Developer Mode** in Discord:
Settings → Advanced → Developer Mode → On

- **Channel ID**: Right-click any channel → **Copy Channel ID**
- **Role ID**: Server Settings → Roles → right-click a role → **Copy Role ID**
- **Your user ID**: Right-click your own name → **Copy User ID**

---

## 5. Configure .env

```env
# Channel IDs (comma-separated)
DISCORD_ALLOWED_CHANNEL_IDS=123456789012345678,987654321098765432

# Role names that are allowed to interact with the bot (comma-separated)
DISCORD_ALLOWED_ROLE_NAMES=Staff,Board,Trustees

# The channel where the bot posts status reports (heartbeat, digest, governance)
DISCORD_STATUS_CHANNEL_ID=123456789012345678

# Role → permission mapping (optional — maps Discord roles to cdcn-agent roles)
DISCORD_ROLE_MAPPING=Board->admin,Staff->staff,Volunteers->trustee
```

The `DISCORD_ROLE_MAPPING` field lets you map Discord server roles to the agent's
three-tier permission model:

| cdcn-agent role | Permissions |
|---|---|
| `trustee` | search, query, read pending changes |
| `staff` | + trigger indexing, draft skills |
| `admin` | + add users, approve/reject changes, view full audit log |

If a user has no mapped role, they are treated as `trustee` (read-only).

---

## 6. Slash Command Registration

Slash commands (if any are added) register automatically on startup.
Global registration can take **up to one hour** to propagate across all Discord servers.

To force immediate registration in a specific server during development, use
guild-scoped registration by setting `DISCORD_GUILD_ID` in `.env` (optional).

---

## 7. Troubleshooting

**Bot not responding in a channel:**
- Check the channel ID is listed in `DISCORD_ALLOWED_CHANNEL_IDS`
- Verify the bot has "View Channel" and "Send Messages" permissions for that channel (not blocked by role overrides)

**Bot can see messages but cannot read content:**
- Ensure "Message Content Intent" is enabled in the Developer Portal → Bot settings

**Permission errors / "Missing Access":**
- Re-invite the bot using the updated permissions URL above
- Check that the bot's role is positioned above any restricted roles in server settings (Server Settings → Roles, drag "CDCN Agent" higher)

**Bot not posting to status channel:**
- Verify `DISCORD_STATUS_CHANNEL_ID` is correct
- Check the bot has "Send Messages" permission in that channel specifically
