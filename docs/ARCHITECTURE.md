# CDCN Agent Architecture

**System:** CDCN Agent -- on-premises AI assistant for a Scottish community development charity (SC048164)

**Hardware:** Raspberry Pi 5 (always-on). A Dell R710 inference server was previously used but is now retired.

**Stack:** FastAPI (Python 3.13), ChromaDB (vectors), SQLite (audit/users/indexer/threads/tracker/cronjobs), Ollama/SiliconFlow LLM

**Privacy:** All computation is local; no documents or queries leave the network (except SiliconFlow API calls for LLM inference).

---

## System Overview

```
Internet ─── Tailscale VPN ─── Caddy (reverse proxy)
                                    │
                              FastAPI :8400
                              ┌─────────────┐
                              │   main.py    │
                              │   (lifespan) │
                              └──────┬───────┘
                     ┌───────────────┼───────────────┐
                     │               │               │
                Web Adapter    Discord Bot    Telegram Bot
                (web.py)       (discord)      (telegram)
                     │
                     ▼
              AgentRouter (gateway/router.py)
              ┌──────────────────────────────┐
              │ 1. Prompt injection guard    │
              │ 2. Session management        │
              │ 3. RAG prefetch             │
              │ 4. LLM call (non-streaming) │
              │ 5. Skill detection + exec   │
              │ 6. Final streaming response │
              │ 7. Citation verification    │
              │ 8. Memory consolidation     │
              └──────────────────────────────┘
                     │
         ┌───────────┼───────────┐
         ▼           ▼           ▼
      Skills      LLM Client   Storage
      (28 modules) (Ollama/     (SQLite,
                   SiliconFlow)  ChromaDB)
```

---

## Components

### Entry Point -- app/main.py

- FastAPI app with lifespan handler
- Constructs all adapters, skills, and the AgentRouter
- Security headers middleware (CSP, HSTS, X-Frame-Options, etc.)
- CORS middleware (restricted origins)
- Mounts `web_router` at root, `gateway_router` at `/api`

### Configuration -- app/config.py

- Pydantic v2 `BaseSettings`, loaded from `/etc/cdcn-agent/.env`
- 50+ typed settings for LLM, storage, auth, and network
- Validates public bind safety

### Authentication -- app/auth/auth.py

- SQLite user store (`users.db`)
- bcrypt password hashing, JWT tokens (HS256, 8-hour expiry)
- Three base roles: `trustee`, `staff`, `admin` (extensible via roles table)
- Token bucket rate limiting (20 req/user/min)
- Login lockout (5 failures triggers a 15-minute cooldown)
- Registration requires admin approval

### AgentRouter -- app/gateway/router.py

- Core agentic loop orchestrator
- Prompt injection guard (3 regex patterns)
- System prompt cache (60-second TTL, per-role)
- RAG prefetch via keyword matching
- Skill call extraction (JSON in fenced blocks, XML fallback for GLM-5)
- Post-tool streaming with no-more-tools instruction
- Citation verification
- Session persistence with periodic memory consolidation

### Web Interface -- app/interfaces/web.py

- Login/registration pages, single-page chat UI
- WebSocket endpoint for streaming chat
- Archive file explorer with upload/download/delete
- Feature pages: Calendar, Action Points, Funding, Memory, Admin Dashboard
- Threaded conversations with multi-participant support

### Skills (28 modules in app/skills/)

| Skill | Purpose |
|---|---|
| search | 3-layer document retrieval (TOC, keyword/grep, semantic) |
| indexer | Document parsing + vector ingestion |
| writer | Template-based document drafting |
| dream_worker | Overnight consolidation tasks |
| funding_feed | RSS scraper + deadline notifications |
| scheduler | APScheduler wrapper + cron jobs |
| memory | Agent memory persistence + retrieval |
| document_editor | Edit documents |
| chunker | Document chunking for embeddings |
| deadline_tracker | Track deadlines |
| action_tracker | Track action points |
| calendar_manager | Calendar integration |
| document_parser | PDF/DOCX parsing (pdfplumber, python-docx, pdftotext fallback) |
| skill_builder | LLM-driven skill creation |
| vision | Image analysis |
| notify | Notifications |
| clarify | Clarification requests |
| delegate | Task delegation |
| meeting_prep | Meeting preparation |
| (and more) | Additional utility skills |

### Storage

- **audit_log.py** -- Append-only SQLite audit log (DELETE/UPDATE triggers prevent tampering)
- **vector_store.py** -- ChromaDB wrapper for semantic search
- **file_store.py** -- File I/O utilities
- **pending_changes.py** -- Config change governance queue
- **threads.py** -- Thread management

