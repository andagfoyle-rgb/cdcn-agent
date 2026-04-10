# CDCN Agent

CDCN Agent is an always-on AI assistant for a Scottish community development charity.
It indexes the organisation's documents — board minutes, funding applications, governance
policies — into a local vector database, answers staff and trustee queries in plain English,
drafts new documents using the organisation's templates, and runs overnight analysis tasks
to surface funding deadlines, flag stale policies, and refine its own knowledge. Everything
runs on hardware you own, inside your network, with no data leaving the building.

---

## Hardware

| Device | Role |
|---|---|
| Raspberry Pi 5 (4 GB+) | Always-on host: runs the agent, ChromaDB, scheduler, Telegram/Discord bots |
| Dell R710 (48 GB RAM) | Wake-hours inference server: runs Ollama with llama3.1:14b |

The Pi consumes ~8 W continuously. The R710 wakes via WoL at 07:00 and can be
scheduled to shut down at 22:00, keeping electricity costs reasonable.

---

## Architecture

```
Pi 5 (always-on, ~8 W)                 R710 (wake hours 07:00–22:00, ~250–400 W)
┌───────────────────────────────┐       ┌────────────────────────────────────────┐
│  FastAPI gateway :8400        │──LAN─▶│  Ollama :11434                         │
│  Telegram bot                 │       │    llama3.1:14b  (chat + drafting)      │
│  Discord bot                  │       │    nomic-embed-text  (embeddings)       │
│  APScheduler                  │       └────────────────────────────────────────┘
│  ChromaDB  (local vector DB)  │
│  SQLite    (audit log, users) │
│  Ollama phi3:mini  (dream)    │
└───────────────────────────────┘
```

All LLM inference is local. No documents, queries, or responses leave your network.

---

## Quick Start

```bash
# 1. Clone the repository
git clone <repo-url> && cd cdcn-agent

# 2. Install (Raspberry Pi OS 64-bit Bookworm)
bash install.sh

# 3. Edit configuration
sudo nano /etc/cdcn-agent/.env

# 4. Install Ollama on the Pi (dream mode)
curl -fsSL https://ollama.com/install.sh | sh
ollama pull phi3:mini && ollama pull nomic-embed-text

# 5. Install Ollama on the R710 (see docs/r710-setup.md)

# 6. Start the service
sudo systemctl start cdcn-agent
sudo journalctl -u cdcn-agent -f

# 7. Create the first admin user
cdcn-agent add-user admin admin

# 8. Verify
curl http://localhost:8400/health
```

---

## Wake / Dream Schedule

| Time | Event |
|---|---|
| 07:00 | Wake transition — WoL packet sent, R710 boots, adapters enabled |
| Every 6 h | Auto-index: scan documents folder for new/changed files |
| Every 6 h | Heartbeat: check funding deadlines, post report if alerts |
| 21:45 | Journal written — nightly reflection |
| 22:00 | Dream transition — adapters disabled, dream worker starts |
| 22:00 – 07:00 | Dream worker: consolidate memory, self-critique, map documents, plan tomorrow, refine style guide |
| Monday 07:15 | Weekly digest posted to Discord |
| 1st of month 08:00 | Governance check: flag policies not reviewed in 12 months |

All times and intervals are configurable in `/etc/cdcn-agent/.env`.

---

## Skills Reference

| Skill | Example prompt |
|---|---|
| **search** | "Find all board minutes where the reserves policy was discussed" |
| **search** | "What did we submit to the National Lottery last year?" |
| **writer** | "Draft a funding application to Community Benefit for the digital skills project" |
| **writer** | "Write board minutes for today's meeting: agenda item 1, item 2..." |
| **memory** | "What are we currently working on?" |
| **memory** | "Update the current task: preparing annual report" |
| **indexer** | (runs automatically; can be triggered manually) |
| **skill_builder** | "Draft a skill that reads Companies House filings for our charity number" |

### Via Telegram or Discord

Just write naturally in a permitted channel or DM:
```
@cdcn-agent Search for our safeguarding policy and summarise the key points
```

### Via the Web UI

Navigate to `http://<pi-ip>:8400` (or Tailscale IP) and log in.

### Via CLI

```bash
cdcn-agent test-skill search --args '{"query": "safeguarding policy"}'
```

---

## CLI Reference

```
cdcn-agent skills                       List all available skills
cdcn-agent test-skill SKILL [--args]    Run a skill directly with optional JSON args
cdcn-agent new-skill "description"      Draft a new skill using the LLM

cdcn-agent pending                      List pending config changes
cdcn-agent approve CHANGE_ID            Approve and apply a pending change
cdcn-agent reject CHANGE_ID [--reason]  Reject a pending change

cdcn-agent journal [--days N]           Show recent journal entries
cdcn-agent list-docs [--type TYPE]      List indexed documents

cdcn-agent add-user USERNAME ROLE       Create a user (roles: admin/staff/trustee)

cdcn-agent wake                         Request immediate wake transition
cdcn-agent dream                        Request immediate dream transition
cdcn-agent dream --dry-run              Test dream cycle with mock LLM
cdcn-agent dream --task TASK            Run a single dream task
```

