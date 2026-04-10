# Wake / Dream Mode Guide

CDCN Agent runs in two distinct states that trade off between full capability
and low-power overnight operation.

---

## The Two States

| | Wake | Dream |
|---|---|---|
| **LLM** | R710 llama3.1:14b (remote, full-size) | Pi 5 phi3:mini (local, compact) |
| **Interfaces** | Telegram, Discord, Web UI all active | All user-facing interfaces disabled |
| **Scheduling** | Default 07:00 – 22:00 | Default 22:00 – 07:00 |
| **What runs** | Answers queries, indexes documents, full skill set | Dream worker tasks, nightly journal |
| **R710** | Powered on via Wake-on-LAN | Can be shut down to save ~300 W |

---

## Wake State

When the scheduled wake time arrives:

1. The Pi sends a Wake-on-LAN magic packet to the R710's MAC address.
2. The agent polls `OLLAMA_BASE_URL/api/tags` every 10 seconds until Ollama responds (up to `WAKEONLAN_BOOT_WAIT_SECS`, default 180 s).
3. The LLM client switches to the R710 endpoint.
4. The agent reads the last journal entries and generates a brief orientation note ("what are we working on today?").
5. Any pending changes with status "approved" are applied.
6. Telegram and Discord adapters are re-enabled.
7. A morning summary is posted to the Discord status channel and Telegram notification chat.

---

## Dream State

When the scheduled dream time arrives:

1. A goodbye message is posted to Discord and Telegram.
2. All user-facing adapters stop accepting new messages.
3. A minimal journal entry is written noting the rest time.
4. The LLM client switches to `DREAM_OLLAMA_BASE_URL` (the Pi's local Ollama).
5. The dream worker runs five overnight tasks (see below).
6. The R710 can be scheduled to shut down to save electricity (see [r710-setup.md](r710-setup.md)).

---

## The Dream Worker — Five Overnight Tasks

Each task runs independently; a failure in one task does not stop the others.

### 1. Consolidate Memory
Reads all session summaries from the past day, identifies recurring themes,
decisions made, and outstanding items, then appends a concise summary to
`skills_config/memory/memory.md`. The goal is to surface patterns that are
worth keeping long-term.

### 2. Self-Critique
Reviews today's conversation transcripts, looks for responses that were
incorrect, incomplete, or unclear, and drafts corrections as pending-change
proposals. These go to the `/pending-changes` review queue — nothing is
auto-applied.

### 3. Map Document Relationships
Scans the ChromaDB vector store and looks for thematic connections between
indexed documents (e.g. a grant application that references a policy that
was last reviewed 18 months ago). Adds notes to the knowledge graph file
in `skills_config/memory/knowledge_graph.md`.

### 4. Anticipate Tomorrow
Reads the calendar of upcoming funding deadlines from `skills_config/funding_deadlines.yaml`
and any meeting dates recorded in memory, then drafts a short "tomorrow's
priorities" note written to `data/memory/current_task.md`. This is surfaced
in the morning orientation.

### 5. Refine Style Guide
Samples recent documents produced by the writer skill, checks whether they
match the tone and format in `skills_config/memory/style_guide.md`, and
proposes any improvements as a pending-change proposal.

---

## Changing the Schedule

Edit `/etc/cdcn-agent/.env`:

```env
WAKE_START_TIME=08:00          # later start for weekends
WAKE_END_TIME=21:00            # earlier end
JOURNAL_TIME=20:45             # 15 minutes before WAKE_END_TIME
HEARTBEAT_INTERVAL_HOURS=4     # more frequent during busy periods
```

Restart after changes:
```bash
sudo systemctl restart cdcn-agent
```

The journal job always runs `JOURNAL_TIME` minutes before the dream transition,
writing the nightly reflection before interfaces go offline.

---

## The Pending Changes Governance Process

The dream worker and the skill-builder skill never directly modify live
configuration files. Instead they propose changes through the pending-changes
queue, which requires human review before anything is applied.

### How proposals appear

1. The dream worker (or a user via chat) proposes a change.
2. A JSON metadata file and a diff file are written to `PENDING_CHANGES_PATH/` (default `/var/lib/cdcn-agent/pending_changes/`).
3. The change is listed in:
   - **Web UI** → `/pending-changes` (requires login)
   - **CLI** → `cdcn-agent pending`

### Who should review

The **admin** role is required to approve or reject changes. Typically this
is a trustee who has technical access — someone comfortable reading a markdown
diff and deciding whether the proposed wording is appropriate.

You do not need to understand Python to review changes — the diffs are plain
markdown edits to `skills_config/memory/` files (agents.md, style_guide.md, etc.).

### Approving a change

**Web UI:**
Navigate to `/pending-changes`, click the change, read the diff, click **Approve**.

**CLI:**
```bash
cdcn-agent pending          # list changes with IDs
cdcn-agent approve abc12345  # apply the change
```

**API:**
```bash
curl -X POST http://localhost:8400/api/pending-changes/abc12345/approve \
  -H "Authorization: Bearer <token>"
```

### Rejecting a change

```bash
cdcn-agent reject abc12345 --reason "Tone is too informal for board documents"
```

The rejection reason is appended to the archived diff file.

### Why changes are never auto-applied

The agent could modify its own operating rules and communication style if
changes were auto-applied. The pending-changes queue ensures a human always
reads and approves proposed changes before they affect the agent's behaviour.
This is a deliberate governance safeguard, not a limitation.

When the agent wakes up in the morning, any changes with status "approved"
(set via the web UI or CLI overnight) are applied automatically during the
wake transition — but only because a human approved them first.

---

## Journal Files

Journal files are written to `/var/lib/cdcn-agent/memory/journal/`.

| File pattern | Contents |
|---|---|
| `YYYY-MM-DD.md` | Nightly journal entry from the `journal_job` |
| `dream_YYYY-MM-DD.md` | Dream-mode journal entry (if the dream worker writes one) |

Read recent entries:
```bash
cdcn-agent journal             # last 3 days
cdcn-agent journal --days 7    # last week
```

Or read directly:
```bash
cat /var/lib/cdcn-agent/memory/journal/$(date +%Y-%m-%d).md
```

---

## Triggering a Dream Cycle for Testing

Test the dream worker without waiting for the overnight schedule:

```bash
# Full cycle with a mock LLM (no R710 or Pi Ollama required)
cdcn-agent dream --dry-run

# Or via Makefile in dev mode
make dream-test
```

Run a single task:
```bash
cdcn-agent dream --dry-run --task consolidate_memory
cdcn-agent dream --dry-run --task self_critique
cdcn-agent dream --dry-run --task map_document_relationships
cdcn-agent dream --dry-run --task anticipate_tomorrow
cdcn-agent dream --dry-run --task refine_style_guide
```

Trigger a live dream transition (requires the service to be running):
```bash
cdcn-agent dream
```

Trigger an immediate wake (useful after a config change):
```bash
cdcn-agent wake
```