### State Management -- app/state_manager.py

- Wake/Dream state transitions based on time-of-day schedule
- **Wake mode:** full LLM inference, all features active
- **Dream mode:** lightweight tasks, overnight consolidation

---

## Database Schema

### cdcn_audit.db

| Table | Purpose |
|---|---|
| audit_log | Generic events (ts, actor, action, target, detail) |
| llm_calls | LLM usage tracking (user, skill, tokens, latency) |
| document_ops | File indexing operations |
| auth_events | Login/logout events |
| state_transitions | Wake/dream transitions |
| dream_cycles | Overnight task summaries |
| pending_change_actions | Governance change audit trail |
| shared_messages | Noticeboard/group messages (with FTS5 index) |
| polls, poll_responses | Polling system |
| funding_opportunities | Scraped funding data |
| learned_skills | Dynamically created skills |
| feed_sources | RSS feed configuration |
| threads, thread_members, thread_messages | Conversation threads (legacy) |

### users.db

| Table | Purpose |
|---|---|
| users | username (PK), hashed_password, role, active, email, display_name |
| registration_requests | Approval queue |
| roles | Custom role definitions with permission sets |

### indexer.db

| Table | Purpose |
|---|---|
| indexed_files | File tracking (path, doc_id, status) |
| document_metadata | Parsed document info (type, date, attendees, topics) |
| chunks | Parent/child chunks with embeddings |
| document_versions | Version history with content hashes |

### threads.db

| Table | Purpose |
|---|---|
| threads | Conversation threads (id, name, created_by) |
| thread_participants | Membership |
| thread_messages | Messages with sender, role, content |

### tracker.db

| Table | Purpose |
|---|---|
| action_points | Meeting action items with status tracking |
| funding_pipeline | Grant application tracking |
| funding_reports | Reporting deadlines |
| deadlines | General deadline tracking with reminders |

### cronjobs.db

| Table | Purpose |
|---|---|
| user_jobs | User-created scheduled tasks |

---

## Data Flow

```
User Query --> Web/Discord/Telegram Adapter
  --> AgentRouter.handle_message()
    --> Prompt injection check
    --> Session load/create
    --> System prompt + RAG prefetch
    --> LLM call (non-streaming)
    --> Skill detection (JSON/XML parsing)
    --> If skill found: execute --> inject result --> LLM again
    --> Stream final response to user
    --> Citation verification (optional)
    --> Session persist + memory consolidation (every 10 exchanges)
```

---

## File Structure

```
/opt/cdcn-agent/
├── app/
│   ├── main.py              # FastAPI entry point
│   ├── config.py            # Pydantic settings
│   ├── llm_client.py        # Async LLM client
│   ├── state_manager.py     # Wake/Dream state
│   ├── auth/auth.py         # Auth, JWT, rate limiting
│   ├── gateway/
│   │   ├── router.py        # Agentic loop + API endpoints
│   │   ├── session.py       # Session persistence
│   │   └── identity_map.py  # Discord<->Web account linking
│   ├── interfaces/
│   │   ├── web.py           # Web UI + REST API
│   │   ├── discord_adapter.py
│   │   ├── telegram_bot.py
│   │   ├── email_adapter.py
│   │   └── base_adapter.py
│   ├── skills/              # 28 skill modules
│   ├── retrieval/pipeline.py
│   ├── storage/             # SQLite, ChromaDB, files
│   └── static/              # JS libraries
├── tests/                   # Test suite
├── docs/                    # Documentation
├── data/                    # Runtime data
├── skills_config/           # Templates, memory files
└── systemd/                 # Service files
```

---

## Configuration Reference

Key environment variables in `/etc/cdcn-agent/.env`:

| Variable | Purpose |
|---|---|
| LLM_PROVIDER | `ollama` or `siliconflow` |
| OLLAMA_BASE_URL | Ollama endpoint |
| SILICONFLOW_API_KEY | API key for cloud LLM |
| SECRET_KEY | JWT signing secret (256-bit) |
| GATEWAY_BIND_HOST / GATEWAY_PORT | Listen address |
| WATCHED_FOLDER | Document archive path |
| CHROMA_PATH | Vector database path |
| AUDIT_LOG_PATH | Audit database path |
| WAKE_START_TIME / WAKE_END_TIME | Active hours |
| RATE_LIMIT_PER_MINUTE | Per-user rate limit |