---

## Security Model

### What stays on your LAN

- All documents indexed into ChromaDB
- All conversation history
- All journal entries, memory files, and audit logs
- All LLM inference (Ollama is entirely local)
- The SQLite user database and audit log

### What leaves the network

- Telegram and Discord messages (only if those integrations are configured)
- Wake-on-LAN UDP broadcasts (local network only)
- Nothing else

### Threat model

- **Gateway**: binds to `127.0.0.1` by default. Set `ALLOW_PUBLIC_BIND=true` only if needed, and restrict with firewall rules and/or Tailscale ACLs.
- **Authentication**: JWT tokens, 8-hour expiry, bcrypt-hashed passwords in SQLite.
- **Authorisation**: three-tier role system (trustee / staff / admin) checked per-endpoint.
- **Rate limiting**: 20 requests per user per minute (token bucket, in-memory).
- **File validation**: MIME type checked before indexing; metadata sanitised before ChromaDB upsert.
- **Config changes**: proposed via pending-changes queue, require admin approval — the agent cannot modify its own operating rules without human review.
- **Audit log**: append-only SQLite, all LLM calls, document operations, auth events, and state transitions are logged permanently.

### Ollama on the R710

- Never expose port 11434 to the public internet.
- Restrict to Tailscale interface, or add Nginx basic auth (see [r710-setup.md](docs/r710-setup.md)).

---

## Backup

CDCN Agent uses [rclone](https://rclone.org/) to sync data to Backblaze B2.

### Set up rclone

```bash
sudo apt-get install -y rclone
rclone config    # follow prompts to add a B2 remote named "b2"
```

### Run a backup

```bash
make backup BACKUP_BUCKET=my-b2-bucket-name
```

The backup syncs:
- `/var/lib/cdcn-agent/` — ChromaDB, memory, documents, audit log
- `/opt/cdcn-agent/skills_config/` — memory files, templates, pending changes
- `/etc/cdcn-agent/` — config (`.env` is excluded — it contains secrets)

### Daily automated backup

```bash
sudo crontab -e
# Add:
30 3 * * * cd /opt/cdcn-agent && make backup BACKUP_BUCKET=my-b2-bucket >> /var/log/cdcn-agent/backup.log 2>&1
```

---

## Updating

```bash
cd /opt/cdcn-agent
sudo git pull                                         # pull latest code
sudo -u cdcn-agent .venv/bin/pip install -r requirements.txt  # update deps
sudo systemctl restart cdcn-agent
sudo journalctl -u cdcn-agent -f                      # check startup logs
```

If the update changes `/etc/cdcn-agent/.env` format, check `.env.example` for new keys.

---

## Troubleshooting

### R710 not responding / Ollama timeout

```bash
# Check WoL reached the R710
ping 192.168.1.100

# Check Ollama is listening
curl http://192.168.1.100:11434/api/tags

# Increase boot wait time in .env
WAKEONLAN_BOOT_WAIT_SECS=300

# Check the R710 BIOS has WoL enabled (see docs/r710-setup.md)
```

### ChromaDB errors on startup

```bash
# Check the data directory is writable by cdcn-agent
ls -la /var/lib/cdcn-agent/

# Repair permissions
sudo chown -R cdcn-agent:cdcn-agent /var/lib/cdcn-agent/
```

### Discord slash commands not appearing

Slash commands take up to one hour to propagate globally. For immediate
availability during development, configure `DISCORD_GUILD_ID` in `.env`
to enable guild-scoped (instant) registration.

### Dream worker not running / tasks failing

```bash
# Check the log
sudo journalctl -u cdcn-agent --since "22:00" | grep dream

# Test the dream worker directly with a mock LLM
cdcn-agent dream --dry-run

# Test a specific task
cdcn-agent dream --dry-run --task self_critique
```

Dream worker failures are logged individually — one failing task does not
stop the others. Check `data/memory/journal/` for dream journal entries.

### "Could not validate credentials" on web UI

The JWT secret has changed, or the token has expired (8-hour lifetime).
Log out and log in again. If the problem persists, check `SECRET_KEY` in `.env`
is set and unchanged between restarts.

### Service won't start

```bash
sudo journalctl -u cdcn-agent -n 50
# Common causes:
# - .env missing or misconfigured (OLLAMA_BASE_URL format)
# - Python venv not created (run: sudo bash install.sh)
# - Port 8400 already in use
```

---

## Documentation Index

| Guide | Contents |
|---|---|
| [R710 Setup](docs/r710-setup.md) | Ubuntu Server, Ollama, WoL, Nginx proxy |
| [Wake/Dream Guide](docs/wake-dream-guide.md) | States, dream tasks, pending changes governance |
| [Discord Setup](docs/discord-setup.md) | Bot creation, permissions, channel structure |
| [Tailscale Setup](docs/tailscale-setup.md) | Secure remote access, ACL configuration |
| [Adding Skills](docs/adding-skills.md) | Skill structure, skill builder, review checklist |
| [Funder Templates](docs/funder-templates.md) | Writer template format and examples |
